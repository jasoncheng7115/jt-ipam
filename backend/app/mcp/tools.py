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

from sqlalchemy import false as sa_false
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.address import IPAddress
from app.models.customer import Customer
from app.models.device import Device
from app.models.dns import DNSRecord
from app.models.librenms import ARPEntry, FDBEntry, LibreNMSDevice
from app.models.location import Location, Rack
from app.models.nat import NATTranslation
from app.models.section import Section
from app.models.subnet import Subnet
from app.models.user import User
from app.models.vlan import VLAN, DeviceVLAN
from app.services.address import (
    IPAlreadyExists,
    IPNotInSubnet,
    SubnetFull,
    allocate_first_free,
    create_ip,
)
from app.services.oui import vendor_for_mac
from app.services.permission import filter_visible, visible_ids
from app.services.subnet import find_first_free_address, find_free_addresses, get_usage


class IPAMToolError(Exception):
    pass


def _as_uuid(value: str, field: str = "id") -> uuid.UUID:
    """把 LLM 給的字串轉 UUID；格式不對就回優雅錯誤（避免 ValueError 變成 tool failed）。"""
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError) as exc:
        raise IPAMToolError(f"invalid {field}: {value!r}") from exc


async def _effective_probes(
    session: AsyncSession, sub: Subnet | None, obj: IPAddress,
) -> list[str]:
    """此 IP 實際會被執行的探測 = 子網路 scan_method − IP excluded ∩ 代理 enabled。"""
    from app.core.scan_probes import effective_probes
    from app.models.scan_agent import ScanAgent
    if sub is None or not sub.scan_enabled:
        return []
    agent_enabled: list[str] | None = None
    if sub.scan_agent_id:
        ag = await session.get(ScanAgent, sub.scan_agent_id)
        if ag is not None:
            agent_enabled = list(ag.enabled_probes or [])
    return effective_probes(
        list(sub.scan_method or []), list(obj.excluded_probes or []), agent_enabled,
    )


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
        subnet = await session.get(Subnet, _as_uuid(subnet_id, "subnet_id"))
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


async def _resolve_subnet(
    session: AsyncSession, *, user: User,
    subnet_id: str | None, subnet_cidr: str | None,
) -> Subnet:
    subnet: Subnet | None = None
    if subnet_id:
        subnet = await session.get(Subnet, _as_uuid(subnet_id, "subnet_id"))
    elif subnet_cidr:
        rows = (await session.execute(
            text("SELECT id::text AS id FROM subnets WHERE cidr = CAST(:c AS cidr) LIMIT 1"),
            {"c": subnet_cidr},
        )).first()
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
    return subnet


async def find_free_ips(
    session: AsyncSession, *, user: User,
    subnet_cidr: str | None = None, subnet_id: str | None = None,
    count: int = 1, consecutive: bool = False,
) -> dict[str, Any]:
    """找指定 subnet 內 count 個可用 IP；consecutive=True 要求連續一段。

    回傳真實未配發的 IP（已排除 ip_addresses 既有紀錄），不要自行臆測。
    """
    count = max(1, min(int(count), 256))
    subnet = await _resolve_subnet(
        session, user=user, subnet_id=subnet_id, subnet_cidr=subnet_cidr,
    )
    ips = await find_free_addresses(
        session, subnet, count=count, consecutive=consecutive,
    )
    return {
        "subnet_id": str(subnet.id),
        "cidr": str(subnet.cidr),
        "requested": count,
        "consecutive": consecutive,
        "found": len(ips),
        "ips": ips,
        "note": (
            "fewer free IPs than requested" if len(ips) < count and not consecutive
            else ("no consecutive run of that size" if consecutive and not ips else "ok")
        ),
    }


async def list_subnets(
    session: AsyncSession, *, user: User, section_id: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    if limit > 200:
        limit = 200
    stmt = select(Subnet)
    if section_id:
        stmt = stmt.where(Subnet.section_id == _as_uuid(section_id, "section_id"))
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
            "customer_id": str(r.customer_id) if r.customer_id else None,
            "gateway": r.gateway, "scan_enabled": r.scan_enabled,
            "used": used, "total": total, "free": free, "used_pct": pct,
        })
    return {"subnets": items, "count": len(items)}


async def get_subnet_usage(
    session: AsyncSession, *, user: User, subnet_id: str,
) -> dict[str, Any]:
    s = await session.get(Subnet, _as_uuid(subnet_id, "subnet_id"))
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
    # RBAC：ARP 依其 IP 子網路可見性、FDB 依其 switch 裝置可見性遮蔽
    vis_sub = await visible_ids(session, user=user, object_type="subnet")
    vis_dev = await visible_ids(session, user=user, object_type="device")
    if arp is not None and vis_sub is not None:
        # 重疊網段：同 IP 可能多筆 → limit(1)+first()，避免 MultipleResultsFound
        ip_sub = (await session.execute(
            select(IPAddress.subnet_id).where(IPAddress.ip == arp.ip).limit(1))).scalars().first()
        if ip_sub is None or ip_sub not in vis_sub:
            arp = None
    if fdb is not None and vis_dev is not None and (
            fdb.device_id is None or fdb.device_id not in vis_dev):
        fdb = None
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
    session: AsyncSession, *, user: User, number: int | None = None,
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
    session: AsyncSession, *, user: User,
) -> dict[str, Any]:
    """彙整 DNS 與 IPAM 資料一致性狀態（呼叫前需先跑 sync_server）。"""
    rows = (
        await session.execute(
            select(DNSRecord.consistency_state, func.count())
            .group_by(DNSRecord.consistency_state)
        )
    ).all()
    return {"summary": {state: int(cnt) for state, cnt in rows}}


async def stats_overview(session: AsyncSession, *, user: User) -> dict[str, Any]:
    """各類實體的總數。逐物件計數依使用者可見範圍縮放；全域基礎設施計數僅 admin/萬用讀取者可見。"""
    async def _all(model) -> int:  # type: ignore[no-untyped-def]
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)

    async def _scoped(model, vis) -> int:  # type: ignore[no-untyped-def]
        if vis is None:
            return await _all(model)
        if not vis:
            return 0
        return int(await session.scalar(
            select(func.count()).select_from(model).where(model.id.in_(vis))) or 0)

    vis_sec = await visible_ids(session, user=user, object_type="section")
    vis_sub = await visible_ids(session, user=user, object_type="subnet")
    vis_dev = await visible_ids(session, user=user, object_type="device")
    vis_rack = await visible_ids(session, user=user, object_type="rack")
    vis_loc = await visible_ids(session, user=user, object_type="location")
    vis_cust = await visible_ids(session, user=user, object_type="customer")
    # IP 數依可見子網路縮放（IPAddress 本身不可逐物件授權，靠所屬子網路）
    if vis_sub is None:
        ip_n = await _all(IPAddress)
    elif not vis_sub:
        ip_n = 0
    else:
        ip_n = int(await session.scalar(
            select(func.count()).select_from(IPAddress)
            .where(IPAddress.subnet_id.in_(vis_sub))) or 0)

    out: dict[str, Any] = {
        "sections": await _scoped(Section, vis_sec),
        "subnets": await _scoped(Subnet, vis_sub),
        "ip_addresses": ip_n,
        "devices": await _scoped(Device, vis_dev),
        "racks": await _scoped(Rack, vis_rack),
        "locations": await _scoped(Location, vis_loc),
        "customers": await _scoped(Customer, vis_cust),
    }
    # 全域基礎設施計數：僅 admin / 萬用讀取者
    if await has_global_read(session, user):
        from app.models.advanced import ASN, Circuit, Contact, Provider, Tenant
        from app.models.physical import Cable
        from app.models.virt import VirtualMachine
        out.update({
            "vlans": await _all(VLAN),
            "nat_rules": await _all(NATTranslation),
            "vms": await _all(VirtualMachine),
            "circuits": await _all(Circuit),
            "providers": await _all(Provider),
            "asns": await _all(ASN),
            "tenants": await _all(Tenant),
            "contacts": await _all(Contact),
            "cables": await _all(Cable),
        })
    return out


async def list_racks(
    session: AsyncSession, *, user: User, limit: int = 200,
) -> dict[str, Any]:
    """列出機櫃（含所在地點與已掛裝置數）。"""
    limit = min(int(limit), 500)
    rows = (await session.execute(
        select(Rack, Location.name)
        .outerjoin(Location, Location.id == Rack.location_id)
        .order_by(Rack.name).limit(limit)
    )).all()
    vis = await visible_ids(session, user=user, object_type="rack")
    out = []
    for rack, loc_name in rows:
        if vis is not None and rack.id not in vis:
            continue
        devs = list((await session.execute(
            select(Device.name, Device.type, Device.u_position, Device.u_size, Device.rack_face)
            .where(Device.rack_id == rack.id).order_by(Device.u_position)
        )).all())
        # 以「實際被佔用的 U 列」計算，半 U（左/右兩台同列）只算一次，避免重複累加把空間算光
        occupied_rows: set[int] = set()
        for (_n, _t, pos, sz, _f) in devs:
            if pos is None:
                continue
            for u in range(int(pos), int(pos) + int(sz or 1)):
                occupied_rows.add(u)
        used_u = len(occupied_rows)
        free_u = max(rack.u_height - used_u, 0)
        # 連續空檔（給「還能放多大的裝置」參考）
        free_rows = sorted(set(range(1, rack.u_height + 1)) - occupied_rows)
        out.append({
            "id": str(rack.id), "name": rack.name, "u_height": rack.u_height,
            "location": loc_name, "device_count": len(devs),
            "used_u": used_u, "free_u": free_u, "free_u_rows": free_rows,
            "devices": [
                {"name": n, "type": t, "u_position": pos, "u_size": sz, "rack_face": f}
                for (n, t, pos, sz, f) in devs
            ],
            "description": rack.description,
        })
    return {"racks": out, "count": len(out)}


async def list_locations(
    session: AsyncSession, *, user: User, limit: int = 200,
) -> dict[str, Any]:
    """列出地點（含機櫃數）。"""
    limit = min(int(limit), 500)
    rows = list((await session.execute(
        select(Location).order_by(Location.name).limit(limit)
    )).scalars().all())
    vis = await visible_ids(session, user=user, object_type="location")
    out = []
    for loc in rows:
        if vis is not None and loc.id not in vis:
            continue
        rack_count = int(await session.scalar(
            select(func.count()).select_from(Rack).where(Rack.location_id == loc.id)
        ) or 0)
        device_count = int(await session.scalar(
            select(func.count()).select_from(Device).where(Device.location_id == loc.id)
        ) or 0)
        out.append({
            "id": str(loc.id), "name": loc.name, "address": loc.address,
            "rack_count": rack_count, "device_count": device_count,
            "customer_id": str(loc.customer_id) if loc.customer_id else None,
            "description": loc.description,
        })
    return {"locations": out, "count": len(out)}


async def list_devices(
    session: AsyncSession, *, user: User,
    name: str | None = None, type: str | None = None, limit: int = 100,
) -> dict[str, Any]:
    """列出/搜尋裝置（可給 name 子字串或 type 過濾）；含地點、機櫃、IP 數。"""
    limit = min(int(limit), 500)
    stmt = select(Device)
    if name:
        stmt = stmt.where(Device.name.ilike(f"%{name}%"))
    if type:
        stmt = stmt.where(Device.type == type)
    rows = list((await session.execute(stmt.order_by(Device.name).limit(limit))).scalars().all())
    vis = await visible_ids(session, user=user, object_type="device")
    cust_cache: dict[Any, str | None] = {}
    out = []
    for d in rows:
        if vis is not None and d.id not in vis:
            continue
        ip_count = int(await session.scalar(
            select(func.count()).select_from(IPAddress).where(IPAddress.device_id == d.id)
        ) or 0)
        cust_name = None
        if d.customer_id is not None:
            if d.customer_id not in cust_cache:
                c = await session.get(Customer, d.customer_id)
                cust_cache[d.customer_id] = c.name if c else None
            cust_name = cust_cache[d.customer_id]
        out.append({
            "id": str(d.id), "name": d.name, "type": d.type, "fqdn": d.fqdn,
            "vendor": d.vendor, "model": d.model, "ip_count": ip_count,
            "customer": cust_name,
            "u_position": d.u_position, "u_size": d.u_size, "rack_face": d.rack_face,
            "rack_id": str(d.rack_id) if d.rack_id else None,
        })
    return {"devices": out, "count": len(out)}


