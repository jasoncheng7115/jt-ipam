"""裝置安裝方向 rack_face 往返；機房清單回傳 rack_count / device_count。"""

from __future__ import annotations


async def _mk_location(client, auth_headers, name="rf-loc") -> str:
    r = await client.post("/api/v1/locations", headers=auth_headers, json={"name": name})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def test_device_rack_face_roundtrip(client, auth_headers):
    loc_id = await _mk_location(client, auth_headers, "rf-loc-1")
    rk = await client.post("/api/v1/racks", headers=auth_headers,
                           json={"name": "rf-rack", "u_height": 42, "location_id": loc_id})
    assert rk.status_code in (200, 201), rk.text
    rack_id = rk.json()["id"]

    dev = await client.post("/api/v1/devices", headers=auth_headers, json={
        "name": "rf-dev", "type": "server", "location_id": loc_id,
        "rack_id": rack_id, "u_position": 5, "u_size": 2, "rack_face": "rear",
    })
    assert dev.status_code in (200, 201), dev.text
    assert dev.json()["rack_face"] == "rear"

    # update 改成 front
    dev_id = dev.json()["id"]
    up = await client.patch(f"/api/v1/devices/{dev_id}", headers=auth_headers,
                            json={"rack_face": "front"})
    assert up.status_code == 200, up.text
    assert up.json()["rack_face"] == "front"


async def test_location_list_counts(client, auth_headers):
    loc_id = await _mk_location(client, auth_headers, "rf-loc-2")
    await client.post("/api/v1/racks", headers=auth_headers,
                      json={"name": "rf-rack-2", "u_height": 42, "location_id": loc_id})
    await client.post("/api/v1/devices", headers=auth_headers,
                      json={"name": "rf-dev-2", "type": "switch", "location_id": loc_id})

    r = await client.get("/api/v1/locations", headers=auth_headers, params={"page_size": 500})
    row = next(x for x in r.json()["items"] if x["id"] == loc_id)
    assert row["rack_count"] == 1
    assert row["device_count"] == 1
