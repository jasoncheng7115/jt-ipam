"""SSH 連線憑證（by-user 個別保管）。

設計重點（見 docs SSH 資安要求）：
- 每筆綁定 owner_user_id，僅擁有者可取用（停用使用者即不可用）。
- 密碼／私鑰／passphrase 各自以信封加密（per-field 隨機 DEK，DEK 由主 KEK 包覆）
  存於 secrets_enc（JSONB）；明文絕不落 DB／log／回前端。
- scope：target_ip_id 指定某 IP；NULL = 個人預設（可用於該使用者有權限連線的任一 IP）。
- 取用時仍須通過 can_use_ssh(target) 授權；前端只持 credential_id（reference）。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class SSHCredential(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "ssh_credentials"

    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    label: Mapped[str] = mapped_column(Text, nullable=False)
    username: Mapped[str] = mapped_column(Text, nullable=False)
    auth_type: Mapped[str] = mapped_column(String(8), nullable=False)  # password | key
    # scope：綁定某 IP；NULL = 個人預設（任一可連線 IP）
    target_ip_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ip_addresses.id", ondelete="CASCADE"), index=True,
    )
    # {field: {ct,n,dek,dn}}（信封加密；只放有填的欄位 password/private_key/passphrase）
    secrets_enc: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
