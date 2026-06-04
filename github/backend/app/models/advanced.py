"""Phase 3 進階模組（NetBox 風格補強，預設關閉）。

包括：
- Tenancy：TenantGroup → Tenant（多客戶 / 多部門）
- Contacts：Contact / ContactGroup / ContactRole / ContactAssignment
- Circuits：Provider → Circuit；CircuitType
- ASN
- Wireless：SSID、WirelessLink

這些表都是「選用」；UI 是否顯示由 Settings 控制（Phase 3.5）。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

# ─────────────────── Tenancy ───────────────────


class TenantGroup(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "tenant_groups"

    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenant_groups.id", ondelete="SET NULL"),
        index=True,
    )


class Tenant(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenant_groups.id", ondelete="SET NULL"),
        index=True,
    )
    description: Mapped[str | None] = mapped_column(Text)


# ─────────────────── Contacts ───────────────────


class ContactGroup(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "contact_groups"

    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("contact_groups.id", ondelete="SET NULL"),
    )


class ContactRole(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "contact_roles"

    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)


class Contact(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "contacts"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str | None] = mapped_column(String(128))
    phone: Mapped[str | None] = mapped_column(String(64))
    email: Mapped[str | None] = mapped_column(String(255))
    address: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)

    group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("contact_groups.id", ondelete="SET NULL"),
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="SET NULL"),
    )


class ContactAssignment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """把 Contact + Role 指派到任意物件（section / subnet / device / circuit / location ...）"""

    __tablename__ = "contact_assignments"

    contact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("contacts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("contact_roles.id", ondelete="SET NULL"),
    )
    object_type: Mapped[str] = mapped_column(String(32), nullable=False)
    object_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "contact_id", "role_id", "object_type", "object_id",
            name="contact_assignment_unique",
        ),
    )


# ─────────────────── ASN ───────────────────


class ASN(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "asns"

    asn: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    rir: Mapped[str | None] = mapped_column(String(16))   # APNIC / RIPE / ARIN / ...
    description: Mapped[str | None] = mapped_column(Text)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="SET NULL"),
    )

    __table_args__ = (
        # 16-bit (1..65535) 與 32-bit (0..2^32-1) 都允許；私有範圍標識可 RIR
        CheckConstraint("asn > 0 AND asn < 4294967295", name="ck_asns_range"),
    )


# ─────────────────── Circuits ───────────────────


class Provider(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "providers"

    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    asn: Mapped[int | None] = mapped_column(BigInteger)
    account_number: Mapped[str | None] = mapped_column(String(128))
    portal_url: Mapped[str | None] = mapped_column(Text)
    noc_contact: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)


class CircuitType(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "circuit_types"

    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)


class Circuit(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "circuits"

    cid: Mapped[str] = mapped_column(String(128), nullable=False)   # provider circuit id
    provider_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("providers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    type_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("circuit_types.id", ondelete="SET NULL"),
    )
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    install_date: Mapped[datetime | None] = mapped_column()
    contract_end_date: Mapped[datetime | None] = mapped_column()
    monthly_fee_cents: Mapped[int | None] = mapped_column(Integer)
    commit_rate_kbps: Mapped[int | None] = mapped_column(Integer)
    # 非對稱頻寬（上傳 / 下載），單位 kbps
    up_kbps: Mapped[int | None] = mapped_column(Integer)
    down_kbps: Mapped[int | None] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(Text)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="SET NULL"),
    )

    __table_args__ = (
        UniqueConstraint("provider_id", "cid", name="circuit_provider_cid_uq"),
        CheckConstraint(
            "status IN ('planned','provisioning','active','offline','decommissioned')",
            name="ck_circuits_status_valid",
        ),
    )


# ─────────────────── Wireless ───────────────────


class WirelessSSID(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "wireless_ssids"

    ssid: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    auth_type: Mapped[str | None] = mapped_column(String(32))   # wpa2-psk / wpa3-personal / 802.1x ...
    vlan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vlans.id", ondelete="SET NULL"),
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="SET NULL"),
    )


class WirelessLink(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "wireless_links"

    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    a_device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="SET NULL"),
    )
    b_device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="SET NULL"),
    )
    ssid: Mapped[str | None] = mapped_column(String(64))
    distance_m: Mapped[int | None] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(Text)
