"""NAT 清單帶裝置名稱（GitHub issue #58 的連帶問題）。

NAT 頁的表格原本拿「裝置選單」（只載第一頁）去找名稱，裝置超過一頁時表格就顯示 ID 前 8 碼。
IP 位址早就由清單 API 帶文字（src_ip / dst_ip）；裝置名稱也一樣由 API 帶，不靠前端的選單。
"""
from __future__ import annotations

import uuid

import pytest

from app.models.device import Device

pytestmark = pytest.mark.anyio


async def test_nat_list_carries_the_device_name(client, auth_headers, db_session) -> None:
    dev = Device(name=f"fw-nat-{uuid.uuid4().hex[:6]}", type="firewall")
    db_session.add(dev)
    await db_session.commit()
    r = await client.post("/api/v1/nat", headers=auth_headers,
                          json={"name": f"pf-{uuid.uuid4().hex[:6]}", "type": "port_forward", "device_id": str(dev.id)})
    assert r.status_code == 201, r.text
    nid = r.json()["id"]
    items = (await client.get(f"/api/v1/nat?device_id={dev.id}", headers=auth_headers)).json()["items"]
    row = next(x for x in items if x["id"] == nid)
    assert row["device_name"] == dev.name
