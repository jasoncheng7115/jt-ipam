"""維護排程 API（2026-11-01）：清單、建立（排定一次 / 每週例行 / 一鍵立即）、預覽影響、提前結束、延長、
查「這個物件現在有沒有在維護」、查被靜音了什麼。

權限（使用者 11-01 定案）：對範圍內每個物件都有修改權才能建立或修改；IP 範圍、整合、掃描代理只限管理員。
看得到維護＝管理員、建立的人，或看得到範圍內任一物件。全部寫入留稽核。
"""
from __future__ import annotations

import ipaddress
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, forbid_zero_visibility, require_admin
from app.core.audit import append_audit
from app.core.db import get_session
from app.core.ui_error import ui_detail
from app.models.maintenance import MaintenanceScope, MaintenanceSuppression, MaintenanceWindow
from app.schemas.base import StrictModel
from app.services import maintenance as svc
from app.services.permission import get_object_permission, has_permission

router = APIRouter(prefix="/maintenance", tags=["maintenance"], dependencies=[Depends(forbid_zero_visibility)])

ScopeType = Literal["ip", "ip_range", "subnet", "section", "device", "rack", "location", "customer", "integration",
                    "scan_agent"]
ADMIN_ONLY_SCOPES = {"ip_range", "integration", "scan_agent"}
SETTINGS_KEY = "maintenance"
DEFAULTS = {"max_hours": 168, "large_threshold": 500}


# ─────────────────── 設定 ───────────────────

async def get_settings(session: AsyncSession) -> dict[str, Any]:
    from app.models.system_setting import SystemSetting
    row = await session.get(SystemSetting, SETTINGS_KEY)
    v = dict(row.value) if row is not None and isinstance(row.value, dict) else {}
    return {"max_hours": max(1, min(int(v.get("max_hours") or DEFAULTS["max_hours"]), 24 * 90)),
            "large_threshold": max(10, min(int(v.get("large_threshold") or DEFAULTS["large_threshold"]), 1_000_000))}


class SettingsIn(StrictModel):
    max_hours: Annotated[int, Field(ge=1, le=24 * 90)]
    large_threshold: Annotated[int, Field(ge=10, le=1_000_000)]


@router.get("/settings")
async def read_settings(_user: CurrentUser, session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, Any]:
    return await get_settings(session)


@router.put("/settings", dependencies=[Depends(require_admin)])
async def write_settings(payload: SettingsIn, user: CurrentUser, request: Request,
                         session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, Any]:
    from sqlalchemy.orm.attributes import flag_modified

    from app.models.system_setting import SystemSetting
    row = await session.get(SystemSetting, SETTINGS_KEY)
    if row is None:
        session.add(SystemSetting(key=SETTINGS_KEY, value=payload.model_dump()))
    else:
        row.value = payload.model_dump()
        flag_modified(row, "value")
    await _audit(session, user, request, None, "update_settings", payload.model_dump())
    await session.commit()
    return await get_settings(session)