async def get_device(
    session: AsyncSession, *, user: User,
    device_id: str | None = None, name: str | None = None,
) -> dict[str, Any]:
    """裝置詳情：基本資料 + IP 清單 + （透過 LibreNMS）VLAN 與 switch port。"""
    dev: Device | None = None
    if device_id:
        dev = await session.get(Device, _as_uuid(device_id, "device_id"))
    elif name:
        dev = (await session.execute(
            select(Device).where(Device.name.ilike(name)).limit(1)
        )).scalar_one_or_none()
    if dev is None:
        raise IPAMToolError("device not found")
    vis = await visible_ids(session, user=user, object_type="device")
    if vis is not None and dev.id not in vis:
        raise IPAMToolError("device not found")   # 不洩漏不可見裝置
    ips = list((await session.execute(
        select(IPAddress.ip, IPAddress.hostname, IPAddress.mac)
        .where(IPAddress.device_id == dev.id)
    )).all())
    # VLAN（透過連結的 librenms device）
    vlans = list((await session.execute(
        select(VLAN.number, VLAN.name)
        .join(DeviceVLAN, DeviceVLAN.vlan_id == VLAN.id)
        .join(LibreNMSDevice, LibreNMSDevice.id == DeviceVLAN.librenms_device_id)
        .where(LibreNMSDevice.jt_ipam_device_id == dev.id)
        .distinct()
    )).all())
    rack_info = None
    if dev.rack_id:
        rk = await session.get(Rack, dev.rack_id)
        if rk is not None:
            rack_info = {"id": str(rk.id), "name": rk.name, "u_height": rk.u_height}
    cust = await session.get(Customer, dev.customer_id) if dev.customer_id else None
    loc = await session.get(Location, dev.location_id) if dev.location_id else None
    # 電源埠 ↔ 插座（NetBox 風）
    from app.models.physical import DevicePowerPort, PowerOutlet
    pports = list((await session.execute(
        select(DevicePowerPort).where(DevicePowerPort.device_id == dev.id)
    )).scalars().all())
    power_ports = []
    for pp in pports:
        outlet = await session.get(PowerOutlet, pp.outlet_id) if pp.outlet_id else None
        power_ports.append({
            "name": pp.name, "max_watts": pp.max_watts,
            "outlet": outlet.label if outlet else None,
        })
    return {
        "id": str(dev.id), "name": dev.name, "type": dev.type, "fqdn": dev.fqdn,
        "vendor": dev.vendor, "model": dev.model, "serial": dev.serial,
        "description": dev.description,
        "customer": cust.name if cust else None,
        "location": loc.name if loc else None,
        # 機櫃 U 位資訊（讓 AI 能判斷占位 / 剩餘空間）
        "u_position": dev.u_position, "u_size": dev.u_size, "rack_face": dev.rack_face,
        "rack": rack_info,
        "ips": [{"ip": str(ip), "hostname": hn, "mac": str(m) if m else None}
                for ip, hn, m in ips],
        "vlans": [{"number": n, "name": nm} for n, nm in vlans],
        "power_ports": power_ports,
    }


async def list_customers(
    session: AsyncSession, *, user: User, limit: int = 200,
) -> dict[str, Any]:
    """列出客戶 / 管理單位。"""
    limit = min(int(limit), 500)
    rows = list((await session.execute(
        select(Customer).order_by(Customer.name).limit(limit)
    )).scalars().all())
    vis = await visible_ids(session, user=user, object_type="customer")
    out = [
        {"id": str(c.id), "name": c.name, "title": c.title, "contact": c.contact,
         "email": c.email, "phone": c.phone, "address": c.address}
        for c in rows
        if vis is None or c.id in vis
    ]
    return {"customers": out, "count": len(out)}


async def list_nat(
    session: AsyncSession, *, user: User, limit: int = 100,
) -> dict[str, Any]:
    """列出 NAT 規則。"""
    limit = min(int(limit), 500)
    rows = list((await session.execute(
        select(NATTranslation).order_by(NATTranslation.name).limit(limit)
    )).scalars().all())
    ip_cache: dict[Any, str | None] = {}

    async def _ip(ip_id: Any) -> str | None:
        if ip_id is None:
            return None
        if ip_id not in ip_cache:
            o = await session.get(IPAddress, ip_id)
            ip_cache[ip_id] = str(o.ip) if o else None
        return ip_cache[ip_id]

    out = []
    for r in rows:
        out.append({
            "id": str(r.id), "name": r.name, "type": r.type,
            "interface": r.src_interface, "protocol": r.protocol,
            "ip_version": r.ip_version, "disabled": r.disabled, "no_rdr": r.no_rdr,
            # 來源 / 目的：優先顯示關聯到的 jt-ipam IP，否則顯示 OPNsense 別名
            "src_ip": await _ip(r.src_ip_id), "src_alias": r.src_alias,
            "dst_ip": await _ip(r.dst_ip_id), "dst_alias": r.dst_alias,
            "src_port": r.src_port, "dst_port": r.dst_port,
            "redirect_alias": r.redirect_alias,
            "source_origin": r.source_origin,
            "description": r.description,
        })
    return {"nat_rules": out, "count": len(out)}


async def list_sections(session: AsyncSession, *, user: User, limit: int = 200) -> dict[str, Any]:
    """列出區段（含每區段子網路數）。"""
    limit = min(int(limit), 500)
    rows = list((await session.execute(
        select(Section).order_by(Section.name).limit(limit)
    )).scalars().all())
    vis = await visible_ids(session, user=user, object_type="section")
    out = []
    for s in rows:
        if vis is not None and s.id not in vis:
            continue
        sub_n = int(await session.scalar(
            select(func.count()).select_from(Subnet).where(Subnet.section_id == s.id)
        ) or 0)
        out.append({"id": str(s.id), "name": s.name, "subnet_count": sub_n,
                    "description": s.description})
    return {"sections": out, "count": len(out)}


async def list_vrfs(session: AsyncSession, *, user: User, limit: int = 200) -> dict[str, Any]:
    """列出 VRF。"""
    from app.models.vrf import VRF
    limit = min(int(limit), 500)
    rows = list((await session.execute(
        select(VRF).order_by(VRF.name).limit(limit)
    )).scalars().all())
    return {"vrfs": [{"id": str(r.id), "name": r.name, "rd": r.rd,
                      "description": r.description} for r in rows], "count": len(rows)}


async def list_vpn_tunnels(session: AsyncSession, *, user: User, limit: int = 200) -> dict[str, Any]:
    """列出 VPN 通道（從防火牆如 OPNsense 拉回的 WireGuard / IPsec / OpenVPN）。

    site_to_site=true 且有 a_device + b_device → 兩台已知裝置間「已確認對接」的 site-to-site 通道；
    b_endpoint 為對端閘道位址（對端非本系統管理的 device 時）。
    """
    from app.models.device import Device
    from app.models.physical import VPNTunnel

    rows = list((await session.execute(
        select(VPNTunnel).order_by(VPNTunnel.name).limit(min(int(limit), 500))
    )).scalars().all())
    dev_ids = {d for t in rows for d in (t.a_device_id, t.b_device_id) if d}
    # 解析每台 device 的名稱與管理 IP（讓答案能說「VPN 做在哪台裝置 / 哪個 IP 上」）
    names: dict[Any, str] = {}
    dev_pip: dict[Any, Any] = {}
    if dev_ids:
        for did, nm, pip in (await session.execute(
            select(Device.id, Device.name, Device.primary_ip_id).where(Device.id.in_(dev_ids))
        )).all():
            names[did] = nm
            dev_pip[did] = pip
    pip_map: dict[Any, str] = {}
    pip_ids = {p for p in dev_pip.values() if p}
    if pip_ids:
        pip_map = {pid: str(ip).split("/")[0] for pid, ip in (await session.execute(
            select(IPAddress.id, IPAddress.ip).where(IPAddress.id.in_(pip_ids))
        )).all()}

    def _dev_ip(did) -> str | None:  # type: ignore[no-untyped-def]
        if did is None:
            return None
        pip = dev_pip.get(did)
        if pip and pip in pip_map:
            return pip_map[pip]
        nm = names.get(did)            # 防火牆常以管理 IP 命名 → 名稱本身可能就是 IP
        if nm:
            try:
                ipaddress.ip_address(nm.strip())
                return nm.strip()
            except ValueError:
                pass
        return None

    out = []
    for t in rows:
        out.append({
            "name": t.name, "type": t.type, "status": t.status,
            "a_device": names.get(t.a_device_id),
            "a_device_ip": _dev_ip(t.a_device_id),
            "a_endpoint": t.a_endpoint,
            "b_device": names.get(t.b_device_id),
            "b_device_ip": _dev_ip(t.b_device_id),
            "b_endpoint": t.b_endpoint,
            "site_to_site": bool(t.a_device_id and t.b_device_id),
        })
    return {"vpn_tunnels": out, "count": len(out)}


async def recent_ip_changes(
    session: AsyncSession, *, user: User, ip: str | None = None, limit: int = 20,
) -> dict[str, Any]:
    """最近的 IP 異動記錄（可指定某 IP）。"""
    from app.models.ip_change_log import IPChangeLog
    limit = min(int(limit), 100)
    stmt = select(IPChangeLog)
    if ip:
        stmt = stmt.where(IPChangeLog.ip_text == ip)
    # RBAC：只回使用者可見子網路內 IP 的異動（限定範圍時 subnet_id 必須落在 vis）
    vis = await visible_ids(session, user=user, object_type="subnet")
    if vis is not None:
        stmt = stmt.where(IPChangeLog.subnet_id.in_(vis)) if vis else stmt.where(sa_false())
    stmt = stmt.order_by(IPChangeLog.created_at.desc()).limit(limit)
    rows = list((await session.execute(stmt)).scalars().all())
    return {"changes": [
        {"ip": r.ip_text, "event": r.event_type, "field": r.field,
         "old": r.old_value, "new": r.new_value, "source": r.source,
         "at": r.created_at.isoformat()} for r in rows], "count": len(rows)}


async def dns_lookup(session: AsyncSession, *, user: User, name: str) -> dict[str, Any]:
    """用名稱（hostname / FQDN 子字串）查 DNS 紀錄。"""
    rows = list((await session.execute(
        select(DNSRecord.name, DNSRecord.type, DNSRecord.value)
        .where(DNSRecord.name.ilike(f"%{name}%")).limit(50)
    )).all())
    return {"records": [{"name": n, "type": ty, "value": v} for n, ty, v in rows],
            "count": len(rows)}


async def global_search(session: AsyncSession, *, user: User, q: str) -> dict[str, Any]:
    """全域搜尋（IP / CIDR / MAC / VLAN / 文字），跨子網路/IP/裝置等。"""
    from app.services.search import search as _search
    return await _search(session, user=user, q=q, limit_per_type=8)


async def oui_lookup(session: AsyncSession, *, user: User, mac: str) -> dict[str, Any]:
    """用 MAC 查 OUI 廠商。"""
    vendor = await vendor_for_mac(session, mac)
    return {"mac": mac, "vendor": vendor}


async def oui_search(
    session: AsyncSession, *, user: User,
    prefix: str | None = None, name: str | None = None, limit: int = 50,
) -> dict[str, Any]:
    """依 OUI 首碼或廠商名搜尋 OUI 紀錄（多筆）。"""
    from app.services.oui import search_oui_vendors
    try:
        return await search_oui_vendors(session, prefix=prefix, name=name, limit=limit)
    except ValueError as exc:
        raise IPAMToolError(str(exc)) from exc


