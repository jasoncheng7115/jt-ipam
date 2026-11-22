"""Hyper-V 整合（直連主機或 SCVMM；WinRM＋PowerShell，唯讀）。

2026-10-20 開發時**沒有實機**：這裡用假的 WinRM 回固定的 JSON（腳本輸出的形狀），守住正規化、寫進共用虛擬化
資料表、IP／MAC／主機名稱接進 IPAM、叢集去重與「讀不完整就不刪」、主機網卡建成裝置埠、SCVMM 備援、API 與權限。
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from app.models.address import IPAddress
from app.models.device import Device
from app.models.hyperv import HyperVInstance
from app.models.ip_hostname import IPHostnameReport
from app.models.physical import DevicePort
from app.models.virt import VirtCluster, VirtualMachine, VMInterface
from app.services import hyperv
from sqlalchemy import select

from tests.test_ip_conflict_evidence import _subnet

pytestmark = pytest.mark.anyio

G1, G2, G3 = (str(uuid.UUID(int=i)) for i in (0xA1, 0xA2, 0xA3))


def _host(name: str, *, cluster: str | None = "HVC01", vms: list[dict[str, Any]] | None = None,
          kvp_ok: bool = True, **extra: Any) -> dict[str, Any]:
    return {"host": name, "fqdn": f"{name}.corp.example", "cluster": cluster, "kvp_ok": kvp_ok,
            "vms": vms or [], "adapters_ok": True, "adapters": [], "switches": [], "ips": [], **extra}


def _vm(gid: str, name: str, state: str = "Running", **kw: Any) -> dict[str, Any]:
    return {"id": gid, "name": name, "state": state, "cpu": 2, "memory_mb": 4096, "notes": "",
            "fqdn": kw.pop("fqdn", None), "os": kw.pop("os", None), "nics": kw.pop("nics", []), **kw}


def _fake(monkeypatch, by_host: dict[str, Any]) -> list[tuple[str, str]]:
    """每台主機回固定的資料；值是 Exception 就丟出來。回傳呼叫紀錄 (host, 腳本種類)。"""
    calls: list[tuple[str, str]] = []

    class _C:
        def __init__(self, host: str) -> None:
            self.host = host

        def run_json(self, script: str) -> dict[str, Any]:
            kind = ("scvmm" if script is hyperv._SCVMM_SCRIPT else "host" if script is hyperv._HOST_SCRIPT
                    else "ping" if script is hyperv._PING_SCRIPT else "probe")
            calls.append((self.host, kind))
            v = by_host[self.host]
            if isinstance(v, Exception):
                raise v
            if kind == "ping":
                return {"host": self.host.upper(), "ps": "5.1.20348"}
            if kind == "probe":
                return {"vms": len(v.get("vms", [])), "cluster": v.get("cluster"), "hosts": len(v.get("hosts", []))}
            return v

    monkeypatch.setattr(hyperv, "_client", lambda inst, host: _C(host))
    return calls


async def _inst(db, **kw: Any) -> HyperVInstance:
    inst = HyperVInstance(name=f"hv-{uuid.uuid4().hex[:6]}", host=kw.pop("host", "hv1"), username="CORP\\ro",
                          password_enc=b"x", password_nonce=b"y", **kw)
    db.add(inst)
    await db.flush()
    return inst


# ── 正規化 ──

@pytest.mark.parametrize(("raw", "want"), [
    ("Running", "running"), ("Off", "stopped"), ("PowerOff", "stopped"), ("Saved", "paused"),
    ("Paused", "paused"), ("UnderMigration", "migrating"), ("Starting", "unknown"), (None, "unknown"),
])
def test_states(raw: Any, want: str) -> None:
    assert hyperv.map_state(raw) == want


def test_macs() -> None:
    assert hyperv.norm_mac("00155D0A0B0C") == "00:15:5d:0a:0b:0c"
    assert hyperv.norm_mac("00-15-5D-0A-0B-0C") == "00:15:5d:0a:0b:0c"
    assert hyperv.norm_mac("000000000000") is None          # 關機的動態 MAC
    assert hyperv.norm_mac("") is None
    assert hyperv.norm_mac(None) is None


def test_vm_normalization() -> None:
    vm = hyperv.normalize_vm(_vm(G1.upper(), "web", nics=[
        {"name": "Network Adapter", "mac": "00155D000001", "switch": "External", "vlan": 20,
         "ips": ["fe80::1", "198.51.100.21"]},
        {"name": "Network Adapter", "mac": "00155D000002", "switch": "Internal", "vlan": 0, "ips": []},
    ]), host="hv1")
    assert vm is not None
    assert vm["id"] == G1                                     # GUID 一律小寫
    assert vm["node"] == "hv1"
    assert [n["vlan"] for n in vm["nics"]] == [20, None]      # VLAN 0＝沒有
    assert hyperv.normalize_vm({"id": "not-a-guid", "name": "x"}, host="hv1") is None


# ── WinRM ──

def test_winrm_session_options(monkeypatch) -> None:
    import sys
    got: list[dict[str, Any]] = []

    class _S:
        def __init__(self, **kw: Any) -> None:
            got.append(kw)

        def run_ps(self, script: str) -> Any:
            return SimpleNamespace(status_code=0, std_out=b'{"host": "HV1"}', std_err=b"")

    monkeypatch.setitem(sys.modules, "winrm", SimpleNamespace(Session=_S))
    monkeypatch.setattr("app.core.net_guard.check_target", lambda *a, **k: [])
    hyperv.HyperVClient(host="hv1", username="u", password="p").run_json(hyperv._PING_SCRIPT)
    hyperv.HyperVClient(host="hv1", username="u", password="p", port=5986, use_ssl=True).run_json("x")
    assert got[0]["target"] == "http://hv1:5985/wsman"
    assert got[0]["message_encryption"] == "always"           # HTTP 一律加密，不退回明文
    assert got[0]["transport"] == "ntlm"
    assert got[1]["target"] == "https://hv1:5986/wsman"
    assert got[1]["server_cert_validation"] == "validate"


def test_error_codes() -> None:
    assert hyperv._classify_transport_error(Exception("401 Unauthorized"), host="h", port=5985,
                                            use_ssl=False).code == "hyperv_auth"
    assert hyperv._classify_transport_error(Exception("CERTIFICATE_VERIFY_FAILED"), host="h", port=5986,
                                            use_ssl=True).code == "hyperv_cert"
    assert hyperv._classify_transport_error(Exception("ConnectTimeout"), host="h", port=5986,
                                            use_ssl=True).code == "hyperv_https_unreachable"
    assert hyperv._classify_ps_error("The term 'Get-VM' is not recognized as the name of a cmdlet",
                                     host="h").code == "hyperv_no_module"


def test_blocked_address() -> None:
    with pytest.raises(hyperv.HyperVError) as e:
        hyperv.HyperVClient(host="169.254.169.254", username="u", password="p")
    assert e.value.code == "hyperv_connect"


# ── 同步（直連主機）──

async def test_cluster_nodes_are_merged_and_written(db_session, monkeypatch) -> None:
    sub = await _subnet(db_session, "198.51.100.0/24")
    ip = IPAddress(subnet_id=sub.id, ip="198.51.100.21", state="active")
    db_session.add(ip)
    inst = await _inst(db_session, extra_hosts="hv2")
    _fake(monkeypatch, {
        "hv1": _host("hv1", vms=[
            _vm(G1, "web", fqdn="web01.corp.example", os="Windows Server 2022 Datacenter", nics=[
                {"name": "Network Adapter", "mac": "00155D000001", "switch": "External", "vlan": None,
                 "ips": ["198.51.100.21", "fe80::1"]},
                {"name": "Network Adapter", "mac": "00155D000002", "switch": "Backup", "vlan": 30, "ips": []}]),
        ]),
        "hv2": _host("hv2", vms=[
            _vm(G2, "db", state="Off", nics=[{"name": "eth0", "mac": "00155D000003", "switch": "External",
                                               "ips": ["198.51.100.99"]}]),
        ]),
    })
    out = await hyperv.sync_instance(db_session, inst)
    await db_session.flush()
    assert out["vms"] == 2
    assert not out["errors"]
    cl = await db_session.get(VirtCluster, inst.cluster_id)
    assert (cl.type, cl.name) == ("hyper-v", "HVC01")          # 容錯移轉叢集的名稱
    vms = {v.name: v for v in (await db_session.execute(select(VirtualMachine).where(
        VirtualMachine.cluster_id == cl.id))).scalars()}
    assert vms["web"].node == "hv1"
    assert vms["web"].status == "running"
    assert vms["web"].os_name == "Windows Server 2022 Datacenter"
    assert vms["web"].primary_ip_id == ip.id
    assert vms["db"].status == "stopped"
    assert vms["db"].primary_ip_id is None                    # 關機的不認領 IP
    nics = {n.name: n for n in (await db_session.execute(select(VMInterface).where(
        VMInterface.vm_id == vms["web"].id))).scalars()}
    assert set(nics) == {"Network Adapter", "Network Adapter #2"}   # 同名網卡補序號
    assert nics["Network Adapter #2"].bridge == "Backup (VLAN 30)"   # 沒有對應的 VLAN 記錄：號碼不丟
    await db_session.refresh(ip)
    assert str(ip.mac) == "00:15:5d:00:00:01"
    rep = (await db_session.execute(select(IPHostnameReport).where(
        IPHostnameReport.ip_id == ip.id, IPHostnameReport.source == "hyperv"))).scalars().one()
    assert rep.hostname == "web01.corp.example"               # KVP 的客體 FQDN
    assert not (await db_session.execute(select(IPAddress).where(
        IPAddress.ip == "198.51.100.99"))).scalars().first()    # 沒開自動建立：不新建


async def test_auto_create_ips(db_session, monkeypatch) -> None:
    await _subnet(db_session, "198.51.100.0/24")
    inst = await _inst(db_session, auto_create_ips=True)
    _fake(monkeypatch, {"hv1": _host("hv1", cluster=None, vms=[
        _vm(G1, "app", nics=[{"name": "n", "mac": "00155D000011", "ips": ["198.51.100.31"]}])])})
    await hyperv.sync_instance(db_session, inst)
    await db_session.flush()
    row = (await db_session.execute(select(IPAddress).where(IPAddress.ip == "198.51.100.31"))).scalars().one()
    assert row.discovery_source == "hyperv"
    cl = await db_session.get(VirtCluster, inst.cluster_id)
    assert cl.name == inst.name                               # 單機：用整合名稱
    assert cl.is_standalone is True


async def test_one_unreachable_node_keeps_vms(db_session, monkeypatch) -> None:
    """叢集 VM 會在節點間移動：有一台沒讀到就不刪（不然移到那台的 VM 會被當成刪除）。"""
    inst = await _inst(db_session, extra_hosts="hv2")
    _fake(monkeypatch, {"hv1": _host("hv1", vms=[_vm(G1, "a"), _vm(G2, "b")]), "hv2": _host("hv2")})
    await hyperv.sync_instance(db_session, inst)
    await db_session.flush()
    _fake(monkeypatch, {"hv1": _host("hv1", vms=[_vm(G1, "a")]),
                        "hv2": hyperv.HyperVError("down", code="hyperv_connect", host="hv2")})
    out = await hyperv.sync_instance(db_session, inst)
    await db_session.flush()
    assert out["removed"] == 0
    assert out["errors"]
    assert inst.last_error
    names = set((await db_session.execute(select(VirtualMachine.name).where(
        VirtualMachine.cluster_id == inst.cluster_id))).scalars())
    assert names == {"a", "b"}
    # 兩台都讀到了：b 真的不見了才刪
    _fake(monkeypatch, {"hv1": _host("hv1", vms=[_vm(G1, "a")]), "hv2": _host("hv2")})
    out = await hyperv.sync_instance(db_session, inst)
    await db_session.flush()
    assert out["removed"] == 1


async def test_all_nodes_unreachable_is_a_failure(db_session, monkeypatch) -> None:
    inst = await _inst(db_session)
    _fake(monkeypatch, {"hv1": hyperv.HyperVError("401", code="hyperv_auth", host="hv1")})
    with pytest.raises(hyperv.HyperVError):
        await hyperv.sync_instance(db_session, inst)
    assert inst.last_error


async def test_live_migration_duplicate_keeps_one(db_session, monkeypatch) -> None:
    inst = await _inst(db_session, extra_hosts="hv2")
    _fake(monkeypatch, {"hv1": _host("hv1", vms=[_vm(G1, "mover", state="Off")]),
                        "hv2": _host("hv2", vms=[_vm(G1, "mover")])})
    await hyperv.sync_instance(db_session, inst)
    await db_session.flush()
    rows = (await db_session.execute(select(VirtualMachine).where(
        VirtualMachine.cluster_id == inst.cluster_id))).scalars().all()
    assert [(r.node, r.status) for r in rows] == [("hv2", "running")]


async def test_unknown_guest_name_is_neither_reported_nor_cleared(db_session, monkeypatch) -> None:
    """那台主機的 KVP 讀不到：客體名稱「不知道」不是「沒有」—— 不報 VM 名稱、也不清掉之前的 FQDN。"""
    sub = await _subnet(db_session, "198.51.100.0/24")
    ip = IPAddress(subnet_id=sub.id, ip="198.51.100.41", state="active")
    db_session.add(ip)
    inst = await _inst(db_session)
    nic = [{"name": "n", "mac": "00155D000041", "ips": ["198.51.100.41"]}]
    _fake(monkeypatch, {"hv1": _host("hv1", vms=[_vm(G3, "VM 41", fqdn="app41.corp.example", nics=nic)])})
    await hyperv.sync_instance(db_session, inst)
    _fake(monkeypatch, {"hv1": _host("hv1", kvp_ok=False, vms=[_vm(G3, "VM 41", nics=nic)])})
    await hyperv.sync_instance(db_session, inst)
    await db_session.flush()
    reps = (await db_session.execute(select(IPHostnameReport.hostname).where(
        IPHostnameReport.ip_id == ip.id, IPHostnameReport.source == "hyperv"))).scalars().all()
    assert reps == ["app41.corp.example"]


async def test_host_nics_become_ports_of_the_existing_device(db_session, monkeypatch) -> None:
    dev = Device(name="hv1", type="server")
    db_session.add(dev)
    await db_session.flush()
    db_session.add(DevicePort(device_id=dev.id, name="iDRAC", type="network"))    # 使用者自己建的
    inst = await _inst(db_session)
    adapters = [{"name": "NIC1", "desc": "Intel(R) Ethernet X710 #1", "mac": "00-25-90-00-00-01", "up": True},
                {"name": "NIC2", "desc": "Intel(R) Ethernet X710 #2", "mac": "00-25-90-00-00-02", "up": True}]
    switches = [{"name": "External", "type": "External", "members": ["Intel(R) Ethernet X710 #1"]}]
    _fake(monkeypatch, {"hv1": _host("hv1", cluster=None, adapters=adapters, switches=switches)})
    out = await hyperv.sync_instance(db_session, inst)
    await db_session.flush()
    assert out["ports"] == 3
    ports = {p.name: p for p in (await db_session.execute(select(DevicePort).where(
        DevicePort.device_id == dev.id))).scalars()}
    assert ports["NIC1"].mac_address == "00:25:90:00:00:01"
    assert ports["vSwitch External"].type == "bridge"
    assert ports["vSwitch External"].peer_port_id == ports["NIC1"].id
    # NIC2 拔掉了：它建的埠清掉，使用者的埠不動
    _fake(monkeypatch, {"hv1": _host("hv1", cluster=None, adapters=adapters[:1], switches=switches)})
    await hyperv.sync_instance(db_session, inst)
    await db_session.flush()
    names = set((await db_session.execute(select(DevicePort.name).where(DevicePort.device_id == dev.id))).scalars())
    assert names == {"iDRAC", "NIC1", "vSwitch External"}


# ── SCVMM ──

async def test_scvmm_mode_with_fallback_server(db_session, monkeypatch) -> None:
    inst = await _inst(db_session, mode="scvmm", host="vmm1", extra_hosts="vmm2")
    calls = _fake(monkeypatch, {
        "vmm1": hyperv.HyperVError("refused", code="hyperv_connect", host="vmm1"),
        "vmm2": {"server": "VMM2", "hosts": [{"name": "hv7", "fqdn": "hv7.corp.example", "cluster": "C7"}],
                 "vms": [{"id": G1, "name": "erp", "state": "Running", "host": "hv7", "cpu": 8, "memory_mb": 16384,
                          "fqdn": "erp.corp.example", "os": "Windows Server 2019 Standard",
                          "nics": [{"name": "Adapter", "mac": "00:15:5D:01:02:03", "switch": "Prod", "vlan": 120,
                                    "ips": ["198.51.100.70"]}]}]},
    })
    out = await hyperv.sync_instance(db_session, inst)
    await db_session.flush()
    assert [c for c in calls if c[1] == "scvmm"] == [("vmm1", "scvmm"), ("vmm2", "scvmm")]
    assert out["vms"] == 1
    vm = (await db_session.execute(select(VirtualMachine).where(
        VirtualMachine.cluster_id == inst.cluster_id))).scalars().one()
    assert (vm.node, vm.memory_mb, vm.os_name) == ("hv7", 16384, "Windows Server 2019 Standard")
    cl = await db_session.get(VirtCluster, inst.cluster_id)
    assert cl.name == inst.name                              # SCVMM 管很多叢集：用整合名稱


async def test_cluster_of_another_platform_is_never_reused(db_session, monkeypatch) -> None:
    db_session.add(VirtCluster(name="HVC01", type="proxmox"))
    await db_session.flush()
    inst = await _inst(db_session)
    _fake(monkeypatch, {"hv1": _host("hv1", vms=[_vm(G1, "a")])})
    await hyperv.sync_instance(db_session, inst)
    cl = await db_session.get(VirtCluster, inst.cluster_id)
    assert cl.type == "hyper-v"
    assert cl.name == inst.name


# ── API ──

def _payload(**kw: Any) -> dict[str, Any]:
    return {"name": f"hv-{uuid.uuid4().hex[:6]}", "mode": "hosts", "host": "hv1.corp.example",
            "extra_hosts": "hv2.corp.example", "port": 5985, "use_ssl": False, "verify_tls": True,
            "username": "CORP\\svc-ipam", "password": "Secret-2026", "enabled": True,
            "sync_interval_seconds": 600, "scope_subnet_ids": None, "auto_create_ips": False,
            "description": None, **kw}


async def test_api_crud_and_secrets(client, auth_headers, db_session, monkeypatch) -> None:
    from app.models.audit import AuditLog
    r = await client.post("/api/v1/hyperv", json=_payload(), headers=auth_headers)
    assert r.status_code == 201, r.text
    body = r.json()
    assert "password" not in body
    assert not any("password" in k for k in body)
    iid = body["id"]
    inst = await db_session.get(HyperVInstance, uuid.UUID(iid))
    assert hyperv._decrypt_password(inst) == "Secret-2026"
    assert (await client.post("/api/v1/hyperv", json=_payload(host="http://x/"), headers=auth_headers)).status_code == 422
    r = await client.patch(f"/api/v1/hyperv/{iid}", json={"mode": "scvmm", "password": "New-2026"},
                           headers=auth_headers)
    assert r.status_code == 200, r.text
    await db_session.refresh(inst)
    assert hyperv._decrypt_password(inst) == "New-2026"
    _fake(monkeypatch, {"hv1.corp.example": {"vms": [], "hosts": [{}]}, "hv2.corp.example": {"vms": []}})
    r = await client.post(f"/api/v1/hyperv/{iid}/test", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["steps"][0]["step"] == "WinRM hv1.corp.example"
    acts = set((await db_session.execute(select(AuditLog.action).where(AuditLog.object_id == iid))).scalars())
    assert {"create", "update"} <= acts
    assert (await client.delete(f"/api/v1/hyperv/{iid}", headers=auth_headers)).status_code == 204


async def test_api_is_admin_only(client, db_session) -> None:
    from app.core.security import hash_password
    from app.models.user import User
    from app.services.auth import issue_access_token
    u = User(username=f"na-{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@t.local",
             password_hash=hash_password("TestPassword2026!"), is_admin=False, is_active=True)
    db_session.add(u)
    await db_session.commit()
    h = {"Authorization": f"Bearer {issue_access_token(u)}"}
    assert (await client.get("/api/v1/hyperv", headers=h)).status_code == 403
    assert (await client.post("/api/v1/hyperv", json=_payload(), headers=h)).status_code == 403


async def test_delete_takes_back_what_it_wrote(client, auth_headers, db_session, monkeypatch) -> None:
    dev = Device(name="hv1", type="server")
    db_session.add(dev)
    inst = await _inst(db_session)
    _fake(monkeypatch, {"hv1": _host("hv1", cluster=None, vms=[_vm(G1, "a")],
                                     adapters=[{"name": "NIC1", "desc": "d", "mac": "002590000001"}])})
    await hyperv.sync_instance(db_session, inst)
    await db_session.commit()
    cluster_id, inst_id = inst.cluster_id, inst.id
    assert (await db_session.execute(select(DevicePort).where(
        DevicePort.source_origin == f"hyperv:{inst_id}"))).scalars().first() is not None
    r = await client.delete(f"/api/v1/hyperv/{inst_id}", headers=auth_headers)
    assert r.status_code == 204
    db_session.expire_all()
    assert not (await db_session.execute(select(VirtualMachine).where(
        VirtualMachine.cluster_id == cluster_id))).scalars().first()
    assert not (await db_session.execute(select(DevicePort).where(
        DevicePort.source_origin == f"hyperv:{inst_id}"))).scalars().first()
    assert await db_session.get(VirtCluster, cluster_id) is not None    # 叢集留著（可能有使用者設的地點）


def test_disk_size_is_parsed_and_zero_means_unknown() -> None:
    """虛擬磁碟大小（2026-10-21 客戶第一次接真機，清單補規格欄）：腳本讀不到（VHD 放在 SMB 共享，WinRM
    第二跳被拒）時給 null；0 也當不知道，不要顯示成「0 GB」。"""
    from app.services.hyperv import _HOST_SCRIPT, _SCVMM_SCRIPT, normalize_vm
    gid = "6f1c2c1e-0000-4000-8000-000000000007"
    assert normalize_vm({"id": gid, "name": "a", "disk_gb": 120}, host="hv1")["disk_gb"] == 120
    assert normalize_vm({"id": gid, "name": "a", "disk_gb": 0}, host="hv1")["disk_gb"] is None
    assert normalize_vm({"id": gid, "name": "a"}, host="hv1")["disk_gb"] is None
    # 讀磁碟一定包在 try 裡：一台 VM 讀不到不可以讓整份清單失敗
    assert "Get-VHD" in _HOST_SCRIPT and "disk_gb = $(try" in _HOST_SCRIPT
    assert "VirtualDiskDrives" in _SCVMM_SCRIPT and "disk_gb = $(try" in _SCVMM_SCRIPT