@router.get("/scope-search")
async def scope_search(
    user: CurrentUser, session: Annotated[AsyncSession, Depends(get_session)],
    type: ScopeType = Query(...), q: str = Query("", max_length=100), limit: int = Query(30, ge=1, le=100),
) -> dict[str, Any]:
    """範圍選擇器：依類型搜尋物件（只列看得到的；IP 範圍、整合、掃描代理只給管理員）。"""
    from sqlalchemy import String, cast, or_

    from app.models.address import IPAddress
    from app.models.customer import Customer
    from app.models.device import Device
    from app.models.location import Location, Rack
    from app.models.scan_agent import ScanAgent
    from app.models.section import Section
    from app.models.subnet import Subnet
    from app.services.permission import visible_ids
    term = q.strip()
    like = f"%{term}%"
    items: list[dict[str, Any]] = []
    if type in ADMIN_ONLY_SCOPES and not user.is_admin:
        return {"items": []}
    if type == "integration":
        from app.services import integration_registry as reg
        for kind, lbl, model in reg.models():
            if not term or term.lower() in lbl.lower():
                items.append({"id": None, "integration_kind": kind, "label": f"{lbl}（全部）"})
            for row in (await session.execute(select(model))).scalars():
                name = reg.display_name(row, lbl)
                if not term or term.lower() in f"{lbl} {name}".lower():
                    items.append({"id": str(row.id), "integration_kind": kind, "label": f"{lbl} · {name}"})
        return {"items": items[:limit]}
    if type == "ip_range":
        return {"items": []}
    model, cols, otype = {
        "ip": (IPAddress, [cast(IPAddress.ip, String), IPAddress.hostname], "subnet"),
        "subnet": (Subnet, [cast(Subnet.cidr, String), Subnet.description], "subnet"),
        "section": (Section, [Section.name], "section"),
        "device": (Device, [Device.name], "device"),
        "rack": (Rack, [Rack.name], "rack"),
        "location": (Location, [Location.name], "location"),
        "customer": (Customer, [Customer.name], "customer"),
        "scan_agent": (ScanAgent, [ScanAgent.name], None),
    }[type]
    stmt = select(model)
    if term:
        stmt = stmt.where(or_(*[c.ilike(like) for c in cols]))
    if otype and not user.is_admin:
        vis = await visible_ids(session, user=user, object_type=otype, required="read")
        if vis is not None:
            from app.core.sqlin import in_values
            col = IPAddress.subnet_id if type == "ip" else model.id
            stmt = stmt.where(in_values(col, vis))
    for row in (await session.execute(stmt.limit(limit))).scalars():
        if type == "ip":
            label = str(row.ip).split("/")[0] + (f"（{row.hostname}）" if row.hostname else "")
        elif type == "subnet":
            label = str(row.cidr) + (f"（{row.description}）" if row.description else "")
        else:
            label = str(getattr(row, "name", row.id))
        items.append({"id": str(row.id), "label": label})
    return {"items": items}


# ─────────────────── 共用 ───────────────────

class ScopeIn(StrictModel):
    scope_type: ScopeType
    object_id: uuid.UUID | None = None
    integration_kind: Annotated[str | None, Field(max_length=32)] = None
    range: Annotated[str | None, Field(max_length=100)] = None   # 「192.0.2.10-192.0.2.20」或 CIDR


class ScheduleIn(StrictModel):
    type: Literal["once", "weekly"]
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    weekdays: list[Annotated[int, Field(ge=0, le=6)]] | None = None
    start_time: time | None = None
    end_time: time | None = None
    valid_from: date | None = None
    valid_until: date | None = None


class WindowIn(StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=200)]
    note: Annotated[str | None, Field(max_length=4000)] = None
    ticket: Annotated[str | None, Field(max_length=100)] = None
    behavior: Literal["mute", "mark"] = "mute"
    schedule: ScheduleIn
    scopes: Annotated[list[ScopeIn], Field(min_length=1, max_length=50)]
    confirm_large: bool = False


class QuickIn(StrictModel):
    scope: ScopeIn
    minutes: Annotated[int, Field(ge=5, le=24 * 60 * 90)]
    title: Annotated[str | None, Field(max_length=200)] = None
    note: Annotated[str | None, Field(max_length=4000)] = None
    ticket: Annotated[str | None, Field(max_length=100)] = None


class ExtendIn(StrictModel):
    minutes: Annotated[int, Field(ge=5, le=24 * 60 * 30)]


async def _audit(session: AsyncSession, user: Any, request: Request, oid: str | None, action: str,
                 diff: dict[str, Any] | None) -> None:
    await append_audit(
        session, actor_user_id=str(user.id), actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"), object_type="maintenance", object_id=oid, action=action,
        diff=diff, request_id=getattr(request.state, "request_id", None))


_SCOPE_NAME = {"ip": "IP", "ip_range": "IP 範圍", "subnet": "子網路", "section": "區段", "device": "裝置",
               "rack": "機櫃", "location": "機房 / 地點", "customer": "單位", "integration": "整合",
               "scan_agent": "掃描代理"}
_WD = "一二三四五六日"


def _schedule_text(w: MaintenanceWindow) -> str:
    """稽核明細用：站台時區、精確到分；同一天的結束只寫時間。星期日排最前面（跟畫面一致）。"""
    if w.schedule_type == "weekly":
        days = "、".join(_WD[d] for d in sorted(w.weekdays or [], key=lambda d: (d + 1) % 7))
        until = f"（到 {w.valid_until.isoformat()}）" if w.valid_until else ""
        return f"每週{days} {w.start_time:%H:%M} - {w.end_time:%H:%M}{until}"
    z = svc.tz()
    a, b = w.starts_at.astimezone(z), w.ends_at.astimezone(z)  # type: ignore[union-attr]
    return f"{a:%Y-%m-%d %H:%M} - {b:%H:%M}" if a.date() == b.date() else f"{a:%Y-%m-%d %H:%M} - {b:%Y-%m-%d %H:%M}"


