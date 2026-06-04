"""機櫃示意圖端點帶出裝置的 rack_face（安裝方向），供前端標示前/後。"""

from __future__ import annotations


async def _mk_location(client, auth_headers, name) -> str:
    r = await client.post("/api/v1/locations", headers=auth_headers, json={"name": name})
    return r.json()["id"]


async def test_diagram_includes_rack_face(client, auth_headers):
    loc = await _mk_location(client, auth_headers, "rd-loc")
    rk = await client.post("/api/v1/racks", headers=auth_headers,
                           json={"name": "rd-rack", "u_height": 42, "location_id": loc})
    rack_id = rk.json()["id"]
    await client.post("/api/v1/devices", headers=auth_headers, json={
        "name": "rear-dev", "type": "server", "location_id": loc,
        "rack_id": rack_id, "u_position": 3, "u_size": 1, "rack_face": "rear",
    })
    r = await client.get(f"/api/v1/racks/{rack_id}/diagram", headers=auth_headers)
    assert r.status_code == 200, r.text
    dev = next(d for d in r.json()["devices"] if d["name"] == "rear-dev")
    assert dev["rack_face"] == "rear"
