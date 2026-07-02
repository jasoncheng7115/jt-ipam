"""LibreNMS API client + 同步邏輯。

API 文件：https://docs.librenms.org/API/

OWASP 對應：
- A02：API token AES-GCM 加密儲存（aad 綁 instance id）
- A05：所有對外請求走 safe_http；timeout 必填
- A09：每次 sync 結果寫 audit summary
"""

from __future__ import annotations

import ipaddress
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import delete, select
from sqlalchemy import true as sa_true
from sqlalchemy.dialects.postgresql import insert as pg_insert
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
from app.models.physical import DevicePort
from app.models.subnet import Subnet
from app.models.vlan import VLAN, DeviceVLAN, VLANDomain
from app.services.hostname import apply_observation
from app.services.ip_history import log_change

LIBRENMS_VLAN_DOMAIN = "LibreNMS"


def _scope_uuids(instance: LibreNMSInstance) -> set[Any]:
    """instance.scope_subnet_ids（JSONB 字串陣列）→ UUID set；空回空 set（不限範圍）。"""
    import uuid as _uuid
    out: set[Any] = set()
    for s in (instance.scope_subnet_ids or []):
        try:
            out.add(_uuid.UUID(str(s)))
        except (ValueError, TypeError):
            pass
    return out


def _looks_like_ip(s: str | None) -> bool:
    import ipaddress
    if not s:
        return True
    try:
        ipaddress.ip_address(s.strip())
        return True
    except ValueError:
        return False


class LibreNMSError(Exception):
    pass


def encrypt_instance_token(instance_id, raw: str) -> tuple[bytes, bytes]:  # type: ignore[no-untyped-def]
    return encrypt_secret(raw, aad=_aad(instance_id))


def _aad(instance_id) -> bytes:  # type: ignore[no-untyped-def]
    return f"librenms_instance:{instance_id}:api_token".encode()


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
            timeout=timeout, verify=instance.verify_tls,
        )
    except UnsafeOutboundURL as exc:
        raise LibreNMSError(f"SSRF guard rejected URL: {exc}") from exc
    except httpx.HTTPError as exc:
        raise LibreNMSError(f"transport: {exc.__class__.__name__}") from exc
    if resp.status_code != 200:
        raise LibreNMSError(f"LibreNMS {path}: {resp.status_code} {resp.text[:200]}")
    return resp.json()  # type: ignore[no-any-return]


async def _api_post(instance: LibreNMSInstance, path: str, body: dict[str, Any], *,
                    timeout: float = 30.0) -> dict[str, Any]:
    url = f"{instance.api_url.rstrip('/')}{path}"
    token = _decrypt_token(instance)
    try:
        resp = await safe_request(
            "POST", url,
            headers={"X-Auth-Token": token, "Content-Type": "application/json"},
            json=body, timeout=timeout, verify=instance.verify_tls,
        )
    except UnsafeOutboundURL as exc:
        raise LibreNMSError(f"SSRF guard rejected URL: {exc}") from exc
    if resp.status_code not in (200, 201):
        raise LibreNMSError(f"LibreNMS POST {path}: {resp.status_code} {resp.text[:200]}")
    return resp.json()  # type: ignore[no-any-return]


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
    vlans_seen: int = 0
    vlans_upserted: int = 0
    vlan_mappings: int = 0
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
            "vlans": {
                "seen": self.vlans_seen,
                "upserted": self.vlans_upserted,
                "mappings": self.vlan_mappings,
            },
            "ip_mac_filled": self.ip_mac_filled,
            "errors": self.errors[:20],
        }


# ─────────────────── 裝置連結（feature D）───────────────────


