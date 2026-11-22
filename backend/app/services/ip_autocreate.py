"""自動建立 IP 記錄時「該放進哪個子網路」的共用判斷。

整合看到一個 IPAM 裡還沒有的 IP（DHCP 租約、LibreNMS 裝置…）時，可以自動建一筆。
難的不是建，是**建到哪個子網路** —— 本專案的核心情境就是重疊網段（多個單位各自
擁有 192.168.123.0/24），建錯單位比不建更糟：資料會靜靜地掛到別人名下。

規則（原本寫在 librenms.py，現在三個整合共用一份）：
- 多層巢狀（10.0.0.0/8 與 10.1.1.0/24 都包含）→ 取**最長首碼**那個，最精確者贏。
- **同長度多個都包含**（真正的重疊網段）→ **不建**。無從得知是誰的，猜就是猜錯。
- 沒有任何既有子網路包含 → **不建**（不會憑空生出子網路）。
- `scope_ids` 有值時只在那些子網路內找 —— 整合設定頁的「關聯子網路」正是用來消除
  重疊歧義的：範圍縮到自己那組，同長度多重命中自然就不會發生。
"""

from __future__ import annotations

import ipaddress
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sqlin import in_values
from app.models.subnet import Subnet

# (network, subnet_id)，依首碼長度由長到短
SubnetCandidates = list[tuple[Any, Any]]


async def addable_subnets(
    session: AsyncSession, scope_ids: set[Any] | list[Any] | None,
) -> SubnetCandidates:
    """可自動建立 IP 的候選子網路，依首碼長度由長到短（最精確優先）。

    `scope_ids` 有值＝只在這些子網路內建（重疊網段下的安全做法）；空＝全部既有子網路。
    """
    stmt = select(Subnet.id, Subnet.cidr)
    if scope_ids:
        stmt = stmt.where(in_values(Subnet.id, scope_ids))
    rows = (await session.execute(stmt)).all()
    nets: SubnetCandidates = []
    for sid, cidr in rows:
        try:
            nets.append((ipaddress.ip_network(str(cidr), strict=False), sid))
        except ValueError:
            continue
    nets.sort(key=lambda x: x[0].prefixlen, reverse=True)
    return nets


def pick_subnet_for_ip(nets: SubnetCandidates, aip: Any) -> Any | None:
    """挑「唯一且最精確」包含此 IP 的子網路；歧義或沒有命中都回 None（不建）。"""
    containing = [(net, sid) for net, sid in nets if aip in net]
    if not containing:
        return None
    maxlen = max(net.prefixlen for net, _ in containing)
    best = [sid for net, sid in containing if net.prefixlen == maxlen]
    return best[0] if len(best) == 1 else None


def subnet_for_ip_str(nets: SubnetCandidates, ip: str) -> UUID | None:
    """字串版：不合法的 IP 直接回 None，呼叫端不必自己先驗一次。"""
    try:
        aip = ipaddress.ip_address(ip)
    except ValueError:
        return None
    return pick_subnet_for_ip(nets, aip)


async def match_existing(
    session: AsyncSession, ip: str | None, scope_ids: set[Any] | list[Any] | None = None,
) -> tuple[Any | None, bool]:
    """整合看到一個 IP，對到 IPAM 既有的哪一筆 → (IP 物件, 是否不明確)。

    與建立同一條原則：**唯一才算**。同一個 IP 在好幾個子網路都有（重疊網段、整合沒設關聯
    子網路）→ (None, True)，呼叫端什麼都不寫、也不可以新建（同一個子網路裡已經有了）。
    以前各整合一律 `.limit(1)` 任意取一筆，主機名稱、MAC、上線證據會掛到別的單位名下
    （2026-09-26 稽核）。查無 → (None, False)。
    """
    from app.models.address import IPAddress

    if not ip:
        return None, False
    stmt = select(IPAddress).where(IPAddress.ip == ip)
    if scope_ids:
        stmt = stmt.where(in_values(IPAddress.subnet_id, scope_ids))
    rows = (await session.execute(stmt.limit(2))).scalars().all()
    if len(rows) > 1:
        return None, True
    return (rows[0] if rows else None), False


