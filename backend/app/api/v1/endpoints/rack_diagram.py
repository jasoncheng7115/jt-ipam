"""機櫃 U 位視覺化用的 endpoint：拿一個機櫃 + 所有設備 + 占位資訊。"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_object_perm
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
    rack_face: str | None = None   # front / rear（安裝方向）
    rack_side: str = "full"        # full / left / right（半 U 占寬）


class RackDiagram(StrictModel):
    rack_id: uuid.UUID
    name: str
    u_height: int
    location_id: uuid.UUID | None
    numbering: str = "top-down"
    face: str = "front"
    devices: list[RackDeviceSlot]
    conflicts: list[dict[str, Any]]    # 同一 U 被多 device 佔用 / 越界


@router.get(
    "/{rack_id}/diagram",
    response_model=RackDiagram,
    dependencies=[Depends(require_object_perm("rack", "read", path_param="rack_id"))],
)
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

    # 沒設 primary_ip 的裝置：退而求其次，抓任一掛在該裝置的 IP（tooltip 也能顯示 IP）
    no_primary = [d.id for d in devices if not d.primary_ip_id]
    fallback_ip: dict[uuid.UUID, str] = {}
    if no_primary:
        fb_rows = (
            await session.execute(
                select(IPAddress)
                .where(IPAddress.device_id.in_(no_primary))
                .order_by(IPAddress.ip)
            )
        ).scalars().all()
        for ip in fb_rows:
            if ip.device_id is not None and ip.device_id not in fallback_ip:
                fallback_ip[ip.device_id] = str(ip.ip).split("/")[0]

    slots: list[RackDeviceSlot] = []
    # 占位以 (安裝方向, U) 為 key：前/後同 U 不算衝突（落地機櫃可前後各掛一台）
    # key=(安裝方向, U, 半格 L/R)：full 同時占 L+R；half 只占一邊 → 一左一右同 U 不衝突
    occupied: dict[tuple[str, int, str], list[uuid.UUID]] = {}
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

        # 占位衝突（同安裝方向才算）；半 U 只占一邊，full 占左右兩邊
        face = d.rack_face or "front"
        side = d.rack_side or "full"
        halves = ("L", "R") if side == "full" else ("L" if side == "left" else "R",)
        for u in range(d.u_position, d.u_position + d.u_size):
            for hh in halves:
                occupied.setdefault((face, u, hh), []).append(d.id)

        slots.append(
            RackDeviceSlot(
                device_id=d.id,
                name=d.name,
                type=d.type,
                vendor=d.vendor,
                model=d.model,
                u_position=d.u_position,
                u_size=d.u_size,
                primary_ip=ip_map.get(d.primary_ip_id) if d.primary_ip_id else fallback_ip.get(d.id),
                rack_face=d.rack_face,
                rack_side=side,
            )
        )

    seen_overlap: set[tuple[str, int, frozenset[str]]] = set()
    for (face, u, _hh), dids in occupied.items():
        if len(dids) > 1:
            key = (face, u, frozenset(str(x) for x in dids))
            if key in seen_overlap:
                continue
            seen_overlap.add(key)
            conflicts.append({
                "type": "overlap",
                "u": u,
                "face": face,
                "device_ids": [str(x) for x in dids],
            })

    return RackDiagram(
        rack_id=rack.id,
        name=rack.name,
        u_height=rack.u_height,
        location_id=rack.location_id,
        numbering=rack.numbering,
        face=rack.face,
        devices=slots,
        conflicts=conflicts,
    )
