"""OPNsense firewall rule（從 OPNsense 拉進 jt-ipam 作為唯讀快取）。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class OPNsenseRule(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "opnsense_rules"

    firewall_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("opnsense_firewalls.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # OPNsense 內部那條 rule 的 UUID
    legacy_uuid: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sequence: Mapped[int | None] = mapped_column(Integer)

    # action: pass / block / reject
    action: Mapped[str | None] = mapped_column(String(16))
    interface: Mapped[str | None] = mapped_column(String(64))
    direction: Mapped[str | None] = mapped_column(String(8))   # in / out
    protocol: Mapped[str | None] = mapped_column(String(16))

    source_net: Mapped[str | None] = mapped_column(Text)
    source_port: Mapped[str | None] = mapped_column(String(64))
    destination_net: Mapped[str | None] = mapped_column(Text)
    destination_port: Mapped[str | None] = mapped_column(String(64))

    description: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()",
    )
