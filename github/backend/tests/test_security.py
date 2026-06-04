"""快速 sanity test：核心安全工具能正常運作。"""

from __future__ import annotations

import pytest

from app.core.safe_http import UnsafeOutboundURL, assert_url_safe
from app.core.security import (
    decrypt_secret,
    encrypt_secret,
    generate_api_token,
    hash_password,
    password_needs_rehash,
    verify_password,
)


def test_argon2_password_roundtrip():
    h = hash_password("CorrectHorseBattery!2026")
    assert verify_password("CorrectHorseBattery!2026", h)
    assert not verify_password("wrong-password-xx", h)
    assert not password_needs_rehash(h)


def test_argon2_rejects_short_password():
    with pytest.raises(ValueError):
        hash_password("short")


def test_aes_gcm_encrypt_decrypt():
    aad = b"dns_server:abc:api_key"
    ct, nonce = encrypt_secret("super-secret-token", aad=aad)
    assert ct != b"super-secret-token"
    assert decrypt_secret(ct, nonce, aad=aad) == b"super-secret-token"


def test_api_token_hash_unique():
    raw1, p1, h1 = generate_api_token()
    raw2, p2, h2 = generate_api_token()
    assert raw1 != raw2
    assert h1 != h2
    assert raw1.startswith("jt_")
    assert len(p1) == 8


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "ftp://example.com/",
        "file:///etc/passwd",
        "gopher://10.0.0.1/_x",
    ],
)
def test_ssrf_blocks_dangerous_urls(url: str):
    with pytest.raises(UnsafeOutboundURL):
        assert_url_safe(url)
