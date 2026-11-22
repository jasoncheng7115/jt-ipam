"""虛實標示：IP／MAC 對得到 VM 網卡才標「虛擬機」；對不到**不得**斷言實體機。

虛擬化整合可能只涵蓋部分叢集 —— 「查無」的意思是「不知道」，不是「實體機」。
把「不知道」顯示成「實體機」是誤導，比不顯示更糟。
"""
from __future__ import annotations

import uuid

import pytest

from app.services.fw_lookup import vm_match_for


async def _mk_vm(db_session, *, name: str, ip: str | None, mac: str | None,
                 kind: str = "vm", platform: str = "proxmox"):
    from app.models.virt import VirtCluster, VirtualMachine, VMInterface
    cl = VirtCluster(name=f"cl-{uuid.uuid4().hex[:6]}", type=platform)
    db_session.add(cl)
    await db_session.flush()
    vm = VirtualMachine(cluster_id=cl.id, name=name, kind=kind)
    db_session.add(vm)
    await db_session.flush()
    db_session.add(VMInterface(vm_id=vm.id, name="net0", primary_ip=ip, mac=mac))
    await db_session.flush()
    return cl, vm


@pytest.mark.anyio
async def test_matches_by_ip(db_session) -> None:
    await _mk_vm(db_session, name="web-vm", ip="198.51.100.30", mac=None)
    m = await vm_match_for(db_session, ip="198.51.100.30")
    assert m and m["vm"] == "web-vm" and m["platform"] == "proxmox"


@pytest.mark.anyio
async def test_matches_by_mac_when_ip_differs(db_session) -> None:
    """VM 換了 IP（或 PVE 沒回報 IP）→ MAC 仍對得到。"""
    await _mk_vm(db_session, name="db-vm", ip=None, mac="00:00:5e:00:53:44")
    m = await vm_match_for(db_session, ip="203.0.113.99", macs=["00:00:5e:00:53:44"])
    assert m and m["vm"] == "db-vm"


@pytest.mark.anyio
async def test_ip_match_needs_the_mac_to_agree(db_session) -> None:
    """DHCP 位址換了主人：PVE 還記得某台 VM 的網卡用過這個 IP，但現在回應這個 IP 的是另一張網卡。
    2026-10-05 正式環境的 iPhone（隨機 MAC）被標成 vm-lab-02 虛擬機，連帶判讀全錯。兩邊 MAC 都知道而不同 → 不是它。"""
    await _mk_vm(db_session, name="vm-lab-02", ip="198.51.100.66", mac="bc:24:11:00:00:66")
    assert await vm_match_for(db_session, ip="198.51.100.66", macs=["d2:11:22:33:44:66"]) is None
    # MAC 一樣（大小寫不同）、或 IP 記錄沒有 MAC、或 VM 網卡沒回報 MAC → 照舊對得到
    assert await vm_match_for(db_session, ip="198.51.100.66", macs=["BC:24:11:00:00:66"])
    assert await vm_match_for(db_session, ip="198.51.100.66")
    await _mk_vm(db_session, name="no-mac", ip="198.51.100.67", mac=None)
    assert await vm_match_for(db_session, ip="198.51.100.67", macs=["d2:11:22:33:44:66"])


@pytest.mark.anyio
async def test_kind_tells_kvm_from_lxc(db_session) -> None:
    """PVE 有 KVM 虛擬機（qemu）與 LXC 容器兩種，畫面要分得出來 → 回傳要帶 kind。"""
    await _mk_vm(db_session, name="ct-app-01", ip="198.51.100.41", mac=None, kind="ct")
    await _mk_vm(db_session, name="win-vm", ip="198.51.100.42", mac=None, kind="vm")
    ct = await vm_match_for(db_session, ip="198.51.100.41")
    vm = await vm_match_for(db_session, ip="198.51.100.42")
    assert ct and ct["kind"] == "ct" and ct["platform"] == "proxmox"
    assert vm and vm["kind"] == "vm"


@pytest.mark.anyio
async def test_no_match_returns_none_not_physical(db_session) -> None:
    """對不到 → None。呼叫端據此「不顯示」，不是顯示「實體機」。"""
    assert await vm_match_for(db_session, ip="203.0.113.1") is None
    assert await vm_match_for(db_session) is None, "沒給任何條件不可以亂配"


@pytest.mark.anyio
async def test_ip_read_carries_virt_vm(client, auth_headers, db_session) -> None:
    from app.models.address import IPAddress
    from app.models.section import Section
    from app.models.subnet import Subnet

    sec = Section(name=f"s-{uuid.uuid4().hex[:6]}")
    db_session.add(sec)
    await db_session.flush()
    sub = Subnet(section_id=sec.id, cidr="198.51.100.0/24")
    db_session.add(sub)
    await db_session.flush()
    ipa = IPAddress(subnet_id=sub.id, ip="198.51.100.31", state="used")
    db_session.add(ipa)
    await _mk_vm(db_session, name="app-vm", ip="198.51.100.31", mac=None)
    await db_session.commit()

    r = await client.get(f"/api/v1/addresses/{ipa.id}", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["virt_vm"]["vm"] == "app-vm"


@pytest.mark.anyio
async def test_guest_kind_and_proxmox_oui(db_session) -> None:
    """KVM 虛擬機與 LXC 容器分開（容器一定是 Linux，虛擬機可能是防火牆或 Windows）；Proxmox 指派的 MAC
    （bc:24:11）只會出現在 PVE 的虛擬機／容器上，盤點沒對到也算（192.0.2.82 的 TrueNAS 虛擬機）。"""
    from app.services.fw_lookup import virtual_guest_kind
    await _mk_vm(db_session, name="ct-x", ip="198.51.100.81", mac="bc:24:11:00:00:81", kind="ct")
    await _mk_vm(db_session, name="vm-x", ip="198.51.100.82", mac="bc:24:11:00:00:82", kind="vm")
    assert await virtual_guest_kind(db_session, "198.51.100.81", "bc:24:11:00:00:81") == "ct"
    assert await virtual_guest_kind(db_session, "198.51.100.82", "BC:24:11:00:00:82") == "vm"
    assert await virtual_guest_kind(db_session, "198.51.100.83", "bc:24:11:aa:bb:cc") == "vm"
    assert await virtual_guest_kind(db_session, "198.51.100.84", "00:25:90:00:00:84") is None