def _infer_device_type(ldev: LibreNMSDevice) -> str:
    """從 LibreNMS os/hardware/sysObjectID 粗略推 jt-ipam Device.type。"""
    blob = " ".join(filter(None, [ldev.os, ldev.hardware, ldev.sysObjectID or ""])).lower()
    if any(k in blob for k in ("firewall", "pfsense", "opnsense", "fortigate", "fortios",
                               "palo alto", "panos", "asa", "sonicwall", "checkpoint")):
        return "firewall"
    # AP 要排在 switch 前面：unifi/aruba 等常同時含 wireless 與 switch 字樣
    if any(k in blob for k in ("access point", "wireless", "aironet", "unifi", "wifi",
                               "aruba", "ruckus", "meraki mr", "airos", "openwrt-ap")):
        return "ap"
    if any(k in blob for k in ("switch", "catalyst", "nexus", "procurve", "powerconnect",
                               "ex2", "ex3", "ex4", "dlink", "d-link", "dgs", "des",
                               "mellanox", "onyx", "arista", "comware", "junos")):
        return "switch"
    if any(k in blob for k in ("router", "ios-xe", "routeros", "mikrotik", "vyos", "isr",
                               "draytek", "vigor", "edgeos", "openwrt")):
        return "router"
    if any(k in blob for k in ("proxmox", "linux", "windows", "ubuntu", "debian", "centos",
                               "freebsd", "esxi", "vmware", "dsm", "synology", "truenas",
                               "freenas", "macos", "server")):
        return "server"
    return "other"


async def link_librenms_device(
    session: AsyncSession, ldev: LibreNMSDevice, *, create: bool = True,
    scope_ids: set[Any] | None = None,
) -> tuple[uuid.UUID | None, bool]:
    """把一台 LibreNMS device 連到 jt-ipam Device（feature D）。

    順序：已連結 → primary_ip 對到的 IPAddress.device_id → 同名 Device → 新建。
    回傳 (jt_ipam_device_id, 是否新建)。純讀 LibreNMS、只寫 jt-ipam 自己的表。
    """
    from app.models.device import Device

    if ldev.jt_ipam_device_id is not None:
        return ldev.jt_ipam_device_id, False

    # 1. 用 primary_ip 對到已存在的 IPAddress → 其 device_id
    ipa = None
    if ldev.primary_ip:
        ipa = (await session.execute(
            select(IPAddress)
            .where(IPAddress.ip == ldev.primary_ip)
            .where(IPAddress.subnet_id.in_(scope_ids) if scope_ids else sa_true())
            .limit(1)
        )).scalars().first()
        if ipa is not None and ipa.device_id is not None:
            ldev.jt_ipam_device_id = ipa.device_id
            return ipa.device_id, False

    # 依「裝置名稱來源優先序」設定挑名稱：hostname→librenms、sysName→snmp。
    # 設定可把 librenms 排在 snmp 前/後或停用某來源；resolver 回 None 時退回原本邏輯。
    from app.services.device_name_precedence import resolve_device_name
    _cand: dict[str, str] = {}
    if ldev.hostname:
        _cand["librenms"] = str(ldev.hostname)
    if ldev.sysname:
        _cand["snmp"] = str(ldev.sysname)
    name = (
        (await resolve_device_name(session, _cand))
        or ldev.sysname or ldev.hostname or ldev.primary_ip
        or f"librenms-{ldev.legacy_device_id}"
    ).strip()

    # 2. 同名 jt-ipam Device
    dev = (await session.execute(
        select(Device).where(Device.name == name)
    )).scalar_one_or_none()
    created = False
    if dev is None:
        if not create:
            return None, False
        from app.services.model_precedence import resolve_device_model
        model_val = await resolve_device_model(session, {"librenms": str(ldev.hardware or "")})
        dev = Device(
            name=name, type=_infer_device_type(ldev),
            vendor=ldev.os or ldev.hardware, model=model_val, serial=ldev.serial,
        )
        session.add(dev)
        await session.flush()
        created = True
        # 順手把 primary_ip 的 IPAddress 掛到這台（若有且未掛）
        if ipa is not None and ipa.device_id is None:
            ipa.device_id = dev.id

    ldev.jt_ipam_device_id = dev.id
    return dev.id, created


# ─────────────────── 同步：裝置 ───────────────────


async def _addable_subnets(
    session: AsyncSession, scope_ids: set[Any],
) -> list[tuple[Any, Any]]:
    """回傳可自動建立 IP 的候選子網路 [(ip_network, subnet_id)]，依首碼長度由長到短排序
    （最精確的子網路優先），給 auto_create_ips 用 longest-prefix 比對落點子網路。
    scope_ids 有值＝只在這些子網路內建（重疊網段安全）；空＝全部既有子網路。"""
    stmt = select(Subnet.id, Subnet.cidr)
    if scope_ids:
        stmt = stmt.where(Subnet.id.in_(scope_ids))
    rows = (await session.execute(stmt)).all()
    nets: list[tuple[Any, Any]] = []
    for sid, cidr in rows:
        try:
            nets.append((ipaddress.ip_network(str(cidr), strict=False), sid))
        except ValueError:
            continue
    nets.sort(key=lambda x: x[0].prefixlen, reverse=True)
    return nets


