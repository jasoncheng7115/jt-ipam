"""非法 DHCP 伺服器偵測。

這條規則守的是一個實務上很痛的問題：有人在辦公室插了台家用路由器，它開始發 IP，
於是一批機器拿到錯的閘道。IPAM 平常看不出來 —— 那台機器可能連 ARP 都沒出現過。

三件容易做錯、而且錯了不會有任何徵兆的事：
1. 合法與否要**查詢時比對**，不是寫死在觀測記錄裡（事後改標記，舊記錄就不一致）
2. 同一個 IP 字串在不同子網路是不同機器 —— 比對必須帶上 subnet_id
3. 經由 relay 轉送的回應不能算非法（那台伺服器本來就不在這個網段）
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.models.address import IPAddress
from app.models.dhcp_sighting import DHCPSighting
from app.models.section import Section
from app.models.subnet import Subnet
from app.services.anomaly import detect_rogue_dhcp


async def _subnet(db_session, cidr: str) -> Subnet:
    sec = Section(name=f"dhcp-sec-{uuid.uuid4().hex[:6]}")
    db_session.add(sec)
    await db_session.flush()
    sub = Subnet(section_id=sec.id, cidr=cidr)
    db_session.add(sub)
    await db_session.flush()
    return sub


def _sighting(subnet_id, ip: str, **kw) -> DHCPSighting:
    now = datetime.now(UTC)
    return DHCPSighting(subnet_id=subnet_id, server_ip=ip,
                        first_seen_at=now, last_seen_at=now, **kw)


async def test_unmarked_dhcp_server_is_reported(db_session):
    sub = await _subnet(db_session, "10.50.1.0/24")
    db_session.add(_sighting(sub.id, "10.50.1.99", offered_ip="10.50.1.150"))
    await db_session.flush()

    out = await detect_rogue_dhcp(db_session)
    assert any(o["server_ip"] == "10.50.1.99" for o in out)


async def test_marked_dhcp_server_is_not_reported(db_session):
    sub = await _subnet(db_session, "10.50.2.0/24")
    db_session.add(IPAddress(subnet_id=sub.id, ip="10.50.2.1", is_dhcp_server=True))
    db_session.add(_sighting(sub.id, "10.50.2.1"))
    await db_session.flush()

    out = await detect_rogue_dhcp(db_session)
    assert not any(o["server_ip"] == "10.50.2.1" for o in out)


async def test_marking_is_matched_per_subnet(db_session):
    """同一個 IP 字串在別的子網路被標記為合法，不該讓這個網段的也變合法。

    重疊網段（多客戶共用 192.168.123.0/24）在這個專案是常態，只比對 IP 字串
    等於把別人的授權套到自己頭上。
    """
    a = await _subnet(db_session, "192.168.77.0/24")
    b = await _subnet(db_session, "192.168.78.0/24")
    db_session.add(IPAddress(subnet_id=a.id, ip="192.168.77.1", is_dhcp_server=True))
    db_session.add(_sighting(b.id, "192.168.77.1"))     # 在 B 網段看到同一個位址
    await db_session.flush()

    out = await detect_rogue_dhcp(db_session)
    assert any(o["server_ip"] == "192.168.77.1" and o["subnet_id"] == str(b.id)
               for o in out), "別的網段的標記被誤用成這個網段的授權"


async def test_relayed_offers_are_not_flagged(db_session):
    """經由 DHCP relay 轉送的回應，伺服器本來就不在這個網段 —— 判它非法是必然誤報。"""
    sub = await _subnet(db_session, "10.50.3.0/24")
    db_session.add(_sighting(sub.id, "10.9.9.9", via_relay=True))
    await db_session.flush()

    out = await detect_rogue_dhcp(db_session)
    assert not any(o["server_ip"] == "10.9.9.9" for o in out)


async def test_old_sightings_age_out(db_session):
    """插一次就拔掉的路由器不該永遠掛在異常清單上。"""
    sub = await _subnet(db_session, "10.50.4.0/24")
    old = datetime.now(UTC) - timedelta(days=30)
    db_session.add(DHCPSighting(subnet_id=sub.id, server_ip="10.50.4.66",
                                first_seen_at=old, last_seen_at=old))
    await db_session.flush()

    out = await detect_rogue_dhcp(db_session, within_days=7)
    assert not any(o["server_ip"] == "10.50.4.66" for o in out)


def test_probe_is_off_by_default():
    """DHCP 偵測會在網段上廣播 —— 該不該做由管理員逐個子網路決定，不是預設就開。"""
    from app.core.scan_probes import PROBES
    assert PROBES["dhcp"]["default_on"] is False


# ── 三層探測模型：漏掉中間那層 = 勾了也不會跑 ────────────────────────────────
# 實際發生：子網路勾了「DHCP 伺服器偵測」、代理也回報做得到，但**代理本身沒啟用**
# 這項探測，於是伺服器在 poll 時就把它濾掉了。畫面上一切正常，探測一整天沒跑過，
# dhcp_sightings 一筆都沒有。

def test_poll_intersects_agent_enabled_with_subnet_request():
    """poll 回給代理的探測清單 = 代理啟用 ∩ 子網路要跑。"""
    from app.core.scan_probes import normalize_probes

    agent_enabled = set(normalize_probes(["icmp", "arp", "os"]))     # 代理沒開 dhcp
    subnet_wants = normalize_probes(["icmp", "arp", "dhcp"])
    out = [p for p in subnet_wants if p in agent_enabled]
    assert "dhcp" not in out, "這正是實際發生的事：子網路勾了但代理沒開 → 被濾掉"
    assert out == ["icmp", "arp"]


def test_probe_catalogue_exposes_dhcp_for_the_agent_ceiling():
    """dhcp 必須是可以被列進代理允許清單的合法探測，否則使用者根本開不了。"""
    from app.core.scan_probes import VALID_PROBES, normalize_probes
    assert "dhcp" in VALID_PROBES
    assert normalize_probes(["icmp", "dhcp"]) == ["icmp", "dhcp"]


async def test_list_tags_use_the_same_window_as_the_detector(db_session):
    """清單的紅色「非法 DHCP」標記以前沒有時間界線：私接的路由器拔掉一個月了還是紅的，
    異常偵測卻早就不報了（2026-09-26 稽核）。兩邊用同一個時間窗。"""
    from types import SimpleNamespace

    from app.api.v1.endpoints.addresses import _enrich_special_flags
    from app.services.anomaly import ROGUE_DHCP_WINDOW_DAYS

    sub = await _subnet(db_session, "198.51.100.0/24")
    old_ip = IPAddress(subnet_id=sub.id, ip="198.51.100.2")
    new_ip = IPAddress(subnet_id=sub.id, ip="198.51.100.3")
    db_session.add_all([old_ip, new_ip])
    long_ago = datetime.now(UTC) - timedelta(days=ROGUE_DHCP_WINDOW_DAYS + 1)
    db_session.add(DHCPSighting(subnet_id=sub.id, server_ip="198.51.100.2",
                                first_seen_at=long_ago, last_seen_at=long_ago))
    db_session.add(_sighting(sub.id, "198.51.100.3"))
    await db_session.flush()
    items = [SimpleNamespace(), SimpleNamespace()]
    await _enrich_special_flags(db_session, items, [old_ip, new_ip])
    assert items[0].dhcp_observed_at is None
    assert items[1].dhcp_observed_at is not None
