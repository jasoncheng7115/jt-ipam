"""VLAN Domain + VLAN endpoints。

Phase 1：read 須認證，write/delete 限 admin。後續可加群組級權限。
"""

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
from app.models.vlan import VLAN, VLANDomain
from app.schemas.base import Paginated
from app.schemas.vlan import (
    VLANCreate,
    VLANDomainCreate,
    VLANDomainRead,
    VLANDomainUpdate,
    VLANRead,
    VLANUpdate,
)

router = APIRouter(tags=["vlans"])


# ─────────────────── VLAN Domains ───────────────────
@router.get("/vlan-domains", response_model=Paginated[VLANDomainRead])
async def list_domains(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=500),
) -> Paginated[VLANDomainRead]:
    rows = list(
        (
            await session.execute(
                select(VLANDomain)
                .order_by(VLANDomain.name)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    total = int(await session.scalar(select(func.count()).select_from(VLANDomain)) or 0)
    return Paginated[VLANDomainRead](
        items=[VLANDomainRead.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/vlan-domains", response_model=VLANDomainRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_domain(
    payload: VLANDomainCreate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> VLANDomainRead:
    obj = VLANDomain(**payload.model_dump())
    session.add(obj)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="VLAN domain conflict") from exc
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="vlan_domain",
        object_id=str(obj.id),
        action="create",
        diff={"after": payload.model_dump(mode="json")},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return VLANDomainRead.model_validate(obj)


@router.patch("/vlan-domains/{domain_id}", response_model=VLANDomainRead,
              dependencies=[Depends(require_admin)])
async def update_domain(
    domain_id: uuid.UUID,
    payload: VLANDomainUpdate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> VLANDomainRead:
    obj = await session.get(VLANDomain, domain_id)
    if obj is None:
        raise HTTPException(404, detail="Not found")
    before = {"name": obj.name, "description": obj.description}
    changes = payload.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(obj, k, v)
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="vlan_domain",
        object_id=str(obj.id),
        action="update",
        diff={"before": before, "changes": changes},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return VLANDomainRead.model_validate(obj)


@router.delete("/vlan-domains/{domain_id}", status_code=204,
               dependencies=[Depends(require_admin)])
async def delete_domain(
    domain_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    obj = await session.get(VLANDomain, domain_id)
    if obj is None:
        raise HTTPException(404, detail="Not found")
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="vlan_domain",
        object_id=str(obj.id),
        action="delete",
        diff={"before": {"name": obj.name}},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.delete(obj)
    await session.commit()


# ─────────────────── VLANs ───────────────────
@router.get("/vlans", response_model=Paginated[VLANRead])
async def list_vlans(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    domain_id: uuid.UUID | None = Query(None),
    number: int | None = Query(None, ge=1, le=4094),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=500),
) -> Paginated[VLANRead]:
    stmt = select(VLAN)
    cstmt = select(func.count()).select_from(VLAN)
    if domain_id is not None:
        stmt = stmt.where(VLAN.domain_id == domain_id)
        cstmt = cstmt.where(VLAN.domain_id == domain_id)
    if number is not None:
        stmt = stmt.where(VLAN.number == number)
        cstmt = cstmt.where(VLAN.number == number)
    stmt = stmt.order_by(VLAN.number).offset((page - 1) * page_size).limit(page_size)
    rows = list((await session.execute(stmt)).scalars().all())
    total = int(await session.scalar(cstmt) or 0)
    return Paginated[VLANRead](
        items=[VLANRead.model_validate(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.post("/vlans", response_model=VLANRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_vlan(
    payload: VLANCreate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> VLANRead:
    obj = VLAN(**payload.model_dump())
    session.add(obj)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="VLAN conflict (duplicate domain+number?)") from exc
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="vlan",
        object_id=str(obj.id),
        action="create",
        diff={"after": payload.model_dump(mode="json")},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return VLANRead.model_validate(obj)


@router.patch("/vlans/{vlan_id}", response_model=VLANRead,
              dependencies=[Depends(require_admin)])
async def update_vlan(
    vlan_id: uuid.UUID,
    payload: VLANUpdate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> VLANRead:
    obj = await session.get(VLAN, vlan_id)
    if obj is None:
        raise HTTPException(404, detail="Not found")
    before = {"name": obj.name, "description": obj.description}
    changes = payload.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(obj, k, v)
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="vlan",
        object_id=str(obj.id),
        action="update",
        diff={"before": before, "changes": changes},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return VLANRead.model_validate(obj)


@router.delete("/vlans/{vlan_id}", status_code=204,
               dependencies=[Depends(require_admin)])
async def delete_vlan(
    vlan_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    obj = await session.get(VLAN, vlan_id)
    if obj is None:
        raise HTTPException(404, detail="Not found")
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="vlan",
        object_id=str(obj.id),
        action="delete",
        diff={"before": {"number": obj.number, "name": obj.name}},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.delete(obj)
    await session.commit()
