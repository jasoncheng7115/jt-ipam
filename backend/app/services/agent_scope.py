"""替「未裝 Agent 的 IP」補上所屬的子網路、區段與單位（Wazuh 與 OCS 整合頁共用）。

畫面要能依這三種篩選。單位的判斷跟權限一致（授權上層就涵蓋下層）：
IP 自己掛的單位 → 子網路的 → 區段的。只看 IP 自己的欄位的話，單位掛在區段上的
整批 IP 都會篩不出來。
"""
from __future__ import annotations

import uuid
from typing import Any, NamedTuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sqlin import in_values
from app.models.address import IPAddress
from app.models.customer import Customer
from app.models.section import Section
from app.models.subnet import Subnet


def _iso(v: Any) -> str | None:
    return v.isoformat() if v else None


async def annotate_scope(session: AsyncSession, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ids = [uuid.UUID(str(r["ip_address_id"])) for r in rows if r.get("ip_address_id")]
    if not ids:
        return rows
    info = {}
    live: dict[str, dict[str, Any]] = {}
    for (rid, ip_cust, sub_id, cidr, sub_cust, sec_id, sec_name, sec_cust, scan_enabled,
         exclude, s_scan, s_lnms, s_arp, s_wazuh, s_zbx, s_cmk, arp_seen, kind, model) in (await session.execute(
        select(IPAddress.id, IPAddress.customer_id, Subnet.id, Subnet.cidr, Subnet.customer_id,
               Section.id, Section.name, Section.customer_id, Subnet.scan_enabled,
               not_probed_expr(), IPAddress.last_seen_scanner, IPAddress.last_seen_librenms,
               IPAddress.last_seen_arp, IPAddress.last_seen_wazuh, IPAddress.last_seen_zabbix,
               IPAddress.last_seen_checkmk, IPAddress.arp_seen, IPAddress.device_kind, IPAddress.device_model)
        .join(Subnet, Subnet.id == IPAddress.subnet_id)
        .join(Section, Section.id == Subnet.section_id, isouter=True)
        .where(in_values(IPAddress.id, ids))           # 大站台一次就是幾萬個
    )).all():
        info[str(rid)] = (ip_cust or sub_cust or sec_cust, sub_id, cidr, sec_id, sec_name)
        # 上線與否由前端用 IP 清單燈號的同一套規則即時算（classifyAddressLiveness），
        # 這裡只帶那套規則要吃的欄位 —— 不回傳後端的 effective_status 快照，免得兩邊講不一樣
        live[str(rid)] = {
            "last_seen_scanner": _iso(s_scan), "last_seen_librenms": _iso(s_lnms),
            "last_seen_arp": _iso(s_arp), "last_seen_wazuh": _iso(s_wazuh),
            "last_seen_zabbix": _iso(s_zbx), "last_seen_checkmk": _iso(s_cmk), "arp_seen": arp_seen or {},
            "exclude_from_ping": bool(exclude), "subnet_scan_enabled": scan_enabled,
            # 「設備類型」欄（圖示＋名稱，滑過看型號）
            "device_kind": kind, "device_model": model,
        }
    cust_ids = {v[0] for v in info.values() if v[0]}
    names = dict((await session.execute(
        select(Customer.id, Customer.name).where(in_values(Customer.id, cust_ids))
    )).all()) if cust_ids else {}
    for r in rows:
        cust, sub_id, cidr, sec_id, sec_name = info.get(str(r.get("ip_address_id")), (None,) * 5)
        r.update({
            "subnet_id": str(sub_id) if sub_id else None,
            "subnet_cidr": str(cidr) if cidr else None,
            "section_id": str(sec_id) if sec_id else None,
            "section_name": sec_name,
            "customer_id": str(cust) if cust else None,
            "customer_name": names.get(cust) if cust else None,
            **live.get(str(r.get("ip_address_id")), {}),
        })
    return rows


def scope_uuids(obj: Any) -> set[uuid.UUID]:
    """整合的 `scope_subnet_ids`（UUID 字串陣列）→ UUID 集合；空集合＝不限範圍。"""
    out: set[uuid.UUID] = set()
    for s in (getattr(obj, "scope_subnet_ids", None) or []):
        try:
            out.add(uuid.UUID(str(s)))
        except (ValueError, TypeError):
            continue
    return out


def expected_subnets(integrations: list[Any]) -> list[uuid.UUID] | None:
    """「未裝 Agent 的 IP」要看哪些子網路：這些整合的限定範圍的聯集。

    限定了範圍就表示範圍外的機器本來就不歸這套 Wazuh／OCS 管，不該算成缺口（使用者要求，
    2026-09-25）。只要有一個整合沒設範圍（＝全域），或根本沒有整合，就回 None（不限）。
    """
    union: set[uuid.UUID] = set()
    for obj in integrations:
        ids = scope_uuids(obj)
        if not ids:
            return None
        union |= ids
    return sorted(union) if union else None


# ─────────────────── 伺服器端分頁（2026-10-01） ───────────────────
# 大站台 5 萬筆缺口時，整份清單一次回傳要 27～42 MB、瀏覽器裡篩選卡好幾秒。帶 page 時改由這裡篩選與分頁，
# 篩選選項也在這裡算。上線狀態的規則必須與畫面燈號（前端 classifyAddressLiveness）完全相同。

#: 最後一次看到的時間超過門檻、但在門檻 × 這個倍數以內 ＝「近期出現」（與前端 STALE_FACTOR 相同）
STALE_FACTOR = 4


def _parse_ts(v: Any) -> Any:
    from datetime import UTC, datetime
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=UTC)
    try:
        dt = datetime.fromisoformat(str(v))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def not_probed_expr() -> Any:
    """「沒在探測」的 SQL 條件：刻意不偵測，或勾了「上線狀態跟著設備」卻沒掛設備（2026-10-14）。

    後者是多 LAN 路由器的閘道這類不回 ping 但在用的位址：沒有設備可以跟，就不要因為探測沒回應而顯示離線。
    清單與異常偵測把它算進 exclude_from_ping 那一欄送出 —— 前端燈號、「沒在探測」的字樣都跟著同一個判斷。
    """
    from sqlalchemy import and_, or_
    return or_(IPAddress.exclude_from_ping,
               and_(IPAddress.liveness_from_device.is_(True), IPAddress.device_id.is_(None)))


