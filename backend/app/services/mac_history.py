"""MAC 歷程：以一個 MAC 為中心，串起它的所有故事（2026-10-01）。

使用者：「我有一個 MAC，但我不確定它用過哪些 IP，可以從哪裡串起來？」以前只能從 IP 查。
資料其實都在，只是散在各處，這裡全部以 MAC 為中心收在一起：

- IP 異動記錄（`field='mac'` 的每一筆：哪天拿到哪個 IP、哪天被誰取代）
- ARP 觀測（各來源最後出現它在哪個 IP）
- 交換器 MAC 表（FDB：它出現在哪台交換器的哪個埠、哪個 VLAN；LibreNMS 與 MikroTik 兩種列）
- DHCP 固定分配、裝置的連接埠（這是哪台裝置自己的網卡）、虛擬機網卡

**隨機（私人）MAC**：新版 iOS、Android、Windows、macOS 連 Wi‑Fi 預設用，第一個位元組的
本地管理位元是 1。它大多每個 Wi‑Fi 網路固定一個、有些設定會定期輪替，所以一個隨機 MAC 只講得出
「這段時間、這個網路」的故事。同一個 IP、主機名稱沒變、換成另一個 MAC 且其中有隨機位址時，
列進 `related`（可能是同一台換了位址）—— 只是線索，不合併。

**可見範圍**：IP 類的資料依子網路可見性過濾；交換器埠與裝置連接埠依裝置可見性；DHCP 固定分配與
虛擬機是全域資料，只有全域讀取的帳號看得到（`restricted` 標示有東西因此沒列出來）。

**規模**：每個來源都以 MAC 精確比對（有索引，migration 0172），每段都有上限並回報總數。
"""
from __future__ import annotations

import re
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, literal_column, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

_NON_HEX = re.compile(r"[^0-9a-f]")
_ALLOWED = re.compile(r"^[0-9a-fA-F:.\-\s]+$")

IPS_LIMIT = 500
EVENTS_LIMIT = 300
#: 用來算每個 IP 起訖時間的異動記錄上限（一個 MAC 正常只有幾筆；閘道這類被代理 ARP 的位址可能很多）
EVENTS_SCAN_LIMIT = 5000
#: 主機名稱在這段時間內沒變，才把「換成另一個 MAC」視為可能是同一台
SAME_HOST_WINDOW = timedelta(hours=24)


def normalize_mac(value: str | None) -> str | None:
    """各種寫法（`aa:bb…`、`AA-BB…`、`aabb.ccdd.eeff`、`aabbccddeeff`）→ `aa:bb:cc:dd:ee:ff`；不是 MAC 回 None。"""
    raw = (value or "").strip()
    if not raw or not _ALLOWED.match(raw):
        return None
    hex12 = _NON_HEX.sub("", raw.lower())
    if len(hex12) != 12:
        return None
    return ":".join(hex12[i:i + 2] for i in range(0, 12, 2))


def is_random_mac(mac: str | None) -> bool:
    """本地管理位址（隨機／私人 MAC、虛擬機、容器）：不是廠商燒錄的硬體位址。"""
    n = normalize_mac(mac)
    return bool(n) and bool(int(n[:2], 16) & 0x02)  # type: ignore[index]


def _hex_of(col: Any) -> Any:
    """欄位裡的 MAC 去掉分隔符號、轉小寫（與 migration 0172 的運算式索引同一個寫法，常數要內嵌才用得到索引）。"""
    return func.regexp_replace(func.lower(col), literal_column("'[^0-9a-f]'"),
                               literal_column("''"), literal_column("'g'"))


def _iso(v: datetime | None) -> str | None:
    return v.isoformat() if v else None


async def _global_read(session: AsyncSession, user: Any) -> bool:
    from app.mcp.tools import has_global_read
    return await has_global_read(session, user)


