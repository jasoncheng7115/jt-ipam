"""Wazuh agent 與 IP 的對映要看「還算不算數」，不能只比對 IP 位址。

實機案例（v0.5.141）：`192.168.123.187` 顯示 OS 是 macOS、來源 Wazuh，但那台其實是
Proxmox 上的 Linux VM（MAC 開頭 bc:24:11），PVE 裡根本沒有 macOS 的 VM。

追下去：Wazuh 的 agent 015 是 `laptop-a1.local`（一台 MacBook），Wazuh 上記錄的 IP
還是 192.168.123.187，狀態 disconnected。那是 DHCP 位址，早就被回收給別台機器用了。
我們只用 IP 比對，就把 Mac 的 OS 貼到了 Linux VM 上。

判準：**agent 已經失聯，而這個 IP 在那之後還被偵測到活著 → 現在佔用這個 IP 的不是它。**
（只是關機的機器不受影響：沒有更新的存活證據，就仍然採用它的資料。）
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.models.address import IPAddress
from app.models.section import Section
from app.models.subnet import Subnet
from app.models.wazuh import WazuhAgent, WazuhInstance
from app.services.wazuh import agent_represents_ip

NOW = datetime.now(UTC)


async def _ip_and_agent(db_session, *, status, keep_alive, seen):
    sec = Section(name=f"s-{uuid.uuid4().hex[:6]}")
    db_session.add(sec)
    await db_session.flush()
    sn = Subnet(section_id=sec.id, cidr="198.51.100.0/24")
    db_session.add(sn)
    await db_session.flush()
    ipa = IPAddress(subnet_id=sn.id, ip="198.51.100.7", last_seen_librenms=seen)
    inst = WazuhInstance(name=f"wz-{uuid.uuid4().hex[:6]}", api_url="https://x",
                         api_user="u", api_password_enc=b"x", api_password_nonce=b"y")
    db_session.add_all([ipa, inst])
    await db_session.flush()
    # ip 與 jt_ipam_address_id 一定要設：少了它們，os_precedence 與曝險偵測根本比對不到
    # 這個 agent，測試會「因為找不到」而通過 —— 把修正拿掉也一樣綠，等於什麼都沒驗到。
    wa = WazuhAgent(instance_id=inst.id, agent_id="015", name="laptop-a1.local",
                    ip="198.51.100.7", jt_ipam_address_id=ipa.id,
                    os_platform="darwin", status=status, last_keep_alive=keep_alive)
    db_session.add(wa)
    await db_session.flush()
    return ipa, wa


@pytest.mark.anyio
async def test_disconnected_agent_loses_to_newer_liveness(db_session):
    """agent 半年前就失聯，這個 IP 今天還活著 → 現在佔用的不是它，不能拿它的 OS。"""
    ipa, wa = await _ip_and_agent(
        db_session, status="disconnected",
        keep_alive=NOW - timedelta(days=180), seen=NOW - timedelta(minutes=5))
    assert agent_represents_ip(wa, ipa) is False


@pytest.mark.anyio
async def test_active_agent_always_counts(db_session):
    ipa, wa = await _ip_and_agent(
        db_session, status="active",
        keep_alive=NOW - timedelta(minutes=1), seen=NOW - timedelta(minutes=1))
    assert agent_represents_ip(wa, ipa) is True


@pytest.mark.anyio
async def test_powered_off_machine_still_counts(db_session):
    """只是關機：agent 失聯，但這個 IP 也沒有更新的存活證據 → 仍然採用它的資料。

    少了這條，任何關機的機器都會立刻失去 OS 資訊 —— 修一個錯誤不該製造另一個。
    """
    ipa, wa = await _ip_and_agent(
        db_session, status="disconnected",
        keep_alive=NOW - timedelta(days=3), seen=None)
    assert agent_represents_ip(wa, ipa) is True


@pytest.mark.anyio
async def test_agent_gone_for_more_than_a_month_no_longer_counts(db_session):
    """斷線超過 30 天（與 OCS「多久算過期」的預設相同）就不再代表這個 IP（使用者 2026-10-13：「停用的不要抓」）。

    以前沒有上限：一台失聯好幾年的代理，只要那個 IP 沒有別的存活證據，就一直替它命名、提供 OS。
    30 天內的「只是關機」照舊採用（上一支測試）。
    """
    ipa, wa = await _ip_and_agent(
        db_session, status="disconnected",
        keep_alive=NOW - timedelta(days=31), seen=None)
    assert agent_represents_ip(wa, ipa) is False


@pytest.mark.anyio
async def test_stale_agent_does_not_supply_os(db_session):
    """端到端：失聯 agent 不該再提供 OS 候選值（這就是畫面上那個 macOS 的來源）。"""
    from app.services.os_precedence import _candidates
    ipa, _ = await _ip_and_agent(
        db_session, status="disconnected",
        keep_alive=NOW - timedelta(days=180), seen=NOW - timedelta(minutes=5))
    await db_session.commit()
    cand = await _candidates(db_session, ipa)
    assert "wazuh" not in cand


@pytest.mark.anyio
async def test_stale_agent_is_not_monitoring_coverage(db_session):
    """對外曝險的「有監控涵蓋」也要照同一個判準 —— 失聯的 agent 沒有在看任何東西。"""
    from app.models.nat import NATTranslation
    from app.services.anomaly import detect_external_exposure
    ipa, _ = await _ip_and_agent(
        db_session, status="disconnected",
        keep_alive=NOW - timedelta(days=180), seen=NOW - timedelta(minutes=5))
    ipa.effective_status = "online"
    db_session.add(NATTranslation(name="pf", type="port_forward", dst_ip_id=ipa.id,
                                  dst_port=443, protocol="tcp"))
    await db_session.commit()

    out = await detect_external_exposure(db_session)
    hits = [x for x in out if x["ip"] == "198.51.100.7"]
    assert hits and hits[0]["kind"] == "exposed_unmonitored"
    assert hits[0]["monitored"] is False


async def test_os_comes_from_the_agent_that_still_represents_the_ip(db_session) -> None:
    """同一個 IP 有兩個 agent（舊的失聯、新的連著）：以前任意取一個，取到舊的就被判不代表，
    連著的那個反而沒用上 → OS 空白（2026-09-26 稽核）。"""
    import uuid as _uuid
    from datetime import UTC, datetime, timedelta

    from app.models.address import IPAddress
    from app.models.section import Section
    from app.models.subnet import Subnet
    from app.models.wazuh import WazuhAgent, WazuhInstance
    from app.services.os_precedence import _candidates

    sec = Section(name=f"s-{_uuid.uuid4().hex[:6]}")
    db_session.add(sec)
    await db_session.flush()
    sub = Subnet(section_id=sec.id, cidr="198.51.100.0/24")
    db_session.add(sub)
    await db_session.flush()
    now = datetime.now(UTC)
    ip = IPAddress(subnet_id=sub.id, ip="198.51.100.60", last_seen_scanner=now)
    db_session.add(ip)
    inst = WazuhInstance(name=f"wz-{_uuid.uuid4().hex[:6]}", api_url="https://wazuh.example",
                         api_user="u", api_password_enc=b"x", api_password_nonce=b"x")
    db_session.add(inst)
    await db_session.flush()
    db_session.add(WazuhAgent(instance_id=inst.id, agent_id="010", ip="198.51.100.60",
                              status="disconnected", os_platform="darwin",
                              last_keep_alive=now - timedelta(days=30)))
    db_session.add(WazuhAgent(instance_id=inst.id, agent_id="011", ip="198.51.100.60",
                              status="active", os_platform="ubuntu", os_version="24.04",
                              last_keep_alive=now))
    await db_session.flush()
    assert (await _candidates(db_session, ip)).get("wazuh") == "ubuntu 24.04"
