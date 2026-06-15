"""IP Address — phpIPAM 對齊 + v0.3 多源欄位。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, MACADDR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class IPAddress(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "ip_addresses"

    subnet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subnets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ip: Mapped[str] = mapped_column(INET, nullable=False)
    hostname: Mapped[str | None] = mapped_column(Text, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    mac: Mapped[str | None] = mapped_column(MACADDR, index=True)
    mac_source: Mapped[str | None] = mapped_column(String(16))  # 目前 MAC 的來源（ARP 優先序用）
    owner: Mapped[str | None] = mapped_column(Text)
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="SET NULL"),
    )
    switch_port: Mapped[str | None] = mapped_column(Text)
    # FDB 推得的交換器位置是否高信心（該 port 僅一個 MAC = 直連存取埠；
    # 多 MAC（uplink/trunk）→ False，前端以灰色 + tooltip 標示）
    switch_port_confident: Mapped[bool | None] = mapped_column(Boolean)

    exclude_from_ping: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 此 IP「略過」的探測項目（扣除）；icmp 與 exclude_from_ping 雙向同步以保留既有行為。
    excluded_probes: Mapped[list[str]] = mapped_column(
        ARRAY(String), server_default=text("'{}'::varchar[]"), nullable=False,
    )
    # OS 偵測結果：原始字串 + 正規化家族 key（前端依 family 配 icon）。see core/os_fingerprint.py
    os_guess: Mapped[str | None] = mapped_column(String(160))
    os_family: Mapped[str | None] = mapped_column(String(24))
    # 各 probe 上次被執行的時間（由 report 回填），給「下次到期」顯示用。{"icmp": "...", "os": "..."}
    probe_last_run: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ptr_ignore: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)

    custom_fields: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="SET NULL"),
        index=True,
    )

    # feature A：固定以某來源的 hostname 為準（NULL = 跟全域優先序）
    hostname_source_pin: Mapped[str | None] = mapped_column(String(16))

    # v0.3 多來源
    discovery_source: Mapped[str] = mapped_column(String(16), default="manual", nullable=False)
    # 自動判定：此 IP 目前有 DHCP 租約（由 OPNsense DHCP lease 同步維護，與手動 state 分開）
    in_dhcp_lease: Mapped[bool] = mapped_column(default=False, nullable=False, server_default=text("false"))
    last_seen_scanner: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_librenms: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_dns: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    effective_status: Mapped[str | None] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint("subnet_id", "ip", name="ip_subnet_ip_uq"),
        CheckConstraint(
            "state IN ('active','reserved','offline','dhcp','used')",
            name="ip_state_valid",
        ),
        CheckConstraint(
            "discovery_source IN ('manual','scanner','librenms','dns','proxmox','opnsense','phpipam')",
            name="ip_discovery_source_valid",
        ),
        Index("ix_ip_addresses_ip_gist", "ip", postgresql_using="gist"),
    )
