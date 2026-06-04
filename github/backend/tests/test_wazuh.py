"""Wazuh：CRUD + agent sync（HTTP mocked）+ missing-agent 偵測。"""

from __future__ import annotations

import uuid
from typing import Any

import pytest


async def test_wazuh_crud_requires_admin(client):  # type: ignore[no-untyped-def]
    r = await client.get("/api/v1/wazuh/instances")
    assert r.status_code == 401


async def test_wazuh_instance_crud(client, auth_headers):  # type: ignore[no-untyped-def]
    payload = {
        "name": "wazuh-prod",
        "api_url": "https://wazuh.example.com:55000/",
        "api_user": "wazuh-api-user",
        "api_password": "S3cret123!",
        "verify_tls": True,
    }
    r = await client.post(
        "/api/v1/wazuh/instances", headers=auth_headers, json=payload,
    )
    assert r.status_code == 201, r.text
    inst = r.json()
    iid = inst["id"]
    assert "api_password" not in inst
    assert inst["api_user"] == "wazuh-api-user"

    r2 = await client.patch(
        f"/api/v1/wazuh/instances/{iid}", headers=auth_headers,
        json={"description": "primary"},
    )
    assert r2.status_code == 200
    assert r2.json()["description"] == "primary"

    r3 = await client.delete(
        f"/api/v1/wazuh/instances/{iid}", headers=auth_headers,
    )
    assert r3.status_code == 204


def test_password_encrypt_aad():
    """同一 instance id 解密 OK；換 id 失敗。"""
    from app.services import wazuh as svc

    fid = uuid.uuid4()
    enc, nonce = svc.encrypt_password(fid, "p@ss")

    class FakeInst:
        id = fid
        api_password_enc = enc
        api_password_nonce = nonce

    assert svc._decrypt_password(FakeInst()) == "p@ss"

    class FakeInst2(FakeInst):
        id = uuid.uuid4()

    with pytest.raises(Exception):
        svc._decrypt_password(FakeInst2())


def test_parse_keep_alive():
    from app.services.wazuh import _parse_keep_alive
    assert _parse_keep_alive(None) is None
    assert _parse_keep_alive("") is None
    assert _parse_keep_alive("9999-12-31T23:59:59Z") is None   # never connected
    dt = _parse_keep_alive("2024-01-15T10:23:45Z")
    assert dt is not None
    assert dt.year == 2024 and dt.month == 1