def classify_liveness(row: Any, *, minutes: int, sources: Any, now: Any) -> str:
    """online／stale／offline／unknown —— 與前端 classifyAddressLiveness 逐條對應（改一邊要改另一邊）。

    刻意不偵測（exclude_from_ping）或子網路沒開掃描：根本沒在探測，過期最多降為 unknown，不顯示離線。
    last_seen_dns（AdGuard）不算：它只代表設定裡有這個 IP，每輪都蓋成現在。
    """
    use = set(sources or ())
    no_probe = (bool(getattr(row, "exclude_from_ping", False)) or getattr(row, "subnet_scan_enabled", None) is False
                # 勾了「上線狀態跟著設備」卻沒掛設備：沒有設備可以跟，不算離線（not_probed_expr 的 Python 版）
                or (bool(getattr(row, "liveness_from_device", False)) and getattr(row, "device_id", None) is None))
    cand = [
        getattr(row, "last_seen_scanner", None) if "scanner" in use else None,
        getattr(row, "last_seen_librenms", None) if "librenms" in use else None,
        getattr(row, "last_seen_wazuh", None) if "wazuh" in use else None,
        getattr(row, "last_seen_zabbix", None) if "zabbix" in use else None,
        getattr(row, "last_seen_checkmk", None) if "checkmk" in use else None,
        getattr(row, "last_seen_arp", None) if ("arp" in use or "arp:librenms" in use) else None,
        *[v for k, v in (getattr(row, "arp_seen", None) or {}).items() if k in use],
    ]
    return _liveness_kind([t for t in (_parse_ts(c) for c in cand if c) if t is not None],
                          no_probe=no_probe, minutes=minutes, now=now)


#: 來源 → IPAddress 上的欄位；其餘來源（arp:／vpn:／lease:<廠牌>）在 arp_seen 裡，鍵就是來源名稱
_SOURCE_COLUMNS = {"scanner": "last_seen_scanner", "librenms": "last_seen_librenms", "wazuh": "last_seen_wazuh",
                   "zabbix": "last_seen_zabbix", "checkmk": "last_seen_checkmk", "arp": "last_seen_arp", "arp:librenms": "last_seen_arp"}


def _liveness_kind(ts: list[Any], *, no_probe: bool, minutes: int, now: Any) -> str:
    if ts:
        grace = minutes or 30
        age = (now - max(ts)).total_seconds() / 60
        kind = "online" if age <= grace else ("stale" if age <= grace * STALE_FACTOR else "offline")
        return "unknown" if kind == "offline" and no_probe else kind
    return "unknown" if no_probe else "offline"


