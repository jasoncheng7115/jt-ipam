"""NAT endpoints。"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin
from app.core.audit import append_audit
from app.core.db import get_session
from app.models.nat import NATTranslation
from app.schemas.base import Paginated
from app.schemas.nat import NATCreate, NATRead, NATUpdate

router = APIRouter(prefix="/nat", tags=["nat"])


@router.get("", response_model=Paginated[NATRead])
async def list_nat(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    type: str | None = Query(None),
    device_id: uuid.UUID | None = Query(None),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=500),
) -> Paginated[NATRead]:
    stmt = select(NATTranslation)
    cstmt = select(func.count()).select_from(NATTranslation)
    if type is not None:
        stmt = stmt.where(NATTranslation.type == type)
        cstmt = cstmt.where(NATTranslation.type == type)
    if device_id is not None:
        stmt = stmt.where(NATTranslation.device_id == device_id)
        cstmt = cstmt.where(NATTranslation.device_id == device_id)
    stmt = stmt.order_by(NATTranslation.name).offset((page - 1) * page_size).limit(page_size)
    rows = list((await session.execute(stmt)).scalars().all())
    total = int(await session.scalar(cstmt) or 0)
    return Paginated[NATRead](
        items=[NATRead.model_validate(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.get("/{nat_id}", response_model=NATRead)
async def get_nat(
    nat_id: uuid.UUID,
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> NATRead:
    obj = await session.get(NATTranslation, nat_id)
    if obj is None:
        raise HTTPException(404, detail="NAT not found")
    return NATRead.model_validate(obj)


@router.post("", response_model=NATRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_nat(
    payload: NATCreate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> NATRead:
    obj = NATTranslation(**payload.model_dump())
    session.add(obj)
    await session.flush()
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="nat", object_id=str(obj.id), action="create",
        diff={"after": payload.model_dump(mode="json")},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return NATRead.model_validate(obj)


@router.patch("/{nat_id}", response_model=NATRead,
              dependencies=[Depends(require_admin)])
async def update_nat(
    nat_id: uuid.UUID,
    payload: NATUpdate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> NATRead:
    obj = await session.get(NATTranslation, nat_id)
    if obj is None:
        raise HTTPException(404, detail="NAT not found")
    before = {"name": obj.name, "type": obj.type, "protocol": obj.protocol}
    changes = payload.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(obj, k, v)
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="nat", object_id=str(obj.id), action="update",
        diff={"before": before, "changes": changes},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return NATRead.model_validate(obj)


@router.delete("/{nat_id}", status_code=204, dependencies=[Depends(require_admin)])
async def delete_nat(
    nat_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    obj = await session.get(NATTranslation, nat_id)
    if obj is None:
        raise HTTPException(404, detail="NAT not found")
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="nat", object_id=str(obj.id), action="delete",
        diff={"before": {"name": obj.name, "type": obj.type}},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.delete(obj)
    await session.commit()
