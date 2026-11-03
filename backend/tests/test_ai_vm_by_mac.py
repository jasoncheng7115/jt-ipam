"""AI 問「這個 MAC 是誰」「誰用了這個 IP」要答得出是哪台 VM（使用者 11-03：印表機的 IP 被一台 VM 衝了，
AI 只說廠商是 Proxmox、看起來在上行埠後面，卻沒給出是哪台 VM —— 明明系統有 VM 網卡的 MAC）。

trace_mac 帶出網卡 MAC 相符的 VM（名稱、VMID、節點、叢集、平台、狀態、網卡、作業系統）；
get_ip_detail 帶出「這個 IP 的 MAC 是哪台 VM 的網卡」與「哪台 VM 自己回報用了這個 IP」。
VM 屬於全域資料（跟 list_vms 一樣），只被授權部分子網路的帳號看不到。
"""
from __future__ import annotations

import uuid

from app.models.address import IPAddress
from app.models.section import Section
from app.models.subnet import Subnet
from app.models.virt import VirtCluster, VirtualMachine, VMInterface

MAC = "bc:24:11:00:53:01"


async def _setup(db):  # type: ignore[no-untyped-def]
    sec = Section(name=f"vmmac-{uuid.uuid4().hex[:6]}")
    db.add(sec)
    await db.flush()
    sub = Subnet(section_id=sec.id, cidr="198.51.100.0/24")
    db.add(sub)
    await db.flush()
    # 印表機的記錄，但掃描器最近看到的 MAC 已經是 VM 的
    db.add(IPAddress(subnet_id=sub.id, ip="198.51.100.52", hostname="mfp-01", mac=MAC, state="active"))
    cl = VirtCluster(name=f"cluster-{uuid.uuid4().hex[:4]}", type="proxmox")
    db.add(cl)
    await db.flush()
    vm = VirtualMachine(cluster_id=cl.id, name="test-clone-07", kind="qemu", node="pve-node-2", status="running",
                        legacy_vmid=1207, os_name="Debian 12")
    other = VirtualMachine(cluster_id=cl.id, name="reports-ct", kind="lxc", node="pve-node-1", status="running")
    db.add_all([vm, other])
    await db.flush()
    db.add(VMInterface(vm_id=vm.id, name="net0", mac=MAC, bridge="vmbr0"))
    # 另一台 CT 自己回報（guest agent）用了同一個 IP
    db.add(VMInterface(vm_id=other.id, name="eth0", mac="bc:24:11:00:53:02", primary_ip="198.51.100.52"))
    await db.commit()
    return sub, cl


async def test_trace_mac_names_the_vm(db_session, admin_user) -> None:
    from app.mcp.tools import TOOLS
    _sub, cl = await _setup(db_session)
    out = await TOOLS["trace_mac"]["fn"](db_session, user=admin_user, mac=MAC.upper())
    assert [v["vm_name"] for v in out["vms"]] == ["test-clone-07"]
    v = out["vms"][0]
    assert v["node"] == "pve-node-2" and v["vmid"] == 1207 and v["cluster"] == cl.name
    assert v["platform"] == "proxmox" and v["kind"] == "qemu" and v["interface"] == "net0" and v["status"] == "running"
    assert v["os_name"] == "Debian 12"


async def test_ip_detail_names_the_vm_behind_the_mac_and_vms_claiming_the_ip(db_session, admin_user) -> None:
    from app.mcp.tools import TOOLS
    await _setup(db_session)
    out = await TOOLS["get_ip_detail"]["fn"](db_session, user=admin_user, ip="198.51.100.52")
    by_mac = out["vms_with_this_mac"]
    assert [v["vm_name"] for v in by_mac] == ["test-clone-07"]
    claims = out["vms_reporting_this_ip"]
    assert [v["vm_name"] for v in claims] == ["reports-ct"] and claims[0]["node"] == "pve-node-1"


async def test_partial_users_do_not_get_vm_data(db_session) -> None:
    from app.core.security import hash_password
    from app.mcp.tools import TOOLS
    from app.models.permission import Permission
    from app.models.user import User
    sub, _cl = await _setup(db_session)
    u = User(username=f"u-{uuid.uuid4().hex[:6]}", email=f"{uuid.uuid4().hex[:6]}@t.local", display_name="U",
             password_hash=hash_password("TestPassword2026!"), auth_provider="local", is_active=True)
    db_session.add(u)
    await db_session.flush()
    db_session.add(Permission(object_type="subnet", object_id=sub.id, principal_type="user", principal_id=u.id,
                              level="read"))
    await db_session.commit()
    out = await TOOLS["trace_mac"]["fn"](db_session, user=u, mac=MAC)
    assert out["vms"] == []
    detail = await TOOLS["get_ip_detail"]["fn"](db_session, user=u, ip="198.51.100.52")
    assert detail["vms_with_this_mac"] == [] and detail["vms_reporting_this_ip"] == []
