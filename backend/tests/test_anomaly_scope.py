"""異常偵測的範圍與雜訊過濾。

實務問題（正式站台實際畫面）：「未授權 IP」53 筆**全部**是 169.254.x.x —— 那是機器
DHCP 拿不到位址時自己指派的 link-local，是「沒拿到 IP」的徵狀，不是有人偷接東西。
真正該看的東西被整個埋掉，於是這一欄就沒人看了。
"""

from __future__ import annotations

import uuid

import pytest
from app.models.address import IPAddress
from app.models.librenms import ARPEntry
from app.models.section import Section
from app.models.subnet import Subnet
from app.services.anomaly import _is_noise_address, detect_ghost_ips, detect_unauthorized_ips


@pytest.mark.parametrize("ip", [
    "169.254.10.1",      # DHCP 失敗的自我指派 —— 最大宗的雜訊
    "224.0.0.251",       # 多點傳送（mDNS）
    "127.0.0.1",
    "0.0.0.0",
    "not-an-ip",
])
def test_noise_addresses_are_never_unauthorized_devices(ip):
    assert _is_noise_address(ip) is True


@pytest.mark.parametrize("ip", ["192.168.123.50", "10.0.0.7", "172.16.5.1"])
def test_normal_addresses_are_not_noise(ip):
    assert _is_noise_address(ip) is False


async def _subnet(db_session, cidr: str, *, anomaly: bool = True) -> Subnet:
    sec = Section(name=f"anom-{uuid.uuid4().hex[:6]}")
    db_session.add(sec)
    await db_session.flush()
    sub = Subnet(section_id=sec.id, cidr=cidr, anomaly_enabled=anomaly)
    db_session.add(sub)
    await db_session.flush()
    return sub


async def test_link_local_is_not_reported_even_inside_a_defined_subnet(db_session):
    """刻意把 169.254.0.0/16 建成子網路，讓「不在任何子網路」那條規則救不了它 ——
    這樣測的才是雜訊過濾本身，而不是範圍檢查順便擋掉。"""
    await _subnet(db_session, "169.254.0.0/16")
    await _subnet(db_session, "10.61.0.0/24")
    db_session.add(ARPEntry(ip="169.254.99.9", mac="00:11:22:33:44:55"))
    db_session.add(ARPEntry(ip="10.61.0.9", mac="00:11:22:33:44:66"))
    await db_session.flush()

    out = {o["ip"] for o in await detect_unauthorized_ips(db_session)}
    assert "169.254.99.9" not in out, "DHCP 拿不到位址不是「未授權裝置」"
    assert "10.61.0.9" in out


async def test_network_and_broadcast_are_not_reported(db_session):
    """192.168.x.0 與 192.168.x.255 不對應到任何一台機器。"""
    await _subnet(db_session, "10.62.0.0/24")
    for ip in ("10.62.0.0", "10.62.0.255", "10.62.0.7"):
        db_session.add(ARPEntry(ip=ip, mac=f"00:11:22:33:44:{ip.split('.')[-1][-2:].zfill(2)}"))
    await db_session.flush()

    out = {o["ip"] for o in await detect_unauthorized_ips(db_session)}
    assert "10.62.0.0" not in out
    assert "10.62.0.255" not in out
    assert "10.62.0.7" in out


async def test_addresses_outside_every_subnet_are_ignored(db_session):
    """不在任何子網路裡的位址，本來就不是這套 IPAM 在管的東西。"""
    await _subnet(db_session, "10.63.0.0/24")
    db_session.add(ARPEntry(ip="203.0.113.9", mac="00:11:22:33:44:99"))
    await db_session.flush()
    out = {o["ip"] for o in await detect_unauthorized_ips(db_session)}
    assert "203.0.113.9" not in out


async def test_subnet_can_be_excluded_from_detection(db_session):
    on = await _subnet(db_session, "10.64.0.0/24", anomaly=True)
    off = await _subnet(db_session, "10.65.0.0/24", anomaly=False)
    db_session.add(ARPEntry(ip="10.64.0.5", mac="00:11:22:33:55:01"))
    db_session.add(ARPEntry(ip="10.65.0.5", mac="00:11:22:33:55:02"))
    await db_session.flush()

    out = {o["ip"] for o in await detect_unauthorized_ips(db_session)}
    assert "10.64.0.5" in out
    assert "10.65.0.5" not in out, "關掉偵測的子網路還是被報出來了"
    assert on.id and off.id


async def test_excluded_subnet_ips_are_not_ghost_reported(db_session):
    """關掉偵測的網段，其 IP 也不該出現在失聯清單（訪客網段一堆沒人用是正常的）。"""
    off = await _subnet(db_session, "10.66.0.0/24", anomaly=False)
    on = await _subnet(db_session, "10.67.0.0/24", anomaly=True)
    db_session.add(IPAddress(subnet_id=off.id, ip="10.66.0.3", hostname="guest-x"))
    db_session.add(IPAddress(subnet_id=on.id, ip="10.67.0.3", hostname="prod-x"))
    await db_session.flush()

    out = {o["ip"] for o in await detect_ghost_ips(db_session)}
    assert "10.67.0.3" in out
    assert "10.66.0.3" not in out


async def test_large_arp_tables_are_not_sampled(db_session, monkeypatch):
    """以前只任取 2,000 個 ARP 位址、清單再切 200 筆（照字串排序），大站台的未授權位址大多看不到。

    現在差集在資料庫做；超過上限時依最後看到時間取最近的，總數另外回報，畫面才講得出「還有多少」。
    """
    from datetime import UTC, datetime, timedelta

    from app.models.address import IPAddress
    from app.services import anomaly
    from sqlalchemy import insert

    sub = await _subnet(db_session, "10.66.0.0/20")
    now = datetime.now(UTC)
    rows = [{"ip": f"10.66.{i // 250}.{i % 250 + 1}", "mac": f"00:00:5e:00:{i // 256:02x}:{i % 256:02x}",
             "first_seen_at": now, "last_seen_at": now - timedelta(minutes=i)} for i in range(2500)]
    await db_session.execute(insert(ARPEntry), rows)
    # 前 2,400 個已登記；只剩最後 100 個（最舊的那批）沒有
    await db_session.execute(insert(IPAddress), [{"subnet_id": sub.id, "ip": r["ip"]} for r in rows[:2400]])
    await db_session.flush()

    meta: dict = {}
    out = await detect_unauthorized_ips(db_session, meta=meta)
    assert {o["ip"] for o in out} == {r["ip"] for r in rows[2400:]}
    assert meta == {"total": 100, "truncated": False}

    monkeypatch.setattr(anomaly, "MAX_UNAUTHORIZED", 10)
    out = await detect_unauthorized_ips(db_session, meta=meta)
    assert [o["ip"] for o in out] == [r["ip"] for r in rows[2400:2410]]    # 最近看到的在前
    assert meta == {"total": 100, "truncated": True}
