"""Device endpoints。"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin
from app.core.audit import append_audit
from app.core.db import get_session
from app.models.device import Device
from app.schemas.base import Paginated
from app.schemas.device import DeviceCreate, DeviceRead, DeviceUpdate
from app.services.custom_field import CustomFieldError, validate_custom_fields

router = APIRouter(prefix="/devices", tags=["devices"])


@router.get("", response_model=Paginated[DeviceRead])
async def list_devices(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    type: str | None = Query(None),
    location_id: uuid.UUID | None = Query(None),
    rack_id: uuid.UUID | None = Query(None),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=500),
) -> Paginated[DeviceRead]:
    stmt = select(Device)
    cstmt = select(func.count()).select_from(Device)
    if type is not None:
        stmt = stmt.where(Device.type == type); cstmt = cstmt.where(Device.type == type)
    if location_id is not None:
        stmt = stmt.where(Device.location_id == location_id)
        cstmt = cstmt.where(Device.location_id == location_id)
    if rack_id is not None:
        stmt = stmt.where(Device.rack_id == rack_id); cstmt = cstmt.where(Device.rack_id == rack_id)
    stmt = stmt.order_by(Device.name).offset((page - 1) * page_size).limit(page_size)
    rows = list((await session.execute(stmt)).scalars().all())
    total = int(await session.scalar(cstmt) or 0)
    return Paginated[DeviceRead](
        items=[DeviceRead.model_validate(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.get("/{device_id}", response_model=DeviceRead)
async def get_device(
    device_id: uuid.UUID,
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DeviceRead:
    obj = await session.get(Device, device_id)
    if obj is None:
        raise HTTPException(404, detail="Device not found")
    return DeviceRead.model_validate(obj)


@router.post("", response_model=DeviceRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_device(
    payload: DeviceCreate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DeviceRead:
    try:
        cf = await validate_custom_fields(
            session, object_type="device", payload=payload.custom_fields
        )
    except CustomFieldError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    data = payload.model_dump()
    data["custom_fields"] = cf or None
    obj = Device(**data)
    session.add(obj)
    await session.flush()
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="device", object_id=str(obj.id), action="create",
        diff={"after": payload.model_dump(mode="json")},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return DeviceRead.model_validate(obj)


@router.patch("/{device_id}", response_model=DeviceRead,
              dependencies=[Depends(require_admin)])
async def update_device(
    device_id: uuid.UUID,
    payload: DeviceUpdate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DeviceRead:
    obj = await session.get(Device, device_id)
    if obj is None:
        raise HTTPException(404, detail="Device not found")
    before = {"name": obj.name, "type": obj.type, "vendor": obj.vendor, "model": obj.model}
    changes = payload.model_dump(exclude_unset=True)
    if "custom_fields" in changes:
        try:
            changes["custom_fields"] = await validate_custom_fields(
                session, object_type="device", payload=changes["custom_fields"]
            ) or None
        except CustomFieldError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    for k, v in changes.items():
        setattr(obj, k, v)
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="device", object_id=str(obj.id), action="update",
        diff={"before": before, "changes": changes},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return DeviceRead.model_validate(obj)


@router.delete("/{device_id}", status_code=204, dependencies=[Depends(require_admin)])
async def delete_device(
    device_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    obj = await session.get(Device, device_id)
    if obj is None:
        raise HTTPException(404, detail="Device not found")
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="device", object_id=str(obj.id), action="delete",
        diff={"before": {"name": obj.name, "type": obj.type}},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.delete(obj)
    await session.commit()
