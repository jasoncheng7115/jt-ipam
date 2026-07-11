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


async def upsert_scheduled_task(
    session: AsyncSession,
    *,
    kind: str,
    target_type: str | None,
    target_id: uuid.UUID | None,
    target_label: str | None,
    ok: bool,
    summary: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """排程同步的心跳：每個整合只保留一列 trigger='scheduled' 的 row，每輪 upsert 更新
    （不累積），讓作業表格看得到排程同步、又不會被每 5 分鐘的排程灌爆。

    絕不 raise —— 失敗只 log + rollback，以免拖垮排程迴圈。呼叫前 session 應為乾淨狀態
    （各整合區塊 sync 成功已 commit、失敗已 rollback + 寫 last_error + commit）。
    """
    now = datetime.now(UTC)
    try:
        conds = [BackgroundTask.kind == kind, BackgroundTask.trigger == "scheduled"]
        # 以 target_id 當心跳鍵；無 id 者退回 target_label
        if target_id is not None:
            conds.append(BackgroundTask.target_id == target_id)
        else:
            conds.append(BackgroundTask.target_label == target_label)
        row = (
            await session.execute(select(BackgroundTask).where(*conds).limit(1))
        ).scalar_one_or_none()
        if row is None:
            row = BackgroundTask(
                kind=kind, trigger="scheduled", target_type=target_type,
                target_id=target_id, target_label=target_label, actor_user_id=None,
            )
            session.add(row)
        row.target_type = target_type
        row.target_label = target_label
        row.status = "succeeded" if ok else "failed"
        row.progress = 100
        row.summary = summary
        row.error = (error or None) if not ok else None
        # queued_at 也更新成 now → 心跳列永遠排在最上面（最新）
        row.queued_at = now
        row.started_at = now
        row.finished_at = now
        await session.commit()
    except Exception:
        logger.exception("upsert_scheduled_task failed for %s / %s", kind, target_label)
        await session.rollback()


async def spawn_task(
    *,
    session: AsyncSession,
    kind: str,
    target_type: str | None = None,
    target_id: uuid.UUID | None = None,
    target_label: str | None = None,
    actor_user_id: uuid.UUID | None = None,
    trigger: str = "manual",
    runner: TaskRunner,
) -> BackgroundTask:
    """建立 BackgroundTask row 並背景啟動 runner。

    回傳 (已 commit 的) BackgroundTask；caller 通常把 id 回給前端，前端 poll
    /api/v1/tasks/{id} 或在 Tasks 頁列出。
    """
    task = BackgroundTask(
        kind=kind,
        status="pending",
        trigger=trigger,
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
