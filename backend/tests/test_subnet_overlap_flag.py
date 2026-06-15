"""回歸測試：has_overlapping_subnets 與 /subnets/overlaps/exists 端點。"""

from __future__ import annotations

import uuid

from app.models.section import Section
from app.models.subnet import Subnet
from app.services.subnet import has_overlapping_subnets


async def _section(db_session) -> Section:
    sec = Section(name=f"sec-{uuid.uuid4().hex[:6]}")
    db_session.add(sec)
    await db_session.flush()
    return sec


async def test_no_overlap_when_distinct(db_session):
    sec = await _section(db_session)
    db_session.add(Subnet(section_id=sec.id, cidr="10.10.0.0/24"))
    db_session.add(Subnet(section_id=sec.id, cidr="10.20.0.0/24"))
    await db_session.commit()
    assert await has_overlapping_subnets(db_session) is False


async def test_overlap_detected_same_cidr(db_session):
    sec = await _section(db_session)
    # 同 VRF（皆 NULL）下兩個重疊 CIDR → 重疊網段
    db_session.add(Subnet(section_id=sec.id, cidr="192.0.2.0/24"))
    db_session.add(Subnet(section_id=sec.id, cidr="192.0.2.0/25"))
    await db_session.commit()
    assert await has_overlapping_subnets(db_session) is True


async def test_endpoint_admin(client, auth_headers, db_session):
    sec = await _section(db_session)
    db_session.add(Subnet(section_id=sec.id, cidr="172.31.0.0/24"))
    db_session.add(Subnet(section_id=sec.id, cidr="172.31.0.0/24"))
    await db_session.commit()
    r = await client.get("/api/v1/subnets/overlaps/exists", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["has_overlap"] is True
