"""維護排程（2026-11-01）：維護期間範圍內的物件不發告警。

**靜音的做法是「不把它記成已經告警過」，不是發送時丟掉。** 狀態類告警（整合同步失敗、代理失聯…）在
`state_alert.observe` 照常累計失敗次數，但維護中不標成「已告警」：維護結束時還壞著，下一輪就照常發出
（補發）；維護期間壞了又好的，不會在結束後冒出一則莫名其妙的「恢復」。異常偵測同理：被靜音的發現
不寫進「看過」清單，維護結束時還在的會再發。

範圍存規則，判斷時建一份索引（`Index`）：子網路、區段（含子區段）、單位、機房地點會先展開成子網路與裝置，
再展開成 IP；IP 範圍與子網路的 CIDR 另外留著，給沒有登記在 IPAM 的位址（例如未授權 IP）比對。
整合與掃描代理除了直接選，**它們連線的主機在維護範圍內也算**：維護 LibreNMS 那台主機時，
「LibreNMS 同步失敗」就不該吵。
"""
from __future__ import annotations

import ipaddress
import time as _time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.maintenance import MaintenanceScope, MaintenanceSuppression, MaintenanceWindow

EVENT_STARTED = "maintenance.started"
EVENT_ENDED = "maintenance.ended"
_INDEX_TTL = 60.0
_CACHE_KEY = "maintenance_index"
_STRENGTH = {"mute": 2, "mark": 1}


# ─────────────────── 時間 ───────────────────

def tz() -> ZoneInfo:
    """每週例行的「每週二 02:00」用站台時區（APP_TIMEZONE）。"""
    from app.core.config import get_settings
    try:
        return ZoneInfo(get_settings().app_timezone)
    except Exception:
        return ZoneInfo("UTC")


def occurrence(w: MaintenanceWindow, now: datetime) -> tuple[datetime, datetime] | None:
    """`now` 落在哪一次維護裡（開始, 結束）；不在維護中回 None。"""
    if w.schedule_type == "once":
        if w.starts_at and w.ends_at and w.starts_at <= now < w.ends_at:
            return w.starts_at, w.ends_at
        return None
    if not (w.weekdays and w.start_time and w.end_time and w.valid_until):
        return None
    z = tz()
    local = now.astimezone(z)
    for back in (0, 1):     # 跨午夜的那一次是「昨天」開始的
        day = local.date() - timedelta(days=back)
        if day.weekday() not in w.weekdays:
            continue
        if (w.valid_from and day < w.valid_from) or day > w.valid_until:
            continue
        start = datetime.combine(day, w.start_time, tzinfo=z)
        end_day = day + timedelta(days=1) if w.end_time <= w.start_time else day
        end = datetime.combine(end_day, w.end_time, tzinfo=z)
        if w.extended_until and start <= w.extended_until and w.extended_until > end:
            end = w.extended_until
        if start <= now < end:
            if w.skip_until and start <= w.skip_until and now < w.skip_until:
                return None          # 這一次已經提前結束
            return start.astimezone(UTC), end.astimezone(UTC)
    return None


def next_occurrence(w: MaintenanceWindow, now: datetime, horizon_days: int = 60) -> tuple[datetime, datetime] | None:
    """下一次（含正在進行的）維護時段，給清單顯示「即將開始」。"""
    cur = occurrence(w, now)
    if cur:
        return cur
    if w.schedule_type == "once":
        return (w.starts_at, w.ends_at) if w.starts_at and w.ends_at and w.starts_at > now else None
    if not (w.weekdays and w.start_time and w.end_time and w.valid_until):
        return None
    z = tz()
    local = now.astimezone(z)
    for ahead in range(horizon_days + 1):
        day = local.date() + timedelta(days=ahead)
        if day > w.valid_until:
            return None
        if day.weekday() not in w.weekdays or (w.valid_from and day < w.valid_from):
            continue
        start = datetime.combine(day, w.start_time, tzinfo=z)
        if start <= now:
            continue
        end_day = day + timedelta(days=1) if w.end_time <= w.start_time else day
        return start.astimezone(UTC), datetime.combine(end_day, w.end_time, tzinfo=z).astimezone(UTC)
    return None


