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

from sqlalchemy import String, and_, func, literal, or_, select, text
from sqlalchemy import false as sa_false
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sqlin import in_values
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
        raise IPAMToolError("subnet not found")   # 跟不存在同一句，不透露它在不在
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
        raise IPAMToolError("subnet not found")   # 跟不存在同一句，不透露它在不在
    return subnet


async def _scope_subnet(
    session: AsyncSession, *, user: User,
    subnet_cidr: str | None = None, subnet_id: str | None = None,
) -> tuple[list[uuid.UUID] | None, str]:
    """清單工具的共用範圍解析：回 (subnet_ids | None 代表全域, 範圍標籤)。

    沒有範圍參數時，問「某網段有哪些…」的答案會變成全站資料 —— 這類錯誤每個數字
    單獨看都是真的，最難察覺。解析同時走 `_resolve_subnet` 的可見性把關。
    """
    if not (subnet_cidr or subnet_id):
        return None, "all"
    subnet = await _resolve_subnet(
        session, user=user, subnet_id=subnet_id, subnet_cidr=subnet_cidr)
    return [subnet.id], str(subnet.cidr)


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
        raise IPAMToolError("subnet not found")   # 跟不存在同一句，不透露它在不在
    total, used, free, pct = await get_usage(session, s)
    return {
        "subnet_id": subnet_id, "cidr": str(s.cidr),
        "total": total, "used": used, "free": free, "used_pct": pct,
    }


async def _librenms_name(session: AsyncSession, ln_id: Any) -> str | None:
    if ln_id is None:
        return None
    ln = await session.get(LibreNMSDevice, ln_id)
    return (ln.sysname or ln.hostname) if ln else None


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
    # ⚠️ FDB／ARP 的 device_id 是 **LibreNMS 的裝置**，要經 jt_ipam_device_id 才對得到使用者看得到的
    # jt-ipam 裝置。以前直接拿來比，非管理員永遠看不到交換器埠（2026-09-30 研究）。沒對映到 jt-ipam
    # 裝置的交換器無從判斷權限 → 非管理員一律遮蔽。
    switch_name = switch_dev = None
    if fdb is not None and fdb.device_id is not None:
        ln = await session.get(LibreNMSDevice, fdb.device_id)
        if ln is not None:
            switch_name = ln.sysname or ln.hostname
            switch_dev = ln.jt_ipam_device_id
    elif fdb is not None and fdb.switch_device_id is not None:
        # MikroTik 回報的列（0170）直接記 jt-ipam 裝置
        switch_dev = fdb.switch_device_id
        switch_name = await session.scalar(select(Device.name).where(Device.id == switch_dev))
    if fdb is not None and vis_dev is not None and (switch_dev is None or switch_dev not in vis_dev):
        fdb = None
    return {
        "mac": mac,
        "arp": (
            {
                "ip": arp.ip,
                # 哪一台回報的（LibreNMS 裝置名稱；防火牆／掃描代理的 ARP 看 source）
                "seen_by": await _librenms_name(session, arp.device_id),
                "source": arp.source,
                "interface": arp.interface,
                "last_seen_at": arp.last_seen_at.isoformat(),
            }
            if arp else None
        ),
        "fdb": (
            {
                "switch": switch_name,
                "switch_device_id": str(switch_dev) if switch_dev else None,
                "port_name": fdb.port_name,
                "vlan_id_num": fdb.vlan_id_num,
                "last_seen_at": fdb.last_seen_at.isoformat(),
            }
            if fdb else None
        ),
    }


async def mac_history(session: AsyncSession, *, user: User, mac: str) -> dict[str, Any]:
    """以 MAC 為中心的完整歷程：用過哪些 IP（起訖時間）、何時被誰取代、出現在哪台交換器的哪個埠、
    DHCP 固定分配、是哪台裝置或虛擬機的網卡，以及隨機 MAC 輪替時可能是同一台的其他 MAC。
    依使用者的可見範圍縮放。"""
    from app.services.mac_history import mac_history as _history
    try:
        out = await _history(session, user=user, mac=mac, ips_limit=100, events_limit=60)
    except ValueError as exc:
        raise IPAMToolError("not a valid MAC address") from exc
    # 上線依據是給畫面畫燈的原始時間，AI 用不到，省 token
    for r in out["ips"]:
        r.pop("live", None)
    return out


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
            select(func.count()).select_from(model).where(in_values(model.id, vis))) or 0)

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
            .where(in_values(IPAddress.subnet_id, vis_sub))) or 0)

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
    session: AsyncSession, *, user: User, limit: int = 200, location_id: str | None = None,
) -> dict[str, Any]:
    """列出機櫃／層架（含所在地點、已掛裝置、每一列還剩多少空間）。問某機房要帶 location_id。

    **不是每一種機架都以 U 計**：層架（一般／鍍鉻／木質，如 IKEA IVAR）以「層」為單位，
    層高可以逐層不同，而且最上面那片板的**上面**還能再放一排。

    而且同一列可以左右並排、也可以上下疊放好幾台。只看「這一列有沒有東西」會把半滿的
    那一層算成滿的，「還能放幾台」就答錯了 —— 所以這裡額外回 `rows_with_space`（哪幾列
    還有空、還剩多少比例）與每台裝置的橫向／層內位置。
    """
    limit = min(int(limit), 500)
    stmt = (select(Rack, Location.name)
            .outerjoin(Location, Location.id == Rack.location_id))
    scope = "all"
    if location_id:
        stmt = stmt.where(Rack.location_id == _as_uuid(location_id, "location_id"))
        scope = f"location:{location_id}"
    # 可見性推進 SQL（同 list_devices：先截斷再過濾會漏資料且總數失真）
    vis = await visible_ids(session, user=user, object_type="rack")
    if vis is not None:
        stmt = stmt.where(in_values(Rack.id, vis) if vis else sa_false())
    total = int(await session.scalar(
        select(func.count()).select_from(stmt.subquery())) or 0)
    rows = (await session.execute(stmt.order_by(Rack.name).limit(limit))).all()
    from app.services.rack import (
        RACK_SLOTS,
        has_open_top,
        level_heights_mm,
        placeable_levels,
        uses_rack_units,
    )
    out = []
    for rack, loc_name in rows:
        devs = list((await session.execute(
            select(Device.name, Device.type, Device.u_position, Device.u_size,
                   Device.rack_face, Device.rack_slot, Device.rack_slot_span,
                   Device.rack_vslot, Device.rack_vslot_span)
            .where(Device.rack_id == rack.id).order_by(Device.u_position)
        )).all())
        kind = getattr(rack, "kind", None)
        levels = not uses_rack_units(kind)
        # 層架最上面那片板的上面也放得下 → 可放的位置比層數多一列
        placeable = placeable_levels(kind, int(rack.u_height or 0))
        # 每一列被佔掉多少「面積」：橫向比例 × 層內上下比例。只數「有沒有東西」會把
        # 一層放了一台半寬裝置的情況當成整層滿了。
        area: dict[int, float] = {}
        for (_n, _t, pos, sz, _f, _hs, hsp, _vs, vsp) in devs:
            if pos is None:
                continue
            frac = ((float(hsp or RACK_SLOTS) / RACK_SLOTS)
                    * (float(vsp or RACK_SLOTS) / RACK_SLOTS))
            for u in range(int(pos), int(pos) + int(sz or 1)):
                area[u] = min(1.0, area.get(u, 0.0) + frac)
        occupied_rows = set(area)
        used_u = len(occupied_rows)
        free_u = max(placeable - used_u, 0)
        # 完全空的列（給「還能放多大的裝置」參考）
        free_rows = sorted(set(range(1, placeable + 1)) - occupied_rows)
        out.append({
            "id": str(rack.id), "name": rack.name, "u_height": rack.u_height,
            # 型態決定單位與畫法：標準／工業機櫃以 U 計，三種層架以「層」計
            "kind": kind, "uses_levels": levels,
            "rows_label": "level" if levels else "U",
            "open_top": has_open_top(kind),
            "placeable_rows": placeable,
            "level_heights_mm": (
                [int(x) for x in level_heights_mm(kind, rack.row_height_mm,
                                                  rack.level_heights, int(rack.u_height or 0))]
                if levels else None),
            "location": loc_name, "device_count": len(devs),
            "used_u": used_u, "free_u": free_u, "free_u_rows": free_rows,
            # 還有空位、但不是全空的那幾列（一層並排／疊放多台時最需要知道的就是這個）
            "rows_with_space": [
                {"row": u, "free_fraction": round(1.0 - a, 3)}
                for u, a in sorted(area.items()) if a < 0.999
            ],
            "devices": [
                {"name": n, "type": t, "u_position": pos, "u_size": sz, "rack_face": f,
                 # 橫向：起始格與跨幾格（整列＝0/60）；層內上下：同一套 60 格
                 "rack_slot": hs, "rack_slot_span": hsp,
                 "rack_vslot": vs, "rack_vslot_span": vsp}
                for (n, t, pos, sz, f, hs, hsp, vs, vsp) in devs
            ],
            "description": rack.description,
        })
    return {"scope": scope, "count": total, "returned": len(out), "racks": out,
            "slots_per_row": RACK_SLOTS,
            "note": ("A row can hold several devices side by side (rack_slot/rack_slot_span) "
                     "and stacked within the row (rack_vslot/rack_vslot_span), both on a "
                     f"{RACK_SLOTS}-cell grid. Shelf kinds are counted in levels, not U — "
                     "use rows_label. Check rows_with_space before saying a row is full.")}


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
    location_id: str | None = None, rack_id: str | None = None,
) -> dict[str, Any]:
    """列出/搜尋裝置（可給 name 子字串或 type 過濾）；含地點、機櫃、IP 數。

    問「某機房／某機櫃有哪些裝置」要帶 location_id / rack_id，否則回的是全站裝置。
    """
    limit = min(int(limit), 500)
    stmt = select(Device)
    if name:
        stmt = stmt.where(Device.name.ilike(f"%{name}%"))
    if type:
        stmt = stmt.where(Device.type == type)
    scope = "all"
    if location_id:
        stmt = stmt.where(Device.location_id == _as_uuid(location_id, "location_id"))
        scope = f"location:{location_id}"
    if rack_id:
        stmt = stmt.where(Device.rack_id == _as_uuid(rack_id, "rack_id"))
        scope = f"rack:{rack_id}"
    # 可見性推進 SQL：先截斷再過濾會讓受限帳號拿到不足 limit 的結果，
    # 且總數會把看不到的也算進去
    vis = await visible_ids(session, user=user, object_type="device")
    if vis is not None:
        stmt = stmt.where(in_values(Device.id, vis) if vis else sa_false())
    total = int(await session.scalar(
        select(func.count()).select_from(stmt.subquery())) or 0)
    rows = list((await session.execute(stmt.order_by(Device.name).limit(limit))).scalars().all())
    cust_cache: dict[Any, str | None] = {}
    out = []
    for d in rows:
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
            # 同一列可以左右並排、也可以上下疊放：少了這四個欄位，同一層的兩台在
            # AI 眼裡是同一個位置（60 格網格，整列＝0/60）
            "rack_slot": d.rack_slot, "rack_slot_span": d.rack_slot_span,
            "rack_vslot": d.rack_vslot, "rack_vslot_span": d.rack_vslot_span,
            "rack_id": str(d.rack_id) if d.rack_id else None,
        })
    return {"scope": scope, "count": total, "returned": len(out), "devices": out}