def _audit_view(w: MaintenanceWindow, scopes: list[MaintenanceScope]) -> dict[str, Any]:
    """稽核記錄寫看得懂的內容（不是請求原文的 UUID 與 UTC 時間）。"""
    return {"title": w.title, "ticket": w.ticket, "note": w.note,
            "behavior": {"mute": "只靜音通知", "mark": "只標示"}.get(w.behavior, w.behavior),
            "schedule": _schedule_text(w),
            "scopes": [f"{_SCOPE_NAME.get(x.scope_type, x.scope_type)} {x.label or ''}".strip() for x in scopes]}


def _err(status: int, code: str, message: str, **params: Any) -> HTTPException:
    return HTTPException(status, detail=ui_detail(code, message, **params))


async def _scope_model(session: AsyncSession, s: ScopeIn) -> tuple[MaintenanceScope, str]:
    """檢查範圍物件存在、算出顯示名稱，回傳（未存）MaintenanceScope 與權限物件類型。"""
    from app.models.address import IPAddress
    from app.models.customer import Customer
    from app.models.device import Device
    from app.models.location import Location, Rack
    from app.models.scan_agent import ScanAgent
    from app.models.section import Section
    from app.models.subnet import Subnet
    sc = MaintenanceScope(scope_type=s.scope_type, object_id=s.object_id)
    if s.scope_type == "ip_range":
        raw = (s.range or "").strip()
        try:
            if "-" in raw:
                a, b = (ipaddress.ip_address(x.strip()) for x in raw.split("-", 1))
            else:
                net = ipaddress.ip_network(raw, strict=False)
                a, b = net[0], net[-1]
            if a.version != b.version:
                raise ValueError
        except ValueError as exc:
            raise _err(422, "maintenance_range_invalid", "不是有效的位址範圍或 CIDR", value=raw[:100]) from exc
        sc.range_start, sc.range_end, sc.label = str(min(a, b)), str(max(a, b)), f"{min(a, b)}-{max(a, b)}"
        return sc, "admin"
    if s.scope_type == "integration":
        from app.services import integration_registry as reg
        kinds = {k: (lbl, model) for k, lbl, model in reg.models()}
        if s.integration_kind not in kinds:
            raise _err(422, "maintenance_scope_invalid", "不認得的整合類型", value=str(s.integration_kind))
        sc.integration_kind = s.integration_kind
        lbl, model = kinds[s.integration_kind]
        if s.object_id is None:
            sc.label = f"{lbl}（全部）"
        else:
            row = await session.get(model, s.object_id)
            if row is None:
                raise _err(404, "maintenance_scope_not_found", "找不到範圍裡的物件")
            sc.label = f"{lbl} · {reg.display_name(row, lbl)}"
        return sc, "admin"
    if s.object_id is None:
        raise _err(422, "maintenance_scope_invalid", "請選擇範圍物件", value=s.scope_type)
    model = {"ip": IPAddress, "subnet": Subnet, "section": Section, "device": Device, "rack": Rack,
             "location": Location, "customer": Customer, "scan_agent": ScanAgent}[s.scope_type]
    row = await session.get(model, s.object_id)
    if row is None:
        raise _err(404, "maintenance_scope_not_found", "找不到範圍裡的物件")
    if s.scope_type == "ip":
        sc.label = str(row.ip).split("/")[0] + (f"（{row.hostname}）" if row.hostname else "")
        return sc, f"subnet:{row.subnet_id}"
    if s.scope_type == "subnet":
        sc.label = str(row.cidr) + (f"（{row.description}）" if row.description else "")
    else:
        sc.label = str(getattr(row, "name", None) or s.object_id)
    return sc, "admin" if s.scope_type == "scan_agent" else f"{s.scope_type}:{s.object_id}"


async def _may_write(session: AsyncSession, user: Any, perm: str) -> bool:
    if user.is_admin:
        return True
    if perm == "admin":
        return False
    otype, oid = perm.split(":", 1)
    level = await get_object_permission(session, user=user, object_type=otype, object_id=uuid.UUID(oid))
    return has_permission(level, "write")


