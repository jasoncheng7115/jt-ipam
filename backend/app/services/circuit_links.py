"""電路的 WAN 位址關聯（circuit_addresses）衍生出的查詢：VPN 端點跑在哪條電路上。

電路詳細資料 API 與 AI 工具共用，免得兩邊各寫一份比對規則。
"""
from __future__ import annotations

import ipaddress
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sqlin import in_values
from app.models.address import IPAddress
from app.models.advanced import Circuit, CircuitAddress
from app.models.subnet import Subnet


async def circuits_carrying(session: AsyncSession, endpoint: str | None) -> list[Circuit]:
    """端點位址是哪條電路關聯的 WAN IP，或落在哪條電路關聯的子網路裡。端點是名稱（FQDN）時不推。"""
    if not endpoint:
        return []
    try:
        ip = ipaddress.ip_address(endpoint.strip().split("/")[0])
    except ValueError:
        return []
    by_ip = (select(CircuitAddress.circuit_id).join(IPAddress, IPAddress.id == CircuitAddress.address_id)
             .where(func.host(IPAddress.ip) == str(ip)))
    by_net = (select(CircuitAddress.circuit_id).join(Subnet, Subnet.id == CircuitAddress.subnet_id)
              .where(Subnet.cidr.op(">>=")(func.cast(str(ip), INET))))
    ids: set[Any] = set((await session.execute(by_ip)).scalars()) | set((await session.execute(by_net)).scalars())
    if not ids:
        return []
    return list((await session.execute(
        select(Circuit).where(in_values(Circuit.id, ids)).order_by(Circuit.cid))).scalars())
