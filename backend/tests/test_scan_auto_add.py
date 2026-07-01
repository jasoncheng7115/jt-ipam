"""掃描代理 push：在「指派且有開掃描」的子網路內，發現未知 IP 會自動建立
（用我們自己的說明文字，非照抄 phpIPAM）；範圍外的 IP / 已死的 IP 不建立。"""

from __future__ import annotations

import hashlib

from app.models.address import IPAddress
from app.models.scan_agent import ScanAgent
from app.models.section import Section
from app.models.subnet import Subnet
from sqlalchemy import func, select

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


async def test_auto_add_with_hostname_records_observation(client, db_session):
    """新建 IP + 帶 rdns 主機名稱：apply_observation 用新 IP 的 FK。
    修正前 session autoflush=False，ipa.id 還是 None → IPHostnameObservation(ip_id=None)
    違反 NOT NULL → 500（客戶回報「掃描代理無法回傳主機名稱」）。"""
    from app.models.ip_hostname import IPHostnameObservation
    await _setup_agent_subnet(db_session, scan_enabled=True)
    r = await client.post(
        "/api/v1/scan-agents/report",
        headers={"X-Agent-Key": RAW_KEY},
        json={"results": [{"ip": "10.88.0.43", "alive": True, "rdns": "host43.lan"}]},
    )
    assert r.status_code == 200, r.text
    ipa = (await db_session.execute(
        select(IPAddress).where(func.host(IPAddress.ip) == "10.88.0.43")
    )).scalar_one()
    obs = (await db_session.execute(
        select(IPHostnameObservation).where(IPHostnameObservation.ip_id == ipa.id)
    )).scalars().all()
    assert any(o.hostname == "host43.lan" for o in obs), "hostname observation not recorded"


async def test_netbios_mdns_recorded_as_distinct_sources(client, db_session):
    """NetBIOS / mDNS 名稱各自記成獨立來源（netbios / mdns），不是混進 scanner，
    這樣使用者才能在來源優先序頁分別排序/停用（v0.4.185）。"""
    from app.models.ip_hostname import IPHostnameObservation
    await _setup_agent_subnet(db_session, scan_enabled=True)
    r = await client.post(
        "/api/v1/scan-agents/report",
        headers={"X-Agent-Key": RAW_KEY},
        json={"results": [{
            "ip": "10.88.0.44", "alive": True,
            "rdns": "host44.lan", "netbios": "DESKTOP-ABC", "mdns": "host44.local",
        }]},
    )
    assert r.status_code == 200, r.text
    ipa = (await db_session.execute(
        select(IPAddress).where(func.host(IPAddress.ip) == "10.88.0.44")
    )).scalar_one()
    obs = {
        o.source: o.hostname
        for o in (await db_session.execute(
            select(IPHostnameObservation).where(IPHostnameObservation.ip_id == ipa.id)
        )).scalars().all()
    }
    assert obs.get("scanner") == "host44.lan"
    assert obs.get("netbios") == "DESKTOP-ABC"
    assert obs.get("mdns") == "host44.local"


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
