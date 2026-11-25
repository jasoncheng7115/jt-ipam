"""Cabling / Power / VPN endpoints。"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import Field, model_validator
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin, require_global_read
from app.core.audit import append_audit
from app.core.db import get_session
from app.core.sqlin import in_values
from app.core.ui_error import ui_detail
from app.models.device import Device
from app.models.librenms import FDBEntry, LibreNMSDevice
from app.models.physical import (
    Cable,
    CableSuggestion,
    CableTermination,
    DevicePort,
    DevicePowerPort,
    DevicePowerProfile,
    PowerFeed,
    PowerOutlet,
    PowerPanel,
    VPNTunnel,
)
from app.schemas.base import Paginated, StrictModel
from app.services.device_port_filter import is_pseudo_iface, prune_pseudo_ports
from app.services.device_port_filter import load_patterns as load_pseudo_patterns
from app.services.physical_links import (
    ensure_cable_ports_free,
    ensure_outlet_free,
    ensure_port_free,
    lock_ports,
    set_peer,
)
from app.services.power_scenario import preview_failure
from app.services.power_summary import feed_detail, feed_summaries, rack_summary

router = APIRouter(tags=["physical"], dependencies=[Depends(require_global_read)])


async def _audit(
    session: AsyncSession, *, user: CurrentUser, request: Request,
    object_type: str, object_id: str | None, action: str, diff: dict[str, Any] | None,
) -> None:
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type=object_type, object_id=object_id, action=action,
        diff=diff,
        request_id=getattr(request.state, "request_id", None),
    )


# ─────────────────── Cables ───────────────────


class CableRead(StrictModel):
    id: uuid.UUID
    label: str | None
    type: str | None
    color: str | None
    length_m: float | None
    description: str | None
    status: str
    # manual＝人建的；auto＝依證據自動建立（services/cable_inference），evidence 講得出依據
    source: str = "manual"
    evidence: dict[str, Any] | None = None
    verified_at: datetime | None = None
    a_end: str | None = None       # 「裝置 / 連接埠」可讀字串
    b_end: str | None = None
    a_device_id: uuid.UUID | None = None
    b_device_id: uuid.UUID | None = None
    # 兩端裝置所屬單位 / 機房（供清單篩選用；任一端符合即算）
    a_customer_id: uuid.UUID | None = None
    b_customer_id: uuid.UUID | None = None
    a_location_id: uuid.UUID | None = None
    b_location_id: uuid.UUID | None = None
    # 追線從這一端出發用；機櫃給清單篩選（機房是有效位置：機櫃的地點優先）
    a_port_id: uuid.UUID | None = None
    b_port_id: uuid.UUID | None = None
    a_rack_id: uuid.UUID | None = None
    b_rack_id: uuid.UUID | None = None


class CableWrite(StrictModel):
    label: Annotated[str | None, Field(max_length=128)] = None
    type: Annotated[str | None, Field(max_length=32)] = None
    color: Annotated[str | None, Field(max_length=16)] = None
    length_m: Annotated[float | None, Field(ge=0, le=10_000)] = None
    description: Annotated[str | None, Field(max_length=1024)] = None
    status: Literal["planned", "connected", "decommissioned"] = "connected"


class CableConnect(CableWrite):
    """一次建好一條線與兩端（同一個交易）：以前前端要打三支 API，一端失敗就留下半條線。"""
    a_port_id: uuid.UUID
    b_port_id: uuid.UUID

    @model_validator(mode="after")
    def _two_ports(self) -> CableConnect:
        if self.a_port_id == self.b_port_id:
            raise ValueError("a_port_id and b_port_id must be different ports")
        return self


class CableTerminationRead(StrictModel):
    id: uuid.UUID
    cable_id: uuid.UUID
    side: str
    object_type: str
    object_id: uuid.UUID
    port_label: str | None


class CableTerminationWrite(StrictModel):
    cable_id: uuid.UUID
    side: Annotated[str, Field(pattern=r"^[AB]$")]
    object_type: Annotated[str, Field(min_length=1, max_length=32)]
    object_id: uuid.UUID
    port_label: Annotated[str | None, Field(max_length=64)] = None


# ─────────────────── 佈線自動推斷（services/cable_inference）───────────────────
class CableInferenceConfig(StrictModel):
    enabled: bool
    last_run_at: str | None = None
    last_summary: dict[str, Any] | None = None


class CableInferenceUpdate(StrictModel):
    enabled: bool


class CableSuggestionRead(StrictModel):
    id: uuid.UUID
    kind: str
    reason: str | None
    status: str
    evidence: dict[str, Any] | None
    a_port_id: uuid.UUID
    b_port_id: uuid.UUID
    a_device_id: uuid.UUID | None = None
    b_device_id: uuid.UUID | None = None
    a_end: str | None = None
    b_end: str | None = None
    first_seen_at: datetime
    last_seen_at: datetime
    dismissed_at: datetime | None = None


@router.get("/cables/inference", response_model=CableInferenceConfig, dependencies=[Depends(require_admin)])
async def get_cable_inference(session: Annotated[AsyncSession, Depends(get_session)]) -> CableInferenceConfig:
    from app.services.cable_inference import get_config
    return CableInferenceConfig(**await get_config(session))


@router.put("/cables/inference", response_model=CableInferenceConfig, dependencies=[Depends(require_admin)])
async def put_cable_inference(
    payload: CableInferenceUpdate, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CableInferenceConfig:
    from app.services.cable_inference import set_config
    cfg = await set_config(session, enabled=payload.enabled, user_id=user.id)
    await _audit(session, user=user, request=request, object_type="cable_inference", object_id=None,
                 action="update", diff={"enabled": payload.enabled})
    await session.commit()
    return CableInferenceConfig(**cfg)


@router.post("/cables/inference/run", dependencies=[Depends(require_admin)])
async def run_cable_inference(
    user: CurrentUser, request: Request, session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """立即跑一輪（不管開關：管理員按了就是要看結果）。"""
    from app.services.cable_inference import run
    st = await run(session)
    await _audit(session, user=user, request=request, object_type="cable_inference", object_id=None,
                 action="run", diff=st)
    await session.commit()
    return st


async def _suggestion_rows(session: AsyncSession, rows: list[CableSuggestion]) -> list[CableSuggestionRead]:
    pids = {r.a_port_id for r in rows} | {r.b_port_id for r in rows}
    ports = {p.id: p for p in (await session.execute(
        select(DevicePort).where(in_values(DevicePort.id, pids)))).scalars().all()} if pids else {}
    devs: dict[uuid.UUID, str] = {}
    if ports:
        devs = {i: n for i, n in (await session.execute(select(Device.id, Device.name).where(
            in_values(Device.id, {p.device_id for p in ports.values()})))).all()}
    out = []
    for r in rows:
        pa, pb = ports.get(r.a_port_id), ports.get(r.b_port_id)
        out.append(CableSuggestionRead(
            id=r.id, kind=r.kind, reason=r.reason, status=r.status, evidence=r.evidence,
            a_port_id=r.a_port_id, b_port_id=r.b_port_id,
            a_device_id=pa.device_id if pa else None, b_device_id=pb.device_id if pb else None,
            a_end=f"{devs.get(pa.device_id, '?')}@{pa.name}" if pa else None,
            b_end=f"{devs.get(pb.device_id, '?')}@{pb.name}" if pb else None,
            first_seen_at=r.first_seen_at, last_seen_at=r.last_seen_at, dismissed_at=r.dismissed_at))
    return out


@router.get("/cables/suggestions", dependencies=[Depends(require_admin)])
async def list_cable_suggestions(
    session: Annotated[AsyncSession, Depends(get_session)],
    status: Literal["open", "dismissed"] = "open",
    page: int = Query(1, ge=1, le=10_000), page_size: int = Query(200, ge=1, le=500),
) -> dict[str, Any]:
    base = select(CableSuggestion).where(CableSuggestion.status == status)
    total = int(await session.scalar(select(func.count()).select_from(base.subquery())) or 0)
    rows = list((await session.execute(base.order_by(CableSuggestion.last_seen_at.desc())
                                       .offset((page - 1) * page_size).limit(page_size))).scalars().all())
    return {"items": [r.model_dump(mode="json") for r in await _suggestion_rows(session, rows)],
            "total": total, "page": page, "page_size": page_size}


async def _suggestion_or_404(session: AsyncSession, sid: uuid.UUID) -> CableSuggestion:
    s = await session.get(CableSuggestion, sid)
    if s is None:
        raise HTTPException(404, detail="Suggestion not found")
    return s


@router.post("/cables/suggestions/{sid}/accept", response_model=CableRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def accept_cable_suggestion(
    sid: uuid.UUID, user: CurrentUser, request: Request, session: Annotated[AsyncSession, Depends(get_session)],
    replace: bool = False,
) -> CableRead:
    """接受建議：建一條「人確認過」的線（source=manual，留著依據）。埠被占用就 409，不會變成兩條。

    `replace=true`（「接受並取代」）：先移除兩端埠上已接線的線（例如人工接到 vmbr 的那條），每條各記一筆刪除稽核。"""
    s = await _suggestion_or_404(session, sid)
    ports = await lock_ports(session, [s.a_port_id, s.b_port_id])
    for pid in (s.a_port_id, s.b_port_id):
        if pid not in ports:
            raise HTTPException(422, detail=ui_detail("port_not_found", "找不到這個連接埠"))
    if replace:
        occupied = (await session.execute(
            select(Cable).join(CableTermination, CableTermination.cable_id == Cable.id)
            .where(CableTermination.object_type == "device_port",
                   in_values(CableTermination.object_id, [s.a_port_id, s.b_port_id]),
                   Cable.status == "connected"))).scalars().unique().all()
        for old in occupied:
            await _audit(session, user=user, request=request, object_type="cable", object_id=str(old.id),
                         action="delete", diff={"replaced_by_suggestion": str(sid), "label": old.label,
                                                "source": old.source})
            await session.execute(delete(CableTermination).where(CableTermination.cable_id == old.id))
            await session.delete(old)
        await session.flush()
    for pid in (s.a_port_id, s.b_port_id):
        await ensure_port_free(session, ports[pid])
    obj = Cable(status="connected", source="manual", evidence=s.evidence, verified_at=datetime.now(UTC))
    session.add(obj)
    await session.flush()
    for side, pid in (("A", s.a_port_id), ("B", s.b_port_id)):
        session.add(CableTermination(cable_id=obj.id, side=side, object_type="device_port", object_id=pid))
    await session.delete(s)
    await session.flush()
    await _audit(session, user=user, request=request, object_type="cable", object_id=str(obj.id),
                 action="create", diff={"from_suggestion": str(sid), "evidence": s.evidence})
    await session.commit()
    await session.refresh(obj)
    return CableRead.model_validate(obj)


@router.post("/cables/suggestions/{sid}/dismiss", dependencies=[Depends(require_admin)])
async def dismiss_cable_suggestion(
    sid: uuid.UUID, user: CurrentUser, request: Request, session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    s = await _suggestion_or_404(session, sid)
    s.status, s.dismissed_by, s.dismissed_at = "dismissed", user.id, datetime.now(UTC)
    await session.flush()
    await _audit(session, user=user, request=request, object_type="cable_suggestion", object_id=str(sid),
                 action="dismiss", diff={"kind": s.kind, "reason": s.reason})
    await session.commit()
    return (await _suggestion_rows(session, [s]))[0].model_dump(mode="json")


@router.post("/cables/suggestions/{sid}/restore", dependencies=[Depends(require_admin)])
async def restore_cable_suggestion(
    sid: uuid.UUID, user: CurrentUser, request: Request, session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """取消忽略：刪掉這筆記錄，下一輪推斷又會照證據處理這對埠。"""
    s = await _suggestion_or_404(session, sid)
    await session.delete(s)
    await _audit(session, user=user, request=request, object_type="cable_suggestion", object_id=str(sid),
                 action="restore", diff={"kind": s.kind})
    await session.commit()
    return {"ok": True}


@router.get("/cables", response_model=Paginated[CableRead])
async def list_cables(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(1, ge=1, le=10_000), page_size: int = Query(50, ge=1, le=500),
    source: Literal["manual", "auto"] | None = None,
) -> Paginated[CableRead]:
    base = select(Cable)
    if source is not None:
        base = base.where(Cable.source == source)
    rows = list((await session.execute(
        base.order_by(Cable.created_at.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    total = int(await session.scalar(select(func.count()).select_from(base.subquery())) or 0)

    # 兩端「裝置 / 連接埠」解析：把各 cable 的 A/B termination 還原成可讀字串
    cable_ids = [r.id for r in rows]
    ends: dict[uuid.UUID, dict[str, Any]] = {cid: {} for cid in cable_ids}
    if cable_ids:
        terms = list((await session.execute(
            select(CableTermination).where(CableTermination.cable_id.in_(cable_ids))  # bounded: one page (≤ 500)
        )).scalars().all())
        # 預載相關 device_port → device 名稱
        port_ids = [t.object_id for t in terms if t.object_type == "device_port"]
        dev_ids = [t.object_id for t in terms if t.object_type == "device"]
        ports: dict[uuid.UUID, DevicePort] = {}
        if port_ids:
            ports = {p.id: p for p in (await session.execute(
                select(DevicePort).where(DevicePort.id.in_(port_ids))  # bounded: ends of one page of cables
            )).scalars().all()}
        # dev_meta: id → (name, customer_id, 有效位置, rack_id)。機房用有效位置：在機櫃裡就算機櫃的地點
        # （交接包 physical-v1.2 第 9 節；機房與機櫃條件才會落在同一端）
        from app.models.location import Rack
        dev_meta: dict[uuid.UUID, tuple[str, uuid.UUID | None, uuid.UUID | None, uuid.UUID | None]] = {}
        need_dev = set(dev_ids) | {p.device_id for p in ports.values()}
        if need_dev:
            dev_meta = {d.id: (d.name, d.customer_id, rack_loc if d.rack_id else d.location_id, d.rack_id)
                        for d, rack_loc in (await session.execute(
                select(Device, Rack.location_id).outerjoin(Rack, Rack.id == Device.rack_id)
                .where(Device.id.in_(need_dev))  # bounded: ends of one page of cables
            )).all()}
        for t in terms:
            label = None
            device_id = None
            if t.object_type == "device_port" and t.object_id in ports:
                p = ports[t.object_id]
                device_id = p.device_id
                label = f"{dev_meta.get(p.device_id, ('?',))[0]}@{p.name}"
            elif t.object_type == "device":
                device_id = t.object_id
                label = dev_meta.get(t.object_id, (str(t.object_id)[:8],))[0]
            else:
                label = t.port_label or t.object_type
            meta = dev_meta.get(device_id) if device_id else None
            ends[t.cable_id][t.side] = {
                "label": label, "device_id": device_id,
                "customer_id": meta[1] if meta else None,
                "location_id": meta[2] if meta else None,
                "rack_id": meta[3] if meta else None,
                "port_id": t.object_id if t.object_type == "device_port" and t.object_id in ports else None,
            }

    items = []
    for r in rows:
        e = ends.get(r.id, {})
        a, b = e.get("A") or {}, e.get("B") or {}
        items.append(CableRead.model_validate(r).model_copy(update={
            "a_end": a.get("label"), "b_end": b.get("label"),
            "a_device_id": a.get("device_id"), "b_device_id": b.get("device_id"),
            "a_customer_id": a.get("customer_id"), "b_customer_id": b.get("customer_id"),
            "a_location_id": a.get("location_id"), "b_location_id": b.get("location_id"),
            "a_port_id": a.get("port_id"), "b_port_id": b.get("port_id"),
            "a_rack_id": a.get("rack_id"), "b_rack_id": b.get("rack_id"),
        }))
    return Paginated[CableRead](items=items, total=total, page=page, page_size=page_size)


@router.post("/cables", response_model=CableRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_cable(
    payload: CableWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CableRead:
    obj = Cable(**payload.model_dump())
    session.add(obj)
    await session.flush()
    await _audit(session, user=user, request=request, object_type="cable",
                 object_id=str(obj.id), action="create",
                 diff=payload.model_dump(mode="json"))
    await session.commit()
    await session.refresh(obj)
    return CableRead.model_validate(obj)


@router.post("/cables/connect", response_model=CableRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def connect_ports(
    payload: CableConnect, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CableRead:
    """建一條線並接好兩端；任一端被已接線的纜線占用就整筆不建（409 port_occupied）。"""
    ports = await lock_ports(session, [payload.a_port_id, payload.b_port_id])
    for pid in (payload.a_port_id, payload.b_port_id):
        if pid not in ports:
            raise HTTPException(422, detail=ui_detail("port_not_found", "找不到這個連接埠"))
        if payload.status == "connected":
            await ensure_port_free(session, ports[pid])
    attrs = payload.model_dump(exclude={"a_port_id", "b_port_id"})
    obj = Cable(**attrs)
    session.add(obj)
    await session.flush()
    for side, pid in (("A", payload.a_port_id), ("B", payload.b_port_id)):
        session.add(CableTermination(cable_id=obj.id, side=side, object_type="device_port", object_id=pid))
    await session.flush()
    await _audit(session, user=user, request=request, object_type="cable",
                 object_id=str(obj.id), action="create",
                 diff=payload.model_dump(mode="json"))
    await session.commit()
    await session.refresh(obj)
    return CableRead.model_validate(obj)


@router.patch("/cables/{cable_id}", response_model=CableRead,
              dependencies=[Depends(require_admin)])
async def update_cable(
    cable_id: uuid.UUID, payload: CableWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CableRead:
    obj = await session.get(Cable, cable_id)
    if obj is None:
        raise HTTPException(404, detail="Cable not found")
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("status") == "connected" and obj.status != "connected":
        # 規劃中 / 已除役改成已接線：兩端可能已經被別條線接走了
        await ensure_cable_ports_free(session, cable_id)
    for k, v in changes.items():
        setattr(obj, k, v)
    if changes and obj.source == "auto":
        obj.source = "manual"     # 人改過＝確認過：之後系統不再更新或撤掉它
    await session.flush()
    await _audit(session, user=user, request=request, object_type="cable",
                 object_id=str(obj.id), action="update",
                 diff=payload.model_dump(mode="json", exclude_unset=True))
    await session.commit()
    await session.refresh(obj)
    return CableRead.model_validate(obj)


@router.delete("/cables/{cable_id}", status_code=204,
               dependencies=[Depends(require_admin)])
async def delete_cable(
    cable_id: uuid.UUID, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    obj = await session.get(Cable, cable_id)
    if obj is None:
        raise HTTPException(404, detail="Cable not found")
    was_auto = obj.source == "auto"
    # 自動線被刪：記住這對埠，下一輪不再自動建回來（cable_inference.dismiss_cable 一併刪掉線與兩端）
    from app.services.cable_inference import dismiss_cable
    await dismiss_cable(session, cable_id, user_id=user.id)
    await _audit(session, user=user, request=request, object_type="cable",
                 object_id=str(cable_id), action="delete", diff={"source": "auto"} if was_auto else None)
    await session.commit()


@router.post("/cable-terminations", response_model=CableTerminationRead,
             status_code=201, dependencies=[Depends(require_admin)])
async def add_termination(
    payload: CableTerminationWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CableTerminationRead:
    if payload.object_type == "device_port":
        cable = await session.get(Cable, payload.cable_id)
        port = (await lock_ports(session, [payload.object_id])).get(payload.object_id)
        if port is None:
            raise HTTPException(422, detail=ui_detail("port_not_found", "找不到這個連接埠"))
        if cable is not None and cable.status == "connected":
            await ensure_port_free(session, port, exclude_cable_id=cable.id)
    obj = CableTermination(**payload.model_dump())
    session.add(obj)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="Side already terminated for this cable") from exc
    await _audit(session, user=user, request=request, object_type="cable_termination",
                 object_id=str(obj.id), action="create",
                 diff=payload.model_dump(mode="json"))
    await session.commit()
    await session.refresh(obj)
    return CableTerminationRead.model_validate(obj)


@router.get("/physical-check", dependencies=[Depends(require_admin)])
async def get_physical_check(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """佈線與供電的既有問題（只讀）：重複占用、不對稱配對、半條線、舊式插座直連。"""
    from app.services.physical_check import physical_check
    return await physical_check(session)


@router.get("/cables/{cable_id}/trace")
async def trace_cable(
    cable_id: uuid.UUID,
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:  # type: ignore[type-arg]
    """傳回 cable 的 A/B 端 termination。Phase 3.5 將擴展為 multi-cable hop trace。"""
    cable = await session.get(Cable, cable_id)
    if cable is None:
        raise HTTPException(404, detail="Cable not found")
    terms = list((await session.execute(
        select(CableTermination).where(CableTermination.cable_id == cable_id)
    )).scalars().all())
    return {
        "cable": {"id": str(cable.id), "label": cable.label, "type": cable.type,
                  "status": cable.status},
        "terminations": [
            {
                "side": t.side,
                "object_type": t.object_type,
                "object_id": str(t.object_id),
                "port_label": t.port_label,
            }
            for t in sorted(terms, key=lambda x: x.side)
        ],
    }


# ─────────────────── Device ports + Cable Trace ───────────────────


class DevicePortRead(StrictModel):
    id: uuid.UUID
    device_id: uuid.UUID
    name: str
    type: str
    peer_port_id: uuid.UUID | None
    position: int | None
    description: str | None
    link: str | None = None   # 對端標籤（已接纜線時）：例「switch-003 · eth1/0/24」
    # 接在這個埠的線（面板格狀檢視顯示正面、背面各自的線標）；有多條時優先已接線的
    cable_id: uuid.UUID | None = None
    cable_label: str | None = None
    cable_status: str | None = None
    cable_source: str | None = None      # auto＝佈線自動推斷建立的線
    mac_address: str | None = None   # 此埠自身的實體 MAC（ifPhysAddress）
    mac_vendor: str | None = None    # MAC 的 OUI 廠商（清單的 MAC 欄第二行，與 IP 清單一致）


class DevicePortWrite(StrictModel):
    device_id: uuid.UUID
    name: Annotated[str, Field(min_length=1, max_length=64)]
    type: str = "network"
    peer_port_id: uuid.UUID | None = None
    position: int | None = None
    description: Annotated[str | None, Field(max_length=1024)] = None


class DevicePortUpdate(StrictModel):
    name: Annotated[str | None, Field(min_length=1, max_length=64)] = None
    type: str | None = None
    peer_port_id: uuid.UUID | None = None
    position: int | None = None
    description: Annotated[str | None, Field(max_length=1024)] = None
    # 目標埠已經跟第三個埠配對時，要明確帶 true 才重配（不然 409 port_peer_taken）
    replace_peer: bool = False


@router.get("/device-ports", response_model=list[DevicePortRead])
async def list_device_ports(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    device_id: uuid.UUID = Query(...),
) -> list[DevicePortRead]:
    rows = list((await session.execute(
        select(DevicePort).where(DevicePort.device_id == device_id)
        .order_by(DevicePort.position, DevicePort.name)
    )).scalars().all())
    out = [DevicePortRead.model_validate(r) for r in rows]
    if not rows:
        return out
    from app.services.oui import mac_prefix, vendor_map
    vmap = await vendor_map(session, [r.mac_address for r in rows])
    for o in out:
        pfx = mac_prefix(o.mac_address)
        o.mac_vendor = vmap.get(pfx) if pfx else None
    by_id = {r.id: r for r in out}
    port_ids = list(by_id)

    # ── 纜線與對端（link）── 批次查：一台裝置可以有上萬個埠（issue #47），以前每個端點各查一次
    rows = (await session.execute(
        select(CableTermination.object_id, CableTermination.id, Cable.id, Cable.label, Cable.status, Cable.source)
        .join(Cable, Cable.id == CableTermination.cable_id)
        .where(CableTermination.object_type == "device_port", in_values(CableTermination.object_id, port_ids))
    )).all()
    # 同一個埠有多條線（舊資料）：已接線 > 規劃中 > 已除役
    rank = {"connected": 0, "planned": 1, "decommissioned": 2}
    best: dict[uuid.UUID, Any] = {}
    for r in rows:
        cur = best.get(r[0])
        if cur is None or rank.get(r[4], 3) < rank.get(cur[4], 3):
            best[r[0]] = r
    others: dict[uuid.UUID, Any] = {}
    if best:
        for o in (await session.execute(select(CableTermination).where(
                in_values(CableTermination.cable_id, [r[2] for r in best.values()])))).scalars():
            others.setdefault(o.cable_id, []).append(o)
    far_ids = [o.object_id for lst in others.values() for o in lst if o.object_type == "device_port"]
    far: dict[uuid.UUID, Any] = {}
    if far_ids:
        far = {pid: (pname, dname) for pid, pname, dname in (await session.execute(
            select(DevicePort.id, DevicePort.name, Device.name).join(Device, Device.id == DevicePort.device_id)
            .where(in_values(DevicePort.id, far_ids)))).all()}
    for pid, r in best.items():
        item = by_id.get(pid)
        if item is None:
            continue
        item.cable_id, item.cable_label, item.cable_status, item.cable_source = r[2], r[3], r[4], r[5]
        other = next((o for o in others.get(r[2], []) if o.id != r[1]), None)
        if other is None:
            continue
        if other.object_type == "device_port":
            pn = far.get(other.object_id)
            item.link = f"{pn[1]} · {pn[0]}" if pn else None
        else:
            item.link = f"{other.object_type} {other.port_label or ''}".strip()

    # 埠自身 MAC（mac_address）已由 model_validate 直接帶出，不再用 FDB/ARP
    return out


@router.post("/device-ports", response_model=DevicePortRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_device_port(
    payload: DevicePortWrite,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DevicePortRead:
    obj = DevicePort(**payload.model_dump(exclude={"peer_port_id"}))
    session.add(obj)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="Port name already exists on this device") from exc
    if payload.peer_port_id is not None:
        await set_peer(session, obj, payload.peer_port_id, replace=False)
        await session.flush()
    await _audit(session, user=user, request=request, object_type="device_port",
                 object_id=str(obj.id), action="create", diff={"name": obj.name})
    await session.commit()
    await session.refresh(obj)
    return DevicePortRead.model_validate(obj)


# 偽介面過濾（排程同步也用同一份，見 services/device_port_filter.py）
_is_pseudo_iface = is_pseudo_iface


@router.post("/device-ports/import", dependencies=[Depends(require_admin)])
async def import_device_ports(
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    device_id: uuid.UUID = Query(...),
) -> dict[str, Any]:
    """從整合來源把連接埠撈進來：優先 LibreNMS 介面清單(ifName，含 server/PVE 主機)，
    退回 FDB 學到的 port_name（交換器）。"""
    from app.models.librenms import LibreNMSInstance

    # 偽介面過濾設定（管理者可調）。關閉時完全不過濾也不清除。
    pseudo_res = await load_pseudo_patterns(session)
    filter_on = pseudo_res is not None

    lns_devs = list((await session.execute(
        select(LibreNMSDevice).where(LibreNMSDevice.jt_ipam_device_id == device_id)
    )).scalars().all())

    names: set[str] = set()
    name_mac: dict[str, str | None] = {}
    sources: set[str] = set()
    # 每台 LibreNMS 這次完整讀到的埠名（記來源、清掉不再回報的；與排程同步同一套規則）
    from app.services.librenms import port_origin, reconcile_librenms_ports
    per_origin: dict[str, set[str]] = {}
    origin_of: dict[str, str] = {}

    # 1) LibreNMS 介面清單（ifName）— 對 server / PVE 主機 / switch 都有效
    for d in lns_devs:
        inst = await session.get(LibreNMSInstance, d.instance_id)
        if inst is None:
            continue
        try:
            from app.services.librenms import _api_get, _norm_mac
            pdata = await _api_get(
                inst, f"/api/v0/devices/{d.legacy_device_id}/ports?columns=ifName,ifType,ifPhysAddress",
                timeout=20.0,
            )
            seen = per_origin.setdefault(port_origin(inst.id), set())
            for p in pdata.get("ports") or []:
                nm = (p.get("ifName") or "").strip()
                if nm and nm.lower() not in ("null", "unrouted vlan 1") \
                        and not (filter_on and _is_pseudo_iface(nm, p.get("ifType"), pseudo_res)):
                    names.add(nm)
                    name_mac[nm] = _norm_mac(p.get("ifPhysAddress"))
                    sources.add("librenms")
                    seen.add(nm)
                    origin_of.setdefault(nm, port_origin(inst.id))
        except Exception as exc:
            # LibreNMS 不可達/回應異常：略過此來源，改用 FDB
            logging.getLogger(__name__).debug("librenms ports fetch failed: %s", exc)

    # 2) 退回 FDB（交換器學到的 port）
    if not names and lns_devs:
        rows = (await session.execute(
            select(FDBEntry.port_name).where(
                FDBEntry.device_id.in_([d.id for d in lns_devs]),  # bounded: LibreNMS entries of one device
                FDBEntry.port_name.is_not(None),
            ).distinct()
        )).all()
        for r in rows:
            if r[0] and r[0].strip():
                names.add(r[0].strip())
                sources.add("librenms-fdb")

    existing_ports = list((await session.execute(
        select(DevicePort).where(DevicePort.device_id == device_id)
    )).scalars().all())
    existing = {p.name for p in existing_ports}

    created = 0
    for n in sorted(names):
        if n in existing:
            continue
        # FDB 學到的埠（交換器）不記來源：它們不在 LibreNMS 的介面清單裡，不該被清除規則碰到
        session.add(DevicePort(device_id=device_id, name=n, type="network", source_origin=origin_of.get(n)))
        created += 1
    await session.flush()
    removed = 0
    for origin, seen_names in per_origin.items():
        removed += await reconcile_librenms_ports(session, device_id, origin, seen_names)

    # 自我修復：清掉先前輪詢時被拉進來的偽介面（ethernet_N / ppp_N 等）。只刪未接線、
    # 未做穿透對應的，避免動到手動建立或已納入佈線的埠。
    pruned = await prune_pseudo_ports(session, device_id, pseudo_res) if pseudo_res is not None else 0

    if created or pruned or removed:
        await _audit(session, user=user, request=request, object_type="device_port",
                     object_id=str(device_id), action="import",
                     diff={"imported": created, "pruned": pruned, "removed": removed,
                           "sources": sorted(sources)})
    # 認領（只改來源欄位）沒有增刪也要寫進去
    await session.commit()
    return {"imported": created, "pruned": pruned, "removed": removed, "found": len(names),
            "linked_librenms": len(lns_devs), "sources": sorted(sources)}


@router.patch("/device-ports/{port_id}", response_model=DevicePortRead,
              dependencies=[Depends(require_admin)])
async def update_device_port(
    port_id: uuid.UUID,
    payload: DevicePortUpdate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DevicePortRead:
    obj = await session.get(DevicePort, port_id)
    if obj is None:
        raise HTTPException(404, detail="Port not found")
    changes = payload.model_dump(exclude_unset=True, exclude={"replace_peer"})
    peer_given = "peer_port_id" in changes
    new_peer = changes.pop("peer_port_id", None)
    for k, v in changes.items():
        setattr(obj, k, v)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="Port name already exists on this device") from exc
    # front↔rear 穿透配對兩邊一起改：舊對端的反向指標要清掉（以前只補寫新對端，舊的還指著自己）
    if peer_given and new_peer != obj.peer_port_id:
        await set_peer(session, obj, new_peer, replace=payload.replace_peer)
        await session.flush()
    await _audit(session, user=user, request=request, object_type="device_port",
                 object_id=str(obj.id), action="update", diff=payload.model_dump(mode="json", exclude_unset=True))
    await session.commit()
    await session.refresh(obj)
    return DevicePortRead.model_validate(obj)


@router.delete("/device-ports/{port_id}", status_code=204,
               dependencies=[Depends(require_admin)])
async def delete_device_port(
    port_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    obj = await session.get(DevicePort, port_id)
    if obj is None:
        raise HTTPException(404, detail="Port not found")
    await _audit(session, user=user, request=request, object_type="device_port",
                 object_id=str(obj.id), action="delete", diff={"name": obj.name})
    await session.delete(obj)
    await session.commit()


@router.get("/ports/{port_id}/trace")
async def trace_port(
    port_id: uuid.UUID,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    first_cable_id: uuid.UUID | None = Query(None, description="從纜線清單出發：第一段走這條線"),
    max_segments: int = Query(64, ge=1, le=64),
) -> dict[str, Any]:
    """端到端追線：沿已接線的纜線、面板正背面配對一路往下，並說明停在哪、為什麼（services/port_trace）。"""
    from app.services.port_trace import trace
    return await trace(session, user, port_id, first_cable_id=first_cable_id, max_segments=max_segments)


# ─────────────────── Power ───────────────────


class PowerPanelRead(StrictModel):
    id: uuid.UUID
    name: str
    location_id: uuid.UUID | None
    description: str | None


class PowerPanelWrite(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    location_id: uuid.UUID | None = None
    description: Annotated[str | None, Field(max_length=1024)] = None


class PowerFeedRead(StrictModel):
    id: uuid.UUID
    panel_id: uuid.UUID
    name: str
    voltage_v: int
    amperage_a: int
    phase: str
    supply_type: str
    rack_id: uuid.UUID | None
    description: str | None
    # 規劃容量（人工確認，null＝未設定）與警戒 %。不再用電壓 × 電流當可用瓦數（交接包 physical-v1.2）
    planning_capacity_w: float | None = None
    warning_pct: int = 80
    capacity_basis: str | None = None
    # 負載（清單才填，services/power_summary）：整條迴路已知的規劃分配；partial＝有裝置的負載或分攤未知，
    # 使用率只能說「至少」，狀態不會是 ok
    allocated_w: float | None = None
    partial: bool | None = None
    unknown_devices: int | None = None
    utilization_pct: float | None = None
    status: str | None = None
    devices: int | None = None
    racks: int | None = None


class PowerFeedWrite(StrictModel):
    panel_id: uuid.UUID
    name: Annotated[str, Field(min_length=1, max_length=64)]
    voltage_v: Annotated[int, Field(ge=12, le=600)] = 220
    amperage_a: Annotated[int, Field(ge=1, le=400)] = 20
    # 資料庫有 CHECK：以前送別的值會變成 500，現在在格式檢查就擋（422）
    phase: Literal["single", "three"] = "single"
    supply_type: Literal["ac", "dc"] = "ac"
    rack_id: uuid.UUID | None = None
    description: Annotated[str | None, Field(max_length=1024)] = None
    planning_capacity_w: Annotated[Decimal | None, Field(ge=0, le=10_000_000, max_digits=10, decimal_places=2)] = None
    warning_pct: Annotated[int, Field(ge=1, le=100)] = 80
    capacity_basis: Annotated[str | None, Field(max_length=128)] = None


class PowerOutletRead(StrictModel):
    id: uuid.UUID
    feed_id: uuid.UUID | None
    rack_id: uuid.UUID | None
    label: str
    device_id: uuid.UUID | None
    description: str | None
    # 清單才填：「機房 / 機櫃 / 配電盤 / 迴路 / 插座」（同名插座才分得出來）與接在上面的電源
    path: str | None = None
    occupied_by: list[str] | None = None


class PowerOutletWrite(StrictModel):
    feed_id: uuid.UUID | None = None
    rack_id: uuid.UUID | None = None
    label: Annotated[str, Field(min_length=1, max_length=64)]
    device_id: uuid.UUID | None = None
    description: Annotated[str | None, Field(max_length=1024)] = None


@router.get("/power-panels", response_model=Paginated[PowerPanelRead])
async def list_power_panels(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(1, ge=1, le=10_000), page_size: int = Query(50, ge=1, le=500),
) -> Paginated[PowerPanelRead]:
    rows = list((await session.execute(
        select(PowerPanel).order_by(PowerPanel.name)
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    total = int(await session.scalar(select(func.count()).select_from(PowerPanel)) or 0)
    return Paginated[PowerPanelRead](
        items=[PowerPanelRead.model_validate(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.post("/power-panels", response_model=PowerPanelRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_power_panel(
    payload: PowerPanelWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PowerPanelRead:
    obj = PowerPanel(**payload.model_dump())
    session.add(obj)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="Panel name+location conflict") from exc
    await _audit(session, user=user, request=request, object_type="power_panel",
                 object_id=str(obj.id), action="create",
                 diff=payload.model_dump(mode="json"))
    await session.commit()
    await session.refresh(obj)
    return PowerPanelRead.model_validate(obj)


@router.get("/power-feeds", response_model=Paginated[PowerFeedRead])
async def list_power_feeds(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    panel_id: uuid.UUID | None = Query(None),
    page: int = Query(1, ge=1, le=10_000), page_size: int = Query(100, ge=1, le=500),
) -> Paginated[PowerFeedRead]:
    stmt = select(PowerFeed)
    cstmt = select(func.count()).select_from(PowerFeed)
    if panel_id is not None:
        stmt = stmt.where(PowerFeed.panel_id == panel_id)
        cstmt = cstmt.where(PowerFeed.panel_id == panel_id)
    rows = list((await session.execute(
        stmt.order_by(PowerFeed.name).offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    total = int(await session.scalar(cstmt) or 0)
    sums = await feed_summaries(session, rows)
    items = []
    for r in rows:
        v = sums.get(r.id, {})
        items.append(PowerFeedRead.model_validate(r).model_copy(update={
            "allocated_w": v.get("full_allocated_w"), "partial": v.get("partial"),
            "unknown_devices": v.get("unknown_devices"), "utilization_pct": v.get("utilization_pct"),
            "status": v.get("status"), "devices": v.get("devices"), "racks": v.get("racks")}))
    return Paginated[PowerFeedRead](items=items, total=total, page=page, page_size=page_size)


@router.get("/power-feeds/{fid}/load-summary")
async def power_feed_load(
    fid: uuid.UUID, _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """整條迴路：接在上面的每台裝置（各機櫃、未入櫃都算）與已知分配。"""
    feed = await session.get(PowerFeed, fid)
    if feed is None:
        raise HTTPException(404, detail="Not found")
    return await feed_detail(session, feed)


@router.get("/power-scenarios/preview")
async def power_failure_preview(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    feed_id: Annotated[list[uuid.UUID], Query(min_length=1, max_length=20)],
) -> dict[str, Any]:
    """失效模擬（唯讀）：這幾條迴路整條斷電時，每台受影響裝置的結果與存活迴路的新負載。"""
    res = await preview_failure(session, feed_id)
    if res is None:
        raise HTTPException(404, detail="Power feed not found")
    return res


@router.get("/racks/{rack_id}/power")
async def rack_power(
    rack_id: uuid.UUID,
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """機櫃的規劃電力（issue #54）：已知小計、完整度、未接線、分配未知、各迴路本櫃份額與全迴路、每台裝置的分配。"""
    from app.models.location import Rack
    if await session.get(Rack, rack_id) is None:
        raise HTTPException(404, detail="Rack not found")
    return await rack_summary(session, rack_id)


