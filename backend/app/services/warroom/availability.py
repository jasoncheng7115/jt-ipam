"""戰情室 10 IP 可用性追蹤（2026-10-23 新增主題；規格：使用者交付的 JT-IPAM-Warroom-Expansion SPEC.md §7）。

追蹤清單：沿用儀表板「IP 可用性追蹤」卡片的清單（user_preferences.pinned["uptime_ips"]，最多 30 個），
看的是觀看者自己的清單；電視用顯示權杖時是代表帳號的清單。只列觀看者看得到、而且在顯示範圍內的 IP
（common.scoped_visibility）；其餘的只回一個數量，不回內容。

資料來源與粒度（與 services/uptime.py 同一套規則）：
- 狀態轉換：ip_change_log 的 effective_status（有時間戳，recompute_effective_status 每輪寫入，
  已經套用系統設定的失聯門檻；不會因為單次採集失敗就判離線）
- 逐日觀測：ip_liveness_days（UTC 日；當天曾被會過期的來源看到 up、曾判離線 down）

重建（timeline → 套逐日觀測 → 切尾 → 分格）：
1. 依狀態轉換排出時間線：online*（來源會過期的）＝上線、offline*＝離線、其餘（unknown、
   online (arp) 這種不會過期的來源）＝未知。視窗開始前的狀態取前一筆轉換；視窗內第一筆之前取它的舊值；
   從沒轉換過但目前有存活來源的，用目前狀態從建立時間回填（uptime.py 的規則）。建立之前一律未知
2. 有逐日觀測的日子（觀測開始之後）：那天沒有記錄＝整天未知；有記錄時，當天沒被看到（up=False）的
   上線段改成未知、當天沒判過離線（down=False）的離線段改成未知。觀測開始之前的舊日子只能靠轉換推估，
   狀態宣稱的來源現在已經不在的就不延續（uptime.carry_forward_ok）
3. 最後觀察不無限延伸：最後一段上線只延續到「最新的會過期證據＋門檻分鐘」；最後一段離線只延續到
   「掃描代理（子網路指定的、IP 沒有略過 ping）或 LibreNMS 最後一次運作＋門檻分鐘」，沒有在運作的
   探測來源就從轉為離線那一刻起改成未知（來源故障、過期、沒在跑都是未知，不是離線）
4. 分格：格內同時有上線與離線＝mixed（不會只取最後一次而把中斷蓋掉）；只有離線（其餘未知）＝離線；
   上線佔一半以上而且沒有離線＝上線；其餘＝未知。還沒到的時間是 future（不畫）

可用率＝上線有效時長／（上線＋離線有效時長）；另給資料覆蓋率＝有效時長／視窗已經過的時長；
有效時長為零時是 None（畫面顯示「—」）。這是 IP 觀察狀態的時間比例，不是應用服務 SLA。

反覆中斷：最近 FLAP_WINDOW 內「確定的」上線↔離線切換（轉換前後兩個值都是已知狀態）達 FLAP_MIN 次；
未知不算切換。

時間範圍：24 小時（每格 1 小時）、7 日（每格 6 小時）、30 日、90 日（每格 1 日）。只提供歷史資料涵蓋得到的
範圍（最早的轉換或逐日觀測要早於範圍起點）；不夠時降到涵蓋得到的最大範圍，最少 24 小時，並在畫面標示。
格子依觀看帳號的時區對齊（使用者偏好，預設 Asia/Taipei）。
"""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sqlin import in_values
from app.models.address import IPAddress
from app.models.ip_change_log import IPChangeLog
from app.models.ip_liveness import IPLivenessDay
from app.services.uptime import carry_forward_ok, status_is_up
from app.services.warroom.common import paginate, section

PIN_NAMESPACE = "uptime_ips"
MAX_TRACKED = 30                       # 與儀表板卡片的上限一致
PER_PAGE = 6
FLAP_WINDOW = timedelta(hours=24)
FLAP_MIN = 4
EVENTS_MAX = 3
DEFAULT_WINDOW = "7d"
DEFAULT_TZ = "Asia/Taipei"


