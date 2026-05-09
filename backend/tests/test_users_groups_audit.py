"""Users / Groups admin CRUD + Audit log read endpoint。"""

from __future__ import annotations

import uuid


# ─────────────────── Audit ───────────────────


async def test_audit_list_admin_only(client):  # type: ignore[no-untyped-def]
    r = await client.get("/api/v1/audit")
    assert r.status_code == 401


async def test_audit_list_returns_paginated(client, auth_headers):  # type: ignore[no-untyped-def]
    # 先觸發一筆 audit：建 section
    await client.post(
        "/api/v1/sections", headers=auth_headers,
        json={"name": "for-audit", "description": None, "strict_mode": False},
    )
    r = await client.get("/api/v1/audit?limit=10", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert "items" in body and "total" in body
    assert body["total"] >= 1
    if body["items"]:
        item = body["items"][0]
        assert {"id", "ts", "object_type", "action", "prev_hash_hex", "this_hash_hex"} <= set(item.keys())
        assert len(item["this_hash_hex"]) == 64   # SHA-256 hex


async def test_audit_filter_by_object_type(client, auth_headers):  # type: ignore[no-untyped-def]
    await client.post(
        "/api/v1/sections", headers=auth_headers,
        json={"name": "for-audit-2", "description": None, "strict_mode": False},
    )
    r = await client.get(
        "/api/v1/audit?object_type=section&limit=5", headers=auth_headers,
    )
    assert r.status_code == 200
    for it in r.json()["items"]:
        assert it["object_type"] == "section"


async def test_audit_chain_verify(client, auth_headers):  # type: ignore[no-untyped-def]
    await client.post(
        "/api/v1/sections", headers=auth_headers,
        json={"name": "verify-1", "description": None, "strict_mode": False},
    )
    r = await client.post("/api/v1/audit/verify", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["broken_at_id"] is None
    assert body["checked"] >= 1


# ─────────────────── Users ───────────────────


async def test_users_admin_only(client):  # type: ignore[no-untyped-def]
    r = await client.get("/api/v1/users")
    assert r.status_code == 401


async def test_user_crud(client, auth_headers):  # type: ignore[no-untyped-def]
    payload = {
        "username": f"alice-{uuid.uuid4().hex[:6]}",
        "email": "alice@example.com",
        "display_name": "Alice",
        "password": "AliceTest2026!",
        "is_admin": False,
    }
    r = await client.post("/api/v1/users", headers=auth_headers, json=payload)
    assert r.status_code == 201, r.text
    u = r.json()
    uid = u["id"]
    assert u["auth_provider"] == "local"
    assert "password" not in u and "password_hash" not in u

    # weak password 拒絕
    bad = await client.post("/api/v1/users", headers=auth_headers, json={
        "username": "bob", "email": "bob@x.com", "password": "short",
    })
    assert bad.status_code == 400

    # 重複 username
    dup = await client.post("/api/v1/users", headers=auth_headers, json=payload)
    assert dup.status_code == 409

    # update：deactivate + 改密
    r2 = await client.patch(f"/api/v1/users/{uid}", headers=auth_headers, json={
        "is_active": False, "password": "NewLongPass2026!",
    })
    assert r2.status_code == 200
    assert r2.json()["is_active"] is False

    # delete
    r3 = await client.delete(f"/api/v1/users/{uid}", headers=auth_headers)
    assert r3.status_code == 204


async def test_cannot_delete_last_admin(client, auth_headers, admin_user):  # type: ignore[no-untyped-def]
    """admin_user fixture 是當下唯一 active admin → 不能砍。"""
    r = await client.delete(
        f"/api/v1/users/{admin_user.id}", headers=auth_headers,
    )
    assert r.status_code == 409


async def test_unlock_clears_lock(client, auth_headers, db_session):  # type: ignore[no-untyped-def]
    """unlock=true 應清掉 locked_until + failed_login_count。"""
    from datetime import UTC, datetime, timedelta
    from app.models.user import User
    from sqlalchemy import select

    # 先建一個被鎖的 user
    create = await client.post("/api/v1/users", headers=auth_headers, json={
        "username": f"lockee-{uuid.uuid4().hex[:6]}",
        "email": f"l-{uuid.uuid4().hex[:4]}@x.com",
        "password": "LockTest123456!",
    })
    uid = create.json()["id"]
    # 直接寫 DB 模擬鎖定
    user = (await db_session.execute(select(User).where(User.id == uid))).scalar_one()
    user.locked_until = datetime.now(UTC) + timedelta(minutes=15)
    user.failed_login_count = 5
    await db_session.commit()

    # unlock
    r = await client.patch(f"/api/v1/users/{uid}", headers=auth_headers, json={"unlock": True})
    assert r.status_code == 200
    body = r.json()
    assert body["locked_until"] is None
    assert body["failed_login_count"] == 0


# ─────────────────── Groups ───────────────────


async def test_group_crud(client, auth_headers):  # type: ignore[no-untyped-def]
    g = await client.post("/api/v1/groups", headers=auth_headers, json={
        "name": f"netadmins-{uuid.uuid4().hex[:6]}", "description": "Network admins",
    })
    assert g.status_code == 201, g.text
    gid = g.json()["id"]
    assert g.json()["member_count"] == 0

    # add member
    user_resp = await client.post("/api/v1/users", headers=auth_headers, json={
        "username": f"member-{uuid.uuid4().hex[:6]}", "email": "m@x.com",
        "password": "MemberTest2026!",
    })
    uid = user_resp.json()["id"]
    add = await client.post(
        f"/api/v1/groups/{gid}/members/{uid}", headers=auth_headers,
    )
    assert add.status_code == 204

    # list 顯示 member_count=1
    listing = await client.get(
        f"/api/v1/groups?limit=200", headers=auth_headers,
    )
    found = next((g for g in listing.json()["items"] if g["id"] == gid), None)
    assert found is not None
    assert found["member_count"] == 1

    # remove
    rm = await client.delete(
        f"/api/v1/groups/{gid}/members/{uid}", headers=auth_headers,
    )
    assert rm.status_code == 204
