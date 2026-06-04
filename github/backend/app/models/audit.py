"""SHA-256 異動鏈（OWASP A08）。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, LargeBinary, String, Text, func
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    actor_ip: Mapped[str | None] = mapped_column(INET)
    actor_user_agent: Mapped[str | None] = mapped_column(Text)

    object_type: Mapped[str] = mapped_column(String(32), nullable=False)
    object_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    diff: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    request_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    prev_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    this_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, unique=True)

    __table_args__ = (
        Index("ix_audit_object", "object_type", "object_id"),
        Index("ix_audit_actor", "actor_user_id"),
        Index("ix_audit_ts", "ts"),
    )