@dataclass(frozen=True)
class Window:
    key: str
    span: timedelta
    step: timedelta
    cells: int
    group: int                         # 表頭每幾格一個標籤
    label_fmt: str


WINDOWS: dict[str, Window] = {
    "24h": Window("24h", timedelta(hours=24), timedelta(hours=1), 24, 3, "%H:%M"),
    "7d": Window("7d", timedelta(days=7), timedelta(hours=6), 28, 4, "%m/%d"),
    "30d": Window("30d", timedelta(days=30), timedelta(days=1), 30, 5, "%m/%d"),
    "90d": Window("90d", timedelta(days=90), timedelta(days=1), 90, 15, "%m/%d"),
}
LADDER = ("90d", "30d", "7d", "24h")


# ───────────────────────────── 純函式（單元測試） ─────────────────────────────

@dataclass
class Seg:
    t0: datetime
    t1: datetime
    state: str                         # online／offline／unknown
    raw: str | None = None             # 這段來自哪個 effective_status 值（舊日子的延續規則要用）


def state_of(value: str | None) -> str:
    up = status_is_up(value)
    return "online" if up is True else "offline" if up is False else "unknown"


def build_timeline(start: datetime, end: datetime, initial: str | None,
                   changes: list[tuple[datetime, str | None]]) -> list[Seg]:
    """依轉換排出 [start, end) 的時間線。initial：start 當下的 effective_status 值（None＝未知）。"""
    segs: list[Seg] = []
    cur_t, cur_v = start, initial
    for ts, nv in changes:
        if ts <= start:
            cur_v = nv
            continue
        if ts >= end:
            break
        if ts > cur_t:
            segs.append(Seg(cur_t, ts, state_of(cur_v), cur_v))
        cur_t, cur_v = ts, nv
    if end > cur_t:
        segs.append(Seg(cur_t, end, state_of(cur_v), cur_v))
    return merge(segs)


def merge(segs: list[Seg]) -> list[Seg]:
    out: list[Seg] = []
    for s in segs:
        if s.t1 <= s.t0:
            continue
        if out and out[-1].state == s.state and out[-1].t1 == s.t0 and out[-1].raw == s.raw:
            out[-1] = Seg(out[-1].t0, s.t1, s.state, s.raw)
        else:
            out.append(s)
    return out