def status_of(w: MaintenanceWindow, now: datetime) -> str:
    """active / upcoming / past。"""
    if occurrence(w, now):
        return "active"
    return "upcoming" if next_occurrence(w, now) else "past"


# ─────────────────── 範圍索引 ───────────────────

@dataclass
class Index:
    windows: dict[uuid.UUID, MaintenanceWindow] = field(default_factory=dict)
    occurrences: dict[uuid.UUID, datetime] = field(default_factory=dict)
    ip_ids: dict[uuid.UUID, uuid.UUID] = field(default_factory=dict)
    ip_texts: dict[str, uuid.UUID] = field(default_factory=dict)
    networks: list[tuple[Any, uuid.UUID]] = field(default_factory=list)
    subnet_ids: dict[uuid.UUID, uuid.UUID] = field(default_factory=dict)
    device_ids: dict[uuid.UUID, uuid.UUID] = field(default_factory=dict)
    integrations: dict[tuple[str, str | None], uuid.UUID] = field(default_factory=dict)
    agents: dict[str, uuid.UUID] = field(default_factory=dict)
    hostnames: dict[str, uuid.UUID] = field(default_factory=dict)

    def _put(self, d: dict[Any, uuid.UUID], k: Any, wid: uuid.UUID) -> None:
        old = d.get(k)
        if old is None or _STRENGTH[self.windows[wid].behavior] > _STRENGTH[self.windows[old].behavior]:
            d[k] = wid

    def behavior(self, wid: uuid.UUID | None) -> str | None:
        return self.windows[wid].behavior if wid in self.windows else None

    def for_ip(self, *, ip_id: Any = None, ip: Any = None, subnet_id: Any = None) -> uuid.UUID | None:
        if ip_id is not None:
            try:
                wid = self.ip_ids.get(uuid.UUID(str(ip_id)))
            except ValueError:
                wid = None
            if wid:
                return wid
        if subnet_id is not None:
            try:
                wid = self.subnet_ids.get(uuid.UUID(str(subnet_id)))
            except ValueError:
                wid = None
            if wid:
                return wid
        if ip:
            txt = str(ip).split("/")[0].strip()
            if txt in self.ip_texts:
                return self.ip_texts[txt]
            try:
                addr = ipaddress.ip_address(txt)
            except ValueError:
                return None
            for net, wid in self.networks:
                if addr.version == net.version and addr in net:
                    return wid
        return None

    def for_item(self, item: dict[str, Any]) -> uuid.UUID | None:
        """異常偵測的一筆發現在不在維護範圍（看它帶的 IP、IP 紀錄、子網路）。"""
        for f in ("ip_address_id", "ip_id"):
            if item.get(f) and (wid := self.for_ip(ip_id=item[f])):
                return wid
        for r in item.get("records") or []:
            if isinstance(r, dict) and (wid := self.for_ip(ip_id=r.get("ip_address_id"))):
                return wid
        for r in item.get("ips") or []:
            if isinstance(r, dict) and (wid := self.for_ip(ip_id=r.get("ip_address_id"), ip=r.get("ip"))):
                return wid
        if item.get("subnet_id") and (wid := self.for_ip(subnet_id=item["subnet_id"])):
            return wid
        for f in ("ip", "server_ip", "value"):
            if item.get(f) and (wid := self.for_ip(ip=item[f])):
                return wid
        return None

    def for_key(self, key: str) -> uuid.UUID | None:
        """狀態類告警的 key（`integration:<kind>:<id>`、`agent:scan:<id>`…）在不在維護範圍。"""
        parts = key.split(":")
        if parts[0] == "integration" and len(parts) >= 3:
            return self.integrations.get((parts[1], parts[2])) or self.integrations.get((parts[1], None))
        if parts[0] == "agent" and len(parts) >= 3 and parts[1] == "scan":
            return self.agents.get(parts[2])
        if parts[0] == "scan_load" and len(parts) >= 2:
            return self.agents.get(parts[1])
        if parts[0] == "dhcp" and len(parts) >= 5:
            wid = self.integrations.get((parts[1], parts[2])) or self.integrations.get((parts[1], None))
            if wid:
                return wid
            start = parts[-1].split("-")[0]
            return self.for_ip(ip=start)
        return None

    def window_info(self, wid: uuid.UUID) -> dict[str, Any]:
        w = self.windows[wid]
        return {"id": str(w.id), "title": w.title, "behavior": w.behavior}


