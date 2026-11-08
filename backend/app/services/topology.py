"""網路拓樸圖：以 device + cabling + LibreNMS FDB（Phase 2 已有）拼出 graph。

回傳 Cytoscape.js 可直接吃的格式：
  {
    "nodes": [{"data": {"id": "...", "label": "...", "type": "..."}}, ...],
    "edges": [{"data": {"source": "...", "target": "...", "label": "..."}}, ...]
  }

邊（edges）來源：
1. 物理 Cable → 兩端 termination（同 cable 兩 termination → 一條邊）
2. WirelessLink（A/B device 都存在時）
3. VPNTunnel（A/B device）
4. L3 推導：device ↔ subnet（依 IP／名稱／ARP／LibreNMS 多訊號）
5. L2 推導：FDB → 存取層（機器 ↔ 交換器埠）與交換器骨幹

⚠️ 第 5 項在 v0.5.213 之前只存在於這段說明裡 —— 這個檔案宣稱用 FDB 拼圖，實際上一行
都沒讀。改動這裡時請一併確認說明與程式仍然對得上：說明會被當成事實引用。
LLDP/CDP（`librenms_links`）實機上是空的，沒有資料源，因此不列入。
"""

from __future__ import annotations

import ipaddress as _ipaddr
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sqlin import in_values
from app.models.address import IPAddress
from app.models.advanced import WirelessLink
from app.models.device import Device
from app.models.librenms import ARPEntry, FDBEntry, LibreNMSDevice
from app.models.location import Location, Rack
from app.models.physical import Cable, CableTermination, DevicePort, VPNTunnel
from app.models.subnet import Subnet

#: 一個埠上出現幾個 MAC 就視為上行／trunk（而不是「這些機器都插在這個埠」）。
#: 存取埠通常是 1～2 個（機器本身 + 可能的一台 IP 話機）；上行埠動輒數十上百。
#: 實機上量到的分布很極端：28 個埠裡 12 個只有 1 個 MAC，13 個超過 4 個、最多 143 個
#: —— 中間幾乎沒有東西，所以這個門檻不敏感，訂在 4 很安全。
UPLINK_MAC_THRESHOLD = 4

#: 每條邊的「這是誰說的」。對應證據契約（`services/evidence.py`）的層級，另加一個
#: `inferred`：契約講的是**來源**，而「名稱剛好長得像某個 IP」不是來源、是猜測，
#: 必須跟其他三種分得出來。把宣告的與推導的畫成同一種線，等於宣稱我們對兩者
#: 有一樣的把握。
EV_ASSERTED = "asserted"     # 人為登記：實體佈線、無線連線、IP↔裝置的連結
EV_MONITORED = "monitored"   # 第三方監控說的：LibreNMS、虛擬化平台
EV_LEARNED = "learned"       # 被動學到：FDB、ARP
EV_INFERRED = "inferred"     # 我們自己推的（名稱比對）

#: L3 邊的 via → 證據層級。一條邊可能有多個 via，取最有把握的那個。
_VIA_EVIDENCE = {
    "ip": EV_ASSERTED,
    "librenms": EV_MONITORED,
    "arp": EV_LEARNED,
    "name": EV_INFERRED,
    "fdb": EV_LEARNED,
    "virtualization": EV_MONITORED,
    # MikroTik /ip/neighbor：鄰居自己用 MNDP／CDP／LLDP 宣告的，路由器轉述 —— 與監控平台同一層
    "neighbor": EV_MONITORED,
}
_EV_RANK = {EV_ASSERTED: 3, EV_MONITORED: 2, EV_LEARNED: 1, EV_INFERRED: 0}


def evidence_of(via: str) -> str:
    """由 via（可能是逗號分隔的多個來源）決定證據層級；取最有把握的那個。"""
    best = EV_INFERRED
    for part in str(via or "").split(","):
        tier = _VIA_EVIDENCE.get(part.strip())
        if tier and _EV_RANK[tier] > _EV_RANK[best]:
            best = tier
    return best


def _as_uuids(ids: set[str]) -> set[uuid.UUID]:
    out = set()
    for x in ids:
        try:
            out.add(uuid.UUID(x))
        except ValueError:
            continue
    return out


class _SubnetIndex:
    """位址 → 包含它的最精確子網路。依前綴長度查表（每個位址只查幾次），不逐一比對所有網段：
    大站台十幾萬筆 ARP × 幾千個子網路，逐一比對就是幾億次。"""

    def __init__(self, cand: list[tuple[Any, Any]]) -> None:
        self._by_len: dict[tuple[int, int], dict[int, Any]] = {}
        for sn, net in cand:
            self._by_len.setdefault((net.version, net.prefixlen), {})[int(net.network_address)] = sn
        self._lens = sorted(self._by_len, key=lambda k: -k[1])

    def lookup(self, addr: Any) -> Any:
        bits = 32 if addr.version == 4 else 128
        for ver, plen in self._lens:
            if ver != addr.version:
                continue
            key = (int(addr) >> (bits - plen)) << (bits - plen) if plen else 0
            hit = self._by_len[(ver, plen)].get(key)
            if hit is not None:
                return hit
        return None


# 一次最多畫幾台裝置。兩萬台的圖瀏覽器畫不動、人也看不懂，後端還要算上十幾秒（期間整個服務卡住）；
# 超過就不建圖，回傳裝置數讓畫面請使用者先用子網路篩選（2026-09-29 超大規模測試）
MAX_TOPOLOGY_DEVICES = 2000


