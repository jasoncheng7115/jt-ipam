"""VLAN / VLAN Domain。"""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Integer, Text, UniqueConstraint
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

    __table_args__ = (
        CheckConstraint("number BETWEEN 1 AND 4094", name="vlan_number_range"),
        UniqueConstraint("domain_id", "number", name="vlan_domain_number_uq"),
    )
