"""實體資料檢查（唯讀盤點，交接包 physical-v1.2：上版前先列出孤端、重複占用與非對稱配對，2026-11-21）。

新的寫入規則只擋「之後」的錯；升級前就存在的問題不自動刪除、合併或改接，要讓管理員看得到、自己決定。
- 一個埠被兩條以上「已接線」的纜線占用
- 一個插座接了兩顆以上 PSU
- 非對稱配對：A 指向 B，B 卻沒指回 A
- 半條線：已接線的纜線少一端，或端點指向已經不存在的埠
- 舊式插座直連（power_outlets.device_id）：那台裝置沒有任何 PSU 接在這個插座 → 待補 PSU；
  插座上的 PSU 屬於另一台裝置 → 衝突
- 只讀：呼叫前後資料完全一樣；只有管理員看得到
"""
from __future__ import annotations

import uuid

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

URL = "/api/v1/physical-check"


@pytest.mark.anyio
async def test_lists_legacy_problems_without_touching_them(client, auth_headers, db_session) -> None:
    sw, srv, pp = Device(name="sw-core01"), Device(name="pve01"), Device(name="pp-a01")
    db_session.add_all([sw, srv, pp])
    await db_session.flush()
    g1 = DevicePort(device_id=sw.id, name="Gi1/0/1")
    e1, e2 = DevicePort(device_id=srv.id, name="eno1"), DevicePort(device_id=srv.id, name="eno2")
    e3, e4 = DevicePort(device_id=srv.id, name="eno3"), DevicePort(device_id=srv.id, name="eno4")
    f1, r1, r2 = (DevicePort(device_id=pp.id, name="F1", type="front"), DevicePort(device_id=pp.id, name="R1", type="rear"),
                  DevicePort(device_id=pp.id, name="R2", type="rear"))
    db_session.add_all([g1, e1, e2, e3, e4, f1, r1, r2])
    await db_session.flush()
    # 非對稱：F1 → R1，R1 → R2
    f1.peer_port_id = r1.id
    r1.peer_port_id = r2.id
    # Gi1/0/1 上有兩條已接線的線（升級前的舊資料）
    for lbl, other in (("A01-01", e1), ("A01-09", e2)):
        c = Cable(label=lbl, status="connected")
        db_session.add(c)
        await db_session.flush()
        db_session.add_all([CableTermination(cable_id=c.id, side="A", object_type="device_port", object_id=g1.id),
                            CableTermination(cable_id=c.id, side="B", object_type="device_port", object_id=other.id)])
    # 半條線：只有一端；另一條的端點指向不存在的埠
    half = Cable(label="A02-01", status="connected")
    dangling = Cable(label="A02-02", status="connected")
    db_session.add_all([half, dangling])
    await db_session.flush()
    db_session.add_all([
        CableTermination(cable_id=half.id, side="A", object_type="device_port", object_id=e3.id),
        CableTermination(cable_id=dangling.id, side="A", object_type="device_port", object_id=e4.id),
        CableTermination(cable_id=dangling.id, side="B", object_type="device_port", object_id=uuid.uuid4()),
    ])
    # 電力：一個插座兩顆 PSU；舊式直連
    panel = PowerPanel(name="PP-1")
    db_session.add(panel)
    await db_session.flush()
    feed = PowerFeed(panel_id=panel.id, name="A")
    db_session.add(feed)
    await db_session.flush()
    o1 = PowerOutlet(feed_id=feed.id, label="A-01")
    o2 = PowerOutlet(feed_id=feed.id, label="A-02", device_id=sw.id)       # 舊直連、沒有 PSU → 待補
    o3 = PowerOutlet(feed_id=feed.id, label="A-03", device_id=sw.id)       # 舊直連但接的是 pve01 的 PSU → 衝突
    db_session.add_all([o1, o2, o3])
    await db_session.flush()
    db_session.add_all([DevicePowerPort(device_id=srv.id, name="PSU1", outlet_id=o1.id),
                        DevicePowerPort(device_id=srv.id, name="PSU2", outlet_id=o1.id),
                        DevicePowerPort(device_id=srv.id, name="PSU3", outlet_id=o3.id)])
    await db_session.commit()

    async def snapshot():  # type: ignore[no-untyped-def]
        out = []
        for m in (Cable, CableTermination, DevicePort, DevicePowerPort, PowerOutlet):
            out.append(int((await db_session.execute(select(func.count()).select_from(m))).scalar_one()))
        out.append(sorted((str(i), str(p)) for i, p in (await db_session.execute(
            select(DevicePort.id, DevicePort.peer_port_id))).all()))
        return out
    before = await snapshot()

    r = await client.get(URL, headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    db_session.expire_all()
    assert await snapshot() == before, "盤點只讀，不可以改任何資料"

    multi = body["ports_multi_cable"]
    assert [(x["device"], x["port"], sorted(x["cables"])) for x in multi["items"]] == [("sw-core01", "Gi1/0/1", ["A01-01", "A01-09"])]
    assert multi["total"] == 1
    assert [(x["outlet"], sorted(x["ports"])) for x in body["outlets_multi_psu"]["items"]] == [("A-01", ["pve01 PSU1", "pve01 PSU2"])]
    asym = body["asymmetric_peers"]["items"]
    assert [(x["device"], x["port"], x["peer"], x["peer_points_to"]) for x in asym] == [
        ("pp-a01", "F1", "R1", "R2"), ("pp-a01", "R1", "R2", None)]
    half_rows = {x["cable"]: x["reason"] for x in body["incomplete_cables"]["items"]}
    assert half_rows == {"A02-01": "one_end", "A02-02": "missing_port"}
    legacy = {x["outlet"]: x["reason"] for x in body["legacy_outlet_links"]["items"]}
    assert legacy == {"A-02": "no_psu", "A-03": "psu_of_other_device"}
    assert body["total"] == 1 + 1 + 2 + 2 + 2


@pytest.mark.anyio
async def test_clean_data_and_admin_only(client, auth_headers, db_session) -> None:
    r = await client.get(URL, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["total"] == 0
    from app.core.security import hash_password
    from app.models.user import User
    from app.services.auth import issue_access_token
    u = User(username="chk-reader", email="chk-reader@t.local", display_name="U",
             password_hash=hash_password("TestPassword2026!"), auth_provider="local", is_active=True)
    db_session.add(u)
    await db_session.commit()
    r = await client.get(URL, headers={"Authorization": f"Bearer {issue_access_token(u)}"})
    assert r.status_code == 403
