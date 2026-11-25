"""佈線自動推斷的 API：清單帶出來源與依據、人改過就變人工、刪掉不再自動建回、設定與建議清單。"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.audit import AuditLog
from app.models.device import Device
from app.models.librenms import FDBEntry
from app.models.physical import Cable, CableSuggestion, DevicePort
from app.services import cable_inference as ci


async def _setup(s) -> dict:
    sw = Device(name=f"sw-{uuid.uuid4().hex[:4]}", type="switch")
    srv = Device(name=f"srv-{uuid.uuid4().hex[:4]}", type="server")
    s.add_all([sw, srv])
    await s.flush()
    p1 = DevicePort(device_id=sw.id, name="Gi1/0/1")
    p2 = DevicePort(device_id=sw.id, name="Gi1/0/2")
    nic = DevicePort(device_id=srv.id, name="eno1", mac_address="02:00:00:00:01:01")
    s.add_all([p1, p2, nic])
    s.add(FDBEntry(mac="02:00:00:00:01:01", port_name="Gi1/0/1", source="snmp", switch_device_id=sw.id,
                   last_seen_at=datetime.now(UTC) - timedelta(minutes=5)))
    await s.flush()
    return {"sw": sw, "srv": srv, "p1": p1, "p2": p2, "nic": nic}


async def _user_headers(s, *, admin: bool) -> dict:
    from app.core.security import hash_password
    from app.models.user import User
    from app.services.auth import issue_access_token
    u = User(username=f"u-{uuid.uuid4().hex[:6]}", email=f"u-{uuid.uuid4().hex[:6]}@test.local", display_name="u",
             password_hash=hash_password("TestPassword2026!"), auth_provider="local", is_active=True, is_admin=admin)
    s.add(u)
    await s.commit()
    return {"Authorization": f"Bearer {issue_access_token(u)}"}


@pytest.mark.anyio
async def test_cable_list_shows_source_and_evidence(client, auth_headers, db_session) -> None:
    await _setup(db_session)
    await ci.run(db_session)
    await db_session.commit()
    r = await client.get("/api/v1/cables", headers=auth_headers, params={"source": "auto"})
    assert r.status_code == 200
    (item,) = r.json()["items"]
    assert item["source"] == "auto" and item["evidence"]["kind"] == "fdb_single_mac" and item["verified_at"]
    r = await client.get("/api/v1/cables", headers=auth_headers, params={"source": "manual"})
    assert r.json()["items"] == []


@pytest.mark.anyio
async def test_editing_an_auto_cable_makes_it_manual(client, auth_headers, db_session) -> None:
    await _setup(db_session)
    await ci.run(db_session)
    await db_session.commit()
    cid = (await db_session.execute(select(Cable.id))).scalar_one()
    r = await client.patch(f"/api/v1/cables/{cid}", headers=auth_headers, json={"label": "R5-01"})
    assert r.status_code == 200 and r.json()["source"] == "manual" and r.json()["label"] == "R5-01"


@pytest.mark.anyio
async def test_deleting_an_auto_cable_is_remembered(client, auth_headers, db_session) -> None:
    await _setup(db_session)
    await ci.run(db_session)
    await db_session.commit()
    cid = (await db_session.execute(select(Cable.id))).scalar_one()
    assert (await client.delete(f"/api/v1/cables/{cid}", headers=auth_headers)).status_code == 204
    db_session.expire_all()
    st = await ci.run(db_session)
    await db_session.commit()
    assert st["created"] == 0
    assert (await db_session.execute(select(Cable))).scalars().all() == []
    r = await client.get("/api/v1/cables/suggestions", headers=auth_headers, params={"status": "dismissed"})
    (s,) = r.json()["items"]
    assert s["status"] == "dismissed" and "@" in s["a_end"] and "@" in s["b_end"]
    # 復原：之後又可以自動建
    assert (await client.post(f"/api/v1/cables/suggestions/{s['id']}/restore", headers=auth_headers)).status_code == 200
    db_session.expire_all()
    assert (await ci.run(db_session))["created"] == 1


@pytest.mark.anyio
async def test_settings_and_run_now(client, auth_headers, db_session) -> None:
    await _setup(db_session)
    await db_session.commit()
    r = await client.get("/api/v1/cables/inference", headers=auth_headers)
    assert r.status_code == 200 and r.json()["enabled"] is True
    r = await client.post("/api/v1/cables/inference/run", headers=auth_headers)
    assert r.status_code == 200 and r.json()["created"] == 1
    r = await client.get("/api/v1/cables/inference", headers=auth_headers)
    assert r.json()["last_summary"]["created"] == 1 and r.json()["last_run_at"]
    r = await client.put("/api/v1/cables/inference", headers=auth_headers, json={"enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] is False
    acts = (await db_session.execute(select(AuditLog.action).where(AuditLog.object_type == "cable_inference"))).scalars().all()
    assert set(acts) >= {"run", "update"}
    # 系統自動建的線也有稽核（沒有操作者）
    auto = (await db_session.execute(select(AuditLog).where(AuditLog.action == "auto_create"))).scalars().all()
    assert auto and auto[0].actor_user_id is None


@pytest.mark.anyio
async def test_accept_and_dismiss_suggestions(client, auth_headers, db_session) -> None:
    d = await _setup(db_session)
    other = Device(name=f"o-{uuid.uuid4().hex[:4]}", type="server")
    db_session.add(other)
    await db_session.flush()
    op = DevicePort(device_id=other.id, name="eth0")
    db_session.add(op)
    await db_session.commit()
    # 人工接在 Gi1/0/1 上 → 證據（srv）跟它衝突，列成建議
    r = await client.post("/api/v1/cables/connect", headers=auth_headers,
                          json={"a_port_id": str(d["p1"].id), "b_port_id": str(op.id)})
    assert r.status_code == 201
    await ci.run(db_session)
    await db_session.commit()
    r = await client.get("/api/v1/cables/suggestions", headers=auth_headers)
    (s,) = r.json()["items"]
    assert s["reason"] == "conflict_manual" and s["kind"] == "fdb_single_mac"
    # 接受：那個埠還被人工的線占著 → 409，不會變成兩條
    r = await client.post(f"/api/v1/cables/suggestions/{s['id']}/accept", headers=auth_headers)
    assert r.status_code == 409
    r = await client.post(f"/api/v1/cables/suggestions/{s['id']}/dismiss", headers=auth_headers)
    assert r.status_code == 200 and r.json()["status"] == "dismissed"
    assert (await client.get("/api/v1/cables/suggestions", headers=auth_headers)).json()["items"] == []


@pytest.mark.anyio
async def test_accepting_a_suggestion_creates_a_confirmed_cable(client, auth_headers, db_session) -> None:
    d = await _setup(db_session)
    a, b = sorted([d["p2"].id, d["nic"].id], key=str)
    s = CableSuggestion(a_port_id=a, b_port_id=b, kind="fdb_uplink", reason="uplink_unverified",
                        evidence={"kind": "fdb_uplink"}, status="open")
    db_session.add(s)
    await db_session.commit()
    r = await client.post(f"/api/v1/cables/suggestions/{s.id}/accept", headers=auth_headers)
    assert r.status_code == 201
    body = r.json()
    assert body["source"] == "manual" and body["status"] == "connected" and body["evidence"]["kind"] == "fdb_uplink"
    assert (await db_session.execute(select(CableSuggestion).where(CableSuggestion.id == s.id))).scalar_one_or_none() is None


@pytest.mark.anyio
async def test_non_admin_cannot_change_inference(client, db_session) -> None:
    h = await _user_headers(db_session, admin=False)
    assert (await client.put("/api/v1/cables/inference", headers=h, json={"enabled": False})).status_code == 403
    assert (await client.post("/api/v1/cables/inference/run", headers=h)).status_code == 403
    assert (await client.get("/api/v1/cables/suggestions", headers=h)).status_code == 403


@pytest.mark.anyio
async def test_accept_with_replace_swaps_the_conflicting_manual_cable(client, auth_headers, db_session) -> None:
    """衝突建議（人工的線接到 vmbr、證據說是實體網卡）：「接受並取代」移除那條人工線再建新的，兩件事都有稽核。"""
    d = await _setup(db_session)
    other = Device(name=f"o-{uuid.uuid4().hex[:4]}", type="server")
    db_session.add(other)
    await db_session.flush()
    op = DevicePort(device_id=other.id, name="vmbr0")
    db_session.add(op)
    await db_session.commit()
    r = await client.post("/api/v1/cables/connect", headers=auth_headers,
                          json={"a_port_id": str(d["p1"].id), "b_port_id": str(op.id), "label": "old"})
    old_id = r.json()["id"]
    await ci.run(db_session)
    await db_session.commit()
    (s,) = (await client.get("/api/v1/cables/suggestions", headers=auth_headers)).json()["items"]
    r = await client.post(f"/api/v1/cables/suggestions/{s['id']}/accept", headers=auth_headers, params={"replace": True})
    assert r.status_code == 201
    db_session.expire_all()
    ids = (await db_session.execute(select(Cable.id))).scalars().all()
    assert len(ids) == 1 and str(ids[0]) != old_id
    acts = (await db_session.execute(select(AuditLog.action).where(AuditLog.object_id == old_id))).scalars().all()
    assert "delete" in acts
