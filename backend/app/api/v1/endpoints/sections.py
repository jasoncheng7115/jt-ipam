"""Sections CRUD（接 auth + 物件級權限）。"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import (
    CurrentUser,
    require_admin,
    require_object_perm,
)
from app.core.audit import append_audit
from app.core.db import get_session
from app.models.section import Section
from app.schemas.base import Paginated
from app.schemas.section import SectionCreate, SectionRead, SectionUpdate
from app.services.permission import filter_visible

router = APIRouter(prefix="/sections", tags=["sections"])


@router.get("", response_model=Paginated[SectionRead])
async def list_sections(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=500),
) -> Paginated[SectionRead]:
    offset = (page - 1) * page_size
    stmt = (
        select(Section)
        .order_by(Section.display_order, Section.name)
        .offset(offset)
        .limit(page_size)
    )
    rows = list((await session.execute(stmt)).scalars().all())

    visible_ids = set(
        await filter_visible(
            session,
            user=user,
            object_type="section",
            object_ids=[r.id for r in rows],
            required="read",
        )
    )
    items = [SectionRead.model_validate(r) for r in rows if r.id in visible_ids]
    total = int(await session.scalar(select(func.count()).select_from(Section)) or 0)
    return Paginated[SectionRead](items=items, total=total, page=page, page_size=page_size)


@router.get(
    "/{section_id}",
    response_model=SectionRead,
    dependencies=[Depends(require_object_perm("section", "read", path_param="section_id"))],
)
async def get_section(
    section_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SectionRead:
    section = await session.get(Section, section_id)
    if section is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Section not found")
    return SectionRead.model_validate(section)


@router.post(
    "",
    response_model=SectionRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],   # 建立 Section 限 admin（A01：頂層命名空間嚴格）
)
async def create_section(
    payload: SectionCreate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SectionRead:
    section = Section(**payload.model_dump())
    session.add(section)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Section conflicts with an existing record",
        ) from exc

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="section",
        object_id=str(section.id),
        action="create",
        diff={"after": payload.model_dump(mode="json")},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(section)
    return SectionRead.model_validate(section)


@router.patch(
    "/{section_id}",
    response_model=SectionRead,
    dependencies=[Depends(require_object_perm("section", "write", path_param="section_id"))],
)
async def update_section(
    section_id: uuid.UUID,
    payload: SectionUpdate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SectionRead:
    section = await session.get(Section, section_id)
    if section is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Section not found")

    before = {
        "name": section.name,
        "description": section.description,
        "parent_id": str(section.parent_id) if section.parent_id else None,
        "strict_mode": section.strict_mode,
        "display_order": section.display_order,
    }
    changes = payload.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(section, key, value)

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="section",
        object_id=str(section.id),
        action="update",
        diff={"before": before, "changes": changes},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(section)
    return SectionRead.model_validate(section)


@router.delete(
    "/{section_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_object_perm("section", "admin", path_param="section_id"))],
)
async def delete_section(
    section_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    section = await session.get(Section, section_id)
    if section is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Section not found")

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="section",
        object_id=str(section.id),
        action="delete",
        diff={"before": {"name": section.name, "description": section.description}},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.delete(section)
    await session.commit()
