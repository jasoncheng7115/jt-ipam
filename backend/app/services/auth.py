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
    hash_password,
    password_needs_rehash,
    verify_password,
)
from app.models.user import User
from app.services import ldap_auth, radius_auth

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
    """以使用者名 / Email + 密碼驗證。

    驗證順序（A07）：
      1. 找本機 user → auth_provider 決定走哪個 backend
      2. 若 settings.ldap_enabled 且 jt-ipam 沒這個 user，嘗試 LDAP；成功則 auto-provision
      3. 若 settings.radius_enabled 且 user.auth_provider=='radius'，走 Radius

    無論成功失敗皆寫 audit；失敗的 reason 不對外顯示（防 enumeration）。
    """
    settings = get_settings()
    stmt = select(User).where((User.username == username) | (User.email == username))
    user = (await session.execute(stmt)).scalar_one_or_none()
    now = datetime.now(UTC)

    async def _audit(action: str, *, success: bool, reason: str | None,
                     target_user: User | None) -> None:
        await append_audit(
            session,
            actor_user_id=str(target_user.id) if target_user else None,
            actor_ip=actor_ip,
            actor_user_agent=actor_user_agent,
            object_type="auth",
            object_id=str(target_user.id) if target_user else None,
            action=action,
            diff={"username": username, "success": success, "reason": reason},
            request_id=request_id,
        )

    # ── 帳號鎖定 / inactive 先檢查 ──
    if user is not None:
        if not user.is_active:
            await _audit("login_failed", success=False, reason="inactive", target_user=user)
            await session.commit()
            raise AccountInactive
        if user.locked_until is not None and user.locked_until > now:
            await _audit("login_failed", success=False, reason="locked", target_user=user)
            await session.commit()
            raise AccountLocked

    # ── 走哪個 backend ──
    provider: str = (user.auth_provider if user else "auto")

    # auto-provision via LDAP：jt-ipam 沒這個帳號但 LDAP 有
    if user is None and settings.ldap_enabled:
        try:
            info = await ldap_auth.authenticate(username, password)
        except ldap_auth.LDAPInvalidCredentials:
            await _audit("login_failed", success=False, reason="ldap_invalid", target_user=None)
            await session.commit()
            raise InvalidCredentials from None
        except ldap_auth.LDAPNotConfigured:
            pass
        except ldap_auth.LDAPAuthError as exc:
            await _audit("login_failed", success=False, reason=f"ldap_error:{exc}", target_user=None)
            await session.commit()
            raise InvalidCredentials from exc
        else:
            # 建 user
            user = User(
                username=info.username,
                email=info.email or f"{info.username}@ldap.local",
                display_name=info.display_name,
                auth_provider="ldap",
                external_subject=info.dn,
                is_active=True,
                is_admin=info.is_admin,
            )
            session.add(user)
            await session.flush()
            await _audit("ldap_auto_provision", success=True, reason=None, target_user=user)
            user.last_login_at = now
            user.last_login_ip = actor_ip
            await _audit("login_success", success=True, reason="ldap", target_user=user)
            await session.commit()
            return user

    # 本機帳號 + 外部 provider 已登錄
    if user is not None and provider == "local":
        # 抗 timing：一律執行密碼比對
        dummy_hash = ("$argon2id$v=19$m=65536,t=3,p=4$00000000000000000000000000000000$"
                      "00000000000000000000000000000000000000000000")
        target_hash = user.password_hash or dummy_hash
        if not verify_password(password, target_hash):
            user.failed_login_count = (user.failed_login_count or 0) + 1
            if user.failed_login_count >= _MAX_FAILED_ATTEMPTS:
                user.locked_until = now + _LOCK_DURATION
            await _audit("login_failed", success=False, reason="invalid_password", target_user=user)
            await session.commit()
            raise InvalidCredentials

        if password_needs_rehash(user.password_hash or ""):
            user.password_hash = hash_password(password)
        user.failed_login_count = 0
        user.locked_until = None
        user.last_login_at = now
        user.last_login_ip = actor_ip
        await _audit("login_success", success=True, reason="local", target_user=user)
        await session.commit()
        return user

    if user is not None and provider == "ldap":
        if not settings.ldap_enabled:
            await _audit("login_failed", success=False, reason="ldap_disabled", target_user=user)
            await session.commit()
            raise InvalidCredentials
        try:
            info = await ldap_auth.authenticate(username, password)
        except (ldap_auth.LDAPInvalidCredentials, ldap_auth.LDAPAuthError) as exc:
            user.failed_login_count = (user.failed_login_count or 0) + 1
            if user.failed_login_count >= _MAX_FAILED_ATTEMPTS:
                user.locked_until = now + _LOCK_DURATION
            await _audit("login_failed", success=False, reason="ldap_reject", target_user=user)
            await session.commit()
            raise InvalidCredentials from exc
        # 同步 admin / display_name
        user.is_admin = info.is_admin
        if info.display_name:
            user.display_name = info.display_name
        if info.email:
            user.email = info.email
        user.failed_login_count = 0
        user.locked_until = None
        user.last_login_at = now
        user.last_login_ip = actor_ip
        await _audit("login_success", success=True, reason="ldap", target_user=user)
        await session.commit()
        return user

    if user is not None and provider == "radius":
        if not settings.radius_enabled:
            await _audit("login_failed", success=False, reason="radius_disabled", target_user=user)
            await session.commit()
            raise InvalidCredentials
        try:
            await radius_auth.authenticate(username, password)
        except (radius_auth.RadiusInvalidCredentials, radius_auth.RadiusAuthError) as exc:
            user.failed_login_count = (user.failed_login_count or 0) + 1
            if user.failed_login_count >= _MAX_FAILED_ATTEMPTS:
                user.locked_until = now + _LOCK_DURATION
            await _audit("login_failed", success=False, reason="radius_reject", target_user=user)
            await session.commit()
            raise InvalidCredentials from exc
        user.failed_login_count = 0
        user.locked_until = None
        user.last_login_at = now
        user.last_login_ip = actor_ip
        await _audit("login_success", success=True, reason="radius", target_user=user)
        await session.commit()
        return user

    # 找不到使用者也跑一次密碼比對抵消 timing
    dummy_hash = ("$argon2id$v=19$m=65536,t=3,p=4$00000000000000000000000000000000$"
                  "00000000000000000000000000000000000000000000")
    verify_password(password, dummy_hash)
    await _audit("login_failed", success=False, reason="no_user", target_user=None)
    await session.commit()
    raise InvalidCredentials


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
