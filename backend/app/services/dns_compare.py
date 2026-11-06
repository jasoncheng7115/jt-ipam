"""DNS 比對群組：比對、合併顯示用的鍵、告警（models/dns_compare_group.py 有完整說明）。

jt-ipam 不會在伺服器之間同步紀錄；比對只用各台已經拉取下來的紀錄（dns_zones／dns_records），不另外連 DNS：
- 整個 zone 只在部分成員上 → 一筆 kind=zone（不再逐筆比那個 zone 的紀錄）；沒有任何可比對紀錄的 zone 不列
- 所有成員都有的 zone → 逐筆比（正規化後的 名稱、型別、值；TTL 不比，SOA 不同步進來）
- 群組設定「不比對的 zone」（excluded_zones）裡的 zone 不比：主從只複寫部分 zone、或某台另外放自己的 zone
- 群組的排除（2026-11-05）：勾「排除 DHCP 範圍」時，IP 在 DHCP 範圍內的紀錄不比；管理員自訂的名稱
  （FQDN、`*.example.com`）與 IP（單一、CIDR、起-迄）也不比。A／AAAA 看紀錄的 IP，PTR 從反解名稱算回 IP、
  並用它指向的主機名稱比對名稱排除
- AD 的服務定位資料（_msdcs、TrustAnchors 兩個 zone，DomainDnsZones／ForestDnsZones 紀錄）不比：
  Windows 與 Samba（UCS）登記方式不同，也不是 IPAM 的主機資料
- 不同軟體可以混搭；Unbound（OPNsense）只有主機覆寫、沒有完整的 zone，只能和 Unbound 同組
- 有成員最近一次拉取失敗、從沒拉取過、或紀錄還沒填正規化欄位 → 資料不完整，先不判定也不告警
- 差異要在「沒有的那台」於差異出現＋寬限時間（伺服器之間的複寫延遲）之後重新拉取過、仍然沒有，才算確認
  （用資料的時間：成員依序拉取，比對時別台的資料可能是舊的）；確認後用通知功能告警，同一批只發一次，
  之後又多出來的再發；全部恢復一致時發一則恢復通知
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sqlin import in_values
from app.models.dns import DNSRecord, DNSServer, DNSZone
from app.models.dns_compare_group import DNSCompareGroup, DNSCompareGroupDiff

EVENT_MISMATCH = "dns.compare_mismatch"
EVENT_RESOLVED = "dns.compare_resolved"
#: 通知內文最多列幾筆例子（差異可能上千筆：伺服器之間的複寫整個斷掉時）
EXAMPLES = 5


# ── 正規化 ──────────────────────────────────────────────────────────────

def normalize_zone(zone: str | None) -> str:
    return (zone or "").strip().rstrip(".").lower()


def normalize_name(name: str | None, zone: str | None = None) -> str:
    """完整名稱、小寫、不帶結尾點；相對名稱（含 @）補上 zone。"""
    z = normalize_zone(zone)
    n = (name or "").strip().rstrip(".").lower()
    if n in ("", "@"):
        return z
    if not z or n == z or n.endswith("." + z):
        return n
    return f"{n}.{z}"


#: 比對群組可以選的紀錄類型（畫面上的順序）；預設只比 IPAM 有對應的三種
COMPARE_TYPES = ("A", "AAAA", "PTR", "CNAME", "MX", "TXT", "NS", "SRV", "CAA")
DEFAULT_COMPARE_TYPES = ("A", "AAAA", "PTR")


def normalize_compare_types(types: list[str] | None) -> list[str]:
    """大寫、去重複、照 COMPARE_TYPES 的順序；不認得的丟掉（呼叫端要先驗證）。"""
    want = {str(t).strip().upper() for t in (types or [])}
    return [t for t in COMPARE_TYPES if t in want]


def _host(v: str) -> str:
    return v.rstrip(".").lower()


def normalize_value(rtype: str | None, value: str | None) -> str:
    """各廠牌寫法不同的值取同一種寫法，比對才不會因為格式誤報。

    A／AAAA 取標準寫法（IPv6 縮寫）；指向名稱的（PTR、CNAME、NS）小寫、去結尾點；
    MX「優先序 主機」、SRV「優先序 權重 埠 主機」的主機也一樣處理；TXT 去掉引號、多段接起來（BIND 寫成 "a" "b"）。
    """
    t = (rtype or "").upper()
    v = (value or "").strip()
    if t in ("A", "AAAA"):
        try:
            return str(ipaddress.ip_address(v))
        except ValueError:
            return v.lower()
    if t in ("PTR", "CNAME", "NS"):
        return _host(v)
    parts = v.split()
    if t == "MX" and len(parts) == 2:
        return f"{parts[0]} {_host(parts[1])}"
    if t == "SRV" and len(parts) == 4:
        return " ".join([*parts[:3], _host(parts[3])])
    if t == "TXT":
        chunks = re.findall(r'"((?:[^"\\]|\\.)*)"', v)
        return "".join(chunks) if chunks else v
    return " ".join(parts)


def normalize_zone_list(zones: list[str] | None) -> list[str]:
    """不比對的 zone 清單：正規化、去空白、去重複、排序。"""
    return sorted({z for z in (normalize_zone(x) for x in (zones or [])) if z})


#: 只有主機覆寫、沒有完整 zone 的來源：只能和同一類放在一起
OVERRIDE_ONLY_TYPES = frozenset({"unbound_opnsense"})


_NAME_RE = re.compile(r"^(\*\.)?([a-z0-9_]([a-z0-9_-]{0,62})?)(\.[a-z0-9_]([a-z0-9_-]{0,62})?)*$")


class BadExclusion(ValueError):
    """排除清單裡有看不懂的項目（API 回 422 dns_compare_bad_exclusion）。"""

    def __init__(self, entry: str) -> None:
        super().__init__(entry)
        self.entry = entry


def normalize_name_list(names: list[str] | None) -> list[str]:
    """不比對的名稱：小寫、去結尾點、去重、保留順序；`*.x` 只比對 x 底下的名稱。"""
    out: list[str] = []
    for raw in names or []:
        n = (raw or "").strip().rstrip(".").lower()
        if not n:
            continue
        if len(n) > 253 or not _NAME_RE.match(n):
            raise BadExclusion(raw)
        if n not in out:
            out.append(n)
    return out


def _parse_ip_entry(raw: str) -> tuple[int, int, int]:
    """IP／CIDR／起-迄 → (版本, 起, 迄)（整數，含兩端）。"""
    s = (raw or "").strip()
    try:
        if "-" in s:
            a, b = (ipaddress.ip_address(x.strip()) for x in s.split("-", 1))
            if a.version != b.version or int(a) > int(b):
                raise ValueError(s)
            return a.version, int(a), int(b)
        if "/" in s:
            net = ipaddress.ip_network(s, strict=False)
            return net.version, int(net.network_address), int(net.broadcast_address)
        a = ipaddress.ip_address(s)
        return a.version, int(a), int(a)
    except ValueError as exc:
        raise BadExclusion(raw) from exc


def normalize_ip_list(entries: list[str] | None) -> list[str]:
    """不比對的 IP：去空白、驗證格式（IP、CIDR、起-迄，版本一致且起不大於迄）、去重。"""
    out: list[str] = []
    for raw in entries or []:
        s = (raw or "").strip().replace(" ", "")
        if not s:
            continue
        _parse_ip_entry(s)
        if s not in out:
            out.append(s)
    return out


def ptr_ip(name: str) -> str | None:
    """反解名稱 → IP（in-addr.arpa／ip6.arpa）；不是完整反解名稱回 None。"""
    n = (name or "").rstrip(".").lower()
    try:
        if n.endswith(".in-addr.arpa"):
            parts = n[: -len(".in-addr.arpa")].split(".")
            if len(parts) == 4:
                return str(ipaddress.IPv4Address(".".join(reversed(parts))))
        elif n.endswith(".ip6.arpa"):
            nibbles = n[: -len(".ip6.arpa")].split(".")
            if len(nibbles) == 32:
                h = "".join(reversed(nibbles))
                return str(ipaddress.IPv6Address(":".join(h[i:i + 4] for i in range(0, 32, 4))))
    except ValueError:
        return None
    return None


class RecordFilter:
    """群組的紀錄排除：名稱（完整或 *.後綴）、IP 區間（自訂＋DHCP 範圍）。"""

    def __init__(self, names: list[str] | None = None, ip_ranges: list[tuple[int, int, int]] | None = None) -> None:
        self.exact = {n for n in (names or []) if not n.startswith("*.")}
        self.suffixes = tuple(n[1:] for n in (names or []) if n.startswith("*."))   # ".lab.example"
        self.ranges = list(ip_ranges or [])

    def __bool__(self) -> bool:
        return bool(self.exact or self.suffixes or self.ranges)

    def _name(self, n: str) -> bool:
        return n in self.exact or (bool(self.suffixes) and n.endswith(self.suffixes))

    def _ip(self, value: str | None) -> bool:
        if not value or not self.ranges:
            return False
        try:
            a = ipaddress.ip_address(value)
        except ValueError:
            return False
        v, i = a.version, int(a)
        return any(rv == v and lo <= i <= hi for rv, lo, hi in self.ranges)

    def skip(self, name_norm: str, rtype: str, value_norm: str) -> bool:
        if rtype in ("A", "AAAA"):
            return self._name(name_norm) or self._ip(value_norm)
        if rtype == "PTR":
            return self._name(value_norm) or self._ip(ptr_ip(name_norm))
        return self._name(name_norm)


async def record_filter(session: AsyncSession, group: Any) -> RecordFilter:
    """群組設定 → RecordFilter；勾了排除 DHCP 範圍時，把子網路位址範圍裡用途為 DHCP 的都加進去。"""
    ranges = [_parse_ip_entry(e) for e in normalize_ip_list(list(getattr(group, "excluded_ips", None) or []))]
    if getattr(group, "exclude_dhcp_ranges", False):
        from app.models.ip_range import IPRange
        for start, end in (await session.execute(
                select(IPRange.start_ip, IPRange.end_ip).where(IPRange.purpose == "dhcp"))).all():
            try:
                a, b = ipaddress.ip_address(str(start)), ipaddress.ip_address(str(end))
            except ValueError:
                continue
            if a.version == b.version:
                ranges.append((a.version, int(a), int(b)))
    return RecordFilter(normalize_name_list(list(getattr(group, "excluded_names", None) or [])), ranges)


def incompatible_types(types: list[str]) -> list[str]:
    """同一組裡不能放在一起的伺服器類型（空清單＝可以）。Unbound 只能和 Unbound 同組。"""
    kinds = {t in OVERRIDE_ONLY_TYPES for t in types}
    if len(kinds) <= 1:
        return []
    return sorted({t for t in types if t in OVERRIDE_ONLY_TYPES})


def is_locator_zone(zone: str) -> bool:
    """AD 的服務定位 zone：_msdcs.<樹系>、TrustAnchors。"""
    z = normalize_zone(zone)
    return z == "trustanchors" or z == "_msdcs" or z.startswith("_msdcs.")


def is_locator_name(name: str) -> bool:
    """AD 的定位紀錄：任何一層是 _msdcs，或開頭是 DomainDnsZones／ForestDnsZones（分割區定位）。"""
    labels = (name or "").lower().split(".")
    return "_msdcs" in labels or labels[0] in ("domaindnszones", "forestdnszones")


def _key_hash(kind: str, zone: str, name: str, rtype: str, value: str) -> str:
    return hashlib.sha256("|".join((kind, zone, name, rtype, value)).encode()).hexdigest()


def merge_key(group_id: Any, server_id: Any, name_norm: str | None, name: str, rtype: str,
              value_norm: str | None, value: str) -> tuple[str, str, str, str]:
    """顯示時合併用：同一組（沒分組＝自己一組）裡正規化後完全相同的紀錄是同一筆。"""
    owner = f"g:{group_id}" if group_id else f"s:{server_id}"
    return (owner, name_norm or (name or "").lower(), (rtype or "").upper(), value_norm or (value or "").lower())


async def group_of_servers(session: AsyncSession) -> dict[uuid.UUID, uuid.UUID | None]:
    """DNS 伺服器 id → 比對群組 id（沒分組為 None）。"""
    return dict((await session.execute(select(DNSServer.id, DNSServer.compare_group_id))).all())


# ── 比對 ────────────────────────────────────────────────────────────────

async def _snapshot(session: AsyncSession, member_ids: list[uuid.UUID], excluded: frozenset[str] = frozenset(),
                    types: tuple[str, ...] = DEFAULT_COMPARE_TYPES, flt: RecordFilter | None = None) -> tuple[
        dict[uuid.UUID, set[str]], dict[uuid.UUID, set[tuple[str, str, str, str]]], int]:
    """每台成員的 zone 集合與紀錄集合（正規化後），以及還沒填正規化欄位的紀錄數。
    AD 服務定位 zone 與群組設定不比對的 zone 一律略過。"""
    def skip(zname: str) -> bool:
        return is_locator_zone(zname) or normalize_zone(zname) in excluded

    zones: dict[uuid.UUID, set[str]] = defaultdict(set)
    for sid, zname in (await session.execute(
            select(DNSZone.server_id, DNSZone.name).where(in_values(DNSZone.server_id, member_ids)))).all():
        if not skip(zname):
            zones[sid].add(normalize_zone(zname))
    recs: dict[uuid.UUID, set[tuple[str, str, str, str]]] = defaultdict(set)
    missing_norm = 0
    rows = (await session.execute(
        select(DNSZone.server_id, DNSZone.name, DNSRecord.name_norm, DNSRecord.type, DNSRecord.value_norm)
        .join(DNSZone, DNSZone.id == DNSRecord.zone_id)
        .where(in_values(DNSZone.server_id, member_ids),
               func.upper(DNSRecord.type).in_(types)))).all()  # bounded: 至多 9 種紀錄類型（COMPARE_TYPES）
    for sid, zname, nn, rtype, vn in rows:
        if skip(zname):
            continue
        if nn is None or vn is None:
            missing_norm += 1
            continue
        if is_locator_name(nn):
            continue
        if flt and flt.skip(nn, (rtype or "").upper(), vn):
            continue
        recs[sid].add((normalize_zone(zname), nn, (rtype or "").upper(), vn))
    return zones, recs, missing_norm


def _compute(members: list[uuid.UUID], zones: dict[uuid.UUID, set[str]],
             recs: dict[uuid.UUID, set[tuple[str, str, str, str]]]) -> list[dict[str, Any]]:
    everyone = set(members)
    diffs: list[dict[str, Any]] = []
    all_zones = set().union(*(zones.get(m, set()) for m in members))
    full_zones = set.intersection(*(zones.get(m, set()) for m in members)) if members else set()
    # 有可比對紀錄的 zone：空的 zone（只放了 SRV、剛建立）只在一台上，對 IPAM 沒有內容差異
    with_records = {k[0] for m in members for k in recs.get(m, set())}
    for z in sorted((all_zones - full_zones) & with_records):
        have = {m for m in members if z in zones.get(m, set())}
        diffs.append({"kind": "zone", "zone": z, "name": "", "type": "", "value": "",
                      "present_on": sorted(str(m) for m in have),
                      "missing_on": sorted(str(m) for m in everyone - have)})
    owners: dict[tuple[str, str, str, str], set[uuid.UUID]] = defaultdict(set)
    for m in members:
        for k in recs.get(m, set()):
            if k[0] in full_zones:
                owners[k].add(m)
    for k in sorted(owners):
        have = owners[k]
        if have != everyone:
            diffs.append({"kind": "record", "zone": k[0], "name": k[1], "type": k[2], "value": k[3],
                          "present_on": sorted(str(m) for m in have),
                          "missing_on": sorted(str(m) for m in everyone - have)})
    return diffs


async def check_group(session: AsyncSession, group: DNSCompareGroup, *, now: datetime | None = None) -> dict[str, Any]:
    """比對一組：更新差異表、群組狀態，必要時發通知。不 commit（呼叫端決定）。"""
    now = now or datetime.now(UTC)
    members = (await session.execute(select(DNSServer).where(
        DNSServer.compare_group_id == group.id, DNSServer.enabled.is_(True)).order_by(DNSServer.name))).scalars().all()
    names = {m.id: m.name for m in members}
    result: dict[str, Any] = {"group": group.name, "members": len(members)}

    def done(status: str, message: str | None = None, **extra: Any) -> dict[str, Any]:
        group.last_checked_at, group.last_status, group.last_message = now, status, message
        result.update(status=status, message=message, **extra)
        return result

    if len(members) < 2:
        await session.execute(delete(DNSCompareGroupDiff).where(DNSCompareGroupDiff.group_id == group.id))
        group.alerted_at = None
        return done("single")
    bad = incompatible_types([m.type for m in members])
    if bad:
        # API 會擋；這裡是萬一（例如舊資料、直接改資料庫）：不比、不列差異、不告警
        await session.execute(delete(DNSCompareGroupDiff).where(DNSCompareGroupDiff.group_id == group.id))
        group.alerted_at = None
        return done("incompatible", ", ".join(m.name for m in members if m.type in bad))
    not_ready = [m.name for m in members if m.last_sync_at is None or m.last_error]
    if not_ready:
        return done("incomplete", ", ".join(not_ready))
    ids = [m.id for m in members]
    types = tuple(normalize_compare_types(group.compare_types) or DEFAULT_COMPARE_TYPES)
    zones, recs, missing_norm = await _snapshot(session, ids, frozenset(normalize_zone_list(group.excluded_zones)), types,
                                                await record_filter(session, group))
    if missing_norm:
        # 升級前同步進來的紀錄還沒有正規化欄位：下一輪同步就會補上
        return done("incomplete", f"{missing_norm} records waiting for the next sync")

    current = {(_key_hash(d["kind"], d["zone"], d["name"], d["type"], d["value"])): d
               for d in _compute(ids, zones, recs)}
    existing = {d.key_hash: d for d in (await session.execute(
        select(DNSCompareGroupDiff).where(DNSCompareGroupDiff.group_id == group.id))).scalars().all()}
    for h, row in existing.items():
        if h not in current:
            await session.delete(row)
    # 資料的時間：每台成員最近一次拉取完成的時間（成員依序拉取，比對時別台的資料可能是幾分鐘前的）
    synced = {str(m.id): m.last_sync_at for m in members}
    for h, d in current.items():
        row = existing.get(h)
        if row is None:
            # 第一次看到＝有這筆的伺服器拉取的時間，不是比對當下的時鐘
            seen = min((synced[x] for x in d["present_on"] if synced.get(x)), default=now)
            session.add(DNSCompareGroupDiff(group_id=group.id, key_hash=h, first_seen_at=seen, last_seen_at=now, **d))
        else:
            row.present_on, row.missing_on, row.last_seen_at = d["present_on"], d["missing_on"], now
    await session.flush()

    grace = timedelta(minutes=group.grace_minutes)
    diffs = (await session.execute(select(DNSCompareGroupDiff).where(DNSCompareGroupDiff.group_id == group.id)
                                   .order_by(DNSCompareGroupDiff.first_seen_at, DNSCompareGroupDiff.zone,
                                             DNSCompareGroupDiff.name))).scalars().all()
    # 確認：每一台「沒有」的伺服器，都在差異出現＋寬限時間之後重新拉取過、仍然沒有
    # （2026-10-11 真實環境：三台依序拉取，第一台一拉完就判定「另外兩台沒有」，其中一台其實有）
    for d in diffs:
        if d.confirmed_at is None:
            due = d.first_seen_at + grace
            if all(synced.get(x) is not None and synced[x] >= due for x in d.missing_on):
                d.confirmed_at = now
    confirmed = [d for d in diffs if d.confirmed_at is not None]
    alerted = None
    if not diffs:
        if group.alerted_at is not None:
            if group.notify_enabled:
                await _notify_resolved(session, group)
                alerted = "resolved"
            group.alerted_at = None
        return done("ok", alerted=alerted)
    if confirmed:
        fresh = [d for d in confirmed if group.alerted_at is None or (d.confirmed_at and d.confirmed_at > group.alerted_at)]
        if fresh and group.notify_enabled:
            await _notify_mismatch(session, group, fresh, len(confirmed), names)
            alerted = "mismatch"
        if fresh:
            group.alerted_at = now
        return done("mismatch", f"{len(confirmed)} confirmed, {len(diffs) - len(confirmed)} pending",
                    diffs=len(diffs), confirmed=len(confirmed), alerted=alerted)
    return done("pending", f"{len(diffs)} pending", diffs=len(diffs), confirmed=0)


async def check_group_of_server(session: AsyncSession, server: DNSServer) -> dict[str, Any] | None:
    """一台同步完：比對它所在的組（沒分組回 None）。"""
    if server.compare_group_id is None:
        return None
    group = await session.get(DNSCompareGroup, server.compare_group_id)
    return await check_group(session, group) if group is not None else None


def _what(d: DNSCompareGroupDiff) -> str:
    return f"zone {d.zone}" if d.kind == "zone" else f"{d.name} {d.type} {d.value}"


def _missing(d: DNSCompareGroupDiff, names: dict[uuid.UUID, str]) -> str:
    return ", ".join(names.get(uuid.UUID(x), x) for x in d.missing_on)


def describe(d: DNSCompareGroupDiff, names: dict[uuid.UUID, str]) -> str:
    """一筆差異的簡短英文（通知的 body 退回值、Email 用；畫面上依語言用 body_key 組句）。"""
    return f"{_what(d)} (missing on {_missing(d, names)})"


async def _notify_mismatch(session: AsyncSession, group: DNSCompareGroup, fresh: list[DNSCompareGroupDiff],
                           total: int, names: dict[uuid.UUID, str]) -> None:
    from app.services.notification import notify_admins_event
    examples = "; ".join(describe(d, names) for d in fresh[:EXAMPLES])
    more = len(fresh) - EXAMPLES
    body = examples + (f" (+{more})" if more > 0 else "")
    # 畫面上的內文由前端依語言組句：第一筆是哪一筆、哪幾台沒有、另外還有幾筆（參數裡不放組好的英文）
    first, others = fresh[0], len(fresh) - 1
    await notify_admins_event(
        session, event=EVENT_MISMATCH, severity="warning", link=f"/dns?compare_group={group.id}",
        title=f"DNS comparison group {group.name}: {total} differences", body=body,
        object_type="dns_compare_group", object_id=group.id,
        title_key="notif.dns_compare_mismatch",
        body_key="notif.dns_compare_mismatch_body_more" if others else "notif.dns_compare_mismatch_body",
        params={"group": group.name, "count": total, "new": len(fresh),
                "what": _what(first), "missing": _missing(first, names), "more": others})


async def _notify_resolved(session: AsyncSession, group: DNSCompareGroup) -> None:
    from app.services.notification import notify_admins_event
    await notify_admins_event(
        session, event=EVENT_RESOLVED, severity="info", link=f"/dns?compare_group={group.id}",
        title=f"DNS comparison group {group.name}: consistent again", body=None,
        object_type="dns_compare_group", object_id=group.id,
        title_key="notif.dns_compare_resolved", body_key="notif.dns_compare_resolved_body",
        params={"group": group.name})


async def diff_counts(session: AsyncSession) -> dict[uuid.UUID, int]:
    return dict((await session.execute(select(DNSCompareGroupDiff.group_id, func.count())
                                       .group_by(DNSCompareGroupDiff.group_id))).all())