def _pick_subnet_for_ip(nets: list[tuple[Any, Any]], aip: Any) -> Any | None:
    """從候選子網路挑「唯一且最精確」包含此 IP 的子網路，回 subnet_id。

    避免建錯子網路：
    - 多層巢狀（如 10.0.0.0/8 與 10.1.1.0/24 都包含）→ 取最長首碼（最精確）那個。
    - **重疊網段歧義**（多個「相同最長首碼」都包含，如兩個客戶各有 192.168.1.0/24）
      → 回 None：寧可不建，也不要建到錯的單位。要消除歧義就在該 LibreNMS 實例設
      scope_subnet_ids，把候選縮到自己的子網路（整合設定頁的 ScopeOverlapWarning 會提醒）。
    - 沒有任何既有子網路包含 → 回 None（不憑空建子網路）。
    """
    containing = [(net, sid) for net, sid in nets if aip in net]
    if not containing:
        return None
    maxlen = max(net.prefixlen for net, _ in containing)
    best = [sid for net, sid in containing if net.prefixlen == maxlen]
    return best[0] if len(best) == 1 else None


async def sync_devices(
    session: AsyncSession, instance: LibreNMSInstance,
) -> tuple[int, int, int]:
    """從 LibreNMS 抓所有 devices；回傳 (seen, inserted, updated)。"""
    data = await _api_get(instance, "/api/v0/devices")
    devices = data.get("devices") or []
    seen = inserted = updated = 0
    # 重疊網段：同一 IP 可能存在多個子網路。限定 instance 的 scope_subnet_ids
    # （留空＝全域）並一律取第一筆，避免 scalar_one_or_none 在重複 IP 上炸掉整個 sync。
    scope_ids = _scope_uuids(instance)

    # auto_create_ips：把裝置主 IP（落在既有且符合 scope 的子網路）自動建成 IPAddress。
    # 只在開啟時才查候選子網路（省掉一次 query）。
    addable_nets = await _addable_subnets(session, scope_ids) if instance.auto_create_ips else []

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
        status_raw = d.get("status")
        is_up = status_raw in (1, "1", True, "up", "ok")
        # status 字串：明確區分 up / down / unknown（status_raw=0 是「離線」不是「未知」，
        # 別讓 `0 or "unknown"` 把離線誤標成未知）
        status_str = "unknown" if status_raw is None else ("up" if is_up else "down")

        # 若 LibreNMS device 上線 + primary_ip 對得到 jt-ipam IPAddress，
        # stamp last_seen_librenms 讓 effective_status 計算抓得到證據。
        if primary_ip:
            ipa = (
                await session.execute(
                    select(IPAddress)
                    .where(IPAddress.ip == primary_ip)
                    .where(IPAddress.subnet_id.in_(scope_ids) if scope_ids else sa_true())
                    .limit(1)
                )
            ).scalars().first()
            # auto_create_ips：IPAM 還沒有這個裝置主 IP，且它落在既有/符合 scope 的子網路
            # → 自動建一筆（discovery_source='librenms'）。只建裝置主 IP、不碰 ARP 鄰居。
            if ipa is None and instance.auto_create_ips and addable_nets:
                try:
                    aip = ipaddress.ip_address(str(primary_ip).split("/")[0])
                except ValueError:
                    aip = None
                # 唯一最精確的子網路；重疊網段歧義或無容器 → None（不猜、不建錯單位）
                sub_id = _pick_subnet_for_ip(addable_nets, aip) if aip is not None else None
                if sub_id is not None:
                    ipa = IPAddress(
                        subnet_id=sub_id, ip=str(primary_ip).split("/")[0],
                        state="active", discovery_source="librenms",
                        description="LibreNMS 自動探索新增",
                        note=(f"此 IP 由 LibreNMS 整合「{instance.name}」於 "
                              f"{datetime.now(UTC).astimezone().strftime('%Y-%m-%d %H:%M')} "
                              f"同步裝置時，依裝置主 IP 自動建立。"),
                    )
                    session.add(ipa)
                    # session 設 autoflush=False；不 flush 則 ipa.id 仍 None，
                    # 下面 apply_observation 會用 ip_id=None 建 FK → NOT NULL 違規 500。
                    await session.flush()
            if ipa is not None:
                if is_up:
                    ipa.last_seen_librenms = datetime.now(UTC)
                # feature A：把 LibreNMS 裝置名稱寫成此 IP 的 librenms 觀測。
                # OPNsense 等常把 hostname 設成 IP（如 192.168.11.1），真正的名稱
                # 在 sysName（如 fw-002.3u）→ hostname 是 IP 時改採 sysName。
                dev_hostname = (d.get("hostname") or "").strip()
                sysname = (d.get("sysName") or d.get("sysname") or "").strip()
                if (not dev_hostname or _looks_like_ip(dev_hostname)) \
                        and sysname and not _looks_like_ip(sysname):
                    dev_hostname = sysname
                if dev_hostname and not _looks_like_ip(dev_hostname):
                    await apply_observation(
                        session, ip=ipa, source="librenms", hostname=dev_hostname,
                    )

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
                status=status_str,
                last_seen_at=datetime.now(UTC),
            )
            session.add(obj)
            await session.flush()
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
            existing.status = status_str
            existing.last_seen_at = datetime.now(UTC)
            updated += 1

        # feature D：開了 auto_add_devices 就 match-or-create jt-ipam Device
        if instance.auto_add_devices:
            ldev = obj if existing is None else existing
            await link_librenms_device(session, ldev, create=True, scope_ids=scope_ids)

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
    # 重疊網段：若 instance 設了 scope_subnet_ids，IP→IPAddress 比對限定在這些子網路內
    scope_ids = _scope_uuids(instance)

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

            # 只要 LibreNMS ARP 有看到這個 IP，就 stamp last_seen_librenms
            # （effective_status 計算靠這個）。補 MAC 是額外副作用。
            ipa = (
                await session.execute(
                    select(IPAddress).where(IPAddress.ip == ip).where(
                        IPAddress.subnet_id.in_(scope_ids) if scope_ids else sa_true()
                    ).limit(1)
                )
            ).scalar_one_or_none()
            if ipa is not None:
                ipa.last_seen_librenms = now
                from app.services.arp_precedence import consider_mac
                if await consider_mac(session, ip=ipa, mac=mac, source="librenms"):
                    filled += 1
                    # feature B：ARP 學到 MAC（原本沒有）
                    await log_change(
                        session, ip=ipa, event_type="arp_changed",
                        field="mac", old=None, new=mac, source="librenms",
                    )

    return seen, inserted, updated, filled