async def _may_read_scope(session: AsyncSession, user: Any, s: MaintenanceScope) -> bool:
    if user.is_admin:
        return True
    if s.scope_type in ADMIN_ONLY_SCOPES or s.object_id is None:
        return False
    otype = s.scope_type
    oid = s.object_id
    if otype == "ip":
        from app.models.address import IPAddress
        ip = await session.get(IPAddress, oid)
        if ip is None:
            return False
        otype, oid = "subnet", ip.subnet_id
    level = await get_object_permission(session, user=user, object_type=otype, object_id=oid)
    return has_permission(level, "read")


async def _scopes(session: AsyncSession, user: Any, items: list[ScopeIn]) -> list[MaintenanceScope]:
    out = []
    for s in items:
        sc, perm = await _scope_model(session, s)
        if not await _may_write(session, user, perm):
            raise _err(403, "maintenance_scope_forbidden", "沒有範圍內物件的修改權限，不能替它設定維護",
                       target=sc.label or s.scope_type)
        out.append(sc)
    return out


async def _preview(session: AsyncSession, scopes: list[MaintenanceScope]) -> dict[str, Any]:
    w = MaintenanceWindow(id=uuid.uuid4(), title="preview", behavior="mute", schedule_type="once")
    idx = svc.Index(windows={w.id: w})
    for s in scopes:
        s.window_id = w.id
    await svc._expand(session, idx, scopes)
    ranges = sum(net.num_addresses for net, _ in idx.networks) if any(
        s.scope_type == "ip_range" for s in scopes) else 0
    # IP 數：登記在 IPAM 的；IP 範圍另外算位址數（範圍裡沒登記的位址也會被靜音，例如未授權 IP）
    return {"ips": len(idx.ip_ids), "subnets": len(idx.subnet_ids), "devices": len(idx.device_ids),
            "integrations": len(idx.integrations), "agents": len(idx.agents), "range_addresses": int(ranges)}


def _validate_schedule(sch: ScheduleIn, max_hours: int, now: datetime) -> dict[str, Any]:
    if sch.type == "once":
        if not (sch.starts_at and sch.ends_at):
            raise _err(422, "maintenance_time_required", "請填開始與結束時間")
        start, end = sch.starts_at, sch.ends_at
        start = start if start.tzinfo else start.replace(tzinfo=svc.tz())
        end = end if end.tzinfo else end.replace(tzinfo=svc.tz())
        if end <= start:
            raise _err(422, "maintenance_end_before_start", "結束時間要晚於開始時間")
        if end <= now:
            raise _err(422, "maintenance_in_past", "結束時間已經過了")
        if end - start > timedelta(hours=max_hours):
            raise _err(422, "maintenance_too_long", "一次維護最長 {hours} 小時", hours=max_hours)
        return {"schedule_type": "once", "starts_at": start, "ends_at": end, "weekdays": None, "start_time": None,
                "end_time": None, "valid_from": None, "valid_until": None}
    if not (sch.weekdays and sch.start_time and sch.end_time and sch.valid_until):
        raise _err(422, "maintenance_weekly_incomplete", "每週例行要選星期、時段與到期日")
    today = svc.today_local()
    if sch.valid_until < today:
        raise _err(422, "maintenance_in_past", "到期日已經過了")
    if sch.valid_until > today + timedelta(days=366):
        raise _err(422, "maintenance_weekly_too_far", "每週例行的到期日最多一年")
    if sch.start_time == sch.end_time:
        raise _err(422, "maintenance_end_before_start", "結束時間要晚於開始時間")
    return {"schedule_type": "weekly", "starts_at": None, "ends_at": None, "weekdays": sorted(set(sch.weekdays)),
            "start_time": sch.start_time, "end_time": sch.end_time, "valid_from": sch.valid_from,
            "valid_until": sch.valid_until}