async def _expand(session: AsyncSession, idx: Index, scopes: list[MaintenanceScope]) -> None:
    from app.services import integration_registry as reg

    subnet_scope: dict[uuid.UUID, uuid.UUID] = {}
    device_scope: dict[uuid.UUID, uuid.UUID] = {}
    for s in scopes:
        wid = s.window_id
        oid = s.object_id
        if s.scope_type == "ip" and oid:
            idx._put(idx.ip_ids, oid, wid)
        elif s.scope_type == "ip_range" and s.range_start:
            try:
                a, b = ipaddress.ip_address(str(s.range_start)), ipaddress.ip_address(str(s.range_end or s.range_start))
                for net in ipaddress.summarize_address_range(min(a, b), max(a, b)):
                    idx.networks.append((net, wid))
            except (ValueError, TypeError):
                continue
        elif s.scope_type == "subnet" and oid:
            subnet_scope.setdefault(oid, wid)
        elif s.scope_type == "section" and oid:
            for (sid,) in (await session.execute(text("""
                WITH RECURSIVE t(id) AS (SELECT CAST(:s AS uuid) UNION SELECT c.id FROM sections c JOIN t ON c.parent_id = t.id)
                SELECT sb.id FROM subnets sb WHERE sb.section_id IN (SELECT id FROM t)
            """), {"s": oid})).all():
                subnet_scope.setdefault(sid, wid)
        elif s.scope_type == "customer" and oid:
            for (sid,) in (await session.execute(text("""
                SELECT sb.id FROM subnets sb LEFT JOIN sections se ON se.id = sb.section_id
                 WHERE sb.customer_id = :c OR se.customer_id = :c
            """), {"c": oid})).all():
                subnet_scope.setdefault(sid, wid)
            for (did,) in (await session.execute(text("SELECT id FROM devices WHERE customer_id = :c"), {"c": oid})).all():
                device_scope.setdefault(did, wid)
        elif s.scope_type == "location" and oid:
            for (sid,) in (await session.execute(text("SELECT id FROM subnets WHERE location_id = :l"), {"l": oid})).all():
                subnet_scope.setdefault(sid, wid)
            for (did,) in (await session.execute(text("""
                SELECT d.id FROM devices d LEFT JOIN racks r ON r.id = d.rack_id
                 WHERE d.location_id = :l OR r.location_id = :l
            """), {"l": oid})).all():
                device_scope.setdefault(did, wid)
        elif s.scope_type == "rack" and oid:
            for (did,) in (await session.execute(text("SELECT id FROM devices WHERE rack_id = :r"), {"r": oid})).all():
                device_scope.setdefault(did, wid)
        elif s.scope_type == "device" and oid:
            device_scope.setdefault(oid, wid)
        elif s.scope_type == "integration" and s.integration_kind:
            idx._put(idx.integrations, (s.integration_kind, str(oid) if oid else None), wid)
        elif s.scope_type == "scan_agent" and oid:
            idx._put(idx.agents, str(oid), wid)

    for sid, wid in subnet_scope.items():
        idx._put(idx.subnet_ids, sid, wid)
    for did, wid in device_scope.items():
        idx._put(idx.device_ids, did, wid)
    if subnet_scope:
        from app.core.sqlin import in_values
        from app.models.subnet import Subnet
        for sid, cidr in (await session.execute(
                select(Subnet.id, Subnet.cidr).where(in_values(Subnet.id, list(subnet_scope))))).all():
            try:
                idx.networks.append((ipaddress.ip_network(str(cidr), strict=False), subnet_scope[sid]))
            except ValueError:
                continue
    # 展開成 IP（id 與文字）：直接選的 IP、子網路裡的、裝置名下的（含主要 IP）
    ip_ids = list(idx.ip_ids)
    prim: dict[uuid.UUID, uuid.UUID] = {}
    if device_scope:
        for pid, did in (await session.execute(text(
                "SELECT primary_ip_id, id FROM devices WHERE id = ANY(:devs) AND primary_ip_id IS NOT NULL"),
                {"devs": list(device_scope)})).all():
            prim[pid] = did
    if ip_ids or subnet_scope or device_scope:
        rows = (await session.execute(text("""
            SELECT a.id, host(a.ip) AS ip, lower(a.hostname) AS hostname, a.subnet_id, a.device_id
              FROM ip_addresses a
             WHERE a.id = ANY(:ips) OR a.subnet_id = ANY(:subs) OR a.device_id = ANY(:devs)
                OR a.id IN (SELECT primary_ip_id FROM devices WHERE id = ANY(:devs) AND primary_ip_id IS NOT NULL)
        """), {"ips": ip_ids, "subs": list(subnet_scope), "devs": list(device_scope)})).all()
        for r in rows:
            wid = idx.ip_ids.get(r.id) or subnet_scope.get(r.subnet_id) or device_scope.get(r.device_id) \
                or device_scope.get(prim.get(r.id))
            if wid is None:
                continue
            idx._put(idx.ip_ids, r.id, wid)
            idx._put(idx.ip_texts, r.ip, wid)
            if r.hostname:
                idx._put(idx.hostnames, r.hostname, wid)

    # 整合與掃描代理：連線的主機在維護範圍內也算
    if idx.ip_texts or idx.networks or idx.hostnames:
        from urllib.parse import urlsplit
        for kind, _label, model in reg.models():
            for row in (await session.execute(select(model))).scalars():
                for attr in ("api_url", "base_url", "host", "hbbs_host", "auth_url", "url", "server"):
                    raw = str(getattr(row, attr, "") or "").strip()
                    if not raw:
                        continue
                    host = (urlsplit(raw if "://" in raw else f"//{raw}").hostname or "").lower()
                    wid = idx.for_ip(ip=host) or idx.hostnames.get(host)
                    if wid:
                        idx._put(idx.integrations, (kind, str(row.id)), wid)
                        break
        from app.models.scan_agent import ScanAgent
        for a in (await session.execute(select(ScanAgent))).scalars():
            if a.last_source_ip and (wid := idx.for_ip(ip=a.last_source_ip)):
                idx._put(idx.agents, str(a.id), wid)


