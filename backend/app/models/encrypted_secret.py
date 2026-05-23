"""加密欄位儲存表（OWASP A04）。

任何敏感欄位（DNS 帳密、SNMP community、API key、TOTP secret 等）
都用 AES-256-GCM 加密後存到這張表，並以 (object_type, object_id, field) 索引。
"""

from __future__ import annotations

import uuid

from sqlalchemy import LargeBinary, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class EncryptedSecret(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "encrypted_secrets"

    object_type: Mapped[str] = mapped_column(String(32), nullable=False)
    object_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    field: Mapped[str] = mapped_column(String(64), nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_id: Mapped[str] = mapped_column(String(64), default="primary", nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "object_type",
            "object_id",
            "field",
            "key_id",
            name="encrypted_secret_unique",
        ),
    )
