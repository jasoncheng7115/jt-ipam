"""物件級權限（OWASP A01：deny-by-default）。"""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Permission(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "permissions"

    object_type: Mapped[str] = mapped_column(String(16), nullable=False)  # section / subnet
    object_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    principal_type: Mapped[str] = mapped_column(String(8), nullable=False)  # user / group
    principal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    level: Mapped[str] = mapped_column(String(8), nullable=False)  # read / write / admin

    __table_args__ = (
        CheckConstraint(
            "object_type IN ('section','subnet')",
            name="permission_object_type_valid",
        ),
        CheckConstraint(
            "principal_type IN ('user','group')",
            name="permission_principal_type_valid",
        ),
        CheckConstraint(
            "level IN ('read','write','admin')",
            name="permission_level_valid",
        ),
        UniqueConstraint(
            "object_type",
            "object_id",
            "principal_type",
            "principal_id",
            name="permission_unique",
        ),
    )
