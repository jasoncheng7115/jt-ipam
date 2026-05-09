"""VRF endpoints。"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin
from app.core.audit import append_audit
from app.core.db import get_session
from app.models.vrf import VRF
from app.schemas.base import Paginated
from app.schemas.vrf import VRFCreate, VRFRead, VRFUpdate

router = APIRouter(prefix="/vrfs", tags=["vrfs"])


@router.get("", response_model=Paginated[VRFRead])
async def list_vrfs(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=500),
) -> Paginated[VRFRead]:
    rows = list(
        (await session.execute(
            select(VRF).order_by(VRF.name).offset((page - 1) * page_size).limit(page_size)
        )).scalars().all()
    )
    total = int(await session.scalar(select(func.count()).select_from(VRF)) or 0)
    return Paginated[VRFRead](
        items=[VRFRead.model_validate(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.get("/{vrf_id}", response_model=VRFRead)
async def get_vrf(
    vrf_id: uuid.UUID,
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> VRFRead:
    obj = await session.get(VRF, vrf_id)
    if obj is None:
        raise HTTPException(404, detail="VRF not found")
    return VRFRead.model_validate(obj)


@router.post("", response_model=VRFRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_vrf(
    payload: VRFCreate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> VRFRead:
    obj = VRF(**payload.model_dump())
    session.add(obj)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="VRF name conflict") from exc
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="vrf", object_id=str(obj.id), action="create",
        diff={"after": payload.model_dump(mode="json")},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return VRFRead.model_validate(obj)


@router.patch("/{vrf_id}", response_model=VRFRead,
              dependencies=[Depends(require_admin)])
async def update_vrf(
    vrf_id: uuid.UUID,
    payload: VRFUpdate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> VRFRead:
    obj = await session.get(VRF, vrf_id)
    if obj is None:
        raise HTTPException(404, detail="VRF not found")
    before = {"name": obj.name, "rd": obj.rd, "allow_overlap": obj.allow_overlap}
    changes = payload.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(obj, k, v)
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="vrf", object_id=str(obj.id), action="update",
        diff={"before": before, "changes": changes},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return VRFRead.model_validate(obj)


@router.delete("/{vrf_id}", status_code=204, dependencies=[Depends(require_admin)])
async def delete_vrf(
    vrf_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    obj = await session.get(VRF, vrf_id)
    if obj is None:
        raise HTTPException(404, detail="VRF not found")
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="vrf", object_id=str(obj.id), action="delete",
        diff={"before": {"name": obj.name}},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.delete(obj)
    await session.commit()
