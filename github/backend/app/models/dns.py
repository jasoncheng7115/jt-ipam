"""DNS 整合 model：DNSServer / DNSZone / DNSRecord。

設計：
- DNSServer 是 provider abstraction（type=powerdns/bind9/unbound_opnsense/windows_dns）
- credentials 用 EncryptedSecret 表存（aad 綁 server id）；此處欄位只存連線 metadata
- DNSRecord.source 區分：from_ipam（IPAM 推送）/ from_dns_pulled（pull 自 server）/ manual
- DNSRecord.consistency_state：consistent / dns_only / ipam_only / mismatch — 不一致報表用
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class DNSServer(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "dns_servers"

    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    # 連線 metadata（不含密鑰；密鑰在 encrypted_secrets 表）
    api_url: Mapped[str | None] = mapped_column(Text)
    server_address: Mapped[str | None] = mapped_column(Text)  # bind9 host
    extra_config: Mapped[str | None] = mapped_column(Text)    # JSON：tsig_keyname、winrm port…
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sync_interval_seconds: Mapped[int] = mapped_column(
        Integer, default=300, nullable=False
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "type IN ('powerdns','bind9','unbound_opnsense','windows_dns','univention_ucs')",
            name="ck_dns_servers_type_valid",
        ),
    )


class DNSZone(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "dns_zones"

    server_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dns_servers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    managed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # 與哪些 subnet 關聯（forward 關聯由 subnet.auto_dns + zone 自動匹配；
    # reverse 由 cidr 推算對應 in-addr.arpa zone）
    associated_subnet_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), default=list, nullable=False
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("type IN ('forward','reverse')", name="ck_dns_zones_type_valid"),
        UniqueConstraint("server_id", "name", name="dns_zone_server_name_uq"),
    )


class DNSRecord(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "dns_records"

    zone_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dns_zones.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(8), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    ttl: Mapped[int] = mapped_column(Integer, default=3600, nullable=False)
    source: Mapped[str] = mapped_column(
        String(16), default="manual", nullable=False
    )
    consistency_state: Mapped[str] = mapped_column(
        String(16), default="consistent", nullable=False
    )
    ipam_address_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ip_addresses.id", ondelete="SET NULL"),
        index=True,
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    __table_args__ = (
        CheckConstraint(
            "type IN ('A','AAAA','PTR','CNAME','MX','TXT','SRV','NS','SOA')",
            name="ck_dns_records_type_valid",
        ),
        CheckConstraint(
            "source IN ('manual','from_ipam','from_dns_pulled')",
            name="ck_dns_records_source_valid",
        ),
        CheckConstraint(
            "consistency_state IN ('consistent','dns_only','ipam_only','mismatch')",
            name="ck_dns_records_consistency_valid",
        ),
        UniqueConstraint("zone_id", "name", "type", "value", name="dns_record_unique"),
    )