async def switch_port_for_ip(
    session: AsyncSession, *, user: User, ip: str,
) -> dict[str, Any]:
    """查某 IP 接在哪台 switch 的哪個 port（用 FDB；access port = 該 port MAC 數最少者）。"""
    ipa = (await session.execute(
        select(IPAddress).where(IPAddress.ip == ip)
    )).scalar_one_or_none()
    if ipa is None:
        raise IPAMToolError("IP not found")
    vis = await visible_ids(session, user=user, object_type="subnet")
    if vis is not None and (ipa.subnet_id is None or ipa.subnet_id not in vis):
        raise IPAMToolError("IP not found")   # RBAC：不可見子網路的 IP
    if ipa.mac is None:
        return {"ip": ip, "mac": None, "locations": [], "note": "no MAC known for this IP"}
    mac = str(ipa.mac).lower()
    rows = list((await session.execute(
        select(FDBEntry.port_name, FDBEntry.vlan_id_num, FDBEntry.last_seen_at,
               LibreNMSDevice.hostname, LibreNMSDevice.primary_ip)
        .outerjoin(LibreNMSDevice, LibreNMSDevice.id == FDBEntry.device_id)
        .where(FDBEntry.mac == mac)
    )).all())
    locs = []
    for port, vlan, seen, sw_host, sw_ip in rows:
        # 該 (switch, port) 上有幾個不同 MAC → 越少越像 access port
        mac_count = int(await session.scalar(
            select(func.count(func.distinct(FDBEntry.mac)))
            .select_from(FDBEntry)
            .join(LibreNMSDevice, LibreNMSDevice.id == FDBEntry.device_id, isouter=True)
            .where(FDBEntry.port_name == port,
                   LibreNMSDevice.hostname == sw_host)
        ) or 0)
        locs.append({
            "switch": sw_host, "switch_ip": str(sw_ip) if sw_ip else None,
            "port": port, "vlan": vlan,
            "macs_on_port": mac_count,
            "last_seen_at": seen.isoformat() if seen else None,
        })
    locs.sort(key=lambda x: (x["macs_on_port"] or 9999))
    return {"ip": ip, "mac": mac, "locations": locs,
            "likely_access_port": locs[0] if locs else None}


# ─────────────────── 寫入工具（admin only）───────────────────


async def allocate_ip(
    session: AsyncSession, *, user: User,
    subnet_id: str | None = None, subnet_cidr: str | None = None,
    hostname: str | None = None, description: str | None = None,
    requested_ip: str | None = None, owner: str | None = None,
    customer: str | None = None, mac: str | None = None,
) -> dict[str, Any]:
    """配發 IP（ADMIN）。可指定 requested_ip（哪個 IP）或留空取第一個空位；
    可一併設定 hostname / owner / customer（用名稱比對）/ mac / description。

    若給了 requested_ip 但沒給 subnet_id / subnet_cidr，會自動找出「包含此 IP 的子網路」
    （取最精確的一個）來配發，使用者不必先知道子網路。"""
    if not user.is_admin:
        raise IPAMToolError("allocate_ip requires admin")
    # 只給 requested_ip 時，自動推導包含它的子網路（最長前綴優先）
    if not subnet_id and not subnet_cidr and requested_ip:
        row = (await session.execute(
            text(
                "SELECT id::text AS id FROM subnets "
                "WHERE cidr >>= CAST(:ip AS inet) "
                "ORDER BY masklen(cidr) DESC LIMIT 1"
            ),
            {"ip": requested_ip.split("/")[0]},
        )).first()
        if row is None:
            raise IPAMToolError(
                f"no subnet contains {requested_ip} — 請先建立包含此 IP 的子網路"
            )
        subnet_id = row.id
    subnet = await _resolve_subnet(
        session, user=user, subnet_id=subnet_id, subnet_cidr=subnet_cidr,
    )

    # customer 用名稱比對（找不到就回報，不硬塞）
    customer_id = None
    customer_note = None
    if customer:
        cust = (await session.execute(
            select(Customer).where(Customer.name.ilike(customer)).limit(1)
        )).scalar_one_or_none()
        if cust is None:
            customer_note = f"customer '{customer}' not found — left unset"
        else:
            customer_id = cust.id

    try:
        if requested_ip:
            obj = await create_ip(
                session, subnet=subnet, ip=requested_ip,
                hostname=hostname, description=description, mac=mac,
            )
        else:
            obj = await allocate_first_free(
                session, subnet=subnet,
                hostname=hostname, description=description,
                mac=mac, state="active",
            )
    except IPAlreadyExists as exc:
        raise IPAMToolError(f"already allocated: {exc}") from exc
    except IPNotInSubnet as exc:
        raise IPAMToolError(f"not in subnet: {exc}") from exc
    except SubnetFull as exc:
        raise IPAMToolError(f"subnet full: {exc}") from exc

    if owner:
        obj.owner = owner
    if customer_id is not None:
        obj.customer_id = customer_id

    # features A/B：記 manual hostname 觀測 + created 異動
    from app.services.hostname import seed_observation
    from app.services.ip_history import log_change
    await seed_observation(session, ip=obj, source="manual", hostname=hostname)
    await log_change(session, ip=obj, event_type="created",
                     source="manual", actor_user_id=str(user.id),
                     note="AI chat 配發")
    await session.commit()
    return {
        "ip_address_id": str(obj.id),
        "ip": str(obj.ip).split("/")[0],
        "subnet_id": str(subnet.id),
        "hostname": obj.hostname,
        "owner": obj.owner,
        "customer_id": str(obj.customer_id) if obj.customer_id else None,
        "note": customer_note or "allocated",
    }


# ─────────────────── 新增工具：完整覆蓋系統功能 ───────────────────

async def get_ip_detail(session: AsyncSession, *, user: User, ip: str) -> dict[str, Any]:
    """單一 IP 的完整資料：狀態 / 主機名稱 / MAC / 擁有者 / 裝置 / 交換器埠 / 客戶 / 最後上線來源。"""
    try:
        ipaddress.ip_address(ip)
    except ValueError as exc:
        raise IPAMToolError(f"Invalid IP: {exc}") from exc
    obj = (await session.execute(select(IPAddress).where(IPAddress.ip == ip))).scalars().first()
    if obj is None:
        return {"found": False, "ip": ip}
    # RBAC：IP 所屬子網路不可見 → 當作查無，不洩漏
    vis = await visible_ids(session, user=user, object_type="subnet")
    if vis is not None and (obj.subnet_id is None or obj.subnet_id not in vis):
        return {"found": False, "ip": ip}
    sub = await session.get(Subnet, obj.subnet_id) if obj.subnet_id else None
    dev = await session.get(Device, obj.device_id) if obj.device_id else None
    cust = await session.get(Customer, obj.customer_id) if obj.customer_id else None
    from app.services.os_precedence import effective_os
    _os = await effective_os(session, obj)
    return {
        "found": True,
        "ip": obj.ip,
        "subnet": str(sub.cidr) if sub else None,
        "subnet_id": str(obj.subnet_id) if obj.subnet_id else None,
        "state": obj.state,
        "effective_status": obj.effective_status,
        "hostname": obj.hostname,
        "hostname_source_pin": obj.hostname_source_pin,
        "mac": obj.mac,
        "mac_source": obj.mac_source,
        "owner": obj.owner,
        "description": obj.description,
        "note": obj.note,
        "device": dev.name if dev else None,
        "device_id": str(obj.device_id) if obj.device_id else None,
        "switch_port": obj.switch_port,
        "customer": cust.name if cust else None,
        "discovery_source": obj.discovery_source,
        "last_seen_scanner": obj.last_seen_scanner,
        "last_seen_librenms": obj.last_seen_librenms,
        "last_seen_dns": obj.last_seen_dns,
        # OS 偵測（依來源優先序 scanner/librenms/wazuh 解析）+ 探測項目
        **_os,
        "effective_probes": await _effective_probes(session, sub, obj),
        "excluded_probes": list(obj.excluded_probes or []),
    }


async def get_subnet_detail(
    session: AsyncSession, *, user: User,
    subnet_id: str | None = None, subnet_cidr: str | None = None,
) -> dict[str, Any]:
    """子網路完整資料：CIDR / 閘道 / DNS / VLAN / VRF / 區段 / 客戶 / 使用率。"""
    sub = await _resolve_subnet(session, user=user, subnet_id=subnet_id, subnet_cidr=subnet_cidr)
    usage = await get_usage(session, sub)
    sec = await session.get(Section, sub.section_id) if sub.section_id else None
    cust = await session.get(Customer, sub.customer_id) if sub.customer_id else None
    vlan = await session.get(VLAN, sub.vlan_id) if sub.vlan_id else None
    parent = await session.get(Subnet, sub.master_subnet_id) if sub.master_subnet_id else None
    vrf_name = None
    if sub.vrf_id:
        from app.models.vrf import VRF
        vrf = await session.get(VRF, sub.vrf_id)
        vrf_name = vrf.name if vrf else None
    agent_name = None
    if sub.scan_agent_id:
        from app.models.scan_agent import ScanAgent
        ag = await session.get(ScanAgent, sub.scan_agent_id)
        agent_name = ag.name if ag else None
    return {
        "id": str(sub.id),
        "cidr": str(sub.cidr),
        "description": sub.description,
        "gateway": sub.gateway,
        "dns_servers": sub.dns_servers,
        "section": sec.name if sec else None,
        "customer": cust.name if cust else None,
        "vlan": {"number": vlan.number, "name": vlan.name} if vlan else None,
        "vrf": vrf_name,
        "parent_subnet": str(parent.cidr) if parent else None,
        "is_pool": sub.is_pool,
        "is_full": sub.is_full,
        "archived": sub.archived_at is not None,
        # 掃描設定：是否開啟、會跑哪些探測、指派的掃描代理
        "scan_enabled": sub.scan_enabled,
        "scan_method": list(sub.scan_method or []),
        "scan_agent": agent_name,
        "usage": usage,
    }


async def list_subnet_ips(
    session: AsyncSession, *, user: User,
    subnet_id: str | None = None, subnet_cidr: str | None = None,
    state: str | None = None, limit: int = 256, offset: int = 0,
) -> dict[str, Any]:
    """列出某子網路內所有「已紀錄／已用」的 IP（選用 state 過濾）。

    回每筆 IP 的 hostname / state / mac / owner / 是否掛裝置。提供 subnet_id 或 subnet_cidr。
    結果多時用 offset 分批：回傳含 has_more / next_offset，需要下一批就帶 next_offset 再呼叫。
    """
    sub = await _resolve_subnet(session, user=user, subnet_id=subnet_id, subnet_cidr=subnet_cidr)
    lim = min(int(limit), 1000)
    off = max(int(offset), 0)
    base = select(IPAddress).where(IPAddress.subnet_id == sub.id)
    if state:
        base = base.where(IPAddress.state == state)
    # 多取一筆判斷是否還有下一批
    stmt = base.order_by(IPAddress.ip).offset(off).limit(lim + 1)
    rows = list((await session.execute(stmt)).scalars().all())
    has_more = len(rows) > lim
    rows = rows[:lim]
    return {
        "subnet": str(sub.cidr),
        "count": len(rows),
        "offset": off,
        "has_more": has_more,
        "next_offset": (off + lim) if has_more else None,
        "ips": [{
            "ip": r.ip, "hostname": r.hostname, "state": r.state,
            "effective_status": r.effective_status,
            "mac": r.mac, "owner": r.owner,
            "os_family": r.os_family,
            "device_id": str(r.device_id) if r.device_id else None,
        } for r in rows],
    }


async def list_firewalls(session: AsyncSession, *, user: User, limit: int = 200) -> dict[str, Any]:
    """OPNsense 防火牆清單（不含密鑰）。"""
    from app.models.firewall import OPNsenseFirewall
    rows = (await session.execute(select(OPNsenseFirewall).limit(limit))).scalars().all()
    return {"firewalls": [{
        "id": str(f.id), "name": f.name, "api_url": f.api_url, "enabled": f.enabled,
        "last_sync_at": f.last_sync_at, "last_error": f.last_error, "description": f.description,
    } for f in rows]}


