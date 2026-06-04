"""AI chat 歷程持久化 model。

- AIChatConversation：一段對話（屬於某 user），title 取自第一句問題
- AIChatMessage：對話裡的逐則訊息（user / assistant），記錄當下用的 model 與耗時

保存的是「使用者問題 + 助手最終答案」的對話紀錄；不存 tool trace（那是 debug 用，
含內部資料，不適合長期保留）。每位 user 看自己的；admin 有獨立頁可看全部；
保留天數由 admin 統一設定（system_settings.ai_chat.retention_days）。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AIChatConversation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "ai_chat_conversations"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str | None] = mapped_column(String(200))

    messages: Mapped[list[AIChatMessage]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="AIChatMessage.created_at",
    )


class AIChatMessage(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "ai_chat_messages"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ai_chat_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user / assistant
    content: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(String(128))
    elapsed_ms: Mapped[int | None] = mapped_column(Integer)
    # clock_timestamp()（非 now()）：同一 transaction 內逐列遞增，保證同回合 user→assistant
    # 與跨回合的訊息排序正確（now() 是 transaction 級時間，整批會相同）
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()"), nullable=False,
    )

    conversation: Mapped[AIChatConversation] = relationship(back_populates="messages")
