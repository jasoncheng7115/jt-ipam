"""「IP 位址」總表的未納管提示（客戶 2026-11-11：新站台開了掃描，IP 位址頁卻是空的，不知道要去哪裡開）。

掃描代理預設不自動收錄（未授權 IP 偵測才有意義），掃到的位址記在 unmanaged_sightings、只在子網路頁出現。
總表要能說出「掃描代理看到 N 個還沒登錄的位址」以及在哪幾個子網路，管理員另外知道有幾台代理開了自動收錄。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models.address import IPAddress
from app.models.scan_agent import ScanAgent
from app.models.section import Section
from app.models.subnet import Subnet
from app.models.unmanaged_sighting import UnmanagedSighting

pytestmark = pytest.mark.anyio


async def _subnet(db, cidr: str) -> Subnet:
    sec = Section(name=f"sec-{uuid.uuid4().hex[:6]}")
    db.add(sec)
    await db.flush()
    sn = Subnet(cidr=cidr, section_id=sec.id)
    db.add(sn)
    await db.flush()
    return sn


async def test_summary_counts_unregistered_scanner_sightings(client, auth_headers, db_session) -> None:
    now = datetime.now(UTC)
    a = await _subnet(db_session, "198.51.100.0/24")
    b = await _subnet(db_session, "203.0.113.0/24")
    agent = ScanAgent(name=f"ag-{uuid.uuid4().hex[:6]}", enroll_key_hash=uuid.uuid4().hex, auto_create_ips=False)
    db_session.add(agent)
    await db_session.flush()
    for sn, ips in ((a, ["198.51.100.10", "198.51.100.11", "198.51.100.12"]), (b, ["203.0.113.5"])):
        for ip in ips:
            db_session.add(UnmanagedSighting(subnet_id=sn.id, ip=ip, source="scanner", source_id=agent.id,
                                             first_seen_at=now, last_seen_at=now))
    # 已經登錄的不算；太久以前看到的也不算（跟指示計同一個 7 天）
    db_session.add(IPAddress(subnet_id=a.id, ip="198.51.100.12"))
    db_session.add(UnmanagedSighting(subnet_id=b.id, ip="203.0.113.6", source="scanner", source_id=agent.id,
                                     first_seen_at=now - timedelta(days=20), last_seen_at=now - timedelta(days=20)))
    await db_session.commit()

    r = await client.get("/api/v1/addresses/unmanaged-summary", headers=auth_headers)
    assert r.status_code == 200, r.text
    d = r.json()
    mine = {s["cidr"]: s["count"] for s in d["subnets"]}
    assert mine.get("198.51.100.0/24") == 2 and mine.get("203.0.113.0/24") == 1
    assert d["count"] >= 3
    assert d["agents_total"] >= 1 and d["agents_auto_create"] is not None


async def test_summary_respects_subnet_visibility(client, db_session) -> None:
    from tests.test_warroom import _hdr, _user
    now = datetime.now(UTC)
    a = await _subnet(db_session, "198.51.100.0/24")
    db_session.add(UnmanagedSighting(subnet_id=a.id, ip="198.51.100.20", source="scanner",
                                     first_seen_at=now, last_seen_at=now))
    await db_session.commit()
    u = await _user(db_session)                         # 沒有任何子網路權限的一般帳號
    r = await client.get("/api/v1/addresses/unmanaged-summary", headers=_hdr(u))
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 0 and r.json()["subnets"] == []
    assert r.json()["agents_total"] is None, "非管理員不給代理設定的資訊"
