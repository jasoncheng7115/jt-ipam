"""Custom Field 定義端點（admin only）。

公開 read：所有 user 看得到欄位定義；只有 admin 可以新增 / 修改 / 刪除。
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin
from app.core.audit import append_audit
from app.core.db import get_session
from app.models.custom_field import CustomFieldDefinition
from app.schemas.base import Paginated
from app.schemas.custom_field import (
    CustomFieldCreate,
    CustomFieldRead,
    CustomFieldUpdate,
)

router = APIRouter(prefix="/custom-fields", tags=["custom-fields"])


@router.get("", response_model=Paginated[CustomFieldRead])
async def list_custom_fields(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    object_type: str | None = Query(None),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(100, ge=1, le=500),
) -> Paginated[CustomFieldRead]:
    stmt = select(CustomFieldDefinition)
    cstmt = select(func.count()).select_from(CustomFieldDefinition)
    if object_type is not None:
        stmt = stmt.where(CustomFieldDefinition.object_type == object_type)
        cstmt = cstmt.where(CustomFieldDefinition.object_type == object_type)
    stmt = (
        stmt.order_by(
            CustomFieldDefinition.object_type,
            CustomFieldDefinition.display_order,
            CustomFieldDefinition.name,
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    total = int(await session.scalar(cstmt) or 0)
    return Paginated[CustomFieldRead](
        items=[CustomFieldRead.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=CustomFieldRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_custom_field(
    payload: CustomFieldCreate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CustomFieldRead:
    obj = CustomFieldDefinition(**payload.model_dump())
    session.add(obj)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="Custom field already exists") from exc

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="custom_field",
        object_id=str(obj.id),
        action="create",
        diff={"after": payload.model_dump(mode="json")},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return CustomFieldRead.model_validate(obj)


@router.patch("/{field_id}", response_model=CustomFieldRead,
              dependencies=[Depends(require_admin)])
async def update_custom_field(
    field_id: uuid.UUID,
    payload: CustomFieldUpdate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CustomFieldRead:
    obj = await session.get(CustomFieldDefinition, field_id)
    if obj is None:
        raise HTTPException(404, detail="Not found")
    before = {
        "label_zh_tw": obj.label_zh_tw,
        "label_en_us": obj.label_en_us,
        "required": obj.required,
        "validation_regex": obj.validation_regex,
    }
    changes = payload.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(obj, k, v)
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="custom_field",
        object_id=str(obj.id),
        action="update",
        diff={"before": before, "changes": changes},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return CustomFieldRead.model_validate(obj)


@router.delete("/{field_id}", status_code=204, dependencies=[Depends(require_admin)])
async def delete_custom_field(
    field_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    obj = await session.get(CustomFieldDefinition, field_id)
    if obj is None:
        raise HTTPException(404, detail="Not found")
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="custom_field",
        object_id=str(obj.id),
        action="delete",
        diff={"before": {"object_type": obj.object_type, "name": obj.name,
                         "field_type": obj.field_type}},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.delete(obj)
    await session.commit()
