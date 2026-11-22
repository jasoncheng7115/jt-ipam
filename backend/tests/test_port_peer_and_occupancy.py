"""跳接面板配對、埠與插座占用（交接包 physical-v1.2 CABLING.md 第 1 項，2026-11-21）。

- 改配對：F1 從 R1 改成 R3 → R1 不再指向 F1；清成空的也要清掉對方的反向指標
- 目標已經配給別人：不靜默搶走，409 port_peer_taken；明確帶 replace_peer 才重配，並清掉原本那一對
- 配對只能是同一台裝置的另一個埠
- 接線一次完成（POST /cables/connect）：兩端不能是同一個埠；任一端已被「已接線」的纜線占用 → 409，
  不留下半條線；規劃中的線不占用；規劃中改成已接線時重新檢查
- 兩人同時把同一個埠接線：只有一筆成功，另一筆 409
- PSU 接插座：一個插座只能接一顆 PSU（併發也一樣）；既有的重複不動，只擋新的
"""
from __future__ import annotations

import asyncio

import pytest
from app.models.device import Device
from app.models.physical import (
    Cable,
    CableTermination,
    DevicePort,
    DevicePowerPort,
    PowerFeed,
    PowerOutlet,
    PowerPanel,
)
from sqlalchemy import func, select


async def _panel(db):  # type: ignore[no-untyped-def]
    d = Device(name="pp-a01", type="patch_panel")
    db.add(d)
    await db.flush()
    ports = {}
    for n, t in (("F1", "front"), ("F2", "front"), ("F3", "front"), ("R1", "rear"), ("R2", "rear"), ("R3", "rear")):
        p = DevicePort(device_id=d.id, name=n, type=t)
        db.add(p)
        ports[n] = p
    await db.flush()
    await db.commit()
    return d, ports


async def _peer(db, port_id):  # type: ignore[no-untyped-def]
    return (await db.execute(select(DevicePort.peer_port_id).where(DevicePort.id == port_id))).scalar_one()


def _code(r) -> str:  # type: ignore[no-untyped-def]
    d = r.json().get("detail")
    return d.get("code") if isinstance(d, dict) else str(d)


@pytest.mark.anyio
async def test_repair_clears_the_old_back_pointer(client, auth_headers, db_session) -> None:
    _d, p = await _panel(db_session)
    r = await client.patch(f"/api/v1/device-ports/{p['F1'].id}", headers=auth_headers, json={"peer_port_id": str(p["R1"].id)})
    assert r.status_code == 200, r.text
    assert await _peer(db_session, p["R1"].id) == p["F1"].id

    r = await client.patch(f"/api/v1/device-ports/{p['F1'].id}", headers=auth_headers, json={"peer_port_id": str(p["R3"].id)})
    assert r.status_code == 200, r.text
    assert await _peer(db_session, p["R3"].id) == p["F1"].id
    assert await _peer(db_session, p["R1"].id) is None, "R1 不可以還指著 F1"

    r = await client.patch(f"/api/v1/device-ports/{p['F1'].id}", headers=auth_headers, json={"peer_port_id": None})
    assert r.status_code == 200, r.text
    assert await _peer(db_session, p["F1"].id) is None
    assert await _peer(db_session, p["R3"].id) is None, "解除配對兩邊都要清"


@pytest.mark.anyio
async def test_target_already_paired_needs_explicit_replace(client, auth_headers, db_session) -> None:
    _d, p = await _panel(db_session)
    for a, b in (("F1", "R1"), ("F3", "R3")):
        assert (await client.patch(f"/api/v1/device-ports/{p[a].id}", headers=auth_headers,
                                   json={"peer_port_id": str(p[b].id)})).status_code == 200
    r = await client.patch(f"/api/v1/device-ports/{p['F1'].id}", headers=auth_headers, json={"peer_port_id": str(p["R3"].id)})
    assert r.status_code == 409
    assert _code(r) == "port_peer_taken"
    assert r.json()["detail"]["params"]["other"] == "F3"
    assert await _peer(db_session, p["R1"].id) == p["F1"].id, "被拒絕時什麼都不改"
    assert await _peer(db_session, p["F3"].id) == p["R3"].id

    r = await client.patch(f"/api/v1/device-ports/{p['F1'].id}", headers=auth_headers,
                           json={"peer_port_id": str(p["R3"].id), "replace_peer": True})
    assert r.status_code == 200, r.text
    assert await _peer(db_session, p["R3"].id) == p["F1"].id
    assert await _peer(db_session, p["F3"].id) is None
    assert await _peer(db_session, p["R1"].id) is None


