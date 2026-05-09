"""Subnet 業務邏輯：重疊偵測、巢狀計算、first_free、usage。"""

from __future__ import annotations

import ipaddress
import uuid

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.address import IPAddress
from app.models.subnet import Subnet
from app.models.vrf import VRF


class SubnetOverlap(ValueError):
    """同 VRF 內 CIDR 重疊（且該 VRF 不允許重疊）。"""


def _net(cidr: str) -> ipaddress.IPv4Network | ipaddress.IPv6Network:
    return ipaddress.ip_network(cidr, strict=False)


def host_count(net: ipaddress.IPv4Network | ipaddress.IPv6Network) -> int:
    """可配發主機數。

    IPv4：扣除 network + broadcast；/31、/32 特例
    IPv6：用所有位址（不扣 anycast）
    """
    if isinstance(net, ipaddress.IPv6Network):
        # IPv6：直接回傳所有位址數（封頂避免極大值）
        # 上限 2^48（一個 /80 子網）；超過視為「足夠大」回傳 2^48
        n = net.num_addresses
        return n if n <= (1 << 48) else (1 << 48)
    if net.prefixlen >= 31:
        return net.num_addresses  # /31 RFC 3021、/32 主機路由
    return net.num_addresses - 2


async def find_overlapping(
    session: AsyncSession,
    *,
    cidr: str,
    vrf_id: uuid.UUID | None,
    exclude_id: uuid.UUID | None = None,
) -> list[Subnet]:
    """同 VRF（或皆 NULL）下，找出與 cidr 有重疊的 subnet。

    用 PostgreSQL 的 `inet` 操作符 `&&`（重疊）。
    """
    vrf_clause = "vrf_id IS NULL" if vrf_id is None else "vrf_id = :vrf_id"
    sql = f"""
        SELECT id FROM subnets
         WHERE {vrf_clause}
           AND cidr && CAST(:cidr AS cidr)
           {' AND id <> :exclude_id' if exclude_id else ''}
    """
    params: dict[str, object] = {"cidr": cidr}
    if vrf_id is not None:
        params["vrf_id"] = str(vrf_id)
    if exclude_id is not None:
        params["exclude_id"] = str(exclude_id)

    rows = (await session.execute(text(sql), params)).all()
    if not rows:
        return []
    ids = [row[0] for row in rows]
    result = await session.execute(select(Subnet).where(Subnet.id.in_(ids)))
    return list(result.scalars().all())


async def assert_no_overlap(
    session: AsyncSession,
    *,
    cidr: str,
    vrf_id: uuid.UUID | None,
    exclude_id: uuid.UUID | None = None,
) -> None:
    """若該 VRF 不允許重疊（allow_overlap=false 或 VRF 為 NULL），則禁止重疊新增。"""
    if vrf_id is not None:
        vrf = await session.get(VRF, vrf_id)
        if vrf is not None and vrf.allow_overlap:
            return
    overlaps = await find_overlapping(
        session, cidr=cidr, vrf_id=vrf_id, exclude_id=exclude_id
    )
    if overlaps:
        existing = ", ".join(f"{s.cidr}({s.id})" for s in overlaps[:5])
        raise SubnetOverlap(
            f"CIDR {cidr} overlaps with existing subnet(s): {existing}"
        )


async def compute_master_subnet(
    session: AsyncSession,
    *,
    cidr: str,
    vrf_id: uuid.UUID | None,
    exclude_id: uuid.UUID | None = None,
) -> uuid.UUID | None:
    """找出包含 cidr 的最小（最近）父 subnet — phpIPAM 巢狀邏輯。

    使用 PG 的 `>>` 操作符（嚴格包含）。
    """
    vrf_clause = "vrf_id IS NULL" if vrf_id is None else "vrf_id = :vrf_id"
    sql = f"""
        SELECT id FROM subnets
         WHERE {vrf_clause}
           AND cidr >> CAST(:cidr AS cidr)
           {' AND id <> :exclude_id' if exclude_id else ''}
         ORDER BY masklen(cidr) DESC
         LIMIT 1
    """
    params: dict[str, object] = {"cidr": cidr}
    if vrf_id is not None:
        params["vrf_id"] = str(vrf_id)
    if exclude_id is not None:
        params["exclude_id"] = str(exclude_id)
    row = (await session.execute(text(sql), params)).first()
    return row[0] if row else None


async def get_usage(session: AsyncSession, subnet: Subnet) -> tuple[int, int, int, float]:
    """回傳 (total, used, free, used_pct)。"""
    net = _net(subnet.cidr)
    total = host_count(net)
    used = await session.scalar(
        select(func.count()).select_from(IPAddress).where(IPAddress.subnet_id == subnet.id)
    )
    used = int(used or 0)
    free = max(total - used, 0)
    used_pct = round((used / total) * 100, 2) if total else 0.0
    return total, used, free, used_pct


async def find_first_free_address(
    session: AsyncSession,
    subnet: Subnet,
) -> str | None:
    """找出 subnet 內第一個可用 IP（host）。

    為了支援 /16 等大網段，不在 Python 層列舉；改用 SQL 過濾既有 IP，
    再 generate_series 產生候選 host 並 LIMIT 1。

    對 IPv4：跳過 network / broadcast（/31、/32 特例除外）。
    對 IPv6：只跳過 ::（subnet anycast）。
    """
    net = _net(subnet.cidr)
    if isinstance(net, ipaddress.IPv4Network):
        if net.prefixlen >= 31:
            first = int(net.network_address)
            last = int(net.broadcast_address)
        else:
            first = int(net.network_address) + 1
            last = int(net.broadcast_address) - 1
        # 用 PG generate_series + EXCEPT 找空位
        sql = text(
            """
            WITH used AS (
                SELECT host(ip)::inet AS ip
                  FROM ip_addresses
                 WHERE subnet_id = :sid
            )
            SELECT (CAST(:base AS inet) + g)::text AS candidate
              FROM generate_series(0, :span) AS g
             WHERE (CAST(:base AS inet) + g) NOT IN (SELECT ip FROM used)
             LIMIT 1
            """
        )
        result = await session.execute(
            sql,
            {
                "sid": str(subnet.id),
                "base": str(ipaddress.IPv4Address(first)),
                "span": last - first,
            },
        )
        row = result.first()
        return row[0] if row else None

    # IPv6 — 範圍極大；採取「找最大現有 +1」策略，初始化為 ::1
    sql = text(
        """
        SELECT MAX(host(ip)::inet) FROM ip_addresses WHERE subnet_id = :sid
        """
    )
    max_ip = (await session.execute(sql, {"sid": str(subnet.id)})).scalar()
    if max_ip is None:
        # 第一個 host = network + 1（::）；通常 network 自身可不 reserve
        first = net.network_address + 1
        return str(first)
    candidate = ipaddress.ip_address(str(max_ip)) + 1
    if candidate not in net:
        return None
    return str(candidate)
