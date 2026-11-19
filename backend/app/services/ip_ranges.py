"""子網路內的位址範圍（集區）—— GitHub issue #40。

驗證、使用率，以及把「用途＝DHCP 集區」的手動範圍，整理成跟各整合同步回來的 DHCP 範圍
（`DHCPPoolRange`）同一個形狀，讓既有的使用端（清單的「在 DHCP 範圍內」、DHCP 集區使用率、
DHCP 範圍清單、AI 工具）直接算進去，不必各自再認識一種資料。
"""

from __future__ import annotations

import ipaddress
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sqlin import in_values
from app.core.ui_error import UiError
from app.models.address import IPAddress
from app.models.ip_range import IPRange
from app.models.subnet import Subnet

#: DHCP 範圍裡「使用中」的 IP 記錄（SQL 版，與 dhcp_in_use() 同一個判斷）：有租約、DHCP 固定分配、
#: 狀態保留／隔離，或沒有確定離線（上線、狀態未知都保守算）。離線＋沒租約＋沒固定分配的是過期記錄：
#: DHCP 客戶端離開後留下來的，DHCP 伺服器早就可以把它租給別人（2026-10-18 外部 AI 實測 MCP 回報
#: 「50/51 已用、沒有可用位址」，DHCP 伺服器自己只租出 31 個）。
DHCP_IN_USE_SQL = ("(in_dhcp_lease OR dhcp_reserved OR state IN ('reserved', 'quarantine') "
                   "OR effective_status IS DISTINCT FROM 'offline')")


def dhcp_in_use(*, effective_status: str | None, in_dhcp_lease: bool, dhcp_reserved: bool, state: str) -> bool:
    return bool(in_dhcp_lease or dhcp_reserved or state in ("reserved", "quarantine")
                or effective_status != "offline")


#: 找下一個可用位址時最多看幾個（/16 的範圍逐一掃會太久；前面這麼多都用掉了就不再找）
_FIRST_FREE_SCAN = 65536