async def list_firewall_rules(
    session: AsyncSession, *, user: User,
    firewall_id: str | None = None, firewall_name: str | None = None, limit: int = 200,
) -> dict[str, Any]:
    """防火牆過濾規則（從 OPNsense 同步回來的）。"""
    from app.models.firewall import OPNsenseFirewall
    from app.models.firewall_rule import OPNsenseRule
    fw_id = None
    if firewall_id:
        try:
            fw_id = uuid.UUID(firewall_id)
        except (ValueError, TypeError):
            # LLM 常把防火牆「名稱」塞進 firewall_id 欄位 → 退而當名稱查
            firewall_name = firewall_name or firewall_id
    if fw_id is None and firewall_name:
        fw = (await session.execute(
            select(OPNsenseFirewall).where(OPNsenseFirewall.name == firewall_name)
        )).scalars().first()
        if fw is None:
            raise IPAMToolError(f"firewall not found: {firewall_name}")
        fw_id = fw.id
    stmt = select(OPNsenseRule)
    if fw_id is not None:
        stmt = stmt.where(OPNsenseRule.firewall_id == fw_id)
    stmt = stmt.order_by(OPNsenseRule.sequence).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return {"rules": [{
        "enabled": r.enabled, "sequence": r.sequence, "action": r.action,
        "interface": r.interface, "direction": r.direction, "protocol": r.protocol,
        "source": r.source_net, "source_port": r.source_port,
        "destination": r.destination_net, "destination_port": r.destination_port,
        "description": r.description,
    } for r in rows]}


async def list_firewall_aliases(session: AsyncSession, *, user: User, limit: int = 200) -> dict[str, Any]:
    """IPAM↔OPNsense alias 對應。"""
    from app.models.firewall import OPNsenseAliasMapping
    rows = (await session.execute(select(OPNsenseAliasMapping).limit(limit))).scalars().all()
    return {"aliases": [{
        "id": str(a.id), "firewall_id": str(a.firewall_id), "alias_name": a.alias_name,
        "alias_type": a.alias_type, "direction": a.direction,
        "last_synced_count": a.last_synced_count, "last_sync_at": a.last_sync_at,
    } for a in rows]}


async def get_topology(
    session: AsyncSession, *, user: User,
    subnet_cidr: str | None = None, include_l3: bool = True, include_vpn: bool = True,
) -> dict[str, Any]:
    """網路拓樸（裝置 / 子網路 / VPN / 纜線）。回傳節點與邊的精簡列表。"""
    from app.services.topology import build_topology
    subnet_ids = None
    if subnet_cidr:
        sub = await _resolve_subnet(session, user=user, subnet_id=None, subnet_cidr=subnet_cidr)
        subnet_ids = [sub.id]
    graph = await build_topology(
        session, user=user, subnet_ids=subnet_ids, include_l3=include_l3, include_vpn=include_vpn,
    )
    labels = {n["data"]["id"]: n["data"].get("label") for n in graph["nodes"]}
    edges = [{
        "from": labels.get(e["data"]["source"], e["data"]["source"]),
        "to": labels.get(e["data"]["target"], e["data"]["target"]),
        "kind": e["data"].get("kind"), "label": e["data"].get("label"),
        "via": e["data"].get("via"),
    } for e in graph["edges"]]
    return {
        "node_count": len(graph["nodes"]),
        "edge_count": len(graph["edges"]),
        "edges": edges[:300],
    }


async def list_dns_servers(session: AsyncSession, *, user: User, limit: int = 200) -> dict[str, Any]:
    """DNS 伺服器/供應商清單（PowerDNS / BIND9 / UCS / OPNsense Unbound…）。"""
    from app.models.dns import DNSServer
    rows = (await session.execute(select(DNSServer).limit(limit))).scalars().all()
    return {"servers": [{
        "id": str(s.id), "name": s.name, "type": s.type,
        "api_url": s.api_url, "server_address": s.server_address,
        "enabled": s.enabled, "last_sync_at": s.last_sync_at, "last_error": s.last_error,
    } for s in rows]}


async def list_dns_zones(session: AsyncSession, *, user: User, limit: int = 200) -> dict[str, Any]:
    """DNS 區域清單。"""
    from app.models.dns import DNSZone
    rows = (await session.execute(select(DNSZone).limit(limit))).scalars().all()
    return {"zones": [{
        "id": str(z.id), "name": z.name, "type": z.type, "managed": z.managed,
        "last_sync_at": z.last_sync_at,
    } for z in rows]}


async def list_dns_records(
    session: AsyncSession, *, user: User,
    name: str | None = None, rtype: str | None = None,
    missing_ip: bool = False, limit: int = 200,
) -> dict[str, Any]:
    """List DNS records pulled from integrated servers (A/AAAA/PTR — IP↔name mapping).
    Filter by name/value substring (name) and record type (rtype). missing_ip=true →
    only A/AAAA records whose target IP has no matching address in IPAM. Each row marks
    has_ipam_ip (resolved by actual IP value) and the source DNS server."""
    from sqlalchemy import or_ as _or

    from app.models.address import IPAddress as _IPA
    from app.models.dns import DNSRecord, DNSServer, DNSZone
    stmt = select(DNSRecord)
    if name:
        pat = f"%{name.strip()}%"
        stmt = stmt.where(_or(DNSRecord.name.ilike(pat), DNSRecord.value.ilike(pat)))
    if rtype:
        stmt = stmt.where(DNSRecord.type == rtype.strip().upper())
    if missing_ip:
        ip_here = (
            select(_IPA.id).where(func.host(_IPA.ip) == DNSRecord.value)
            .correlate(DNSRecord).exists()
        )
        stmt = stmt.where(DNSRecord.type.in_(("A", "AAAA")), ~ip_here)
    stmt = stmt.order_by(DNSRecord.name, DNSRecord.type).limit(min(limit, 500))
    rows = list((await session.execute(stmt)).scalars().all())
    ip_vals = {r.value for r in rows if r.type in ("A", "AAAA") and r.value}
    have: set[str] = set()
    if ip_vals:
        for (host,) in (await session.execute(
            select(func.host(_IPA.ip)).where(func.host(_IPA.ip).in_(ip_vals))
        )).all():
            have.add(str(host))
    zone_ids = {r.zone_id for r in rows if r.zone_id}
    zsrv: dict[Any, str] = {}
    if zone_ids:
        for zid, sname in (await session.execute(
            select(DNSZone.id, DNSServer.name)
            .join(DNSServer, DNSServer.id == DNSZone.server_id)
            .where(DNSZone.id.in_(zone_ids))
        )).all():
            zsrv[zid] = sname
    return {"records": [{
        "name": r.name, "type": r.type, "value": r.value,
        "consistency_state": r.consistency_state,
        "has_ipam_ip": (r.value in have) if r.type in ("A", "AAAA") else None,
        "server": zsrv.get(r.zone_id),
    } for r in rows]}


async def list_ip_requests(
    session: AsyncSession, *, user: User, status: str | None = None, limit: int = 200,
) -> dict[str, Any]:
    """IP 申請工作流清單。非管理員只看自己提出的。"""
    from app.models.ip_request import IPRequest
    stmt = select(IPRequest)
    if not user.is_admin:
        stmt = stmt.where(IPRequest.requester_user_id == user.id)
    if status:
        stmt = stmt.where(IPRequest.status == status)
    stmt = stmt.order_by(IPRequest.created_at.desc()).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return {"requests": [{
        "id": str(r.id), "status": r.status, "subnet_id": str(r.subnet_id),
        "requested_ip": r.requested_ip, "hostname": r.hostname, "purpose": r.purpose,
        "description": r.description, "created_at": r.created_at,
    } for r in rows]}


async def list_scan_agents(session: AsyncSession, *, user: User, limit: int = 200) -> dict[str, Any]:
    """掃描代理清單與狀態。"""
    from app.models.scan_agent import ScanAgent
    rows = (await session.execute(select(ScanAgent).limit(limit))).scalars().all()
    return {"agents": [{
        "id": str(a.id), "name": a.name, "enabled": a.enabled,
        "last_seen_at": a.last_seen_at, "agent_version": a.agent_version, "last_error": a.last_error,
        "last_source_ip": a.last_source_ip,
        # 此代理被允許執行的探測 / 實際裝得起的探測（os 需 nmap…）
        "enabled_probes": list(a.enabled_probes or []),
        "available_probes": list(a.available_probes) if a.available_probes is not None else None,
    } for a in rows]}


async def list_certificates(
    session: AsyncSession, *, user: User,
    expiring_within_days: int | None = None, limit: int = 200,
) -> dict[str, Any]:
    """集中保管的 TLS 憑證（僅中繼資料，永不回私鑰/PEM）：名稱、網域、目前版本指紋、
    到期日、剩餘天數、版本數、是否自簽、自動抓取來源。expiring_within_days 可只列即將到期的。"""
    from datetime import UTC, datetime

    from sqlalchemy import func

    from app.models.certificate import Certificate, CertVersion
    now = datetime.now(UTC)
    rows = (await session.execute(
        select(Certificate).order_by(Certificate.name).limit(limit))).scalars().all()
    out: list[dict[str, Any]] = []
    for c in rows:
        cur = (await session.execute(select(CertVersion).where(
            CertVersion.certificate_id == c.id, CertVersion.is_current.is_(True)).limit(1)
        )).scalar_one_or_none()
        days = (cur.not_after - now).days if cur else None
        if expiring_within_days is not None and (days is None or days > expiring_within_days):
            continue
        count = int((await session.execute(select(func.count()).select_from(CertVersion).where(
            CertVersion.certificate_id == c.id))).scalar_one())
        out.append({
            "id": str(c.id), "name": c.name,
            "domains": list((cur.domains if cur else c.domains) or []),
            "current_fingerprint": (cur.fingerprint_sha256[:16] + "…") if cur else None,
            "not_after": cur.not_after if cur else None,
            "days_remaining": days,
            "version_count": count,
            "self_signed": bool(cur and cur.subject and cur.issuer and cur.subject == cur.issuer),
            "source_type": c.source_type,
            "last_fetch_error": c.last_fetch_error,
            "has_current_version": cur is not None,
        })
    return {"certificates": out}


async def list_cert_distribution(
    session: AsyncSession, *, user: User, limit: int = 200,
) -> dict[str, Any]:
    """憑證派送代理與各站台部署現況（無私鑰）：每個代理部署了哪張憑證/服務、是否為最新
    （up_to_date）或飄移、到期日/剩餘天數、代理版本，以及同一把 Key 是否近期被多台主機共用。"""
    from datetime import UTC, datetime, timedelta

    from app.models.certificate import CertAgent, Certificate, CertVersion
    now = datetime.now(UTC)
    cur_rows = (await session.execute(
        select(Certificate, CertVersion)
        .join(CertVersion, CertVersion.certificate_id == Certificate.id)
        .where(CertVersion.is_current.is_(True)))).all()
    cur = {cert.name: ver for cert, ver in cur_rows}
    cutoff = now - timedelta(days=7)
    agents = (await session.execute(
        select(CertAgent).order_by(CertAgent.name).limit(limit))).scalars().all()
    out: list[dict[str, Any]] = []
    for a in agents:
        recent_ips: list[str] = []
        for e in (a.recent_sources or []):
            try:
                if datetime.fromisoformat(e["at"]) >= cutoff and e["ip"] not in recent_ips:
                    recent_ips.append(e["ip"])
            except (KeyError, TypeError, ValueError):
                continue
        deps: list[dict[str, Any]] = []
        for d in (a.reported or []):
            if not isinstance(d, dict):
                continue
            ver = cur.get(d.get("cert"))
            na = ver.not_after if ver else None
            deps.append({
                "cert": d.get("cert"), "profile": d.get("profile"), "status": d.get("status"),
                "up_to_date": bool(ver and d.get("fingerprint") == ver.fingerprint_sha256),
                "not_after": na, "days_remaining": (na - now).days if na else None,
            })
        out.append({
            "agent": a.name, "enabled": a.enabled, "last_seen_at": a.last_seen_at,
            "last_source_ip": a.last_source_ip, "agent_version": a.agent_version,
            "recent_source_ips": recent_ips, "multi_source_recent": len(recent_ips) > 1,
            "deployments": deps,
        })
    return {"agents": out}


