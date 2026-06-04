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
from app.models.subnet import Subnet
from app.schemas.base import Paginated, StrictModel
from app.schemas.section import SectionCreate, SectionRead, SectionUpdate
from app.services.permission import filter_visible


async def _subnet_counts_by_section(
    session: AsyncSession, section_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    if not section_ids:
        return {}
    stmt = (
        select(Subnet.section_id, func.count().label("c"))
        .where(Subnet.section_id.in_(section_ids))
        .group_by(Subnet.section_id)
    )
    return {row.section_id: int(row.c) for row in (await session.execute(stmt)).all()}


def _attach_subnet_count(section: Section, counts: dict[uuid.UUID, int]) -> SectionRead:
    read = SectionRead.model_validate(section)
    read.subnet_count = counts.get(section.id, 0)
    return read

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
    visible = [r for r in rows if r.id in visible_ids]
    counts = await _subnet_counts_by_section(session, [r.id for r in visible])
    items = [_attach_subnet_count(r, counts) for r in visible]
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
    counts = await _subnet_counts_by_section(session, [section.id])
    return _attach_subnet_count(section, counts)


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


class _BulkDelete(StrictModel):
    ids: list[uuid.UUID]


@router.post("/bulk-delete", dependencies=[Depends(require_admin)])
async def bulk_delete_sections(
    payload: _BulkDelete, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    if not payload.ids: return {"deleted": 0, "failed": 0, "errors": []}
    if len(payload.ids) > 500:
        raise HTTPException(400, detail="too many ids (max 500)")
    deleted, errors = 0, []
    actor_ip = request.client.host if request.client else None
    actor_ua = request.headers.get("user-agent")
    request_id = getattr(request.state, "request_id", None)
    for sid in payload.ids:
        s = await session.get(Section, sid)
        if s is None:
            errors.append({"id": str(sid), "error": "not_found"}); continue
        await append_audit(
            session, actor_user_id=str(user.id),
            actor_ip=actor_ip, actor_user_agent=actor_ua,
            object_type="section", object_id=str(s.id), action="delete",
            diff={"before": {"name": s.name}, "bulk": True},
            request_id=request_id,
        )
        await session.delete(s)
        deleted += 1
    await session.commit()
    return {"deleted": deleted, "failed": len(errors), "errors": errors[:50]}
