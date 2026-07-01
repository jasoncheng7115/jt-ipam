"""Proxmox 同步（mock _api_get，path 分派）：獨立節點拉一台 qemu VM →
upsert VirtualMachine + 解析 netN 介面；冪等更新。"""

from __future__ import annotations

from sqlalchemy import select

from app.models.virt import (
    ProxmoxInstance,
    VirtCluster,
    VirtualMachine,
    VMInterface,
)
from app.services import proxmox as px

_RESP = {
    "/api2/json/version": {"data": {"version": "8.1"}},
    "/api2/json/cluster/status": {"data": [
        {"type": "node", "name": "pve1", "ip": "10.3.0.1"},
    ]},
    "/api2/json/nodes": {"data": [{"node": "pve1"}]},
    "/api2/json/nodes/pve1/network": {"data": []},
    "/api2/json/nodes/pve1/qemu": {"data": [
        {"vmid": 100, "name": "web-vm", "status": "stopped",
         "cpus": 2, "maxmem": 2147483648, "maxdisk": 10737418240},
    ]},
    "/api2/json/nodes/pve1/lxc": {"data": []},
    "/api2/json/nodes/pve1/qemu/100/config": {"data": {
        "net0": "virtio=AA:BB:CC:DD:EE:FF,bridge=vmbr0,tag=10",
    }},
}


def _patch(monkeypatch, resp=_RESP):
    async def _fake(_session, _instance, path, *, base_url=None, timeout=None):
        return resp.get(path, {"data": []})
    monkeypatch.setattr(px, "_api_get", _fake)


async def _instance(session) -> ProxmoxInstance:
    inst = ProxmoxInstance(
        api_url="https://pve.example.com:8006", auth_username="root@pam",
        auth_token_id="jtipam",
    )
    session.add(inst)
    await session.flush()
    return inst


async def test_sync_standalone_one_vm(db_session, monkeypatch):
    _patch(monkeypatch)
    inst = await _instance(db_session)
    summary = await px.sync_instance(db_session, inst)

    assert summary.cluster == "pve1"
    assert summary.vms_seen == 1
    assert summary.vms_inserted == 1

    cl = (await db_session.execute(
        select(VirtCluster).where(VirtCluster.name == "pve1")
    )).scalar_one()
    assert cl.is_standalone is True

    vm = (await db_session.execute(
        select(VirtualMachine).where(VirtualMachine.legacy_vmid == 100)
    )).scalar_one()
    assert vm.name == "web-vm"
    assert vm.kind == "vm"
    assert vm.status == "stopped"
    assert vm.vcpus == 2
    assert vm.memory_mb == 2048
    assert vm.disk_gb == 10

    itf = (await db_session.execute(
        select(VMInterface).where(VMInterface.vm_id == vm.id)
    )).scalar_one()
    assert itf.name == "net0"
    assert str(itf.mac).lower() == "aa:bb:cc:dd:ee:ff"
    assert itf.bridge == "vmbr0"


async def test_sync_is_idempotent(db_session, monkeypatch):
    _patch(monkeypatch)
    inst = await _instance(db_session)
    await px.sync_instance(db_session, inst)

    # 第二次：狀態變 running → 更新同一筆，不新增
    resp2 = dict(_RESP)
    resp2["/api2/json/nodes/pve1/qemu"] = {"data": [
        {"vmid": 100, "name": "web-vm", "status": "running",
         "cpus": 4, "maxmem": 2147483648, "maxdisk": 10737418240},
    ]}
    # running 會打 agent → 給空 result
    resp2["/api2/json/nodes/pve1/qemu/100/agent/network-get-interfaces"] = {"data": {"result": []}}
    _patch(monkeypatch, resp2)
    summary = await px.sync_instance(db_session, inst)
    assert summary.vms_updated == 1
    assert summary.vms_inserted == 0

    vms = (await db_session.execute(select(VirtualMachine))).scalars().all()
    assert len(vms) == 1
    assert vms[0].status == "running"
    assert vms[0].vcpus == 4
