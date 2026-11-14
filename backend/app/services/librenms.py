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
from sqlalchemy import delete, func, insert, or_, select, update
from sqlalchemy import true as sa_true
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.safe_http import UnsafeOutboundURL, safe_request, transport_detail
from app.core.security import decrypt_secret, encrypt_secret
from app.core.sqlin import in_values, not_in_values
from app.models.address import IPAddress
from app.models.librenms import (
    ARPEntry,
    FDBEntry,
    LibreNMSDevice,
    LibreNMSInstance,
    LibreNMSLink,
)
from app.models.physical import DevicePort
from app.models.subnet import Subnet
from app.models.vlan import VLAN, DeviceVLAN, VLANDomain
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
        raise LibreNMSError(f"transport: {transport_detail(exc)}") from exc
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
    links_seen: int = 0
    links_upserted: int = 0
    links_pruned: int = 0
    ip_mac_filled: int = 0   # 自動把 ARP 學到的 MAC 填回 IPAddress 表
    arp_ips_created: int = 0                     # 依 ARP 自動建立的 IP（#48）
    arp_create_skipped: dict[str, int] = field(default_factory=dict)   # 沒建的原因與筆數
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
                "ips_created": self.arp_ips_created,
                "create_skipped": self.arp_create_skipped,
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
    """從 LibreNMS 原生 type 欄位 + os/hardware/sysObjectID 推 jt-ipam Device.type。

    優先序：先抓最明確的（UPS/PDU 電源設備、防火牆、AP），再一般網路/伺服器，最後用
    LibreNMS 原生 type 欄位（network/power/wireless/storage/…）補推、否則 other。
    patch_panel 是被動設備、LibreNMS 不會回報，只能手動建立。
    """
    native = (getattr(ldev, "type", None) or "").strip().lower()   # LibreNMS 原生分類
    blob = " ".join(filter(None, [ldev.os, ldev.hardware, ldev.sysObjectID or ""])).lower()

    # 電源設備最優先（vendor/型號名很有辨識度）：先分 UPS / PDU
    if native == "power" or any(k in blob for k in (
        "ups", "smart-ups", "pdu", "rpdu", "apc", "eaton", "tripp lite", "tripplite",
        "cyberpower", "powerware", "liebert", "vertiv", "riello", "socomec", "raritan",
        "servertech", "geist", "netbotz",
    )):
        if any(k in blob for k in ("pdu", "rpdu", "raritan", "servertech", "geist", "switched rack")):
            return "pdu"
        return "ups"   # 其餘電源設備（含裸 native=power）多為 UPS
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
    # LibreNMS 自己標成 workstation 的就是使用者電腦（以前併進 server）
    if native == "workstation":
        return "workstation"
    if any(k in blob for k in ("proxmox", "linux", "windows", "ubuntu", "debian", "centos",
                               "freebsd", "esxi", "vmware", "dsm", "synology", "truenas",
                               "freenas", "macos", "server")):
        return "server"
    # 關鍵字沒命中 → 用 LibreNMS 原生 type 欄位補推
    return {
        "firewall": "firewall", "wireless": "ap", "storage": "storage",
        "server": "server", "network": "switch",
    }.get(native, "other")


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
        # 重疊網段（例如甲乙兩單位都用 192.168.1.0/24）下，同一個 IP 是兩台不同機器。
        # 取兩筆來判斷：剛好一筆才採用；多筆＝分不出是誰的，寧可不對應也不要掛錯 ——
        # 掛錯會把甲單位的裝置資料寫進乙單位的紀錄，而且沒有人會發現。
        # 要讓這些位址對應得上，就在該實例設定「限定子網路範圍」。
        cands = (await session.execute(
            select(IPAddress)
            .where(IPAddress.ip == ldev.primary_ip)
            .where(in_values(IPAddress.subnet_id, scope_ids) if scope_ids else sa_true())
            .limit(2)
        )).scalars().all()
        ipa = cands[0] if len(cands) == 1 else None
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
        dtype = _infer_device_type(ldev)
        dev = Device(
            name=name, type=dtype, type_source="librenms" if dtype != "other" else None,
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
        stmt = stmt.where(in_values(Subnet.id, scope_ids))
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
    from app.models.librenms import LibreNMSInstance as _Inst
    from app.services.hostname_reports import HostnameRun, enabled_peers
    hn_run = HostnameRun(session, source="librenms", origin=f"librenms:{instance.id}",
                         peers=await enabled_peers(session, _Inst))

    seen_legacy: set[int] = set()
    disabled_legacy: set[int] = set()
    # 超大規模：鏡像列與主 IP 一次撈回來（以前每台各查兩次：5,000 台＝1.5 萬次查詢、31 秒）
    from app.services.ip_autocreate import match_existing_many
    mirror = {r.legacy_device_id: r for r in (await session.execute(
        select(LibreNMSDevice).where(LibreNMSDevice.instance_id == instance.id))).scalars().all()}
    matches = await match_existing_many(
        session, {str(d.get("ip") or d.get("ip_address") or d.get("snmp_ip") or "").split("/")[0]
                  for d in devices if not _is_disabled(d)}, scope_ids)
    for d in devices:
        legacy = int(d.get("device_id"))
        seen += 1
        seen_legacy.add(legacy)
        existing = mirror.get(legacy)

        primary_ip = d.get("ip") or d.get("ip_address") or d.get("snmp_ip")
        # 不是合法 IP（有的裝置在 ip 欄位填主機名稱）→ 當作沒有主 IP；以前會讓整輪裝置同步崩掉
        try:
            if primary_ip:
                ipaddress.ip_address(str(primary_ip).split("/")[0])
        except ValueError:
            primary_ip = None
        status_raw = d.get("status")
        is_up = status_raw in (1, "1", True, "up", "ok")
        # status 字串：明確區分 up / down / unknown（status_raw=0 是「離線」不是「未知」，
        # 別讓 `0 or "unknown"` 把離線誤標成未知）
        status_str = "unknown" if status_raw is None else ("up" if is_up else "down")
        # LibreNMS 停用（停止輪詢）：鏡像留著、標成 disabled，但不採用它的任何資料（使用者 2026-10-13：
        # 「停用的不要抓」）。停用後 status 停在最後的值，常常還是 1，以前一直算「上線」、名稱照報、
        # 凍結的 ARP 每輪被當成剛看到。`ignore` 只是不告警、仍在輪詢 → 照常採用。
        disabled = _is_disabled(d)
        if disabled:
            status_str = "disabled"
            disabled_legacy.add(legacy)

        # 若 LibreNMS device 上線 + primary_ip 對得到 jt-ipam IPAddress，
        # stamp last_seen_librenms 讓 effective_status 計算抓得到證據。
        if primary_ip and not disabled:
            # 唯一才算：重疊網段又沒設範圍時不猜、也不新建（以前任意取一筆）
            ipa, ambiguous = matches.get(str(primary_ip).split("/")[0], (None, False))
            # auto_create_ips：IPAM 還沒有這個裝置主 IP，且它落在既有/符合 scope 的子網路
            # → 自動建一筆（discovery_source='librenms'）。只建裝置主 IP、不碰 ARP 鄰居。
            if ipa is None and not ambiguous and instance.auto_create_ips and addable_nets:
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
                    # 同一輪後面若有別台用同一個主 IP，要對到這筆（不可以再建一筆）
                    matches[str(primary_ip).split("/")[0]] = (ipa, False)
            if ipa is not None:
                if is_up:
                    ipa.last_seen_librenms = datetime.now(UTC)
                # feature A：把 LibreNMS 裝置名稱寫成此 IP 的 librenms 觀測。
                # OPNsense 等常把 hostname 設成 IP（如 192.168.11.1），真正的名稱
                # 在 sysName（如 fw-01.example.net）→ hostname 是 IP 時改採 sysName。
                dev_hostname = (d.get("hostname") or "").strip()
                sysname = (d.get("sysName") or d.get("sysname") or "").strip()
                if (not dev_hostname or _looks_like_ip(dev_hostname)) \
                        and sysname and not _looks_like_ip(sysname):
                    dev_hostname = sysname
                # 名稱只有 IP 或空白＝LibreNMS 對這台沒有名字 → 也要報，舊名才會被清
                hn_run.report(ipa, dev_hostname if dev_hostname and not _looks_like_ip(dev_hostname)
                              else None)

        if existing is None:
            obj = LibreNMSDevice(
                instance_id=instance.id,
                legacy_device_id=legacy,
                hostname=d.get("hostname"),
                sysname=d.get("sysName"),
                primary_ip=primary_ip,
                hardware=d.get("hardware"),
                os=d.get("os"),
                type=d.get("type"),
                version=d.get("version"),
                serial=d.get("serial"),
                sysObjectID=d.get("sysObjectID"),
                uptime=int(d.get("uptime") or 0) or None,
                status=status_str,
                last_seen_at=datetime.now(UTC),
            )
            session.add(obj)
            await session.flush()
            mirror[legacy] = obj
            inserted += 1
        else:
            existing.hostname = d.get("hostname")
            existing.sysname = d.get("sysName")
            existing.primary_ip = primary_ip
            existing.hardware = d.get("hardware")
            existing.os = d.get("os")
            existing.type = d.get("type")
            existing.version = d.get("version")
            existing.serial = d.get("serial")
            existing.sysObjectID = d.get("sysObjectID")
            existing.uptime = int(d.get("uptime") or 0) or None
            existing.status = status_str
            existing.last_seen_at = datetime.now(UTC)
            updated += 1

        # feature D：開了 auto_add_devices 就 match-or-create jt-ipam Device（停用的不建；已建的是使用者的資料，不動）
        if instance.auto_add_devices and not disabled:
            ldev = obj if existing is None else existing
            await link_librenms_device(session, ldev, create=True, scope_ids=scope_ids)

    # /devices 讀取失敗會往外拋；走到這裡就是完整的裝置清單
    # （停用的裝置沒報名稱 → finish 收回它以前報過的）
    await hn_run.finish(complete=True)
    await _prune_devices(session, instance, seen_legacy)
    # 停用裝置凍結的 ARP／FDB：LibreNMS 不會清，留著就一直被當成目前的觀測（假上線、假衝突）
    gone = [mirror[lg].id for lg in disabled_legacy if lg in mirror]
    if gone:
        await session.execute(delete(ARPEntry).where(in_values(ARPEntry.device_id, gone)))
        await session.execute(delete(FDBEntry).where(in_values(FDBEntry.device_id, gone)))
    return seen, inserted, updated


def _is_disabled(d: dict[str, Any]) -> bool:
    """LibreNMS 的 `disabled`（停用輪詢）：0/1、字串或布林都有可能。"""
    v = d.get("disabled")
    return v in (1, True) or str(v).strip().lower() in ("1", "true", "yes")


def _active_devices(instance_id: Any) -> Any:
    """要拿資料的鏡像裝置：排除 LibreNMS 停用的（舊資料的 status 可能是空的，算啟用）。"""
    return select(LibreNMSDevice).where(
        LibreNMSDevice.instance_id == instance_id,
        or_(LibreNMSDevice.status.is_(None), LibreNMSDevice.status != "disabled"))


def _live_devices(instance_id: Any) -> Any:
    """要收 ARP／MAC 表的鏡像裝置：再排除 down 的。

    LibreNMS 對 down 的設備照樣回傳最後一次輪詢到的 ARP／MAC 表（正式環境 2026-10-20：重灌換位址的舊主機
    down 了 23 天，凍結的 ARP 每輪被蓋成剛看到 → 假衝突）。不收新的、不更新既有的，讓它照保存期限過期；
    回到 up 下一輪就恢復。不刪：暫時斷線的交換器不該讓 MAC 表整片消失。
    """
    return _active_devices(instance_id).where(
        or_(LibreNMSDevice.status.is_(None), LibreNMSDevice.status != "down"))


async def _prune_devices(session: AsyncSession, instance: LibreNMSInstance, seen: set[int]) -> int:
    """LibreNMS 已刪除的裝置：鏡像連同它的 VLAN 對應（CASCADE）、ARP、FDB 一起刪。

    以前永遠留著，狀態停在最後的值，舊的 ARP／FDB 還繼續參與交換器埠推算與拓樸（2026-09-26 稽核）。
    讀到 0 台、之前卻有 → 多半是 token 權限被收；一次要刪掉一大半 → 回傳多半不完整：都不刪。
    對映到的 jt-ipam 裝置是使用者的物件，不動。
    """
    from app.services.hostname_reports import BREAKER_MIN, BREAKER_RATIO, EMPTY_GUARD

    rows = (await session.execute(select(LibreNMSDevice.id, LibreNMSDevice.legacy_device_id).where(
        LibreNMSDevice.instance_id == instance.id))).all()
    gone = [rid for rid, legacy in rows if legacy not in seen]
    if not gone:
        return 0
    if (not seen and len(rows) >= EMPTY_GUARD) or (
            len(gone) > BREAKER_MIN and len(gone) > len(rows) * BREAKER_RATIO):
        logging.getLogger(__name__).warning(
            "librenms %s: not removing %d of %d devices (listing looks incomplete)",
            instance.name, len(gone), len(rows))
        return 0
    await session.execute(delete(ARPEntry).where(in_values(ARPEntry.device_id, gone)))
    await session.execute(delete(FDBEntry).where(in_values(FDBEntry.device_id, gone)))
    await session.execute(delete(LibreNMSDevice).where(in_values(LibreNMSDevice.id, gone)))
    return len(gone)


# ─────────────────── 同步：ARP ───────────────────


async def sync_arp(
    session: AsyncSession, instance: LibreNMSInstance, *, autocreate_stats: dict[str, Any] | None = None,
) -> tuple[int, int, int, int]:
    """逐 device 抓 ARP；回傳 (seen, inserted, updated, ip_mac_filled)。

    `autocreate_stats`：有給就填入「依 ARP 自動建立 IP」的結果（{"created", "skipped"}）。
    """
    # 停用與 down 的裝置不在對照表裡 → 它凍結的 ARP 一筆都不收（下面「未追蹤的裝置 → 跳過」）
    devices = list((await session.execute(_live_devices(instance.id))).scalars().all())
    seen = inserted = updated = filled = 0
    now = datetime.now(UTC)
    # 重疊網段：若 instance 設了 scope_subnet_ids，IP→IPAddress 比對限定在這些子網路內
    scope_ids = _scope_uuids(instance)

    # legacy_device_id（LibreNMS 的 device_id）→ jt-ipam LibreNMSDevice
    legacy_map = {d.legacy_device_id: d for d in devices if d.legacy_device_id is not None}
    # 一次取回全站 ARP：/api/v0/resources/ip/arp/all（"all" 是 {ip} 位置的關鍵字＝回全部）。
    # 取代舊的逐 device /api/v0/devices/{id}/ip/arp/all —— 那在新版 LibreNMS 是不存在的
    # 路由，會對「每一台」裝置各回一次 404（每輪 sync 數十筆 4xx → 被對端 Wazuh 判為
    # web-scan/recon 告警，而且 ARP 其實一筆都同步不到）。改用單一 resources 端點正確拿到。
    try:
        data = await _api_get(instance, "/api/v0/resources/ip/arp/all", timeout=60.0)
    except LibreNMSError:
        return seen, inserted, updated, filled

    # 全域 ARP 端點同一 (ip, mac, device) 可能因跨多個 port 而重複回報；arp_entries 唯一鍵是
    # (ip, mac, device_id) → 一輪只處理一次（MAC 先正規化：同一個 MAC 兩種寫法也算同一筆）。
    #
    # 超大規模：以前每一筆各查一次 ARP 列、一次 IP —— 15 萬筆 ARP 一輪同步 29 萬次查詢、9 分半
    # （2026-09-29 超大規模測試）。現在先把需要的一次撈回來（這台實例的 ARP 列、這一輪出現的 IP），
    # last_seen 整批更新、新的整批寫入；只有 MAC 真的要改的那幾筆才載入 IP 物件、走 consider_mac
    # （規則同一份：arp_precedence.would_take）。
    from app.services.arp_evidence import normalize as arp_normalize
    from app.services.arp_precedence import consider_mac, load_precedence, normalize_mac, would_take

    entries: dict[tuple[str, str, uuid.UUID], dict[str, Any]] = {}
    for arp in data.get("arp") or []:
        ip = arp.get("ipv4_address") or arp.get("ip_address")
        mac = arp.get("mac_address")
        if not ip or not mac:
            continue
        d = legacy_map.get(arp.get("device_id"))
        if d is None:
            continue   # ARP 來自本 instance 未追蹤的裝置 → 跳過
        # 全零、廣播、只有第一個位元組的殘缺值都不是機器（以前照收，AI 看到就說位址被假網卡占用）
        if arp_normalize(mac) is None:
            continue
        mac = mac.lower()
        key = (str(ip), normalize_mac(mac), d.id)
        if key not in entries:
            entries[key] = {"ip": str(ip), "mac": mac, "device_id": d.id,
                            "interface": arp.get("port_name") or arp.get("interface"),
                            "vrf": arp.get("context_name")}
    seen = len(entries)
    if not entries:
        return seen, inserted, updated, filled

    # ARP 列：既有的更新 last_seen_at、新的整批新增
    existing: dict[tuple[str, str, uuid.UUID], uuid.UUID] = {}
    for rid, rip, rmac, rdev in (await session.execute(
            select(ARPEntry.id, ARPEntry.ip, ARPEntry.mac, ARPEntry.device_id)
            .where(in_values(ARPEntry.device_id, {d.id for d in legacy_map.values()})))).all():
        existing[(str(rip).split("/")[0], normalize_mac(rmac), rdev)] = rid
    touch = [existing[k] for k in entries if k in existing]
    fresh = [{"ip": e["ip"], "mac": e["mac"], "instance_id": instance.id, "device_id": e["device_id"],
              "interface": e["interface"], "vrf": e["vrf"], "source": "librenms",
              "first_seen_at": now, "last_seen_at": now}
             for k, e in entries.items() if k not in existing]
    if touch:
        await session.execute(update(ARPEntry).where(in_values(ARPEntry.id, touch))
                              .values(last_seen_at=now).execution_options(synchronize_session=False))
    for i in range(0, len(fresh), 5000):
        await session.execute(insert(ARPEntry), fresh[i:i + 5000])
    inserted, updated = len(fresh), len(touch)

    # 依 ARP 自動建立 IP（#48，預設關）：放在這裡，新建的列會走下面同一段補 last_seen_arp 與 MAC
    from app.services.arp_autocreate import create_from_arp
    ac = await create_from_arp(session, instance, [(e["ip"], e["mac"]) for e in entries.values()],
                               scope_ids=scope_ids, now=now)
    if autocreate_stats is not None:
        autocreate_stats.update(ac)

    # ARP 看到這個 IP → 蓋 `last_seen_arp`（**不是** last_seen_librenms）。
    # 兩者可信度差很多：LibreNMS 的 ARP API 不回任何時間欄位，我們只能因為
    # 「這筆還在清單裡」就蓋上同步當下的時間 —— 來源設備（例如 AP）的 ARP 快取
    # 不老化的話，機器早就關了也會一直看起來是「剛剛才看到」。補 MAC 是額外副作用。
    # 同一個位址有多筆 IP 記錄（重疊網段、沒設範圍）時取最早建立的那筆，跟以前一樣只碰一筆。
    ipq = (select(IPAddress.id, IPAddress.ip, IPAddress.mac, IPAddress.mac_source)
           .where(in_values(IPAddress.ip, {e["ip"] for e in entries.values()}))
           .order_by(IPAddress.created_at, IPAddress.id))
    if scope_ids:
        ipq = ipq.where(in_values(IPAddress.subnet_id, scope_ids))
    target: dict[str, list[Any]] = {}
    for iid, ipv, cur_mac, cur_src in (await session.execute(ipq)).all():
        target.setdefault(str(ipv).split("/")[0], [iid, cur_mac, cur_src])
    if not target:
        return seen, inserted, updated, filled
    await session.execute(update(IPAddress).where(in_values(IPAddress.id, [v[0] for v in target.values()]))
                          .values(last_seen_arp=now).execution_options(synchronize_session=False))

    # MAC：同一個 IP 可能有好幾台設備各回一個 MAC（某台的 ARP 快取沒老化，還留著上一台的 MAC；
    # LibreNMS 不給時間，分不出誰新）。以前照回報順序一筆一筆套，同一輪 MAC 來回換好幾次、每輪都記異動
    # （2026-10-05 正式環境 14 個 IP 每 16 分鐘來回跳）。現在每個 IP 一輪只決定一次：取最多台設備回報的
    # MAC；目前的 MAC 也在並列最多之中就不動；最多的有兩個以上又都不是目前的 → 不猜。
    votes: dict[str, dict[str, list[Any]]] = {}
    for e in entries.values():
        votes.setdefault(e["ip"], {}).setdefault(normalize_mac(e["mac"]), [0, e["mac"]])[0] += 1
    order, disabled = await load_precedence(session)
    changes: list[tuple[uuid.UUID, str]] = []
    for ipv, by_mac in votes.items():
        t = target.get(ipv)
        if not t:
            continue
        top = max(n for n, _m in by_mac.values())
        best = [k for k, (n, _m) in by_mac.items() if n == top]
        cur = normalize_mac(t[1]) if t[1] else None
        if cur in best:
            if t[2] is not None:
                continue
            pick = cur          # 值是對的、只是來源不明：讓 consider_mac 補記來源
        elif len(best) == 1:
            pick = best[0]
        else:
            continue
        mac = by_mac[pick][1]
        if would_take(order, disabled, cur_mac=t[1], cur_source=t[2], mac=mac, source="librenms"):
            changes.append((t[0], mac))
    if changes:
        from app.services.ip_history import latest_changes
        objs = {o.id: o for o in (await session.execute(
            select(IPAddress).where(in_values(IPAddress.id, {c[0] for c in changes})))).scalars().all()}
        latest = await latest_changes(session, objs, "mac")
        for iid, mac in changes:
            ipa = objs.get(iid)
            if ipa is None:
                continue
            ipa.last_seen_arp = now
            # 舊值要在覆寫前先取：寫死 None 的話，異動記錄的舊值永遠空白，
            # 看不出「從哪個 MAC 換成哪個」——真的換網卡時反而查不出來
            prev_mac = str(ipa.mac) if ipa.mac else None
            if await consider_mac(session, ip=ipa, mac=mac, source="librenms", latest=latest) \
                    and normalize_mac(prev_mac) != normalize_mac(mac):
                filled += 1
                await log_change(
                    session, ip=ipa, event_type="arp_changed",
                    field="mac", old=prev_mac, new=mac, source="librenms", latest=latest,
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
    # 停用與 down 的裝置不打它的 FDB API（資料是凍結的）
    devices = list((await session.execute(_live_devices(instance.id))).scalars().all())
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
        # 這台裝置既有的 FDB 一次讀進來，以「MAC＋埠＋VLAN」為鍵（跟唯一鍵 fdb_entry_unique 同一組）。
        # issue #43：同一次回應裡同一組鍵可能出現兩次（LibreNMS 以內部 VLAN row id 記，換算成
        # VLAN 號後會重疊）。正式環境 session 是 autoflush=False，逐筆 SELECT 查不到剛 add 還沒
        # 寫入的那筆 → 新增兩次 → 唯一鍵衝突、整輪同步中斷。改成在記憶體裡以鍵合併。
        known: dict[tuple[str, str | None, int | None], FDBEntry] = {
            (str(r.mac).lower(), r.port_name, r.vlan_id_num): r
            for r in (await session.execute(
                select(FDBEntry).where(FDBEntry.device_id == d.id))).scalars().all()
        }
        for entry in data.get("ports_fdb") or []:
            mac = entry.get("mac_address")
            if not mac:
                continue
            mac = _norm_fdb_mac(mac)
            if mac is None:
                continue
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
            key = (mac, port_name, vlan_int)
            existing = known.get(key)
            first_at, last_at = _fdb_times(entry, now)
            if existing is None:
                row = FDBEntry(
                    mac=mac, vlan_id_num=vlan_int,
                    instance_id=instance.id, device_id=d.id,
                    port_name=port_name, source="librenms",
                    first_seen_at=first_at, last_seen_at=last_at,
                )
                session.add(row)
                known[key] = row
                inserted += 1
            else:
                # 只往前走：上游若回了較舊的時間（分頁、重送），不要把末見時間往回改；
                # 首見只往回走（同一次回應裡重複的那幾筆，取最早的）
                if existing.last_seen_at is None or last_at > _aware(existing.last_seen_at):
                    existing.last_seen_at = last_at
                if existing.first_seen_at is not None and first_at < _aware(existing.first_seen_at):
                    existing.first_seen_at = first_at
                updated += 1
    return seen, inserted, updated


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _norm_fdb_mac(raw: object) -> str | None:
    """LibreNMS 的 FDB MAC 是 12 位 hex（`00005e005321`）；轉成資料庫 MACADDR 的寫法（`00:00:5e:00:53:21`），
    記憶體裡合併時的鍵才會跟讀回來的既有資料一致。解不出來的不收。"""
    hexs = "".join(ch for ch in str(raw).lower() if ch in "0123456789abcdef")
    if len(hexs) != 12:
        return None
    return ":".join(hexs[i:i + 2] for i in range(0, 12, 2))


def _fdb_times(row: dict[str, Any], now: datetime) -> tuple[datetime, datetime]:
    """一筆 FDB 的首見／末見時間。

    LibreNMS 自己就記了 `created_at` / `updated_at`（實機驗過：某筆是 2021 年首見、
    當天才更新）。把它們蓋成 jt-ipam 的同步時間，等於把「這個 MAC 在這個埠待了幾年」
    變成「今天才第一次看到」—— 而那正是查案時最想知道的事。

    上游給的東西不能直接信：解不出來就退回同步時間，不讓一筆壞資料炸掉整批同步。
    """
    def _parse(v: object) -> datetime | None:
        if not isinstance(v, str) or not v:
            return None
        try:
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)

    first = _parse(row.get("created_at")) or now
    last = _parse(row.get("updated_at")) or now
    # 上游偶爾會給出 updated < created（時鐘、資料修補）；末見不該早於首見
    return (first, max(first, last))


def current_fdb_cutoff(*, now: datetime | None = None, max_age_hours: int = 24) -> datetime:
    """「目前仍有效」的界線。超過這個時間沒再被看到的條目只算歷史。"""
    return (now or datetime.now(UTC)) - timedelta(hours=max_age_hours)


def is_current(entry: Any, cutoff: datetime) -> bool:
    last = getattr(entry, "last_seen_at", None)
    if last is None:
        return False
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    return last >= cutoff


async def prune_stale_fdb(session: AsyncSession, *, max_age_days: int = 365) -> int:
    """刪掉超過保留期限的 FDB 條目；回傳刪除筆數。0 = 永久保留。

    ARP 早就有回收（`prune_stale_arp`），FDB 沒有 —— 只會無限累積。但保留期限預設
    給得長（一年）：這張表的價值就是「那台機器以前接在哪個埠」，砍太快等於把查案的
    依據丟掉。
    """
    if max_age_days <= 0:
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
    res = await session.execute(
        delete(FDBEntry).where(FDBEntry.last_seen_at < cutoff)
    )
    return int(res.rowcount or 0)


async def derive_switch_ports(session: AsyncSession, instance: Any, *, source: str = "librenms") -> int:
    """用 FDB 把每個 IP 的「交換器位置」(switch_port) 填出來。

    access port 啟發式：一個 MAC 可能出現在多個 (switch, port)（含 uplink/trunk）；
    取「該 port 上 MAC 數最少」者當作存取埠。值格式 "switchhostname / ifName"。

    `instance` 只用來取範圍（`scope_subnet_ids`）—— LibreNMS 整合或 MikroTik 路由器都行。
    FDB 有兩種列：LibreNMS 的（`device_id` 是 LibreNMS 裝置）與 MikroTik 的（`switch_device_id`
    是 jt-ipam 裝置，0170）；兩種一起投票，交換器以 ("ln", id)／("dev", id) 區分。
    """
    from collections import defaultdict

    from app.models.device import Device
    from app.services.ip_history import log_change

    # FDB（有 port_name 的）：mac → list[(device_id, port)]；同時算每個 (device,port) 的 MAC 數
    # **只採用近期仍被看到的條目**。這張表刻意保留歷史（MAC 搬過的每個埠都留著），
    # 但「目前接在哪」不能讓半年前的舊埠參與投票 —— 那會讓 switch_port 指向一個早就
    # 搬走的位置，而畫面上完全看不出哪裡不對。
    cutoff = current_fdb_cutoff(max_age_hours=get_settings().fdb_current_max_age_hours)
    def _switch(ln_id: Any, dev_id: Any) -> tuple[str, Any] | None:
        if ln_id is not None:
            return ("ln", ln_id)
        if dev_id is not None:
            return ("dev", dev_id)
        return None

    rows = (await session.execute(
        select(FDBEntry.mac, FDBEntry.device_id, FDBEntry.switch_device_id, FDBEntry.port_name)
        .where(FDBEntry.port_name.is_not(None), FDBEntry.last_seen_at >= cutoff)
    )).all()
    if not rows:
        return 0
    port_macs: dict[tuple[Any, ...], set[Any]] = defaultdict(set)
    mac_ports: dict[str, list[Any]] = defaultdict(list)
    for mac, ln_id, dev_id, port in rows:
        sw = _switch(ln_id, dev_id)
        if sw is None:
            continue
        key = (sw, port)
        port_macs[key].add(str(mac))
        mac_ports[str(mac)].append((sw, port))

    # switch device_id → 顯示名稱：優先 device/hostname（非 IP）→ 該 IP 的 IPAddress.hostname → IP
    sw_rows = (await session.execute(
        select(LibreNMSDevice.id, LibreNMSDevice.hostname, LibreNMSDevice.sysname,
               LibreNMSDevice.primary_ip)
    )).all()
    # 批次查 switch primary_ip 對應的 IPAddress.hostname（把 10.0.0.9 → switch-01.example.net）
    sw_ips = [str(ip) for _sid, _h, _sn, ip in sw_rows if ip]
    ip_host: dict[str, str] = {}
    if sw_ips:
        for ipv, hn in (await session.execute(
            select(IPAddress.ip, IPAddress.hostname)
            .where(in_values(IPAddress.ip, sw_ips), IPAddress.hostname.is_not(None))
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
        sw_name[("ln", sid)] = name or str(sid)[:8]
    dev_ids = {sw[1] for sw, _p in port_macs if sw[0] == "dev"}
    if dev_ids:
        for did, dname in (await session.execute(
                select(Device.id, Device.name).where(in_values(Device.id, list(dev_ids))))).all():
            sw_name[("dev", did)] = dname

    # scope（重疊網段）：只處理 instance 指定的子網路
    scope_ids = _scope_uuids(instance)
    ip_stmt = select(IPAddress).where(IPAddress.mac.is_not(None))
    if scope_ids:
        ip_stmt = ip_stmt.where(in_values(IPAddress.subnet_id, scope_ids))
    ips = list((await session.execute(ip_stmt)).scalars().all())

    # 目前的 FDB 裡找不到這個 MAC：分兩種 ——
    # 同一台機器只是關機（它的 MAC 以前確實在那個埠出現過）→ 保留「最後接在哪」；
    # IP 換了機器（新 MAC 從沒出現在那個埠）→ 那是上一台的埠，清掉（2026-09-26 稽核）
    orphan = [ip for ip in ips if ip.switch_port and not mac_ports.get(str(ip.mac))]
    history: dict[str, set[str]] = defaultdict(set)
    if orphan:
        for mac, ln_id, dev_id, port in (await session.execute(
            select(FDBEntry.mac, FDBEntry.device_id, FDBEntry.switch_device_id, FDBEntry.port_name).where(
                in_values(FDBEntry.mac, [str(ip.mac) for ip in orphan]), FDBEntry.port_name.is_not(None))
        )).all():
            history[str(mac)].add(f"{sw_name.get(_switch(ln_id, dev_id), '?')} / {port}")

    updated = 0
    for ip in ips:
        cands = mac_ports.get(str(ip.mac))
        if not cands:
            if ip.switch_port and ip.switch_port not in history.get(str(ip.mac), set()):
                old = ip.switch_port
                ip.switch_port = None
                ip.switch_port_confident = None
                updated += 1
                await log_change(session, ip=ip, event_type="edited", field="switch_port",
                                 old=old, new=None, source=source)
            continue
        # 取該 MAC 所有 (switch,port) 中 MAC 數最少的 → 最像 access port
        sw, port = min(cands, key=lambda k: len(port_macs[k]))
        loc = f"{sw_name.get(sw, '?')} / {port}"
        # 信心：該 port 僅一個 MAC = 直連存取埠（對應 LibreNMS 的星號）；多 MAC → uplink/trunk
        confident = len(port_macs[(sw, port)]) <= 1
        if ip.switch_port != loc or ip.switch_port_confident != confident:
            old = ip.switch_port
            ip.switch_port = loc
            ip.switch_port_confident = confident
            updated += 1
            if old != loc:
                await log_change(session, ip=ip, event_type="edited", field="switch_port",
                                 old=old, new=loc, source=source)
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
    devices = list((await session.execute(_active_devices(instance.id))).scalars().all())
    # 指定的 domain（issue #53）：沿用裡面同編號的 VLAN、不覆寫已有的名稱；沒指定（或被刪掉）＝專用的 LibreNMS domain
    chosen = await session.get(VLANDomain, instance.vlan_domain_id) if instance.vlan_domain_id else None
    domain = chosen or await _get_or_create_vlan_domain(session)
    create_missing = chosen is None or bool(instance.vlan_create_missing)
    now = datetime.now(UTC)
    seen = upserted = mapped = 0
    # 同一次 sync 的 number→VLAN 快取，避免重複 query（None＝domain 裡沒有、而且設定成不新建）
    vlan_cache: dict[int, VLAN | None] = {}

    for d in devices:
        path = f"/api/v0/devices/{d.legacy_device_id}/vlans"
        try:
            data = await _api_get(instance, path, timeout=20.0)
        except LibreNMSError:
            continue  # device 不支援 VLAN 查詢（讀不到就不知道還有哪些 → 既有對應不動）
        dev_vlans: set[uuid.UUID] = set()
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

            if number in vlan_cache:
                vlan = vlan_cache[number]
            else:
                vlan = (await session.execute(
                    select(VLAN).where(VLAN.domain_id == domain.id, VLAN.number == number)
                )).scalar_one_or_none()
                if vlan is None:
                    if create_missing:
                        vlan = VLAN(domain_id=domain.id, number=number, name=name)
                        session.add(vlan)
                        await session.flush()
                        upserted += 1
                elif name and vlan.name != name and name != f"VLAN{number}" \
                        and (chosen is None or not (vlan.name or "").strip()):
                    # 專用 domain 用 LibreNMS 最新名稱；指定的 domain 是 IPAM 規劃的 VLAN，已有名稱就不覆寫
                    vlan.name = name
                    upserted += 1
                vlan_cache[number] = vlan
            if vlan is None:
                continue      # domain 裡沒有、而且設定成不新建 → 這台的這個 VLAN 不對應

            # device↔VLAN 對應（直接掛在 LibreNMS 裝置）
            existing = (await session.execute(
                select(DeviceVLAN).where(
                    DeviceVLAN.librenms_device_id == d.id,
                    DeviceVLAN.vlan_id == vlan.id,
                )
            )).scalar_one_or_none()
            dev_vlans.add(vlan.id)
            if existing is None:
                session.add(DeviceVLAN(
                    librenms_device_id=d.id, vlan_id=vlan.id,
                    source="librenms", last_seen_at=now,
                ))
                mapped += 1
            else:
                existing.last_seen_at = now
        # 這台讀到了：裝置上已經拿掉的 VLAN 不再對應（以前永遠留著）
        stale = delete(DeviceVLAN).where(DeviceVLAN.librenms_device_id == d.id)
        if dev_vlans:
            stale = stale.where(DeviceVLAN.vlan_id.notin_(dev_vlans))  # bounded: VLAN ids of one device (≤ 4094)
        await session.execute(stale)

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


async def sync_interface_ips(session: AsyncSession, instance: LibreNMSInstance) -> dict[str, int]:
    """LibreNMS 記錄的設備介面位址＝上線證據 iface:librenms（多 LAN 的閘道常不回 ping；services/iface_evidence）。

    設備在 LibreNMS 是 up（停用的不算）、而且那個埠是 up 才算。位址清單只給 port_id，
    用一次 /api/v0/ports（只取三個欄位）對回設備 —— 逐台查會是 N 次請求。
    """
    from app.services import iface_evidence
    addrs = (await _api_get(instance, "/api/v0/resources/ip/addresses", timeout=60.0)).get("ip_addresses") or []
    ports = (await _api_get(instance, "/api/v0/ports?columns=port_id,device_id,ifOperStatus",
                            timeout=60.0)).get("ports") or []
    up = {int(x) for (x,) in (await session.execute(
        select(LibreNMSDevice.legacy_device_id).where(
            LibreNMSDevice.instance_id == instance.id, LibreNMSDevice.status == "up"))).all()}
    scope = list(instance.scope_subnet_ids) if instance.scope_subnet_ids else None
    return await iface_evidence.stamp(session, "iface:librenms",
                                      iface_evidence.librenms_addrs(addrs, ports, up_devices=up), scope_ids=scope,
                                      instance=(instance.id, instance.name))


async def recompute_effective_status(
    session: AsyncSession, instance: LibreNMSInstance | None = None,
) -> int:
    """規格書 §6.4.2 對照表：用 ARP 學到的 MAC + 最近 last_seen_arp 推 online。

    全站重算（instance 沒有用到，保留參數相容舊呼叫）；jt-ipam-sync 每輪都跑一次，
    不依附 LibreNMS —— 以前只有 LibreNMS 同步會呼叫，沒有 LibreNMS 的站台永遠不會變回離線。

    保守規則：
    - last_seen_librenms 在過去 30 分鐘內 → online
    - last_seen_scanner 在過去 30 分鐘內 → online
    - 兩者都很久沒見 → offline
    - 兩者都從沒見過 → unknown
    """
    from datetime import timedelta

    from app.services import arp_seen as arp_seen_svc
    from app.services.system_config import get_liveness_config
    cfg = await get_liveness_config(session)
    sources = set(cfg["sources"])
    now = datetime.now(UTC)
    # 閾值跟著系統設定走（原本寫死 30 分鐘，和設定頁講的不一致）
    cutoff = now - timedelta(minutes=int(cfg["minutes"]))

    # 超大規模（2026-09-30）：這裡每 5 分鐘跑一次。以前載入每一個 IP 的 ORM 物件、逐日觀測塞進
    # 同一個 INSERT（每個 IP 5 個參數）—— 超過約 6,500 個 IP 就超過 asyncpg 的參數上限，整個重算
    # 失敗、上線狀態不再更新；舊資料的 ARP 起點與每次翻轉的異動記錄也是逐筆查。
    # 現在：只讀要用的欄位、需要的查詢各一次、變更整批寫回、逐日觀測只寫今天有變的。
    from sqlalchemy import String
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.models.ip_liveness import IPLivenessDay
    from app.services.ip_history import latest_changes

    await session.flush()             # 同一個 session 裡還沒寫出的證據（防火牆 ARP 等）要算進來
    # 「上線狀態跟著設備」：先把同設備其他位址的證據搬過來（services/device_liveness），下面才算得到
    from app.services.device_liveness import materialize as _materialize_device
    await _materialize_device(session)
    rows = (await session.execute(select(
        IPAddress.id, IPAddress.ip, IPAddress.subnet_id, IPAddress.mac, IPAddress.effective_status,
        IPAddress.last_seen_scanner, IPAddress.last_seen_librenms, IPAddress.last_seen_arp,
        IPAddress.last_seen_wazuh, IPAddress.last_seen_zabbix, IPAddress.last_seen_checkmk,
        IPAddress.arp_seen))).all()
    # 從沒對應過 ARP 記錄的舊資料：補一次起點（之後由 sync_arp 維護）—— 一次查完
    need = {str(r.ip).split("/")[0] for r in rows if not r.last_seen_arp and r.mac}
    arp_start: dict[str, datetime] = {}
    if need:
        host = func.host(ARPEntry.ip)
        arp_start = dict((await session.execute(
            select(host, func.max(ARPEntry.last_seen_at))
            .where(in_values(host, need, type_=String())).group_by(host))).all())
    today = now.date()
    have_today = {r[0]: (r[1], r[2], r[3]) for r in (await session.execute(
        select(IPLivenessDay.ip_id, IPLivenessDay.up, IPLivenessDay.down, IPLivenessDay.arp_only)
        .where(IPLivenessDay.day == today))).all()}

    use_s = "scanner" in sources
    use_l = "librenms" in sources
    use_a = "arp" in sources or "arp:librenms" in sources
    use_w = "wazuh" in sources
    use_z = "zabbix" in sources
    use_c = "checkmk" in sources
    observations: list[dict[str, Any]] = []
    status_rows: list[dict[str, Any]] = []
    arp_rows: list[dict[str, Any]] = []
    flips: list[tuple[Any, str, str, bool, str]] = []
    for ip in rows:
        s_seen = ip.last_seen_scanner
        l_seen = ip.last_seen_librenms
        a_seen = ip.last_seen_arp
        if not a_seen and ip.mac:
            arp = arp_start.get(str(ip.ip).split("/")[0])
            if arp:
                a_seen = arp
                arp_rows.append({"id": ip.id, "last_seen_arp": arp})

        # 只有被勾選的來源算數；每個來源會不會過期由 services/evidence.py 的契約決定
        # （LibreNMS 的 ARP 不會過期 → 不在預設之內；防火牆自己的 ARP 表會逾時淘汰
        #  → 逐廠牌各自一個來源，預設採信）
        s_fresh = use_s and bool(s_seen and s_seen >= cutoff)
        l_fresh = use_l and bool(l_seen and l_seen >= cutoff)
        a_fresh = use_a and bool(a_seen and a_seen >= cutoff)
        w_seen = ip.last_seen_wazuh
        w_fresh = use_w and bool(w_seen and w_seen >= cutoff)
        z_seen = ip.last_seen_zabbix
        z_fresh = use_z and bool(z_seen and z_seen >= cutoff)
        c_seen = ip.last_seen_checkmk
        c_fresh = use_c and bool(c_seen and c_seen >= cutoff)
        # 防火牆逐來源證據（arp:/vpn:/lease:）—— 會過期的才有資格宣稱上線
        d_seen, d_src = arp_seen_svc.newest_aging(ip, sources)
        d_fresh = bool(d_seen and d_seen >= cutoff)

        new_status: str
        # 翻轉記錄的來源（#49）：上線＝讓它上線的那個來源；失聯＝系統判定證據過期
        flip_src = "system"
        if s_fresh or l_fresh or w_fresh or z_fresh or c_fresh or d_fresh:
            # 會老化的證據：掃描代理實際探測、LibreNMS／Wazuh 的裝置狀態、
            # 防火牆表上此刻還在的項目
            fresh_srcs = [n for n, ok in (
                ("scanner", s_fresh), ("librenms", l_fresh), ("wazuh", w_fresh),
                ("zabbix", z_fresh), ("checkmk", c_fresh), (d_src or "", d_fresh),
            ) if ok and n]
            new_status = "online" if len(fresh_srcs) > 1 else f"online ({fresh_srcs[0]})"
            # 逐來源鍵（arp:opnsense、lease:kea…）記廠牌，跟各整合自己寫的異動記錄一致
            flip_src = fresh_srcs[0].split(":", 1)[-1]
        elif a_fresh:
            # **只有 ARP 撐著**：算上線，但標成獨立等級。ARP 只證明某個 MAC↔IP 對應
            # 曾被學到，不證明機器現在活著 —— 來源設備快取不老化就會一直是這個狀態。
            new_status = "online (arp)"
            flip_src = "librenms"         # last_seen_arp 只有 LibreNMS 的 ARP 同步會寫
        elif ((use_s and s_seen) or (use_l and l_seen) or (use_a and a_seen)
              or (use_w and w_seen) or (use_z and z_seen) or (use_c and c_seen) or d_seen):
            # 有被採信的來源看過它，但都過期 → 離線
            new_status = "offline"
        else:
            new_status = "unknown"

        # 逐日觀測：長條圖要能區分「當天真的看到」與「只是沿用上一筆轉換」。
        # arp_only 的日子不算可用 —— ARP 沒有時間概念（見 models/ip_liveness）。
        # 同一天同一個 IP 只有一列，旗標用 OR 累積（一天內先看到上線、後判定離線，兩者都要留下）；
        # 今天已經記過同樣旗標的不再寫
        up = bool(s_fresh or l_fresh or w_fresh or z_fresh or c_fresh or d_fresh)
        down = new_status == "offline"
        arp_only = new_status == "online (arp)"
        before = have_today.get(ip.id)
        merged = (up, down, arp_only) if before is None else (
            before[0] or up, before[1] or down, before[2] or arp_only)
        if merged != before:
            observations.append({"ip_id": ip.id, "day": today, "up": up, "down": down, "arp_only": arp_only})

        if ip.effective_status != new_status:
            prev = ip.effective_status
            status_rows.append({"id": ip.id, "effective_status": new_status})
            # feature B：只記真正的 online↔offline 翻轉（避開首次 None→unknown 噪音）
            if prev is not None:
                was_online = prev.startswith("online")
                now_online = new_status.startswith("online")
                if was_online != now_online:
                    flips.append((ip, prev, new_status, now_online, flip_src))

    if arp_rows:
        await session.execute(update(IPAddress), arp_rows)
    if status_rows:
        await session.execute(update(IPAddress), status_rows)
    if flips:
        latest = await latest_changes(session, [f[0].id for f in flips], "effective_status")
        for ip, prev, new_status, now_online, flip_src in flips:
            # 以前寫死 librenms：沒設 LibreNMS 的站台，失聯事件的來源／操作者也顯示 librenms（#49）
            await log_change(
                session, ip=ip,
                event_type="online" if now_online else "offline",
                field="effective_status", old=prev, new=new_status,
                source=flip_src, latest=latest,
            )
    if observations:
        stmt = pg_insert(IPLivenessDay)
        await session.execute(stmt.on_conflict_do_update(
            index_elements=["ip_id", "day"],
            set_={
                "up": IPLivenessDay.up.op("OR")(stmt.excluded.up),
                "down": IPLivenessDay.down.op("OR")(stmt.excluded.down),
                "arp_only": IPLivenessDay.arp_only.op("OR")(stmt.excluded.arp_only),
            },
        ), observations)
    # 整批寫回繞過了 session 裡已載入的 IP 物件：讓它們下次讀取時重新載入
    for obj in list(session.identity_map.values()):
        if isinstance(obj, IPAddress):
            session.expire(obj, ["effective_status", "last_seen_arp"])
    await session.flush()
    updated = len(status_rows)
    return updated


def port_origin(instance_id: uuid.UUID) -> str:
    return f"librenms:{instance_id}"


async def reconcile_librenms_ports(session: AsyncSession, device_id: uuid.UUID,
                                   origin: str, current: set[str]) -> int:
    """這台 LibreNMS 完整讀到裝置的埠清單（`current`）之後：

    1. 認領：清單裡有、但還沒記來源的舊資料 → 記成這台（升級前匯入的埠在這裡被認領）。
    2. 清除：記成這台、清單裡已經沒有的 → 刪掉（網卡拔掉、USB 網卡移除；LibreNMS 已標 deleted，
       而它的 API 不回傳已刪的埠）。已接線、有穿透對應的埠不動。
    使用者自己建的埠（沒有來源）與別台 LibreNMS 匯入的埠都不碰。`current` 是空的一律不做
    （讀到 0 個埠多半是讀取出問題，不是真的全拔了）。回傳刪除數。
    """
    from app.models.physical import CableTermination, DevicePort
    if not current:
        return 0
    rows = list((await session.execute(
        select(DevicePort).where(DevicePort.device_id == device_id))).scalars().all())
    gone = []
    for p in rows:
        if p.name in current:
            if p.source_origin is None:
                p.source_origin = origin
        elif p.source_origin == origin and p.peer_port_id is None:
            gone.append(p)
    if not gone:
        return 0
    ids = [p.id for p in gone]           # 可能是上萬個（issue #47）：用陣列參數
    keep = set((await session.execute(
        select(DevicePort.peer_port_id).where(in_values(DevicePort.peer_port_id, ids)))).scalars().all())
    keep |= set((await session.execute(
        select(CableTermination.object_id).where(in_values(CableTermination.object_id, ids)))).scalars().all())
    removed = 0
    for p in gone:
        if p.id not in keep:
            await session.delete(p)
            removed += 1
    return removed


async def sync_device_ports(session: AsyncSession, instance: LibreNMSInstance) -> int:
    """把 LibreNMS 介面清單(ifName)同步成已連結 jt-ipam 裝置的 device_ports。

    對 server / switch / OPNsense 等任何受監控裝置都有效。新增缺少的埠並記下來源；
    LibreNMS 不再回報的、而且是它匯入的埠清掉（見 reconcile_librenms_ports）；使用者手動建立
    或改名的埠保留。回傳新增的埠數。

    **`ifAlias` → `device_ports.description`**：交換器上設的 port description
    （「人資-王小明-10.0.0.5」這種）是現場最有價值的一欄，本來沒有拉進來（使用者回報）。
    ⚠️ 只有**真正是描述**的才寫：Linux 主機的 `ifAlias` 會等於 `ifName`／`ifDescr`
    （實機上一整台都是 `eno1np0` 這種），照抄只會把說明欄塞滿介面名稱。
    LibreNMS 沒有值時不動既有說明（與 MAC 同一條規則）。
    """
    from app.services.device_port_filter import is_pseudo_iface, load_patterns, prune_pseudo_ports

    ldevs = list((await session.execute(
        _active_devices(instance.id).where(LibreNMSDevice.jt_ipam_device_id.is_not(None))
    )).scalars().all())
    # 偽介面過濾（與手動匯入同一份設定）：以前排程同步沒套，手動清完下一輪又加回來
    pseudo = await load_patterns(session)
    created = 0
    # 所有裝置的埠一次撈回來（只取判斷要用的欄位）：以前每台各查三次同一批資料、每個埠各 upsert 一次，
    # 5,000 台一輪 6 萬次查詢、200 秒。現在只有真的有新埠／值變了、真的有偽介面或消失的埠才動資料庫。
    have: dict[Any, dict[str, tuple[Any, Any, Any, Any]]] = {}
    if ldevs:
        from app.core.sqlin import in_values
        for dev_id, name, pmac, pdescr, porigin, ppeer in (await session.execute(
                select(DevicePort.device_id, DevicePort.name, DevicePort.mac_address, DevicePort.description,
                       DevicePort.source_origin, DevicePort.peer_port_id)
                .where(in_values(DevicePort.device_id, {d.jt_ipam_device_id for d in ldevs})))).all():
            have.setdefault(dev_id, {})[name] = (pmac, pdescr, porigin, ppeer)
    for d in ldevs:
        try:
            pdata = await _api_get(
                instance,
                f"/api/v0/devices/{d.legacy_device_id}/ports"
                "?columns=ifName,ifAlias,ifDescr,ifType,ifPhysAddress",
                timeout=20.0,
            )
        except LibreNMSError as exc:
            logging.getLogger(__name__).debug(
                "librenms ports fetch failed for %s: %s", d.legacy_device_id, exc)
            continue
        # ifName → (此埠自身的實體 MAC, 交換器上設的描述)
        name_mac: dict[str, str | None] = {}
        # 說明要**整台一起看**才分得出「人寫的」與「韌體樣板」（見 _port_descriptions）
        name_descr = _port_descriptions(list(pdata.get("ports") or []))
        for p in (pdata.get("ports") or []):
            # device_ports.name 上限 255；Windows NDIS 過濾介面描述可能更長 → 先截斷，
            # 讓「既有名稱比對」與 upsert 用同一個值，也不再 StringDataRightTruncation。
            nm = (p.get("ifName") or "").strip()[:255]
            if not nm or nm.lower() in ("null", "unrouted vlan 1"):
                continue
            if pseudo is not None and is_pseudo_iface(nm, p.get("ifType"), pseudo):
                continue
            name_mac[nm] = _norm_mac(p.get("ifPhysAddress"))
        mine = have.get(d.jt_ipam_device_id, {})
        if pseudo is not None and any(peer is None and is_pseudo_iface(n, None, pseudo)
                                      for n, (_m, _ds, _o, peer) in mine.items()):
            await prune_pseudo_ports(session, d.jt_ipam_device_id, pseudo)
        if not name_mac:
            continue
        existing_names = set(mine)
        origin = port_origin(instance.id)
        # 整台一次 upsert（以前每個埠各一次：5,000 台 × 8 埠＝一輪 6 萬次查詢、200 秒）。
        # 用 ON CONFLICT：避免「多台 LibreNMS 裝置對映到同一台 jt-ipam 裝置」或同一輪重複處理時，
        # INSERT 撞 device_port_unique_name (device_id, name) 而中斷整批同步（issue #12）。
        # 有值才覆寫（coalesce）；LibreNMS 沒給就不動既有欄位（別把使用者填的東西清掉）；
        # 值都一樣的不寫（不然每一輪都把所有埠重寫一遍）。一次最多 4,000 列（參數上限 32767）。
        rows = [{"device_id": d.jt_ipam_device_id, "name": n, "type": "network",
                 "mac_address": name_mac[n], "description": name_descr.get(n), "source_origin": origin}
                for n in sorted(name_mac) if _port_changed(mine.get(n), name_mac[n], name_descr.get(n))]
        if rows:
            ins = pg_insert(DevicePort)
            new_mac = func.coalesce(ins.excluded.mac_address, DevicePort.mac_address)
            new_descr = func.coalesce(ins.excluded.description, DevicePort.description)
            upsert = ins.on_conflict_do_update(
                index_elements=["device_id", "name"],
                set_={"mac_address": new_mac, "description": new_descr},
                where=DevicePort.mac_address.is_distinct_from(new_mac)
                | DevicePort.description.is_distinct_from(new_descr))
            for i in range(0, len(rows), 10000):
                await session.execute(upsert, rows[i:i + 10000])
        created += sum(1 for n in name_mac if n not in existing_names)
        # 認領舊資料、清掉 LibreNMS 不再回報的（這裡一定是完整讀到、而且非空的清單）——有事可做才做
        if any((n in name_mac and o is None) or (n not in name_mac and o == origin and peer is None)
               for n, (_m, _ds, o, peer) in mine.items()):
            await session.flush()
            await reconcile_librenms_ports(session, d.jt_ipam_device_id, origin, set(name_mac))
    return created


def _port_descriptions(ports: list[dict[str, Any]]) -> dict[str, str]:
    """哪些 `ifAlias` 真的是「人寫給這個埠的說明」—— 逐台判斷，不是逐筆。

    `ifAlias` 是 IF-MIB 裡唯一由管理員設定的欄位（交換器上的 `description`），
    所以要拉的是它，不是 `ifDescr`（那是設備自己產生的、不可設定）。問題在於**沒設過的
    埠不會是空白**：各家會塞不同的東西，實機上看到三種：

    - Linux 主機：`ifAlias` ＝ `ifName`（`eno1np0`）
    - Cisco Nexus：`ifAlias` ＝ `ifName` ＝ `ifDescr`（`Eth1/1/1`）
    - D-Link DGS-1510：`ifAlias` ＝ `ifDescr` ＝ 韌體樣板
      （`D-Link Corporation DGS-1510-28X … Port 24 on Unit 1`）

    所以判準不能只比對某一個欄位，而是問：**這串字有沒有帶出「埠自己的名字」以外的資訊**。
    兩個訊號：

    1. 等於 `ifName` → 沒有資訊。
    2. 把數字抽掉之後，同一台機器上有三個以上的埠共用同一串 → 那是機器產生的樣板
       （`… Port 1 on Unit 1` / `… Port 2 on Unit 1` 抽掉數字後完全一樣），不是人寫的。

    3. 等於 `ifDescr` → 那是設備自己產生的字（IF-MIB 定義 `ifDescr` 是廠牌／型號／版本，
       不是管理員能設的欄位）。實機上 D-Link 的 `cpu0`、`L2VLAN 1` 這種**一次性**樣板
       只有這個訊號抓得到 —— 它們每台只出現一次，訊號 2 的「重複三次」擋不住。

    ⚠️ 訊號 3 的前提：沒有設備會把管理員設的說明同時反映進 `ifDescr`。三種實機樣態都成立，
    IF-MIB 的定義也支持。若哪天遇到反例（`ifDescr` 跟著人寫的說明一起變），要改的是這一條，
    而不是整套判斷。
    """
    import re

    aliases: dict[str, str] = {}
    for p in ports:
        name = (p.get("ifName") or "").strip()[:255]
        alias = (p.get("ifAlias") or "").strip()
        descr = (p.get("ifDescr") or "").strip()
        if name and alias and alias != name and alias != descr:
            aliases[name] = alias

    # 抽掉數字後相同 → 同一套樣板（`Port 1 on Unit 1` 與 `Port 2 on Unit 1` 視為同一串）
    shape_count: dict[str, int] = {}
    for alias in aliases.values():
        shape = re.sub(r"\d+", "#", alias)
        shape_count[shape] = shape_count.get(shape, 0) + 1

    return {
        name: alias[:500]
        for name, alias in aliases.items()
        if shape_count[re.sub(r"\d+", "#", alias)] < 3
    }


def _port_changed(cur: tuple[Any, Any, Any, Any] | None, new_mac: str | None, new_descr: str | None) -> bool:
    """這個埠要不要寫：新的，或 LibreNMS 給了值而且跟現在不同（沒給的不動，同 upsert 的 coalesce）。"""
    if cur is None:
        return True
    cur_mac, cur_descr = cur[0], cur[1]
    return bool((new_mac and _norm_mac(cur_mac) != new_mac) or (new_descr and cur_descr != new_descr))


def _norm_mac(raw: object) -> str | None:
    """LibreNMS ifPhysAddress 多為無分隔 12 hex（如 bc24112508a0）→ 標準冒號小寫格式。"""
    if not raw:
        return None
    s = str(raw).strip().lower().replace("-", "").replace(":", "").replace(".", "")
    if len(s) != 12 or any(c not in "0123456789abcdef" for c in s):
        return None
    return ":".join(s[i:i + 2] for i in range(0, 12, 2))


# ─────────────────── 主入口 ───────────────────


def _first(d: dict[str, Any], *keys: str) -> Any:
    """依序取第一個有值的 key —— LibreNMS 各版本欄位名略有出入。"""
    for k in keys:
        v = d.get(k)
        if v not in (None, "", []):
            return v
    return None


async def sync_links(
    session: AsyncSession, instance: LibreNMSInstance,
) -> tuple[int, int, int]:
    """撈 LibreNMS 的 LLDP / CDP 鄰居（links 表）→ 鏡像進 `librenms_links`。

    回 `(seen, upserted, pruned)`。

    來源端點：`GET /api/v0/resources/links`（全域）。**已對實機確認過路徑**：
    LibreNMS 認得這條路由，沒有資料時回 404 且 body 是
    `{"status":"error","message":"Links do not exist"}` —— 這代表「links 表是空的」
    而不是「端點不支援」，所以那個 404 要當成「0 筆」正常處理，不能當錯誤讓整輪 sync 掛掉。
    （`/api/v0/resources/links/all` 不存在，會被當成 `links/{id}` 回 400。）

    欄位一律容錯取：LibreNMS 各版本欄位名略有出入，抓不到就留空，不讓單一欄位
    拖垮整批。對端未被監控時 `remote_device_id` 為 0/空 —— 這種只有 LLDP 通報字串
    的鄰居仍然要留下來，那正是「接到未納管交換器」的線索。
    """
    if not instance.sync_links:
        return 0, 0, 0

    try:
        data = await _api_get(instance, "/api/v0/resources/links", timeout=60.0)
    except LibreNMSError as exc:
        # 沒有任何鄰居資料 → LibreNMS 回 404 "Links do not exist"，這是正常狀態
        if "Links do not exist" in str(exc) or ": 404" in str(exc):
            data = {"links": []}
        else:
            raise

    rows = data.get("links") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        rows = []

    def _int(v: object) -> int | None:
        try:
            n = int(str(v))
        except (TypeError, ValueError):
            return None
        return n or None          # LibreNMS 用 0 表示「沒有對應」

    def _txt(v: object) -> str | None:
        s2 = str(v).strip() if v is not None else ""
        return s2 or None

    seen = upserted = 0
    now = datetime.now(UTC)
    fresh_ids: set[int] = set()
    # 本端是 LibreNMS 停用的裝置：鄰居資料是凍結的，不收（沒算進 fresh_ids，下面會把舊的一起清掉）
    disabled_legacy = set((await session.execute(select(LibreNMSDevice.legacy_device_id).where(
        LibreNMSDevice.instance_id == instance.id, LibreNMSDevice.status == "disabled"))).scalars().all())

    for r in rows:
        if not isinstance(r, dict):
            continue
        lid = _int(_first(r, "id", "link_id"))
        if lid is None:
            continue
        if _int(_first(r, "local_device_id")) in disabled_legacy:
            continue
        seen += 1
        fresh_ids.add(lid)

        existing = (await session.execute(
            select(LibreNMSLink).where(
                LibreNMSLink.instance_id == instance.id,
                LibreNMSLink.legacy_link_id == lid,
            )
        )).scalars().first()
        if existing is None:
            existing = LibreNMSLink(instance_id=instance.id, legacy_link_id=lid)
            session.add(existing)

        existing.protocol = _txt(_first(r, "protocol"))
        existing.active = bool(_first(r, "active") in (1, "1", True, "true", None))
        existing.local_device_id = _int(_first(r, "local_device_id"))
        existing.local_port_id = _int(_first(r, "local_port_id"))
        existing.local_port_name = _txt(_first(r, "local_port", "local_ifname", "ifName"))
        existing.remote_device_id = _int(_first(r, "remote_device_id"))
        existing.remote_port_id = _int(_first(r, "remote_port_id"))
        existing.remote_hostname = _txt(_first(r, "remote_hostname", "remote_device"))
        existing.remote_port = _txt(_first(r, "remote_port", "remote_ifname"))
        existing.remote_platform = _txt(_first(r, "remote_platform"))
        existing.remote_version = _txt(_first(r, "remote_version"))
        existing.last_seen_at = now
        upserted += 1

    await session.flush()

    # 清掉這個實例先前有、這次沒看到的鄰居（拔線 / 對端下線 → 關係就不該留著）
    stale = select(LibreNMSLink.id).where(LibreNMSLink.instance_id == instance.id)
    if fresh_ids:
        stale = stale.where(not_in_values(LibreNMSLink.legacy_link_id, fresh_ids))
    stale_ids = list((await session.execute(stale)).scalars().all())
    pruned = 0
    if stale_ids:
        await session.execute(
            delete(LibreNMSLink).where(in_values(LibreNMSLink.id, stale_ids))
        )
        pruned = len(stale_ids)
    return seen, upserted, pruned


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
            # 設備自己的介面位址（多 LAN 的閘道）：只在這個實例「用於上線判定」時採用；讀不到不算失敗
            if instance.use_for_status:
                try:
                    await sync_interface_ips(session, instance)
                    await session.commit()
                except LibreNMSError as exc:
                    await session.rollback()
                    logging.getLogger(__name__).warning("librenms %s interface ips: %s", instance.name, exc)
        if instance.sync_arp:
            ac: dict[str, Any] = {}
            s, i, u, f = await sync_arp(session, instance, autocreate_stats=ac)
            summary.arp_seen, summary.arp_inserted, summary.arp_updated = s, i, u
            summary.ip_mac_filled = f
            summary.arp_ips_created = int(ac.get("created") or 0)
            summary.arp_create_skipped = dict(ac.get("skipped") or {})
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
        if instance.sync_links:
            s, u, pr = await sync_links(session, instance)
            summary.links_seen, summary.links_upserted, summary.links_pruned = s, u, pr
            await session.commit()
        if instance.use_for_status:
            await recompute_effective_status(session, instance)
            await session.commit()
        instance.last_sync_at = datetime.now(UTC)
        instance.last_error = None
    except LibreNMSError as exc:
        # 連不上／認證失敗是硬失敗：寫 last_error 後往上拋，作業才會是「失敗」而不是「成功、0 筆」
        # （issue #44 在 Proxmox 回報的同一個寫法）。前面已經 commit 的階段不受影響。
        instance.last_error = str(exc)
        await session.commit()
        raise
    await session.commit()
    return summary
