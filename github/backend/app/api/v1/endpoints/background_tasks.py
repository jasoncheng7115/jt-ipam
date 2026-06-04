"""Background tasks endpoints — UI 任務區資料來源。

列表（含 filter）+ 單筆。任何 admin 可看；普通使用者只看自己 actor_user_id 的（A01）。
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser
from app.core.db import get_session
from app.models.background_task import BackgroundTask
from app.schemas.background_task import BackgroundTaskRead
from app.schemas.base import Paginated

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("", response_model=Paginated[BackgroundTaskRead])
async def list_tasks(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    status_in: str | None = Query(None, description="逗號分隔，e.g. running,pending"),
    kind: str | None = Query(None),
    active_only: bool = Query(False, description="只看 pending / running"),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=200),
) -> Paginated[BackgroundTaskRead]:
    stmt = select(BackgroundTask)
    count_stmt = select(func.count()).select_from(BackgroundTask)

    if not user.is_admin:
        stmt = stmt.where(BackgroundTask.actor_user_id == user.id)
        count_stmt = count_stmt.where(BackgroundTask.actor_user_id == user.id)

    if active_only:
        stmt = stmt.where(BackgroundTask.status.in_(["pending", "running"]))
        count_stmt = count_stmt.where(BackgroundTask.status.in_(["pending", "running"]))
    elif status_in:
        statuses = [s.strip() for s in status_in.split(",") if s.strip()]
        if statuses:
            stmt = stmt.where(BackgroundTask.status.in_(statuses))
            count_stmt = count_stmt.where(BackgroundTask.status.in_(statuses))

    if kind:
        stmt = stmt.where(BackgroundTask.kind == kind)
        count_stmt = count_stmt.where(BackgroundTask.kind == kind)

    stmt = (
        stmt.order_by(BackgroundTask.queued_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    total = int(await session.scalar(count_stmt) or 0)
    return Paginated[BackgroundTaskRead](
        items=[BackgroundTaskRead.model_validate(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.get("/{task_id}", response_model=BackgroundTaskRead)
async def get_task(
    task_id: uuid.UUID,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BackgroundTaskRead:
    task = await session.get(BackgroundTask, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    # 非 admin 只能看自己
    if not user.is_admin and task.actor_user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return BackgroundTaskRead.model_validate(task)
