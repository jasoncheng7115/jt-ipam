"""對外路徑（客戶許願清單 2026-10-28）：網際網路 → WAN IP → 防火牆 → [手動插入的設備] → 交換器埠 → 內部主機。

自動推的部分（每一段都是有資料才出現，推不出來就不畫，不猜）：
- WAN IP 與外部埠：NAT 規則的外部位址（沒有位址時寫介面，例如 wan）；承載它的電路（電路關聯的 WAN 位址）
- 防火牆：NAT 規則從哪台防火牆同步來的
- 交換器與接入埠：內部主機 MAC 在交換器 MAC 表上「看到 MAC 最少」的埠（跟戰情室 WARNING 的位置同一套）
- 內部主機：IP:內部埠、所屬裝置、或是哪個節點上的 VM
手動插入的 WAF、負載平衡器、反向代理…掛在內部主機的 IP 上，放在防火牆與交換器之間。
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.address import IPAddress
from app.models.device import Device
from app.models.ip_path import IPPathHop
from app.models.nat import NATTranslation

MAX_PATHS = 50


def _ports(a: int | None, b: int | None) -> str | None:
    if not a:
        return None
    return f"{a}-{b}" if b and b != a else str(a)


def _host(ip: Any) -> str:
    return str(ip).split("/")[0]


async def manual_hops(session: AsyncSession, address_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (await session.execute(
        select(IPPathHop, Device.name).outerjoin(Device, Device.id == IPPathHop.device_id)
        .where(IPPathHop.address_id == address_id).order_by(IPPathHop.position, IPPathHop.created_at))).all()
    return [{"id": str(h.id), "kind": h.kind, "position": h.position,
             "device": {"id": str(h.device_id), "name": name} if h.device_id else None,
             "label": h.label, "note": h.note} for h, name in rows]


async def _target_hops(session: AsyncSession, target: IPAddress, port: str | None) -> list[dict[str, Any]]:
    """交換器埠＋內部主機（兩段都看內部主機的 MAC / IP）。"""
    from app.services.fw_lookup import vm_match_for
    from app.services.warroom.hints import switch_port_of
    out: list[dict[str, Any]] = []
    mac = str(target.mac) if target.mac else None
    if mac:
        sw = await switch_port_of(session, mac)
        if sw:
            out.append({"kind": "switch", "label": sw["switch"], "port": sw["port"], "vlan": sw.get("vlan"),
                        "auto": True})
    dev = await session.get(Device, target.device_id) if target.device_id else None
    vm = await vm_match_for(session, ip=_host(target.ip), macs=[mac] if mac else None)
    out.append({"kind": "target", "label": _host(target.ip), "port": port, "address_id": str(target.id),
                "hostname": target.hostname,
                "device": {"id": str(dev.id), "name": dev.name} if dev else None,
                "vm": {"name": vm["vm"], "node": vm.get("node"), "cluster": vm.get("cluster")} if vm else None,
                "auto": True})
    return out


async def build_paths(session: AsyncSession, ip: IPAddress) -> list[dict[str, Any]]:
    """這個 IP 參與的每一條對外轉發（它是內部目標，或它是外部位址）各畫一條路徑。停用的 NAT 不算。"""
    from app.mcp.tools import _firewall_name_map, _sync_origin
    from app.services.circuit_links import circuits_carrying
    nats = list((await session.execute(
        select(NATTranslation).where(
            or_(NATTranslation.dst_ip_id == ip.id, NATTranslation.src_ip_id == ip.id),
            NATTranslation.disabled.is_(False),
            NATTranslation.type.in_(("port_forward", "one_to_one")))   # bounded: 兩個常數
        .order_by(NATTranslation.name).limit(MAX_PATHS))).scalars())
    if not nats:
        return []
    fw_names = await _firewall_name_map(session)
    paths: list[dict[str, Any]] = []
    for n in nats:
        target = ip if n.dst_ip_id == ip.id else (await session.get(IPAddress, n.dst_ip_id) if n.dst_ip_id else None)
        ext_ip = ip if n.src_ip_id == ip.id else (await session.get(IPAddress, n.src_ip_id) if n.src_ip_id else None)
        ext_text = _host(ext_ip.ip) if ext_ip else None
        circuits = await circuits_carrying(session, ext_text) if ext_text else []
        vendor, fw = _sync_origin(n.source_origin, fw_names)
        hops: list[dict[str, Any]] = [
            {"kind": "internet", "label": None, "auto": True},
            {"kind": "wan", "label": ext_text or n.src_interface, "port": _ports(n.src_port, n.src_port_to),
             "address_id": str(ext_ip.id) if ext_ip else None, "is_interface": ext_ip is None,
             "circuits": [{"id": str(c.id), "cid": c.cid} for c in circuits], "auto": True},
            {"kind": "firewall", "label": fw, "vendor": None if vendor == "manual" else vendor,
             "nat": {"id": str(n.id), "name": n.name, "protocol": n.protocol}, "auto": True},
        ]
        if target is not None:
            hops += [{**h, "auto": False} for h in await manual_hops(session, target.id)]
            hops += await _target_hops(session, target, _ports(n.dst_port, n.dst_port_to))
        else:
            hops.append({"kind": "target", "label": n.redirect_alias, "port": _ports(n.dst_port, n.dst_port_to),
                         "unresolved": True, "auto": True})
        paths.append({"nat_id": str(n.id), "nat_name": n.name, "protocol": n.protocol, "hops": hops,
                      "editable_address_id": str(target.id) if target is not None else None})
    return paths
