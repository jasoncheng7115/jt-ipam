"""端到端追線（交接包 physical-v1.2 CABLING.md 的驗收案例，2026-11-21）。

- 段別：cable（實體線，只有它算纜線數）、panel_passthrough（跳接面板正背面）、logical_bridge（bridge 對 NIC）
- 只走「已接線」的線；同一個埠有兩條已接線的線 → 停下來說有衝突，不任選一條
- 停止原因：end（走到底）、unpaired_panel_port（面板埠沒有背面對應）、no_connected_cable（只有規劃中或已除役的線）、
  ambiguous（同一埠多條已接線）、loop、limit、external_end、missing_port、no_permission
- 從纜線清單出發可以指定第一段走哪條線（first_cable_id），不會默默改走另一條
- 看不到的裝置：不回名稱、id
- 回應保留舊欄位（start、nodes、hops）
"""
from __future__ import annotations

import pytest
from app.models.device import Device
from app.models.physical import Cable, CableTermination, DevicePort


async def _cable(db, a, b, label, status="connected"):  # type: ignore[no-untyped-def]
    c = Cable(label=label, status=status, type="cat6a")
    db.add(c)
    await db.flush()
    db.add(CableTermination(cable_id=c.id, side="A", object_type="device_port", object_id=a.id))
    if b is not None:
        db.add(CableTermination(cable_id=c.id, side="B", object_type="device_port", object_id=b.id))
    await db.flush()
    return c


async def _fixture(db):  # type: ignore[no-untyped-def]
    sw, pp, pve = Device(name="sw-core01"), Device(name="pp-a01", type="patch_panel"), Device(name="pve01")
    db.add_all([sw, pp, pve])
    await db.flush()
    p = {}
    for dev, name, typ in ((sw, "Gi1/0/1", "network"), (pp, "F1", "front"), (pp, "F2", "front"),
                           (pp, "R1", "rear"), (pp, "R2", "rear"), (pve, "eno1", "network")):
        p[name] = DevicePort(device_id=dev.id, name=name, type=typ)
        db.add(p[name])
    await db.flush()
    p["F1"].peer_port_id, p["R1"].peer_port_id = p["R1"].id, p["F1"].id
    p["F2"].peer_port_id, p["R2"].peer_port_id = p["R2"].id, p["F2"].id
    c1 = await _cable(db, p["Gi1/0/1"], p["F1"], "A01-01")
    c2 = await _cable(db, p["R1"], p["eno1"], "A01-02")
    await db.commit()
    return p, c1, c2


def _path(r) -> list[str]:  # type: ignore[no-untyped-def]
    return [f"{n.get('device_name')}:{n.get('port_name')}" for n in r["nodes"]]


@pytest.mark.anyio
async def test_normal_path_both_directions(client, auth_headers, db_session) -> None:
    p, c1, _c2 = await _fixture(db_session)
    r = (await client.get(f"/api/v1/ports/{p['Gi1/0/1'].id}/trace", headers=auth_headers)).json()
    assert _path(r) == ["sw-core01:Gi1/0/1", "pp-a01:F1", "pp-a01:R1", "pve01:eno1"]
    assert [s["kind"] for s in r["segments"]] == ["cable", "panel_passthrough", "cable"]
    assert r["stop_reason"] == "end"
    assert r["cable_count"] == 2
    assert len(r["hops"]) == 3, "舊欄位保留"
    back = (await client.get(f"/api/v1/ports/{p['eno1'].id}/trace", headers=auth_headers)).json()
    assert _path(back) == ["pve01:eno1", "pp-a01:R1", "pp-a01:F1", "sw-core01:Gi1/0/1"]
    assert "F2" not in str(back) and "R2" not in str(back)
    # 從纜線清單出發：指定第一段
    r = (await client.get(f"/api/v1/ports/{p['F1'].id}/trace?first_cable_id={c1.id}", headers=auth_headers)).json()
    assert _path(r) == ["pp-a01:F1", "sw-core01:Gi1/0/1"]


