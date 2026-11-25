"""佈線自動推斷（使用者 11-25：「有明確可以判斷的就自動 不要全都人工設」）。

明確的才自動接：
- LLDP / CDP 鄰居（SNMP 收集、LibreNMS、MikroTik）：對方自己宣告接在哪個埠，兩端的埠都對得到
- 交換器的埠只看到一個 MAC，這個 MAC 只屬於一台裝置、也只出現在這台交換器的這一個埠
- 埠上看到的全是同一台虛擬化主機和它上面的 VM
不明確的（交換器之間只靠 MAC 表推出來的、MAC 對到好幾個埠）只列成建議。
人工的線一律不動；人刪掉的自動線不再建回來；證據消失太久的自動線撤掉。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.address import IPAddress
from app.models.device import Device
from app.models.librenms import FDBEntry, LibreNMSDevice, LibreNMSInstance, LibreNMSLink
from app.models.physical import Cable, CableSuggestion, CableTermination, DevicePort
from app.models.section import Section
from app.models.subnet import Subnet
from app.services import cable_inference as ci

NOW = datetime.now(UTC)


def _mac(n: int) -> str:
    return "02:00:00:00:%02x:%02x" % (n // 256, n % 256)


async def _dev(s, name: str, *ports: tuple[str, str | None], type_: str = "server") -> Device:
    d = Device(name=f"{name}-{uuid.uuid4().hex[:4]}", type=type_)
    s.add(d)
    await s.flush()
    for pname, mac in ports:
        s.add(DevicePort(device_id=d.id, name=pname, mac_address=mac))
    await s.flush()
    return d


async def _port(s, dev: Device, name: str) -> DevicePort:
    return (await s.execute(select(DevicePort).where(DevicePort.device_id == dev.id,
                                                     DevicePort.name == name))).scalar_one()


def _fdb(s, sw: Device, port: str, mac: str, *, age: timedelta = timedelta(minutes=5)) -> None:
    s.add(FDBEntry(mac=mac, port_name=port, source="snmp", switch_device_id=sw.id,
                   first_seen_at=NOW - timedelta(days=3), last_seen_at=NOW - age))


async def _cables(s) -> list[dict]:
    out = []
    for c in (await s.execute(select(Cable))).scalars().all():
        ends = {t.side: t.object_id for t in (await s.execute(
            select(CableTermination).where(CableTermination.cable_id == c.id))).scalars().all()}
        out.append({"id": c.id, "source": c.source, "status": c.status, "ends": {ends.get("A"), ends.get("B")},
                    "kind": (c.evidence or {}).get("kind"), "verified_at": c.verified_at})
    return out


async def _manual(s, a: DevicePort, b: DevicePort) -> Cable:
    c = Cable(status="connected", source="manual", label="manual")
    s.add(c)
    await s.flush()
    s.add_all([CableTermination(cable_id=c.id, side="A", object_type="device_port", object_id=a.id),
               CableTermination(cable_id=c.id, side="B", object_type="device_port", object_id=b.id)])
    await s.flush()
    return c


async def _switch(s, n_ports: int = 4) -> Device:
    return await _dev(s, "sw", *[(f"Gi1/0/{i}", None) for i in range(1, n_ports + 1)], type_="switch")


# ─────────────────── 單一 MAC ───────────────────
@pytest.mark.anyio
async def test_single_mac_port_is_cabled_automatically(db_session) -> None:
    sw = await _switch(db_session)
    srv = await _dev(db_session, "srv", ("eno1", _mac(1)))
    _fdb(db_session, sw, "Gi1/0/1", _mac(1))
    await db_session.flush()

    st = await ci.run(db_session, now=NOW)
    cables = await _cables(db_session)
    assert st["created"] == 1
    assert len(cables) == 1
    c = cables[0]
    assert c["source"] == "auto" and c["status"] == "connected" and c["kind"] == "fdb_single_mac"
    assert c["ends"] == {(await _port(db_session, sw, "Gi1/0/1")).id, (await _port(db_session, srv, "eno1")).id}

    # 再跑一次：不重複建立，只更新確認時間
    st2 = await ci.run(db_session, now=NOW + timedelta(minutes=5))
    assert st2["created"] == 0 and st2["refreshed"] == 1
    assert len(await _cables(db_session)) == 1


@pytest.mark.anyio
async def test_bridge_sharing_the_nic_mac_picks_the_physical_port(db_session) -> None:
    sw = await _switch(db_session)
    srv = await _dev(db_session, "pve", ("vmbr0", _mac(2)), ("eno1", _mac(2)))
    _fdb(db_session, sw, "Gi1/0/2", _mac(2))
    await db_session.flush()
    await ci.run(db_session, now=NOW)
    (c,) = await _cables(db_session)
    assert (await _port(db_session, srv, "eno1")).id in c["ends"]


@pytest.mark.anyio
async def test_bond_members_sharing_a_mac_are_not_guessed(db_session) -> None:
    sw = await _switch(db_session)
    await _dev(db_session, "nas", ("eth0", _mac(3)), ("eth1", _mac(3)))
    _fdb(db_session, sw, "Gi1/0/3", _mac(3))
    await db_session.flush()
    await ci.run(db_session, now=NOW)
    assert await _cables(db_session) == []


@pytest.mark.anyio
async def test_port_with_two_unrelated_devices_is_not_cabled(db_session) -> None:
    sw = await _switch(db_session)
    await _dev(db_session, "a", ("eth0", _mac(4)))
    await _dev(db_session, "b", ("eth0", _mac(5)))
    _fdb(db_session, sw, "Gi1/0/1", _mac(4))
    _fdb(db_session, sw, "Gi1/0/1", _mac(5))
    await db_session.flush()
    await ci.run(db_session, now=NOW)
    assert await _cables(db_session) == []


@pytest.mark.anyio
async def test_mac_seen_on_two_ports_of_the_same_switch_is_not_cabled(db_session) -> None:
    sw = await _switch(db_session)
    await _dev(db_session, "srv", ("eno1", _mac(6)))
    _fdb(db_session, sw, "Gi1/0/1", _mac(6))
    _fdb(db_session, sw, "Gi1/0/2", _mac(6))
    await db_session.flush()
    await ci.run(db_session, now=NOW)
    assert await _cables(db_session) == []


@pytest.mark.anyio
async def test_stale_fdb_entries_do_not_count(db_session) -> None:
    sw = await _switch(db_session)
    await _dev(db_session, "srv", ("eno1", _mac(7)))
    _fdb(db_session, sw, "Gi1/0/1", _mac(7), age=timedelta(days=3))
    await db_session.flush()
    await ci.run(db_session, now=NOW)
    assert await _cables(db_session) == []


@pytest.mark.anyio
async def test_device_known_only_by_ip_gets_a_nic_port(db_session) -> None:
    """裝置沒有網卡埠的資料、但 IP 記錄（MAC＋裝置）講得出這個 MAC 是它的：替它建一個網卡埠再接線。"""
    sw = await _switch(db_session)
    srv = await _dev(db_session, "vm-less")
    sec = Section(name=f"s-{uuid.uuid4().hex[:4]}")
    db_session.add(sec)
    await db_session.flush()
    sub = Subnet(section_id=sec.id, cidr="198.51.100.0/24")
    db_session.add(sub)
    await db_session.flush()
    db_session.add(IPAddress(subnet_id=sub.id, ip="198.51.100.20", mac=_mac(8), device_id=srv.id, state="used"))
    _fdb(db_session, sw, "Gi1/0/4", _mac(8))
    await db_session.flush()

    st = await ci.run(db_session, now=NOW)
    assert st["ports_created"] == 1
    ports = (await db_session.execute(select(DevicePort).where(DevicePort.device_id == srv.id))).scalars().all()
    assert len(ports) == 1
    p = ports[0]
    assert p.source_origin == "auto:cabling" and p.mac_address == _mac(8)
    (c,) = await _cables(db_session)
    assert p.id in c["ends"]


# ─────────────────── 人工優先、衝突、撤掉 ───────────────────
@pytest.mark.anyio
async def test_manual_cable_is_never_touched_and_conflict_is_suggested(db_session) -> None:
    sw = await _switch(db_session)
    srv = await _dev(db_session, "srv", ("eno1", _mac(9)))
    other = await _dev(db_session, "other", ("eth0", None))
    manual = await _manual(db_session, await _port(db_session, sw, "Gi1/0/1"), await _port(db_session, other, "eth0"))
    _fdb(db_session, sw, "Gi1/0/1", _mac(9))
    await db_session.flush()

    st = await ci.run(db_session, now=NOW)
    assert st["created"] == 0 and st["conflicts"] == 1
    (c,) = await _cables(db_session)
    assert c["id"] == manual.id and c["source"] == "manual"
    sug = (await db_session.execute(select(CableSuggestion))).scalars().all()
    assert len(sug) == 1 and sug[0].reason == "conflict_manual" and sug[0].status == "open"
    assert {sug[0].a_port_id, sug[0].b_port_id} == {(await _port(db_session, sw, "Gi1/0/1")).id,
                                                    (await _port(db_session, srv, "eno1")).id}


@pytest.mark.anyio
async def test_manual_cable_matching_the_evidence_is_left_alone(db_session) -> None:
    sw = await _switch(db_session)
    srv = await _dev(db_session, "srv", ("eno1", _mac(10)))
    await _manual(db_session, await _port(db_session, sw, "Gi1/0/1"), await _port(db_session, srv, "eno1"))
    _fdb(db_session, sw, "Gi1/0/1", _mac(10))
    await db_session.flush()
    st = await ci.run(db_session, now=NOW)
    assert st["created"] == 0 and st["conflicts"] == 0
    assert [c["source"] for c in await _cables(db_session)] == ["manual"]
    assert (await db_session.execute(select(CableSuggestion))).scalars().all() == []


@pytest.mark.anyio
async def test_device_moved_to_another_port_moves_the_auto_cable(db_session) -> None:
    sw = await _switch(db_session)
    srv = await _dev(db_session, "srv", ("eno1", _mac(11)))
    _fdb(db_session, sw, "Gi1/0/1", _mac(11))
    await db_session.flush()
    await ci.run(db_session, now=NOW)
    # 換到 Gi1/0/2：舊的那筆已經不是「目前」
    for e in (await db_session.execute(select(FDBEntry))).scalars().all():
        e.last_seen_at = NOW - timedelta(days=2)
    _fdb(db_session, sw, "Gi1/0/2", _mac(11))
    await db_session.flush()
    st = await ci.run(db_session, now=NOW + timedelta(minutes=5))
    assert st["created"] == 1 and st["retired"] == 1
    (c,) = await _cables(db_session)
    assert c["ends"] == {(await _port(db_session, sw, "Gi1/0/2")).id, (await _port(db_session, srv, "eno1")).id}


@pytest.mark.anyio
async def test_auto_cable_without_evidence_is_kept_for_a_while_then_retired(db_session) -> None:
    sw = await _switch(db_session)
    await _dev(db_session, "srv", ("eno1", _mac(12)))
    _fdb(db_session, sw, "Gi1/0/1", _mac(12))
    await db_session.flush()
    await ci.run(db_session, now=NOW)
    for e in (await db_session.execute(select(FDBEntry))).scalars().all():
        await db_session.delete(e)
    await db_session.flush()
    # 關機幾天：證據沒了但線不會馬上消失
    st = await ci.run(db_session, now=NOW + timedelta(days=3))
    assert st["retired"] == 0 and len(await _cables(db_session)) == 1
    st = await ci.run(db_session, now=NOW + timedelta(days=ci.STALE_DAYS + 1))
    assert st["retired"] == 1 and await _cables(db_session) == []


@pytest.mark.anyio
async def test_auto_created_nic_port_goes_away_with_its_cable(db_session) -> None:
    sw = await _switch(db_session)
    srv = await _dev(db_session, "srv")
    sec = Section(name=f"s-{uuid.uuid4().hex[:4]}")
    db_session.add(sec)
    await db_session.flush()
    sub = Subnet(section_id=sec.id, cidr="203.0.113.0/24")
    db_session.add(sub)
    await db_session.flush()
    db_session.add(IPAddress(subnet_id=sub.id, ip="203.0.113.20", mac=_mac(13), device_id=srv.id, state="used"))
    _fdb(db_session, sw, "Gi1/0/1", _mac(13))
    await db_session.flush()
    await ci.run(db_session, now=NOW)
    for e in (await db_session.execute(select(FDBEntry))).scalars().all():
        await db_session.delete(e)
    await db_session.flush()
    await ci.run(db_session, now=NOW + timedelta(days=ci.STALE_DAYS + 1))
    assert (await db_session.execute(select(DevicePort).where(DevicePort.device_id == srv.id))).scalars().all() == []


@pytest.mark.anyio
async def test_dismissed_pair_is_not_created_again(db_session) -> None:
    sw = await _switch(db_session)
    srv = await _dev(db_session, "srv", ("eno1", _mac(14)))
    _fdb(db_session, sw, "Gi1/0/1", _mac(14))
    await db_session.flush()
    await ci.run(db_session, now=NOW)
    (c,) = await _cables(db_session)
    await ci.dismiss_cable(db_session, c["id"], user_id=None)
    assert await _cables(db_session) == []
    st = await ci.run(db_session, now=NOW + timedelta(minutes=5))
    assert st["created"] == 0 and await _cables(db_session) == []
    sug = (await db_session.execute(select(CableSuggestion))).scalar_one()
    assert sug.status == "dismissed"
    assert {sug.a_port_id, sug.b_port_id} == {(await _port(db_session, sw, "Gi1/0/1")).id,
                                              (await _port(db_session, srv, "eno1")).id}


# ─────────────────── 鄰居（LLDP / CDP）───────────────────
@pytest.mark.anyio
async def test_lldp_neighbour_from_snmp_collector_is_cabled(db_session) -> None:
    from app.models.snmp import SnmpCredential, SnmpNeighbor, SnmpTarget
    from app.services import snmp as snmp_svc
    sw1 = await _dev(db_session, "core", ("Gi1/0/48", None), type_="switch")
    sw2 = await _dev(db_session, "edge", ("Gi0/1", None), type_="switch")
    cred = SnmpCredential(id=uuid.uuid4(), name=f"c-{uuid.uuid4().hex[:4]}", version="v2c")
    snmp_svc.set_secret(cred, "community", "x")
    db_session.add(cred)
    await db_session.flush()
    t = SnmpTarget(device_id=sw1.id, credential_id=cred.id)
    db_session.add(t)
    await db_session.flush()
    db_session.add(SnmpNeighbor(target_id=t.id, device_id=sw1.id, protocol="lldp", local_port="Gi1/0/48",
                                remote_sysname=f"{sw2.name}.example.net", remote_port="Gi0/1",
                                last_seen_at=NOW))
    await db_session.flush()
    st = await ci.run(db_session, now=NOW)
    assert st["created"] == 1
    (c,) = await _cables(db_session)
    assert c["kind"] == "lldp"
    assert c["ends"] == {(await _port(db_session, sw1, "Gi1/0/48")).id, (await _port(db_session, sw2, "Gi0/1")).id}


@pytest.mark.anyio
async def test_librenms_link_is_cabled(db_session) -> None:
    sw1 = await _dev(db_session, "a", ("xe-0/0/1", None), type_="switch")
    sw2 = await _dev(db_session, "b", ("Te1/1", None), type_="switch")
    inst = LibreNMSInstance(name=f"ln-{uuid.uuid4().hex[:4]}", api_url="https://ln.example.net",
                            api_token_enc=b"x", api_token_nonce=b"y")
    db_session.add(inst)
    await db_session.flush()
    db_session.add_all([
        LibreNMSDevice(instance_id=inst.id, legacy_device_id=11, hostname="a", jt_ipam_device_id=sw1.id),
        LibreNMSDevice(instance_id=inst.id, legacy_device_id=12, hostname="b", jt_ipam_device_id=sw2.id),
        LibreNMSLink(instance_id=inst.id, legacy_link_id=1, protocol="cdp", active=True,
                     local_device_id=11, local_port_name="xe-0/0/1", remote_device_id=12, remote_port="Te1/1",
                     last_seen_at=NOW),
    ])
    await db_session.flush()
    await ci.run(db_session, now=NOW)
    (c,) = await _cables(db_session)
    assert c["kind"] == "cdp"
    assert c["ends"] == {(await _port(db_session, sw1, "xe-0/0/1")).id, (await _port(db_session, sw2, "Te1/1")).id}


@pytest.mark.anyio
async def test_neighbour_takes_precedence_over_fdb_on_the_same_port(db_session) -> None:
    """交換器之間的埠上看得到對方的 MAC；有鄰居宣告就照鄰居，不會再用 MAC 推出別的。"""
    sw1 = await _dev(db_session, "a", ("Gi1/0/1", _mac(20)), type_="switch")
    sw2 = await _dev(db_session, "b", ("Gi0/1", _mac(21)), type_="switch")
    inst = LibreNMSInstance(name=f"ln-{uuid.uuid4().hex[:4]}", api_url="https://ln.example.net",
                            api_token_enc=b"x", api_token_nonce=b"y")
    db_session.add(inst)
    await db_session.flush()
    db_session.add_all([
        LibreNMSDevice(instance_id=inst.id, legacy_device_id=21, hostname="a", jt_ipam_device_id=sw1.id),
        LibreNMSDevice(instance_id=inst.id, legacy_device_id=22, hostname="b", jt_ipam_device_id=sw2.id),
        LibreNMSLink(instance_id=inst.id, legacy_link_id=2, protocol="lldp", active=True,
                     local_device_id=21, local_port_name="Gi1/0/1", remote_device_id=22, remote_port="Gi0/1",
                     last_seen_at=NOW),
    ])
    _fdb(db_session, sw1, "Gi1/0/1", _mac(21))
    await db_session.flush()
    await ci.run(db_session, now=NOW)
    (c,) = await _cables(db_session)
    assert c["kind"] == "lldp"


# ─────────────────── 虛擬化主機 ───────────────────
@pytest.mark.anyio
async def test_port_seeing_a_host_and_only_its_vms_is_cabled_to_the_host(db_session) -> None:
    from app.models.virt import VirtCluster, VirtualMachine, VMInterface
    sw = await _switch(db_session)
    host = await _dev(db_session, "pve1", ("eno1", _mac(30)))
    cl = VirtCluster(name=f"cl-{uuid.uuid4().hex[:4]}", type="proxmox")
    db_session.add(cl)
    await db_session.flush()
    for i in range(3):
        vm = VirtualMachine(cluster_id=cl.id, name=f"vm{i}", node=host.name)
        db_session.add(vm)
        await db_session.flush()
        db_session.add(VMInterface(vm_id=vm.id, name="net0", mac=_mac(31 + i)))
        _fdb(db_session, sw, "Gi1/0/1", _mac(31 + i))
    _fdb(db_session, sw, "Gi1/0/1", _mac(30))
    await db_session.flush()
    await ci.run(db_session, now=NOW)
    (c,) = await _cables(db_session)
    assert c["kind"] == "fdb_vm_host"
    assert c["ends"] == {(await _port(db_session, sw, "Gi1/0/1")).id, (await _port(db_session, host, "eno1")).id}


# ─────────────────── 只建議 ───────────────────
@pytest.mark.anyio
async def test_switch_to_switch_from_fdb_is_only_suggested(db_session) -> None:
    sw1 = await _dev(db_session, "a", ("Gi1/0/1", _mac(40)), ("Gi1/0/2", None), type_="switch")
    sw2 = await _dev(db_session, "b", ("Gi0/1", _mac(41)), ("Gi0/2", None), type_="switch")
    h1 = await _dev(db_session, "h1", ("eth0", _mac(42)))
    h2 = await _dev(db_session, "h2", ("eth0", _mac(43)))
    # a 的 Gi1/0/1 看得到 b 和 b 底下的 h2；b 的 Gi0/1 看得到 a 和 a 底下的 h1
    _fdb(db_session, sw1, "Gi1/0/1", _mac(41))
    _fdb(db_session, sw1, "Gi1/0/1", _mac(43))
    _fdb(db_session, sw2, "Gi0/1", _mac(40))
    _fdb(db_session, sw2, "Gi0/1", _mac(42))
    _fdb(db_session, sw1, "Gi1/0/2", _mac(42))
    _fdb(db_session, sw2, "Gi0/2", _mac(43))
    await db_session.flush()
    st = await ci.run(db_session, now=NOW)
    kinds = sorted(c["kind"] for c in await _cables(db_session))
    assert kinds == ["fdb_single_mac", "fdb_single_mac"], "主機各自接到自己的埠"
    assert st["suggested"] == 1
    sug = (await db_session.execute(select(CableSuggestion))).scalar_one()
    assert sug.kind == "fdb_uplink" and sug.status == "open"
    assert {sug.a_port_id, sug.b_port_id} == {(await _port(db_session, sw1, "Gi1/0/1")).id,
                                              (await _port(db_session, sw2, "Gi0/1")).id}
    assert {h1.id, h2.id}


# ─────────────────── 設定 ───────────────────
@pytest.mark.anyio
async def test_enabled_by_default_and_can_be_turned_off(db_session, admin_user) -> None:
    cfg = await ci.get_config(db_session)
    assert cfg["enabled"] is True
    await ci.set_config(db_session, enabled=False, user_id=admin_user.id)
    await db_session.commit()
    assert (await ci.get_config(db_session))["enabled"] is False
    sw = await _switch(db_session)
    await _dev(db_session, "srv", ("eno1", _mac(50)))
    _fdb(db_session, sw, "Gi1/0/1", _mac(50))
    await db_session.flush()
    assert await ci.run_if_enabled(db_session, now=NOW) is None
    assert await _cables(db_session) == []


@pytest.mark.anyio
async def test_trace_marks_auto_cables_with_their_evidence(db_session, admin_user) -> None:
    from app.services.port_trace import trace
    sw = await _switch(db_session)
    await _dev(db_session, "srv", ("eno1", _mac(60)))
    _fdb(db_session, sw, "Gi1/0/1", _mac(60))
    await db_session.flush()
    await ci.run(db_session, now=NOW)
    out = await trace(db_session, admin_user, (await _port(db_session, sw, "Gi1/0/1")).id)
    (hop,) = [h for h in out["hops"] if h["kind"] == "cable"]
    assert hop["source"] == "auto" and hop["evidence"]["kind"] == "fdb_single_mac"


@pytest.mark.anyio
async def test_switch_side_aggregate_or_vlan_interface_is_not_a_cable_end(db_session) -> None:
    """MAC 表學在 Port-channel / ae / Trk / VLAN 介面上：那不是實體埠，不能當接線端。"""
    sw = await _dev(db_session, "sw", ("Po1", None), ("ae0", None), ("Trk1", None), ("Vlan10", None), type_="switch")
    for i, pn in enumerate(("Po1", "ae0", "Trk1", "Vlan10")):
        await _dev(db_session, f"srv{i}", ("eno1", _mac(70 + i)))
        _fdb(db_session, sw, pn, _mac(70 + i))
    await db_session.flush()
    await ci.run(db_session, now=NOW)
    assert await _cables(db_session) == []
