"""規劃電力（交接包 physical-v1.2 SPEC.md，issue #54 重做，2026-11-21）。

以前把 PSU 額定容量（max_watts）當負載：2 × 800 W PSU、整機 500 W 被算成 800（每一路也各 800），迴路容量用
電壓 × 電流。現在：
- 整機規劃負載整台只填一次；PSU 額定容量不是負載
- 各顆電源分到「整機負載 × 分攤比例」；比例沒設好就列「分配未知」，不自動均分（只有一顆電源時就是 100%）
- 未知不當 0：顯示已知小計與完整度；資料不完整時不給「安全」狀態
- 迴路容量是人工確認的規劃容量，沒設就不算使用率
- 已知總量守恆：已知小計 = 已分配到迴路 + 未接線 + 分配未知
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from app.models.device import Device
from app.models.location import Rack
from app.models.physical import DevicePowerPort, DevicePowerProfile, PowerFeed, PowerOutlet, PowerPanel


async def _site(db, *, cap_a=None, cap_b=None):  # type: ignore[no-untyped-def]
    r1, r2 = Rack(name="TYO-R01"), Rack(name="TYO-R02")
    panel = PowerPanel(name="PP-1")
    db.add_all([r1, r2, panel])
    await db.flush()
    fa = PowerFeed(panel_id=panel.id, name="A", rack_id=r1.id, planning_capacity_w=cap_a)
    fb = PowerFeed(panel_id=panel.id, name="B", rack_id=r1.id, planning_capacity_w=cap_b)
    db.add_all([fa, fb])
    await db.flush()
    outs = {}
    for f, tag in ((fa, "A"), (fb, "B")):
        for i in range(1, 7):
            o = PowerOutlet(feed_id=f.id, rack_id=r1.id, label=f"{tag}-{i:02d}")
            db.add(o)
            outs[o.label] = o
    await db.flush()
    return r1, r2, fa, fb, outs


async def _dev(db, rack, name, *, load=None, psus=(), mode="shared", include=True, reason=None, dtype="server"):  # type: ignore[no-untyped-def]
    """psus：(名稱, 額定 W, 插座或 None, 分攤 % 或 None)"""
    d = Device(name=name, type=dtype, rack_id=rack.id if rack else None)
    db.add(d)
    await db.flush()
    for n, rated, outlet, share in psus:
        db.add(DevicePowerPort(device_id=d.id, name=n, max_watts=rated, outlet_id=outlet.id if outlet else None,
                               normal_share_pct=None if share is None else Decimal(str(share))))
    if load is not None or not include or mode != "shared":
        db.add(DevicePowerProfile(device_id=d.id, planning_input_w=None if load is None else Decimal(str(load)),
                                  supply_mode=mode, include_in_planning=include, exclude_reason=reason))
    await db.flush()
    return d


def _feed(summary, name):  # type: ignore[no-untyped-def]
    return next(f for f in summary["feeds"] if f["name"] == name)


def _conserved(s) -> None:  # type: ignore[no-untyped-def]
    assert Decimal(str(s["known_total_w"])) == (Decimal(str(s["assigned_w"])) + Decimal(str(s["unassigned_w"]))
                                                + Decimal(str(s["allocation_unknown_w"]))), s


@pytest.mark.anyio
async def test_p01_p02_rated_capacity_is_not_load(client, auth_headers, db_session) -> None:
    r1, _r2, _fa, _fb, o = await _site(db_session)
    await _dev(db_session, r1, "app-srv-01", load=500,
               psus=[("PSU1", 800, o["A-01"], 50), ("PSU2", 800, o["B-01"], 50)])
    await db_session.commit()
    s = (await client.get(f"/api/v1/racks/{r1.id}/power", headers=auth_headers)).json()
    assert s["known_total_w"] == 500, "兩顆 800 W 的 PSU 不是 1600 W，整機 500"
    assert _feed(s, "A")["rack_allocated_w"] == 250
    assert _feed(s, "B")["rack_allocated_w"] == 250
    _conserved(s)


@pytest.mark.anyio
async def test_p02_same_feed_gets_both_halves(client, auth_headers, db_session) -> None:
    r1, _r2, _fa, _fb, o = await _site(db_session)
    d = await _dev(db_session, r1, "nas-01", load=600, psus=[("PSU1", 800, o["A-01"], 50), ("PSU2", 800, o["A-02"], 50)])
    await db_session.commit()
    s = (await client.get(f"/api/v1/racks/{r1.id}/power", headers=auth_headers)).json()
    assert _feed(s, "A")["rack_allocated_w"] == 600
    row = next(x for x in s["devices"] if x["id"] == str(d.id))
    assert "single_feed" in row["issues"], "兩顆電源接在同一個迴路"


@pytest.mark.anyio
async def test_p03_null_is_unknown_zero_is_zero(client, auth_headers, db_session) -> None:
    r1, _r2, _fa, _fb, o = await _site(db_session)
    await _dev(db_session, r1, "unknown-01", psus=[("PSU1", 450, o["A-01"], 100)])
    await _dev(db_session, r1, "zero-01", load=0, psus=[("PSU1", None, o["A-02"], 100)])
    await db_session.commit()
    s = (await client.get(f"/api/v1/racks/{r1.id}/power", headers=auth_headers)).json()
    assert s["known_total_w"] == 0
    assert s["completeness"] == {"known": 1, "included": 2}
    assert s["unknown_devices"] == 1
    assert s["partial"] is True
    rows = {x["name"]: x for x in s["devices"]}
    assert rows["unknown-01"]["load_w"] is None
    assert "load_unknown" in rows["unknown-01"]["issues"]
    assert rows["zero-01"]["load_w"] == 0
    assert "load_unknown" not in rows["zero-01"]["issues"]


@pytest.mark.anyio
async def test_p04_unknown_share_is_not_split(client, auth_headers, db_session) -> None:
    r1, _r2, _fa, _fb, o = await _site(db_session, cap_a=Decimal(3000))
    await _dev(db_session, r1, "app-srv-02", load=500, psus=[("PSU1", 800, o["A-01"], None), ("PSU2", 800, o["B-01"], None)])
    await db_session.commit()
    s = (await client.get(f"/api/v1/racks/{r1.id}/power", headers=auth_headers)).json()
    assert s["allocation_unknown_w"] == 500
    assert _feed(s, "A")["rack_allocated_w"] == 0
    assert _feed(s, "A")["partial"] is True
    assert _feed(s, "A")["status"] != "ok", "資料不完整不可以給安全狀態"
    _conserved(s)


@pytest.mark.anyio
async def test_single_psu_is_100_percent(client, auth_headers, db_session) -> None:
    r1, _r2, _fa, _fb, o = await _site(db_session)
    await _dev(db_session, r1, "fw-01", load=120, psus=[("PSU1", 150, o["A-01"], None)])
    await db_session.commit()
    s = (await client.get(f"/api/v1/racks/{r1.id}/power", headers=auth_headers)).json()
    assert _feed(s, "A")["rack_allocated_w"] == 120
    assert s["allocation_unknown_w"] == 0


@pytest.mark.anyio
async def test_p05_unconnected_is_unassigned_and_conserved(client, auth_headers, db_session) -> None:
    r1, _r2, _fa, _fb, o = await _site(db_session)
    await _dev(db_session, r1, "core-sw-01", load=300, psus=[("PSU1", 350, o["A-01"], 50), ("PSU2", 350, None, 50)])
    await _dev(db_session, r1, "no-psu-01", load=200)
    await db_session.commit()
    s = (await client.get(f"/api/v1/racks/{r1.id}/power", headers=auth_headers)).json()
    assert s["known_total_w"] == 500
    assert s["assigned_w"] == 150
    assert s["unassigned_w"] == 350
    _conserved(s)
    rows = {x["name"]: x for x in s["devices"]}
    assert "unconnected" in rows["core-sw-01"]["issues"]
    assert "no_power_ports" in rows["no-psu-01"]["issues"]


@pytest.mark.anyio
async def test_p06_p07_cross_rack_and_shared_feed(client, auth_headers, db_session) -> None:
    r1, r2, _fa, _fb, o = await _site(db_session, cap_a=Decimal(3000))
    # r2 的裝置接到 r1 的 A 迴路
    await _dev(db_session, r1, "app-srv-01", load=500, psus=[("PSU1", 800, o["A-01"], 50), ("PSU2", 800, o["B-01"], 50)])
    d2 = await _dev(db_session, r2, "db-01", load=1000, psus=[("PSU1", 1100, o["A-02"], 100)])
    await db_session.commit()
    s1 = (await client.get(f"/api/v1/racks/{r1.id}/power", headers=auth_headers)).json()
    a = _feed(s1, "A")
    assert a["rack_allocated_w"] == 250, "本機櫃只算本櫃裝置的份額"
    assert a["full_allocated_w"] == 1250, "全迴路包含其他機櫃"
    assert a["utilization_pct"] == pytest.approx(41.67, abs=0.01), "使用率用全迴路除規劃容量"
    s2 = (await client.get(f"/api/v1/racks/{r2.id}/power", headers=auth_headers)).json()
    assert s2["known_total_w"] == 1000, "裝置負載算在裝置所在的機櫃"
    row = next(x for x in s2["devices"] if x["id"] == str(d2.id))
    assert row["allocations"][0]["cross_rack"] is True
    assert _feed(s2, "A")["rack_allocated_w"] == 1000


@pytest.mark.anyio
async def test_p11_capacity_and_status(client, auth_headers, db_session) -> None:
    r1, _r2, fa, fb, o = await _site(db_session, cap_a=Decimal(1000), cap_b=None)
    fa.warning_pct = 80
    await _dev(db_session, r1, "a", load=850, psus=[("PSU1", 900, o["A-01"], 100)])
    await _dev(db_session, r1, "b", load=100, psus=[("PSU1", 200, o["B-01"], 100)])
    await db_session.commit()
    s = (await client.get(f"/api/v1/racks/{r1.id}/power", headers=auth_headers)).json()
    assert _feed(s, "A")["status"] == "warn"
    assert _feed(s, "B")["status"] == "no_capacity"
    assert _feed(s, "B")["utilization_pct"] is None
    feeds = (await client.get("/api/v1/power-feeds", headers=auth_headers)).json()["items"]
    a = next(f for f in feeds if f["id"] == str(fa.id))
    assert a["allocated_w"] == 850
    assert a["planning_capacity_w"] == 1000
    assert a["status"] == "warn"
    assert "capacity_w" not in a, "不再用電壓 × 電流當容量"
    assert fb.id


@pytest.mark.anyio
async def test_p08_device_counted_once_and_exclusions(client, auth_headers, db_session) -> None:
    r1, _r2, _fa, _fb, o = await _site(db_session)
    await _dev(db_session, r1, "blade-chassis", load=2000, psus=[(f"PSU{i}", 1500, o[f"A-0{i}"], 25) for i in range(1, 5)])
    await _dev(db_session, r1, "pp-01", dtype="patch_panel")
    await _dev(db_session, r1, "old-srv", load=300, include=False, reason="已停機待報廢")
    await db_session.commit()
    s = (await client.get(f"/api/v1/racks/{r1.id}/power", headers=auth_headers)).json()
    assert s["known_total_w"] == 2000
    assert s["completeness"] == {"known": 1, "included": 1}
    rows = {x["name"]: x for x in s["devices"]}
    assert rows["pp-01"]["included"] is False, "跳接面板是被動元件，預設不納入"
    assert rows["old-srv"]["included"] is False
    assert rows["old-srv"]["exclude_reason"] == "已停機待報廢"


@pytest.mark.anyio
async def test_decimal_remainder_is_reproducible(client, auth_headers, db_session) -> None:
    r1, _r2, _fa, _fb, o = await _site(db_session)
    await _dev(db_session, r1, "x", load="100.00",
               psus=[("PSU1", None, o["A-01"], "33.33"), ("PSU2", None, o["A-02"], "33.33"), ("PSU3", None, o["B-01"], "33.34")])
    await db_session.commit()
    s = (await client.get(f"/api/v1/racks/{r1.id}/power", headers=auth_headers)).json()
    assert _feed(s, "A")["rack_allocated_w"] + _feed(s, "B")["rack_allocated_w"] == pytest.approx(100.0)
    _conserved(s)


@pytest.mark.anyio
async def test_empty_rack_has_no_division(client, auth_headers, db_session) -> None:
    r1, _r2, _fa, _fb, _o = await _site(db_session)
    await db_session.commit()
    s = (await client.get(f"/api/v1/racks/{r1.id}/power", headers=auth_headers)).json()
    assert s["completeness"] is None
    assert s["known_total_w"] == 0


# ── 裝置電源設定 API ──

@pytest.mark.anyio
async def test_profile_put_is_atomic_with_revision(client, auth_headers, db_session) -> None:
    r1, _r2, _fa, _fb, o = await _site(db_session)
    d = await _dev(db_session, r1, "app-srv-01", psus=[("PSU1", 800, o["A-01"], None), ("PSU2", 800, o["B-01"], None)])
    await db_session.commit()
    url = f"/api/v1/devices/{d.id}/power-profile"
    g = (await client.get(url, headers=auth_headers)).json()
    assert g["revision"] == 0
    assert g["planning_input_w"] is None
    assert [p["legacy_rating"] for p in g["ports"]] == [True, True], "0247 以前填的瓦數標舊值"
    p1, p2 = (p["id"] for p in g["ports"])
    body = {"revision": 0, "planning_input_w": 500, "value_basis": "manual_estimate", "supply_mode": "shared",
            "failover_capability": "confirmed_full_load", "notes": "平均分攤為規劃假設",
            "ports": [{"id": p1, "normal_share_pct": 50}, {"id": p2, "normal_share_pct": 50}]}
    r = await client.put(url, headers=auth_headers, json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["revision"] == 1
    assert out["planning_input_w"] == 500
    assert out["confirmed_at"]
    assert [x["allocated_w"] for x in out["ports"]] == [250, 250]
    assert out["wiring"]["distinct_feeds"] == 2
    # 別人已經改過（revision 不對）→ 409，不蓋掉
    r = await client.put(url, headers=auth_headers, json={**body, "planning_input_w": 900})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "power_profile_conflict"


@pytest.mark.anyio
async def test_profile_validation(client, auth_headers, db_session) -> None:
    r1, _r2, _fa, _fb, o = await _site(db_session)
    d = await _dev(db_session, r1, "a", psus=[("PSU1", 800, o["A-01"], None), ("PSU2", 800, o["B-01"], None)])
    other = await _dev(db_session, r1, "b", psus=[("PSU1", 800, o["A-02"], None)])
    await db_session.commit()
    url = f"/api/v1/devices/{d.id}/power-profile"
    ports = (await client.get(url, headers=auth_headers)).json()["ports"]
    oport = (await client.get(f"/api/v1/devices/{other.id}/power-profile", headers=auth_headers)).json()["ports"][0]["id"]
    base = {"revision": 0, "planning_input_w": 500}
    r = await client.put(url, headers=auth_headers, json={**base, "ports": [{"id": oport, "normal_share_pct": 100}]})
    assert r.status_code == 422, "別台裝置的電源埠"
    r = await client.put(url, headers=auth_headers, json={**base, "ports": [
        {"id": ports[0]["id"], "normal_share_pct": 70}, {"id": ports[1]["id"], "normal_share_pct": 40}]})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "power_share_over"
    r = await client.put(url, headers=auth_headers, json={**base, "include_in_planning": False})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "power_exclude_reason_required"
    r = await client.put(url, headers=auth_headers, json={**base, "planning_input_w": -1})
    assert r.status_code == 422
    r = await client.put(url, headers=auth_headers, json={**base, "primary_port_id": oport})
    assert r.status_code == 422
    # 未知可以存：不強迫填負載
    r = await client.put(url, headers=auth_headers, json={"revision": 0, "planning_input_w": None, "supply_mode": "unknown"})
    assert r.status_code == 200, r.text


@pytest.mark.anyio
async def test_confirm_legacy_rating(client, auth_headers, db_session) -> None:
    r1, _r2, _fa, _fb, o = await _site(db_session)
    d = await _dev(db_session, r1, "a", psus=[("PSU1", 800, o["A-01"], None)])
    await db_session.commit()
    pid = (await client.get(f"/api/v1/devices/{d.id}/power-profile", headers=auth_headers)).json()["ports"][0]["id"]
    r = await client.patch(f"/api/v1/device-power-ports/{pid}", headers=auth_headers, json={"rating_confirmed": True})
    assert r.status_code == 200, r.text
    g = (await client.get(f"/api/v1/devices/{d.id}/power-profile", headers=auth_headers)).json()
    assert g["ports"][0]["legacy_rating"] is False
    assert g["ports"][0]["max_watts"] == 800, "確認不改值"


@pytest.mark.anyio
async def test_delete_in_use_is_refused(client, auth_headers, db_session) -> None:
    r1, _r2, fa, _fb, o = await _site(db_session)
    await _dev(db_session, r1, "a", psus=[("PSU1", 800, o["A-01"], None)])
    await db_session.commit()
    r = await client.delete(f"/api/v1/power-outlets/{o['A-01'].id}", headers=auth_headers)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "outlet_in_use"
    r = await client.delete(f"/api/v1/power-feeds/{fa.id}", headers=auth_headers)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "feed_in_use"
    r = await client.delete(f"/api/v1/power-panels/{fa.panel_id}", headers=auth_headers)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "panel_in_use"
    # 空的插座可以刪
    assert (await client.delete(f"/api/v1/power-outlets/{o['A-06'].id}", headers=auth_headers)).status_code == 204


@pytest.mark.anyio
async def test_outlet_list_has_path_and_occupancy(client, auth_headers, db_session) -> None:
    from app.models.location import Location
    r1, _r2, _fa, _fb, o = await _site(db_session)
    loc = Location(name="東京機房")
    db_session.add(loc)
    await db_session.flush()
    r1.location_id = loc.id
    await _dev(db_session, r1, "app-srv-01", psus=[("PSU1", 800, o["A-01"], None)])
    await db_session.commit()
    items = (await client.get("/api/v1/power-outlets?page_size=500", headers=auth_headers)).json()["items"]
    a1 = next(x for x in items if x["label"] == "A-01")
    assert a1["path"] == "東京機房 / TYO-R01 / PP-1 / A / A-01"
    assert a1["occupied_by"] == ["app-srv-01 PSU1"]
    assert next(x for x in items if x["label"] == "A-02")["occupied_by"] == []
