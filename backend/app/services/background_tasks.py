"""背景任務 spawn helper。

用 asyncio.create_task 把 long-running 操作丟到背景跑，立刻回 task_id 給前端。
每個背景 task 用自己的 session（FastAPI request 的 session 在回應後就關了）。

進階版（Phase 4）可以換成 RQ / Celery；目前 single-process / 4 worker 場景夠用。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal
from app.models.background_task import BackgroundTask

logger = logging.getLogger(__name__)

# 保留 fire-and-forget task 的強參照，避免被 GC 在跑完前回收（asyncio 只持弱參照）。
_BG_TASKS: set[asyncio.Task[Any]] = set()

# runner 簽名：(session, task) → 回 dict summary 或 raise
TaskRunner = Callable[[AsyncSession, BackgroundTask], Awaitable[dict[str, Any] | None]]


async def spawn_task(
    *,
    session: AsyncSession,
    kind: str,
    target_type: str | None = None,
    target_id: uuid.UUID | None = None,
    target_label: str | None = None,
    actor_user_id: uuid.UUID | None = None,
    runner: TaskRunner,
) -> BackgroundTask:
    """建立 BackgroundTask row 並背景啟動 runner。

    回傳 (已 commit 的) BackgroundTask；caller 通常把 id 回給前端，前端 poll
    /api/v1/tasks/{id} 或在 Tasks 頁列出。
    """
    task = BackgroundTask(
        kind=kind,
        status="pending",
        target_type=target_type,
        target_id=target_id,
        target_label=target_label,
        actor_user_id=actor_user_id,
        progress=0,
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)

    task_id = task.id
    # 排到 event loop；返回不等。保留參照到跑完才釋放（見 _BG_TASKS）。
    t = asyncio.create_task(_run(task_id, runner))
    _BG_TASKS.add(t)
    t.add_done_callback(_BG_TASKS.discard)
    return task


async def _run(task_id: uuid.UUID, runner: TaskRunner) -> None:
    """背景執行 runner，全程更新 BackgroundTask 狀態。

    用自己的 session — request 那個 session 已經關了。任何 exception 都吞掉
    並寫進 task.error，不要讓 asyncio loop 看到 unhandled exception。
    """
    async with SessionLocal() as sess:
        # 重新拿 row
        task = (
            await sess.execute(select(BackgroundTask).where(BackgroundTask.id == task_id))
        ).scalar_one_or_none()
        if task is None:
            logger.error("background_task %s missing on dispatch", task_id)
            return

        task.status = "running"
        task.started_at = datetime.now(UTC)
        await sess.commit()
        await sess.refresh(task)

        try:
            summary = await runner(sess, task)
            task.summary = summary
            task.status = "succeeded"
            task.progress = 100
            task.error = None
        except Exception as exc:
            logger.exception("background_task %s (%s) failed", task.id, task.kind)
            task.status = "failed"
            task.error = f"{type(exc).__name__}: {exc}"[:4096]
        finally:
            task.finished_at = datetime.now(UTC)
            try:
                await sess.commit()
            except Exception:
                logger.exception("failed to persist task %s final state", task_id)
                await sess.rollback()
