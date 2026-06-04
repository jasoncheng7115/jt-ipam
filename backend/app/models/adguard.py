"""AdGuard Home 整合 — 拉 clients / DNS rewrites 來補充 IPAM 資料。

AdGuard 走 HTTP Basic Auth；密碼以 AES-GCM 加密儲存（aad 綁 instance id）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AdGuardInstance(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "adguard_instances"

    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    api_url: Mapped[str] = mapped_column(Text, nullable=False)

    api_user: Mapped[str] = mapped_column(String(128), nullable=False)
    api_password_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    api_password_nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # 拉哪些資料
    sync_clients: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sync_rewrites: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    sync_interval_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    description: Mapped[str | None] = mapped_column(Text)