async def _out(session: AsyncSession, w: MaintenanceWindow, now: datetime, *,
               scopes: list[MaintenanceScope] | None = None) -> dict[str, Any]:
    if scopes is None:
        scopes = list((await session.execute(select(MaintenanceScope).where(MaintenanceScope.window_id == w.id)))
                      .scalars())
    occ = svc.occurrence(w, now)
    nxt = occ or svc.next_occurrence(w, now)
    n_sup = int(await session.scalar(select(func.count()).select_from(MaintenanceSuppression)
                                     .where(MaintenanceSuppression.window_id == w.id)) or 0)
    return {
        "id": str(w.id), "title": w.title, "note": w.note, "ticket": w.ticket, "behavior": w.behavior,
        "schedule_type": w.schedule_type, "starts_at": w.starts_at, "ends_at": w.ends_at,
        "weekdays": w.weekdays, "start_time": w.start_time.strftime("%H:%M") if w.start_time else None,
        "end_time": w.end_time.strftime("%H:%M") if w.end_time else None,
        "valid_from": w.valid_from, "valid_until": w.valid_until,
        "source": w.source, "source_ref": w.source_ref, "created_by": str(w.created_by) if w.created_by else None,
        "status": "active" if occ else ("upcoming" if nxt else "past"),
        "current_start": occ[0] if occ else None, "current_end": occ[1] if occ else None,
        "next_start": nxt[0] if nxt and not occ else None, "next_end": nxt[1] if nxt and not occ else None,
        "suppressed": n_sup,
        "scopes": [{"scope_type": s.scope_type, "object_id": str(s.object_id) if s.object_id else None,
                    "integration_kind": s.integration_kind,
                    "range": f"{s.range_start}-{s.range_end}" if s.range_start else None, "label": s.label}
                   for s in scopes],
    }


async def _visible(session: AsyncSession, user: Any, w: MaintenanceWindow, scopes: list[MaintenanceScope]) -> bool:
    if user.is_admin or w.created_by == user.id:
        return True
    for s in scopes:
        if await _may_read_scope(session, user, s):
            return True
    return False


async def _get(session: AsyncSession, user: Any, wid: uuid.UUID, *, write: bool = False) -> MaintenanceWindow:
    w = await session.get(MaintenanceWindow, wid)
    if w is None:
        raise _err(404, "maintenance_not_found", "找不到這個維護排程")
    scopes = list((await session.execute(select(MaintenanceScope).where(MaintenanceScope.window_id == w.id))).scalars())
    if not await _visible(session, user, w, scopes):
        raise _err(404, "maintenance_not_found", "找不到這個維護排程")
    if write:
        if w.source != "manual":
            raise _err(409, "maintenance_readonly_source", "這個維護來自 {source}，請在那邊修改", source=w.source)
        for s in scopes:
            perm = "admin" if s.scope_type in ADMIN_ONLY_SCOPES else (
                f"{s.scope_type}:{s.object_id}" if s.scope_type != "ip" else None)
            if perm is None:
                from app.models.address import IPAddress
                ip = await session.get(IPAddress, s.object_id)
                perm = f"subnet:{ip.subnet_id}" if ip else "admin"
            if not await _may_write(session, user, perm):
                raise _err(403, "maintenance_scope_forbidden", "沒有範圍內物件的修改權限，不能替它設定維護",
                           target=s.label or s.scope_type)
    return w


# ─────────────────── 端點 ───────────────────

@router.get("")
async def list_windows(
    user: CurrentUser, session: Annotated[AsyncSession, Depends(get_session)],
    status: Literal["active", "upcoming", "past", "all"] = Query("all"),
    limit: int = Query(200, ge=1, le=500),
) -> dict[str, Any]:
    now = datetime.now(UTC)
    rows = list((await session.execute(
        select(MaintenanceWindow).order_by(MaintenanceWindow.created_at.desc()).limit(2000))).scalars())
    scopes: dict[uuid.UUID, list[MaintenanceScope]] = {}
    if rows:
        for s in (await session.execute(select(MaintenanceScope).where(
                MaintenanceScope.window_id.in_([w.id for w in rows])))).scalars():   # bounded: ≤ 2000 筆維護
            scopes.setdefault(s.window_id, []).append(s)
    items = []
    for w in rows:
        if not await _visible(session, user, w, scopes.get(w.id, [])):
            continue
        if status != "all" and svc.status_of(w, now) != status:
            continue
        items.append(await _out(session, w, now, scopes=scopes.get(w.id, [])))
        if len(items) >= limit:
            break
    order = {"active": 0, "upcoming": 1, "past": 2}
    items.sort(key=lambda x: (order[x["status"]], str(x.get("current_end") or x.get("next_start") or "")))
    return {"items": items, "total": len(items)}


