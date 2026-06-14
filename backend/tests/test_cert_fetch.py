"""憑證自動抓取(URL/SFTP)服務測試:dedup(同 fingerprint 跳過)、updated、沿用 key、error。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.core.security import encrypt_secret
from app.models.certificate import Certificate, CertVersion
from app.services import cert_fetch
from app.services.cert_service import validate_bundle
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def _cert(cn="svc.example.com", days=90, key=None):
    key = key or rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = datetime.now(UTC)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=days))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(cn)]), critical=False)
            .sign(key, hashes.SHA256()))
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = key.private_bytes(serialization.Encoding.PEM,
                                serialization.PrivateFormat.TraditionalOpenSSL,
                                serialization.NoEncryption()).decode()
    return cert_pem, key_pem, key


async def _seed(db_session, cert_pem, key_pem, *, source_type="url"):
    c = Certificate(name=f"c-{uuid.uuid4().hex[:6]}", source_type=source_type,
                    source_config={"cert_url": "https://ca.example.com/cert.pem"})
    db_session.add(c)
    await db_session.flush()
    info = validate_bundle(cert_pem, key_pem)
    enc, nonce = encrypt_secret(key_pem, aad=cert_fetch._key_aad(c.id, info.fingerprint_sha256))
    db_session.add(CertVersion(
        certificate_id=c.id, fingerprint_sha256=info.fingerprint_sha256, serial=info.serial,
        subject=info.subject, issuer=info.issuer, not_before=info.not_before, not_after=info.not_after,
        domains=info.domains, cert_pem=cert_pem, chain_pem=None, key_enc=enc, key_nonce=nonce,
        is_current=True))
    await db_session.commit()
    return c, info.fingerprint_sha256


async def test_same_fingerprint_skipped(db_session, monkeypatch):
    cert_pem, key_pem, _ = _cert()
    c, fp = await _seed(db_session, cert_pem, key_pem)

    async def fake(cfg):
        return cert_pem, key_pem, None  # 來源回同一張
    monkeypatch.setattr(cert_fetch, "_fetch_url", fake)
    res = await cert_fetch.fetch_certificate(db_session, c)
    assert res["status"] == "skipped"
    assert res["fingerprint"] == fp


async def test_new_cert_updated(db_session, monkeypatch):
    cert_pem, key_pem, _ = _cert()
    c, old_fp = await _seed(db_session, cert_pem, key_pem)
    new_cert, new_key, _ = _cert(days=120)  # 不同憑證

    async def fake(cfg):
        return new_cert, new_key, None
    monkeypatch.setattr(cert_fetch, "_fetch_url", fake)
    res = await cert_fetch.fetch_certificate(db_session, c)
    assert res["status"] == "updated"
    assert res["fingerprint"] != old_fp
    # 目前版本變新的
    from sqlalchemy import select
    cur = (await db_session.execute(select(CertVersion).where(
        CertVersion.certificate_id == c.id, CertVersion.is_current.is_(True)))).scalar_one()
    assert cur.fingerprint_sha256 == res["fingerprint"]


async def test_reuse_current_key_when_source_has_no_key(db_session, monkeypatch):
    cert_pem, key_pem, key = _cert()
    c, _ = await _seed(db_session, cert_pem, key_pem)
    # 用「同一把 key」簽的續約憑證,來源只給 cert、不給 key → 應沿用目前版本 key
    renewed_cert, _, _ = _cert(days=200, key=key)

    async def fake(cfg):
        return renewed_cert, None, None
    monkeypatch.setattr(cert_fetch, "_fetch_url", fake)
    res = await cert_fetch.fetch_certificate(db_session, c)
    assert res["status"] == "updated"


async def test_fetch_error_recorded(db_session, monkeypatch):
    cert_pem, key_pem, _ = _cert()
    c, _ = await _seed(db_session, cert_pem, key_pem)

    async def fake(cfg):
        raise cert_fetch.FetchError("連線失敗")
    monkeypatch.setattr(cert_fetch, "_fetch_url", fake)
    res = await cert_fetch.fetch_certificate(db_session, c)
    assert res["status"] == "error"
    await db_session.refresh(c)
    assert c.last_fetch_error == "連線失敗"


def test_generate_source_ssh_keypair():
    """產生的 SSH 金鑰對：公鑰 ed25519、私鑰 OpenSSH PEM、彼此相符可被 asyncssh 解析。"""
    import asyncssh
    priv, pub = cert_fetch.generate_source_ssh_keypair("unit-test")
    assert pub.startswith("ssh-ed25519 ")
    assert "OPENSSH PRIVATE KEY" in priv
    # 私鑰可被解析，且其公鑰與回傳公鑰一致
    k = asyncssh.import_private_key(priv)
    assert k.export_public_key().decode().split()[1] == pub.split()[1]
