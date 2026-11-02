"""IP 的對外路徑（網際網路 → WAN IP → 防火牆 → [手動插入] → 交換器埠 → 內部主機）與手動插入的中間設備。

NAT、防火牆、交換器 MAC 表屬全域基礎設施：要全域讀取，且看得到這個 IP 所在的子網路。
手動插入的設備是這個 IP 的文件：對子網路有修改權就能加、改、刪，留稽核。
"""
from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_global_read
from app.core.audit import append_audit
from app.core.db import get_session
from app.core.ui_error import ui_detail
from app.models.address import IPAddress
from app.models.device import Device
from app.models.ip_path import IPPathHop
from app.schemas.base import StrictModel
from app.services.exposure_path import build_paths, manual_hops
from app.services.permission import get_object_permission, has_permission

router = APIRouter(tags=["addresses"], dependencies=[Depends(require_global_read)])

HopKind = Literal["firewall", "waf", "load_balancer", "proxy", "router", "switch", "other"]


async def _ip_or_404(session: AsyncSession, user: Any, address_id: uuid.UUID, required: str) -> IPAddress:
    ip = await session.get(IPAddress, address_id)
    if ip is None:
        raise HTTPException(404, detail="Not found")
    level = await get_object_permission(session, user=user, object_type="subnet", object_id=ip.subnet_id)
    if not has_permission(level, required):
        raise HTTPException(404, detail="Not found")     # 不透露存在
    return ip


@router.get("/addresses/{address_id}/exposure-path")
async def exposure_path(
    address_id: uuid.UUID, user: CurrentUser, session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    ip = await _ip_or_404(session, user, address_id, "read")
    level = await get_object_permission(session, user=user, object_type="subnet", object_id=ip.subnet_id)
    return {"paths": await build_paths(session, ip), "manual_hops": await manual_hops(session, ip.id),
            "can_edit": has_permission(level, "write")}


class HopWrite(StrictModel):
    kind: HopKind = "other"
    device_id: uuid.UUID | None = None
    label: Annotated[str | None, Field(max_length=128)] = None
    note: Annotated[str | None, Field(max_length=1024)] = None
    position: Annotated[int | None, Field(ge=0, le=1000)] = None


async def _audit(session: AsyncSession, user: Any, request: Request, ip: IPAddress, action: str,
                 diff: dict[str, Any]) -> None:
    await append_audit(
        session, actor_user_id=str(user.id), actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"), object_type="ip_path_hop", object_id=str(ip.id),
        action=action, diff={"ip": str(ip.ip).split("/")[0], **diff},
        request_id=getattr(request.state, "request_id", None))


async def _check_what(session: AsyncSession, device_id: uuid.UUID | None, label: str | None) -> None:
    if device_id is None and not (label or "").strip():
        raise HTTPException(422, detail=ui_detail("path_hop_needs_device_or_label", "請選一台裝置或填名稱"))
    if device_id is not None and await session.get(Device, device_id) is None:
        raise HTTPException(404, detail=ui_detail("device_not_found", "找不到這台裝置"))


@router.post("/addresses/{address_id}/path-hops", status_code=201)
async def add_hop(
    address_id: uuid.UUID, payload: HopWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    ip = await _ip_or_404(session, user, address_id, "write")
    await _check_what(session, payload.device_id, payload.label)
    pos = payload.position
    if pos is None:      # 預設接在最後
        pos = int(await session.scalar(select(func.coalesce(func.max(IPPathHop.position), -1) + 1)
                                       .where(IPPathHop.address_id == ip.id)) or 0)
    hop = IPPathHop(address_id=ip.id, position=pos, kind=payload.kind, device_id=payload.device_id,
                    label=(payload.label or "").strip() or None, note=payload.note)
    session.add(hop)
    await session.flush()
    await _audit(session, user, request, ip, "create", payload.model_dump(mode="json") | {"position": pos})
    await session.commit()
    return {"id": str(hop.id), "position": hop.position}


@router.patch("/addresses/{address_id}/path-hops/{hop_id}")
async def update_hop(
    address_id: uuid.UUID, hop_id: uuid.UUID, payload: HopWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    ip = await _ip_or_404(session, user, address_id, "write")
    hop = await session.get(IPPathHop, hop_id)
    if hop is None or hop.address_id != ip.id:
        raise HTTPException(404, detail="Not found")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(hop, k, (v.strip() or None) if k == "label" and isinstance(v, str) else v)
    await _check_what(session, hop.device_id, hop.label)
    await _audit(session, user, request, ip, "update", payload.model_dump(mode="json", exclude_unset=True))
    await session.commit()
    return {"id": str(hop.id), "position": hop.position}


@router.delete("/addresses/{address_id}/path-hops/{hop_id}", status_code=204)
async def delete_hop(
    address_id: uuid.UUID, hop_id: uuid.UUID, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    ip = await _ip_or_404(session, user, address_id, "write")
    hop = await session.get(IPPathHop, hop_id)
    if hop is None or hop.address_id != ip.id:
        raise HTTPException(404, detail="Not found")
    await _audit(session, user, request, ip, "delete", {"kind": hop.kind, "label": hop.label,
                                                      "device_id": str(hop.device_id) if hop.device_id else None})
    await session.delete(hop)
    await session.commit()