async def list_arp(
    session: AsyncSession, *, user: User,
    ip: str | None = None, mac: str | None = None, limit: int = 100,
) -> dict[str, Any]:
    """ARP 紀錄（IP↔MAC，哪台裝置在哪個介面看到的）。"""
    stmt = select(ARPEntry)
    if ip:
        stmt = stmt.where(ARPEntry.ip == ip)
    if mac:
        stmt = stmt.where(ARPEntry.mac == mac.strip().lower())
    stmt = stmt.order_by(ARPEntry.last_seen_at.desc()).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return {"arp": [{
        "ip": str(e.ip), "mac": str(e.mac), "device_id": str(e.device_id) if e.device_id else None,
        "interface": e.interface, "vrf": e.vrf, "source": e.source, "last_seen_at": e.last_seen_at,
    } for e in rows]}


async def list_fdb(
    session: AsyncSession, *, user: User, mac: str | None = None, limit: int = 50,
) -> dict[str, Any]:
    """交換器 FDB 紀錄（MAC↔埠）。"""
    stmt = select(FDBEntry)
    if mac:
        stmt = stmt.where(FDBEntry.mac == mac.strip().lower())
    stmt = stmt.order_by(FDBEntry.last_seen_at.desc()).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return {"fdb": [{
        "mac": str(e.mac), "vlan": e.vlan_id_num, "port": e.port_name,
        "device_id": str(e.device_id) if e.device_id else None, "source": e.source,
        "last_seen_at": e.last_seen_at,
    } for e in rows]}


async def wazuh_missing_agents(session: AsyncSession, *, user: User, limit: int = 200) -> dict[str, Any]:
    """有設主機名稱、卻沒裝 Wazuh agent 的 IP（資安覆蓋缺口）。"""
    from app.services.wazuh import find_missing_agents
    rows = await find_missing_agents(session)
    return {"missing_count": len(rows), "missing": rows[:limit]}


async def get_customer_summary(
    session: AsyncSession, *, user: User,
    customer_id: str | None = None, name: str | None = None,
) -> dict[str, Any]:
    """單一客戶/單位的掛載統計：sections / subnets / devices / IPs。"""
    cust: Customer | None = None
    if customer_id:
        cust = await session.get(Customer, _as_uuid(customer_id, "customer_id"))
    elif name:
        cust = (await session.execute(
            select(Customer).where(Customer.name == name)
        )).scalars().first()
    if cust is None:
        raise IPAMToolError("customer not found")
    # RBAC：客戶不在可見範圍 → 當作查無，不洩漏
    vis = await visible_ids(session, user=user, object_type="customer")
    if vis is not None and cust.id not in vis:
        raise IPAMToolError("customer not found")
    from sqlalchemy import func as _f
    n_sec = await session.scalar(select(_f.count()).select_from(Section).where(Section.customer_id == cust.id))
    n_sub = await session.scalar(select(_f.count()).select_from(Subnet).where(Subnet.customer_id == cust.id))
    n_dev = await session.scalar(select(_f.count()).select_from(Device).where(Device.customer_id == cust.id))
    n_ip = await session.scalar(select(_f.count()).select_from(IPAddress).where(IPAddress.customer_id == cust.id))
    return {
        "id": str(cust.id), "name": cust.name, "title": cust.title, "contact": cust.contact,
        "sections": int(n_sec or 0), "subnets": int(n_sub or 0),
        "devices": int(n_dev or 0), "ips": int(n_ip or 0),
    }


async def list_vms(session: AsyncSession, *, user: User, limit: int = 200) -> dict[str, Any]:
    """虛擬機清單（Proxmox VE 等同步回來）。"""
    from app.models.virt import VirtualMachine, VMInterface
    rows = list((await session.execute(select(VirtualMachine).limit(limit))).scalars().all())
    out = []
    for v in rows:
        ifaces = list((await session.execute(
            select(VMInterface).where(VMInterface.vm_id == v.id)
        )).scalars().all())
        prim = await session.get(IPAddress, v.primary_ip_id) if v.primary_ip_id else None
        dev = await session.get(Device, v.device_id) if v.device_id else None
        out.append({
            "id": str(v.id), "name": v.name, "node": v.node, "status": v.status,
            "vcpus": v.vcpus, "memory_mb": v.memory_mb, "disk_gb": v.disk_gb, "kind": v.kind,
            "is_template": v.is_template,
            "primary_ip": str(prim.ip) if prim else None,
            "device": dev.name if dev else None,
            "interfaces": [{
                "name": i.name, "mac": i.mac, "primary_ip": i.primary_ip, "bridge": i.bridge,
            } for i in ifaces],
        })
    return {"vms": out, "count": len(out)}


async def list_wireless_links(session: AsyncSession, *, user: User, limit: int = 200) -> dict[str, Any]:
    """無線連線（point-to-point / SSID）。"""
    from app.models.advanced import WirelessLink
    rows = (await session.execute(select(WirelessLink).limit(limit))).scalars().all()
    out = []
    for w in rows:
        a = await session.get(Device, w.a_device_id) if w.a_device_id else None
        b = await session.get(Device, w.b_device_id) if w.b_device_id else None
        out.append({
            "id": str(w.id), "name": w.name, "ssid": w.ssid,
            "a_device": a.name if a else None, "b_device": b.name if b else None,
            "distance_m": w.distance_m,
        })
    return {"wireless_links": out}


async def list_ssids(session: AsyncSession, *, user: User, limit: int = 200) -> dict[str, Any]:
    """無線 SSID 清單（auth_type / VLAN）。"""
    from app.models.advanced import WirelessSSID
    rows = (await session.execute(select(WirelessSSID).limit(min(int(limit), 500)))).scalars().all()
    out = []
    for s in rows:
        vlan = await session.get(VLAN, s.vlan_id) if s.vlan_id else None
        out.append({"id": str(s.id), "ssid": s.ssid, "auth_type": s.auth_type,
                    "vlan": vlan.number if vlan else None, "description": s.description})
    return {"ssids": out, "count": len(out)}


async def list_circuits(session: AsyncSession, *, user: User, limit: int = 200) -> dict[str, Any]:
    """電路 / 線路清單（供應商、頻寬、固定 IP、關聯裝置）。"""
    from app.models.advanced import Circuit, CircuitType, Provider
    rows = list((await session.execute(
        select(Circuit).limit(min(int(limit), 500))
    )).scalars().all())
    out = []
    for c in rows:
        prov = await session.get(Provider, c.provider_id) if c.provider_id else None
        ctype = await session.get(CircuitType, c.type_id) if c.type_id else None
        dev = await session.get(Device, c.device_id) if c.device_id else None
        out.append({
            "id": str(c.id), "cid": c.cid, "provider": prov.name if prov else None,
            "type": ctype.name if ctype else None, "status": c.status,
            "up_kbps": c.up_kbps, "down_kbps": c.down_kbps, "commit_rate_kbps": c.commit_rate_kbps,
            "ip_address": c.ip_address, "gateway": c.gateway, "netmask": c.netmask,
            "device": dev.name if dev else None,
            "monthly_fee_cents": c.monthly_fee_cents, "description": c.description,
        })
    return {"circuits": out, "count": len(out)}


async def list_providers(session: AsyncSession, *, user: User, limit: int = 200) -> dict[str, Any]:
    """電信 / 線路供應商清單。"""
    from app.models.advanced import Provider
    rows = (await session.execute(select(Provider).limit(min(int(limit), 500)))).scalars().all()
    return {"providers": [{
        "id": str(p.id), "name": p.name, "asn": p.asn, "account_number": p.account_number,
        "portal_url": p.portal_url, "noc_contact": p.noc_contact, "description": p.description,
    } for p in rows]}


async def list_asns(session: AsyncSession, *, user: User, limit: int = 200) -> dict[str, Any]:
    """自治系統號碼（ASN）清單。"""
    from app.models.advanced import ASN
    rows = (await session.execute(select(ASN).limit(min(int(limit), 500)))).scalars().all()
    return {"asns": [{
        "id": str(a.id), "asn": a.asn, "rir": a.rir, "description": a.description,
    } for a in rows]}


async def list_tenants(session: AsyncSession, *, user: User, limit: int = 200) -> dict[str, Any]:
    """租戶（Tenant）清單。"""
    from app.models.advanced import Tenant
    rows = (await session.execute(select(Tenant).limit(min(int(limit), 500)))).scalars().all()
    return {"tenants": [{
        "id": str(t.id), "name": t.name, "slug": t.slug, "description": t.description,
    } for t in rows]}


async def list_contacts(session: AsyncSession, *, user: User, limit: int = 200) -> dict[str, Any]:
    """聯絡人清單（名稱 / 職稱 / 電話 / Email）。"""
    from app.models.advanced import Contact
    rows = (await session.execute(select(Contact).limit(min(int(limit), 500)))).scalars().all()
    return {"contacts": [{
        "id": str(c.id), "name": c.name, "title": c.title, "phone": c.phone,
        "email": c.email, "address": c.address,
    } for c in rows]}


async def list_cables(session: AsyncSession, *, user: User, limit: int = 200) -> dict[str, Any]:
    """佈線 / 纜線清單（線標、類型、長度、兩端 termination）。"""
    from app.models.physical import Cable, CableTermination
    rows = list((await session.execute(select(Cable).limit(min(int(limit), 500)))).scalars().all())
    out = []
    for c in rows:
        terms = list((await session.execute(
            select(CableTermination).where(CableTermination.cable_id == c.id)
        )).scalars().all())
        out.append({
            "id": str(c.id), "label": c.label, "type": c.type, "color": c.color,
            "length_m": c.length_m, "status": c.status,
            "terminations": [{
                "side": t.side, "object_type": t.object_type,
                "object_id": str(t.object_id), "port_label": t.port_label,
            } for t in sorted(terms, key=lambda x: x.side)],
        })
    return {"cables": out, "count": len(out)}


async def cable_trace(session: AsyncSession, *, user: User, cable_id: str) -> dict[str, Any]:
    """追蹤一條纜線的 A/B 端 termination（接到哪個裝置 / 埠）。"""
    from app.models.physical import Cable, CableTermination
    c = await session.get(Cable, _as_uuid(cable_id, "cable_id"))
    if c is None:
        raise IPAMToolError("cable not found")
    terms = list((await session.execute(
        select(CableTermination).where(CableTermination.cable_id == c.id)
    )).scalars().all())
    return {
        "cable": {"id": str(c.id), "label": c.label, "type": c.type, "status": c.status},
        "terminations": [{
            "side": t.side, "object_type": t.object_type,
            "object_id": str(t.object_id), "port_label": t.port_label,
        } for t in sorted(terms, key=lambda x: x.side)],
    }


async def list_power(session: AsyncSession, *, user: User, limit: int = 200) -> dict[str, Any]:
    """電力清單：饋線（電壓/電流/相位）與插座（接到哪台裝置）。"""
    from app.models.physical import PowerFeed, PowerOutlet
    feeds = list((await session.execute(select(PowerFeed).limit(min(int(limit), 500)))).scalars().all())
    outlets = list((await session.execute(select(PowerOutlet).limit(min(int(limit), 500)))).scalars().all())
    feed_out = [{
        "id": str(f.id), "name": f.name, "voltage_v": f.voltage_v, "amperage_a": f.amperage_a,
        "phase": f.phase, "supply_type": f.supply_type,
        "rack_id": str(f.rack_id) if f.rack_id else None,
    } for f in feeds]
    outlet_out = []
    for o in outlets:
        dev = await session.get(Device, o.device_id) if o.device_id else None
        outlet_out.append({
            "id": str(o.id), "label": o.label, "feed_id": str(o.feed_id) if o.feed_id else None,
            "device": dev.name if dev else None,
        })
    return {"feeds": feed_out, "outlets": outlet_out}