async def build_index(session: AsyncSession, now: datetime | None = None) -> Index:
    now = now or datetime.now(UTC)
    idx = Index()
    res = await session.execute(select(MaintenanceWindow).where(or_(
        and_(MaintenanceWindow.schedule_type == "once", MaintenanceWindow.starts_at <= now,
             MaintenanceWindow.ends_at > now),
        and_(MaintenanceWindow.schedule_type == "weekly",
             MaintenanceWindow.valid_until >= (now - timedelta(days=1)).date()))))
    cands = list(res.scalars())
    for w in cands:
        occ = occurrence(w, now)
        if occ:
            idx.windows[w.id] = w
            idx.occurrences[w.id] = occ[0]
    if not idx.windows:
        return idx
    scopes = list((await session.execute(
        select(MaintenanceScope).where(MaintenanceScope.window_id.in_(list(idx.windows))))).scalars())  # bounded: 進行中的維護
    await _expand(session, idx, scopes)
    return idx


async def active_for_object(session: AsyncSession, object_type: str, object_id: uuid.UUID, *,
                            integration_kind: str | None = None,
                            now: datetime | None = None) -> list[tuple[MaintenanceWindow, datetime | None]]:
    """這個物件現在被哪些維護涵蓋：[(維護, 這一次結束時間)]，依結束時間排序。

    物件頁的橫幅（REST /maintenance/active）與 AI 工具（get_ip_detail／get_device）共用同一份判斷。"""
    now = now or datetime.now(UTC)
    idx = await build_index(session, now)
    if not idx.windows:
        return []
    wids: set[uuid.UUID] = set()
    if object_type == "ip":
        if (wid := idx.for_ip(ip_id=object_id)):
            wids.add(wid)
    elif object_type == "device":
        if object_id in idx.device_ids:
            wids.add(idx.device_ids[object_id])
    elif object_type == "subnet":
        if object_id in idx.subnet_ids:
            wids.add(idx.subnet_ids[object_id])
    elif object_type == "integration" and integration_kind:
        if (wid := idx.for_key(f"integration:{integration_kind}:{object_id}")):
            wids.add(wid)
    elif object_type == "scan_agent":
        if (wid := idx.for_key(f"agent:scan:{object_id}")):
            wids.add(wid)
    # 直接選了這個物件的維護（區段、機櫃、地點、單位…）
    for (wid,) in (await session.execute(select(MaintenanceScope.window_id).where(
            MaintenanceScope.scope_type == object_type, MaintenanceScope.object_id == object_id))).all():
        if wid in idx.windows:
            wids.add(wid)
    out = []
    for wid in wids:
        w = idx.windows[wid]
        occ = occurrence(w, now)
        out.append((w, occ[1] if occ else None))
    return sorted(out, key=lambda x: str(x[1]))


