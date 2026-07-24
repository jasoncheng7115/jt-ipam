"""虛擬化 → 叢集刪除（客戶回報：手動新增的叢集無法刪除 → 之前根本沒有 DELETE 端點）。

規則：只有「無虛擬機、無 Proxmox 連線」的叢集可刪；否則 409（避免誤刪同步資料）。
"""

from __future__ import annotations

import uuid


async def _make_cluster(client, auth_headers, name: str) -> str:
    resp = await client.post("/api/v1/virt/clusters", headers=auth_headers, json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_delete_empty_cluster(client, auth_headers, admin_user):  # type: ignore[no-untyped-def]
    cid = await _make_cluster(client, auth_headers, f"manual-{uuid.uuid4().hex[:6]}")
    resp = await client.delete(f"/api/v1/virt/clusters/{cid}", headers=auth_headers)
    assert resp.status_code == 204, resp.text
    # 再刪一次 → 404
    resp2 = await client.delete(f"/api/v1/virt/clusters/{cid}", headers=auth_headers)
    assert resp2.status_code == 404


async def test_delete_cluster_with_vm_blocked(client, db_session, auth_headers, admin_user):  # type: ignore[no-untyped-def]
    from app.models.virt import VirtualMachine
    cid = await _make_cluster(client, auth_headers, f"withvm-{uuid.uuid4().hex[:6]}")
    db_session.add(VirtualMachine(cluster_id=uuid.UUID(cid), name="vm1"))
    await db_session.commit()
    resp = await client.delete(f"/api/v1/virt/clusters/{cid}", headers=auth_headers)
    assert resp.status_code == 409, resp.text
    assert "虛擬機" in resp.json()["detail"]


async def test_delete_cluster_with_proxmox_blocked(client, db_session, auth_headers, admin_user):  # type: ignore[no-untyped-def]
    from app.models.virt import ProxmoxInstance
    cid = await _make_cluster(client, auth_headers, f"withpx-{uuid.uuid4().hex[:6]}")
    db_session.add(ProxmoxInstance(
        cluster_id=uuid.UUID(cid), api_url="https://pve.local:8006",
        auth_username="root@pam", auth_token_id="jt",
    ))
    await db_session.commit()
    resp = await client.delete(f"/api/v1/virt/clusters/{cid}", headers=auth_headers)
    assert resp.status_code == 409, resp.text
    assert "Proxmox" in resp.json()["detail"]


async def test_delete_cluster_requires_admin(client, db_session):  # type: ignore[no-untyped-def]
    # 無授權 → 401（require_admin）
    resp = await client.delete(f"/api/v1/virt/clusters/{uuid.uuid4()}")
    assert resp.status_code in (401, 403)
