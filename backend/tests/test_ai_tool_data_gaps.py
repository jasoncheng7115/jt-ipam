"""AI 工具「系統有答案、工具卻沒給」的缺口（2026-11-03 稽核）。

使用者問「誰占了 198.51.100.52？跟印表機衝突」，AI 答不出是哪台 VM —— 每張 VM 網卡的 MAC 都在資料庫裡。
同一類缺口還有：

1. 沒有 IP 記錄的位址：get_ip_detail／search_ip／investigate_ip 只回「查無」，但掃描代理目擊、ARP、
   VM 回報、DHCP 固定分配都看得到它（unregistered）
2. IP 衝突而 VM 沒有 guest agent：ARP 看得到另一個 MAC → 那是哪台 VM（arp_macs）
3. trace_mac「這個 MAC 是誰」：IP 記錄、裝置自己的連接埠、DHCP 固定分配
4. 維護中：get_ip_detail／get_device 帶出目前涵蓋它的維護
5. get_device：上面跑的 VM、電路、VPN 通道、租用設備
6. list_subnet_ips：看得到在用、但沒有記錄的位址
7. 全站搜尋 MAC：VM 網卡、裝置連接埠
8. investigate 的 ARP 與 DHCP 固定分配：重疊網段下不可給只被授權一邊的帳號看到另一邊的資料

全域基礎設施（VM、ARP、DHCP 固定分配、電路、VPN、租用）只給全域讀取；逐物件的照可見範圍。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.models.address import IPAddress
from app.models.advanced import Circuit, DeviceLease, Provider
from app.models.device import Device
from app.models.dhcp import DHCPReservation
from app.models.librenms import ARPEntry
from app.models.maintenance import MaintenanceScope, MaintenanceWindow
from app.models.physical import DevicePort, VPNTunnel
from app.models.section import Section
from app.models.subnet import Subnet
from app.models.unmanaged_sighting import UnmanagedSighting
from app.models.virt import VirtCluster, VirtualMachine, VMInterface

PRINTER_MAC = "00:00:5e:00:53:10"
VM_MAC = "bc:24:11:00:53:01"
GHOST_MAC = "bc:24:11:00:53:03"


def _now() -> datetime:
    return datetime.now(UTC)


async def _base(db):  # type: ignore[no-untyped-def]
    sec = Section(name=f"gaps-{uuid.uuid4().hex[:6]}")
    db.add(sec)
    await db.flush()
    sub = Subnet(section_id=sec.id, cidr="198.51.100.0/24")
    other = Subnet(section_id=sec.id, cidr="203.0.113.0/24")
    db.add_all([sub, other])
    await db.flush()
    cl = VirtCluster(name=f"cluster-{uuid.uuid4().hex[:4]}", type="proxmox")
    db.add(cl)
    await db.flush()
    vm = VirtualMachine(cluster_id=cl.id, name="test-clone-07", kind="vm", node="pve-node-2", status="running",
                        legacy_vmid=1207)
    ghost = VirtualMachine(cluster_id=cl.id, name="ghost-ct", kind="ct", node="pve-node-2", status="running")
    db.add_all([vm, ghost])
    await db.flush()
    db.add(VMInterface(vm_id=vm.id, name="net0", mac=VM_MAC, bridge="vmbr0"))
    db.add(VMInterface(vm_id=ghost.id, name="eth0", mac=GHOST_MAC, primary_ip="198.51.100.77"))
    await db.flush()
    return sub, other, vm, ghost


async def _partial_user(db, *, subnets=(), devices=()):  # type: ignore[no-untyped-def]
    from app.core.security import hash_password
    from app.models.permission import Permission
    from app.models.user import User
    u = User(username=f"u-{uuid.uuid4().hex[:6]}", email=f"{uuid.uuid4().hex[:6]}@example.com", display_name="U",
             password_hash=hash_password("TestPassword2026!"), auth_provider="local", is_active=True)
    db.add(u)
    await db.flush()
    for s in subnets:
        db.add(Permission(object_type="subnet", object_id=s.id, principal_type="user", principal_id=u.id,
                          level="read"))
    for d in devices:
        db.add(Permission(object_type="device", object_id=d.id, principal_type="user", principal_id=u.id,
                          level="read"))
    await db.commit()
    return u


# ── 1. 沒有 IP 記錄的位址 ──

async def _unregistered_setup(db):  # type: ignore[no-untyped-def]
    sub, other, _vm, _ghost = await _base(db)
    now = _now()
    db.add(UnmanagedSighting(subnet_id=sub.id, ip="198.51.100.77", source="scanner", mac=GHOST_MAC,
                             hostname="ghost.example.com", first_seen_at=now - timedelta(days=2), last_seen_at=now))
    db.add(UnmanagedSighting(subnet_id=other.id, ip="203.0.113.77", source="scanner", mac=GHOST_MAC,
                             first_seen_at=now, last_seen_at=now))
    db.add(ARPEntry(ip="198.51.100.77", mac=GHOST_MAC, source="librenms", last_seen_at=now))
    db.add(DHCPReservation(source_type="kea", source_id=uuid.uuid4(), source_name="kea-01",
                           ip="198.51.100.77", mac=GHOST_MAC, hostname="ghost-res"))
    await db.commit()
    return sub, other


async def test_ip_detail_of_unregistered_address_lists_what_the_system_saw(db_session, admin_user) -> None:
    from app.mcp.tools import TOOLS
    await _unregistered_setup(db_session)
    out = await TOOLS["get_ip_detail"]["fn"](db_session, user=admin_user, ip="198.51.100.77")
    assert out["found"] is False
    u = out["unregistered"]
    assert [(s["source"], s["mac"], s["hostname"], s["subnet"]) for s in u["sightings"]] == [
        ("scanner", GHOST_MAC, "ghost.example.com", "198.51.100.0/24")]
    assert u["sightings"][0]["first_seen"] and u["sightings"][0]["last_seen"]
    assert [a["mac"] for a in u["arp"]] == [GHOST_MAC]
    assert [v["vm_name"] for v in u["arp"][0]["vms"]] == ["ghost-ct"]
    assert u["arp"][0]["last_seen"] and "librenms" in u["arp"][0]["sources"]
    assert [v["vm_name"] for v in u["vms"]] == ["ghost-ct"]
    assert [(r["hostname"], r["mac"], r["source_name"]) for r in u["dhcp_reservations"]] == [
        ("ghost-res", GHOST_MAC, "kea-01")]


async def test_search_ip_empty_result_carries_unregistered(db_session, admin_user) -> None:
    from app.mcp.tools import TOOLS
    await _unregistered_setup(db_session)
    out = await TOOLS["search_ip"]["fn"](db_session, user=admin_user, ip="198.51.100.77")
    assert out["count"] == 0
    assert [v["vm_name"] for v in out["unregistered"]["vms"]] == ["ghost-ct"]
    assert out["unregistered"]["sightings"]


async def test_investigate_not_found_carries_unregistered(db_session, admin_user) -> None:
    from app.mcp.tools import TOOLS
    await _unregistered_setup(db_session)
    out = await TOOLS["investigate_ip"]["fn"](db_session, user=admin_user, ip="198.51.100.77")
    assert out["found"] is False
    assert [v["vm_name"] for v in out["unregistered"]["vms"]] == ["ghost-ct"]


async def test_unregistered_for_partial_user_only_shows_own_subnet_sightings(db_session) -> None:
    from app.mcp.tools import TOOLS
    sub, _other = await _unregistered_setup(db_session)
    u = await _partial_user(db_session, subnets=[sub])
    out = await TOOLS["get_ip_detail"]["fn"](db_session, user=u, ip="198.51.100.77")
    un = out["unregistered"]
    assert [s["source"] for s in un["sightings"]] == ["scanner"]
    assert un["arp"] == [] and un["vms"] == [] and un["dhcp_reservations"] == []
    # 另一個子網路的目擊不給
    out2 = await TOOLS["get_ip_detail"]["fn"](db_session, user=u, ip="203.0.113.77")
    assert out2["unregistered"]["sightings"] == []


# ── 2. IP 衝突而 VM 沒有 guest agent：ARP 看得到另一個 MAC ──

async def _conflict_setup(db):  # type: ignore[no-untyped-def]
    sub, _other, _vm, _ghost = await _base(db)
    now = _now()
    db.add(IPAddress(subnet_id=sub.id, ip="198.51.100.52", hostname="mfp-01", mac=PRINTER_MAC, state="active"))
    db.add(ARPEntry(ip="198.51.100.52", mac=PRINTER_MAC, source="librenms", last_seen_at=now - timedelta(hours=3)))
    db.add(ARPEntry(ip="198.51.100.52", mac=VM_MAC, source="scanner", subnet_id=sub.id, last_seen_at=now))
    # 太舊的不算
    db.add(ARPEntry(ip="198.51.100.52", mac="00:00:5e:00:53:99", source="scanner", subnet_id=sub.id,
                    first_seen_at=now - timedelta(days=40), last_seen_at=now - timedelta(days=30)))
    await db.commit()
    return sub


async def test_ip_detail_arp_macs_names_the_vm_behind_the_other_mac(db_session, admin_user) -> None:
    from app.mcp.tools import TOOLS
    await _conflict_setup(db_session)
    out = await TOOLS["get_ip_detail"]["fn"](db_session, user=admin_user, ip="198.51.100.52")
    rows = {r["mac"]: r for r in out["arp_macs"]}
    assert set(rows) == {PRINTER_MAC, VM_MAC}
    assert rows[PRINTER_MAC]["is_record_mac"] is True and rows[PRINTER_MAC]["vms"] == []
    vmrow = rows[VM_MAC]
    assert vmrow["is_record_mac"] is False
    assert [(v["vm_name"], v["node"]) for v in vmrow["vms"]] == [("test-clone-07", "pve-node-2")]
    assert vmrow["last_seen"] and "scanner" in vmrow["sources"]


async def test_ip_detail_arp_macs_hidden_from_partial_users(db_session) -> None:
    from app.mcp.tools import TOOLS
    sub = await _conflict_setup(db_session)
    u = await _partial_user(db_session, subnets=[sub])
    out = await TOOLS["get_ip_detail"]["fn"](db_session, user=u, ip="198.51.100.52")
    assert out["found"] is True and out["arp_macs"] == []


async def test_ip_detail_arp_macs_ignore_the_overlapping_twin(db_session, admin_user) -> None:
    """重疊網段：另一個子網路（同一個位址的另一筆記錄）掃到的 MAC 不是這筆的衝突。"""
    from app.mcp.tools import TOOLS
    sub = await _conflict_setup(db_session)
    twin = Subnet(section_id=sub.section_id, cidr="198.51.100.0/24")
    db_session.add(twin)
    await db_session.flush()
    db_session.add(IPAddress(subnet_id=twin.id, ip="198.51.100.52", hostname="other-site", state="active"))
    db_session.add(ARPEntry(ip="198.51.100.52", mac="00:00:5e:00:53:42", source="scanner", subnet_id=twin.id,
                            last_seen_at=_now()))
    await db_session.commit()
    out = await TOOLS["get_ip_detail"]["fn"](db_session, user=admin_user, ip="198.51.100.52")
    macs = {r["mac"] for r in out["arp_macs"]}
    # get_ip_detail 取的是 subnet_id 排序的第一筆；另一筆的 MAC 不該出現在它的 arp_macs
    rec_sub = out["subnet_id"]
    if rec_sub == str(sub.id):
        assert "00:00:5e:00:53:42" not in macs
    else:
        assert VM_MAC not in macs


# ── 3. trace_mac：這個 MAC 是誰 ──

async def _mac_owner_setup(db):  # type: ignore[no-untyped-def]
    sub, other, _vm, _ghost = await _base(db)
    mine = Device(name="sw-mine.example.com")
    theirs = Device(name="sw-theirs.example.com")
    db.add_all([mine, theirs])
    await db.flush()
    mac = "00:00:5e:00:53:20"
    db.add(IPAddress(subnet_id=sub.id, ip="198.51.100.20", hostname="host-a", mac=mac, state="active",
                     device_id=mine.id, owner="ops"))
    db.add(IPAddress(subnet_id=other.id, ip="203.0.113.20", hostname="host-b", mac=mac, state="active"))
    db.add(DevicePort(device_id=mine.id, name="eth0", mac_address="00-00-5E-00-53-20"))
    db.add(DevicePort(device_id=theirs.id, name="mgmt0", mac_address="00005e005320"))
    db.add(DHCPReservation(source_type="kea", source_id=uuid.uuid4(), source_name="kea-01",
                           ip="198.51.100.20", mac="00:00:5E:00:53:20", hostname="host-a-res"))
    await db.commit()
    return sub, mine, mac


async def test_trace_mac_lists_records_ports_and_reservations(db_session, admin_user) -> None:
    from app.mcp.tools import TOOLS
    _sub, _mine, mac = await _mac_owner_setup(db_session)
    out = await TOOLS["trace_mac"]["fn"](db_session, user=admin_user, mac=mac.upper())
    assert sorted(r["ip"] for r in out["ipam_records"]) == ["198.51.100.20", "203.0.113.20"]
    a = next(r for r in out["ipam_records"] if r["ip"] == "198.51.100.20")
    assert a["hostname"] == "host-a" and a["device"] == "sw-mine.example.com" and a["owner"] == "ops"
    assert a["subnet"] == "198.51.100.0/24"
    assert sorted((p["device_name"], p["port"]) for p in out["device_ports"]) == [
        ("sw-mine.example.com", "eth0"), ("sw-theirs.example.com", "mgmt0")]
    assert [r["hostname"] for r in out["dhcp_reservations"]] == ["host-a-res"]


async def test_trace_mac_owner_blocks_follow_visibility(db_session) -> None:
    from app.mcp.tools import TOOLS
    sub, mine, mac = await _mac_owner_setup(db_session)
    u = await _partial_user(db_session, subnets=[sub], devices=[mine])
    out = await TOOLS["trace_mac"]["fn"](db_session, user=u, mac=mac)
    assert [r["ip"] for r in out["ipam_records"]] == ["198.51.100.20"]
    assert [p["device_name"] for p in out["device_ports"]] == ["sw-mine.example.com"]
    assert out["dhcp_reservations"] == []


# ── 4. 維護中 ──

async def test_ip_detail_and_device_show_active_maintenance(db_session, admin_user) -> None:
    from app.mcp.tools import TOOLS
    from app.services import maintenance as msvc
    sub, _o, _vm, _g = await _base(db_session)
    dev = Device(name="core-sw.example.com")
    db_session.add(dev)
    await db_session.flush()
    ip = IPAddress(subnet_id=sub.id, ip="198.51.100.9", hostname="nas-01", state="active")
    db_session.add(ip)
    await db_session.flush()
    now = _now()
    w1 = MaintenanceWindow(title="NAS firmware", behavior="mute", schedule_type="once",
                           starts_at=now - timedelta(minutes=5), ends_at=now + timedelta(hours=1))
    w2 = MaintenanceWindow(title="Core switch reboot", behavior="mark", schedule_type="once",
                           starts_at=now - timedelta(minutes=5), ends_at=now + timedelta(hours=2))
    w3 = MaintenanceWindow(title="Later", behavior="mute", schedule_type="once",
                           starts_at=now + timedelta(days=1), ends_at=now + timedelta(days=1, hours=1))
    db_session.add_all([w1, w2, w3])
    await db_session.flush()
    db_session.add(MaintenanceScope(window_id=w1.id, scope_type="ip", object_id=ip.id))
    db_session.add(MaintenanceScope(window_id=w2.id, scope_type="device", object_id=dev.id))
    db_session.add(MaintenanceScope(window_id=w3.id, scope_type="device", object_id=dev.id))
    await db_session.commit()
    msvc.invalidate(db_session)
    out = await TOOLS["get_ip_detail"]["fn"](db_session, user=admin_user, ip="198.51.100.9")
    assert [(m["title"], m["behavior"]) for m in out["maintenance"]] == [("NAS firmware", "mute")]
    assert out["maintenance"][0]["until"]
    d = await TOOLS["get_device"]["fn"](db_session, user=admin_user, device_id=str(dev.id))
    assert [(m["title"], m["behavior"]) for m in d["maintenance"]] == [("Core switch reboot", "mark")]


# ── 5. get_device：上面跑的 VM、電路、VPN、租用設備 ──

async def _hypervisor_setup(db):  # type: ignore[no-untyped-def]
    sub, _o, vm, _ghost = await _base(db)
    host = Device(name="pve-node-2", type="server")
    peer = Device(name="fw-branch.example.com", type="firewall")
    db.add_all([host, peer])
    await db.flush()
    vm_ip = IPAddress(subnet_id=sub.id, ip="198.51.100.31", hostname="test-clone-07", state="active")
    db.add(vm_ip)
    await db.flush()
    vm.primary_ip_id = vm_ip.id
    prov = Provider(name=f"isp-{uuid.uuid4().hex[:4]}")
    db.add(prov)
    await db.flush()
    circ = Circuit(cid="CID-0001", provider_id=prov.id, device_id=host.id, status="active")
    db.add(circ)
    await db.flush()
    db.add(VPNTunnel(name=f"tun-{uuid.uuid4().hex[:4]}", type="wireguard", a_device_id=peer.id,
                     b_device_id=host.id, status="active"))
    db.add(DeviceLease(device_id=host.id, provider_id=prov.id, circuit_id=circ.id, return_required=True))
    await db.commit()
    return sub, host, prov


async def test_get_device_lists_hosted_vms_circuits_vpns_and_lease(db_session, admin_user) -> None:
    from app.mcp.tools import TOOLS
    _sub, host, prov = await _hypervisor_setup(db_session)
    d = await TOOLS["get_device"]["fn"](db_session, user=admin_user, device_id=str(host.id))
    assert d["hosted_vm_count"] == 2
    names = {v["name"]: v for v in d["hosted_vms"]}
    assert set(names) == {"test-clone-07", "ghost-ct"}
    assert names["test-clone-07"]["primary_ip"] == "198.51.100.31" and names["test-clone-07"]["status"] == "running"
    assert names["ghost-ct"]["kind"] == "ct"
    assert [c["cid"] for c in d["circuits"]] == ["CID-0001"] and d["circuits"][0]["provider"] == prov.name
    assert len(d["vpn_tunnels"]) == 1 and d["vpn_tunnels"][0]["peer_device"] == "fw-branch.example.com"
    assert d["lease"]["provider"] == prov.name and d["lease"]["circuit"] == "CID-0001"
    assert d["lease"]["return_required"] is True


async def test_get_device_infra_blocks_hidden_from_partial_users(db_session) -> None:
    from app.mcp.tools import TOOLS
    _sub, host, _prov = await _hypervisor_setup(db_session)
    u = await _partial_user(db_session, devices=[host])
    d = await TOOLS["get_device"]["fn"](db_session, user=u, device_id=str(host.id))
    assert d["hosted_vms"] == [] and d["hosted_vm_count"] is None
    assert d["circuits"] == [] and d["vpn_tunnels"] == [] and d["lease"] is None


# ── 6. list_subnet_ips：看得到在用、但沒有記錄的位址 ──

async def test_list_subnet_ips_includes_unmanaged_on_first_page(db_session, admin_user) -> None:
    from app.mcp.tools import TOOLS
    sub, _other = await _unregistered_setup(db_session)
    db_session.add(IPAddress(subnet_id=sub.id, ip="198.51.100.5", hostname="known", state="active"))
    await db_session.commit()
    out = await TOOLS["list_subnet_ips"]["fn"](db_session, user=admin_user, subnet_id=str(sub.id))
    assert [e["ip"] for e in out["unmanaged"]] == ["198.51.100.77"]
    assert out["unmanaged"][0]["mac"] == GHOST_MAC
    later = await TOOLS["list_subnet_ips"]["fn"](db_session, user=admin_user, subnet_id=str(sub.id), offset=1)
    assert "unmanaged" not in later


async def test_list_subnet_ips_unmanaged_for_partial_user_with_subnet_read(db_session) -> None:
    from app.mcp.tools import TOOLS
    sub, _other = await _unregistered_setup(db_session)
    u = await _partial_user(db_session, subnets=[sub])
    out = await TOOLS["list_subnet_ips"]["fn"](db_session, user=u, subnet_id=str(sub.id))
    assert [e["ip"] for e in out["unmanaged"]] == ["198.51.100.77"]


# ── 7. 全站搜尋 MAC：VM 網卡、裝置連接埠 ──

async def test_global_search_mac_finds_vm_nics_and_device_ports(db_session, admin_user) -> None:
    from app.services.search import search
    _sub, _o, _vm, _g = await _base(db_session)
    sw = Device(name="edge-sw.example.com")
    db_session.add(sw)
    await db_session.flush()
    db_session.add(DevicePort(device_id=sw.id, name="ge-0/0/1", mac_address="00:00:5E:00:53:30"))
    await db_session.commit()
    vm_hits = (await search(db_session, user=admin_user, q=VM_MAC.upper()))["results"]
    assert any(h["type"] in ("vm", "vm_record") and h["label"] == "test-clone-07" for h in vm_hits)
    port_hits = (await search(db_session, user=admin_user, q="00:00:5e:00:53:30"))["results"]
    assert any(h["type"] == "device" and h["id"] == str(sw.id) for h in port_hits)
    # 部分 MAC（首碼）也找得到裝置連接埠
    frag = (await search(db_session, user=admin_user, q="00:00:5e:00:53"))["results"]
    assert any(h["type"] == "device" and h["id"] == str(sw.id) for h in frag)


async def test_global_search_mac_respects_visibility(db_session) -> None:
    from app.services.search import search
    sub, _o, _vm, _g = await _base(db_session)
    sw = Device(name="edge-sw.example.com")
    mine = Device(name="my-sw.example.com")
    db_session.add_all([sw, mine])
    await db_session.flush()
    db_session.add(DevicePort(device_id=sw.id, name="ge-0/0/1", mac_address="00:00:5e:00:53:30"))
    db_session.add(DevicePort(device_id=mine.id, name="ge-0/0/2", mac_address="00:00:5e:00:53:31"))
    await db_session.commit()
    u = await _partial_user(db_session, subnets=[sub], devices=[mine])
    vm_hits = (await search(db_session, user=u, q=VM_MAC))["results"]
    assert not any(h["label"] == "test-clone-07" for h in vm_hits), "VM 沒有看得到的 IP 記錄：全域資料"
    hidden = (await search(db_session, user=u, q="00:00:5e:00:53:30"))["results"]
    assert not any(h["type"] == "device" for h in hidden)
    shown = (await search(db_session, user=u, q="00:00:5e:00:53:31"))["results"]
    assert any(h["type"] == "device" and h["id"] == str(mine.id) for h in shown)


# ── 8. investigate：重疊網段的 ARP 與 DHCP 固定分配 ──

async def test_investigate_arp_and_reservations_do_not_leak_the_overlapping_twin(db_session) -> None:
    from app.services.investigate import collect_dossier
    sec = Section(name=f"inv-{uuid.uuid4().hex[:6]}")
    db_session.add(sec)
    await db_session.flush()
    a = Subnet(section_id=sec.id, cidr="198.51.100.0/24")
    b = Subnet(section_id=sec.id, cidr="198.51.100.0/24")
    db_session.add_all([a, b])
    await db_session.flush()
    rec_a = IPAddress(subnet_id=a.id, ip="198.51.100.60", hostname="site-a", state="active")
    rec_b = IPAddress(subnet_id=b.id, ip="198.51.100.60", hostname="site-b", state="active")
    db_session.add_all([rec_a, rec_b])
    await db_session.flush()
    now = _now()
    db_session.add(ARPEntry(ip="198.51.100.60", mac="00:00:5e:00:53:a1", source="scanner", subnet_id=a.id,
                            last_seen_at=now))
    db_session.add(ARPEntry(ip="198.51.100.60", mac="00:00:5e:00:53:b1", source="scanner", subnet_id=b.id,
                            last_seen_at=now))
    # LibreNMS 的列沒有子網路歸屬：重疊網段分不出是哪一邊，受限帳號不給
    db_session.add(ARPEntry(ip="198.51.100.60", mac="00:00:5e:00:53:c1", source="librenms", last_seen_at=now))
    db_session.add(DHCPReservation(source_type="kea", source_id=uuid.uuid4(), source_name="kea-a",
                                   ip="198.51.100.60", mac="00:00:5e:00:53:a1", ip_address_id=rec_a.id))
    db_session.add(DHCPReservation(source_type="kea", source_id=uuid.uuid4(), source_name="kea-b",
                                   ip="198.51.100.60", mac="00:00:5e:00:53:b1", ip_address_id=rec_b.id))
    await db_session.commit()
    u = await _partial_user(db_session, subnets=[a])
    d = await collect_dossier(db_session, user=u, ip="198.51.100.60")
    assert d["found"] is True and d["address"]["hostname"] == "site-a"
    assert {r["mac"] for r in d["arp"]} == {"00:00:5e:00:53:a1"}
    res = (d.get("dhcp") or {}).get("reservations") or []
    assert [r["source_name"] for r in res] == ["kea-a"]