async def prune_stale_arp(session: AsyncSession, *, max_age_days: int = 30) -> int:
    """刪除 last_seen_at 超過 max_age_days 的 ARP 紀錄，回傳刪除筆數。

    ARP 是「曾經看過」的歷史紀錄（sync_arp 只新增/更新、從不刪）；MAC 換 IP、IP 換 MAC、
    甚至來源 device 被刪（device_id→NULL 的孤兒）都會各留一筆，靠 last_seen_at 區分新舊。
    若不回收，arp_entries 會無限累積。此函式定期把過期的整批刪除（含孤兒 row）。
    max_age_days<=0 視為停用（不刪）。
    """
    if max_age_days <= 0:
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
    result = await session.execute(
        delete(ARPEntry).where(ARPEntry.last_seen_at < cutoff)
    )
    return int(result.rowcount or 0)


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

    # LibreNMS FDB 的 vlan_id 是內部 row id，不是 dot1q VLAN 號。
    # /api/v0/resources/vlans 會給 vlan_id + vlan_vlan（真正 VLAN 號）→ 建全域對照。
    vlanmap: dict[int, int] = {}
    try:
        vdata = await _api_get(instance, "/api/v0/resources/vlans", timeout=30.0)
        for v in vdata.get("vlans") or []:
            vid, vnum = v.get("vlan_id"), v.get("vlan_vlan")
            if vid is not None and vnum is not None:
                vlanmap[int(vid)] = int(vnum)
    except (LibreNMSError, ValueError, TypeError):
        vlanmap = {}

    for d in devices:
        # LibreNMS fdb 只有 port_id（數字）→ 先抓該裝置的 ports 建 port_id→ifName 對照
        portmap: dict[int, str] = {}
        try:
            pdata = await _api_get(
                instance, f"/api/v0/devices/{d.legacy_device_id}/ports?columns=port_id,ifName",
                timeout=20.0,
            )
            for p in pdata.get("ports") or []:
                pid = p.get("port_id")
                if pid is not None and p.get("ifName"):
                    portmap[int(pid)] = p["ifName"]
        except (LibreNMSError, ValueError, TypeError):
            portmap = {}

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
                raw_vid = int(vlan) if vlan is not None else None
            except (ValueError, TypeError):
                raw_vid = None
            # 用對照表還原真正 VLAN 號；對不到就存 None（不存誤導的 row id）
            vlan_int = vlanmap.get(raw_vid) if raw_vid is not None else None
            if vlan_int is None and not vlanmap and raw_vid is not None:
                # 完全沒有 vlan 對照（API 不支援）時，退回原值以免整段沒資料
                vlan_int = raw_vid
            port_name = entry.get("ifName") or entry.get("port_name")
            if not port_name and entry.get("port_id") is not None:
                try:
                    port_name = portmap.get(int(entry["port_id"]))
                except (ValueError, TypeError):
                    port_name = None
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


