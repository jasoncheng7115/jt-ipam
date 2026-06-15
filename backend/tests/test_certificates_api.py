"""憑證管理 API 測試：建立、上傳版本(驗證+加密)、列表、RBAC。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.core.security import hash_password
from app.models.user import User
from app.services.auth import issue_access_token
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def _make_cert(cn="example.com", sans=("example.com",), days=90):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=400)).not_valid_after(now + timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(s) for s in sans]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption()).decode()
    return cert_pem, key_pem


def _files(cert_pem, key_pem):
    return {
        "cert_file": ("cert.crt", cert_pem, "application/x-pem-file"),
        "key_file": ("cert.key", key_pem, "application/x-pem-file"),
    }


async def _create_cert(client, auth_headers, name=None) -> str:
    name = name or f"cert-{uuid.uuid4().hex[:6]}"
    r = await client.post("/api/v1/certificates", headers=auth_headers,
                          json={"name": name, "description": "d"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_create_and_upload_version(client, auth_headers):
    cid = await _create_cert(client, auth_headers)
    cert_pem, key_pem = _make_cert(sans=("a.example.com", "b.example.com"))
    r = await client.post(f"/api/v1/certificates/{cid}/versions", headers=auth_headers,
                          files=_files(cert_pem, key_pem))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["is_current"] is True
    assert len(body["fingerprint_sha256"]) == 64
    assert set(body["domains"]) == {"a.example.com", "b.example.com"}
    # 私鑰一律不在回應
    assert "key" not in body
    assert "key_enc" not in body

    # 列表帶出目前版本摘要
    lst = await client.get("/api/v1/certificates", headers=auth_headers)
    item = next(c for c in lst.json()["items"] if c["id"] == cid)
    assert item["current_fingerprint"] == body["fingerprint_sha256"]
    assert item["version_count"] == 1


async def test_upload_key_mismatch_400(client, auth_headers):
    cid = await _create_cert(client, auth_headers)
    cert_pem, _ = _make_cert()
    _, other_key = _make_cert()
    r = await client.post(f"/api/v1/certificates/{cid}/versions", headers=auth_headers,
                          files=_files(cert_pem, other_key))
    assert r.status_code == 400


async def test_upload_expired_400(client, auth_headers):
    cid = await _create_cert(client, auth_headers)
    cert_pem, key_pem = _make_cert(days=-2)
    r = await client.post(f"/api/v1/certificates/{cid}/versions", headers=auth_headers,
                          files=_files(cert_pem, key_pem))
    assert r.status_code == 400


async def test_upload_duplicate_fingerprint_409(client, auth_headers):
    cid = await _create_cert(client, auth_headers)
    cert_pem, key_pem = _make_cert()
    r1 = await client.post(f"/api/v1/certificates/{cid}/versions", headers=auth_headers,
                           files=_files(cert_pem, key_pem))
    assert r1.status_code == 201
    r2 = await client.post(f"/api/v1/certificates/{cid}/versions", headers=auth_headers,
                           files=_files(cert_pem, key_pem))
    assert r2.status_code == 409


async def test_generate_self_signed_version(client, auth_headers):
    cid = await _create_cert(client, auth_headers)
    r = await client.post(f"/api/v1/certificates/{cid}/self-signed", headers=auth_headers,
                          json={"common_name": "lab.lan", "sans": ["lab.lan", "x.lan"], "days": 30})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["is_current"] is True
    assert set(body["domains"]) == {"lab.lan", "x.lan"}
    assert "key" not in body
    # 自簽版本可被 agent 派送：列表會顯示為目前版本
    lst = await client.get("/api/v1/certificates", headers=auth_headers)
    item = next(c for c in lst.json()["items"] if c["id"] == cid)
    assert item["current_fingerprint"] == body["fingerprint_sha256"]
    # 自簽偵測 + 帶出 CN/SAN（前端據此提供「續簽」並預填）
    assert item["current_is_self_signed"] is True
    assert item["current_common_name"] == "lab.lan"
    assert set(item["current_sans"]) == {"lab.lan", "x.lan"}


async def test_delete_cert_blocked_when_used_by_agent(client, auth_headers):
    """憑證仍被派送代理選用時不可刪（避免代理 scope 殘留孤兒 UUID）；移除後可刪。"""
    cid = await _create_cert(client, auth_headers)
    ra = await client.post("/api/v1/cert-agents", headers=auth_headers,
                           json={"name": f"ag-{uuid.uuid4().hex[:6]}", "scope_cert_ids": [cid]})
    assert ra.status_code == 201, ra.text
    aid = ra.json()["id"]
    rd = await client.delete(f"/api/v1/certificates/{cid}", headers=auth_headers)
    assert rd.status_code == 409, rd.text
    # 從代理的可取憑證移除後即可刪除
    rp = await client.patch(f"/api/v1/cert-agents/{aid}", headers=auth_headers,
                            json={"scope_cert_ids": []})
    assert rp.status_code == 200, rp.text
    rd2 = await client.delete(f"/api/v1/certificates/{cid}", headers=auth_headers)
    assert rd2.status_code == 204, rd2.text


async def test_self_signed_renew_creates_new_version(client, auth_headers):
    """續簽＝沿用 CN/SAN 再呼叫 self-signed，產生第二個版本並成為目前版本。"""
    cid = await _create_cert(client, auth_headers)
    r1 = await client.post(f"/api/v1/certificates/{cid}/self-signed", headers=auth_headers,
                           json={"common_name": "renew.lan", "sans": ["renew.lan"], "days": 30})
    assert r1.status_code == 201, r1.text
    fp1 = r1.json()["fingerprint_sha256"]
    # 續簽：同 CN/SAN、較長效期
    r2 = await client.post(f"/api/v1/certificates/{cid}/self-signed", headers=auth_headers,
                           json={"common_name": "renew.lan", "sans": ["renew.lan"], "days": 825})
    assert r2.status_code == 201, r2.text
    assert r2.json()["fingerprint_sha256"] != fp1
    lst = await client.get("/api/v1/certificates", headers=auth_headers)
    item = next(c for c in lst.json()["items"] if c["id"] == cid)
    assert item["version_count"] == 2
    assert item["current_fingerprint"] == r2.json()["fingerprint_sha256"]
    assert item["current_is_self_signed"] is True


async def test_set_source_returns_200(client, auth_headers):
    """設定 SFTP 來源（含密碼/私鑰）要回 200 — 回歸 commit 後 model_validate MissingGreenlet 500。"""
    cid = await _create_cert(client, auth_headers)
    r = await client.put(f"/api/v1/certificates/{cid}/source", headers=auth_headers, json={
        "source_type": "sftp",
        "source_config": {"host": "ca.example.com", "port": 22, "username": "svc",
                          "cert_path": "/etc/ssl/cert.pem"},
        "fetch_interval_seconds": 86400,
        "source_password": "s3cret",
    })
    assert r.status_code == 200, r.text
    assert r.json()["source_type"] == "sftp"
    assert r.json()["source_config"]["host"] == "ca.example.com"
    # 機敏不外洩
    assert "source_password" not in r.json()


async def test_patch_certificate_returns_200(client, auth_headers):
    """PATCH 憑證要回 200 — 回歸 commit 後 updated_at(onupdate)過期的 MissingGreenlet 500。"""
    cid = await _create_cert(client, auth_headers)
    r = await client.patch(f"/api/v1/certificates/{cid}", headers=auth_headers,
                           json={"description": "updated"})
    assert r.status_code == 200, r.text
    assert r.json()["description"] == "updated"


async def test_source_test_connection_blocked_host(client, auth_headers):
    """測試連線端點：SSRF 封鎖主機 → ok:false（不真的連外、快速失敗）。"""
    cid = await _create_cert(client, auth_headers)
    r = await client.post(f"/api/v1/certificates/{cid}/source/test", headers=auth_headers, json={
        "source_type": "sftp",
        "source_config": {"host": "169.254.169.254", "username": "svc", "cert_path": "/x"},
        "source_password": "pw",
    })
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is False
    assert r.json()["message"]


async def test_gen_source_ssh_keypair(client, auth_headers):
    """自動產生 SSH 金鑰：回 ed25519 公鑰，私鑰只加密存、不外洩明文；source_type none 不嘗試安裝。"""
    cid = await _create_cert(client, auth_headers)
    r = await client.post(f"/api/v1/certificates/{cid}/source/ssh-keypair", headers=auth_headers,
                          json={"source_type": "none", "source_config": {}})
    assert r.status_code == 200, r.text
    pub = r.json()["public_key"]
    assert pub.startswith("ssh-ed25519 ")
    assert r.json()["installed"] is False
    assert "private" not in r.json()
    assert "BEGIN" not in str(r.json())


async def test_gen_keypair_install_blocked_host(client, auth_headers):
    """SFTP + 封鎖主機：金鑰仍產生(回公鑰),但 installed:false + 可讀原因(SSRF 快速失敗)。"""
    cid = await _create_cert(client, auth_headers)
    r = await client.post(f"/api/v1/certificates/{cid}/source/ssh-keypair", headers=auth_headers, json={
        "source_type": "sftp",
        "source_config": {"host": "169.254.169.254", "username": "root"},
        "source_password": "pw",
    })
    assert r.status_code == 200, r.text
    assert r.json()["public_key"].startswith("ssh-ed25519 ")
    assert r.json()["installed"] is False
    assert r.json()["message"]


async def test_download_version_formats(client, auth_headers):
    """版本檔多格式匯出：PEM(cert/key/chain/fullchain/combined) + DER + PKCS#12。"""
    cid = await _create_cert(client, auth_headers)
    cert_pem, key_pem = _make_cert(sans=("a.example.com",))
    rv = await client.post(f"/api/v1/certificates/{cid}/versions", headers=auth_headers,
                           files=_files(cert_pem, key_pem))
    vid = rv.json()["id"]
    base = f"/api/v1/certificates/{cid}/versions/{vid}/file"

    rc = await client.get(f"{base}?fmt=cert", headers=auth_headers)
    assert rc.status_code == 200, rc.text
    assert "BEGIN CERTIFICATE" in rc.text
    assert ".crt" in rc.headers["content-disposition"]

    rk = await client.get(f"{base}?fmt=key", headers=auth_headers)
    assert "PRIVATE KEY" in rk.text

    rco = await client.get(f"{base}?fmt=combined", headers=auth_headers)
    assert "BEGIN CERTIFICATE" in rco.text
    assert "PRIVATE KEY" in rco.text

    rd = await client.get(f"{base}?fmt=der", headers=auth_headers)
    assert rd.status_code == 200
    assert rd.content[:1] == b"\x30"  # DER SEQUENCE

    rp = await client.get(f"{base}?fmt=pfx", headers=auth_headers)
    assert rp.status_code == 200
    assert len(rp.content) > 100
    assert ".pfx" in rp.headers["content-disposition"]

    rb = await client.get(f"{base}?fmt=evil", headers=auth_headers)
    assert rb.status_code == 400


async def test_requires_admin(client, db_session):
    u = User(username=f"na-{uuid.uuid4().hex[:6]}", email=f"{uuid.uuid4().hex[:6]}@t.local",
             display_name="NA", password_hash=hash_password("TestPassword2026!"),
             auth_provider="local", is_active=True, is_admin=False)
    db_session.add(u)
    await db_session.commit()
    token = issue_access_token(u)
    r = await client.get("/api/v1/certificates", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