@pytest.mark.anyio
async def test_missing_pair_stops_at_panel(client, auth_headers, db_session) -> None:
    p, _c1, _c2 = await _fixture(db_session)
    p["F1"].peer_port_id = None
    p["R1"].peer_port_id = None
    db_session.add_all([p["F1"], p["R1"]])
    await db_session.commit()
    r = (await client.get(f"/api/v1/ports/{p['Gi1/0/1'].id}/trace", headers=auth_headers)).json()
    assert _path(r) == ["sw-core01:Gi1/0/1", "pp-a01:F1"]
    assert r["stop_reason"] == "unpaired_panel_port"
    assert r["stop_port"]["port_name"] == "F1"


@pytest.mark.anyio
async def test_planned_cable_is_not_followed(client, auth_headers, db_session) -> None:
    p, _c1, c2 = await _fixture(db_session)
    c2.status = "planned"
    await db_session.commit()
    r = (await client.get(f"/api/v1/ports/{p['Gi1/0/1'].id}/trace", headers=auth_headers)).json()
    assert _path(r) == ["sw-core01:Gi1/0/1", "pp-a01:F1", "pp-a01:R1"]
    assert r["stop_reason"] == "no_connected_cable"


@pytest.mark.anyio
async def test_two_connected_cables_on_one_port_is_ambiguous(client, auth_headers, db_session) -> None:
    p, _c1, _c2 = await _fixture(db_session)
    extra = DevicePort(device_id=p["eno1"].device_id, name="eno2")
    db_session.add(extra)
    await db_session.flush()
    await _cable(db_session, p["R1"], extra, "dup")    # 升級前的舊資料：R1 上兩條已接線
    await db_session.commit()
    r = (await client.get(f"/api/v1/ports/{p['Gi1/0/1'].id}/trace", headers=auth_headers)).json()
    assert _path(r) == ["sw-core01:Gi1/0/1", "pp-a01:F1", "pp-a01:R1"]
    assert r["stop_reason"] == "ambiguous"
    assert sorted(r["candidates"]) == ["A01-02", "dup"]


@pytest.mark.anyio
async def test_loop_and_limit(client, auth_headers, db_session) -> None:
    p, c1, c2 = await _fixture(db_session)
    # 環：Gi1/0/1 →A01-01→ F1 ↔ R1 →loop-1→ F2 ↔ R2 →loop-2→ Gi1/0/1（回到起點）
    c2.status = "decommissioned"
    await _cable(db_session, p["R1"], p["F2"], "loop-1")
    await _cable(db_session, p["R2"], p["Gi1/0/1"], "loop-2")
    await db_session.commit()
    r = (await client.get(f"/api/v1/ports/{p['Gi1/0/1'].id}/trace?first_cable_id={c1.id}", headers=auth_headers)).json()
    assert r["stop_reason"] == "loop"
    assert _path(r) == ["sw-core01:Gi1/0/1", "pp-a01:F1", "pp-a01:R1", "pp-a01:F2", "pp-a01:R2"]
    # 不指定第一段：Gi1/0/1 上有兩條已接線的線 → 不任選
    amb = (await client.get(f"/api/v1/ports/{p['Gi1/0/1'].id}/trace", headers=auth_headers)).json()
    assert amb["stop_reason"] == "ambiguous"
    lim = (await client.get(f"/api/v1/ports/{p['Gi1/0/1'].id}/trace?first_cable_id={c1.id}&max_segments=1",
                            headers=auth_headers)).json()
    assert lim["stop_reason"] == "limit"
    assert lim["truncated"] is True
    assert len(lim["segments"]) == 1


@pytest.mark.anyio
async def test_first_cable_must_be_on_the_port(client, auth_headers, db_session) -> None:
    p, _c1, c2 = await _fixture(db_session)
    r = await client.get(f"/api/v1/ports/{p['Gi1/0/1'].id}/trace?first_cable_id={c2.id}", headers=auth_headers)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "trace_cable_not_on_port"