async def switch_ports_for_mac(session: AsyncSession, mac: str, *, dev_ok: Any,
                               limit: int = 200) -> list[dict[str, Any]]:
    """交換器 MAC 表（FDB）上這個 MAC 出現在哪台交換器的哪個埠（MAC 歷程與調查共用）。

    fdb_entries 有兩種列（0170）：LibreNMS 的 `device_id` 指向 LibreNMS 裝置（再經它的 jt_ipam_device_id
    對到 jt-ipam 裝置）；MikroTik 的直接記 `switch_device_id`（jt-ipam 裝置）。`dev_ok(jt 裝置 id)` 決定
    看不看得到那台交換器（依裝置可見性；沒對到 jt-ipam 裝置的列只有全部可見的帳號看得到）。
    `mac` 要先正規化成 `aa:bb:cc:dd:ee:ff`（走 mac 欄位的索引）。
    """
    from app.models.device import Device
    from app.models.librenms import FDBEntry, LibreNMSDevice
    from app.models.mikrotik import MikroTikRouter

    sw_dev = Device.__table__.alias("sw_dev")
    fdb_rows = (await session.execute(
        select(FDBEntry.port_name, FDBEntry.vlan_id_num, FDBEntry.source,
               func.min(FDBEntry.first_seen_at), func.max(FDBEntry.last_seen_at),
               LibreNMSDevice.sysname, LibreNMSDevice.hostname, LibreNMSDevice.jt_ipam_device_id,
               FDBEntry.switch_device_id, sw_dev.c.name, MikroTikRouter.name)
        .outerjoin(LibreNMSDevice, LibreNMSDevice.id == FDBEntry.device_id)
        .outerjoin(sw_dev, sw_dev.c.id == FDBEntry.switch_device_id)
        .outerjoin(MikroTikRouter, MikroTikRouter.id == FDBEntry.mikrotik_router_id)
        .where(FDBEntry.mac == mac)
        .group_by(FDBEntry.port_name, FDBEntry.vlan_id_num, FDBEntry.source, LibreNMSDevice.sysname,
                  LibreNMSDevice.hostname, LibreNMSDevice.jt_ipam_device_id, FDBEntry.switch_device_id,
                  sw_dev.c.name, MikroTikRouter.name)
        .order_by(func.max(FDBEntry.last_seen_at).desc()).limit(limit))).all()
    out: list[dict[str, Any]] = []
    for port, vlan, source, first, last, sysname, lhost, ln_dev, ros_dev, ros_name, router_name in fdb_rows:
        jt_dev = ln_dev or ros_dev
        if not dev_ok(jt_dev):
            continue
        out.append({"switch": sysname or lhost or ros_name or router_name,
                    "switch_device_id": str(jt_dev) if jt_dev else None,
                    "port": port, "vlan": vlan, "source": source,
                    "first_seen": _iso(first), "last_seen": _iso(last)})
    return out


async def device_ports_for_mac(session: AsyncSession, mac: str, *, dev_ok: Any,
                               limit: int = 50) -> list[dict[str, Any]]:
    """這個 MAC 是哪台裝置自己的網卡（裝置連接埠的 MAC；MAC 歷程與 trace_mac 共用）。

    連接埠的 MAC 是各來源原樣寫入的字串（`AA-BB…`、`aabb…` 都有），用去掉分隔符號的比對（migration 0172 的索引）。
    `dev_ok(裝置 id)` 決定看不看得到那台裝置。`mac` 要先正規化。"""
    from app.models.device import Device
    from app.models.physical import DevicePort
    hex12 = mac.replace(":", "")
    return [
        {"device_id": str(did), "device_name": dname, "port": pname}
        for did, dname, pname in (await session.execute(
            select(DevicePort.device_id, Device.name, DevicePort.name)
            .join(Device, Device.id == DevicePort.device_id)
            .where(_hex_of(DevicePort.mac_address) == hex12).limit(limit))).all()
        if dev_ok(did)
    ]


async def dhcp_reservations_for_mac(session: AsyncSession, mac: str, *, limit: int = 50) -> list[dict[str, Any]]:
    """這個 MAC 的 DHCP 固定分配（全域資料：呼叫端只給全域讀取的帳號）。`mac` 要先正規化。"""
    from app.models.dhcp import DHCPReservation
    hex12 = mac.replace(":", "")
    return [
        {"ip": ip, "hostname": hn, "source_type": st, "source_name": sn, "description": desc}
        for ip, hn, st, sn, desc in (await session.execute(
            select(DHCPReservation.ip, DHCPReservation.hostname, DHCPReservation.source_type,
                   DHCPReservation.source_name, DHCPReservation.description)
            .where(_hex_of(DHCPReservation.mac) == hex12).limit(limit))).all()
    ]


