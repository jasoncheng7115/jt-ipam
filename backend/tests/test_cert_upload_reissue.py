"""憑證重新簽發的上傳（2026-11-03 使用者：某家 CA 的中繼憑證不合規、全部重新簽發的情境）。

守的是：
1. 只上傳新憑證（＋鏈）時沿用目前版本的私鑰；重新簽發多半沿用原本的 CSR，私鑰只在伺服器上的人
   不必再交一次。新憑證跟目前私鑰不成對要講清楚是「換了金鑰」，沒有目前版本就要求私鑰。
2. 可以直接匯入 PFX / P12（含中繼憑證），密碼錯要講清楚，密碼不進稽核。
3. 清單帶出目前版本的簽發者，才查得出「哪些是某家 CA 簽的」。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID


def _key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _name(cn: str, org: str | None = None) -> x509.Name:
    attrs = [x509.NameAttribute(NameOID.COMMON_NAME, cn)]
    if org:
        attrs.insert(0, x509.NameAttribute(NameOID.ORGANIZATION_NAME, org))
    return x509.Name(attrs)


def _ca(cn: str, org: str):
    k = _key()
    now = datetime.now(UTC)
    c = (x509.CertificateBuilder().subject_name(_name(cn, org)).issuer_name(_name(cn, org))
         .public_key(k.public_key()).serial_number(x509.random_serial_number())
         .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=3650))
         .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
         .sign(k, hashes.SHA256()))
    return c, k


def _leaf(key, ca_cert, ca_key, host="www.example.net"):
    now = datetime.now(UTC)
    return (x509.CertificateBuilder().subject_name(_name(host)).issuer_name(ca_cert.subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=200))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
            .sign(ca_key, hashes.SHA256()))


def _pem(obj) -> str:
    return obj.public_bytes(serialization.Encoding.PEM).decode()


def _key_pem(k) -> str:
    return k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                           serialization.NoEncryption()).decode()


async def _new_cert(client, auth_headers) -> str:
    r = await client.post("/api/v1/certificates", headers=auth_headers,
                          json={"name": f"reissue-{uuid.uuid4().hex[:6]}"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _upload(client, auth_headers, cid, *, cert=None, key=None, chain=None, pfx=None, pfx_password=None):
    files = {}
    if cert is not None:
        files["cert_file"] = ("cert.crt", cert, "application/x-pem-file")
    if key is not None:
        files["key_file"] = ("cert.key", key, "application/x-pem-file")
    if chain is not None:
        files["chain_file"] = ("chain.crt", chain, "application/x-pem-file")
    if pfx is not None:
        files["pfx_file"] = ("cert.pfx", pfx, "application/x-pkcs12")
    data = {"pfx_password": pfx_password} if pfx_password is not None else {}
    return await client.post(f"/api/v1/certificates/{cid}/versions", headers=auth_headers,
                             files=files, data=data)


async def _key_of(client, auth_headers, cid, vid) -> str:
    r = await client.get(f"/api/v1/certificates/{cid}/versions/{vid}/file?fmt=key", headers=auth_headers)
    assert r.status_code == 200, r.text
    return r.text


def _same_key(pem_a: str, pem_b: str) -> bool:
    a = serialization.load_pem_private_key(pem_a.encode(), password=None)
    b = serialization.load_pem_private_key(pem_b.encode(), password=None)
    spki = serialization.PublicFormat.SubjectPublicKeyInfo
    return a.public_key().public_bytes(serialization.Encoding.DER, spki) == \
        b.public_key().public_bytes(serialization.Encoding.DER, spki)


async def test_reissued_cert_without_key_reuses_current_private_key(client, auth_headers, db_session) -> None:
    from app.models.audit import AuditLog
    from sqlalchemy import select
    old_ca, old_ca_key = _ca("Old Issuing CA", "Example Trust")
    new_ca, new_ca_key = _ca("New Issuing CA 2026", "Example Trust")
    k = _key()
    cid = await _new_cert(client, auth_headers)
    r1 = await _upload(client, auth_headers, cid, cert=_pem(_leaf(k, old_ca, old_ca_key)), key=_key_pem(k),
                       chain=_pem(old_ca))
    assert r1.status_code == 201, r1.text

    # 重新簽發：同一把私鑰、新的中繼 CA；只交新憑證與新鏈
    r2 = await _upload(client, auth_headers, cid, cert=_pem(_leaf(k, new_ca, new_ca_key)), chain=_pem(new_ca))
    assert r2.status_code == 201, r2.text
    v2 = r2.json()
    assert v2["is_current"] is True
    assert "New Issuing CA 2026" in v2["issuer"]
    assert _same_key(await _key_of(client, auth_headers, cid, v2["id"]), _key_pem(k))

    logs = (await db_session.execute(select(AuditLog).where(
        AuditLog.object_id == cid, AuditLog.action == "cert_version_upload"))).scalars().all()
    assert any((x.diff or {}).get("key_reused") is True for x in logs)


async def test_new_key_without_key_file_says_the_key_changed(client, auth_headers) -> None:
    ca, ca_key = _ca("Issuing CA", "Example Trust")
    k1, k2 = _key(), _key()
    cid = await _new_cert(client, auth_headers)
    assert (await _upload(client, auth_headers, cid, cert=_pem(_leaf(k1, ca, ca_key)), key=_key_pem(k1))).status_code == 201
    r = await _upload(client, auth_headers, cid, cert=_pem(_leaf(k2, ca, ca_key)))
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "cert_key_reuse_mismatch"


async def test_first_version_still_needs_a_private_key(client, auth_headers) -> None:
    ca, ca_key = _ca("Issuing CA", "Example Trust")
    cid = await _new_cert(client, auth_headers)
    r = await _upload(client, auth_headers, cid, cert=_pem(_leaf(_key(), ca, ca_key)))
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "cert_key_required"


async def test_pfx_import_with_chain(client, auth_headers, db_session) -> None:
    from app.models.audit import AuditLog
    from sqlalchemy import select
    ca, ca_key = _ca("Issuing CA", "Example Trust")
    k = _key()
    leaf = _leaf(k, ca, ca_key, host="iis.example.net")
    pw = "Pfx-Pass-123"
    blob = pkcs12.serialize_key_and_certificates(
        b"iis", k, leaf, [ca], serialization.BestAvailableEncryption(pw.encode()))
    cid = await _new_cert(client, auth_headers)
    r = await _upload(client, auth_headers, cid, pfx=blob, pfx_password=pw)
    assert r.status_code == 201, r.text
    v = r.json()
    assert v["domains"] == ["iis.example.net"]
    assert _same_key(await _key_of(client, auth_headers, cid, v["id"]), _key_pem(k))
    chain = (await client.get(f"/api/v1/certificates/{cid}/versions/{v['id']}/file?fmt=chain",
                              headers=auth_headers)).text
    assert "BEGIN CERTIFICATE" in chain, "PFX 裡的中繼憑證要成為鏈"

    logs = (await db_session.execute(select(AuditLog).where(
        AuditLog.object_id == cid, AuditLog.action == "cert_version_upload"))).scalars().all()
    assert any((x.diff or {}).get("format") == "pfx" for x in logs)
    assert not any(pw in str(x.diff) for x in logs), "PFX 密碼不可進稽核"


async def test_pfx_wrong_password_is_explained(client, auth_headers) -> None:
    ca, ca_key = _ca("Issuing CA", "Example Trust")
    k = _key()
    blob = pkcs12.serialize_key_and_certificates(
        b"x", k, _leaf(k, ca, ca_key), None, serialization.BestAvailableEncryption(b"right-one"))
    cid = await _new_cert(client, auth_headers)
    r = await _upload(client, auth_headers, cid, pfx=blob, pfx_password="wrong-one")
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "cert_pfx_password"
    r = await _upload(client, auth_headers, cid, pfx=b"not a pfx at all", pfx_password="x")
    assert r.status_code == 400
    assert r.json()["detail"]["code"] in ("cert_pfx_bad", "cert_pfx_password")


async def test_pfx_without_key_reuses_current_key(client, auth_headers) -> None:
    ca, ca_key = _ca("Issuing CA", "Example Trust")
    k = _key()
    cid = await _new_cert(client, auth_headers)
    assert (await _upload(client, auth_headers, cid, cert=_pem(_leaf(k, ca, ca_key)), key=_key_pem(k))).status_code == 201
    blob = pkcs12.serialize_key_and_certificates(b"c", None, _leaf(k, ca, ca_key), None,
                                                 serialization.NoEncryption())
    r = await _upload(client, auth_headers, cid, pfx=blob)
    assert r.status_code == 201, r.text


async def test_list_shows_the_current_issuer(client, auth_headers) -> None:
    ca, ca_key = _ca("TW Example Secure SSL CA", "Example Trust Corp")
    k = _key()
    cid = await _new_cert(client, auth_headers)
    assert (await _upload(client, auth_headers, cid, cert=_pem(_leaf(k, ca, ca_key)), key=_key_pem(k))).status_code == 201
    items = (await client.get("/api/v1/certificates", headers=auth_headers)).json()["items"]
    it = next(i for i in items if i["id"] == cid)
    assert "TW Example Secure SSL CA" in it["current_issuer"]
    assert it["current_issuer_name"] == "TW Example Secure SSL CA"
    assert it["current_issuer_org"] == "Example Trust Corp"