async def derive_switch_ports(session: AsyncSession, instance: LibreNMSInstance) -> int:
    """用 FDB 把每個 IP 的「交換器位置」(switch_port) 填出來。

    access port 啟發式：一個 MAC 可能出現在多個 (switch, port)（含 uplink/trunk）；
    取「該 port 上 MAC 數最少」者當作存取埠。值格式 "switchhostname / ifName"。
    """
    from collections import defaultdict

    from app.services.ip_history import log_change

    # FDB（有 port_name 的）：mac → list[(device_id, port)]；同時算每個 (device,port) 的 MAC 數
    rows = (await session.execute(
        select(FDBEntry.mac, FDBEntry.device_id, FDBEntry.port_name)
        .where(FDBEntry.port_name.is_not(None))
    )).all()
    if not rows:
        return 0
    port_macs: dict[tuple[Any, ...], set[Any]] = defaultdict(set)
    mac_ports: dict[str, list[Any]] = defaultdict(list)
    for mac, dev_id, port in rows:
        key = (dev_id, port)
        port_macs[key].add(str(mac))
        mac_ports[str(mac)].append((dev_id, port))

    # switch device_id → 顯示名稱：優先 device/hostname（非 IP）→ 該 IP 的 IPAddress.hostname → IP
    sw_rows = (await session.execute(
        select(LibreNMSDevice.id, LibreNMSDevice.hostname, LibreNMSDevice.sysname,
               LibreNMSDevice.primary_ip)
    )).all()
    # 批次查 switch primary_ip 對應的 IPAddress.hostname（把 192.168.1.9 → switch-003.example.com）
    sw_ips = [str(ip) for _sid, _h, _sn, ip in sw_rows if ip]
    ip_host: dict[str, str] = {}
    if sw_ips:
        for ipv, hn in (await session.execute(
            select(IPAddress.ip, IPAddress.hostname)
            .where(IPAddress.ip.in_(sw_ips), IPAddress.hostname.is_not(None))
        )).all():
            ip_host[str(ipv)] = hn
    sw_name: dict[str, Any] = {}
    for sid, h, sn, ip in sw_rows:
        ip_s = str(ip) if ip else None
        name = None
        for cand in (h, sn):
            if cand and not _looks_like_ip(cand):
                name = cand
                break
        if not name and ip_s:
            name = ip_host.get(ip_s) or ip_s
        sw_name[sid] = name or str(sid)[:8]

    # scope（重疊網段）：只處理 instance 指定的子網路
    scope_ids = _scope_uuids(instance)
    ip_stmt = select(IPAddress).where(IPAddress.mac.is_not(None))
    if scope_ids:
        ip_stmt = ip_stmt.where(IPAddress.subnet_id.in_(scope_ids))
    ips = list((await session.execute(ip_stmt)).scalars().all())

    updated = 0
    for ip in ips:
        cands = mac_ports.get(str(ip.mac))
        if not cands:
            continue
        # 取該 MAC 所有 (switch,port) 中 MAC 數最少的 → 最像 access port
        dev_id, port = min(cands, key=lambda k: len(port_macs[k]))
        loc = f"{sw_name.get(dev_id, '?')} / {port}"
        # 信心：該 port 僅一個 MAC = 直連存取埠（對應 LibreNMS 的星號）；多 MAC → uplink/trunk
        confident = len(port_macs[(dev_id, port)]) <= 1
        if ip.switch_port != loc or ip.switch_port_confident != confident:
            old = ip.switch_port
            ip.switch_port = loc
            ip.switch_port_confident = confident
            updated += 1
            if old != loc:
                await log_change(session, ip=ip, event_type="edited", field="switch_port",
                                 old=old, new=loc, source="librenms")
    return updated


