"""phpIPAM `/sections/`：讀寫端點。

寫入仍要 admin（與 /api/v1/ 的策略一致）。
"""

from __future__ import annotations

import time
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.phpipam.helpers import (
    phpipam_current_user,
    phpipam_response,
    section_to_phpipam,
    subnet_to_phpipam,
)
from app.core.audit import append_audit
from app.core.db import get_session
from app.models.section import Section
from app.models.subnet import Subnet
from app.services.permission import filter_visible

router = APIRouter()


def _require_admin(user) -> None:  # type: ignore[no-untyped-def]
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin required")


def _bool(v: Any) -> bool:  # noqa: ANN401
    """phpIPAM 用 "0"/"1"；接受 bool/str/int。"""
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return v != 0
    if isinstance(v, str):
        return v.lower() in {"1", "true", "yes", "on"}
    return False


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


@router.post("/{app_id}/sections/")
async def create_section(
    app_id: str,
    request: Request,
    payload: Annotated[dict[str, Any], Body()],
    user=Depends(phpipam_current_user),
    session: Annotated[AsyncSession, Depends(get_session)] = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    _require_admin(user)
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        raise HTTPException(400, detail="name is required")

    parent_raw = payload.get("masterSection") or payload.get("parent_id")
    parent_id: uuid.UUID | None = None
    if parent_raw and parent_raw != "0":
        try:
            parent_id = uuid.UUID(str(parent_raw))
        except ValueError as exc:
            raise HTTPException(400, detail="invalid masterSection") from exc

    sec = Section(
        name=name.strip(),
        description=payload.get("description"),
        parent_id=parent_id,
        strict_mode=_bool(payload.get("strictMode", False)),
        display_order=int(payload.get("order") or 0),
    )
    session.add(sec)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="Section conflict") from exc

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="section",
        object_id=str(sec.id),
        action="create",
        diff={"after": {"name": sec.name, "via": "phpipam"}},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(sec)
    return phpipam_response(
        success=True,
        code=201,
        message="Section created",
        data={"id": str(sec.id)},
        started=started,
    )


@router.patch("/{app_id}/sections/{ident}/")
async def update_section(
    app_id: str,
    ident: str,
    request: Request,
    payload: Annotated[dict[str, Any], Body()],
    user=Depends(phpipam_current_user),
    session: Annotated[AsyncSession, Depends(get_session)] = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    _require_admin(user)
    try:
        sid = uuid.UUID(ident)
    except ValueError as exc:
        raise HTTPException(400, detail="Invalid id") from exc

    sec = await session.get(Section, sid)
    if sec is None:
        raise HTTPException(404, detail="Not found")

    before = {"name": sec.name, "description": sec.description}
    if "name" in payload:
        sec.name = str(payload["name"]).strip()
    if "description" in payload:
        sec.description = payload["description"]
    if "strictMode" in payload:
        sec.strict_mode = _bool(payload["strictMode"])
    if "order" in payload:
        sec.display_order = int(payload["order"] or 0)
    if "masterSection" in payload:
        v = payload["masterSection"]
        sec.parent_id = uuid.UUID(str(v)) if v and v != "0" else None

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="section",
        object_id=str(sec.id),
        action="update",
        diff={"before": before, "via": "phpipam"},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return phpipam_response(success=True, message="Section updated", started=started)


@router.delete("/{app_id}/sections/{ident}/")
async def delete_section(
    app_id: str,
    ident: str,
    request: Request,
    user=Depends(phpipam_current_user),
    session: Annotated[AsyncSession, Depends(get_session)] = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    _require_admin(user)
    try:
        sid = uuid.UUID(ident)
    except ValueError as exc:
        raise HTTPException(400, detail="Invalid id") from exc

    sec = await session.get(Section, sid)
    if sec is None:
        raise HTTPException(404, detail="Not found")

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="section",
        object_id=str(sec.id),
        action="delete",
        diff={"before": {"name": sec.name}, "via": "phpipam"},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.delete(sec)
    await session.commit()
    return phpipam_response(success=True, message="Section deleted", started=started)