@pytest.mark.anyio
async def test_peer_must_be_another_port_on_the_same_device(client, auth_headers, db_session) -> None:
    _d, p = await _panel(db_session)
    other = Device(name="sw-01")
    db_session.add(other)
    await db_session.flush()
    op = DevicePort(device_id=other.id, name="Gi1/0/1")
    db_session.add(op)
    await db_session.commit()
    r = await client.patch(f"/api/v1/device-ports/{p['F1'].id}", headers=auth_headers, json={"peer_port_id": str(p["F1"].id)})
    assert r.status_code == 422
    r = await client.patch(f"/api/v1/device-ports/{p['F1'].id}", headers=auth_headers, json={"peer_port_id": str(op.id)})
    assert r.status_code == 422
    assert _code(r) == "port_peer_other_device"


async def _two_devices(db):  # type: ignore[no-untyped-def]
    sw, srv = Device(name="sw-core01"), Device(name="pve01")
    db.add_all([sw, srv])
    await db.flush()
    a, b, c = DevicePort(device_id=sw.id, name="Gi1/0/1"), DevicePort(device_id=srv.id, name="eno1"), DevicePort(device_id=srv.id, name="eno2")
    db.add_all([a, b, c])
    await db.commit()
    return a, b, c


async def _cable_count(db) -> int:  # type: ignore[no-untyped-def]
    return int((await db.execute(select(func.count()).select_from(Cable))).scalar_one())


@pytest.mark.anyio
async def test_connect_is_one_transaction_and_checks_occupancy(client, auth_headers, db_session) -> None:
    a, b, c = await _two_devices(db_session)
    r = await client.post("/api/v1/cables/connect", headers=auth_headers, json={
        "a_port_id": str(a.id), "b_port_id": str(b.id), "label": "A01-01", "type": "cat6", "color": "blue"})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    sides = {t.side: t.object_id for t in (await db_session.execute(
        select(CableTermination).where(CableTermination.cable_id == cid))).scalars()}
    assert sides == {"A": a.id, "B": b.id}

    before = await _cable_count(db_session)
    r = await client.post("/api/v1/cables/connect", headers=auth_headers,
                          json={"a_port_id": str(c.id), "b_port_id": str(a.id)})
    assert r.status_code == 409
    assert _code(r) == "port_occupied"
    assert r.json()["detail"]["params"]["port"] == "Gi1/0/1"
    assert await _cable_count(db_session) == before, "被拒絕不可以留下半條線"

    r = await client.post("/api/v1/cables/connect", headers=auth_headers,
                          json={"a_port_id": str(c.id), "b_port_id": str(c.id)})
    assert r.status_code == 422

    # 規劃中的線不占用；改成已接線時重新檢查
    r = await client.post("/api/v1/cables/connect", headers=auth_headers,
                          json={"a_port_id": str(c.id), "b_port_id": str(a.id), "status": "planned"})
    assert r.status_code == 201, r.text
    planned = r.json()["id"]
    r = await client.patch(f"/api/v1/cables/{planned}", headers=auth_headers, json={"status": "connected"})
    assert r.status_code == 409
    assert _code(r) == "port_occupied"
    # 舊的那條除役後就可以
    assert (await client.patch(f"/api/v1/cables/{cid}", headers=auth_headers, json={"status": "decommissioned"})).status_code == 200
    assert (await client.patch(f"/api/v1/cables/{planned}", headers=auth_headers, json={"status": "connected"})).status_code == 200


@pytest.mark.anyio
async def test_single_termination_endpoint_also_checks_occupancy(client, auth_headers, db_session) -> None:
    a, b, c = await _two_devices(db_session)
    assert (await client.post("/api/v1/cables/connect", headers=auth_headers,
                              json={"a_port_id": str(a.id), "b_port_id": str(b.id)})).status_code == 201
    cable = (await client.post("/api/v1/cables", headers=auth_headers, json={"label": "x"})).json()
    r = await client.post("/api/v1/cable-terminations", headers=auth_headers, json={
        "cable_id": cable["id"], "side": "A", "object_type": "device_port", "object_id": str(b.id)})
    assert r.status_code == 409
    assert _code(r) == "port_occupied"
    r = await client.post("/api/v1/cable-terminations", headers=auth_headers, json={
        "cable_id": cable["id"], "side": "A", "object_type": "device_port", "object_id": str(c.id)})
    assert r.status_code == 201


