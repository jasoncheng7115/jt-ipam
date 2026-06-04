"""LibreNMS device → jt-ipam Device 連結：
- 命名優先用 sysName（snmp）勝過 hostname（常是 IP）
- model 由 hardware 帶入、type 由 _infer_device_type 推
- primary_ip 對到已連裝置的 IPAddress → 直接沿用、不新建
- 重複呼叫具冪等性
"""

from __future__ import annotations

from app.models.address import IPAddress
from app.models.device import Device
from app.models.librenms import LibreNMSDevice, LibreNMSInstance
from app.models.section import Section
from app.models.subnet import Subnet
from app.services.librenms import link_librenms_device


async def _instance(session) -> LibreNMSInstance:
    inst = LibreNMSInstance(
        name="ln-test", api_url="https://ln.example.com",
        api_token_enc=b"x", api_token_nonce=b"x",
    )
    session.add(inst)
    await session.flush()
    return inst


async def test_prefers_sysname_over_hostname_ip(db_session):
    inst = await _instance(db_session)
    ldev = LibreNMSDevice(
        instance_id=inst.id, legacy_device_id=1,
        hostname="10.0.0.1", sysname="core-sw01",
        hardware="Cisco Catalyst 2960", os="ios",
    )
    db_session.add(ldev)
    await db_session.flush()

    dev_id, created = await link_librenms_device(db_session, ldev)
    assert created is True
    dev = await db_session.get(Device, dev_id)
    assert dev.name == "core-sw01"          # sysName 勝過 hostname(IP)
    assert dev.model == "Cisco Catalyst 2960"
    assert dev.type == "switch"
    assert ldev.jt_ipam_device_id == dev_id  # 回填

    # 冪等：再呼叫一次 → 同一台、created False
    dev_id2, created2 = await link_librenms_device(db_session, ldev)
    assert dev_id2 == dev_id
    assert created2 is False


async def test_links_to_existing_ip_device_without_creating(db_session):
    inst = await _instance(db_session)
    # 既有 Device + IPAddress 10.9.0.1 已連到它
    existing = Device(name="existing-dev", type="server")
    db_session.add(existing)
    await db_session.flush()
    sec = Section(name="ln-sec")
    db_session.add(sec)
    await db_session.flush()
    sub = Subnet(section_id=sec.id, cidr="10.9.0.0/24")
    db_session.add(sub)
    await db_session.flush()
    addr = IPAddress(subnet_id=sub.id, ip="10.9.0.1", device_id=existing.id)
    db_session.add(addr)
    await db_session.flush()

    ldev = LibreNMSDevice(
        instance_id=inst.id, legacy_device_id=2,
        hostname="whatever", sysname="ignored", primary_ip="10.9.0.1",
    )
    db_session.add(ldev)
    await db_session.flush()

    dev_id, created = await link_librenms_device(db_session, ldev)
    assert dev_id == existing.id   # 沿用既有裝置
    assert created is False
    assert ldev.jt_ipam_device_id == existing.id


async def test_no_create_when_create_false(db_session):
    inst = await _instance(db_session)
    ldev = LibreNMSDevice(
        instance_id=inst.id, legacy_device_id=3, sysname="ghost-only",
    )
    db_session.add(ldev)
    await db_session.flush()
    dev_id, created = await link_librenms_device(db_session, ldev, create=False)
    assert dev_id is None
    assert created is False
