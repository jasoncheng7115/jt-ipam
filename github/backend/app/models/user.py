"""使用者 / 群組 / 使用者偏好 / API Token。

OWASP A01 / A04 / A07：
- password_hash 永遠是 argon2id（外部認證者為 NULL）
- totp_secret_enc 為 AES-GCM 密文 + nonce
- failed_login_count / locked_until 用於帳號鎖定
- API Token 只存 sha256 雜湊，不存 plaintext
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import CITEXT, INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:  # pragma: no cover
    pass


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False, index=True)
    display_name: Mapped[str | None] = mapped_column(Text)

    password_hash: Mapped[str | None] = mapped_column(Text)  # NULL = 由外部 IdP 認證
    auth_provider: Mapped[str] = mapped_column(String(32), default="local", nullable=False)
    external_subject: Mapped[str | None] = mapped_column(Text)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # MFA — secret 存密文（A02）
    totp_secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    totp_nonce: Mapped[bytes | None] = mapped_column(LargeBinary)

    # 帳號鎖定（A07）
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_ip: Mapped[str | None] = mapped_column(INET)

    __table_args__ = (
        CheckConstraint(
            "auth_provider IN ('local','ldap','radius','saml','oidc')",
            name="auth_provider_valid",
        ),
        CheckConstraint(
            "(password_hash IS NOT NULL) OR (auth_provider <> 'local')",
            name="local_user_must_have_password",
        ),
    )


class Group(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "groups"

    name: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class UserGroupMember(Base):
    __tablename__ = "user_group_members"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("groups.id", ondelete="CASCADE"),
        primary_key=True,
    )


class APIToken(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """API Token；只存 sha256 雜湊（A07）。"""

    __tablename__ = "api_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, unique=True)
    token_prefix: Mapped[str] = mapped_column(String(8), nullable=False, index=True)

    scopes: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, nullable=False)
    object_filters: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_ip: Mapped[str | None] = mapped_column(INET)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserPreference(Base):
    __tablename__ = "user_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    locale: Mapped[str] = mapped_column(String(8), default="zh-TW", nullable=False)
    theme: Mapped[str] = mapped_column(String(8), default="auto", nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Taipei", nullable=False)
    calendar: Mapped[str] = mapped_column(String(16), default="gregorian", nullable=False)
    page_size: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    # 每張表要顯示的欄位偏好，e.g. {"addresses": ["ip","hostname","state"], ...}
    table_columns: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # 註：上線判定閾值已改為全域系統設定（system_settings.online_grace_minutes），
    # 不再是個人偏好。
    # Dashboard 釘選的子網路 UUID 清單（顯示「常用子網路」卡片）
    pinned_subnet_ids: Mapped[list[str] | None] = mapped_column(JSONB)

    __table_args__ = (
        CheckConstraint("locale IN ('zh-TW','en-US')", name="locale_valid"),
        CheckConstraint("theme IN ('light','dark','auto')", name="theme_valid"),
        CheckConstraint("calendar IN ('gregorian','minguo')", name="calendar_valid"),
        UniqueConstraint("user_id", name="user_preferences_user_uq"),
    )