async def match_existing_many(
    session: AsyncSession, ips: Any, scope_ids: set[Any] | list[Any] | None = None,
) -> dict[str, tuple[Any | None, bool]]:
    """match_existing 的整批版（同一條「唯一才算」）：{ip: (IP 物件, 是否不明確)}，一次查詢。

    超大規模：整合一輪逐一 match_existing，5,000 台裝置就是 5,000 次查詢。不是合法 IP 的字串
    直接當查無（以前單筆比對時會讓整輪同步崩掉；整批時更會拖垮整批）。
    """
    import ipaddress as _ip
    from collections import defaultdict

    from app.core.sqlin import in_values
    from app.models.address import IPAddress

    out: dict[str, tuple[Any | None, bool]] = {}
    valid: set[str] = set()
    for raw in ips:
        if not raw:
            continue
        out[raw] = (None, False)
        try:
            _ip.ip_address(raw)
            valid.add(raw)
        except ValueError:
            continue
    if not valid:
        return out
    stmt = select(IPAddress).where(in_values(IPAddress.ip, valid))
    if scope_ids:
        stmt = stmt.where(in_values(IPAddress.subnet_id, scope_ids))
    found: dict[str, list[Any]] = defaultdict(list)
    for row in (await session.execute(stmt)).scalars().all():
        found[str(row.ip).split("/")[0]].append(row)
    for key, rows in found.items():
        if key in out:
            out[key] = (rows[0], False) if len(rows) == 1 else (None, True)
    return out


class SubnetIndex:
    """`pick_subnet_for_ip` 的索引版：規則完全相同，但不必逐一比對每個子網路。

    超大規模：ARP 自動建立第一次打開時可能有幾萬個候選位址、站台有幾千個子網路，逐一比對是
    幾千萬次。這裡依（位址版本, 首碼長度）分組、以網路位址為鍵，查一個位址只要試過出現過的
    首碼長度（最多 33／129 種，實際通常個位數），由長到短，第一個命中的長度就是「最精確」。
    """

    def __init__(self, nets: SubnetCandidates) -> None:
        self._by: dict[tuple[int, int], dict[int, list[tuple[Any, Any]]]] = {}
        for net, sid in nets:
            key = (net.version, net.prefixlen)
            self._by.setdefault(key, {}).setdefault(int(net.network_address), []).append((net, sid))
        self._lens: dict[int, list[int]] = {}
        for ver, plen in self._by:
            self._lens.setdefault(ver, []).append(plen)
        for v in self._lens.values():
            v.sort(reverse=True)

    def pick_net(self, aip: Any) -> tuple[Any, Any] | None:
        """(子網路, id)；歧義或沒有命中回 None。"""
        bits = aip.max_prefixlen
        n = int(aip)
        for plen in self._lens.get(aip.version, ()):
            mask = ((1 << bits) - 1) ^ ((1 << (bits - plen)) - 1)
            hit = self._by[(aip.version, plen)].get(n & mask)
            if hit:
                return hit[0] if len(hit) == 1 else None
        return None

    def pick(self, aip: Any) -> Any | None:
        got = self.pick_net(aip)
        return got[1] if got else None

    def longest(self, aip: Any) -> Any | None:
        """最精確包含此位址的網段（不管是否唯一）；沒有命中回 None。"""
        bits = aip.max_prefixlen
        n = int(aip)
        for plen in self._lens.get(aip.version, ()):
            mask = ((1 << bits) - 1) ^ ((1 << (bits - plen)) - 1)
            hit = self._by[(aip.version, plen)].get(n & mask)
            if hit:
                return hit[0][0]
        return None


def subnet_index(nets: SubnetCandidates) -> SubnetIndex:
    return SubnetIndex(nets)
