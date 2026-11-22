"""AI 工具接上機房摘要與失效模擬（交接包 physical-v1.2 SPEC.md 第 14 節，2026-11-21）。

AI 呼叫確定性計算的結果，不自己從截斷清單加總；工具清單有 5 萬字上限，所以擴充既有工具、不新增：
- list_locations 帶 location_id → summary（有效位置：機櫃的地點優先；未入櫃、位置衝突、規劃電力）
- list_locations 的 device_count 跟機房詳情同一套有效位置
- list_power 帶 fail_feeds → failure_preview（跟畫面的失效模擬同一份計算）；迴路可給 id、名稱或「配電盤 / 名稱」
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from app.mcp.tools import TOOLS, IPAMToolError
from app.models.device import Device
from app.models.location import Location, Rack
from app.models.physical import (
    DevicePowerPort,
    DevicePowerProfile,
    PowerFeed,
    PowerOutlet,
    PowerPanel,
)

pytestmark = pytest.mark.anyio


async def _call(db, user, tool, **args):  # type: ignore[no-untyped-def]
    return await TOOLS[tool]["fn"](db, user=user, **args)


async def test_location_summary_uses_effective_placement(db_session, admin_user) -> None:
    tyo, osa = Location(name="東京機房"), Location(name="大阪機房")
    db_session.add_all([tyo, osa])
    await db_session.flush()
    rack = Rack(name="TYO-R01", location_id=tyo.id)
    db_session.add(rack)
    await db_session.flush()
    # 裝置寫大阪、但機櫃在東京 → 算東京（衝突）；另一台未入櫃在東京
    db_session.add_all([Device(name="srv-01", location_id=osa.id, rack_id=rack.id),
                        Device(name="loose-01", location_id=tyo.id)])
    await db_session.flush()
    out = await _call(db_session, admin_user, "list_locations")
    counts = {x["name"]: x["device_count"] for x in out["locations"]}
    assert counts == {"東京機房": 2, "大阪機房": 0}
    s = (await _call(db_session, admin_user, "list_locations", location_id=str(tyo.id)))["summary"]
    assert s["devices"] == {"total": 2, "racked": 1, "unracked": 1}
    assert s["pending"]["location_conflicts"] == 1
    assert s["name"] == "東京機房"


async def _feeds(db):  # type: ignore[no-untyped-def]
    panel = PowerPanel(name="PP-1")
    db.add(panel)
    await db.flush()
    fa = PowerFeed(panel_id=panel.id, name="A", planning_capacity_w=Decimal(3000))
    fb = PowerFeed(panel_id=panel.id, name="B", planning_capacity_w=Decimal(600))
    db.add_all([fa, fb])
    await db.flush()
    oa, ob = PowerOutlet(feed_id=fa.id, label="A-01"), PowerOutlet(feed_id=fb.id, label="B-01")
    db.add_all([oa, ob])
    dev = Device(name="db-01", type="server")
    db.add(dev)
    await db.flush()
    db.add_all([DevicePowerPort(device_id=dev.id, name="PSU1", outlet_id=oa.id, normal_share_pct=Decimal(50)),
                DevicePowerPort(device_id=dev.id, name="PSU2", outlet_id=ob.id, normal_share_pct=Decimal(50)),
                DevicePowerProfile(device_id=dev.id, planning_input_w=Decimal(500),
                                   failover_capability="confirmed_full_load")])
    await db.flush()
    return fa, fb


async def test_list_power_fail_feeds(db_session, admin_user) -> None:
    fa, _fb = await _feeds(db_session)
    for ref in (str(fa.id), "PP-1 / A", ["a"]):
        out = await _call(db_session, admin_user, "list_power", fail_feeds=ref)
        fp = out["failure_preview"]
        assert [d["outcome"] for d in fp["devices"]] == ["transferable"], ref
        b = next(f for f in fp["feeds"] if f["name"] == "B")
        assert (b["before_w"], b["after_w"]) == (250, 500)
        assert "feeds_not_proven_independent" in fp["assumptions"]


async def test_list_power_fail_feeds_bad_names(db_session, admin_user) -> None:
    await _feeds(db_session)
    p2 = PowerPanel(name="PP-2")
    db_session.add(p2)
    await db_session.flush()
    db_session.add(PowerFeed(panel_id=p2.id, name="A"))
    await db_session.flush()
    with pytest.raises(IPAMToolError, match="ambiguous"):
        await _call(db_session, admin_user, "list_power", fail_feeds="A")
    with pytest.raises(IPAMToolError, match="not found"):
        await _call(db_session, admin_user, "list_power", fail_feeds=["no-such-feed"])
