"""代理自動改成驗證 jt-ipam：伺服器端的信任資料（2026-11-07）。

使用者：「叫已經裝過代理的客戶一台一台去做，非常不合理」。代理升級後自己來拿這份資料、自己換成驗證；
換憑證時用 jt-ipam 的身分金鑰確認新指紋。這裡守伺服器那一半：
1. 資料用一把固定的身分金鑰簽章（跟 HTTPS 憑證無關），身分金鑰加密存在資料庫
2. 系統設定能關掉自動採用，而且這個開關簽在資料裡（假冒的伺服器改不了）
3. Host 標頭不能把伺服器導去連本機別的埠
4. 代理從不驗證變成會驗證時記一筆稽核
"""
from __future__ import annotations

import base64
import uuid

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from sqlalchemy import select

from app.models.audit import AuditLog
from app.models.encrypted_secret import EncryptedSecret
from app.services import agent_trust

pytestmark = pytest.mark.anyio

FAKE = {"available": True, "ca_trusted": False, "chain_valid": False,
        "spki_pin": "sha256//" + base64.b64encode(b"\x01" * 32).decode(),
        "cert_sha256": "AB" * 32, "subject": "CN=ipam", "issuer": "CN=ipam",
        "not_after": "2030-01-02T03:04:05+00:00"}


@pytest.fixture
def local(monkeypatch):  # type: ignore[no-untyped-def]
    calls: list[str] = []

    async def fake(origin: str):  # type: ignore[no-untyped-def]
        calls.append(origin)
        return dict(FAKE)
    import app.services.server_tls as st
    monkeypatch.setattr(st, "local_identity", fake)
    agent_trust.reset_cache()
    yield calls
    agent_trust.reset_cache()


def _parse(text: str) -> tuple[dict[str, str], bytes, bytes]:
    body, _, sig_line = text.rpartition("sig=")
    fields = dict(line.split("=", 1) for line in body.splitlines())
    return fields, body.encode(), base64.b64decode(sig_line.strip())


async def test_bundle_is_signed_by_a_stable_identity_key(client, local) -> None:
    r = await client.get("/api/v1/agent-trust", headers={"host": "ipam.example.net"})
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    f, body, sig = _parse(r.text)
    assert f["jt-ipam-agent-trust"] == "1" and f["auto_pin"] == "1"
    assert f["spki_pin"] == FAKE["spki_pin"] and f["cert_sha256"] == FAKE["cert_sha256"]
    assert f["not_after"] == "1893553445"
    pub = serialization.load_der_public_key(base64.b64decode(f["identity_spki"]))
    pub.verify(sig, body, padding.PKCS1v15(), hashes.SHA256())       # 驗不過會丟例外
    nums = pub.public_numbers()
    assert int.from_bytes(base64.b64decode(f["identity_n"]), "big") == nums.n
    assert int.from_bytes(base64.b64decode(f["identity_e"]), "big") == nums.e
    assert local == ["https://ipam.example.net:443"], "只連本機、用請求的主機名稱當 SNI"

    agent_trust.reset_cache()                    # 換一個 worker（重新從資料庫讀）也是同一把
    again, _, _ = _parse((await client.get("/api/v1/agent-trust", headers={"host": "ipam.example.net"})).text)
    assert again["identity_spki"] == f["identity_spki"]


async def test_identity_key_is_stored_encrypted(client, db_session, local) -> None:
    await client.get("/api/v1/agent-trust", headers={"host": "ipam.example.net"})
    row = (await db_session.execute(select(EncryptedSecret).where(
        EncryptedSecret.object_type == agent_trust.OBJECT_TYPE))).scalar_one()
    assert b"PRIVATE KEY" not in row.ciphertext


async def test_auto_pin_switch_is_signed_into_the_bundle(client, auth_headers, local) -> None:
    assert (await client.get("/api/v1/system/agent-auto-pin", headers=auth_headers)).json() == {"enabled": True}
    r = await client.put("/api/v1/system/agent-auto-pin", headers=auth_headers, json={"enabled": False})
    assert r.status_code == 200 and r.json() == {"enabled": False}
    f, _, _ = _parse((await client.get("/api/v1/agent-trust", headers={"host": "ipam.example.net"})).text)
    assert f["auto_pin"] == "0"
    await client.put("/api/v1/system/agent-auto-pin", headers=auth_headers, json={"enabled": True})
    f, _, _ = _parse((await client.get("/api/v1/agent-trust", headers={"host": "ipam.example.net"})).text)
    assert f["auto_pin"] == "1"


async def test_auto_pin_setting_is_admin_only(client, local) -> None:
    assert (await client.put("/api/v1/system/agent-auto-pin", json={"enabled": False})).status_code == 401


@pytest.mark.parametrize("host", ["ipam.example.net:22", "127.0.0.1:5432", "a b", "ipam.example.net/x", "user@h"])
async def test_host_header_cannot_point_at_other_local_ports(client, local, host) -> None:
    r = await client.get("/api/v1/agent-trust", headers={"host": host})
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "agent_trust_bad_host"
    assert local == []


async def test_no_certificate_means_no_bundle(client, monkeypatch) -> None:
    async def fake(origin: str):  # type: ignore[no-untyped-def]
        return {"available": False, "reason": "unreachable"}
    import app.services.server_tls as st
    monkeypatch.setattr(st, "local_identity", fake)
    agent_trust.reset_cache()
    r = await client.get("/api/v1/agent-trust", headers={"host": "ipam.example.net"})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "agent_trust_unavailable"


async def test_agent_switching_to_verified_is_audited_once(client, auth_headers, db_session) -> None:
    r = await client.post("/api/v1/cert-agents", headers=auth_headers,
                          json={"name": f"tofu-{uuid.uuid4().hex[:6]}", "scope_cert_ids": []})
    assert r.status_code == 201, r.text
    aid, key = r.json()["id"], r.json()["enroll_key"]
    for mode in ("insecure", "insecure", "pinned", "pinned"):
        await client.get("/api/v1/cert-agents/check", headers={"X-Agent-Key": key, "X-Agent-Tls": mode})
    rows = (await db_session.execute(select(AuditLog).where(
        AuditLog.action == "agent_tls_verified", AuditLog.object_id == aid))).scalars().all()
    assert len(rows) == 1
    assert rows[0].diff["from"] == "insecure" and rows[0].diff["to"] == "pinned"
