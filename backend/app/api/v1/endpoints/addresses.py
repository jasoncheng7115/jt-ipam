"""IPAddress CRUD + first_free 配發。"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser
from app.core.audit import append_audit
from app.core.db import get_session
from app.models.address import IPAddress
from app.models.subnet import Subnet
from app.schemas.address import (
    IPAddressAllocate,
    IPAddressCreate,
    IPAddressRead,
    IPAddressUpdate,
)
from app.schemas.base import Paginated
from app.services.address import (
    IPAlreadyExists,
    IPNotInSubnet,
    SubnetFull,
    allocate_first_free,
    assert_in_subnet,
    create_ip,
)
from app.services.custom_field import CustomFieldError, validate_custom_fields
from app.services.permission import (
    filter_visible,
    get_object_permission,
    has_permission,
)

router = APIRouter(prefix="/addresses", tags=["addresses"])


async def _require_subnet_perm(
    session: AsyncSession,
    user,
    subnet_id: uuid.UUID,
    required: str,
) -> Subnet:
    subnet = await session.get(Subnet, subnet_id)
    if subnet is None:
        raise HTTPException(status_code=404, detail="Subnet not found")
    level = await get_object_permission(
        session, user=user, object_type="subnet", object_id=subnet.id
    )
    if not has_permission(level, required):
        # A01：不洩漏存在性
        raise HTTPException(status_code=404, detail="Subnet not found")
    return subnet


@router.get("", response_model=Paginated[IPAddressRead])
async def list_addresses(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    subnet_id: uuid.UUID | None = Query(None),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(100, ge=1, le=1000),
) -> Paginated[IPAddressRead]:
    stmt = select(IPAddress)
    count_stmt = select(func.count()).select_from(IPAddress)
    if subnet_id is not None:
        # 須對該 subnet 有 read 權限
        await _require_subnet_perm(session, user, subnet_id, "read")
        stmt = stmt.where(IPAddress.subnet_id == subnet_id)
        count_stmt = count_stmt.where(IPAddress.subnet_id == subnet_id)

    stmt = stmt.order_by(IPAddress.ip).offset((page - 1) * page_size).limit(page_size)
    rows = list((await session.execute(stmt)).scalars().all())

    if subnet_id is None:
        # 跨 subnet 列表 — 必須逐筆檢查 subnet 權限
        candidate_subnet_ids = list({r.subnet_id for r in rows})
        visible_subnet_ids = set(
            await filter_visible(
                session,
                user=user,
                object_type="subnet",
                object_ids=candidate_subnet_ids,
                required="read",
            )
        )
        rows = [r for r in rows if r.subnet_id in visible_subnet_ids]

    items = [IPAddressRead.model_validate(r) for r in rows]
    total = int(await session.scalar(count_stmt) or 0)
    return Paginated[IPAddressRead](items=items, total=total, page=page, page_size=page_size)


@router.get("/{address_id}", response_model=IPAddressRead)
async def get_address(
    address_id: uuid.UUID,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> IPAddressRead:
    obj = await session.get(IPAddress, address_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Address not found")
    await _require_subnet_perm(session, user, obj.subnet_id, "read")
    return IPAddressRead.model_validate(obj)


@router.post("", response_model=IPAddressRead, status_code=status.HTTP_201_CREATED)
async def create_address(
    payload: IPAddressCreate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> IPAddressRead:
    subnet = await _require_subnet_perm(session, user, payload.subnet_id, "write")

    try:
        obj = await create_ip(
            session,
            subnet=subnet,
            ip=payload.ip,
            hostname=payload.hostname,
            description=payload.description,
            mac=payload.mac,
            state=payload.state,
        )
    except IPNotInSubnet as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IPAlreadyExists as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # 應用後續欄位
    try:
        cf = await validate_custom_fields(
            session, object_type="ip", payload=payload.custom_fields
        )
    except CustomFieldError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    obj.owner = payload.owner
    obj.device_id = payload.device_id
    obj.switch_port = payload.switch_port
    obj.exclude_from_ping = payload.exclude_from_ping
    obj.ptr_ignore = payload.ptr_ignore
    obj.note = payload.note
    obj.custom_fields = cf or None

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="ip_address",
        object_id=str(obj.id),
        action="create",
        diff={"after": payload.model_dump(mode="json")},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return IPAddressRead.model_validate(obj)


@router.post("/first_free", response_model=IPAddressRead, status_code=status.HTTP_201_CREATED)
async def allocate_first_free_address(
    payload: IPAddressAllocate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> IPAddressRead:
    subnet = await _require_subnet_perm(session, user, payload.subnet_id, "write")

    try:
        obj = await allocate_first_free(
            session,
            subnet=subnet,
            hostname=payload.hostname,
            description=payload.description,
            mac=payload.mac,
            state=payload.state,
        )
    except SubnetFull as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="ip_address",
        object_id=str(obj.id),
        action="allocate_first_free",
        diff={"after": {"subnet_id": str(subnet.id), "ip": str(obj.ip), "hostname": obj.hostname}},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return IPAddressRead.model_validate(obj)


@router.patch("/{address_id}", response_model=IPAddressRead)
async def update_address(
    address_id: uuid.UUID,
    payload: IPAddressUpdate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> IPAddressRead:
    obj = await session.get(IPAddress, address_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Address not found")
    await _require_subnet_perm(session, user, obj.subnet_id, "write")

    before = {
        "hostname": obj.hostname,
        "state": obj.state,
        "mac": str(obj.mac) if obj.mac else None,
        "description": obj.description,
    }
    changes = payload.model_dump(exclude_unset=True)
    if "custom_fields" in changes:
        try:
            changes["custom_fields"] = await validate_custom_fields(
                session, object_type="ip", payload=changes["custom_fields"]
            ) or None
        except CustomFieldError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    for key, value in changes.items():
        setattr(obj, key, value)

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="ip_address",
        object_id=str(obj.id),
        action="update",
        diff={"before": before, "changes": changes},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return IPAddressRead.model_validate(obj)


@router.delete("/{address_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_address(
    address_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    obj = await session.get(IPAddress, address_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Address not found")
    await _require_subnet_perm(session, user, obj.subnet_id, "admin")

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="ip_address",
        object_id=str(obj.id),
        action="delete",
        diff={"before": {"ip": str(obj.ip), "subnet_id": str(obj.subnet_id), "hostname": obj.hostname}},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.delete(obj)
    await session.commit()
