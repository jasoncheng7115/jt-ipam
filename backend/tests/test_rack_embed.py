"""機櫃圖的可嵌入 SVG（給 LibreNMS dashboard 之類的外部系統用 `<img>` 直接顯示）。

**這是一個不需登入的端點**，token 是唯一的守門，所以測試的重點不是「畫得對不對」，
而是「沒有 token 的人拿不到、沒開放的機櫃拿不到、而且問不出哪些機櫃存在」。

為什麼是圖片而不是 iframe：我們的 CSP 是 `frame-ancestors 'none'`、又送
`X-Frame-Options: DENY`，iframe 嵌入本來就會被擋；要開就得針對特定來源放行，
那是點擊劫持的攻擊面。`<img>` 走的是圖片，兩邊都不用動。
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.device import Device
from app.models.location import Location, Rack
from app.services.system_config import set_rack_embed


async def _rack(session, *, exposed: bool) -> Rack:
    loc = Location(name="機房 A")
    session.add(loc)
    await session.flush()
    rack = Rack(name="R1", u_height=12, location_id=loc.id, expose_svg=exposed)
    session.add(rack)
    await session.flush()
    session.add(Device(name="sw-1", type="switch", rack_id=rack.id,
                       u_position=3, u_size=1))
    await session.commit()
    return rack


async def _token(session) -> str:
    cfg = await set_rack_embed(session, enabled=True, regenerate_token=True)
    await session.commit()
    return cfg["token"]


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as c:
        yield c


async def test_serves_svg_with_a_valid_token(db_session, client):
    rack = await _rack(db_session, exposed=True)
    token = await _token(db_session)

    r = await client.get(f"/api/v1/racks/{rack.id}/embed.svg", params={"token": token})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/svg+xml")
    # 這張圖是要被別的網站用 <img> 貼上去的：站台預設的 CORP same-origin 會讓瀏覽器擋下跨站載入
    # （2026-09-30 發現；nginx 另有一段 location 處理同一件事）
    assert r.headers["cross-origin-resource-policy"] == "cross-origin"
    assert r.headers["cache-control"] == "no-store"
    body = r.text
    assert body.startswith("<svg") or body.lstrip().startswith("<svg")
    assert "R1" in body and "sw-1" in body


async def test_rejects_a_wrong_or_missing_token(db_session, client):
    rack = await _rack(db_session, exposed=True)
    await _token(db_session)

    for params in ({}, {"token": ""}, {"token": "nope"}):
        r = await client.get(f"/api/v1/racks/{rack.id}/embed.svg", params=params)
        assert r.status_code == 401, f"{params} 應該被擋"


async def test_rack_not_opted_in_is_not_served(db_session, client):
    """預設不開放。沒開的機櫃即使拿著正確 token 也拿不到。"""
    rack = await _rack(db_session, exposed=False)
    token = await _token(db_session)

    r = await client.get(f"/api/v1/racks/{rack.id}/embed.svg", params={"token": token})
    assert r.status_code == 404


async def test_unknown_and_closed_racks_are_indistinguishable(db_session, client):
    """不存在的機櫃與「存在但沒開放」要回一樣的東西 —— 否則這個端點會變成
    「拿著 token 就能列舉哪些機櫃存在」的探測管道。"""
    import uuid as _uuid

    closed = await _rack(db_session, exposed=False)
    token = await _token(db_session)

    a = await client.get(f"/api/v1/racks/{closed.id}/embed.svg", params={"token": token})
    b = await client.get(f"/api/v1/racks/{_uuid.uuid4()}/embed.svg", params={"token": token})
    assert a.status_code == b.status_code == 404
    assert a.text == b.text


async def test_disabled_feature_serves_nothing(db_session, client):
    """整個功能關掉時，即使機櫃開放、token 正確也不給。"""
    rack = await _rack(db_session, exposed=True)
    token = await _token(db_session)
    await set_rack_embed(db_session, enabled=False, regenerate_token=False)
    await db_session.commit()

    r = await client.get(f"/api/v1/racks/{rack.id}/embed.svg", params={"token": token})
    assert r.status_code == 401


async def test_response_cannot_run_scripts_if_opened_directly(db_session, client):
    """SVG 可以夾帶腳本。我們自己產生的內容不會，但仍要把回應鎖死 ——
    這張圖會被貼到別人的儀表板上。"""
    rack = await _rack(db_session, exposed=True)
    token = await _token(db_session)

    r = await client.get(f"/api/v1/racks/{rack.id}/embed.svg", params={"token": token})
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert "default-src 'none'" in r.headers.get("content-security-policy", "")


async def test_device_names_are_escaped(db_session, client):
    """裝置名稱是使用者輸入，直接串進 SVG 會變成注入點。"""
    loc = Location(name="機房 B")
    db_session.add(loc)
    await db_session.flush()
    rack = Rack(name='R<2>', u_height=6, location_id=loc.id, expose_svg=True)
    db_session.add(rack)
    await db_session.flush()
    db_session.add(Device(name='<script>alert(1)</script>', type="server",
                          rack_id=rack.id, u_position=1, u_size=1))
    await db_session.commit()
    token = await _token(db_session)

    r = await client.get(f"/api/v1/racks/{rack.id}/embed.svg", params={"token": token})
    assert r.status_code == 200
    assert "<script>" not in r.text
    assert "&lt;script&gt;" in r.text


async def test_device_group_bracket_is_drawn(db_session, client):
    """裝置群組（堆疊／HA）在對外嵌入圖上也要標得出來：同色括線＋「名稱 本櫃/總數」，跟畫面與匯出一樣。"""
    from app.models.device_group import DeviceGroup
    rack = await _rack(db_session, exposed=True)
    token = await _token(db_session)
    g = DeviceGroup(name="N2048P-stack", kind="stack")
    db_session.add(g)
    await db_session.flush()
    sw1 = (await db_session.execute(Device.__table__.select().where(Device.name == "sw-1"))).first()
    db_session.add_all([Device(name="sw-2", type="switch", rack_id=rack.id, u_position=4, u_size=1, group_id=g.id),
                        Device(name="sw-3", type="switch", group_id=g.id)])     # 沒放機櫃
    await db_session.execute(Device.__table__.update().where(Device.id == sw1.id).values(group_id=g.id))
    await db_session.commit()

    body = (await client.get(f"/api/v1/racks/{rack.id}/embed.svg", params={"token": token})).text
    assert body.count('class="rack-group"') == 1
    assert "N2048P-stack 2/3" in body


def test_svg_without_groups_is_unchanged():
    """沒有群組的機櫃，嵌入圖逐位元組不變（不能因為加了群組就讓既有的嵌入圖變寬）。"""
    from app.services.rack_svg import build_rack_svg
    devs = [{"name": "a", "type": "switch", "u_position": 3, "u_size": 1}]
    assert build_rack_svg("R", 12, devs) == build_rack_svg("R", 12, [{**devs[0], "group": None}])
    with_group = build_rack_svg("R", 12, [{**devs[0], "group": {"id": "g", "name": "G", "total": 2}}])
    assert 'class="rack-group"' in with_group and "G 1/2" in with_group
    assert 'class="rack-group"' not in build_rack_svg("R", 12, devs)
