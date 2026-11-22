"""Phase 3 — 物理基礎建設：Cabling、Power、VPN。

  Cabling：Cable + CableTermination（端對端追蹤、Patch Panel 對映）
  Power：PowerPanel → PowerFeed → PowerOutlet → Device 電源連線
  VPN：VPNTunnel + IKEPolicy（IPsec/IKEv2、L2VPN/VXLAN/EVPN 留 future）

NetBox 風格設計但精簡：cable termination 用 (object_type, object_id) 多型，
不像 NetBox 拆 InterfaceTermination/RearPortTermination 等多表。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    true,
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
    # 介面名稱可能很長（Windows NDIS 過濾介面描述可達 70+ 字）→ 255（見 migration 0096）
    name: Mapped[str] = mapped_column(String(255), nullable=False)
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
    # 誰匯入的：`librenms:<實例 id>`。LibreNMS 完整讀到這台裝置、不再回報這個埠時由同步清掉；
    # NULL＝使用者建立（或尚未被認領的舊資料），同步一律不刪（2026-09-27）
    source_origin: Mapped[str | None] = mapped_column(String(64))

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
    # 規劃容量（W）：人工確認的值，null＝未設定。不由電壓 × 電流推算：交流的 V × A 是視在功率，
    # 功率因數、三相各相負載沒建模之前，算出來的數字看起來精確其實不是（交接包 physical-v1.2）
    planning_capacity_w: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    warning_pct: Mapped[int] = mapped_column(Integer, default=80, server_default="80", nullable=False)
    capacity_basis: Mapped[str | None] = mapped_column(String(128))   # 容量的依據（誰核定、哪份文件）

    __table_args__ = (
        UniqueConstraint("panel_id", "name", name="power_feed_panel_name_uq"),
        CheckConstraint("phase IN ('single','three')", name="ck_power_feeds_phase_valid"),
        CheckConstraint("supply_type IN ('ac','dc')", name="ck_power_feeds_supply_valid"),
        CheckConstraint("planning_capacity_w IS NULL OR planning_capacity_w >= 0", name="ck_power_feed_capacity_nonneg"),
        CheckConstraint("warning_pct BETWEEN 1 AND 100", name="ck_power_feed_warning_pct"),
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
    # PSU 額定容量（不是耗電量，也不是整機負載）。rating_confirmed_at 為空而有值＝0247 以前填的舊值，
    # 當時畫面只寫「瓦數」，用途不確定 → 畫面標「舊值，請確認」，不自動轉成規劃負載
    max_watts: Mapped[int | None] = mapped_column(Integer)
    rating_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 平常這顆電源分攤整機負載的幾 %（管理員設定；不因為有兩顆就自動存 50/50）
    normal_share_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    description: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("device_id", "name", name="device_power_port_unique_name"),
        CheckConstraint("normal_share_pct IS NULL OR (normal_share_pct >= 0 AND normal_share_pct <= 100)",
                        name="ck_power_port_share_range"),
    )


class DevicePowerProfile(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """裝置的規劃電力（一對一）：整台只填一次的規劃負載，與怎麼分給各顆電源。

    planning_input_w：null＝未知（不當成 0），0＝明確填零。是規劃值，不是即時量測。
    """

    __tablename__ = "device_power_profiles"

    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, unique=True)
    planning_input_w: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    value_basis: Mapped[str | None] = mapped_column(String(24))   # manual_estimate / vendor_reference / measurement_reference
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    supply_mode: Mapped[str] = mapped_column(String(16), default="unknown", server_default="unknown", nullable=False)
    failover_capability: Mapped[str] = mapped_column(
        String(24), default="unknown", server_default="unknown", nullable=False)
    primary_port_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("device_power_ports.id", ondelete="SET NULL"), index=True)
    include_in_planning: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)
    exclude_reason: Mapped[str | None] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)

    __table_args__ = (
        CheckConstraint("planning_input_w IS NULL OR planning_input_w >= 0", name="ck_power_profile_load_nonneg"),
        CheckConstraint(
            "value_basis IS NULL OR value_basis IN ('manual_estimate','vendor_reference','measurement_reference')",
            name="ck_power_profile_basis"),
        CheckConstraint("supply_mode IN ('unknown','single','shared','active_standby','custom')",
                        name="ck_power_profile_mode"),
        CheckConstraint("failover_capability IN ('unknown','confirmed_full_load','insufficient')",
                        name="ck_power_profile_failover"),
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
    # 同步建立的通道：`<來源>:<實例 id>`（例如 `fortigate:<uuid>`）；手動建立的是 NULL。
    # 以前以「防火牆名稱/」前綴認定歸屬 → 防火牆改名後舊通道成了孤兒、刪掉防火牆也不會清（2026-09-26）
    source_origin: Mapped[str | None] = mapped_column(String(64), index=True)

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
