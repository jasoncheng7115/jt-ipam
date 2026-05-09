"""Location 與 Rack。"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Integer, Numeric, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Location(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "locations"

    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    address: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[float | None] = mapped_column(Numeric(10, 7))
    longitude: Mapped[float | None] = mapped_column(Numeric(10, 7))
    description: Mapped[str | None] = mapped_column(Text)


class Rack(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "racks"

    location_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("locations.id", ondelete="SET NULL"),
        index=True,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    u_height: Mapped[int] = mapped_column(Integer, default=42, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
