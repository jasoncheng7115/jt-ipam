"""儀表板「IP 使用率」卡片：最多 4 個子網路各自的使用率（客戶 2026-10-23）。

客戶：「網段多的時候（12 個）單一總表的使用率沒有任何用途，只有在單一網段下有用。」
全部加總的那一圈只要有一個 /8 就永遠是 0%，所以改成逐子網路。

- 選哪幾個：user_preferences.pinned["usage_subnets"]（跟「IP 可用性追蹤」的 pinned["uptime_ips"]
  同一套通用釘選，經 PATCH /me/preferences 寫），上限 4。
- 寫入時（sanitize_pins）就把重複、不存在、已歸檔、看不到的拿掉：受限帳號釘不了範圍外的子網路。
  讀取時（usage_cards）再依可見範圍擋一次：權限之後被收回，舊的 id 還留在偏好裡。
- 沒選、或選的全部看不到了 → 自動：可見、未歸檔的子網路裡使用率最高的 4 個。
  排序在資料庫裡做（ORDER BY 比例 LIMIT 4），不把整個站台的子網路搬進 Python。
- 數字跟子網路清單頁同一個定義（services.subnet.get_usage）：已用＝這個子網路的 IP 記錄數、
  總數＝host_count（IPv4 扣網路／廣播位址，/31、/32 例外；IPv6 封頂 2^48）。
"""
from __future__ import annotations

import ipaddress
import uuid
from typing import Any, Literal

from sqlalchemy import Numeric, case, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sqlin import in_values
from app.models.address import IPAddress
from app.models.subnet import Subnet
from app.models.user import User
from app.schemas.base import StrictModel
from app.services.permission import visible_ids
from app.services.subnet import host_count

PIN_NAMESPACE = "usage_subnets"
MAX_SUBNETS = 4
#: 一次送進來的候選上限（防止塞一個超長清單進偏好）；畫面最多只送 4 個
_MAX_CANDIDATES = 200


class UsageSubnet(StrictModel):
    subnet_id: str
    cidr: str
    description: str | None = None
    customer_label: str | None = None
    ip_version: int
    used: int
    total: int
    used_pct: float


class SubnetUsageCards(StrictModel):
    #: pinned＝使用者自己選的；auto＝沒選，系統挑使用率最高的
    mode: Literal["pinned", "auto"]
    max: int = MAX_SUBNETS
    items: list[UsageSubnet]


def _parse_ids(raw: list[Any]) -> list[uuid.UUID]:
    out: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    for x in raw[:_MAX_CANDIDATES]:
        try:
            u = uuid.UUID(str(x))
        except ValueError:
            continue
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


async def _usable(session: AsyncSession, user: User, ids: list[uuid.UUID]) -> list[uuid.UUID]:
    """ids 裡存在、未歸檔、這個人看得到的（保留原本順序）。"""
    if not ids:
        return []
    vis = await visible_ids(session, user=user, object_type="subnet", required="read")
    if vis is not None:
        ids = [i for i in ids if i in vis]
        if not ids:
            return []
    alive = set((await session.execute(
        select(Subnet.id).where(Subnet.id.in_(ids), Subnet.archived_at.is_(None))   # bounded: ≤ 200
    )).scalars().all())
    return [i for i in ids if i in alive]


async def usable_subnet_ids(session: AsyncSession, user: User, raw: list[Any]) -> list[str]:
    """一般的「常用子網路」（pinned_subnet_ids）：同一套檢查，不限個數（最多看前 200 個）。

    客戶 2026-11-06：常用子網路裡留著一個已經刪掉或看不到的子網路，拓樸圖拿它當預設篩選 → 篩選欄只有一串 UUID、
    圖畫不出來。讀與寫都過這一關。
    """
    return [str(i) for i in await _usable(session, user, _parse_ids(raw))]


async def sanitize_pins(session: AsyncSession, user: User, raw: list[Any]) -> list[str]:
    """寫進偏好之前：去重、去掉不存在／已歸檔／看不到的，最多留 4 個。"""
    return [str(i) for i in (await _usable(session, user, _parse_ids(raw)))[:MAX_SUBNETS]]


