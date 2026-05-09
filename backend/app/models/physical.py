"""Phase 3 — 物理基礎建設：Cabling、Power、VPN。

  Cabling：Cable + CableTermination（端對端追蹤、Patch Panel 對映）
  Power：PowerPanel → PowerFeed → PowerOutlet → Device 電源連線
  VPN：VPNTunnel + IKEPolicy（IPsec/IKEv2、L2VPN/VXLAN/EVPN 留 future）

NetBox 風格設計但精簡：cable termination 用 (object_type, object_id) 多型，
不像 NetBox 拆 InterfaceTermination/RearPortTermination 等多表。
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


# ─────────────────── Cabling ───────────────────


class Cable(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "cables"

    label: Mapped[str | None] = mapped_column(String(128))
    type: Mapped[str | None] = mapped_column(String(32))     # cat6 / fiber-mm / fiber-sm / power
    color: Mapped[str | None] = mapped_column(String(16))
    length_m: Mapped[float | None] = mapped_column(Numeric(6, 2))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="connected", nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('planned','connected','decommissioned')",
            name="ck_cables_status_valid",
        ),
    )


class CableTermination(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Cable 的兩端；object_type 多型（device / patch_panel_port / outlet 等）。"""

    __tablename__ = "cable_terminations"

    cable_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cables.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    side: Mapped[str] = mapped_column(String(1), nullable=False)  # 'A' / 'B'
    object_type: Mapped[str] = mapped_column(String(32), nullable=False)
    object_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    port_label: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        UniqueConstraint("cable_id", "side", name="cable_termination_unique_side"),
        CheckConstraint("side IN ('A','B')", name="ck_cable_terminations_side_valid"),
    )


# ─────────────────── Power ───────────────────


class PowerPanel(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "power_panels"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    location_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("locations.id", ondelete="SET NULL"),
    )
    description: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("name", "location_id", name="power_panel_unique"),
    )


class PowerFeed(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "power_feeds"

    panel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("power_panels.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    voltage_v: Mapped[int] = mapped_column(Integer, default=220, nullable=False)
    amperage_a: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    phase: Mapped[str] = mapped_column(String(8), default="single", nullable=False)
    supply_type: Mapped[str] = mapped_column(String(8), default="ac", nullable=False)
    rack_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("racks.id", ondelete="SET NULL"),
    )
    description: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("panel_id", "name", name="power_feed_panel_name_uq"),
        CheckConstraint("phase IN ('single','three')", name="ck_power_feeds_phase_valid"),
        CheckConstraint("supply_type IN ('ac','dc')", name="ck_power_feeds_supply_valid"),
    )


class PowerOutlet(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "power_outlets"

    feed_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("power_feeds.id", ondelete="SET NULL"),
        index=True,
    )
    rack_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("racks.id", ondelete="SET NULL"),
    )
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    # Phase 3：device 透過 PowerCord 抽象連到 outlet；MVP 直接用 device_id 簡化
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="SET NULL"),
    )
    description: Mapped[str | None] = mapped_column(Text)


# ─────────────────── VPN ───────────────────


class VPNTunnel(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "vpn_tunnels"

    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)

    a_device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="SET NULL"),
    )
    b_device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="SET NULL"),
    )
    a_endpoint: Mapped[str | None] = mapped_column(Text)   # IP / FQDN
    b_endpoint: Mapped[str | None] = mapped_column(Text)

    encryption_algo: Mapped[str | None] = mapped_column(String(32))
    auth_algo: Mapped[str | None] = mapped_column(String(32))
    description: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "type IN ('ipsec_ikev1','ipsec_ikev2','wireguard','openvpn',"
            "'l2tp','vxlan','vpls','evpn','other')",
            name="ck_vpn_tunnels_type_valid",
        ),
        CheckConstraint(
            "status IN ('planned','active','offline','decommissioned')",
            name="ck_vpn_tunnels_status_valid",
        ),
    )