_STATUS_ORDER = ("online", "stale", "offline", "unknown")


#: 算好的缺口（含上線狀態）快取多久：翻頁、換篩選時不必每次重查、重算（5 萬筆時一次約 3 秒）。
#: 資料一有變動（IP 或代理表的筆數／最後更新時間）就失效；時間上限讓上線狀態跟著時間走。
_CACHE_TTL = 60.0
_CACHE: dict[Any, tuple[float, Any, Any]] = {}


async def missing_page(
    session: AsyncSession, *, missing: Any, subnet_ids: list[Any] | None, hostnamed_only: bool = True,
    page: int = 1, page_size: int = 100, section_id: Any = None, subnet_id: Any = None,
    customer_id: Any = None, status: str | None = None, q: str | None = None,
    sort: str = "ip", order: str = "asc",
    cache_key: Any = None, version_tables: tuple[Any, ...] = (),
) -> dict[str, Any]:
    """缺口清單的一頁：{items, total, total_all, facets}。`missing` 是「算缺口」的 SQL 條件（各整合自己給）。

    篩選選項比照原本畫面：區段、單位、上線狀態從全部缺口產生；子網路只列所選區段底下的。
    `cache_key`＋`version_tables`（缺口條件用到的其他表，例如 wazuh_agents）：見 _CACHE_TTL。
    """
    import time

    from sqlalchemy import func

    from app.services.system_config import get_liveness_config

    if subnet_ids is not None and not subnet_ids:
        return {"items": [], "total": 0, "total_all": 0,
                "facets": {"sections": [], "subnets": [], "customers": [], "statuses": []}}
    cfg = await get_liveness_config(session)
    version: Any = None
    gaps: _Gaps | None = None
    if cache_key is not None:
        parts = []
        for model in (IPAddress, *version_tables):
            parts.append(tuple((await session.execute(
                select(func.count(), func.max(model.updated_at)))).one()))
        version = (tuple(parts), int(cfg["minutes"]), tuple(sorted(cfg["sources"])))
        # 範圍（整合設定的子網路）與是否只看有主機名稱的，也決定這份缺口長什麼樣
        cache_key = (cache_key, hostnamed_only,
                     None if subnet_ids is None else hash(frozenset(str(x) for x in subnet_ids)))
        hit = _CACHE.get(cache_key)
        if hit and hit[1] == version and time.monotonic() - hit[0] < _CACHE_TTL:
            gaps = hit[2]
    if gaps is None:
        gaps = await _compute_missing(session, missing=missing, subnet_ids=subnet_ids,
                                      hostnamed_only=hostnamed_only, cfg=cfg)
        if cache_key is not None:
            now = time.monotonic()
            for k in [k for k, v in _CACHE.items() if now - v[0] >= _CACHE_TTL]:
                _CACHE.pop(k, None)
            _CACHE[cache_key] = (now, version, gaps)
    return await _page_from(session, gaps, page=page, page_size=page_size, section_id=section_id,
                            subnet_id=subnet_id, customer_id=customer_id, status=status, q=q, sort=sort, order=order)


class _Gaps(NamedTuple):
    """算好的缺口：recs 每筆是（區段, 子網路, 單位, 上線狀態, 搜尋字串, 顯示欄位…），篩選選項也先算好。

    全部用普通 tuple：5 萬筆時 SQLAlchemy Row 逐欄以名稱取值就要將近 1 秒。
    """

    recs: list[tuple[Any, ...]]
    names: dict[str, str]
    sections: list[dict[str, str]]
    customers: list[dict[str, str]]
    statuses: list[dict[str, str]]
    subnets: dict[str | None, list[dict[str, str]]]   # 區段 → 子網路選項；None ＝ 全部
    ranks: dict[str, dict[Any, int]]                   # 排序用的名次，第一次依該欄排序時才算（_ranks）


def _opts(pairs: Any) -> list[dict[str, str]]:
    seen: dict[str, str] = {}
    for value, label in pairs:
        if value and value not in seen:
            seen[value] = str(label or value)
    return sorted(({"value": v, "label": lbl} for v, lbl in seen.items()), key=lambda o: _natural(o["label"]))


