"""TOTP MFA：enroll、confirm、verify、disable。

OWASP A04 / A07：
- secret 用 AES-256-GCM 加密儲存（aad 綁定 user.id）
- 啟用 / 停用都寫 audit
- 防 replay：可在 Redis 用 (user_id, code) 24h 過期 hold 一次
"""

from __future__ import annotations

import secrets
import uuid

import pyotp
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decrypt_secret, encrypt_secret
from app.models.user import User

_ISSUER = "jt-ipam"


def _aad(user_id: uuid.UUID) -> bytes:
    return f"user:{user_id}:totp".encode()


def is_enabled(user: User) -> bool:
    return user.totp_secret_enc is not None and user.totp_nonce is not None


def begin_enrollment() -> str:
    """產生新的 base32 secret（呼叫者尚未持久化，需 confirm）。"""
    return pyotp.random_base32()


def provisioning_uri(secret: str, account: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=account, issuer_name=_ISSUER)


async def confirm_enrollment(
    session: AsyncSession,
    *,
    user: User,
    secret: str,
    code: str,
) -> bool:
    """用 user 提交的 6-digit code 驗證 secret 正確；正確才寫入 DB。

    回傳 True 表成功（已 commit）；False 表 code 錯誤（未寫入）。
    """
    if not pyotp.TOTP(secret).verify(code, valid_window=1):
        return False

    ciphertext, nonce = encrypt_secret(secret, aad=_aad(user.id))
    user.totp_secret_enc = ciphertext
    user.totp_nonce = nonce
    await session.commit()
    return True


async def disable(session: AsyncSession, *, user: User) -> None:
    user.totp_secret_enc = None
    user.totp_nonce = None
    await session.commit()


async def verify_code(user: User, code: str) -> bool:
    """登入第二步使用：以使用者持久化的 secret 驗證 code。"""
    if not is_enabled(user):
        return False
    secret = decrypt_secret(
        user.totp_secret_enc,  # type: ignore[arg-type]
        user.totp_nonce,        # type: ignore[arg-type]
        aad=_aad(user.id),
    ).decode("utf-8")
    return pyotp.TOTP(secret).verify(code, valid_window=1)


def issue_mfa_challenge(user: User) -> str:
    """登入第一步成功後發給 client 的 ephemeral token。

    用短 JWT（5 min）；type=mfa_challenge；client 第二步 POST 此 token + code。
    """

    from app.core.security import create_access_token

    return create_access_token(
        subject=str(user.id),
        extra_claims={"type": "mfa_challenge", "jti": secrets.token_urlsafe(8)},
        expires_in_minutes=5,
    )
