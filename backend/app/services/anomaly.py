"""異常偵測（規格書 §6.9）。

偵測規則：
- IP 衝突：同 IP 在短時間（1h）內 ARP 看到不同 MAC
- MAC 變動：同 MAC 在多個 switch+port 跳動（1h 內）
- 失聯 IP：IPAM 有 IP 紀錄但 ARP/FDB 從未看過超過 N 天
- 未授權設備：ARP 出現的 IP 但 IPAM 沒有
- **非法 DHCP 伺服器**：掃描代理在網段上收到 DHCPOFFER，但該位址沒有被標記為 DHCP
  伺服器。這是少數「一出現幾乎必定有事」的異常 —— 多半是有人插了台家用路由器，或某台
  虛擬機誤開了 DHCP，會把租約發給不該拿的機器。

每次偵測結果寫站內通知 + Webhook 事件 + audit。
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import String, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sqlin import in_values
from app.models.address import IPAddress
from app.models.librenms import ARPEntry, FDBEntry, LibreNMSDevice
from app.models.user import User
from app.services.notification import deliver_event, push_notification
from app.services.oui import mac_prefix, vendor_map


@dataclass
class AnomalyReport:
    ip_conflicts: list[dict[str, Any]] = field(default_factory=list)
    mac_drifts: list[dict[str, Any]] = field(default_factory=list)
    # 參考用的換埠（虛擬機遷移、隨機 MAC 漫遊、上行埠之間的路徑變更）：畫面上收合顯示，不通知、不算總數
    mac_drift_reference: list[dict[str, Any]] = field(default_factory=list)
    ghost_ips: list[dict[str, Any]] = field(default_factory=list)
    unauthorized_ips: list[dict[str, Any]] = field(default_factory=list)
    rogue_dhcp: list[dict[str, Any]] = field(default_factory=list)
    external_exposure: list[dict[str, Any]] = field(default_factory=list)
    dangling_dns: list[dict[str, Any]] = field(default_factory=list)
    duplicate_ip_records: list[dict[str, Any]] = field(default_factory=list)
    suspicious_changes: list[dict[str, Any]] = field(default_factory=list)
    fw_rule_rot: list[dict[str, Any]] = field(default_factory=list)
    arp_only_liveness: list[dict[str, Any]] = field(default_factory=list)
    stale_device_links: list[dict[str, Any]] = field(default_factory=list)
    mac_flapping: list[dict[str, Any]] = field(default_factory=list)
    identity_changes: list[dict[str, Any]] = field(default_factory=list)
    #: 同一台主機的多張網卡回應同一個 IP（ARP flux，2026-10-09 從 IP 衝突分出來）
    arp_flux: list[dict[str, Any]] = field(default_factory=list)
    #: 兩個子網段混在同一個二層（2026-10-09）
    l2_subnet_bleed: list[dict[str, Any]] = field(default_factory=list)
    #: DNS 比對群組各台的紀錄不一樣（已確認：持續超過寬限時間；2026-10-10）
    dns_compare_mismatch: list[dict[str, Any]] = field(default_factory=list)
    #: 未授權 IP 的總數（清單最多列 MAX_UNAUTHORIZED 筆）
    unauthorized_total: int = 0

    def total(self) -> int:
        """所有類別的發現筆數合計（排程的日誌用）。"""
        return sum(len(v) for v in self.to_dict().values() if isinstance(v, list))

    def to_dict(self) -> dict[str, Any]:
        return {
            "ip_conflicts": self.ip_conflicts,
            "arp_flux": self.arp_flux,
            "l2_subnet_bleed": self.l2_subnet_bleed,
            "mac_drifts": self.mac_drifts,
            "mac_drift_reference": self.mac_drift_reference,
            "ghost_ips": self.ghost_ips,
            "arp_only_liveness": self.arp_only_liveness,
            "stale_device_links": self.stale_device_links,
            "mac_flapping": self.mac_flapping,
            "identity_changes": self.identity_changes,
            "unauthorized_ips": self.unauthorized_ips,
            "unauthorized_total": max(self.unauthorized_total, len(self.unauthorized_ips)),
            "rogue_dhcp": self.rogue_dhcp,
            "external_exposure": self.external_exposure,
            "dangling_dns": self.dangling_dns,
            "dns_compare_mismatch": self.dns_compare_mismatch,
            "duplicate_ip_records": self.duplicate_ip_records,
            "suspicious_changes": self.suspicious_changes,
            "fw_rule_rot": self.fw_rule_rot,
            "total": (
                len(self.ip_conflicts) + len(self.mac_drifts)
                + len(self.ghost_ips) + len(self.unauthorized_ips)
                + len(self.rogue_dhcp) + len(self.external_exposure)
                + len(self.fw_rule_rot) + len(self.arp_only_liveness)
                + len(self.dangling_dns) + len(self.duplicate_ip_records)
                + len(self.suspicious_changes) + len(self.stale_device_links)
                + len(self.mac_flapping) + len(self.identity_changes)
                + len(self.arp_flux) + len(self.l2_subnet_bleed)
                + len(self.dns_compare_mismatch)
            ),
        }


def _is_locally_administered(mac: str) -> bool:
    """第一個位元組的 bit 1 為 1 ＝ 本地管理位址（不是廠商燒錄的全球唯一位址）。

    為什麼要標出來：虛擬機、容器、以及手機的 MAC 隨機化隱私功能都會用這類位址，
    它們沒有 OUI 登記所以查不到廠商。同一個 IP 上出現這種位址，多半是同一台裝置換了
    位址（重新連線、遷移、故障接手），而不是兩台機器搶同一個 IP —— 不標示的話，
    這些會混在真正的衝突裡讓整張表看起來像雜訊。
    """
    from app.services.arp_quality import is_locally_administered
    return is_locally_administered(mac)


#: MAC 來回切換：這段時間內，在同樣兩個 MAC 之間切換至少這麼多次才算（見 detect_ip_conflicts）
FLIP_WINDOW = timedelta(hours=24)
FLIP_MIN_CHANGES = 3


async def detect_ip_conflicts(
    session: AsyncSession, *, window: timedelta = timedelta(hours=1),
    flip_window: timedelta = FLIP_WINDOW,
) -> list[dict[str, Any]]:
    """同一個網路裡的同一個 IP 被兩台以上的機器使用。兩種依據（GitHub issue #41）：

    - `arp`：最近 1 小時內，ARP 觀測看到 ≥2 個 MAC。觀測來自 LibreNMS、掃描代理、防火牆
      ARP 表（以前只有 LibreNMS 會寫，沒接 LibreNMS 的站台這條永遠是空的）。
    - `mac_flip`：最近 24 小時內，IP 記錄上的 MAC 在同樣兩個位址之間切換 ≥3 次。掃描代理
      每輪只看得到一個 MAC，掃描間隔比 1 小時長時 `arp` 那條湊不到兩個；切換一次（換網卡）、
      來回一次（DHCP 租約發回原主）都不算。

    範圍以子網路界定 —— 重疊網段（兩個單位各有一個 10.9.0.5）不能互相判成衝突。LibreNMS 的
    觀測沒有子網路：IP 只落在一個子網路時歸到那裡，否則只跟同樣沒有子網路的觀測比。

    2026-10-09：疑似讀壞的 MAC 與過期快取（services/arp_quality）照樣列出、標上原因，但不算機器數；
    全部 MAC 都屬於同一台裝置的移到 `detect_arp_flux`。
    """
    conflicts, _flux = await analyze_ip_conflicts(session, window=window, flip_window=flip_window)
    return conflicts


async def detect_arp_flux(
    session: AsyncSession, *, window: timedelta = timedelta(hours=1),
    flip_window: timedelta = FLIP_WINDOW,
) -> list[dict[str, Any]]:
    """同一台主機的多張網卡回應同一個 IP（ARP flux），不是兩台機器在搶。

    以前直接從 IP 衝突裡拿掉、什麼都不說；但它本身是要處理的設定問題（路由器的日誌每 20 分鐘一筆
    `arp: ... moved from ... to ...`），而且一般「IP 衝突」的處理方式是去找另一台設錯的機器 ——
    使用者照那個方向查會白繞一大圈（2026-10-09 回饋）。成因通常是 Linux 的 arp_ignore=0 加上
    兩個網段在同一個二層；回傳裡直接附上建議的 sysctl。
    """
    _conflicts, flux = await analyze_ip_conflicts(session, window=window, flip_window=flip_window)
    return flux


#: ARP flux 的建議修法（Linux）：只用擁有那個位址的網卡回答、只用同網段的來源位址發 ARP
ARP_FLUX_FIX = ("net.ipv4.conf.all.arp_ignore=1", "net.ipv4.conf.all.arp_announce=2")


async def reporter_names(session: AsyncSession, device_ids: set[Any]) -> dict[str, str]:
    """LibreNMS 設備 → 顯示名稱：連到的 jt-ipam 裝置名稱優先，其次 sysName，最後才是 hostname（多半是 IP）。"""
    from app.models.device import Device
    ids = {d for d in device_ids if d}
    if not ids:
        return {}
    rows = (await session.execute(
        select(LibreNMSDevice.id, LibreNMSDevice.sysname, LibreNMSDevice.hostname, Device.name)
        .outerjoin(Device, Device.id == LibreNMSDevice.jt_ipam_device_id)
        .where(in_values(LibreNMSDevice.id, ids)))).all()
    return {str(i): (dn or sn or hn or str(i)) for i, sn, hn, dn in rows}


def mac_rows(macs: dict[str, Any], vendors: dict[str, str | None], suspects: dict[str, str | None],
             names: dict[str, str]) -> list[dict[str, Any]]:
    """一個 IP 的 MAC 清單（最近看到的在前）：廠商、本地管理、誰回報、幾個回報者、可疑原因。"""
    out = []
    for m, seen in sorted(macs.items(), key=lambda kv: kv[1].last or datetime.min.replace(tzinfo=UTC),
                          reverse=True):
        reporters = sorted(seen.reporters.values(),
                           key=lambda r: r["last"] or datetime.min.replace(tzinfo=UTC), reverse=True)
        out.append({
            "mac": m,
            "vendor": vendors.get(m),
            "local": _is_locally_administered(m),
            "last_seen_at": seen.last.isoformat() if seen.last else None,
            "sources": sorted(seen.sources),
            # 2026-10-09：每個回報者（哪台設備的 ARP 表、哪個介面、最後一次）—— 看得出「只有一台看過」
            "reporter_count": len(seen.reporters),
            "reporters": [{"source": r["source"],
                           "device": names.get(str(r["device_id"])) if r["device_id"] else None,
                           "interface": r["interface"],
                           "last_seen_at": r["last"].isoformat() if r["last"] else None}
                          for r in reporters],
            "suspect": suspects.get(m),
        })
    return out


async def analyze_ip_conflicts(
    session: AsyncSession, *, window: timedelta = timedelta(hours=1),
    flip_window: timedelta = FLIP_WINDOW,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(IP 衝突, ARP flux)。兩者同一份資料算出來，run_detection 只跑一次。"""
    from app.models.ip_change_log import IPChangeLog
    from app.services.arp_evidence import normalize
    from app.services.arp_quality import MacSeen, classify_suspects, reporter_key

    now = datetime.now(UTC)
    rows = (
        await session.execute(
            select(ARPEntry.ip, ARPEntry.mac, ARPEntry.subnet_id, ARPEntry.source, ARPEntry.device_id,
                   ARPEntry.interface, func.max(ARPEntry.last_seen_at), func.min(ARPEntry.first_seen_at))
            .where(ARPEntry.last_seen_at >= now - window)
            .group_by(ARPEntry.ip, ARPEntry.mac, ARPEntry.subnet_id, ARPEntry.source, ARPEntry.device_id,
                      ARPEntry.interface)
        )
    ).all()
    # asyncpg 把 INET/MACADDR 回成物件不是字串（已知地雷 #10）—— 一進來就轉成字串
    unscoped = {str(r[0]).split("/")[0] for r in rows if r[2] is None}
    owner: dict[str, str] = {}
    if unscoped:
        seen_in: dict[str, set[str]] = defaultdict(set)
        for ip, sid in (await session.execute(
                select(IPAddress.ip, IPAddress.subnet_id).where(in_values(IPAddress.ip, unscoped)))).all():
            seen_in[str(ip).split("/")[0]].add(str(sid))
        owner = {ip: next(iter(sids)) for ip, sids in seen_in.items() if len(sids) == 1}

    # (子網路, IP) → MAC → 誰在什麼時候看到
    found: dict[tuple[str | None, str], dict[str, MacSeen]] = defaultdict(dict)
    evidence: dict[tuple[str | None, str], set[str]] = defaultdict(set)
    changes: dict[tuple[str | None, str], int] = {}

    for ip, mac, sid, source, device_id, interface, last, first in rows:
        ip_s = str(ip).split("/")[0]
        m = normalize(mac)
        if m is None:
            continue
        key = (str(sid) if sid else owner.get(ip_s), ip_s)
        found[key].setdefault(m, MacSeen()).add(
            reporter_key(str(source), device_id), source=str(source), at=last, first=first,
            device_id=device_id, interface=interface)
    for key in [k for k, macs in found.items() if len(macs) >= 2]:
        evidence[key].add("arp")

    # MAC 來回切換（異動記錄）
    flips = (await session.execute(
        select(IPChangeLog.subnet_id, IPChangeLog.ip_text, IPChangeLog.old_value,
               IPChangeLog.new_value, IPChangeLog.source, IPChangeLog.created_at)
        .where(IPChangeLog.event_type == "mac_changed",
               IPChangeLog.created_at >= now - flip_window)
        .order_by(IPChangeLog.created_at)
    )).all()
    pairs: dict[tuple[tuple[str | None, str], frozenset[str]], list[tuple[str, datetime, str]]] = (
        defaultdict(list))
    for sid, ip_text, old, new, source, at in flips:
        a, b = normalize(old), normalize(new)
        if a is None or b is None or a == b:
            continue
        pairs[((str(sid) if sid else None, str(ip_text)), frozenset((a, b)))].append(
            (b, at, str(source)))
    for (key, _pair), seq in pairs.items():
        if len(seq) < FLIP_MIN_CHANGES:
            continue
        for mac, at, source in seq:
            found[key].setdefault(mac, MacSeen()).add(source, source=source, at=at)
        evidence[key].add("mac_flip")
        changes[key] = max(changes.get(key, 0), len(seq))

    if not evidence:
        return [], []
    # 帶上 OUI 廠商：兩個裸 MAC 位址擺在一起看不出是誰在打架，「Dell vs Apple」才讓人知道該去找
    # 哪一台；判斷讀壞的 MAC 也要用到（查不到廠商是條件之一）。一次批次查完，不要逐筆查。
    all_macs = {m for k in evidence for m in found[k]}
    by_prefix = await vendor_map(session, list(all_macs))
    vendors = {m: by_prefix.get(mac_prefix(m) or "") for m in all_macs}
    device_of, nics = await _device_nics(session, list(evidence))
    known, corroborated_known = await _known_macs(session, list(evidence), device_of, nics)
    suspects = {k: classify_suspects(found[k], vendors, known.get(k, ()), corroborated_known.get(k[1], ()))
                for k in evidence}

    # 同一台機器的多張網卡（ARP flux）不是衝突：可信的 MAC 只剩一台機器的 → 移到 arp_flux
    flux_keys: list[tuple[str | None, str]] = []
    flux_evidence: dict[tuple[str | None, str], list[str]] = {}
    for key in list(evidence):
        good = {m for m, why in suspects[key].items() if why is None}
        dev = device_of.get(key)
        own = set(nics.get(dev, {})) if dev else set()
        machines = len(good - own) + (1 if good & own else 0)
        if machines >= 2:
            continue
        if len(good & own) >= 2:
            flux_keys.append(key)
            flux_evidence[key] = sorted(evidence[key])
        del evidence[key]

    names = await reporter_names(session, {r["device_id"] for k in [*evidence, *flux_keys]
                                           for s_ in found[k].values() for r in s_.reporters.values()})
    conflicts: list[dict[str, Any]] = []
    for key in sorted(evidence, key=lambda k: (k[1], k[0] or "")):
        sid, ip = key
        rows_ = mac_rows(found[key], vendors, suspects[key], names)
        dev = device_of.get(key)
        own = set(nics.get(dev, {})) if dev else set()
        conflicts.append({
            "ip": ip,
            "subnet_id": sid,
            "evidence": sorted(evidence[key]),
            "changes": changes.get(key),
            "suspect_count": sum(1 for r in rows_ if r["suspect"]),
            # 可信度：至少兩台機器各有兩個以上回報者（或 MAC 來回切換的紀錄）才算 high。
            # 正式環境 2026-10-09：很多衝突的另一方只有某一台 AP 的 ARP 表回報、另一方有十幾個來源 ——
            # 多半是那台的過期快取，但 MAC 是正式燒錄位址，不能直接藏起來，只標低可信度
            "confidence": _conflict_confidence(found[key], suspects[key], own, bool(changes.get(key))),
            "macs": rows_,
        })

    flux: list[dict[str, Any]] = []
    dev_names = await _device_names(session, {device_of[k] for k in flux_keys})
    for key in sorted(flux_keys, key=lambda k: (k[1], k[0] or "")):
        sid, ip = key
        dev = device_of[key]
        rows_ = mac_rows(found[key], vendors, suspects[key], names)
        for r in rows_:
            # 這個 MAC 是那台裝置的哪一張網卡（登記在哪個 IP、或哪個裝置埠）
            r["nic"] = nics.get(dev, {}).get(r["mac"], [])
        flux.append({
            "ip": ip,
            "subnet_id": sid,
            "host_id": str(dev),
            # 不叫 device_name：畫面上那個欄名在 MAC 變動頁的意思是「交換器」
            "host": dev_names.get(dev),
            "evidence": flux_evidence[key],
            "macs": rows_,
            "cause": "arp_ignore",
            "fix": list(ARP_FLUX_FIX),
        })
    return conflicts, flux


