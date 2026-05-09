"""Sections CRUD（Phase 1 樣本端點）。

注意：
- 所有寫入端點將透過 dependency 套權限檢查（A01）— 目前佔位以利骨架啟動
- 所有寫入會寫 audit_log（A08）
- Pydantic strict schema 嚴格驗證輸入（A03）
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import append_audit
from app.core.db import get_session
from app.models.section import Section
from app.schemas.base import Paginated
from app.schemas.section import SectionCreate, SectionRead, SectionUpdate

router = APIRouter(prefix="/sections", tags=["sections"])


@router.get("", response_model=Paginated[SectionRead])
async def list_sections(
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=500),
) -> Paginated[SectionRead]:
    offset = (page - 1) * page_size
    stmt = select(Section).order_by(Section.display_order, Section.name).offset(offset).limit(page_size)
    rows = (await session.execute(stmt)).scalars().all()
    total = await session.scalar(select(Section.id).count() if False else _count_stmt())
    return Paginated[SectionRead](
        items=[SectionRead.model_validate(r) for r in rows],
        total=int(total or 0),
        page=page,
        page_size=page_size,
    )


def _count_stmt():  # type: ignore[no-untyped-def]
    from sqlalchemy import func
    return select(func.count()).select_from(Section)


@router.get("/{section_id}", response_model=SectionRead)
async def get_section(
    section_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SectionRead:
    section = await session.get(Section, section_id)
    if section is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Section not found")
    return SectionRead.model_validate(section)


@router.post("", response_model=SectionRead, status_code=status.HTTP_201_CREATED)
async def create_section(
    payload: SectionCreate,
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
        actor_user_id=None,  # TODO: 接 auth
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="section",
        object_id=str(section.id),
        action="create",
        diff={"after": payload.model_dump(mode="json")},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return SectionRead.model_validate(section)


@router.patch("/{section_id}", response_model=SectionRead)
async def update_section(
    section_id: uuid.UUID,
    payload: SectionUpdate,
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
        actor_user_id=None,
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="section",
        object_id=str(section.id),
        action="update",
        diff={"before": before, "changes": changes},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return SectionRead.model_validate(section)


@router.delete("/{section_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_section(
    section_id: uuid.UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    section = await session.get(Section, section_id)
    if section is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Section not found")

    await append_audit(
        session,
        actor_user_id=None,
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
