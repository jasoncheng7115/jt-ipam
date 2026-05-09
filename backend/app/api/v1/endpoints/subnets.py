"""Subnet CRUD + first_free_address + usage（Phase 1 重點）。"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_object_perm
from app.core.audit import append_audit
from app.core.db import get_session
from app.models.section import Section
from app.models.subnet import Subnet
from app.schemas.base import Paginated
from app.schemas.subnet import (
    FirstFreeAddress,
    SubnetCreate,
    SubnetRead,
    SubnetUpdate,
    SubnetUsage,
)
from app.services.permission import (
    filter_visible,
    get_object_permission,
    has_permission,
)
from app.services.subnet import (
    SubnetOverlap,
    assert_no_overlap,
    compute_master_subnet,
    find_first_free_address,
    get_usage,
)

router = APIRouter(prefix="/subnets", tags=["subnets"])


@router.get("", response_model=Paginated[SubnetRead])
async def list_subnets(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    section_id: uuid.UUID | None = Query(None),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=500),
) -> Paginated[SubnetRead]:
    stmt = select(Subnet)
    count_stmt = select(func.count()).select_from(Subnet)
    if section_id is not None:
        stmt = stmt.where(Subnet.section_id == section_id)
        count_stmt = count_stmt.where(Subnet.section_id == section_id)

    stmt = stmt.order_by(Subnet.cidr).offset((page - 1) * page_size).limit(page_size)
    rows = list((await session.execute(stmt)).scalars().all())

    # A01：篩出 user 有 read 權限的
    visible_ids = await filter_visible(
        session,
        user=user,
        object_type="subnet",
        object_ids=[r.id for r in rows],
        required="read",
    )
    visible_set = set(visible_ids)
    items = [SubnetRead.model_validate(r) for r in rows if r.id in visible_set]

    total = int(await session.scalar(count_stmt) or 0)
    return Paginated[SubnetRead](items=items, total=total, page=page, page_size=page_size)


@router.get(
    "/{subnet_id}",
    response_model=SubnetRead,
    dependencies=[Depends(require_object_perm("subnet", "read", path_param="subnet_id"))],
)
async def get_subnet(
    subnet_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SubnetRead:
    subnet = await session.get(Subnet, subnet_id)
    if subnet is None:
        raise HTTPException(status_code=404, detail="Subnet not found")
    return SubnetRead.model_validate(subnet)


@router.get(
    "/{subnet_id}/usage",
    response_model=SubnetUsage,
    dependencies=[Depends(require_object_perm("subnet", "read", path_param="subnet_id"))],
)
async def subnet_usage(
    subnet_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SubnetUsage:
    subnet = await session.get(Subnet, subnet_id)
    if subnet is None:
        raise HTTPException(status_code=404, detail="Subnet not found")
    total, used, free, pct = await get_usage(session, subnet)
    return SubnetUsage(
        subnet_id=subnet.id,
        cidr=str(subnet.cidr),
        total=total,
        used=used,
        free=free,
        used_pct=pct,
    )


@router.get(
    "/{subnet_id}/first_free_address",
    response_model=FirstFreeAddress,
    dependencies=[Depends(require_object_perm("subnet", "read", path_param="subnet_id"))],
)
async def first_free_address(
    subnet_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FirstFreeAddress:
    subnet = await session.get(Subnet, subnet_id)
    if subnet is None:
        raise HTTPException(status_code=404, detail="Subnet not found")
    ip = await find_first_free_address(session, subnet)
    return FirstFreeAddress(subnet_id=subnet.id, cidr=str(subnet.cidr), ip=ip)


@router.post("", response_model=SubnetRead, status_code=status.HTTP_201_CREATED)
async def create_subnet(
    payload: SubnetCreate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SubnetRead:
    # A01：要在指定 section 有 write 權限
    section = await session.get(Section, payload.section_id)
    if section is None:
        raise HTTPException(status_code=400, detail="Invalid section_id")
    level = await get_object_permission(
        session, user=user, object_type="section", object_id=section.id
    )
    if not has_permission(level, "write"):
        raise HTTPException(status_code=403, detail="Forbidden")

    try:
        await assert_no_overlap(
            session, cidr=payload.cidr, vrf_id=payload.vrf_id
        )
    except SubnetOverlap as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    master_id = await compute_master_subnet(
        session, cidr=payload.cidr, vrf_id=payload.vrf_id
    )

    data = payload.model_dump()
    data["master_subnet_id"] = master_id
    subnet = Subnet(**data)
    session.add(subnet)
    await session.flush()

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="subnet",
        object_id=str(subnet.id),
        action="create",
        diff={"after": payload.model_dump(mode="json")},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(subnet)
    return SubnetRead.model_validate(subnet)


@router.patch(
    "/{subnet_id}",
    response_model=SubnetRead,
    dependencies=[Depends(require_object_perm("subnet", "write", path_param="subnet_id"))],
)
async def update_subnet(
    subnet_id: uuid.UUID,
    payload: SubnetUpdate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SubnetRead:
    subnet = await session.get(Subnet, subnet_id)
    if subnet is None:
        raise HTTPException(status_code=404, detail="Subnet not found")

    before = {
        "section_id": str(subnet.section_id),
        "vrf_id": str(subnet.vrf_id) if subnet.vrf_id else None,
        "vlan_id": str(subnet.vlan_id) if subnet.vlan_id else None,
        "is_pool": subnet.is_pool,
        "is_full": subnet.is_full,
    }
    changes = payload.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(subnet, key, value)

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="subnet",
        object_id=str(subnet.id),
        action="update",
        diff={"before": before, "changes": changes},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(subnet)
    return SubnetRead.model_validate(subnet)


@router.delete(
    "/{subnet_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_object_perm("subnet", "admin", path_param="subnet_id"))],
)
async def delete_subnet(
    subnet_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    subnet = await session.get(Subnet, subnet_id)
    if subnet is None:
        raise HTTPException(status_code=404, detail="Subnet not found")

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="subnet",
        object_id=str(subnet.id),
        action="delete",
        diff={"before": {"cidr": str(subnet.cidr), "section_id": str(subnet.section_id)}},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.delete(subnet)
    await session.commit()
