"""子網路重疊：預設禁止重疊（無 VRF），但 allow_overlap=True 明確放行
（例如同 CIDR 但單位 / 地點不同）。"""

from __future__ import annotations

import pytest

from app.models.section import Section
from app.models.subnet import Subnet
from app.services.subnet import SubnetOverlap, assert_no_overlap


async def _mk_subnet(session, cidr: str) -> Subnet:
    sec = Section(name=f"ov-{cidr}")
    session.add(sec)
    await session.flush()
    sub = Subnet(section_id=sec.id, cidr=cidr)
    session.add(sub)
    await session.flush()
    return sub


async def test_overlap_rejected_without_vrf(db_session):
    await _mk_subnet(db_session, "192.168.50.0/24")
    with pytest.raises(SubnetOverlap):
        await assert_no_overlap(db_session, cidr="192.168.50.0/24", vrf_id=None)


async def test_allow_overlap_bypasses(db_session):
    await _mk_subnet(db_session, "192.168.51.0/24")
    # 明確允許重疊 → 不應丟出
    await assert_no_overlap(
        db_session, cidr="192.168.51.0/24", vrf_id=None, allow_overlap=True,
    )