@router.get("/summary")
async def summary(user: CurrentUser, session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, Any]:
    """頂端列的「維護中 N」：目前進行中、24 小時內開始的。"""
    now = datetime.now(UTC)
    idx = await svc.build_index(session, now)
    active = []
    for w in idx.windows.values():
        scopes = list((await session.execute(select(MaintenanceScope).where(MaintenanceScope.window_id == w.id)))
                      .scalars())
        if await _visible(session, user, w, scopes):
            occ = svc.occurrence(w, now)
            active.append({"id": str(w.id), "title": w.title, "behavior": w.behavior, "until": occ[1] if occ else None})
    return {"active": len(active), "items": sorted(active, key=lambda x: str(x["until"]))}


@router.get("/active")
async def active_for(
    user: CurrentUser, session: Annotated[AsyncSession, Depends(get_session)],
    object_type: ScopeType = Query(...), object_id: uuid.UUID = Query(...),
    integration_kind: str | None = Query(None, max_length=32),
) -> list[dict[str, Any]]:
    """這個物件現在被哪些維護涵蓋（物件頁的橫幅與一鍵維護按鈕用）。"""
    out = []
    for w, until in await svc.active_for_object(session, object_type, object_id,
                                                integration_kind=integration_kind):
        out.append({"id": str(w.id), "title": w.title, "behavior": w.behavior, "source": w.source,
                    "until": until, "schedule_type": w.schedule_type,
                    "can_manage": user.is_admin or w.created_by == user.id})
    return out


@router.post("/preview")
async def preview(payload: Annotated[list[ScopeIn], Field(min_length=1, max_length=50)], user: CurrentUser,
                  session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, Any]:
    scopes = [(await _scope_model(session, s))[0] for s in payload]
    out = await _preview(session, scopes)
    out["large_threshold"] = (await get_settings(session))["large_threshold"]
    return out


async def _create(session: AsyncSession, user: Any, request: Request, payload: WindowIn) -> dict[str, Any]:
    now = datetime.now(UTC)
    cfg = await get_settings(session)
    sched = _validate_schedule(payload.schedule, cfg["max_hours"], now)
    scopes = await _scopes(session, user, payload.scopes)
    counts = await _preview(session, [MaintenanceScope(**{c: getattr(s, c) for c in (
        "scope_type", "object_id", "integration_kind", "range_start", "range_end", "label")}) for s in scopes])
    if counts["ips"] > cfg["large_threshold"] and not payload.confirm_large:
        raise _err(409, "maintenance_scope_large", "範圍很大（{n} 個 IP），請確認後再送出", n=counts["ips"])
    w = MaintenanceWindow(title=payload.title.strip(), note=payload.note, ticket=payload.ticket,
                          behavior=payload.behavior, created_by=user.id, source="manual", **sched)
    session.add(w)
    await session.flush()
    for s in scopes:
        s.window_id = w.id
        session.add(s)
    await session.flush()
    await _audit(session, user, request, str(w.id), "create", _audit_view(w, scopes))
    await session.commit()
    svc.invalidate(session)
    return await _out(session, w, now)


@router.post("", status_code=201)
async def create_window(payload: WindowIn, user: CurrentUser, request: Request,
                        session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, Any]:
    return await _create(session, user, request, payload)


@router.post("/quick", status_code=201)
async def quick(payload: QuickIn, user: CurrentUser, request: Request,
                session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, Any]:
    """一鍵維護：從現在開始 N 分鐘。標題沒填就用「範圍名稱 維護」。"""
    sc, _perm = await _scope_model(session, payload.scope)
    now = datetime.now(UTC)
    body = WindowIn(title=(payload.title or f"{sc.label or payload.scope.scope_type} 維護")[:200], note=payload.note,
                    ticket=payload.ticket, behavior="mute",
                    schedule=ScheduleIn(type="once", starts_at=now - timedelta(seconds=1),
                                        ends_at=now + timedelta(minutes=payload.minutes)),
                    scopes=[payload.scope], confirm_large=True)
    return await _create(session, user, request, body)


@router.get("/{window_id}")
async def get_window(window_id: uuid.UUID, user: CurrentUser,
                     session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, Any]:
    w = await _get(session, user, window_id)
    out = await _out(session, w, datetime.now(UTC))
    scopes = list((await session.execute(select(MaintenanceScope).where(MaintenanceScope.window_id == w.id))).scalars())
    out["counts"] = await _preview(session, scopes)
    out["can_manage"] = user.is_admin or w.created_by == user.id
    return out


