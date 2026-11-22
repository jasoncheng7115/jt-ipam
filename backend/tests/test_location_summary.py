"""機房詳情與有效位置（交接包 physical-v1.2 SPEC.md 第 8、9 節、CABLING.md，2026-11-21）。

有效位置（API、清單、總覽共用一套）：
1. 裝置在機櫃裡、機櫃有地點 → 算機櫃的地點
2. 裝置沒入櫃 → 算裝置自己的地點
3. 機櫃沒有地點 → 不靜默改用裝置欄位，列「機櫃地點未設定」
4. 裝置自己的地點跟機櫃不同 → 只算機櫃的地點一次，另列位置衝突
5. 看不到的機櫃 / 裝置 → 不計數也不列名
機房的關聯 IP 用 IP 紀錄的 id 去重（不同子網路的同一個位址字串算兩筆）。
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from app.models.address import IPAddress
from app.models.device import Device
from app.models.location import Location, Rack
from app.models.physical import (
    Cable,
    CableTermination,
    DevicePort,
    DevicePowerPort,
    DevicePowerProfile,
    PowerFeed,
    PowerOutlet,
    PowerPanel,
)
from app.models.section import Section
from app.models.subnet import Subnet


async def _site(db):  # type: ignore[no-untyped-def]
    l1, l2 = Location(name="東京機房"), Location(name="大阪機房")
    db.add_all([l1, l2])
    await db.flush()
    r1, r2, r3 = Rack(name="TYO-R01", location_id=l1.id), Rack(name="TYO-R02", location_id=l1.id), Rack(name="OSA-R01", location_id=l2.id)
    r_unset = Rack(name="NO-LOC")
    db.add_all([r1, r2, r3, r_unset])
    await db.flush()
    d = {}
    for name, rack, loc in (("app-01", r1, None), ("app-02", r2, l1.id), ("moved", r1, l2.id), ("desk-pc", None, l1.id),
                            ("osa-01", r3, None), ("orphan", r_unset, l1.id)):
        d[name] = Device(name=name, type="server", rack_id=rack.id if rack else None, location_id=loc)
        db.add(d[name])
    await db.flush()
    return l1, l2, (r1, r2, r3, r_unset), d


@pytest.mark.anyio
async def test_effective_location_and_conflicts(client, auth_headers, db_session) -> None:
    l1, l2, _r, _d = await _site(db_session)
    await db_session.commit()
    s = (await client.get(f"/api/v1/locations/{l1.id}/summary", headers=auth_headers)).json()
    assert s["racks"] == 2
    assert s["devices"] == {"total": 4, "racked": 3, "unracked": 1}, "app-01、app-02、moved（機櫃在東京）＋未入櫃 desk-pc"
    assert s["pending"]["unracked"] == 1
    assert s["pending"]["location_conflicts"] == 1, "moved 記在大阪、機櫃在東京"
    assert s["pending"]["rack_location_unset"] == 1, "orphan 的機櫃沒有地點，不算進東京"
    devs = (await client.get(f"/api/v1/locations/{l1.id}/devices?filter=all", headers=auth_headers)).json()
    assert devs["total"] == 4
    st = {x["name"]: x["status"] for x in devs["items"]}
    assert st == {"app-01": "racked", "app-02": "racked", "moved": "conflict", "desk-pc": "unracked"}
    conflicts = (await client.get(f"/api/v1/locations/{l1.id}/devices?filter=conflict", headers=auth_headers)).json()
    assert [x["name"] for x in conflicts["items"]] == ["moved"]
    assert conflicts["items"][0]["recorded_location"] == "大阪機房"
    unset = (await client.get(f"/api/v1/locations/{l1.id}/devices?filter=rack_location_unset", headers=auth_headers)).json()
    assert [x["name"] for x in unset["items"]] == ["orphan"]
    # 大阪：只有 osa-01；moved 不算兩次，但大阪也看得到這筆衝突
    s2 = (await client.get(f"/api/v1/locations/{l2.id}/summary", headers=auth_headers)).json()
    assert s2["devices"]["total"] == 1
    assert s2["pending"]["location_conflicts"] == 1


@pytest.mark.anyio
async def test_location_list_counts_use_effective_location(client, auth_headers, db_session) -> None:
    l1, l2, _r, _d = await _site(db_session)
    await db_session.commit()
    items = {x["name"]: x for x in (await client.get("/api/v1/locations", headers=auth_headers)).json()["items"]}
    assert items["東京機房"]["device_count"] == 4
    assert items["大阪機房"]["device_count"] == 1
    assert l2.id


@pytest.mark.anyio
async def test_ips_counted_by_record_id(client, auth_headers, db_session) -> None:
    l1, _l2, _r, d = await _site(db_session)
    sec = Section(name="s")
    db_session.add(sec)
    await db_session.flush()
    s1 = Subnet(cidr="10.0.0.0/24", section_id=sec.id)
    s2 = Subnet(cidr="10.0.0.0/24", section_id=sec.id, description="另一個 VRF")
    db_session.add_all([s1, s2])
    await db_session.flush()
    db_session.add_all([IPAddress(subnet_id=s1.id, ip="10.0.0.5", device_id=d["app-01"].id, state="active"),
                        IPAddress(subnet_id=s2.id, ip="10.0.0.5", device_id=d["desk-pc"].id, state="active"),
                        IPAddress(subnet_id=s1.id, ip="10.0.0.9", device_id=d["osa-01"].id, state="active")])
    await db_session.commit()
    s = (await client.get(f"/api/v1/locations/{l1.id}/summary", headers=auth_headers)).json()
    assert s["ips"] == 2, "同一個位址字串在兩個子網路是兩筆紀錄；大阪的不算"
    devs = (await client.get(f"/api/v1/locations/{l1.id}/devices?filter=unracked", headers=auth_headers)).json()
    assert devs["items"][0]["ips"][0]["ip"].startswith("10.0.0.5")


@pytest.mark.anyio
async def test_location_power_includes_unracked(client, auth_headers, db_session) -> None:
    l1, _l2, _r, d = await _site(db_session)
    for name, w in (("app-01", 500), ("desk-pc", 120), ("osa-01", 900)):
        db_session.add(DevicePowerProfile(device_id=d[name].id, planning_input_w=Decimal(w)))
    await db_session.commit()
    s = (await client.get(f"/api/v1/locations/{l1.id}/summary", headers=auth_headers)).json()
    assert s["power"]["known_total_w"] == 620, "機櫃裡的 500 ＋ 未入櫃的 120；大阪的 900 不算"
    assert s["power"]["completeness"] == {"known": 2, "included": 4}
    p = (await client.get(f"/api/v1/locations/{l1.id}/power-summary", headers=auth_headers)).json()
    assert p["known_total_w"] == 620


@pytest.mark.anyio
async def test_location_feeds_show_location_share(client, auth_headers, db_session) -> None:
    l1, _l2, (r1, _r2, r3, _u), d = await _site(db_session)
    panel = PowerPanel(name="PP-1", location_id=l1.id)
    db_session.add(panel)
    await db_session.flush()
    feed = PowerFeed(panel_id=panel.id, name="A", planning_capacity_w=Decimal(5000))
    db_session.add(feed)
    await db_session.flush()
    o1, o2 = PowerOutlet(feed_id=feed.id, label="A-1", rack_id=r1.id), PowerOutlet(feed_id=feed.id, label="A-2", rack_id=r3.id)
    db_session.add_all([o1, o2])
    await db_session.flush()
    for name, w, o in (("app-01", 500, o1), ("osa-01", 900, o2)):
        db_session.add(DevicePowerProfile(device_id=d[name].id, planning_input_w=Decimal(w)))
        db_session.add(DevicePowerPort(device_id=d[name].id, name="PSU1", outlet_id=o.id))
    await db_session.commit()
    p = (await client.get(f"/api/v1/locations/{l1.id}/power-summary", headers=auth_headers)).json()
    f = next(x for x in p["feeds"] if x["name"] == "A")
    assert f["location_allocated_w"] == 500
    assert f["full_allocated_w"] == 1400, "同一路還供大阪，全迴路要算進去"


@pytest.mark.anyio
async def test_cabling_in_room_cross_room_unknown(client, auth_headers, db_session) -> None:
    l1, _l2, _r, d = await _site(db_session)
    ports = {}
    for name in ("app-01", "app-02", "moved", "osa-01", "desk-pc"):
        for i in (1, 2, 3):
            p = DevicePort(device_id=d[name].id, name=f"eth{i}")
            db_session.add(p)
            ports[(name, i)] = p
    await db_session.flush()

    async def cable(label, a, b, status="connected"):  # type: ignore[no-untyped-def]
        c = Cable(label=label, status=status)
        db_session.add(c)
        await db_session.flush()
        db_session.add(CableTermination(cable_id=c.id, side="A", object_type="device_port", object_id=ports[a].id))
        if b is not None:
            db_session.add(CableTermination(cable_id=c.id, side="B", object_type="device_port", object_id=ports[b].id))
    await cable("same-rack", ("app-01", 1), ("moved", 1))       # 都在 TYO-R01
    await cable("cross-rack", ("app-01", 2), ("app-02", 1))     # 同機房不同櫃
    await cable("cross-room", ("app-02", 2), ("osa-01", 1))     # 東京 ↔ 大阪
    await cable("dangling", ("desk-pc", 1), None)               # 對端未登錄
    await cable("planned", ("app-01", 3), ("osa-01", 2), status="planned")   # 規劃中不算
    await db_session.commit()
    c = (await client.get(f"/api/v1/locations/{l1.id}/cabling", headers=auth_headers)).json()
    assert c["total"] == 4
    assert c["in_room"] == 2
    assert c["cross_room"] == 1
    assert c["unknown"] == 1
    kinds = {x["label"]: x["kind"] for x in c["items"]}
    assert kinds == {"same-rack": "in_room", "cross-rack": "in_room", "cross-room": "cross_room", "dangling": "unknown"}


@pytest.mark.anyio
async def test_location_permission_boundaries(client, db_session) -> None:
    from app.core.security import hash_password
    from app.models.permission import Permission
    from app.models.user import User
    from app.services.auth import issue_access_token
    l1, _l2, _r, _d = await _site(db_session)
    u = User(username="loc-only", email="loc-only@t.local", display_name="U",
             password_hash=hash_password("TestPassword2026!"), auth_provider="local", is_active=True)
    db_session.add(u)
    await db_session.flush()
    db_session.add(Permission(object_type="location", object_id=l1.id, principal_type="user", principal_id=u.id, level="read"))
    await db_session.commit()
    h = {"Authorization": f"Bearer {issue_access_token(u)}"}
    # 機房授權本來就涵蓋其中的機櫃與裝置（services/permission）：看得到東京的 4 台
    s = (await client.get(f"/api/v1/locations/{l1.id}/summary", headers=h)).json()
    assert s["devices"]["total"] == 4
    names = {x["name"] for x in (await client.get(f"/api/v1/locations/{l1.id}/devices", headers=h)).json()["items"]}
    assert "osa-01" not in names
    # 大阪沒有授權：詳情、裝置、電力、佈線都不給
    for path in ("summary", "devices", "power-summary", "cabling"):
        assert (await client.get(f"/api/v1/locations/{_l2.id}/{path}", headers=h)).status_code in (403, 404), path
    # 電力與佈線是全域資料：只有機房授權、沒有全域讀取 → 連自己的機房也不給
    for path in ("power-summary", "cabling"):
        assert (await client.get(f"/api/v1/locations/{l1.id}/{path}", headers=h)).status_code == 403, path


@pytest.mark.anyio
async def test_device_list_sorts_across_pages(client, auth_headers, db_session) -> None:
    """機房的裝置清單是後端分頁：排序要在分頁前做，不能只排畫面上那一頁（2026-11-22 使用者：新頁表格缺排序）。"""
    l1, _l2, _r, d = await _site(db_session)
    sec = Section(name="s")
    db_session.add(sec)
    await db_session.flush()
    sub = Subnet(cidr="198.51.100.0/24", section_id=sec.id)
    db_session.add(sub)
    await db_session.flush()
    for name, ip in (("app-01", "198.51.100.20"), ("app-02", "198.51.100.3"), ("moved", "198.51.100.100")):
        db_session.add(IPAddress(subnet_id=sub.id, ip=ip, device_id=d[name].id, state="active"))
    await db_session.commit()
    url = f"/api/v1/locations/{l1.id}/devices?page_size=2"

    async def names(**q):  # type: ignore[no-untyped-def]
        qs = "".join(f"&{k}={v}" for k, v in q.items())
        out = []
        for page in (1, 2):
            out += [x["name"] for x in (await client.get(f"{url}&page={page}{qs}", headers=auth_headers)).json()["items"]]
        return out

    assert await names(sort="name", order="desc") == ["moved", "desk-pc", "app-02", "app-01"]
    assert await names(sort="ips") == ["app-02", "app-01", "moved", "desk-pc"], "依位址數值排，沒有 IP 的排最後"
    assert await names(sort="rack") == ["app-01", "moved", "app-02", "desk-pc"], "未入櫃排最後；同機櫃依名稱"
    assert (await names(sort="status"))[-1] == "desk-pc"
    r = await client.get(f"{url}&sort=password", headers=auth_headers)
    assert r.status_code == 422
