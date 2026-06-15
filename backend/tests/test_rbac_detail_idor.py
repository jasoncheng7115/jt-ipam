"""RBAC IDOR 回歸測試：詳情/彙整/拓樸端點不可被無權限帳號用 ID 直撈。

對應修補：
- devices.py 的 /{id} 與子資源（IDOR）
- customers.py /{cid} 與 /{cid}/summary（IDOR）
- rack_diagram.py /{rack_id}/diagram（IDOR）
- topology.py 補 require_global_read 閘
"""

from __future__ import annotations

import uuid

from app.models.customer import Customer
from app.models.device import Device
from app.models.location import Rack
from app.models.permission import Permission
from app.models.user import User


async def _nonadmin_token(db_session) -> tuple[User, str]:
    from app.core.security import hash_password
    from app.services.auth import issue_access_token
    u = User(
        username=f"na-{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@t.local",
        display_name="NA", password_hash=hash_password("TestPassword2026!"),
        auth_provider="local", is_active=True, is_admin=False,
    )
    db_session.add(u)
    await db_session.flush()
    return u, issue_access_token(u)


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ───────────────────────── devices ─────────────────────────

async def test_device_detail_idor_hidden_returns_404(client, db_session):
    u, token = await _nonadmin_token(db_session)
    vis = Device(name=f"vis-{uuid.uuid4().hex[:6]}", type="switch")
    hidden = Device(name=f"hid-{uuid.uuid4().hex[:6]}", type="switch")
    db_session.add_all([vis, hidden])
    await db_session.flush()
    db_session.add(Permission(object_type="device", object_id=vis.id,
                              principal_type="user", principal_id=u.id, level="read"))
    await db_session.commit()

    # 看得到的 → 200
    r_ok = await client.get(f"/api/v1/devices/{vis.id}", headers=_hdr(token))
    assert r_ok.status_code == 200
    # 看不到的 → 404（不洩漏存在性）
    r_no = await client.get(f"/api/v1/devices/{hidden.id}", headers=_hdr(token))
    assert r_no.status_code == 404


async def test_device_subresources_idor_hidden_returns_404(client, db_session):
    _u, token = await _nonadmin_token(db_session)
    hidden = Device(name=f"hid-{uuid.uuid4().hex[:6]}", type="switch")
    db_session.add(hidden)
    await db_session.commit()
    for sub in ("integrations", "librenms", "vlans", "relations"):
        r = await client.get(f"/api/v1/devices/{hidden.id}/{sub}", headers=_hdr(token))
        assert r.status_code == 404, f"/{sub} leaked: {r.status_code}"


async def test_device_detail_admin_ok(client, auth_headers, db_session):
    d = Device(name=f"any-{uuid.uuid4().hex[:6]}", type="switch")
    db_session.add(d)
    await db_session.commit()
    r = await client.get(f"/api/v1/devices/{d.id}", headers=auth_headers)
    assert r.status_code == 200


# ───────────────────────── customers ─────────────────────────

async def test_customer_summary_idor_hidden_returns_404(client, db_session):
    _u, token = await _nonadmin_token(db_session)
    c = Customer(name=f"cust-{uuid.uuid4().hex[:6]}")
    db_session.add(c)
    await db_session.commit()
    # 無 customer 授權的非 admin → 404
    r = await client.get(f"/api/v1/customers/{c.id}/summary", headers=_hdr(token))
    assert r.status_code == 404
    r2 = await client.get(f"/api/v1/customers/{c.id}", headers=_hdr(token))
    assert r2.status_code == 404


async def test_customer_summary_with_perm_ok(client, db_session):
    u, token = await _nonadmin_token(db_session)
    c = Customer(name=f"cust-{uuid.uuid4().hex[:6]}")
    db_session.add(c)
    await db_session.flush()
    db_session.add(Permission(object_type="customer", object_id=c.id,
                              principal_type="user", principal_id=u.id, level="read"))
    await db_session.commit()
    r = await client.get(f"/api/v1/customers/{c.id}/summary", headers=_hdr(token))
    assert r.status_code == 200


# ───────────────────────── rack diagram ─────────────────────────

async def test_rack_diagram_idor_hidden_returns_404(client, db_session):
    _u, token = await _nonadmin_token(db_session)
    rack = Rack(name=f"rk-{uuid.uuid4().hex[:6]}", u_height=42)
    db_session.add(rack)
    await db_session.commit()
    r = await client.get(f"/api/v1/racks/{rack.id}/diagram", headers=_hdr(token))
    assert r.status_code == 404


# ───────────────────────── topology global-read gate ─────────────────────────

async def test_topology_requires_global_read(client, db_session):
    """只被指派特定物件（部門帳號）→ topology 403；admin → 200。"""
    u, token = await _nonadmin_token(db_session)
    d = Device(name=f"vis-{uuid.uuid4().hex[:6]}", type="switch")
    db_session.add(d)
    await db_session.flush()
    db_session.add(Permission(object_type="device", object_id=d.id,
                              principal_type="user", principal_id=u.id, level="read"))
    await db_session.commit()
    r = await client.get("/api/v1/topology", headers=_hdr(token))
    assert r.status_code == 403


async def test_topology_admin_ok(client, auth_headers):
    r = await client.get("/api/v1/topology", headers=auth_headers)
    assert r.status_code == 200
