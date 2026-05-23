"""站內通知 + Webhook 出站定義。

OWASP A04 / A06：
- WebhookSubscription 的 secret 雖然是 HMAC 簽章用的（非機密請求），仍加密
- 出站 URL 由 safe_http 過 SSRF 白名單；此處只儲存定義
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    ARRAY,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Notification(Base, UUIDPrimaryKeyMixin):
    """站內通知（給特定 user 看）。"""

    __tablename__ = "notifications"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    severity: Mapped[str] = mapped_column(String(8), default="info", nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(Text)
    object_type: Mapped[str | None] = mapped_column(String(32))
    object_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )


class WebhookSubscription(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Webhook 出站訂閱。

    `secret_enc` / `secret_nonce`：HMAC-SHA256 簽章金鑰，AES-GCM 加密儲存。
    `events`：訂閱的事件類別（例 "subnet.created", "ip.allocated"）。
    `target_url`：發送目標；每次發送前會經 safe_http SSRF 檢查。
    """

    __tablename__ = "webhook_subscriptions"

    name: Mapped[str] = mapped_column(Text, nullable=False)
    target_url: Mapped[str] = mapped_column(Text, nullable=False)
    events: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, nullable=False)
    secret_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    secret_nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    failure_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    headers: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
