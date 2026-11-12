"""機房、機櫃、裝置、單位、區段的寫入照角色授權判斷（客戶 2026-11-12）。

客戶把 LDAP 帳號加進「系統管理員」角色，「新增機櫃」卻反灰、提示要管理員；一定要打開帳號的
「管理員」開關才行。原因：這幾類的寫入端點只看 `is_admin`，角色給的授權（系統管理員＝全部 admin、
網路操作員＝裝置 write）完全沒用到。子網路與 IP 早就照授權判斷，這幾類漏了。

這裡用內建角色的實際授權內容（DEFAULT_ROLES）建群組，走真的 HTTP 請求。
"""
from __future__ import annotations

import uuid

import pytest
from app.models.location import Location, Rack
from app.models.permission import Permission
from app.models.user import Group, User, UserGroupMember
from app.services.permission import DEFAULT_ROLES


async def _user_with(db_session, grants: dict[str, str], *, scoped: list[tuple[str, uuid.UUID, str]] = ()) -> dict[str, str]:
    """建一個非管理員帳號，掛在一個帶 `grants`（萬用）與 `scoped`（指定物件）授權的群組。"""
    from app.core.security import hash_password
    from app.services.auth import issue_access_token
    tag = uuid.uuid4().hex[:8]
    u = User(username=f"role-{tag}", email="", display_name="Role", password_hash=hash_password("TestPassword2026!"),
             auth_provider="local", is_active=True, is_admin=False)
    g = Group(name=f"role-group-{tag}")
    db_session.add_all([u, g])
    await db_session.flush()
    db_session.add(UserGroupMember(user_id=u.id, group_id=g.id))
    for t, lvl in grants.items():
        db_session.add(Permission(object_type=t, object_id=None, principal_type="group", principal_id=g.id, level=lvl))
    for t, oid, lvl in scoped:
        db_session.add(Permission(object_type=t, object_id=oid, principal_type="group", principal_id=g.id, level=lvl))
    await db_session.commit()
    await db_session.refresh(u)
    return {"Authorization": f"Bearer {issue_access_token(u)}"}


async def _loc(db_session) -> Location:
    loc = Location(name=f"room-{uuid.uuid4().hex[:6]}")
    db_session.add(loc)
    await db_session.commit()
    return loc


async def _rack(db_session, loc: Location) -> Rack:
    r = Rack(name=f"rk-{uuid.uuid4().hex[:6]}", location_id=loc.id)
    db_session.add(r)
    await db_session.commit()
    return r


@pytest.mark.anyio
async def test_system_admin_role_can_manage_rooms_racks_devices(client, db_session) -> None:
    h = await _user_with(db_session, DEFAULT_ROLES["系統管理員"])
    r = await client.post("/api/v1/locations", headers=h, json={"name": f"room-{uuid.uuid4().hex[:6]}"})
    assert r.status_code == 201, r.text
    loc_id = r.json()["id"]
    r = await client.patch(f"/api/v1/locations/{loc_id}", headers=h, json={"address": "1F"})
    assert r.status_code == 200, r.text
    r = await client.post("/api/v1/racks", headers=h, json={"name": "R01", "location_id": loc_id})
    assert r.status_code == 201, r.text
    rack_id = r.json()["id"]
    r = await client.patch(f"/api/v1/racks/{rack_id}", headers=h, json={"u_height": 45})
    assert r.status_code == 200, r.text
    r = await client.post("/api/v1/devices", headers=h, json={"name": f"sw-{uuid.uuid4().hex[:6]}", "rack_id": rack_id,
                                                              "location_id": loc_id, "u_position": 1, "u_size": 1})
    assert r.status_code == 201, r.text
    dev_id = r.json()["id"]
    r = await client.patch(f"/api/v1/devices/{dev_id}", headers=h, json={"description": "core"})
    assert r.status_code == 200, r.text
    r = await client.post("/api/v1/customers", headers=h, json={"name": f"cust-{uuid.uuid4().hex[:6]}"})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    r = await client.patch(f"/api/v1/customers/{cid}", headers=h, json={"title": "t"})
    assert r.status_code == 200, r.text
    r = await client.post("/api/v1/sections", headers=h, json={"name": f"sec-{uuid.uuid4().hex[:6]}"})
    assert r.status_code == 201, r.text
    for path in (f"/api/v1/devices/{dev_id}", f"/api/v1/racks/{rack_id}", f"/api/v1/locations/{loc_id}",
                 f"/api/v1/customers/{cid}"):
        r = await client.delete(path, headers=h)
        assert r.status_code == 204, (path, r.text)


