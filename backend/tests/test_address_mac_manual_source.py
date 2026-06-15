"""人工編輯 MAC 必須標記 mac_source='manual'，否則下次掃描/ARP 同步會把它蓋掉。

與 hostname 同屬「多來源優先序」欄位：hostname 走 apply_observation(source=manual)，
MAC 則靠 mac_source 決定 ARP 優先序。PATCH 端點若只 setattr ip.mac 而不設 source，
人工填的 MAC 就不受保護。本測試鎖住此行為。
"""

from __future__ import annotations

from app.models.address import IPAddress
from app.models.section import Section
from app.models.subnet import Subnet
from app.services.arp_precedence import consider_mac


async def _mk_addr(session, *, mac=None, mac_source=None):
    sec = Section(name="mac-sec")
    session.add(sec)
    await session.flush()
    sub = Subnet(section_id=sec.id, cidr="10.8.0.0/24")
    session.add(sub)
    await session.flush()
    addr = IPAddress(subnet_id=sub.id, ip="10.8.0.5", mac=mac, mac_source=mac_source)
    session.add(addr)
    await session.flush()
    return addr


async def test_manual_mac_edit_sets_source_and_resists_scan(client, db_session, auth_headers):
    # 既有 MAC 來自掃描 → 之後人工改 MAC
    addr = await _mk_addr(db_session, mac="aa:bb:cc:00:00:01", mac_source="scanner")
    await db_session.commit()

    r = await client.patch(
        f"/api/v1/addresses/{addr.id}",
        headers=auth_headers,
        json={"mac": "aa:bb:cc:99:99:99"},
    )
    assert r.status_code == 200, r.text

    await db_session.refresh(addr)
    assert str(addr.mac) == "aa:bb:cc:99:99:99"
    assert addr.mac_source == "manual"   # ← 關鍵：人工編輯標記為 manual

    # 之後掃描代理回報不同 MAC：manual 優先序最高 → 不得覆寫
    changed = await consider_mac(db_session, ip=addr, mac="aa:bb:cc:11:11:11", source="scanner")
    assert changed is False
    assert str(addr.mac) == "aa:bb:cc:99:99:99"


async def test_clearing_mac_clears_source(client, db_session, auth_headers):
    addr = await _mk_addr(db_session, mac="aa:bb:cc:00:00:02", mac_source="scanner")
    await db_session.commit()

    r = await client.patch(
        f"/api/v1/addresses/{addr.id}", headers=auth_headers, json={"mac": None},
    )
    assert r.status_code == 200, r.text
    await db_session.refresh(addr)
    assert addr.mac is None
    assert addr.mac_source is None
