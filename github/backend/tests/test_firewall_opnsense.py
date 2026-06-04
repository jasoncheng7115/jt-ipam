"""OPNsense firewall：CRUD + selector resolution + 加解密 round-trip。

不接真實 OPNsense；對外請求由 monkeypatch 攔截。
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select


async def test_firewall_crud_requires_admin(client):  # type: ignore[no-untyped-def]
    """admin 才能操作 /firewalls/opnsense；無 token → 401。"""
    r = await client.get("/api/v1/firewalls/opnsense")
    assert r.status_code == 401


async def test_firewall_crud_full(client, auth_headers):  # type: ignore[no-untyped-def]
    payload = {
        "name": "fw-edge-01",
        "api_url": "https://opnsense.example.com/",
        "api_key": "key" * 4,
        "api_secret": "secret" * 4,
        "verify_tls": True,
        "description": "邊界防火牆",
    }
    r = await client.post(
        "/api/v1/firewalls/opnsense", headers=auth_headers, json=payload,
    )
    assert r.status_code == 201, r.text
    fw = r.json()
    fw_id = fw["id"]
    assert fw["name"] == "fw-edge-01"
    assert "api_key" not in fw and "api_secret" not in fw  # 不外洩

    # update（只改 name）
    r2 = await client.patch(
        f"/api/v1/firewalls/opnsense/{fw_id}",
        headers=auth_headers,
        json={"name": "fw-edge-rename"},
    )
    assert r2.status_code == 200
    assert r2.json()["name"] == "fw-edge-rename"

    # 旋轉憑證 — 同時改 api_key 與 api_secret
    r3 = await client.patch(
        f"/api/v1/firewalls/opnsense/{fw_id}",
        headers=auth_headers,
        json={"api_key": "newkey1234", "api_secret": "newsecret1234"},
    )
    assert r3.status_code == 200

    # 只給一個 → 400（避免不一致）
    r4 = await client.patch(
        f"/api/v1/firewalls/opnsense/{fw_id}",
        headers=auth_headers,
        json={"api_key": "newkeyonly1"},
    )
    assert r4.status_code == 400

    # delete
    r5 = await client.delete(
        f"/api/v1/firewalls/opnsense/{fw_id}", headers=auth_headers,
    )
    assert r5.status_code == 204


async def test_alias_mapping_crud(client, auth_headers, db_session):  # type: ignore[no-untyped-def]
    """建一個 firewall + 一個 alias mapping。"""
    fw = (await client.post(
        "/api/v1/firewalls/opnsense", headers=auth_headers,
        json={
            "name": "fw-test", "api_url": "https://1.2.3.4/",
            "api_key": "k" * 8, "api_secret": "s" * 8,
        },
    )).json()
    sec = (await client.post(
        "/api/v1/sections", headers=auth_headers,
        json={"name": "fw-section", "description": None, "strict_mode": False},
    )).json()

    payload = {
        "firewall_id": fw["id"],
        "alias_name": "jt_section_addrs",
        "alias_type": "host",
        "selector": {"type": "section", "section_id": sec["id"]},
        "direction": "push",
    }
    r = await client.post(
        "/api/v1/firewalls/opnsense/mappings", headers=auth_headers, json=payload,
    )
    assert r.status_code == 201, r.text
    m = r.json()
    assert m["alias_name"] == "jt_section_addrs"
    assert m["selector"]["type"] == "section"

    # alias name 規則：英數+_，開頭字母
    bad = await client.post(
        "/api/v1/firewalls/opnsense/mappings", headers=auth_headers,
        json={**payload, "alias_name": "1bad-name"},
    )
    assert bad.status_code == 422


def test_credential_encrypt_roundtrip():
    """加解密同 instance id，aad 對得上才能解開。"""
    from app.services import opnsense_firewall as svc

    fid = uuid.uuid4()
    creds = svc.encrypt_credentials(fid, "key123", "secret123")

    class FakeFw:
        id = fid
        api_key_enc = creds["api_key_enc"]
        api_key_nonce = creds["api_key_nonce"]
        api_secret_enc = creds["api_secret_enc"]
        api_secret_nonce = creds["api_secret_nonce"]

    k, s = svc._decrypt_creds(FakeFw())
    assert (k, s) == ("key123", "secret123")

    # 不同 instance id 應解密失敗（aad 不對）
    other = uuid.uuid4()

    class FakeFw2(FakeFw):
        id = other

    with pytest.raises(Exception):  # cryptography.InvalidTag
        svc._decrypt_creds(FakeFw2())


async def test_selector_resolves_section_ips(db_session, admin_user):  # type: ignore[no-untyped-def]
    """selector type=section 應該抓出該 section 下所有 IP。"""
    from app.models.address import IPAddress
    from app.models.section import Section
    from app.models.subnet import Subnet
    from app.services import opnsense_firewall as svc

    sec = Section(name=f"sec-{uuid.uuid4().hex[:6]}", description=None, strict_mode=False)
    db_session.add(sec)
    await db_session.flush()
    sub = Subnet(section_id=sec.id, cidr="192.0.2.0/29")
    db_session.add(sub)
    await db_session.flush()
    for i in (1, 2, 5):
        db_session.add(IPAddress(subnet_id=sub.id, ip=f"192.0.2.{i}", hostname=f"h{i}"))
    await db_session.commit()

    ips = await svc._resolve_selector_ips(
        db_session, {"type": "section", "section_id": str(sec.id)},
    )
    assert ips == ["192.0.2.1", "192.0.2.2", "192.0.2.5"]


async def test_sync_mapping_pushes_via_mocked_http(
    db_session, admin_user, monkeypatch,  # type: ignore[no-untyped-def]
):
    """sync_mapping(direction=push) 應呼叫 upsert_alias 並送對的 IP 列表。"""
    from app.models.address import IPAddress
    from app.models.firewall import OPNsenseAliasMapping, OPNsenseFirewall
    from app.models.section import Section
    from app.models.subnet import Subnet
    from app.services import opnsense_firewall as svc

    sec = Section(name=f"sec-fw-{uuid.uuid4().hex[:6]}", strict_mode=False)
    db_session.add(sec)
    await db_session.flush()
    sub = Subnet(section_id=sec.id, cidr="10.99.0.0/30")
    db_session.add(sub)
    await db_session.flush()
    db_session.add(IPAddress(subnet_id=sub.id, ip="10.99.0.1", hostname="a"))
    db_session.add(IPAddress(subnet_id=sub.id, ip="10.99.0.2", hostname="b"))
    await db_session.flush()

    creds = svc.encrypt_credentials(uuid.uuid4(), "k", "s")
    # 先建 firewall（用 placeholder 加密之後 flush 取得 id 再回填）
    fw = OPNsenseFirewall(
        name=f"fw-{uuid.uuid4().hex[:6]}",
        api_url="https://1.2.3.4",
        api_key_enc=creds["api_key_enc"], api_key_nonce=creds["api_key_nonce"],
        api_secret_enc=creds["api_secret_enc"], api_secret_nonce=creds["api_secret_nonce"],
        enabled=True, verify_tls=True,
    )
    db_session.add(fw)
    await db_session.flush()
    real = svc.encrypt_credentials(fw.id, "k", "s")
    fw.api_key_enc = real["api_key_enc"]
    fw.api_key_nonce = real["api_key_nonce"]
    fw.api_secret_enc = real["api_secret_enc"]
    fw.api_secret_nonce = real["api_secret_nonce"]

    mapping = OPNsenseAliasMapping(
        firewall_id=fw.id, alias_name="jt_test", alias_type="host",
        selector={"type": "section", "section_id": str(sec.id)}, direction="push",
    )
    db_session.add(mapping)
    await db_session.commit()

    captured: dict[str, Any] = {}

    async def fake_upsert(_fw, *, name, alias_type, content, description=None):  # type: ignore[no-untyped-def]
        captured["name"] = name
        captured["alias_type"] = alias_type
        captured["content"] = content
        return "fake-uuid-1"

    monkeypatch.setattr(svc, "upsert_alias", fake_upsert)

    summary = await svc.sync_mapping(db_session, mapping)
    assert summary["pushed"] == 2
    assert captured["name"] == "jt_test"
    assert captured["content"] == ["10.99.0.1", "10.99.0.2"]
    assert mapping.last_alias_uuid == "fake-uuid-1"
    assert mapping.last_synced_count == 2
    assert mapping.last_error is None
