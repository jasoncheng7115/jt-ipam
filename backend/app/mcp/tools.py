"""IPAM 工具實作（給 MCP server 與 NL chat 共用）。

所有工具：
- 接受 plain dict 參數（避免 Pydantic 把 LLM 不嚴謹的型別當錯）
- 走 SQLAlchemy session；不繞 REST
- 每個工具回傳 JSON-serialisable dict
- 失敗時 raise IPAMToolError（含人讀訊息）
"""

from __future__ import annotations

import ipaddress
import uuid
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.address import IPAddress
from app.models.device import Device
from app.models.dns import DNSRecord
from app.models.librenms import ARPEntry, FDBEntry
from app.models.section import Section
from app.models.subnet import Subnet
from app.models.user import User
from app.models.vlan import VLAN
from app.services.address import (
    IPAlreadyExists,
    IPNotInSubnet,
    SubnetFull,
    allocate_first_free,
    create_ip,
)
from app.services.permission import filter_visible
from app.services.subnet import find_first_free_address, get_usage


class IPAMToolError(Exception):
    pass


# ─────────────────── 唯讀工具 ───────────────────


async def search_ip(session: AsyncSession, *, user: User, ip: str) -> dict[str, Any]:
    """根據 IP 找它在 IPAM 的紀錄與所屬 subnet。"""
    try:
        ipaddress.ip_address(ip)
    except ValueError as exc:
        raise IPAMToolError(f"Invalid IP: {exc}") from exc
    ips = list(
        (
            await session.execute(
                select(IPAddress).where(IPAddress.ip == ip)
            )
        ).scalars().all()
    )
    visible = set(
        await filter_visible(
            session, user=user, object_type="subnet",
            object_ids=[r.subnet_id for r in ips], required="read",
        )
    )
    out = []
    for r in ips:
        if r.subnet_id not in visible:
            continue
        out.append({
            "id": str(r.id),
            "subnet_id": str(r.subnet_id),
            "ip": str(r.ip).split("/")[0],
            "hostname": r.hostname,
            "mac": str(r.mac) if r.mac else None,
            "state": r.state,
            "owner": r.owner,
            "description": r.description,
            "effective_status": r.effective_status,
        })
    return {"ip": ip, "matches": out, "count": len(out)}


async def find_free_ip(
    session: AsyncSession, *, user: User, subnet_cidr: str | None = None,
    subnet_id: str | None = None,
) -> dict[str, Any]:
    """找指定 subnet 的第一個空閒 IP。可給 cidr 或 subnet_id。"""
    subnet: Subnet | None = None
    if subnet_id:
        subnet = await session.get(Subnet, uuid.UUID(subnet_id))
    elif subnet_cidr:
        # 透過 cidr 直接查
        rows = (
            await session.execute(
                text("SELECT id::text AS id FROM subnets WHERE cidr = CAST(:c AS cidr) LIMIT 1"),
                {"c": subnet_cidr},
            )
        ).first()
        if rows:
            subnet = await session.get(Subnet, uuid.UUID(rows.id))
    if subnet is None:
        raise IPAMToolError("subnet not found")
    visible = set(await filter_visible(
        session, user=user, object_type="subnet",
        object_ids=[subnet.id], required="read",
    ))
    if subnet.id not in visible:
        raise IPAMToolError("subnet not visible to this user")
    ip = await find_first_free_address(session, subnet)
    return {
        "subnet_id": str(subnet.id),
        "cidr": str(subnet.cidr),
        "ip": ip,
    }