async def get_device(
    session: AsyncSession, *, user: User,
    device_id: str | None = None, name: str | None = None,
) -> dict[str, Any]:
    """裝置詳細資料：基本資料 + IP 清單 + （透過 LibreNMS）VLAN 與 switch port。"""
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
    # 這台裝置的 IP 只列看得到的（以前全部回，連看不到的子網路裡的位址、主機名稱、MAC 都在；
    # REST 的裝置詳細資料只給主要 IP，2026-10-07 稽核）
    ip_stmt = select(IPAddress.ip, IPAddress.hostname, IPAddress.mac).where(IPAddress.device_id == dev.id)
    vis_ip = await visible_ids(session, user=user, object_type="ip")
    ips: list[Any] = []
    if vis_ip is None or vis_ip:
        if vis_ip is not None:
            ip_stmt = ip_stmt.where(in_values(IPAddress.id, vis_ip))
        ips = list((await session.execute(ip_stmt)).all())
    # VLAN（透過連結的 librenms device）是全域資料：要全域讀取
    vlans: list[Any] = []
    if await has_global_read(session, user):
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
            from app.services.rack import placeable_levels, uses_rack_units
            rack_info = {
                "id": str(rk.id), "name": rk.name, "u_height": rk.u_height,
                # 型態決定單位：層架以「層」計，說成 U 就錯了
                "kind": getattr(rk, "kind", None),
                "uses_levels": not uses_rack_units(getattr(rk, "kind", None)),
                "placeable_rows": placeable_levels(getattr(rk, "kind", None),
                                                   int(rk.u_height or 0)),
            }
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
        # 列內的橫向與上下位置（60 格網格，整列＝0/60）
        "rack_slot": dev.rack_slot, "rack_slot_span": dev.rack_slot_span,
        "rack_vslot": dev.rack_vslot, "rack_vslot_span": dev.rack_vslot_span,
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
    subnet_cidr: str | None = None, subnet_id: str | None = None,
) -> dict[str, Any]:
    """列出 NAT 規則。問某網段對外開了什麼要帶 subnet_cidr。"""
    limit = min(int(limit), 500)
    scope_ids, scope = await _scope_subnet(
        session, user=user, subnet_cidr=subnet_cidr, subnet_id=subnet_id)
    stmt = select(NATTranslation)
    if scope_ids is not None:
        in_scope = select(IPAddress.id).where(in_values(IPAddress.subnet_id, scope_ids))
        stmt = stmt.where(NATTranslation.src_ip_id.in_(in_scope)
                          | NATTranslation.dst_ip_id.in_(in_scope))
    total = int(await session.scalar(
        select(func.count()).select_from(stmt.subquery())) or 0)
    rows = list((await session.execute(
        stmt.order_by(NATTranslation.name).limit(limit)
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
    return {"scope": scope, "count": total, "returned": len(out), "nat_rules": out}


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
            select(Device.id, Device.name, Device.primary_ip_id).where(in_values(Device.id, dev_ids))
        )).all():
            names[did] = nm
            dev_pip[did] = pip
    pip_map: dict[Any, str] = {}
    pip_ids = {p for p in dev_pip.values() if p}
    if pip_ids:
        pip_map = {pid: str(ip).split("/")[0] for pid, ip in (await session.execute(
            select(IPAddress.id, IPAddress.ip).where(in_values(IPAddress.id, pip_ids))
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
        stmt = stmt.where(in_values(IPChangeLog.subnet_id, vis)) if vis else stmt.where(sa_false())
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
    # 先把可見範圍套進查詢、再取一筆 —— 不可「先任取一筆再檢查可見性」。
    # 原寫法有兩個問題：
    #  (1) 沒 scope 也沒 limit(1) → 重疊網段（多客戶共用 198.51.100.0/24）下同一個 IP
    #      字串會有多筆，`scalar_one_or_none()` 拋 MultipleResultsFound 直接炸掉；
    #  (2) 任取一筆才驗權限 → 若剛好取到不可見子網路那筆就回「IP not found」，
    #      即使使用者其實看得到另一個子網路的同一個 IP。
    vis = await visible_ids(session, user=user, object_type="subnet")
    stmt = select(IPAddress).where(IPAddress.ip == ip)
    if vis is not None:                      # None＝全部可見（admin 或萬用授權）
        if not vis:
            raise IPAMToolError("IP not found")
        stmt = stmt.where(in_values(IPAddress.subnet_id, vis))
    ipa = (await session.execute(stmt.limit(1))).scalars().first()
    if ipa is None:
        raise IPAMToolError("IP not found")
    if ipa.mac is None:
        return {"ip": ip, "mac": None, "locations": [], "note": "no MAC known for this IP"}
    mac = str(ipa.mac).lower()
    rows = list((await session.execute(
        select(FDBEntry.port_name, FDBEntry.vlan_id_num, FDBEntry.last_seen_at,
               FDBEntry.device_id, FDBEntry.switch_device_id,
               LibreNMSDevice.hostname, LibreNMSDevice.primary_ip, Device.name)
        .outerjoin(LibreNMSDevice, LibreNMSDevice.id == FDBEntry.device_id)
        .outerjoin(Device, Device.id == FDBEntry.switch_device_id)     # MikroTik 回報的列（0170）
        .where(FDBEntry.mac == mac)
    )).all())
    locs = []
    for port, vlan, seen, ln_id, dev_id, sw_host, sw_ip, dev_name in rows:
        # 該 (switch, port) 上有幾個不同 MAC → 越少越像 access port
        same_switch = (FDBEntry.device_id == ln_id) if ln_id is not None else (
            FDBEntry.switch_device_id == dev_id)
        mac_count = int(await session.scalar(
            select(func.count(func.distinct(FDBEntry.mac)))
            .where(FDBEntry.port_name == port, same_switch)
        ) or 0)
        locs.append({
            "switch": sw_host or dev_name, "switch_ip": str(sw_ip) if sw_ip else None,
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
    # 只給 requested_ip 時，自動推導包含它的子網路（最長首碼優先）
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

async def get_ip_history(
    session: AsyncSession, *, user: User, ip: str, days: int = 30,
) -> dict[str, Any]:
    """IP 鑑識：某個 IP 在指定期間內「是誰」的證據時間軸。

    資安事件調查的第一個問題永遠是「那個 IP 當時是誰」。把四種既有證據依時間彙整：
    異動記錄（欄位級，含來源）、ARP（IP↔MAC 對應與最後出現時間）、主機名稱觀測
    （各來源獨立）、DHCP 觀測。全部是確定性檢索 —— 判讀交給呼叫端（人或模型），
    資料本身不做任何推測。

    RBAC 與 get_ip_detail 同規：先縮到可見子網路，重疊網段下不會把別單位的同 IP
    洩出來，也不會因為先取到不可見那筆而假報「查無」。
    """
    try:
        ipaddress.ip_address(ip)
    except ValueError as exc:
        raise IPAMToolError(f"Invalid IP: {exc}") from exc
    from datetime import UTC, datetime, timedelta
    days = max(1, min(int(days or 30), 365))
    since = datetime.now(UTC) - timedelta(days=days)

    from app.models.dhcp_sighting import DHCPSighting
    from app.models.ip_change_log import IPChangeLog
    from app.models.ip_hostname import IPHostnameObservation
    from app.models.librenms import ARPEntry

    vis = await visible_ids(session, user=user, object_type="subnet")
    ip_stmt = select(IPAddress).where(IPAddress.ip == ip)
    if vis is not None:
        if not vis:
            return {"ip": ip, "days": days, "visible": False, "events": []}
        ip_stmt = ip_stmt.where(in_values(IPAddress.subnet_id, vis))
    ipa = (await session.execute(ip_stmt.limit(1))).scalars().first()

    events: list[dict[str, Any]] = []
    # ARP／DHCP 觀測不掛在子網路底下（全域基礎設施資料）。受限帳號只有在
    # 「看得到這個 IP 的登錄紀錄」時才給 —— 否則對未登錄 IP 查歷史就能繞過
    # 可見性把 MAC 對應撈出來（admin／萬用讀取者 vis is None，不受此限）。
    can_see_global_evidence = (vis is None) or (ipa is not None)

    # 異動記錄：who/what/when，含來源（scanner/librenms/manual…）
    ch_stmt = (select(IPChangeLog)
               .where(IPChangeLog.ip_text == ip, IPChangeLog.created_at >= since))
    if vis is not None:
        ch_stmt = ch_stmt.where(in_values(IPChangeLog.subnet_id, vis))
    for c in (await session.execute(
            ch_stmt.order_by(IPChangeLog.created_at.desc()).limit(200))).scalars().all():
        events.append({"at": c.created_at.isoformat(), "kind": "change",
                       "source": c.source, "event": c.event_type, "field": c.field,
                       "old": c.old_value, "new": c.new_value})

    # ARP：這個 IP 綁過哪些 MAC（換 MAC ＝ 換機器或偽冒，是鑑識關鍵）。
    # 受限帳號只看這個子網路的 ARP；沒有子網路歸屬的列（LibreNMS）只在這個子網路是唯一最細的容器時才給 ——
    # 重疊網段裡同一個位址可能是別的單位的機器（2026-10-07 稽核；規則同 unmanaged.for_subnet）
    arp_stmt = select(ARPEntry).where(ARPEntry.ip == ip)
    if vis is not None and ipa is not None:
        others = await session.scalar(text("""
            SELECT count(*) FROM subnets o, subnets s
             WHERE s.id = :sid AND o.id <> s.id AND o.archived_at IS NULL
               AND o.cidr >>= CAST(:ip AS inet) AND masklen(o.cidr) >= masklen(s.cidr)
        """), {"sid": ipa.subnet_id, "ip": ip})
        arp_stmt = arp_stmt.where(or_(ARPEntry.subnet_id == ipa.subnet_id,
                                      and_(ARPEntry.subnet_id.is_(None), literal(not others))))
    # 一個 MAC 一筆：誰回報過（哪台設備的 ARP 表、哪個介面）、幾個回報者、是否疑似讀壞或過期快取 ——
    # 以前一列一筆原始紀錄、最多 20 筆、沒有來源，看不出「這個 MAC 只有路由器看過一次」（2026-10-09 回饋）
    if can_see_global_evidence:
        from app.services.anomaly import mac_rows, reporter_names
        from app.services.arp_quality import MacSeen, classify_suspects, reporter_key
        from app.services.oui import mac_prefix, vendor_map
        arp_macs: dict[str, MacSeen] = {}
        for a in (await session.execute(arp_stmt.order_by(ARPEntry.last_seen_at.desc()).limit(500))).scalars().all():
            arp_macs.setdefault(str(a.mac), MacSeen()).add(
                reporter_key(str(a.source), a.device_id), source=str(a.source), at=a.last_seen_at,
                first=getattr(a, "first_seen_at", None), device_id=a.device_id, interface=a.interface)
        if arp_macs:
            by_prefix = await vendor_map(session, list(arp_macs))
            vendors = {m: by_prefix.get(mac_prefix(m) or "") for m in arp_macs}
            names = await reporter_names(session, {r["device_id"] for sn in arp_macs.values()
                                                   for r in sn.reporters.values()})
            for row in mac_rows(arp_macs, vendors, classify_suspects(arp_macs, vendors), names):
                first = arp_macs[row["mac"]].first
                events.append({"at": row["last_seen_at"], "kind": "arp", "mac": row["mac"],
                               "first_seen": first.isoformat() if first else None,
                               "vendor": row["vendor"], "reporter_count": row["reporter_count"],
                               "reporters": row["reporters"][:20], "suspect": row["suspect"]})

    # 主機名稱觀測：各來源（rdns/netbios/mdns/dhcp…）各自的說法
    if ipa is not None:
        for o in (await session.execute(
                select(IPHostnameObservation)
                .where(IPHostnameObservation.ip_id == ipa.id)
                .order_by(IPHostnameObservation.observed_at.desc()).limit(20))).scalars().all():
            events.append({"at": o.observed_at.isoformat() if o.observed_at else None,
                           "kind": "hostname", "source": o.source,
                           "hostname": o.hostname})

    # DHCP 觀測（掃描代理看到誰在回應 DHCP —— 非法 DHCP 調查用）
    for d in ([] if not can_see_global_evidence else (await session.execute(
            select(DHCPSighting).where(DHCPSighting.server_ip == ip)
            .order_by(DHCPSighting.last_seen_at.desc()).limit(5))).scalars().all()):
        events.append({"at": d.last_seen_at.isoformat() if d.last_seen_at else None,
                       "kind": "dhcp_server_sighting", "mac": str(d.server_mac) if getattr(d, "server_mac", None) else None})

    events.sort(key=lambda e: e.get("at") or "", reverse=True)
    return {
        "ip": ip, "days": days, "visible": True,
        "registered": ipa is not None,
        "current": None if ipa is None else {
            "hostname": ipa.hostname, "mac": str(ipa.mac) if ipa.mac else None,
            "status": ipa.effective_status, "state": ipa.state,
            "discovery_source": ipa.discovery_source,
        },
        "events": events,
        "note": "資料為系統記錄的原始證據；時間為 UTC。判讀請以多筆證據交叉為準。ARP 每個 MAC 一筆："
                "reporter_count＝幾個來源（設備）看過；suspect＝corrupt（只有一個來源、查不到廠商、"
                "是另外兩個 MAC 拼起來的，多半是 SNMP 讀到一半）或 stale_cache（單一來源的本地管理位址，"
                "與多來源佐證的 MAC 不一致），這兩種不要當成真的另一台機器。",
    }


async def get_ip_detail(session: AsyncSession, *, user: User, ip: str) -> dict[str, Any]:
    """單一 IP 的完整資料：狀態 / 主機名稱 / MAC / 擁有者 / 裝置 / 交換器埠 / 客戶 / 最後上線來源。"""
    try:
        ipaddress.ip_address(ip)
    except ValueError as exc:
        raise IPAMToolError(f"Invalid IP: {exc}") from exc
    # RBAC：先縮到可見子網路再取一筆。若「先任取一筆再驗可見性」，在重疊網段
    # （多客戶共用 198.51.100.0/24）下可能取到不可見那筆而回報「查無」——
    # 但使用者其實看得到另一個子網路的同一個 IP，那是假的查無。
    vis = await visible_ids(session, user=user, object_type="subnet")
    stmt = select(IPAddress).where(IPAddress.ip == ip)
    if vis is not None:                      # None＝全部可見（admin 或萬用授權）
        if not vis:
            return {"found": False, "ip": ip}
        stmt = stmt.where(in_values(IPAddress.subnet_id, vis))
    obj = (await session.execute(stmt.limit(1))).scalars().first()
    if obj is None:
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
        # OCS Inventory（透過網卡 MAC 比對到這個 IP 的資產盤點）
        "last_seen_ocs": obj.last_seen_ocs,
        "ocs_tag": obj.ocs_tag,
        "ocs_agent": obj.ocs_agent,
        "ocs_notes": obj.ocs_notes or [],
        # OCS 回報的硬體：系統／主機板／BIOS／CPU／記憶體／磁碟／顯示卡（記憶體、磁碟單位 MB）
        "ocs_hardware": obj.ocs_hw,
        # RustDesk Server（開源版）：對應到這個 IP 的 RustDesk ID 與線上狀態（不帶連線網址）
        "rustdesk": await _rustdesk_brief(session, obj.id),
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
    """所有防火牆清單（OPNsense / pfSense / FortiGate / Palo Alto / Check Point / MikroTik，不含密鑰）。
    每筆帶 `vendor` 標明廠牌。

    **所有廠牌一起回**：只回其中一種的話，模型會拿一份不完整的清單當成全部去回答
    「我們有哪些防火牆」—— 那比答不出來更糟。新增廠牌時這裡一定要跟著加
    （`tests/test_integration_coverage.py` 會擋）。
    """
    from app.models.checkpoint import CheckPointServer
    from app.models.firewall import OPNsenseFirewall
    from app.models.fortigate import FortiGateFirewall
    from app.models.mikrotik import MikroTikRouter
    from app.models.paloalto import PaloAltoFirewall
    from app.models.pfsense import PfSenseFirewall

    out: list[dict[str, Any]] = []
    # Check Point 的整合單位是管理伺服器（一台管多個閘道），閘道清單在 list_checkpoint_rules 的 install_on
    for vendor, model in (("opnsense", OPNsenseFirewall), ("pfsense", PfSenseFirewall),
                          ("fortigate", FortiGateFirewall), ("paloalto", PaloAltoFirewall),
                          ("checkpoint", CheckPointServer), ("mikrotik", MikroTikRouter)):
        rows = (await session.execute(select(model).limit(limit))).scalars().all()
        out.extend({
            "id": str(f.id), "vendor": vendor, "name": f.name,
            "api_url": getattr(f, "api_url", None), "enabled": f.enabled,
            "last_sync_at": f.last_sync_at, "last_error": f.last_error,
            "description": getattr(f, "description", None),
        } for f in rows)
    return {"firewalls": out[:limit]}


async def list_mikrotik_rules(
    session: AsyncSession, *, user: User,
    router_name: str | None = None, table: str | None = None, limit: int = 200,
) -> dict[str, Any]:
    """MikroTik RouterOS 防火牆規則（唯讀鏡像）。可依路由器名稱與表（filter/nat/mangle）篩選。

    依 `position` 排序而不是名稱：RouterOS 由上而下比對，第一條命中就決定結果 ——
    順序本身就是語意，打散了規則集就讀不出行為。
    """
    from app.models.mikrotik import MikroTikRouter, MikroTikRule
    stmt = select(MikroTikRule, MikroTikRouter.name).join(
        MikroTikRouter, MikroTikRouter.id == MikroTikRule.router_id)
    if router_name:
        stmt = stmt.where(MikroTikRouter.name == router_name)
    if table in ("filter", "nat", "mangle"):
        stmt = stmt.where(MikroTikRule.table_name == table)
    rows = (await session.execute(stmt.order_by(
        MikroTikRule.table_name, MikroTikRule.position).limit(limit))).all()
    return {"rules": [{
        "router": name, "table": r.table_name, "chain": r.chain, "position": r.position,
        "action": r.action, "disabled": r.disabled, "protocol": r.protocol,
        "src_address": r.src_address, "dst_address": r.dst_address,
        "src_port": r.src_port, "dst_port": r.dst_port,
        "in_interface": r.in_interface, "out_interface": r.out_interface,
        "to_addresses": r.to_addresses, "to_ports": r.to_ports, "comment": r.comment,
    } for r, name in rows]}


async def list_mikrotik_address_lists(
    session: AsyncSession, *, user: User,
    router_name: str | None = None, list_name: str | None = None, limit: int = 300,
) -> dict[str, Any]:
    """MikroTik 的 address-list（等同其他廠牌的別名）。

    與其他家的差別：RouterOS 的清單是**逐筆位址**，不是一個物件裝很多成員 ——
    所以一列就是一個位址，回傳可能很長（動態封鎖清單常常上萬筆）。
    """
    from app.models.mikrotik import MikroTikAddressList, MikroTikRouter
    stmt = select(MikroTikAddressList, MikroTikRouter.name).join(
        MikroTikRouter, MikroTikRouter.id == MikroTikAddressList.router_id)
    if router_name:
        stmt = stmt.where(MikroTikRouter.name == router_name)
    if list_name:
        stmt = stmt.where(MikroTikAddressList.list_name == list_name)
    total = int(await session.scalar(
        select(func.count()).select_from(stmt.subquery())) or 0)
    rows = (await session.execute(stmt.order_by(
        MikroTikAddressList.list_name, MikroTikAddressList.address).limit(limit))).all()
    return {"total": total, "returned": len(rows), "entries": [{
        "router": name, "list": e.list_name, "address": e.address,
        "dynamic": e.dynamic, "timeout": e.timeout, "comment": e.comment,
    } for e, name in rows]}


async def list_dhcp_ranges(
    session: AsyncSession, *, user: User, limit: int = 300,
    subnet_cidr: str | None = None, subnet_id: str | None = None,
) -> dict[str, Any]:
    """各整合同步回來的 DHCP 發放範圍（OPNsense / pfSense / FortiGate / Windows DHCP），
    加上子網路裡手動定義的 DHCP 集區（source_type=manual）。

    每筆帶來源整合（`source_type` / `source_name`）與 DHCP 引擎（`source`：kea / isc /
    windows）。「這個 IP 是不是落在 DHCP 集區裡」這種問題要靠它，不能拿子網路去猜。
    """
    from app.models.dhcp import DHCPPoolRange
    scope_ids, scope = await _scope_subnet(
        session, user=user, subnet_cidr=subnet_cidr, subnet_id=subnet_id)
    stmt = select(DHCPPoolRange)
    if scope_ids is not None:
        # 範圍表存的是 CIDR 字串，直接以該子網路的 cidr 比對
        stmt = stmt.where(DHCPPoolRange.subnet_cidr.in_(
            select(Subnet.cidr).where(in_values(Subnet.id, scope_ids))))
    from app.services.ip_ranges import manual_dhcp_pools
    # 子網路裡手動定義的 DHCP 集區（issue #40），跟整合同步回來的一起列
    manual = await manual_dhcp_pools(session, list(scope_ids) if scope_ids is not None else None)
    total = int(await session.scalar(
        select(func.count()).select_from(stmt.subquery())) or 0) + len(manual)
    rows: list[Any] = [*(await session.execute(
        stmt.order_by(DHCPPoolRange.source_type, DHCPPoolRange.start_ip).limit(limit)
    )).scalars().all(), *manual][:limit]
    return {"scope": scope, "count": total, "returned": len(rows), "ranges": [{
        "source_type": r.source_type, "source_name": r.source_name,
        "subnet_cidr": str(r.subnet_cidr) if r.subnet_cidr else None,
        "start_ip": str(r.start_ip), "end_ip": str(r.end_ip),
        "family": r.family, "engine": r.source, "synced_at": r.synced_at,
    } for r in rows]}


async def list_fortigate_policies(
    session: AsyncSession, *, user: User,
    firewall_name: str | None = None, vdom: str | None = None, limit: int = 200,
) -> dict[str, Any]:
    """FortiGate 防火牆政策（唯讀鏡像）。可依防火牆名稱與 VDOM 篩選。"""
    from app.models.fortigate import FortiGateFirewall, FortiGatePolicy
    stmt = select(FortiGatePolicy, FortiGateFirewall.name).join(
        FortiGateFirewall, FortiGateFirewall.id == FortiGatePolicy.firewall_id)
    if firewall_name:
        stmt = stmt.where(FortiGateFirewall.name == firewall_name)
    if vdom:
        stmt = stmt.where(FortiGatePolicy.vdom == vdom)
    rows = (await session.execute(
        stmt.order_by(FortiGatePolicy.vdom, FortiGatePolicy.policyid).limit(limit))).all()
    return {"policies": [{
        "firewall": fw_name, "vdom": p.vdom, "policyid": p.policyid, "name": p.name,
        "status": p.status, "action": p.action, "srcintf": p.srcintf, "dstintf": p.dstintf,
        "srcaddr": p.srcaddr, "dstaddr": p.dstaddr, "service": p.service, "nat": p.nat,
        "comments": p.comments,
    } for p, fw_name in rows]}


async def list_fortigate_addresses(
    session: AsyncSession, *, user: User,
    firewall_name: str | None = None, vdom: str | None = None, limit: int = 300,
) -> dict[str, Any]:
    """FortiGate 位址物件與位址群組（唯讀鏡像）。群組的 `members` 是成員名稱清單。"""
    from app.models.fortigate import FortiGateAddressObject, FortiGateFirewall
    stmt = select(FortiGateAddressObject, FortiGateFirewall.name).join(
        FortiGateFirewall, FortiGateFirewall.id == FortiGateAddressObject.firewall_id)
    if firewall_name:
        stmt = stmt.where(FortiGateFirewall.name == firewall_name)
    if vdom:
        stmt = stmt.where(FortiGateAddressObject.vdom == vdom)
    rows = (await session.execute(
        stmt.order_by(FortiGateAddressObject.vdom, FortiGateAddressObject.name)
        .limit(limit))).all()
    return {"addresses": [{
        "firewall": fw_name, "vdom": a.vdom, "name": a.name, "kind": a.kind,
        "obj_type": a.obj_type, "value": a.value, "members": a.members, "comment": a.comment,
    } for a, fw_name in rows]}


async def list_paloalto_policies(
    session: AsyncSession, *, user: User,
    firewall_name: str | None = None, vsys: str | None = None, limit: int = 200,
) -> dict[str, Any]:
    """Palo Alto（PAN-OS）安全政策（唯讀鏡像）。可依防火牆名稱與 vsys 篩選。

    依 `position` 排序而不是名稱：PAN-OS 由上而下比對，順序本身就是語意。
    `application` 是 App-ID —— PAN-OS 規則真正在管的東西，不可以省略。
    """
    from app.models.paloalto import PaloAltoFirewall, PaloAltoPolicy
    stmt = select(PaloAltoPolicy, PaloAltoFirewall.name).join(
        PaloAltoFirewall, PaloAltoFirewall.id == PaloAltoPolicy.firewall_id)
    if firewall_name:
        stmt = stmt.where(PaloAltoFirewall.name == firewall_name)
    if vsys:
        stmt = stmt.where(PaloAltoPolicy.vsys == vsys)
    rows = (await session.execute(
        stmt.order_by(PaloAltoPolicy.vsys, PaloAltoPolicy.position).limit(limit))).all()
    return {"policies": [{
        "firewall": fw_name, "vsys": p.vsys, "position": p.position, "name": p.name,
        "action": p.action, "disabled": p.disabled,
        "from_zone": p.from_zone, "to_zone": p.to_zone,
        "source": p.source, "destination": p.destination,
        "application": p.application, "service": p.service, "description": p.description,
    } for p, fw_name in rows]}


async def list_paloalto_addresses(
    session: AsyncSession, *, user: User,
    firewall_name: str | None = None, vsys: str | None = None, limit: int = 300,
) -> dict[str, Any]:
    """Palo Alto 位址物件與位址群組（唯讀鏡像）。群組的 `members` 是成員名稱清單。"""
    from app.models.paloalto import PaloAltoAddressObject, PaloAltoFirewall
    stmt = select(PaloAltoAddressObject, PaloAltoFirewall.name).join(
        PaloAltoFirewall, PaloAltoFirewall.id == PaloAltoAddressObject.firewall_id)
    if firewall_name:
        stmt = stmt.where(PaloAltoFirewall.name == firewall_name)
    if vsys:
        stmt = stmt.where(PaloAltoAddressObject.vsys == vsys)
    rows = (await session.execute(
        stmt.order_by(PaloAltoAddressObject.vsys, PaloAltoAddressObject.name)
        .limit(limit))).all()
    return {"addresses": [{
        "firewall": fw_name, "vsys": a.vsys, "name": a.name, "kind": a.kind,
        "obj_type": a.obj_type, "value": a.value, "members": a.members,
        "description": a.description,
    } for a, fw_name in rows]}


async def list_checkpoint_rules(
    session: AsyncSession, *, user: User,
    server_name: str | None = None, layer: str | None = None, limit: int = 200,
) -> dict[str, Any]:
    """Check Point 存取規則（唯讀鏡像，來自管理伺服器）。可依管理伺服器名稱與政策層篩選。

    依網域／政策套件／層／規則編號排序：Check Point 由上而下比對，順序本身就是語意。
    `source_negate`／`destination_negate` 為真時欄位意思是「除了這些以外」，不可以讀成正向。
    """
    from app.models.checkpoint import CheckPointRule, CheckPointServer
    stmt = select(CheckPointRule, CheckPointServer.name).join(
        CheckPointServer, CheckPointServer.id == CheckPointRule.server_id)
    if server_name:
        stmt = stmt.where(CheckPointServer.name == server_name)
    if layer:
        stmt = stmt.where(CheckPointRule.layer == layer)
    rows = (await session.execute(stmt.order_by(
        CheckPointRule.domain, CheckPointRule.package, CheckPointRule.layer, CheckPointRule.rule_number)
        .limit(limit))).all()
    return {"rules": [{
        "server": srv, "domain": r.domain or None, "package": r.package, "layer": r.layer,
        "section": r.section, "rule_number": r.rule_number, "name": r.name, "action": r.action,
        "enabled": r.enabled, "source": r.source, "source_negate": r.source_negate,
        "destination": r.destination, "destination_negate": r.destination_negate,
        "service": r.service, "install_on": r.install_on, "hits": r.hits, "comments": r.comments,
    } for r, srv in rows]}


async def list_checkpoint_objects(
    session: AsyncSession, *, user: User,
    server_name: str | None = None, name: str | None = None, limit: int = 300,
) -> dict[str, Any]:
    """Check Point 網路物件（host／network／address-range／group／group-with-exclusion，唯讀鏡像）。"""
    from app.models.checkpoint import CheckPointObject, CheckPointServer
    stmt = select(CheckPointObject, CheckPointServer.name).join(
        CheckPointServer, CheckPointServer.id == CheckPointObject.server_id)
    if server_name:
        stmt = stmt.where(CheckPointServer.name == server_name)
    if name:
        stmt = stmt.where(CheckPointObject.name.ilike(f"%{name}%"))
    rows = (await session.execute(stmt.order_by(CheckPointObject.domain, CheckPointObject.name).limit(limit))).all()
    return {"objects": [{
        "server": srv, "domain": o.domain or None, "name": o.name, "type": o.obj_type, "value": o.value,
        "members": o.members, "comments": o.comments,
    } for o, srv in rows]}


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
        scope_limited=not await has_global_read(session, user),
    )
    if graph.get("too_large"):
        return {"too_large": graph["too_large"],
                "hint": "Too many devices to draw at once; call again with subnet_cidr to narrow it down."}
    labels = {n["data"]["id"]: n["data"].get("label") for n in graph["nodes"]}
    edges = [{
        "from": labels.get(e["data"]["source"], e["data"]["source"]),
        "to": labels.get(e["data"]["target"], e["data"]["target"]),
        "kind": e["data"].get("kind"), "label": e["data"].get("label"),
        "via": e["data"].get("via"),
    } for e in graph["edges"]]
    out: dict[str, Any] = {
        "node_count": len(graph["nodes"]),
        "edge_count": len(graph["edges"]),
        "edges": edges[:300],
    }
    if graph.get("scope") == "limited":
        out["scope"] = "limited: only the devices and subnets this account can see"
        out["hidden_layers"] = graph.get("hidden_layers", [])
    return out


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
            select(func.host(_IPA.ip)).where(in_values(func.host(_IPA.ip), ip_vals, type_=String()))
        )).all():
            have.add(str(host))
    zone_ids = {r.zone_id for r in rows if r.zone_id}
    zsrv: dict[Any, str] = {}
    if zone_ids:
        for zid, sname in (await session.execute(
            select(DNSZone.id, DNSServer.name)
            .join(DNSServer, DNSServer.id == DNSZone.server_id)
            .where(in_values(DNSZone.id, zone_ids))
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
    """IP 申請工作流清單。

    可見範圍與 REST 端點同一套判定，不另立規則：審核人（admin 或指定審核人）看得到全部，
    其餘只看自己提出的。以前這裡用「全域讀取」判斷，跟 REST 不同（2026-10-07 稽核）。
    """
    from app.models.ip_request import IPRequest
    from app.services.ip_request_policy import is_global_approver
    stmt = select(IPRequest)
    if not await is_global_approver(session, user):
        stmt = stmt.where(IPRequest.requester_user_id == user.id)
    if status:
        stmt = stmt.where(IPRequest.status == status)
    total = int(await session.scalar(
        select(func.count()).select_from(stmt.subquery())) or 0)
    rows = (await session.execute(
        stmt.order_by(IPRequest.created_at.desc()).limit(limit))).scalars().all()
    return {"count": total, "returned": len(rows), "requests": [{
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
    subnet_cidr: str | None = None, subnet_id: str | None = None,
) -> dict[str, Any]:
    """交換器 FDB 紀錄（MAC↔埠）。問某網段的機器接在哪要帶 subnet_cidr。"""
    scope_ids, scope = await _scope_subnet(
        session, user=user, subnet_cidr=subnet_cidr, subnet_id=subnet_id)
    stmt = select(FDBEntry)
    if mac:
        stmt = stmt.where(FDBEntry.mac == mac.strip().lower())
    if scope_ids is not None:
        # FDB 只有 MAC 沒有 IP → 以該網段 IP 已知的 MAC 反查
        stmt = stmt.where(FDBEntry.mac.in_(
            select(IPAddress.mac).where(
                in_values(IPAddress.subnet_id, scope_ids), IPAddress.mac.is_not(None))))
    total = int(await session.scalar(
        select(func.count()).select_from(stmt.subquery())) or 0)
    stmt = stmt.order_by(FDBEntry.last_seen_at.desc()).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return {"scope": scope, "count": total, "returned": len(rows), "fdb": [{
        "mac": str(e.mac), "vlan": e.vlan_id_num, "port": e.port_name,
        "device_id": str(e.device_id) if e.device_id else None, "source": e.source,
        # source=mikrotik 的列：回報的路由器所對應的 jt-ipam 裝置（device_id 是 LibreNMS 裝置）
        "switch_device_id": str(e.switch_device_id) if e.switch_device_id else None,
        "last_seen_at": e.last_seen_at,
    } for e in rows]}


async def wazuh_missing_agents(
    session: AsyncSession, *, user: User, limit: int = 200,
    subnet_cidr: str | None = None, subnet_id: str | None = None,
) -> dict[str, Any]:
    """有設主機名稱、卻沒裝 Wazuh agent 的 IP（資安覆蓋缺口）。

    問「某網段有誰沒裝」一定要帶 subnet_cidr/subnet_id：不帶會回全站缺口，
    模型就會把別的網段當成該網段的答案（曾實際發生：問 1.0/24 卻回 11.x/40.x）。
    """
    from app.models.wazuh import WazuhInstance
    from app.services.agent_scope import expected_subnets
    from app.services.wazuh import find_missing_agents
    scope_ids: list[uuid.UUID] | None = None
    scope_label = "all"
    if subnet_cidr or subnet_id:
        subnet = await _resolve_subnet(
            session, user=user, subnet_id=subnet_id, subnet_cidr=subnet_cidr)
        scope_ids = [subnet.id]
        scope_label = str(subnet.cidr)
    else:
        # 沒指定網段 → 跟畫面一致，只看 Wazuh 整合設定的「限定子網路範圍」
        scope_ids = expected_subnets(list((await session.execute(
            select(WazuhInstance).where(WazuhInstance.enabled.is_(True)))).scalars().all()))
        if scope_ids is not None:
            scope_label = "integration_scope"
    rows = await find_missing_agents(session, subnet_ids=scope_ids)
    return {
        "scope": scope_label,          # 回答時必須說明涵蓋範圍
        "missing_count": len(rows),    # 此範圍內的總數（非全站）
        "returned": min(len(rows), limit),
        "missing": rows[:limit],
    }



async def list_attack_surface(
    session: AsyncSession, *, user: User, fqdn: str | None = None,
    ip: str | None = None, limit: int = 200,
) -> dict[str, Any]:
    """對外開放服務清單（從外面可達的 IP:port，每項配 IPAM 身分與 DNS 名稱）。

    支援兩種問法，因為人記得的常是名字不是位址：
    - `fqdn="meet.example.net"` → 先由 DNS 記錄對應到 IP，再列該 IP 的對外開口
    - `ip="198.51.100.7"` → 直接列該位址的開口
    兩者都不給就是全部（回傳的 `scope` 會標明）。
    """
    from app.services.fw_lookup import attack_surface

    items = await attack_surface(session)
    scope = "all"
    if fqdn:
        want = fqdn.strip().rstrip(".").lower()
        items = [i for i in items
                 if any(f.lower() == want for f in (i["identity"].get("fqdns") or []))]
        scope = f"fqdn:{want}"
    elif ip:
        want_ip = ip.strip()
        items = [i for i in items if str(i["identity"].get("ip") or "") == want_ip]
        scope = f"ip:{want_ip}"
    n = max(1, min(int(limit), 500))
    return {
        "scope": scope,
        "count": len(items),
        "returned": min(len(items), n),
        # 未登錄的目標本身就是警訊，一併帶出來讓對話端可以指出來
        "items": [{
            "ip": i["identity"].get("ip"),
            "registered": i["identity"].get("registered"),
            "hostname": i["identity"].get("hostname"),
            "fqdns": i["identity"].get("fqdns") or [],
            "port": i.get("port"), "protocol": i.get("protocol"),
            "via": i.get("via"), "source": i.get("source"),
            "firewall": i.get("firewall"), "name": i.get("name"),
            "customer": i["identity"].get("customer"),
            "subnet": i["identity"].get("subnet"),
            "wazuh": i["identity"].get("wazuh"),
            "status": i["identity"].get("status"),
        } for i in items[:n]],
    }


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


async def list_vms(
    session: AsyncSession, *, user: User, limit: int = 200,
    subnet_cidr: str | None = None, subnet_id: str | None = None,
) -> dict[str, Any]:
    """虛擬機清單（Proxmox VE 等同步回來）。問某網段有哪些 VM 要帶 subnet_cidr。"""
    from app.models.virt import VirtualMachine, VMInterface
    scope_ids, scope = await _scope_subnet(
        session, user=user, subnet_cidr=subnet_cidr, subnet_id=subnet_id)
    stmt = select(VirtualMachine)
    if scope_ids is not None:
        stmt = stmt.where(VirtualMachine.primary_ip_id.in_(
            select(IPAddress.id).where(in_values(IPAddress.subnet_id, scope_ids))))
    total = int(await session.scalar(
        select(func.count()).select_from(stmt.subquery())) or 0)
    rows = list((await session.execute(stmt.limit(limit))).scalars().all())
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
    return {"scope": scope, "count": total, "returned": len(out), "vms": out}


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


async def list_power(
    session: AsyncSession, *, user: User, limit: int = 200, rack_id: str | None = None,
) -> dict[str, Any]:
    """電力清單：饋線（電壓/電流/相位）與插座（接到哪台裝置）。

    問「某機櫃／某機房的電力」要帶 rack_id，否則回的是全站電力。
    """
    from app.models.physical import PowerFeed, PowerOutlet
    lim = min(int(limit), 500)
    fstmt = select(PowerFeed)
    ostmt = select(PowerOutlet)
    scope = "all"
    if rack_id:
        rid = _as_uuid(rack_id, "rack_id")
        vis = await visible_ids(session, user=user, object_type="rack")
        if vis is not None and rid not in vis:
            raise IPAMToolError("rack not visible to this user")
        scope = f"rack:{rid}"
        fstmt = fstmt.where(PowerFeed.rack_id == rid)
        # 插座掛在饋線上 → 以範圍內的饋線反查
        ostmt = ostmt.where(PowerOutlet.feed_id.in_(select(PowerFeed.id).where(
            PowerFeed.rack_id == rid)))
    feed_total = int(await session.scalar(
        select(func.count()).select_from(fstmt.subquery())) or 0)
    outlet_total = int(await session.scalar(
        select(func.count()).select_from(ostmt.subquery())) or 0)
    feeds = list((await session.execute(fstmt.limit(lim))).scalars().all())
    outlets = list((await session.execute(ostmt.limit(lim))).scalars().all())
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
    return {"scope": scope, "feed_count": feed_total, "outlet_count": outlet_total,
            "feeds_returned": len(feed_out), "outlets_returned": len(outlet_out),
            "feeds": feed_out, "outlets": outlet_out}


async def list_wazuh_agents(
    session: AsyncSession, *, user: User, limit: int = 200,
    subnet_cidr: str | None = None, subnet_id: str | None = None,
) -> dict[str, Any]:
    """Wazuh 代理清單（狀態、OS、版本、CVE 數）。問某網段時要帶 subnet_cidr。"""
    from app.models.wazuh import WazuhAgent
    scope_ids, scope = await _scope_subnet(
        session, user=user, subnet_cidr=subnet_cidr, subnet_id=subnet_id)
    stmt = select(WazuhAgent)
    if scope_ids is not None:
        # agent 以 jt_ipam_address_id 對映 IP → 用該 IP 的子網路限縮
        stmt = stmt.where(WazuhAgent.jt_ipam_address_id.in_(
            select(IPAddress.id).where(in_values(IPAddress.subnet_id, scope_ids))))
    total = int(await session.scalar(
        select(func.count()).select_from(stmt.subquery())) or 0)
    rows = (await session.execute(
        stmt.order_by(WazuhAgent.name).limit(min(int(limit), 500))
    )).scalars().all()
    return {"scope": scope, "count": total, "returned": len(rows), "agents": [{
        "id": str(a.id), "agent_id": a.agent_id, "name": a.name, "ip": a.ip,
        "status": a.status, "os_platform": a.os_platform, "os_version": a.os_version, "os_name": a.os_name,
        "agent_version": a.agent_version, "group": a.group,
        "last_keep_alive": a.last_keep_alive,
    } for a in rows]}


async def list_ocs_computers(
    session: AsyncSession, *, user: User, limit: int = 200,
    subnet_cidr: str | None = None, subnet_id: str | None = None,
    stale_days: int | None = None,
) -> dict[str, Any]:
    """OCS Inventory 盤點到的電腦（OS／資產標籤／代理版本／盤點時間／備註）。

    OCS 沒有自己的每台記錄表 —— 它是**透過網卡 MAC** 比對到既有 IP，再把資料補上去，
    所以這裡列的就是「有被 OCS 盤點過」的 IP。問某網段時要帶 subnet_cidr。
    stale_days=N 只列「超過 N 天沒被盤點」的（找資產盤點缺口用）。
    """
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import or_

    scope_ids, scope = await _scope_subnet(
        session, user=user, subnet_cidr=subnet_cidr, subnet_id=subnet_id)
    stmt = select(IPAddress).where(
        or_(IPAddress.ocs_id.isnot(None), IPAddress.last_seen_ocs.isnot(None)))
    if scope_ids is not None:
        stmt = stmt.where(in_values(IPAddress.subnet_id, scope_ids))
    if stale_days is not None:
        cutoff = datetime.now(UTC) - timedelta(days=max(0, int(stale_days)))
        stmt = stmt.where(or_(IPAddress.last_seen_ocs.is_(None),
                              IPAddress.last_seen_ocs < cutoff))
    total = int(await session.scalar(
        select(func.count()).select_from(stmt.subquery())) or 0)
    rows = (await session.execute(
        stmt.order_by(IPAddress.last_seen_ocs.desc().nullslast()).limit(min(int(limit), 500))
    )).scalars().all()
    return {"scope": scope, "count": total, "returned": len(rows), "computers": [{
        "ip": str(a.ip).split("/")[0], "hostname": a.hostname,
        "mac": str(a.mac) if a.mac else None,
        "os": a.os_ocs, "tag": a.ocs_tag, "agent_version": a.ocs_agent,
        "last_inventory": a.last_seen_ocs, "ocs_id": a.ocs_id,
        "notes": a.ocs_notes or [],
    } for a in rows]}


async def _rustdesk_brief(session: AsyncSession, address_id: Any) -> dict[str, Any] | None:
    from app.services.rustdesk import for_address
    rd = await for_address(session, address_id)
    if rd is None:
        return None
    return {"id": rd["id"], "online": rd["online"], "last_online_at": rd["last_online_at"],
            "server": rd["server_name"]}


async def list_rustdesk_peers(
    session: AsyncSession, *, user: User, limit: int = 200,
    subnet_cidr: str | None = None, subnet_id: str | None = None,
    online: bool | None = None, q: str | None = None,
) -> dict[str, Any]:
    """RustDesk Server（開源版）上註冊的裝置：ID、是否上線、最後上線、登記 IP、對應到的 IP 記錄。

    hbbs 不存主機名稱／OS；hostname 欄是對應到的 jt-ipam IP 記錄的。問某網段時要帶 subnet_cidr ——
    帶了就只列「對應到該網段 IP」的裝置（沒對應到的不知道在哪個網段）。
    """
    from sqlalchemy import or_

    from app.models.rustdesk import RustDeskPeer, RustDeskServer

    scope_ids, scope = await _scope_subnet(
        session, user=user, subnet_cidr=subnet_cidr, subnet_id=subnet_id)
    stmt = (select(RustDeskPeer, RustDeskServer.name, IPAddress.ip, IPAddress.hostname)
            .join(RustDeskServer, RustDeskServer.id == RustDeskPeer.server_id)
            .outerjoin(IPAddress, IPAddress.id == RustDeskPeer.address_id))
    if scope_ids is not None:
        stmt = stmt.where(in_values(IPAddress.subnet_id, scope_ids))
    if online is not None:
        stmt = stmt.where(RustDeskPeer.online.is_(bool(online)))
    if q and str(q).strip():
        like = f"%{str(q).strip()[:100]}%"
        stmt = stmt.where(or_(RustDeskPeer.rustdesk_id.ilike(like), IPAddress.hostname.ilike(like),
                              func.host(RustDeskPeer.registered_ip).ilike(like)))
    total = int(await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = (await session.execute(
        stmt.order_by(RustDeskPeer.online.desc(), RustDeskPeer.last_online_at.desc().nullslast())
        .limit(min(int(limit), 500)))).all()
    return {"scope": scope, "count": total, "returned": len(rows), "peers": [{
        "rustdesk_id": p.rustdesk_id, "server": server, "online": p.online,
        "last_online_at": p.last_online_at,
        "registered_ip": str(p.registered_ip).split("/")[0] if p.registered_ip else None,
        "match_status": p.match_status,
        "ip": str(ip).split("/")[0] if ip else None, "hostname": hostname,
    } for p, server, ip, hostname in rows]}


async def list_rustdesk_audit(
    session: AsyncSession, *, user: User, limit: int = 100, hours: int = 168,
    kind: str | None = None, q: str | None = None, subnet_cidr: str | None = None, subnet_id: str | None = None,
) -> dict[str, Any]:
    """RustDesk 客戶端回報的稽核：誰（對方 ID、名稱、IP）在什麼時候連進哪台、傳了什麼檔案、告警（密碼錯太多次等）。

    受控端要開著回報（客戶端 API 伺服器沒填時會自動送到 ID 伺服器的 21114，代理在那裡收）才會有資料。
    問某網段時帶 subnet_cidr：只列受控端對應到該網段 IP 的紀錄。
    """
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import and_, or_

    from app.models.rustdesk import RustDeskAuditEvent, RustDeskPeer

    scope_ids, scope = await _scope_subnet(session, user=user, subnet_cidr=subnet_cidr, subnet_id=subnet_id)
    E = RustDeskAuditEvent
    since = datetime.now(UTC) - timedelta(hours=max(1, min(int(hours), 24 * 400)))
    stmt = (select(E, IPAddress.ip, IPAddress.hostname, RustDeskPeer.hostname)
            .outerjoin(RustDeskPeer, and_(RustDeskPeer.server_id == E.server_id,
                                          RustDeskPeer.rustdesk_id == E.rustdesk_id))
            .outerjoin(IPAddress, IPAddress.id == RustDeskPeer.address_id)
            .where(E.occurred_at >= since))
    if kind in ("conn", "file", "alarm", "note"):
        stmt = stmt.where(E.kind == kind)
    if scope_ids is not None:
        stmt = stmt.where(in_values(IPAddress.subnet_id, scope_ids))
    if q and str(q).strip():
        like = f"%{str(q).strip()[:100]}%"
        stmt = stmt.where(or_(E.rustdesk_id.ilike(like), E.peer_id.ilike(like), E.peer_name.ilike(like),
                              IPAddress.hostname.ilike(like), RustDeskPeer.hostname.ilike(like)))
    total = int(await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = (await session.execute(stmt.order_by(E.occurred_at.desc()).limit(min(int(limit), 500)))).all()
    return {"scope": scope, "since": since, "count": total, "returned": len(rows), "events": [{
        "at": e.occurred_at, "kind": e.kind, "action": e.action, "device_rustdesk_id": e.rustdesk_id,
        "device_ip": str(ip).split("/")[0] if ip else None, "device_hostname": host or rhost,
        "peer_id": e.peer_id, "peer_name": e.peer_name, "peer_ip": str(e.ip).split("/")[0] if e.ip else None,
        "conn_type": e.conn_type, "alarm_type": e.alarm_type, "detail": e.detail, "verified": e.verified,
    } for e, ip, host, rhost in rows]}


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
    dev = Device(name=name.strip(), type=type, fqdn=fqdn, vendor=vendor, model=model,
                 type_source="manual" if type != "other" else None)
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
async def list_connection_targets(
    session: AsyncSession, *, user: User, protocol: str | None = None, limit: int = 200,
) -> dict[str, Any]:
    """瀏覽器遠端連線管理（SSH / RDP / VNC）已啟用、且呼叫者可連線的 IP / 裝置。唯讀，絕不回帳密。"""
    from app.services.permission import get_object_permission, has_permission
    if limit > 500:
        limit = 500
    proto = (protocol or "").lower().strip()
    if proto == "ssh":
        stmt = select(IPAddress).where(IPAddress.ssh_enabled.is_(True))
    elif proto == "rdp":
        stmt = select(IPAddress).where(IPAddress.rdp_enabled.is_(True))
    elif proto == "vnc":
        stmt = select(IPAddress).where(IPAddress.vnc_enabled.is_(True))
    else:
        stmt = select(IPAddress).where(
            IPAddress.ssh_enabled.is_(True)
            | IPAddress.rdp_enabled.is_(True)
            | IPAddress.vnc_enabled.is_(True)
        )
    if not user.is_admin:
        vis = await visible_ids(session, user=user, object_type="subnet")
        if vis is not None:
            if not vis:
                return {"items": [], "count": 0}
            stmt = stmt.where(in_values(IPAddress.subnet_id, vis))
    rows = list((await session.execute(stmt)).scalars().all())
    perm_cache: dict[Any, str] = {}
    kept: list[IPAddress] = []
    for ip in rows:
        if user.is_admin:
            usable = True
        else:
            lvl = perm_cache.get(ip.subnet_id)
            if lvl is None:
                lvl = await get_object_permission(
                    session, user=user, object_type="subnet", object_id=ip.subnet_id,
                )
                perm_cache[ip.subnet_id] = lvl
            if lvl == "none":
                continue
            usable = has_permission(lvl, "write") or bool(user.can_ssh)
        if not usable:
            continue
        kept.append(ip)
        if len(kept) >= limit:
            break
    dev_ids = {ip.device_id for ip in kept if ip.device_id}
    dev_names: dict[Any, str] = {}
    if dev_ids:
        drows = (await session.execute(
            select(Device.id, Device.name).where(in_values(Device.id, dev_ids))
        )).all()
        dev_names = {d[0]: d[1] for d in drows}
    items = [{
        "ip": str(ip.ip).split("/")[0],
        "hostname": ip.hostname,
        "device": dev_names.get(ip.device_id) if ip.device_id else None,
        "ssh": bool(ip.ssh_enabled),
        "rdp": bool(ip.rdp_enabled),
        "vnc": bool(ip.vnc_enabled),
    } for ip in kept]
    return {"items": items, "count": len(items)}


async def list_ai_findings(
    session: AsyncSession, user: User, *, severity: str | None = None, limit: int = 20,
) -> dict[str, Any]:
    """AI 巡檢的未處理發現。

    這些是模型自己的推測，不是查核過的事實 —— 一併回傳 `evidence`，讓對話端有依據
    可以轉述，而不是把推測講成結論。
    """
    from app.models.ai_finding import AIFinding
    stmt = select(AIFinding).where(AIFinding.status == "open")
    if severity in ("low", "medium", "high"):
        stmt = stmt.where(AIFinding.severity == severity)
    rows = (await session.execute(
        stmt.order_by(AIFinding.created_at.desc()).limit(max(1, min(int(limit), 100)))
    )).scalars().all()
    return {"note": "These are AI inferences, not verified facts.",
            "findings": [{
                "severity": f.severity, "category": f.category, "title": f.title,
                "detail": f.detail, "recommendation": f.recommendation,
                "evidence": f.evidence,
                "found_at": f.created_at.isoformat() if f.created_at else None,
            } for f in rows]}

async def list_anomalies(
    session: AsyncSession, user: User, *, kind: str | None = None, limit: int = 20,
) -> dict[str, Any]:
    """異常偵測結果（IP 衝突／MAC 變動／失聯 IP／未授權 IP／非法 DHCP）。

    與 AI 巡檢不同：這裡是量到的事實（ARP 真的看到兩個 MAC），不是模型的推測。
    對話端可以直接轉述，不必加「可能」。

    偵測是即時算出來的（沒有結果表）。這裡**逐條呼叫偵測函式，不走 run_detection** ——
    那支除了發通知（使用者在對話裡問一句，不該讓全體管理員收到信）之外，結尾還會無條件
    session.commit()，會把同一個 session 裡其他未定的異動一起送出去。查詢就只該查詢。
    """
    from app.services import anomaly as _an
    detectors = {
        "ip_conflicts": _an.detect_ip_conflicts,
        # 2026-10-09：同一台主機多張網卡回應同一個 IP（從 IP 衝突分出來）、兩個子網段混在同一個二層
        "arp_flux": _an.detect_arp_flux,
        "l2_subnet_bleed": _an.detect_l2_subnet_bleed,
        "mac_drifts": _an.detect_mac_drifts,
        "ghost_ips": _an.detect_ghost_ips,
        "unauthorized_ips": _an.detect_unauthorized_ips,
        "rogue_dhcp": _an.detect_rogue_dhcp,
        "external_exposure": _an.detect_external_exposure,
        "dangling_dns": _an.detect_dangling_dns,
        "dns_compare_mismatch": _an.detect_dns_compare_mismatch,   # 2026-10-10：DNS 比對群組各台不一樣
        "duplicate_ip_records": _an.detect_duplicate_ip_records,
        "suspicious_changes": _an.detect_suspicious_changes,
        "fw_rule_rot": _an.detect_fw_rule_rot,      # 原本漏掉 → AI 問不到規則劣化
        # 這三類也曾經漏掉（2026-10-01 補）：畫面上有、AI 問不到
        "arp_only_liveness": _an.detect_arp_only_liveness,
        "stale_device_links": _an.detect_stale_device_links,
        "mac_flapping": _an.detect_mac_flapping,
        "identity_changes": _an.detect_identity_changes,
    }
    n = max(1, min(int(limit), 100))
    if kind:
        key = kind.strip().lower()
        if key not in detectors:
            return {"error": f"unknown kind: {kind}", "available": sorted(detectors)}
        detectors = {key: detectors[key]}
    buckets = {k: list(await fn(session)) for k, fn in detectors.items()}
    total = sum(len(v) for v in buckets.values())
    # 一疊 0 對小模型來說不夠清楚 —— GitHub issue #34：查無結果時模型自己編了兩個
    # 根本不在這套 IPAM 裡的位址（連 MAC 都是示範用的 VMware 前綴）。事實由查詢決定、
    # 模型只負責敘述，所以把「沒有就是沒有」寫進**工具輸出**，不要指望提示詞。
    note = (
        "No anomalies were detected. Say exactly that. There is nothing to list — "
        "do NOT invent example IPs, MACs, hostnames or subnets to illustrate."
        if total == 0 else
        "Report only the items listed here, copied verbatim. Every IP, MAC and hostname "
        "in your answer must appear in this result."
    )
    out: dict[str, Any] = {
        "total": total,
        "counts": {k: len(v) for k, v in buckets.items()},
        # 指定單一 kind 時 items 直接是陣列（附 kind）；沒指定時是 {kind: [...]}。以前一律是物件，
        # 用戶端以為是陣列、拿到長度 1 或 0，就誤判成「有統計沒明細」（2026-10-09 回饋）
        "items": {k: v[:n] for k, v in buckets.items()},
    }
    if kind:
        only = next(iter(buckets))
        out["kind"] = only
        out["items"] = buckets[only][:n]
    if "ip_conflicts" in buckets:
        # GitHub issue #41：偵測器沒有資料時，模型把空結果講成「系統中沒有任何已記錄的 IP 衝突」。
        # 「沒有依據」與「看過了、沒有衝突」要分得開，而且結果是**此刻**的狀態、不是歷史。
        cov = await _an.ip_conflict_coverage(session)
        out["coverage"] = {"ip_conflicts": cov}
        scope = (f"IP conflicts are current state only: ARP observations from the last "
                 f"{cov['window_minutes']} minutes and MAC changes from the last "
                 f"{cov['flip_window_hours']} hours. They are not a history of past conflicts.")
        if cov["observations"] == 0 and not buckets["ip_conflicts"]:
            blind = ("There was no ARP evidence at all in that window (no LibreNMS, scan agent "
                     "or firewall ARP observations), so whether any IP conflict exists cannot be "
                     "determined. Say exactly that — do NOT say there are no IP conflicts.")
            if total == 0:
                # 「沒有偵測到異常，照這樣講」與「不可以說沒有衝突」不能同時出現
                others = [k for k in buckets if k != "ip_conflicts"]
                note = ((f"No anomalies were detected in: {', '.join(others)}. " if others else "")
                        + "Do NOT invent example IPs, MACs, hostnames or subnets. "
                        + blind + " " + scope)
            else:
                note = f"{note} {blind} {scope}"
        else:
            note = f"{note} {scope}"
    out["note"] = note
    return out


async def investigate_ip(
    session: AsyncSession, user: User, *, ip: str,
) -> dict[str, Any]:
    """把一個位址散落在各處的線索收成一份檔案（只回事實，不做推論）。

    可見性由 collect_dossier 依子網路授權處理；全域基礎設施那幾段（NAT／防火牆／DNS／非法 DHCP）
    只有具全域讀取權限者才會拿到，探測、異常、AI 巡檢與主控台連線只有管理員才會拿到
    （與對應的 REST 端點同一層，不會因為走 AI 對話就鬆一級）。
    """
    from app.services.investigate import collect_dossier
    return await collect_dossier(session, user=user, ip=str(ip).strip())



async def check_ip_exposure(session: AsyncSession, user: User, ip: str) -> dict[str, Any]:
    """這個位址對外開了什麼 —— NAT 轉發與防火牆放行規則。

    「調查」畫面本來就把這些湊在一起，但只有人點進去才看得到。使用者真正會問的是
    「192.0.2.10 有對外開放嗎、開了哪些埠」，那是一句話的問題，不該要人先知道
    要去哪一頁、再自己讀四張表。

    只回事實：有哪些 NAT 轉發、哪些防火牆規則允許進入。**不下「安全或不安全」的結論**
    —— 那取決於這台機器本來就該不該對外，而那件事只有人知道。
    """
    from app.services.investigate import collect_dossier

    d = await collect_dossier(session, user=user, ip=ip)
    if not d.get("found"):
        return {"found": False, "ip": ip}
    nat = d.get("nat") or []
    # 檔案裡的鍵是 firewall_rules、主機名稱在 address 底下（以前讀 firewall／hostname，永遠是空的）
    fw = [r for r in (d.get("firewall_rules") or []) if str(r.get("action", "")).lower() == "pass"]
    ports = sorted({str(n.get("port")) for n in nat if n.get("port")}
                   | {str(r.get("port")) for r in fw if r.get("port")})
    return {
        "found": True,
        "ip": ip,
        "hostname": (d.get("address") or {}).get("hostname"),
        # 有 NAT 轉發＝從外網打得到；沒有不代表安全（可能走反向代理或另一條路徑）
        "reachable_from_wan": bool(nat),
        "open_ports": ports,
        "nat_rules": nat,
        "firewall_allow_rules": fw,
        "note": ("Facts only. NAT forwards and pass rules are listed; whether that is"
                 " appropriate depends on what this host is meant to do."),
    }


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
    "mac_history": {
        "fn": mac_history,
        "description": (
            "Everything known about one MAC address: every IP it used (first/last seen, still in use), "
            "when it was replaced and by which MAC, switch ports it appeared on, DHCP reservations, the device "
            "or VM it belongs to, and other MACs that are probably the same device (random/private MAC rotation). "
            "Use when the user has a MAC and asks which IPs it used, where it is, or its history."
        ),
        "parameters": {
            "type": "object",
            "properties": {"mac": {"type": "string", "description": "MAC address in any common format"}},
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
            "List racks and shelving units (機櫃／層架) with location, device count, "
            "total/used/free rows, and each mounted device's position: u_position/u_size "
            "plus rack_slot/rack_slot_span (side by side) and rack_vslot/rack_vslot_span "
            "(stacked within one row), on a 60-cell grid. kind tells you the type "
            "(rack / industrial / lackrack = U-based; shelf / wire_shelf / wood_shelf / "
            "angle_shelf = slotted angle steel shelving / kallax = IKEA KALLAX cube unit are "
            "shelf kinds). Shelf kinds are counted "
            "in LEVELS not U — use rows_label, and placeable_rows (shelves can also take "
            "devices on top of the highest board). A row may hold several devices, so check "
            "rows_with_space before saying a row is full."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                "location_id": {"type": "string", "description": "Restrict to one location (機房); otherwise all racks"},
            },
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
            "rack row position/size (u_position, u_size, rack_face), its position WITHIN "
            "that row (rack_slot/rack_slot_span side by side, rack_vslot/rack_vslot_span "
            "stacked, on a 60-cell grid; 0/60 means the whole row) and rack_id. On shelving "
            "units a row is a LEVEL, not a U — call list_racks for the rack's kind."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "type": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                "location_id": {"type": "string", "description": "Restrict to one location (機房)"},
                "rack_id": {"type": "string", "description": "Restrict to one rack"},
            },
        },
    },
    "get_device": {
        "fn": get_device,
        "description": (
            "Device details by id or name: IPs, VLANs (via LibreNMS), its rack row "
            "position/size (u_position, u_size, rack_face), its position within that row "
            "(rack_slot/rack_slot_span, rack_vslot/rack_vslot_span on a 60-cell grid) and "
            "the rack it is mounted in (the rack's kind and uses_levels fields say whether "
            "rows are levels or U)."
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
        "description": "List NAT rules (NAT 規則). If the question is about one subnet/CIDR you MUST pass subnet_cidr, otherwise the answer covers the whole system. The reply carries 'scope' and 'count'; state them.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                "subnet_cidr": {"type": "string", "description": "Restrict to this subnet, e.g. 198.51.100.0/24"},
                "subnet_id": {"type": "string"},
            },
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
    "list_connection_targets": {
        "fn": list_connection_targets,
        "description": (
            "List IPs/devices that have a browser remote console enabled (SSH / RDP / VNC) and "
            "that you may connect to. Read-only — returns ip, hostname, device and which of "
            "ssh/rdp/vnc are enabled; NEVER returns credentials. Optional protocol=ssh|rdp|vnc "
            "to filter to one transport."
        ),
        "parameters": {"type": "object", "properties": {
            "protocol": {"type": "string", "enum": ["ssh", "rdp", "vnc"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
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
    "check_ip_exposure": {
        "fn": check_ip_exposure,
        "description": ("Whether an IP is reachable from the internet and which ports are open:"
                        " NAT port-forwards plus firewall pass rules that target it."
                        " Use for questions like 'is 10.0.0.5 exposed?' or 'what ports are open on X?'."),
        "parameters": {"type": "object", "properties": {"ip": {"type": "string"}},
                       "required": ["ip"]},
    },
    "get_ip_history": {
        "fn": get_ip_history,
        "description": "Forensic timeline for one IP: field-level change log, ARP MAC bindings, per-source hostname observations, DHCP sightings. Answers 'who was this IP on that day'.",
        "parameters": {"type": "object", "properties": {
            "ip": {"type": "string"},
            "days": {"type": "integer", "description": "lookback window, default 30, max 365"}},
            "required": ["ip"]},
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
        "description": ("List all firewalls across vendors — OPNsense, pfSense and FortiGate "
                        "(no secrets). Each entry carries a `vendor` field."),
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 200}}},
    },
    "list_dhcp_ranges": {
        "fn": list_dhcp_ranges,
        "description": ("List DHCP pool ranges synced from the firewall / DHCP integrations "
                        "(OPNsense, pfSense, FortiGate, Windows DHCP). Use this to tell whether "
                        "an address falls inside a DHCP pool — do not guess from the subnet."),
        "parameters": {"type": "object", "properties": {"subnet_cidr": {"type": "string", "description": "Restrict to this subnet, e.g. 198.51.100.0/24"}, "subnet_id": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_fortigate_policies": {
        "fn": list_fortigate_policies,
        "description": "List FortiGate firewall policies. Filter by firewall_name and/or vdom.",
        "parameters": {"type": "object", "properties": {
            "firewall_name": {"type": "string"}, "vdom": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_fortigate_addresses": {
        "fn": list_fortigate_addresses,
        "description": ("List FortiGate address objects and address groups. "
                        "Filter by firewall_name and/or vdom."),
        "parameters": {"type": "object", "properties": {
            "firewall_name": {"type": "string"}, "vdom": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_paloalto_policies": {
        "fn": list_paloalto_policies,
        "description": ("List Palo Alto (PAN-OS) security policies in evaluation order. "
                        "Includes the App-ID, which is what a PAN-OS rule actually matches on. "
                        "Filter by firewall_name and/or vsys."),
        "parameters": {"type": "object", "properties": {
            "firewall_name": {"type": "string"}, "vsys": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_paloalto_addresses": {
        "fn": list_paloalto_addresses,
        "description": ("List Palo Alto address objects and address groups. "
                        "Filter by firewall_name and/or vsys."),
        "parameters": {"type": "object", "properties": {
            "firewall_name": {"type": "string"}, "vsys": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_checkpoint_rules": {
        "fn": list_checkpoint_rules,
        "description": ("List Check Point access rules (from the management server) in evaluation order. "
                        "When source_negate/destination_negate is true the field means 'anything except these'. "
                        "Filter by server_name and/or layer."),
        "parameters": {"type": "object", "properties": {
            "server_name": {"type": "string"}, "layer": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_checkpoint_objects": {
        "fn": list_checkpoint_objects,
        "description": ("List Check Point network objects (hosts, networks, ranges, groups, groups with exclusion). "
                        "Filter by server_name and/or a name fragment."),
        "parameters": {"type": "object", "properties": {
            "server_name": {"type": "string"}, "name": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_mikrotik_rules": {
        "fn": list_mikrotik_rules,
        "description": ("List MikroTik RouterOS firewall rules in evaluation order "
                        "(RouterOS matches top-down; the order is the semantics). "
                        "Filter by router_name and/or table (filter|nat|mangle)."),
        "parameters": {"type": "object", "properties": {
            "router_name": {"type": "string"},
            "table": {"type": "string", "enum": ["filter", "nat", "mangle"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_mikrotik_address_lists": {
        "fn": list_mikrotik_address_lists,
        "description": ("List MikroTik address-list entries (RouterOS's equivalent of "
                        "aliases; one row per address). Filter by router_name and/or list_name."),
        "parameters": {"type": "object", "properties": {
            "router_name": {"type": "string"}, "list_name": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
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
    "investigate_ip": {
        "fn": investigate_ip,
        "description": "Everything known about one IP address in one place: the record, "
                       "other records for the same address in overlapping subnets, what "
                       "each source reports as its hostname and OS, device type and how it "
                       "was decided, NIC vendor, monitoring (Wazuh, LibreNMS, Zabbix), "
                       "endpoint agents (OCS inventory, RustDesk), the matching VM or "
                       "container, DHCP reservations/leases/pool, the switch ports its MAC "
                       "was learned on, firewall ARP/VPN/lease evidence, last seen by "
                       "source, ARP history, recent changes, and a computed list of "
                       "contradictions (conflicts). Global readers also get DNS, NAT, "
                       "firewall rules/objects on every vendor and rogue DHCP sightings; "
                       "admins also get the latest identify probe, open anomalies, AI "
                       "findings and recent console sessions. Facts only, no inference.",
        "parameters": {
            "type": "object",
            "properties": {"ip": {"type": "string", "description": "IPv4 or IPv6"}},
            "required": ["ip"],
        },
    },
    "list_anomalies": {
        "fn": list_anomalies,
        "description": "Anomaly detection results (measured facts, not AI inference): IP "
                       "conflicts, ARP flux (one host answering an IP on several NICs), two subnets "
                       "sharing one layer-2 segment, MAC drifts, ghost IPs, unauthorised IPs, rogue DHCP "
                       "servers, and externally exposed hosts (NAT / WAN firewall rules, cross-checked "
                       "against liveness, monitoring coverage and DNS). Result shape: {total, counts: "
                       "{kind: n}, items, note}. Without `kind`, items is an object {kind: [rows]}; with "
                       "`kind`, items is the array of rows for that kind and `kind` is echoed. In "
                       "ip_conflicts / arp_flux rows each MAC carries `reporters` (which device's ARP "
                       "table, interface, last seen), `reporter_count` and `suspect` (\"corrupt\" = a "
                       "MAC spliced from two others by a half-read SNMP walk, \"stale_cache\" = a "
                       "single-source locally administered MAC contradicting a corroborated one); "
                       "suspect MACs are listed but not counted as machines. arp_flux rows include the "
                       "host, which NIC each MAC belongs to and the sysctl fix.",
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "description":
                         "ip_conflicts | arp_flux | l2_subnet_bleed | mac_drifts | ghost_ips | unauthorized_ips | "
                         "rogue_dhcp | external_exposure | dangling_dns | dns_compare_mismatch | duplicate_ip_records | suspicious_changes | "
                         "fw_rule_rot | arp_only_liveness | stale_device_links | mac_flapping | "
                         "identity_changes (device type or OS family changed)"},
                "limit": {"type": "integer", "description": "max items per kind (default 20)"},
            },
        },
    },
    "list_ai_findings": {
        "fn": list_ai_findings,
        "description": "Open findings from the scheduled AI inventory review: severity, category, "
                       "title, detail, recommendation and the evidence the model cited. These are "
                       "the model's own inferences, not verified facts -- always present them as "
                       "such and cite the evidence when relaying them.",
        "parameters": {"type": "object", "properties": {
            "severity": {"type": "string", "enum": ["low", "medium", "high"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100}}},
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
        "description": "Switch FDB entries (MAC↔port/VLAN). Filter by mac. If the question is about one subnet/CIDR you MUST pass subnet_cidr, otherwise the answer covers the whole system. The reply carries 'scope' and 'count'; state them.",
        "parameters": {"type": "object", "properties": {"subnet_cidr": {"type": "string", "description": "Restrict to this subnet, e.g. 198.51.100.0/24"}, "subnet_id": {"type": "string"},
            "mac": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "wazuh_missing_agents": {
        "fn": wazuh_missing_agents,
        "description": ("IPs that have a hostname but no active Wazuh agent (security coverage gap). "
                        "If the question is about one subnet/CIDR, you MUST pass subnet_cidr "
                        "(e.g. '198.51.100.0/24') — otherwise the result covers every subnet the "
                        "Wazuh integration is limited to (scope 'integration_scope'), or the whole "
                        "system (scope 'all') when it has no limit, and answering with it would be "
                        "wrong. The reply includes 'scope'; state it."),
        "parameters": {"type": "object", "properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            "subnet_cidr": {"type": "string", "description": "Restrict to this subnet, e.g. 198.51.100.0/24"},
            "subnet_id": {"type": "string"}}},
    },
    "list_attack_surface": {
        "fn": list_attack_surface,
        "description": ("Externally reachable services (the attack surface): IP:port entries "
                        "with their IPAM identity and the DNS names that resolve to them. "
                        "Ask by name with fqdn='meet.example.net' (resolved through synced DNS "
                        "records) or by address with ip=. The reply carries 'scope' and 'count'; "
                        "state them. Entries marked registered=false point at hosts IPAM does "
                        "not know — that is itself a finding worth reporting."),
        "parameters": {"type": "object", "properties": {
            "fqdn": {"type": "string", "description": "Restrict to services reachable via this FQDN"},
            "ip": {"type": "string", "description": "Restrict to this address"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "get_customer_summary": {
        "fn": get_customer_summary,
        "description": "Counts of sections/subnets/devices/IPs for a customer. Provide customer_id or name.",
        "parameters": {"type": "object", "properties": {
            "customer_id": {"type": "string"}, "name": {"type": "string"}}},
    },
    "list_vms": {
        "fn": list_vms,
        "description": "List virtual machines (synced from Proxmox VE etc.). If the question is about one subnet/CIDR you MUST pass subnet_cidr, otherwise the answer covers the whole system. The reply carries 'scope' and 'count'; state them.",
        "parameters": {"type": "object", "properties": {"subnet_cidr": {"type": "string", "description": "Restrict to this subnet, e.g. 198.51.100.0/24"}, "subnet_id": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
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
            "cidrs": {"type": "string", "description": "e.g. '192.168.0.0/24, 198.51.100.0/24'"}},
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
        "parameters": {"type": "object", "properties": {"rack_id": {"type": "string", "description": "Restrict to one rack"}, "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_ocs_computers": {
        "fn": list_ocs_computers,
        "description": "List computers inventoried by OCS Inventory (OS, asset tag, agent version, last inventory time, notes). OCS matches machines to existing IPs by network-card MAC. If the question is about one subnet/CIDR you MUST pass subnet_cidr, otherwise the answer covers the whole system. Use stale_days=N to find assets not inventoried for N days. The reply carries 'scope' and 'count'; state them.",
        "parameters": {"type": "object", "properties": {"subnet_cidr": {"type": "string", "description": "Restrict to this subnet, e.g. 198.51.100.0/24"}, "subnet_id": {"type": "string"}, "stale_days": {"type": "integer", "minimum": 0, "description": "Only computers not inventoried for this many days"}, "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_rustdesk_audit": {
        "fn": list_rustdesk_audit,
        "description": "RustDesk connection audit reported by the RustDesk clients themselves: who (peer RustDesk ID, name, IP) connected to which device and when (kind=conn, action new/auth/close; conn_type 0 desktop, 1 file transfer, 2 port forward, 3 camera, 4 terminal), file transfers (kind=file) and alarms (kind=alarm; alarm_type 1 = over 30 wrong passwords, 2 = 6 wrong passwords within a minute, 6 = too many from one IPv6 prefix, 0/10 = IP/ID allowlist violation). Default window is the last 168 hours. If the question is about one subnet/CIDR you MUST pass subnet_cidr. The reply carries 'scope', 'since' and 'count'; state them.",
        "parameters": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["conn", "file", "alarm", "note"]}, "q": {"type": "string", "description": "Search device or peer RustDesk ID, peer name, hostname"}, "hours": {"type": "integer", "minimum": 1, "maximum": 9600}, "subnet_cidr": {"type": "string", "description": "Restrict to devices mapped to this subnet, e.g. 198.51.100.0/24"}, "subnet_id": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_rustdesk_peers": {
        "fn": list_rustdesk_peers,
        "description": "List devices registered on the RustDesk Server (open source): RustDesk ID, online now, last time seen online, the IP the server saw, and the jt-ipam IP record it maps to (hostname comes from that record; RustDesk itself does not store hostnames or OS). If the question is about one subnet/CIDR you MUST pass subnet_cidr (only devices mapped to that subnet are listed). The reply carries 'scope' and 'count'; state them.",
        "parameters": {"type": "object", "properties": {"subnet_cidr": {"type": "string", "description": "Restrict to devices mapped to this subnet, e.g. 198.51.100.0/24"}, "subnet_id": {"type": "string"}, "online": {"type": "boolean", "description": "Only online (true) or offline (false) devices"}, "q": {"type": "string", "description": "Search RustDesk ID, hostname or IP"}, "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_wazuh_agents": {
        "fn": list_wazuh_agents,
        "description": "List Wazuh agents (status, OS, version, CVE critical/high counts). For the coverage GAP use wazuh_missing_agents instead. If the question is about one subnet/CIDR you MUST pass subnet_cidr, otherwise the answer covers the whole system. The reply carries 'scope' and 'count'; state them.",
        "parameters": {"type": "object", "properties": {"subnet_cidr": {"type": "string", "description": "Restrict to this subnet, e.g. 198.51.100.0/24"}, "subnet_id": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
}


# ─────────────────── AI 對話：異動類工具需使用者確認 ───────────────────
# 這些工具會新增 / 修改 / 刪除資料；AI 對話中不直接執行，先回前端請使用者按「確認」。
from app.mcp.impact_tools import IMPACT_TOOLS  # noqa: E402 -- 工具字典建好之後才併入

TOOLS.update(IMPACT_TOOLS)
# ISOinsight 來源租約（唯讀、逐物件權限：工具內依可見子網路過濾）
from app.mcp.isoinsight_tools import ISOINSIGHT_TOOLS  # noqa: E402

TOOLS.update(ISOINSIGHT_TOOLS)

MUTATING_TOOLS: frozenset[str] = frozenset({
    "allocate_ip", "update_ip", "create_subnet", "create_device",
    "approve_ip_request", "reject_ip_request",
    # IP 變更評估：建立計畫、開始分析、存 AI 草擬的待辦（都不改來源資料，但會寫入計畫）
    "impact_create_plan", "impact_start_run", "impact_accept_task_draft",
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
    "list_vms", "list_wireless_links", "list_vpn_tunnels",
    "list_arp", "list_fdb", "list_circuits", "list_providers", "list_asns",
    "list_tenants", "list_contacts", "list_ssids", "list_cables", "cable_trace",
    "list_power",
    # get_topology 不在這裡（2026-10-09）：與 REST /topology 相同，只被授權部分物件的帳號也看得到
    # 自己範圍內的那一塊（build_topology 的 scope_limited）
    "list_attack_surface",
    "list_dhcp_ranges", "list_fortigate_policies", "list_fortigate_addresses",
    "list_paloalto_policies", "list_paloalto_addresses",
    "list_checkpoint_rules", "list_checkpoint_objects",
    "list_mikrotik_rules", "list_mikrotik_address_lists",
    # NAT 與防火牆規則是全域基礎設施資料 —— 與 list_nat / list_firewall_rules 同一層，
    # 不能因為它是「以 IP 為單位查」就鬆一級（改端點權限時要同步收 MCP，這裡踩過）。
    "check_ip_exposure",
})


# 僅管理員可呼叫的唯讀工具。
# 對應 CLAUDE.md 的「純管理資料」分類：AI 巡檢結論與異常偵測清單本身就是一份跨部門的
# 弱點盤點（哪些網段沒監測、哪裡有未授權裝置），REST 端已是 require_admin。
# MCP 是同一份資料的另一道門，鎖不一樣就等於沒鎖 —— 這個專案在 get_topology 踩過一次。
ADMIN_TOOLS: frozenset[str] = frozenset({
    "list_ai_findings", "list_anomalies",
    # 這幾個讀的資料在 REST 上都只給 admin（掃描代理、憑證與派送、Wazuh 代理與缺口、OCS 電腦）；
    # 以前放在全域讀取，網頁打不開的資料在 AI 對話裡問得到（2026-09-30 盤點 API 手冊時發現）。
    # tests/test_mcp_tools_match_rest_permissions.py 守著：工具不可以比對應的 REST 端點寬
    "list_scan_agents", "list_certificates", "list_cert_distribution",
    "list_wazuh_agents", "wazuh_missing_agents", "list_ocs_computers",
    # RustDesk 整合頁（REST /rustdesk）只給 admin
    "list_rustdesk_peers", "list_rustdesk_audit",
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
    - 管理資料唯讀工具（巡檢／異常）：需 admin
    - 全域基礎設施工具：需 admin 或萬用讀取
    - 其餘（逐物件資料）：需至少有可見範圍；工具內部再依 visible_ids 過濾
    """
    if name in UTILITY_TOOLS:
        return None
    if name.startswith("impact_"):
        # IP 變更評估關閉時，工具清單裡也不出現（少佔小模型的提示詞）
        from app.services.change_impact.config import get_config
        if not (await get_config(session))["enabled"]:
            return "feature_disabled: IP 變更評估尚未啟用。"
    if name in MUTATING_TOOLS and not getattr(user, "is_admin", False):
        return "permission_denied: 此操作需要管理員權限。"
    if name in ADMIN_TOOLS and not getattr(user, "is_admin", False):
        return "permission_denied: 此為管理資料，僅限管理員檢視。"
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
    if name == "impact_create_plan":
        return f"建立 IP 變更評估計畫「{a.get('title') or ''}」（只建立計畫，不改任何設備）"
    if name == "impact_start_run":
        return "開始一次 IP 變更評估的分析（唯讀）"
    if name == "impact_accept_task_draft":
        return f"把 {len(a.get('indices') or [])} 個 AI 草擬的待辦存進計畫"
    return f"執行 {name}"