def _total_expr() -> Any:
    """資料庫端的 host_count（只拿來排序；回傳的數字一律用 Python 的 host_count 算）。"""
    ml = func.masklen(Subnet.cidr)
    two = literal(2, Numeric)
    v4 = case((ml >= 31, func.power(two, 32 - ml)), else_=func.power(two, 32 - ml) - 2)
    v6 = func.least(func.power(two, 128 - ml), literal(1 << 48, Numeric))
    return case((func.family(Subnet.cidr) == 4, v4), else_=v6)


async def _customer_labels(session: AsyncSession, subs: list[Subnet]) -> dict[uuid.UUID, str]:
    from app.models.customer import Customer
    cids = {s.customer_id for s in subs if s.customer_id}
    if not cids:
        return {}
    rows = (await session.execute(
        select(Customer.id, Customer.name, Customer.title).where(Customer.id.in_(cids))   # bounded: ≤ 4
    )).all()
    return {r[0]: (r[2] or r[1]) for r in rows}


async def _items(session: AsyncSession, subs: list[Subnet], used: dict[uuid.UUID, int]) -> list[UsageSubnet]:
    labels = await _customer_labels(session, subs)
    out: list[UsageSubnet] = []
    for s in subs:
        try:
            net = ipaddress.ip_network(str(s.cidr), strict=False)
        except ValueError:
            continue
        total = host_count(net)
        u = int(used.get(s.id, 0))
        out.append(UsageSubnet(
            subnet_id=str(s.id), cidr=str(s.cidr), description=s.description,
            customer_label=labels.get(s.customer_id) if s.customer_id else None,
            ip_version=net.version, used=u, total=total,
            used_pct=round((u / total) * 100, 2) if total else 0.0,
        ))
    return out


async def usage_cards(session: AsyncSession, user: User, pinned_raw: list[Any] | None) -> SubnetUsageCards:
    pinned = (await _usable(session, user, _parse_ids(pinned_raw or [])))[:MAX_SUBNETS]
    if pinned:
        subs_by_id = {s.id: s for s in (await session.execute(
            select(Subnet).where(Subnet.id.in_(pinned))   # bounded: ≤ 4
        )).scalars().all()}
        counts = {r[0]: int(r[1]) for r in (await session.execute(
            select(IPAddress.subnet_id, func.count()).where(IPAddress.subnet_id.in_(pinned))   # bounded: ≤ 4
            .group_by(IPAddress.subnet_id)
        )).all()}
        subs = [subs_by_id[i] for i in pinned if i in subs_by_id]
        return SubnetUsageCards(mode="pinned", items=await _items(session, subs, counts))

    # 自動：可見、未歸檔、容量 > 0 的子網路裡比例最高的 4 個（平手看已用數，再看 CIDR）
    vis = await visible_ids(session, user=user, object_type="subnet", required="read")
    if vis is not None and not vis:
        return SubnetUsageCards(mode="auto", items=[])
    used_q = select(IPAddress.subnet_id.label("sid"), func.count().label("n")).group_by(IPAddress.subnet_id)
    if vis is not None:
        used_q = used_q.where(in_values(IPAddress.subnet_id, vis))
    used_sq = used_q.subquery()
    n = func.coalesce(used_sq.c.n, 0)
    total = _total_expr()
    stmt = (select(Subnet, n.label("used"))
            .outerjoin(used_sq, used_sq.c.sid == Subnet.id)
            .where(Subnet.archived_at.is_(None), total > 0)
            .order_by((n / total).desc(), n.desc(), Subnet.cidr)
            .limit(MAX_SUBNETS))
    if vis is not None:
        stmt = stmt.where(in_values(Subnet.id, vis))
    rows = (await session.execute(stmt)).all()
    subs = [r[0] for r in rows]
    counts = {r[0].id: int(r[1]) for r in rows}
    return SubnetUsageCards(mode="auto", items=await _items(session, subs, counts))