def uplink_pairs_from_fdb(
    by_port: dict[tuple[str, str], set[str]],
    own_macs: dict[str, set[str]],
    switch_ids: set[str],
    visible_device_ids: set[str],
    adjacency: set[tuple[str, str]],
) -> dict[tuple[str, str], tuple[str, str]]:
    """兩台交換器直連（骨幹）：A 的埠 P 看得到 B 自己的 MAC、B 的埠 Q 看得到 A 自己的 MAC，
    而且 P 與 Q 背後的機器（扣掉對方交換器自己的 MAC）不相交。回傳 {(a, b): (P, Q)}，a < b。

    **只看真的互相看得到的那幾對**：以前是把每一對交換器都拿出來、每一對再把全部的埠掃一遍
    （交換器數平方 × 埠數）。5,000 台交換器時就是一千多萬對 × 幾十萬個埠 —— 拓樸頁讓後端單核
    跑滿十幾分鐘，整個服務都卡住（2026-09-29 超大規模測試抓到）。現在先從 FDB 反查「誰的埠上
    出現了哪台交換器自己的 MAC」，候選對只剩真的有目擊的；判準與挑選順序跟以前一模一樣
    （同一對有多組埠符合時，取埠在 by_port 裡先出現的那組）。
    """
    owners: dict[str, set[str]] = {}
    for sw, macs in own_macs.items():
        for mac in macs:
            owners.setdefault(mac, set()).add(sw)
    all_own = set(owners)
    # (看的人, 被看到的交換器) → [(埠在 by_port 的順序, 埠, 那個埠背後的 MAC)]
    sees: dict[tuple[str, str], list[tuple[int, str, set[str]]]] = {}
    for order, ((sw, port), macs) in enumerate(by_port.items()):
        for mac in (macs & all_own if len(macs) > len(all_own) else all_own & macs):
            for other in owners[mac]:
                if other != sw:
                    lst = sees.setdefault((sw, other), [])
                    if not lst or lst[-1][0] != order:
                        lst.append((order, port, macs))
    pairs: dict[tuple[str, str], tuple[str, str]] = {}
    for (a, b) in sorted({(min(x, y), max(x, y)) for x, y in sees}):
        if a not in switch_ids or b not in switch_ids or (a, b) in adjacency:
            continue
        if a not in visible_device_ids or b not in visible_device_ids:
            continue
        a_ports, b_ports = sees.get((a, b)), sees.get((b, a))
        if not a_ports or not b_ports:
            continue                       # 單邊看得到對方：無從證實，不畫
        own_a, own_b = own_macs.get(a, set()), own_macs.get(b, set())
        for _o1, pa, ma in a_ports:
            hit = next((pb for _o2, pb, mb in b_ports if not ((ma - own_b) & (mb - own_a))), None)
            if hit is not None:
                pairs[(a, b)] = (pa, hit)
                break
    return pairs



#: 掃描代理判讀出的設備類型（IP 的 device_kind）→ 拓樸的裝置類型。沒有對應的（印表機、攝影機…）
#: 拓樸沒有那一類，維持 other
_KIND_TO_TYPE = {"router": "router", "switch": "switch", "firewall": "firewall", "wireless_ap": "ap",
                 "storage": "storage", "server": "server", "windows": "server", "hypervisor": "server"}