async def list_wazuh_agents(session: AsyncSession, *, user: User, limit: int = 200) -> dict[str, Any]:
    """Wazuh 代理清單（狀態、OS、版本、CVE 數）。"""
    from app.models.wazuh import WazuhAgent
    rows = (await session.execute(
        select(WazuhAgent).order_by(WazuhAgent.name).limit(min(int(limit), 500))
    )).scalars().all()
    return {"agents": [{
        "id": str(a.id), "agent_id": a.agent_id, "name": a.name, "ip": a.ip,
        "status": a.status, "os_platform": a.os_platform, "os_version": a.os_version,
        "agent_version": a.agent_version, "group": a.group,
        "cve_critical": a.cve_critical_count, "cve_high": a.cve_high_count,
        "last_keep_alive": a.last_keep_alive,
    } for a in rows]}


# ── 寫入類（一律 ADMIN ONLY，與 allocate_ip 同模式） ──

async def update_ip(
    session: AsyncSession, *, user: User, ip: str,
    hostname: str | None = None, state: str | None = None, owner: str | None = None,
    description: str | None = None, mac: str | None = None,
) -> dict[str, Any]:
    """ADMIN ONLY。更新某 IP 的 hostname / state / owner / description / mac。"""
    if not user.is_admin:
        raise IPAMToolError("update_ip requires admin")
    obj = (await session.execute(select(IPAddress).where(IPAddress.ip == ip))).scalars().first()
    if obj is None:
        raise IPAMToolError(f"IP not found: {ip}")
    changed = []
    if hostname is not None:
        obj.hostname = hostname.strip() or None; changed.append("hostname")
    if state is not None:
        obj.state = state.strip(); changed.append("state")
    if owner is not None:
        obj.owner = owner.strip() or None; changed.append("owner")
    if description is not None:
        obj.description = description.strip() or None; changed.append("description")
    if mac is not None:
        obj.mac = (mac.strip().lower() or None); obj.mac_source = "manual"; changed.append("mac")
    await session.flush()
    return {"ip": obj.ip, "updated": changed}


async def create_subnet(
    session: AsyncSession, *, user: User, cidr: str,
    section_id: str | None = None, section_name: str | None = None,
    description: str | None = None, gateway: str | None = None,
) -> dict[str, Any]:
    """ADMIN ONLY。在某區段建立子網路。"""
    if not user.is_admin:
        raise IPAMToolError("create_subnet requires admin")
    try:
        ipaddress.ip_network(cidr, strict=False)
    except ValueError as exc:
        raise IPAMToolError(f"Invalid CIDR: {exc}") from exc
    sec: Section | None = None
    if section_id:
        sec = await session.get(Section, _as_uuid(section_id, "section_id"))
    elif section_name:
        sec = (await session.execute(select(Section).where(Section.name == section_name))).scalars().first()
    if sec is None:
        raise IPAMToolError("section not found (provide section_id or section_name)")
    sub = Subnet(cidr=cidr, section_id=sec.id, description=description, gateway=gateway)
    session.add(sub)
    await session.flush()
    return {"id": str(sub.id), "cidr": str(sub.cidr), "section": sec.name}


async def create_device(
    session: AsyncSession, *, user: User, name: str,
    type: str = "other", fqdn: str | None = None,
    vendor: str | None = None, model: str | None = None,
) -> dict[str, Any]:
    """ADMIN ONLY。建立裝置。"""
    if not user.is_admin:
        raise IPAMToolError("create_device requires admin")
    dev = Device(name=name.strip(), type=type, fqdn=fqdn, vendor=vendor, model=model)
    session.add(dev)
    await session.flush()
    return {"id": str(dev.id), "name": dev.name, "type": dev.type}


async def approve_ip_request(session: AsyncSession, *, user: User, request_id: str) -> dict[str, Any]:
    """ADMIN ONLY。核准 IP 申請並原子配發。"""
    if not user.is_admin:
        raise IPAMToolError("approve_ip_request requires admin")
    from app.models.ip_request import IPRequest
    from app.services.ip_request import approve_request
    req = await session.get(IPRequest, _as_uuid(request_id, "request_id"))
    if req is None:
        raise IPAMToolError("request not found")
    sub = await session.get(Subnet, req.subnet_id)
    if sub is None:
        raise IPAMToolError("subnet not found")
    try:
        res = await approve_request(session, request=req, subnet=sub, approver=user)
    except Exception as exc:
        raise IPAMToolError(f"approve failed: {exc}") from exc
    return {"id": str(res.id), "status": res.status,
            "allocated_ip_id": str(res.allocated_ip_id) if res.allocated_ip_id else None}


async def reject_ip_request(
    session: AsyncSession, *, user: User, request_id: str, reason: str,
) -> dict[str, Any]:
    """ADMIN ONLY。駁回 IP 申請。"""
    if not user.is_admin:
        raise IPAMToolError("reject_ip_request requires admin")
    from app.models.ip_request import IPRequest
    from app.services.ip_request import reject_request
    req = await session.get(IPRequest, _as_uuid(request_id, "request_id"))
    if req is None:
        raise IPAMToolError("request not found")
    try:
        res = await reject_request(session, request=req, approver=user, reason=reason)
    except Exception as exc:
        raise IPAMToolError(f"reject failed: {exc}") from exc
    return {"id": str(res.id), "status": res.status}


# ─────────────────── 網路工具（純運算，對應「網路工具」頁）───────────────────
# 邏輯共用 app/services/nettools.py；這裡只是 MCP fn 簽章包裝（session/user 不用）。
# 失敗時把 NetToolError 翻成 IPAMToolError，讓 LLM 收到人讀錯誤而非 500。

async def calc_ip_info(session: AsyncSession, *, user: User, ip: str) -> dict[str, Any]:
    from app.services import nettools
    try:
        return nettools.ip_info(ip)
    except nettools.NetToolError as exc:
        raise IPAMToolError(str(exc)) from exc


async def calc_cidr_info(session: AsyncSession, *, user: User, cidr: str) -> dict[str, Any]:
    from app.services import nettools
    try:
        return nettools.cidr_info(cidr)
    except nettools.NetToolError as exc:
        raise IPAMToolError(str(exc)) from exc


async def calc_cidr_split(
    session: AsyncSession, *, user: User, cidr: str, new_prefix: int,
) -> dict[str, Any]:
    from app.services import nettools
    try:
        return nettools.cidr_split(cidr, int(new_prefix))
    except nettools.NetToolError as exc:
        raise IPAMToolError(str(exc)) from exc


async def calc_eui64(
    session: AsyncSession, *, user: User, mac: str, prefix: str,
) -> dict[str, Any]:
    from app.services import nettools
    try:
        return nettools.eui64(mac, prefix)
    except nettools.NetToolError as exc:
        raise IPAMToolError(str(exc)) from exc


async def calc_ip_in_cidr(
    session: AsyncSession, *, user: User, ip: str, cidr: str,
) -> dict[str, Any]:
    from app.services import nettools
    try:
        return nettools.ip_in_cidr(ip, cidr)
    except nettools.NetToolError as exc:
        raise IPAMToolError(str(exc)) from exc


async def calc_cidr_relation(
    session: AsyncSession, *, user: User, a: str, b: str,
) -> dict[str, Any]:
    from app.services import nettools
    try:
        return nettools.cidr_relation(a, b)
    except nettools.NetToolError as exc:
        raise IPAMToolError(str(exc)) from exc


async def calc_range_to_cidr(
    session: AsyncSession, *, user: User, start: str, end: str,
) -> dict[str, Any]:
    from app.services import nettools
    try:
        return nettools.range_to_cidr(start, end)
    except nettools.NetToolError as exc:
        raise IPAMToolError(str(exc)) from exc


async def calc_cidr_to_range(
    session: AsyncSession, *, user: User, cidr: str,
) -> dict[str, Any]:
    from app.services import nettools
    try:
        return nettools.cidr_to_range(cidr)
    except nettools.NetToolError as exc:
        raise IPAMToolError(str(exc)) from exc


async def calc_aggregate(
    session: AsyncSession, *, user: User, cidrs: str,
) -> dict[str, Any]:
    from app.services import nettools
    try:
        return nettools.aggregate(cidrs)
    except nettools.NetToolError as exc:
        raise IPAMToolError(str(exc)) from exc


async def calc_netmask(
    session: AsyncSession, *, user: User, value: str,
) -> dict[str, Any]:
    from app.services import nettools
    try:
        return nettools.netmask(value)
    except nettools.NetToolError as exc:
        raise IPAMToolError(str(exc)) from exc


async def calc_mac_format(
    session: AsyncSession, *, user: User, mac: str,
) -> dict[str, Any]:
    from app.services import nettools
    try:
        return nettools.mac_format(mac)
    except nettools.NetToolError as exc:
        raise IPAMToolError(str(exc)) from exc


async def calc_fqdn(
    session: AsyncSession, *, user: User, name: str,
) -> dict[str, Any]:
    from app.services import nettools
    return nettools.fqdn_parse(name)


async def dns_resolve(
    session: AsyncSession, *, user: User, name: str, type: str = "ANY",
) -> dict[str, Any]:
    from app.services import nettools
    try:
        return await nettools.dns_lookup_live(name, type)
    except nettools.NetToolError as exc:
        raise IPAMToolError(str(exc)) from exc


async def dns_mail_check(
    session: AsyncSession, *, user: User, domain: str, dkim_selector: str = "",
) -> dict[str, Any]:
    from app.services import nettools
    try:
        return await nettools.dns_mail(domain, dkim_selector)
    except nettools.NetToolError as exc:
        raise IPAMToolError(str(exc)) from exc


async def geoip_locate(
    session: AsyncSession, *, user: User, ip: str,
) -> dict[str, Any]:
    from app.services import nettools
    from app.services.geoip import geoip_lookup
    try:
        addr = nettools.parse_addr(ip)
    except nettools.NetToolError as exc:
        raise IPAMToolError(str(exc)) from exc
    return await geoip_lookup(session, str(addr))