async def get_index(session: AsyncSession) -> Index:
    """一輪同步裡共用一份（60 秒內不重建）。"""
    cached = session.info.get(_CACHE_KEY)
    if cached and _time.monotonic() - cached[0] < _INDEX_TTL:
        return cached[1]
    idx = await build_index(session)
    session.info[_CACHE_KEY] = (_time.monotonic(), idx)
    return idx


def invalidate(session: AsyncSession) -> None:
    session.info.pop(_CACHE_KEY, None)


async def record_suppression(session: AsyncSession, idx: Index, wid: uuid.UUID, *, event: str, key: str,
                             label: str | None) -> None:
    occ = idx.occurrences.get(wid)
    if occ is None:
        return
    now = datetime.now(UTC)
    await session.execute(pg_insert(MaintenanceSuppression).values(
        id=uuid.uuid4(), window_id=wid, occurrence=occ, event=event, key=key[:255], label=(label or "")[:500] or None,
        first_at=now, count=1,
    ).on_conflict_do_update(constraint="uq_maintenance_suppressions_key",
                            set_={"count": MaintenanceSuppression.count + 1}))


#: 狀態類告警 key 的前綴 → 通知事件（記錄被靜音的是哪一類）
KEY_EVENT = {"integration": "integration.sync_failed", "agent": "agent.offline", "scan_load": "agent.overloaded",
             "dhcp": "dhcp.pool_exhausted"}


async def muted_key(session: AsyncSession, key: str, *, label: str | None = None) -> bool:
    """state_alert 用：這個對象現在被維護靜音？是的話順便記一筆被靜音。"""
    idx = await get_index(session)
    if not idx.windows:
        return False
    wid = idx.for_key(key)
    if wid is None or idx.behavior(wid) != "mute":
        return False
    await record_suppression(session, idx, wid, event=KEY_EVENT.get(key.split(":")[0], "state"), key=key,
                             label=label or key)
    return True


# ─────────────────── 每輪：開始 / 結束 ───────────────────

