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
    # 全部走 bound param（vrf / exclude 用 NULL 判斷，避免字串拼接 SQL）
    sql = """
        SELECT id FROM subnets
         WHERE ((CAST(:vrf_id AS uuid) IS NULL AND vrf_id IS NULL)
                OR vrf_id = CAST(:vrf_id AS uuid))
           AND archived_at IS NULL
           AND cidr && CAST(:cidr AS cidr)
           AND (CAST(:exclude_id AS uuid) IS NULL
                OR id <> CAST(:exclude_id AS uuid))
    """
    params: dict[str, object | None] = {
        "cidr": cidr,
        "vrf_id": str(vrf_id) if vrf_id is not None else None,
        "exclude_id": str(exclude_id) if exclude_id is not None else None,
    }
    rows = (await session.execute(text(sql), params)).all()
    if not rows:
        return []
    ids = [row[0] for row in rows]
    result = await session.execute(select(Subnet).where(Subnet.id.in_(ids)))
    return list(result.scalars().all())


async def has_overlapping_subnets(session: AsyncSession) -> bool:
    """全庫是否存在「同 VRF（或皆 NULL）下 CIDR 互相重疊」的子網路。

    用於提醒：有重疊網段時，未設 scope_subnet_ids 的整合（LibreNMS/Wazuh/Proxmox/
    AdGuard/DNS/OPNsense）在比對同一個 IP 字串時可能標到錯誤客戶的那一筆。
    """
    sql = """
        SELECT EXISTS (
            SELECT 1 FROM subnets a JOIN subnets b
              ON a.id < b.id
             AND ((a.vrf_id IS NULL AND b.vrf_id IS NULL) OR a.vrf_id = b.vrf_id)
             AND a.archived_at IS NULL AND b.archived_at IS NULL
             AND a.cidr && b.cidr
        )
    """
    return bool((await session.execute(text(sql))).scalar())


async def assert_no_overlap(
    session: AsyncSession,
    *,
    cidr: str,
    vrf_id: uuid.UUID | None,
    exclude_id: uuid.UUID | None = None,
    allow_overlap: bool = False,
) -> None:
    """若該 VRF 不允許重疊（allow_overlap=false 或 VRF 為 NULL），則禁止重疊新增。
    allow_overlap=True 為使用者明確允許（例如同 CIDR 但單位/地點不同）→ 直接放行。"""
    if allow_overlap:
        return
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
    sql = """
        SELECT id FROM subnets
         WHERE ((CAST(:vrf_id AS uuid) IS NULL AND vrf_id IS NULL)
                OR vrf_id = CAST(:vrf_id AS uuid))
           AND archived_at IS NULL
           AND cidr >> CAST(:cidr AS cidr)
           AND (CAST(:exclude_id AS uuid) IS NULL
                OR id <> CAST(:exclude_id AS uuid))
         ORDER BY masklen(cidr) DESC
         LIMIT 1
    """
    params: dict[str, object | None] = {
        "cidr": cidr,
        "vrf_id": str(vrf_id) if vrf_id is not None else None,
        "exclude_id": str(exclude_id) if exclude_id is not None else None,
    }
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


async def rebuild_subnet_hierarchy(session: AsyncSession) -> int:
    """依 CIDR 包含關係重算所有 subnet 的 master_subnet_id（巢狀階層）。

    建立父網段後可讓既有子網段自動歸位；匯入後也用它補階層。回傳變更筆數。
    """
    rows = (await session.execute(select(Subnet.id, Subnet.cidr, Subnet.vrf_id, Subnet.master_subnet_id))).all()
    changed = 0
    for sid, cidr, vrf_id, cur_master in rows:
        master = await compute_master_subnet(
            session, cidr=str(cidr), vrf_id=vrf_id, exclude_id=sid,
        )
        if cur_master != master:
            await session.execute(
                text("UPDATE subnets SET master_subnet_id = :m WHERE id = :s"),
                {"m": str(master) if master else None, "s": str(sid)},
            )
            changed += 1
    # 子網段繼承上層「單位」：master 有 customer、子網段 customer 為空時補上。
    # 由淺到深反覆執行，支援多層串接（祖→父→孫），直到沒有可補的為止。
    while True:
        res = await session.execute(text(
            """
            UPDATE subnets AS c SET customer_id = p.customer_id
              FROM subnets AS p
             WHERE c.master_subnet_id = p.id
               AND c.customer_id IS NULL
               AND p.customer_id IS NOT NULL
            """
        ))
        n = res.rowcount or 0
        if n <= 0:
            break
        changed += n
    return changed


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
            SELECT host(CAST(:base AS inet) + g) AS candidate
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
        first = net.network_address + 1  # type: ignore[assignment]
        return str(first)
    candidate = ipaddress.ip_address(str(max_ip)) + 1
    if candidate not in net:
        return None
    return str(candidate)


async def find_free_addresses(
    session: AsyncSession,
    subnet: Subnet,
    *,
    count: int = 1,
    consecutive: bool = False,
    scan_limit: int = 100_000,
) -> list[str]:
    """找 subnet 內 count 個可用 IP；consecutive=True 時要求連續一段。

    IPv4：撈出已用 host 整數集合，再在 Python 掃描（scan_limit 上限避免 /8 之類爆掉）。
    IPv6：範圍過大，採序列 best-effort（從第一個可用往後取）。
    找不到足夠數量時回傳能找到的（consecutive 找不到整段則回空）。
    """
    count = max(1, count)
    net = _net(subnet.cidr)

    if isinstance(net, ipaddress.IPv4Network):
        if net.prefixlen >= 31:
            first = int(net.network_address)
            last = int(net.broadcast_address)
        else:
            first = int(net.network_address) + 1
            last = int(net.broadcast_address) - 1
        if last < first:
            return []
        used_rows = (await session.execute(
            text("SELECT host(ip)::text FROM ip_addresses WHERE subnet_id = :sid"),
            {"sid": str(subnet.id)},
        )).scalars().all()
        used: set[int] = set()
        for u in used_rows:
            try:
                used.add(int(ipaddress.IPv4Address(u)))
            except (ValueError, ipaddress.AddressValueError):
                pass

        if consecutive:
            run_start: int | None = None
            run_len = 0
            i = first
            scanned = 0
            while i <= last and scanned < scan_limit:
                if i not in used:
                    if run_len == 0:
                        run_start = i
                    run_len += 1
                    if run_len == count and run_start is not None:
                        return [str(ipaddress.IPv4Address(x))
                                for x in range(run_start, run_start + count)]
                else:
                    run_len = 0
                i += 1
                scanned += 1
            return []

        out: list[str] = []
        i = first
        scanned = 0
        while i <= last and len(out) < count and scanned < scan_limit:
            if i not in used:
                out.append(str(ipaddress.IPv4Address(i)))
            i += 1
            scanned += 1
        return out

    # IPv6：best-effort 序列
    base = await find_first_free_address(session, subnet)
    if base is None:
        return []
    start = ipaddress.ip_address(base)
    out6: list[str] = []
    for k in range(count):
        cand = start + k
        if cand not in net:
            break
        out6.append(str(cand))
    return out6
