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


class DevicePort(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """裝置上的連接埠 / 介面；front/rear 可用 peer_port_id 互指做跳接面板 pass-through。"""

    __tablename__ = "device_ports"

    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    # network / front / rear / console / power
    type: Mapped[str] = mapped_column(String(16), default="network", nullable=False, server_default="network")
    # front↔rear pass-through 對應（跳接面板穿透）
    peer_port_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("device_ports.id", ondelete="SET NULL"),
    )
    position: Mapped[int | None] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(Text)
    # 此埠自身的實體 MAC（LibreNMS ifPhysAddress）；非 FDB/ARP 學到的對端 MAC
    mac_address: Mapped[str | None] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint("device_id", "name", name="device_port_unique_name"),
    )



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


class DevicePowerPort(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """裝置側電源埠（PSU / 電源輸入），可接到某個 PDU 插座（power_outlets）。

    NetBox PowerPort 風：一台裝置可有多個電源埠（雙電源 PSU1/PSU2），各自接到不同
    插座（A/B 迴路）做電源備援；outlet_id 為空＝尚未接線。
    """

    __tablename__ = "device_power_ports"

    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)   # 例：PSU1 / PSU2
    outlet_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("power_outlets.id", ondelete="SET NULL"),
        index=True,
    )
    max_watts: Mapped[int | None] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("device_id", "name", name="device_power_port_unique_name"),
    )


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

    # WireGuard 對接偵測：a 端本地公鑰 / 設定的對端公鑰。
    # 兩條 tunnel 互為對接 ⟺ A.peer_public_key == B.local_public_key 且反之亦然。
    local_public_key: Mapped[str | None] = mapped_column(Text)
    peer_public_key: Mapped[str | None] = mapped_column(Text)

    encryption_algo: Mapped[str | None] = mapped_column(String(32))
    auth_algo: Mapped[str | None] = mapped_column(String(32))
    # 對接是怎麼判定的：wireguard_pubkey（公鑰，可靠）/ ipsec_endpoint（端點比對，best-effort）。
    # 給 UI 標示對接可信度用；None = 尚未自動對接到對端裝置。
    pairing_method: Mapped[str | None] = mapped_column(String(24))
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
