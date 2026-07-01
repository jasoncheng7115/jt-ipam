"""回歸測試：cert-agent 對應裝置（device_id）→ list/status 回 device_name；
last_source_ip 解析到 IPAddress → source_ip_id（讓前端名稱/IP 欄可點）。"""

from __future__ import annotations

import uuid

from app.models.address import IPAddress
from app.models.certificate import CertAgent
from app.models.device import Device
from app.models.section import Section
from app.models.subnet import Subnet
from sqlalchemy import select


async def test_agent_device_and_source_ip_links(client, auth_headers, db_session):
    # 既有裝置 + 該裝置的 IP（10.55.0.9）
    sec = Section(name="cert-link-sec")
    db_session.add(sec)
    await db_session.flush()
    sub = Subnet(section_id=sec.id, cidr="10.55.0.0/24")
    db_session.add(sub)
    await db_session.flush()
    dev = Device(name="cert-host-1", type="other")
    db_session.add(dev)
    await db_session.flush()
    db_session.add(IPAddress(subnet_id=sub.id, ip="10.55.0.9", device_id=dev.id))
    await db_session.commit()

    # 建代理並對應到該裝置
    r = await client.post("/api/v1/cert-agents", headers=auth_headers, json={
        "name": f"agent-{uuid.uuid4().hex[:6]}", "scope_cert_ids": [], "device_id": str(dev.id),
    })
    assert r.status_code == 201, r.text
    agent_id = r.json()["id"]
    assert r.json()["device_id"] == str(dev.id)

    # 模擬代理回報過來源 IP
    obj = await db_session.get(CertAgent, uuid.UUID(agent_id))
    obj.last_source_ip = "10.55.0.9"
    await db_session.commit()

    # list 回 device_name + source_ip_id
    rl = await client.get("/api/v1/cert-agents", headers=auth_headers)
    assert rl.status_code == 200, rl.text
    row = next(a for a in rl.json()["items"] if a["id"] == agent_id)
    assert row["device_id"] == str(dev.id)
    assert row["device_name"] == "cert-host-1"
    ip_row = (await db_session.execute(
        select(IPAddress).where(IPAddress.subnet_id == sub.id)
    )).scalar_one()
    assert row["source_ip_id"] == str(ip_row.id)


async def test_agent_without_device_has_null_links(client, auth_headers):
    r = await client.post("/api/v1/cert-agents", headers=auth_headers, json={
        "name": f"agent-{uuid.uuid4().hex[:6]}", "scope_cert_ids": [],
    })
    assert r.status_code == 201, r.text
    agent_id = r.json()["id"]
    rl = await client.get("/api/v1/cert-agents", headers=auth_headers)
    row = next(a for a in rl.json()["items"] if a["id"] == agent_id)
    assert row["device_id"] is None
    assert row["device_name"] is None
    assert row["source_ip_id"] is None