# ─────────────────── 同步：VLAN（feature C）───────────────────


async def _get_or_create_vlan_domain(session: AsyncSession) -> VLANDomain:
    dom = (await session.execute(
        select(VLANDomain).where(VLANDomain.name == LIBRENMS_VLAN_DOMAIN)
    )).scalar_one_or_none()
    if dom is None:
        dom = VLANDomain(name=LIBRENMS_VLAN_DOMAIN, description="VLANs imported from LibreNMS")
        session.add(dom)
        await session.flush()
    return dom


async def sync_vlans(
    session: AsyncSession, instance: LibreNMSInstance,
) -> tuple[int, int, int]:
    """逐 device 抓 VLAN，寫進 vlans（LibreNMS domain）+ device_vlans 對應。

    回傳 (vlans_seen, vlans_upserted, mappings_upserted)。device↔VLAN 對應直接掛在
    librenms_devices（拉進來的裝置），不需先建 jt-ipam Device。
    """
    devices = list(
        (await session.execute(
            select(LibreNMSDevice).where(LibreNMSDevice.instance_id == instance.id)
        )).scalars().all()
    )
    domain = await _get_or_create_vlan_domain(session)
    now = datetime.now(UTC)
    seen = upserted = mapped = 0
    # 同一次 sync 的 number→VLAN 快取，避免重複 query
    vlan_cache: dict[int, VLAN] = {}

    for d in devices:
        path = f"/api/v0/devices/{d.legacy_device_id}/vlans"
        try:
            data = await _api_get(instance, path, timeout=20.0)
        except LibreNMSError:
            continue  # device 不支援 VLAN 查詢
        for v in data.get("vlans") or []:
            raw_num = v.get("vlan_vlan", v.get("vlan_id"))
            try:
                number = int(raw_num)
            except (ValueError, TypeError):
                continue
            if not (1 <= number <= 4094):
                continue
            # vlans.name 上限 64（VLANRead schema 驗證），先截斷
            name = (v.get("vlan_name") or f"VLAN{number}").strip()[:64] or f"VLAN{number}"
            seen += 1

            vlan = vlan_cache.get(number)
            if vlan is None:
                vlan = (await session.execute(
                    select(VLAN).where(VLAN.domain_id == domain.id, VLAN.number == number)
                )).scalar_one_or_none()
                if vlan is None:
                    vlan = VLAN(domain_id=domain.id, number=number, name=name)
                    session.add(vlan)
                    await session.flush()
                    upserted += 1
                elif name and vlan.name != name and name != f"VLAN{number}":
                    vlan.name = name  # 用 LibreNMS 最新名稱
                    upserted += 1
                vlan_cache[number] = vlan

            # device↔VLAN 對應（直接掛在 LibreNMS 裝置）
            existing = (await session.execute(
                select(DeviceVLAN).where(
                    DeviceVLAN.librenms_device_id == d.id,
                    DeviceVLAN.vlan_id == vlan.id,
                )
            )).scalar_one_or_none()
            if existing is None:
                session.add(DeviceVLAN(
                    librenms_device_id=d.id, vlan_id=vlan.id,
                    source="librenms", last_seen_at=now,
                ))
                mapped += 1
            else:
                existing.last_seen_at = now

    return seen, upserted, mapped


# ─────────────────── effective_status 計算 ───────────────────


