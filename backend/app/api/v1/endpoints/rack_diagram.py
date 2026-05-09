"""機櫃 U 位視覺化用的 endpoint：拿一個機櫃 + 所有設備 + 占位資訊。"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser
from app.core.db import get_session
from app.models.address import IPAddress
from app.models.device import Device
from app.models.location import Rack
from app.schemas.base import StrictModel

router = APIRouter(prefix="/racks", tags=["racks"])


class RackDeviceSlot(StrictModel):
    device_id: uuid.UUID
    name: str
    type: str
    vendor: str | None
    model: str | None
    u_position: int   # bottom-most U (1-based, 1 = 最下面)
    u_size: int
    primary_ip: str | None


class RackDiagram(StrictModel):
    rack_id: uuid.UUID
    name: str
    u_height: int
    location_id: uuid.UUID | None
    devices: list[RackDeviceSlot]
    conflicts: list[dict[str, Any]]    # 同一 U 被多 device 佔用 / 越界


@router.get("/{rack_id}/diagram", response_model=RackDiagram)
async def rack_diagram(
    rack_id: uuid.UUID,
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RackDiagram:
    rack = await session.get(Rack, rack_id)
    if rack is None:
        raise HTTPException(404, detail="Rack not found")

    devices = list(
        (
            await session.execute(
                select(Device)
                .where(Device.rack_id == rack_id)
                .order_by(Device.u_position)
            )
        ).scalars().all()
    )

    # 拼 primary IP（如果有）
    primary_ip_ids = [d.primary_ip_id for d in devices if d.primary_ip_id]
    ip_map: dict[uuid.UUID, str] = {}
    if primary_ip_ids:
        ip_rows = (
            await session.execute(
                select(IPAddress).where(IPAddress.id.in_(primary_ip_ids))
            )
        ).scalars().all()
        for ip in ip_rows:
            ip_map[ip.id] = str(ip.ip).split("/")[0]

    slots: list[RackDeviceSlot] = []
    occupied: dict[int, list[uuid.UUID]] = {}
    conflicts: list[dict[str, Any]] = []

    for d in devices:
        if d.u_position is None or d.u_size is None:
            # 未設定 U 位的設備不畫；給 conflict 報告
            conflicts.append({
                "type": "unpositioned",
                "device_id": str(d.id),
                "name": d.name,
            })
            continue

        # 越界
        if d.u_position < 1 or (d.u_position + d.u_size - 1) > rack.u_height:
            conflicts.append({
                "type": "out_of_bounds",
                "device_id": str(d.id),
                "name": d.name,
                "u_position": d.u_position,
                "u_size": d.u_size,
                "rack_u_height": rack.u_height,
            })
            continue

        # 占位衝突
        for u in range(d.u_position, d.u_position + d.u_size):
            occupied.setdefault(u, []).append(d.id)

        slots.append(
            RackDeviceSlot(
                device_id=d.id,
                name=d.name,
                type=d.type,
                vendor=d.vendor,
                model=d.model,
                u_position=d.u_position,
                u_size=d.u_size,
                primary_ip=ip_map.get(d.primary_ip_id) if d.primary_ip_id else None,
            )
        )

    for u, dids in occupied.items():
        if len(dids) > 1:
            conflicts.append({
                "type": "overlap",
                "u": u,
                "device_ids": [str(x) for x in dids],
            })

    return RackDiagram(
        rack_id=rack.id,
        name=rack.name,
        u_height=rack.u_height,
        location_id=rack.location_id,
        devices=slots,
        conflicts=conflicts,
    )
