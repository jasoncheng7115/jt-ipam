"""無線連線與 SSID 的畫面需要的 API（2026-11-21：無線頁籤以前只有 SSID 表格，點對點連線沒有任何畫面）。

- 清單直接帶兩端裝置名稱、SSID 的 VLAN 與租戶名稱（表格不靠前端只載第一頁的選單去找名稱）
- 兩端不可以是同一台裝置
"""
from __future__ import annotations

import uuid

import pytest

from app.models.device import Device
from app.models.vlan import VLAN, VLANDomain

pytestmark = pytest.mark.anyio


def _u() -> str:
    return uuid.uuid4().hex[:6]


async def test_link_list_carries_device_names_and_rejects_same_device(client, auth_headers, db_session) -> None:
    a = Device(name=f"ap-a-{_u()}", type="ap")
    b = Device(name=f"ap-b-{_u()}", type="ap")
    db_session.add_all([a, b])
    await db_session.commit()
    name = f"bridge-{_u()}"
    r = await client.post("/api/v1/wireless/links", headers=auth_headers, json={
        "name": name, "a_device_id": str(a.id), "b_device_id": str(b.id), "ssid": "bh-5g", "distance_m": 850})
    assert r.status_code == 201, r.text
    lid = r.json()["id"]
    rows = (await client.get("/api/v1/wireless/links", headers=auth_headers, params={"page_size": 500})).json()["items"]
    row = next(x for x in rows if x["id"] == lid)
    assert (row["a_device_name"], row["b_device_name"]) == (a.name, b.name)
    r = await client.patch(f"/api/v1/wireless/links/{lid}", headers=auth_headers,
                           json={"name": name, "a_device_id": str(a.id), "b_device_id": str(a.id)})
    assert r.status_code == 422
    r = await client.post("/api/v1/wireless/links", headers=auth_headers, json={"name": name})
    assert r.status_code == 409, "名稱重複"
    assert (await client.delete(f"/api/v1/wireless/links/{lid}", headers=auth_headers)).status_code == 204


async def test_ssid_list_carries_vlan_and_tenant_names(client, auth_headers, db_session) -> None:
    from app.models.advanced import Tenant
    dom = VLANDomain(name=f"d-{_u()}")
    db_session.add(dom)
    await db_session.flush()
    vlan = VLAN(domain_id=dom.id, number=310, name=f"guest-{_u()}")
    tenant = Tenant(name=f"t-{_u()}", slug=f"t-{_u()}")
    db_session.add_all([vlan, tenant])
    await db_session.commit()
    r = await client.post("/api/v1/wireless/ssids", headers=auth_headers, json={
        "ssid": f"Guest-{_u()}", "auth_type": "wpa3-personal", "vlan_id": str(vlan.id), "tenant_id": str(tenant.id)})
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    rows = (await client.get("/api/v1/wireless/ssids", headers=auth_headers, params={"page_size": 500})).json()["items"]
    row = next(x for x in rows if x["id"] == sid)
    assert row["vlan_label"] == f"310 {vlan.name}"
    assert row["tenant_name"] == tenant.name
    assert row["auth_type"] == "wpa3-personal"
