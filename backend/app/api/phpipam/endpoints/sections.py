"""phpIPAM `/sections/`：唯讀為主，透過 jt-ipam 內部模型回 phpIPAM 風格。"""

from __future__ import annotations

import time
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.phpipam.helpers import (
    phpipam_current_user,
    phpipam_response,
    section_to_phpipam,
    subnet_to_phpipam,
)
from app.core.db import get_session
from app.models.section import Section
from app.models.subnet import Subnet
from app.services.permission import filter_visible

router = APIRouter()


@router.get("/{app_id}/sections/")
async def list_sections(
    app_id: str,
    user=Depends(phpipam_current_user),
    session: Annotated[AsyncSession, Depends(get_session)] = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    rows = list((await session.execute(select(Section).order_by(Section.display_order))).scalars().all())
    visible = set(
        await filter_visible(
            session, user=user, object_type="section",
            object_ids=[r.id for r in rows], required="read",
        )
    )
    data = [section_to_phpipam(r) for r in rows if r.id in visible]
    return phpipam_response(success=True, data=data, started=started)


@router.get("/{app_id}/sections/{ident}/")
async def get_section(
    app_id: str,
    ident: str,
    user=Depends(phpipam_current_user),
    session: Annotated[AsyncSession, Depends(get_session)] = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    section: Section | None = None
    # 嘗試 UUID
    try:
        section = await session.get(Section, uuid.UUID(ident))
    except ValueError:
        # by name
        section = (
            await session.execute(select(Section).where(Section.name == ident))
        ).scalar_one_or_none()
    if section is None:
        raise HTTPException(404, detail="Section not found")

    from app.services.permission import get_object_permission, has_permission
    level = await get_object_permission(
        session, user=user, object_type="section", object_id=section.id
    )
    if not has_permission(level, "read"):
        raise HTTPException(404, detail="Section not found")

    return phpipam_response(success=True, data=section_to_phpipam(section), started=started)


@router.get("/{app_id}/sections/{ident}/subnets/")
async def list_section_subnets(
    app_id: str,
    ident: str,
    user=Depends(phpipam_current_user),
    session: Annotated[AsyncSession, Depends(get_session)] = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        section_id = uuid.UUID(ident)
    except ValueError as exc:
        raise HTTPException(400, detail="Invalid section id") from exc

    rows = list(
        (await session.execute(select(Subnet).where(Subnet.section_id == section_id))).scalars().all()
    )
    visible = set(
        await filter_visible(
            session, user=user, object_type="subnet",
            object_ids=[r.id for r in rows], required="read",
        )
    )
    data = [subnet_to_phpipam(r) for r in rows if r.id in visible]
    return phpipam_response(success=True, data=data, started=started)