@router.patch("/{window_id}")
async def update_window(window_id: uuid.UUID, payload: WindowIn, user: CurrentUser, request: Request,
                        session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, Any]:
    w = await _get(session, user, window_id, write=True)
    now = datetime.now(UTC)
    cfg = await get_settings(session)
    sched = _validate_schedule(payload.schedule, cfg["max_hours"], now)
    scopes = await _scopes(session, user, payload.scopes)
    old_scopes = list((await session.execute(select(MaintenanceScope).where(MaintenanceScope.window_id == w.id)))
                      .scalars())
    before = _audit_view(w, old_scopes)
    for k, v in sched.items():
        setattr(w, k, v)
    w.title, w.note, w.ticket, w.behavior = payload.title.strip(), payload.note, payload.ticket, payload.behavior
    for old in old_scopes:
        await session.delete(old)
    for s in scopes:
        s.window_id = w.id
        session.add(s)
    after = _audit_view(w, scopes)
    changed = {k: v for k, v in after.items() if before.get(k) != v}
    await _audit(session, user, request, str(w.id), "update",
                 {"before": {k: before[k] for k in changed}, "changes": changed} if changed else {"title": w.title})
    await session.commit()
    svc.invalidate(session)
    return await _out(session, w, now)


@router.delete("/{window_id}", status_code=204)
async def delete_window(window_id: uuid.UUID, user: CurrentUser, request: Request,
                        session: Annotated[AsyncSession, Depends(get_session)]) -> None:
    w = await _get(session, user, window_id, write=True)
    await _audit(session, user, request, str(w.id), "delete", {"title": w.title})
    await session.delete(w)
    await session.commit()
    svc.invalidate(session)


@router.post("/{window_id}/end")
async def end_now(window_id: uuid.UUID, user: CurrentUser, request: Request,
                  session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, Any]:
    """提前結束：一次性的把結束時間改成現在；每週例行的只略過這一次。下一輪排程會發摘要並補發。"""
    w = await _get(session, user, window_id, write=True)
    now = datetime.now(UTC)
    occ = svc.occurrence(w, now)
    if occ is None:
        raise _err(409, "maintenance_not_active", "這個維護現在沒有在進行")
    if w.schedule_type == "once":
        w.ends_at = max(now, (w.starts_at or now) + timedelta(seconds=1))
    else:
        w.skip_until = occ[1]
    await _audit(session, user, request, str(w.id), "end",
                 {"title": w.title, "at": f"{now.astimezone(svc.tz()):%Y-%m-%d %H:%M}"})
    await session.commit()
    svc.invalidate(session)
    return await _out(session, w, now)


@router.post("/{window_id}/extend")
async def extend(window_id: uuid.UUID, payload: ExtendIn, user: CurrentUser, request: Request,
                 session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, Any]:
    """延長：一次性的延後結束時間（總長不超過上限）；每週例行的只延長這一次。"""
    w = await _get(session, user, window_id, write=True)
    now = datetime.now(UTC)
    occ = svc.occurrence(w, now)
    if occ is None:
        raise _err(409, "maintenance_not_active", "這個維護現在沒有在進行")
    new_end = occ[1] + timedelta(minutes=payload.minutes)
    cfg = await get_settings(session)
    if new_end - occ[0] > timedelta(hours=cfg["max_hours"]):
        raise _err(422, "maintenance_too_long", "一次維護最長 {hours} 小時", hours=cfg["max_hours"])
    if w.schedule_type == "once":
        w.ends_at = new_end
    else:
        w.extended_until = new_end
    await _audit(session, user, request, str(w.id), "extend",
                 {"title": w.title, "minutes": payload.minutes, "until": f"{new_end.astimezone(svc.tz()):%Y-%m-%d %H:%M}"})
    await session.commit()
    svc.invalidate(session)
    return await _out(session, w, now)


@router.get("/{window_id}/suppressed")
async def suppressed(window_id: uuid.UUID, user: CurrentUser,
                     session: Annotated[AsyncSession, Depends(get_session)],
                     limit: int = Query(500, ge=1, le=2000)) -> list[dict[str, Any]]:
    w = await _get(session, user, window_id)
    rows = (await session.execute(select(MaintenanceSuppression).where(MaintenanceSuppression.window_id == w.id)
                                  .order_by(MaintenanceSuppression.first_at.desc()).limit(limit))).scalars()
    return [{"event": r.event, "label": r.label, "key": r.key if user.is_admin else None, "first_at": r.first_at,
             "count": r.count, "occurrence": r.occurrence} for r in rows]

