"""「非法 DHCP 伺服器」的兩個誤報（實機回報）。

1. **永久性誤報**：合法比對是拿「觀測到的那個網段」去查標記。一台路由器同時服務
   多個網段時，`192.168.124.0/24` 裡看到的 DHCPOFFER 來自 `192.168.123.1`，而那個位址
   只存在於 `192.168.123.0/24` —— 在觀測網段裡永遠找不到那筆紀錄，**使用者再怎麼勾
   都消不掉這條警告**。

2. **要人確認系統已經知道的事**：那個位址就是整合中的 OPNsense 防火牆的管理位址。
   我們同步它的租約、規則、NAT，卻在這裡假裝不知道它是誰，還要人去勾一個框。

「非法 DHCP 伺服器」是資安性質的警告，誤報會很快讓人不再認真看它。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from app.services.anomaly import detect_rogue_dhcp


async def _setup(db_session, *, marked=False, with_firewall=False, observe_elsewhere=False):
    from app.models.dhcp_sighting import DHCPSighting
    from app.models.address import IPAddress
    from app.models.section import Section
    from app.models.subnet import Subnet

    sec = Section(name=f"sec-{uuid.uuid4().hex[:6]}")
    db_session.add(sec)
    await db_session.flush()
    home = Subnet(section_id=sec.id, cidr="198.51.100.0/24")
    other = Subnet(section_id=sec.id, cidr="203.0.113.0/24")
    db_session.add_all([home, other])
    await db_session.flush()
    db_session.add(IPAddress(subnet_id=home.id, ip="198.51.100.1",
                             is_dhcp_server=marked))
    if with_firewall:
        from app.models.firewall import OPNsenseFirewall
        db_session.add(OPNsenseFirewall(
            name=f"fw-{uuid.uuid4().hex[:6]}", api_url="https://198.51.100.1",
            api_key_enc=b"x", api_key_nonce=b"x",
            api_secret_enc=b"x", api_secret_nonce=b"x"))
    db_session.add(DHCPSighting(
        subnet_id=(other.id if observe_elsewhere else home.id),
        server_ip="198.51.100.1", via_relay=False,
        first_seen_at=datetime.now(UTC), last_seen_at=datetime.now(UTC)))
    await db_session.flush()
    return home, other


@pytest.mark.anyio
async def test_an_integrated_firewall_serving_another_subnet_is_not_rogue(db_session):
    """一台服務多個網段的路由器，在別的網段被看到時不該變成永遠消不掉的誤報。

    人工標記**維持逐網段**（重疊網段下別人的授權不能套到自己頭上，見
    test_rogue_dhcp.py::test_marking_is_matched_per_subnet）。可以跨網段成立的是
    「這台是我們自己在管的防火牆」—— 那是確定的事實，證據強度不同。
    """
    await _setup(db_session, marked=False, with_firewall=True, observe_elsewhere=True)
    out = await detect_rogue_dhcp(db_session)
    assert not any(o["server_ip"] == "198.51.100.1" for o in out)


@pytest.mark.anyio
async def test_an_integrated_firewall_needs_no_tick(db_session):
    """它的租約、規則、NAT 都是我們同步進來的，卻要人來告訴我們它是 DHCP 伺服器？"""
    await _setup(db_session, marked=False, with_firewall=True)
    out = await detect_rogue_dhcp(db_session)
    assert not any(o["server_ip"] == "198.51.100.1" for o in out)


@pytest.mark.anyio
async def test_an_unknown_server_is_still_reported(db_session):
    """真正該報的還是要報 —— 修誤報不能把偵測本身弄啞。"""
    await _setup(db_session, marked=False, with_firewall=False)
    out = await detect_rogue_dhcp(db_session)
    assert any(o["server_ip"] == "198.51.100.1" for o in out)
