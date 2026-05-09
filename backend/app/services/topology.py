"""網路拓樸圖：以 device + cabling + LibreNMS FDB（Phase 2 已有）拼出 graph。

回傳 Cytoscape.js 可直接吃的格式：
  {
    "nodes": [{"data": {"id": "...", "label": "...", "type": "..."}}, ...],
    "edges": [{"data": {"source": "...", "target": "...", "label": "..."}}, ...]
  }

邊（edges）來源：
1. 物理 Cable → 兩端 termination（同 cable 兩 termination → 一條邊）
2. WirelessLink（A/B device 都存在時）
3. VPNTunnel（A/B device）
4. Phase 4：LLDP / FDB 推導邏輯邏輯接（目前簡化）
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.advanced import WirelessLink
from app.models.device import Device
from app.models.physical import Cable, CableTermination, VPNTunnel


async def build_topology(
    session: AsyncSession,
    *,
    location_id: uuid.UUID | None = None,
    include_wireless: bool = True,
    include_vpn: bool = True,
) -> dict[str, list[dict[str, Any]]]:
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []

    # ── nodes：所有 device（可選依 location 過濾） ──
    dstmt = select(Device)
    if location_id is not None:
        dstmt = dstmt.where(Device.location_id == location_id)
    devices = list((await session.execute(dstmt)).scalars().all())
    for d in devices:
        nodes[str(d.id)] = {
            "data": {
                "id": str(d.id),
                "label": d.name,
                "type": d.type,
                "vendor": d.vendor,
                "model": d.model,
                "rack_id": str(d.rack_id) if d.rack_id else None,
                "location_id": str(d.location_id) if d.location_id else None,
            }
        }

    visible_device_ids = set(nodes.keys())

    # ── 物理纜線 ──
    cables = list((await session.execute(select(Cable))).scalars().all())
    for cable in cables:
        terms = list((await session.execute(
            select(CableTermination).where(CableTermination.cable_id == cable.id)
        )).scalars().all())
        if len(terms) != 2:
            continue
        a, b = sorted(terms, key=lambda t: t.side)
        # MVP：device-to-device 才畫
        if a.object_type != "device" or b.object_type != "device":
            continue
        sid, tid = str(a.object_id), str(b.object_id)
        if sid not in visible_device_ids or tid not in visible_device_ids:
            continue
        edges.append({
            "data": {
                "id": f"cable:{cable.id}",
                "source": sid, "target": tid,
                "label": cable.label or cable.type or "cable",
                "kind": "cable",
                "type": cable.type,
                "color": cable.color,
                "status": cable.status,
            }
        })

    # ── 無線連線 ──
    if include_wireless:
        wlinks = list((await session.execute(select(WirelessLink))).scalars().all())
        for w in wlinks:
            if not (w.a_device_id and w.b_device_id):
                continue
            sid, tid = str(w.a_device_id), str(w.b_device_id)
            if sid not in visible_device_ids or tid not in visible_device_ids:
                continue
            edges.append({
                "data": {
                    "id": f"wireless:{w.id}",
                    "source": sid, "target": tid,
                    "label": w.ssid or w.name,
                    "kind": "wireless",
                    "ssid": w.ssid,
                    "distance_m": w.distance_m,
                }
            })

    # ── VPN 邏輯連線 ──
    if include_vpn:
        tunnels = list((await session.execute(select(VPNTunnel))).scalars().all())
        for t in tunnels:
            if not (t.a_device_id and t.b_device_id):
                continue
            sid, tid = str(t.a_device_id), str(t.b_device_id)
            if sid not in visible_device_ids or tid not in visible_device_ids:
                continue
            edges.append({
                "data": {
                    "id": f"vpn:{t.id}",
                    "source": sid, "target": tid,
                    "label": t.type,
                    "kind": "vpn",
                    "type": t.type,
                    "status": t.status,
                }
            })

    return {"nodes": list(nodes.values()), "edges": edges}