async def power_calc(
    session: AsyncSession, *, user: User,
    volts: float = 220, amps: float = 16, phase: str = "1", pf: float = 0.95,
    heat_watts: float | None = None, batt_wh: float | None = None,
    load_w: float | None = None, pdu_a: float | None = None,
) -> dict[str, Any]:
    from app.services import nettools
    try:
        return nettools.power_calc(
            volts=volts, amps=amps, phase=str(phase), pf=pf, heat_watts=heat_watts,
            batt_wh=batt_wh, load_w=load_w, pdu_a=pdu_a,
        )
    except nettools.NetToolError as exc:
        raise IPAMToolError(str(exc)) from exc


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
        "description": (
            "Get the FIRST free IP in a subnet (by id or CIDR). For multiple or "
            "consecutive free IPs, use find_free_ips instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "subnet_id": {"type": "string"},
                "subnet_cidr": {"type": "string", "description": "e.g. 10.0.0.0/24"},
            },
        },
    },
    "find_free_ips": {
        "fn": find_free_ips,
        "description": (
            "Find multiple free IPs in a subnet (by id or CIDR). Set count for how "
            "many; set consecutive=true to require a contiguous run. Returns only "
            "genuinely unallocated IPs — never invent or extend the list yourself."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "subnet_id": {"type": "string"},
                "subnet_cidr": {"type": "string", "description": "e.g. 10.0.0.0/24"},
                "count": {"type": "integer", "minimum": 1, "maximum": 256},
                "consecutive": {"type": "boolean"},
            },
            "required": ["count"],
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
    "stats_overview": {
        "fn": stats_overview,
        "description": (
            "Total counts of each entity (sections, subnets, IPs, devices, racks, "
            "locations, customers, VLANs, NAT rules). Use for 'how many X' questions."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    "list_racks": {
        "fn": list_racks,
        "description": (
            "List racks (機櫃) with location, device count, total/used/free U, and each "
            "mounted device's U position & size (u_position/u_size/rack_face). Use this to "
            "answer how many more devices/U fit in a rack — free_u is the free U count."
        ),
        "parameters": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500}},
        },
    },
    "list_locations": {
        "fn": list_locations,
        "description": "List locations (地點) with rack counts.",
        "parameters": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500}},
        },
    },
    "list_devices": {
        "fn": list_devices,
        "description": (
            "List or search devices (裝置). Optional name substring or type filter "
            "(server/switch/router/firewall/ap/storage/ipmi/other). Includes each device's "
            "rack U position/size (u_position, u_size, rack_face) and rack_id."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "type": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
        },
    },
    "get_device": {
        "fn": get_device,
        "description": (
            "Device details by id or name: IPs, VLANs (via LibreNMS), and its rack U "
            "position/size (u_position, u_size, rack_face) + the rack it's mounted in."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string"},
                "name": {"type": "string"},
            },
        },
    },
    "list_customers": {
        "fn": list_customers,
        "description": "List customers / management units (客戶 / 管理單位).",
        "parameters": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500}},
        },
    },
    "list_nat": {
        "fn": list_nat,
        "description": "List NAT rules (NAT 規則).",
        "parameters": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500}},
        },
    },
    "switch_port_for_ip": {
        "fn": switch_port_for_ip,
        "description": (
            "Find which switch and port an IP is connected to, using FDB data. "
            "Returns sightings ordered by likelihood of being the access port "
            "(fewest MACs on that port first)."
        ),
        "parameters": {
            "type": "object",
            "properties": {"ip": {"type": "string"}},
            "required": ["ip"],
        },
    },
    "list_sections": {
        "fn": list_sections,
        "description": "List sections (區段) with subnet counts.",
        "parameters": {"type": "object", "properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_vrfs": {
        "fn": list_vrfs,
        "description": "List VRFs.",
        "parameters": {"type": "object", "properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "recent_ip_changes": {
        "fn": recent_ip_changes,
        "description": "Recent IP change-log entries (hostname/mac/online-offline/edits); optional ip filter.",
        "parameters": {"type": "object", "properties": {
            "ip": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100}}},
    },
    "list_vpn_tunnels": {
        "fn": list_vpn_tunnels,
        "description": (
            "List VPN tunnels (WireGuard / IPsec / OpenVPN) pulled from firewalls such as "
            "OPNsense. Use this to answer whether a site-to-site VPN exists between two "
            "subnets/firewalls: site_to_site=true with both a_device and b_device means a "
            "confirmed tunnel between two managed devices; b_endpoint is the remote gateway "
            "when the far side is not a managed device."
        ),
        "parameters": {"type": "object", "properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "dns_lookup": {
        "fn": dns_lookup,
        "description": "Look up DNS records by hostname / FQDN substring.",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}},
                       "required": ["name"]},
    },
    "global_search": {
        "fn": global_search,
        "description": "Global search across IP / CIDR / MAC / VLAN / text (subnets, IPs, devices).",
        "parameters": {"type": "object", "properties": {"q": {"type": "string"}},
                       "required": ["q"]},
    },
    "oui_lookup": {
        "fn": oui_lookup,
        "description": "Look up the hardware vendor for a single, complete MAC address (OUI).",
        "parameters": {"type": "object", "properties": {"mac": {"type": "string"}},
                       "required": ["mac"]},
    },
    "oui_search": {
        "fn": oui_search,
        "description": (
            "Search the OUI registry for MULTIPLE vendors by partial MAC prefix and/or "
            "vendor name. Use this for 'which vendors have a MAC starting with 22' "
            "(prefix='22') or 'find Cisco OUIs' (name='Cisco'). Returns up to `limit` "
            "matches. (oui_lookup is for one full MAC; this is for prefix/name search.)"
        ),
        "parameters": {"type": "object", "properties": {
            "prefix": {"type": "string", "description": "partial OUI hex, e.g. '22', '00:11', '0011aa'"},
            "name": {"type": "string", "description": "vendor name substring"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "allocate_ip": {
        "fn": allocate_ip,
        "description": (
            "ADMIN ONLY. Allocate an IP in a subnet. Provide subnet_id or "
            "subnet_cidr; if requested_ip is given that exact IP is used, otherwise "
            "the first free IP. Optionally set hostname, owner, customer (matched by "
            "name), mac and description."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "subnet_id": {"type": "string"},
                "subnet_cidr": {"type": "string"},
                "requested_ip": {"type": "string", "description": "exact IP to allocate"},
                "hostname": {"type": "string"},
                "owner": {"type": "string"},
                "customer": {"type": "string", "description": "customer/unit name"},
                "mac": {"type": "string"},
                "description": {"type": "string"},
            },
        },
    },
    "get_ip_detail": {
        "fn": get_ip_detail,
        "description": "Full record for one IP: state, hostname, MAC, owner, device, switch port, customer, last-seen sources.",
        "parameters": {"type": "object", "properties": {"ip": {"type": "string"}}, "required": ["ip"]},
    },
    "get_subnet_detail": {
        "fn": get_subnet_detail,
        "description": "Full subnet info: gateway, DNS, VLAN, section, customer, usage. Provide subnet_id or subnet_cidr.",
        "parameters": {"type": "object", "properties": {
            "subnet_id": {"type": "string"}, "subnet_cidr": {"type": "string"}}},
    },
    "list_subnet_ips": {
        "fn": list_subnet_ips,
        "description": "List the registered/used IPs inside a subnet (ip, hostname, state, mac, owner, device). Provide subnet_id or subnet_cidr; optional state filter. Returns has_more/next_offset — to fetch the next batch, call again with offset=next_offset.",
        "parameters": {"type": "object", "properties": {
            "subnet_id": {"type": "string"}, "subnet_cidr": {"type": "string"},
            "state": {"type": "string", "description": "optional filter e.g. active"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
            "offset": {"type": "integer", "minimum": 0, "description": "skip N rows; use next_offset from a previous call for the next batch"}}},
    },
    "list_firewalls": {
        "fn": list_firewalls,
        "description": "List OPNsense firewalls (no secrets).",
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 200}}},
    },
    "list_firewall_rules": {
        "fn": list_firewall_rules,
        "description": "List synced OPNsense filter rules. Filter by firewall_id or firewall_name.",
        "parameters": {"type": "object", "properties": {
            "firewall_id": {"type": "string"}, "firewall_name": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_firewall_aliases": {
        "fn": list_firewall_aliases,
        "description": "List IPAM↔OPNsense alias mappings.",
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 200}}},
    },
    "get_topology": {
        "fn": get_topology,
        "description": "Network topology (devices/subnets/VPN/cables) as node count + edge list. Optionally scope to subnet_cidr.",
        "parameters": {"type": "object", "properties": {
            "subnet_cidr": {"type": "string"},
            "include_l3": {"type": "boolean"}, "include_vpn": {"type": "boolean"}}},
    },
    "list_dns_servers": {
        "fn": list_dns_servers,
        "description": "List DNS servers/providers (PowerDNS/BIND9/Univention UCS/OPNsense Unbound…).",
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 200}}},
    },
    "list_dns_zones": {
        "fn": list_dns_zones,
        "description": "List DNS zones.",
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_dns_records": {
        "fn": list_dns_records,
        "description": "List DNS records from integrated servers (A/AAAA/PTR). Filter by name/value "
                       "substring (name) and type (rtype). missing_ip=true → only A/AAAA whose target IP "
                       "has no matching IPAM address. Each row marks has_ipam_ip and the source server.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"}, "rtype": {"type": "string"},
            "missing_ip": {"type": "boolean"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_ip_requests": {
        "fn": list_ip_requests,
        "description": "List IP allocation requests. Non-admins see only their own. Optional status filter (pending/approved/rejected…).",
        "parameters": {"type": "object", "properties": {
            "status": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_scan_agents": {
        "fn": list_scan_agents,
        "description": "List scan agents and their status.",
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 200}}},
    },
    "list_certificates": {
        "fn": list_certificates,
        "description": "List managed TLS certificates (metadata only, never private keys): name, domains, "
                       "current fingerprint, expiry date, days remaining, version count, self-signed flag and "
                       "auto-fetch source. Pass expiring_within_days to list only certs expiring within N days.",
        "parameters": {"type": "object", "properties": {
            "expiring_within_days": {"type": "integer", "minimum": 0, "maximum": 3650},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200}}},
    },
    "list_cert_distribution": {
        "fn": list_cert_distribution,
        "description": "Certificate distribution agents and their per-site deployment status: which cert/profile "
                       "each agent deployed, whether it is up to date or drifted, expiry/days-remaining, agent "
                       "version, and whether one enrollment key is shared by multiple hosts (multi_source_recent).",
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 200}}},
    },
    "list_arp": {
        "fn": list_arp,
        "description": "ARP entries (IP↔MAC seen by which device/interface). Filter by ip or mac.",
        "parameters": {"type": "object", "properties": {
            "ip": {"type": "string"}, "mac": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_fdb": {
        "fn": list_fdb,
        "description": "Switch FDB entries (MAC↔port/VLAN). Filter by mac.",
        "parameters": {"type": "object", "properties": {
            "mac": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "wazuh_missing_agents": {
        "fn": wazuh_missing_agents,
        "description": "IPs that have a hostname but no active Wazuh agent (security coverage gap).",
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "get_customer_summary": {
        "fn": get_customer_summary,
        "description": "Counts of sections/subnets/devices/IPs for a customer. Provide customer_id or name.",
        "parameters": {"type": "object", "properties": {
            "customer_id": {"type": "string"}, "name": {"type": "string"}}},
    },
    "list_vms": {
        "fn": list_vms,
        "description": "List virtual machines (synced from Proxmox VE etc.).",
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_wireless_links": {
        "fn": list_wireless_links,
        "description": "List wireless point-to-point links / SSIDs.",
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 200}}},
    },
    "update_ip": {
        "fn": update_ip,
        "description": "ADMIN ONLY. Update an IP's hostname / state / owner / description / mac.",
        "parameters": {"type": "object", "properties": {
            "ip": {"type": "string"}, "hostname": {"type": "string"}, "state": {"type": "string"},
            "owner": {"type": "string"}, "description": {"type": "string"}, "mac": {"type": "string"}},
            "required": ["ip"]},
    },
    "create_subnet": {
        "fn": create_subnet,
        "description": "ADMIN ONLY. Create a subnet in a section. Provide cidr and section_id or section_name.",
        "parameters": {"type": "object", "properties": {
            "cidr": {"type": "string"}, "section_id": {"type": "string"}, "section_name": {"type": "string"},
            "description": {"type": "string"}, "gateway": {"type": "string"}}, "required": ["cidr"]},
    },
    "create_device": {
        "fn": create_device,
        "description": "ADMIN ONLY. Create a device (name, type, fqdn, vendor, model).",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"}, "type": {"type": "string"}, "fqdn": {"type": "string"},
            "vendor": {"type": "string"}, "model": {"type": "string"}}, "required": ["name"]},
    },
    "approve_ip_request": {
        "fn": approve_ip_request,
        "description": "ADMIN ONLY. Approve an IP request and atomically allocate the IP.",
        "parameters": {"type": "object", "properties": {"request_id": {"type": "string"}}, "required": ["request_id"]},
    },
    "reject_ip_request": {
        "fn": reject_ip_request,
        "description": "ADMIN ONLY. Reject an IP request with a reason.",
        "parameters": {"type": "object", "properties": {
            "request_id": {"type": "string"}, "reason": {"type": "string"}}, "required": ["request_id", "reason"]},
    },
    # ─── 網路工具（純運算，不碰資料庫）───
    "calc_ip_info": {
        "fn": calc_ip_info,
        "description": (
            "Analyse a single IP address: version, private/global/reserved/multicast/"
            "loopback/link-local flags, decimal/hex/binary, reverse DNS pointer. Pure "
            "calculation, no lookup against IPAM records."
        ),
        "parameters": {"type": "object", "properties": {
            "ip": {"type": "string", "description": "IPv4 or IPv6"}}, "required": ["ip"]},
    },
    "calc_cidr_info": {
        "fn": calc_cidr_info,
        "description": (
            "Analyse a CIDR/network: network & broadcast address, netmask, hostmask, "
            "prefix length, total addresses, usable host count, first/last host."
        ),
        "parameters": {"type": "object", "properties": {
            "cidr": {"type": "string", "description": "e.g. 192.168.0.0/24"}}, "required": ["cidr"]},
    },
    "calc_cidr_split": {
        "fn": calc_cidr_split,
        "description": "Split a CIDR into equal-sized smaller subnets of new_prefix length.",
        "parameters": {"type": "object", "properties": {
            "cidr": {"type": "string"},
            "new_prefix": {"type": "integer", "minimum": 0, "maximum": 128}},
            "required": ["cidr", "new_prefix"]},
    },
    "calc_eui64": {
        "fn": calc_eui64,
        "description": "Generate the EUI-64 IPv6 address from a MAC and an IPv6 prefix (RFC 4291).",
        "parameters": {"type": "object", "properties": {
            "mac": {"type": "string"}, "prefix": {"type": "string", "description": "e.g. 2001:db8::/64"}},
            "required": ["mac", "prefix"]},
    },
    "calc_ip_in_cidr": {
        "fn": calc_ip_in_cidr,
        "description": "Check whether an IP falls inside a CIDR; also flags network/broadcast address.",
        "parameters": {"type": "object", "properties": {
            "ip": {"type": "string"}, "cidr": {"type": "string"}}, "required": ["ip", "cidr"]},
    },
    "calc_cidr_relation": {
        "fn": calc_cidr_relation,
        "description": (
            "Relationship between two CIDRs: equal / a_contains_b / a_within_b / overlap / disjoint."
        ),
        "parameters": {"type": "object", "properties": {
            "a": {"type": "string"}, "b": {"type": "string"}}, "required": ["a", "b"]},
    },
    "calc_range_to_cidr": {
        "fn": calc_range_to_cidr,
        "description": "Summarise an IP range (start..end) into the minimal set of CIDR blocks.",
        "parameters": {"type": "object", "properties": {
            "start": {"type": "string"}, "end": {"type": "string"}}, "required": ["start", "end"]},
    },
    "calc_cidr_to_range": {
        "fn": calc_cidr_to_range,
        "description": "Convert a CIDR to its first/last address and total address count.",
        "parameters": {"type": "object", "properties": {"cidr": {"type": "string"}}, "required": ["cidr"]},
    },
    "calc_aggregate": {
        "fn": calc_aggregate,
        "description": "Collapse/aggregate multiple CIDRs (comma- or space-separated) into the minimal set.",
        "parameters": {"type": "object", "properties": {
            "cidrs": {"type": "string", "description": "e.g. '192.168.0.0/24, 192.168.1.0/24'"}},
            "required": ["cidrs"]},
    },
    "calc_netmask": {
        "fn": calc_netmask,
        "description": "Convert between prefix length (24 or /24) and dotted netmask (255.255.255.0); returns wildcard/hostmask.",
        "parameters": {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]},
    },
    "calc_mac_format": {
        "fn": calc_mac_format,
        "description": "Normalise a MAC into colon/dash/cisco-dot/bare forms; returns OUI, locally-administered & multicast bits.",
        "parameters": {"type": "object", "properties": {"mac": {"type": "string"}}, "required": ["mac"]},
    },
    "calc_fqdn": {
        "fn": calc_fqdn,
        "description": "Parse/validate an FQDN (RFC 1123): labels, host, domain, TLD, validity.",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    },
    "dns_resolve": {
        "fn": dns_resolve,
        "description": (
            "Live DNS resolution via the system resolver: A / AAAA / PTR. Use this to "
            "resolve a hostname to IPs or do reverse lookup. (Differs from dns_lookup, "
            "which searches IPAM's own DNS records.)"
        ),
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"}, "type": {"type": "string", "enum": ["A", "AAAA", "PTR", "ANY"]}},
            "required": ["name"]},
    },
    "dns_mail_check": {
        "fn": dns_mail_check,
        "description": "Mail-related DNS diagnostics for a domain: MX, SPF, DMARC, and DKIM (if a selector is given).",
        "parameters": {"type": "object", "properties": {
            "domain": {"type": "string"}, "dkim_selector": {"type": "string"}}, "required": ["domain"]},
    },
    "geoip_locate": {
        "fn": geoip_locate,
        "description": "Geolocate an IP (MaxMind GeoLite2 web service). Requires GeoIP credentials configured in system settings.",
        "parameters": {"type": "object", "properties": {"ip": {"type": "string"}}, "required": ["ip"]},
    },
    "power_calc": {
        "fn": power_calc,
        "description": (
            "Datacenter power/cooling calculations: load watts (V×A×PF, ×√3 for 3-phase), "
            "BTU/hr heat, UPS runtime minutes (batt_wh / load_w), and PDU 80% safe amps. "
            "Provide whichever inputs are relevant."
        ),
        "parameters": {"type": "object", "properties": {
            "volts": {"type": "number"}, "amps": {"type": "number"},
            "phase": {"type": "string", "enum": ["1", "3"]}, "pf": {"type": "number"},
            "heat_watts": {"type": "number"}, "batt_wh": {"type": "number"},
            "load_w": {"type": "number"}, "pdu_a": {"type": "number"}}},
    },
    # ─── 進階資源 / 實體層 / 整合（全域基礎設施）───
    "list_circuits": {
        "fn": list_circuits,
        "description": "List circuits / WAN links (供應商、頻寬 up/down kbps、固定 IP、關聯裝置、月費).",
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_providers": {
        "fn": list_providers,
        "description": "List circuit/transit providers (供應商：ASN、帳號、入口網址、NOC).",
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_asns": {
        "fn": list_asns,
        "description": "List autonomous system numbers (ASN) with RIR.",
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_tenants": {
        "fn": list_tenants,
        "description": "List tenants (租戶).",
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_contacts": {
        "fn": list_contacts,
        "description": "List contacts (聯絡人：職稱 / 電話 / Email).",
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_ssids": {
        "fn": list_ssids,
        "description": "List wireless SSIDs (auth type, VLAN).",
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_cables": {
        "fn": list_cables,
        "description": "List cables (佈線：線標、類型、長度、兩端 termination/裝置/埠).",
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "cable_trace": {
        "fn": cable_trace,
        "description": "Trace one cable's A/B terminations (which device/port each end connects to). Provide cable_id.",
        "parameters": {"type": "object", "properties": {"cable_id": {"type": "string"}}, "required": ["cable_id"]},
    },
    "list_power": {
        "fn": list_power,
        "description": "List power feeds (voltage/amperage/phase) and outlets (which device each outlet powers).",
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_wazuh_agents": {
        "fn": list_wazuh_agents,
        "description": "List Wazuh agents (status, OS, version, CVE critical/high counts). For the coverage GAP use wazuh_missing_agents instead.",
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
}


# ─────────────────── AI 對話：異動類工具需使用者確認 ───────────────────
# 這些工具會新增 / 修改 / 刪除資料；AI 對話中不直接執行，先回前端請使用者按「確認」。
MUTATING_TOOLS: frozenset[str] = frozenset({
    "allocate_ip", "update_ip", "create_subnet", "create_device",
    "approve_ip_request", "reject_ip_request",
})


# 純計算 / 外部查詢類工具（不碰 IPAM 資料）→ 不受 RBAC 可見範圍限制
UTILITY_TOOLS: frozenset[str] = frozenset({
    "calc_ip_info", "calc_cidr_info", "calc_cidr_split", "calc_eui64",
    "calc_ip_in_cidr", "calc_cidr_relation", "calc_range_to_cidr",
    "calc_cidr_to_range", "calc_aggregate", "calc_netmask", "calc_mac_format",
    "calc_fqdn", "dns_resolve", "dns_mail_check", "geoip_locate", "power_calc",
    "oui_lookup", "oui_search",
})


# 全域基礎設施工具（無法逐物件授權）→ 僅 admin 或具萬用讀取權限者可呼叫。
# 對應 CLAUDE.md 的 require_global_read 分類；只被指派特定物件的部門帳號一律擋。
GLOBAL_READ_TOOLS: frozenset[str] = frozenset({
    "list_vlans", "list_vrfs", "list_nat", "list_firewalls", "list_firewall_rules",
    "list_firewall_aliases", "list_dns_servers", "list_dns_zones", "list_dns_records",
    "check_dns_consistency", "dns_lookup",
    "list_vms", "list_wireless_links", "list_vpn_tunnels", "list_scan_agents",
    "list_arp", "list_fdb", "list_circuits", "list_providers", "list_asns",
    "list_tenants", "list_contacts", "list_ssids", "list_cables", "cable_trace",
    "list_power", "list_wazuh_agents", "wazuh_missing_agents", "get_topology",
    "list_certificates", "list_cert_distribution",
})


async def has_no_visibility(session: AsyncSession, user: User) -> bool:
    """非管理員且對所有物件類型都無可見範圍 → AI 對話不該回任何 IPAM 資料。"""
    if getattr(user, "is_admin", False):
        return False
    for ot in ("subnet", "device", "customer", "section", "rack", "location"):
        v = await visible_ids(session, user=user, object_type=ot)
        if v is None or v:   # None=全部可見、或非空集合 → 有可見範圍
            return False
    return True


async def has_global_read(session: AsyncSession, user: User) -> bool:
    """admin 或任一物件類型有「萬用」授權（visible_ids 回 None）→ 可讀全域基礎設施。"""
    if getattr(user, "is_admin", False):
        return True
    for ot in ("subnet", "device", "customer", "section", "rack", "location"):
        if await visible_ids(session, user=user, object_type=ot) is None:
            return True
    return False


async def authorize_tool(session: AsyncSession, user: User, name: str) -> str | None:
    """單一 RBAC 閘（HTTP MCP 與 NL chat 共用）。回 None=放行；回字串=拒絕原因。

    - 純計算工具：永遠放行
    - 異動工具：需 admin
    - 全域基礎設施工具：需 admin 或萬用讀取
    - 其餘（逐物件資料）：需至少有可見範圍；工具內部再依 visible_ids 過濾
    """
    if name in UTILITY_TOOLS:
        return None
    if name in MUTATING_TOOLS and not getattr(user, "is_admin", False):
        return "permission_denied: 此操作需要管理員權限。"
    if await has_no_visibility(session, user):
        return "permission_denied: 你目前沒有可檢視的資源權限，請聯絡管理員指派。"
    if name in GLOBAL_READ_TOOLS and not await has_global_read(session, user):
        return "permission_denied: 此為全域基礎設施資料，僅限管理員或具全域讀取權限者檢視。"
    return None


async def allowed_tool_names(session: AsyncSession, user: User) -> set[str]:
    """此使用者實際可呼叫的工具名稱集合（給 LLM 工具清單與 MCP tools/list 過濾用）。"""
    allowed: set[str] = set()
    for name in TOOLS:
        if await authorize_tool(session, user, name) is None:
            allowed.add(name)
    return allowed


def summarize_action(name: str, args: dict[str, Any]) -> str:
    """給前端確認卡用的人類可讀摘要（繁中）。"""
    a = args or {}
    if name == "allocate_ip":
        base = f"配發 IP {a.get('requested_ip') or '（自動取第一個空位）'}"
        if a.get("hostname"):
            base += f"，主機名稱「{a['hostname']}」"
        if a.get("owner"):
            base += f"，擁有者「{a['owner']}」"
        return base
    if name == "update_ip":
        return f"修改 IP {a.get('ip') or a.get('ip_address_id') or ''} 的資料"
    if name == "create_subnet":
        return f"建立子網路 {a.get('cidr') or ''}"
    if name == "create_device":
        return f"建立裝置「{a.get('name') or ''}」"
    if name == "approve_ip_request":
        return "核准一筆 IP 申請"
    if name == "reject_ip_request":
        return "駁回一筆 IP 申請"
    return f"執行 {name}"