def _addr(value: str, which: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    try:
        return ipaddress.ip_address(str(value).split("/")[0].strip())
    except ValueError as exc:
        raise UiError(f"「{value}」不是有效的 IP 位址", code="range_invalid_address",
                      value=str(value), which=which) from exc


async def validate_range(session: AsyncSession, subnet: Subnet, start: str, end: str, *,
                         exclude_id: uuid.UUID | None = None) -> tuple[str, str]:
    """檢查一段範圍能不能放進這個子網路；回傳正規化後的 (起, 迄)。

    - 起、迄都要是位址，而且同一個家族（IPv4／IPv6）
    - 起 ≤ 迄
    - 整段都在子網路的 CIDR 裡
    - 不跟同一個子網路裡的其他範圍重疊（緊鄰可以）—— 一個位址同時屬於「DHCP 集區」與
      「保留」沒有意義，而且會讓「這個 IP 在哪個範圍」答不出來
    """
    a, b = _addr(start, "start"), _addr(end, "end")
    if a.version != b.version:
        raise UiError("起迄位址的 IP 版本不同", code="range_family_mismatch")
    if int(a) > int(b):
        raise UiError(f"起始位址 {a} 比結束位址 {b} 大", code="range_start_after_end",
                      start=str(a), end=str(b))
    net = ipaddress.ip_network(str(subnet.cidr), strict=False)
    if a not in net or b not in net:
        raise UiError(f"範圍 {a}～{b} 超出子網路 {net}", code="range_outside_subnet",
                      start=str(a), end=str(b), cidr=str(net))
    others = (await session.execute(
        select(IPRange).where(IPRange.subnet_id == subnet.id))).scalars().all()
    for o in others:
        if exclude_id is not None and o.id == exclude_id:
            continue
        lo, hi = int(_addr(o.start_ip, "start")), int(_addr(o.end_ip, "end"))
        if int(a) <= hi and lo <= int(b):
            label = o.name or f"{_addr(o.start_ip, 'start')}～{_addr(o.end_ip, 'end')}"
            raise UiError(f"與既有範圍「{label}」重疊", code="range_overlap", other=label)
    return str(a), str(b)


async def ranges_with_usage(session: AsyncSession, subnet: Subnet) -> list[dict[str, Any]]:
    """子網路裡的每段範圍，連同大小、已用、可用、下一個可用位址。

    「已用」：保留／其他用途的範圍＝範圍內**已經存在的 IP 記錄**；DHCP 範圍＝其中使用中的
    （`DHCP_IN_USE_SQL`，與 DHCP 集區使用率同一個定義），有人把固定 IP 設在集區裡一樣吃掉可用量，
    但離線又沒租約的過期記錄不算，另外以 `stale` 列出（`records` 是全部記錄數）。
    「下一個可用位址」仍然跳過所有已有記錄的位址：按下去是新增 IP，已有記錄會被擋。

    自動建立的範圍：DHCP 伺服器上的一個範圍被排除區間切成好幾段（一段一筆，「在不在範圍內」要的是段），
    這裡合成一列顯示整個範圍：總數扣掉排除、已用只算各段、下一個可用位址跳過排除的位址，`segments`
    給各段起訖（位址圖照段畫）。以前一段一列，第一段租滿就顯示「已滿」，看不出其他段還有空位（客戶 2026-10-12）。
    """
    from app.models.dhcp import DHCPPoolRange

    rows = (await session.execute(
        select(IPRange).where(IPRange.subnet_id == subnet.id).order_by(IPRange.start_ip)
    )).scalars().all()
    # 位址 → 在 DHCP 範圍裡算不算使用中
    in_use: dict[int, bool] = {}
    for ip, eff, lease, resv, state in (await session.execute(
            select(IPAddress.ip, IPAddress.effective_status, IPAddress.in_dhcp_lease, IPAddress.dhcp_reserved,
                   IPAddress.state).where(IPAddress.subnet_id == subnet.id))).all():
        in_use[int(ipaddress.ip_address(str(ip).split("/")[0]))] = dhcp_in_use(
            effective_status=eff, in_dhcp_lease=lease, dhcp_reserved=resv, state=state)
    used_ints = sorted(in_use)
    used_set = set(used_ints)
    # 「下一個可用」也跳過冷卻中的位址（按下去是新增 IP，冷卻中的會被擋）；冷卻中不算「已用」
    from app.services.ip_lifecycle import active_cooldowns
    taken = used_set | {int(ipaddress.ip_address(c)) for c in await active_cooldowns(session, subnet.id)}

    # 自動範圍的每一段 → 它來自的設定範圍（DHCPPoolRange.pool_start／pool_end，0210）
    parent: dict[tuple[str, str, str], tuple[str, str]] = {}
    if any(r.source_origin for r in rows):
        for p in (await session.execute(
                select(DHCPPoolRange).where(DHCPPoolRange.pool_start.is_not(None)))).scalars().all():
            parent[(f"{p.source_type}:{p.source_id}"[:64], str(p.start_ip), str(p.end_ip))] = (
                str(p.pool_start), str(p.pool_end))

    groups: dict[Any, dict[str, Any]] = {}
    for r in rows:
        s, e = str(r.start_ip).split("/")[0], str(r.end_ip).split("/")[0]
        whole = parent.get((str(r.source_origin), s, e)) if r.source_origin else None
        key = (str(r.source_origin), *whole) if whole else r.id
        g = groups.get(key)
        if g is None:
            g = groups[key] = {"row": r, "start": whole[0] if whole else s, "end": whole[1] if whole else e,
                               "segments": [], "merged": bool(whole)}
        g["segments"].append((s, e))

    out: list[dict[str, Any]] = []
    for g in groups.values():
        r = g["row"]
        spans = sorted((int(ipaddress.ip_address(a)), int(ipaddress.ip_address(b))) for a, b in g["segments"])
        size = sum(hi - lo + 1 for lo, hi in spans)
        inside = [v for v in used_ints if any(lo <= v <= hi for lo, hi in spans)]
        stale = sum(1 for v in inside if not in_use[v]) if r.purpose == "dhcp" else 0
        used = len(inside) - stale
        first_free = None
        scanned = 0      # 各段合計最多看 _FIRST_FREE_SCAN 個
        for lo, hi in spans:
            for v in range(lo, hi + 1):
                if v not in taken:
                    first_free = str(ipaddress.ip_address(v))
                    break
                scanned += 1
                if scanned >= _FIRST_FREE_SCAN:
                    break
            if first_free or scanned >= _FIRST_FREE_SCAN:
                break
        whole = int(ipaddress.ip_address(g["end"])) - int(ipaddress.ip_address(g["start"])) + 1
        out.append({
            "id": r.id, "subnet_id": r.subnet_id,
            "start_ip": g["start"], "end_ip": g["end"],
            "purpose": r.purpose, "name": r.name, "description": r.description,
            "size": size, "used": used, "free": max(0, size - used), "records": len(inside), "stale": stale,
            "first_free": first_free,
            "excluded": max(0, whole - size) if g["merged"] else 0,
            "segments": [list(x) for x in g["segments"]] if g["merged"] else None,
            "auto": bool(r.source_origin), "source_label": r.name if r.source_origin else None,
        })
    return out


def _label(pool: Any) -> str:
    """自動範圍的名稱：跟子網路上方「DHCP 發放範圍」同一個寫法（firewall-a · KEA）。"""
    parts = [str(pool.source_name or pool.source_type)]
    if pool.source:
        parts.append(str(pool.source).upper())
    return " · ".join(parts)[:64]


async def sync_auto_dhcp_ranges(session: AsyncSession) -> dict[str, int]:
    """整合偵測到的 DHCP 發放範圍 → 子網路的「位址範圍（集區）」（使用者要求，2026-09-27）。

    全站對帳（jt-ipam-sync 每輪一次）：`dhcp_pool_ranges` 的每一段，落在**唯一**一個最精確的
    子網路裡就建一筆用途＝DHCP 集區的範圍，記下來源；上游改了就換、不再回報（或整合刪掉）就移除。
    - 手動建立的範圍一律不動；跟手動的（或另一台整合的同一段）重疊時不建 —— 範圍不可以重疊。
    - 落點不唯一（重疊網段）不猜，照「唯一才算」的原則（見 ip_autocreate）。
    發放範圍讀取失敗時 `dhcp_pool_ranges` 會保留原值（各整合已經這樣做），這裡不會因此誤刪。
    """
    from app.models.dhcp import DHCPPoolRange

    out = {"created": 0, "updated": 0, "removed": 0, "skipped_overlap": 0, "skipped_ambiguous": 0}
    nets: list[tuple[Any, uuid.UUID]] = []
    for sid, cidr in (await session.execute(select(Subnet.id, Subnet.cidr))).all():
        try:
            nets.append((ipaddress.ip_network(str(cidr), strict=False), sid))
        except ValueError:
            continue

    desired: dict[tuple[uuid.UUID, str, str, str], str] = {}
    for pool in (await session.execute(select(DHCPPoolRange))).scalars().all():
        try:
            a = ipaddress.ip_address(str(pool.start_ip).split("/")[0].strip())
            b = ipaddress.ip_address(str(pool.end_ip).split("/")[0].strip())
        except ValueError:
            continue
        if a.version != b.version or int(a) > int(b):
            continue
        hits = [(n, sid) for n, sid in nets if a in n and b in n]
        if not hits:
            continue
        best = max(n.prefixlen for n, _ in hits)
        top = [sid for n, sid in hits if n.prefixlen == best]
        if len(top) != 1:
            out["skipped_ambiguous"] += 1
            continue
        origin = f"{pool.source_type}:{pool.source_id}"[:64]
        desired.setdefault((top[0], str(a), str(b), origin), _label(pool))

    # 只管 DHCP 的：別的整合自動建的範圍（例如 Kubernetes LB 集區，用途＝保留）由它們自己對帳，不可以在這裡被刪
    auto_rows = (await session.execute(
        select(IPRange).where(IPRange.source_origin.is_not(None), IPRange.purpose == "dhcp"))).scalars().all()
    have: dict[tuple[uuid.UUID, str, str, str], IPRange] = {}
    for r in auto_rows:
        key = (r.subnet_id, str(r.start_ip).split("/")[0], str(r.end_ip).split("/")[0], str(r.source_origin))
        if key in desired and key not in have:
            have[key] = r
            if r.name != desired[key]:
                r.name = desired[key]
                out["updated"] += 1
        else:
            await session.delete(r)
            out["removed"] += 1
    await session.flush()

    for key, label in desired.items():
        if key in have:
            continue
        subnet_id, start, end, origin = key
        lo, hi = int(ipaddress.ip_address(start)), int(ipaddress.ip_address(end))
        overlap = False
        for o in (await session.execute(select(IPRange).where(IPRange.subnet_id == subnet_id))).scalars().all():
            olo = int(ipaddress.ip_address(str(o.start_ip).split("/")[0]))
            ohi = int(ipaddress.ip_address(str(o.end_ip).split("/")[0]))
            if lo <= ohi and olo <= hi:
                overlap = True
                break
        if overlap:
            out["skipped_overlap"] += 1
            continue
        session.add(IPRange(subnet_id=subnet_id, start_ip=start, end_ip=end, purpose="dhcp",
                            name=label, source_origin=origin))
        await session.flush()
        out["created"] += 1
    return out


@dataclass
class ManualDhcpPool:
    """手動定義的 DHCP 集區，長得跟 `DHCPPoolRange` 一樣（使用端讀的欄位名稱相同）。"""

    id: uuid.UUID
    source_id: uuid.UUID          # ＝子網路 id
    source_name: str
    subnet_cidr: str
    start_ip: str
    end_ip: str
    family: int
    source_type: str = "manual"
    source: str = "manual"
    synced_at: datetime | None = None


async def manual_dhcp_pools(session: AsyncSession,
                            subnet_ids: list[uuid.UUID] | None = None) -> list[ManualDhcpPool]:
    """用途＝DHCP 集區的手動範圍（`subnet_ids` 給了就只取那些子網路的）。

    自動建立的（source_origin 有值）不算：它們就是整合同步回來的 DHCP 範圍，使用端已經算過一次。
    """
    stmt = (select(IPRange, Subnet.cidr).join(Subnet, Subnet.id == IPRange.subnet_id)
            .where(IPRange.purpose == "dhcp", IPRange.source_origin.is_(None)))
    if subnet_ids is not None:
        stmt = stmt.where(in_values(IPRange.subnet_id, subnet_ids))
    out: list[ManualDhcpPool] = []
    for r, cidr in (await session.execute(stmt.order_by(IPRange.start_ip))).all():
        start = str(r.start_ip).split("/")[0]
        out.append(ManualDhcpPool(
            id=r.id, source_id=r.subnet_id, source_name=r.name or "手動定義",
            subnet_cidr=str(cidr), start_ip=start, end_ip=str(r.end_ip).split("/")[0],
            family=ipaddress.ip_address(start).version))
    return out
