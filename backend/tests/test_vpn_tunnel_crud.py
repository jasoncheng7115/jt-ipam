"""站對站 VPN 通道的編輯與刪除（2026-11-21）。

以前只有清單與一支沒有畫面的新增 API：手動記的通道建了改不了、刪不掉。
- 手動建立的通道：可編輯、可刪除，都寫稽核
- 防火牆同步來的通道（source_origin 有值）：唯讀，下次同步會蓋掉，改了也沒用 → 409 vpn_tunnel_synced
- 類型、狀態填錯回 422（以前撞上資料庫檢查約束，被當成「名稱重複」回 409）
- 兩端不可以是同一台裝置
"""
from __future__ import annotations

import pytest
from app.models.audit import AuditLog
from app.models.device import Device
from app.models.physical import VPNTunnel
from sqlalchemy import select

BASE = "/api/v1/vpn-tunnels"


async def _devices(db):  # type: ignore[no-untyped-def]
    a, b = Device(name="fw-hq"), Device(name="fw-branch")
    db.add_all([a, b])
    await db.commit()
    return a, b


@pytest.mark.anyio
async def test_manual_tunnel_edit_and_delete(client, auth_headers, db_session) -> None:
    a, b = await _devices(db_session)
    r = await client.post(BASE, headers=auth_headers, json={
        "name": "hq-branch", "type": "wireguard", "a_device_id": str(a.id), "b_device_id": str(b.id),
        "a_endpoint": "198.51.100.1", "b_endpoint": "203.0.113.1"})
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    assert r.json()["source_origin"] is None

    r = await client.patch(f"{BASE}/{tid}", headers=auth_headers, json={
        "name": "hq-branch-2", "type": "ipsec_ikev2", "status": "offline", "a_device_id": str(a.id),
        "b_device_id": None, "a_endpoint": "198.51.100.1", "b_endpoint": None,
        "encryption_algo": "aes256gcm", "auth_algo": None, "description": "備援線路"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "hq-branch-2"
    assert body["status"] == "offline"
    assert body["b_device_id"] is None, "清空要真的清掉"
    assert body["description"] == "備援線路"

    rows = (await client.get(BASE, headers=auth_headers)).json()["items"]
    assert [x["name"] for x in rows] == ["hq-branch-2"]

    assert (await client.delete(f"{BASE}/{tid}", headers=auth_headers)).status_code == 204
    assert (await client.get(BASE, headers=auth_headers)).json()["items"] == []
    acts = (await db_session.execute(select(AuditLog.action).where(
        AuditLog.object_type == "vpn_tunnel", AuditLog.object_id == tid))).scalars().all()
    assert sorted(acts) == ["create", "delete", "update"]


@pytest.mark.anyio
async def test_synced_tunnel_is_read_only(client, auth_headers, db_session) -> None:
    t = VPNTunnel(name="fg/hq-to-dc", type="ipsec_ikev2", source_origin="fortigate:00000000-0000-0000-0000-000000000001")
    db_session.add(t)
    await db_session.commit()
    item = (await client.get(BASE, headers=auth_headers)).json()["items"][0]
    assert item["source_origin"].startswith("fortigate:")
    r = await client.patch(f"{BASE}/{t.id}", headers=auth_headers, json={"name": "x", "type": "other"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "vpn_tunnel_synced"
    r = await client.delete(f"{BASE}/{t.id}", headers=auth_headers)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "vpn_tunnel_synced"


@pytest.mark.anyio
async def test_bad_type_status_and_same_device(client, auth_headers, db_session) -> None:
    a, _b = await _devices(db_session)
    assert (await client.post(BASE, headers=auth_headers, json={"name": "x", "type": "gre"})).status_code == 422
    assert (await client.post(BASE, headers=auth_headers,
                              json={"name": "x", "type": "other", "status": "up"})).status_code == 422
    r = await client.post(BASE, headers=auth_headers, json={
        "name": "x", "type": "other", "a_device_id": str(a.id), "b_device_id": str(a.id)})
    assert r.status_code == 422
    r = await client.post(BASE, headers=auth_headers, json={"name": "dup", "type": "other"})
    assert r.status_code == 201
    r = await client.post(BASE, headers=auth_headers, json={"name": "dup", "type": "other"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "vpn_tunnel_name_taken"


@pytest.mark.anyio
async def test_non_admin_cannot_write(client, db_session) -> None:
    from app.core.security import hash_password
    from app.models.user import User
    from app.services.auth import issue_access_token
    u = User(username="vpn-reader", email="vpn-reader@t.local", display_name="U",
             password_hash=hash_password("TestPassword2026!"), auth_provider="local", is_active=True)
    db_session.add(u)
    await db_session.commit()
    r = await client.post(BASE, headers={"Authorization": f"Bearer {issue_access_token(u)}"},
                          json={"name": "x", "type": "other"})
    assert r.status_code == 403
