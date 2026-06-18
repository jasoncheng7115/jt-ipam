"""回歸測試：LibreNMS sync_devices 的 auto_create_ips。

開啟時，落在「既有且符合 scope」子網路內的裝置主 IP 會自動建成 IPAddress
（discovery_source='librenms'）；關閉時只 stamp 既有 IP、不建立；落在任何既有
子網路外的裝置主 IP 不建立。
"""

from __future__ import annotations

import ipaddress

from app.models.address import IPAddress
from app.models.librenms import LibreNMSInstance
from app.models.section import Section
from app.models.subnet import Subnet
from app.services import librenms as lib
from sqlalchemy import func, select


async def _setup(session, *, auto_create_ips: bool) -> LibreNMSInstance:
    sec = Section(name="lnms-sec")
    session.add(sec)
    await session.flush()
    session.add(Subnet(section_id=sec.id, cidr="10.77.0.0/24"))
    inst = LibreNMSInstance(
        name="lnms-test", api_url="https://librenms.example",
        api_token_enc=b"x", api_token_nonce=b"y",
        auto_create_ips=auto_create_ips,
    )
    session.add(inst)
    await session.commit()
    return inst


def _fake_devices(monkeypatch, devices: list[dict]) -> None:
    async def _fake_api_get(instance, path, *, timeout=30.0):  # noqa: ANN001
        return {"devices": devices}
    monkeypatch.setattr(lib, "_api_get", _fake_api_get)


async def _host_count(session, ip: str) -> int:
    return (await session.execute(
        select(func.count()).select_from(IPAddress)
        .where(func.host(IPAddress.ip) == ip)
    )).scalar_one()


async def test_auto_create_makes_device_primary_ip(db_session, monkeypatch):
    inst = await _setup(db_session, auto_create_ips=True)
    _fake_devices(monkeypatch, [
        {"device_id": 1, "ip": "10.77.0.5", "status": 1, "hostname": "sw1"},
    ])
    await lib.sync_devices(db_session, inst)
    await db_session.commit()

    row = (await db_session.execute(
        select(IPAddress).where(func.host(IPAddress.ip) == "10.77.0.5")
    )).scalar_one()
    assert row.discovery_source == "librenms"
    assert row.last_seen_librenms is not None  # status up → stamped


async def test_auto_create_off_does_not_create(db_session, monkeypatch):
    inst = await _setup(db_session, auto_create_ips=False)
    _fake_devices(monkeypatch, [
        {"device_id": 1, "ip": "10.77.0.5", "status": 1, "hostname": "sw1"},
    ])
    await lib.sync_devices(db_session, inst)
    await db_session.commit()
    assert await _host_count(db_session, "10.77.0.5") == 0


async def test_auto_create_skips_ip_outside_any_subnet(db_session, monkeypatch):
    inst = await _setup(db_session, auto_create_ips=True)
    # 10.99.x 不在任何既有子網路 → 不建立
    _fake_devices(monkeypatch, [
        {"device_id": 1, "ip": "10.99.0.5", "status": 1, "hostname": "sw1"},
    ])
    await lib.sync_devices(db_session, inst)
    await db_session.commit()
    assert await _host_count(db_session, "10.99.0.5") == 0


def test_pick_subnet_longest_prefix_and_ambiguity():
    """_pick_subnet_for_ip：巢狀取最精確、重疊歧義不猜、無容器回 None（純函式）。"""
    from app.services.librenms import _pick_subnet_for_ip
    n8 = ipaddress.ip_network("10.0.0.0/8")
    n24 = ipaddress.ip_network("10.1.1.0/24")
    # 巢狀 → 取最長首碼 /24
    assert _pick_subnet_for_ip([(n8, "A"), (n24, "B")], ipaddress.ip_address("10.1.1.5")) == "B"
    # 無任何容器 → None
    assert _pick_subnet_for_ip([(n8, "A")], ipaddress.ip_address("192.168.1.1")) is None
    # 重疊網段、相同最長首碼 → 歧義 → None（不建錯單位）
    dup = ipaddress.ip_network("192.168.1.0/24")
    assert _pick_subnet_for_ip([(dup, "X"), (dup, "Y")], ipaddress.ip_address("192.168.1.9")) is None
