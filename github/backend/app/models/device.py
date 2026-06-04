"""Device — 簡潔設備清單。"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Device(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "devices"

    name: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    fqdn: Mapped[str | None] = mapped_column(Text)   # 完整網域名稱（如 sw1.dc.example.com）
    primary_ip_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ip_addresses.id", ondelete="SET NULL", use_alter=True),
    )
    type: Mapped[str] = mapped_column(String(16), default="other", nullable=False)
    vendor: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    serial: Mapped[str | None] = mapped_column(Text)
    location_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("locations.id", ondelete="SET NULL"),
    )
    rack_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("racks.id", ondelete="SET NULL"),
    )
    u_position: Mapped[int | None] = mapped_column(Integer)
    u_size: Mapped[int | None] = mapped_column(Integer)
    rack_face: Mapped[str | None] = mapped_column(String(8))   # front / rear（裝在機櫃前面或後面）
    # full（整 U 全寬，預設）/ left / right（半 U，同一 U 左右各放一台）
    rack_side: Mapped[str] = mapped_column(String(8), default="full", server_default="full", nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="SET NULL"),
        index=True,
    )
    custom_fields: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    __table_args__ = (
        CheckConstraint(
            "type IN ('server','switch','router','firewall','ap','storage','ipmi','other')",
            name="device_type_valid",
        ),
    )
