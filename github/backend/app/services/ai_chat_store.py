"""AI chat 歷程的存取邏輯（與 endpoint 解耦，方便單元測試）。

- save_turn：存一輪「使用者問題 + 助手答案」；conversation_id 為 None 時開新對話
- list_conversations / get_conversation / get_messages：每位 user 看自己的
- list_all_conversations：admin 看全部
- purge_old：依保留天數清除舊對話（0 = 永久保留）
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_chat import AIChatConversation, AIChatMessage


def _title_from(text: str) -> str:
    t = " ".join(text.strip().split())
    return t[:200] if t else "(empty)"


async def save_turn(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID | None,
    user_text: str,
    assistant_text: str,
    model: str | None = None,
    elapsed_ms: int | None = None,
) -> AIChatConversation:
    """存一輪對話；回傳（新建或既有的）conversation。"""
    conv: AIChatConversation | None = None
    if conversation_id is not None:
        conv = await session.get(AIChatConversation, conversation_id)
        # 只能寫進自己的對話；對不上就當新對話開（避免越權寫別人歷程）
        if conv is not None and conv.user_id != user_id:
            conv = None
    if conv is None:
        conv = AIChatConversation(user_id=user_id, title=_title_from(user_text))
        session.add(conv)
        await session.flush()

    session.add(AIChatMessage(
        conversation_id=conv.id, role="user", content=user_text,
    ))
    session.add(AIChatMessage(
        conversation_id=conv.id, role="assistant", content=assistant_text,
        model=model, elapsed_ms=elapsed_ms,
    ))
    # 觸碰 updated_at（onupdate 只在欄位變更時才動，這裡明確標記活動時間）
    conv.updated_at = datetime.now(UTC)
    await session.flush()
    return conv


async def get_conversation(
    session: AsyncSession, *, conversation_id: uuid.UUID,
) -> AIChatConversation | None:
    return await session.get(AIChatConversation, conversation_id)


async def get_messages(
    session: AsyncSession, *, conversation_id: uuid.UUID,
) -> list[AIChatMessage]:
    rows = (await session.execute(
        select(AIChatMessage)
        .where(AIChatMessage.conversation_id == conversation_id)
        .order_by(AIChatMessage.created_at)
    )).scalars().all()
    return list(rows)


async def list_conversations(
    session: AsyncSession, *, user_id: uuid.UUID,
) -> list[AIChatConversation]:
    rows = (await session.execute(
        select(AIChatConversation)
        .where(AIChatConversation.user_id == user_id)
        .order_by(AIChatConversation.updated_at.desc())
    )).scalars().all()
    return list(rows)


async def list_all_conversations(
    session: AsyncSession, *, limit: int = 500,
) -> list[AIChatConversation]:
    rows = (await session.execute(
        select(AIChatConversation)
        .order_by(AIChatConversation.updated_at.desc())
        .limit(limit)
    )).scalars().all()
    return list(rows)


async def message_counts(
    session: AsyncSession, conversation_ids: list[uuid.UUID],
) -> dict[uuid.UUID, int]:
    if not conversation_ids:
        return {}
    rows = (await session.execute(
        select(AIChatMessage.conversation_id, func.count())
        .where(AIChatMessage.conversation_id.in_(conversation_ids))
        .group_by(AIChatMessage.conversation_id)
    )).all()
    return {cid: n for cid, n in rows}


async def delete_conversation(
    session: AsyncSession, *, conversation_id: uuid.UUID,
) -> None:
    conv = await session.get(AIChatConversation, conversation_id)
    if conv is not None:
        await session.delete(conv)
        await session.flush()


async def purge_old(session: AsyncSession, *, retention_days: int) -> int:
    """清除 updated_at 早於保留期的對話；retention_days<=0 代表永久保留，不清。回傳清除筆數。"""
    if retention_days <= 0:
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    ids = (await session.execute(
        select(AIChatConversation.id).where(AIChatConversation.updated_at < cutoff)
    )).scalars().all()
    if not ids:
        return 0
    await session.execute(
        delete(AIChatConversation).where(AIChatConversation.id.in_(ids))
    )
    await session.flush()
    return len(ids)