async def list_subnets(
    session: AsyncSession, *, user: User, section_id: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    if limit > 200:
        limit = 200
    stmt = select(Subnet)
    if section_id:
        stmt = stmt.where(Subnet.section_id == uuid.UUID(section_id))
    stmt = stmt.order_by(Subnet.cidr).limit(limit)
    rows = list((await session.execute(stmt)).scalars().all())
    visible = set(await filter_visible(
        session, user=user, object_type="subnet",
        object_ids=[r.id for r in rows], required="read",
    ))
    items = []
    for r in rows:
        if r.id not in visible:
            continue
        total, used, free, pct = await get_usage(session, r)
        items.append({
            "id": str(r.id),
            "cidr": str(r.cidr),
            "description": r.description,
            "section_id": str(r.section_id),
            "vlan_id": str(r.vlan_id) if r.vlan_id else None,
            "vrf_id": str(r.vrf_id) if r.vrf_id else None,
            "used": used, "total": total, "free": free, "used_pct": pct,
        })
    return {"subnets": items, "count": len(items)}


async def get_subnet_usage(
    session: AsyncSession, *, user: User, subnet_id: str,
) -> dict[str, Any]:
    s = await session.get(Subnet, uuid.UUID(subnet_id))
    if s is None:
        raise IPAMToolError("subnet not found")
    visible = set(await filter_visible(
        session, user=user, object_type="subnet",
        object_ids=[s.id], required="read",
    ))
    if s.id not in visible:
        raise IPAMToolError("subnet not visible")
    total, used, free, pct = await get_usage(session, s)
    return {
        "subnet_id": subnet_id, "cidr": str(s.cidr),
        "total": total, "used": used, "free": free, "used_pct": pct,
    }


async def trace_mac(
    session: AsyncSession, *, user: User, mac: str,
) -> dict[str, Any]:
    """從 MAC 反查 ARP（→ IP）+ FDB（→ switch port）。"""
    mac = mac.lower().replace("-", ":")
    arp = (
        await session.execute(
            select(ARPEntry).where(ARPEntry.mac == mac)
            .order_by(ARPEntry.last_seen_at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    fdb = (
        await session.execute(
            select(FDBEntry).where(FDBEntry.mac == mac)
            .order_by(FDBEntry.last_seen_at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    return {
        "mac": mac,
        "arp": (
            {
                "ip": arp.ip,
                "device_id": str(arp.device_id) if arp.device_id else None,
                "interface": arp.interface,
                "last_seen_at": arp.last_seen_at.isoformat(),
            }
            if arp else None
        ),
        "fdb": (
            {
                "port_name": fdb.port_name,
                "vlan_id_num": fdb.vlan_id_num,
                "device_id": str(fdb.device_id) if fdb.device_id else None,
                "last_seen_at": fdb.last_seen_at.isoformat(),
            }
            if fdb else None
        ),
    }


async def list_vlans(
    session: AsyncSession, *, _user: User, number: int | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    if limit > 500:
        limit = 500
    stmt = select(VLAN)
    if number is not None:
        stmt = stmt.where(VLAN.number == number)
    rows = list((await session.execute(stmt.order_by(VLAN.number).limit(limit))).scalars().all())
    return {
        "vlans": [
            {
                "id": str(r.id), "domain_id": str(r.domain_id),
                "number": r.number, "name": r.name, "description": r.description,
            }
            for r in rows
        ],
        "count": len(rows),
    }


async def check_dns_consistency(
    session: AsyncSession, *, _user: User,
) -> dict[str, Any]:
    """彙整 DNS 與 IPAM 資料一致性狀態（呼叫前需先跑 sync_server）。"""
    rows = (
        await session.execute(
            select(DNSRecord.consistency_state, func.count())
            .group_by(DNSRecord.consistency_state)
        )
    ).all()
    return {"summary": {state: int(cnt) for state, cnt in rows}}


# ─────────────────── 寫入工具（admin only）───────────────────


async def allocate_ip(
    session: AsyncSession, *, user: User,
    subnet_id: str | None = None, subnet_cidr: str | None = None,
    hostname: str | None = None, description: str | None = None,
    requested_ip: str | None = None,
) -> dict[str, Any]:
    if not user.is_admin:
        raise IPAMToolError("allocate_ip requires admin")
    subnet: Subnet | None = None
    if subnet_id:
        subnet = await session.get(Subnet, uuid.UUID(subnet_id))
    elif subnet_cidr:
        rows = (
            await session.execute(
                text("SELECT id::text AS id FROM subnets WHERE cidr = CAST(:c AS cidr) LIMIT 1"),
                {"c": subnet_cidr},
            )
        ).first()
        if rows:
            subnet = await session.get(Subnet, uuid.UUID(rows.id))
    if subnet is None:
        raise IPAMToolError("subnet not found")
    try:
        if requested_ip:
            obj = await create_ip(
                session, subnet=subnet, ip=requested_ip,
                hostname=hostname, description=description,
            )
        else:
            obj = await allocate_first_free(
                session, subnet=subnet,
                hostname=hostname, description=description,
                mac=None, state="active",
            )
    except IPAlreadyExists as exc:
        raise IPAMToolError(f"already allocated: {exc}") from exc
    except IPNotInSubnet as exc:
        raise IPAMToolError(f"not in subnet: {exc}") from exc
    except SubnetFull as exc:
        raise IPAMToolError(f"subnet full: {exc}") from exc
    await session.commit()
    return {
        "ip_address_id": str(obj.id),
        "ip": str(obj.ip).split("/")[0],
        "subnet_id": str(subnet.id),
        "hostname": obj.hostname,
    }


# ─────────────────── 工具註冊表（給 MCP / chat 共用）───────────────────


# 每個 tool entry：name → (callable, description, json schema for parameters)
TOOLS: dict[str, dict[str, Any]] = {
    "search_ip": {
        "fn": search_ip,
        "description": "Find IPAM records and subnet for a given IP address.",
        "parameters": {
            "type": "object",
            "properties": {"ip": {"type": "string", "description": "IPv4 or IPv6"}},
            "required": ["ip"],
        },
    },
    "find_free_ip": {
        "fn": find_free_ip,
        "description": "Get the first free IP in a subnet (by id or CIDR).",
        "parameters": {
            "type": "object",
            "properties": {
                "subnet_id": {"type": "string"},
                "subnet_cidr": {"type": "string", "description": "e.g. 10.0.0.0/24"},
            },
        },
    },
    "list_subnets": {
        "fn": list_subnets,
        "description": "List subnets with usage; optional section_id filter.",
        "parameters": {
            "type": "object",
            "properties": {
                "section_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
        },
    },
    "get_subnet_usage": {
        "fn": get_subnet_usage,
        "description": "Get used/total/pct for a subnet by id.",
        "parameters": {
            "type": "object",
            "properties": {"subnet_id": {"type": "string"}},
            "required": ["subnet_id"],
        },
    },
    "trace_mac": {
        "fn": trace_mac,
        "description": "Trace a MAC: ARP (→ IP + L3 device) + FDB (→ switch port + VLAN).",
        "parameters": {
            "type": "object",
            "properties": {"mac": {"type": "string", "description": "MAC address"}},
            "required": ["mac"],
        },
    },
    "list_vlans": {
        "fn": list_vlans,
        "description": "List VLANs; optional exact number lookup.",
        "parameters": {
            "type": "object",
            "properties": {
                "number": {"type": "integer", "minimum": 1, "maximum": 4094},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
        },
    },
    "check_dns_consistency": {
        "fn": check_dns_consistency,
        "description": "Summary of DNS↔IPAM consistency states across all zones.",
        "parameters": {"type": "object", "properties": {}},
    },
    "allocate_ip": {
        "fn": allocate_ip,
        "description": (
            "ADMIN ONLY. Allocate an IP in a subnet. Provide subnet_id or "
            "subnet_cidr; if requested_ip given, that IP is used; otherwise the "
            "first free IP is allocated."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "subnet_id": {"type": "string"},
                "subnet_cidr": {"type": "string"},
                "hostname": {"type": "string"},
                "description": {"type": "string"},
                "requested_ip": {"type": "string"},
            },
        },
    },
}
