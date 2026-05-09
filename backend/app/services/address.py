"""IPAddress 業務邏輯。"""

from __future__ import annotations

import ipaddress

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.address import IPAddress
from app.models.subnet import Subnet
from app.services.subnet import find_first_free_address


class IPNotInSubnet(ValueError):
    pass


class IPAlreadyExists(ValueError):
    pass


class SubnetFull(ValueError):
    pass


def assert_in_subnet(ip: str, subnet_cidr: str) -> None:
    # 容忍 "192.168.10.1/32" 這種帶 mask 的字串（asyncpg INET 反序列化結果）
    addr = ipaddress.ip_address(ip.split("/", 1)[0])
    # 同樣 subnet_cidr 也可能是 IPv4Network 物件被轉字串
    net = ipaddress.ip_network(subnet_cidr, strict=False)
    if addr not in net:
        raise IPNotInSubnet(f"{ip} not in {subnet_cidr}")


async def allocate_first_free(
    session: AsyncSession,
    *,
    subnet: Subnet,
    hostname: str | None,
    description: str | None,
    mac: str | None,
    state: str,
) -> IPAddress:
    candidate = await find_first_free_address(session, subnet)
    if candidate is None:
        raise SubnetFull(f"Subnet {subnet.cidr} is full")
    return await create_ip(
        session,
        subnet=subnet,
        ip=candidate,
        hostname=hostname,
        description=description,
        mac=mac,
        state=state,
    )


async def create_ip(
    session: AsyncSession,
    *,
    subnet: Subnet,
    ip: str,
    hostname: str | None = None,
    description: str | None = None,
    mac: str | None = None,
    state: str = "active",
    discovery_source: str = "manual",
) -> IPAddress:
    assert_in_subnet(ip, str(subnet.cidr))

    existing = await session.scalar(
        select(IPAddress).where(IPAddress.subnet_id == subnet.id, IPAddress.ip == ip)
    )
    if existing is not None:
        raise IPAlreadyExists(f"{ip} already exists in subnet {subnet.cidr}")

    obj = IPAddress(
        subnet_id=subnet.id,
        ip=ip,
        hostname=hostname,
        description=description,
        mac=mac,
        state=state,
        discovery_source=discovery_source,
    )
    session.add(obj)
    await session.flush()
    return obj
