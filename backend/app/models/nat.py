"""NAT 模組（phpIPAM 招牌）。"""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class NATTranslation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "nat_translations"

    name: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)  # one_to_one / many_to_one / port_forward
    src_ip_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ip_addresses.id", ondelete="SET NULL"),
    )
    dst_ip_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ip_addresses.id", ondelete="SET NULL"),
    )
    src_port: Mapped[int | None] = mapped_column(Integer)
    dst_port: Mapped[int | None] = mapped_column(Integer)
    protocol: Mapped[str] = mapped_column(String(8), default="any", nullable=False)
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="SET NULL"),
    )
    description: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "type IN ('one_to_one','many_to_one','port_forward')",
            name="nat_type_valid",
        ),
        CheckConstraint(
            "protocol IN ('tcp','udp','any')",
            name="nat_protocol_valid",
        ),
        CheckConstraint(
            "(src_port IS NULL OR src_port BETWEEN 1 AND 65535) "
            "AND (dst_port IS NULL OR dst_port BETWEEN 1 AND 65535)",
            name="nat_port_range",
        ),
    )
