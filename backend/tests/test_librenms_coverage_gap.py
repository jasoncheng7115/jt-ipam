"""LibreNMS 的「未監控的位址」：IPAM 有主機名稱、但這台 LibreNMS 沒在監控的 IP。

使用者 11-25：未納管比對的「監控系統」只有 Zabbix、Checkmk，「監控系統 librenms 也是」。
跟 Zabbix / Checkmk 同一個模式；被監控的判定看三件事（任一成立）：
- IP 等於 LibreNMS 裝置的主要 IP；
- LibreNMS 的 hostname 直接填的就是這個 IP（很常見，LibreNMS 用 IP 加裝置時 primary_ip 可能還沒有值）；
- IP 所屬的 jt-ipam 裝置已對應到這台 LibreNMS 的裝置（同一台機器的其他介面 IP 也算有監控）。
"""
from __future__ import annotations

import uuid

import pytest

from app.models.address import IPAddress
from app.models.device import Device
from app.models.librenms import LibreNMSDevice, LibreNMSInstance
from app.models.section import Section
from app.models.subnet import Subnet
from app.services.librenms import coverage_gap


async def _inst(session, name: str = "") -> LibreNMSInstance:
    inst = LibreNMSInstance(name=name or f"ln-{uuid.uuid4().hex[:6]}", api_url="https://ln.example.com",
                            api_token_enc=b"x", api_token_nonce=b"y")
    session.add(inst)
    await session.flush()
    return inst


async def _fixture(session):
    sec = Section(name=f"s-{uuid.uuid4().hex[:6]}")
    session.add(sec)
    await session.flush()
    a = Subnet(section_id=sec.id, cidr="198.51.100.0/24")
    b = Subnet(section_id=sec.id, cidr="203.0.113.0/24")
    srv = Device(name=f"srv-{uuid.uuid4().hex[:4]}")
    session.add_all([a, b, srv])
    await session.flush()
    ips = {
        "by_primary": IPAddress(subnet_id=a.id, ip="198.51.100.10", hostname="by-primary", state="used"),
        "by_hostname": IPAddress(subnet_id=a.id, ip="198.51.100.11", hostname="by-hostname", state="used"),
        "second_nic": IPAddress(subnet_id=a.id, ip="198.51.100.12", hostname="srv-backup", state="used",
                                device_id=srv.id),
        "missing": IPAddress(subnet_id=a.id, ip="198.51.100.13", hostname="not-monitored", state="used"),
        "no_name": IPAddress(subnet_id=a.id, ip="198.51.100.14", state="used"),
        "other_net": IPAddress(subnet_id=b.id, ip="203.0.113.10", hostname="other-net", state="used"),
    }
    session.add_all(ips.values())
    inst = await _inst(session)
    session.add_all([
        LibreNMSDevice(instance_id=inst.id, legacy_device_id=1, hostname="sw1.example.net",
                       primary_ip="198.51.100.10"),
        LibreNMSDevice(instance_id=inst.id, legacy_device_id=2, hostname="198.51.100.11"),
        LibreNMSDevice(instance_id=inst.id, legacy_device_id=3, hostname="srv.example.net",
                       primary_ip="192.0.2.99", jt_ipam_device_id=srv.id),
    ])
    await session.flush()
    return inst, a, b, ips


@pytest.mark.anyio
async def test_gap_lists_only_named_ips_that_are_not_monitored(db_session) -> None:
    inst, a, _b, _ips = await _fixture(db_session)
    rows = await coverage_gap(db_session, instance_id=inst.id, subnet_ids=[a.id])
    assert [r["hostname"] for r in rows] == ["not-monitored"]
    assert rows[0]["ip"] == "198.51.100.13"


@pytest.mark.anyio
async def test_gap_is_scoped_and_empty_scope_is_empty(db_session) -> None:
    """限定網段才不會用全站資料回答某網段的問題（v0.5.194 的教訓）；空範圍回空，不退化成全域。"""
    inst, _a, b, _ips = await _fixture(db_session)
    assert [r["hostname"] for r in await coverage_gap(db_session, instance_id=inst.id, subnet_ids=[b.id])] \
        == ["other-net"]
    assert await coverage_gap(db_session, instance_id=inst.id, subnet_ids=[]) == []


@pytest.mark.anyio
async def test_another_instance_does_not_count_as_monitoring(db_session) -> None:
    inst, a, _b, _ips = await _fixture(db_session)
    other = await _inst(db_session)
    rows = await coverage_gap(db_session, instance_id=other.id, subnet_ids=[a.id])
    assert {r["hostname"] for r in rows} == {"by-primary", "by-hostname", "srv-backup", "not-monitored"}


@pytest.mark.anyio
async def test_api_returns_gap_with_scope(client, auth_headers, db_session) -> None:
    inst, a, _b, _ips = await _fixture(db_session)
    await db_session.commit()
    r = await client.get(f"/api/v1/librenms/instances/{inst.id}/coverage-gap", headers=auth_headers,
                         params={"subnet_ids": [str(a.id)]})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1 and body["scope"] == "subnets"
    assert body["items"][0]["hostname"] == "not-monitored"
    r = await client.get(f"/api/v1/librenms/instances/{uuid.uuid4()}/coverage-gap", headers=auth_headers)
    assert r.status_code == 404