@pytest.mark.anyio
async def test_concurrent_connects_to_one_port_only_one_wins(client, auth_headers, db_session) -> None:
    a, b, c = await _two_devices(db_session)
    rs = await asyncio.gather(
        client.post("/api/v1/cables/connect", headers=auth_headers, json={"a_port_id": str(a.id), "b_port_id": str(b.id)}),
        client.post("/api/v1/cables/connect", headers=auth_headers, json={"a_port_id": str(c.id), "b_port_id": str(a.id)}),
    )
    assert sorted(r.status_code for r in rs) == [201, 409]
    assert await _cable_count(db_session) == 1


async def _outlet(db):  # type: ignore[no-untyped-def]
    panel = PowerPanel(name="PP-1")
    db.add(panel)
    await db.flush()
    feed = PowerFeed(panel_id=panel.id, name="A")
    db.add(feed)
    await db.flush()
    o1, o2 = PowerOutlet(feed_id=feed.id, label="A-01"), PowerOutlet(feed_id=feed.id, label="A-02")
    d = Device(name="app-srv-01")
    db.add_all([o1, o2, d])
    await db.flush()
    p1, p2 = DevicePowerPort(device_id=d.id, name="PSU1"), DevicePowerPort(device_id=d.id, name="PSU2")
    db.add_all([p1, p2])
    await db.commit()
    return o1, o2, d, p1, p2


@pytest.mark.anyio
async def test_one_psu_per_outlet(client, auth_headers, db_session) -> None:
    o1, o2, d, p1, p2 = await _outlet(db_session)
    assert (await client.patch(f"/api/v1/device-power-ports/{p1.id}", headers=auth_headers,
                               json={"outlet_id": str(o1.id)})).status_code == 200
    r = await client.patch(f"/api/v1/device-power-ports/{p2.id}", headers=auth_headers, json={"outlet_id": str(o1.id)})
    assert r.status_code == 409
    assert _code(r) == "outlet_occupied"
    assert r.json()["detail"]["params"]["port"] == "PSU1"
    r = await client.post("/api/v1/device-power-ports", headers=auth_headers,
                          json={"device_id": str(d.id), "name": "PSU3", "outlet_id": str(o1.id)})
    assert r.status_code == 409
    # 同一顆 PSU 改其他欄位（插座不變）照常可以
    r = await client.patch(f"/api/v1/device-power-ports/{p1.id}", headers=auth_headers,
                           json={"outlet_id": str(o1.id), "description": "上層 PDU"})
    assert r.status_code == 200, r.text


@pytest.mark.anyio
async def test_existing_duplicate_outlet_is_kept_and_still_editable(client, auth_headers, db_session) -> None:
    """升級前就有兩顆 PSU 接同一個插座：不刪、不改，各自仍可以改說明；只擋新的占用。"""
    o1, _o2, _d, p1, p2 = await _outlet(db_session)
    p1.outlet_id = o1.id
    p2.outlet_id = o1.id
    await db_session.commit()
    r = await client.patch(f"/api/v1/device-power-ports/{p2.id}", headers=auth_headers,
                           json={"outlet_id": str(o1.id), "description": "舊資料"})
    assert r.status_code == 200, r.text
    rows = (await db_session.execute(select(DevicePowerPort.outlet_id).where(
        DevicePowerPort.id.in_([p1.id, p2.id])))).scalars().all()  # bounded: two ids
    assert rows == [o1.id, o1.id]


@pytest.mark.anyio
async def test_concurrent_psu_to_one_outlet_only_one_wins(client, auth_headers, db_session) -> None:
    o1, _o2, _d, p1, p2 = await _outlet(db_session)
    rs = await asyncio.gather(
        client.patch(f"/api/v1/device-power-ports/{p1.id}", headers=auth_headers, json={"outlet_id": str(o1.id)}),
        client.patch(f"/api/v1/device-power-ports/{p2.id}", headers=auth_headers, json={"outlet_id": str(o1.id)}),
    )
    assert sorted(r.status_code for r in rs) == [200, 409]
