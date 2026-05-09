"""Audit log read endpoint（admin only）+ chain verify。

寫入由各 service 透過 app.core.audit.append_audit；這裡只提供 admin 查看 + 鏈完整性 verify。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import require_admin
from app.core.audit import verify_chain
from app.core.db import get_session
from app.models.audit import AuditLog
from app.schemas.base import Paginated, StrictModel

router = APIRouter(
    prefix="/audit",
    tags=["audit"],
    dependencies=[Depends(require_admin)],
)


class AuditLogRead(StrictModel):
    id: int
    ts: datetime
    actor_user_id: uuid.UUID | None
    actor_ip: str | None
    actor_user_agent: str | None
    object_type: str
    object_id: uuid.UUID | None
    action: str
    diff: dict[str, Any] | None
    request_id: uuid.UUID | None
    prev_hash_hex: str
    this_hash_hex: str

    @classmethod
    def from_orm_row(cls, row: AuditLog) -> "AuditLogRead":
        return cls(
            id=row.id,
            ts=row.ts,
            actor_user_id=row.actor_user_id,
            actor_ip=str(row.actor_ip) if row.actor_ip else None,
            actor_user_agent=row.actor_user_agent,
            object_type=row.object_type,
            object_id=row.object_id,
            action=row.action,
            diff=row.diff,
            request_id=row.request_id,
            prev_hash_hex=row.prev_hash.hex(),
            this_hash_hex=row.this_hash.hex(),
        )


class ChainVerifyResult(BaseModel):
    ok: bool
    broken_at_id: int | None
    checked: int


@router.get("", response_model=Paginated[AuditLogRead])
async def list_audit(
    session: Annotated[AsyncSession, Depends(get_session)],
    object_type: str | None = None,
    object_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    action: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Any:
    base = select(AuditLog)
    if object_type:
        base = base.where(AuditLog.object_type == object_type)
    if object_id is not None:
        base = base.where(AuditLog.object_id == object_id)
    if actor_user_id is not None:
        base = base.where(AuditLog.actor_user_id == actor_user_id)
    if action:
        base = base.where(AuditLog.action == action)
    if since is not None:
        base = base.where(AuditLog.ts >= since)
    if until is not None:
        base = base.where(AuditLog.ts <= until)

    total = (
        await session.execute(select(func.count()).select_from(base.subquery()))
    ).scalar_one()
    rows = (
        await session.execute(
            base.order_by(desc(AuditLog.id)).offset(offset).limit(limit)
        )
    ).scalars().all()
    return {
        "items": [AuditLogRead.from_orm_row(r) for r in rows],
        "total": total,
        "page": offset // limit + 1,
        "page_size": limit,
    }


@router.post("/verify", response_model=ChainVerifyResult)
async def verify_audit_chain(
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int | None, Query(ge=1, le=1_000_000)] = None,
) -> Any:
    ok, broken = await verify_chain(session, limit=limit)
    # checked = total rows we walked
    if limit is None:
        checked_q = (
            await session.execute(select(func.count()).select_from(AuditLog))
        ).scalar_one()
    else:
        checked_q = limit
    return ChainVerifyResult(ok=ok, broken_at_id=broken, checked=int(checked_q))
