"""Wazuh agent 同步（mock fetch_agents）：upsert agents、跳過 manager(000)、
對映同 IP 的位址物件並回填 wazuh 主機名稱。"""

from __future__ import annotations

from sqlalchemy import select

from app.models.address import IPAddress
from app.models.section import Section
from app.models.subnet import Subnet
from app.models.wazuh import WazuhAgent, WazuhInstance
from app.services import wazuh as wz


async def _instance(session) -> WazuhInstance:
    inst = WazuhInstance(
        name="wz-test", api_url="https://wz.example.com", api_user="u",
        api_password_enc=b"x", api_password_nonce=b"x",
    )
    session.add(inst)
    await session.flush()
    return inst


async def _mk_ip(session, ip="10.4.0.5"):
    sec = Section(name="wz-sec")
    session.add(sec)
    await session.flush()
    sub = Subnet(section_id=sec.id, cidr="10.4.0.0/24")
    session.add(sub)
    await session.flush()
    obj = IPAddress(subnet_id=sub.id, ip=ip)
    session.add(obj)
    await session.flush()
    return obj


def _patch(monkeypatch, agents):
    async def _fake(_inst, *, batch=500):
        return agents
    monkeypatch.setattr(wz, "fetch_agents", _fake)


async def test_sync_agents_upsert_skip_manager_match_ip(db_session, monkeypatch):
    inst = await _instance(db_session)
    ipa = await _mk_ip(db_session)
    _patch(monkeypatch, [
        {"id": "000", "name": "manager", "ip": "127.0.0.1", "status": "active"},
        {"id": "001", "name": "web01", "ip": "10.4.0.5", "status": "active",
         "os": {"platform": "ubuntu", "version": "22.04"}, "group": ["default"],
         "version": "Wazuh v4.7"},
        {"id": "002", "name": "lonely", "ip": "10.4.0.250", "status": "disconnected",
         "os": {}},
    ])
    res = await wz.sync_agents(db_session, inst)
    await db_session.flush()

    rows = {a.agent_id: a for a in (await db_session.execute(
        select(WazuhAgent).where(WazuhAgent.instance_id == inst.id)
    )).scalars().all()}
    assert set(rows) == {"001", "002"}         # manager(000) 跳過
    assert rows["001"].name == "web01"
    assert rows["001"].os_platform == "ubuntu"
    assert rows["001"].jt_ipam_address_id == ipa.id   # 對映到位址物件
    assert rows["002"].jt_ipam_address_id is None      # 無相符 IP

    # web01 的名稱回填到該 IP（wazuh 來源，無更高來源 → 生效）
    await db_session.refresh(ipa)
    assert ipa.hostname == "web01"
    assert res.get("matched_ip", 0) >= 1


async def test_sync_agents_idempotent_update(db_session, monkeypatch):
    inst = await _instance(db_session)
    _patch(monkeypatch, [
        {"id": "001", "name": "old", "ip": "10.4.0.9", "status": "active", "os": {}},
    ])
    await wz.sync_agents(db_session, inst)
    # 第二次：名稱/狀態改變 → 更新同一筆，不新增
    _patch(monkeypatch, [
        {"id": "001", "name": "new", "ip": "10.4.0.9", "status": "disconnected", "os": {}},
    ])
    await wz.sync_agents(db_session, inst)
    await db_session.flush()

    rows = (await db_session.execute(
        select(WazuhAgent).where(WazuhAgent.instance_id == inst.id)
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].name == "new"
    assert rows[0].status == "disconnected"
