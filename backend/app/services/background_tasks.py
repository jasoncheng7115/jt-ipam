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

from sqlalchemy import select, update
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


# 不是 jt-ipam-sync 排程寫的作業：代理推上來的回報、使用者發起的探測、各自 timer 跑的資料庫更新。
# 系統診斷用「最後一筆排程作業」判斷 jt-ipam-sync.timer 有沒有在跑，這幾種要排除，
# 否則代理每 5 分鐘一列會把停擺的排程遮掉。
NOT_SYNC_TIMER_KINDS = ("ip.identify", "rustdesk.sync", "isc_dhcp.sync",
                        "oui.refresh", "recog.refresh", "geoip.refresh")

# 資料庫更新（沒有對應的整合物件）在作業頁上的目標名稱；也是 upsert 的鍵，所以要固定
REFRESH_LABELS = {
    "oui.refresh": "Wireshark manuf",
    "recog.refresh": "Recog",
    "geoip.refresh": "MaxMind GeoIP",
}


async def record_refresh(
    session: AsyncSession, kind: str, *, ok: bool,
    summary: dict[str, Any] | None = None, error: str | None = None,
) -> None:
    """OUI／Recog／GeoIP 的排程更新：跟整合的排程同步一樣每種只留一列。絕不 raise。"""
    await upsert_scheduled_task(session, kind=kind, target_type="system", target_id=None,
                                target_label=REFRESH_LABELS[kind], ok=ok, summary=summary, error=error)


async def record_finished_task(
    session: AsyncSession, *, kind: str, ok: bool,
    target_type: str | None = None, target_id: uuid.UUID | None = None,
    target_label: str | None = None, actor_user_id: uuid.UUID | None = None,
    started_at: datetime | None = None,
    summary: dict[str, Any] | None = None, error: str | None = None,
) -> None:
    """在請求裡同步做完的手動操作（「立即更新」）補一列已完成的作業，作業頁才看得到誰、何時、結果。

    每次手動一列（跟 spawn_task 一樣），不 upsert。絕不 raise：記不進去不可以讓操作本身失敗。
    """
    now = datetime.now(UTC)
    try:
        session.add(BackgroundTask(
            kind=kind, trigger="manual", status="succeeded" if ok else "failed", progress=100,
            target_type=target_type, target_id=target_id,
            target_label=target_label or REFRESH_LABELS.get(kind), actor_user_id=actor_user_id,
            summary=summary, error=(error or None) if not ok else None,
            queued_at=started_at or now, started_at=started_at or now, finished_at=now,
        ))
        await session.commit()
    except Exception:
        logger.exception("record_finished_task failed for %s", kind)
        await session.rollback()


async def forget_scheduled_rows(session: AsyncSession, target_id: uuid.UUID) -> None:
    """刪掉某個整合時，一併拿掉它的排程心跳列（否則作業頁永遠留著一台已經不存在的來源）。
    由 integration_cleanup.forget_instance 呼叫，所有整合一體適用。"""
    from sqlalchemy import delete
    await session.execute(delete(BackgroundTask).where(
        BackgroundTask.trigger == "scheduled", BackgroundTask.target_id == target_id))


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

        kind = task.kind
        status, summary, error = "succeeded", None, None
        try:
            summary = await runner(sess, task)
            await sess.commit()           # 作業自己留下的變更；這裡失敗也算作業失敗
        except Exception as exc:
            logger.exception("background_task %s (%s) failed", task_id, kind)
            status, error = "failed", f"{type(exc).__name__}: {exc}"[:4096]
            # issue #43：作業裡的資料庫錯誤（唯一鍵衝突…）會讓這個 session 停在「交易已失敗」，
            # 不先還原的話，接下來什麼都寫不進去 —— 作業就永遠停在「執行中」
            try:
                await sess.rollback()
            except Exception:
                logger.exception("rollback after task %s failure failed", task_id)

    # 最終狀態用一個乾淨的 session 寫：作業那個 session 不管壞成什麼樣子，這筆都要寫得進去
    await _persist_final(task_id, status=status, summary=summary, error=error)


async def update_task_progress(task_id: uuid.UUID, summary: dict[str, Any], progress: int | None = None) -> None:
    """執行中的作業回報進度（例如憑證推送做到哪一步）：用自己的 session，只寫還在跑的作業，
    晚到的回報不會蓋掉最終結果。絕不 raise。"""
    values: dict[str, Any] = {"summary": summary}
    if progress is not None:
        values["progress"] = max(0, min(99, int(progress)))
    try:
        async with SessionLocal() as sess:
            await sess.execute(update(BackgroundTask).where(
                BackgroundTask.id == task_id, BackgroundTask.status == "running").values(**values))
            await sess.commit()
    except Exception:
        logger.exception("failed to write progress of task %s", task_id)


async def _persist_final(task_id: uuid.UUID, *, status: str, summary: dict[str, Any] | None,
                         error: str | None) -> None:
    values: dict[str, Any] = {"status": status, "error": error, "finished_at": datetime.now(UTC)}
    if status == "succeeded":
        values.update(summary=summary, progress=100)
    try:
        async with SessionLocal() as sess:
            await sess.execute(update(BackgroundTask).where(BackgroundTask.id == task_id).values(**values))
            await sess.commit()
    except Exception:
        logger.exception("failed to persist task %s final state", task_id)