async def _compute_missing(session: AsyncSession, *, missing: Any, subnet_ids: list[Any] | None,
                           hostnamed_only: bool, cfg: dict[str, Any]) -> _Gaps:
    """整份缺口只取判斷與篩選要用的欄位；顯示用的各來源時間等翻到那一頁再抓（_page_details）。

    5 萬筆時把每列 5 個時間欄位＋arp_seen JSON 全帶回來、轉成 Python 物件，就要 2 秒多。
    這裡改由資料庫先取「勾選的欄位來源裡最近的一次」（GREATEST 會略過 NULL），arp_seen 只取勾選的鍵；
    判斷規則仍是 classify_liveness 那一套（_liveness_kind）。
    """
    from datetime import UTC, datetime

    from sqlalchemy import DateTime, Text, cast, func, literal, null

    minutes, sources = int(cfg["minutes"]), list(cfg["sources"] or ())
    cols = sorted({_SOURCE_COLUMNS[k] for k in sources if k in _SOURCE_COLUMNS})
    seen_col = (func.greatest(*[getattr(IPAddress, c) for c in cols]) if len(cols) > 1
                else getattr(IPAddress, cols[0]) if cols else cast(null(), DateTime(timezone=True)))
    keyed = [IPAddress.arp_seen.op("->>")(literal(k)).label(f"k{i}") for i, k in enumerate(sources)]
    cust = func.coalesce(IPAddress.customer_id, Subnet.customer_id, Section.customer_id)
    # 位址與編號直接取字串：轉成 ipaddress／UUID 物件再轉回字串又是 2 秒多，這裡只拿來顯示與比對
    stmt = (select(cast(IPAddress.id, Text).label("id"), func.host(IPAddress.ip).label("ip"), IPAddress.hostname,
                   cast(Subnet.id, Text).label("sid"), cast(Subnet.cidr, Text).label("cidr"),
                   cast(Section.id, Text).label("secid"), Section.name.label("secname"),
                   cast(cust, Text).label("cust"), Subnet.scan_enabled.label("subnet_scan_enabled"),
                   not_probed_expr().label("exclude_from_ping"), IPAddress.device_kind, IPAddress.device_model,
                   seen_col.label("seen"), *keyed)
            .join(Subnet, Subnet.id == IPAddress.subnet_id)
            .join(Section, Section.id == Subnet.section_id, isouter=True)
            .where(missing))
    if hostnamed_only:
        stmt = stmt.where(IPAddress.hostname.is_not(None), IPAddress.hostname != "")
    if subnet_ids is not None:
        stmt = stmt.where(in_values(IPAddress.subnet_id, subnet_ids))
    rows = (await session.execute(stmt.order_by(IPAddress.ip))).tuples().all()
    now = datetime.now(UTC)
    recs = []
    by_section: dict[str | None, dict[str, str]] = {}
    sections: dict[str, str] = {}
    cust_ids: set[str] = set()
    for rid, ip, host, sid, cidr, secid, secname, cid, scan_en, excl, kind, model, seen, *keys in rows:
        ts = [t for t in (_parse_ts(v) for v in (seen, *keys) if v) if t is not None]
        st = _liveness_kind(ts, no_probe=bool(excl) or scan_en is False, minutes=minutes, now=now)
        recs.append((secid, sid, cid, st, f"{(host or '').lower()}\n{ip}", rid, ip, host, cidr, secname,
                     scan_en, excl, kind, model))
        by_section.setdefault(secid, {}).setdefault(sid, cidr)
        if secid:
            sections.setdefault(secid, secname)
        if cid:
            cust_ids.add(cid)
    names = {str(k): v for k, v in (await session.execute(select(Customer.id, Customer.name).where(
        in_values(Customer.id, cust_ids)))).all()} if cust_ids else {}
    subnets: dict[str | None, list[dict[str, str]]] = {k: _opts(v.items()) for k, v in by_section.items() if k}
    subnets[None] = _opts(kv for v in by_section.values() for kv in v.items())
    present = {rec[3] for rec in recs}
    return _Gaps(recs=recs, names=names, sections=_opts(sections.items()),
                 customers=_opts((c, names.get(c)) for c in cust_ids),
                 statuses=[{"value": s, "label": s} for s in _STATUS_ORDER if s in present],
                 subnets=subnets, ranks={})


