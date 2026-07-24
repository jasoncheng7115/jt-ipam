"""虛擬化 → 叢集刪除（客戶回報：手動新增的叢集無法刪除 → 之前根本沒有 DELETE 端點）。

行為：任何叢集都能刪（含還接著 PVE 的活叢集，因為可能決定不接 PVE 了）。
- cluster 對 proxmox_instances / virtual_machines 的 FK 是 CASCADE → 連帶清整個虛擬化子樹
- VM 對 ip_addresses / devices / vlans 是「指向 + SET NULL」→ 這些核心資料不受影響
- cascade 清不到的 encrypted_secrets（PVE token）與 background_tasks（心跳）由端點手動補清
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select


async def _make_cluster(client, auth_headers, name: str) -> str:
    resp = await client.post("/api/v1/virt/clusters", headers=auth_headers, json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_delete_empty_cluster(client, auth_headers, admin_user):  # type: ignore[no-untyped-def]
    cid = await _make_cluster(client, auth_headers, f"manual-{uuid.uuid4().hex[:6]}")
    resp = await client.delete(f"/api/v1/virt/clusters/{cid}", headers=auth_headers)
    assert resp.status_code == 204, resp.text
    resp2 = await client.delete(f"/api/v1/virt/clusters/{cid}", headers=auth_headers)
    assert resp2.status_code == 404


async def test_delete_live_cluster_cascades_but_keeps_core_data(client, db_session, auth_headers, admin_user):  # type: ignore[no-untyped-def]
    from app.models.background_task import BackgroundTask
    from app.models.device import Device
    from app.models.encrypted_secret import EncryptedSecret
    from app.models.virt import ProxmoxInstance, VirtualMachine, VMInterface

    cid = uuid.UUID(await _make_cluster(client, auth_headers, f"live-{uuid.uuid4().hex[:6]}"))

    # 一台會存活的核心裝置（VM 指向它，SET NULL）
    dev = Device(name=f"host-{uuid.uuid4().hex[:6]}")
    db_session.add(dev)
    await db_session.flush()

    vm = VirtualMachine(cluster_id=cid, name="vm1", device_id=dev.id)
    db_session.add(vm)
    await db_session.flush()
    db_session.add(VMInterface(vm_id=vm.id, name="net0"))

    px = ProxmoxInstance(cluster_id=cid, api_url="https://pve.local:8006",
                         auth_username="root@pam", auth_token_id="jt")
    db_session.add(px)
    await db_session.flush()
    # PVE token 密文（非 FK，cascade 不會清）
    db_session.add(EncryptedSecret(object_type="proxmox_instance", object_id=px.id,
                                   field="token_secret", ciphertext=b"x", nonce=b"y"))
    # 排程同步心跳列（非 FK）
    db_session.add(BackgroundTask(kind="proxmox.sync", status="succeeded", trigger="scheduled",
                                  target_type="proxmox_cluster", target_id=cid, progress=100))
    await db_session.commit()

    resp = await client.delete(f"/api/v1/virt/clusters/{cid}", headers=auth_headers)
    assert resp.status_code == 204, resp.text

    # 虛擬化子樹全清
    assert await db_session.scalar(select(func.count()).select_from(VirtualMachine).where(VirtualMachine.cluster_id == cid)) == 0
    assert await db_session.scalar(select(func.count()).select_from(VMInterface).where(VMInterface.vm_id == vm.id)) == 0
    assert await db_session.scalar(select(func.count()).select_from(ProxmoxInstance).where(ProxmoxInstance.id == px.id)) == 0
    # cascade 清不到的孤兒也被手動清掉
    assert await db_session.scalar(select(func.count()).select_from(EncryptedSecret).where(EncryptedSecret.object_id == px.id)) == 0
    assert await db_session.scalar(select(func.count()).select_from(BackgroundTask).where(BackgroundTask.target_id == cid)) == 0
    # 核心資料存活：裝置還在（count 直接打 DB，避開 session 身分對應快取）
    assert await db_session.scalar(select(func.count()).select_from(Device).where(Device.id == dev.id)) == 1


async def test_delete_cluster_requires_admin(client, db_session):  # type: ignore[no-untyped-def]
    resp = await client.delete(f"/api/v1/virt/clusters/{uuid.uuid4()}")
    assert resp.status_code in (401, 403)