async def test_sync_agents_with_mocked_fetch(  # type: ignore[no-untyped-def]
    db_session, admin_user, monkeypatch,
):
    """fetch_agents → 假資料；sync 應建 agent + 對映 IPAddress。"""
    from app.models.address import IPAddress
    from app.models.section import Section
    from app.models.subnet import Subnet
    from app.models.wazuh import WazuhAgent, WazuhInstance
    from app.services import wazuh as svc

    # 準備 Section/Subnet/IP（10.10.0.10）
    sec = Section(name=f"wazuh-sec-{uuid.uuid4().hex[:6]}", strict_mode=False)
    db_session.add(sec)
    await db_session.flush()
    sub = Subnet(section_id=sec.id, cidr="10.10.0.0/24")
    db_session.add(sub)
    await db_session.flush()
    addr1 = IPAddress(subnet_id=sub.id, ip="10.10.0.10", hostname="srv01")
    addr2 = IPAddress(subnet_id=sub.id, ip="10.10.0.20", hostname="srv02")
    db_session.add_all([addr1, addr2])
    await db_session.flush()

    enc, nonce = svc.encrypt_password(uuid.uuid4(), "p")
    inst = WazuhInstance(
        name=f"w-{uuid.uuid4().hex[:6]}",
        api_url="https://wazuh.local:55000",
        api_user="apiu",
        api_password_enc=enc, api_password_nonce=nonce,
        enabled=True, verify_tls=True,
    )
    db_session.add(inst)
    await db_session.flush()
    real_enc, real_nonce = svc.encrypt_password(inst.id, "p")
    inst.api_password_enc = real_enc
    inst.api_password_nonce = real_nonce
    await db_session.commit()

    fake_agents: list[dict[str, Any]] = [
        {
            "id": "001", "name": "srv01", "ip": "10.10.0.10",
            "registerIP": "10.10.0.10", "status": "active",
            "os": {"platform": "ubuntu", "version": "22.04"},
            "version": "v4.7.0", "group": ["default"], "node_name": "manager",
            "lastKeepAlive": "2026-05-09T12:00:00Z",
        },
        {
            "id": "002", "name": "srv-unknown", "ip": "172.16.99.1",
            "registerIP": "172.16.99.1", "status": "disconnected",
            "os": {"platform": "windows", "version": "Server 2022"},
            "version": "v4.7.0", "group": "default", "node_name": "manager",
            "lastKeepAlive": "9999-12-31T23:59:59Z",
        },
        # 000 = manager 自己，應被忽略
        {
            "id": "000", "name": "manager", "ip": "127.0.0.1", "status": "active",
            "lastKeepAlive": "2026-05-09T12:00:01Z",
        },
    ]

    async def fake_fetch(_inst, *, batch=500):  # type: ignore[no-untyped-def]
        return fake_agents

    monkeypatch.setattr(svc, "fetch_agents", fake_fetch)

    summary = await svc.sync_agents(db_session, inst)
    assert summary["fetched"] == 3
    assert summary["new"] == 2     # 000 被排除
    assert summary["updated"] == 0
    assert summary["matched_ip"] == 1   # 只有 10.10.0.10 對得上

    # 第二次跑 → updated
    summary2 = await svc.sync_agents(db_session, inst)
    assert summary2["new"] == 0
    assert summary2["updated"] == 2

    # 確認 agent 001 link 到 addr1
    rows = (await db_session.execute(
        __import__("sqlalchemy").select(WazuhAgent).where(WazuhAgent.agent_id == "001")
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].jt_ipam_address_id == addr1.id


async def test_missing_agents_lists_unmonitored_ips(  # type: ignore[no-untyped-def]
    db_session, admin_user,
):
    """有 hostname 但沒 active WazuhAgent → 出現在 missing-agents。"""
    from app.models.address import IPAddress
    from app.models.section import Section
    from app.models.subnet import Subnet
    from app.models.wazuh import WazuhAgent, WazuhInstance
    from app.services import wazuh as svc

    sec = Section(name=f"miss-sec-{uuid.uuid4().hex[:6]}", strict_mode=False)
    db_session.add(sec)
    await db_session.flush()
    sub = Subnet(section_id=sec.id, cidr="10.20.0.0/24")
    db_session.add(sub)
    await db_session.flush()
    a_with = IPAddress(subnet_id=sub.id, ip="10.20.0.10", hostname="has-agent")
    a_without = IPAddress(subnet_id=sub.id, ip="10.20.0.11", hostname="missing-agent")
    a_no_host = IPAddress(subnet_id=sub.id, ip="10.20.0.12", hostname=None)
    db_session.add_all([a_with, a_without, a_no_host])
    await db_session.flush()

    enc, nonce = svc.encrypt_password(uuid.uuid4(), "p")
    inst = WazuhInstance(
        name=f"miss-{uuid.uuid4().hex[:6]}",
        api_url="https://w.local",
        api_user="u",
        api_password_enc=enc, api_password_nonce=nonce,
    )
    db_session.add(inst)
    await db_session.flush()
    db_session.add(WazuhAgent(
        instance_id=inst.id, agent_id="001", name="x", ip="10.20.0.10",
        status="active", jt_ipam_address_id=a_with.id,
    ))
    await db_session.commit()

    rows = await svc.find_missing_agents(db_session, hostnamed_only=True)
    rows_ids = {r["ip_address_id"] for r in rows}
    assert str(a_without.id) in rows_ids
    assert str(a_with.id) not in rows_ids
    assert str(a_no_host.id) not in rows_ids   # hostname 為空被濾掉
