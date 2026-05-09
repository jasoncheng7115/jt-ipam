"""認證服務：密碼登入、帳號鎖定、JWT。

OWASP 對應：
- A02：密碼用 argon2id（core.security.hash_password），自動 rehash
- A07：失敗計數 + 暫時鎖定、JWT 短有效期、refresh token 旋轉
- A09：所有 login 嘗試（成功/失敗）寫入 audit log
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import append_audit
from app.core.config import get_settings
from app.core.security import (
    create_access_token,
    decode_access_token,
    password_needs_rehash,
    verify_password,
)
from app.models.user import User

# A07：lockout 政策
_MAX_FAILED_ATTEMPTS: Final[int] = 5
_LOCK_DURATION: Final[timedelta] = timedelta(minutes=15)


class AuthError(Exception):
    """所有 auth 失敗的基底例外；endpoint 層轉成 401。

    刻意統一訊息以避免 user enumeration（A07）。
    """

    public_message: str = "Invalid credentials"


class InvalidCredentials(AuthError):
    pass


class AccountLocked(AuthError):
    public_message = "Account temporarily locked"


class AccountInactive(AuthError):
    public_message = "Account is not active"


class TokenInvalid(AuthError):
    public_message = "Invalid or expired token"


async def authenticate(
    session: AsyncSession,
    *,
    username: str,
    password: str,
    actor_ip: str | None,
    actor_user_agent: str | None,
    request_id: str | None,
) -> User:
    """以使用者名 / Email + 密碼驗證；同步處理失敗計數與鎖定。

    成功時：重置 failed_login_count、寫 audit、回傳 User。
    失敗時：累加計數、超過閾值鎖定、寫 audit、丟例外。
    """
    # 用 username 或 email 查詢（CITEXT 已經大小寫不敏感）
    stmt = select(User).where((User.username == username) | (User.email == username))
    user = (await session.execute(stmt)).scalar_one_or_none()

    now = datetime.now(UTC)

    # 一律執行密碼比對（防 user enumeration timing attack）
    dummy_hash = "$argon2id$v=19$m=65536,t=3,p=4$00000000000000000000000000000000$" \
                 "00000000000000000000000000000000000000000000"
    target_hash = (user.password_hash if (user and user.password_hash) else dummy_hash)
    password_ok = verify_password(password, target_hash)

    async def _audit(action: str, *, success: bool, reason: str | None = None) -> None:
        await append_audit(
            session,
            actor_user_id=str(user.id) if user else None,
            actor_ip=actor_ip,
            actor_user_agent=actor_user_agent,
            object_type="auth",
            object_id=str(user.id) if user else None,
            action=action,
            diff={
                "username": username,
                "success": success,
                "reason": reason,
            },
            request_id=request_id,
        )

    if user is None or not password_ok:
        if user is not None:
            user.failed_login_count = (user.failed_login_count or 0) + 1
            if user.failed_login_count >= _MAX_FAILED_ATTEMPTS:
                user.locked_until = now + _LOCK_DURATION
        await _audit("login_failed", success=False, reason="invalid_credentials")
        await session.commit()
        raise InvalidCredentials

    if not user.is_active:
        await _audit("login_failed", success=False, reason="inactive")
        await session.commit()
        raise AccountInactive

    if user.locked_until is not None and user.locked_until > now:
        await _audit("login_failed", success=False, reason="locked")
        await session.commit()
        raise AccountLocked

    if user.auth_provider != "local":
        # 僅本機帳號走此流程；外部 IdP 走 OIDC/SAML
        await _audit("login_failed", success=False, reason="external_account")
        await session.commit()
        raise InvalidCredentials

    # 成功
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    user.last_login_ip = actor_ip

    if password_needs_rehash(user.password_hash or ""):
        # argon2 參數提升時自動 rehash（A02）
        from app.core.security import hash_password
        user.password_hash = hash_password(password)

    await _audit("login_success", success=True)
    await session.commit()
    return user


def issue_access_token(user: User) -> str:
    return create_access_token(
        subject=str(user.id),
        extra_claims={
            "username": user.username,
            "is_admin": user.is_admin,
            "type": "access",
        },
    )


def issue_refresh_token(user: User) -> str:
    """Refresh token 走 JWT；放在 HttpOnly cookie。"""
    settings = get_settings()
    return create_access_token(
        subject=str(user.id),
        extra_claims={"type": "refresh", "jti": secrets.token_urlsafe(16)},
        expires_in_minutes=settings.refresh_token_expire_days * 24 * 60,
    )


def decode_token(token: str, *, expected_type: str) -> dict[str, object]:
    try:
        payload = decode_access_token(token)
    except Exception as exc:  # noqa: BLE001
        raise TokenInvalid from exc
    if payload.get("type") != expected_type:
        raise TokenInvalid
    return payload
