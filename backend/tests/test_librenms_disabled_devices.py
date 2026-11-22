"""LibreNMS 停用的裝置不採用（使用者 2026-10-13：「停用的不要抓」）。

事故：一個位址被命名成一台 UniFi 基地台的名稱、也關聯到它，其實那個位址是一台電視在用。
那台基地台在 LibreNMS 已停用（`disabled=1`，最後輪詢是一年多前），但同步完全不看這個欄位：
- 照樣把它的名稱報給主要 IP、狀態停在最後的值（status=1 時一直算「上線（LibreNMS）」）
- 它凍結的 ARP 表每輪被當成剛看到（LibreNMS 不會清停用裝置的舊 ARP）→ 假上線、假衝突
- 照樣建 IP、建裝置、抓 FDB／連接埠／VLAN／LLDP

規則：停用的裝置留在鏡像（狀態「disabled」，畫面看得到），但所有衍生資料都不採用，
它既有的 ARP／FDB 也一起清掉。`ignore=1` 只是不告警、仍在輪詢，照常採用。
已經建好的 jt-ipam 裝置與 IP 關聯是使用者的資料，不動。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.models.address import IPAddress
from app.models.device import Device
from app.models.ip_hostname import IPHostnameObservation
from app.models.librenms import ARPEntry, FDBEntry, LibreNMSDevice, LibreNMSInstance
from app.models.section import Section
from app.models.subnet import Subnet
from app.services import librenms as lib


async def _setup(db, **inst_kw):  # type: ignore[no-untyped-def]
    inst = LibreNMSInstance(name=f"lnms-{uuid.uuid4().hex[:6]}", api_url="https://librenms.example",
                            api_token_enc=b"x", api_token_nonce=b"y", **inst_kw)
    sec = Section(name=f"s-{uuid.uuid4().hex[:6]}")
    db.add_all([inst, sec])
    await db.flush()
    sub = Subnet(section_id=sec.id, cidr="198.51.100.0/24")
    db.add(sub)
    await db.flush()
    return inst, sub


def _api(monkeypatch, routes: dict, called: list | None = None) -> None:
    async def fake(_inst, path, *, timeout=30.0):
        if called is not None:
            called.append(path)
        for prefix, val in routes.items():
            if path.startswith(prefix):
                return val
        return {}
    monkeypatch.setattr(lib, "_api_get", fake)


def _dev(i: int, ip: str, *, disabled: int = 0, ignore: int = 0, status: int = 1) -> dict:
    return {"device_id": i, "hostname": f"ap{i}.example.com", "sysName": f"ap{i}", "ip": ip,
            "status": status, "disabled": disabled, "ignore": ignore, "os": "unifi", "hardware": "UAP"}


async def _count(db, model, *where) -> int:  # type: ignore[no-untyped-def]
    return (await db.execute(select(func.count()).select_from(model).where(*where))).scalar_one()


async def test_disabled_device_gives_no_name_no_liveness_and_is_marked(db_session, monkeypatch) -> None:
    inst, sub = await _setup(db_session)
    ip = IPAddress(subnet_id=sub.id, ip="198.51.100.190", state="used")
    db_session.add(ip)
    await db_session.flush()
    # 先是啟用中：名稱與上線證據都寫進來
    _api(monkeypatch, {"/api/v0/devices": {"devices": [_dev(65, "198.51.100.190")]}})
    await lib.sync_devices(db_session, inst)
    await db_session.flush()
    assert ip.last_seen_librenms is not None
    assert await _count(db_session, IPHostnameObservation, IPHostnameObservation.ip_id == ip.id,
                        IPHostnameObservation.source == "librenms") == 1

    # 在 LibreNMS 停用（status 停在最後的 1）
    ip.last_seen_librenms = None
    _api(monkeypatch, {"/api/v0/devices": {"devices": [_dev(65, "198.51.100.190", disabled=1, status=1)]}})
    await lib.sync_devices(db_session, inst)
    await db_session.flush()
    mirror = (await db_session.execute(select(LibreNMSDevice).where(
        LibreNMSDevice.instance_id == inst.id))).scalar_one()
    assert mirror.status == "disabled", "鏡像留著、標成停用（畫面看得到）"
    assert ip.last_seen_librenms is None, "停用的裝置不是上線證據"
    assert await _count(db_session, IPHostnameObservation, IPHostnameObservation.ip_id == ip.id,
                        IPHostnameObservation.source == "librenms") == 0, "它報過的名稱要收回"


async def test_disabled_device_creates_no_ip_and_no_device(db_session, monkeypatch) -> None:
    inst, _sub = await _setup(db_session, auto_create_ips=True, auto_add_devices=True)
    _api(monkeypatch, {"/api/v0/devices": {"devices": [
        _dev(1, "198.51.100.11"), _dev(2, "198.51.100.12", disabled=1)]}})
    await lib.sync_devices(db_session, inst)
    await db_session.flush()
    ips = set((await db_session.execute(select(func.host(IPAddress.ip)))).scalars().all())
    assert "198.51.100.11" in ips
    assert "198.51.100.12" not in ips, "停用裝置的主要 IP 不自動建立"
    names = set((await db_session.execute(select(Device.name))).scalars().all())
    assert any("ap1" in n for n in names)
    assert not any("ap2" in n for n in names), "停用裝置不自動建 jt-ipam 裝置"


async def test_ignored_device_is_still_used(db_session, monkeypatch) -> None:
    """ignore＝只是不告警，LibreNMS 仍在輪詢，資料是新的：照常採用。"""
    inst, sub = await _setup(db_session)
    ip = IPAddress(subnet_id=sub.id, ip="198.51.100.20", state="used")
    db_session.add(ip)
    await db_session.flush()
    _api(monkeypatch, {"/api/v0/devices": {"devices": [_dev(3, "198.51.100.20", ignore=1)]}})
    await lib.sync_devices(db_session, inst)
    await db_session.flush()
    assert ip.last_seen_librenms is not None
    mirror = (await db_session.execute(select(LibreNMSDevice).where(
        LibreNMSDevice.instance_id == inst.id))).scalar_one()
    assert mirror.status == "up"


async def test_frozen_arp_and_fdb_of_a_disabled_device_are_dropped(db_session, monkeypatch) -> None:
    inst, _sub = await _setup(db_session)
    _api(monkeypatch, {"/api/v0/devices": {"devices": [_dev(1, "198.51.100.1"), _dev(2, "198.51.100.2")]}})
    await lib.sync_devices(db_session, inst)
    await db_session.flush()
    devs = {d.legacy_device_id: d for d in (await db_session.execute(select(LibreNMSDevice).where(
        LibreNMSDevice.instance_id == inst.id))).scalars().all()}
    old = datetime.now(UTC) - timedelta(hours=2)
    for legacy, mac in ((1, "02:00:00:00:01:01"), (2, "02:00:00:00:02:02")):
        db_session.add(ARPEntry(ip=f"198.51.100.{100 + legacy}", mac=mac, device_id=devs[legacy].id,
                                instance_id=inst.id, last_seen_at=old))
        db_session.add(FDBEntry(mac=mac, device_id=devs[legacy].id, instance_id=inst.id, port_name="ge-0/0/1"))
    await db_session.flush()

    _api(monkeypatch, {"/api/v0/devices": {"devices": [_dev(1, "198.51.100.1"), _dev(2, "198.51.100.2", disabled=1)]}})
    await lib.sync_devices(db_session, inst)
    await db_session.flush()
    assert await _count(db_session, ARPEntry, ARPEntry.device_id == devs[2].id) == 0
    assert await _count(db_session, FDBEntry, FDBEntry.device_id == devs[2].id) == 0
    assert await _count(db_session, ARPEntry, ARPEntry.device_id == devs[1].id) == 1, "啟用中的不動"

    # 下一輪 ARP：LibreNMS 仍回傳停用裝置凍結的 ARP → 不收、不當成剛看到
    _api(monkeypatch, {"/api/v0/resources/ip/arp": {"arp": [
        {"device_id": 1, "ipv4_address": "198.51.100.101", "mac_address": "02:00:00:00:01:01"},
        {"device_id": 2, "ipv4_address": "198.51.100.102", "mac_address": "02:00:00:00:02:02"},
    ]}})
    await lib.sync_arp(db_session, inst)
    await db_session.flush()
    assert await _count(db_session, ARPEntry, ARPEntry.device_id == devs[2].id) == 0
    assert await _count(db_session, ARPEntry, ARPEntry.device_id == devs[1].id) == 1


async def test_per_device_calls_skip_disabled_devices(db_session, monkeypatch) -> None:
    """FDB、連接埠、VLAN 這些逐台呼叫的 API，停用的裝置不再打（資料是凍結的，也白花時間）。"""
    inst, _sub = await _setup(db_session)
    _api(monkeypatch, {"/api/v0/devices": {"devices": [_dev(1, "198.51.100.1"), _dev(2, "198.51.100.2", disabled=1)]}})
    await lib.sync_devices(db_session, inst)
    await db_session.flush()
    called: list[str] = []
    _api(monkeypatch, {}, called)
    await lib.sync_fdb(db_session, inst)
    await lib.sync_vlans(db_session, inst)
    per_device = [p for p in called if p.startswith("/api/v0/devices/")]
    assert any(p.startswith("/api/v0/devices/1/") for p in per_device), per_device
    assert not any(p.startswith("/api/v0/devices/2/") for p in per_device), per_device


async def test_lldp_links_of_a_disabled_device_are_dropped(db_session, monkeypatch) -> None:
    from app.models.librenms import LibreNMSLink
    inst, _sub = await _setup(db_session, sync_links=True)
    _api(monkeypatch, {"/api/v0/devices": {"devices": [_dev(1, "198.51.100.1"), _dev(2, "198.51.100.2")]}})
    await lib.sync_devices(db_session, inst)
    links = {"links": [{"id": 11, "local_device_id": 1, "local_port": "ge-0/0/1", "remote_hostname": "sw-b"},
                       {"id": 12, "local_device_id": 2, "local_port": "ge-0/0/2", "remote_hostname": "sw-c"}]}
    _api(monkeypatch, {"/api/v0/resources/links": links})
    await lib.sync_links(db_session, inst)
    await db_session.flush()
    assert await _count(db_session, LibreNMSLink, LibreNMSLink.instance_id == inst.id) == 2

    _api(monkeypatch, {"/api/v0/devices": {"devices": [_dev(1, "198.51.100.1"), _dev(2, "198.51.100.2", disabled=1)]}})
    await lib.sync_devices(db_session, inst)
    _api(monkeypatch, {"/api/v0/resources/links": links})
    await lib.sync_links(db_session, inst)
    await db_session.flush()
    left = set((await db_session.execute(select(LibreNMSLink.legacy_link_id).where(
        LibreNMSLink.instance_id == inst.id))).scalars().all())
    assert left == {11}


async def test_frozen_arp_of_a_down_device_is_not_refreshed(db_session, monkeypatch) -> None:
    """正式環境 2026-10-20：一台主機重灌後換了位址，LibreNMS 裡舊的那筆一直 down（最後輪詢 23 天前、
    沒有停用），但 ARP API 還是回它關機前的 ARP 表；每輪被蓋成「剛看到」→ 假衝突（閘道位址出現兩個 MAC）、
    舊位址看起來還有 ARP。down 的設備：不收新的、不更新既有的（讓它照保存期限自然過期；回到 up 就恢復）。"""
    inst, _sub = await _setup(db_session)
    _api(monkeypatch, {"/api/v0/devices": {"devices": [_dev(1, "198.51.100.1"), _dev(2, "198.51.100.2", status=0)]}})
    await lib.sync_devices(db_session, inst)
    await db_session.flush()
    devs = {d.legacy_device_id: d for d in (await db_session.execute(select(LibreNMSDevice).where(
        LibreNMSDevice.instance_id == inst.id))).scalars().all()}
    old = datetime.now(UTC) - timedelta(days=3)
    db_session.add(ARPEntry(ip="198.51.100.102", mac="02:00:00:00:02:02", device_id=devs[2].id,
                            instance_id=inst.id, source="librenms", last_seen_at=old))
    await db_session.flush()
    _api(monkeypatch, {"/api/v0/resources/ip/arp": {"arp": [
        {"device_id": 1, "ipv4_address": "198.51.100.101", "mac_address": "02:00:00:00:01:01"},
        {"device_id": 2, "ipv4_address": "198.51.100.102", "mac_address": "02:00:00:00:02:02"},
        {"device_id": 2, "ipv4_address": "198.51.100.103", "mac_address": "02:00:00:00:02:03"},
    ]}})
    await lib.sync_arp(db_session, inst)
    await db_session.flush()
    rows = (await db_session.execute(select(ARPEntry).where(ARPEntry.device_id == devs[2].id))).scalars().all()
    assert len(rows) == 1, "down 的設備不收新的 ARP"
    assert rows[0].last_seen_at <= old + timedelta(seconds=1), "也不把凍結的那筆蓋成剛看到"
    assert await _count(db_session, ARPEntry, ARPEntry.device_id == devs[1].id) == 1

    # 回到 up：照常收
    _api(monkeypatch, {"/api/v0/devices": {"devices": [_dev(1, "198.51.100.1"), _dev(2, "198.51.100.2")]}})
    await lib.sync_devices(db_session, inst)
    _api(monkeypatch, {"/api/v0/resources/ip/arp": {"arp": [
        {"device_id": 2, "ipv4_address": "198.51.100.103", "mac_address": "02:00:00:00:02:03"}]}})
    await lib.sync_arp(db_session, inst)
    await db_session.flush()
    assert await _count(db_session, ARPEntry, ARPEntry.device_id == devs[2].id) == 2


async def test_per_device_calls_skip_down_devices(db_session, monkeypatch) -> None:
    """MAC 表同理：down 的設備 LibreNMS 回的是最後一次輪詢的凍結資料。"""
    inst, _sub = await _setup(db_session)
    _api(monkeypatch, {"/api/v0/devices": {"devices": [_dev(1, "198.51.100.1"), _dev(2, "198.51.100.2", status=0)]}})
    await lib.sync_devices(db_session, inst)
    await db_session.flush()
    called: list[str] = []
    _api(monkeypatch, {}, called)
    await lib.sync_fdb(db_session, inst)
    per_device = [p for p in called if p.startswith("/api/v0/devices/")]
    assert any(p.startswith("/api/v0/devices/1/") for p in per_device), per_device
    assert not any(p.startswith("/api/v0/devices/2/") for p in per_device), per_device