def _conflict_confidence(macs: dict[str, Any], suspects: dict[str, str | None], own: set[str],
                         flipping: bool) -> str:
    """high＝至少兩台機器各有兩個以上回報者佐證（同一台裝置的多張網卡算一台）；有來回切換紀錄也算 high。"""
    if flipping:
        return "high"
    strong = {m for m, seen in macs.items() if suspects.get(m) is None and len(seen.reporters) >= 2}
    machines = len(strong - own) + (1 if strong & own else 0)
    return "high" if machines >= 2 else "low"


#: 「已知的真實 MAC」往回看多久（判斷拼接出來的 MAC 用）
KNOWN_MAC_DAYS = 30


async def _known_macs(
    session: AsyncSession, keys: list[tuple[str | None, str]],
    device_of: dict[tuple[str | None, str], Any], nics: dict[Any, dict[str, list[str]]],
) -> tuple[dict[tuple[str | None, str], set[str]], dict[str, set[str]]]:
    """((子網路, IP) → 真實存在的 MAC：所屬裝置的網卡、登記的 MAC、30 天內有兩個以上來源看過的；
    IP → 30 天內有兩個以上來源看過的 MAC)。"""
    from app.services.arp_evidence import normalize
    from app.services.arp_quality import reporter_key

    out: dict[tuple[str | None, str], set[str]] = defaultdict(set)
    for key, dev in device_of.items():
        out[key] |= set(nics.get(dev, {}))
    ips = {ip for _s, ip in keys}
    if not ips:
        return out, {}
    wanted = set(keys)
    for sid, ip, mac in (await session.execute(
            select(IPAddress.subnet_id, IPAddress.ip, IPAddress.mac)
            .where(in_values(IPAddress.ip, ips), IPAddress.mac.is_not(None)))).all():
        k = (str(sid), str(ip).split("/")[0])
        if k in wanted and (m := normalize(str(mac))) is not None:
            out[k].add(m)
    since = datetime.now(UTC) - timedelta(days=KNOWN_MAC_DAYS)
    reporters: dict[tuple[str, str], set[str]] = defaultdict(set)
    for ip, mac, source, dev in (await session.execute(
            select(ARPEntry.ip, ARPEntry.mac, ARPEntry.source, ARPEntry.device_id)
            .where(in_values(ARPEntry.ip, ips), ARPEntry.last_seen_at >= since)
            .group_by(ARPEntry.ip, ARPEntry.mac, ARPEntry.source, ARPEntry.device_id))).all():
        if (m := normalize(mac)) is not None:
            reporters[(str(ip).split("/")[0], m)].add(reporter_key(str(source), dev))
    corroborated: dict[str, set[str]] = defaultdict(set)
    for (ip, m), who in reporters.items():
        if len(who) >= 2:
            corroborated[ip].add(m)
    for key in keys:
        out[key] |= corroborated.get(key[1], set())
    return out, corroborated


async def _device_names(session: AsyncSession, device_ids: set[Any]) -> dict[Any, str]:
    from app.models.device import Device
    if not device_ids:
        return {}
    return {i: n for i, n in (await session.execute(
        select(Device.id, Device.name).where(in_values(Device.id, device_ids)))).all()}