#: 可排序的欄 → recs 裡的位置。名次沿用篩選選項的順序（自然排序；單位依名稱），沒有值的一律排最後。
_SORT_FIELD = {"section": 0, "subnet": 1, "customer": 2, "status": 3, "hostname": 7, "device_kind": 12}


def _ranks(gaps: _Gaps, sort: str) -> dict[Any, int]:
    got = gaps.ranks.get(sort)
    if got is None:
        if sort == "status":
            got = {s: i for i, s in enumerate(_STATUS_ORDER)}
        elif sort in ("section", "subnet", "customer"):
            opts = {"section": gaps.sections, "subnet": gaps.subnets[None], "customer": gaps.customers}[sort]
            got = {o["value"]: i for i, o in enumerate(opts)}
        else:
            idx = _SORT_FIELD[sort]
            got = {v: i for i, v in enumerate(sorted({rec[idx] for rec in gaps.recs if rec[idx]}, key=_natural))}
        gaps.ranks[sort] = got
    return got


async def _page_from(session: AsyncSession, gaps: _Gaps, *, page: int, page_size: int, section_id: Any,
                     subnet_id: Any, customer_id: Any, status: str | None, q: str | None,
                     sort: str = "ip", order: str = "asc") -> dict[str, Any]:
    sec_f, sub_f, cust_f = (str(x) if x else None for x in (section_id, subnet_id, customer_id))
    needle = (q or "").strip().lower()
    hit = [rec for rec in gaps.recs
           if (not sec_f or rec[0] == sec_f) and (not sub_f or rec[1] == sub_f)
           and (not cust_f or rec[2] == cust_f) and (not status or rec[3] == status)
           and (not needle or needle in rec[4])]
    if sort in _SORT_FIELD:     # 穩定排序：同名次維持位址順序
        rank, idx, sign = _ranks(gaps, sort), _SORT_FIELD[sort], -1 if order == "desc" else 1
        hit.sort(key=lambda rec: (rec[idx] not in rank, sign * rank.get(rec[idx], 0)))
    elif order == "desc":       # recs 本來就依位址排好
        hit.reverse()
    start = (max(page, 1) - 1) * page_size
    shown = hit[start:start + page_size]
    detail = await _page_details(session, [rec[5] for rec in shown])
    names = gaps.names
    items = []
    for secid, sid, cid, st, _key, rid, ip, host, cidr, secname, scan_en, excl, kind, model in shown:
        d = detail.get(rid)
        if d is None:        # 算好之後才被刪掉的 IP：這一頁少一筆，下次重算就會修正
            continue
        items.append({
            "ip_address_id": rid, "ip": ip, "hostname": host,
            "subnet_id": sid, "subnet_cidr": cidr, "section_id": secid,
            "section_name": secname, "customer_id": cid,
            "customer_name": names.get(cid) if cid else None,
            "last_seen_scanner": _iso(d.last_seen_scanner), "last_seen_librenms": _iso(d.last_seen_librenms),
            "last_seen_arp": _iso(d.last_seen_arp), "last_seen_wazuh": _iso(d.last_seen_wazuh),
            "last_seen_zabbix": _iso(d.last_seen_zabbix), "last_seen_checkmk": _iso(d.last_seen_checkmk),
            "arp_seen": d.arp_seen or {},
            "exclude_from_ping": bool(excl), "subnet_scan_enabled": scan_en,
            "device_kind": kind, "device_model": model,
            "status": st,
        })
    facets = {"sections": gaps.sections, "subnets": gaps.subnets.get(sec_f, []) if sec_f else gaps.subnets[None],
              "customers": gaps.customers, "statuses": gaps.statuses}
    return {"items": items, "total": len(hit), "total_all": len(gaps.recs), "facets": facets}


async def _page_details(session: AsyncSession, ids: list[str]) -> dict[str, Any]:
    if not ids:
        return {}
    rows = (await session.execute(select(
        IPAddress.id, IPAddress.last_seen_scanner, IPAddress.last_seen_librenms, IPAddress.last_seen_arp,
        IPAddress.last_seen_wazuh, IPAddress.last_seen_zabbix, IPAddress.last_seen_checkmk, IPAddress.arp_seen,
    ).where(in_values(IPAddress.id, [uuid.UUID(i) for i in ids])))).all()
    return {str(r.id): r for r in rows}


def _natural(s: str) -> list[Any]:
    """自然排序（198.51.100.0/24 排在 203.0.113.0/24 前面、sec-2 在 sec-10 前面）。"""
    import re
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s or "")]
