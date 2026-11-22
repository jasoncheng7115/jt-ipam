"""雙路失效模擬（交接包 physical-v1.2 SPEC.md 第 6 節、P3；驗收 P12–P14，2026-11-21）。

輸入失效的迴路，以整條迴路為單位重算所有受影響的裝置（不只目前開著的機櫃）：
- 結果只有四種：可轉移 / 失去供電 / 剩餘容量不足 / 無法判斷
- 只有 failover_capability=confirmed_full_load 且另一顆電源接在存活的迴路上，才把整機負載移過去
- 兩顆電源接同一路：一起斷，不當成備援（P13）
- 三顆以上、自訂模式、負載未知、另一顆沒接線：無法判斷，不套雙路公式（P14）
- 存活迴路的新負載從頭算；無法判斷的裝置不算進去，只標「至少」
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from app.models.device import Device
from app.models.location import Rack
from app.models.physical import (
    DevicePowerPort,
    DevicePowerProfile,
    PowerFeed,
    PowerOutlet,
    PowerPanel,
)


async def _site(db, *, cap_b=None):  # type: ignore[no-untyped-def]
    r1, r2 = Rack(name="TYO-R01"), Rack(name="TYO-R02")
    panel = PowerPanel(name="PP-1")
    db.add_all([r1, r2, panel])
    await db.flush()
    fa = PowerFeed(panel_id=panel.id, name="A", rack_id=r1.id, planning_capacity_w=Decimal(3000))
    fb = PowerFeed(panel_id=panel.id, name="B", rack_id=r1.id,
                   planning_capacity_w=None if cap_b is None else Decimal(cap_b))
    db.add_all([fa, fb])
    await db.flush()
    outs = {}
    for f, tag in ((fa, "A"), (fb, "B")):
        for i in range(1, 9):
            o = PowerOutlet(feed_id=f.id, rack_id=r1.id, label=f"{tag}-{i:02d}")
            db.add(o)
            outs[o.label] = o
    await db.flush()
    return r1, r2, fa, fb, outs


async def _dev(db, rack, name, *, load=None, psus=(), mode="shared", cap="confirmed_full_load"):  # type: ignore[no-untyped-def]
    d = Device(name=name, type="server", rack_id=rack.id if rack else None)
    db.add(d)
    await db.flush()
    for n, outlet, share in psus:
        db.add(DevicePowerPort(device_id=d.id, name=n, outlet_id=outlet.id if outlet else None,
                               normal_share_pct=None if share is None else Decimal(str(share))))
    db.add(DevicePowerProfile(device_id=d.id, planning_input_w=None if load is None else Decimal(str(load)),
                              supply_mode=mode, failover_capability=cap))
    await db.flush()
    return d


async def _preview(client, headers, *feeds):  # type: ignore[no-untyped-def]
    q = "&".join(f"feed_id={f.id}" for f in feeds)
    r = await client.get(f"/api/v1/power-scenarios/preview?{q}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _by_name(res):  # type: ignore[no-untyped-def]
    return {d["name"]: d for d in res["devices"]}


@pytest.mark.anyio
async def test_p12_single_supply_loses_dual_confirmed_transfers(client, auth_headers, db_session) -> None:
    r1, r2, fa, _fb, o = await _site(db_session, cap_b=2000)
    await _dev(db_session, r1, "single-01", load=200, psus=[("PSU1", o["A-01"], None)], mode="single", cap="unknown")
    await _dev(db_session, r2, "dual-01", load=500, psus=[("PSU1", o["A-02"], 50), ("PSU2", o["B-02"], 50)])
    await _dev(db_session, r1, "b-only", load=300, psus=[("PSU1", o["B-01"], None)], mode="single", cap="unknown")
    await db_session.commit()
    res = await _preview(client, auth_headers, fa)
    d = _by_name(res)
    assert set(d) == {"single-01", "dual-01"}, "只列受影響的裝置；不受影響的不列"
    assert d["single-01"]["outcome"] == "loses_power"
    assert d["dual-01"]["outcome"] == "transferable"
    assert [f["name"] for f in d["dual-01"]["to_feeds"]] == ["B"]
    b = next(f for f in res["feeds"] if f["name"] == "B")
    assert b["before_w"] == 550, "B 原本 300＋250"
    assert b["after_w"] == 800, "失效後 dual-01 整台 500 移過來"
    assert b["status"] == "ok"
    assert res["lost_load_w"] == 200
    assert res["counts"] == {"transferable": 1, "loses_power": 1, "insufficient_capacity": 0, "undetermined": 0}
    assert [f["name"] for f in res["failed_feeds"]] == ["A"]


@pytest.mark.anyio
async def test_p13_both_supplies_on_failed_feed(client, auth_headers, db_session) -> None:
    r1, _r2, fa, _fb, o = await _site(db_session, cap_b=2000)
    await _dev(db_session, r1, "nas-01", load=600, psus=[("PSU1", o["A-01"], 50), ("PSU2", o["A-02"], 50)])
    await db_session.commit()
    d = _by_name(await _preview(client, auth_headers, fa))["nas-01"]
    assert d["outcome"] == "loses_power"
    assert d["reason"] == "all_supplies_failed"


@pytest.mark.anyio
async def test_p14_unsupported_and_unknown_are_undetermined(client, auth_headers, db_session) -> None:
    r1, _r2, fa, _fb, o = await _site(db_session, cap_b=5000)
    await _dev(db_session, r1, "n1-01", load=900,
               psus=[("PSU1", o["A-01"], 34), ("PSU2", o["B-01"], 33), ("PSU3", o["B-02"], 33)])
    await _dev(db_session, r1, "unconfirmed", load=400, psus=[("PSU1", o["A-02"], 50), ("PSU2", o["B-03"], 50)],
               cap="unknown")
    await _dev(db_session, r1, "load-unknown", psus=[("PSU1", o["A-03"], 50), ("PSU2", o["B-04"], 50)])
    await _dev(db_session, r1, "half-wired", load=300, psus=[("PSU1", o["A-04"], 50), ("PSU2", None, 50)])
    await _dev(db_session, r1, "custom-01", load=100, psus=[("PSU1", o["A-05"], 50), ("PSU2", o["B-05"], 50)],
               mode="custom")
    await _dev(db_session, r1, "weak-psu", load=700, psus=[("PSU1", o["A-06"], 50), ("PSU2", o["B-06"], 50)],
               cap="insufficient")
    await db_session.commit()
    res = await _preview(client, auth_headers, fa)
    d = _by_name(res)
    assert (d["n1-01"]["outcome"], d["n1-01"]["reason"]) == ("undetermined", "unsupported_multi_supply")
    assert (d["unconfirmed"]["outcome"], d["unconfirmed"]["reason"]) == ("undetermined", "failover_unconfirmed")
    assert (d["load-unknown"]["outcome"], d["load-unknown"]["reason"]) == ("undetermined", "load_unknown")
    assert (d["half-wired"]["outcome"], d["half-wired"]["reason"]) == ("undetermined", "path_incomplete")
    assert (d["custom-01"]["outcome"], d["custom-01"]["reason"]) == ("undetermined", "unsupported_mode")
    assert (d["weak-psu"]["outcome"], d["weak-psu"]["reason"]) == ("loses_power", "failover_insufficient")
    b = next(f for f in res["feeds"] if f["name"] == "B")
    # 無法判斷的裝置不算進失效後的負載（不知道會不會轉過來），只標「至少」；不給 ok
    assert b["after_w"] == 0
    assert b["partial"] is True
    assert b["status"] != "ok"
    assert b["unknown_devices"] == 4, "n1-01、unconfirmed、load-unknown、custom-01；weak-psu 斷電不算"


@pytest.mark.anyio
async def test_survivor_over_capacity_is_insufficient(client, auth_headers, db_session) -> None:
    r1, _r2, fa, _fb, o = await _site(db_session, cap_b=800)
    await _dev(db_session, r1, "db-01", load=500, psus=[("PSU1", o["A-01"], 50), ("PSU2", o["B-01"], 50)])
    await _dev(db_session, r1, "db-02", load=500, psus=[("PSU1", o["A-02"], 50), ("PSU2", o["B-02"], 50)])
    await db_session.commit()
    res = await _preview(client, auth_headers, fa)
    d = _by_name(res)
    assert d["db-01"]["outcome"] == d["db-02"]["outcome"] == "insufficient_capacity"
    b = next(f for f in res["feeds"] if f["name"] == "B")
    assert (b["before_w"], b["after_w"], b["status"]) == (500, 1000, "over")


@pytest.mark.anyio
async def test_survivor_without_capacity_is_not_ok(client, auth_headers, db_session) -> None:
    r1, _r2, fa, _fb, o = await _site(db_session)
    await _dev(db_session, r1, "app-01", load=500, psus=[("PSU1", o["A-01"], 50), ("PSU2", o["B-01"], 50)])
    await db_session.commit()
    res = await _preview(client, auth_headers, fa)
    assert _by_name(res)["app-01"]["outcome"] == "transferable"
    b = next(f for f in res["feeds"] if f["name"] == "B")
    assert b["status"] == "no_capacity"
    assert b["utilization_pct"] is None


@pytest.mark.anyio
async def test_two_feeds_fail_together(client, auth_headers, db_session) -> None:
    r1, _r2, fa, fb, o = await _site(db_session, cap_b=2000)
    await _dev(db_session, r1, "app-01", load=500, psus=[("PSU1", o["A-01"], 50), ("PSU2", o["B-01"], 50)])
    await db_session.commit()
    res = await _preview(client, auth_headers, fa, fb)
    assert _by_name(res)["app-01"]["outcome"] == "loses_power"
    assert res["feeds"] == []


@pytest.mark.anyio
async def test_legacy_outlet_link_is_undetermined(client, auth_headers, db_session) -> None:
    r1, _r2, fa, _fb, o = await _site(db_session, cap_b=2000)
    d = Device(name="old-ups-link", type="server", rack_id=r1.id)
    db_session.add(d)
    await db_session.flush()
    o["A-07"].device_id = d.id
    await db_session.commit()
    got = _by_name(await _preview(client, auth_headers, fa))["old-ups-link"]
    assert (got["outcome"], got["reason"]) == ("undetermined", "legacy_outlet_link")


@pytest.mark.anyio
async def test_bad_input(client, auth_headers, db_session) -> None:
    import uuid
    r = await client.get(f"/api/v1/power-scenarios/preview?feed_id={uuid.uuid4()}", headers=auth_headers)
    assert r.status_code == 404
    r = await client.get("/api/v1/power-scenarios/preview", headers=auth_headers)
    assert r.status_code == 422


@pytest.mark.anyio
async def test_failed_supply_that_carried_nothing_keeps_running(client, auth_headers, db_session) -> None:
    """失效那顆的分攤是 0%（本來就全由另一路供電）：不必確認能不能撐，負載照原樣留在存活迴路。"""
    r1, _r2, fa, _fb, o = await _site(db_session, cap_b=2000)
    await _dev(db_session, r1, "standby-a", load=400, psus=[("PSU1", o["A-01"], 0), ("PSU2", o["B-01"], 100)],
               mode="active_standby", cap="unknown")
    await db_session.commit()
    res = await _preview(client, auth_headers, fa)
    d = _by_name(res)["standby-a"]
    assert (d["outcome"], d["reason"]) == ("transferable", "failed_supply_idle")
    b = next(f for f in res["feeds"] if f["name"] == "B")
    assert b["before_w"] == b["after_w"] == 400
    assert b["partial"] is False