@router.post("/power-feeds", response_model=PowerFeedRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_power_feed(
    payload: PowerFeedWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PowerFeedRead:
    obj = PowerFeed(**payload.model_dump())
    session.add(obj)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="Feed conflict") from exc
    await _audit(session, user=user, request=request, object_type="power_feed",
                 object_id=str(obj.id), action="create",
                 diff=payload.model_dump(mode="json"))
    await session.commit()
    await session.refresh(obj)
    return PowerFeedRead.model_validate(obj)


@router.get("/power-outlets", response_model=Paginated[PowerOutletRead])
async def list_power_outlets(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(1, ge=1, le=10_000), page_size: int = Query(50, ge=1, le=500),
) -> Paginated[PowerOutletRead]:
    rows = list((await session.execute(
        select(PowerOutlet).order_by(PowerOutlet.label)
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    total = int(await session.scalar(select(func.count()).select_from(PowerOutlet)) or 0)
    from app.models.location import Location, Rack
    ids = [r.id for r in rows]
    where: dict[uuid.UUID, tuple[Any, ...]] = {}
    used: dict[uuid.UUID, list[str]] = {}
    if ids:
        for oid, feed, panel, rack, loc in (await session.execute(
                select(PowerOutlet.id, PowerFeed.name, PowerPanel.name, Rack.name, Location.name)
                .outerjoin(PowerFeed, PowerFeed.id == PowerOutlet.feed_id)
                .outerjoin(PowerPanel, PowerPanel.id == PowerFeed.panel_id)
                .outerjoin(Rack, Rack.id == PowerOutlet.rack_id)
                .outerjoin(Location, Location.id == Rack.location_id)
                .where(in_values(PowerOutlet.id, ids)))).all():
            where[oid] = (loc, rack, panel, feed)
        for oid, dev, port in (await session.execute(
                select(DevicePowerPort.outlet_id, Device.name, DevicePowerPort.name)
                .join(Device, Device.id == DevicePowerPort.device_id)
                .where(in_values(DevicePowerPort.outlet_id, ids)).order_by(Device.name))).all():
            used.setdefault(oid, []).append(f"{dev} {port}")
    items = []
    for r in rows:
        parts = [*where.get(r.id, ()), r.label]
        items.append(PowerOutletRead.model_validate(r).model_copy(update={
            "path": " / ".join(x for x in parts if x), "occupied_by": used.get(r.id, [])}))
    return Paginated[PowerOutletRead](items=items, total=total, page=page, page_size=page_size)


@router.post("/power-outlets", response_model=PowerOutletRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_power_outlet(
    payload: PowerOutletWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PowerOutletRead:
    obj = PowerOutlet(**payload.model_dump())
    session.add(obj)
    await session.flush()
    await _audit(session, user=user, request=request, object_type="power_outlet",
                 object_id=str(obj.id), action="create",
                 diff=payload.model_dump(mode="json"))
    await session.commit()
    await session.refresh(obj)
    return PowerOutletRead.model_validate(obj)


# ── 電力資源 編輯 / 刪除（admin）──
async def _power_update(session, model, obj_id, payload, otype, user, request):  # type: ignore[no-untyped-def]
    obj = await session.get(model, obj_id)
    if obj is None:
        raise HTTPException(404, detail="Not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    await session.flush()
    await _audit(session, user=user, request=request, object_type=otype,
                 object_id=str(obj_id), action="update",
                 diff=payload.model_dump(mode="json", exclude_unset=True))
    await session.commit()
    await session.refresh(obj)
    return obj


async def _power_delete(session, model, obj_id, otype, user, request):  # type: ignore[no-untyped-def]
    obj = await session.get(model, obj_id)
    if obj is None:
        raise HTTPException(404, detail="Not found")
    await session.delete(obj)
    await _audit(session, user=user, request=request, object_type=otype,
                 object_id=str(obj_id), action="delete", diff=None)
    await session.commit()


@router.patch("/power-panels/{pid}", response_model=PowerPanelRead, dependencies=[Depends(require_admin)])
async def update_power_panel(pid: uuid.UUID, payload: PowerPanelWrite, user: CurrentUser, request: Request,
                             session: Annotated[AsyncSession, Depends(get_session)]) -> PowerPanelRead:
    return PowerPanelRead.model_validate(await _power_update(session, PowerPanel, pid, payload, "power_panel", user, request))


@router.delete("/power-panels/{pid}", status_code=204, dependencies=[Depends(require_admin)])
async def delete_power_panel(pid: uuid.UUID, user: CurrentUser, request: Request,
                             session: Annotated[AsyncSession, Depends(get_session)]) -> None:
    # 還有迴路就不刪：CASCADE 會連迴路、插座的關聯一起靜靜消失，備援資訊就沒了
    n = int(await session.scalar(select(func.count()).select_from(PowerFeed).where(PowerFeed.panel_id == pid)) or 0)
    if n:
        raise HTTPException(409, detail=ui_detail("panel_in_use", f"這個配電盤還有 {n} 個迴路，先刪除或移走", n=n))
    await _power_delete(session, PowerPanel, pid, "power_panel", user, request)


@router.patch("/power-feeds/{fid}", response_model=PowerFeedRead, dependencies=[Depends(require_admin)])
async def update_power_feed(fid: uuid.UUID, payload: PowerFeedWrite, user: CurrentUser, request: Request,
                            session: Annotated[AsyncSession, Depends(get_session)]) -> PowerFeedRead:
    return PowerFeedRead.model_validate(await _power_update(session, PowerFeed, fid, payload, "power_feed", user, request))


@router.delete("/power-feeds/{fid}", status_code=204, dependencies=[Depends(require_admin)])
async def delete_power_feed(fid: uuid.UUID, user: CurrentUser, request: Request,
                            session: Annotated[AsyncSession, Depends(get_session)]) -> None:
    n = int(await session.scalar(select(func.count()).select_from(PowerOutlet).where(PowerOutlet.feed_id == fid)) or 0)
    if n:
        raise HTTPException(409, detail=ui_detail("feed_in_use", f"這個迴路還有 {n} 個插座，先刪除或改到別的迴路", n=n))
    await _power_delete(session, PowerFeed, fid, "power_feed", user, request)


@router.patch("/power-outlets/{oid}", response_model=PowerOutletRead, dependencies=[Depends(require_admin)])
async def update_power_outlet(oid: uuid.UUID, payload: PowerOutletWrite, user: CurrentUser, request: Request,
                              session: Annotated[AsyncSession, Depends(get_session)]) -> PowerOutletRead:
    return PowerOutletRead.model_validate(await _power_update(session, PowerOutlet, oid, payload, "power_outlet", user, request))


@router.delete("/power-outlets/{oid}", status_code=204, dependencies=[Depends(require_admin)])
async def delete_power_outlet(oid: uuid.UUID, user: CurrentUser, request: Request,
                              session: Annotated[AsyncSession, Depends(get_session)]) -> None:
    # 有電源接著就不刪（SET NULL 會讓那顆電源靜靜變成未接線）
    users = (await session.execute(
        select(Device.name, DevicePowerPort.name).join(Device, Device.id == DevicePowerPort.device_id)
        .where(DevicePowerPort.outlet_id == oid).limit(5))).all()
    if users:
        names = "、".join(f"{d} {p}" for d, p in users)
        raise HTTPException(409, detail=ui_detail("outlet_in_use", f"這個插座接著 {names}，先改接或拔掉", ports=names))
    await _power_delete(session, PowerOutlet, oid, "power_outlet", user, request)


# ─────────────────── 裝置電源埠（PSU → 插座） ───────────────────
class DevicePowerPortRead(StrictModel):
    id: uuid.UUID
    device_id: uuid.UUID
    name: str
    outlet_id: uuid.UUID | None
    outlet_label: str | None = None
    max_watts: int | None                # PSU 額定容量（不是負載）
    rating_confirmed_at: datetime | None = None
    normal_share_pct: float | None = None
    description: str | None


class DevicePowerPortWrite(StrictModel):
    device_id: uuid.UUID
    name: Annotated[str, Field(min_length=1, max_length=64)]
    outlet_id: uuid.UUID | None = None
    max_watts: Annotated[int | None, Field(ge=0, le=100_000)] = None
    description: Annotated[str | None, Field(max_length=1024)] = None


class DevicePowerPortPatch(StrictModel):
    name: Annotated[str | None, Field(min_length=1, max_length=64)] = None
    outlet_id: uuid.UUID | None = None
    max_watts: Annotated[int | None, Field(ge=0, le=100_000)] = None
    description: Annotated[str | None, Field(max_length=1024)] = None
    # 「舊值，請確認」：確認 0247 以前填的瓦數確實是 PSU 額定容量（不改值）
    rating_confirmed: bool | None = None


async def _power_port_out(session: AsyncSession, obj: DevicePowerPort) -> DevicePowerPortRead:
    out = DevicePowerPortRead.model_validate(obj)
    if obj.outlet_id:
        outlet = await session.get(PowerOutlet, obj.outlet_id)
        if outlet is not None:
            out.outlet_label = outlet.label
    return out


@router.get("/device-power-ports", response_model=list[DevicePowerPortRead])
async def list_device_power_ports(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    device_id: uuid.UUID = Query(...),
) -> list[DevicePowerPortRead]:
    rows = list((await session.execute(
        select(DevicePowerPort).where(DevicePowerPort.device_id == device_id)
        .order_by(DevicePowerPort.name)
    )).scalars().all())
    return [await _power_port_out(session, r) for r in rows]


@router.post("/device-power-ports", response_model=DevicePowerPortRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_device_power_port(
    payload: DevicePowerPortWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DevicePowerPortRead:
    if payload.outlet_id is not None:
        await ensure_outlet_free(session, payload.outlet_id, psu_id=None)
    obj = DevicePowerPort(**payload.model_dump())
    if obj.max_watts is not None:
        obj.rating_confirmed_at = datetime.now(UTC)   # 新畫面填的就是額定容量
    session.add(obj)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="Power port name already exists on this device") from exc
    await _audit(session, user=user, request=request, object_type="device_power_port",
                 object_id=str(obj.id), action="create", diff=payload.model_dump(mode="json"))
    await session.commit()
    await session.refresh(obj)
    return await _power_port_out(session, obj)


@router.patch("/device-power-ports/{pid}", response_model=DevicePowerPortRead,
              dependencies=[Depends(require_admin)])
async def update_device_power_port(
    pid: uuid.UUID, payload: DevicePowerPortPatch, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DevicePowerPortRead:
    obj = await session.get(DevicePowerPort, pid)
    if obj is None:
        raise HTTPException(404, detail="Not found")
    changes = payload.model_dump(exclude_unset=True, exclude={"rating_confirmed"})
    # 用新畫面（標「PSU 額定容量」）改的值、或按了確認：都算確認過
    if payload.rating_confirmed or "max_watts" in changes:
        obj.rating_confirmed_at = datetime.now(UTC)
    # 只在換插座時檢查：升級前就有的重複占用照樣可以改說明（不動舊資料，由資料檢查頁列出）
    if changes.get("outlet_id") is not None and changes["outlet_id"] != obj.outlet_id:
        await ensure_outlet_free(session, changes["outlet_id"], psu_id=obj.id)
    for k, v in changes.items():
        setattr(obj, k, v)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="Power port name conflict") from exc
    await _audit(session, user=user, request=request, object_type="device_power_port",
                 object_id=str(obj.id), action="update",
                 diff=payload.model_dump(mode="json", exclude_unset=True))
    await session.commit()
    await session.refresh(obj)
    return await _power_port_out(session, obj)


@router.delete("/device-power-ports/{pid}", status_code=204, dependencies=[Depends(require_admin)])
async def delete_device_power_port(
    pid: uuid.UUID, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    obj = await session.get(DevicePowerPort, pid)
    if obj is None:
        return
    await session.delete(obj)
    await _audit(session, user=user, request=request, object_type="device_power_port",
                 object_id=str(pid), action="delete", diff=None)
    await session.commit()


# ── 裝置的規劃電力（交接包 physical-v1.2 設計圖 02）──

PowerBasis = Literal["manual_estimate", "vendor_reference", "measurement_reference"]
SupplyMode = Literal["unknown", "single", "shared", "active_standby", "custom"]
Failover = Literal["unknown", "confirmed_full_load", "insufficient"]


class PowerShareIn(StrictModel):
    id: uuid.UUID
    normal_share_pct: Annotated[Decimal | None, Field(ge=0, le=100, max_digits=5, decimal_places=2)] = None


class PowerProfileWrite(StrictModel):
    """整台的規劃電力與各顆電源的分攤，一次存（同一個交易）。revision 不對＝別人剛改過 → 409，不蓋掉。"""
    revision: Annotated[int, Field(ge=0)]
    planning_input_w: Annotated[Decimal | None, Field(ge=0, le=10_000_000, max_digits=10, decimal_places=2)] = None
    value_basis: PowerBasis | None = None
    notes: Annotated[str | None, Field(max_length=2048)] = None
    supply_mode: SupplyMode = "unknown"
    failover_capability: Failover = "unknown"
    primary_port_id: uuid.UUID | None = None
    include_in_planning: bool = True
    exclude_reason: Annotated[str | None, Field(max_length=1024)] = None
    ports: Annotated[list[PowerShareIn], Field(max_length=64)] = []


async def _power_profile_out(session: AsyncSession, device_id: uuid.UUID) -> dict[str, Any]:
    from app.models.location import Location, Rack
    from app.services.power_summary import allocate, num
    prof = (await session.execute(select(DevicePowerProfile).where(
        DevicePowerProfile.device_id == device_id))).scalar_one_or_none()
    rows = (await session.execute(
        select(DevicePowerPort, PowerOutlet, PowerFeed, PowerPanel.name, Rack.name, Location.name)
        .outerjoin(PowerOutlet, PowerOutlet.id == DevicePowerPort.outlet_id)
        .outerjoin(PowerFeed, PowerFeed.id == PowerOutlet.feed_id)
        .outerjoin(PowerPanel, PowerPanel.id == PowerFeed.panel_id)
        .outerjoin(Rack, Rack.id == PowerOutlet.rack_id)
        .outerjoin(Location, Location.id == Rack.location_id)
        .where(DevicePowerPort.device_id == device_id).order_by(DevicePowerPort.name))).all()
    alloc = (await allocate(session, [device_id])).get(device_id)
    watts = {p.port_id: p.watts for p in alloc.ports} if alloc else {}
    ports, by_feed = [], {}
    for pp, outlet, feed, panel, rack, loc in rows:
        w = watts.get(pp.id)
        if feed is not None and w is not None:
            cur = by_feed.setdefault(feed.id, {"feed_id": str(feed.id), "feed": feed.name, "watts": Decimal(0)})
            cur["watts"] += w
        ports.append({
            "id": str(pp.id), "name": pp.name, "max_watts": pp.max_watts,
            "legacy_rating": pp.max_watts is not None and pp.rating_confirmed_at is None,
            "normal_share_pct": num(pp.normal_share_pct), "allocated_w": num(w),
            "outlet_id": str(outlet.id) if outlet else None, "outlet_label": outlet.label if outlet else None,
            # 機房 / 機櫃 / 配電盤 / 迴路 / 插座：同名插座才分得出來
            "outlet_path": " / ".join(x for x in (loc, rack, panel, feed.name if feed else None,
                                                  outlet.label if outlet else None) if x) or None,
            "feed_id": str(feed.id) if feed else None, "feed": feed.name if feed else None,
        })
    shares = [p.normal_share_pct for p, *_ in rows if p.normal_share_pct is not None]
    feeds = {p["feed_id"] for p in ports if p["feed_id"]}
    return {
        "device_id": str(device_id), "revision": prof.revision if prof else 0,
        "planning_input_w": num(prof.planning_input_w) if prof else None,
        "value_basis": prof.value_basis if prof else None,
        "confirmed_at": prof.confirmed_at.isoformat() if prof and prof.confirmed_at else None,
        "notes": prof.notes if prof else None,
        "supply_mode": prof.supply_mode if prof else "unknown",
        "failover_capability": prof.failover_capability if prof else "unknown",
        "primary_port_id": str(prof.primary_port_id) if prof and prof.primary_port_id else None,
        "include_in_planning": alloc.included if alloc else True,
        "exclude_reason": prof.exclude_reason if prof else None,
        "ports": ports,
        "share_total": num(sum(shares, Decimal(0))) if shares else None,
        "preview": {"by_feed": [{**v, "watts": num(v["watts"])} for v in by_feed.values()],
                    "unassigned_w": num(alloc.unassigned) if alloc else 0,
                    "allocation_unknown_w": num(alloc.allocation_unknown) if alloc else 0},
        "issues": alloc.issues if alloc else [],
        # 不同迴路只證明迴路不同，不證明上游 UPS / 配電盤各自獨立
        "wiring": {"distinct_feeds": len(feeds), "same_feed": len(ports) >= 2 and len(feeds) == 1,
                   "unconnected": [p["name"] for p in ports if not p["feed_id"]],
                   "upstream_independence": "unconfirmed"},
    }


@router.get("/devices/{device_id}/power-profile")
async def get_power_profile(
    device_id: uuid.UUID, _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    if await session.get(Device, device_id) is None:
        raise HTTPException(404, detail="Device not found")
    return await _power_profile_out(session, device_id)


@router.put("/devices/{device_id}/power-profile", dependencies=[Depends(require_admin)])
async def put_power_profile(
    device_id: uuid.UUID, payload: PowerProfileWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    # 鎖住裝置那一列：同一台的兩次存檔排隊，revision 檢查才準
    dev = (await session.execute(select(Device).where(Device.id == device_id).with_for_update())).scalar_one_or_none()
    if dev is None:
        raise HTTPException(404, detail="Device not found")
    prof = (await session.execute(select(DevicePowerProfile).where(
        DevicePowerProfile.device_id == device_id))).scalar_one_or_none()
    current = prof.revision if prof else 0
    if payload.revision != current:
        raise HTTPException(409, detail=ui_detail(
            "power_profile_conflict", "這台裝置的電源設定剛被別人改過，請重新整理後再存", current=current))
    if not payload.include_in_planning and not (payload.exclude_reason or "").strip():
        raise HTTPException(422, detail=ui_detail("power_exclude_reason_required", "不納入規劃要寫原因"))
    ports = {p.id: p for p in (await session.execute(select(DevicePowerPort).where(
        DevicePowerPort.device_id == device_id).with_for_update())).scalars()}
    if payload.primary_port_id is not None and payload.primary_port_id not in ports:
        raise HTTPException(422, detail=ui_detail("power_port_other_device", "電源埠不屬於這台裝置"))
    for sh in payload.ports:
        if sh.id not in ports:
            raise HTTPException(422, detail=ui_detail("power_port_other_device", "電源埠不屬於這台裝置"))
        ports[sh.id].normal_share_pct = sh.normal_share_pct
    total = sum((p.normal_share_pct for p in ports.values() if p.normal_share_pct is not None), Decimal(0))
    if total > 100:
        raise HTTPException(422, detail=ui_detail("power_share_over", f"分攤比例加總 {total}% 超過 100%", total=str(total)))
    if prof is None:
        prof = DevicePowerProfile(device_id=device_id, revision=0)
        session.add(prof)
    if payload.planning_input_w is None:
        prof.confirmed_at = None
    elif prof.planning_input_w != payload.planning_input_w or prof.value_basis != payload.value_basis \
            or prof.confirmed_at is None:
        prof.confirmed_at = datetime.now(UTC)
    for k in ("planning_input_w", "value_basis", "notes", "supply_mode", "failover_capability", "primary_port_id",
              "include_in_planning"):
        setattr(prof, k, getattr(payload, k))
    prof.exclude_reason = None if payload.include_in_planning else (payload.exclude_reason or "").strip()
    prof.revision = current + 1
    await session.flush()
    await _audit(session, user=user, request=request, object_type="device_power_profile",
                 object_id=str(device_id), action="update", diff=payload.model_dump(mode="json"))
    await session.commit()
    return await _power_profile_out(session, device_id)


# ─────────────────── VPN ───────────────────


class VPNTunnelRead(StrictModel):
    id: uuid.UUID
    name: str
    type: str
    status: str
    a_device_id: uuid.UUID | None
    b_device_id: uuid.UUID | None
    a_endpoint: str | None
    b_endpoint: str | None
    encryption_algo: str | None
    auth_algo: str | None
    description: str | None
    pairing_method: str | None = None   # wireguard_pubkey（可靠）/ ipsec_endpoint（best-effort）
    a_device_name: str | None = None
    b_device_name: str | None = None
    peered: bool = False   # 兩端對接成立（公鑰或端點比對）
    # 防火牆同步來的（`fortigate:<id>` 之類）唯讀；手動建立的是 None
    source_origin: str | None = None


VPNType = Literal["ipsec_ikev1", "ipsec_ikev2", "wireguard", "openvpn", "l2tp", "vxlan", "vpls", "evpn", "other"]
VPNStatus = Literal["planned", "active", "offline", "decommissioned"]


class VPNTunnelWrite(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    # 跟資料表的檢查約束同一份：以前是 str，填錯撞上約束被當成「名稱重複」回 409
    type: VPNType
    status: VPNStatus = "active"
    a_device_id: uuid.UUID | None = None
    b_device_id: uuid.UUID | None = None
    a_endpoint: Annotated[str | None, Field(max_length=255)] = None
    b_endpoint: Annotated[str | None, Field(max_length=255)] = None
    encryption_algo: Annotated[str | None, Field(max_length=32)] = None
    auth_algo: Annotated[str | None, Field(max_length=32)] = None
    description: Annotated[str | None, Field(max_length=1024)] = None

    @model_validator(mode="after")
    def _two_ends(self) -> VPNTunnelWrite:
        if self.a_device_id is not None and self.a_device_id == self.b_device_id:
            raise ValueError("a_device_id and b_device_id must be different devices")
        return self


@router.get("/vpn-tunnels", response_model=Paginated[VPNTunnelRead])
async def list_vpn(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(1, ge=1, le=10_000), page_size: int = Query(50, ge=1, le=500),
) -> Paginated[VPNTunnelRead]:
    rows = list((await session.execute(
        select(VPNTunnel).order_by(VPNTunnel.name)
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    total = int(await session.scalar(select(func.count()).select_from(VPNTunnel)) or 0)

    # 解析 a/b device 名稱供前端顯示對接關係
    dev_ids = {d for r in rows for d in (r.a_device_id, r.b_device_id) if d}
    names: dict[uuid.UUID, str] = {}
    if dev_ids:
        names = {
            did: nm for did, nm in (await session.execute(
                select(Device.id, Device.name).where(Device.id.in_(dev_ids))  # bounded: one page (≤ 500)
            )).all()
        }

    items: list[VPNTunnelRead] = []
    for r in rows:
        item = VPNTunnelRead.model_validate(r)
        item.a_device_name = names.get(r.a_device_id) if r.a_device_id else None
        item.b_device_name = names.get(r.b_device_id) if r.b_device_id else None
        item.peered = r.b_device_id is not None
        items.append(item)
    return Paginated[VPNTunnelRead](
        items=items, total=total, page=page, page_size=page_size,
    )


def _vpn_name_taken(name: str) -> HTTPException:
    return HTTPException(409, detail=ui_detail("vpn_tunnel_name_taken", f"已經有同名的 VPN 通道「{name}」", name=name))


async def _manual_vpn(session: AsyncSession, tunnel_id: uuid.UUID) -> VPNTunnel:
    obj = await session.get(VPNTunnel, tunnel_id)
    if obj is None:
        raise HTTPException(404, detail="Not found")
    if obj.source_origin:
        # 防火牆同步來的：下次同步會照設備的設定蓋回去／重建，改了、刪了都沒用
        raise HTTPException(409, detail=ui_detail(
            "vpn_tunnel_synced", "這條通道是從防火牆同步來的，請在防火牆上修改", origin=obj.source_origin))
    return obj


@router.post("/vpn-tunnels", response_model=VPNTunnelRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_vpn(
    payload: VPNTunnelWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> VPNTunnelRead:
    obj = VPNTunnel(**payload.model_dump())
    session.add(obj)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise _vpn_name_taken(payload.name) from exc
    await _audit(session, user=user, request=request, object_type="vpn_tunnel",
                 object_id=str(obj.id), action="create",
                 diff=payload.model_dump(mode="json"))
    await session.commit()
    await session.refresh(obj)
    return VPNTunnelRead.model_validate(obj)


@router.patch("/vpn-tunnels/{tunnel_id}", response_model=VPNTunnelRead, dependencies=[Depends(require_admin)])
async def update_vpn(
    tunnel_id: uuid.UUID, payload: VPNTunnelWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> VPNTunnelRead:
    obj = await _manual_vpn(session, tunnel_id)
    changes = payload.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(obj, k, v)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise _vpn_name_taken(payload.name) from exc
    await _audit(session, user=user, request=request, object_type="vpn_tunnel",
                 object_id=str(tunnel_id), action="update",
                 diff=payload.model_dump(mode="json", exclude_unset=True))
    await session.commit()
    await session.refresh(obj)
    return VPNTunnelRead.model_validate(obj)


@router.delete("/vpn-tunnels/{tunnel_id}", status_code=204, dependencies=[Depends(require_admin)])
async def delete_vpn(
    tunnel_id: uuid.UUID, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    obj = await _manual_vpn(session, tunnel_id)
    await session.delete(obj)
    await _audit(session, user=user, request=request, object_type="vpn_tunnel",
                 object_id=str(tunnel_id), action="delete", diff={"name": obj.name})
    await session.commit()
