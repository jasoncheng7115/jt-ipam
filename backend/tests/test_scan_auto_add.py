"""掃描代理 push：在「指派且有開掃描」的子網路內，發現未知 IP 會自動建立
（用我們自己的說明文字，非照抄 phpIPAM）；範圍外的 IP / 已死的 IP 不建立。"""

from __future__ import annotations

import hashlib

from sqlalchemy import func, select

from app.models.address import IPAddress
from app.models.scan_agent import ScanAgent
from app.models.section import Section
from app.models.subnet import Subnet

RAW_KEY = "test-raw-agent-key-abc123"


async def _setup_agent_subnet(session, *, scan_enabled: bool) -> ScanAgent:
    agent = ScanAgent(
        name="auto-add-agent",
        enabled=True,
        enroll_key_hash=hashlib.sha256(RAW_KEY.encode()).hexdigest(),
    )
    session.add(agent)
    await session.flush()
    sec = Section(name="auto-add-sec")
    session.add(sec)
    await session.flush()
    sub = Subnet(
        section_id=sec.id, cidr="10.88.0.0/24",
        scan_agent_id=agent.id, scan_enabled=scan_enabled,
    )
    session.add(sub)
    await session.commit()
    return agent


async def _ip_count(session, ip: str) -> int:
    return (await session.execute(
        select(func.count()).select_from(IPAddress)
        .where(func.host(IPAddress.ip) == ip)
    )).scalar_one()


async def test_auto_add_creates_unknown_ip(client, db_session):
    await _setup_agent_subnet(db_session, scan_enabled=True)
    r = await client.post(
        "/api/v1/scan-agents/report",
        headers={"X-Agent-Key": RAW_KEY},
        json={"results": [{"ip": "10.88.0.42", "alive": True}]},
    )
    assert r.status_code == 200, r.text
    row = (await db_session.execute(
        select(IPAddress).where(func.host(IPAddress.ip) == "10.88.0.42")
    )).scalar_one()
    assert row.discovery_source == "scanner"
    assert row.description == "掃描代理自動探索新增"
    assert row.last_seen_scanner is not None


async def test_no_add_when_out_of_assigned_range(client, db_session):
    await _setup_agent_subnet(db_session, scan_enabled=True)
    r = await client.post(
        "/api/v1/scan-agents/report",
        headers={"X-Agent-Key": RAW_KEY},
        json={"results": [{"ip": "10.99.0.5", "alive": True}]},
    )
    assert r.status_code == 200, r.text
    assert await _ip_count(db_session, "10.99.0.5") == 0


async def test_dead_ip_not_created(client, db_session):
    await _setup_agent_subnet(db_session, scan_enabled=True)
    r = await client.post(
        "/api/v1/scan-agents/report",
        headers={"X-Agent-Key": RAW_KEY},
        json={"results": [{"ip": "10.88.0.77", "alive": False}]},
    )
    assert r.status_code == 200, r.text
    assert await _ip_count(db_session, "10.88.0.77") == 0


async def test_bad_key_rejected(client, db_session):
    await _setup_agent_subnet(db_session, scan_enabled=True)
    r = await client.post(
        "/api/v1/scan-agents/report",
        headers={"X-Agent-Key": "wrong-key"},
        json={"results": [{"ip": "10.88.0.9", "alive": True}]},
    )
    assert r.status_code == 401
