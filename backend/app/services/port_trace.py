"""端到端追線（交接包 physical-v1.2 CABLING.md，2026-11-21）。

從一個連接埠出發，沿「已接線」的纜線與跳接面板的正背面配對一路往下走，直到走不下去，並說出為什麼停：

- 段別：cable（實體線；只有它算纜線數）、panel_passthrough（面板正面 ↔ 背面）、logical_bridge（bridge ↔ NIC，
  邏輯對應、不是線）
- 只走已接線的線。規劃中、已除役的不走；同一個埠上有兩條以上已接線的線 → 停在 ambiguous 並列出候選，
  不任選一條（以前 `.limit(1)` 隨便拿一條）
- 從纜線清單出發可指定第一段（first_cable_id），不會默默改走另一條
- 每條線、每個埠只走一次；回到走過的埠 → loop；超過上限（預設 64 段）→ limit
- 看不到的裝置：不回名稱、id，停在 no_permission
- 回應保留舊欄位（start、nodes、hops），另加 segments、stop_reason、stop_port、candidates、cable_count、truncated
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ui_error import ui_detail
from app.models.device import Device
from app.models.physical import Cable, CableTermination, DevicePort
from app.models.user import User
from app.services.permission import visible_ids

MAX_SEGMENTS = 64
PANEL_TYPES = ("front", "rear")


async def trace(session: AsyncSession, user: User, start_id: uuid.UUID, *,
                first_cable_id: uuid.UUID | None = None, max_segments: int = MAX_SEGMENTS) -> dict[str, Any]:
    vis = await visible_ids(session, user=user, object_type="device")

    def visible(dev_id: uuid.UUID) -> bool:
        return vis is None or dev_id in vis

    start = await session.get(DevicePort, start_id)
    if start is None or not visible(start.device_id):
        raise HTTPException(404, detail="Port not found")

    names: dict[uuid.UUID, str] = {}

    async def node(p: DevicePort) -> dict[str, Any]:
        if p.device_id not in names:
            d = await session.get(Device, p.device_id)
            names[p.device_id] = d.name if d else ""
        return {"port_id": str(p.id), "port_name": p.name, "port_type": p.type,
                "device_id": str(p.device_id), "device_name": names[p.device_id]}

    nodes: list[dict[str, Any]] = [await node(start)]
    hops: list[dict[str, Any]] = []
    segments: list[dict[str, Any]] = []
    visited: set[uuid.UUID] = {start.id}
    used: set[uuid.UUID] = set()
    out: dict[str, Any] = {"stop_reason": "end", "stop_port": None, "candidates": None, "truncated": False}

    def add(kind: str, frm: DevicePort, to: dict[str, Any] | None, cable: Cable | None = None) -> None:
        segments.append({"kind": kind, "from_port_id": str(frm.id), "to_port_id": (to or {}).get("port_id"),
                         "cable_id": str(cable.id) if cable else None, "cable_label": cable.label if cable else None,
                         "status": cable.status if cable else None})
        hops.append({"cable_id": str(cable.id) if cable else None, "cable_label": cable.label if cable else None,
                     "cable_type": (cable.type if cable else "internal"), "cable_color": cable.color if cable else None,
                     "internal": cable is None, "kind": kind, "to": to})
        if to is not None:
            nodes.append(to)

    current = start
    first = True
    while True:
        if len(segments) >= max_segments:
            out.update(stop_reason="limit", truncated=True, stop_port=await node(current))
            break
        # bridge：實體上行其實是對應的 NIC，先插一段邏輯對應（纜線仍掛在 bridge 上，由下面續走）
        if current.type == "bridge" and current.peer_port_id and current.peer_port_id not in visited:
            nic = await session.get(DevicePort, current.peer_port_id)
            if nic is not None and nic.device_id == current.device_id:
                visited.add(nic.id)
                add("logical_bridge", current, await node(nic))
                if len(segments) >= max_segments:
                    out.update(stop_reason="limit", truncated=True, stop_port=await node(current))
                    break

        rows = (await session.execute(
            select(CableTermination, Cable).join(Cable, Cable.id == CableTermination.cable_id)
            .where(CableTermination.object_type == "device_port", CableTermination.object_id == current.id)
            .order_by(Cable.label))).all()
        if first and first_cable_id is not None:
            chosen = [(t, c) for t, c in rows if c.id == first_cable_id]
            if not chosen:
                raise HTTPException(422, detail=ui_detail("trace_cable_not_on_port", "這條線沒有接在這個埠上"))
        else:
            chosen = [(t, c) for t, c in rows if c.status == "connected" and c.id not in used]
        first = False

        if len(chosen) > 1:
            out.update(stop_reason="ambiguous", stop_port=await node(current),
                       candidates=[c.label or "" for _t, c in chosen])
            break
        if chosen:
            term, cable = chosen[0]
            used.add(cable.id)
            other = (await session.execute(select(CableTermination).where(
                CableTermination.cable_id == cable.id, CableTermination.id != term.id).limit(1))).scalar_one_or_none()
            if other is None:
                add("cable", current, None, cable)
                out.update(stop_reason="cable_missing_end", stop_port=await node(current))
                break
            if other.object_type != "device_port":
                add("cable", current, {"object_type": other.object_type, "object_id": str(other.object_id),
                                       "port_name": other.port_label}, cable)
                out["stop_reason"] = "external_end"
                break
            far = await session.get(DevicePort, other.object_id)
            if far is None:
                add("cable", current, None, cable)
                out.update(stop_reason="missing_port", stop_port=await node(current))
                break
            if not visible(far.device_id):
                add("cable", current, None, cable)   # 對端看不到：不回名稱、id
                out.update(stop_reason="no_permission", stop_port=await node(current))
                break
            if far.id in visited:
                out.update(stop_reason="loop", stop_port=await node(current))
                break
            visited.add(far.id)
            add("cable", current, await node(far), cable)
            current = far
            continue

        # 沒有可走的線：面板正背面（或其他內部對應）穿透
        if current.peer_port_id and current.peer_port_id not in visited:
            peer = await session.get(DevicePort, current.peer_port_id)
            if peer is not None and visible(peer.device_id):
                visited.add(peer.id)
                add("panel_passthrough" if current.type in PANEL_TYPES else "logical_bridge", current, await node(peer))
                current = peer
                continue
        if current.type in PANEL_TYPES and current.peer_port_id is None:
            out.update(stop_reason="unpaired_panel_port", stop_port=await node(current))
        elif any(c.status != "connected" and c.id not in used for _t, c in rows):
            out.update(stop_reason="no_connected_cable", stop_port=await node(current))
        break

    return {"start": nodes[0], "nodes": nodes, "hops": hops, "segments": segments,
            "cable_count": sum(1 for s in segments if s["kind"] == "cable"), **out}
