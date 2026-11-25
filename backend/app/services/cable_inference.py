"""佈線自動推斷：從鄰居宣告與交換器的 MAC 表，把「明確判斷得出來」的實體連線自動接起來。

使用者 2026-11-25：「連線路徑追蹤 除了人工設定 儘量我們系統內要有自動偵測 推斷 找出關係 自動連起來
有明確可以判斷的就自動 不要全都人工設」。

**自動接（certain）** —— 證據本身就指出兩端各是哪個埠：
- `lldp` / `cdp` / `mndp`：鄰居宣告（SNMP 收集器、LibreNMS、MikroTik）。對方自己說「我接在你的 X 埠、我這端是 Y 埠」，
  兩端的埠都對得到 jt-ipam 的連接埠才算。
- `fdb_single_mac`：交換器的某個埠目前只看到一個 MAC，這個 MAC 只屬於一台裝置、也只出現在這台交換器的這一個埠。
- `fdb_vm_host`：埠上看到的 MAC 全是同一台虛擬化主機和它上面的 VM（主機本身至少有一個 MAC 在場）。

**只建議（suggest）** —— 可能是對的，但有合理的反例：
- `fdb_uplink`：兩台交換器只靠 MAC 表推出的直連（中間可能還有一台不能管理的交換器）。
- 證據互相矛盾（同一個埠被兩組鄰居宣告指到不同地方）。

**人工優先**：
- 人建的線（source=manual）一律不動；跟證據不同時列成「衝突」建議，由人決定。
- 人刪掉的自動線記成 dismissed，同一對埠不再自動建回來。人改過的自動線變成 manual（等於確認過）。
- 自動線的證據指向別處（設備換埠）就撤掉、改接；證據只是消失（關機、MAC 表老化）先留著，
  超過 `STALE_DAYS` 才撤掉。

**守門（缺一個就會接出「很有自信但是錯」的線）**：
- 只用「目前」的 MAC 表（最近 24 小時看過，同 librenms.current_fdb_cutoff）。
- 同一個 MAC 出現在同一台交換器的兩個埠 → 不接（上行埠或在搬動中）。
- MAC 屬於好幾台裝置 → 不接。
- 目標裝置上同一個 MAC 有好幾個實體埠（bond 成員共用 MAC）→ 不接；橋接、bond、VLAN 等虛擬介面不當接線端。
- MAC 是另一台交換器自己的 → 交給 `fdb_uplink`，不用單一 MAC 規則接（對方那端是哪個埠看不出來）。
- 鄰居宣告過的埠，不再用 MAC 推別的。

裝置沒有網卡埠的資料、但 IP 記錄（MAC＋所屬裝置）講得出這個 MAC 是它的：替它建一個網卡埠
（source_origin=`auto:cabling`）再接線；那條線撤掉、埠上沒有別的線時，這個埠也一起收掉。
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import append_audit
from app.core.sqlin import in_values
from app.models.address import IPAddress
from app.models.device import Device
from app.models.librenms import FDBEntry, LibreNMSDevice, LibreNMSLink
from app.models.physical import Cable, CableSuggestion, CableTermination, DevicePort
from app.models.system_setting import SystemSetting
from app.services.arp_precedence import normalize_mac

log = logging.getLogger("cable_inference")

CONFIG_KEY = "cable_inference"
AUTO_PORT_ORIGIN = "auto:cabling"
#: 自動線的證據消失多久才撤掉（關機、MAC 表老化不該讓線馬上消失）
STALE_DAYS = 14
#: MAC 表「目前」的界線（同 librenms.current_fdb_cutoff 的預設）
FDB_CURRENT_HOURS = 24
#: 鄰居宣告多久沒再看到就不算（收集間隔預設 1 小時）
NEIGHBOR_CURRENT_HOURS = 48
_AUDIT_AGENT = "system:cable-inference"

CERTAIN_KINDS = ("lldp", "cdp", "mndp", "fdb_single_mac", "fdb_vm_host")

#: 不當接線端的介面：橋接、bond / team、VLAN 子介面、容器與虛擬網卡
_VIRTUAL_TYPES = {"bridge", "bond", "vlan", "virtual", "lag"}
_VIRTUAL_NAME = re.compile(
    r"^(vmbr|virbr|br-|bridge|bond|team|vlan|docker|veth|tap|tun|lo$|wg|vnet|cni|flannel|cali|kube|ovs|vxlan|"
    r"dummy|ifb|macvlan|ipvlan|vEthernet)", re.IGNORECASE)


@dataclass(frozen=True)
class _Port:
    """推斷只用得到這幾欄；連接埠可能有十幾萬個，不載整個 ORM 物件（超大規模）。"""
    id: uuid.UUID
    device_id: uuid.UUID
    name: str
    type: str | None
    mac_address: str | None
    source_origin: str | None


def _is_virtual(p: _Port) -> bool:
    name = p.name or ""
    return (p.type or "").lower() in _VIRTUAL_TYPES or bool(_VIRTUAL_NAME.match(name)) or "." in name


#: 交換器端不是實體埠的介面：鏈路聚合（Port-channel / ae / Trk / LAG / bond）與 VLAN 介面
_SWITCH_LOGICAL = re.compile(r"^(po\d|port-channel|ae\d|trk\d|lag|bond|bridge-aggregation|vlan|vl\d|irb|bvi)", re.IGNORECASE)


def _is_switch_logical(p: _Port) -> bool:
    return (p.type or "").lower() in _VIRTUAL_TYPES or bool(_SWITCH_LOGICAL.match((p.name or "").strip()))


def _colon(hexmac: str) -> str:
    return ":".join(hexmac[i:i + 2] for i in range(0, 12, 2))


def _pair(a: uuid.UUID, b: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    return (a, b) if str(a) < str(b) else (b, a)


@dataclass
class Finding:
    """一條推斷出來的連線。b_port 為 None＝目標裝置還沒有這個 MAC 的網卡埠，要替它建。"""
    a_port: uuid.UUID
    b_port: uuid.UUID | None
    kind: str
    certain: bool
    evidence: dict[str, Any] = field(default_factory=dict)
    reason: str | None = None
    b_device: uuid.UUID | None = None
    b_mac: str | None = None          # 十六進位（建埠用）


# ─────────────────── 設定 ───────────────────
async def get_config(session: AsyncSession) -> dict[str, Any]:
    """預設開啟（使用者要求「有明確可以判斷的就自動」；網頁上可以關）。"""
    row = await session.get(SystemSetting, CONFIG_KEY)
    v = row.value if row and isinstance(row.value, dict) else {}
    return {
        "enabled": v.get("enabled") is not False,
        "last_run_at": v.get("last_run_at"),
        "last_summary": v.get("last_summary"),
    }


async def set_config(session: AsyncSession, *, enabled: bool, user_id: uuid.UUID | None) -> dict[str, Any]:
    from sqlalchemy.orm.attributes import flag_modified
    row = await session.get(SystemSetting, CONFIG_KEY)
    if row is None:
        row = SystemSetting(key=CONFIG_KEY, value={}, updated_by=user_id)
        session.add(row)
    val = dict(row.value or {})
    val["enabled"] = bool(enabled)
    row.value = val
    row.updated_by = user_id
    flag_modified(row, "value")
    await session.flush()
    return await get_config(session)


async def _save_summary(session: AsyncSession, now: datetime, summary: dict[str, Any]) -> None:
    from sqlalchemy.orm.attributes import flag_modified
    row = await session.get(SystemSetting, CONFIG_KEY)
    if row is None:
        row = SystemSetting(key=CONFIG_KEY, value={})
        session.add(row)
    val = dict(row.value or {})
    val["last_run_at"] = now.isoformat()
    val["last_summary"] = summary
    row.value = val
    flag_modified(row, "value")


# ─────────────────── 蒐集證據 ───────────────────
class _Ports:
    """連接埠的查詢表：依裝置＋名稱、依 MAC。"""

    def __init__(self) -> None:
        self.by_id: dict[uuid.UUID, _Port] = {}
        self.by_dev_name: dict[uuid.UUID, dict[str, _Port]] = {}
        self.by_mac: dict[str, list[_Port]] = {}

    def add(self, p: _Port) -> None:
        self.by_id[p.id] = p
        names = self.by_dev_name.setdefault(p.device_id, {})
        names.setdefault(p.name, p)
        names.setdefault(p.name.lower(), p)
        h = normalize_mac(p.mac_address)
        if len(h) == 12:
            self.by_mac.setdefault(h, []).append(p)

    def named(self, dev: uuid.UUID | None, name: str | None) -> _Port | None:
        if dev is None or not name:
            return None
        names = self.by_dev_name.get(dev) or {}
        n = name.strip()
        return names.get(n) or names.get(n.lower())

    def nic_for(self, dev: uuid.UUID, hexmac: str) -> _Port | bool | None:
        """這台裝置上 MAC 是 hexmac 的接線端。None＝沒有；False＝不只一個（猜不出來）。

        虛擬介面（橋接、bond…）不算；自動建的網卡埠只在沒有別的實體埠時才用。"""
        cands = [p for p in self.by_mac.get(hexmac, []) if p.device_id == dev and not _is_virtual(p)]
        real = [p for p in cands if p.source_origin != AUTO_PORT_ORIGIN]
        pick = real or cands
        if not pick:
            return None
        return pick[0] if len(pick) == 1 else False

    def endpoint_for_remote(self, dev: uuid.UUID | None, remote: str | None) -> _Port | None:
        """鄰居宣告的對端埠：先比名稱，對方報的是 MAC（LLDP portId 子類型 3）就比 MAC。"""
        p = self.named(dev, remote)
        if p is not None or dev is None or not remote:
            return p
        h = normalize_mac(remote)
        if len(h) == 12 and len(remote.strip()) <= 17:
            hit = self.nic_for(dev, h)
            return hit if isinstance(hit, _Port) else None
        return None


async def _load_ports(session: AsyncSession) -> _Ports:
    out = _Ports()
    for row in (await session.execute(select(
            DevicePort.id, DevicePort.device_id, DevicePort.name, DevicePort.type,
            DevicePort.mac_address, DevicePort.source_origin))).all():
        out.add(_Port(*row))
    return out


async def _neighbor_findings(session: AsyncSession, ports: _Ports, now: datetime) -> list[Finding]:
    """三個來源的鄰居宣告 → (本端埠, 對端埠)。兩端都對得到才算。"""
    since = now - timedelta(hours=NEIGHBOR_CURRENT_HOURS)
    out: list[Finding] = []

    # SNMP 收集器（掃描代理讀 LLDP-MIB / CISCO-CDP-MIB）
    from app.models.snmp import SnmpNeighbor
    from app.services.snmp_collect import match_neighbors
    rows = (await session.execute(select(SnmpNeighbor).where(SnmpNeighbor.last_seen_at >= since))).scalars().all()
    if rows:
        remote = await match_neighbors(session, list(rows))
        for r in rows:
            rd = remote.get(r.id, {}).get("remote_device_id")
            rdev = uuid.UUID(str(rd)) if rd else None
            a = ports.named(r.device_id, r.local_port)
            b = ports.endpoint_for_remote(rdev, r.remote_port)
            if a is not None and b is not None and a.device_id != b.device_id:
                out.append(Finding(a.id, b.id, r.protocol, True, {
                    "kind": r.protocol, "source": "snmp", "local_port": r.local_port,
                    "remote_sysname": r.remote_sysname, "remote_port": r.remote_port}))

    # LibreNMS 的 links（LLDP / CDP / EDP…）
    links = (await session.execute(select(LibreNMSLink).where(LibreNMSLink.active.is_(True)))).scalars().all()
    if links:
        devmap: dict[tuple[uuid.UUID, int], uuid.UUID] = {
            (i, legacy): dev for i, legacy, dev in (await session.execute(
                select(LibreNMSDevice.instance_id, LibreNMSDevice.legacy_device_id, LibreNMSDevice.jt_ipam_device_id)
                .where(LibreNMSDevice.jt_ipam_device_id.is_not(None)))).all()}
        by_name = await _devices_by_short_name(session, {lk.remote_hostname for lk in links if lk.remote_hostname})
        for lk in links:
            ldev = devmap.get((lk.instance_id, lk.local_device_id)) if lk.local_device_id else None
            rdev = devmap.get((lk.instance_id, lk.remote_device_id)) if lk.remote_device_id else None
            if rdev is None and lk.remote_hostname:
                rdev = by_name.get(_short(lk.remote_hostname))
            a = ports.named(ldev, lk.local_port_name)
            b = ports.endpoint_for_remote(rdev, lk.remote_port)
            if a is not None and b is not None and a.device_id != b.device_id:
                kind = (lk.protocol or "lldp").lower()
                kind = kind if kind in ("lldp", "cdp") else "lldp"
                out.append(Finding(a.id, b.id, kind, True, {
                    "kind": kind, "source": "librenms", "local_port": lk.local_port_name,
                    "remote_sysname": lk.remote_hostname, "remote_port": lk.remote_port}))

    # MikroTik /ip/neighbor
    from app.models.mikrotik import MikroTikNeighbor, MikroTikRouter
    from app.services.mikrotik import match_neighbor_devices
    mrows = (await session.execute(
        select(MikroTikNeighbor, MikroTikRouter.device_id).join(MikroTikRouter, MikroTikRouter.id == MikroTikNeighbor.router_id)
        .where(MikroTikNeighbor.last_seen_at >= since, MikroTikRouter.device_id.is_not(None)))).all()
    if mrows:
        mremote = await match_neighbor_devices(session, [n for n, _ in mrows])
        for n, local_dev in mrows:
            rd = mremote.get(n.id, {}).get("device_id")
            rdev = uuid.UUID(str(rd)) if rd else None
            a = ports.named(local_dev, (n.interface or "").split(",")[0])
            b = ports.endpoint_for_remote(rdev, n.remote_interface)
            if a is not None and b is not None and a.device_id != b.device_id:
                by = (n.discovered_by or "").lower()
                kind = "lldp" if "lldp" in by else "cdp" if "cdp" in by else "mndp"
                out.append(Finding(a.id, b.id, kind, True, {
                    "kind": kind, "source": "mikrotik", "local_port": n.interface,
                    "remote_sysname": n.identity, "remote_port": n.remote_interface}))

    # 同一對埠被兩端各宣告一次 → 合併；同一個埠被指到兩個不同地方 → 都降成建議
    merged: dict[tuple[uuid.UUID, uuid.UUID], Finding] = {}
    for f in out:
        merged.setdefault(_pair(f.a_port, f.b_port), f)  # type: ignore[arg-type]
    claims: dict[uuid.UUID, set[tuple[uuid.UUID, uuid.UUID]]] = {}
    for key in merged:
        for pid in key:
            claims.setdefault(pid, set()).add(key)
    for key, f in merged.items():
        if any(len(claims[pid]) > 1 for pid in key):
            f.certain = False
            f.reason = "conflicting_evidence"
    return list(merged.values())


def _short(name: str) -> str:
    return (name or "").split(".")[0].strip().lower()


async def _devices_by_short_name(session: AsyncSession, names: set[str]) -> dict[str, uuid.UUID]:
    """系統名稱（去掉網域）→ 裝置；同名好幾台就不算。"""
    wanted = {_short(n) for n in names if n}
    wanted.discard("")
    if not wanted:
        return {}
    seen: dict[str, list[uuid.UUID]] = {}
    for did, dname in (await session.execute(select(Device.id, Device.name).where(
            in_values(func.lower(Device.name), wanted, type_=Device.name.type)))).all():
        seen.setdefault(str(dname).strip().lower(), []).append(did)
    return {n: ids[0] for n, ids in seen.items() if len(ids) == 1}


async def _fdb_findings(session: AsyncSession, ports: _Ports, now: datetime,
                        neighbor_ports: set[uuid.UUID]) -> list[Finding]:
    cutoff = now - timedelta(hours=FDB_CURRENT_HOURS)
    sw_expr = func.coalesce(FDBEntry.switch_device_id, LibreNMSDevice.jt_ipam_device_id)
    rows = (await session.execute(
        select(sw_expr, FDBEntry.port_name, FDBEntry.mac)
        .select_from(FDBEntry).outerjoin(LibreNMSDevice, LibreNMSDevice.id == FDBEntry.device_id)
        .where(FDBEntry.last_seen_at >= cutoff, sw_expr.is_not(None), FDBEntry.port_name.is_not(None)))).all()
    if not rows:
        return []
    by_port: dict[tuple[uuid.UUID, str], set[str]] = {}
    mac_ports: dict[tuple[uuid.UUID, str], set[str]] = {}
    for sw, pname, mac in rows:
        h = normalize_mac(mac)
        if len(h) != 12:
            continue
        by_port.setdefault((sw, pname), set()).add(h)
        mac_ports.setdefault((sw, h), set()).add(pname)
    switches = {sw for sw, _ in by_port}

    # 每個 MAC 是誰的：網卡埠、IP 記錄（有所屬裝置的）
    ip_owner: dict[str, set[uuid.UUID]] = {}
    all_macs = {h for macs in by_port.values() for h in macs}
    for mac, dev in (await session.execute(select(IPAddress.mac, IPAddress.device_id).where(
            IPAddress.mac.is_not(None), IPAddress.device_id.is_not(None)))).all():
        h = normalize_mac(mac)
        if h in all_macs:
            ip_owner.setdefault(h, set()).add(dev)

    def owners(h: str) -> set[uuid.UUID]:
        return {p.device_id for p in ports.by_mac.get(h, [])} | ip_owner.get(h, set())

    # 虛擬化：VM 的 MAC → 主機；VM 對應成的裝置 → 主機（virtual_machines.node 比對裝置名稱）
    vm_mac_host, vm_dev_host = await _vm_hosts(session)

    out: list[Finding] = []
    for (sw, pname), macs in by_port.items():
        sw_port = ports.named(sw, pname)
        if sw_port is None or sw_port.id in neighbor_ports or _is_switch_logical(sw_port):
            continue
        if any(len(mac_ports.get((sw, h), ())) > 1 for h in macs):
            continue                              # 同一個 MAC 在這台交換器好幾個埠：上行或搬動中
        ev_base = {"switch_port": pname, "macs": sorted(_colon(h) for h in macs)[:20], "mac_count": len(macs)}
        if len(macs) == 1:
            (h,) = macs
            own = owners(h) - {sw}
            if len(own) != 1:
                continue
            (dev,) = own
            if dev in switches:
                continue                          # 另一台交換器自己的 MAC：交給 fdb_uplink
            nic = ports.nic_for(dev, h)
            if nic is False:
                continue                          # 同一個 MAC 好幾個實體埠（bond）
            if nic is None and dev not in ip_owner.get(h, set()):
                continue
            out.append(Finding(sw_port.id, nic.id if isinstance(nic, _Port) else None, "fdb_single_mac", True,
                               {"kind": "fdb_single_mac", **ev_base}, b_device=dev, b_mac=h))
            continue
        # 好幾個 MAC：全部是同一台虛擬化主機和它的 VM 才接
        hosts: set[uuid.UUID] = set()
        host_own: set[str] = set()
        ok = True
        for h in macs:
            if h in vm_mac_host:
                hosts.add(vm_mac_host[h])
                continue
            own = owners(h) - {sw}
            if len(own) != 1:
                ok = False
                break
            (dev,) = own
            host = vm_dev_host.get(dev, dev)
            hosts.add(host)
            if host == dev:
                host_own.add(h)
        if not ok or len(hosts) != 1 or not host_own:
            continue
        (host,) = hosts
        if host in switches:
            continue
        nics = {ports.nic_for(host, h) for h in host_own}
        real = [n for n in nics if isinstance(n, _Port)]
        if False in nics or len(real) != 1:
            continue
        out.append(Finding(sw_port.id, real[0].id, "fdb_vm_host", True, {"kind": "fdb_vm_host", **ev_base},
                           b_device=host))

    # 交換器之間只靠 MAC 表推出來的直連：只建議
    from app.services.topology import uplink_pairs_from_fdb
    # 每台交換器自己的 MAC：掃一次 MAC 表就好（以前每台交換器各掃一次全部 MAC，2,000 台 × 4 萬個 MAC）
    own_macs: dict[str, set[str]] = {str(sw): set() for sw in switches}
    for h, plist in ports.by_mac.items():
        for p in plist:
            if p.device_id in switches:
                own_macs[str(p.device_id)].add(h)
    for h, devs in ip_owner.items():
        for d in devs:
            if d in switches:
                own_macs[str(d)].add(h)
    str_by_port = {(str(sw), pname): macs for (sw, pname), macs in by_port.items()}
    sw_ids = {str(s) for s in switches}
    for (a, b), (pa, pb) in uplink_pairs_from_fdb(str_by_port, own_macs, sw_ids, sw_ids, set()).items():
        port_a = ports.named(uuid.UUID(a), pa)
        port_b = ports.named(uuid.UUID(b), pb)
        if port_a is None or port_b is None or port_a.id in neighbor_ports or port_b.id in neighbor_ports:
            continue
        out.append(Finding(port_a.id, port_b.id, "fdb_uplink", False,
                           {"kind": "fdb_uplink", "a_port": pa, "b_port": pb}, reason="uplink_unverified"))
    return out


async def _vm_hosts(session: AsyncSession) -> tuple[dict[str, uuid.UUID], dict[uuid.UUID, uuid.UUID]]:
    from app.models.virt import VirtualMachine, VMInterface
    rows = (await session.execute(select(VirtualMachine.id, VirtualMachine.node, VirtualMachine.device_id)
                                  .where(VirtualMachine.node.is_not(None)))).all()
    if not rows:
        return {}, {}
    hosts = await _devices_by_exact_name(session, {n for _, n, _ in rows if n})
    vm_host = {vid: hosts[str(n).strip().lower()] for vid, n, _ in rows if n and str(n).strip().lower() in hosts}
    dev_host = {d: vm_host[vid] for vid, _, d in rows if d is not None and vid in vm_host and d != vm_host[vid]}
    mac_host: dict[str, uuid.UUID] = {}
    for vid, mac in (await session.execute(select(VMInterface.vm_id, VMInterface.mac).where(
            VMInterface.mac.is_not(None)))).all():
        if vid in vm_host:
            h = normalize_mac(mac)
            if len(h) == 12:
                mac_host[h] = vm_host[vid]
    return mac_host, dev_host


async def _devices_by_exact_name(session: AsyncSession, names: set[str]) -> dict[str, uuid.UUID]:
    wanted = {n.strip().lower() for n in names if n and n.strip()}
    if not wanted:
        return {}
    seen: dict[str, list[uuid.UUID]] = {}
    for did, dname in (await session.execute(select(Device.id, Device.name).where(
            in_values(func.lower(Device.name), wanted, type_=Device.name.type)))).all():
        seen.setdefault(str(dname).strip().lower(), []).append(did)
    return {n: ids[0] for n, ids in seen.items() if len(ids) == 1}


async def collect(session: AsyncSession, now: datetime) -> tuple[list[Finding], _Ports]:
    ports = await _load_ports(session)
    nb = await _neighbor_findings(session, ports, now)
    neighbor_ports = {pid for f in nb if f.certain for pid in (f.a_port, f.b_port) if pid}
    return nb + await _fdb_findings(session, ports, now, neighbor_ports), ports


# ─────────────────── 套用 ───────────────────
@dataclass
class _CableRec:
    id: uuid.UUID
    source: str
    evidence: dict[str, Any] | None
    ends: dict[str, uuid.UUID] = field(default_factory=dict)


async def _connected_cables(session: AsyncSession) -> tuple[dict[uuid.UUID, _CableRec],
                                                            dict[uuid.UUID, list[tuple[uuid.UUID, uuid.UUID | None]]]]:
    """「已接線」的纜線與每個埠上的 (纜線, 另一端)。只取需要的欄位（站台可能有幾萬條線）。"""
    cables: dict[uuid.UUID, _CableRec] = {}
    for cid, src, ev, side, oid in (await session.execute(
            select(Cable.id, Cable.source, Cable.evidence, CableTermination.side, CableTermination.object_id)
            .join(CableTermination, CableTermination.cable_id == Cable.id)
            .where(Cable.status == "connected", CableTermination.object_type == "device_port"))).all():
        rec = cables.get(cid)
        if rec is None:
            rec = cables[cid] = _CableRec(cid, src, ev)
        rec.ends[side] = oid
    by_port: dict[uuid.UUID, list[tuple[uuid.UUID, uuid.UUID | None]]] = {}
    for rec in cables.values():
        a, b = rec.ends.get("A"), rec.ends.get("B")
        if a:
            by_port.setdefault(a, []).append((rec.id, b))
        if b:
            by_port.setdefault(b, []).append((rec.id, a))
    return cables, by_port


async def _audit_batches(session: AsyncSession, action: str, rows: list[dict[str, Any]]) -> None:
    """系統的自動異動寫進稽核：每 200 條一筆，列出每一條（大站台第一輪可能一次接上萬條，逐條寫太慢）。"""
    for i in range(0, len(rows), 200):
        chunk = rows[i:i + 200]
        await append_audit(session, actor_user_id=None, actor_ip=None, actor_user_agent=_AUDIT_AGENT,
                           object_type="cable", object_id=str(chunk[0]["id"]) if len(chunk) == 1 else None,
                           action=action, diff={"count": len(chunk), "cables": chunk}, request_id=None)


async def _upsert_suggestion(session: AsyncSession, f: Finding, now: datetime,
                             existing: dict[tuple[uuid.UUID, uuid.UUID], CableSuggestion]) -> None:
    a, b = _pair(f.a_port, f.b_port)  # type: ignore[arg-type]
    s = existing.get((a, b))
    if s is None:
        s = CableSuggestion(a_port_id=a, b_port_id=b, kind=f.kind, reason=f.reason, evidence=f.evidence,
                            status="open", first_seen_at=now, last_seen_at=now)
        session.add(s)
        existing[(a, b)] = s
        return
    if s.status == "open":
        s.kind, s.reason, s.evidence, s.last_seen_at = f.kind, f.reason, f.evidence, now


def _chunks(seq: list[Any], n: int = 1000) -> list[list[Any]]:
    return [seq[i:i + n] for i in range(0, len(seq), n)]


async def run(session: AsyncSession, *, now: datetime | None = None) -> dict[str, Any]:
    """跑一輪推斷並寫入。呼叫端負責 commit。回傳各項筆數。

    寫入整批做：大站台第一輪可能一次接上萬條（實測 4 萬台伺服器：逐筆寫要 8 分多鐘，超過同步的時限）。"""
    from sqlalchemy import insert, update
    now = now or datetime.now(UTC)
    findings, ports = await collect(session, now)
    cables, by_port = await _connected_cables(session)
    sugg = {(s.a_port_id, s.b_port_id): s for s in (await session.execute(select(CableSuggestion))).scalars().all()}
    dismissed = {k for k, s in sugg.items() if s.status == "dismissed"}
    st = {"created": 0, "refreshed": 0, "retired": 0, "conflicts": 0, "suggested": 0, "ports_created": 0,
          "confirmed_manual": 0}
    touched: set[uuid.UUID] = set()
    refresh_ids: list[uuid.UUID] = []
    evidence_changed: dict[uuid.UUID, dict[str, Any]] = {}
    retired: dict[uuid.UUID, str] = {}
    new_ports: list[dict[str, Any]] = []
    new_cables: list[dict[str, Any]] = []
    new_terms: list[dict[str, Any]] = []

    def retire(cid: uuid.UUID, reason: str) -> None:
        rec = cables.pop(cid, None)
        if rec is None:
            return
        retired[cid] = reason
        for pid in rec.ends.values():
            by_port[pid] = [(c, o) for c, o in by_port.get(pid, []) if c != cid]

    for f in [x for x in findings if x.certain]:
        b_port = f.b_port
        if b_port is None and f.b_device is not None and f.b_mac:
            hit = ports.nic_for(f.b_device, f.b_mac)        # 先前建過的自動網卡埠就沿用
            b_port = hit.id if isinstance(hit, _Port) else None
        if b_port is not None and _pair(f.a_port, b_port) in dismissed:
            continue
        a_cables = by_port.get(f.a_port, [])
        b_cables = by_port.get(b_port, []) if b_port else []
        same = [c for c, other in a_cables if b_port is not None and other == b_port]
        if same:
            for cid in same:
                rec = cables[cid]
                if rec.source == "auto":
                    refresh_ids.append(cid)
                    if rec.evidence != f.evidence:
                        evidence_changed[cid] = f.evidence
                    touched.add(cid)
                    st["refreshed"] += 1
                else:
                    st["confirmed_manual"] += 1
            continue
        if any(cables[c].source == "manual" for c, _ in a_cables + b_cables):
            if b_port is not None:
                await _upsert_suggestion(session, Finding(f.a_port, b_port, f.kind, False, f.evidence,
                                                          reason="conflict_manual"), now, sugg)
            st["conflicts"] += 1
            continue
        for cid, _ in list(a_cables) + list(b_cables):
            if cid in cables and cables[cid].source == "auto" and cid not in touched:
                retire(cid, "superseded")
        if b_port is None:
            b_port = uuid.uuid4()
            mac = _colon(f.b_mac or "")
            new_ports.append({"id": b_port, "device_id": f.b_device, "name": f"NIC {mac}", "type": "network",
                              "mac_address": mac, "source_origin": AUTO_PORT_ORIGIN,
                              "description": "Created by cable inference: this device's IP record has this MAC"})
            ports.add(_Port(b_port, f.b_device, f"NIC {mac}", "network", mac, AUTO_PORT_ORIGIN))  # type: ignore[arg-type]
            st["ports_created"] += 1
        cid = uuid.uuid4()
        new_cables.append({"id": cid, "status": "connected", "source": "auto", "evidence": f.evidence,
                           "verified_at": now})
        new_terms += [{"id": uuid.uuid4(), "cable_id": cid, "side": "A", "object_type": "device_port",
                       "object_id": f.a_port},
                      {"id": uuid.uuid4(), "cable_id": cid, "side": "B", "object_type": "device_port",
                       "object_id": b_port}]
        cables[cid] = _CableRec(cid, "auto", f.evidence, {"A": f.a_port, "B": b_port})
        by_port.setdefault(f.a_port, []).append((cid, b_port))
        by_port.setdefault(b_port, []).append((cid, f.a_port))
        touched.add(cid)
        st["created"] += 1

    for f in [x for x in findings if not x.certain and x.b_port is not None]:
        key = _pair(f.a_port, f.b_port)  # type: ignore[arg-type]
        if key in dismissed or any(other == f.b_port for _, other in by_port.get(f.a_port, [])):
            continue
        await _upsert_suggestion(session, f, now, sugg)
        st["suggested"] += 1

    # 證據消失太久的自動線（這一輪沒被確認過的）
    stale_before = now - timedelta(days=STALE_DAYS)
    for (cid,) in (await session.execute(select(Cable.id).where(
            Cable.source == "auto", Cable.status == "connected", Cable.verified_at < stale_before))).all():
        if cid not in touched:
            retire(cid, "evidence_gone")
    st["retired"] = len(retired)

    # ── 整批寫入 ──
    for chunk in _chunks(new_ports):
        await session.execute(insert(DevicePort), chunk)
    for chunk in _chunks(new_cables):
        await session.execute(insert(Cable), chunk)
    for chunk in _chunks(new_terms):
        await session.execute(insert(CableTermination), chunk)
    for chunk in _chunks(refresh_ids):
        await session.execute(update(Cable).where(in_values(Cable.id, chunk)).values(verified_at=now)
                              .execution_options(synchronize_session=False))
    for cid, ev in evidence_changed.items():
        await session.execute(update(Cable).where(Cable.id == cid).values(evidence=ev)
                              .execution_options(synchronize_session=False))
    retired_rows = []
    if retired:
        ev_of: dict[Any, Any] = {cid: ev for cid, ev in (await session.execute(select(Cable.id, Cable.evidence).where(
            in_values(Cable.id, list(retired))))).all()}
        retired_rows = [{"id": str(cid), "reason": reason, "evidence": ev_of.get(cid)} for cid, reason in retired.items()]
        for chunk in _chunks(list(retired)):
            await session.execute(delete(CableTermination).where(in_values(CableTermination.cable_id, chunk)))
            await session.execute(delete(Cable).where(in_values(Cable.id, chunk))
                                  .execution_options(synchronize_session=False))
    await _audit_batches(session, "auto_create", [
        {"id": str(c["id"]), "a_port_id": str(a["object_id"]), "b_port_id": str(b["object_id"]),
         "kind": (c["evidence"] or {}).get("kind")}
        for c, a, b in zip(new_cables, new_terms[0::2], new_terms[1::2], strict=True)])
    await _audit_batches(session, "auto_retire", retired_rows)
    await session.flush()

    # 這一輪沒再出現的建議收掉（已忽略的留著）
    await session.execute(delete(CableSuggestion).where(CableSuggestion.status == "open",
                                                        CableSuggestion.last_seen_at < now))
    # 自動建的網卡埠：上面已經沒有線就收掉
    used = select(CableTermination.object_id).where(CableTermination.object_type == "device_port")
    orphans = (await session.execute(select(DevicePort.id).where(
        DevicePort.source_origin == AUTO_PORT_ORIGIN, DevicePort.id.not_in(used)))).scalars().all()
    for chunk in _chunks(list(orphans)):
        await session.execute(delete(DevicePort).where(in_values(DevicePort.id, chunk)))
    st["ports_removed"] = len(orphans)
    await _save_summary(session, now, st)
    await session.flush()
    # 剛才繞過 ORM 改了纜線與連接埠：同一個 session 裡已載入的這幾類物件要重讀（只動這幾類；
    # 全部 expire 會讓呼叫端手上的其他物件在非同步環境下觸發隱性載入）
    for obj in list(session.identity_map.values()):
        if isinstance(obj, (Cable, CableTermination, DevicePort)):
            session.expire(obj)
    return st


async def run_if_enabled(session: AsyncSession, *, now: datetime | None = None) -> dict[str, Any] | None:
    if not (await get_config(session))["enabled"]:
        return None
    return await run(session, now=now)


async def dismiss_cable(session: AsyncSession, cable_id: uuid.UUID, *, user_id: uuid.UUID | None) -> None:
    """人刪掉一條自動線：記住這對埠，之後不再自動建回來。呼叫端負責稽核與 commit。"""
    c = await session.get(Cable, cable_id)
    if c is None:
        return
    ends = (await session.execute(select(CableTermination.object_id).where(
        CableTermination.cable_id == cable_id, CableTermination.object_type == "device_port"))).scalars().all()
    if c.source == "auto" and len(ends) == 2:
        a, b = _pair(ends[0], ends[1])
        s = (await session.execute(select(CableSuggestion).where(
            CableSuggestion.a_port_id == a, CableSuggestion.b_port_id == b))).scalar_one_or_none()
        if s is None:
            s = CableSuggestion(a_port_id=a, b_port_id=b, kind=(c.evidence or {}).get("kind") or "auto",
                                evidence=c.evidence, status="dismissed")
            session.add(s)
        s.status, s.dismissed_by, s.dismissed_at = "dismissed", user_id, datetime.now(UTC)
    await session.execute(delete(CableTermination).where(CableTermination.cable_id == cable_id))
    await session.delete(c)
    await session.flush()
