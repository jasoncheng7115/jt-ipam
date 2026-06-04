"""名稱來源分離：DNS（權威伺服器）/ AdGuard / Wazuh 是各自獨立的來源，
依名稱順序決定 IP 主機名稱。"""

from __future__ import annotations

from app.models.address import IPAddress
from app.models.ip_hostname import HOSTNAME_SOURCES
from app.models.section import Section
from app.models.subnet import Subnet
from app.services.hostname import apply_observation


def test_dns_adguard_wazuh_are_distinct_sources():
    for s in ("dns", "adguard", "wazuh"):
        assert s in HOSTNAME_SOURCES
    # dns 與 adguard 必須是不同 key（行為不同，要分開）
    assert "dns" != "adguard"


async def test_precedence_order_covers_all_sources(db_session):
    """順序清單必須涵蓋所有來源（曾有 wazuh/adguard 被漏掉、不出現在設定頁的 bug）。"""
    from app.services.hostname import get_precedence
    order = await get_precedence(db_session)
    for s in HOSTNAME_SOURCES:
        assert s in order, f"{s} 不在 hostname 來源順序中"


async def _mk_ip(session) -> IPAddress:
    sec = Section(name="hsrc-sec")
    session.add(sec)
    await session.flush()
    sub = Subnet(section_id=sec.id, cidr="10.77.0.0/24")
    session.add(sub)
    await session.flush()
    ip = IPAddress(subnet_id=sub.id, ip="10.77.0.5")
    session.add(ip)
    await session.flush()
    return ip


async def test_separate_observations_and_precedence(db_session):
    ip = await _mk_ip(db_session)
    # 三個分開的來源各記一筆不同的主機名稱
    await apply_observation(db_session, ip=ip, source="adguard", hostname="from-adguard")
    await apply_observation(db_session, ip=ip, source="wazuh", hostname="from-wazuh")
    await apply_observation(db_session, ip=ip, source="dns", hostname="from-dns")

    # 預設順序 dns 在 adguard/wazuh 之前 → 有效名取 dns
    assert ip.hostname == "from-dns"

    # 觀測各自獨立保存（不互相覆蓋）
    from app.services.hostname import _observations_for
    obs = await _observations_for(db_session, ip.id)
    assert obs.get("dns") == "from-dns"
    assert obs.get("adguard") == "from-adguard"
    assert obs.get("wazuh") == "from-wazuh"