async def mac_history(session: AsyncSession, *, user: Any, mac: str,
                      ips_limit: int = IPS_LIMIT, events_limit: int = EVENTS_LIMIT) -> dict[str, Any]:
    """一個 MAC 的完整歷程。不是 MAC → ValueError("mac_invalid")。"""
    from app.models.address import IPAddress
    from app.models.device import Device
    from app.models.ip_change_log import IPChangeLog
    from app.models.librenms import ARPEntry
    from app.models.subnet import Subnet
    from app.models.virt import VirtCluster, VirtualMachine, VMInterface
    from app.services.anomaly import liveness_lookup
    from app.services.oui import vendor_for_mac
    from app.services.permission import visible_ids

    m = normalize_mac(mac)
    if m is None:
        raise ValueError("mac_invalid")
    hex12 = m.replace(":", "")
    vis_sub = await visible_ids(session, user=user, object_type="subnet")
    vis_dev = await visible_ids(session, user=user, object_type="device")
    full = await _global_read(session, user)

    def sub_ok(sid: Any) -> bool:
        return vis_sub is None or (sid is not None and sid in vis_sub)

    def dev_ok(did: Any) -> bool:
        return vis_dev is None or (did is not None and did in vis_dev)

    # ── 目前用著這個 MAC 的 IP ──
    cur_rows = (await session.execute(
        select(IPAddress.id, IPAddress.ip, IPAddress.subnet_id, Subnet.cidr, IPAddress.hostname,
               IPAddress.device_id, IPAddress.mac_source, IPAddress.dhcp_reserved)
        .join(Subnet, Subnet.id == IPAddress.subnet_id)
        .where(IPAddress.mac == m).limit(ips_limit))).all()

    # ── IP 異動記錄：這個 MAC 是舊值或新值的每一筆 ──
    involves = (IPChangeLog.field == "mac") & or_(
        _hex_of(IPChangeLog.old_value) == hex12, _hex_of(IPChangeLog.new_value) == hex12)
    ev_where = [involves]
    if vis_sub is not None:
        from app.core.sqlin import in_values
        ev_where.append(in_values(IPChangeLog.subnet_id, list(vis_sub)))
    events_total = int(await session.scalar(select(func.count()).select_from(IPChangeLog).where(*ev_where)) or 0)
    ev_rows = (await session.execute(
        select(IPChangeLog.id, IPChangeLog.ip_id, IPChangeLog.subnet_id, IPChangeLog.ip_text,
               IPChangeLog.old_value, IPChangeLog.new_value, IPChangeLog.source, IPChangeLog.created_at,
               IPChangeLog.event_type)
        .where(*ev_where).order_by(IPChangeLog.created_at.desc()).limit(EVENTS_SCAN_LIMIT))).all()

    # ── ARP 觀測 ──
    # 每個回報者（LibreNMS 的每台設備、掃描代理、各防火牆）各一列：看得出「只有一台看過」（2026-10-09 回饋）
    arp_rows = (await session.execute(
        select(ARPEntry.ip, ARPEntry.subnet_id, ARPEntry.source, ARPEntry.device_id, ARPEntry.interface,
               func.min(ARPEntry.first_seen_at), func.max(ARPEntry.last_seen_at))
        .where(ARPEntry.mac == m)
        .group_by(ARPEntry.ip, ARPEntry.subnet_id, ARPEntry.source, ARPEntry.device_id, ARPEntry.interface)
        .limit(8 * ips_limit))).all()

    # ── 合併成「用過的 IP」：(位址, 子網路) 一列 ──
    rows: dict[tuple[str, Any], dict[str, Any]] = {}

    def _row(ip_text: str, sid: Any) -> dict[str, Any]:
        key = (ip_text, sid)
        if key not in rows:
            rows[key] = {"ip": ip_text, "subnet_id": sid, "ip_id": None, "first_seen": None, "last_seen": None,
                         "current": False, "evidence": set(), "_last_event": None, "_reporters": {}}
        return rows[key]

    def _span(r: dict[str, Any], first: datetime | None, last: datetime | None) -> None:
        if first and (r["first_seen"] is None or first < r["first_seen"]):
            r["first_seen"] = first
        if last and (r["last_seen"] is None or last > r["last_seen"]):
            r["last_seen"] = last

    for iid, ipv, sid, _cidr, _hn, _dev, _src, _resv in cur_rows:
        r = _row(str(ipv).split("/")[0], sid)
        r["ip_id"] = iid
        r["current"] = True
        r["evidence"].add("ipam")

    events: list[dict[str, Any]] = []
    for eid, iid, sid, ip_text, old, new, source, at, etype in ev_rows:
        o, n = normalize_mac(old), normalize_mac(new)
        assigned = n == m
        r = _row(str(ip_text), sid)
        r["ip_id"] = r["ip_id"] or iid
        r["evidence"].add("change_log")
        if assigned:
            _span(r, at, None)
        else:
            # 被別的 MAC 取代：在這個 IP 上最後一次出現
            _span(r, None, at)
        if r["_last_event"] is None:            # ev_rows 由新到舊，第一筆就是這個 IP 最近的一次
            r["_last_event"] = "assigned" if assigned else "released"
        if len(events) < events_limit:
            other = o if assigned else n
            events.append({"id": str(eid), "at": at, "kind": "assigned" if assigned else "released",
                           "ip": str(ip_text), "ip_id": str(iid) if iid else None, "subnet_id": sid,
                           "other_mac": other, "other_random": is_random_mac(other) if other else False,
                           "source": source, "event_type": etype})

    # ARP 沒有子網路的列（LibreNMS）：對到唯一一筆同位址的列；對不到就看 IP 記錄
    def _reporter(r: dict[str, Any], source: str, device_id: Any, interface: str | None,
                  last: datetime | None) -> None:
        k = f"{source}:{device_id}" if device_id else source
        cur = r["_reporters"].setdefault(k, {"source": source, "device_id": device_id,
                                             "interface": interface, "last": last})
        if last and (cur["last"] is None or last > cur["last"]):
            cur["last"] = last

    unresolved: list[tuple[str, str, Any, str | None, datetime | None, datetime | None]] = []
    for ipv, sid, source, device_id, interface, first, last in arp_rows:
        ip_text = str(ipv).split("/")[0]
        if sid is None:
            same = [k for k in rows if k[0] == ip_text]
            if len(same) == 1:
                sid = same[0][1]
            else:
                unresolved.append((ip_text, str(source), device_id, interface, first, last))
                continue
        r = _row(ip_text, sid)
        r["evidence"].add(f"arp:{source}")
        _reporter(r, str(source), device_id, interface, last)
        _span(r, first, last)
    if unresolved:
        from app.core.sqlin import in_values
        owners: dict[str, list[tuple[Any, Any]]] = defaultdict(list)
        for iid, ipv, sid in (await session.execute(
                select(IPAddress.id, IPAddress.ip, IPAddress.subnet_id)
                .where(in_values(IPAddress.ip, list({u[0] for u in unresolved}))))).all():
            owners[str(ipv).split("/")[0]].append((iid, sid))
        for ip_text, source, device_id, interface, first, last in unresolved:
            own = owners.get(ip_text) or []
            sid = own[0][1] if len(own) == 1 else None      # 重疊網段對到好幾筆：不猜是哪一個子網路
            r = _row(ip_text, sid)
            if len(own) == 1:
                r["ip_id"] = r["ip_id"] or own[0][0]
            r["evidence"].add(f"arp:{source}")
            _reporter(r, source, device_id, interface, last)
            _span(r, first, last)

    # 可見範圍：看不到的子網路整列拿掉；說不出子網路的（IPAM 沒記錄）只有全域讀取看得到
    visible_rows = [r for r in rows.values() if (r["subnet_id"] is None and full) or
                    (r["subnet_id"] is not None and sub_ok(r["subnet_id"]))]
    # 補 IP 記錄的資料（主機名稱、子網路）與上線依據
    ids = {r["ip_id"] for r in visible_rows if r["ip_id"]}
    rec: dict[Any, tuple[Any, ...]] = {}
    if ids:
        from app.core.sqlin import in_values
        for iid, _ipv, sid, cidr, hn, mac_now in (await session.execute(
                select(IPAddress.id, IPAddress.ip, IPAddress.subnet_id, Subnet.cidr, IPAddress.hostname,
                       IPAddress.mac).join(Subnet, Subnet.id == IPAddress.subnet_id)
                .where(in_values(IPAddress.id, list(ids))))).all():
            rec[iid] = (sid, str(cidr), hn, str(mac_now) if mac_now else None)
    cidr_of: dict[Any, str] = {}
    missing_sids = {r["subnet_id"] for r in visible_rows if r["subnet_id"]} - {v[0] for v in rec.values()}
    if missing_sids:
        from app.core.sqlin import in_values
        cidr_of = {sid: str(c) for sid, c in (await session.execute(
            select(Subnet.id, Subnet.cidr).where(in_values(Subnet.id, list(missing_sids))))).all()}
    by_id, by_text = await liveness_lookup(session, ids, {r["ip"] for r in visible_rows if not r["ip_id"]})
    from app.services.anomaly import reporter_names
    rep_names = await reporter_names(session, {rp["device_id"] for r in visible_rows
                                               for rp in r["_reporters"].values()})
    out_ips = []
    for r in visible_rows:
        info = rec.get(r["ip_id"])
        if info and not r["current"] and info[3] == m:
            r["current"] = True
        out_ips.append({
            "ip": r["ip"], "ip_id": str(r["ip_id"]) if r["ip_id"] else None,
            "subnet_id": str(r["subnet_id"]) if r["subnet_id"] else None,
            "subnet_cidr": info[1] if info else cidr_of.get(r["subnet_id"]),
            "hostname": info[2] if info else None,
            "deleted": bool(r["ip_id"]) and info is None,
            "current": r["current"],
            "first_seen": _iso(r["first_seen"]),
            # 目前還用著的，「現在是否在線上」看上線燈；這裡是各來源記到的最後時間
            "last_seen": _iso(r["last_seen"]),
            "evidence": sorted(r["evidence"]),
            # ARP：幾個回報者、各是哪台設備的哪個介面（只有一台看過一次的，多半是讀壞或過期快取）
            "arp_reporter_count": len(r["_reporters"]),
            "arp_reporters": [
                {"source": rp["source"],
                 "device": rep_names.get(str(rp["device_id"])) if rp["device_id"] else None,
                 "interface": rp["interface"], "last_seen": _iso(rp["last"])}
                for rp in sorted(r["_reporters"].values(),
                                 key=lambda x: x["last"] or datetime.min.replace(tzinfo=UTC), reverse=True)
            ][:20],
            "live": by_id.get(r["ip_id"]) if r["ip_id"] else by_text.get(r["ip"]),
        })
    # 目前用著的在前，其餘由近到遠
    out_ips.sort(key=lambda x: x["last_seen"] or x["first_seen"] or "", reverse=True)
    out_ips.sort(key=lambda x: not x["current"])
    ips_total = len(out_ips)
    out_ips = out_ips[:ips_limit]

    current = [{"ip": str(ipv).split("/")[0], "ip_id": str(iid), "subnet_cidr": str(cidr), "hostname": hn,
                "device_id": str(dev) if dev else None, "mac_source": src, "dhcp_reserved": bool(resv)}
               for iid, ipv, sid, cidr, hn, dev, src, resv in cur_rows if sub_ok(sid)]
    if current:
        dev_ids = [uuid.UUID(c["device_id"]) for c in current if c["device_id"]]
        names = {}
        if dev_ids:
            from app.core.sqlin import in_values
            names = {str(i): n for i, n in (await session.execute(
                select(Device.id, Device.name).where(in_values(Device.id, dev_ids)))).all()}
        for c in current:
            c["device_name"] = names.get(c["device_id"]) if c["device_id"] and dev_ok(uuid.UUID(c["device_id"])) else None

    events_out = [e for e in events if sub_ok(e["subnet_id"])]
    for e in events_out:
        e["at"] = _iso(e["at"])
        e["subnet_id"] = str(e["subnet_id"]) if e["subnet_id"] else None

    # ── 交換器 MAC 表（FDB） ──
    switch_ports = await switch_ports_for_mac(session, m, dev_ok=dev_ok)

    # ── 這是哪台裝置自己的網卡 ──
    device_ports = await device_ports_for_mac(session, m, dev_ok=dev_ok)

    # ── 全域資料：DHCP 固定分配、虛擬機網卡 ──
    dhcp_res: list[dict[str, Any]] = []
    vms: list[dict[str, Any]] = []
    if full:
        dhcp_res = await dhcp_reservations_for_mac(session, m)
        vms = [
            {"vm_id": str(vid), "vm_name": vname, "cluster": cname, "interface": iname, "status": vst,
             "node": node, "vmid": vmid, "platform": ctype}
            for vid, vname, cname, iname, vst, node, vmid, ctype in (await session.execute(
                select(VirtualMachine.id, VirtualMachine.name, VirtCluster.name, VMInterface.name,
                       VirtualMachine.status, VirtualMachine.node, VirtualMachine.legacy_vmid, VirtCluster.type)
                .join(VirtualMachine, VirtualMachine.id == VMInterface.vm_id)
                .join(VirtCluster, VirtCluster.id == VirtualMachine.cluster_id)
                .where(VMInterface.mac == m).limit(50))).all()
        ]

    # ── 可能是同一台（隨機 MAC 輪替）：同一個 IP、主機名稱沒變、換成另一個 MAC，且其中有隨機位址 ──
    related = await _related(session, m, ev_rows, sub_ok)

    return {
        "mac": m,
        "vendor": await vendor_for_mac(session, m),
        "random": is_random_mac(m),
        "restricted": not full,
        "current": current,
        "ips": out_ips, "ips_total": ips_total,
        "events": events_out, "events_total": events_total,
        "switch_ports": switch_ports,
        "device_ports": device_ports,
        "dhcp_reservations": dhcp_res,
        "vms": vms,
        "related": related,
    }


