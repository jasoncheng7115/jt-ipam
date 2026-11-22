"""平面配置的版本檢查（交接包 physical-v1.2 SPEC.md 第 10 節，2026-11-21）。

機房詳情與機櫃頁兩個入口編輯同一份座標；兩個人同時調整時，後存的以前會靜靜蓋掉先存的。
- GET /locations/{id}/layout 回目前的 revision（座標＋底圖算出來，不另存欄位）
- PUT rack-positions 帶 revision：跟目前不一致 → 409 floorplan_conflict，什麼都不改
- 不帶 revision 的舊用戶端照常可以存（相容）
- 換底圖也會改變 revision
"""
from __future__ import annotations

import pytest
from app.models.location import Location, Rack


async def _room(db):  # type: ignore[no-untyped-def]
    loc = Location(name="東京機房")
    db.add(loc)
    await db.flush()
    r1, r2 = Rack(name="R01", location_id=loc.id), Rack(name="R02", location_id=loc.id)
    db.add_all([r1, r2])
    await db.commit()
    return loc, r1, r2


def _pos(r, x):  # type: ignore[no-untyped-def]
    return {"id": str(r.id), "pos_x": x, "pos_y": 0.5, "pos_rot": 0}


@pytest.mark.anyio
async def test_stale_revision_is_refused(client, auth_headers, db_session) -> None:
    loc, r1, r2 = await _room(db_session)
    url = f"/api/v1/locations/{loc.id}"
    rev0 = (await client.get(f"{url}/layout", headers=auth_headers)).json()["revision"]
    # A 存檔
    r = await client.put(f"{url}/rack-positions", headers=auth_headers,
                         json={"positions": [_pos(r1, 0.2)], "revision": rev0})
    assert r.status_code == 200, r.text
    rev1 = r.json()["revision"]
    assert rev1 != rev0
    # B 還拿著舊的 revision → 409，不蓋掉 A 的
    r = await client.put(f"{url}/rack-positions", headers=auth_headers,
                         json={"positions": [_pos(r2, 0.8)], "revision": rev0})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "floorplan_conflict"
    assert (await client.get(f"{url}/layout", headers=auth_headers)).json()["revision"] == rev1
    racks = {x["name"]: x for x in (await client.get(f"/api/v1/racks?location_id={loc.id}", headers=auth_headers)).json()["items"]}
    assert racks["R01"]["pos_x"] == pytest.approx(0.2)
    assert racks["R02"]["pos_x"] is None


@pytest.mark.anyio
async def test_without_revision_still_saves(client, auth_headers, db_session) -> None:
    loc, r1, _r2 = await _room(db_session)
    r = await client.put(f"/api/v1/locations/{loc.id}/rack-positions", headers=auth_headers,
                         json={"positions": [_pos(r1, 0.3)]})
    assert r.status_code == 200
    assert r.json()["updated"] == 1


@pytest.mark.anyio
async def test_floor_plan_change_changes_revision(client, auth_headers, db_session) -> None:
    loc, _r1, _r2 = await _room(db_session)
    url = f"/api/v1/locations/{loc.id}"
    rev0 = (await client.get(f"{url}/layout", headers=auth_headers)).json()["revision"]
    png = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
           b"\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")
    r = await client.post(f"{url}/floorplan", headers=auth_headers, files={"file": ("plan.png", png, "image/png")})
    assert r.status_code in (200, 201), r.text
    assert (await client.get(f"{url}/layout", headers=auth_headers)).json()["revision"] != rev0
