"""安全相關工具。

涵蓋：
- argon2id 密碼雜湊（A02 / A07）
- AES-256-GCM 應用層加密（A02）
- JWT 短時 token（A07）
- API Token hash（A07）
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import get_settings

# =============================================================================
# Password hashing (argon2id)
# =============================================================================

_settings = get_settings()

_password_hasher: Final = PasswordHasher(
    time_cost=_settings.argon2_time_cost,
    memory_cost=_settings.argon2_memory_cost_kib,
    parallelism=_settings.argon2_parallelism,
    hash_len=32,
    salt_len=16,
)


def hash_password(password: str) -> str:
    """argon2id 雜湊；含參數內嵌，可未來輪替。"""
    if not password or len(password) < 12:
        raise ValueError("Password must be ≥ 12 characters (A07)")
    return _password_hasher.hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    """常數時間比較。"""
    try:
        _password_hasher.verify(stored_hash, password)
        return True
    except VerifyMismatchError:
        return False


def password_needs_rehash(stored_hash: str) -> bool:
    return _password_hasher.check_needs_rehash(stored_hash)


# =============================================================================
# Symmetric encryption (AES-256-GCM)  — A02
# =============================================================================
# 用於 DB 內敏感欄位：DNS 帳密、SNMP community、API token、TOTP secret 等。
# Key 透過 ENCRYPTION_KEY env 傳入；正式環境改 KMS（後續 plug 入 envelope encryption）。


def _derive_key() -> bytes:
    """由 ENCRYPTION_KEY (base64) 取出 32-byte 主金鑰。"""
    raw = _settings.encryption_key.get_secret_value()
    try:
        key = base64.b64decode(raw, validate=True)
    except Exception:  # noqa: BLE001
        # 退回：當作 hex
        key = bytes.fromhex(raw)
    if len(key) != 32:
        # 最後保險：用 SHA-256 派生 32 bytes（仍需 raw 為 ≥32 bytes 高熵）
        if len(raw) < 32:
            raise ValueError("ENCRYPTION_KEY must contain ≥ 32 bytes of entropy")
        key = hashlib.sha256(raw.encode("utf-8")).digest()
    return key


_master_key: bytes = _derive_key()
_aead: AESGCM = AESGCM(_master_key)


def encrypt_secret(plaintext: str | bytes, *, aad: bytes | None = None) -> tuple[bytes, bytes]:
    """加密敏感欄位；回傳 (ciphertext, nonce)。

    `aad` 應綁定上下文（例如 b"dns_server:<id>:api_key"），避免密文搬遷。
    """
    if isinstance(plaintext, str):
        plaintext = plaintext.encode("utf-8")
    nonce = secrets.token_bytes(12)
    ciphertext = _aead.encrypt(nonce, plaintext, aad)
    return ciphertext, nonce


def decrypt_secret(ciphertext: bytes, nonce: bytes, *, aad: bytes | None = None) -> bytes:
    return _aead.decrypt(nonce, ciphertext, aad)


# =============================================================================
# API Token hashing (A07)
# =============================================================================
# Token 格式：jt_<env>_<32 bytes random base64url>
#   * 顯示時前 8 字元 = prefix（用於 UI 識別）
#   * 儲存時只存 sha256(raw_token) 與 prefix


def generate_api_token(env_label: str = "live") -> tuple[str, str, bytes]:
    """產生 API token，回傳 (raw_token, prefix, sha256_hash)。"""
    raw_random = secrets.token_urlsafe(32)
    raw_token = f"jt_{env_label}_{raw_random}"
    prefix = raw_token[:8]
    digest = hashlib.sha256(raw_token.encode("utf-8")).digest()
    return raw_token, prefix, digest


def hash_api_token(raw_token: str) -> bytes:
    return hashlib.sha256(raw_token.encode("utf-8")).digest()


def constant_time_eq(a: bytes, b: bytes) -> bool:
    return hmac.compare_digest(a, b)


# =============================================================================
# JWT (A07)
# =============================================================================

_JWT_ALG: Final = "HS256"


def create_access_token(
    subject: str,
    *,
    extra_claims: dict[str, Any] | None = None,
    expires_in_minutes: int | None = None,
) -> str:
    now = datetime.now(UTC)
    minutes = expires_in_minutes or _settings.access_token_expire_minutes
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=minutes)).timestamp()),
        "iss": "jt-ipam",
    }
    if extra_claims:
        # 禁止覆寫 reserved claims
        for reserved in ("sub", "iat", "nbf", "exp", "iss"):
            extra_claims.pop(reserved, None)
        payload.update(extra_claims)
    return jwt.encode(
        payload,
        _settings.secret_key.get_secret_value(),
        algorithm=_JWT_ALG,
    )


def decode_access_token(token: str) -> dict[str, Any]:
    return jwt.decode(
        token,
        _settings.secret_key.get_secret_value(),
        algorithms=[_JWT_ALG],
        issuer="jt-ipam",
        options={"require": ["exp", "iat", "nbf", "sub"]},
    )