def _split_days(segs: list[Seg]) -> list[Seg]:
    """在 UTC 日界切開（逐日觀測是 UTC 日）。"""
    out: list[Seg] = []
    for s in segs:
        t = s.t0
        while t < s.t1:
            nxt = datetime.combine(t.astimezone(UTC).date() + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
            out.append(Seg(t, min(nxt, s.t1), s.state, s.raw))
            t = min(nxt, s.t1)
    return out


def apply_observations(segs: list[Seg], obs: dict[date, tuple[bool, bool]], observed_from: date | None,
                       carry: Any = None) -> list[Seg]:
    """逐日觀測：觀測開始後沒記錄的日子整天未知；有記錄時沒被證實的狀態改成未知。
    觀測開始前的舊日子：carry(raw) 為 False 的延續段改成未知。"""
    out: list[Seg] = []
    for s in _split_days(segs):
        d = s.t0.astimezone(UTC).date()
        st = s.state
        if observed_from is not None and d >= observed_from:
            rec = obs.get(d)
            if rec is None:
                st = "unknown"
            elif (st == "online" and not rec[0]) or (st == "offline" and not rec[1]):
                st = "unknown"
        elif st != "unknown" and carry is not None and not carry(s.raw):
            st = "unknown"
        out.append(Seg(s.t0, s.t1, st, s.raw))
    return merge(out)


def unknown_before(segs: list[Seg], t: datetime | None) -> list[Seg]:
    """t（建立時間）之前一律未知。"""
    if t is None:
        return segs
    out: list[Seg] = []
    for s in segs:
        if s.t1 <= t:
            out.append(Seg(s.t0, s.t1, "unknown", s.raw))
        elif s.t0 < t:
            out += [Seg(s.t0, t, "unknown", s.raw), Seg(t, s.t1, s.state, s.raw)]
        else:
            out.append(s)
    return merge(out)


def cap_tail(segs: list[Seg], valid_until: dict[str, datetime | None]) -> list[Seg]:
    """最後一段（延續到現在的那段）只延續到該狀態的有效期限，之後是未知。"""
    if not segs or segs[-1].state == "unknown":
        return segs
    last = segs[-1]
    until = valid_until.get(last.state)
    if until is None:
        until = last.t0
    if until >= last.t1:
        return segs
    head = [Seg(last.t0, until, last.state, last.raw)] if until > last.t0 else []
    return merge([*segs[:-1], *head, Seg(max(until, last.t0), last.t1, "unknown", last.raw)])


def durations(segs: list[Seg], t0: datetime, t1: datetime) -> dict[str, float]:
    out = {"online": 0.0, "offline": 0.0, "unknown": 0.0}
    for s in segs:
        a, b = max(s.t0, t0), min(s.t1, t1)
        if b > a:
            out[s.state] += (b - a).total_seconds()
    return out


def cell_state(segs: list[Seg], t0: datetime, t1: datetime, now: datetime) -> str:
    if t0 >= now:
        return "future"
    t1 = min(t1, now)
    d = durations(segs, t0, t1)
    span = (t1 - t0).total_seconds()
    if d["online"] > 0 and d["offline"] > 0:
        return "mixed"
    if d["offline"] > 0:
        return "offline"
    if span > 0 and d["online"] >= span / 2:
        return "online"
    return "unknown"


def confirmed_flip(old: str | None, new: str | None) -> bool:
    """確定的上線↔離線切換：前後都是已知狀態而且不同（未知不算）。"""
    a, b = status_is_up(old), status_is_up(new)
    return a is not None and b is not None and a != b


def pct(part: float, whole: float) -> float | None:
    return round(part * 100 / whole, 1) if whole > 0 else None


def cell_edges(win: Window, now: datetime, tz: ZoneInfo) -> list[datetime]:
    """格子邊界（UTC）。24 小時：對齊整點、最後一格含現在；7／30／90 日：對齊當地午夜，今天是最後一天。"""
    local = now.astimezone(tz)
    if win.key == "24h":
        first = local.replace(minute=0, second=0, microsecond=0) - win.step * (win.cells - 1)
    else:
        days = math.ceil(win.cells * win.step / timedelta(days=1))
        first = local.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
    first = first.replace(tzinfo=None)
    return [(first + win.step * i).replace(tzinfo=tz).astimezone(UTC) for i in range(win.cells + 1)]


def choose_window(requested: str, history_from: datetime | None, now: datetime) -> tuple[Window, list[str]]:
    """(用的範圍, 歷史涵蓋得到的範圍)。要求的範圍不夠就降到涵蓋得到的最大範圍，最少 24 小時。"""
    supported = [k for k in LADDER if history_from is not None and history_from <= now - WINDOWS[k].span]
    if "24h" not in supported:
        supported.append("24h")
    req = requested if requested in WINDOWS else DEFAULT_WINDOW
    for k in LADDER[LADDER.index(req):]:
        if k in supported:
            return WINDOWS[k], [k for k in reversed(LADDER) if k in supported]
    return WINDOWS["24h"], ["24h"]


# ───────────────────────────── 資料 ─────────────────────────────

@dataclass
class Tracked:
    ip: IPAddress
    changes: list[tuple[datetime, str | None, str | None]] = field(default_factory=list)   # (ts, old, new)
    before: str | None = None          # 視窗開始前最後一筆轉換的新值
    has_before: bool = False


async def tracked_ids(session: AsyncSession, user: Any) -> list[uuid.UUID]:
    """觀看者（或顯示權杖的代表帳號）在儀表板設定的追蹤清單，保留排的順序。"""
    from app.models.user import UserPreference
    pref = await session.get(UserPreference, user.id)
    raw = ((pref.pinned or {}).get(PIN_NAMESPACE) if pref else None) or []
    out: list[uuid.UUID] = []
    for x in raw[:MAX_TRACKED]:
        try:
            out.append(uuid.UUID(str(x)))
        except ValueError:
            continue
    return list(dict.fromkeys(out))


async def _timezone(session: AsyncSession, user: Any) -> ZoneInfo:
    from app.models.user import UserPreference
    pref = await session.get(UserPreference, user.id)
    try:
        return ZoneInfo((pref.timezone if pref else None) or DEFAULT_TZ)
    except Exception:
        return ZoneInfo(DEFAULT_TZ)


async def _observers(session: AsyncSession, ips: list[IPAddress], sources: set[str]) -> dict[Any, datetime | None]:
    """每個 IP 的離線判定者最後一次運作的時間：子網路指定的掃描代理（IP 沒有略過 ping），或 LibreNMS。"""
    from app.models.librenms import LibreNMSInstance
    from app.models.scan_agent import ScanAgent
    from app.models.subnet import Subnet

    sub_ids = list({i.subnet_id for i in ips})
    agents = dict((await session.execute(
        select(Subnet.id, ScanAgent.last_seen_at).join(ScanAgent, ScanAgent.id == Subnet.scan_agent_id)
        .where(in_values(Subnet.id, sub_ids), ScanAgent.enabled.is_(True)))).all()) if sub_ids else {}
    lnms = await session.scalar(select(LibreNMSInstance.last_sync_at).where(
        LibreNMSInstance.enabled.is_(True), LibreNMSInstance.use_for_status.is_(True))
        .order_by(LibreNMSInstance.last_sync_at.desc().nulls_last()).limit(1))
    out: dict[Any, datetime | None] = {}
    for ip in ips:
        cands: list[datetime] = []
        if "scanner" in sources and not ip.exclude_from_ping and agents.get(ip.subnet_id):
            cands.append(agents[ip.subnet_id])
        if "librenms" in sources and ip.last_seen_librenms and lnms:
            cands.append(lnms)
        out[ip.id] = max(cands) if cands else None
    return out


def newest_evidence(ip: IPAddress, sources: set[str]) -> datetime | None:
    """被採信、而且會過期的來源最近一次看到它的時間（判上線的證據）。"""
    from app.services import arp_seen
    cands = [ts for name, ts in (("scanner", ip.last_seen_scanner), ("librenms", ip.last_seen_librenms),
                                 ("wazuh", ip.last_seen_wazuh), ("zabbix", ip.last_seen_zabbix),
                                 ("checkmk", ip.last_seen_checkmk))
             if name in sources and ts]
    d_seen, _src = arp_seen.newest_aging(ip, sources)
    if d_seen:
        cands.append(d_seen)
    return max(cands) if cands else None


async def build(session: AsyncSession, *, user: Any, cfg: dict[str, Any], page: int = 1,
                now: datetime | None = None, window: str | None = None) -> dict[str, Any]:
    from app.services.system_config import get_liveness_config
    from app.services.uptime import _aging_keys
    from app.services.warroom.common import scoped_visibility

    now = now or datetime.now(UTC)
    ids = await tracked_ids(session, user)
    visible = await scoped_visibility(session, user, cfg)
    ips: list[IPAddress] = []
    if ids and (visible is None or visible):
        stmt = select(IPAddress).where(in_values(IPAddress.id, ids))
        if visible is not None:
            stmt = stmt.where(in_values(IPAddress.subnet_id, list(visible)))
        got = {i.id: i for i in (await session.execute(stmt)).scalars().all()}
        ips = [got[i] for i in ids if i in got]
    excluded = len(ids) - len(ips)

    live = await get_liveness_config(session)
    minutes = timedelta(minutes=int(live["minutes"]))
    sources = set(live["sources"])
    tz = await _timezone(session, user)

    ip_ids = [i.id for i in ips]
    # 歷史起點：最早的轉換或逐日觀測（決定支援哪些範圍）
    first_change = None
    observed_from: date | None = None
    obs: dict[Any, dict[date, tuple[bool, bool]]] = {}
    if ip_ids:
        from sqlalchemy import func
        first_change = await session.scalar(select(func.min(IPChangeLog.created_at)).where(
            in_values(IPChangeLog.ip_id, ip_ids), IPChangeLog.field == "effective_status"))
        observed_from = await session.scalar(select(func.min(IPLivenessDay.day)).where(
            in_values(IPLivenessDay.ip_id, ip_ids)))
    cands = [t for t in (first_change, datetime.combine(observed_from, datetime.min.time(), tzinfo=UTC)
                         if observed_from else None) if t is not None]
    history_from = min(cands) if cands else None
    win, supported = choose_window(window or cfg.get("availability_window") or DEFAULT_WINDOW, history_from, now)
    edges = cell_edges(win, now, tz)
    start = edges[0]
    flap_since = now - FLAP_WINDOW
    fetch_from = min(start, flap_since)

    tracked = {i.id: Tracked(i) for i in ips}
    if ip_ids:
        for iid, ts, old, new in (await session.execute(
                select(IPChangeLog.ip_id, IPChangeLog.created_at, IPChangeLog.old_value, IPChangeLog.new_value)
                .where(in_values(IPChangeLog.ip_id, ip_ids), IPChangeLog.field == "effective_status",
                       IPChangeLog.created_at >= fetch_from)
                .order_by(IPChangeLog.created_at))).all():
            tracked[iid].changes.append((ts, old, new))
        for iid, new in (await session.execute(
                select(IPChangeLog.ip_id, IPChangeLog.new_value)
                .where(in_values(IPChangeLog.ip_id, ip_ids), IPChangeLog.field == "effective_status",
                       IPChangeLog.created_at < fetch_from)
                .distinct(IPChangeLog.ip_id).order_by(IPChangeLog.ip_id, IPChangeLog.created_at.desc()))).all():
            tracked[iid].before, tracked[iid].has_before = new, True
        for iid, d, up, down in (await session.execute(
                select(IPLivenessDay.ip_id, IPLivenessDay.day, IPLivenessDay.up, IPLivenessDay.down)
                .where(in_values(IPLivenessDay.ip_id, ip_ids),
                       IPLivenessDay.day >= fetch_from.astimezone(UTC).date()))).all():
            obs.setdefault(iid, {})[d] = (bool(up), bool(down))
    observers = await _observers(session, ips, sources) if ips else {}

    rows: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    for order, tr in enumerate(tracked.values()):
        ip = tr.ip
        aging = _aging_keys(ip.arp_seen)
        has_live = bool(ip.last_seen_scanner or ip.last_seen_librenms or aging)

        def carry(raw: str | None, ip: IPAddress = ip, aging: set[str] = aging) -> bool:
            return carry_forward_ok(raw, has_scanner=bool(ip.last_seen_scanner),
                                    has_librenms=bool(ip.last_seen_librenms), present=aging)

        changes = [(ts, new) for ts, _old, new in tr.changes]
        if tr.has_before:
            initial = tr.before
        elif tr.changes:
            initial = tr.changes[0][1]                 # 第一筆轉換之前的狀態＝它的舊值
        elif has_live and status_is_up(ip.effective_status) is not None:
            initial = ip.effective_status              # 從沒斷過才會沒有轉換：用目前狀態回填
        else:
            initial = None
        segs = build_timeline(fetch_from, now, initial, changes)
        segs = unknown_before(segs, ip.created_at)
        segs = apply_observations(segs, obs.get(ip.id, {}), observed_from, carry)
        evidence = newest_evidence(ip, sources)
        observer = observers.get(ip.id)
        segs = cap_tail(segs, {"online": evidence + minutes if evidence else None,
                               "offline": observer + minutes if observer else None})

        cells = [cell_state(segs, edges[k], edges[k + 1], now) for k in range(win.cells)]
        d = durations(segs, start, now)
        valid = d["online"] + d["offline"]
        current = segs[-1].state if segs else "unknown"     # 時間線一路排到現在
        flips = [(ts, old, new) for ts, old, new in tr.changes if ts >= flap_since and confirmed_flip(old, new)]
        flapping = len(flips) >= FLAP_MIN
        rows.append({
            "id": str(ip.id), "ip": str(ip.ip).split("/")[0], "name": (ip.hostname or "").strip() or None,
            "cells": cells, "state": current, "flapping": flapping, "flips": len(flips),
            "availability": pct(d["online"], valid), "coverage": pct(valid, (now - start).total_seconds()),
            "last_valid_at": evidence.isoformat() if evidence else None, "_order": order,
        })
        if flapping:
            events.append({"kind": "flapping", "at": flips[-1][0].isoformat(), "id": str(ip.id),
                           "ip": rows[-1]["ip"], "name": rows[-1]["name"], "count": len(flips),
                           "last": state_of(flips[-1][2])})
            continue
        last_off: datetime | None = None
        for ts, old, new in tr.changes:
            if not confirmed_flip(old, new):
                continue
            if status_is_up(new) is False:
                last_off = ts
                if ts >= start:
                    events.append({"kind": "offline", "at": ts.isoformat(), "id": str(ip.id),
                                   "ip": rows[-1]["ip"], "name": rows[-1]["name"]})
            elif ts >= start:
                events.append({"kind": "recovered", "at": ts.isoformat(), "id": str(ip.id),
                               "ip": rows[-1]["ip"], "name": rows[-1]["name"],
                               "down_s": int((ts - last_off).total_seconds()) if last_off else None})

    rank = {"offline": 0, "unknown": 2, "online": 3}
    rows.sort(key=lambda r: (0 if r["state"] == "offline" else 1 if r["flapping"] else rank[r["state"]], r["_order"]))
    for r in rows:
        r.pop("_order", None)
    events.sort(key=lambda e: e["at"], reverse=True)
    shown, page, pages = paginate(rows, PER_PAGE, page)
    last_valid = max((r["last_valid_at"] for r in rows if r["last_valid_at"]), default=None)
    columns = [{"at": edges[k].isoformat(), "span": min(win.group, win.cells - k),
                "label": edges[k].astimezone(tz).strftime(win.label_fmt)} for k in range(0, win.cells, win.group)]
    return {
        "key": "availability", "data_at": now.isoformat(), "page": page, "pages": pages, "per_page": PER_PAGE,
        "total": len(rows),
        "metrics": {
            "tracked": len(rows),
            "down_now": sum(1 for r in rows if r["state"] == "offline"),
            "flapping": sum(1 for r in rows if r["flapping"]),
        },
        "body": section("ok" if rows else "empty", excluded=excluded),
        "rows": shown,
        "events": events[:EVENTS_MAX],
        "window": {"key": win.key, "requested": window or cfg.get("availability_window") or DEFAULT_WINDOW,
                   "supported": supported, "cells": win.cells, "step_s": int(win.step.total_seconds()),
                   "start": start.isoformat(), "history_from": history_from.isoformat() if history_from else None,
                   "columns": columns},
        "last_valid_at": last_valid,
        "definitions": {"flap_window_hours": int(FLAP_WINDOW.total_seconds() // 3600), "flap_min": FLAP_MIN,
                        "liveness_minutes": int(live["minutes"]), "timezone": str(tz.key)},
    }
