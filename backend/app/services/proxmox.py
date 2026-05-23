"""Proxmox VE 同步服務。

API：https://pve.proxmox.com/wiki/Proxmox_VE_API

認證走 API Token（推薦）：
  Authorization: PVEAPIToken=<USER@REALM>!<TOKEN_ID>=<TOKEN_SECRET>

OWASP A04：token secret 走 EncryptedSecret 表（aad 綁 instance id）；不在
ProxmoxInstance 上常駐
A05：所有對外請求一律走 safe_request；TLS verify 預設 True
A09：每次 sync 寫 audit summary
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.safe_http import UnsafeOutboundURL, safe_request
from app.core.security import decrypt_secret, encrypt_secret
from app.models.encrypted_secret import EncryptedSecret
from app.models.virt import (
    ProxmoxInstance,
    VirtCluster,
    VirtualMachine,
    VMInterface,
)


class ProxmoxError(Exception):
    pass


def _aad(instance_id) -> bytes:  # type: ignore[no-untyped-def]
    return f"proxmox_instance:{instance_id}:token_secret".encode("utf-8")


def encrypt_instance_secret(instance_id, raw: str) -> tuple[bytes, bytes]:  # type: ignore[no-untyped-def]
    return encrypt_secret(raw, aad=_aad(instance_id))


async def _get_secret(session: AsyncSession, instance: ProxmoxInstance) -> str:
    row = (
        await session.execute(
            select(EncryptedSecret).where(
                EncryptedSecret.object_type == "proxmox_instance",
                EncryptedSecret.object_id == instance.id,
                EncryptedSecret.field == "token_secret",
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise ProxmoxError(
            f"Proxmox instance {instance.id} has no token secret stored"
        )
    return decrypt_secret(row.ciphertext, row.nonce, aad=_aad(instance.id)).decode("utf-8")


def _auth_header(instance: ProxmoxInstance, secret: str) -> dict[str, str]:
    return {
        "Authorization": (
            f"PVEAPIToken={instance.auth_username}!"
            f"{instance.auth_token_id}={secret}"
        )
    }


async def _api_get(session: AsyncSession, instance: ProxmoxInstance, path: str) -> dict[str, Any]:
    secret = await _get_secret(session, instance)
    url = f"{instance.api_url.rstrip('/')}{path}"
    try:
        resp = await safe_request("GET", url, headers=_auth_header(instance, secret), timeout=20.0)
    except UnsafeOutboundURL as exc:
        raise ProxmoxError(f"SSRF guard rejected URL: {exc}") from exc
    except httpx.HTTPError as exc:
        raise ProxmoxError(f"transport: {exc.__class__.__name__}") from exc
    if resp.status_code != 200:
        raise ProxmoxError(f"Proxmox {path}: {resp.status_code} {resp.text[:200]}")
    return resp.json()


@dataclass
class SyncSummary:
    cluster: str = ""
    nodes_seen: int = 0
    vms_seen: int = 0
    vms_inserted: int = 0
    vms_updated: int = 0
    interfaces_seen: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cluster": self.cluster,
            "nodes_seen": self.nodes_seen,
            "vms_seen": self.vms_seen,
            "vms_inserted": self.vms_inserted,
            "vms_updated": self.vms_updated,
            "interfaces_seen": self.interfaces_seen,
            "errors": self.errors[:20],
        }


async def healthcheck(session: AsyncSession, instance: ProxmoxInstance) -> dict[str, Any]:
    return await _api_get(session, instance, "/api2/json/version")


async def sync_instance(
    session: AsyncSession, instance: ProxmoxInstance,
) -> SyncSummary:
    """從 Proxmox cluster 拉所有 VM/CT 並 upsert 到 jt-ipam 對映表。"""
    cluster = await session.get(VirtCluster, instance.cluster_id)
    if cluster is None:
        raise ProxmoxError(f"Cluster {instance.cluster_id} not found")

    summary = SyncSummary(cluster=cluster.name)
    try:
        nodes_data = await _api_get(session, instance, "/api2/json/nodes")
    except ProxmoxError as exc:
        instance.last_error = str(exc)
        summary.errors.append(str(exc))
        await session.commit()
        return summary

    nodes = nodes_data.get("data") or []
    summary.nodes_seen = len(nodes)

    for node in nodes:
        node_name = node.get("node")
        if not node_name:
            continue
        # VMs (qemu)
        try:
            vms = (await _api_get(
                session, instance, f"/api2/json/nodes/{node_name}/qemu"
            )).get("data") or []
        except ProxmoxError as exc:
            summary.errors.append(f"{node_name}/qemu: {exc}")
            vms = []
        # Containers (lxc)
        try:
            cts = (await _api_get(
                session, instance, f"/api2/json/nodes/{node_name}/lxc"
            )).get("data") or []
        except ProxmoxError as exc:
            summary.errors.append(f"{node_name}/lxc: {exc}")
            cts = []

        for entry in [*vms, *cts]:
            vmid = int(entry.get("vmid") or 0)
            if vmid == 0:
                continue
            summary.vms_seen += 1
            existing = (
                await session.execute(
                    select(VirtualMachine).where(
                        VirtualMachine.cluster_id == cluster.id,
                        VirtualMachine.legacy_vmid == vmid,
                    )
                )
            ).scalar_one_or_none()

            status = str(entry.get("status") or "unknown")
            if status not in (
                "running", "stopped", "paused", "migrating", "unknown"
            ):
                status = "unknown"

            if existing is None:
                vm = VirtualMachine(
                    cluster_id=cluster.id,
                    legacy_vmid=vmid,
                    name=entry.get("name") or f"vm-{vmid}",
                    status=status,
                    vcpus=int(entry.get("cpus") or 0) or None,
                    memory_mb=int(entry.get("maxmem") or 0) // (1024 * 1024) or None,
                    disk_gb=int(entry.get("maxdisk") or 0) // (1024**3) or None,
                    is_template=bool(entry.get("template", False)),
                )
                session.add(vm)
                await session.flush()
                summary.vms_inserted += 1
            else:
                existing.name = entry.get("name") or existing.name
                existing.status = status
                existing.vcpus = int(entry.get("cpus") or 0) or None
                existing.memory_mb = int(entry.get("maxmem") or 0) // (1024 * 1024) or None
                existing.disk_gb = int(entry.get("maxdisk") or 0) // (1024**3) or None
                existing.is_template = bool(entry.get("template", False))
                summary.vms_updated += 1
                vm = existing

            # 抓網卡資訊（qemu 才有 /config）
            try:
                cfg = (await _api_get(
                    session, instance,
                    f"/api2/json/nodes/{node_name}/qemu/{vmid}/config",
                )).get("data") or {}
            except ProxmoxError:
                cfg = {}

            for key, raw in cfg.items():
                if not key.startswith("net"):
                    continue
                summary.interfaces_seen += 1
                # 例：virtio=AA:BB:CC:DD:EE:FF,bridge=vmbr0,tag=10
                parts = dict(
                    p.split("=", 1) for p in raw.split(",") if "=" in p
                )
                # 第一段（model=mac）
                first_pair = next(
                    (p for p in raw.split(",") if "=" in p and ":" in p.split("=", 1)[1]),
                    None,
                )
                mac = None
                if first_pair:
                    mac_candidate = first_pair.split("=", 1)[1].split(":", 6)
                    if len(mac_candidate) == 6:
                        mac = ":".join(mac_candidate).lower()
                bridge = parts.get("bridge")

                ifobj = (
                    await session.execute(
                        select(VMInterface).where(
                            VMInterface.vm_id == vm.id, VMInterface.name == key,
                        )
                    )
                ).scalar_one_or_none()
                if ifobj is None:
                    session.add(VMInterface(
                        vm_id=vm.id, name=key, mac=mac, bridge=bridge,
                    ))
                else:
                    ifobj.mac = mac
                    ifobj.bridge = bridge

    instance.last_sync_at = datetime.now(UTC)
    instance.last_error = None
    await session.commit()
    return summary