@pytest.mark.anyio
async def test_network_operator_manages_devices_but_not_racks(client, db_session) -> None:
    loc = await _loc(db_session)
    rack = await _rack(db_session, loc)
    h = await _user_with(db_session, DEFAULT_ROLES["網路操作員"])
    # 裝置 write：可以新增到看得到的機櫃、可以修改
    r = await client.post("/api/v1/devices", headers=h, json={"name": f"ap-{uuid.uuid4().hex[:6]}", "rack_id": str(rack.id),
                                                              "location_id": str(loc.id), "u_position": 2, "u_size": 1})
    assert r.status_code == 201, r.text
    dev_id = r.json()["id"]
    assert (await client.patch(f"/api/v1/devices/{dev_id}", headers=h, json={"serial": "X1"})).status_code == 200
    # 刪除要 admin
    assert (await client.delete(f"/api/v1/devices/{dev_id}", headers=h)).status_code in (403, 404)
    # 機櫃、機房只有 read
    r = await client.post("/api/v1/racks", headers=h, json={"name": "R9", "location_id": str(loc.id)})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "create_needs_write"
    assert (await client.patch(f"/api/v1/racks/{rack.id}", headers=h, json={"name": "x"})).status_code in (403, 404)
    assert (await client.post("/api/v1/locations", headers=h, json={"name": "nope"})).status_code == 403


@pytest.mark.anyio
async def test_scoped_room_manager_creates_racks_only_in_their_room(client, db_session) -> None:
    mine, other = await _loc(db_session), await _loc(db_session)
    h = await _user_with(db_session, {}, scoped=[("location", mine.id, "admin")])
    r = await client.post("/api/v1/racks", headers=h, json={"name": "A1", "location_id": str(mine.id)})
    assert r.status_code == 201, r.text
    rack_id = r.json()["id"]
    # 別的機房看不到 → 跟不存在一樣
    assert (await client.post("/api/v1/racks", headers=h, json={"name": "A2", "location_id": str(other.id)})).status_code == 404
    # 不指定機房 → 沒有萬用授權
    assert (await client.post("/api/v1/racks", headers=h, json={"name": "A3"})).status_code == 403
    assert (await client.patch(f"/api/v1/racks/{rack_id}", headers=h, json={"u_height": 40})).status_code == 200
    # 搬到看不到的機房
    assert (await client.patch(f"/api/v1/racks/{rack_id}", headers=h,
                               json={"location_id": str(other.id)})).status_code in (403, 404)
    # 機房本身：可以改自己的，不能新增別的
    assert (await client.patch(f"/api/v1/locations/{mine.id}", headers=h, json={"address": "2F"})).status_code == 200
    assert (await client.patch(f"/api/v1/locations/{other.id}", headers=h, json={"address": "2F"})).status_code == 404
    assert (await client.post("/api/v1/locations", headers=h, json={"name": "new"})).status_code == 403


@pytest.mark.anyio
async def test_read_only_role_cannot_write(client, db_session) -> None:
    loc = await _loc(db_session)
    rack = await _rack(db_session, loc)
    h = await _user_with(db_session, DEFAULT_ROLES["唯讀檢視者"])
    assert (await client.post("/api/v1/racks", headers=h, json={"name": "R", "location_id": str(loc.id)})).status_code == 403
    assert (await client.patch(f"/api/v1/racks/{rack.id}", headers=h, json={"name": "x"})).status_code == 404
    assert (await client.delete(f"/api/v1/racks/{rack.id}", headers=h)).status_code == 404
    assert (await client.post("/api/v1/devices", headers=h, json={"name": "d"})).status_code == 403
    assert (await client.post("/api/v1/customers", headers=h, json={"name": "c"})).status_code == 403
    r = await client.post("/api/v1/racks/bulk-delete", headers=h, json={"ids": [str(rack.id)]})
    assert r.status_code == 200
    assert r.json()["deleted"] == 0
    assert r.json()["errors"][0]["error"] == "no_permission"


@pytest.mark.anyio
async def test_device_tenant_needs_write_on_that_tenant(client, db_session) -> None:
    """裝置 write 但單位只有 read：不能把裝置掛到那個單位（等於把資料塞進別人的範圍）。"""
    from app.models.customer import Customer
    c = Customer(name=f"tenant-{uuid.uuid4().hex[:6]}")
    db_session.add(c)
    await db_session.commit()
    h = await _user_with(db_session, DEFAULT_ROLES["網路操作員"])
    r = await client.post("/api/v1/devices", headers=h, json={"name": "d1", "customer_id": str(c.id)})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "parent_write_required"
    r = await client.post("/api/v1/devices", headers=h, json={"name": f"d2-{uuid.uuid4().hex[:4]}"})
    assert r.status_code == 201
    r = await client.patch(f"/api/v1/devices/{r.json()['id']}", headers=h, json={"customer_id": str(c.id)})
    assert r.status_code == 403


@pytest.mark.anyio
async def test_me_reports_which_types_can_be_created(client, db_session) -> None:
    loc = await _loc(db_session)
    h = await _user_with(db_session, DEFAULT_ROLES["網路操作員"])
    me = (await client.get("/api/v1/auth/me", headers=h)).json()
    assert set(me["create_types"]) == {"subnet", "ip", "device"}
    h = await _user_with(db_session, {}, scoped=[("location", loc.id, "write")])
    me = (await client.get("/api/v1/auth/me", headers=h)).json()
    # 被指派機房 → 可以在那裡新增機櫃與裝置（機房本身不能新增）
    assert set(me["create_types"]) == {"rack", "device"}
