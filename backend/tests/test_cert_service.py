"""憑證 bundle 驗證服務測試。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.services.cert_service import CertError, generate_self_signed, validate_bundle
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def _make_cert(cn: str = "example.com", sans=("example.com", "www.example.com"), days: int = 90):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = datetime.now(UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject).issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(s) for s in sans]), critical=False)
    )
    cert = builder.sign(key, hashes.SHA256())
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()
    return cert_pem, key_pem


def test_valid_bundle_returns_metadata():
    cert_pem, key_pem = _make_cert(days=90)
    info = validate_bundle(cert_pem, key_pem)
    assert set(info.domains) == {"example.com", "www.example.com"}
    assert len(info.fingerprint_sha256) == 64
    assert info.is_expired is False
    assert 85 <= info.days_remaining <= 90


def test_expired_cert_is_flagged_not_rejected():
    cert_pem, key_pem = _make_cert(days=-1)  # 已過期
    info = validate_bundle(cert_pem, key_pem)  # 過期仍可解析（上傳端決定是否擋）
    assert info.is_expired is True


def test_key_cert_mismatch_rejected():
    cert_pem, _ = _make_cert()
    _, other_key = _make_cert()  # 另一把不相干的 key
    with pytest.raises(CertError, match="不配對"):
        validate_bundle(cert_pem, other_key)


def test_garbage_cert_rejected():
    _, key_pem = _make_cert()
    with pytest.raises(CertError):
        validate_bundle("not a pem", key_pem)


def test_encrypted_key_rejected_with_clear_message():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cert_pem, _ = _make_cert()
    enc_key = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.BestAvailableEncryption(b"secret"),
    ).decode()
    with pytest.raises(CertError):
        validate_bundle(cert_pem, enc_key)


def test_chain_certs_counted():
    cert_pem, key_pem = _make_cert()
    chain_pem, _ = _make_cert(cn="intermediate")
    info = validate_bundle(cert_pem, key_pem, chain_pem)
    assert info.chain_len == 1


def test_generate_self_signed_roundtrips():
    cert_pem, key_pem = generate_self_signed("my.lan", sans=["my.lan", "alt.lan"], days=30)
    info = validate_bundle(cert_pem, key_pem)  # 產出的 key↔cert 必配對
    assert set(info.domains) == {"my.lan", "alt.lan"}
    assert 27 <= info.days_remaining <= 30
    assert info.is_expired is False


def test_generate_self_signed_defaults_san_to_cn():
    cert_pem, key_pem = generate_self_signed("solo.lan")
    info = validate_bundle(cert_pem, key_pem)
    assert info.domains == ["solo.lan"]
