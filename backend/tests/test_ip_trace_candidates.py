"""從 IP 詳情進追線（交接包 physical-v1.2 CABLING.md 畫面第 5 點，2026-11-21）。

沿 IP 既有的關聯找可以追線的連接埠，不從 IP 在不在線推定實體線路：
- 裝置上 MAC 跟這個 IP 相同的網卡（mac_match）
- FDB 學到的交換器埠（switch_port「交換器 / 埠」對得到 jt-ipam 的裝置與埠）（switch_port）
- 只有裝置關聯、找不到候選 → 回那台裝置的埠清單讓人選
- 什麼都沒有 → 空的（畫面顯示「尚未對應裝置或連接埠」）
- 看不到的裝置不列
"""
from __future__ import annotations

import pytest
from app.models.address import IPAddress
from app.models.device import Device
from app.models.physical import DevicePort
from app.models.section import Section
from app.models.subnet import Subnet


async def _base(db):  # type: ignore[no-untyped-def]
    sec = Section(name="s")
    db.add(sec)
    await db.flush()
    sub = Subnet(cidr="198.51.100.0/24", section_id=sec.id)
    srv, sw = Device(name="pve01"), Device(name="sw-core01")
    db.add_all([sub, srv, sw])
    await db.flush()
    p = {"eno1": DevicePort(device_id=srv.id, name="eno1", mac_address="aa:bb:cc:00:00:01"),
         "eno2": DevicePort(device_id=srv.id, name="eno2", mac_address="aa:bb:cc:00:00:02"),
         "Gi1/0/7": DevicePort(device_id=sw.id, name="Gi1/0/7")}
    db.add_all(p.values())
    await db.flush()
    return sub, srv, sw, p


@pytest.mark.anyio
async def test_mac_and_switch_port_candidates(client, auth_headers, db_session) -> None:
    sub, srv, _sw, p = await _base(db_session)
    ip = IPAddress(subnet_id=sub.id, ip="198.51.100.10", device_id=srv.id, mac="AA:BB:CC:00:00:02",
                   switch_port="sw-core01 / Gi1/0/7", state="active")
    db_session.add(ip)
    await db_session.commit()
    r = (await client.get(f"/api/v1/addresses/{ip.id}/trace-candidates", headers=auth_headers)).json()
    got = [(c["device"], c["port"], c["reason"]) for c in r["candidates"]]
    assert got == [("pve01", "eno2", "mac_match"), ("sw-core01", "Gi1/0/7", "switch_port")]
    assert r["device"]["name"] == "pve01"


@pytest.mark.anyio
async def test_device_only_lists_its_ports(client, auth_headers, db_session) -> None:
    sub, srv, _sw, _p = await _base(db_session)
    ip = IPAddress(subnet_id=sub.id, ip="198.51.100.11", device_id=srv.id, state="active")
    db_session.add(ip)
    await db_session.commit()
    r = (await client.get(f"/api/v1/addresses/{ip.id}/trace-candidates", headers=auth_headers)).json()
    assert r["candidates"] == []
    assert [x["port"] for x in r["device_ports"]] == ["eno1", "eno2"]


@pytest.mark.anyio
async def test_nothing_linked(client, auth_headers, db_session) -> None:
    sub, _srv, _sw, _p = await _base(db_session)
    ip = IPAddress(subnet_id=sub.id, ip="198.51.100.12", state="active", switch_port="unknown-sw / 5")
    db_session.add(ip)
    await db_session.commit()
    r = (await client.get(f"/api/v1/addresses/{ip.id}/trace-candidates", headers=auth_headers)).json()
    assert r == {"device": None, "candidates": [], "device_ports": []}