async def _related(session: AsyncSession, m: str, ev_rows: list[Any], sub_ok: Any) -> list[dict[str, Any]]:
    from app.core.sqlin import in_values
    from app.models.ip_change_log import IPChangeLog

    swaps = []
    for _eid, iid, sid, ip_text, old, new, _source, at, _etype in ev_rows:
        o, n = normalize_mac(old), normalize_mac(new)
        other = o if n == m else n
        if not other or not iid or not sub_ok(sid):
            continue
        if not (is_random_mac(m) or is_random_mac(other)):
            continue                     # 兩個都是廠商燒錄的位址：換的是網卡或機器，不是位址輪替
        swaps.append((iid, str(ip_text), at, other))
    if not swaps:
        return []
    host_changes: dict[Any, list[datetime]] = defaultdict(list)
    for iid, at in (await session.execute(
            select(IPChangeLog.ip_id, IPChangeLog.created_at).where(
                in_values(IPChangeLog.ip_id, list({s[0] for s in swaps})),
                IPChangeLog.field == "hostname"))).all():
        host_changes[iid].append(at)
    out: dict[str, dict[str, Any]] = {}
    for iid, ip_text, at, other in swaps:
        if any(abs(h - at) <= SAME_HOST_WINDOW for h in host_changes.get(iid, [])):
            continue                     # 主機名稱也跟著換了：是另一台
        prev = out.get(other)
        if prev is None or at.isoformat() > prev["at"]:
            out[other] = {"mac": other, "ip": ip_text, "ip_id": str(iid), "at": at.isoformat(),
                          "random": is_random_mac(other), "reason": "same_ip_same_hostname"}
    return sorted(out.values(), key=lambda r: r["at"], reverse=True)
