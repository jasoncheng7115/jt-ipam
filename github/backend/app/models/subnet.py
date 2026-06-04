"""Subnet（子網）— phpIPAM 對齊；CIDR 用 PG 原生型別。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    ARRAY,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import CIDR, INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Subnet(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "subnets"

    section_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    master_subnet_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subnets.id", ondelete="SET NULL"),
        index=True,
    )

    cidr: Mapped[str] = mapped_column(CIDR, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    vlan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vlans.id", ondelete="SET NULL"),
    )
    vrf_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vrfs.id", ondelete="SET NULL"),
    )

    is_pool: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_full: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # phpIPAM 對齊欄位
    gateway: Mapped[str | None] = mapped_column(INET)
    dns_servers: Mapped[str | None] = mapped_column(Text)   # 逗號分隔多個
    location_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("locations.id", ondelete="SET NULL"), index=True,
    )
    # 歸檔：非 NULL = 已歸檔（保留資料但不顯示、不掃描；重疊檢查忽略已歸檔）
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    scan_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    scan_method: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        server_default=text("ARRAY['icmp']::varchar[]"),
        nullable=False,
    )

    threshold_pct: Mapped[int | None] = mapped_column(Integer)
    auto_dns: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    scan_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scan_agents.id", ondelete="SET NULL"),
    )

    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="SET NULL"),
        index=True,
    )

    custom_fields: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    __table_args__ = (
        Index("ix_subnets_cidr_gist", "cidr", postgresql_using="gist"),
    )
