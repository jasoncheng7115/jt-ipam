"""LibreNMS 整合 model。

設計：
- LibreNMSInstance：多站點支援（規格書 §6.10）；api_token 加密
- LibreNMSDevice：每次 sync 從 LibreNMS 抓回的裝置；legacy_id = LibreNMS device_id
- ARPEntry：從 LibreNMS API /resources/ip/arp/ 取得，自動補 IP 的 MAC
- FDBEntry：從 LibreNMS API /devices/{id}/fdb 取得，定位 MAC 在哪個 switch port
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, MACADDR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class LibreNMSInstance(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "librenms_instances"

    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    api_url: Mapped[str] = mapped_column(Text, nullable=False)

    api_token_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    api_token_nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # 細分開關（規格書 §6.10）
    sync_devices: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sync_arp: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sync_fdb: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sync_vlans: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    use_for_status: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    auto_add_devices: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, server_default=text("true"),
    )
    # 同步裝置時，把落在「既有且符合 scope」子網路內的裝置主 IP 自動建成 IPAddress
    # （discovery_source='librenms'）。預設開啟——使用者通常預期「接了 NMS 就會長出 IP」。
    # 只建裝置主 IP，不建 ARP 學到的鄰居（避免把雜訊端點灌進來）。
    auto_create_ips: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, server_default=text("true"),
    )

    # 限定 sync 解析 IP 的子網路範圍（解決重疊網段：A/B 客戶都用 192.168.1.x）。
    # 空 = 全域比對（向下相容）。存 subnet UUID 字串陣列。
    scope_subnet_ids: Mapped[list[Any] | None] = mapped_column(JSONB)

    sync_interval_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class LibreNMSDevice(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "librenms_devices"

    instance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("librenms_instances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    legacy_device_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    hostname: Mapped[str | None] = mapped_column(Text)
    sysname: Mapped[str | None] = mapped_column(Text)
    primary_ip: Mapped[str | None] = mapped_column(INET)
    hardware: Mapped[str | None] = mapped_column(Text)
    os: Mapped[str | None] = mapped_column(Text)
    version: Mapped[str | None] = mapped_column(Text)
    serial: Mapped[str | None] = mapped_column(Text)
    sysObjectID: Mapped[str | None] = mapped_column(Text)
    uptime: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str | None] = mapped_column(String(16))   # up / down

    # 對映到 jt-ipam 的 Device（如已連結）
    jt_ipam_device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="SET NULL"),
    )

    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "instance_id", "legacy_device_id", name="librenms_device_unique",
        ),
    )


class ARPEntry(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "arp_entries"

    ip: Mapped[str] = mapped_column(INET, nullable=False, index=True)
    mac: Mapped[str] = mapped_column(MACADDR, nullable=False, index=True)
    instance_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("librenms_instances.id", ondelete="SET NULL"),
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("librenms_devices.id", ondelete="SET NULL"),
    )
    interface: Mapped[str | None] = mapped_column(String(64))
    vrf: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(16), default="librenms", nullable=False)

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True,
    )

    __table_args__ = (
        UniqueConstraint("ip", "mac", "device_id", name="arp_entry_unique"),
    )


class FDBEntry(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "fdb_entries"

    mac: Mapped[str] = mapped_column(MACADDR, nullable=False, index=True)
    vlan_id_num: Mapped[int | None] = mapped_column(Integer)
    instance_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("librenms_instances.id", ondelete="SET NULL"),
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("librenms_devices.id", ondelete="SET NULL"),
    )
    port_name: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(16), default="librenms", nullable=False)

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "mac", "device_id", "port_name", "vlan_id_num",
            name="fdb_entry_unique",
        ),
    )
