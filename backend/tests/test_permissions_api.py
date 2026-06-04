"""RBAC 權限指派 endpoints（admin）。"""

from __future__ import annotations

import uuid

from app.models.user import Group


async def _group(db_session) -> Group:
    g = Group(name=f"role-{uuid.uuid4().hex[:6]}")
    db_session.add(g)
    await db_session.commit()
    return g


async def test_permission_grant_upsert_list_delete(client, auth_headers, db_session):
    g = await _group(db_session)
    # 授權：全部 subnet read（wildcard）
    body = {"object_type": "subnet", "object_id": None,
            "principal_type": "group", "principal_id": str(g.id), "level": "read"}
    r = await client.post("/api/v1/system/permissions", json=body, headers=auth_headers)
    assert r.status_code == 200, r.text
    gid = r.json()["id"]
    assert r.json()["object_id"] is None and r.json()["level"] == "read"

    # 同一目標再 POST → upsert 成 write（不應變兩筆）
    body["level"] = "write"
    r2 = await client.post("/api/v1/system/permissions", json=body, headers=auth_headers)
    assert r2.status_code == 200 and r2.json()["id"] == gid and r2.json()["level"] == "write"

    lst = await client.get("/api/v1/system/permissions",
                           params={"principal_type": "group", "principal_id": str(g.id)},
                           headers=auth_headers)
    assert lst.status_code == 200 and len([x for x in lst.json() if x["id"] == gid]) == 1

    d = await client.delete(f"/api/v1/system/permissions/{gid}", headers=auth_headers)
    assert d.status_code == 204
    lst2 = await client.get("/api/v1/system/permissions",
                            params={"principal_type": "group", "principal_id": str(g.id)},
                            headers=auth_headers)
    assert all(x["id"] != gid for x in lst2.json())


async def test_roles_endpoint(client, auth_headers, db_session):
    g = await _group(db_session)
    r = await client.get("/api/v1/system/roles", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert "object_types" in body and len(body["object_types"]) == 7
    assert any(role["id"] == str(g.id) for role in body["roles"])