async def mark_scanner_seen(
    session: AsyncSession, ip: IPAddress, now: datetime,
) -> None:
    """掃描代理回報某 IP 存活時，立即更新其 effective_status（不必等下次 LibreNMS sync）。

    與 recompute_effective_status 的判定一致：scanner 證據在 30 分鐘窗內 → online；
    若 LibreNMS 也在窗內則標 "online"，否則 "online (scanner)"。並記錄 offline→online 翻轉。
    呼叫前 caller 應已把 ip.last_seen_scanner 設為 now。
    """
    from datetime import timedelta
    cutoff = now - timedelta(minutes=30)
    l_seen = ip.last_seen_librenms
    new_status = "online" if (l_seen and l_seen >= cutoff) else "online (scanner)"
    if ip.effective_status != new_status:
        prev = ip.effective_status
        ip.effective_status = new_status
        # 只記真正的 offline/unknown → online 翻轉（避開 online→online 細分變動噪音）
        if prev is not None and not prev.startswith("online"):
            await log_change(
                session, ip=ip, event_type="online",
                field="effective_status", old=prev, new=new_status,
                source="scanner",
            )


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
            prev = ip.effective_status
            ip.effective_status = new_status
            updated += 1
            # feature B：只記真正的 online↔offline 翻轉（避開首次 None→unknown 噪音）
            if prev is not None:
                was_online = prev.startswith("online")
                now_online = new_status.startswith("online")
                if was_online != now_online:
                    await log_change(
                        session, ip=ip,
                        event_type="online" if now_online else "offline",
                        field="effective_status", old=prev, new=new_status,
                        source="librenms",
                    )
    return updated


async def sync_device_ports(session: AsyncSession, instance: LibreNMSInstance) -> int:
    """把 LibreNMS 介面清單(ifName)同步成已連結 jt-ipam 裝置的 device_ports。

    對 server / switch / OPNsense 等任何受監控裝置都有效；只新增缺少的埠，不刪既有
    （使用者手動建立或改名的埠保留）。回傳新增的埠數。
    """
    ldevs = list((await session.execute(
        select(LibreNMSDevice).where(
            LibreNMSDevice.instance_id == instance.id,
            LibreNMSDevice.jt_ipam_device_id.is_not(None),
        )
    )).scalars().all())
    created = 0
    for d in ldevs:
        try:
            pdata = await _api_get(
                instance,
                f"/api/v0/devices/{d.legacy_device_id}/ports?columns=ifName,ifType,ifPhysAddress",
                timeout=20.0,
            )
        except LibreNMSError as exc:
            logging.getLogger(__name__).debug(
                "librenms ports fetch failed for %s: %s", d.legacy_device_id, exc)
            continue
        # ifName → 此埠自身的實體 MAC（ifPhysAddress），正規化成小寫冒號格式
        name_mac: dict[str, str | None] = {}
        for p in (pdata.get("ports") or []):
            nm = (p.get("ifName") or "").strip()
            if not nm or nm.lower() in ("null", "unrouted vlan 1"):
                continue
            name_mac[nm] = _norm_mac(p.get("ifPhysAddress"))
        if not name_mac:
            continue
        existing_names = set((await session.execute(
            select(DevicePort.name).where(DevicePort.device_id == d.jt_ipam_device_id)
        )).scalars().all())
        for n in sorted(name_mac):
            mac = name_mac[n]
            # 用 ON CONFLICT upsert：避免「多台 LibreNMS 裝置對映到同一台 jt-ipam 裝置」或同一輪
            # 重複處理時，INSERT 撞 device_port_unique_name (device_id, name) 而中斷整批同步（issue #12）。
            ins = pg_insert(DevicePort).values(
                device_id=d.jt_ipam_device_id, name=n, type="network", mac_address=mac)
            if mac:  # 有埠 MAC 才覆寫；沒有就不動既有欄位
                stmt = ins.on_conflict_do_update(
                    index_elements=["device_id", "name"], set_={"mac_address": mac})
            else:
                stmt = ins.on_conflict_do_nothing(index_elements=["device_id", "name"])
            await session.execute(stmt)
            if n not in existing_names:
                created += 1
                existing_names.add(n)
    return created


def _norm_mac(raw: object) -> str | None:
    """LibreNMS ifPhysAddress 多為無分隔 12 hex（如 bc24112508a0）→ 標準冒號小寫格式。"""
    if not raw:
        return None
    s = str(raw).strip().lower().replace("-", "").replace(":", "").replace(".", "")
    if len(s) != 12 or any(c not in "0123456789abcdef" for c in s):
        return None
    return ":".join(s[i:i + 2] for i in range(0, 12, 2))


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
            # LibreNMS 介面清單 → 已連結裝置的連接埠（device_ports），自動帶入不必手動匯入
            await sync_device_ports(session, instance)
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
            # FDB → 交換器位置（switch_port）
            await derive_switch_ports(session, instance)
            await session.commit()
        if instance.sync_vlans:
            s, u, m = await sync_vlans(session, instance)
            summary.vlans_seen, summary.vlans_upserted, summary.vlan_mappings = s, u, m
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