async def _still_failing(session: AsyncSession, keys: list[str]) -> int:
    from app.models.system_setting import SystemSetting
    from app.services.state_alert import ALERT_STATE_KEY
    row = await session.get(SystemSetting, ALERT_STATE_KEY)
    state = dict(row.value or {}) if row else {}
    return sum(1 for k in keys if isinstance(state.get(k), dict) and int(state[k].get("fails") or 0) > 0)


async def _notify(session: AsyncSession, w: MaintenanceWindow, *, event: str, title: str, body: str,
                  title_key: str, body_key: str, params: dict[str, Any]) -> None:
    from app.services.notification import notify_admins_event
    await notify_admins_event(session, event=event, title=title, body=body, severity="info",
                              link=f"/maintenance/{w.id}", object_type="maintenance", object_id=w.id,
                              title_key=title_key, body_key=body_key, params=params)


async def _end(session: AsyncSession, w: MaintenanceWindow, occ_start: datetime) -> dict[str, int]:
    rows = list((await session.execute(select(MaintenanceSuppression).where(
        MaintenanceSuppression.window_id == w.id, MaintenanceSuppression.occurrence == occ_start))).scalars())
    state_keys = [r.key for r in rows if not r.event.startswith("anomaly.")]
    failing = await _still_failing(session, state_keys)
    anomaly_left = 0
    if any(r.event.startswith("anomaly.") for r in rows):
        # 被靜音的異常沒寫進「看過」清單：拿最近一次偵測結果再跑一次通知，結束時還在的會在這裡補發
        from app.services.anomaly import _notify_categories
        from app.services.system_config import get_anomaly_report
        report = await get_anomaly_report(session)
        if isinstance(report, dict) and report:
            invalidate(session)
            anomaly_left = await _notify_categories(session, report, only_new=True)
    summary = {"suppressed": len(rows), "still_failing": failing, "anomaly_categories": anomaly_left}
    await _notify(session, w, event=EVENT_ENDED, title=f"維護結束：{w.title}",
                  body=f"維護「{w.title}」已結束：期間靜音 {len(rows)} 則，結束時仍未恢復 {failing} 則（會照常發出）。",
                  title_key="notif.maint_ended", body_key="notif.maint_ended_body",
                  params={"title": w.title, "suppressed": len(rows), "failing": failing})
    return summary


async def tick(session: AsyncSession, now: datetime | None = None) -> dict[str, int]:
    """同步排程每輪呼叫：偵測維護開始與結束，發通知與摘要，結束時補發。"""
    now = now or datetime.now(UTC)
    res = await session.execute(select(MaintenanceWindow).where(or_(
        MaintenanceWindow.active_since.is_not(None),
        and_(MaintenanceWindow.schedule_type == "once", MaintenanceWindow.starts_at <= now,
             MaintenanceWindow.ends_at > now),
        and_(MaintenanceWindow.schedule_type == "weekly",
             MaintenanceWindow.valid_until >= (now - timedelta(days=1)).date()))))
    rows = list(res.scalars())
    started = ended = 0
    for w in rows:
        occ = occurrence(w, now)
        if w.active_since and (occ is None or occ[0] != w.active_since):
            await _end(session, w, w.active_since)
            w.active_since = None
            ended += 1
        if occ and w.active_since is None:
            w.active_since = occ[0]
            started += 1
            await _notify(session, w, event=EVENT_STARTED, title=f"維護開始：{w.title}",
                          body=f"維護「{w.title}」開始，到 {occ[1].astimezone(tz()):%m-%d %H:%M}；範圍內的告警暫時靜音。",
                          title_key="notif.maint_started", body_key="notif.maint_started_body",
                          params={"title": w.title, "until": occ[1].isoformat()})
        await session.flush()
    if started or ended:
        invalidate(session)
    return {"started": started, "ended": ended}


def today_local() -> date:
    return datetime.now(tz()).date()
