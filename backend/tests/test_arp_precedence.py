"""ARP/MAC 來源順序：停用的來源不得覆寫 MAC。"""

from __future__ import annotations

from app.models.address import IPAddress
from app.services.arp_precedence import (
    consider_mac,
    get_arp_disabled,
    set_arp_precedence,
)


async def test_disabled_arp_source_does_not_overwrite(db_session):
    await set_arp_precedence(
        db_session,
        order=["manual", "scanner", "opnsense", "librenms"],
        disabled=["scanner"],
    )
    assert "scanner" in await get_arp_disabled(db_session)

    ip = IPAddress(ip="10.9.9.9")
    # 停用的來源（scanner）→ 不寫入
    changed = await consider_mac(db_session, ip=ip, mac="aa:bb:cc:dd:ee:01", source="scanner")
    assert changed is False
    assert ip.mac is None

    # 啟用的來源（opnsense）→ 可寫入
    changed2 = await consider_mac(db_session, ip=ip, mac="aa:bb:cc:dd:ee:02", source="opnsense")
    assert changed2 is True
    assert ip.mac == "aa:bb:cc:dd:ee:02"


async def test_manual_cannot_be_disabled(db_session):
    _, disabled = await set_arp_precedence(
        db_session, order=["manual", "scanner"], disabled=["manual", "scanner"],
    )
    assert "manual" not in disabled   # manual 永遠不可停用
    assert "scanner" in disabled
