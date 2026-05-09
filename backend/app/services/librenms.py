"""LibreNMS API client + 同步邏輯。

API 文件：https://docs.librenms.org/API/

OWASP 對應：
- A02：API token AES-GCM 加密儲存（aad 綁 instance id）
- A05：所有對外請求走 safe_http；timeout 必填
- A09：每次 sync 結果寫 audit summary
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
from app.models.address import IPAddress
from app.models.librenms import (
    ARPEntry,
    FDBEntry,
    LibreNMSDevice,
    LibreNMSInstance,
)


class LibreNMSError(Exception):
    pass


def encrypt_instance_token(instance_id, raw: str) -> tuple[bytes, bytes]:  # type: ignore[no-untyped-def]
    return encrypt_secret(raw, aad=_aad(instance_id))


def _aad(instance_id) -> bytes:  # type: ignore[no-untyped-def]
    return f"librenms_instance:{instance_id}:api_token".encode("utf-8")


def _decrypt_token(instance: LibreNMSInstance) -> str:
    return decrypt_secret(
        instance.api_token_enc, instance.api_token_nonce, aad=_aad(instance.id)
    ).decode("utf-8")


# ─────────────────── 低階 HTTP ───────────────────


async def _api_get(instance: LibreNMSInstance, path: str, *, timeout: float = 30.0) -> dict[str, Any]:
    url = f"{instance.api_url.rstrip('/')}{path}"
    token = _decrypt_token(instance)
    try:
        resp = await safe_request(
            "GET", url,
            headers={"X-Auth-Token": token, "Accept": "application/json"},
            timeout=timeout,
        )
    except UnsafeOutboundURL as exc:
        raise LibreNMSError(f"SSRF guard rejected URL: {exc}") from exc
    except httpx.HTTPError as exc:
        raise LibreNMSError(f"transport: {exc.__class__.__name__}") from exc
    if resp.status_code != 200:
        raise LibreNMSError(f"LibreNMS {path}: {resp.status_code} {resp.text[:200]}")
    return resp.json()


async def _api_post(instance: LibreNMSInstance, path: str, body: dict, *,
                    timeout: float = 30.0) -> dict[str, Any]:  # type: ignore[type-arg]
    url = f"{instance.api_url.rstrip('/')}{path}"
    token = _decrypt_token(instance)
    try:
        resp = await safe_request(
            "POST", url,
            headers={"X-Auth-Token": token, "Content-Type": "application/json"},
            json=body, timeout=timeout,
        )
    except UnsafeOutboundURL as exc:
        raise LibreNMSError(f"SSRF guard rejected URL: {exc}") from exc
    if resp.status_code not in (200, 201):
        raise LibreNMSError(f"LibreNMS POST {path}: {resp.status_code} {resp.text[:200]}")
    return resp.json()


async def healthcheck(instance: LibreNMSInstance) -> dict[str, Any]:
    return await _api_get(instance, "/api/v0/system", timeout=8.0)


# ─────────────────── 結果結構 ───────────────────


@dataclass
class SyncSummary:
    instance: str = ""
    devices_seen: int = 0
    devices_inserted: int = 0
    devices_updated: int = 0
    arp_seen: int = 0
    arp_inserted: int = 0
    arp_updated: int = 0
    fdb_seen: int = 0
    fdb_inserted: int = 0
    fdb_updated: int = 0
    ip_mac_filled: int = 0   # 自動把 ARP 學到的 MAC 填回 IPAddress 表
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance": self.instance,
            "devices": {
                "seen": self.devices_seen,
                "inserted": self.devices_inserted,
                "updated": self.devices_updated,
            },
            "arp": {
                "seen": self.arp_seen,
                "inserted": self.arp_inserted,
                "updated": self.arp_updated,
            },
            "fdb": {
                "seen": self.fdb_seen,
                "inserted": self.fdb_inserted,
                "updated": self.fdb_updated,
            },
            "ip_mac_filled": self.ip_mac_filled,
            "errors": self.errors[:20],
        }


# ─────────────────── 同步：裝置 ───────────────────


async def sync_devices(
    session: AsyncSession, instance: LibreNMSInstance,
) -> tuple[int, int, int]:
    """從 LibreNMS 抓所有 devices；回傳 (seen, inserted, updated)。"""
    data = await _api_get(instance, "/api/v0/devices")
    devices = data.get("devices") or []
    seen = inserted = updated = 0

    for d in devices:
        legacy = int(d.get("device_id"))
        seen += 1
        existing = (
            await session.execute(
                select(LibreNMSDevice).where(
                    LibreNMSDevice.instance_id == instance.id,
                    LibreNMSDevice.legacy_device_id == legacy,
                )
            )
        ).scalar_one_or_none()

        primary_ip = d.get("ip") or d.get("ip_address") or d.get("snmp_ip")

        if existing is None:
            obj = LibreNMSDevice(
                instance_id=instance.id,
                legacy_device_id=legacy,
                hostname=d.get("hostname"),
                sysname=d.get("sysName"),
                primary_ip=primary_ip,
                hardware=d.get("hardware"),
                os=d.get("os"),
                version=d.get("version"),
                serial=d.get("serial"),
                sysObjectID=d.get("sysObjectID"),
                uptime=int(d.get("uptime") or 0) or None,
                status=str(d.get("status") or "unknown")[:16],
                last_seen_at=datetime.now(UTC),
            )
            session.add(obj)
            inserted += 1
        else:
            existing.hostname = d.get("hostname")
            existing.sysname = d.get("sysName")
            existing.primary_ip = primary_ip
            existing.hardware = d.get("hardware")
            existing.os = d.get("os")
            existing.version = d.get("version")
            existing.serial = d.get("serial")
            existing.sysObjectID = d.get("sysObjectID")
            existing.uptime = int(d.get("uptime") or 0) or None
            existing.status = str(d.get("status") or "unknown")[:16]
            existing.last_seen_at = datetime.now(UTC)
            updated += 1
    return seen, inserted, updated


# ─────────────────── 同步：ARP ───────────────────


async def sync_arp(
    session: AsyncSession, instance: LibreNMSInstance,
) -> tuple[int, int, int, int]:
    """逐 device 抓 ARP；回傳 (seen, inserted, updated, ip_mac_filled)。"""
    devices = list(
        (await session.execute(
            select(LibreNMSDevice).where(
                LibreNMSDevice.instance_id == instance.id,
            )
        )).scalars().all()
    )
    seen = inserted = updated = filled = 0
    now = datetime.now(UTC)

    for d in devices:
        path = f"/api/v0/devices/{d.legacy_device_id}/ip/arp/all"
        try:
            data = await _api_get(instance, path, timeout=20.0)
        except LibreNMSError:
            continue   # device 可能不支援 ARP（例如非 L3）
        for arp in data.get("arp") or []:
            ip = arp.get("ipv4_address") or arp.get("ip_address")
            mac = arp.get("mac_address")
            if not ip or not mac:
                continue
            mac = mac.lower()
            seen += 1
            existing = (
                await session.execute(
                    select(ARPEntry).where(
                        ARPEntry.ip == ip,
                        ARPEntry.mac == mac,
                        ARPEntry.device_id == d.id,
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                session.add(ARPEntry(
                    ip=ip, mac=mac,
                    instance_id=instance.id, device_id=d.id,
                    interface=arp.get("port_name") or arp.get("interface"),
                    vrf=arp.get("context_name"),
                    source="librenms",
                    first_seen_at=now, last_seen_at=now,
                ))
                inserted += 1
            else:
                existing.last_seen_at = now
                updated += 1

            # 自動補 IP 的 MAC（A09：明確記錄為 librenms 來源）
            ipa = (
                await session.execute(select(IPAddress).where(IPAddress.ip == ip))
            ).scalar_one_or_none()
            if ipa is not None and ipa.mac is None:
                ipa.mac = mac
                ipa.last_seen_librenms = now
                filled += 1

    return seen, inserted, updated, filled


# ─────────────────── 同步：FDB ───────────────────


async def sync_fdb(
    session: AsyncSession, instance: LibreNMSInstance,
) -> tuple[int, int, int]:
    devices = list(
        (await session.execute(
            select(LibreNMSDevice).where(
                LibreNMSDevice.instance_id == instance.id,
            )
        )).scalars().all()
    )
    seen = inserted = updated = 0
    now = datetime.now(UTC)

    for d in devices:
        path = f"/api/v0/devices/{d.legacy_device_id}/fdb"
        try:
            data = await _api_get(instance, path, timeout=20.0)
        except LibreNMSError:
            continue
        for entry in data.get("ports_fdb") or []:
            mac = entry.get("mac_address")
            if not mac:
                continue
            mac = mac.lower()
            vlan = entry.get("vlan_id")
            try:
                vlan_int = int(vlan) if vlan is not None else None
            except (ValueError, TypeError):
                vlan_int = None
            port_name = entry.get("ifName") or entry.get("port_name")
            seen += 1
            existing = (
                await session.execute(
                    select(FDBEntry).where(
                        FDBEntry.mac == mac,
                        FDBEntry.device_id == d.id,
                        FDBEntry.port_name == port_name,
                        FDBEntry.vlan_id_num == vlan_int,
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                session.add(FDBEntry(
                    mac=mac, vlan_id_num=vlan_int,
                    instance_id=instance.id, device_id=d.id,
                    port_name=port_name, source="librenms",
                    first_seen_at=now, last_seen_at=now,
                ))
                inserted += 1
            else:
                existing.last_seen_at = now
                updated += 1
    return seen, inserted, updated


# ─────────────────── effective_status 計算 ───────────────────


async def recompute_effective_status(
    session: AsyncSession, instance: LibreNMSInstance,
) -> int:
    """規格書 §6.4.2 對照表：用 ARP 學到的 MAC + 最近 last_seen_arp 推 online。

    保守規則：
    - last_seen_librenms 在過去 30 分鐘內 → online
    - last_seen_scanner 在過去 30 分鐘內 → online
    - 兩者都很久沒見 → offline
    - 兩者都從沒見過 → unknown
    """
    from datetime import timedelta
    now = datetime.now(UTC)
    cutoff = now - timedelta(minutes=30)

    rows = list(
        (await session.execute(select(IPAddress))).scalars().all()
    )
    updated = 0
    for ip in rows:
        s_seen = ip.last_seen_scanner
        l_seen = ip.last_seen_librenms
        # ARP 也算 librenms 證據
        if not l_seen and ip.mac:
            arp = (
                await session.execute(
                    select(ARPEntry.last_seen_at)
                    .where(ARPEntry.ip == str(ip.ip).split("/")[0])
                    .order_by(ARPEntry.last_seen_at.desc()).limit(1)
                )
            ).scalar_one_or_none()
            if arp:
                l_seen = arp
                ip.last_seen_librenms = arp

        new_status: str
        if (s_seen and s_seen >= cutoff) or (l_seen and l_seen >= cutoff):
            if (s_seen and s_seen >= cutoff) and (l_seen and l_seen >= cutoff):
                new_status = "online"
            elif s_seen and s_seen >= cutoff:
                new_status = "online (scanner)"
            else:
                new_status = "online (librenms)"
        elif s_seen or l_seen:
            new_status = "offline"
        else:
            new_status = "unknown"

        if ip.effective_status != new_status:
            ip.effective_status = new_status
            updated += 1
    return updated


# ─────────────────── 主入口 ───────────────────


async def sync_instance(
    session: AsyncSession, instance: LibreNMSInstance,
) -> SyncSummary:
    summary = SyncSummary(instance=instance.name)
    try:
        if instance.sync_devices:
            s, i, u = await sync_devices(session, instance)
            summary.devices_seen, summary.devices_inserted, summary.devices_updated = s, i, u
            await session.commit()
        if instance.sync_arp:
            s, i, u, f = await sync_arp(session, instance)
            summary.arp_seen, summary.arp_inserted, summary.arp_updated = s, i, u
            summary.ip_mac_filled = f
            await session.commit()
        if instance.sync_fdb:
            s, i, u = await sync_fdb(session, instance)
            summary.fdb_seen, summary.fdb_inserted, summary.fdb_updated = s, i, u
            await session.commit()
        if instance.use_for_status:
            await recompute_effective_status(session, instance)
            await session.commit()
        instance.last_sync_at = datetime.now(UTC)
        instance.last_error = None
    except LibreNMSError as exc:
        instance.last_error = str(exc)
        summary.errors.append(str(exc))
    await session.commit()
    return summary