async def _device_nics(
    session: AsyncSession, keys: list[tuple[str | None, str]],
) -> tuple[dict[tuple[str | None, str], Any], dict[Any, dict[str, list[str]]]]:
    """(子網路, IP) → 所屬裝置；裝置 → {MAC: [這個 MAC 是哪張網卡：它登記在哪個 IP、或哪個裝置埠]}。

    Linux 預設會用任何一張網卡回答本機任何一個 IP 的 ARP（arp_ignore=0）；兩個網段在同一個
    廣播網域時，雙網卡主機的一個 IP 會被兩張網卡同時回答，看起來就像兩台機器在搶這個 IP
    （正式環境 2026-10-04：SuperMicro 主機板網卡＋HP 擴充網卡）。沒有連到裝置的 IP 不在結果裡，
    照舊判斷。
    """
    from app.models.physical import DevicePort
    from app.services.arp_evidence import normalize

    scoped = [(sid, ip) for sid, ip in keys if sid]
    if not scoped:
        return {}, {}
    device_of: dict[tuple[str | None, str], Any] = {}
    wanted = {(str(sid), ip) for sid, ip in scoped}
    for sid, ip, dev in (await session.execute(
            select(IPAddress.subnet_id, IPAddress.ip, IPAddress.device_id)
            .where(in_values(IPAddress.ip, {ip for _s, ip in scoped}),
                   IPAddress.device_id.is_not(None)))).all():
        k = (str(sid), str(ip).split("/")[0])
        if k in wanted:
            device_of[k] = dev
    if not device_of:
        return {}, {}
    devices = set(device_of.values())
    nics: dict[Any, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for dev, mac, ip in (await session.execute(
            select(IPAddress.device_id, IPAddress.mac, IPAddress.ip)
            .where(in_values(IPAddress.device_id, devices), IPAddress.mac.is_not(None)))).all():
        if (m := normalize(str(mac))) is not None:
            nics[dev][m].append(str(ip).split("/")[0])
    for dev, mac, name in (await session.execute(
            select(DevicePort.device_id, DevicePort.mac_address, DevicePort.name)
            .where(in_values(DevicePort.device_id, devices),
                   DevicePort.mac_address.is_not(None)))).all():
        if (m := normalize(str(mac))) is not None:
            nics[dev][m].append(str(name))
    return device_of, {d: dict(v) for d, v in nics.items()}


async def _device_macs(
    session: AsyncSession, keys: list[tuple[str | None, str]],
) -> dict[tuple[str | None, str], set[str]]:
    """(子網路, IP) → 這個 IP 所屬裝置的全部 MAC（它其他 IP 上登記的、裝置埠的）。"""
    device_of, nics = await _device_nics(session, keys)
    return {k: set(nics[d]) for k, d in device_of.items() if nics.get(d)}


async def ip_conflict_coverage(
    session: AsyncSession, *, window: timedelta = timedelta(hours=1),
) -> dict[str, Any]:
    """IP 衝突偵測這次有沒有資料可看（給 AI 工具講清楚「沒有依據」與「沒有衝突」的差別）。"""
    from app.services.arp_evidence import coverage
    cov = await coverage(session, window=window)
    cov["flip_window_hours"] = int(FLIP_WINDOW.total_seconds() // 3600)
    return cov


# ── MAC 漂移：同一台交換器上換了埠 ────────────────────────────────────────────
# 2026-09-30 研究（正式機資料）：舊規則「同一個 MAC 在 1 小時內出現在 ≥2 個（交換器, 埠）」
# 在多台交換器的網路裡全是誤報 —— 一台主機的 MAC 本來就會同時出現在它插的存取埠，以及沿路
# 每一台交換器的上行埠（那是路徑，不是移動）。時間窗涵蓋 LibreNMS 最近一次 FDB 探索時 83 筆、
# 全部牽涉上行埠；平常看起來 0 筆只是因為 FDB 的時間是 LibreNMS 每 6 小時才刷新的 updated_at，
# 1 小時窗剛好把它擋掉。
#
# 新規則只看**同一台交換器**：最近一次看到它的埠，跟它前幾天待的埠不一樣＝它換了埠。
# 分類（使用者選「分類顯示」）：實體設備換埠才是正式異常；虛擬機遷移、隨機 MAC 漫遊、
# 上行埠之間的路徑變更列為參考（run_detection 放在 mac_drift_reference，不通知）。

# 虛擬化平台配給 VM 的 MAC 前綴（整合沒登記到的 VM 也認得出來）
_VM_OUIS = ("bc:24:11", "52:54:00", "00:50:56", "00:0c:29", "00:05:69", "00:1c:14", "00:15:5d",
            "00:16:3e", "08:00:27", "00:1c:42")
MAC_DRIFT_REFERENCE = ("vm_migration", "random_mac", "uplink_change")


async def detect_mac_drifts(
    session: AsyncSession, *, window: timedelta = timedelta(hours=24),
    lookback: timedelta = timedelta(days=7),
) -> list[dict[str, Any]]:
    """同一台交換器上換了埠的 MAC。

    - 新位置（最近一次看到它的埠）要在 `window` 內（涵蓋 LibreNMS 的 FDB 探索週期）
    - 舊位置要在 `lookback` 內還看得到（一個月前待過的埠不算這一次的移動）
    - 跟其他偵測一樣：只看有開異常偵測的子網路裡的位址、套用逐 IP 的忽略清單
    每筆帶 category：device_move／vm_migration／random_mac／uplink_change。
    """
    from app.models.virt import VMInterface
    from app.services.topology import UPLINK_MAC_THRESHOLD

    now = datetime.now(UTC)
    since = now - lookback
    rows = (await session.execute(
        select(FDBEntry.mac, FDBEntry.device_id, FDBEntry.port_name,
               func.min(FDBEntry.first_seen_at), func.max(FDBEntry.last_seen_at))
        .where(FDBEntry.last_seen_at >= since, FDBEntry.port_name.is_not(None),
               FDBEntry.device_id.is_not(None))
        .group_by(FDBEntry.mac, FDBEntry.device_id, FDBEntry.port_name)
    )).all()
    # (交換器, MAC) → [(埠, 首見, 末見)]；每個埠背後幾個 MAC（分辨上行埠）
    by_key: dict[tuple[str, str], list[tuple[str, datetime, datetime]]] = defaultdict(list)
    port_macs: dict[tuple[str, str], int] = defaultdict(int)
    for mac, did, port, first, last in rows:
        by_key[(str(did), str(mac))].append((port, first or last, last))
        port_macs[(str(did), port)] += 1

    moves: list[dict[str, Any]] = []
    cutoff = now - window
    for (did, mac), locs in by_key.items():
        if len(locs) < 2:
            continue
        locs.sort(key=lambda x: x[2], reverse=True)
        to = locs[0]
        if to[2] < cutoff:
            continue                               # 新位置不是最近的事
        prev = next((loc for loc in locs[1:] if loc[0] != to[0]), None)
        if prev is None:
            continue
        moves.append({"did": did, "mac": mac, "to": to, "prev": prev, "locs": locs})
    if not moves:
        return []

    # 範圍與忽略：MAC 要對得到「有開異常偵測的子網路」裡的 IP 記錄
    subnet_ids = await _anomaly_subnet_ids(session)
    if not subnet_ids:
        return []
    macs = {m["mac"] for m in moves}
    ips_by_mac: dict[str, list[dict[str, str | None]]] = defaultdict(list)
    ip_id_of: dict[str, Any] = {}
    ignored: set[str] = set()
    for iid, m, ip, hn, sid, ignore in (await session.execute(
            select(IPAddress.id, IPAddress.mac, IPAddress.ip, IPAddress.hostname, IPAddress.subnet_id,
                   IPAddress.anomaly_ignore)
            .where(in_values(IPAddress.mac, macs)).order_by(IPAddress.ip))).all():
        key = str(m)
        if sid not in subnet_ids:
            continue
        if "mac_drifts" in {str(x) for x in (ignore or [])}:
            ignored.add(key)              # 這台設備的任何一筆 IP 標了忽略＝不再報它換埠
        ip_id_of.setdefault(key, iid)
        ips_by_mac[key].append({"ip": str(ip).split("/")[0], "hostname": hn})
    moves = [m for m in moves if m["mac"] in ip_id_of and m["mac"] not in ignored]
    if not moves:
        return []

    vm_macs = {str(v) for v in (await session.execute(
        select(VMInterface.mac).where(in_values(VMInterface.mac, {m["mac"] for m in moves})))).scalars().all()
        if v}
    name_by_id = {str(i): (sn or hn or str(i)[:8]) for i, sn, hn in (await session.execute(
        select(LibreNMSDevice.id, LibreNMSDevice.sysname, LibreNMSDevice.hostname)
        .where(in_values(LibreNMSDevice.id, {uuid.UUID(m["did"]) for m in moves})))).all()}

    def _busy(did: str, port: str) -> bool:
        return port_macs.get((did, port), 0) > UPLINK_MAC_THRESHOLD

    out: list[dict[str, Any]] = []
    for m in moves:
        mac, did, (to_port, to_first, _tl), (from_port, _pf, from_last) = m["mac"], m["did"], m["to"], m["prev"]
        if mac in vm_macs or mac.startswith(_VM_OUIS):
            category = "vm_migration"
        elif _is_locally_administered(mac):
            category = "random_mac"
        elif _busy(did, to_port) and _busy(did, from_port):
            # 兩個多台設備共用的埠之間：上行切換／STP 重新收斂，或在兩台無線基地台之間漫遊
            # （正式機實例：Apple 裝置在兩台 AP 的埠之間來回）—— 不是有人換插線
            category = "uplink_change"
        else:
            category = "device_move"
        switch = name_by_id.get(did, did[:8])
        out.append({
            "mac": mac,
            "category": category,
            "device_id": did,
            "device_name": switch,
            "from_port": from_port,
            "to_port": to_port,
            "port": to_port,                   # 去重指紋：換到新的埠＝新的一筆
            # 第一次出現在新埠的時間；再搬回曾經待過的埠時，不早於它最後一次待在舊埠
            "moved_at": max(to_first, from_last).isoformat(),
            "ips": ips_by_mac.get(mac, []),
            "ip_id": str(ip_id_of[mac]),       # 「忽略」按鈕用（第一筆 IP 記錄）
            "locations": [
                {"device_id": did, "device_name": switch, "port": p, "last_seen_at": last.isoformat()}
                for p, _f, last in m["locs"]
            ],
        })
    out.sort(key=lambda x: x["moved_at"], reverse=True)
    return out


async def _anomaly_subnet_ids(session: AsyncSession) -> set[Any]:
    """有開啟異常偵測的子網路 ID。"""
    from app.models.subnet import Subnet
    return {
        r[0] for r in (await session.execute(
            select(Subnet.id).where(Subnet.anomaly_enabled.is_(True))
        )).all()
    }


async def detect_ghost_ips(
    session: AsyncSession, *, days: int = 30,
) -> list[dict[str, Any]]:
    """IPAM 有的 IP，但 ARP 從未看過或上次看到 > days 天前。

    只看有開啟異常偵測的子網路 —— 訪客／實驗網段本來就常常一堆位址沒人在用，
    報出來只會把真正該處理的埋掉。
    """
    cutoff = datetime.now(UTC) - timedelta(days=days)
    subnet_ids = await _anomaly_subnet_ids(session)
    if not subnet_ids:
        return []
    # 取所有有寫進 IPAM 但其實沒 last_seen_scanner / last_seen_librenms 的
    rows = (
        await session.execute(
            select(IPAddress)
            .where(
                in_values(IPAddress.subnet_id, subnet_ids),
                (
                    (IPAddress.last_seen_scanner.is_(None))
                    | (IPAddress.last_seen_scanner < cutoff)
                )
                & (
                    (IPAddress.last_seen_librenms.is_(None))
                    | (IPAddress.last_seen_librenms < cutoff)
                )
                # ARP 還看得到就不算「失聯」——它是弱證據，但仍是證據。
                # 「只有 ARP 看得到」另有專屬偵測項（arp_only_liveness）。
                & (
                    (IPAddress.last_seen_arp.is_(None))
                    | (IPAddress.last_seen_arp < cutoff)
                ),
            )
            .limit(500)
        )
    ).scalars().all()
    # 防火牆自己的 ARP／VPN 表也是會過期的證據 —— 那邊看得到就不是「失聯」。
    # （這些原本混在 last_seen_scanner 裡，拆開之後如果不補這一關，所有只被防火牆
    #  看到的 IP 會一夜之間全被報成失聯。）在 Python 過濾而不是寫進 SQL：JSONB 裡的
    # 時間是字串，硬轉型會被一筆壞資料炸掉整個偵測。
    from app.services.arp_seen import newest_aging
    from app.services.evidence import DETAILED_SOURCES
    keep = []
    for r in rows:
        fw_seen, _ = newest_aging(r, set(DETAILED_SOURCES))
        if fw_seen is None or fw_seen < cutoff:
            keep.append(r)
    return [
        {
            "ip_address_id": str(r.id),
            "ip": str(r.ip).split("/")[0],
            "hostname": r.hostname,
            "last_seen_scanner": r.last_seen_scanner.isoformat() if r.last_seen_scanner else None,
            "last_seen_librenms": r.last_seen_librenms.isoformat() if r.last_seen_librenms else None,
        }
        for r in keep
    ]


def _is_noise_address(ip: str) -> bool:
    """這個位址天生就不該被當成「未授權裝置」。

    最大宗是 **169.254.x.x**（DHCP 拿不到位址時自己指派的 link-local）—— 那是「這台
    機器沒拿到 IP」的徵狀，不是有人偷接東西。實測一台正式站台的未授權清單 53 筆全是
    這個，真正該看的東西整個被埋掉。

    另外排除多點傳送／保留位址與網段的網路位址、廣播位址：它們不對應到任何一台機器。
    """
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True          # 解析不出來的字串一律不報
    return bool(
        addr.is_link_local or addr.is_multicast or addr.is_loopback
        or addr.is_unspecified or addr.is_reserved
    )


async def detect_arp_only_liveness(
    session: AsyncSession, *, days: int = 3,
) -> list[dict[str, Any]]:
    """看起來上線、但**只有 ARP 這一個來源**在說話的 IP。

    為什麼這值得單獨列出來：ARP 記錄沒有時間概念（LibreNMS 的 ARP API 不回任何時間
    欄位），我們只能因為「這筆還在清單裡」就當成剛看到。來源設備（AP／路由器）的
    ARP 快取不老化的話，機器關掉幾十天，這個 IP 還是會一直顯示上線 —— 實機上就發生過
    一台早就關掉的 VM 被畫成 52 天全綠。

    判準刻意保守：ARP 在 `days` 天內看得到，而掃描代理與 LibreNMS 裝置狀態**從來沒有**
    看過它。兩者只要有一個看過，就代表有會老化的證據可以驗證，不必列進來吵人。
    """
    cutoff = datetime.now(UTC) - timedelta(days=days)
    subnet_ids = await _anomaly_subnet_ids(session)
    if not subnet_ids:
        return []
    rows = (
        await session.execute(
            select(IPAddress)
            .where(
                in_values(IPAddress.subnet_id, subnet_ids),
                IPAddress.last_seen_arp.is_not(None),
                IPAddress.last_seen_arp >= cutoff,
                IPAddress.last_seen_scanner.is_(None),
                IPAddress.last_seen_librenms.is_(None),
            )
            .limit(500)
        )
    ).scalars().all()
    # 防火牆的 ARP／VPN 表會逾時淘汰 → 有它撐著就不是「只有 ARP 在說話」
    from app.services.arp_seen import newest_aging
    from app.services.evidence import DETAILED_SOURCES
    rows = [r for r in rows if newest_aging(r, set(DETAILED_SOURCES))[0] is None]
    return [
        {
            "ip_address_id": str(r.id),
            "ip": str(r.ip).split("/")[0],
            "hostname": r.hostname,
            "mac": str(r.mac) if r.mac else None,
            "last_seen_arp": r.last_seen_arp.isoformat() if r.last_seen_arp else None,
        }
        for r in rows
    ]


# ── 一個 IP 頻繁更換 MAC ────────────────────────────────────────────────────
# detect_mac_drifts 問的是「同一個 MAC 在同一台交換器上換了埠」（換插、遷移）。
# 這一條是反過來：**同一個 IP 一直換 MAC** —— DHCP 集區被反覆重用、有人手動搶用固定 IP，
# 或某台機器在做位址隨機化。
#
# ⚠️ 隨機化是常態不是異常：Windows 11 / macOS / iOS / Android 開了隱私功能之後，每次
# 連線都會換一個本地管理位址。所以這條規則**逐 IP 可以忽略**（`ip_addresses.anomaly_ignore`）。
# 但**不自動**跳過隨機化位址：那也可能是有人在做 MAC 偽裝，該由人看過再決定 ——
# 程式只負責把「這些看起來是隨機化位址」講出來。

# 可以逐 IP 忽略的類別。**刻意不是全部** —— 例如「非法 DHCP 伺服器」不該讓人用
# 「這台就是這樣」關掉，那正是要立刻處理的事。
ANOMALY_IGNORABLE: tuple[str, ...] = (
    "mac_drifts",
    "mac_flapping",
    "identity_changes",
    "ghost_ips",
    "external_exposure",
    "arp_only_liveness",
    "suspicious_changes",
)


def is_ignored(ip: Any, category: str) -> bool:
    """這個 IP 是否被管理員標記為忽略某一類異常。"""
    raw = getattr(ip, "anomaly_ignore", None) or []
    return category in {str(x) for x in raw}


async def detect_mac_flapping(
    session: AsyncSession, *, days: int = 7, min_macs: int = 4,
) -> list[dict[str, Any]]:
    """N 天內出現 ≥ min_macs 種不同 MAC 的 IP。

    疑似讀壞的 MAC、過期快取（services/arp_quality）不算進種數，但照樣列出、標上原因 ——
    正式環境 2026-10-09：一台雙網卡主機的 IP 被算成「4 個 MAC」，其中兩個是路由器 ARP 表切換時
    SNMP 讀到一半拼出來的。
    """
    from app.models.librenms import ARPEntry
    from app.services.arp_quality import MacSeen, classify_suspects, reporter_key

    cutoff = datetime.now(UTC) - timedelta(days=days)
    rows = (await session.execute(
        select(ARPEntry.ip, ARPEntry.mac, ARPEntry.source, ARPEntry.device_id,
               func.max(ARPEntry.last_seen_at), func.min(ARPEntry.first_seen_at))
        .where(ARPEntry.last_seen_at >= cutoff)
        .group_by(ARPEntry.ip, ARPEntry.mac, ARPEntry.source, ARPEntry.device_id)
    )).all()

    by_ip: dict[str, dict[str, MacSeen]] = defaultdict(dict)
    for ip_val, mac, source, device_id, last, first in rows:
        by_ip[str(ip_val).split("/")[0]].setdefault(str(mac), MacSeen()).add(
            reporter_key(str(source), device_id), source=str(source), at=last, first=first,
            device_id=device_id)

    candidates = {ip: macs for ip, macs in by_ip.items() if len(macs) >= min_macs}
    if not candidates:
        return []

    # 只報「有開異常偵測的子網路」裡的位址，並套用逐 IP 的忽略清單
    subnet_ids = await _anomaly_subnet_ids(session)
    if not subnet_ids:
        return []
    ip_rows = (await session.execute(
        select(IPAddress).where(in_values(IPAddress.subnet_id, subnet_ids),
                                in_values(IPAddress.ip, set(candidates)))
    )).scalars().all()
    known = {str(r.ip).split("/")[0]: r for r in ip_rows}
    all_macs = {m for macs in candidates.values() for m in macs}
    by_prefix = await vendor_map(session, list(all_macs))
    vendors = {m: by_prefix.get(mac_prefix(m) or "") for m in all_macs}

    out: list[dict[str, Any]] = []
    for ip_text, macs in candidates.items():
        row = known.get(ip_text)
        if row is None or is_ignored(row, "mac_flapping"):
            continue
        suspects = classify_suspects(macs, vendors)
        good = [m for m, why in suspects.items() if why is None]
        if len(good) < min_macs:
            continue
        macs_sorted = sorted(macs.items(), key=lambda kv: kv[1].last or datetime.min.replace(tzinfo=UTC),
                             reverse=True)
        local = [m for m in good if _is_locally_administered(m)]
        out.append({
            "ip": ip_text,
            "ip_id": str(row.id),
            "hostname": row.hostname,
            "subnet_id": str(row.subnet_id),
            "mac_count": len(good),
            # 疑似讀壞或過期、沒算進 mac_count 的 MAC 數
            "suspect_count": len(macs) - len(good),
            "days": days,
            # 多數是本地管理位址 → 幾乎可以確定是隱私隨機化。講出來，讓人一眼決定
            # 要不要把這個 IP 加進忽略清單，而不是替他決定。
            "randomized": len(local) * 2 >= len(good),
            "randomized_count": len(local),
            "macs": [
                {"mac": m, "last_seen_at": (seen.last.isoformat() if seen.last else None),
                 "first_seen_at": (seen.first.isoformat() if seen.first else None),
                 "randomized": _is_locally_administered(m),
                 "reporter_count": len(seen.reporters),
                 "suspect": suspects.get(m)}
                for m, seen in macs_sorted
            ],
        })
    out.sort(key=lambda r: r["mac_count"], reverse=True)
    return out


# ── 兩個子網段混在同一個二層 ──────────────────────────────────────────────
# 2026-10-09 回饋：ARP flux 的根因是兩個網段沒有真正隔離（交換器之間的連接把兩邊接成同一個廣播網域）。
# 兩種證據：
# - ARP：子網段 A 的某個 IP，ARP 回應用的是子網段 B 的某個 MAC —— 那個 MAC 登記在 B 的某個 IP 上、
#   而且最近確實在 B 回應過（只登記沒在用的舊紀錄不算：VM 改過 IP 時舊紀錄常還留著它的 MAC）。
# - 交換器 MAC 表：同一台交換器、同一個 VLAN 學到兩個不同子網段的 MAC。
# 同一個 MAC 登記在好幾個子網段（路由器的子介面共用一個 MAC）不拿來判斷。
BLEED_DAYS = 7
BLEED_EXAMPLES = 10


async def detect_l2_subnet_bleed(
    session: AsyncSession, *, days: int = BLEED_DAYS,
) -> list[dict[str, Any]]:
    """兩個子網段混在同一個二層（同一個廣播網域）：一組子網段一筆，附 ARP 與交換器 MAC 表的例子。"""
    from app.models.mikrotik import MikroTikRouter
    from app.models.subnet import Subnet
    from app.services.arp_evidence import normalize

    cutoff = datetime.now(UTC) - timedelta(days=days)
    enabled = {str(x) for x in await _anomaly_subnet_ids(session)}   # 下面一律用字串比
    if not enabled:
        return []

    # 登記：(子網路, IP) → MAC；MAC → 登記在哪些子網路的哪些 IP
    reg_mac: dict[tuple[str, str], str] = {}
    reg_of: dict[str, set[tuple[str, str]]] = defaultdict(set)
    ip_subnets: dict[str, set[str]] = defaultdict(set)
    for ip, sid, mac in (await session.execute(
            select(IPAddress.ip, IPAddress.subnet_id, IPAddress.mac))).all():
        ip_s, sid_s = str(ip).split("/")[0], str(sid)
        ip_subnets[ip_s].add(sid_s)
        if mac is not None and (m := normalize(str(mac))) is not None:
            reg_mac[(sid_s, ip_s)] = m
            reg_of[m].add((sid_s, ip_s))

    arp = (await session.execute(
        select(ARPEntry.ip, ARPEntry.mac, ARPEntry.subnet_id, func.max(ARPEntry.last_seen_at))
        .where(ARPEntry.last_seen_at >= cutoff)
        .group_by(ARPEntry.ip, ARPEntry.mac, ARPEntry.subnet_id))).all()
    observed: list[tuple[str, str, str]] = []          # (子網路, IP, MAC)
    for ip, mac, sid, _last in arp:
        ip_s = str(ip).split("/")[0]
        m = normalize(mac)
        if m is None:
            continue
        sid_s = str(sid) if sid else (next(iter(ip_subnets[ip_s])) if len(ip_subnets.get(ip_s, ())) == 1 else None)
        if sid_s:
            observed.append((sid_s, ip_s, m))
    # MAC 的「家」：登記在那裡、而且最近在那裡被看到自己的 IP 回應過；只認只有一個家的 MAC
    active = {(sid, ip, m) for sid, ip, m in observed if reg_mac.get((sid, ip)) == m}
    home: dict[str, tuple[str, str]] = {}
    for m, regs in reg_of.items():
        live = [(sid, ip) for sid, ip in regs if (sid, ip, m) in active]
        if len({sid for sid, _ip in regs}) == 1 and live:
            home[m] = live[0]

    pairs: dict[tuple[str, str], dict[str, list[dict[str, Any]]]] = defaultdict(lambda: {"arp": [], "fdb": []})
    for sid, ip, m in observed:
        h = home.get(m)
        if h is None or h[0] == sid or reg_mac.get((sid, ip)) == m:
            continue
        if sid not in enabled and h[0] not in enabled:
            continue
        pairs[tuple(sorted((sid, h[0])))]["arp"].append(
            {"ip": ip, "subnet_id": sid, "mac": m, "mac_owner_ip": h[1], "mac_owner_subnet_id": h[0]})

    # 交換器 MAC 表：同一台交換器、同一個 VLAN（沒有 VLAN 的列不拿來判斷：一條 trunk 本來就帶著所有 VLAN）
    fdb = (await session.execute(
        select(FDBEntry.device_id, FDBEntry.mikrotik_router_id, FDBEntry.vlan_id_num, FDBEntry.port_name,
               FDBEntry.mac)
        .where(FDBEntry.last_seen_at >= cutoff, FDBEntry.vlan_id_num.is_not(None)))).all()
    groups: dict[tuple[str, int], dict[str, list[tuple[str, str | None]]]] = defaultdict(lambda: defaultdict(list))
    ln_ids: set[Any] = set()
    ros_ids: set[Any] = set()
    for ln_dev, ros, vlan, port, mac in fdb:
        m = normalize(mac)
        if m is None or m not in home or not (ln_dev or ros):
            continue
        if ln_dev:
            ln_ids.add(ln_dev)
        else:
            ros_ids.add(ros)
        groups[(f"librenms:{ln_dev}" if ln_dev else f"mikrotik:{ros}", int(vlan))][home[m][0]].append((m, port))
    sw_names = {f"librenms:{k}": v for k, v in (await reporter_names(session, ln_ids)).items()}
    if ros_ids:
        sw_names.update({f"mikrotik:{i}": n for i, n in (await session.execute(
            select(MikroTikRouter.id, MikroTikRouter.name).where(in_values(MikroTikRouter.id, ros_ids)))).all()})
    for (sw, vlan), by_subnet in groups.items():
        subs = sorted(by_subnet)
        if len(subs) < 2:
            continue
        name = sw_names.get(sw)
        for i, sa in enumerate(subs):
            for sb in subs[i + 1:]:
                if sa not in enabled and sb not in enabled:
                    continue
                pairs[(sa, sb)]["fdb"].append({
                    "switch": name or sw, "vlan": vlan,
                    "macs": {sa: [{"mac": m, "port": pt} for m, pt in by_subnet[sa][:5]],
                             sb: [{"mac": m, "port": pt} for m, pt in by_subnet[sb][:5]]},
                })

    if not pairs:
        return []
    cidrs = {str(i): str(c) for i, c in (await session.execute(
        select(Subnet.id, Subnet.cidr).where(in_values(Subnet.id, {x for k in pairs for x in k})))).all()}
    out: list[dict[str, Any]] = []
    for (sa, sb), ev in sorted(pairs.items(), key=lambda kv: -(len(kv[1]["arp"]) + len(kv[1]["fdb"]))):
        ca, cb = cidrs.get(sa, sa), cidrs.get(sb, sb)
        for x in ev["arp"]:
            x["subnet"] = cidrs.get(x.pop("subnet_id"))
            x["mac_owner_subnet"] = cidrs.get(x.pop("mac_owner_subnet_id"))
        for x in ev["fdb"]:
            x["macs"] = {cidrs.get(k, k): v for k, v in x["macs"].items()}
        out.append({
            "id": f"{ca}|{cb}",                    # 通知去重用
            "subnets": [ca, cb],
            # 代碼跟 IP 衝突的 arp 分開：意思不同（這裡是「ARP 回應用了另一個子網段的 MAC」）
            "evidence": [code for k, code in (("arp", "arp_answer"), ("fdb", "switch_fdb")) if ev[k]],
            "arp_count": len(ev["arp"]),
            "fdb_count": len(ev["fdb"]),
            "arp_examples": ev["arp"][:BLEED_EXAMPLES],
            "fdb_examples": ev["fdb"][:BLEED_EXAMPLES],
        })
    return out


#: 類型或 OS 突變看多久以內的變化
IDENTITY_DAYS = 14


async def detect_identity_changes(
    session: AsyncSession, *, days: int = IDENTITY_DAYS,
) -> list[dict[str, Any]]:
    """同一個位址判讀出的設備類型或 OS 家族變了（Recog 的其他用途 ②）。

    依據是掃描代理定期偵測寫下的異動記錄（kind_changed／os_changed，services/device_identity）：
    只記「從一個確定的值變成另一個」，從不知道到知道不算。印表機突然變成 Windows 主機、攝影機
    變成 Linux 伺服器 —— 可能是 IP 被別台機器拿去用、設備被換掉，或有人冒用。

    只報**現在仍是變更後的樣子**的：A→B 之後又變回 A，表示判讀在兩個答案之間搖擺，不是設備換了。
    雙系統開機這種已知會變的，用逐 IP 忽略關掉。
    """
    from app.models.ip_change_log import IPChangeLog

    subnet_ids = await _anomaly_subnet_ids(session)
    if not subnet_ids:
        return []
    cutoff = datetime.now(UTC) - timedelta(days=days)
    logs = (await session.execute(
        select(IPChangeLog).where(
            IPChangeLog.event_type.in_(("kind_changed", "os_changed")),
            IPChangeLog.created_at >= cutoff,
            in_values(IPChangeLog.subnet_id, subnet_ids),
            IPChangeLog.ip_id.is_not(None),
        ).order_by(IPChangeLog.created_at)
    )).scalars().all()
    if not logs:
        return []
    by_ip: dict[Any, list[Any]] = defaultdict(list)
    for lg in logs:
        by_ip[lg.ip_id].append(lg)
    ips = {r.id: r for r in (await session.execute(
        select(IPAddress).where(in_values(IPAddress.id, list(by_ip))))).scalars().all()}

    out: list[dict[str, Any]] = []
    for ip_id, changes in by_ip.items():
        row = ips.get(ip_id)
        if row is None or is_ignored(row, "identity_changes"):
            continue
        current = {"device_kind": row.device_kind, "os_family": row.os_family}
        kept = []
        for fld in ("device_kind", "os_family"):
            mine = [c for c in changes if c.field == fld]
            if not mine:
                continue
            first, last = mine[0], mine[-1]
            # 現在的值要等於最後一次變更的結果，而且不能是變回原本的樣子
            if current[fld] != last.new_value or current[fld] == first.old_value:
                continue
            kept.append({"field": fld, "old": first.old_value, "new": last.new_value,
                         "at": last.created_at.isoformat() if last.created_at else None,
                         "times": len(mine), "note": last.note})
        if not kept:
            continue
        out.append({
            "ip": str(row.ip).split("/")[0],
            "ip_id": str(row.id),
            "hostname": row.hostname,
            "subnet_id": str(row.subnet_id),
            "device_kind": row.device_kind,
            "device_model": row.device_model,
            "os_guess": row.os_guess,
            "shifts": kept,
            "last_at": max((c["at"] or "") for c in kept) or None,
            "days": days,
        })
    out.sort(key=lambda r: r["last_at"] or "", reverse=True)
    return out


async def _anomaly_networks(session: AsyncSession) -> list[Any]:
    """有開啟異常偵測的子網路（網段物件）。"""
    from app.models.subnet import Subnet
    rows = (await session.execute(
        select(Subnet.cidr).where(Subnet.anomaly_enabled.is_(True))
    )).all()
    nets = []
    for (cidr,) in rows:
        try:
            nets.append(ipaddress.ip_network(str(cidr), strict=False))
        except ValueError:
            continue
    return nets


#: 未授權 IP 清單最多列幾筆（超過時取最近看到的，總數另外回報）
MAX_UNAUTHORIZED = 1000


async def detect_unauthorized_ips(
    session: AsyncSession, *, meta: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """ARP 看到但 IPAM 沒紀錄的 IP。

    只看**有開啟異常偵測的子網路**範圍內的位址。落在所有子網路之外的位址，本來就不是
    這套 IPAM 在管的東西，報出來只會製造雜訊。

    超大規模：以前只任取 2,000 個 ARP 位址、全部 IPAM 位址載進記憶體比對、結果再照字串排序
    切 200 筆 —— 大站台的未授權位址大多根本看不到，也不知道被截掉。現在差集在資料庫做，
    依最後看到時間排序（最近的在前），超過 MAX_UNAUTHORIZED 才截斷；`meta` 有給就填入
    {"total": 範圍內總數, "truncated": 是否截斷}。
    """
    from app.services.ip_autocreate import subnet_index

    host = func.host(ARPEntry.ip)
    registered = select(IPAddress.id).where(func.host(IPAddress.ip) == host).exists()
    cand = list((await session.execute(
        select(host, func.max(ARPEntry.last_seen_at).label("last"))
        .where(~registered).group_by(host)
    )).all())
    # 掃描代理看到、但自動收錄關閉而沒建記錄的位址（unmanaged_sightings）也算：以前這種目擊直接丟掉，
    # 只有 LibreNMS 的 ARP 表也看得到時才會列出來
    from app.models.unmanaged_sighting import UnmanagedSighting
    from app.services.unmanaged import GRID_WINDOW
    s_host = func.host(UnmanagedSighting.ip)
    s_reg = select(IPAddress.id).where(func.host(IPAddress.ip) == s_host).exists()
    sighted: dict[str, dict[str, Any]] = {}
    for ip_v, mac_v, last in (await session.execute(
            select(s_host, func.max(UnmanagedSighting.mac), func.max(UnmanagedSighting.last_seen_at))
            .where(UnmanagedSighting.last_seen_at >= datetime.now(UTC) - GRID_WINDOW, ~s_reg)
            .group_by(s_host))).all():
        sighted[str(ip_v)] = {"mac": mac_v, "last": last}
    known = {str(ip): i for i, (ip, _l) in enumerate(cand)}
    for ip_s, info in sighted.items():
        if ip_s in known:
            ip0, last0 = cand[known[ip_s]]
            cand[known[ip_s]] = (ip0, max(x for x in (last0, info["last"]) if x is not None))
        else:
            cand.append((ip_s, info["last"]))
    nets = subnet_index([(n, None) for n in await _anomaly_networks(session)])

    def _in_scope(ip: str) -> bool:
        if _is_noise_address(ip):
            return False
        addr = ipaddress.ip_address(ip)
        net = nets.longest(addr)
        if net is None:
            return False
        # 網段的網路位址／廣播位址不對應到機器（/31、/32 例外，那兩種沒有這個概念）
        return not (net.version == 4 and net.prefixlen < 31
                    and addr in (net.network_address, net.broadcast_address))

    scoped = [(str(ip), last) for ip, last in cand if _in_scope(str(ip))]
    floor = datetime.min.replace(tzinfo=UTC)
    scoped.sort(key=lambda r: (-(r[1] or floor).timestamp(), int(ipaddress.ip_address(r[0]))))
    if meta is not None:
        meta.clear()
        meta.update({"total": len(scoped), "truncated": len(scoped) > MAX_UNAUTHORIZED})
    unauthorized = [ip for ip, _last in scoped[:MAX_UNAUTHORIZED]]
    if not unauthorized:
        return []
    # 只有一個位址看不出是誰：附上 ARP 看到的 MAC（廠商、隨機 MAC、誰看到的、最後時間）。
    # 一個位址可能有好幾個 MAC（隨機 MAC 輪替、真的有兩台），最近看到的排前面。
    seen: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for ip_v, mac_v, source, last in (await session.execute(
        select(ARPEntry.ip, ARPEntry.mac, ARPEntry.source, func.max(ARPEntry.last_seen_at))
        .where(in_values(ARPEntry.ip, unauthorized))
        .group_by(ARPEntry.ip, ARPEntry.mac, ARPEntry.source)
    )).all():
        ip_s, mac_s = str(ip_v).split("/")[0], str(mac_v)
        info = seen[ip_s].setdefault(mac_s, {"last": last, "sources": set()})
        info["sources"].add(str(source))
        if last and (info["last"] is None or last > info["last"]):
            info["last"] = last
    # 掃描代理的目擊：有 MAC 就併進同一個 MAC 的來源，沒有 MAC 的只算最後看到的時間
    for ip_s, info in sighted.items():
        if ip_s not in seen and not info["mac"]:
            continue
        if info["mac"]:
            m = str(info["mac"]).lower()
            e = seen[ip_s].setdefault(m, {"last": info["last"], "sources": set()})
            e["sources"].add("scanner")
            if info["last"] and (e["last"] is None or info["last"] > e["last"]):
                e["last"] = info["last"]
    vendors = await vendor_map(session, [m for macs in seen.values() for m in macs])
    out: list[dict[str, Any]] = []
    for ip in unauthorized:
        macs = sorted(seen.get(ip, {}).items(), key=lambda kv: kv[1]["last"] or datetime.min.replace(tzinfo=UTC),
                      reverse=True)
        out.append({
            "ip": ip,
            "macs": [{"mac": m, "vendor": vendors.get(mac_prefix(m) or ""),
                      "local": _is_locally_administered(m),
                      "last_seen_at": info["last"].isoformat() if info["last"] else None,
                      "sources": sorted(info["sources"])} for m, info in macs],
            "last_seen_at": (macs[0][1]["last"].isoformat() if macs and macs[0][1]["last"]
                             else (sighted[ip]["last"].isoformat() if ip in sighted and sighted[ip]["last"] else None)),
        })
    return out


# ── 上線狀態（顯示當下才算）──────────────────────────────────────────────────
# 使用者要求（2026-10-01）：異常偵測每一頁有 IP 清單的，都要順便顯示它現在有沒有上線。
# 狀態在**送出結果的當下**算，不存進結果裡：保留下來的結果可能是一個小時前跑的。
# 只送「依據」（各來源最後看到的時間），判定交給前端 —— 與 IP 清單同一顆燈、同一套規則
# （上線門檻與採用哪些來源是每個使用者自己的設定）。

#: 各類別：哪一欄是 IP、哪一欄是 IP 記錄的 id（有 id 就用 id 對，重疊網段才不會對錯筆）
_LIVE_FIELDS: dict[str, tuple[str, str | None]] = {
    "ip_conflicts": ("ip", None),
    "ghost_ips": ("ip", "ip_address_id"),
    "unauthorized_ips": ("ip", None),
    "rogue_dhcp": ("server_ip", None),
    "external_exposure": ("ip", "ip_address_id"),
    "dangling_dns": ("value", None),
    "duplicate_ip_records": ("ip", None),
    "arp_only_liveness": ("ip", "ip_address_id"),
    "stale_device_links": ("ip", "ip_address_id"),
    "mac_flapping": ("ip", "ip_id"),
    "identity_changes": ("ip", "ip_id"),
}
#: 一列有好幾個 IP 的類別（`ips: [{ip, ip_address_id}]`）→ 附 `live_ips: {ip: 依據}`
_LIVE_LISTS = ("mac_drifts", "mac_drift_reference")
_LIVE_COLS = ("last_seen_scanner", "last_seen_librenms", "last_seen_arp", "last_seen_wazuh",
              "last_seen_zabbix")


def _ip_text(v: Any) -> str | None:
    if not isinstance(v, str) or not v:
        return None
    try:
        return str(ipaddress.ip_address(v.split("/")[0]))
    except ValueError:
        return None


def _uuid_or_none(v: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(v)) if v else None
    except ValueError:
        return None


def _iso(v: Any) -> str | None:
    return v.isoformat() if isinstance(v, datetime) else (str(v) if v else None)


async def _liveness_lookup(
    session: AsyncSession, ids: set[uuid.UUID], texts: set[str],
) -> tuple[dict[uuid.UUID, dict[str, Any]], dict[str, dict[str, Any]]]:
    from app.models.subnet import Subnet

    cols = [IPAddress.id, IPAddress.ip, *[getattr(IPAddress, c) for c in _LIVE_COLS],
            IPAddress.arp_seen, IPAddress.exclude_from_ping, Subnet.scan_enabled,
            IPAddress.device_kind, IPAddress.device_model]
    by_id: dict[uuid.UUID, dict[str, Any]] = {}
    by_text: dict[str, dict[str, Any]] = {}

    def _row(r: Any) -> dict[str, Any]:
        d: dict[str, Any] = {"registered": True}
        for i, c in enumerate(_LIVE_COLS):
            d[c] = _iso(r[2 + i])
        d["arp_seen"] = dict(r[2 + len(_LIVE_COLS)] or {})
        d["exclude_from_ping"] = bool(r[3 + len(_LIVE_COLS)])
        d["subnet_scan_enabled"] = bool(r[4 + len(_LIVE_COLS)])
        # 「設備類型」欄（不是上線依據；順路帶，免得每一頁各查一次）
        d["device_kind"] = r[5 + len(_LIVE_COLS)]
        d["device_model"] = r[6 + len(_LIVE_COLS)]
        return d

    conds = []
    if ids:
        conds.append(in_values(IPAddress.id, list(ids)))
    if texts:
        conds.append(in_values(IPAddress.ip, list(texts)))
    if conds:
        from sqlalchemy import or_
        for r in (await session.execute(
                select(*cols).join(Subnet, Subnet.id == IPAddress.subnet_id).where(or_(*conds)))).all():
            d = _row(r)
            by_id[r[0]] = d
            t = str(r[1]).split("/")[0]
            prev = by_text.get(t)
            if prev is None:
                by_text[t] = d
            else:
                # 重疊網段：同一個位址有好幾筆、這一列又沒有 id 可以分 —— 各欄取最新的
                # （「其中一台上線」；有 id 的列不走這裡）
                merged = dict(prev)
                for c in _LIVE_COLS:
                    if d[c] and (not merged[c] or d[c] > merged[c]):
                        merged[c] = d[c]
                merged["arp_seen"] = {**prev["arp_seen"], **d["arp_seen"]}
                merged["device_kind"] = prev.get("device_kind") or d.get("device_kind")
                merged["device_model"] = prev.get("device_model") or d.get("device_model")
                by_text[t] = merged
    # IPAM 沒有記錄的位址（未授權 IP、非法 DHCP 伺服器…）：依據是各來源的 ARP 觀測，
    # 來源對到與 IP 記錄相同的欄位，前端才能用同一套規則判定
    unregistered = [t for t in texts if t not in by_text]
    if unregistered:
        for ip_v, source, last in (await session.execute(
            select(ARPEntry.ip, ARPEntry.source, func.max(ARPEntry.last_seen_at))
            .where(in_values(ARPEntry.ip, unregistered))
            .group_by(ARPEntry.ip, ARPEntry.source)
        )).all():
            t = str(ip_v).split("/")[0]
            d = by_text.setdefault(t, {"registered": False, **dict.fromkeys(_LIVE_COLS),
                                       "arp_seen": {}, "exclude_from_ping": False,
                                       "subnet_scan_enabled": None})
            src = str(source)
            col = {"librenms": "last_seen_arp", "scanner": "last_seen_scanner"}.get(src)
            if col:
                if d[col] is None or _iso(last) > d[col]:
                    d[col] = _iso(last)
            else:
                d["arp_seen"][src] = _iso(last)
    return by_id, by_text


#: 給其他頁面（MAC 歷程）用同一套上線依據
liveness_lookup = _liveness_lookup


async def attach_liveness(session: AsyncSession, data: dict[str, Any]) -> dict[str, Any]:
    """在每一列 IP 附上 `live`（多 IP 的列附 `live_ips`）。回傳新的 dict，不改原本的。

    `live` 是 None ＝ IPAM 沒有記錄、也從沒在 ARP 看過（前端顯示「—」，不猜）。
    """
    ids: set[uuid.UUID] = set()
    texts: set[str] = set()
    for cat, (ip_key, id_key) in _LIVE_FIELDS.items():
        for row in data.get(cat) or []:
            if not isinstance(row, dict):
                continue
            if id_key and (u := _uuid_or_none(row.get(id_key))):
                ids.add(u)
            elif t := _ip_text(row.get(ip_key)):
                texts.add(t)
    for cat in _LIVE_LISTS:
        for row in data.get(cat) or []:
            for it in (row.get("ips") or []) if isinstance(row, dict) else []:
                if isinstance(it, dict):
                    if u := _uuid_or_none(it.get("ip_address_id")):
                        ids.add(u)
                    elif t := _ip_text(it.get("ip")):
                        texts.add(t)
    by_id, by_text = await _liveness_lookup(session, ids, texts)

    def _for(ip_v: Any, id_v: Any) -> dict[str, Any] | None:
        u = _uuid_or_none(id_v)
        if u and u in by_id:
            return by_id[u]
        t = _ip_text(ip_v)
        return by_text.get(t) if t else None

    out = dict(data)
    for cat, (ip_key, id_key) in _LIVE_FIELDS.items():
        rows = data.get(cat)
        if not isinstance(rows, list):
            continue
        new_rows = []
        for row in rows:
            if not isinstance(row, dict) or _ip_text(row.get(ip_key)) is None:
                new_rows.append(row)
                continue
            live = _for(row.get(ip_key), row.get(id_key) if id_key else None)
            new_rows.append({**row, "live": live,
                             # 「設備類型」欄：列自己帶的（例如類型突變的現在型號）優先
                             "device_kind": row.get("device_kind") or (live or {}).get("device_kind"),
                             "device_model": row.get("device_model") or (live or {}).get("device_model")})
        out[cat] = new_rows
    for cat in _LIVE_LISTS:
        rows = data.get(cat)
        if not isinstance(rows, list):
            continue
        out[cat] = [
            {**row, "live_ips": {str(it.get("ip")): _for(it.get("ip"), it.get("ip_address_id"))
                                 for it in (row.get("ips") or []) if isinstance(it, dict) and it.get("ip")}}
            if isinstance(row, dict) else row
            for row in rows
        ]
    return out


#: 非法 DHCP 的觀測多久內算數 —— 異常偵測與清單上的紅色標記共用（以前清單沒有時間界線）
ROGUE_DHCP_WINDOW_DAYS = 7


async def detect_rogue_dhcp(
    session: AsyncSession, *, within_days: int = ROGUE_DHCP_WINDOW_DAYS,
) -> list[dict[str, Any]]:
    """在網段上回應 DHCP、但沒有被標記為 DHCP 伺服器的主機。

    合法與否是**查詢時**才比對的：把它存成欄位的話，管理員事後把某台標記為合法，
    舊記錄仍然會寫著非法。

    `via_relay` 的回應不算 —— 經由中繼轉送過來的伺服器本來就不在這個網段上，
    拿本網段的標記去判它會是必然的誤報。
    """
    from app.models.dhcp_sighting import DHCPSighting
    from app.models.subnet import Subnet

    cutoff = datetime.now(UTC) - timedelta(days=within_days)
    rows = (await session.execute(
        select(DHCPSighting, Subnet.cidr)
        .join(Subnet, Subnet.id == DHCPSighting.subnet_id)
        .where(DHCPSighting.last_seen_at >= cutoff,
               DHCPSighting.via_relay.is_(False))
        .order_by(DHCPSighting.last_seen_at.desc())
        .limit(200)
    )).all()
    if not rows:
        return []

    # ── 這個位址是不是「已知合法」的 DHCP 伺服器。
    #
    # 人工標記**維持逐子網路比對**：重疊網段（多單位共用 192.168.1.0/24）下，
    # 同一個 IP 字串在不同網段是不同機器，只比對字串等於把別人的授權套到自己頭上。
    marked = {
        (r[0], str(r[1]))
        for r in (await session.execute(
            select(IPAddress.subnet_id, IPAddress.ip)
            .where(IPAddress.is_dhcp_server.is_(True))
        )).all()
    }

    # 整合中的防火牆是另一回事：那是**我們自己在管的設備**，租約、規則、NAT 都是
    # 我們同步進來的，還要人來勾一個框說「它是 DHCP 伺服器」並不合理。
    #
    # 這條之所以可以跨網段成立、而人工標記不行，差別在證據強度：「我們管理這台防火牆」
    # 是確定的事實；「別的網段有人勾過同一個 IP 字串」不是（實機：一台服務多個網段的
    # 路由器，在別的網段被看到時會變成永遠消不掉的誤報）。
    integrated: set[str] = set()
    from urllib.parse import urlparse

    from app.models.firewall import OPNsenseFirewall
    _fw_models = [OPNsenseFirewall]
    try:
        from app.models.pfsense import PfSenseFirewall
        _fw_models.append(PfSenseFirewall)
    except Exception:
        pass
    try:
        from app.models.fortigate import FortiGateFirewall
        _fw_models.append(FortiGateFirewall)
    except Exception:
        pass
    try:
        from app.models.paloalto import PaloAltoFirewall
        _fw_models.append(PaloAltoFirewall)
    except Exception:
        pass
    # Check Point：DHCP 由閘道發，管理伺服器的網址不是發 DHCP 的主機 → 放行它管的閘道位址
    from app.models.checkpoint import CheckPointGateway
    for (gw_ip,) in (await session.execute(select(CheckPointGateway.ipv4_address).where(
            CheckPointGateway.ipv4_address.is_not(None)))).all():
        integrated.add(str(gw_ip))
    # 第二階段：手動新增、不在管理伺服器清單裡的閘道也是發 DHCP 的主機 → 看 Gaia API 網址的主機
    from app.models.checkpoint_gaia import CheckPointGaiaTarget
    for (url,) in (await session.execute(select(CheckPointGaiaTarget.gaia_url))).all():
        host = (urlparse(str(url)).hostname or "").strip()
        if host:
            integrated.add(host)
    # 獨立 DHCP 伺服器（issue #45）本來就是發 IP 的：Kea 看控制網址的主機、ISC DHCP 看回報代理的來源位址、
    # Windows DHCP 看設定的主機（以前沒列，Windows DHCP 主機會被報成非法 DHCP）
    from app.models.dhcp_standalone import KeaDhcpServer
    _fw_models.append(KeaDhcpServer)
    for model in _fw_models:
        for (url,) in (await session.execute(select(model.api_url))).all():
            host = (urlparse(str(url)).hostname or "").strip()
            if host:
                integrated.add(host)
    from app.models.dhcp_standalone import IscDhcpServer
    from app.models.scan_agent import ScanAgent
    from app.models.windows_dhcp import WindowsDhcpServer
    for (ip,) in (await session.execute(select(ScanAgent.last_source_ip).join(
            IscDhcpServer, IscDhcpServer.agent_id == ScanAgent.id))).all():
        if ip:
            integrated.add(str(ip).strip())
    for (host,) in (await session.execute(select(WindowsDhcpServer.host))).all():
        if host:
            integrated.add(str(host).strip())
    # ISOinsight 本身就是發 IP 的設備：看設定的 Base URL 主機
    from app.models.isoinsight import IsoInsightSource
    for (url,) in (await session.execute(select(IsoInsightSource.base_url))).all():
        host = (urlparse(str(url)).hostname or "").strip()
        if host:
            integrated.add(host)
    # Technitium DHCP：主控台網址的主機，加上它回報的 DHCP 介面位址（可以跟主控台不是同一個位址）
    from app.models.technitium import TechnitiumDhcpScope, TechnitiumDhcpServer
    for (url,) in (await session.execute(select(TechnitiumDhcpServer.api_url))).all():
        host = (urlparse(str(url)).hostname or "").strip()
        if host:
            integrated.add(host)
    for (addr,) in (await session.execute(select(TechnitiumDhcpScope.server_address).where(
            TechnitiumDhcpScope.server_address.is_not(None)).distinct())).all():
        integrated.add(str(addr))

    out: list[dict[str, Any]] = []
    for sighting, cidr in rows:
        server_ip = str(sighting.server_ip)
        if (sighting.subnet_id, server_ip) in marked or server_ip in integrated:
            continue
        mac = str(sighting.server_mac) if sighting.server_mac else None
        out.append({
            "subnet_id": str(sighting.subnet_id),
            "subnet_cidr": str(cidr),
            "server_ip": server_ip,
            "mac": mac,
            "vendor": None,          # 下面統一補（一次查完 OUI，避免逐筆打 DB）
            "offered_ip": str(sighting.offered_ip) if sighting.offered_ip else None,
            "router": str(sighting.router) if sighting.router else None,
            "first_seen_at": sighting.first_seen_at,
            "last_seen_at": sighting.last_seen_at,
        })

    macs = [o["mac"] for o in out if o["mac"]]
    if macs:
        vendors = await vendor_map(session, macs)
        for o in out:
            if o["mac"]:
                o["vendor"] = vendors.get(mac_prefix(o["mac"]))
    return out




async def detect_external_exposure(session: AsyncSession) -> list[dict[str, Any]]:
    """對外曝險：哪些內部主機被開到外面，而且狀態不對。

    **只讀 jt-ipam 已同步進來的資料表**（`nat_translations`、`opnsense_rules`、
    `dns_records`、`ip_addresses`…），不會連到防火牆或任何設備 —— 這是異常偵測，
    每輪都要跑，不能依賴外部服務通不通。

    這裡全部是算得出來的事實，所以放異常偵測而不是 AI 巡檢：可以直接講「這台對外開著」，
    不必加「可能」。

    曝險來源有兩種，都取自同步結果：
      NAT     ── `nat_translations` 裡未停用、且目標指到某個 IP 的規則
      防火牆規則 ── WAN 介面上 action=pass、direction=in 且目的地是某個內部 IP 的規則

    每個位址只報一次，取最嚴重的一種：
      exposed_archived    子網路已歸檔，門卻還開著 —— 退役沒退乾淨
      exposed_offline     主機已離線，門還開著
      exposed_unmonitored 對外開放，但 Wazuh／LibreNMS 都沒看著它
    另外獨立一種（與上面互斥的另一份清單）：
      dns_to_offline      DNS 還指著這個位址，主機卻已離線

    **不用 owner 當判準**：實機上 360 個 IP 只有 1 個填了 owner，拿它當訊號會把幾乎每一台
    對外主機都標成問題。owner 只當附註帶出去，讓看的人知道找誰。
    """
    import ipaddress as _ipaddr

    from app.models.dns import DNSRecord
    from app.models.firewall_rule import OPNsenseRule
    from app.models.nat import NATTranslation
    from app.models.subnet import Subnet
    from app.models.wazuh import WazuhAgent

    # ── 1. NAT（已同步的表）
    exposures: dict[Any, dict[str, Any]] = {}   # ip_id → {ports, rules}

    def _note(ip_id: Any, port_label: str | None, rule: dict[str, Any]) -> None:
        e = exposures.setdefault(ip_id, {"ports": [], "rules": []})
        if port_label and port_label not in e["ports"]:
            e["ports"].append(port_label)
        e["rules"].append(rule)

    nat_rows = (await session.execute(
        select(NATTranslation).where(
            NATTranslation.disabled.is_(False),
            NATTranslation.dst_ip_id.is_not(None),
        )
    )).scalars().all()
    for nat in nat_rows:
        proto = (nat.protocol or "any").lower()
        _note(nat.dst_ip_id,
              f"{proto}/{nat.dst_port}" if nat.dst_port else proto,
              {"source": "nat", "name": nat.name, "type": nat.type,
               "interface": nat.src_interface})

    # ── 2. 防火牆規則（已同步的表）：WAN 介面上放行進來、且目的地就是某台內部主機
    #     目的地可能是別名（如 allowlist_taiwan）→ 只在解析得出 IP 時才算
    fw_rows = (await session.execute(
        select(OPNsenseRule).where(
            OPNsenseRule.enabled.is_(True),
            func.lower(OPNsenseRule.action) == "pass",
        )
    )).scalars().all()
    wanted: dict[str, list[OPNsenseRule]] = {}
    for r in fw_rows:
        iface = (r.interface or "").upper()
        if "WAN" not in iface:      # 只看對外介面；LAN→LAN 的放行不是曝險
            continue
        if (r.direction or "in").lower() != "in":
            continue
        dest = (r.destination_net or "").strip()
        try:
            _ipaddr.ip_address(dest)
        except ValueError:
            continue                # 別名或網段 → 指不到單一主機，略過
        wanted.setdefault(dest, []).append(r)
    if wanted:
        for ip_id, host in (await session.execute(
            select(IPAddress.id, func.host(IPAddress.ip))
            .where(in_values(func.host(IPAddress.ip), wanted, type_=String()))
        )).all():
            for r in wanted.get(str(host), []):
                proto = (r.protocol or "any").lower()
                _note(ip_id,
                      f"{proto}/{r.destination_port}" if r.destination_port else proto,
                      {"source": "firewall_rule", "name": r.description,
                       "type": "pass", "interface": r.interface})

    out: list[dict[str, Any]] = []
    if exposures:
        rows = (await session.execute(
            select(IPAddress, Subnet)
            .join(Subnet, IPAddress.subnet_id == Subnet.id)
            .where(in_values(IPAddress.id, exposures))
        )).all()
        ip_ids = [ipa.id for ipa, _ in rows]
        # 失聯的 agent 不算「有監控」—— 它沒有在看任何東西，而且它登記的 IP 可能早被回收
        from app.services.wazuh import agent_represents_ip
        ip_by_id = {ipa.id: ipa for ipa, _ in rows}
        monitored: set[Any] = {
            wa.jt_ipam_address_id
            for wa in (await session.execute(
                select(WazuhAgent).where(in_values(WazuhAgent.jt_ipam_address_id, ip_ids))
            )).scalars().all()
            if wa.jt_ipam_address_id
            and agent_represents_ip(wa, ip_by_id.get(wa.jt_ipam_address_id))
        }
        dev_ids = [ipa.device_id for ipa, _ in rows if ipa.device_id]
        if dev_ids:
            ln = {
                r[0] for r in (await session.execute(
                    select(LibreNMSDevice.jt_ipam_device_id)
                    .where(in_values(LibreNMSDevice.jt_ipam_device_id, dev_ids))
                )).all() if r[0]
            }
            monitored |= {ipa.id for ipa, _ in rows if ipa.device_id in ln}

        for ipa, subnet in rows:
            if subnet.archived_at is not None:
                kind = "exposed_archived"
            elif ipa.effective_status == "offline":
                kind = "exposed_offline"
            elif ipa.id not in monitored:
                kind = "exposed_unmonitored"
            else:
                continue    # 對外開放、活著、也有人看著 → 正常，不報
            e = exposures[ipa.id]
            out.append({
                "kind": kind,
                "ip_address_id": str(ipa.id),
                "ip": str(ipa.ip),
                "hostname": ipa.hostname,
                "owner": ipa.owner,
                "effective_status": ipa.effective_status,
                "subnet": str(subnet.cidr),
                "monitored": ipa.id in monitored,
                "ports": e["ports"],
                "rules": e["rules"],
                "names": [],
            })

    # ── 3. DNS 還指著，主機卻已離線（同樣只讀已同步的 dns_records）
    #     用實際 IP 值比對，不靠 ipam_address_id —— 實機上那個欄位 121 筆全是空的
    dns_rows = (await session.execute(
        select(DNSRecord.name, DNSRecord.value, IPAddress.id, IPAddress.hostname,
               IPAddress.owner, Subnet.cidr)
        .join(IPAddress, func.host(IPAddress.ip) == DNSRecord.value)
        .join(Subnet, IPAddress.subnet_id == Subnet.id)
        .where(
            func.upper(DNSRecord.type).in_(("A", "AAAA")),
            IPAddress.effective_status == "offline",
        )
    )).all()
    # 每一筆都給同一組欄位（ports / rules / monitored 也要有），前端才不必為了
    # 少數幾種 kind 特別判斷 —— 少一個鍵就會是一個 undefined 錯誤
    by_ip: dict[Any, dict[str, Any]] = {}
    for name, value, ip_id, host, owner, cidr in dns_rows:
        rec = by_ip.setdefault(ip_id, {
            "kind": "dns_to_offline",
            "ip_address_id": str(ip_id),
            "ip": str(value),
            "hostname": host,
            "owner": owner,
            "effective_status": "offline",
            "subnet": str(cidr),
            "monitored": False,
            "ports": [],
            "rules": [],
            "names": [],
        })
        if name not in rec["names"]:
            rec["names"].append(name)
    out.extend(by_ip.values())
    return out




async def detect_new_exposure(
    session: AsyncSession, *, surface: Any = None,
) -> list[dict[str, Any]]:
    """**新出現**的對外開放服務。

    與 `detect_external_exposure` 的差別：那一條判斷「開著而且狀態不對」，這一條
    只問「這個對外開口以前沒有」。合併回同一類（`external_exposure`）而不是另開事件 ——
    同一個新開的埠若同時觸發兩類，使用者要管兩個開關，而且會開始懷疑哪一個才是真的。

    ⚠️ **第一次執行只建立基準、不報任何東西**：沒有基準時站上每一個既有的對外服務
    都會是「新增」，那會在啟用的當下送出幾十則通知，然後這個功能就被關掉了。
    """
    from sqlalchemy.orm.attributes import flag_modified

    from app.models.system_setting import SystemSetting

    if surface is None:
        from app.services.fw_lookup import attack_surface as surface  # type: ignore[assignment]

    items = await surface(session)
    seen_now = {
        f"{it.get('ip')}|{it.get('port')}|{it.get('proto') or it.get('protocol') or ''}": it
        for it in items if it.get("ip")
    }

    row = await session.get(SystemSetting, "attack_surface_baseline")
    first_run = row is None or not isinstance(row.value, dict) or not row.value.get("keys")
    known = set((row.value or {}).get("keys") or []) if row is not None else set()

    if row is None:
        row = SystemSetting(key="attack_surface_baseline", value={})
        session.add(row)
    row.value = {"keys": sorted(seen_now)}
    flag_modified(row, "value")
    await session.flush()

    if first_run:
        return []

    out: list[dict[str, Any]] = []
    for key, it in seen_now.items():
        if key in known:
            continue
        out.append({**it, "kind": "exposed_new"})
    return out


async def detect_dangling_dns(session: AsyncSession) -> list[dict[str, Any]]:
    """DNS 還解析得到，但指向的位址在 IPAM 裡根本不存在。

    對外網域時這是**子網域接管**的前置條件：名字還在、位址已經沒人管，誰拿到那個位址
    就等於拿到那個名字。內部網域則多半是退役沒清乾淨。

    只看 A／AAAA —— CNAME 的值是名字不是位址，拿去跟 IP 比對必定「找不到」，
    全收會把每一筆 CNAME 都報成懸空。

    （「DNS 指向已離線主機」是另一條，在對外曝險裡：那是位址存在但機器不在；
    這裡是位址根本沒登記。）
    """
    from app.models.dns import DNSRecord, DNSServer, DNSZone
    from app.models.dns_compare_group import DNSCompareGroup
    from app.services.dns_compare import merge_key

    rows = (await session.execute(
        select(DNSRecord.name, DNSRecord.value, DNSRecord.type,
               DNSZone.name.label("zone"), DNSServer.name.label("server"),
               DNSServer.id, DNSServer.compare_group_id, DNSCompareGroup.name.label("group"),
               DNSRecord.name_norm, DNSRecord.value_norm)
        .join(DNSZone, DNSRecord.zone_id == DNSZone.id)
        .join(DNSServer, DNSZone.server_id == DNSServer.id)
        .outerjoin(DNSCompareGroup, DNSCompareGroup.id == DNSServer.compare_group_id)
        .where(func.upper(DNSRecord.type).in_(("A", "AAAA")))
    )).all()
    if not rows:
        return []
    known = {
        str(h) for (h,) in (await session.execute(
            select(func.host(IPAddress.ip))
        )).all()
    }
    # 同一個比對群組裡相同的紀錄合成一筆，列出哪幾台都有（2026-10-10；不同組照舊分開）
    merged: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for name, value, rtype, zone, server, sid, gid, gname, nn, vn in rows:
        v = str(value or "").strip()
        if not v or v in known:
            continue
        k = merge_key(gid, sid, nn, name, rtype, vn, v)
        item = merged.get(k)
        if item is None:
            merged[k] = {"name": name, "value": v, "type": rtype, "zone": zone, "server": server,
                         "compare_group": gname, "_servers": {server}}
        else:
            item["_servers"].add(server)
    out: list[dict[str, Any]] = []
    for item in merged.values():
        item["server"] = ", ".join(sorted(item.pop("_servers")))
        out.append(item)
    return out


async def detect_dns_compare_mismatch(session: AsyncSession) -> list[dict[str, Any]]:
    """DNS 比對群組各台不一樣、而且持續超過寬限時間的差異（services/dns_compare 每次同步後算好的）。

    還在寬限期內的不列：伺服器之間同步本來就有延遲，剛改完的紀錄會短暫只在一台。"""

    from app.models.dns import DNSServer
    from app.models.dns_compare_group import DNSCompareGroup, DNSCompareGroupDiff

    names = {str(i): n for i, n in (await session.execute(select(DNSServer.id, DNSServer.name))).all()}
    out: list[dict[str, Any]] = []
    # 只列已確認的（沒有的那台在寬限時間之後重新拉取過仍然沒有；services/dns_compare.check_group 判定）
    for d, gname in (await session.execute(
            select(DNSCompareGroupDiff, DNSCompareGroup.name)
            .join(DNSCompareGroup, DNSCompareGroup.id == DNSCompareGroupDiff.group_id)
            .where(DNSCompareGroupDiff.confirmed_at.is_not(None))
            .order_by(DNSCompareGroup.name, DNSCompareGroupDiff.zone, DNSCompareGroupDiff.name))).all():
        out.append({"group": gname, "kind": d.kind, "zone": d.zone, "name": d.name or None,
                    "type": d.type or None, "value": d.value or None,
                    "present_on": ", ".join(names.get(x, x) for x in d.present_on),
                    "missing_on": ", ".join(names.get(x, x) for x in d.missing_on),
                    "first_seen_at": d.first_seen_at.isoformat()})
    return out


async def detect_duplicate_ip_records(session: AsyncSession) -> list[dict[str, Any]]:
    """同一個位址在**互相包含**的子網路裡各有一筆紀錄。

    只挑「一個網段包含另一個」的情形。兩個單位各自登記一模一樣的 CIDR 是刻意支援的
    多租戶用法（同一個私網位址在不同單位是不同機器），把那個也報出來，多單位環境會被
    自己的正常設定洗版。

    為什麼要報：整合同步只會標到其中一筆，另一筆的存活狀態與主機名稱會永遠停在舊值 ——
    實機上就這樣讓一台正常運作的機器在畫面上顯示離線、可用率 0%。
    """
    import ipaddress as _ipaddr

    from app.models.subnet import Subnet

    rows = (await session.execute(
        select(IPAddress.id, func.host(IPAddress.ip), IPAddress.hostname,
               IPAddress.effective_status, Subnet.cidr)
        .join(Subnet, IPAddress.subnet_id == Subnet.id)
    )).all()
    by_ip: dict[str, list[dict[str, Any]]] = {}
    for ip_id, host, hostname, status, cidr in rows:
        by_ip.setdefault(str(host), []).append({
            "ip_address_id": str(ip_id), "hostname": hostname,
            "effective_status": status, "subnet": str(cidr),
        })

    out: list[dict[str, Any]] = []
    for ip, recs in by_ip.items():
        if len(recs) < 2:
            continue
        nets = []
        for r in recs:
            try:
                nets.append(_ipaddr.ip_network(r["subnet"], strict=False))
            except ValueError:
                nets.append(None)
        contained = any(
            a is not None and b is not None and a != b and (a.subnet_of(b) or b.subnet_of(a))
            for i, a in enumerate(nets) for b in nets[i + 1:]
        )
        if contained:
            out.append({"ip": ip, "records": recs})
    return out




# 變更行為分析的門檻。刻意保守 —— 會誤報的規則會訓練人忽略整個清單。
CHANGE_WINDOW_HOURS = 24
BULK_DELETE_MIN = 20          # 單一帳號在窗內刪除幾筆算異常
LOGIN_FAIL_MIN = 8            # 同一來源 IP 幾次登入失敗算異常
# 只要發生就該被看見的物件類型（不需要「量大」）
PRIVILEGE_OBJECTS = ("permission", "user", "group", "api_token", "system_settings")


async def detect_suspicious_changes(session: AsyncSession) -> list[dict[str, Any]]:
    """從稽核記錄找出值得看一眼的操作。

    稽核記錄平常沒有人會翻，但裡面藏著出事後才會回頭找的線索。三條規則：
    大量刪除、集中的登入失敗、權限與憑證的變更。

    刻意**不做**「非上班時段的變更」：那需要可靠的時區與工時設定，猜錯會把正常的白天
    工作標成可疑。一條會誤報的規則比沒有規則更糟。
    """
    from app.models.audit import AuditLog
    from app.models.user import User as _User

    since = datetime.now(UTC) - timedelta(hours=CHANGE_WINDOW_HOURS)
    out: list[dict[str, Any]] = []

    names = dict((await session.execute(select(_User.id, _User.username))).all())

    # 1) 同一帳號短時間內大量刪除
    for actor, cnt, first, last in (await session.execute(
        select(AuditLog.actor_user_id, func.count(), func.min(AuditLog.ts), func.max(AuditLog.ts))
        # 只看「有帳號」的刪除：沒有 actor 的是系統同步刪掉重建（實機上一次 967 筆），
        # 那是例行作業。把它算進來，清單第一名永遠是同步，真正的人為誤刪反而被埋掉。
        .where(AuditLog.ts >= since, AuditLog.action == "delete",
               AuditLog.actor_user_id.is_not(None))
        .group_by(AuditLog.actor_user_id)
        .having(func.count() >= BULK_DELETE_MIN)
    )).all():
        out.append({
            "kind": "bulk_delete", "actor": names.get(actor) or str(actor or "?"),
            "count": int(cnt), "first_at": first, "last_at": last,
        })

    # 2) 同一來源 IP 反覆登入失敗
    for ip, cnt, last in (await session.execute(
        select(AuditLog.actor_ip, func.count(), func.max(AuditLog.ts))
        .where(AuditLog.ts >= since, AuditLog.action == "login_failed")
        .group_by(AuditLog.actor_ip)
        .having(func.count() >= LOGIN_FAIL_MIN)
    )).all():
        out.append({
            "kind": "login_failures", "actor_ip": str(ip) if ip else None,
            "count": int(cnt), "last_at": last,
        })

    # 3) 權限／帳號／憑證的變更 —— 發生就要看見
    for otype, action, actor, cnt, last in (await session.execute(
        select(AuditLog.object_type, AuditLog.action, AuditLog.actor_user_id,
               func.count(), func.max(AuditLog.ts))
        .where(AuditLog.ts >= since,
               AuditLog.object_type.in_(PRIVILEGE_OBJECTS),
               AuditLog.action.in_(("create", "update", "delete")))
        .group_by(AuditLog.object_type, AuditLog.action, AuditLog.actor_user_id)
    )).all():
        out.append({
            "kind": "privilege_change", "object_type": otype, "action": action,
            "actor": names.get(actor) or str(actor or "?"),
            "count": int(cnt), "last_at": last,
        })

    return out


async def detect_stale_device_links(session: AsyncSession) -> list[dict[str, Any]]:
    """IP 掛著某台裝置，但那個位址後來換了 MAC —— 關聯多半已經過期。

    這是「把 IP 掛到裝置」這件事本來就有的殘留風險：關聯一旦寫下去就不再重新評估，
    位址日後被別台機器拿去用（DHCP 尤其常見），關聯會**安靜地變成錯的** ——
    畫面上看起來一切正常，只是指到了另一台機器。

    刻意做成事後偵測而不是在寫入端多加判斷：寫入端再怎麼聰明也只是猜，
    而這裡有真正的證據 —— **異動記錄裡，MAC 的變更發生在關聯之後**。

    只用記錄下來的事實，不推論：
    - 只看有 `device_id` 的 IP
    - 找出「設定 device_id」的最後一次時間
    - 那之後若有 `mac` 欄位的變更 → 提出來讓人確認（不自動解除關聯：那同樣是猜）
    """
    from app.models.device import Device
    from app.models.ip_change_log import IPChangeLog

    link_q = (
        select(IPChangeLog.ip_id.label("ip_id"),
               func.max(IPChangeLog.created_at).label("linked_at"))
        .where(IPChangeLog.field == "device_id", IPChangeLog.new_value.isnot(None))
        .group_by(IPChangeLog.ip_id)
    ).subquery()
    rows = (await session.execute(
        select(IPAddress.id, IPAddress.ip, IPAddress.hostname, IPAddress.mac,
               Device.id, Device.name, link_q.c.linked_at,
               func.max(IPChangeLog.created_at))
        .join(link_q, link_q.c.ip_id == IPAddress.id)
        .join(Device, Device.id == IPAddress.device_id)
        .join(IPChangeLog, IPChangeLog.ip_id == IPAddress.id)
        .where(IPChangeLog.field == "mac")
        .group_by(IPAddress.id, IPAddress.ip, IPAddress.hostname, IPAddress.mac,
                  Device.id, Device.name, link_q.c.linked_at)
        .having(func.max(IPChangeLog.created_at) > link_q.c.linked_at)
        .limit(200)
    )).all()

    out: list[dict[str, Any]] = []
    for ip_id, ip, hostname, mac, dev_id, dev_name, linked_at, mac_changed_at in rows:
        out.append({
            "kind": "stale_device_link",
            "ip_id": str(ip_id),
            "ip": str(ip).split("/")[0],
            "hostname": hostname,
            "mac": str(mac) if mac else None,
            "device_id": str(dev_id),
            "device": dev_name,
            "linked_at": linked_at.isoformat() if linked_at else None,
            "mac_changed_at": mac_changed_at.isoformat() if mac_changed_at else None,
        })
    return out


async def run_detection(
    session: AsyncSession, *, notify_admins: bool = True,
) -> AnomalyReport:
    """一次跑所有偵測規則；命中時發通知 + webhook event。"""
    drifts = await detect_mac_drifts(session)
    unauth_meta: dict[str, Any] = {}
    conflicts, flux = await analyze_ip_conflicts(session)
    report = AnomalyReport(
        ip_conflicts=conflicts,
        arp_flux=flux,
        l2_subnet_bleed=await detect_l2_subnet_bleed(session),
        mac_drifts=[d for d in drifts if d["category"] not in MAC_DRIFT_REFERENCE],
        mac_drift_reference=[d for d in drifts if d["category"] in MAC_DRIFT_REFERENCE],
        ghost_ips=await detect_ghost_ips(session),
        unauthorized_ips=await detect_unauthorized_ips(session, meta=unauth_meta),
        rogue_dhcp=await detect_rogue_dhcp(session),
        external_exposure=[*await detect_external_exposure(session),
                           *await detect_new_exposure(session)],
        dangling_dns=await detect_dangling_dns(session),
        dns_compare_mismatch=await detect_dns_compare_mismatch(session),
        duplicate_ip_records=await detect_duplicate_ip_records(session),
        suspicious_changes=await detect_suspicious_changes(session),
        fw_rule_rot=await detect_fw_rule_rot(session),
        arp_only_liveness=await detect_arp_only_liveness(session),
        stale_device_links=await detect_stale_device_links(session),
        mac_flapping=await detect_mac_flapping(session),
        identity_changes=await detect_identity_changes(session),
    )
    # 清單超過上限時只列最近看到的那些；總數另外帶著，畫面才講得出「還有多少沒列出」
    report.unauthorized_total = int(unauth_meta.get("total") or len(report.unauthorized_ips))

    if notify_admins:
        # 手動按「執行偵測」：把當下所有發現都通知（結果就在眼前，這裡不去重）。
        # 與排程共用同一份送出邏輯 —— 兩份實作的話，逐類別的通知設定只會對其中一條路徑生效。
        await _notify_categories(session, report.to_dict(), only_new=False)
        await deliver_event(session, event="anomaly.detected", payload=report.to_dict())

    await session.commit()
    return report



# ── 排程執行 ────────────────────────────────────────────────────────────────
# 偵測結果是「**目前的狀態**」，不是事件流：同一個沒處理的 IP 衝突，每次跑都會再出現一次。
# 人按「執行掃描」時這無所謂（結果就在眼前），但排程每天跑就會每天通知一次、永遠不停 ——
# 最後的下場是整類通知被使用者當成雜訊忽略，真正的新事件也一起被忽略。
# 所以排程只通知「與上次相比是新的」。異常消失不通知：那不是需要有人立刻處理的事。

#   (資料鍵, 中文名稱（郵件與舊介面的退路）, 類別名稱的 i18n 鍵, 頁籤)
# 第三欄只是「類別名稱」而不是整句標題：句型由 _TITLE_NEW / _TITLE_NOW 決定。
# 早期這裡放的是每類一句完整的 notif.anom_*，那些鍵仍留在語言檔裡 —— 發出去的通知
# 存在資料庫，舊資料列還指著它們，刪掉會讓歷史通知顯示成鍵的原文。
_NOTIFY_CATEGORIES: tuple[tuple[str, str, str, str], ...] = (
    ("ip_conflicts", "IP 衝突", "anomaly.ip_conflicts", "ip_conflicts"),
    ("arp_flux", "同一台主機多張網卡回應同一個 IP", "anomaly.arp_flux", "arp_flux"),
    ("l2_subnet_bleed", "兩個子網段混在同一個二層", "anomaly.l2_bleed", "l2_subnet_bleed"),
    ("mac_drifts", "MAC 變動", "anomaly.mac_drifts", "mac_drifts"),
    ("ghost_ips", "失聯 IP", "anomaly.ghost_ips", "ghost_ips"),
    ("unauthorized_ips", "未授權 IP", "anomaly.unauthorized", "unauthorized_ips"),
    ("rogue_dhcp", "非法 DHCP 伺服器", "anomaly.rogue_dhcp", "rogue_dhcp"),
    ("external_exposure", "對外曝險", "anomaly.exposure", "external_exposure"),
    ("dangling_dns", "懸空 DNS", "anomaly.dangling_dns", "dangling_dns"),
    ("duplicate_ip_records", "重複的 IP 紀錄", "anomaly.dup_ip", "duplicate_ip_records"),
    ("suspicious_changes", "可疑的變更", "anomaly.changes", "suspicious_changes"),
    ("fw_rule_rot", "防火牆規則劣化", "anomaly.fw_rot", "fw_rule_rot"),
    ("mac_flapping", "IP 頻繁更換 MAC", "anomaly.mac_flapping", "mac_flapping"),
    ("identity_changes", "類型或 OS 突變", "anomaly.identity_changes", "identity_changes"),
)

# 排程（只報新的）與手動（報當下全部）是兩句不同的話。共用一句的話，人按了「執行掃描」
# 會收到「新增 N 筆」—— 那批其實是早就在那裡的舊帳。
_TITLE_NEW = "notif.anom_new"
_TITLE_NOW = "notif.anom_now"


def _item_fingerprint(item: dict[str, Any]) -> str:
    """一筆發現的識別碼。

    用穩定的欄位組合而不是整包 JSON 雜湊：像「最後出現時間」「天數」這種欄位每次跑都會變，
    拿整包去比的話每一輪都會是「新的」，去重等於沒做。
    """
    # firewall／interface：同一台防火牆好幾條沒有描述的規則，靠它們才分得開
    keys = ("ip", "mac", "cidr", "subnet_cidr", "hostname", "device", "device_name",
            "name", "rule", "rule_id", "server_ip", "fqdn", "port", "id", "firewall", "interface")
    parts = [f"{k}={item[k]}" for k in keys if item.get(k) not in (None, "")]
    if not parts:                      # 沒有任何可辨識欄位就退回整包（保守：寧可多通知一次）
        parts = [json.dumps(item, sort_keys=True, default=str)]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


async def _notify_categories(
    session: AsyncSession, data: dict[str, Any], *, only_new: bool,
) -> int:
    """把發現依類別發出去，回傳實際發出的類別數。

    `only_new=True`（排程）：只發「與上次相比是新的」，比對狀態存在
    `system_settings.anomaly.seen`。`only_new=False`（手動）：當下有什麼就發什麼。
    """
    from app.services.notification import email_users
    from app.services.notify_channels import broadcast_channels
    from app.services.system_config import (
        get_anomaly_seen,
        get_notification_matrix,
        set_anomaly_seen,
    )

    matrix = await get_notification_matrix(session)
    seen = await get_anomaly_seen(session) if only_new else {}
    new_seen: dict[str, list[str]] = dict(seen)
    sent = 0

    admins = (await session.execute(
        select(User).where(User.is_admin.is_(True), User.is_active.is_(True))
    )).scalars().all()

    for key, label, lkey, tab in _NOTIFY_CATEGORIES:
        items = [i for i in (data.get(key) or []) if isinstance(i, dict)]
        fps = [_item_fingerprint(i) for i in items]
        if only_new:
            before = set(seen.get(key) or [])
            fresh = [f for f in fps if f not in before]
            new_seen[key] = fps        # 消失的要跟著移除，否則它再出現時不會通知
            count, total = len(fresh), len(fps)
        else:
            count, total = len(items), len(items)

        # 逐類別的通知設定（管理 → 通知發送設定）。十種發現的份量差很多，
        # 全有全無會讓人為了不被吵而整類關掉，連要緊的那幾種一起消失。
        ch = matrix.get(f"anomaly.{key}", {"in_app": True, "email": False})
        if not count or not (ch.get("in_app") or ch.get("email")):
            continue
        sent += 1
        title = (f"{label}：新增 {count} 筆（共 {total} 筆）" if only_new
                 else f"{label}：{count} 筆")
        body = "詳見「異常偵測」頁面。"
        if ch.get("in_app"):
            for admin in admins:
                await push_notification(
                    session, user_id=admin.id, severity="warning", title=title, body=body,
                    # 路由是 /anomaly（單數）—— 舊值 /anomalies 是錯的，點了會 404
                    link=f"/anomaly?tab={tab}", object_type="anomaly",
                    title_key=_TITLE_NEW if only_new else _TITLE_NOW,
                    body_key="notif.anom_body",
                    # `label_key` 的 `_key` 後綴是給前端看的：它會先把值當翻譯鍵翻好，
                    # 再代入句子。後端不知道收件者的語言，只能組到「鍵」為止。
                    params={"label_key": lkey, "count": count, "total": total},
                )
        if ch.get("email"):
            await email_users(session, [a.email for a in admins], f"[jt-ipam] {title}", body)
        await broadcast_channels(session, subject=title, text=body)

    if only_new:
        await set_anomaly_seen(session, new_seen)
    return sent


async def notify_new_findings(
    session: AsyncSession, report: dict[str, Any] | AnomalyReport,
) -> int:
    """只通知「上次沒看過」的發現，回傳實際發出的類別數。"""
    data = report.to_dict() if isinstance(report, AnomalyReport) else dict(report)
    return await _notify_categories(session, data, only_new=True)


async def run_scheduled(session: AsyncSession) -> AnomalyReport:
    """排程觸發的偵測：跑完只通知新的，並記錄執行時間。"""
    from app.services.system_config import set_anomaly_last_run, set_anomaly_report

    report = await run_detection(session, notify_admins=False)
    await notify_new_findings(session, report)
    await deliver_event(session, event="anomaly.detected", payload=report.to_dict())
    await set_anomaly_last_run(session, at=datetime.now(UTC))
    await set_anomaly_report(session, report.to_dict(), trigger="schedule")
    # 掃描代理／防火牆的 ARP 觀測會隨隨機化 MAC 一直增加，太舊的清掉（偵測只看 1 小時～7 天）
    from app.services.arp_evidence import prune
    await prune(session)
    await session.commit()
    return report


# ── 防火牆規則劣化（rule rot）──
# 規則是資安邊界，但沒有人回頭看它們：埠轉發指向早已回收的位址、any-any 放行、
# WAN 開管理埠。這裡全是確定性檢查 —— 這一頁的敵人是誤報，所以每一條都刻意保守：
# 手動 NAT 不算懸空（沒連 IP 是常態）、停用中的不報（不在生效路徑）、
# 管理埠只看 WAN 類介面（LAN 開 SSH 給 any 是日常）。

_MGMT_PORTS = {"22", "23", "3389", "5900", "623"}
_MGMT_SERVICES = {"ssh", "telnet", "rdp", "vnc"}


def _is_any(v: Any) -> bool:
    """規則的來源／目的是不是「任意」。pfSense 是物件（{"any": ...}），其餘是字串。"""
    if v is None:
        return False
    if isinstance(v, dict):
        return "any" in v or v.get("network") in ("any", "*")
    return str(v).strip().lower() in ("any", "*", "all")


async def _firewall_names(session: AsyncSession) -> dict[str, str]:
    """各廠牌防火牆實例的 id → 名稱（NAT 的 source_origin 是「廠牌:實例 id」）。"""
    from app.models.checkpoint import CheckPointServer
    from app.models.firewall import OPNsenseFirewall
    from app.models.fortigate import FortiGateFirewall
    from app.models.mikrotik import MikroTikRouter
    from app.models.paloalto import PaloAltoFirewall
    from app.models.pfsense import PfSenseFirewall

    out: dict[str, str] = {}
    for model in (OPNsenseFirewall, PfSenseFirewall, FortiGateFirewall, PaloAltoFirewall,
                  MikroTikRouter, CheckPointServer):
        for fid, name in (await session.execute(select(model.id, model.name))).all():
            out[str(fid)] = name
    return out


def _is_generated_nat(n: Any) -> bool:
    """防火牆自己產生的 NAT（OPNsense 的 Anti-Lockout：放行管理介面到防火牆本身）。

    正式環境曾把它們報成懸空轉發。它們沒有目標主機、也沒有轉發埠；OPNsense 給的
    編號是 lockout_N（不隨介面語言改變，分類名稱則可能被翻譯，所以兩個都看）。
    """
    ext = str(n.external_id or "").lower()
    return ext.startswith("lockout") or (n.category or "") == "Automatically generated rules"


def _any_protocol(v: Any) -> bool:
    """規則是否不限協定。只放行 ICMP／ESP 這類單一協定的，不等於「沒有防火牆」。"""
    return str(v or "any").strip().lower() in ("any", "tcp/udp", "tcp", "udp", "")


async def detect_fw_rule_rot(session: AsyncSession) -> list[dict[str, Any]]:
    from app.models.nat import NATTranslation
    from app.models.pfsense import PfSenseFirewall

    items: list[dict[str, Any]] = []
    fw_names = await _firewall_names(session)

    def _fw_of(origin: str | None) -> str | None:
        parts = (origin or "").split(":", 1)
        return fw_names.get(parts[1]) if len(parts) > 1 else None

    # (1) 懸空 NAT：防火牆同步來的、生效中的 port forward，目標解析不到 IPAM。
    #     手動建立的（source_origin 空）不算 —— 手動 NAT 沒連 IP 是常態。
    #     防火牆自動產生的規則、目標是別名的轉發也不算（見 _is_generated_nat）。
    #     目標 IP 與埠都不明的無從判定，比照攻擊面盤點不列（誤報比漏報傷害大）。
    rows = (await session.execute(
        select(NATTranslation).where(
            NATTranslation.type == "port_forward",
            NATTranslation.disabled.is_(False),
            NATTranslation.source_origin.is_not(None),
            NATTranslation.dst_ip_id.is_(None),
            NATTranslation.redirect_alias.is_(None),
        ).limit(100))).scalars().all()
    for n in rows:
        if _is_generated_nat(n) or n.dst_port is None:
            continue
        descr = (n.description or "")[:120]
        items.append({"kind": "dangling_nat", "firewall": _fw_of(n.source_origin),
                      "name": n.name,
                      "source": (n.source_origin or "").split(":")[0],
                      "interface": n.src_interface,
                      "port": n.dst_port,
                      # 名稱多半就是描述（同步時沒有名稱就拿描述來當），一樣的就不重複
                      "descr": descr if descr != n.name else "",
                      "detail": "埠轉發的目標位址不在 IPAM —— 目標可能已回收，或從未登記",
                      "detail_key": "anomaly.rot.dangling_nat"})

    # (2)(3) any-any 放行與 WAN 管理埠：pfSense 精簡規則（JSONB）。
    #     OPNsense / FortiGate 的規則表欄位語意各異，先做 pfSense（資料形狀最穩定），
    #     其餘兩家由規則異動偵測盯變更；誤報比漏報傷害大，逐一驗證過才納入。
    fws = (await session.execute(
        select(PfSenseFirewall).where(PfSenseFirewall.rules.is_not(None)))).scalars().all()
    for fw in fws:
        for r in (fw.rules or []):
            if not isinstance(r, dict) or r.get("disabled"):
                continue
            if str(r.get("type") or "").lower() not in ("pass", "match"):
                continue
            iface = str(r.get("interface") or "").lower()
            dport = str(r.get("destination_port") or "").strip().lower()
            descr = (r.get("descr") or "")[:120]
            # any → any 要連協定與埠都不限：只放行 ping、或只開 443 的，都不是「沒有防火牆」
            if (_is_any(r.get("source")) and _is_any(r.get("destination"))
                    and _any_protocol(r.get("protocol")) and dport in ("", "any")):
                items.append({"kind": "any_any", "firewall": fw.name, "name": descr,
                              "source": "pfsense", "interface": iface, "descr": descr,
                              "detail": "any → any 放行 —— 等於這個介面沒有防火牆",
                              "detail_key": "anomaly.rot.any_any"})
            if "wan" in iface and _is_any(r.get("source")) and (
                    dport in _MGMT_PORTS or dport in _MGMT_SERVICES):
                items.append({"kind": "mgmt_exposed", "firewall": fw.name, "name": descr,
                              "source": "pfsense", "interface": iface, "port": dport,
                              "descr": descr,
                              "detail": "WAN 介面對任意來源開放管理埠",
                              "detail_key": "anomaly.rot.mgmt_exposed"})
    # (4) 別名劣化：別名成員落在「本 IPAM 管理且有開異常偵測」的網段內、卻沒有
    #     IP 紀錄 —— 規則看起來沒變，但別名內容已經指向不明位址。
    #     只看管理範圍內的成員：別名裡放外部位址（放行遠端端點、封鎖清單）是常態，
    #     不設這個門檻會把每個外部 IP 都誤報成劣化。
    from app.models.firewall import OPNsenseSyncedAlias
    from app.models.pfsense import PfSenseSyncedAlias

    nets = await _anomaly_networks(session)
    if nets:
        ipam_ips = {str(r[0]).split("/")[0] for r in
                    (await session.execute(select(IPAddress.ip))).all()}

        def _stale_members(members: list | None) -> list[str]:
            out = []
            for m in (members or [])[:500]:
                text = str(m).strip()
                if "/" in text:
                    continue          # 網段成員不判定（涵蓋範圍太廣，逐一比對必誤報）
                try:
                    addr = ipaddress.ip_address(text)
                except ValueError:
                    continue
                if text in ipam_ips:
                    continue
                if any(addr in n for n in nets):
                    out.append(text)
            return out[:10]

        for alias in (await session.execute(
                select(OPNsenseSyncedAlias).where(OPNsenseSyncedAlias.enabled.is_(True))
        )).scalars().all():
            stale = _stale_members(alias.content)
            if stale:
                items.append({"kind": "alias_rot", "firewall": fw_names.get(str(alias.firewall_id)),
                              "name": alias.name, "source": "opnsense",
                              "descr": (alias.description or "")[:120],
                              "detail": f"別名成員 {', '.join(stale)} 在管理網段內但 IPAM 沒有紀錄",
                              "detail_key": "anomaly.rot.alias_rot",
                              "detail_params": {"members": ", ".join(stale)}})
        for alias in (await session.execute(select(PfSenseSyncedAlias))).scalars().all():
            stale = _stale_members(alias.members)
            if stale:
                items.append({"kind": "alias_rot", "firewall": fw_names.get(str(alias.firewall_id)),
                              "name": alias.name, "source": "pfsense",
                              "descr": (alias.descr or "")[:120],
                              "detail": f"別名成員 {', '.join(stale)} 在管理網段內但 IPAM 沒有紀錄",
                              "detail_key": "anomaly.rot.alias_rot",
                              "detail_params": {"members": ", ".join(stale)}})

    return items[:200]
