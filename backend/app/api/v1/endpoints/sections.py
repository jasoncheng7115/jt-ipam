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
    require_object_perm,
)
from app.api.v1.write_guards import require_create, require_move, require_visible
from app.core.audit import append_audit
from app.core.db import get_session
from app.core.sqlin import in_values
from app.models.section import Section
from app.models.subnet import Subnet
from app.schemas.base import Paginated, StrictModel
from app.schemas.section import SectionCreate, SectionRead, SectionUpdate
from app.services.permission import get_object_permission, has_permission, visible_ids


async def _subnet_counts_by_section(
    session: AsyncSession, section_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    if not section_ids:
        return {}
    stmt = (
        select(Subnet.section_id, func.count().label("c"))
        .where(in_values(Subnet.section_id, section_ids))
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
    # A01：先把可見範圍套進查詢，再分頁 —— 不可「先分頁再過濾」。
    # 舊寫法先全域分頁、事後才篩掉不可見的，造成兩個問題：
    #   (1) total 回全系統 section 數 → 洩漏受限帳號不該知道的規模
    #   (2) 每頁實際筆數少於 page_size、甚至整頁空白，分頁邏輯壞掉
    vis = await visible_ids(session, user=user, object_type="section", required="read")
    stmt = select(Section)
    count_stmt = select(func.count()).select_from(Section)
    if vis is not None:                    # None＝全部可見（admin 或萬用授權）
        if not vis:                        # 空 set＝完全沒有可見範圍
            return Paginated[SectionRead](items=[], total=0, page=page, page_size=page_size)
        stmt = stmt.where(in_values(Section.id, vis))
        count_stmt = count_stmt.where(in_values(Section.id, vis))

    rows = list((await session.execute(
        stmt.order_by(Section.display_order, Section.name)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )).scalars().all())

    counts = await _subnet_counts_by_section(session, [r.id for r in rows])
    items = [_attach_subnet_count(r, counts) for r in rows]
    total = int(await session.scalar(count_stmt) or 0)
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
)
async def create_section(
    payload: SectionCreate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SectionRead:
    # 區段是頂層命名空間（A01）：要萬用的區段寫入權（「系統管理員」角色有），不是只看「管理員」開關。
    # 上層區段要看得到；單位是權限上層，要有寫入權
    await require_create(session, user, "section", parents={"section": payload.parent_id},
                         tenant=("customer", payload.customer_id), wildcard_only=True)
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
    if "customer_id" in changes and changes["customer_id"] != section.customer_id:
        await require_move(session, user, object_type="section", object_id=section.id,
                           dest_type="customer", dest_id=changes["customer_id"])
    if "parent_id" in changes and changes["parent_id"] != section.parent_id:
        await require_visible(session, user, "section", changes["parent_id"], "Section not found")
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
    # 物件沒了，指向它的授權也不該留著（permissions.object_id 沒有外鍵，沒有人會自動清）
    from app.services.permission import purge_permissions_for_object
    await purge_permissions_for_object(session, object_type="section", object_id=section_id)

    await session.commit()


class _BulkDelete(StrictModel):
    ids: list[uuid.UUID]


@router.post("/bulk-delete")
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
        if not has_permission(await get_object_permission(session, user=user, object_type="section",
                                                          object_id=s.id), "admin"):
            errors.append({"id": str(sid), "error": "no_permission"}); continue
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
