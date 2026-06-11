"""回歸測試：prune_stale_arp 刪除過期 ARP（含 device 被刪的孤兒 row），保留新鮮的。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models.librenms import ARPEntry
from app.services.librenms import prune_stale_arp
from sqlalchemy import func, select


async def _add_arp(db_session, *, mac: str, days_old: int, device_id=None) -> None:
    ts = datetime.now(UTC) - timedelta(days=days_old)
    db_session.add(ARPEntry(
        ip="192.0.2.50", mac=mac, instance_id=None, device_id=device_id,
        source="librenms", first_seen_at=ts, last_seen_at=ts,
    ))


async def _count(db_session) -> int:
    return (await db_session.execute(select(func.count()).select_from(ARPEntry))).scalar_one()


async def test_prune_removes_stale_keeps_fresh(db_session):
    await _add_arp(db_session, mac="aa:bb:cc:dd:ee:01", days_old=40)   # 過期
    await _add_arp(db_session, mac="aa:bb:cc:dd:ee:02", days_old=5)    # 新鮮
    await _add_arp(db_session, mac="aa:bb:cc:dd:ee:03", days_old=99, device_id=None)  # 孤兒+過期
    await db_session.commit()

    removed = await prune_stale_arp(db_session, max_age_days=30)
    await db_session.commit()

    assert removed == 2
    remaining = (await db_session.execute(select(ARPEntry.mac))).scalars().all()
    assert remaining == ["aa:bb:cc:dd:ee:02"]


async def test_prune_disabled_when_non_positive(db_session):
    await _add_arp(db_session, mac="aa:bb:cc:dd:ee:04", days_old=999)
    await db_session.commit()
    removed = await prune_stale_arp(db_session, max_age_days=0)
    assert removed == 0
    assert await _count(db_session) == 1
