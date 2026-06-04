"""VLAN / VLAN Domain。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import CITEXT, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class VLANDomain(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "vlan_domains"

    name: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)


class VLAN(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "vlans"

    domain_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vlan_domains.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    # 客戶 / 區段歸屬（可篩選）
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="SET NULL"),
        index=True,
    )
    section_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sections.id", ondelete="SET NULL"),
        index=True,
    )

    __table_args__ = (
        CheckConstraint("number BETWEEN 1 AND 4094", name="vlan_number_range"),
        UniqueConstraint("domain_id", "number", name="vlan_domain_number_uq"),
    )


class DeviceVLAN(Base, UUIDPrimaryKeyMixin):
    """LibreNMS 裝置 ↔ VLAN 對應（feature C，由 LibreNMS sync 寫入）。

    刻意掛在 librenms_devices（拉進來的裝置）而非 jt-ipam Device：使用者要求純
    pull-only、不需先把 LibreNMS 裝置建成 jt-ipam Device 就能看到 VLAN 對應。
    """

    __tablename__ = "device_vlans"

    librenms_device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("librenms_devices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    vlan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vlans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source: Mapped[str] = mapped_column(String(16), default="librenms", nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "librenms_device_id", "vlan_id", name="uq_device_vlans_ldev_vlan",
        ),
    )