async def build_topology(
    session: AsyncSession,
    *,
    user: Any = None,  # RBAC：限縮成該 user 可見的 device/subnet
    location_id: uuid.UUID | None = None,
    subnet_ids: list[uuid.UUID] | None = None,
    include_wireless: bool = True,
    include_vpn: bool = True,
    include_l3: bool = True,
    include_fdb: bool = True,
    include_vms: bool = False,
    online_only: bool = False,
    max_devices: int | None = None,
    scope_limited: bool = False,
) -> dict[str, Any]:
    """`scope_limited`：只被授權部分物件的帳號（沒有萬用讀取）。2026-10-09 以前這種帳號整張圖都看不到
    （403）；現在只畫看得到的裝置與子網路、兩端都看得到的連線。VPN 通道與虛擬機是全域基礎設施資料
    （REST 上也只給有全域讀取的人），這種帳號一律不畫，回應的 `hidden_layers` 寫明少了哪些。"""
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    hidden_layers: list[str] = []
    vis_sub: set[uuid.UUID] | None = None
    if scope_limited:
        if include_vpn:
            hidden_layers.append("vpn")
        if include_vms:
            hidden_layers.append("vms")
        include_vpn = include_vms = False

    # RBAC：可見的 device / subnet id（None = 全部可見，admin/wildcard）
    vis_dev: set[uuid.UUID] | None = None
    if user is not None and not getattr(user, "is_admin", False):
        from app.services.permission import visible_ids
        vis_dev = await visible_ids(session, user=user, object_type="device")
        if scope_limited:
            vis_sub = await visible_ids(session, user=user, object_type="subnet")

    # 若指定 subnet_ids：只保留「在這些子網路裡有 IP」的裝置，圖才不會被無關裝置塞爆
    subnet_filter = set(subnet_ids) if subnet_ids else None
    allowed_device_ids: set[str] | None = None
    if subnet_filter:
        # 篩選與「全部」一致：用三種訊號決定哪些裝置屬於這些子網路，
        # 否則只靠 IPAddress 連結會漏掉「以 IP 命名」或「ARP 看到」的裝置。
        fsubs = (await session.execute(
            select(Subnet).where(in_values(Subnet.id, subnet_filter))
        )).scalars().all()
        fnets = []
        for sn in fsubs:
            try:
                fnets.append(_ipaddr.ip_network(str(sn.cidr), strict=False))
            except ValueError:
                continue
        allowed_device_ids = set()
        # (ip) 有 IPAddress 連結到這些子網路
        for d in (await session.execute(
            select(IPAddress.device_id).where(
                in_values(IPAddress.subnet_id, subnet_filter),
                IPAddress.device_id.is_not(None),
            )
        )).all():
            if d[0] is not None:
                allowed_device_ids.add(str(d[0]))
        # (name) 裝置名稱即 IP 且落在這些子網路
        for did, dname in (await session.execute(select(Device.id, Device.name))).all():
            try:
                nip = _ipaddr.ip_address((dname or "").strip())
            except ValueError:
                continue
            if any(nip in n for n in fnets):
                allowed_device_ids.add(str(did))
        # (arp) 裝置的 ARP 鄰居 IP 落在這些子網路。⚠️ arp_entries.device_id 是 **LibreNMS 的裝置**：
        # 要經 jt_ipam_device_id 才對得回 jt-ipam 的裝置（以前直接拿來比，從來沒對上過）
        findex = _SubnetIndex([(n, n) for n in fnets])
        for did, ip in (await session.execute(
            select(LibreNMSDevice.jt_ipam_device_id, ARPEntry.ip)
            .join(LibreNMSDevice, LibreNMSDevice.id == ARPEntry.device_id)
            .where(LibreNMSDevice.jt_ipam_device_id.is_not(None))
        )).all():
            try:
                aip = _ipaddr.ip_address(str(ip).split("/")[0])
            except ValueError:
                continue
            if findex.lookup(aip) is not None:
                allowed_device_ids.add(str(did))

    # ── nodes：所有 device（選用依 location / subnet 過濾） ──
    # 裝置群組（堆疊／HA）：子網路篩選選進一台成員，同組其他成員一起帶進來 —— 共用 IP 只連到代表裝置，
    # 不這樣做另一台堆疊成員就不會出現在圖上
    if allowed_device_ids:
        gids = {g for (g,) in (await session.execute(select(Device.group_id).where(
            in_values(Device.id, {uuid.UUID(x) for x in allowed_device_ids}), Device.group_id.is_not(None)))).all()}
        if gids:
            allowed_device_ids |= {str(i) for (i,) in (await session.execute(
                select(Device.id).where(in_values(Device.group_id, gids)))).all()}
    dstmt = select(Device)
    if location_id is not None:
        dstmt = dstmt.where(Device.location_id == location_id)
    if allowed_device_ids is not None:
        dstmt = dstmt.where(in_values(Device.id, {uuid.UUID(x) for x in allowed_device_ids}))
    if vis_dev is not None:
        dstmt = dstmt.where(in_values(Device.id, vis_dev))
    devices = list((await session.execute(dstmt)).scalars().all())
    # 只畫上線：限縮成「至少有一個 IP 的 effective_status = online」的裝置
    if online_only:
        online_ids = {
            str(row[0]) for row in (await session.execute(
                select(IPAddress.device_id).where(
                    IPAddress.device_id.is_not(None),
                    IPAddress.effective_status == "online",
                ).distinct()
            )).all() if row[0]
        }
        devices = [d for d in devices if str(d.id) in online_ids]
    limit = MAX_TOPOLOGY_DEVICES if max_devices is None else max_devices
    if len(devices) > limit:
        return {"nodes": [], "edges": [], "too_large": {"devices": len(devices), "limit": limit}}
    # 批次查每台裝置的主要 IP（給 node 帶上，連線卡片可顯示兩端 IP）
    pip_ids = {d.primary_ip_id for d in devices if d.primary_ip_id}  # type: ignore[attr-defined]
    pip_map: dict[uuid.UUID, str] = {}
    mac_map: dict[uuid.UUID, str] = {}      # 主要 IP 的 MAC：選取裝置後「查看 MAC 歷程」用
    if pip_ids:
        for iid, ipval, mac in (await session.execute(
            select(IPAddress.id, IPAddress.ip, IPAddress.mac).where(in_values(IPAddress.id, pip_ids))
        )).all():
            pip_map[iid] = str(ipval).split("/")[0]
            if mac:
                mac_map[iid] = str(mac)
    # 裝置群組：節點帶群組資訊，前端把同組成員框在一起
    from app.models.device_group import DeviceGroup
    gids = {d.group_id for d in devices if d.group_id}
    groups = {g.id: g for g in (await session.execute(
        select(DeviceGroup).where(in_values(DeviceGroup.id, gids)))).scalars().all()} if gids else {}
    device_objs: dict[str, Device] = {}
    for d in devices:  # type: ignore[assignment]
        device_objs[str(d.id)] = d  # type: ignore[assignment]
        grp = groups.get(d.group_id) if d.group_id else None
        nodes[str(d.id)] = {
            "data": {
                "id": str(d.id),
                "label": d.name,
                "type": d.type,
                "vendor": d.vendor,
                "model": d.model,
                "serial": d.serial,
                "ip": pip_map.get(d.primary_ip_id) if d.primary_ip_id else None,
                "mac": mac_map.get(d.primary_ip_id) if d.primary_ip_id else None,
                "rack_id": str(d.rack_id) if d.rack_id else None,
                "location_id": str(d.location_id) if d.location_id else None,
                "group_id": str(grp.id) if grp else None,
                "group_name": grp.name if grp else None,
                "group_kind": grp.kind if grp else None,
                "group_role": d.group_role if grp else None,
                "group_order": d.group_order if grp else None,
            }
        }

    visible_device_ids = set(nodes.keys())

    # ── 物理纜線 ──
    cables = list((await session.execute(select(Cable))).scalars().all())
    # 端點一次撈回來再分組：以前每條纜線各查一次，兩萬條纜線就是兩萬次查詢（超大規模測試抓到，
    # 拓樸頁因此要四十秒、期間整個後端卡住）
    terms_of: dict[Any, list[CableTermination]] = {}
    if cables:
        for t in (await session.execute(select(CableTermination))).scalars().all():
            terms_of.setdefault(t.cable_id, []).append(t)
    for cable in cables:
        terms = terms_of.get(cable.id, [])
        if len(terms) != 2:
            continue
        a, b = sorted(terms, key=lambda t: t.side)
        # MVP：device-to-device 才畫
        if a.object_type != "device" or b.object_type != "device":
            continue
        sid, tid = str(a.object_id), str(b.object_id)
        if sid not in visible_device_ids or tid not in visible_device_ids:
            continue
        edges.append({
            "data": {
                "id": f"cable:{cable.id}",
                "source": sid, "target": tid,
                "label": cable.label or cable.type or "cable",
                "kind": "cable", "evidence": EV_ASSERTED,
                "type": cable.type,
                "color": cable.color,
                "status": cable.status,
                "source_port": a.port_label,
                "target_port": b.port_label,
            }
        })

    # ── 無線連線 ──
    if include_wireless:
        wlinks = list((await session.execute(select(WirelessLink))).scalars().all())
        for w in wlinks:
            if not (w.a_device_id and w.b_device_id):
                continue
            sid, tid = str(w.a_device_id), str(w.b_device_id)
            if sid not in visible_device_ids or tid not in visible_device_ids:
                continue
            edges.append({
                "data": {
                    "id": f"wireless:{w.id}",
                    "source": sid, "target": tid,
                    "label": w.ssid or w.name,
                    "kind": "wireless", "evidence": EV_ASSERTED,
                    "ssid": w.ssid,
                    "distance_m": w.distance_m,
                }
            })

    # ── VPN 邏輯連線 ──
    if include_vpn:
        tunnels = list((await session.execute(select(VPNTunnel))).scalars().all())

        # 對接的 VPN（兩端都是已知 device）一律要顯示並連起來——即使某端被子網路
        # 過濾掉、或該防火牆的管理 IP 沒掛進該網段。先把缺的端點 device 節點補進來。
        pair_dev_ids = {
            d for t in tunnels if t.a_device_id and t.b_device_id
            for d in (t.a_device_id, t.b_device_id)
        }
        missing = [d for d in pair_dev_ids if str(d) not in nodes]
        # RBAC：只補「可見」的對接端點裝置，非管理員不得藉 VPN 看到無權裝置/子網路
        if vis_dev is not None:
            missing = [d for d in missing if d in vis_dev]
        if missing:
            extra = (await session.execute(select(Device).where(in_values(Device.id, missing)))).scalars().all()
            for d in extra:  # type: ignore[assignment]
                device_objs[str(d.id)] = d  # type: ignore[assignment]
                nodes[str(d.id)] = {"data": {
                    "id": str(d.id), "label": d.name, "type": d.type,
                    "vendor": d.vendor, "model": d.model, "serial": d.serial,
                    "rack_id": str(d.rack_id) if d.rack_id else None,
                    "location_id": str(d.location_id) if d.location_id else None,
                }}
                visible_device_ids.add(str(d.id))

        seen_vpn_pairs: set[frozenset[str]] = set()
        for t in tunnels:
            a_id = str(t.a_device_id) if t.a_device_id else None
            b_id = str(t.b_device_id) if t.b_device_id else None
            a_vis = bool(a_id) and a_id in visible_device_ids
            b_vis = bool(b_id) and b_id in visible_device_ids

            # 1) 兩端都是已知 device 且都可見 → device↔device 邊
            if a_vis and b_vis:
                # 對接的兩端各有一條 tunnel（A→B、B→A），同一對 device 只畫一條邊
                pair = frozenset((a_id, b_id))
                if pair in seen_vpn_pairs:
                    continue
                seen_vpn_pairs.add(pair)  # type: ignore[arg-type]
                # 對接邊上標出「中間經過的 WAN 端點」：a_endpoint ↔ b_endpoint
                # （WireGuard/IPsec/OpenVPN 的對外公網 IP / FQDN）
                wan = " ↔ ".join(x for x in (t.a_endpoint, t.b_endpoint) if x)
                edges.append({"data": {
                    "id": f"vpn:{t.id}", "source": a_id, "target": b_id,
                    "label": f"{t.type} · {wan}" if wan else t.type,
                    "kind": "vpn", "type": t.type, "status": t.status,
                    "evidence": EV_ASSERTED if "/" not in (t.name or "") else EV_MONITORED,
                    "a_endpoint": t.a_endpoint, "b_endpoint": t.b_endpoint,
                }})
                continue

            # 2) 只有一端可見（對端是外部站點，或對端 device 被過濾掉）→ 畫成遠端站點節點
            local = a_id if a_vis else (b_id if b_vis else None)
            if local is None:
                continue
            remote_label = t.b_endpoint or (t.name.split("/")[-1])
            site_id = f"vpnsite:{t.id}"
            nodes[site_id] = {"data": {
                "id": site_id,
                "label": remote_label,
                "type": "vpn_site",
                "kind": "vpn_site",
                "endpoint": t.b_endpoint,
                "tunnel": t.name,
                "vpn_type": t.type,
            }}
            edges.append({"data": {
                "id": f"vpn:{t.id}", "source": local, "target": site_id,
                "label": t.type, "kind": "vpn", "type": t.type, "status": t.status,
                "evidence": EV_ASSERTED if "/" not in (t.name or "") else EV_MONITORED,
            }})

    # ── L3 子網路自動拓樸：多訊號推導 device↔subnet 鄰接（精確，不亂猜） ──
    #   (ip)   IPAddress.device_id 明確連結，且 IP 落在子網路
    #   (name) 裝置名稱本身就是某 IP（防火牆/路由器常以管理 IP 命名）落在子網路
    #   (arp)  該裝置的 ARP 紀錄（device_id 看到的鄰居 IP）落在子網路 → 它有介面在該網段
    # 每條邊標 via=來源，前端可顯示「依 IP / 名稱 / ARP 推得」。
    if include_l3:
        subnets_all = list((await session.execute(select(Subnet))).scalars().all())
        cand = []  # [(Subnet, ip_network)]
        for sn in subnets_all:
            if subnet_filter is not None and sn.id not in subnet_filter:
                continue
            if vis_sub is not None and sn.id not in vis_sub:
                continue          # 只被授權部分子網路的帳號：看不到的子網路不畫
            try:
                cand.append((sn, _ipaddr.ip_network(str(sn.cidr), strict=False)))
            except ValueError:
                continue
        cand_sub_ids = {str(sn.id) for sn, _ in cand}

        # (device_id, subnet_id) → {"ip": 範例IP, "via": set()}
        assoc: dict[tuple[str, str], dict[str, Any]] = {}

        def _add(d_str: str, s_str: str, sample_ip: str | None, via: str) -> None:
            rec = assoc.get((d_str, s_str))
            if rec is None:
                rec = {"ip": sample_ip, "via": set()}
                assoc[(d_str, s_str)] = rec
            rec["via"].add(via)
            if sample_ip and not rec["ip"]:
                rec["ip"] = sample_ip

        # (ip) 明確連結的 IP
        ip_rows = (await session.execute(
            select(IPAddress.subnet_id, IPAddress.device_id, IPAddress.ip)
            .where(IPAddress.device_id.is_not(None))
        )).all()
        for sub_id, dev_id, ip in ip_rows:
            if sub_id is None or dev_id is None:
                continue
            s_str, d_str = str(sub_id), str(dev_id)
            if s_str not in cand_sub_ids or d_str not in visible_device_ids:
                continue
            _add(d_str, s_str, str(ip).split("/")[0], "ip")

        # (name) 裝置名稱即 IP，落在候選子網路（即使該 IP 未連結 device_id）
        for d_str in visible_device_ids:
            dev = device_objs.get(d_str)
            if dev is None:
                continue
            try:
                name_ip = _ipaddr.ip_address((dev.name or "").strip())
            except ValueError:
                continue
            for sn, net in cand:
                if name_ip in net:
                    _add(d_str, str(sn.id), str(name_ip), "name")

        # (arp) 裝置的 ARP 紀錄落在候選子網路 → 它接在該網段（ARP 只會有直連網段的鄰居）。
        # ⚠️ arp_entries.device_id 是 **LibreNMS 的裝置**，要經 jt_ipam_device_id 對回 jt-ipam 的裝置；
        # 以前直接拿來比 jt-ipam 裝置，從 v0.4.29 起一次都沒對上（2026-09-30 研究）。
        # 網段取包含那個位址的最精確子網路（以前取資料庫順序的第一個）。
        sindex = _SubnetIndex(cand)
        arp_rows = (await session.execute(
            select(LibreNMSDevice.jt_ipam_device_id, ARPEntry.ip)
            .join(LibreNMSDevice, LibreNMSDevice.id == ARPEntry.device_id)
            .where(in_values(LibreNMSDevice.jt_ipam_device_id, _as_uuids(visible_device_ids)))
        )).all()
        for dev_id, ip in arp_rows:
            d_str = str(dev_id)
            try:
                a = _ipaddr.ip_address(str(ip).split("/")[0])  # type: ignore[assignment]
            except ValueError:
                continue
            hit = sindex.lookup(a)
            if hit is not None:
                _add(d_str, str(hit.id), None, "arp")

        # (librenms) 用 LibreNMS 已知的管理 IP（primary_ip / hostname）關聯 device→subnet。
        #   交換器 / AP / 伺服器常沒有 IPAddress 連結、名稱也不是 IP，但 LibreNMS 知道
        #   它的管理 IP。這就是把 L2 裝置「掛進它所屬子網路」的訊號，否則它們會變成
        #   孤立節點，被前端藏掉（switch-003 / ap-001 不顯示就是這個原因）。
        #   注意：jt_ipam_device_id 可能指向「以 IP 命名」的重複裝置，而使用者看到的
        #   節點是「友善名稱」那一筆（同名重複）。所以除了用 jt_ipam_device_id 連，
        #   也用 sysname / hostname 去 match 同名的可見裝置節點，兩邊都連到子網路，
        #   重複裝置在收斂前也不會有人「不見」。
        name_to_dev: dict[str, str] = {}
        for _ds, _dev in device_objs.items():
            nm = (_dev.name or "").strip().lower()
            if nm:
                name_to_dev.setdefault(nm, _ds)
        ln_ip_rows = (await session.execute(
            select(LibreNMSDevice.jt_ipam_device_id, LibreNMSDevice.primary_ip,
                   LibreNMSDevice.hostname, LibreNMSDevice.sysname)
        )).all()
        for dev_id, pip, host, sysname in ln_ip_rows:
            mgmt = None
            for cand_ip in (pip, host):
                if not cand_ip:
                    continue
                try:
                    mgmt = _ipaddr.ip_address(str(cand_ip).split("/")[0].strip())
                    break
                except ValueError:
                    continue
            if mgmt is None:
                continue
            sub_id = None
            for sn, net in cand:
                if mgmt in net:
                    sub_id = str(sn.id)
                    break
            if sub_id is None:
                continue
            # 解析出所有對得上的可見裝置節點：直接連結 + 同名（sysname/hostname）
            targets: set[str] = set()
            if dev_id is not None and str(dev_id) in visible_device_ids:
                targets.add(str(dev_id))
            for nm in (sysname, host):
                key = (str(nm).strip().lower()) if nm else ""
                if key and key in name_to_dev:
                    targets.add(name_to_dev[key])
            for d_str in targets:
                _add(d_str, sub_id, str(mgmt), "librenms")

        # subnet nodes（只建有被關聯到的）
        used_subnet_ids = {s_str for (_, s_str) in assoc}
        if used_subnet_ids:
            for sn in subnets_all:
                sn_id = str(sn.id)
                if sn_id not in used_subnet_ids:
                    continue
                nodes[f"subnet:{sn_id}"] = {
                    "data": {
                        "id": f"subnet:{sn_id}",
                        "label": str(sn.cidr),
                        "type": "subnet",
                        "kind": "subnet",
                        "description": sn.description,
                        "subnet_uuid": sn_id,
                    }
                }

        # device → subnet edges
        for (d_str, s_str), rec in assoc.items():
            edges.append({
                "data": {
                    "id": f"l3:{d_str}:{s_str}",
                    "source": d_str,
                    "target": f"subnet:{s_str}",
                    "label": rec["ip"] or "",
                    "kind": "l3",
                    "via": ",".join(sorted(rec["via"])),
                    "evidence": evidence_of(",".join(sorted(rec["via"]))),
                }
            })

    # ── L2 存取層：FDB（哪個 MAC 出現在哪台交換器的哪個埠）──
    #
    # FDB 是目前唯一能自動畫出「東西掛在哪台交換器」與「交換器之間怎麼接」的來源
    # （LLDP/CDP 那張表在實機上是空的，沒有資料源）。兩個古典陷阱寫在
    # tests/test_topology_fdb.py：上行埠不是端點、同 MAC 對到多台裝置就不猜。
    #
    # FDB 屬證據契約的 learned 層：它說「曾經學到這個對應」，不說「現在活著」，
    # 所以這裡只畫線、不碰任何上線判定。
    if include_fdb:
        from app.services.arp_precedence import normalize_mac

        # ⚠️ `fdb_entries.device_id` 指的是 **LibreNMS 的裝置**，不是 jt-ipam 的 Device。
        # 直接在 SQL 裡接起來：一來沒連結到 Device 的交換器根本沒有節點可連，
        # 二來 FDB 是整個資料庫最會長的表之一（一台交換器就能有上萬列），
        # 撈回來再用 Python 篩會白扛一大堆用不到的列。
        fdb_rows = (await session.execute(
            select(FDBEntry.port_name, FDBEntry.mac, FDBEntry.vlan_id_num,
                   LibreNMSDevice.jt_ipam_device_id)
            .join(LibreNMSDevice, LibreNMSDevice.id == FDBEntry.device_id)
            .where(FDBEntry.port_name.is_not(None),
                   LibreNMSDevice.jt_ipam_device_id.is_not(None))
        )).all()
        # MikroTik 的 bridge host 表（0170）與 SNMP 收集的 MAC 表（0217）：直接記 jt-ipam 裝置，不經 LibreNMS。
        # 不分來源（以「沒有 LibreNMS 裝置、有 jt-ipam 裝置」判斷）：以前寫死 source == "mikrotik"，新來源就畫不出來
        fdb_rows = [*fdb_rows, *(await session.execute(
            select(FDBEntry.port_name, FDBEntry.mac, FDBEntry.vlan_id_num, FDBEntry.switch_device_id)
            .where(FDBEntry.device_id.is_(None), FDBEntry.port_name.is_not(None),
                   FDBEntry.switch_device_id.is_not(None))
        )).all()]

        if fdb_rows:
            # (交換器, 埠) → MAC 集合
            by_port: dict[tuple[str, str], set[str]] = {}
            vlan_of: dict[tuple[str, str, str], int | None] = {}
            for port_name, mac, vlan_num, sw_dev_id in fdb_rows:
                key = normalize_mac(mac)
                if not key:
                    continue
                pk = (str(sw_dev_id), str(port_name))
                by_port.setdefault(pk, set()).add(key)
                vlan_of.setdefault((*pk, key), vlan_num)

            switch_ids = {sw for sw, _ in by_port}

            # MAC → 裝置。對到多台就標成不明確（重疊網段下同一個 MAC 會有多筆 IP 記錄）。
            mac_to_dev: dict[str, str | None] = {}
            # 網卡名稱（裝置埠匯入的 MAC 才有；給前端說「哪張卡接在哪」）
            mac_nic: dict[str, str] = {}

            def _claim(dev_id: Any, mac: str | None) -> str | None:
                key = normalize_mac(mac) if mac else None
                if not key:
                    return None
                d_str = str(dev_id)
                if key in mac_to_dev and mac_to_dev[key] != d_str:
                    mac_to_dev[key] = None       # 不明確 → 之後一律跳過
                else:
                    mac_to_dev.setdefault(key, d_str)
                return key

            for dev_id, mac in (await session.execute(
                select(IPAddress.device_id, IPAddress.mac).where(
                    IPAddress.device_id.is_not(None), IPAddress.mac.is_not(None)
                )
            )).all():
                _claim(dev_id, mac)
            # 沒有 IP 記錄的網卡（例如虛擬化主機的儲存網路卡）也算這台裝置的（10-23 雙接主機）。
            # 交換器自己的埠 MAC 不加：骨幹推導看的「交換器自己的 MAC」維持只用管理位址那顆
            for dev_id, name, mac in (await session.execute(
                select(DevicePort.device_id, DevicePort.name, DevicePort.mac_address).where(
                    DevicePort.mac_address.is_not(None), DevicePort.type == "network")
            )).all():
                if str(dev_id) in switch_ids:
                    continue
                key = _claim(dev_id, mac)
                if key and mac_to_dev.get(key) == str(dev_id):
                    mac_nic.setdefault(key, name)
            # 每台交換器自己的 MAC（用來認出「對面那台就是它」）
            own_macs: dict[str, set[str]] = {}
            for mac, dev in mac_to_dev.items():
                if dev is not None:
                    own_macs.setdefault(dev, set()).add(mac)

            # ① 存取層：MAC 數不超過門檻的埠
            #
            #    ⚠️ 只有「這個埠上就這麼一個 MAC」才證明直接插在上面（`direct=True`）。
            #    埠上有兩三個 MAC 時，可能是底下接了一台笨集線器／小交換器，也可能是
            #    一台跑著多個虛擬機的主機 —— 那些機器確實在這個埠後面，但不見得是直連。
            #    實機上這個差別是看得到的：某台交換器的一個埠後面掛著三台不同的機器，
            #    而同一批機器又出現在另一台交換器的另一個埠上。兩邊都畫成直連就是說謊，
            #    所以邊上帶 `direct`，前端據此畫實線或虛線。
            #
            #    埠上 MAC 很多**不代表**那是上行埠：AP、虛擬化主機、下游笨交換器的埠
            #    上也會有幾十個 MAC，而那就是它插的地方。實機上六台網路設備一度全部
            #    落到「查不出位置」，就是被這個過度簡化的判準誤傷。
            #    分得出來的是**包含關係**：離裝置最近的埠，背後的 MAC 集合會是外層埠的
            #    子集（實機驗證：某 AP 的埠 35 個 MAC 正是上行埠 144 個 MAC 的子集）。
            #    找不到唯一的最內層埠時就不猜。
            l2_seen: set[tuple[str, str]] = set()
            adjacency: set[tuple[str, str]] = set()   # 已用單一 MAC 埠證實直連的交換器對

            # 側錄資料要看**全部**的埠，不能只看畫得出來的交換器：內外層的比較靠的是
            # 上行埠那份「什麼都看得到」的清單，而上行的那台交換器常常不在目前的篩選
            # 範圍內（管理 IP 在別的網段）。少了它就退化成「只看到一次」而放棄推導。
            # 可見性只在**畫線的時候**才判斷。
            sightings: dict[str, list[tuple[str, str]]] = {}
            for (sw_id, port), macs in by_port.items():
                for mac in macs:
                    sightings.setdefault(mac, []).append((sw_id, port))

            # 看得到別台交換器自己 MAC 的埠＝朝那台的上聯（一次算好；大型站台一個埠上千個 MAC）
            switch_mac_of = {m: sw for sw in switch_ids for m in own_macs.get(sw, ())}
            uplink_ports = {pk for pk, macs in by_port.items()
                            if any(switch_mac_of.get(m) not in (None, pk[0]) for m in macs)}

            for mac in sorted(sightings):
                peer = mac_to_dev.get(mac)
                if peer is None or peer not in visible_device_ids:
                    continue                                   # 不明確／不在圖上
                spots = [(sw, pt) for sw, pt in sightings[mac] if sw != peer]
                if not spots:
                    continue                                   # 只看到自己身上
                # 上聯埠（看得到別台交換器自己 MAC 的埠）不是端點：這張網卡是從那條上聯被對面學到的。
                # 拿掉之後只剩一個埠，就是它插的地方，即使那個埠上有好幾個 MAC（主機上的 VM）——
                # 兩張表各自老化，包含關係常常判不出內外（10-23 實機：雙接的虛擬化主機一半的線畫不出來）
                facing = [x for x in spots if x in uplink_ports]
                ruled_out_uplink = bool(facing) and len(facing) < len(spots)
                if ruled_out_uplink:
                    spots = [x for x in spots if x not in facing]
                small = [(sw, pt) for sw, pt in spots
                         if len(by_port[(sw, pt)]) <= UPLINK_MAC_THRESHOLD]
                bond: list[tuple[str, str]] = []
                if small:
                    # 網卡綁定（bond / LACP）跨兩台交換器：兩張卡共用一個 MAC，兩台各在一個埠學到它
                    # （實機 11-07：檔案伺服器、日誌伺服器只畫出一邊）。每台取最乾淨的埠；
                    # 那些埠背後的 MAC 集合完全一樣（直連就是只有它自己）才算 —— 朝外的路不會跟存取埠一模一樣
                    per_sw: dict[str, tuple[str, str]] = {}
                    for x in sorted(small, key=lambda x: (len(by_port[x]), x[1])):
                        per_sw.setdefault(x[0], x)
                    picked = list(per_sw.values())
                    if len(picked) >= 2 and all(by_port[x] == by_port[picked[0]] for x in picked):
                        bond = sorted(picked)
                if bond:
                    sw_id, port = bond[0]
                elif small:
                    # 有「乾淨」的埠就用它（同時看到多個時取 MAC 最少的那個）
                    sw_id, port = min(small, key=lambda x: (len(by_port[x]), x[1]))
                elif len(spots) == 1 and ruled_out_uplink:
                    sw_id, port = spots[0]
                elif len(spots) < 2:
                    # 只在一個熱鬧的埠上看到，沒有內外可比 —— 它可能在那個上行埠後面的
                    # 任何地方。（注意：對空集合做 all() 是恆真的，少了這個判斷就會
                    # 把「唯一一次側錄」誤當成「最內層」。）
                    continue
                else:
                    # 全是熱鬧的埠 → 找唯一一個「背後 MAC 集合是其他所有埠的真子集」的
                    inner = [
                        (sw, pt) for sw, pt in spots
                        if all(by_port[(sw, pt)] < by_port[(sw2, pt2)]
                               for sw2, pt2 in spots if (sw2, pt2) != (sw, pt))
                    ]
                    if len(inner) != 1:
                        continue                               # 分不出內外 → 不猜
                    sw_id, port = inner[0]
                ends = bond or [(sw_id, port)]
                for sw_id, port in ends:
                    if sw_id not in visible_device_ids or (peer, sw_id) in l2_seen:
                        continue                               # 交換器不在圖上就不畫線
                    macs_here = by_port[(sw_id, port)]
                    direct = len(macs_here) == 1
                    l2_seen.add((peer, sw_id))
                    if peer in switch_ids and direct:
                        adjacency.add((min(sw_id, peer), max(sw_id, peer)))
                    edges.append({"data": {
                        "id": f"l2:{peer}:{sw_id}:{port}",
                        "source": peer, "target": sw_id,
                        "label": port, "kind": "l2", "via": "fdb", "evidence": EV_LEARNED,
                        "port": port, "direct": direct,
                        "port_mac_count": len(macs_here),
                        "vlan": vlan_of.get((sw_id, port, mac)),
                        # 哪張網卡：前端用來分辨「兩張卡各接一台」（雙接）
                        "mac": ":".join(mac[i:i + 2] for i in range(0, len(mac), 2)) if len(mac) == 12 else mac,
                        "nic": mac_nic.get(mac),
                        # 同一張（綁定的）網卡插在兩台交換器：前端照雙接畫（只有綁定的才帶）
                        **({"bond": True} if bond else {}),
                    }})

            # ② 骨幹：兩台交換器直連的判準（Breitbart 等人的 FDB 拓樸推導條件）
            #
            #    A 的某個埠 P 上看得到 B 自己的 MAC，且 B 的某個埠 Q 上看得到 A 自己的
            #    MAC，**而且 P 與 Q 背後的 MAC 集合不相交**。
            #
            #    第三個條件才是關鍵：少了它，A—B—C 這種串接會被畫成 A—C 也直連
            #    （C 的 MAC 當然會出現在 A 朝 B 的那個埠上）。相交檢查會擋掉，因為
            #    A 朝 B 的埠與 C 朝 B 的埠都含有 B 與 B 底下那些機器。
            #    單邊只看得到對方（對面那台沒在回報 FDB）時**不畫** —— 無從證實。
            uplink_pairs = uplink_pairs_from_fdb(by_port, own_macs, switch_ids, visible_device_ids, adjacency)

            for (a, b), (pa, pb) in sorted(uplink_pairs.items()):
                edges.append({"data": {
                    "id": f"l2u:{a}:{b}",
                    "source": a, "target": b,
                    # port 對應 source 那端、peer_port 對應 target 那端（source/target
                    # 由 id 排序決定，兩端誰先誰後沒有意義）
                    "label": f"{pa} ↔ {pb}", "kind": "l2_uplink", "via": "fdb",
                    "evidence": EV_LEARNED,
                    "port": pa, "peer_port": pb,
                }})

    # ── 鄰居探索：MikroTik 的 /ip/neighbor（MNDP／CDP／LLDP，0170）──
    #
    # 鄰居自己宣告「我是誰、接在你哪個埠」，交換器之間的連線也畫得出來 —— 那正是 FDB 推導最弱的地方。
    # 鄰居對不到唯一一台裝置就不畫（services/mikrotik.match_neighbor_devices）。
    if include_fdb:
        from app.models.mikrotik import MikroTikNeighbor, MikroTikRouter
        from app.services.mikrotik import match_neighbor_devices
        router_dev = {rid: str(did) for rid, did in (await session.execute(
            select(MikroTikRouter.id, MikroTikRouter.device_id).where(
                MikroTikRouter.device_id.is_not(None), MikroTikRouter.enabled.is_(True)))).all()}
        neigh = list((await session.execute(select(MikroTikNeighbor).where(
            in_values(MikroTikNeighbor.router_id, list(router_dev))))).scalars().all()) if router_dev else []
        matched = await match_neighbor_devices(session, neigh) if neigh else {}
        drawn: set[tuple[str, str]] = set()
        for n in neigh:
            a = router_dev.get(n.router_id)
            b = (matched.get(n.id) or {}).get("device_id")
            if not a or not b or a == b or a not in visible_device_ids or b not in visible_device_ids:
                continue
            pair = (min(a, b), max(a, b))
            if pair in drawn:
                continue
            drawn.add(pair)
            label = f"{n.interface} ↔ {n.remote_interface}" if n.remote_interface else n.interface
            edges.append({"data": {
                "id": f"nb:{a}:{b}:{n.interface}",
                "source": a, "target": b, "label": label,
                "kind": "l2_uplink", "via": "neighbor", "evidence": EV_MONITORED,
                "port": n.interface, "peer_port": n.remote_interface,
                "protocol": n.discovered_by,
            }})

    # ── 鄰居探索：SNMP 收集的 LLDP／CDP（0217）──
    #
    # 跟上面 MikroTik 的鄰居同一個道理：對方親口說「我是誰、接在你哪個埠」。對到唯一一台裝置才畫
    # （services/snmp_collect.match_neighbors：管理位址、chassis MAC、系統名稱）。同一對裝置已經畫過就不重複
    if include_fdb:
        from app.models.snmp import SnmpNeighbor, SnmpTarget
        from app.services.snmp_collect import match_neighbors
        snmp_neigh = list((await session.execute(
            select(SnmpNeighbor).join(SnmpTarget, SnmpTarget.id == SnmpNeighbor.target_id)
            .where(SnmpTarget.enabled.is_(True)))).scalars().all())
        snmp_matched = await match_neighbors(session, snmp_neigh) if snmp_neigh else {}
        drawn_snmp = {(min(e["data"]["source"], e["data"]["target"]), max(e["data"]["source"], e["data"]["target"]))
                      for e in edges if e["data"].get("via") == "neighbor"}
        for n in snmp_neigh:
            a = str(n.device_id)
            b = (snmp_matched.get(n.id) or {}).get("remote_device_id")
            if not b or a == b or a not in visible_device_ids or b not in visible_device_ids:
                continue
            pair = (min(a, b), max(a, b))
            if pair in drawn_snmp:
                continue
            drawn_snmp.add(pair)
            label = f"{n.local_port} ↔ {n.remote_port}" if n.local_port and n.remote_port else (n.local_port or "")
            edges.append({"data": {
                "id": f"nbs:{a}:{b}:{n.local_port}",
                "source": a, "target": b, "label": label,
                "kind": "l2_uplink", "via": "neighbor", "evidence": EV_MONITORED,
                "port": n.local_port, "peer_port": n.remote_port, "protocol": n.protocol,
            }})

    # ── 虛擬機 ↔ 它跑在哪台實體主機 ──
    #
    # ⚠️ `virtual_machines.device_id` 是 **VM 自己**對映到的裝置，不是它的實體主機。
    # 主機在 `node`（PVE 節點名／ESXi 主機名），只能拿名稱去比對裝置 ——
    # 實機上 5 個節點名全部對得上 `devices.name`。
    #
    # 預設不畫（`include_vms=False`）：實機上一次會多出 149 顆節點，圖直接淹掉。
    if include_vms:
        from app.models.virt import VirtCluster, VirtualMachine

        vms = list((await session.execute(
            select(VirtualMachine).where(VirtualMachine.node.is_not(None))
        )).scalars().all())
        if vms:
            # 裝置名稱 → id；同名多台就標成不明確，不猜（同重疊網段同 MAC 的原則）
            name_to_dev: dict[str, str | None] = {}
            for did, dname in (await session.execute(select(Device.id, Device.name))).all():
                key = (dname or "").strip().lower()
                if not key:
                    continue
                if key in name_to_dev and name_to_dev[key] != str(did):
                    name_to_dev[key] = None
                else:
                    name_to_dev.setdefault(key, str(did))

            cl_names: dict[str, str] = {
                str(cid): cname
                for cid, cname in (await session.execute(
                    select(VirtCluster.id, VirtCluster.name)
                )).all()
            }

            for vm in vms:
                host_id = name_to_dev.get((vm.node or "").strip().lower())
                if host_id is None or host_id not in visible_device_ids:
                    continue          # 找不到主機／不明確／主機不在圖上 → 不畫
                # VM 已經對映成一台 Device 就用那顆既有節點，不要讓同一台機器出現兩次
                own = str(vm.device_id) if vm.device_id else None
                if own and own in visible_device_ids:
                    src = own
                else:
                    src = f"vm:{vm.id}"
                    nodes[src] = {"data": {
                        "id": src, "label": vm.name, "type": "vm", "kind": "vm",
                        "status": vm.status, "host": vm.node,
                        "cluster": cl_names.get(str(vm.cluster_id)),
                        "vcpus": vm.vcpus, "memory_mb": vm.memory_mb,
                        "vm_uuid": str(vm.id),
                    }}
                if src == host_id:
                    continue          # 自己跑在自己上面（資料異常）不畫成自環
                edges.append({"data": {
                    "id": f"vmhost:{vm.id}",
                    "source": src, "target": host_id,
                    "label": "", "kind": "vm_host", "via": "virtualization",
                    "evidence": EV_MONITORED,
                    "status": vm.status,
                }})

    # ── 節點細節加值：rack/location 名稱、管理 IP、LibreNMS 撈回的 os/hardware/版本/狀態 ──
    if device_objs:
        dev_uuids = [d.id for d in device_objs.values()]
        rack_ids = {d.rack_id for d in device_objs.values() if d.rack_id}
        loc_ids = {d.location_id for d in device_objs.values() if d.location_id}
        pip_ids = {d.primary_ip_id for d in device_objs.values() if d.primary_ip_id}

        rack_names: dict[str, str] = {}
        if rack_ids:
            for r in (await session.execute(select(Rack).where(in_values(Rack.id, rack_ids)))).scalars().all():
                rack_names[str(r.id)] = r.name
        loc_names: dict[str, str] = {}
        if loc_ids:
            for lo in (await session.execute(select(Location).where(in_values(Location.id, loc_ids)))).scalars().all():
                loc_names[str(lo.id)] = lo.name
        pip_map: dict[str, str] = {}
        pip_kind: dict[str, str] = {}
        if pip_ids:
            for pid, pip, kind in (await session.execute(
                select(IPAddress.id, IPAddress.ip, IPAddress.device_kind).where(in_values(IPAddress.id, pip_ids))
            )).all():
                pip_map[str(pid)] = str(pip).split("/")[0]
                if kind:
                    pip_kind[str(pid)] = kind
        ln_map: dict[str, LibreNMSDevice] = {}
        ln_rows = (await session.execute(
            select(LibreNMSDevice).where(in_values(LibreNMSDevice.jt_ipam_device_id, dev_uuids))
        )).scalars().all()
        for ln in ln_rows:
            ln_map[str(ln.jt_ipam_device_id)] = ln

        for d_str, dev in device_objs.items():
            data = nodes[d_str]["data"]
            # 管理 IP：primary_ip → 否則 device 名稱本身若是 IP
            ip = pip_map.get(str(dev.primary_ip_id)) if dev.primary_ip_id else None
            if not ip:
                try:
                    ip = str(_ipaddr.ip_address((dev.name or "").strip()))
                except ValueError:
                    ip = None
            if ip:
                data["ip"] = ip
            if dev.rack_id and str(dev.rack_id) in rack_names:
                data["rack"] = rack_names[str(dev.rack_id)]
            if dev.location_id and str(dev.location_id) in loc_names:
                data["location"] = loc_names[str(dev.location_id)]
            ln = ln_map.get(d_str)  # type: ignore[assignment]
            if ln is not None:
                # Device.type 多半是 "other"（LibreNMS 早期 sync 沒細分）；用 os/hardware
                # 重新推一次，讓 AP/交換器/伺服器在圖例與顏色上分得出來。
                if data.get("type") in (None, "other"):
                    from app.services.librenms import _infer_device_type
                    refined = _infer_device_type(ln)
                    if refined != "other":
                        data["type"] = refined
                if ln.os:
                    data["os"] = ln.os
                if ln.hardware:
                    data["hardware"] = ln.hardware
                if ln.version:
                    data["sw_version"] = ln.version
                if ln.sysname:
                    data["sysname"] = ln.sysname
                if ln.status:
                    data["status"] = ln.status
            # 還是不知道是什麼：看主要 IP 的掃描代理判讀（含 Recog 指紋庫，services/device_identity）
            if data.get("type") in (None, "other") and dev.primary_ip_id:
                mapped = _KIND_TO_TYPE.get(pip_kind.get(str(dev.primary_ip_id), ""))
                if mapped:
                    data["type"] = mapped

    if scope_limited:
        # 最後一道：兩端都在圖上的邊才留（任何一種邊漏過濾也不會帶出看不到的節點編號）
        edges = [e for e in edges if e["data"].get("source") in nodes and e["data"].get("target") in nodes]
        return {"nodes": list(nodes.values()), "edges": edges, "scope": "limited",
                "hidden_layers": hidden_layers}
    return {"nodes": list(nodes.values()), "edges": edges}
