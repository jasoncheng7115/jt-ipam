"""新增 IP 時表單上的設定不可以被丟掉（2026-11-21 掃描表單缺口時發現）。

新增視窗跟編輯一樣顯示主控台出口、略過的探測、上線跟著裝置、略過 PTR 與各主控台開關，但：
- 前端新增時只送 12 個欄位，其餘選了也不送
- 後端新增也沒存 jump_host_id／console_agent_id／excluded_probes（連前端有送的跳板都被丟掉），
  主控台開關根本不在新增格式裡
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.address import IPAddress
from app.models.jump_host import JumpHost
from app.models.section import Section
from app.models.subnet import Subnet

pytestmark = pytest.mark.anyio


async def _subnet(db):  # type: ignore[no-untyped-def]
    sec = Section(name=f"s-{uuid.uuid4().hex[:6]}")
    db.add(sec)
    await db.flush()
    sub = Subnet(section_id=sec.id, cidr="198.51.100.0/24")
    db.add(sub)
    await db.commit()
    return sub


async def test_create_keeps_console_probe_and_switch_fields(client, auth_headers, db_session) -> None:
    sub = await _subnet(db_session)
    jump = JumpHost(name=f"j-{uuid.uuid4().hex[:6]}", host="198.51.100.9", username="j", auth_kind="key")
    db_session.add(jump)
    await db_session.commit()
    r = await client.post("/api/v1/addresses", headers=auth_headers, json={
        "subnet_id": str(sub.id), "ip": "198.51.100.20", "jump_host_id": str(jump.id),
        "excluded_probes": ["icmp", "mdns"], "liveness_from_device": True, "ptr_ignore": True,
        "ssh_enabled": True, "rdp_enabled": True, "bmc_enabled": True, "is_dhcp_server": True})
    assert r.status_code == 201, r.text
    obj = (await db_session.execute(select(IPAddress).where(IPAddress.id == uuid.UUID(r.json()["id"])))).scalar_one()
    await db_session.refresh(obj)
    assert obj.jump_host_id == jump.id
    assert sorted(obj.excluded_probes) == ["icmp", "mdns"]
    assert obj.exclude_from_ping is True, "略過 icmp＝不 ping（兩欄同步，同編輯）"
    assert (obj.liveness_from_device, obj.ptr_ignore) == (True, True)
    assert (obj.ssh_enabled, obj.rdp_enabled, obj.bmc_enabled, obj.is_dhcp_server) == (True, True, True, True)
    assert obj.vnc_enabled is False


async def test_create_rejects_both_console_exits(client, auth_headers, db_session) -> None:
    """跳板與代理中繼擇一（同編輯的檢查）；代理要是這個子網路的掃描代理。"""
    from app.models.scan_agent import ScanAgent
    sub = await _subnet(db_session)
    jump = JumpHost(name=f"j-{uuid.uuid4().hex[:6]}", host="198.51.100.9", username="j", auth_kind="key")
    agent = ScanAgent(name=f"a-{uuid.uuid4().hex[:6]}")
    db_session.add_all([jump, agent])
    await db_session.commit()
    r = await client.post("/api/v1/addresses", headers=auth_headers, json={
        "subnet_id": str(sub.id), "ip": "198.51.100.21", "jump_host_id": str(jump.id), "console_agent_id": str(agent.id)})
    assert r.status_code == 422, r.text
