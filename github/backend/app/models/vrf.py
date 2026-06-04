"""VRF（Virtual Routing & Forwarding）。"""

from __future__ import annotations

from sqlalchemy import Boolean, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class VRF(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "vrfs"

    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    rd: Mapped[str | None] = mapped_column(Text)  # Route Distinguisher
    description: Mapped[str | None] = mapped_column(Text)
    allow_overlap: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