@pytest.mark.anyio
async def test_external_end_and_bridge(client, auth_headers, db_session) -> None:
    host = Device(name="pve02")
    db_session.add(host)
    await db_session.flush()
    nic, br = DevicePort(device_id=host.id, name="eno1"), DevicePort(device_id=host.id, name="vmbr10", type="bridge")
    db_session.add_all([nic, br])
    await db_session.flush()
    br.peer_port_id = nic.id
    c = Cable(label="uplink", status="connected")
    db_session.add(c)
    await db_session.flush()
    db_session.add_all([CableTermination(cable_id=c.id, side="A", object_type="device_port", object_id=br.id),
                        CableTermination(cable_id=c.id, side="B", object_type="circuit", object_id=host.id, port_label="ISP-1")])
    await db_session.commit()
    r = (await client.get(f"/api/v1/ports/{br.id}/trace", headers=auth_headers)).json()
    assert [s["kind"] for s in r["segments"]] == ["logical_bridge", "cable"]
    assert r["stop_reason"] == "external_end"
    assert r["cable_count"] == 1, "橋接不算纜線"


@pytest.mark.anyio
async def test_hidden_far_end_is_redacted(client, db_session) -> None:
    from app.core.security import hash_password
    from app.models.permission import Permission
    from app.models.user import User
    from app.services.auth import issue_access_token
    p, _c1, _c2 = await _fixture(db_session)
    u = User(username="trace-ro", email="trace-ro@t.local", display_name="U",
             password_hash=hash_password("TestPassword2026!"), auth_provider="local", is_active=True)
    db_session.add(u)
    await db_session.flush()
    # 萬用子網路讀取＝可以進實體模組（全域讀取），但裝置只看得到交換器
    db_session.add_all([Permission(object_type="subnet", object_id=None, principal_type="user", principal_id=u.id, level="read"),
                        Permission(object_type="device", object_id=p["Gi1/0/1"].device_id, principal_type="user",
                                   principal_id=u.id, level="read")])
    await db_session.commit()
    r = (await client.get(f"/api/v1/ports/{p['Gi1/0/1'].id}/trace", headers={"Authorization": f"Bearer {issue_access_token(u)}"})).json()
    assert r["stop_reason"] == "no_permission"
    assert "pp-a01" not in str(r) and str(p["F1"].id) not in str(r)


@pytest.mark.anyio
async def test_cable_list_carries_port_rack_and_effective_location(client, auth_headers, db_session) -> None:
    """纜線清單每端帶埠 id（追線從這一端出發）、機櫃，機房用有效位置（機櫃的地點優先）。"""
    from app.models.location import Location, Rack
    p, c1, _c2 = await _fixture(db_session)
    l1, l2 = Location(name="TYO"), Location(name="OSA")
    db_session.add_all([l1, l2])
    await db_session.flush()
    r1 = Rack(name="A01", location_id=l1.id)
    db_session.add(r1)
    await db_session.flush()
    sw = await db_session.get(Device, p["Gi1/0/1"].device_id)
    sw.rack_id, sw.location_id = r1.id, l2.id          # 記錄說大阪，但機櫃在東京
    await db_session.commit()
    items = (await client.get("/api/v1/cables?page_size=500", headers=auth_headers)).json()["items"]
    row = next(x for x in items if x["id"] == str(c1.id))
    assert row["a_port_id"] == str(p["Gi1/0/1"].id)
    assert row["b_port_id"] == str(p["F1"].id)
    assert row["a_rack_id"] == str(r1.id)
    assert row["a_location_id"] == str(l1.id), "機房用機櫃的地點"


@pytest.mark.anyio
async def test_port_list_carries_cable_label_and_prefers_connected(client, auth_headers, db_session) -> None:
    """面板格狀檢視要顯示正面、背面各自的線標；同一個埠有舊的已除役線時，顯示已接線的那條。"""
    p, _c1, _c2 = await _fixture(db_session)
    old_port = DevicePort(device_id=p["eno1"].device_id, name="old")
    db_session.add(old_port)
    await db_session.flush()
    await _cable(db_session, p["F1"], old_port, "OLD-01", status="decommissioned")
    await db_session.commit()
    ports = {x["name"]: x for x in (await client.get(f"/api/v1/device-ports?device_id={p['F1'].device_id}",
                                                    headers=auth_headers)).json()}
    assert ports["F1"]["cable_label"] == "A01-01"
    assert ports["F1"]["cable_status"] == "connected"
    assert ports["F1"]["link"] == "sw-core01 · Gi1/0/1"
    assert ports["R1"]["cable_label"] == "A01-02"
    assert ports["F2"]["cable_label"] is None
