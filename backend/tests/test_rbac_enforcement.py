"""RBAC Phase 2：list endpoint 只回該 user 可見的物件（非 admin）。"""

from __future__ import annotations

import uuid

from app.models.device import Device
from app.models.permission import Permission
from app.models.user import User


async def _nonadmin_token(db_session) -> tuple[User, str]:
    from app.core.security import hash_password
    from app.services.auth import issue_access_token
    u = User(username=f"na-{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@t.local",
             display_name="NA", password_hash=hash_password("TestPassword2026!"),
             auth_provider="local", is_active=True, is_admin=False)
    db_session.add(u)
    await db_session.flush()
    return u, issue_access_token(u)


async def test_devices_endpoint_filters_by_visibility(client, db_session):
    u, token = await _nonadmin_token(db_session)
    vis = Device(name=f"vis-{uuid.uuid4().hex[:6]}", type="switch")
    hidden = Device(name=f"hid-{uuid.uuid4().hex[:6]}", type="switch")
    db_session.add_all([vis, hidden])
    await db_session.flush()
    # 只授權看 vis 這台
    db_session.add(Permission(object_type="device", object_id=vis.id,
                              principal_type="user", principal_id=u.id, level="read"))
    await db_session.commit()

    r = await client.get("/api/v1/devices", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    names = {x["name"] for x in r.json()["items"]}
    assert vis.name in names
    assert hidden.name not in names


async def test_devices_endpoint_admin_sees_all(client, auth_headers, db_session):
    d = Device(name=f"any-{uuid.uuid4().hex[:6]}", type="switch")
    db_session.add(d)
    await db_session.commit()
    r = await client.get("/api/v1/devices", headers=auth_headers)
    assert r.status_code == 200
    assert d.name in {x["name"] for x in r.json()["items"]}
