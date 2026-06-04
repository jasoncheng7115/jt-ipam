"""通用背景任務記錄（給 UI 任務區用）。

每個長時間操作（LibreNMS sync、OPNsense sync、phpIPAM migration、scanner run...）
都在這張表登記一筆 row，UI 統一去 /api/v1/tasks 列。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin


class BackgroundTask(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "background_tasks"

    # 例：librenms.sync / dns.sync / phpipam.migration / scanner.run
    kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)

    # 對應的物件（選用）— 例如 LibreNMSInstance.id
    target_type: Mapped[str | None] = mapped_column(String(64))
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    target_label: Mapped[str | None] = mapped_column(Text)  # 顯示用，例如 "mon5"

    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
    )

    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)

    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()",
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','running','succeeded','failed','cancelled')",
            name="background_task_status_valid",
        ),
        CheckConstraint(
            "progress BETWEEN 0 AND 100",
            name="background_task_progress_range",
        ),
    )
