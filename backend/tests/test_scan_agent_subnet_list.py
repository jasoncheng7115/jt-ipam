"""掃描代理頁點子網路數字會彈出它負責哪些子網路（使用者 2026-10-22）：清單要帶 CIDR 與說明，不只是 id。"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.models.scan_agent import ScanAgent
from app.models.section import Section
from app.models.subnet import Subnet

pytestmark = pytest.mark.anyio


async def test_agent_subnets_include_cidr_and_description(client, auth_headers, db_session) -> None:
    sec = Section(name=f"s-{uuid.uuid4().hex[:6]}")
    agent = ScanAgent(name=f"a-{uuid.uuid4().hex[:6]}", enroll_key_hash=uuid.uuid4().hex * 2, enabled=True,
                      last_seen_at=datetime.now(UTC))
    db_session.add_all([sec, agent])
    await db_session.flush()
    db_session.add_all([
        Subnet(section_id=sec.id, cidr="198.51.100.0/24", description="Office", scan_agent_id=agent.id, scan_enabled=True),
        Subnet(section_id=sec.id, cidr="192.0.2.0/24", description=None, scan_agent_id=agent.id, scan_enabled=False),
        Subnet(section_id=sec.id, cidr="203.0.113.0/24", description="Other"),
    ])
    await db_session.commit()
    r = await client.get(f"/api/v1/scan-agents/{agent.id}/subnets", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["subnet_ids"]) == 2                                  # 舊用法不變
    rows = body["subnets"]
    assert [x["cidr"] for x in rows] == ["192.0.2.0/24", "198.51.100.0/24"]   # 依 CIDR 排序
    office = next(x for x in rows if x["cidr"] == "198.51.100.0/24")
    assert office["description"] == "Office" and office["scan_enabled"] is True


async def test_agent_can_be_renamed_and_names_stay_unique(client, auth_headers, db_session) -> None:
    """掃描代理可以改名（使用者 10-22：「scanner 名稱不能改正常嗎」）；名稱只是顯示用，代理靠金鑰辨識。"""
    a = ScanAgent(name=f"a-{uuid.uuid4().hex[:6]}", enroll_key_hash=uuid.uuid4().hex * 2, enabled=True)
    b = ScanAgent(name=f"b-{uuid.uuid4().hex[:6]}", enroll_key_hash=uuid.uuid4().hex * 2, enabled=True)
    db_session.add_all([a, b])
    await db_session.commit()
    r = await client.patch(f"/api/v1/scan-agents/{a.id}", headers=auth_headers, json={"name": "site-a-scanner"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "site-a-scanner"
    dup = await client.patch(f"/api/v1/scan-agents/{b.id}", headers=auth_headers, json={"name": "site-a-scanner"})
    assert dup.status_code == 409 and dup.json()["detail"]["code"] == "scan_agent_name_taken"


async def test_description_can_be_cleared(client, auth_headers, db_session) -> None:
    """說明全部刪掉再儲存要真的清空（使用者 10-22：刪掉按儲存，進來又出現了）。"""
    a = ScanAgent(name=f"c-{uuid.uuid4().hex[:6]}", description="host-x 上的 LXC",
                  enroll_key_hash=uuid.uuid4().hex * 2, enabled=True)
    db_session.add(a)
    await db_session.commit()
    r = await client.patch(f"/api/v1/scan-agents/{a.id}", headers=auth_headers, json={"description": ""})
    assert r.status_code == 200
    assert not r.json()["description"]


async def test_assigning_a_subnet_held_by_another_agent_needs_confirmation(client, auth_headers, db_session) -> None:
    """客戶 2026-11-05：代理編輯畫面勾子網路會默默把別台代理的子網路搶過來（高雄的網段被移到本機代理，
    本機到不了 → 整段判離線）。指派到別台代理手上的子網路要先確認；確認後才搬，稽核寫出從哪台搬來。"""
    from app.models.audit import AuditLog
    from sqlalchemy import select
    sec = Section(name=f"s-{uuid.uuid4().hex[:6]}")
    local = ScanAgent(name=f"local-{uuid.uuid4().hex[:4]}", enroll_key_hash=uuid.uuid4().hex * 2, enabled=True)
    remote = ScanAgent(name=f"remote-{uuid.uuid4().hex[:4]}", enroll_key_hash=uuid.uuid4().hex * 2, enabled=True)
    db_session.add_all([sec, local, remote])
    await db_session.flush()
    held = Subnet(section_id=sec.id, cidr="198.51.100.0/24", scan_agent_id=remote.id, scan_enabled=True)
    free = Subnet(section_id=sec.id, cidr="192.0.2.0/24")
    db_session.add_all([held, free])
    await db_session.commit()
    held_id, free_id, local_id, remote_id, remote_name = held.id, free.id, local.id, remote.id, remote.name

    url = f"/api/v1/scan-agents/{local_id}/subnets"
    r = await client.put(url, headers=auth_headers, json={"subnet_ids": [str(held_id), str(free_id)]})
    assert r.status_code == 409, r.text
    d = r.json()["detail"]
    assert d["code"] == "scan_agent_subnets_taken"
    assert d["params"]["count"] == 1 and "198.51.100.0/24" in d["params"]["subnets"] and remote_name in d["params"]["subnets"]
    db_session.expire_all()
    assert (await db_session.get(Subnet, held_id)).scan_agent_id == remote_id      # 什麼都沒動
    assert (await db_session.get(Subnet, free_id)).scan_agent_id is None

    r = await client.put(url, headers=auth_headers,
                         json={"subnet_ids": [str(held_id), str(free_id)], "move_from_other_agents": True})
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert (await db_session.get(Subnet, held_id)).scan_agent_id == local_id
    log = (await db_session.execute(select(AuditLog).where(AuditLog.object_id == str(local_id))
                                    .order_by(AuditLog.ts.desc()))).scalars().first()
    assert log.diff["moved"] == [{"subnet": "198.51.100.0/24", "from_agent": remote_name}]

    # 只動自己的子網路（或沒人管的）不用確認
    r = await client.put(url, headers=auth_headers, json={"subnet_ids": [str(free_id)]})
    assert r.status_code == 200, r.text
