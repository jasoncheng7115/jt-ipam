"""憑證派送 agent 協定測試：check / bundle（含 scope）/ report + key 認證。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def _make_cert(cn="svc.example.com", days=90):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(cn)]), critical=False)
        .sign(key, hashes.SHA256())
    )
    return (cert.public_bytes(serialization.Encoding.PEM).decode(),
            key.private_bytes(serialization.Encoding.PEM,
                              serialization.PrivateFormat.TraditionalOpenSSL,
                              serialization.NoEncryption()).decode())


async def _cert_with_version(client, auth_headers) -> tuple[str, str, str]:
    """建立憑證 + 上傳一版,回 (cert_id, name, fingerprint)。"""
    name = f"cert-{uuid.uuid4().hex[:6]}"
    r = await client.post("/api/v1/certificates", headers=auth_headers, json={"name": name})
    cid = r.json()["id"]
    cert_pem, key_pem = _make_cert()
    rv = await client.post(f"/api/v1/certificates/{cid}/versions", headers=auth_headers, files={
        "cert_file": ("c.crt", cert_pem, "application/x-pem-file"),
        "key_file": ("c.key", key_pem, "application/x-pem-file"),
    })
    return cid, name, rv.json()["fingerprint_sha256"]


async def _make_agent(client, auth_headers, scope_ids) -> str:
    r = await client.post("/api/v1/cert-agents", headers=auth_headers,
                          json={"name": f"agent-{uuid.uuid4().hex[:6]}", "scope_cert_ids": scope_ids})
    assert r.status_code == 201, r.text
    return r.json()["enroll_key"]


async def test_check_and_bundle_in_scope(client, auth_headers):
    cid, name, fp = await _cert_with_version(client, auth_headers)
    key = await _make_agent(client, auth_headers, [cid])

    # check 回目前版本指紋
    rc = await client.get("/api/v1/cert-agents/check", headers={"X-Agent-Key": key})
    assert rc.status_code == 200, rc.text
    # 自我更新用：check 一律回 server 端 agent.sh 的 sha256（64 hex）
    assert len(rc.json()["agent_sha"]) == 64
    certs = rc.json()["certificates"]
    assert len(certs) == 1
    assert certs[0]["cert"] == name
    assert certs[0]["fingerprint"] == fp

    # bundle 回 crt/key/chain（私鑰明文，僅 agent 認證後可取）
    rb = await client.get(f"/api/v1/cert-agents/bundle?cert={name}", headers={"X-Agent-Key": key})
    assert rb.status_code == 200, rb.text
    body = rb.json()
    assert "BEGIN CERTIFICATE" in body["cert_pem"]
    assert "PRIVATE KEY" in body["key_pem"]
    assert body["fingerprint"] == fp


async def test_bundle_out_of_scope_404(client, auth_headers):
    _cid, name, _ = await _cert_with_version(client, auth_headers)
    other_cid, _, _ = await _cert_with_version(client, auth_headers)
    key = await _make_agent(client, auth_headers, [other_cid])  # scope 只含別張
    rb = await client.get(f"/api/v1/cert-agents/bundle?cert={name}", headers={"X-Agent-Key": key})
    assert rb.status_code == 404  # 不在 scope 與不存在回相同 404


async def test_empty_scope_sees_nothing(client, auth_headers):
    await _cert_with_version(client, auth_headers)
    key = await _make_agent(client, auth_headers, [])  # deny-by-default
    rc = await client.get("/api/v1/cert-agents/check", headers={"X-Agent-Key": key})
    assert rc.json()["certificates"] == []


async def test_report_stored(client, auth_headers):
    cid, name, fp = await _cert_with_version(client, auth_headers)
    key = await _make_agent(client, auth_headers, [cid])
    rr = await client.post("/api/v1/cert-agents/report", headers={"X-Agent-Key": key}, json={
        "deployments": [{"cert": name, "profile": "nginx", "fingerprint": fp,
                         "status": "ok", "dry_run": False}],
    })
    assert rr.status_code == 200
    assert rr.json()["received"] == 1


async def test_list_reports_version_and_ip(client, auth_headers):
    """管理頁列表回傳 agent_version / server_agent_version / last_source_ip。"""
    cid, _name, _fp = await _cert_with_version(client, auth_headers)
    key = await _make_agent(client, auth_headers, [cid])
    # agent 帶版本 header poll 一次 → 記錄 version + source_ip
    await client.get("/api/v1/cert-agents/check",
                     headers={"X-Agent-Key": key, "X-Agent-Version": "0.0.1-test"})
    r = await client.get("/api/v1/cert-agents", headers=auth_headers)
    assert r.status_code == 200, r.text
    item = next(x for x in r.json()["items"] if x["agent_version"] == "0.0.1-test")
    assert item["server_agent_version"]  # server 端解析得到版本（非 None）
    assert item["server_agent_version"] != item["agent_version"]  # 落後 → UI 標可更新
    assert item["last_source_ip"]  # 來源 IP 有記錄


async def test_patch_and_rotate_agent_return_200(client, auth_headers):
    """PATCH 代理 / rotate-key 要回 200 — 回歸 commit 後 updated_at(onupdate)過期 500。"""
    r = await client.post("/api/v1/cert-agents", headers=auth_headers,
                          json={"name": f"agent-{uuid.uuid4().hex[:6]}", "scope_cert_ids": []})
    aid = r.json()["id"]
    rp = await client.patch(f"/api/v1/cert-agents/{aid}", headers=auth_headers,
                            json={"description": "edited"})
    assert rp.status_code == 200, rp.text
    assert rp.json()["description"] == "edited"
    rr = await client.post(f"/api/v1/cert-agents/{aid}/rotate-key", headers=auth_headers)
    assert rr.status_code == 200, rr.text
    assert rr.json()["enroll_key"]


async def test_check_text_format(client, auth_headers):
    """純 bash 代理用 ?format=text：逐行 agent_sha= + name\\tfp\\tnot_after，免解 JSON。"""
    cid, name, fp = await _cert_with_version(client, auth_headers)
    key = await _make_agent(client, auth_headers, [cid])
    rc = await client.get("/api/v1/cert-agents/check?format=text", headers={"X-Agent-Key": key})
    assert rc.status_code == 200, rc.text
    assert rc.headers["content-type"].startswith("text/plain")
    lines = rc.text.strip().splitlines()
    assert lines[0].startswith("agent_sha=")
    assert len(lines[0].split("=", 1)[1]) == 64
    row = next(line for line in lines[1:] if line.startswith(name + "\t"))
    cols = row.split("\t")
    assert cols[0] == name
    assert cols[1] == fp


async def test_bundle_raw_parts(client, auth_headers):
    """/bundle/raw 各 part 回原始 PEM + 指紋 header；key/combined 含私鑰。"""
    cid, name, fp = await _cert_with_version(client, auth_headers)
    key = await _make_agent(client, auth_headers, [cid])
    h = {"X-Agent-Key": key}
    rc = await client.get(f"/api/v1/cert-agents/bundle/raw?cert={name}&part=cert", headers=h)
    assert rc.status_code == 200, rc.text
    assert "BEGIN CERTIFICATE" in rc.text
    assert "PRIVATE KEY" not in rc.text
    assert rc.headers["x-cert-fingerprint"] == fp
    rk = await client.get(f"/api/v1/cert-agents/bundle/raw?cert={name}&part=key", headers=h)
    assert "PRIVATE KEY" in rk.text
    rf = await client.get(f"/api/v1/cert-agents/bundle/raw?cert={name}&part=fullchain", headers=h)
    assert "BEGIN CERTIFICATE" in rf.text
    # pkcs12（jetty 用）回 binary keystore + 指紋 header
    rp = await client.get(f"/api/v1/cert-agents/bundle/raw?cert={name}&part=pkcs12", headers=h)
    assert rp.status_code == 200, rp.text
    assert rp.headers["x-cert-fingerprint"] == fp
    assert rp.content[:4] == b"\x30\x82\x0a"[:2] or rp.content[0] == 0x30  # DER SEQUENCE
    # 不在 scope 回 404
    other = await _make_agent(client, auth_headers, [])
    r404 = await client.get(f"/api/v1/cert-agents/bundle/raw?cert={name}&part=cert",
                            headers={"X-Agent-Key": other})
    assert r404.status_code == 404
    # 非法 part 回 400
    rbad = await client.get(f"/api/v1/cert-agents/bundle/raw?cert={name}&part=evil", headers=h)
    assert rbad.status_code == 400


async def test_report_tsv(client, auth_headers):
    """/report 接受 TSV（bash 代理）：每行 cert\\tprofile\\tstatus\\tfp\\tnot_after\\tdry_run\\tmessage。"""
    cid, name, fp = await _cert_with_version(client, auth_headers)
    key = await _make_agent(client, auth_headers, [cid])
    tsv = f"{name}\tnginx\tok\t{fp}\t2030-01-01T00:00:00+00:00\tfalse\t已套用\n"
    rr = await client.post("/api/v1/cert-agents/report", headers={
        "X-Agent-Key": key, "Content-Type": "text/plain"}, content=tsv)
    assert rr.status_code == 200, rr.text
    assert rr.json()["received"] == 1
    # 回報有進 status
    r = await client.get("/api/v1/cert-agents/status", headers=auth_headers)
    a = next(x for x in r.json()["agents"] if any(d["cert"] == name for d in x["deployments"]))
    dep = next(d for d in a["deployments"] if d["cert"] == name)
    assert dep["status"] == "ok"
    assert dep["up_to_date"] is True


async def test_agent_key_reviewable(client, auth_headers):
    """建立後可再次檢視 enroll key（加密保存）；輪替後檢視回新 key；非 admin 不可取。"""
    r = await client.post("/api/v1/cert-agents", headers=auth_headers,
                          json={"name": f"agent-{uuid.uuid4().hex[:6]}", "scope_cert_ids": []})
    aid = r.json()["id"]
    created_key = r.json()["enroll_key"]
    # 再次檢視回相同 key
    rk = await client.get(f"/api/v1/cert-agents/{aid}/key", headers=auth_headers)
    assert rk.status_code == 200, rk.text
    assert rk.json()["enroll_key"] == created_key
    # 取回的 key 真的能通過 agent 認證
    rc = await client.get("/api/v1/cert-agents/check", headers={"X-Agent-Key": created_key})
    assert rc.status_code == 200
    # 輪替後檢視回新 key，舊 key 失效
    rr = await client.post(f"/api/v1/cert-agents/{aid}/rotate-key", headers=auth_headers)
    new_key = rr.json()["enroll_key"]
    assert new_key != created_key
    rk2 = await client.get(f"/api/v1/cert-agents/{aid}/key", headers=auth_headers)
    assert rk2.json()["enroll_key"] == new_key


async def test_agent_key_requires_admin(client):
    r = await client.get(f"/api/v1/cert-agents/{uuid.uuid4()}/key")
    assert r.status_code in (401, 403)


async def test_server_agent_version(client, auth_headers):
    """管理頁用：回 server 端 agent.sh 的版本。"""
    r = await client.get("/api/v1/cert-agents/server-version", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert "version" in r.json()
    rn = await client.get("/api/v1/cert-agents/server-version")
    assert rn.status_code in (401, 403)


async def test_bad_agent_key_401(client, auth_headers):
    await _cert_with_version(client, auth_headers)
    rc = await client.get("/api/v1/cert-agents/check", headers={"X-Agent-Key": "wrong"})
    assert rc.status_code == 401


async def test_agent_list_requires_admin(client):
    r = await client.get("/api/v1/cert-agents")
    assert r.status_code in (401, 403)


async def test_status_payload_and_global_read(client, auth_headers):
    cid, name, fp = await _cert_with_version(client, auth_headers)
    key = await _make_agent(client, auth_headers, [cid])
    await client.post("/api/v1/cert-agents/report", headers={"X-Agent-Key": key}, json={
        "deployments": [{"cert": name, "profile": "nginx", "fingerprint": fp,
                         "status": "ok", "dry_run": False}]})
    r = await client.get("/api/v1/cert-agents/status", headers=auth_headers)  # admin = global-read
    assert r.status_code == 200, r.text
    a = next(x for x in r.json()["agents"] if any(d["cert"] == name for d in x["deployments"]))
    dep = next(d for d in a["deployments"] if d["cert"] == name)
    assert dep["up_to_date"] is True
    assert dep["not_after"] is not None
    assert dep["days_remaining"] is not None


async def test_status_forbidden_for_non_global_read(client, db_session):
    import uuid as _uuid

    from app.core.security import hash_password
    from app.models.user import User
    from app.services.auth import issue_access_token
    u = User(username=f"dep-{_uuid.uuid4().hex[:6]}", email=f"{_uuid.uuid4().hex[:6]}@t.local",
             display_name="dep", password_hash=hash_password("TestPassword2026!"),
             auth_provider="local", is_active=True, is_admin=False)
    db_session.add(u)
    await db_session.commit()
    token = issue_access_token(u)
    r = await client.get("/api/v1/cert-agents/status", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403  # 非 admin 且無萬用讀取 → 403
