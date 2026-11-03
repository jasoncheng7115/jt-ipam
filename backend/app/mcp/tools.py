"""IPAM 工具實作（給 MCP server 與 NL chat 共用）。

所有工具：
- 接受 plain dict 參數（避免 Pydantic 把 LLM 不嚴謹的型別當錯）
- 走 SQLAlchemy session；不繞 REST
- 每個工具回傳 JSON-serialisable dict
- 失敗時 raise IPAMToolError（含人讀訊息）
"""

from __future__ import annotations

import ipaddress
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import String, and_, func, literal, or_, select, text
from sqlalchemy import false as sa_false
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sqlin import in_values
from app.models.address import IPAddress
from app.models.customer import Customer
from app.models.device import Device
from app.models.dns import DNSRecord
from app.models.librenms import ARPEntry, FDBEntry, LibreNMSDevice
from app.models.location import Location, Rack
from app.models.nat import NATTranslation
from app.models.section import Section
from app.models.subnet import Subnet
from app.models.user import User
from app.models.vlan import VLAN, DeviceVLAN
from app.services.address import (
    IPAlreadyExists,
    IPNotInSubnet,
)
from app.services.oui import vendor_for_mac
from app.services.permission import filter_visible, visible_ids
from app.services.subnet import find_first_free_address, find_free_addresses, get_usage


class IPAMToolError(Exception):
    pass


def _as_uuid(value: str, field: str = "id") -> uuid.UUID:
    """把 LLM 給的字串轉 UUID；格式不對就回優雅錯誤（避免 ValueError 變成 tool failed）。"""
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError) as exc:
        raise IPAMToolError(f"invalid {field}: {value!r}") from exc


async def _effective_probes(
    session: AsyncSession, sub: Subnet | None, obj: IPAddress,
) -> list[str]:
    """此 IP 實際會被執行的探測 = 子網路 scan_method − IP excluded ∩ 代理 enabled。"""
    from app.core.scan_probes import effective_probes
    from app.models.scan_agent import ScanAgent
    if sub is None or not sub.scan_enabled:
        return []
    agent_enabled: list[str] | None = None
    if sub.scan_agent_id:
        ag = await session.get(ScanAgent, sub.scan_agent_id)
        if ag is not None:
            agent_enabled = list(ag.enabled_probes or [])
    return effective_probes(
        list(sub.scan_method or []), list(obj.excluded_probes or []), agent_enabled,
    )


# ─────────────────── 唯讀工具 ───────────────────
#
# 2026-10-15 盤點（使用者問電路到期日記在哪，AI 答「沒有這個欄位」）：模型只看得到工具給的欄位，
# 少給一個它就斷言系統沒有。所以這一批工具回傳畫面上看得到、設得到的每個欄位，外鍵一律帶名稱
# （不只 UUID），清單一律帶 total／truncated（模型才分得出「就這些」還是「被截斷」）。
# tests/test_mcp_core_tool_fields.py 逐支守著。


def _iso(v: Any) -> str | None:
    return v.isoformat() if v is not None and hasattr(v, "isoformat") else None


def _host(v: Any) -> str | None:
    """INET → 不帶 /前綴 的位址字串。"""
    return str(v).split("/")[0] if v is not None else None


#: 會長到幾百萬筆的表（異動記錄）只數到這裡，超過就回「至少這麼多」
_COUNT_CAP = 10_000


def _page(total: int, returned: int) -> dict[str, Any]:
    """清單的分頁資訊：total＝可見範圍內、LIMIT 之前的總數。"""
    return {"total": total, "returned": returned, "truncated": total > returned}


async def _count(session: AsyncSession, stmt: Any) -> int:
    return int(await session.scalar(
        select(func.count()).select_from(stmt.order_by(None).subquery())) or 0)


async def _name_map(session: AsyncSession, column: Any, ids: Any) -> dict[Any, Any]:
    """{id: 欄位值}，一個 SQL 批次查（清單幾百列時不逐列查）。column 例如 Section.name。"""
    wanted = {i for i in ids if i is not None}
    if not wanted:
        return {}
    model_id = column.class_.id
    return dict((await session.execute(
        select(model_id, column).where(in_values(model_id, list(wanted))))).all())


async def _timestamps(session: AsyncSession, model: Any, obj_id: Any) -> dict[str, str | None]:
    """建立／更新時間直接查欄位：updated_at 是資料庫端 onupdate，物件更新過後這個屬性是過期的，
    在 async 底下直接讀會變成隱性 IO（MissingGreenlet）。"""
    row = (await session.execute(
        select(model.created_at, model.updated_at).where(model.id == obj_id))).first()
    return {"created_at": _iso(row[0]) if row else None, "updated_at": _iso(row[1]) if row else None}


async def _user_names(session: AsyncSession, ids: Any) -> dict[Any, str]:
    """使用者 id → 顯示名稱（沒設顯示名稱就用帳號）。"""
    from app.models.user import User
    wanted = {i for i in ids if i is not None}
    if not wanted:
        return {}
    return {uid: (dn or un) for uid, dn, un in (await session.execute(
        select(User.id, User.display_name, User.username).where(in_values(User.id, list(wanted))))).all()}


async def _visible_device_names(session: AsyncSession, user: User, ids: Any) -> dict[Any, str]:
    """裝置名稱，只給看得到的裝置（與 REST 位址清單同一規則：看不到的裝置不帶名稱，2026-10-07 稽核）。"""
    wanted = {i for i in ids if i is not None}
    if not wanted:
        return {}
    vis = await visible_ids(session, user=user, object_type="device")
    if vis is not None:
        wanted &= vis
    from app.models.device import Device as _Dev
    return await _name_map(session, _Dev.name, wanted)


def _ip_last_seen(o: Any) -> str | None:
    """清單「最後出現」欄：掃描代理／LibreNMS／ARP 三者取最新（與 REST 位址清單的排序欄同一個定義）。"""
    vals = [v for v in (o.last_seen_scanner, o.last_seen_librenms, o.last_seen_arp) if v is not None]
    return max(vals).isoformat() if vals else None


def _ip_fields(o: IPAddress) -> dict[str, Any]:
    """一筆 IP 在畫面（位址清單＋編輯表單）上看得到的欄位；名稱類（子網路、裝置、單位）由呼叫端補。"""
    return {
        "id": str(o.id), "ip": _host(o.ip), "subnet_id": str(o.subnet_id) if o.subnet_id else None,
        "hostname": o.hostname, "mac": str(o.mac) if o.mac else None,
        "state": o.state, "effective_status": o.effective_status,
        "owner": o.owner, "description": o.description, "note": o.note,
        "switch_port": o.switch_port, "switch_port_confident": o.switch_port_confident,
        # 掃描代理定期判讀的設備類型與型號（camera／printer／storage…）
        "device_kind": o.device_kind, "device_model": o.device_model,
        "os_family": o.os_family, "os_guess": o.os_guess,
        "discovery_source": o.discovery_source,
        "last_seen_scanner": _iso(o.last_seen_scanner), "last_seen_librenms": _iso(o.last_seen_librenms),
        "last_seen_arp": _iso(o.last_seen_arp), "last_seen_wazuh": _iso(o.last_seen_wazuh),
        "last_seen_zabbix": _iso(o.last_seen_zabbix), "last_seen_ocs": _iso(o.last_seen_ocs),
        "last_seen_dns": _iso(o.last_seen_dns),
        "dhcp_reserved": bool(o.dhcp_reserved), "in_dhcp_lease": bool(o.in_dhcp_lease),
        "is_dhcp_server": bool(o.is_dhcp_server),
        "ptr_ignore": bool(o.ptr_ignore), "exclude_from_ping": bool(o.exclude_from_ping),
        # 上線狀態跟著所屬裝置（多 LAN 閘道不回 ping）
        "liveness_from_device": bool(o.liveness_from_device),
    }


async def search_ip(session: AsyncSession, *, user: User, ip: str) -> dict[str, Any]:
    """根據 IP 找它在 IPAM 的紀錄與所屬 subnet（重疊網段會有多筆，全部列出）。"""
    try:
        ipaddress.ip_address(ip)
    except ValueError as exc:
        raise IPAMToolError(f"Invalid IP: {exc}") from exc
    from app.services.oui import mac_prefix, vendor_map
    vis = await visible_ids(session, user=user, object_type="subnet")
    stmt = (select(IPAddress, Subnet.cidr).join(Subnet, Subnet.id == IPAddress.subnet_id)
            .where(IPAddress.ip == ip))
    if vis is not None:
        if not vis:
            return {"ip": ip, "matches": [], "count": 0, "unregistered": await _unregistered(session, user, ip)}
        stmt = stmt.where(in_values(IPAddress.subnet_id, vis))
    rows = (await session.execute(stmt.order_by(Subnet.cidr))).all()
    if not rows:
        # 沒有記錄不等於系統沒看過（同 get_ip_detail）
        return {"ip": ip, "matches": [], "count": 0, "unregistered": await _unregistered(session, user, ip)}
    devs = await _visible_device_names(session, user, [r.device_id for r, _c in rows])
    custs = await _name_map(session, Customer.name, [r.customer_id for r, _c in rows])
    vendors = await vendor_map(session, [r.mac for r, _c in rows])
    out = []
    for r, cidr in rows:
        out.append({
            **_ip_fields(r),
            "subnet": str(cidr),
            "mac_vendor": vendors.get(mac_prefix(r.mac) or "") if r.mac else None,
            "device": devs.get(r.device_id),
            "customer": custs.get(r.customer_id),
        })
    return {"ip": ip, "matches": out, "count": len(out)}


async def find_free_ip(
    session: AsyncSession, *, user: User, subnet_cidr: str | None = None,
    subnet_id: str | None = None,
) -> dict[str, Any]:
    """找指定 subnet 的第一個空閒 IP。可給 cidr 或 subnet_id。"""
    # 與其他工具同一個解析（可見性、只給位址、重疊網段不猜）；以前這裡自己另寫一份 CAST 查詢
    subnet = await _resolve_subnet(session, user=user, subnet_id=subnet_id, subnet_cidr=subnet_cidr)
    ip = await find_first_free_address(session, subnet)
    return {
        "subnet_id": str(subnet.id),
        "cidr": str(subnet.cidr),
        "ip": ip,
        "skipped_in_use": await _skipped_in_use(session, subnet),
    }


async def _skipped_in_use(session: AsyncSession, subnet: Any) -> list[dict[str, Any]]:
    """沒有 IP 記錄、但看得到在用而被跳過的位址（指示計的「未納管」）。要講出來：不說的話模型會以為系統
    沒有這份資料，又自己去 ping 一輪（使用者 11-02）。"""
    from app.services.unmanaged import for_subnet
    rows = sorted(await for_subnet(session, subnet.id), key=lambda e: ipaddress.ip_address(e["ip"]))
    return [{k: e.get(k) for k in ("ip", "sources", "last_seen_at", "mac", "hostname")} for e in rows[:50]]


async def _resolve_subnet(
    session: AsyncSession, *, user: User,
    subnet_id: str | None, subnet_cidr: str | None,
) -> Subnet:
    subnet: Subnet | None = None
    if subnet_id:
        subnet = await session.get(Subnet, _as_uuid(subnet_id, "subnet_id"))
    elif subnet_cidr:
        subnet = await _subnet_by_cidr_arg(session, user=user, value=subnet_cidr)
    if subnet is None:
        raise IPAMToolError("subnet not found")
    visible = set(await filter_visible(
        session, user=user, object_type="subnet",
        object_ids=[subnet.id], required="read",
    ))
    if subnet.id not in visible:
        raise IPAMToolError("subnet not found")   # 跟不存在同一句，不透露它在不在
    return subnet


async def _subnet_by_cidr_arg(session: AsyncSession, *, user: User, value: str) -> Subnet | None:
    """模型給的子網路字串 → 子網路。

    - `198.51.100.0/24`（主機位元有值也行）→ 完全相同的子網路
    - 只給位址（`192.0.2.0`，使用者這樣問很常見；以前被當成 /32 而找不到）→ 包含它、最小的那個子網路
    - 在 Python 先解析：不合法的字串是看得懂的錯誤，不能交給資料庫轉型（轉型失敗會讓整個交易中止）
    - 重疊網段（不同 VRF／客戶）同時符合、而且這個帳號都看得到時不猜，列出候選要模型改帶 subnet_id
    """
    raw = (value or "").strip()
    try:
        if "/" in raw:
            net = ipaddress.ip_network(raw, strict=False)
            rows = (await session.execute(
                text("SELECT id::text AS id, cidr::text AS cidr, masklen(cidr) AS ml FROM subnets "
                     "WHERE cidr = CAST(:c AS cidr)"), {"c": str(net)})).all()
        else:
            addr = ipaddress.ip_address(raw)
            rows = (await session.execute(
                text("SELECT id::text AS id, cidr::text AS cidr, masklen(cidr) AS ml FROM subnets "
                     "WHERE cidr >>= CAST(:a AS inet) ORDER BY masklen(cidr) DESC"), {"a": str(addr)})).all()
    except ValueError:
        raise IPAMToolError(f"invalid subnet_cidr: {raw[:80]!r} (expected a CIDR such as 192.0.2.0/24)") from None
    if not rows:
        return None
    visible = set(await filter_visible(
        session, user=user, object_type="subnet",
        object_ids=[uuid.UUID(r.id) for r in rows], required="read",
    ))
    seen = [r for r in rows if uuid.UUID(r.id) in visible]
    if not seen:
        return None
    best = [r for r in seen if r.ml == seen[0].ml]
    if len(best) > 1:
        listed = "; ".join(f"{r.cidr} (subnet_id={r.id})" for r in best[:10])
        raise IPAMToolError(f"{len(best)} subnets match {raw!r} (overlapping networks); "
                            f"call again with subnet_id: {listed}")
    return await session.get(Subnet, uuid.UUID(best[0].id))


async def _scope_subnet(
    session: AsyncSession, *, user: User,
    subnet_cidr: str | None = None, subnet_id: str | None = None,
) -> tuple[list[uuid.UUID] | None, str]:
    """清單工具的共用範圍解析：回 (subnet_ids | None 代表全域, 範圍標籤)。

    沒有範圍參數時，問「某網段有哪些…」的答案會變成全站資料 —— 這類錯誤每個數字
    單獨看都是真的，最難察覺。解析同時走 `_resolve_subnet` 的可見性把關。
    """
    if not (subnet_cidr or subnet_id):
        return None, "all"
    subnet = await _resolve_subnet(
        session, user=user, subnet_id=subnet_id, subnet_cidr=subnet_cidr)
    return [subnet.id], str(subnet.cidr)


async def find_free_ips(
    session: AsyncSession, *, user: User,
    subnet_cidr: str | None = None, subnet_id: str | None = None,
    count: int = 1, consecutive: bool = False,
) -> dict[str, Any]:
    """找指定 subnet 內 count 個可用 IP；consecutive=True 要求連續一段。

    回傳真實未配發的 IP（已排除 ip_addresses 既有紀錄、冷卻中、保留／DHCP 範圍，以及看得到在用的未納管位址），
    不要自行臆測。被跳過的在用位址列在 skipped_in_use。
    """
    count = max(1, min(int(count), 256))
    subnet = await _resolve_subnet(
        session, user=user, subnet_id=subnet_id, subnet_cidr=subnet_cidr,
    )
    ips = await find_free_addresses(
        session, subnet, count=count, consecutive=consecutive,
    )
    return {
        "subnet_id": str(subnet.id),
        "cidr": str(subnet.cidr),
        "requested": count,
        "consecutive": consecutive,
        "found": len(ips),
        "ips": ips,
        "skipped_in_use": await _skipped_in_use(session, subnet),
        "note": (
            "fewer free IPs than requested" if len(ips) < count and not consecutive
            else ("no consecutive run of that size" if consecutive and not ips else "ok")
        ),
    }


async def list_subnets(
    session: AsyncSession, *, user: User, section_id: str | None = None,
    limit: int = 50, archived: bool = False,
) -> dict[str, Any]:
    """子網路清單（畫面「子網路」頁的欄位＋名稱）。長尾設定（掃描方式、連線出口、位址範圍…）在 get_subnet_detail。

    - 與畫面一致：預設只列未歸檔；archived=True 看歸檔區（每列都標 archived）
    - 可見性推進 SQL：以前先 LIMIT 再過濾，受限帳號看得到的子網路只要排在後面就整個消失
    """
    from app.models.location import Location as _Loc
    from app.models.scan_agent import ScanAgent
    from app.models.vrf import VRF
    from app.services.subnet import host_count
    limit = max(1, min(int(limit), 200))
    stmt = select(Subnet).where(
        Subnet.archived_at.is_not(None) if archived else Subnet.archived_at.is_(None))
    if section_id:
        stmt = stmt.where(Subnet.section_id == _as_uuid(section_id, "section_id"))
    vis = await visible_ids(session, user=user, object_type="subnet")
    if vis is not None:
        stmt = stmt.where(in_values(Subnet.id, vis) if vis else sa_false())
    total_rows = await _count(session, stmt)
    rows = list((await session.execute(stmt.order_by(Subnet.cidr).limit(limit))).scalars().all())
    ids = [r.id for r in rows]
    used_map: dict[Any, int] = {}
    if ids:
        used_map = {sid: int(n) for sid, n in (await session.execute(
            select(IPAddress.subnet_id, func.count()).where(in_values(IPAddress.subnet_id, ids))
            .group_by(IPAddress.subnet_id))).all()}
    secs = await _name_map(session, Section.name, [r.section_id for r in rows])
    custs = await _name_map(session, Customer.name, [r.customer_id for r in rows])
    locs = await _name_map(session, _Loc.name, [r.location_id for r in rows])
    vrfs = await _name_map(session, VRF.name, [r.vrf_id for r in rows])
    agents = await _name_map(session, ScanAgent.name, [r.scan_agent_id for r in rows])
    masters = await _name_map(session, Subnet.cidr, [r.master_subnet_id for r in rows])
    vlans: dict[Any, dict[str, Any]] = {}
    vlan_ids = {r.vlan_id for r in rows if r.vlan_id}
    if vlan_ids:
        vlans = {vid: {"number": n, "name": nm} for vid, n, nm in (await session.execute(
            select(VLAN.id, VLAN.number, VLAN.name).where(in_values(VLAN.id, list(vlan_ids))))).all()}
    items = []
    for r in rows:
        try:
            size = host_count(ipaddress.ip_network(str(r.cidr), strict=False))
        except ValueError:
            size = 0
        used = used_map.get(r.id, 0)
        items.append({
            "id": str(r.id),
            "cidr": str(r.cidr),
            "description": r.description,
            "section_id": str(r.section_id),
            "section": secs.get(r.section_id),
            "master_subnet": str(masters[r.master_subnet_id]) if r.master_subnet_id in masters else None,
            "vlan": vlans.get(r.vlan_id),
            "vrf": vrfs.get(r.vrf_id),
            "customer_id": str(r.customer_id) if r.customer_id else None,
            "customer": custs.get(r.customer_id),
            "location": locs.get(r.location_id),
            "gateway": _host(r.gateway), "dns_servers": r.dns_servers,
            "is_pool": r.is_pool, "is_full": r.is_full, "threshold_pct": r.threshold_pct,
            "scan_enabled": r.scan_enabled, "scan_agent": agents.get(r.scan_agent_id),
            "anomaly_enabled": r.anomaly_enabled, "ai_audit_enabled": r.ai_audit_enabled,
            "archived": r.archived_at is not None,
            "used": used, "total": size, "free": max(size - used, 0),
            "used_pct": round(used / size * 100, 2) if size else 0.0,
        })
    return {"subnets": items, "count": len(items), "archived": archived,
            **_page(total_rows, len(items))}


async def get_subnet_usage(
    session: AsyncSession, *, user: User, subnet_id: str,
) -> dict[str, Any]:
    s = await session.get(Subnet, _as_uuid(subnet_id, "subnet_id"))
    if s is None:
        raise IPAMToolError("subnet not found")
    visible = set(await filter_visible(
        session, user=user, object_type="subnet",
        object_ids=[s.id], required="read",
    ))
    if s.id not in visible:
        raise IPAMToolError("subnet not found")   # 跟不存在同一句，不透露它在不在
    total, used, free, pct = await get_usage(session, s)
    return {
        "subnet_id": subnet_id, "cidr": str(s.cidr),
        "total": total, "used": used, "free": free, "used_pct": pct,
        # 使用率告警門檻（%）與手動標記的「已滿」：畫面上兩個都看得到
        "threshold_pct": s.threshold_pct, "is_full": s.is_full,
        "over_threshold": (pct >= s.threshold_pct) if s.threshold_pct is not None else None,
    }


async def _librenms_name(session: AsyncSession, ln_id: Any) -> str | None:
    if ln_id is None:
        return None
    ln = await session.get(LibreNMSDevice, ln_id)
    return (ln.sysname or ln.hostname) if ln else None


async def _vm_nics(session: AsyncSession, user: User, *, mac: str | None = None,
                   ip: str | None = None) -> list[dict[str, Any]]:
    """網卡 MAC 相符（或 guest agent 回報用了這個 IP）的 VM／CT：名稱、VMID、節點、叢集、平台、狀態、網卡。

    使用者 11-03：印表機的 IP 被一台 VM 衝了，AI 只說「廠商是 Proxmox、在上行埠後面」，沒說是哪台 ——
    系統明明有 VM 網卡的 MAC。VM 是全域資料（同 list_vms），只被授權部分物件的帳號不給。"""
    from app.models.virt import VirtCluster, VirtualMachine, VMInterface
    if not (mac or ip) or not await has_global_read(session, user):
        return []
    cond = VMInterface.mac == mac if mac else func.host(VMInterface.primary_ip) == ip
    rows = (await session.execute(
        select(VirtualMachine, VirtCluster.name, VirtCluster.type, VMInterface.name, VMInterface.mac,
               VMInterface.primary_ip, VMInterface.bridge)
        .join(VirtualMachine, VirtualMachine.id == VMInterface.vm_id)
        .join(VirtCluster, VirtCluster.id == VirtualMachine.cluster_id)
        .where(cond).order_by(VirtualMachine.name).limit(20))).all()
    return [{"vm_name": vm.name, "vmid": vm.legacy_vmid, "kind": vm.kind, "node": vm.node, "cluster": cname,
             "platform": ctype, "status": vm.status, "os_name": vm.os_name, "interface": iname,
             "mac": str(imac) if imac else None, "ip": _host(iip), "bridge": bridge}
            for vm, cname, ctype, iname, imac, iip, bridge in rows]


#: arp_macs 只看最近這段時間的 ARP（太久以前的對應多半是換過機器，不是現在的衝突）
_ARP_RECENT_DAYS = 7
#: 沒有記錄的位址：ARP 看最近 30 天（與未納管目擊的保留期限相同）
_ARP_UNREGISTERED_DAYS = 30


async def _arp_mac_summary(session: AsyncSession, user: User, rows: list[Any], *,
                           record_mac: str | None = None, with_record: bool = False,
                           limit: int = 10) -> list[dict[str, Any]]:
    """ARP 列（由新到舊）→ 每個 MAC 一筆：廠商、最早／最後看到、誰回報、來源、是哪台 VM 的網卡。

    IP 衝突而 VM 沒有 guest agent 時，答案就在這裡：印表機的位址，ARP 卻也看到一個 Proxmox 的 MAC →
    那是哪台 VM、在哪個節點（使用者 11-03）。呼叫端負責只給全域讀取的帳號（ARP 是全域資料）。"""
    from app.services.anomaly import reporter_names
    from app.services.unmanaged import usable_mac
    by_mac: dict[str, dict[str, Any]] = {}
    for a in rows:
        m = str(a.mac)
        if not usable_mac(m):
            continue                       # 全零、殘缺、廣播：不是某一台機器
        e = by_mac.get(m)
        if e is None:
            if len(by_mac) >= limit:
                continue
            e = by_mac[m] = {"mac": m, "first": a.first_seen_at, "last": a.last_seen_at,
                             "devs": set(), "sources": set()}
        if a.first_seen_at and (e["first"] is None or a.first_seen_at < e["first"]):
            e["first"] = a.first_seen_at
        if a.last_seen_at and (e["last"] is None or a.last_seen_at > e["last"]):
            e["last"] = a.last_seen_at
        e["sources"].add(str(a.source))
        if a.device_id:
            e["devs"].add(a.device_id)
    names = await reporter_names(session, {d for e in by_mac.values() for d in e["devs"]})
    out = []
    for m, e in by_mac.items():
        row: dict[str, Any] = {
            "mac": m, "vendor": await vendor_for_mac(session, m),
            "first_seen": _iso(e["first"]), "last_seen": _iso(e["last"]),
            # 哪幾台設備的 ARP 表看到的（LibreNMS）；掃描代理／防火牆的看 sources
            "seen_by": sorted(names.get(str(d), str(d)) for d in e["devs"]),
            "sources": sorted(e["sources"]),
            "vms": await _vm_nics(session, user, mac=m),
        }
        if with_record:
            row["is_record_mac"] = bool(record_mac) and m == str(record_mac).lower()
        out.append(row)
    return out


async def _arp_scope_for_record(session: AsyncSession, obj: IPAddress, ip: str, vis: Any) -> Any:
    """某筆 IP 記錄看得到的 ARP 列（重疊網段：另一筆同位址記錄的子網路掃到的不是這筆的）。

    受限帳號同 get_ip_history：只看這個子網路的列；沒有子網路歸屬的（LibreNMS）要這個子網路是唯一最細的容器。"""
    from app.services.unmanaged import sole_container
    if vis is not None:
        sole = await sole_container(session, obj.subnet_id, ip)
        return or_(ARPEntry.subnet_id == obj.subnet_id, and_(ARPEntry.subnet_id.is_(None), literal(sole)))
    twins = [sid for (sid,) in (await session.execute(
        select(IPAddress.subnet_id).where(IPAddress.ip == ip, IPAddress.subnet_id != obj.subnet_id)
        .limit(100))).all()]
    if not twins:
        return literal(True)
    from app.core.sqlin import not_in_values
    return or_(ARPEntry.subnet_id.is_(None), not_in_values(ARPEntry.subnet_id, twins))


async def _record_arp_macs(session: AsyncSession, user: User, obj: IPAddress, ip: str,
                           vis: Any) -> list[dict[str, Any]]:
    """這個 IP 最近 7 天在 ARP 上出現過的 MAC（最多 10 個），標出哪個是記錄上的、各是哪台 VM 的網卡。"""
    from datetime import timedelta
    if not await has_global_read(session, user):
        return []
    since = datetime.now(UTC) - timedelta(days=_ARP_RECENT_DAYS)
    rows = list((await session.execute(
        select(ARPEntry).where(ARPEntry.ip == ip, ARPEntry.last_seen_at >= since,
                               await _arp_scope_for_record(session, obj, ip, vis))
        .order_by(ARPEntry.last_seen_at.desc()).limit(200))).scalars().all())
    return await _arp_mac_summary(session, user, rows, record_mac=str(obj.mac) if obj.mac else None,
                                  with_record=True)


async def _unregistered(session: AsyncSession, user: User, ip: str) -> dict[str, Any]:
    """IPAM 沒有（看得到的）記錄的位址：系統還看到了什麼。

    以前 get_ip_detail／search_ip 只回「查無」，模型就說系統沒有這個位址的資料 —— 其實掃描代理目擊過、
    ARP 有它、VM 自己回報用了它、DHCP 有它的固定分配（2026-11-03 稽核）。
    目擊照子網路可見性；ARP、VM、DHCP 固定分配是全域資料，只給全域讀取（受限的子網路只看那些子網路的 ARP 列）。"""
    from datetime import timedelta

    from app.models.dhcp import DHCPReservation
    from app.models.unmanaged_sighting import UnmanagedSighting
    vis = await visible_ids(session, user=user, object_type="subnet")
    s_stmt = (select(UnmanagedSighting, Subnet.cidr).join(Subnet, Subnet.id == UnmanagedSighting.subnet_id)
              .where(UnmanagedSighting.ip == ip))
    if vis is not None:
        s_stmt = s_stmt.where(in_values(UnmanagedSighting.subnet_id, vis) if vis else sa_false())
    sightings = [{"subnet": str(cidr), "source": s.source, "mac": s.mac, "hostname": s.hostname,
                  "first_seen": _iso(s.first_seen_at), "last_seen": _iso(s.last_seen_at)}
                 for s, cidr in (await session.execute(
                     s_stmt.order_by(UnmanagedSighting.last_seen_at.desc()).limit(20))).all()]
    arp: list[dict[str, Any]] = []
    reservations: list[dict[str, Any]] = []
    if await has_global_read(session, user):
        since = datetime.now(UTC) - timedelta(days=_ARP_UNREGISTERED_DAYS)
        a_stmt = select(ARPEntry).where(ARPEntry.ip == ip, ARPEntry.last_seen_at >= since)
        if vis is not None:
            a_stmt = a_stmt.where(in_values(ARPEntry.subnet_id, vis) if vis else sa_false())
        arp = await _arp_mac_summary(session, user, list((await session.execute(
            a_stmt.order_by(ARPEntry.last_seen_at.desc()).limit(200))).scalars().all()))
        reservations = [{"hostname": r.hostname, "mac": r.mac, "source_type": r.source_type,
                         "source_name": r.source_name, "description": r.description}
                        for r in (await session.execute(
                            select(DHCPReservation).where(DHCPReservation.ip == ip).limit(20))).scalars().all()]
    return {"sightings": sightings, "arp": arp, "vms": await _vm_nics(session, user, ip=ip),
            "dhcp_reservations": reservations}


async def _maintenance(session: AsyncSession, object_type: str, object_id: Any) -> list[dict[str, Any]]:
    """目前涵蓋這個物件的維護（與物件頁的維護橫幅同一份判斷；看得到物件就看得到，同 REST）。"""
    from app.services.maintenance import active_for_object
    return [{"title": w.title, "behavior": w.behavior, "until": _iso(until)}
            for w, until in await active_for_object(session, object_type, object_id)]


async def trace_mac(
    session: AsyncSession, *, user: User, mac: str,
) -> dict[str, Any]:
    """從 MAC 反查 ARP（→ IP）+ FDB（→ switch port），列出**目前所有**目擊紀錄。

    以前各只取最新一筆：FDB 最新那筆常常是核心交換器的上行埠（背後幾十個 MAC），模型就把上行埠
    當成這台機器插的埠回答。現在每個 (交換器, 埠) 都列，算出那個埠背後有幾個 MAC，
    超過 topology.UPLINK_MAC_THRESHOLD 標 uplink、其餘標 access；`fdb` 是最可能的存取埠。
    """
    from app.models.mikrotik import MikroTikRouter
    from app.services.mac_history import normalize_mac
    from app.services.oui import vendor_for_mac
    from app.services.topology import UPLINK_MAC_THRESHOLD
    mac = normalize_mac(mac) or mac.lower().replace("-", ":")
    # RBAC：ARP 依其 IP 子網路可見性、FDB 依其 switch 裝置可見性遮蔽
    vis_sub = await visible_ids(session, user=user, object_type="subnet")
    vis_dev = await visible_ids(session, user=user, object_type="device")

    # ── ARP：這個 MAC 目前在哪些 IP 上（多台路由器／防火牆各自回報）
    arps = list((await session.execute(
        select(ARPEntry).where(ARPEntry.mac == mac).order_by(ARPEntry.last_seen_at.desc()).limit(100)
    )).scalars().all())
    if vis_sub is not None and arps:
        # 受限帳號：ARP 列有子網路歸屬就看那個子網路；沒有的（LibreNMS）要「這個位址的登錄紀錄全在看得到的
        # 子網路裡」才給 —— 重疊網段裡同一個位址可能是別的單位的機器（不猜；比以前任取一筆更嚴）
        ip_texts = sorted({_host(a.ip) or "" for a in arps if a.subnet_id is None})
        recs: dict[str, set[Any]] = {}
        if ip_texts:
            for rip, rsub in (await session.execute(
                    select(func.host(IPAddress.ip), IPAddress.subnet_id)
                    .where(in_values(func.host(IPAddress.ip), ip_texts, type_=String())))).all():
                recs.setdefault(str(rip), set()).add(rsub)

        def _arp_ok(a: Any) -> bool:
            if a.subnet_id is not None:
                return a.subnet_id in vis_sub
            subs = recs.get(_host(a.ip) or "")
            return bool(subs) and subs <= vis_sub
        arps = [a for a in arps if _arp_ok(a)]
    ln_names = await _name_map(session, LibreNMSDevice.sysname, [a.device_id for a in arps])
    ln_hosts = await _name_map(session, LibreNMSDevice.hostname, [a.device_id for a in arps])
    arp_rows = [{
        "ip": _host(a.ip),
        # 哪一台回報的（LibreNMS 裝置名稱；防火牆／掃描代理的 ARP 看 source）
        "seen_by": ln_names.get(a.device_id) or ln_hosts.get(a.device_id),
        "source": a.source, "interface": a.interface,
        "first_seen": _iso(a.first_seen_at), "last_seen_at": _iso(a.last_seen_at),
    } for a in arps]

    # ── FDB：每個 (交換器, 埠, VLAN, 來源) 一筆
    # ⚠️ FDB／ARP 的 device_id 是 **LibreNMS 的裝置**，要經 jt_ipam_device_id 才對得到使用者看得到的
    # jt-ipam 裝置。以前直接拿來比，非管理員永遠看不到交換器埠（2026-09-30 研究）。沒對映到 jt-ipam
    # 裝置的交換器無從判斷權限 → 非管理員一律遮蔽。MikroTik 回報的列（0170）直接記 switch_device_id。
    sw_dev = Device.__table__.alias("sw_dev")
    fdb_raw = (await session.execute(
        select(FDBEntry.device_id, FDBEntry.switch_device_id, FDBEntry.port_name, FDBEntry.vlan_id_num,
               FDBEntry.source, func.min(FDBEntry.first_seen_at), func.max(FDBEntry.last_seen_at),
               LibreNMSDevice.sysname, LibreNMSDevice.hostname, LibreNMSDevice.jt_ipam_device_id,
               sw_dev.c.name, MikroTikRouter.name)
        .outerjoin(LibreNMSDevice, LibreNMSDevice.id == FDBEntry.device_id)
        .outerjoin(sw_dev, sw_dev.c.id == FDBEntry.switch_device_id)
        .outerjoin(MikroTikRouter, MikroTikRouter.id == FDBEntry.mikrotik_router_id)
        .where(FDBEntry.mac == mac)
        .group_by(FDBEntry.device_id, FDBEntry.switch_device_id, FDBEntry.port_name, FDBEntry.vlan_id_num,
                  FDBEntry.source, LibreNMSDevice.sysname, LibreNMSDevice.hostname,
                  LibreNMSDevice.jt_ipam_device_id, sw_dev.c.name, MikroTikRouter.name)
        .order_by(func.max(FDBEntry.last_seen_at).desc()).limit(100))).all()
    seen = []
    for ln_id, ros_dev, port, vlan, source, first, last, sysname, lhost, ln_dev, ros_name, router in fdb_raw:
        jt_dev = ln_dev or ros_dev
        if vis_dev is not None and (jt_dev is None or jt_dev not in vis_dev):
            continue
        seen.append({"ln_id": ln_id, "ros_dev": ros_dev, "switch": sysname or lhost or ros_name or router,
                     "switch_device_id": str(jt_dev) if jt_dev else None, "port": port, "vlan": vlan,
                     "source": source, "first_seen": _iso(first), "last_seen": _iso(last)})
    # 每個埠背後幾個 MAC（一個 SQL）：分辨存取埠與上行埠
    port_macs: dict[tuple[Any, Any, Any], int] = {}
    ports = sorted({s["port"] for s in seen if s["port"]})
    if ports:
        ln_ids = [s["ln_id"] for s in seen if s["ln_id"]]
        ros_ids = [s["ros_dev"] for s in seen if s["ros_dev"] and not s["ln_id"]]
        conds = []
        if ln_ids:
            conds.append(in_values(FDBEntry.device_id, ln_ids))
        if ros_ids:
            conds.append(in_values(FDBEntry.switch_device_id, ros_ids))
        if conds:
            for did, sdid, port, n in (await session.execute(
                    select(FDBEntry.device_id, FDBEntry.switch_device_id, FDBEntry.port_name,
                           func.count(func.distinct(FDBEntry.mac)))
                    .where(in_values(FDBEntry.port_name, ports), or_(*conds))
                    .group_by(FDBEntry.device_id, FDBEntry.switch_device_id, FDBEntry.port_name))).all():
                port_macs[(did, None if did else sdid, port)] = int(n)
    fdb_rows = []
    for s in seen:
        n = port_macs.get((s["ln_id"], None if s["ln_id"] else s["ros_dev"], s["port"]))
        row = {k: v for k, v in s.items() if k not in ("ln_id", "ros_dev")}
        row["macs_on_port"] = n
        row["role"] = "uplink" if (n or 0) > UPLINK_MAC_THRESHOLD else "access"
        fdb_rows.append(row)
    access = sorted((r for r in fdb_rows if r["role"] == "access"),
                    key=lambda r: (r["macs_on_port"] or 0, -(datetime.fromisoformat(r["last_seen"]).timestamp()
                                                            if r["last_seen"] else 0)))
    best = access[0] if access else (fdb_rows[0] if fdb_rows else None)

    # ── 這個 MAC 是誰：IP 記錄（照子網路可見性）、裝置自己的連接埠（照裝置可見性）、DHCP 固定分配（全域）
    from app.services.mac_history import device_ports_for_mac, dhcp_reservations_for_mac
    rec_stmt = (select(IPAddress, Subnet.cidr).join(Subnet, Subnet.id == IPAddress.subnet_id)
                .where(IPAddress.mac == mac))
    if vis_sub is not None:
        rec_stmt = rec_stmt.where(in_values(IPAddress.subnet_id, vis_sub) if vis_sub else sa_false())
    recs = (await session.execute(rec_stmt.order_by(IPAddress.ip).limit(50))).all()
    rec_devs = await _visible_device_names(session, user, [r.device_id for r, _c in recs])
    rec_custs = await _name_map(session, Customer.name, [r.customer_id for r, _c in recs])
    ipam_records = [{"ip": _host(r.ip), "subnet": str(cidr), "hostname": r.hostname,
                     "effective_status": r.effective_status, "owner": r.owner,
                     "device": rec_devs.get(r.device_id), "customer": rec_custs.get(r.customer_id)}
                    for r, cidr in recs]
    device_ports = await device_ports_for_mac(
        session, mac, dev_ok=lambda did: vis_dev is None or (did is not None and did in vis_dev))
    dhcp_res = await dhcp_reservations_for_mac(session, mac) if await has_global_read(session, user) else []
    return {
        "mac": mac,
        "vendor": await vendor_for_mac(session, mac),
        # 相容舊欄位：最新的一筆 ARP、最可能的存取埠
        "arp": arp_rows[0] if arp_rows else None,
        "fdb": ({"switch": best["switch"], "switch_device_id": best["switch_device_id"],
                 "port_name": best["port"], "vlan_id_num": best["vlan"], "source": best["source"],
                 "first_seen": best["first_seen"], "last_seen_at": best["last_seen"],
                 "macs_on_port": best["macs_on_port"], "role": best["role"]} if best else None),
        "arp_sightings": arp_rows,
        "fdb_sightings": fdb_rows,
        # 這個 MAC 是哪台 VM／CT 的網卡（名稱、節點、叢集…）：上行埠後面的機器常常就是虛擬機
        "vms": await _vm_nics(session, user, mac=mac),
        # IPAM 裡用這個 MAC 的記錄、是哪台裝置自己的連接埠、DHCP 固定分配
        "ipam_records": ipam_records,
        "device_ports": device_ports,
        "dhcp_reservations": dhcp_res,
        "note": (f"vms lists the virtual machine whose NIC has this MAC (answer with its name and node); "
                 "ipam_records, device_ports and dhcp_reservations also say whose MAC it is. "
                 f"fdb is the most likely access port. A port with more than {UPLINK_MAC_THRESHOLD} MACs "
                 "behind it is an uplink/trunk (role=uplink) — the device is not plugged into it. "
                 "For the full history of this MAC use mac_history."),
    }


async def mac_history(session: AsyncSession, *, user: User, mac: str) -> dict[str, Any]:
    """以 MAC 為中心的完整歷程：用過哪些 IP（起訖時間）、何時被誰取代、出現在哪台交換器的哪個埠、
    DHCP 固定分配、是哪台裝置或虛擬機的網卡，以及隨機 MAC 輪替時可能是同一台的其他 MAC。
    依使用者的可見範圍縮放。"""
    from app.services.mac_history import mac_history as _history
    try:
        out = await _history(session, user=user, mac=mac, ips_limit=100, events_limit=60)
    except ValueError as exc:
        raise IPAMToolError("not a valid MAC address") from exc
    # 上線依據是給畫面畫燈的原始時間，AI 用不到，省 token
    for r in out["ips"]:
        r.pop("live", None)
    return out


async def list_vlans(
    session: AsyncSession, *, user: User, number: int | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """VLAN 清單：VID、名稱、網域、單位、區段、說明、掛在這個 VLAN 的子網路數。"""
    from app.models.vlan import VLANDomain
    limit = max(1, min(int(limit), 500))
    stmt = select(VLAN)
    if number is not None:
        stmt = stmt.where(VLAN.number == number)
    total = await _count(session, stmt)
    rows = list((await session.execute(stmt.order_by(VLAN.number, VLAN.name).limit(limit))).scalars().all())
    doms = await _name_map(session, VLANDomain.name, [r.domain_id for r in rows])
    custs = await _name_map(session, Customer.name, [r.customer_id for r in rows])
    secs = await _name_map(session, Section.name, [r.section_id for r in rows])
    sub_n: dict[Any, int] = {}
    if rows:
        sub_n = {vid: int(n) for vid, n in (await session.execute(
            select(Subnet.vlan_id, func.count()).where(in_values(Subnet.vlan_id, [r.id for r in rows]))
            .group_by(Subnet.vlan_id))).all()}
    return {
        "vlans": [
            {
                "id": str(r.id), "domain_id": str(r.domain_id), "domain": doms.get(r.domain_id),
                "number": r.number, "name": r.name, "description": r.description,
                "customer": custs.get(r.customer_id), "section": secs.get(r.section_id),
                "subnet_count": sub_n.get(r.id, 0),
            }
            for r in rows
        ],
        "count": len(rows),
        **_page(total, len(rows)),
    }


async def check_dns_consistency(
    session: AsyncSession, *, user: User,
) -> dict[str, Any]:
    """彙整 DNS 與 IPAM 資料一致性狀態（呼叫前需先跑 sync_server）：總數、各狀態、各 DNS 伺服器，
    以及最多 20 筆不一致的例子（與 GET /dns/consistency、/consistency/inconsistent 同一個範圍）。"""
    from app.models.dns import DNSServer, DNSZone
    # 只看 IPAM 有對應的類型（與 GET /dns/consistency 一致）
    ipam_types = DNSRecord.type.in_(("A", "AAAA", "PTR"))
    rows = (
        await session.execute(
            select(DNSRecord.consistency_state, func.count())
            .where(ipam_types)
            .group_by(DNSRecord.consistency_state)
        )
    ).all()
    summary = {state: int(cnt) for state, cnt in rows}
    by_server: dict[str, dict[str, int]] = {}
    for srv, state, cnt in (await session.execute(
        select(DNSServer.name, DNSRecord.consistency_state, func.count())
        .join(DNSZone, DNSZone.id == DNSRecord.zone_id).join(DNSServer, DNSServer.id == DNSZone.server_id)
        .where(ipam_types).group_by(DNSServer.name, DNSRecord.consistency_state)
    )).all():
        by_server.setdefault(srv, {})[state] = int(cnt)
    bad = (select(DNSRecord, DNSZone.name, DNSServer.name)
           .join(DNSZone, DNSZone.id == DNSRecord.zone_id).join(DNSServer, DNSServer.id == DNSZone.server_id)
           .where(ipam_types, DNSRecord.consistency_state != "consistent"))
    bad_total = sum(n for s, n in summary.items() if s != "consistent")
    examples = (await session.execute(bad.order_by(DNSServer.name, DNSRecord.name).limit(20))).all()
    return {
        "total": sum(summary.values()), "summary": summary,
        "inconsistent_total": bad_total, "truncated": bad_total > len(examples),
        "by_server": [{"server": s, "states": st} for s, st in sorted(by_server.items())],
        "inconsistent_examples": [{
            "name": r.name, "type": r.type, "value": r.value, "state": r.consistency_state,
            "zone": zone, "server": srv,
        } for r, zone, srv in examples],
        "note": ("States: consistent = DNS and IPAM agree; dns_only = the record exists in DNS but IPAM has no "
                 "matching address; ipam_only = IPAM has it but DNS has no record; mismatch = both exist but "
                 "the name or address differs. Only A/AAAA/PTR records are compared."),
    }


async def stats_overview(session: AsyncSession, *, user: User) -> dict[str, Any]:
    """各類實體的總數。逐物件計數依使用者可見範圍縮放；全域基礎設施計數僅 admin/萬用讀取者可見。"""
    async def _all(model) -> int:  # type: ignore[no-untyped-def]
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)

    async def _scoped(model, vis) -> int:  # type: ignore[no-untyped-def]
        if vis is None:
            return await _all(model)
        if not vis:
            return 0
        return int(await session.scalar(
            select(func.count()).select_from(model).where(in_values(model.id, vis))) or 0)

    vis_sec = await visible_ids(session, user=user, object_type="section")
    vis_sub = await visible_ids(session, user=user, object_type="subnet")
    vis_dev = await visible_ids(session, user=user, object_type="device")
    vis_rack = await visible_ids(session, user=user, object_type="rack")
    vis_loc = await visible_ids(session, user=user, object_type="location")
    vis_cust = await visible_ids(session, user=user, object_type="customer")
    # IP 數依可見子網路縮放（IPAddress 本身不可逐物件授權，靠所屬子網路）
    if vis_sub is None:
        ip_n = await _all(IPAddress)
    elif not vis_sub:
        ip_n = 0
    else:
        ip_n = int(await session.scalar(
            select(func.count()).select_from(IPAddress)
            .where(in_values(IPAddress.subnet_id, vis_sub))) or 0)

    out: dict[str, Any] = {
        "sections": await _scoped(Section, vis_sec),
        "subnets": await _scoped(Subnet, vis_sub),
        "ip_addresses": ip_n,
        "devices": await _scoped(Device, vis_dev),
        "racks": await _scoped(Rack, vis_rack),
        "locations": await _scoped(Location, vis_loc),
        "customers": await _scoped(Customer, vis_cust),
    }
    # 全域基礎設施計數：僅 admin / 萬用讀取者
    if await has_global_read(session, user):
        from app.models.advanced import ASN, Circuit, Contact, Provider, Tenant
        from app.models.physical import Cable, VPNTunnel
        from app.models.virt import VirtualMachine
        from app.models.vrf import VRF
        out.update({
            "vlans": await _all(VLAN),
            "vrfs": await _all(VRF),
            "vpn_tunnels": await _all(VPNTunnel),
            "nat_rules": await _all(NATTranslation),
            "vms": await _all(VirtualMachine),
            "circuits": await _all(Circuit),
            "providers": await _all(Provider),
            "asns": await _all(ASN),
            "tenants": await _all(Tenant),
            "contacts": await _all(Contact),
            "cables": await _all(Cable),
        })
    return out


async def list_racks(
    session: AsyncSession, *, user: User, limit: int = 200, location_id: str | None = None,
) -> dict[str, Any]:
    """列出機櫃／層架（含所在地點、已掛裝置、每一列還剩多少空間）。問某機房要帶 location_id。

    **不是每一種機架都以 U 計**：層架（一般／鍍鉻／木質，如 IKEA IVAR）以「層」為單位，
    層高可以逐層不同，而且最上面那片板的**上面**還能再放一排。

    而且同一列可以左右並排、也可以上下疊放好幾台。只看「這一列有沒有東西」會把半滿的
    那一層算成滿的，「還能放幾台」就答錯了 —— 所以這裡額外回 `rows_with_space`（哪幾列
    還有空、還剩多少比例）與每台裝置的橫向／層內位置。
    """
    limit = max(1, min(int(limit), 500))
    stmt = (select(Rack, Location.name)
            .outerjoin(Location, Location.id == Rack.location_id))
    scope = "all"
    if location_id:
        stmt = stmt.where(Rack.location_id == _as_uuid(location_id, "location_id"))
        scope = f"location:{location_id}"
    # 可見性推進 SQL（同 list_devices：先截斷再過濾會漏資料且總數失真）
    vis = await visible_ids(session, user=user, object_type="rack")
    if vis is not None:
        stmt = stmt.where(in_values(Rack.id, vis) if vis else sa_false())
    total = int(await session.scalar(
        select(func.count()).select_from(stmt.subquery())) or 0)
    # 排序與畫面並排顯示一致：排序編號小的在左（沒有編號排最後），同編號再依名稱
    rows = (await session.execute(
        stmt.order_by(Rack.seq.asc().nulls_last(), Rack.name).limit(limit))).all()
    from app.services.rack import (
        RACK_SLOTS,
        has_open_top,
        level_heights_mm,
        placeable_levels,
        uses_rack_units,
    )
    # 所有機櫃的裝置一次查完（以前一櫃一個 SQL，五百櫃就是五百次）
    devs_by_rack: dict[Any, list[Any]] = {}
    if rows:
        for rid, *dev in (await session.execute(
            select(Device.rack_id, Device.name, Device.type, Device.u_position, Device.u_size,
                   Device.rack_face, Device.rack_slot, Device.rack_slot_span,
                   Device.rack_vslot, Device.rack_vslot_span)
            .where(in_values(Device.rack_id, [r.id for r, _n in rows]))
            .order_by(Device.rack_id, Device.u_position)
        )).all():
            devs_by_rack.setdefault(rid, []).append(tuple(dev))
    out = []
    for rack, loc_name in rows:
        devs = devs_by_rack.get(rack.id, [])
        kind = getattr(rack, "kind", None)
        levels = not uses_rack_units(kind)
        # 層架最上面那片板的上面也放得下 → 可放的位置比層數多一列
        placeable = placeable_levels(kind, int(rack.u_height or 0))
        # 每一列被佔掉多少「面積」：橫向比例 × 層內上下比例。只數「有沒有東西」會把
        # 一層放了一台半寬裝置的情況當成整層滿了。
        area: dict[int, float] = {}
        for (_n, _t, pos, sz, _f, _hs, hsp, _vs, vsp) in devs:
            if pos is None:
                continue
            frac = ((float(hsp or RACK_SLOTS) / RACK_SLOTS)
                    * (float(vsp or RACK_SLOTS) / RACK_SLOTS))
            for u in range(int(pos), int(pos) + int(sz or 1)):
                area[u] = min(1.0, area.get(u, 0.0) + frac)
        occupied_rows = set(area)
        used_u = len(occupied_rows)
        free_u = max(placeable - used_u, 0)
        # 完全空的列（給「還能放多大的裝置」參考）
        free_rows = sorted(set(range(1, placeable + 1)) - occupied_rows)
        out.append({
            "id": str(rack.id), "name": rack.name, "u_height": rack.u_height,
            # 型態決定單位與畫法：標準／工業機櫃以 U 計，三種層架以「層」計
            "kind": kind, "uses_levels": levels,
            "rows_label": "level" if levels else "U",
            "open_top": has_open_top(kind),
            "placeable_rows": placeable,
            "level_heights_mm": (
                [int(x) for x in level_heights_mm(kind, rack.row_height_mm,
                                                  rack.level_heights, int(rack.u_height or 0))]
                if levels else None),
            "location": loc_name, "device_count": len(devs),
            "used_u": used_u, "free_u": free_u, "free_u_rows": free_rows,
            # 還有空位、但不是全空的那幾列（一層並排／疊放多台時最需要知道的就是這個）
            "rows_with_space": [
                {"row": u, "free_fraction": round(1.0 - a, 3)}
                for u, a in sorted(area.items()) if a < 0.999
            ],
            "devices": [
                {"name": n, "type": t, "u_position": pos, "u_size": sz, "rack_face": f,
                 # 橫向：起始格與跨幾格（整列＝0/60）；層內上下：同一套 60 格
                 "rack_slot": hs, "rack_slot_span": hsp,
                 "rack_vslot": vs, "rack_vslot_span": vsp}
                for (n, t, pos, sz, f, hs, hsp, vs, vsp) in devs
            ],
            "description": rack.description,
            # 編輯表單的其餘欄位：排序編號、U 編號方向、示意圖呈現面、表面顏色、實體尺寸（mm）、
            # 層板厚度與離地高度（層架）、是否對外公開示意圖（嵌入 SVG）
            "location_id": str(rack.location_id) if rack.location_id else None,
            "seq": rack.seq, "numbering": rack.numbering, "face": rack.face, "finish": rack.finish,
            "width_mm": rack.width_mm, "depth_mm": rack.depth_mm, "row_height_mm": rack.row_height_mm,
            "board_mm": rack.board_mm, "floor_mm": rack.floor_mm, "expose_svg": bool(rack.expose_svg),
        })
    return {"scope": scope, "count": total, **_page(total, len(out)), "racks": out,
            "slots_per_row": RACK_SLOTS,
            "note": ("A row can hold several devices side by side (rack_slot/rack_slot_span) "
                     "and stacked within the row (rack_vslot/rack_vslot_span), both on a "
                     f"{RACK_SLOTS}-cell grid. Shelf kinds are counted in levels, not U — "
                     "use rows_label. Check rows_with_space before saying a row is full.")}


async def list_locations(
    session: AsyncSession, *, user: User, limit: int = 200,
) -> dict[str, Any]:
    """列出地點（機房）：地址、座標、單位、有沒有平面圖、機櫃數、裝置數。

    可見性推進 SQL：以前先 LIMIT 再過濾，受限帳號看得到的地點只要排在後面就消失。
    """
    limit = max(1, min(int(limit), 500))
    stmt = select(Location)
    vis = await visible_ids(session, user=user, object_type="location")
    if vis is not None:
        stmt = stmt.where(in_values(Location.id, vis) if vis else sa_false())
    total = await _count(session, stmt)
    rows = list((await session.execute(stmt.order_by(Location.name).limit(limit))).scalars().all())
    ids = [loc.id for loc in rows]
    rack_n: dict[Any, int] = {}
    dev_n: dict[Any, int] = {}
    if ids:
        rack_n = {lid: int(n) for lid, n in (await session.execute(
            select(Rack.location_id, func.count()).where(in_values(Rack.location_id, ids))
            .group_by(Rack.location_id))).all()}
        dev_n = {lid: int(n) for lid, n in (await session.execute(
            select(Device.location_id, func.count()).where(in_values(Device.location_id, ids))
            .group_by(Device.location_id))).all()}
    custs = await _name_map(session, Customer.name, [loc.customer_id for loc in rows])
    out = [{
        "id": str(loc.id), "name": loc.name, "address": loc.address,
        "latitude": float(loc.latitude) if loc.latitude is not None else None,
        "longitude": float(loc.longitude) if loc.longitude is not None else None,
        "rack_count": rack_n.get(loc.id, 0), "device_count": dev_n.get(loc.id, 0),
        "customer_id": str(loc.customer_id) if loc.customer_id else None,
        "customer": custs.get(loc.customer_id),
        "has_floor_plan": bool(loc.floor_plan_path),
        "description": loc.description,
    } for loc in rows]
    return {"locations": out, "count": len(out), **_page(total, len(out))}


async def list_devices(
    session: AsyncSession, *, user: User,
    name: str | None = None, type: str | None = None, limit: int = 100,
    location_id: str | None = None, rack_id: str | None = None,
) -> dict[str, Any]:
    """列出/搜尋裝置（可給 name 子字串或 type 過濾）：畫面「裝置」清單的欄位（主要 IP、類型、虛實、
    製造商、型號、序號、地點、機櫃、單位、說明）＋機櫃位置與 IP 數。其餘（各 IP 明細、VLAN、連接埠、
    電源、LibreNMS／Wazuh／虛擬化／OCS）在 get_device。

    問「某機房／某機櫃有哪些裝置」要帶 location_id / rack_id，否則回的是全站裝置。
    """
    from app.models.virt import VirtualMachine
    limit = max(1, min(int(limit), 500))
    stmt = select(Device)
    if name:
        stmt = stmt.where(Device.name.ilike(f"%{name}%"))
    if type:
        stmt = stmt.where(Device.type == type)
    scope = "all"
    if location_id:
        stmt = stmt.where(Device.location_id == _as_uuid(location_id, "location_id"))
        scope = f"location:{location_id}"
    if rack_id:
        stmt = stmt.where(Device.rack_id == _as_uuid(rack_id, "rack_id"))
        scope = f"rack:{rack_id}"
    # 可見性推進 SQL：先截斷再過濾會讓受限帳號拿到不足 limit 的結果，
    # 且總數會把看不到的也算進去
    vis = await visible_ids(session, user=user, object_type="device")
    if vis is not None:
        stmt = stmt.where(in_values(Device.id, vis) if vis else sa_false())
    total = int(await session.scalar(
        select(func.count()).select_from(stmt.subquery())) or 0)
    rows = list((await session.execute(stmt.order_by(Device.name).limit(limit))).scalars().all())
    # 以下全部批次查（以前每台一個 SQL 算 IP 數）
    ids = [d.id for d in rows]
    ip_n: dict[Any, int] = {}
    if ids:
        ip_n = {did: int(n) for did, n in (await session.execute(
            select(IPAddress.device_id, func.count()).where(in_values(IPAddress.device_id, ids))
            .group_by(IPAddress.device_id))).all()}
    custs = await _name_map(session, Customer.name, [d.customer_id for d in rows])
    locs = await _name_map(session, Location.name, [d.location_id for d in rows])
    racks = await _name_map(session, Rack.name, [d.rack_id for d in rows])
    # 主要 IP 與畫面同一套解析：primary_ip → LibreNMS 管理 IP → 名稱本身是 IP
    from app.api.v1.endpoints.devices import _resolve_device_ips
    ip_map = await _resolve_device_ips(session, rows)
    # 虛擬／實體：與畫面同一個判斷（有同名的虛擬機），只查這一頁的名稱
    lowered = sorted({(d.name or "").strip().lower() for d in rows if d.name})
    vm_names: set[str] = set()
    if lowered:
        vm_names = {str(n) for n in (await session.execute(
            select(func.lower(VirtualMachine.name))
            .where(in_values(func.lower(VirtualMachine.name), lowered, type_=String())))).scalars().all()}
    out = []
    for d in rows:
        out.append({
            "id": str(d.id), "name": d.name, "type": d.type, "fqdn": d.fqdn,
            "primary_ip": ip_map.get(d.id),
            "is_virtual": (d.name or "").strip().lower() in vm_names,
            "vendor": d.vendor, "model": d.model, "serial": d.serial,
            "description": d.description, "ip_count": ip_n.get(d.id, 0),
            "customer": custs.get(d.customer_id),
            "location": locs.get(d.location_id),
            "rack": racks.get(d.rack_id),
            "u_position": d.u_position, "u_size": d.u_size, "rack_face": d.rack_face,
            # 同一列可以左右並排、也可以上下疊放：少了這四個欄位，同一層的兩台在
            # AI 眼裡是同一個位置（60 格網格，整列＝0/60）
            "rack_slot": d.rack_slot, "rack_slot_span": d.rack_slot_span,
            "rack_vslot": d.rack_vslot, "rack_vslot_span": d.rack_vslot_span,
            "rack_id": str(d.rack_id) if d.rack_id else None,
        })
    return {"scope": scope, "count": total, **_page(total, len(out)), "devices": out}


async def get_device(
    session: AsyncSession, *, user: User,
    device_id: str | None = None, name: str | None = None,
) -> dict[str, Any]:
    """裝置詳細資料：畫面「裝置詳細資料」頁的每一塊 —— 基本欄位、主要 IP、虛實、每個 IP 的明細、
    VLAN（LibreNMS）、機櫃位置、連接埠、電源埠、LibreNMS 資料、Wazuh／虛擬化／OCS／RustDesk 整合。

    依名稱查：只在看得到的裝置裡找完全同名（不分大小寫）；同名不只一台時**不挑**，回候選清單要模型改帶 device_id
    （以前 limit(1) 任取一台，答的可能是另一台機器）。
    """
    vis = await visible_ids(session, user=user, object_type="device")
    dev: Device | None = None
    if device_id:
        dev = await session.get(Device, _as_uuid(device_id, "device_id"))
        if dev is not None and vis is not None and dev.id not in vis:
            dev = None                                   # 不洩漏不可見裝置
    elif name:
        want = name.strip()

        def _scoped(st: Any) -> Any:
            return st if vis is None else st.where(in_values(Device.id, vis) if vis else sa_false())
        cands = list((await session.execute(_scoped(
            select(Device).where(func.lower(Device.name) == want.lower())
        ).order_by(Device.name).limit(21))).scalars().all())
        exact = True
        if not cands:
            # 沒有完全同名：列出名稱包含它的（最多 10 台）讓模型挑，不自己猜
            like = want.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            cands = list((await session.execute(_scoped(
                select(Device).where(Device.name.ilike(f"%{like}%", escape="\\"))
            ).order_by(Device.name).limit(10))).scalars().all())
            exact = False
        if len(cands) == 1 and exact:
            dev = cands[0]
        elif cands:
            from app.api.v1.endpoints.devices import _resolve_device_ips
            ipm = await _resolve_device_ips(session, cands)
            locs = await _name_map(session, Location.name, [c.location_id for c in cands])
            racks = await _name_map(session, Rack.name, [c.rack_id for c in cands])
            return {
                "ambiguous": True, "name": want, "exact_match": exact,
                "candidates": [{"id": str(c.id), "name": c.name, "type": c.type, "primary_ip": ipm.get(c.id),
                                "serial": c.serial, "location": locs.get(c.location_id),
                                "rack": racks.get(c.rack_id)} for c in cands[:20]],
                "note": ("Several devices are named like this; do not pick one — ask the user which, "
                         "or call get_device again with device_id." if exact else
                         "No device has exactly this name; these names contain it. Call get_device with device_id."),
            }
    if dev is None:
        raise IPAMToolError("device not found")
    # 這台裝置的 IP 只列看得到的（以前全部回，連看不到的子網路裡的位址、主機名稱、MAC 都在；
    # REST 的裝置詳細資料只給主要 IP，2026-10-07 稽核）
    ip_stmt = (select(IPAddress, Subnet.cidr).join(Subnet, Subnet.id == IPAddress.subnet_id)
               .where(IPAddress.device_id == dev.id))
    vis_ip = await visible_ids(session, user=user, object_type="ip")
    ips: list[Any] = []
    if vis_ip is None or vis_ip:
        if vis_ip is not None:
            ip_stmt = ip_stmt.where(in_values(IPAddress.id, vis_ip))
        ips = list((await session.execute(ip_stmt.order_by(IPAddress.ip).limit(500))).all())
    # VLAN（透過連結的 librenms device）是全域資料：要全域讀取
    vlans: list[Any] = []
    if await has_global_read(session, user):
        vlans = list((await session.execute(
            select(VLAN.number, VLAN.name)
            .join(DeviceVLAN, DeviceVLAN.vlan_id == VLAN.id)
            .join(LibreNMSDevice, LibreNMSDevice.id == DeviceVLAN.librenms_device_id)
            .where(LibreNMSDevice.jt_ipam_device_id == dev.id)
            .distinct()
        )).all())
    cust = await session.get(Customer, dev.customer_id) if dev.customer_id else None
    loc = await session.get(Location, dev.location_id) if dev.location_id else None
    rack_info = None
    if dev.rack_id:
        rk = await session.get(Rack, dev.rack_id)
        if rk is not None:
            from app.services.rack import placeable_levels, uses_rack_units
            rk_loc = loc if (loc is not None and loc.id == rk.location_id) else (
                await session.get(Location, rk.location_id) if rk.location_id else None)
            rack_info = {
                "id": str(rk.id), "name": rk.name, "u_height": rk.u_height,
                "location": rk_loc.name if rk_loc else None,
                # 型態決定單位：層架以「層」計，說成 U 就錯了
                "kind": getattr(rk, "kind", None),
                "uses_levels": not uses_rack_units(getattr(rk, "kind", None)),
                "placeable_rows": placeable_levels(getattr(rk, "kind", None),
                                                   int(rk.u_height or 0)),
            }
    # 主要 IP 與虛實：與 REST 裝置詳細資料同一套判斷
    from app.api.v1.endpoints.devices import _resolve_device_ips
    from app.models.physical import DevicePort, DevicePowerPort, PowerOutlet
    from app.models.virt import VirtualMachine
    from app.services.fw_lookup import vm_match_for
    primary_ip = (await _resolve_device_ips(session, [dev])).get(dev.id)
    is_virtual = bool(await session.scalar(
        select(VirtualMachine.id).where(func.lower(VirtualMachine.name) == (dev.name or "").strip().lower())
        .limit(1)))
    port_macs = [str(m) for (m,) in (await session.execute(
        select(DevicePort.mac_address).where(DevicePort.device_id == dev.id,
                                             DevicePort.mac_address.is_not(None)).limit(10))).all()]
    virt_vm = await vm_match_for(session, ip=primary_ip, macs=port_macs or None)
    if virt_vm:
        is_virtual = True
    # 電源埠 ↔ 插座（NetBox 風）
    pports = list((await session.execute(
        select(DevicePowerPort).where(DevicePowerPort.device_id == dev.id)
    )).scalars().all())
    outlets = await _name_map(session, PowerOutlet.label, [pp.outlet_id for pp in pports])
    power_ports = [{"name": pp.name, "max_watts": pp.max_watts, "outlet": outlets.get(pp.outlet_id)}
                   for pp in pports]
    # 連接埠（裝置頁「連接埠」面板）：名稱、類型、MAC、說明、接到哪台的哪個埠（對端看不到就不給名稱）
    port_total = int(await session.scalar(
        select(func.count()).select_from(DevicePort).where(DevicePort.device_id == dev.id)) or 0)
    peer = DevicePort.__table__.alias("peer")
    port_rows = (await session.execute(
        select(DevicePort.name, DevicePort.type, DevicePort.mac_address, DevicePort.description,
               peer.c.name, peer.c.device_id)
        .outerjoin(peer, peer.c.id == DevicePort.peer_port_id)
        .where(DevicePort.device_id == dev.id)
        .order_by(DevicePort.position.asc().nulls_last(), DevicePort.name).limit(200))).all()
    peer_devs = await _visible_device_names(session, user, [r[5] for r in port_rows])
    ports = [{"name": n, "type": t, "mac": m, "description": d,
              "peer_device": peer_devs.get(pd), "peer_port": pn if pd in peer_devs else None}
             for n, t, m, d, pn, pd in port_rows]
    # LibreNMS 與其他整合（與 REST /devices/{id}/librenms、/integrations 同一份資料、同一層權限：裝置可讀）
    from app.api.v1.endpoints.devices import get_device_integrations, get_device_librenms
    librenms = await get_device_librenms(device_id=dev.id, _user=user, session=session)
    integrations = await get_device_integrations(device_id=dev.id, _user=user, session=session)
    if integrations.get("ocs"):
        integrations["ocs"].pop("hw", None)      # 硬體明細很長；要看找 get_ip_detail 的 ocs_hardware
    return {
        "id": str(dev.id), "name": dev.name, "type": dev.type, "type_source": dev.type_source,
        "fqdn": dev.fqdn, "primary_ip": primary_ip, "is_virtual": is_virtual, "virt_vm": virt_vm,
        "vendor": dev.vendor, "model": dev.model, "serial": dev.serial,
        "description": dev.description,
        "customer": cust.name if cust else None,
        "location": loc.name if loc else None,
        # 機櫃 U 位資訊（讓 AI 能判斷占位 / 剩餘空間）
        "u_position": dev.u_position, "u_size": dev.u_size, "rack_face": dev.rack_face,
        # 列內的橫向與上下位置（60 格網格，整列＝0/60）
        "rack_slot": dev.rack_slot, "rack_slot_span": dev.rack_slot_span,
        "rack_vslot": dev.rack_vslot, "rack_vslot_span": dev.rack_vslot_span,
        "rack": rack_info,
        "custom_fields": dev.custom_fields or None,
        **await _timestamps(session, Device, dev.id),
        "ips": [{"ip": _host(o.ip), "subnet": str(cidr), "hostname": o.hostname,
                 "mac": str(o.mac) if o.mac else None, "state": o.state,
                 "effective_status": o.effective_status, "switch_port": o.switch_port,
                 "description": o.description, "note": o.note, "last_seen": _ip_last_seen(o),
                 "is_primary": o.id == dev.primary_ip_id}
                for o, cidr in ips],
        "vlans": [{"number": n, "name": nm} for n, nm in vlans],
        "ports": ports, "port_count": port_total, "ports_truncated": port_total > len(ports),
        "power_ports": power_ports,
        # 目前涵蓋這台裝置的維護（維護中不告警）
        "maintenance": await _maintenance(session, "device", dev.id),
        # 上面跑的 VM、接的電路、VPN 通道、向供應商租用（全域資料）
        **await _device_infra(session, user, dev),
        "librenms": librenms,
        "integrations": integrations,
    }


async def _device_infra(session: AsyncSession, user: User, dev: Device) -> dict[str, Any]:
    """裝置頁之外、但跟這台裝置有關的全域資料：上面跑的 VM（虛擬化主機）、電路、VPN 通道、租用設備。

    以前問「這台 PVE 節點上有哪些 VM」「這台路由器接哪條電路、要不要歸還」，get_device 都沒有，模型就說沒有。
    VM／電路／VPN／租用的清單工具都要全域讀取，這裡同一層：只被授權部分物件的帳號拿到空的（count 是 None＝不知道）。"""
    empty: dict[str, Any] = {"hosted_vm_count": None, "hosted_vms": [], "circuits": [], "vpn_tunnels": [],
                             "lease": None}
    if not await has_global_read(session, user):
        return empty
    from app.models.advanced import Circuit, DeviceLease, Provider
    from app.models.physical import VPNTunnel
    from app.models.virt import VirtCluster, VirtualMachine
    # 跑在這台上的 VM：同 relation_chain.vm_host_device —— VM 記錄連到這台，或（沒連時）節點名稱＝裝置名稱／FQDN；
    # 跟裝置同名的那台是這台自己（裝置記錄的是 VM 的客體），不算
    own = (dev.name or "").strip().lower()
    names = [n for n in {own, (dev.fqdn or "").strip().lower()} if n]
    cond = VirtualMachine.device_id == dev.id
    if names:
        cond = or_(cond, and_(VirtualMachine.device_id.is_(None),
                              func.lower(VirtualMachine.node).in_(names)))  # bounded: 名稱與 FQDN 兩個
    vm_stmt = select(VirtualMachine).where(cond, func.lower(VirtualMachine.name) != own)
    vm_total = await _count_rows(session, vm_stmt)
    vms = list((await session.execute(vm_stmt.order_by(VirtualMachine.name).limit(50))).scalars().all())
    vm_ips = await _name_map(session, IPAddress.ip, [v.primary_ip_id for v in vms])
    clusters = await _name_map(session, VirtCluster.name, [v.cluster_id for v in vms])
    hosted = [{"name": v.name, "vmid": v.legacy_vmid, "kind": v.kind, "status": v.status,
               "cluster": clusters.get(v.cluster_id), "primary_ip": _host(vm_ips.get(v.primary_ip_id))}
              for v in vms]
    circuits = [{"cid": c.cid, "provider": pname, "status": c.status, "ip_address": c.ip_address,
                 "contract_end_date": _date_only(c.contract_end_date)}
                for c, pname in (await session.execute(
                    select(Circuit, Provider.name).outerjoin(Provider, Provider.id == Circuit.provider_id)
                    .where(Circuit.device_id == dev.id).order_by(Circuit.cid).limit(50))).all()]
    tunnels = list((await session.execute(
        select(VPNTunnel).where(or_(VPNTunnel.a_device_id == dev.id, VPNTunnel.b_device_id == dev.id))
        .order_by(VPNTunnel.name).limit(50))).scalars().all())
    peers = await _visible_device_names(session, user, [
        t.b_device_id if t.a_device_id == dev.id else t.a_device_id for t in tunnels])
    vpn = []
    for t in tunnels:
        a_side = t.a_device_id == dev.id
        peer_id = t.b_device_id if a_side else t.a_device_id
        vpn.append({"name": t.name, "type": t.type, "status": t.status,
                    "peer_device": peers.get(peer_id), "peer_endpoint": t.b_endpoint if a_side else t.a_endpoint,
                    "pairing_method": t.pairing_method})
    lease_row = (await session.execute(
        select(DeviceLease, Provider.name, Circuit.cid)
        .outerjoin(Provider, Provider.id == DeviceLease.provider_id)
        .outerjoin(Circuit, Circuit.id == DeviceLease.circuit_id)
        .where(DeviceLease.device_id == dev.id))).first()
    lease = None
    if lease_row is not None:
        ls, pname, cid = lease_row
        lease = {"provider": pname, "circuit": cid, "return_required": ls.return_required,
                 "return_due": ls.return_due.isoformat() if ls.return_due else None, "note": ls.note}
    return {"hosted_vm_count": vm_total, "hosted_vms": hosted, "circuits": circuits, "vpn_tunnels": vpn,
            "lease": lease}


async def list_customers(
    session: AsyncSession, *, user: User, limit: int = 200,
) -> dict[str, Any]:
    """列出客戶 / 管理單位：名稱、顯示名稱、說明、聯絡人、Email、電話、地址、子網路數。

    可見性推進 SQL：以前先 LIMIT 再過濾，受限帳號看得到的單位只要排在後面就消失。
    """
    limit = max(1, min(int(limit), 500))
    stmt = select(Customer)
    vis = await visible_ids(session, user=user, object_type="customer")
    if vis is not None:
        stmt = stmt.where(in_values(Customer.id, vis) if vis else sa_false())
    total = await _count(session, stmt)
    rows = list((await session.execute(stmt.order_by(Customer.name).limit(limit))).scalars().all())
    sub_n: dict[Any, int] = {}
    if rows:
        # 與畫面「子網路」欄同一個算法（REST /customers）
        sub_n = {cid: int(n) for cid, n in (await session.execute(
            select(Subnet.customer_id, func.count()).where(in_values(Subnet.customer_id, [c.id for c in rows]))
            .group_by(Subnet.customer_id))).all()}
    out = [
        {"id": str(c.id), "name": c.name, "title": c.title, "description": c.description,
         "contact": c.contact, "email": c.email, "phone": c.phone, "address": c.address,
         "subnet_count": sub_n.get(c.id, 0), "created_at": _iso(c.created_at)}
        for c in rows
    ]
    return {"customers": out, "count": len(out), **_page(total, len(out))}


async def list_nat(
    session: AsyncSession, *, user: User, limit: int = 100,
    subnet_cidr: str | None = None, subnet_id: str | None = None,
) -> dict[str, Any]:
    """列出 NAT 規則（NAT 頁編輯畫面的每個欄位＋來自哪台防火牆）。問某網段對外開了什麼要帶 subnet_cidr。"""
    limit = _clamp_limit(limit, 100)
    scope_ids, scope = await _scope_subnet(
        session, user=user, subnet_cidr=subnet_cidr, subnet_id=subnet_id)
    stmt = select(NATTranslation)
    if scope_ids is not None:
        in_scope = select(IPAddress.id).where(in_values(IPAddress.subnet_id, scope_ids))
        stmt = stmt.where(NATTranslation.src_ip_id.in_(in_scope)
                          | NATTranslation.dst_ip_id.in_(in_scope))
    total = await _count_rows(session, stmt)
    rows = list((await session.execute(
        stmt.order_by(NATTranslation.name).limit(limit)
    )).scalars().all())
    ip_ids = {i for r in rows for i in (r.src_ip_id, r.dst_ip_id) if i}
    ips = {i: _inet_host(v) for i, v in (await session.execute(
        select(IPAddress.id, IPAddress.ip).where(in_values(IPAddress.id, ip_ids)))).all()} if ip_ids else {}
    devs = await _names_for_ids(session, Device, {r.device_id for r in rows})
    fw_names = await _firewall_name_map(session)
    out = []
    for r in rows:
        source, fw = _sync_origin(r.source_origin, fw_names)
        out.append({
            "id": str(r.id), "name": r.name, "type": r.type,
            # 來源：哪台防火牆同步來的（畫面上的「來源」欄），手動建立的是 manual
            "source": source, "firewall": fw, "device": devs.get(r.device_id),
            "interface": r.src_interface, "protocol": r.protocol,
            "ip_version": r.ip_version, "disabled": r.disabled, "no_rdr": r.no_rdr, "log": r.log,
            # 來源 / 目的：優先顯示關聯到的 jt-ipam IP，否則顯示防火牆上的別名；*_not＝「除了這些以外」
            "src_ip": ips.get(r.src_ip_id), "src_alias": r.src_alias, "src_not": r.src_not,
            "src_port": r.src_port, "src_port_to": r.src_port_to, "src_port_alias": r.src_port_alias,
            "dst_ip": ips.get(r.dst_ip_id), "dst_alias": r.dst_alias, "dst_not": r.dst_not,
            "dst_port": r.dst_port, "dst_port_to": r.dst_port_to, "dst_port_alias": r.dst_port_alias,
            "redirect_alias": r.redirect_alias, "nat_reflection": r.nat_reflection,
            "pool_options": r.pool_options, "category": r.category, "filter_rule": r.filter_rule,
            "description": r.description,
        })
    return {"scope": scope, "count": total, **_page_meta(total, len(out)), "nat_rules": out}


async def list_sections(session: AsyncSession, *, user: User, limit: int = 200) -> dict[str, Any]:
    """列出區段：名稱、說明、上層區段、單位、顯示順序、嚴格模式、子網路數（排序同畫面：顯示順序、名稱）。

    可見性推進 SQL：以前先 LIMIT 再過濾，受限帳號看得到的區段只要排在後面就消失。
    """
    limit = max(1, min(int(limit), 500))
    stmt = select(Section)
    vis = await visible_ids(session, user=user, object_type="section")
    if vis is not None:
        stmt = stmt.where(in_values(Section.id, vis) if vis else sa_false())
    total = await _count(session, stmt)
    rows = list((await session.execute(
        stmt.order_by(Section.display_order, Section.name).limit(limit))).scalars().all())
    sub_n: dict[Any, int] = {}
    if rows:
        sub_n = {sid: int(n) for sid, n in (await session.execute(
            select(Subnet.section_id, func.count()).where(in_values(Subnet.section_id, [r.id for r in rows]))
            .group_by(Subnet.section_id))).all()}
    parents = await _name_map(session, Section.name, [r.parent_id for r in rows])
    custs = await _name_map(session, Customer.name, [r.customer_id for r in rows])
    out = [{"id": str(r.id), "name": r.name, "description": r.description,
            "parent": parents.get(r.parent_id), "customer": custs.get(r.customer_id),
            "display_order": r.display_order, "strict_mode": r.strict_mode,
            "subnet_count": sub_n.get(r.id, 0)} for r in rows]
    return {"sections": out, "count": len(out), **_page(total, len(out))}


async def list_vrfs(session: AsyncSession, *, user: User, limit: int = 200) -> dict[str, Any]:
    """列出 VRF：名稱、RD、說明、是否允許重疊網段、子網路數。"""
    from app.models.vrf import VRF
    limit = max(1, min(int(limit), 500))
    total = await _count(session, select(VRF))
    rows = list((await session.execute(
        select(VRF).order_by(VRF.name).limit(limit)
    )).scalars().all())
    sub_n: dict[Any, int] = {}
    if rows:
        sub_n = {vid: int(n) for vid, n in (await session.execute(
            select(Subnet.vrf_id, func.count()).where(in_values(Subnet.vrf_id, [r.id for r in rows]))
            .group_by(Subnet.vrf_id))).all()}
    return {"vrfs": [{"id": str(r.id), "name": r.name, "rd": r.rd, "description": r.description,
                      # 同一個 VRF 內可不可以有重疊的子網路（建立子網路時的檢查依據）
                      "allow_overlap": r.allow_overlap, "subnet_count": sub_n.get(r.id, 0)}
                     for r in rows], "count": len(rows), **_page(total, len(rows))}


async def list_vpn_tunnels(session: AsyncSession, *, user: User, limit: int = 200) -> dict[str, Any]:
    """列出 VPN 通道（從各家防火牆拉回的 WireGuard / IPsec / OpenVPN，及手動建立的）。

    site_to_site=true＝兩端都對到本系統的裝置；**對接可信度看 pairing_method**：wireguard_pubkey 是公鑰互相
    對上（可靠），ipsec_endpoint 是用端點位址比對（best effort，可能配錯），沒有值＝手動指定或尚未自動對接。
    b_endpoint 為對端閘道位址（對端非本系統管理的 device 時）。
    """
    from app.models.device import Device
    from app.models.physical import VPNTunnel

    lim = _clamp_limit(limit)
    total = await _count_rows(session, select(VPNTunnel))
    rows = list((await session.execute(
        select(VPNTunnel).order_by(VPNTunnel.name).limit(lim)
    )).scalars().all())
    dev_ids = {d for t in rows for d in (t.a_device_id, t.b_device_id) if d}
    # 解析每台 device 的名稱與管理 IP（讓答案能說「VPN 做在哪台裝置 / 哪個 IP 上」）
    names: dict[Any, str] = {}
    dev_pip: dict[Any, Any] = {}
    if dev_ids:
        for did, nm, pip in (await session.execute(
            select(Device.id, Device.name, Device.primary_ip_id).where(in_values(Device.id, dev_ids))
        )).all():
            names[did] = nm
            dev_pip[did] = pip
    pip_map: dict[Any, str] = {}
    pip_ids = {p for p in dev_pip.values() if p}
    if pip_ids:
        pip_map = {pid: str(ip).split("/")[0] for pid, ip in (await session.execute(
            select(IPAddress.id, IPAddress.ip).where(in_values(IPAddress.id, pip_ids))
        )).all()}

    def _dev_ip(did) -> str | None:  # type: ignore[no-untyped-def]
        if did is None:
            return None
        pip = dev_pip.get(did)
        if pip and pip in pip_map:
            return pip_map[pip]
        nm = names.get(did)            # 防火牆常以管理 IP 命名 → 名稱本身可能就是 IP
        if nm:
            try:
                ipaddress.ip_address(nm.strip())
                return nm.strip()
            except ValueError:
                pass
        return None

    fw_names = await _firewall_name_map(session)
    from app.models.advanced import Contact, ContactAssignment, ContactRole
    from app.services.circuit_links import circuits_carrying
    contacts: dict[Any, list[dict[str, Any]]] = {}
    if rows:
        for asg, who, role in (await session.execute(
                select(ContactAssignment, Contact, ContactRole)
                .join(Contact, Contact.id == ContactAssignment.contact_id)
                .outerjoin(ContactRole, ContactRole.id == ContactAssignment.role_id)
                .where(ContactAssignment.object_type == "vpn_tunnel",
                       in_values(ContactAssignment.object_id, [t.id for t in rows]))
                .order_by(ContactAssignment.priority.desc(), Contact.name))).all():
            contacts.setdefault(asg.object_id, []).append(
                {"name": who.name, "role": (role.builtin_key or role.name) if role else None,
                 "phone": who.phone, "email": who.email})
    out = []
    for t in rows:
        source, fw = _sync_origin(t.source_origin, fw_names)
        carried = []
        for side, ep in (("a", t.a_endpoint), ("b", t.b_endpoint)):
            carried += [{"side": side, "circuit": c.cid} for c in await circuits_carrying(session, ep)]
        out.append({
            "id": str(t.id), "name": t.name, "type": t.type, "status": t.status,
            "contacts": contacts.get(t.id, []), "carried_by_circuits": carried,
            "source": source, "firewall": fw,
            "a_device": names.get(t.a_device_id),
            "a_device_ip": _dev_ip(t.a_device_id),
            "a_endpoint": t.a_endpoint,
            "b_device": names.get(t.b_device_id),
            "b_device_ip": _dev_ip(t.b_device_id),
            "b_endpoint": t.b_endpoint,
            "site_to_site": bool(t.a_device_id and t.b_device_id),
            "pairing_method": t.pairing_method,
            "encryption_algo": t.encryption_algo, "auth_algo": t.auth_algo,
            "local_public_key": t.local_public_key, "peer_public_key": t.peer_public_key,
            "description": t.description,
        })
    return {"count": total, **_page_meta(total, len(out)), "vpn_tunnels": out}


async def recent_ip_changes(
    session: AsyncSession, *, user: User, ip: str | None = None, limit: int = 20,
) -> dict[str, Any]:
    """最近的 IP 異動記錄（可指定某 IP）：何時、哪個子網路的哪個位址、哪個欄位從什麼改成什麼、
    來源、操作者（人工編輯時）與備註。"""
    from app.models.ip_change_log import IPChangeLog
    limit = max(1, min(int(limit), 100))
    stmt = select(IPChangeLog)
    if ip:
        stmt = stmt.where(IPChangeLog.ip_text == ip)
    # RBAC：只回使用者可見子網路內 IP 的異動（限定範圍時 subnet_id 必須落在 vis）
    vis = await visible_ids(session, user=user, object_type="subnet")
    if vis is not None:
        stmt = stmt.where(in_values(IPChangeLog.subnet_id, vis)) if vis else stmt.where(sa_false())
    # 異動記錄可能有幾百萬筆：總數最多數到 _COUNT_CAP，超過就標「至少」（不為了一個數字整表掃）
    n = int(await session.scalar(select(func.count()).select_from(
        stmt.limit(_COUNT_CAP + 1).subquery())) or 0)
    rows = list((await session.execute(
        stmt.order_by(IPChangeLog.created_at.desc()).limit(limit))).scalars().all())
    actors = await _user_names(session, [r.actor_user_id for r in rows])
    subs = await _name_map(session, Subnet.cidr, [r.subnet_id for r in rows])
    return {"changes": [
        {"ip": r.ip_text, "subnet": str(subs[r.subnet_id]) if r.subnet_id in subs else None,
         "event": r.event_type, "field": r.field,
         "old": r.old_value, "new": r.new_value, "source": r.source,
         "actor": actors.get(r.actor_user_id), "note": r.note,
         "at": r.created_at.isoformat()} for r in rows], "count": len(rows),
        **_page(min(n, _COUNT_CAP), len(rows)), "total_is_lower_bound": n > _COUNT_CAP}


async def dns_lookup(session: AsyncSession, *, user: User, name: str) -> dict[str, Any]:
    """用名稱（hostname / FQDN 子字串）查 DNS 紀錄：每筆帶 TTL、所在 zone 與 DNS 伺服器、一致性、對應的 IPAM 位址。"""
    from app.models.dns import DNSServer, DNSZone
    stmt = (select(DNSRecord, DNSZone.name, DNSServer.name)
            .join(DNSZone, DNSZone.id == DNSRecord.zone_id).join(DNSServer, DNSServer.id == DNSZone.server_id)
            .where(DNSRecord.name.ilike(f"%{name}%")))
    total = await _count_rows(session, stmt)
    rows = (await session.execute(stmt.order_by(DNSRecord.name, DNSRecord.type).limit(50))).all()
    ip_ids = {r.ipam_address_id for r, _z, _s in rows if r.ipam_address_id}
    ips = {i: _inet_host(v) for i, v in (await session.execute(
        select(IPAddress.id, IPAddress.ip).where(in_values(IPAddress.id, ip_ids)))).all()} if ip_ids else {}
    return {**_page_meta(total, len(rows)), "count": total, "records": [{
        "name": r.name, "type": r.type, "value": r.value, "ttl": r.ttl, "zone": zone, "server": srv,
        "consistency_state": r.consistency_state, "ipam_ip": ips.get(r.ipam_address_id),
    } for r, zone, srv in rows]}


async def global_search(session: AsyncSession, *, user: User, q: str) -> dict[str, Any]:
    """全域搜尋（IP / CIDR / MAC / VLAN / 文字），跨子網路/IP/裝置等。"""
    from app.services.search import search as _search
    return await _search(session, user=user, q=q, limit_per_type=8)


async def oui_lookup(session: AsyncSession, *, user: User, mac: str) -> dict[str, Any]:
    """用 MAC 查 OUI 廠商。"""
    vendor = await vendor_for_mac(session, mac)
    return {"mac": mac, "vendor": vendor}


async def oui_search(
    session: AsyncSession, *, user: User,
    prefix: str | None = None, name: str | None = None, limit: int = 50,
) -> dict[str, Any]:
    """依 OUI 首碼或廠商名搜尋 OUI 紀錄（多筆）。"""
    from app.services.oui import search_oui_vendors
    try:
        return await search_oui_vendors(session, prefix=prefix, name=name, limit=limit)
    except ValueError as exc:
        raise IPAMToolError(str(exc)) from exc


# ── 依 IP／名稱解析物件、REST 規則的錯誤轉換（寫入工具與 switch_port_for_ip 共用；2026-10-15 MCP 工具盤點）──


def _rest_rule_error(exc: Exception) -> IPAMToolError:
    """REST 共用的守門／檢查丟 HTTPException → 工具錯誤。

    訊息前面帶錯誤代碼（模型看得懂是哪一條規則擋下來），代碼與參數另外掛在例外上：
    AI 對話的確認端點用 detail_of 取 `code`／`params`，畫面就能照使用者的語言翻譯（與 REST 同一個代碼）。
    """
    detail = getattr(exc, "detail", None)
    if not isinstance(detail, dict):
        return IPAMToolError(str(detail or exc))
    code = detail.get("code")
    msg = str(detail.get("message") or code or "rejected")
    err = IPAMToolError(f"{code}: {msg}" if code else msg)
    if code:
        err.code = code                                   # type: ignore[attr-defined]
        err.params = dict(detail.get("params") or {})     # type: ignore[attr-defined]
    return err


def _ui_error(exc: Exception) -> IPAMToolError:
    """服務層的 UiError（例如機櫃 U 位衝突）→ 工具錯誤，保留代碼與參數。"""
    code = getattr(exc, "code", None)
    err = IPAMToolError(f"{code}: {exc}" if code else str(exc))
    if code:
        err.code = code                                            # type: ignore[attr-defined]
        err.params = dict(getattr(exc, "params", {}) or {})        # type: ignore[attr-defined]
    return err


def _invalid_args(exc: Any) -> IPAMToolError:
    """Pydantic 驗證錯誤（與 REST 同一份 schema）→ 看得懂的工具錯誤。"""
    parts = []
    for e in exc.errors()[:6]:
        loc = ".".join(str(x) for x in e.get("loc", ()))
        parts.append(f"{loc}: {e.get('msg')}" if loc else str(e.get("msg")))
    return IPAMToolError("invalid arguments: " + "; ".join(parts))


async def _resolve_ip_record(
    session: AsyncSession, *, user: User, ip: str | None,
    subnet_cidr: str | None = None, ip_id: str | None = None,
) -> IPAddress:
    """依 IP 字串（或 ip_id）找出唯一一筆位址記錄，只在看得到的子網路裡找。

    重疊網段（兩個單位共用同一段位址）同一個 IP 字串會有好幾筆：以前 `.first()` 任取一筆，改到／查到
    別的單位那一筆而不自知。現在不猜：列出候選，要模型帶 ip_id（或分得出來的 subnet_cidr）再呼叫一次。
    """
    addr: str | None = None
    if ip:
        try:
            addr = str(ipaddress.ip_interface(ip.strip()).ip)
        except ValueError:
            raise IPAMToolError(f"invalid ip: {ip[:80]!r} (expected an IPv4 or IPv6 address)") from None
    vis = await visible_ids(session, user=user, object_type="subnet")
    if ip_id:
        obj = await session.get(IPAddress, _as_uuid(ip_id, "ip_id"))
        if obj is None or (vis is not None and obj.subnet_id not in vis):
            raise IPAMToolError(f"IP not found: ip_id={ip_id}")
        actual = str(obj.ip).split("/")[0]
        if addr is not None and actual != addr:
            raise IPAMToolError(f"ip_id {ip_id} is {actual}, not {addr}")
        return obj
    if addr is None:
        raise IPAMToolError("ip is required (or ip_id)")
    stmt = (
        select(IPAddress, Subnet.cidr, Subnet.description, Customer.name)
        .join(Subnet, Subnet.id == IPAddress.subnet_id)
        .outerjoin(Customer, Customer.id == Subnet.customer_id)
        .where(IPAddress.ip == addr)
    )
    if subnet_cidr:
        raw = subnet_cidr.strip()
        try:
            if "/" in raw:
                stmt = stmt.where(text("subnets.cidr = CAST(:wta_net AS cidr)").bindparams(
                    wta_net=str(ipaddress.ip_network(raw, strict=False))))
            else:
                stmt = stmt.where(text("subnets.cidr >>= CAST(:wta_addr AS inet)").bindparams(
                    wta_addr=str(ipaddress.ip_address(raw))))
        except ValueError:
            raise IPAMToolError(f"invalid subnet_cidr: {raw[:80]!r} (expected a CIDR such as 192.0.2.0/24)") from None
    if vis is not None:
        if not vis:
            raise IPAMToolError(f"IP not found: {addr}")
        stmt = stmt.where(in_values(IPAddress.subnet_id, vis))
    rows = (await session.execute(stmt.order_by(IPAddress.id).limit(11))).all()
    if not rows:
        raise IPAMToolError(f"IP not found: {addr}" + (f" in {subnet_cidr}" if subnet_cidr else ""))
    if len(rows) > 1:
        listed = "; ".join(
            f"ip_id={r[0].id} (subnet {r[1]}" + (f", customer {r[3]}" if r[3] else "")
            + (f", {str(r[2])[:40]}" if r[2] else "") + ")"
            for r in rows[:10])
        more = "+" if len(rows) > 10 else ""
        raise IPAMToolError(f"{addr} exists in {min(len(rows), 10)}{more} subnets (overlapping networks); "
                            f"call again with ip_id: {listed}")
    return rows[0][0]


async def _lookup_by_name(
    session: AsyncSession, model: Any, value: str, *, label: str, name_cols: tuple[Any, ...],
    describe: Any = None,
) -> Any:
    """UUID 或名稱（不分大小寫、完全相同）→ 物件。找不到、或同名多筆 → 工具錯誤（不猜，列出候選）。

    只給管理員的寫入工具用（MUTATING_TOOLS），引用物件的可見性另由 REST 的 write_guards 把關。
    """
    raw = (value or "").strip()
    try:
        oid: uuid.UUID | None = uuid.UUID(raw)
    except ValueError:
        oid = None
    if oid is not None:
        obj = await session.get(model, oid)
        if obj is None:
            raise IPAMToolError(f"{label} not found: {raw}")
        return obj
    rows = list((await session.execute(
        select(model).where(or_(*[func.lower(c) == raw.lower() for c in name_cols])).limit(11)
    )).scalars().all())
    if not rows:
        raise IPAMToolError(f"{label} not found: {raw[:80]!r}")
    if len(rows) > 1:
        listed = "; ".join(describe(r) if describe else f"{r.name} (id={r.id})" for r in rows[:10])
        raise IPAMToolError(f"{len(rows)} {label}s match {raw[:80]!r}; call again with the id: {listed}")
    return rows[0]


async def _customer_arg(session: AsyncSession, value: str) -> Customer:
    return await _lookup_by_name(session, Customer, value, label="customer",
                                 name_cols=(Customer.name, Customer.title))


async def _device_arg(session: AsyncSession, value: str) -> Device:
    return await _lookup_by_name(session, Device, value, label="device", name_cols=(Device.name, Device.fqdn),
                                 describe=lambda d: f"{d.name} ({d.type}, id={d.id})")


def _blank_to_none(value: str) -> str | None:
    """寫入工具的字串參數：空字串＝清空（存 None）。"""
    return value.strip() or None


def _writable_ip_states() -> frozenset[str]:
    """寫得進資料庫的 IP 狀態＝schema 允許清單 ∩ 資料表 CHECK 約束（ip_state_valid）。

    ⚠️ schema（與畫面的狀態選單）有 deprecated／quarantine，資料表約束卻還沒有，存下去是
    IntegrityError（2026-10-15 盤點時發現，REST 也一樣）。工具只提供兩邊都接受的，約束放寬後自動跟上。
    """
    import re

    from app.schemas.address import IP_STATES
    for c in IPAddress.__table__.constraints:
        # 約束名稱會被命名慣例改寫（實際是 ck_ip_addresses_…），所以看內容不看名稱
        sql = str(getattr(c, "sqltext", "")).strip()
        if sql.startswith("state IN"):
            return IP_STATES & frozenset(re.findall(r"'([^']+)'", sql))
    return IP_STATES


def _check_ip_state(state: str) -> str:
    allowed = _writable_ip_states()
    s = (state or "").strip()
    if s not in allowed:
        raise IPAMToolError(f"invalid state {s[:32]!r}; allowed: {', '.join(sorted(allowed))}")
    return s


def _check_probes(values: Any, field: str) -> list[str]:
    from app.core.scan_probes import LEGACY_ALIASES, VALID_PROBES
    if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
        raise IPAMToolError(f"invalid {field}: expected a list such as [\"icmp\"]")
    bad = [v for v in values if v not in VALID_PROBES and v not in LEGACY_ALIASES]
    if bad:
        raise IPAMToolError(f"invalid {field}: {bad[:5]}; allowed: {', '.join(sorted(VALID_PROBES))}")
    return list(values)


async def _audit_tool_write(session: AsyncSession, *, user: User, object_type: str, object_id: Any,
                            action: str, diff: dict[str, Any] | None) -> None:
    """與 REST 端點同一筆物件稽核（object_type／action／diff 相同）。來源 IP 等由外層的
    ai_tool_exec／mcp_tool_exec 稽核記錄補上（工具拿不到請求）。"""
    import json

    from app.core.audit import append_audit
    # UUID 等先轉成字串：與正式環境引擎的 JSONB 序列化（core.db._json_dumps_utf8，default=str）結果相同，
    # 不依賴呼叫端 session 用的是哪個引擎
    safe = json.loads(json.dumps(diff, default=str)) if diff is not None else None
    await append_audit(session, actor_user_id=str(user.id), actor_ip=None, actor_user_agent=None,
                       object_type=object_type, object_id=str(object_id) if object_id else None,
                       action=action, diff=safe, request_id=None)


async def switch_port_for_ip(
    session: AsyncSession, *, user: User, ip: str,
    subnet_cidr: str | None = None, ip_id: str | None = None,
) -> dict[str, Any]:
    """查某 IP 接在哪台 switch 的哪個 port（用 FDB；access port = 該 port MAC 數最少者）。

    - 找位址記錄走 `_resolve_ip_record`：可見範圍先進 SQL、重疊網段不猜（2026-10-15 以前任取第一筆）。
      REST 的 /addresses/{id}/switch-port 也借用這支，帶 ip_id 才回得到「那一筆」。
    - FDB 依交換器可見性遮蔽，規則與 trace_mac 相同：LibreNMS 的列看它對映到的 jt-ipam 裝置、
      MikroTik 的列看 switch_device_id；沒對映到 jt-ipam 裝置的交換器無從判斷權限 → 非管理員一律遮蔽。
    """
    ipa = await _resolve_ip_record(session, user=user, ip=ip, subnet_cidr=subnet_cidr, ip_id=ip_id)
    cidr = await session.scalar(select(Subnet.cidr).where(Subnet.id == ipa.subnet_id))
    base: dict[str, Any] = {
        "ip": str(ipa.ip).split("/")[0], "ip_id": str(ipa.id), "subnet": str(cidr) if cidr else None,
        # 同步／人工記在 IP 上的交換器埠（IP 詳細資料頁顯示的那一欄）與它是否高信心（該埠只有一個 MAC）
        "switch_port": ipa.switch_port, "switch_port_confident": ipa.switch_port_confident,
    }
    if ipa.mac is None:
        return {**base, "mac": None, "locations": [], "likely_access_port": None,
                "note": "no MAC known for this IP"}
    mac = str(ipa.mac).lower()
    vis_dev = await visible_ids(session, user=user, object_type="device")
    rows = list((await session.execute(
        select(FDBEntry.port_name, FDBEntry.vlan_id_num, FDBEntry.first_seen_at, FDBEntry.last_seen_at,
               FDBEntry.source, FDBEntry.device_id, FDBEntry.switch_device_id,
               LibreNMSDevice.hostname, LibreNMSDevice.sysname, LibreNMSDevice.primary_ip,
               LibreNMSDevice.jt_ipam_device_id)
        .outerjoin(LibreNMSDevice, LibreNMSDevice.id == FDBEntry.device_id)
        .where(FDBEntry.mac == mac)
    )).all())
    # ⚠️ FDB 的 device_id 是 LibreNMS 裝置；權限要看它對映到的 jt-ipam 裝置（MikroTik 的列直接記 switch_device_id）
    visible_rows = []
    for r in rows:
        sw_dev = r.jt_ipam_device_id if r.device_id is not None else r.switch_device_id
        if vis_dev is not None and (sw_dev is None or sw_dev not in vis_dev):
            continue
        visible_rows.append((r, sw_dev))
    sw_ids = {d for _r, d in visible_rows if d is not None}
    sw_names: dict[Any, str] = {}
    if sw_ids:
        sw_names = {i: n for i, n in (await session.execute(
            select(Device.id, Device.name).where(in_values(Device.id, sw_ids)))).all()}
    locs = []
    for r, sw_dev in visible_rows:
        # 該 (switch, port) 上有幾個不同 MAC → 越少越像 access port
        same_switch = (FDBEntry.device_id == r.device_id) if r.device_id is not None else (
            FDBEntry.switch_device_id == r.switch_device_id)
        mac_count = int(await session.scalar(
            select(func.count(func.distinct(FDBEntry.mac)))
            .where(FDBEntry.port_name == r.port_name, same_switch)
        ) or 0)
        locs.append({
            "switch": sw_names.get(sw_dev) or r.sysname or r.hostname,
            "switch_device_id": str(sw_dev) if sw_dev else None,
            "switch_ip": str(r.primary_ip) if r.primary_ip else None,
            "port": r.port_name, "vlan": r.vlan_id_num,
            "macs_on_port": mac_count,
            "source": r.source,                 # librenms / mikrotik
            "first_seen_at": r.first_seen_at.isoformat() if r.first_seen_at else None,
            "last_seen_at": r.last_seen_at.isoformat() if r.last_seen_at else None,
        })
    # 越少 MAC 越像存取埠；同樣多時最近看到的排前面（兩段穩定排序）
    locs.sort(key=lambda x: x["last_seen_at"] or "", reverse=True)
    locs.sort(key=lambda x: x["macs_on_port"] or 9999)
    return {**base, "mac": mac, "locations": locs, "likely_access_port": locs[0] if locs else None}


# ─────────────────── 寫入工具（admin only）───────────────────


async def allocate_ip(
    session: AsyncSession, *, user: User,
    subnet_id: str | None = None, subnet_cidr: str | None = None,
    hostname: str | None = None, description: str | None = None,
    requested_ip: str | None = None, owner: str | None = None,
    customer: str | None = None, mac: str | None = None,
    state: str | None = None, note: str | None = None,
    device: str | None = None, switch_port: str | None = None,
) -> dict[str, Any]:
    """配發 IP（ADMIN）。可指定 requested_ip（哪個 IP）或留空取第一個空位；欄位與畫面的新增 IP 表單相同。

    若給了 requested_ip 但沒給 subnet_id / subnet_cidr，會自動找出「包含此 IP 的子網路」
    （取最精確的一個；重疊網段不猜，列出候選要模型帶 subnet_id）。
    建立走 REST POST /addresses 同一份規則（services/address_write）：冷卻期守門、主機名稱格式、
    手動主機名稱觀測、異動記錄、物件稽核。"""
    from fastapi import HTTPException
    from pydantic import ValidationError

    from app.schemas.address import IPAddressCreate
    from app.services.address_write import create_address_checked
    from app.services.custom_field import CustomFieldError
    if not user.is_admin:
        raise IPAMToolError("allocate_ip requires admin")
    if not subnet_id and not subnet_cidr and requested_ip:
        # 只給 requested_ip：包含它、最小的子網路（只在看得到的範圍；重疊網段列候選，不再默默取第一筆）
        found = await _subnet_by_cidr_arg(session, user=user, value=requested_ip.split("/")[0])
        if found is None:
            raise IPAMToolError(f"no subnet contains {requested_ip}; create a subnet that contains it first")
        subnet_id = str(found.id)
    subnet = await _resolve_subnet(
        session, user=user, subnet_id=subnet_id, subnet_cidr=subnet_cidr,
    )

    fields: dict[str, Any] = {"subnet_id": subnet.id, "state": _check_ip_state(state) if state else "active"}
    for key, value in (("hostname", hostname), ("description", description), ("owner", owner),
                       ("mac", mac), ("note", note), ("switch_port", switch_port)):
        if value is not None and value.strip():
            fields[key] = value.strip()
    if customer is not None and customer.strip():
        fields["customer_id"] = (await _customer_arg(session, customer)).id
    if device is not None and device.strip():
        fields["device_id"] = (await _device_arg(session, device)).id
    if requested_ip:
        fields["ip"] = requested_ip.split("/")[0]
    else:
        free = await find_first_free_address(session, subnet)   # 已排除冷卻中的位址
        if free is None:
            raise IPAMToolError(f"subnet full: Subnet {subnet.cidr} is full")
        fields["ip"] = free
    try:
        payload = IPAddressCreate.model_validate(fields)
    except ValidationError as exc:
        raise _invalid_args(exc) from exc

    try:
        obj = await create_address_checked(session, user=user, subnet=subnet, payload=payload,
                                           change_note="AI chat 配發")
    except HTTPException as exc:
        raise _rest_rule_error(exc) from exc
    except IPAlreadyExists as exc:
        raise IPAMToolError(f"already allocated: {exc}") from exc
    except IPNotInSubnet as exc:
        raise IPAMToolError(f"not in subnet: {exc}") from exc
    except CustomFieldError as exc:
        raise _ui_error(exc) from exc

    # 物件稽核與 REST 相同：指定 IP＝POST /addresses、取第一個空位＝POST /addresses/first_free
    if requested_ip:
        await _audit_tool_write(session, user=user, object_type="ip_address", object_id=obj.id,
                                action="create", diff={"after": payload.model_dump(mode="json")})
    else:
        await _audit_tool_write(session, user=user, object_type="ip_address", object_id=obj.id,
                                action="allocate_first_free",
                                diff={"after": {"subnet_id": str(subnet.id), "ip": str(obj.ip),
                                                "hostname": obj.hostname}})
    await session.flush()
    return {
        "ip_address_id": str(obj.id),
        "ip": str(obj.ip).split("/")[0],
        "subnet_id": str(subnet.id),
        "subnet": str(subnet.cidr),
        "hostname": obj.hostname,
        "state": obj.state,
        "owner": obj.owner,
        "customer_id": str(obj.customer_id) if obj.customer_id else None,
        "device_id": str(obj.device_id) if obj.device_id else None,
        "switch_port": obj.switch_port,
        "note": obj.note,
    }


# ─────────────────── 新增工具：完整覆蓋系統功能 ───────────────────

async def get_ip_history(
    session: AsyncSession, *, user: User, ip: str, days: int = 30,
) -> dict[str, Any]:
    """IP 鑑識：某個 IP 在指定期間內「是誰」的證據時間軸。

    資安事件調查的第一個問題永遠是「那個 IP 當時是誰」。把四種既有證據依時間彙整：
    異動記錄（欄位級，含來源）、ARP（IP↔MAC 對應與最後出現時間）、主機名稱觀測
    （各來源獨立）、DHCP 觀測。全部是確定性檢索 —— 判讀交給呼叫端（人或模型），
    資料本身不做任何推測。

    RBAC 與 get_ip_detail 同規：先縮到可見子網路，重疊網段下不會把別單位的同 IP
    洩出來，也不會因為先取到不可見那筆而假報「查無」。
    """
    try:
        ipaddress.ip_address(ip)
    except ValueError as exc:
        raise IPAMToolError(f"Invalid IP: {exc}") from exc
    from datetime import UTC, datetime, timedelta
    days = max(1, min(int(days or 30), 365))
    since = datetime.now(UTC) - timedelta(days=days)

    from app.models.dhcp_sighting import DHCPSighting
    from app.models.ip_change_log import IPChangeLog
    from app.models.ip_hostname import IPHostnameObservation
    from app.models.librenms import ARPEntry

    vis = await visible_ids(session, user=user, object_type="subnet")
    ip_stmt = select(IPAddress).where(IPAddress.ip == ip)
    if vis is not None:
        if not vis:
            return {"ip": ip, "days": days, "visible": False, "events": []}
        ip_stmt = ip_stmt.where(in_values(IPAddress.subnet_id, vis))
    ipa = (await session.execute(ip_stmt.limit(1))).scalars().first()

    events: list[dict[str, Any]] = []
    # ARP／DHCP 觀測不掛在子網路底下（全域基礎設施資料）。受限帳號只有在
    # 「看得到這個 IP 的登錄紀錄」時才給 —— 否則對未登錄 IP 查歷史就能繞過
    # 可見性把 MAC 對應撈出來（admin／萬用讀取者 vis is None，不受此限）。
    can_see_global_evidence = (vis is None) or (ipa is not None)

    # 異動記錄：who/what/when，含來源（scanner/librenms/manual…）
    ch_stmt = (select(IPChangeLog)
               .where(IPChangeLog.ip_text == ip, IPChangeLog.created_at >= since))
    if vis is not None:
        ch_stmt = ch_stmt.where(in_values(IPChangeLog.subnet_id, vis))
    # 每一段都有上限；撞到上限的段落記在 truncated，模型才不會把「前 200 筆」說成「全部」
    truncated: dict[str, int] = {}
    changes = list((await session.execute(
        ch_stmt.order_by(IPChangeLog.created_at.desc()).limit(200))).scalars().all())
    if len(changes) >= 200:
        truncated["change"] = 200
    actors = await _user_names(session, [c.actor_user_id for c in changes])
    ch_subs = await _name_map(session, Subnet.cidr, [c.subnet_id for c in changes])
    for c in changes:
        events.append({"at": c.created_at.isoformat(), "kind": "change",
                       "source": c.source, "event": c.event_type, "field": c.field,
                       "old": c.old_value, "new": c.new_value,
                       # 人工編輯的操作者與備註；重疊網段時看 subnet 分得出是哪一筆
                       "actor": actors.get(c.actor_user_id), "note": c.note,
                       "subnet": str(ch_subs[c.subnet_id]) if c.subnet_id in ch_subs else None})

    # ARP：這個 IP 綁過哪些 MAC（換 MAC ＝ 換機器或偽冒，是鑑識關鍵）。
    # 受限帳號只看這個子網路的 ARP；沒有子網路歸屬的列（LibreNMS）只在這個子網路是唯一最細的容器時才給 ——
    # 重疊網段裡同一個位址可能是別的單位的機器（2026-10-07 稽核；規則同 unmanaged.for_subnet）
    arp_stmt = select(ARPEntry).where(ARPEntry.ip == ip)
    if vis is not None and ipa is not None:
        others = await session.scalar(text("""
            SELECT count(*) FROM subnets o, subnets s
             WHERE s.id = :sid AND o.id <> s.id AND o.archived_at IS NULL
               AND o.cidr >>= CAST(:ip AS inet) AND masklen(o.cidr) >= masklen(s.cidr)
        """), {"sid": ipa.subnet_id, "ip": ip})
        arp_stmt = arp_stmt.where(or_(ARPEntry.subnet_id == ipa.subnet_id,
                                      and_(ARPEntry.subnet_id.is_(None), literal(not others))))
    # 一個 MAC 一筆：誰回報過（哪台設備的 ARP 表、哪個介面）、幾個回報者、是否疑似讀壞或過期快取 ——
    # 以前一列一筆原始紀錄、最多 20 筆、沒有來源，看不出「這個 MAC 只有路由器看過一次」（2026-10-09 回饋）
    if can_see_global_evidence:
        from app.services.anomaly import mac_rows, reporter_names
        from app.services.arp_quality import MacSeen, classify_suspects, reporter_key
        from app.services.oui import mac_prefix, vendor_map
        arp_macs: dict[str, MacSeen] = {}
        arp_list = list((await session.execute(
            arp_stmt.order_by(ARPEntry.last_seen_at.desc()).limit(500))).scalars().all())
        if len(arp_list) >= 500:
            truncated["arp_rows"] = 500
        for a in arp_list:
            arp_macs.setdefault(str(a.mac), MacSeen()).add(
                reporter_key(str(a.source), a.device_id), source=str(a.source), at=a.last_seen_at,
                first=getattr(a, "first_seen_at", None), device_id=a.device_id, interface=a.interface)
        if arp_macs:
            by_prefix = await vendor_map(session, list(arp_macs))
            vendors = {m: by_prefix.get(mac_prefix(m) or "") for m in arp_macs}
            names = await reporter_names(session, {r["device_id"] for sn in arp_macs.values()
                                                   for r in sn.reporters.values()})
            for row in mac_rows(arp_macs, vendors, classify_suspects(arp_macs, vendors), names):
                first = arp_macs[row["mac"]].first
                events.append({"at": row["last_seen_at"], "kind": "arp", "mac": row["mac"],
                               "first_seen": first.isoformat() if first else None,
                               "vendor": row["vendor"], "reporter_count": row["reporter_count"],
                               "reporters": row["reporters"][:20], "suspect": row["suspect"]})

    # 主機名稱觀測：各來源（rdns/netbios/mdns/dhcp…）各自的說法
    if ipa is not None:
        hn_obs = list((await session.execute(
                select(IPHostnameObservation)
                .where(IPHostnameObservation.ip_id == ipa.id)
                .order_by(IPHostnameObservation.observed_at.desc()).limit(20))).scalars().all())
        if len(hn_obs) >= 20:
            truncated["hostname"] = 20
        for o in hn_obs:
            events.append({"at": o.observed_at.isoformat() if o.observed_at else None,
                           "kind": "hostname", "source": o.source,
                           "hostname": o.hostname})

    # DHCP 觀測（掃描代理看到誰在回應 DHCP —— 非法 DHCP 調查用）
    sightings = [] if not can_see_global_evidence else list((await session.execute(
        select(DHCPSighting).where(DHCPSighting.server_ip == ip)
        .order_by(DHCPSighting.last_seen_at.desc()).limit(5))).scalars().all())
    if len(sightings) >= 5:
        truncated["dhcp_server_sighting"] = 5
    for d in sightings:
        events.append({"at": d.last_seen_at.isoformat() if d.last_seen_at else None,
                       "kind": "dhcp_server_sighting", "mac": str(d.server_mac) if getattr(d, "server_mac", None) else None})

    events.sort(key=lambda e: e.get("at") or "", reverse=True)
    sub_cidr = None
    if ipa is not None:
        sub_cidr = await session.scalar(select(Subnet.cidr).where(Subnet.id == ipa.subnet_id))
    return {
        "ip": ip, "days": days, "visible": True,
        "registered": ipa is not None,
        "subnet": str(sub_cidr) if sub_cidr is not None else None,
        # 哪些段落撞到上限（段落 → 上限筆數）；空＝每一段都是完整的
        "truncated": truncated,
        "current": None if ipa is None else {
            "hostname": ipa.hostname, "mac": str(ipa.mac) if ipa.mac else None,
            "status": ipa.effective_status, "state": ipa.state,
            "discovery_source": ipa.discovery_source,
        },
        "events": events,
        "note": "資料為系統記錄的原始證據；時間為 UTC。判讀請以多筆證據交叉為準。ARP 每個 MAC 一筆："
                "reporter_count＝幾個來源（設備）看過；suspect＝corrupt（只有一個來源、查不到廠商、"
                "是另外兩個 MAC 拼起來的，多半是 SNMP 讀到一半）或 stale_cache（單一來源的本地管理位址，"
                "與多來源佐證的 MAC 不一致），這兩種不要當成真的另一台機器。",
    }


async def get_ip_detail(session: AsyncSession, *, user: User, ip: str) -> dict[str, Any]:
    """單一 IP 的完整資料：IP 編輯表單與詳細資料頁上的每個欄位。

    除了基本欄位，還有：設備類型／型號、各種旗標（不 ping、忽略 PTR、上線跟著裝置、DHCP 固定分配／
    租約／伺服器）、逐來源最後出現時間與防火牆 ARP 證據、各主控台開關與連線出口、閘道／DHCP 範圍／
    排除區間標記、MAC 廠商、OCS／RustDesk、有效 OS 與探測項目、建立／更新時間。
    """
    try:
        ipaddress.ip_address(ip)
    except ValueError as exc:
        raise IPAMToolError(f"Invalid IP: {exc}") from exc
    # RBAC：先縮到可見子網路再取一筆。若「先任取一筆再驗可見性」，在重疊網段
    # （多客戶共用 198.51.100.0/24）下可能取到不可見那筆而回報「查無」——
    # 但使用者其實看得到另一個子網路的同一個 IP，那是假的查無。
    vis = await visible_ids(session, user=user, object_type="subnet")
    stmt = select(IPAddress).where(IPAddress.ip == ip)
    if vis is not None:                      # None＝全部可見（admin 或萬用授權）
        if not vis:
            return {"found": False, "ip": ip, "unregistered": await _unregistered(session, user, ip)}
        stmt = stmt.where(in_values(IPAddress.subnet_id, vis))
    found = list((await session.execute(stmt.order_by(IPAddress.subnet_id).limit(20))).scalars().all())
    if not found:
        # 沒有記錄不等於系統沒看過：目擊、ARP、VM 回報、DHCP 固定分配
        return {"found": False, "ip": ip, "unregistered": await _unregistered(session, user, ip)}
    obj = found[0]
    sub = await session.get(Subnet, obj.subnet_id) if obj.subnet_id else None
    devs = await _visible_device_names(session, user, [obj.device_id])
    cust = await session.get(Customer, obj.customer_id) if obj.customer_id else None
    from types import SimpleNamespace

    from app.api.v1.endpoints.addresses import _enrich_special_flags
    from app.models.jump_host import JumpHost
    from app.models.scan_agent import ScanAgent
    from app.services.os_precedence import effective_os
    _os = await effective_os(session, obj)
    jump = await session.get(JumpHost, obj.jump_host_id) if obj.jump_host_id else None
    relay = await session.get(ScanAgent, obj.console_agent_id) if obj.console_agent_id else None
    # 閘道／DHCP 範圍／排除區間／DHCP 伺服器（自動）／固定分配明細：與位址清單同一份推導
    flags = SimpleNamespace()
    await _enrich_special_flags(session, [flags], [obj])
    others = []
    if len(found) > 1:
        cidrs = await _name_map(session, Subnet.cidr, [o.subnet_id for o in found[1:]])
        others = [{"subnet": str(cidrs.get(o.subnet_id)), "subnet_id": str(o.subnet_id),
                   "hostname": o.hostname, "effective_status": o.effective_status} for o in found[1:]]
    mac_txt = str(obj.mac) if obj.mac else None
    return {
        "found": True,
        **_ip_fields(obj),
        # 這個 IP 的 MAC 是哪台 VM 的網卡；哪台 VM 自己回報用了這個 IP（IP 衝突時就是對方）
        "vms_with_this_mac": await _vm_nics(session, user, mac=mac_txt) if mac_txt else [],
        "vms_reporting_this_ip": await _vm_nics(session, user, ip=_host(obj.ip)),
        # 最近在 ARP 上用這個 IP 的每個 MAC（is_record_mac＝記錄上的那個；其他的＝衝突的對方，vms 是哪台 VM）
        "arp_macs": await _record_arp_macs(session, user, obj, ip, vis),
        # 目前涵蓋這個 IP 的維護（維護中不告警）
        "maintenance": await _maintenance(session, "ip", obj.id),
        "subnet": str(sub.cidr) if sub else None,
        "subnet_description": sub.description if sub else None,
        "hostname_source_pin": obj.hostname_source_pin,
        "mac_source": obj.mac_source,
        "mac_vendor": await vendor_for_mac(session, obj.mac),
        "device": devs.get(obj.device_id),
        "device_id": str(obj.device_id) if obj.device_id else None,
        "customer": cust.name if cust else None,
        "customer_id": str(obj.customer_id) if obj.customer_id else None,
        # 「上線跟著裝置」這一輪的依據是同裝置的哪個位址
        "device_evidence_ip": _host(obj.device_evidence_ip),
        "device_identified_at": _iso(obj.device_identified_at),
        "device_since": _iso(obj.device_since),
        # 逐來源的防火牆／路由器 ARP 證據時間（arp:/vpn:/lease:<廠牌>）
        "arp_seen": dict(obj.arp_seen or {}),
        "anomaly_ignore": list(obj.anomaly_ignore or []),
        # 主控台開關（瀏覽器內連線按鈕要不要出現；能不能用另看權限）
        "ssh_enabled": bool(obj.ssh_enabled), "sftp_enabled": bool(obj.sftp_enabled),
        "rdp_enabled": bool(obj.rdp_enabled), "vnc_enabled": bool(obj.vnc_enabled),
        "novnc_enabled": bool(obj.novnc_enabled), "bmc_enabled": bool(obj.bmc_enabled),
        "rustdesk_enabled": bool(obj.rustdesk_enabled),
        # 連線出口：空＝沿用子網路的設定；跳板主機或掃描代理中繼（擇一）
        "jump_host": jump.name if jump else None,
        "console_agent": relay.name if relay else None,
        "is_gateway": bool(getattr(flags, "is_gateway", False)),
        "in_dhcp_range": bool(getattr(flags, "in_dhcp_range", False)),
        "dhcp_exclusion": getattr(flags, "dhcp_exclusion", None),
        "dhcp_reservation": getattr(flags, "dhcp_reservation", None),
        "dhcp_server_auto": bool(getattr(flags, "dhcp_server_auto", False)),
        "dhcp_server_integration": getattr(flags, "dhcp_server_integration", None),
        "dhcp_observed_at": getattr(flags, "dhcp_observed_at", None),
        "custom_fields": obj.custom_fields or None,
        **await _timestamps(session, IPAddress, obj.id),
        # OCS Inventory（透過網卡 MAC 比對到這個 IP 的資產盤點）
        "ocs_tag": obj.ocs_tag,
        "ocs_agent": obj.ocs_agent,
        "ocs_notes": obj.ocs_notes or [],
        # OCS 回報的硬體：系統／主機板／BIOS／CPU／記憶體／磁碟／顯示卡（記憶體、磁碟單位 MB）
        "ocs_hardware": obj.ocs_hw,
        # RustDesk Server（開源版）：對應到這個 IP 的 RustDesk ID 與線上狀態（不帶連線網址）
        "rustdesk": await _rustdesk_brief(session, obj.id),
        # OS 偵測（依來源優先序 scanner/librenms/wazuh 解析）+ 探測項目
        **_os,
        "effective_probes": await _effective_probes(session, sub, obj),
        "excluded_probes": list(obj.excluded_probes or []),
        "probe_last_run": obj.probe_last_run or None,
        # 重疊網段：同一個位址在其他（看得到的）子網路也有紀錄
        "other_records": others,
    }


async def get_subnet_detail(
    session: AsyncSession, *, user: User,
    subnet_id: str | None = None, subnet_cidr: str | None = None,
) -> dict[str, Any]:
    """子網路完整資料：子網路編輯表單的每個欄位（CIDR、說明、閘道、DNS、VLAN、VRF、區段、單位、地點、
    集區／已滿、使用率門檻、掃描設定、異常偵測／AI 巡檢、連線出口、自動 DNS），加上上層子網路、
    使用率、位址範圍（DHCP 集區／保留／其他）與建立／更新時間。"""
    sub = await _resolve_subnet(session, user=user, subnet_id=subnet_id, subnet_cidr=subnet_cidr)
    usage = await get_usage(session, sub)
    sec = await session.get(Section, sub.section_id) if sub.section_id else None
    cust = await session.get(Customer, sub.customer_id) if sub.customer_id else None
    vlan = await session.get(VLAN, sub.vlan_id) if sub.vlan_id else None
    loc = await session.get(Location, sub.location_id) if sub.location_id else None
    parent = await session.get(Subnet, sub.master_subnet_id) if sub.master_subnet_id else None
    from app.models.ip_range import IPRange
    from app.models.jump_host import JumpHost
    from app.models.scan_agent import ScanAgent
    from app.models.vlan import VLANDomain
    from app.models.vrf import VRF
    vrf = await session.get(VRF, sub.vrf_id) if sub.vrf_id else None
    dom = await session.get(VLANDomain, vlan.domain_id) if vlan is not None else None
    agent = await session.get(ScanAgent, sub.scan_agent_id) if sub.scan_agent_id else None
    jump = await session.get(JumpHost, sub.jump_host_id) if sub.jump_host_id else None
    relay = await session.get(ScanAgent, sub.console_agent_id) if sub.console_agent_id else None
    child_n = int(await session.scalar(
        select(func.count()).select_from(Subnet).where(Subnet.master_subnet_id == sub.id)) or 0)
    # 位址範圍（畫面「位址範圍」區塊）：有範圍才算使用量（ranges_with_usage 會讀整個子網路的 IP）
    ranges: list[dict[str, Any]] = []
    if await session.scalar(select(IPRange.id).where(IPRange.subnet_id == sub.id).limit(1)):
        from app.services.ip_ranges import ranges_with_usage
        ranges = [{
            "range": f"{r['start_ip']}-{r['end_ip']}", "start_ip": r["start_ip"], "end_ip": r["end_ip"],
            "purpose": r["purpose"], "name": r["name"], "description": r["description"],
            "size": r["size"], "used": r["used"], "free": r["free"], "records": r["records"],
            "stale": r["stale"], "first_free": r["first_free"],
            "excluded": r["excluded"], "auto": r["auto"],
        } for r in await ranges_with_usage(session, sub)]
    return {
        "id": str(sub.id),
        "cidr": str(sub.cidr),
        "description": sub.description,
        "gateway": _host(sub.gateway),
        "dns_servers": sub.dns_servers,
        "section": sec.name if sec else None,
        "section_id": str(sub.section_id) if sub.section_id else None,
        "customer": cust.name if cust else None,
        "customer_id": str(sub.customer_id) if sub.customer_id else None,
        "location": loc.name if loc else None,
        "vlan": ({"number": vlan.number, "name": vlan.name, "domain": dom.name if dom else None}
                 if vlan else None),
        "vrf": vrf.name if vrf else None,
        "vrf_allow_overlap": vrf.allow_overlap if vrf else None,
        "parent_subnet": str(parent.cidr) if parent else None,
        "child_subnet_count": child_n,
        "is_pool": sub.is_pool,
        "is_full": sub.is_full,
        "threshold_pct": sub.threshold_pct,
        "auto_dns": sub.auto_dns,
        "archived": sub.archived_at is not None,
        "archived_at": _iso(sub.archived_at),
        # 掃描設定：是否開啟、會跑哪些探測、指派的掃描代理、大子網路的掃描範圍（auto／full／known）
        "scan_enabled": sub.scan_enabled,
        "scan_method": list(sub.scan_method or []),
        "scan_agent": agent.name if agent else None,
        "scan_scope": sub.scan_scope,
        "anomaly_enabled": sub.anomaly_enabled,
        "ai_audit_enabled": sub.ai_audit_enabled,
        # 主控台連線出口：跳板主機或掃描代理中繼（擇一；IP 可以個別覆寫）
        "jump_host": jump.name if jump else None,
        "console_agent": relay.name if relay else None,
        "custom_fields": sub.custom_fields or None,
        **await _timestamps(session, Subnet, sub.id),
        "usage": usage,
        "ranges": ranges,
    }


async def list_subnet_ips(
    session: AsyncSession, *, user: User,
    subnet_id: str | None = None, subnet_cidr: str | None = None,
    state: str | None = None, limit: int = 256, offset: int = 0,
) -> dict[str, Any]:
    """列出某子網路內所有「已紀錄／已用」的 IP（選用 state 過濾）。

    每筆是子網路頁 IP 清單的欄位：IP、主機名稱、狀態、MAC＋廠商、擁有者、說明、備註、裝置、交換器埠、
    設備類型、OS、最後出現、探索來源。更多（旗標、主控台、逐來源時間…）用 get_ip_detail。
    結果多時用 offset 分批：回傳含 total / has_more / next_offset，需要下一批就帶 next_offset 再呼叫。
    """
    from app.services.oui import mac_prefix, vendor_map
    sub = await _resolve_subnet(session, user=user, subnet_id=subnet_id, subnet_cidr=subnet_cidr)
    lim = max(1, min(int(limit), 1000))
    off = max(int(offset), 0)
    base = select(IPAddress).where(IPAddress.subnet_id == sub.id)
    if state:
        base = base.where(IPAddress.state == state)
    total = await _count(session, base)
    rows = list((await session.execute(base.order_by(IPAddress.ip).offset(off).limit(lim))).scalars().all())
    has_more = off + len(rows) < total
    devs = await _visible_device_names(session, user, [r.device_id for r in rows])
    vendors = await vendor_map(session, [r.mac for r in rows])
    extra: dict[str, Any] = {}
    if off == 0:
        # 看得到在用、但沒有記錄的位址（指示計的「未納管」，前 50 個）：問「這個網段哪些 IP 在用」時也算
        extra["unmanaged"] = await _skipped_in_use(session, sub)
    return {
        "subnet": str(sub.cidr),
        "count": len(rows),
        "total": total,
        # 這一批不是全部（後面還有，或這是從 offset 開始的後段）
        "truncated": len(rows) < total,
        "offset": off,
        "has_more": has_more,
        "next_offset": (off + lim) if has_more else None,
        "ips": [{
            "ip": _host(r.ip), "hostname": r.hostname, "state": r.state,
            "effective_status": r.effective_status,
            "mac": str(r.mac) if r.mac else None,
            "mac_vendor": vendors.get(mac_prefix(r.mac) or "") if r.mac else None,
            "owner": r.owner, "description": r.description, "note": r.note,
            "device": devs.get(r.device_id),
            "device_id": str(r.device_id) if r.device_id else None,
            "switch_port": r.switch_port, "device_kind": r.device_kind,
            "os_family": r.os_family, "os_guess": r.os_guess,
            "last_seen": _ip_last_seen(r), "discovery_source": r.discovery_source,
        } for r in rows],
        # 放在清單後面：結果過長時從尾巴截掉的是這段，不是記錄
        **extra,
    }


# ─────────────────── 網路／整合／進階資源工具共用（2026-10-15 盤點） ───────────────────
# 工具少給一個欄位，模型就很有把握地說「系統沒有這個欄位」（list_circuits 的到期日事件）。這一批的共同修正：
# - 清單回真正的總數（LIMIT 之前算）與 truncated，模型才分得出「就這些」還是「被截掉了」
# - 外鍵給名稱、不給 UUID（防火牆、交換器、DNS 伺服器、纜線兩端…）：模型只能照抄它看到的東西
# - 時間一律 ISO 字串；機密欄位（*_enc／*_nonce／密碼／金鑰雜湊）一律不給
# - 總數與 truncated 放在清單之前：AI 對話會把過長的工具結果從尾巴截掉（services/ai._TOOL_RESULT_CAP）


def _iso_ts(v: Any) -> str | None:
    """時間戳記 → ISO 字串（畫面上是「選日期」的欄位改用 _date_only）。"""
    if v is None:
        return None
    return v.isoformat() if hasattr(v, "isoformat") else str(v)


def _inet_host(v: Any) -> str | None:
    """INET 欄位 → 純位址（拿掉 /32）。"""
    return str(v).split("/")[0] if v is not None else None


def _page_meta(total: int, returned: int) -> dict[str, Any]:
    """清單共用：total＝符合條件的總數（LIMIT 之前）、truncated＝還有沒列出來的。"""
    return {"total": int(total), "returned": int(returned), "truncated": int(total) > int(returned)}


def _clamp_limit(limit: Any, default: int = 200, cap: int = 500) -> int:
    try:
        n = int(limit)
    except (TypeError, ValueError):
        n = default
    return max(1, min(n, cap))


async def _count_rows(session: AsyncSession, stmt: Any) -> int:
    return int(await session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0)


async def _names_for_ids(session: AsyncSession, model: Any, ids: Any, attr: str = "name") -> dict[Any, Any]:
    """批次把外鍵換成名稱（一次查詢，不逐筆 session.get —— 幾百列時就是幾百次查詢）。"""
    wanted = {i for i in (ids or ()) if i is not None}
    if not wanted:
        return {}
    return dict((await session.execute(
        select(model.id, getattr(model, attr)).where(in_values(model.id, wanted)))).all())


async def _subnet_cidr_map(session: AsyncSession, ids: Any) -> dict[str, str]:
    """子網路 id（UUID 或字串；各整合的「限定子網路」兩種存法都有）→ CIDR，鍵是字串。"""
    wanted: set[uuid.UUID] = set()
    for i in ids or ():
        try:
            wanted.add(uuid.UUID(str(i)))
        except (ValueError, TypeError):
            continue
    if not wanted:
        return {}
    return {str(k): str(v) for k, v in (await session.execute(
        select(Subnet.id, Subnet.cidr).where(in_values(Subnet.id, wanted)))).all()}


def _scope_cidrs(ids: Any, cidrs: dict[str, str]) -> list[str]:
    """限定子網路 → CIDR 清單；對不到的（子網路已刪除）照樣列出 id，不默默丟掉。空清單＝不限定。"""
    return [cidrs.get(str(i), str(i)) for i in (ids or [])]


def _looks_secret(key: str) -> bool:
    k = key.lower()
    if any(w in k for w in ("pass", "secret", "token", "private", "credential")):
        return True
    return "key" in k and not k.endswith(("keyname", "key_name"))


def _public_extra_config(raw: Any) -> dict[str, Any] | None:
    """整合的 extra_config（JSON 文字）只給非機密設定：鍵名看起來像密碼／金鑰／權杖的一律拿掉。"""
    import json
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    return {k: v for k, v in data.items() if not _looks_secret(str(k))}


async def _firewall_name_map(session: AsyncSession) -> dict[str, str]:
    """六家防火牆（含 Check Point 管理伺服器、MikroTik 路由器）的 id（字串）→ 名稱。"""
    out: dict[str, str] = {}
    for _vendor, model in _firewall_vendor_models():
        out.update({str(i): n for i, n in (await session.execute(select(model.id, model.name))).all()})
    return out


def _sync_origin(origin: str | None, fw_names: dict[str, str]) -> tuple[str, str | None]:
    """同步來源 `<廠牌>:<實例 id>` → (來源, 防火牆名稱)。手動建立的是 ("manual", None)，畫面也是這樣顯示。"""
    if not origin:
        return "manual", None
    kind, _, ref = origin.partition(":")
    return kind, (fw_names.get(ref) if ref else None)


async def list_firewalls(session: AsyncSession, *, user: User, limit: int = 200) -> dict[str, Any]:
    """所有防火牆清單：OPNsense / pfSense / FortiGate / Palo Alto / Check Point（管理伺服器）/ MikroTik。
    每筆帶 `vendor` 與編輯畫面上的設定（同步項目、間隔、驗證憑證、限定子網路、VDOM／vsys／網域…），不含密鑰。

    **所有廠牌一起回**：只回其中一種的話，模型會拿一份不完整的清單當成全部去回答
    「我們有哪些防火牆」—— 那比答不出來更糟。新增廠牌時這裡一定要跟著加
    （`tests/test_integration_coverage.py` 會擋）。
    2026-10-15：以前每家各取 limit 筆、最後 out[:limit]，被截掉了卻沒說 —— 現在先算總數，回 truncated。
    """
    lim = _clamp_limit(limit, 200)
    total = 0
    rows: list[tuple[str, Any]] = []
    for vendor, model in _firewall_vendor_models():
        total += await _count_rows(session, select(model))
        if len(rows) < lim:
            rows.extend((vendor, f) for f in (await session.execute(
                select(model).order_by(model.name).limit(lim - len(rows)))).scalars().all())
    scope_ids: list[Any] = []
    for _v, f in rows:
        scope_ids.extend(getattr(f, "scope_subnet_ids", None) or [])
        scope_ids.extend((getattr(f, "iface_subnet_map", None) or {}).values())
    cidrs = await _subnet_cidr_map(session, scope_ids)
    dev_names = await _names_for_ids(session, Device, {getattr(f, "device_id", None) for _v, f in rows})
    loc_names = await _names_for_ids(session, Location, {getattr(f, "scope_location_id", None) for _v, f in rows})
    cust_names = await _names_for_ids(session, Customer, {getattr(f, "scope_customer_id", None) for _v, f in rows})
    out: list[dict[str, Any]] = []
    for vendor, f in rows:
        cols = [c.name for c in f.__table__.columns]
        item: dict[str, Any] = {
            "id": str(f.id), "vendor": vendor, "name": f.name,
            "api_url": getattr(f, "api_url", None), "enabled": f.enabled,
            "verify_tls": getattr(f, "verify_tls", None),
            "sync_interval_seconds": getattr(f, "sync_interval_seconds", None),
            # 編輯畫面上的「同步項目」勾選（sync_nat → nat）
            "sync": {c[5:]: bool(getattr(f, c)) for c in cols
                     if c.startswith("sync_") and c != "sync_interval_seconds"},
            "scope_subnets": _scope_cidrs(getattr(f, "scope_subnet_ids", None), cidrs),
            "description": getattr(f, "description", None),
            "last_sync_at": _iso_ts(f.last_sync_at), "last_error": f.last_error,
        }
        for opt in ("auto_create_ips", "expose_dsv", "api_version"):
            if opt in cols:
                item[opt] = getattr(f, opt)
        if vendor == "opnsense":
            item["scope_location"] = loc_names.get(f.scope_location_id)
            item["scope_customer"] = cust_names.get(f.scope_customer_id)
            item["iface_subnet_map"] = {k: cidrs.get(str(v), str(v))
                                        for k, v in (f.iface_subnet_map or {}).items()}
        elif vendor == "fortigate":
            item["vdoms"] = list(f.vdoms or [])
        elif vendor == "paloalto":
            item["vsys"] = list(f.vsys_list or [])
        elif vendor == "checkpoint":
            item.update({"auth_mode": f.auth_mode, "username": f.username, "domains": list(f.domains or []),
                         "packages": list(f.packages or []), "last_summary": f.last_summary})
        elif vendor == "mikrotik":
            item.update({"api_username": f.api_username, "device": dev_names.get(f.device_id),
                         "routeros_version": f.routeros_version, "board_name": f.board_name,
                         "cpu_load_limit": f.cpu_load_limit, "section_delay_ms": f.section_delay_ms,
                         "max_response_mb": f.max_response_mb})
        out.append(item)
    return {**_page_meta(total, len(out)), "firewalls": out}


def _firewall_vendor_models() -> tuple[tuple[str, Any], ...]:
    """六家防火牆整合的資料表（Check Point 的整合單位是管理伺服器、MikroTik 是路由器）。"""
    from app.models.checkpoint import CheckPointServer
    from app.models.firewall import OPNsenseFirewall
    from app.models.fortigate import FortiGateFirewall
    from app.models.mikrotik import MikroTikRouter
    from app.models.paloalto import PaloAltoFirewall
    from app.models.pfsense import PfSenseFirewall
    return (("opnsense", OPNsenseFirewall), ("pfsense", PfSenseFirewall), ("fortigate", FortiGateFirewall),
            ("paloalto", PaloAltoFirewall), ("checkpoint", CheckPointServer), ("mikrotik", MikroTikRouter))


async def list_mikrotik_rules(
    session: AsyncSession, *, user: User,
    router_name: str | None = None, table: str | None = None, limit: int = 200,
) -> dict[str, Any]:
    """MikroTik RouterOS 防火牆規則（唯讀鏡像）。可依路由器名稱與表（filter/nat/mangle）篩選。

    依 `position` 排序而不是名稱：RouterOS 由上而下比對，第一條命中就決定結果 ——
    順序本身就是語意，打散了規則集就讀不出行為。
    """
    from app.models.mikrotik import MikroTikRouter, MikroTikRule
    stmt = select(MikroTikRule, MikroTikRouter.name).join(
        MikroTikRouter, MikroTikRouter.id == MikroTikRule.router_id)
    if router_name:
        stmt = stmt.where(MikroTikRouter.name == router_name)
    if table in ("filter", "nat", "mangle"):
        stmt = stmt.where(MikroTikRule.table_name == table)
    total = await _count_rows(session, stmt)
    rows = (await session.execute(stmt.order_by(
        MikroTikRouter.name, MikroTikRule.table_name, MikroTikRule.position).limit(_clamp_limit(limit)))).all()
    return {**_page_meta(total, len(rows)), "rules": [{
        "router": name, "table": r.table_name, "chain": r.chain, "position": r.position,
        "action": r.action, "disabled": r.disabled, "protocol": r.protocol,
        "src_address": r.src_address, "dst_address": r.dst_address,
        "src_port": r.src_port, "dst_port": r.dst_port,
        "in_interface": r.in_interface, "out_interface": r.out_interface,
        "to_addresses": r.to_addresses, "to_ports": r.to_ports, "comment": r.comment,
        "synced_at": _iso_ts(r.synced_at),
    } for r, name in rows]}


async def list_mikrotik_address_lists(
    session: AsyncSession, *, user: User,
    router_name: str | None = None, list_name: str | None = None, limit: int = 300,
) -> dict[str, Any]:
    """MikroTik 的 address-list（等同其他廠牌的別名）。

    與其他家的差別：RouterOS 的清單是**逐筆位址**，不是一個物件裝很多成員 ——
    所以一列就是一個位址，回傳可能很長（動態封鎖清單常常上萬筆）。
    """
    from app.models.mikrotik import MikroTikAddressList, MikroTikRouter
    stmt = select(MikroTikAddressList, MikroTikRouter.name).join(
        MikroTikRouter, MikroTikRouter.id == MikroTikAddressList.router_id)
    if router_name:
        stmt = stmt.where(MikroTikRouter.name == router_name)
    if list_name:
        stmt = stmt.where(MikroTikAddressList.list_name == list_name)
    total = await _count_rows(session, stmt)
    rows = (await session.execute(stmt.order_by(
        MikroTikAddressList.list_name, MikroTikAddressList.address).limit(_clamp_limit(limit, 300)))).all()
    return {**_page_meta(total, len(rows)), "entries": [{
        "router": name, "list": e.list_name, "address": e.address,
        "dynamic": e.dynamic, "timeout": e.timeout, "comment": e.comment, "synced_at": _iso_ts(e.synced_at),
    } for e, name in rows]}


async def list_dhcp_ranges(
    session: AsyncSession, *, user: User, limit: int = 300,
    subnet_cidr: str | None = None, subnet_id: str | None = None,
) -> dict[str, Any]:
    """各整合同步回來的 DHCP 發放範圍（`models/dhcp.DHCP_SOURCE_TYPES` 列的每一種來源），
    加上子網路裡手動定義的 DHCP 集區（source_type=manual），以及各來源的排除區間。

    每筆帶來源整合（`source_type` / `source_name`）與 DHCP 引擎（`engine`）。「這個 IP 是不是落在
    DHCP 集區裡」這種問題要靠它，不能拿子網路去猜。排除區間會把一個設定的範圍切成好幾段各存一筆 ——
    同一個集區的各段 `pool_start`／`pool_end` 相同（0210），回答「有幾個集區」要以它合併。
    """
    from app.models.dhcp import DHCPExclusion, DHCPPoolRange
    scope_ids, scope = await _scope_subnet(
        session, user=user, subnet_cidr=subnet_cidr, subnet_id=subnet_id)
    from app.services.ip_ranges import manual_dhcp_pools
    lim = _clamp_limit(limit, 300)
    nets: list[Any] = []
    if scope_ids is not None:
        nets = [ipaddress.ip_network(str(c), strict=False) for c in (await session.execute(
            select(Subnet.cidr).where(in_values(Subnet.id, scope_ids)))).scalars().all()]

    def _inside(r: Any) -> bool:
        try:
            start = ipaddress.ip_address(str(r.start_ip))
        except ValueError:
            return False
        return any(start.version == n.version and start in n for n in nets)

    async def _rows(model: Any) -> tuple[int, list[Any]]:
        stmt = select(model).order_by(model.source_type, model.start_ip)
        if scope_ids is None:
            return await _count_rows(session, select(model)), list((await session.execute(stmt.limit(lim))).scalars().all())
        # 以「範圍的起點落在這個子網路裡」判斷（與位址範圍頁、集區使用率同一個規則）。以前拿字串欄位
        # subnet_cidr 跟子網路的 cidr 比 —— 型別不同，只要帶範圍就是 SQL 錯誤（2026-10-13 事故）；
        # 而且 pfSense 的範圍沒有 CIDR，就算型別對了也比不到。範圍表的列數是 DHCP 集區數，整張讀進來比較即可
        matched = [r for r in (await session.execute(stmt)).scalars().all() if _inside(r)]
        return len(matched), matched[:lim]

    total_synced, synced = await _rows(DHCPPoolRange)
    # 子網路裡手動定義的 DHCP 集區（issue #40），跟整合同步回來的一起列
    manual = await manual_dhcp_pools(session, list(scope_ids) if scope_ids is not None else None)
    total = total_synced + len(manual)
    rows: list[Any] = [*synced, *manual][:lim]
    ex_total, ex_rows = await _rows(DHCPExclusion)
    return {"scope": scope, "count": total, **_page_meta(total, len(rows)), "ranges": [{
        "source_type": r.source_type, "source_name": r.source_name,
        "subnet_cidr": str(r.subnet_cidr) if r.subnet_cidr else None,
        "start_ip": str(r.start_ip), "end_ip": str(r.end_ip),
        # 扣掉排除區間之前、DHCP 伺服器上設定的那一段（沒有排除的來源＝跟 start_ip／end_ip 相同）
        "pool_start": getattr(r, "pool_start", None) or str(r.start_ip),
        "pool_end": getattr(r, "pool_end", None) or str(r.end_ip),
        "family": r.family, "engine": r.source, "synced_at": _iso_ts(r.synced_at),
    } for r in rows], "exclusions_total": ex_total, "exclusions_truncated": ex_total > len(ex_rows),
        "exclusions": [{
            "source_type": e.source_type, "source_name": e.source_name,
            "subnet_cidr": e.subnet_cidr, "start_ip": e.start_ip, "end_ip": e.end_ip,
            "family": e.family, "engine": e.source, "synced_at": _iso_ts(e.synced_at),
        } for e in ex_rows]}


async def list_fortigate_policies(
    session: AsyncSession, *, user: User,
    firewall_name: str | None = None, vdom: str | None = None, limit: int = 200,
) -> dict[str, Any]:
    """FortiGate 防火牆政策（唯讀鏡像）。可依防火牆名稱與 VDOM 篩選。"""
    from app.models.fortigate import FortiGateFirewall, FortiGatePolicy
    stmt = select(FortiGatePolicy, FortiGateFirewall.name).join(
        FortiGateFirewall, FortiGateFirewall.id == FortiGatePolicy.firewall_id)
    if firewall_name:
        stmt = stmt.where(FortiGateFirewall.name == firewall_name)
    if vdom:
        stmt = stmt.where(FortiGatePolicy.vdom == vdom)
    total = await _count_rows(session, stmt)
    rows = (await session.execute(stmt.order_by(
        FortiGateFirewall.name, FortiGatePolicy.vdom, FortiGatePolicy.policyid).limit(_clamp_limit(limit)))).all()
    return {**_page_meta(total, len(rows)), "policies": [{
        "firewall": fw_name, "vdom": p.vdom, "policyid": p.policyid, "name": p.name,
        "status": p.status, "action": p.action, "srcintf": p.srcintf, "dstintf": p.dstintf,
        "srcaddr": p.srcaddr, "dstaddr": p.dstaddr, "service": p.service, "nat": p.nat,
        "comments": p.comments, "last_sync_at": _iso_ts(p.last_sync_at),
    } for p, fw_name in rows]}


async def list_fortigate_addresses(
    session: AsyncSession, *, user: User,
    firewall_name: str | None = None, vdom: str | None = None, limit: int = 300,
) -> dict[str, Any]:
    """FortiGate 位址物件與位址群組（唯讀鏡像）。群組的 `members` 是成員名稱清單。"""
    from app.models.fortigate import FortiGateAddressObject, FortiGateFirewall
    stmt = select(FortiGateAddressObject, FortiGateFirewall.name).join(
        FortiGateFirewall, FortiGateFirewall.id == FortiGateAddressObject.firewall_id)
    if firewall_name:
        stmt = stmt.where(FortiGateFirewall.name == firewall_name)
    if vdom:
        stmt = stmt.where(FortiGateAddressObject.vdom == vdom)
    total = await _count_rows(session, stmt)
    rows = (await session.execute(
        stmt.order_by(FortiGateFirewall.name, FortiGateAddressObject.vdom, FortiGateAddressObject.name)
        .limit(_clamp_limit(limit, 300)))).all()
    return {**_page_meta(total, len(rows)), "addresses": [{
        "firewall": fw_name, "vdom": a.vdom, "name": a.name, "kind": a.kind,
        "obj_type": a.obj_type, "value": a.value, "members": a.members, "comment": a.comment,
        "last_sync_at": _iso_ts(a.last_sync_at),
    } for a, fw_name in rows]}


async def list_paloalto_policies(
    session: AsyncSession, *, user: User,
    firewall_name: str | None = None, vsys: str | None = None, limit: int = 200,
) -> dict[str, Any]:
    """Palo Alto（PAN-OS）安全政策（唯讀鏡像）。可依防火牆名稱與 vsys 篩選。

    依 `position` 排序而不是名稱：PAN-OS 由上而下比對，順序本身就是語意。
    `application` 是 App-ID —— PAN-OS 規則真正在管的東西，不可以省略。
    """
    from app.models.paloalto import PaloAltoFirewall, PaloAltoPolicy
    stmt = select(PaloAltoPolicy, PaloAltoFirewall.name).join(
        PaloAltoFirewall, PaloAltoFirewall.id == PaloAltoPolicy.firewall_id)
    if firewall_name:
        stmt = stmt.where(PaloAltoFirewall.name == firewall_name)
    if vsys:
        stmt = stmt.where(PaloAltoPolicy.vsys == vsys)
    total = await _count_rows(session, stmt)
    rows = (await session.execute(stmt.order_by(
        PaloAltoFirewall.name, PaloAltoPolicy.vsys, PaloAltoPolicy.position).limit(_clamp_limit(limit)))).all()
    return {**_page_meta(total, len(rows)), "policies": [{
        "firewall": fw_name, "vsys": p.vsys, "position": p.position, "name": p.name,
        "action": p.action, "disabled": p.disabled,
        "from_zone": p.from_zone, "to_zone": p.to_zone,
        "source": p.source, "destination": p.destination,
        "application": p.application, "service": p.service, "description": p.description,
        "last_sync_at": _iso_ts(p.last_sync_at),
    } for p, fw_name in rows]}


async def list_paloalto_addresses(
    session: AsyncSession, *, user: User,
    firewall_name: str | None = None, vsys: str | None = None, limit: int = 300,
) -> dict[str, Any]:
    """Palo Alto 位址物件與位址群組（唯讀鏡像）。群組的 `members` 是成員名稱清單。"""
    from app.models.paloalto import PaloAltoAddressObject, PaloAltoFirewall
    stmt = select(PaloAltoAddressObject, PaloAltoFirewall.name).join(
        PaloAltoFirewall, PaloAltoFirewall.id == PaloAltoAddressObject.firewall_id)
    if firewall_name:
        stmt = stmt.where(PaloAltoFirewall.name == firewall_name)
    if vsys:
        stmt = stmt.where(PaloAltoAddressObject.vsys == vsys)
    total = await _count_rows(session, stmt)
    rows = (await session.execute(
        stmt.order_by(PaloAltoFirewall.name, PaloAltoAddressObject.vsys, PaloAltoAddressObject.name)
        .limit(_clamp_limit(limit, 300)))).all()
    return {**_page_meta(total, len(rows)), "addresses": [{
        "firewall": fw_name, "vsys": a.vsys, "name": a.name, "kind": a.kind,
        "obj_type": a.obj_type, "value": a.value, "members": a.members,
        "description": a.description, "last_sync_at": _iso_ts(a.last_sync_at),
    } for a, fw_name in rows]}


async def list_checkpoint_rules(
    session: AsyncSession, *, user: User,
    server_name: str | None = None, layer: str | None = None, limit: int = 200,
) -> dict[str, Any]:
    """Check Point 存取規則（唯讀鏡像，來自管理伺服器）。可依管理伺服器名稱與政策層篩選。

    依網域／政策套件／層／規則編號排序：Check Point 由上而下比對，順序本身就是語意。
    `source_negate`／`destination_negate` 為真時欄位意思是「除了這些以外」，不可以讀成正向。
    """
    from app.models.checkpoint import CheckPointRule, CheckPointServer
    stmt = select(CheckPointRule, CheckPointServer.name).join(
        CheckPointServer, CheckPointServer.id == CheckPointRule.server_id)
    if server_name:
        stmt = stmt.where(CheckPointServer.name == server_name)
    if layer:
        stmt = stmt.where(CheckPointRule.layer == layer)
    total = await _count_rows(session, stmt)
    rows = (await session.execute(stmt.order_by(
        CheckPointServer.name, CheckPointRule.domain, CheckPointRule.package, CheckPointRule.layer,
        CheckPointRule.rule_number).limit(_clamp_limit(limit)))).all()
    return {**_page_meta(total, len(rows)), "rules": [{
        "server": srv, "domain": r.domain or None, "package": r.package, "layer": r.layer,
        "section": r.section, "rule_number": r.rule_number, "name": r.name, "action": r.action,
        "enabled": r.enabled, "source": r.source, "source_negate": r.source_negate,
        "destination": r.destination, "destination_negate": r.destination_negate,
        "service": r.service, "install_on": r.install_on, "hits": r.hits, "last_hit_at": _iso_ts(r.last_hit_at),
        "comments": r.comments, "last_sync_at": _iso_ts(r.last_sync_at),
    } for r, srv in rows]}


async def list_checkpoint_objects(
    session: AsyncSession, *, user: User,
    server_name: str | None = None, name: str | None = None, limit: int = 300,
) -> dict[str, Any]:
    """Check Point 網路物件（host／network／address-range／group／group-with-exclusion，唯讀鏡像）。"""
    from app.models.checkpoint import CheckPointObject, CheckPointServer
    stmt = select(CheckPointObject, CheckPointServer.name).join(
        CheckPointServer, CheckPointServer.id == CheckPointObject.server_id)
    if server_name:
        stmt = stmt.where(CheckPointServer.name == server_name)
    if name:
        stmt = stmt.where(CheckPointObject.name.ilike(f"%{name}%"))
    total = await _count_rows(session, stmt)
    rows = (await session.execute(stmt.order_by(
        CheckPointServer.name, CheckPointObject.domain, CheckPointObject.name).limit(_clamp_limit(limit, 300)))).all()
    return {**_page_meta(total, len(rows)), "objects": [{
        "server": srv, "domain": o.domain or None, "name": o.name, "type": o.obj_type, "value": o.value,
        "members": o.members, "comments": o.comments, "last_sync_at": _iso_ts(o.last_sync_at),
    } for o, srv in rows]}


async def list_firewall_rules(
    session: AsyncSession, *, user: User,
    firewall_id: str | None = None, firewall_name: str | None = None, limit: int = 200,
) -> dict[str, Any]:
    """防火牆過濾規則（從 OPNsense 同步回來的）。每列帶防火牆名稱（有兩台以上時才分得出是哪一台的）。"""
    from app.models.firewall import OPNsenseFirewall
    from app.models.firewall_rule import OPNsenseRule
    fw_id = None
    if firewall_id:
        try:
            fw_id = uuid.UUID(firewall_id)
        except (ValueError, TypeError):
            # LLM 常把防火牆「名稱」塞進 firewall_id 欄位 → 退而當名稱查
            firewall_name = firewall_name or firewall_id
    if fw_id is None and firewall_name:
        fw = (await session.execute(
            select(OPNsenseFirewall).where(OPNsenseFirewall.name == firewall_name)
        )).scalars().first()
        if fw is None:
            raise IPAMToolError(f"firewall not found: {firewall_name}")
        fw_id = fw.id
    stmt = select(OPNsenseRule, OPNsenseFirewall.name).join(
        OPNsenseFirewall, OPNsenseFirewall.id == OPNsenseRule.firewall_id)
    if fw_id is not None:
        stmt = stmt.where(OPNsenseRule.firewall_id == fw_id)
    total = await _count_rows(session, stmt)
    rows = (await session.execute(stmt.order_by(
        OPNsenseFirewall.name, OPNsenseRule.sequence).limit(_clamp_limit(limit)))).all()
    return {**_page_meta(total, len(rows)), "rules": [{
        "firewall": fw_name, "enabled": r.enabled, "sequence": r.sequence, "action": r.action,
        "interface": r.interface, "direction": r.direction, "protocol": r.protocol,
        "source": r.source_net, "source_port": r.source_port,
        "destination": r.destination_net, "destination_port": r.destination_port,
        "description": r.description, "last_synced_at": _iso_ts(r.last_synced_at),
    } for r, fw_name in rows]}


async def list_firewall_aliases(session: AsyncSession, *, user: User, limit: int = 200) -> dict[str, Any]:
    """防火牆別名：`aliases`＝jt-ipam 依選取條件推送到 OPNsense 的別名對應；`synced_aliases`＝從
    OPNsense／pfSense 同步回來、防火牆上實際存在的別名與成員。每筆都帶防火牆名稱。"""
    from app.models.firewall import OPNsenseAliasMapping, OPNsenseFirewall, OPNsenseSyncedAlias
    from app.models.pfsense import PfSenseFirewall, PfSenseSyncedAlias
    lim = _clamp_limit(limit)
    members_cap = 50      # 封鎖清單類的別名動輒上千個成員，逐一列出會擠掉其他別名
    mstmt = select(OPNsenseAliasMapping, OPNsenseFirewall.name).join(
        OPNsenseFirewall, OPNsenseFirewall.id == OPNsenseAliasMapping.firewall_id)
    total = await _count_rows(session, mstmt)
    maps = (await session.execute(mstmt.order_by(
        OPNsenseFirewall.name, OPNsenseAliasMapping.alias_name).limit(lim))).all()
    ostmt = select(OPNsenseSyncedAlias, OPNsenseFirewall.name).join(
        OPNsenseFirewall, OPNsenseFirewall.id == OPNsenseSyncedAlias.firewall_id)
    pstmt = select(PfSenseSyncedAlias, PfSenseFirewall.name).join(
        PfSenseFirewall, PfSenseFirewall.id == PfSenseSyncedAlias.firewall_id)
    s_total = await _count_rows(session, ostmt) + await _count_rows(session, pstmt)
    synced: list[dict[str, Any]] = []

    def _members(m: Any) -> dict[str, Any]:
        lst = list(m or [])
        return {"members": lst[:members_cap], "member_count": len(lst), "members_truncated": len(lst) > members_cap}

    for a, fw_name in (await session.execute(ostmt.order_by(
            OPNsenseFirewall.name, OPNsenseSyncedAlias.name).limit(lim))).all():
        synced.append({"vendor": "opnsense", "firewall": fw_name, "name": a.name, "type": a.alias_type,
                       "enabled": a.enabled, **_members(a.content), "description": a.description,
                       "last_synced_at": _iso_ts(a.last_synced_at)})
    if len(synced) < lim:
        for a, fw_name in (await session.execute(pstmt.order_by(
                PfSenseFirewall.name, PfSenseSyncedAlias.name).limit(lim - len(synced)))).all():
            synced.append({"vendor": "pfsense", "firewall": fw_name, "name": a.name, "type": a.alias_type,
                           **_members(a.members), "description": a.descr, "last_synced_at": _iso_ts(a.last_sync_at)})
    return {**_page_meta(total, len(maps)), "synced_total": s_total, "synced_truncated": s_total > len(synced),
            "aliases": [{
                "id": str(m.id), "firewall": fw_name, "alias_name": m.alias_name, "alias_type": m.alias_type,
                # 選取條件（哪些子網路／標籤的位址會被推到這個別名）
                "selector": m.selector, "direction": m.direction,
                "last_synced_count": m.last_synced_count, "last_sync_at": _iso_ts(m.last_sync_at),
                "last_error": m.last_error,
            } for m, fw_name in maps],
            "synced_aliases": synced}


async def get_topology(
    session: AsyncSession, *, user: User,
    subnet_cidr: str | None = None, include_l3: bool = True, include_vpn: bool = True, limit: int = 150,
) -> dict[str, Any]:
    """網路拓樸（裝置 / 子網路 / VPN / 纜線 / FDB 推導的二層連線）：節點與邊。

    2026-10-15 以前只回邊（而且少了狀態、證據、埠、VPN 端點），模型答不出「這台是什麼、IP 多少」。
    節點與邊各自最多 `limit` 筆，並受字元預算限制（AI 對話會把過長的結果從尾巴硬截）；
    少列了就 `truncated=true`，要模型改帶 subnet_cidr 縮小範圍。
    """
    import json

    from app.services.topology import build_topology
    lim = _clamp_limit(limit, 150, 1000)
    subnet_ids = None
    if subnet_cidr:
        sub = await _resolve_subnet(session, user=user, subnet_id=None, subnet_cidr=subnet_cidr)
        subnet_ids = [sub.id]
    graph = await build_topology(
        session, user=user, subnet_ids=subnet_ids, include_l3=include_l3, include_vpn=include_vpn,
        scope_limited=not await has_global_read(session, user),
    )
    if graph.get("too_large"):
        return {"too_large": graph["too_large"], "truncated": True,
                "hint": "Too many devices to draw at once; call again with subnet_cidr to narrow it down."}
    labels = {n["data"]["id"]: n["data"].get("label") for n in graph["nodes"]}
    node_keys = ("id", "label", "type", "vendor", "model", "ip", "status", "os", "hardware", "sw_version",
                 "sysname", "rack", "location", "description", "host", "cluster", "endpoint", "tunnel", "vpn_type")
    edge_keys = ("kind", "label", "type", "status", "via", "evidence", "port", "peer_port", "source_port",
                 "target_port", "vlan", "direct", "port_mac_count", "a_endpoint", "b_endpoint", "ssid", "color",
                 "distance_m", "protocol")

    def _keep(d: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
        return {k: d[k] for k in keys if d.get(k) not in (None, "")}

    nodes = [_keep(n["data"], node_keys) for n in graph["nodes"]]
    edges = [{"from": labels.get(e["data"]["source"], e["data"]["source"]),
              "to": labels.get(e["data"]["target"], e["data"]["target"]),
              "from_id": e["data"]["source"], "to_id": e["data"]["target"],
              **_keep(e["data"], edge_keys)} for e in graph["edges"]]

    def _fit(items: list[dict[str, Any]], budget: int) -> list[dict[str, Any]]:
        out, used = [], 0
        for it in items[:lim]:
            used += len(json.dumps(it, ensure_ascii=False, default=str)) + 2
            if used > budget:
                break
            out.append(it)
        return out

    # 字元預算：節點先拿一半，邊用剩下的（AI 對話的單一工具結果上限約一萬兩千字元）
    node_out = _fit(nodes, 4800)
    used = len(json.dumps(node_out, ensure_ascii=False, default=str))
    edge_out = _fit(edges, max(2000, 10000 - used))
    out: dict[str, Any] = {
        "node_count": len(nodes), "edge_count": len(edges),
        "nodes_returned": len(node_out), "edges_returned": len(edge_out),
        "truncated": len(node_out) < len(nodes) or len(edge_out) < len(edges),
    }
    if out["truncated"]:
        out["hint"] = "Not every node/edge is listed; call again with subnet_cidr to narrow it down."
    if graph.get("scope") == "limited":
        out["scope"] = "limited: only the devices and subnets this account can see"
        out["hidden_layers"] = graph.get("hidden_layers", [])
    out["nodes"] = node_out
    out["edges"] = edge_out
    return out


async def list_dns_servers(session: AsyncSession, *, user: User, limit: int = 200) -> dict[str, Any]:
    """DNS 伺服器/供應商清單（PowerDNS / BIND9 / UCS / OPNsense Unbound / Windows DNS / Technitium）：
    編輯畫面的設定（同步間隔、限定子網路、比對群組、非機密的連線設定）與它有哪些 zone。"""
    from app.models.dns import DNSServer, DNSZone
    from app.models.dns_compare_group import DNSCompareGroup
    total = await _count_rows(session, select(DNSServer))
    rows = list((await session.execute(select(DNSServer).order_by(DNSServer.name).limit(_clamp_limit(limit)))).scalars().all())
    groups = await _names_for_ids(session, DNSCompareGroup, {s.compare_group_id for s in rows})
    cidrs = await _subnet_cidr_map(session, [i for s in rows for i in (s.scope_subnet_ids or [])])
    zones: dict[Any, list[str]] = {}
    if rows:
        for sid, zname in (await session.execute(
            select(DNSZone.server_id, DNSZone.name).where(in_values(DNSZone.server_id, [s.id for s in rows]))
            .order_by(DNSZone.name)
        )).all():
            zones.setdefault(sid, []).append(zname)
    return {**_page_meta(total, len(rows)), "servers": [{
        "id": str(s.id), "name": s.name, "type": s.type,
        "api_url": s.api_url, "server_address": s.server_address,
        "enabled": s.enabled, "sync_interval_seconds": s.sync_interval_seconds,
        "scope_subnets": _scope_cidrs(s.scope_subnet_ids, cidrs),
        "compare_group": groups.get(s.compare_group_id),
        # 帳號、驗證憑證、WinRM 連接埠／傳輸、TSIG 金鑰名稱這類非機密設定；密碼與金鑰在別處加密保存
        "config": _public_extra_config(s.extra_config),
        "zone_count": len(zones.get(s.id, [])), "zones": zones.get(s.id, [])[:20],
        "last_sync_at": _iso_ts(s.last_sync_at), "last_error": s.last_error,
    } for s in rows]}


async def list_dns_zones(session: AsyncSession, *, user: User, limit: int = 200) -> dict[str, Any]:
    """DNS 區域清單：所在 DNS 伺服器、關聯子網路、記錄數。"""
    from app.models.dns import DNSServer, DNSZone
    stmt = select(DNSZone, DNSServer.name, DNSServer.type).join(DNSServer, DNSServer.id == DNSZone.server_id)
    total = await _count_rows(session, stmt)
    rows = (await session.execute(stmt.order_by(DNSServer.name, DNSZone.name).limit(_clamp_limit(limit)))).all()
    cidrs = await _subnet_cidr_map(session, [i for z, _n, _t in rows for i in (z.associated_subnet_ids or [])])
    counts: dict[Any, int] = {}
    if rows:
        counts = {zid: int(n) for zid, n in (await session.execute(
            select(DNSRecord.zone_id, func.count()).where(in_values(DNSRecord.zone_id, [z.id for z, _n, _t in rows]))
            .group_by(DNSRecord.zone_id))).all()}
    return {**_page_meta(total, len(rows)), "zones": [{
        "id": str(z.id), "name": z.name, "type": z.type, "managed": z.managed,
        "server": srv, "server_type": stype,
        "associated_subnets": _scope_cidrs(z.associated_subnet_ids, cidrs),
        "record_count": counts.get(z.id, 0), "last_sync_at": _iso_ts(z.last_sync_at),
    } for z, srv, stype in rows]}


async def list_dns_records(
    session: AsyncSession, *, user: User,
    name: str | None = None, rtype: str | None = None,
    missing_ip: bool = False, limit: int = 200,
) -> dict[str, Any]:
    """List DNS records pulled from integrated servers — every type (A/AAAA/PTR/CNAME/MX/TXT/SRV/NS/SOA).
    Filter by name/value substring (name) and record type (rtype). missing_ip=true →
    only A/AAAA records whose target IP has no matching address in IPAM. Each row carries ttl, zone,
    source DNS server, has_ipam_ip (resolved by actual IP value) and consistency_state."""
    from sqlalchemy import or_ as _or

    from app.models.address import IPAddress as _IPA
    from app.models.dns import DNSRecord, DNSServer, DNSZone
    stmt = (select(DNSRecord, DNSZone.name, DNSServer.name)
            .join(DNSZone, DNSZone.id == DNSRecord.zone_id).join(DNSServer, DNSServer.id == DNSZone.server_id))
    if name:
        pat = f"%{name.strip()}%"
        stmt = stmt.where(_or(DNSRecord.name.ilike(pat), DNSRecord.value.ilike(pat)))
    if rtype:
        stmt = stmt.where(DNSRecord.type == rtype.strip().upper())
    if missing_ip:
        ip_here = (
            select(_IPA.id).where(func.host(_IPA.ip) == DNSRecord.value)
            .correlate(DNSRecord).exists()
        )
        stmt = stmt.where(DNSRecord.type.in_(("A", "AAAA")), ~ip_here)
    total = await _count_rows(session, stmt)
    rows = list((await session.execute(
        stmt.order_by(DNSRecord.name, DNSRecord.type).limit(_clamp_limit(limit)))).all())
    ip_vals = {r.value for r, _z, _s in rows if r.type in ("A", "AAAA") and r.value}
    have: set[str] = set()
    if ip_vals:
        for (host,) in (await session.execute(
            select(func.host(_IPA.ip)).where(in_values(func.host(_IPA.ip), ip_vals, type_=String()))
        )).all():
            have.add(str(host))
    return {**_page_meta(total, len(rows)), "records": [{
        "name": r.name, "type": r.type, "value": r.value, "ttl": r.ttl,
        "zone": zone, "server": srv, "source": r.source,
        "consistency_state": r.consistency_state,
        "has_ipam_ip": (r.value in have) if r.type in ("A", "AAAA") else None,
        "last_seen_at": _iso_ts(r.last_seen_at),
    } for r, zone, srv in rows]}


async def list_ip_requests(
    session: AsyncSession, *, user: User, status: str | None = None, limit: int = 200,
) -> dict[str, Any]:
    """IP 申請工作流清單：狀態、子網路、指定 IP、主機名稱、用途、說明、申請人、審核人、已配發的 IP、
    駁回原因、各時間點與多關卡審核進度。

    可見範圍與 REST 端點同一套判定，不另立規則：審核人（admin、指定審核人、多關卡模式下任一關的審核人）
    看得到全部，其餘只看自己提出的。以前這裡用「全域讀取」判斷，跟 REST 不同（2026-10-07 稽核）。
    """
    from app.models.ip_request import IPRequest, IPRequestStageApproval
    from app.services.ip_request_policy import MULTI_STEP_MODES, get_policy, is_global_approver
    limit = max(1, min(int(limit), 500))
    stmt = select(IPRequest)
    if not await is_global_approver(session, user):
        stmt = stmt.where(IPRequest.requester_user_id == user.id)
    if status:
        stmt = stmt.where(IPRequest.status == status)
    total = int(await session.scalar(
        select(func.count()).select_from(stmt.subquery())) or 0)
    rows = list((await session.execute(
        stmt.order_by(IPRequest.created_at.desc()).limit(limit))).scalars().all())
    subs = await _name_map(session, Subnet.cidr, [r.subnet_id for r in rows])
    allocated = await _name_map(session, IPAddress.ip, [r.allocated_ip_id for r in rows])
    people = await _user_names(session, [u for r in rows for u in (r.requester_user_id, r.approver_user_id)])
    # 多關卡（會簽／依序）模式：待審的申請過了幾關（一個 SQL）
    pol = await get_policy(session)
    steps_total = len(pol.get("stages") or []) if pol.get("approver_mode") in MULTI_STEP_MODES else 0
    passed: dict[Any, int] = {}
    pending = [r.id for r in rows if r.status == "pending"]
    if steps_total and pending:
        passed = {rid: int(n) for rid, n in (await session.execute(
            select(IPRequestStageApproval.request_id, func.count())
            .where(in_values(IPRequestStageApproval.request_id, pending))
            .group_by(IPRequestStageApproval.request_id))).all()}
    return {"count": total, **_page(total, len(rows)), "requests": [{
        "id": str(r.id), "status": r.status, "subnet_id": str(r.subnet_id),
        "subnet_cidr": str(subs[r.subnet_id]) if r.subnet_id in subs else None,
        "requested_ip": _host(r.requested_ip), "hostname": r.hostname, "purpose": r.purpose,
        "description": r.description,
        "requester": people.get(r.requester_user_id), "approver": people.get(r.approver_user_id),
        "allocated_ip": _host(allocated.get(r.allocated_ip_id)),
        "rejected_reason": r.rejected_reason,
        "created_at": _iso(r.created_at), "expires_at": _iso(r.expires_at),
        "approved_at": _iso(r.approved_at), "rejected_at": _iso(r.rejected_at),
        "fulfilled_at": _iso(r.fulfilled_at), "cancelled_at": _iso(r.cancelled_at),
        "approval_steps": ({"approved": passed.get(r.id, 0), "total": steps_total}
                           if steps_total and r.status == "pending" else None),
    } for r in rows]}


async def list_scan_agents(session: AsyncSession, *, user: User, limit: int = 200) -> dict[str, Any]:
    """掃描代理清單與狀態：畫面「掃描代理」頁的欄位與編輯表單（不含金鑰與權杖）。"""
    from app.models.scan_agent import ScanAgent
    from app.services.scan_load import summary
    limit = max(1, min(int(limit), 200))
    total = await _count(session, select(ScanAgent))
    rows = list((await session.execute(
        select(ScanAgent).order_by(ScanAgent.name).limit(limit))).scalars().all())
    sub_n: dict[Any, int] = {}
    if rows:
        sub_n = {aid: int(n) for aid, n in (await session.execute(
            select(Subnet.scan_agent_id, func.count()).where(in_values(Subnet.scan_agent_id, [a.id for a in rows]))
            .group_by(Subnet.scan_agent_id))).all()}
    return {"agents": [{
        "id": str(a.id), "name": a.name, "description": a.description, "enabled": a.enabled,
        # 跑在 jt-ipam 主機上的那一台（安裝時建立）
        "is_local": bool(a.is_local),
        # 掃到未登錄的位址要不要自動建立 IP 紀錄
        "auto_create_ips": bool(a.auto_create_ips),
        # 有沒有設好註冊金鑰（金鑰本身不回）
        "has_key": bool(a.enroll_key_hash),
        "last_seen_at": _iso(a.last_seen_at), "agent_version": a.agent_version, "last_error": a.last_error,
        "last_source_ip": a.last_source_ip,
        # 負責掃描的子網路數
        "subnet_count": sub_n.get(a.id, 0),
        # 此代理被允許執行的探測 / 實際裝得起的探測（os 需 nmap…）/ 各探測的間隔（秒）
        "enabled_probes": list(a.enabled_probes or []),
        "available_probes": list(a.available_probes) if a.available_probes is not None else None,
        "probe_intervals": a.probe_intervals or None,
        # 相依套件盤點：[{name, installed, version}]
        "tools": [{"name": t.get("name"), "installed": t.get("installed"), "version": t.get("version")}
                  for t in (a.tools or []) if isinstance(t, dict)] or None,
        # 最近一輪的負載摘要（ratio、level＝ok／busy／overloaded、每輪秒數…）；舊代理沒有
        "load": summary(a),
        # 主控台中繼：允許與否、同時中繼上限、允許的埠
        "relay_allowed": bool(a.relay_allowed), "relay_max_sessions": a.relay_max_sessions,
        "relay_ports": a.relay_ports,
    } for a in rows], "count": len(rows), **_page(total, len(rows))}


async def list_certificates(
    session: AsyncSession, *, user: User,
    expiring_within_days: int | None = None, limit: int = 200,
) -> dict[str, Any]:
    """集中保管的 TLS 憑證（僅中繼資料，永不回私鑰/PEM）：名稱、說明、網域、目前版本（完整指紋、序號、
    主體、簽發者、生效／到期、剩餘天數）、版本數、是否自簽、到期提醒天數、自動抓取來源（不含密碼／私鑰）。

    expiring_within_days：只列 N 天內到期的。2026-10-15 以前先依名稱取 limit 筆才篩到期 —— 排在後面的
    即將到期憑證直接被丟掉；現在篩選在資料庫做、依到期日排序。
    """
    from datetime import timedelta

    from app.models.certificate import Certificate, CertVersion
    now = datetime.now(UTC)
    stmt = select(Certificate, CertVersion).outerjoin(CertVersion, and_(
        CertVersion.certificate_id == Certificate.id, CertVersion.is_current.is_(True)))
    order: Any = Certificate.name
    if expiring_within_days is not None:
        stmt = stmt.where(CertVersion.not_after <= now + timedelta(days=max(0, int(expiring_within_days))))
        order = CertVersion.not_after
    total = await _count_rows(session, stmt)
    rows = (await session.execute(stmt.order_by(order, Certificate.name).limit(_clamp_limit(limit)))).all()
    counts: dict[Any, int] = {}
    if rows:
        counts = {cid: int(n) for cid, n in (await session.execute(
            select(CertVersion.certificate_id, func.count()).where(
                in_values(CertVersion.certificate_id, [c.id for c, _v in rows]))
            .group_by(CertVersion.certificate_id))).all()}
    out: list[dict[str, Any]] = []
    for c, cur in rows:
        out.append({
            "id": str(c.id), "name": c.name, "description": c.description,
            "domains": list((cur.domains if cur else c.domains) or []),
            "fingerprint_sha256": cur.fingerprint_sha256 if cur else None,
            "serial": cur.serial if cur else None,
            "subject": cur.subject if cur else None, "issuer": cur.issuer if cur else None,
            "not_before": _iso_ts(cur.not_before) if cur else None,
            "not_after": _iso_ts(cur.not_after) if cur else None,
            "days_remaining": (cur.not_after - now).days if cur else None,
            "version_count": counts.get(c.id, 0),
            "self_signed": bool(cur and cur.subject and cur.issuer and cur.subject == cur.issuer),
            # None＝沿用全域預設
            "expiry_warn_days": c.expiry_warn_days,
            "source_type": c.source_type, "source_config": _cert_source_config(c.source_config),
            "fetch_interval_seconds": c.fetch_interval_seconds,
            "last_fetch_at": _iso_ts(c.last_fetch_at), "last_fetch_error": c.last_fetch_error,
            "has_current_version": cur is not None,
        })
    return {**_page_meta(total, len(out)), "certificates": out}


def _cert_source_config(cfg: Any) -> dict[str, Any] | None:
    """自動抓取來源設定只給畫面上的非機密欄位（主機、埠、帳號、路徑、網址）。網址拿掉帳密與查詢字串
    （有人把權杖放在 URL 裡）；密碼與私鑰本來就另外加密保存，不在這裡。"""
    from urllib.parse import urlsplit, urlunsplit
    if not isinstance(cfg, dict):
        return None
    out: dict[str, Any] = {}
    for k in ("host", "port", "username", "cert_path", "key_path", "chain_path", "cert_url", "key_url", "chain_url"):
        v = cfg.get(k)
        if v in (None, ""):
            continue
        if k.endswith("_url") and isinstance(v, str):
            try:
                p = urlsplit(v)
                host = p.hostname or ""
                if ":" in host:
                    host = f"[{host}]"
                v = urlunsplit((p.scheme, host + (f":{p.port}" if p.port else ""), p.path, "", ""))
            except ValueError:
                v = None
        out[k] = v
    return out


async def list_cert_distribution(
    session: AsyncSession, *, user: User, limit: int = 200,
) -> dict[str, Any]:
    """憑證派送代理與各站台部署現況（無私鑰）：每個代理的說明、對應裝置、允許取用的憑證（空＝一張都不能取）、
    部署了哪張憑證/服務、是否為最新（up_to_date）或飄移、生效／到期日與剩餘天數、代理版本，
    以及同一把 Key 是否近期被多台主機共用。"""
    from datetime import UTC, datetime, timedelta

    from app.models.certificate import CertAgent, Certificate, CertVersion
    now = datetime.now(UTC)
    cur_rows = (await session.execute(
        select(Certificate, CertVersion)
        .join(CertVersion, CertVersion.certificate_id == Certificate.id)
        .where(CertVersion.is_current.is_(True)))).all()
    cur = {cert.name: ver for cert, ver in cur_rows}
    cert_names = {str(cert.id): cert.name for cert, _v in cur_rows}
    cutoff = now - timedelta(days=7)
    total = await _count_rows(session, select(CertAgent))
    agents = (await session.execute(
        select(CertAgent).order_by(CertAgent.name).limit(_clamp_limit(limit)))).scalars().all()
    missing = {str(i) for a in agents for i in (a.scope_cert_ids or []) if str(i) not in cert_names}
    if missing:
        cert_names.update({str(k): v for k, v in (await _names_for_ids(
            session, Certificate, [uuid.UUID(i) for i in missing if _looks_uuid(i)])).items()})
    devs = await _names_for_ids(session, Device, {a.device_id for a in agents})
    out: list[dict[str, Any]] = []
    for a in agents:
        recent_ips: list[str] = []
        for e in (a.recent_sources or []):
            try:
                if datetime.fromisoformat(e["at"]) >= cutoff and e["ip"] not in recent_ips:
                    recent_ips.append(e["ip"])
            except (KeyError, TypeError, ValueError):
                continue
        deps: list[dict[str, Any]] = []
        for d in (a.reported or []):
            if not isinstance(d, dict):
                continue
            ver = cur.get(d.get("cert"))
            na = ver.not_after if ver else None
            deps.append({
                "cert": d.get("cert"), "profile": d.get("profile"), "status": d.get("status"),
                "message": d.get("message"), "applied_at": d.get("applied_at"), "dry_run": d.get("dry_run"),
                "up_to_date": bool(ver and d.get("fingerprint") == ver.fingerprint_sha256),
                "not_before": _iso_ts(ver.not_before) if ver else None,
                "not_after": _iso_ts(na), "days_remaining": (na - now).days if na else None,
            })
        out.append({
            "agent": a.name, "description": a.description, "enabled": a.enabled,
            "device": devs.get(a.device_id),
            "scope_certs": [cert_names.get(str(i), str(i)) for i in (a.scope_cert_ids or [])],
            "last_seen_at": _iso_ts(a.last_seen_at),
            "last_source_ip": a.last_source_ip, "agent_version": a.agent_version,
            "recent_source_ips": recent_ips, "multi_source_recent": len(recent_ips) > 1,
            "deployments": deps,
        })
    return {**_page_meta(total, len(out)), "agents": out}


def _looks_uuid(v: Any) -> bool:
    try:
        uuid.UUID(str(v))
    except (ValueError, TypeError):
        return False
    return True


async def list_arp(
    session: AsyncSession, *, user: User,
    ip: str | None = None, mac: str | None = None, limit: int = 100,
) -> dict[str, Any]:
    """ARP 紀錄（IP↔MAC，哪台裝置在哪個介面看到的）。

    ⚠️ `arp_entries.device_id` 是 **LibreNMS 的裝置**：經 jt_ipam_device_id 換成 jt-ipam 裝置名稱，
    沒對到時給 LibreNMS 的名稱。防火牆／掃描代理的觀測沒有裝置，改看 `source` 與子網路。
    """
    stmt = select(ARPEntry)
    if ip:
        stmt = stmt.where(ARPEntry.ip == ip)
    if mac:
        stmt = stmt.where(ARPEntry.mac == mac.strip().lower())
    total = await _count_rows(session, stmt)
    rows = (await session.execute(stmt.order_by(ARPEntry.last_seen_at.desc()).limit(_clamp_limit(limit, 100)))).scalars().all()
    reporters = await _librenms_reporters(session, {e.device_id for e in rows})
    cidrs = await _subnet_cidr_map(session, {e.subnet_id for e in rows if e.subnet_id})
    return {**_page_meta(total, len(rows)), "arp": [{
        "ip": _inet_host(e.ip), "mac": str(e.mac),
        "device": reporters.get(e.device_id, (None, None))[0],
        "librenms_device": reporters.get(e.device_id, (None, None))[1],
        "interface": e.interface, "vrf": e.vrf, "source": e.source,
        "subnet": cidrs.get(str(e.subnet_id)) if e.subnet_id else None,
        "first_seen_at": _iso_ts(e.first_seen_at), "last_seen_at": _iso_ts(e.last_seen_at),
    } for e in rows]}


async def _librenms_reporters(session: AsyncSession, ln_ids: Any) -> dict[Any, tuple[str | None, str | None]]:
    """LibreNMS 裝置 id → (jt-ipam 裝置名稱（對不到時用 LibreNMS 名稱）, LibreNMS 名稱)。批次查詢。"""
    ids = {i for i in (ln_ids or ()) if i is not None}
    if not ids:
        return {}
    rows = (await session.execute(
        select(LibreNMSDevice.id, LibreNMSDevice.sysname, LibreNMSDevice.hostname, Device.name)
        .outerjoin(Device, Device.id == LibreNMSDevice.jt_ipam_device_id)
        .where(in_values(LibreNMSDevice.id, ids)))).all()
    return {i: (dev or sysname or host, sysname or host) for i, sysname, host, dev in rows}


async def list_fdb(
    session: AsyncSession, *, user: User, mac: str | None = None, limit: int = 50,
    subnet_cidr: str | None = None, subnet_id: str | None = None,
) -> dict[str, Any]:
    """交換器 FDB 紀錄（MAC↔埠）。問某網段的機器接在哪要帶 subnet_cidr。

    `switch`＝回報的交換器名稱：LibreNMS 的列經 jt_ipam_device_id 換成 jt-ipam 裝置（`device_id` 是
    LibreNMS 裝置），MikroTik 的列看 switch_device_id（0170）。
    """
    scope_ids, scope = await _scope_subnet(
        session, user=user, subnet_cidr=subnet_cidr, subnet_id=subnet_id)
    stmt = select(FDBEntry)
    if mac:
        stmt = stmt.where(FDBEntry.mac == mac.strip().lower())
    if scope_ids is not None:
        # FDB 只有 MAC 沒有 IP → 以該網段 IP 已知的 MAC 反查
        stmt = stmt.where(FDBEntry.mac.in_(
            select(IPAddress.mac).where(
                in_values(IPAddress.subnet_id, scope_ids), IPAddress.mac.is_not(None))))
    total = await _count_rows(session, stmt)
    rows = (await session.execute(stmt.order_by(FDBEntry.last_seen_at.desc()).limit(_clamp_limit(limit, 50)))).scalars().all()
    reporters = await _librenms_reporters(session, {e.device_id for e in rows})
    switches = await _names_for_ids(session, Device, {e.switch_device_id for e in rows})
    return {"scope": scope, "count": total, **_page_meta(total, len(rows)), "fdb": [{
        "mac": str(e.mac), "vlan": e.vlan_id_num, "port": e.port_name,
        "switch": switches.get(e.switch_device_id) if e.switch_device_id
        else reporters.get(e.device_id, (None, None))[0],
        "source": e.source,
        "first_seen_at": _iso_ts(e.first_seen_at), "last_seen_at": _iso_ts(e.last_seen_at),
    } for e in rows]}


async def wazuh_missing_agents(
    session: AsyncSession, *, user: User, limit: int = 200,
    subnet_cidr: str | None = None, subnet_id: str | None = None,
) -> dict[str, Any]:
    """有設主機名稱、卻沒裝 Wazuh agent 的 IP（資安覆蓋缺口）。

    問「某網段有誰沒裝」一定要帶 subnet_cidr/subnet_id：不帶會回全站缺口，
    模型就會把別的網段當成該網段的答案（曾實際發生：問 1.0/24 卻回 11.x/40.x）。
    """
    from app.models.wazuh import WazuhInstance
    from app.services.agent_scope import expected_subnets
    from app.services.wazuh import find_missing_agents
    scope_ids: list[uuid.UUID] | None = None
    scope_label = "all"
    if subnet_cidr or subnet_id:
        subnet = await _resolve_subnet(
            session, user=user, subnet_id=subnet_id, subnet_cidr=subnet_cidr)
        scope_ids = [subnet.id]
        scope_label = str(subnet.cidr)
    else:
        # 沒指定網段 → 跟畫面一致，只看 Wazuh 整合設定的「限定子網路範圍」
        scope_ids = expected_subnets(list((await session.execute(
            select(WazuhInstance).where(WazuhInstance.enabled.is_(True)))).scalars().all()))
        if scope_ids is not None:
            scope_label = "integration_scope"
    rows = await find_missing_agents(session, subnet_ids=scope_ids)
    limit = max(1, int(limit))
    page = rows[:limit]
    # 這一頁補上畫面「缺口」清單看得到的脈絡：上線狀態、設備類型、OS、子網路、區段、單位，
    # 以及「有 agent 但不是 active」的狀態（disconnected／never_connected 跟根本沒裝是兩回事）。全部批次查
    from app.models.wazuh import WazuhAgent
    ids = [uuid.UUID(r["ip_address_id"]) for r in page]
    info: dict[Any, Any] = {}
    agent_status: dict[Any, str] = {}
    if ids:
        info = {r.id: r for r in (await session.execute(
            select(IPAddress.id, IPAddress.state, IPAddress.effective_status, IPAddress.device_kind,
                   IPAddress.os_family, IPAddress.subnet_id, IPAddress.customer_id)
            .where(in_values(IPAddress.id, ids)))).all()}
        for aid, st in (await session.execute(
                select(WazuhAgent.jt_ipam_address_id, WazuhAgent.status)
                .where(in_values(WazuhAgent.jt_ipam_address_id, ids)))).all():
            agent_status.setdefault(aid, st)
    sub_rows = {}
    sub_ids = {i.subnet_id for i in info.values()}
    if sub_ids:
        sub_rows = {sid: (cidr, sec_id, cust_id) for sid, cidr, sec_id, cust_id in (await session.execute(
            select(Subnet.id, Subnet.cidr, Subnet.section_id, Subnet.customer_id)
            .where(in_values(Subnet.id, list(sub_ids))))).all()}
    secs = await _name_map(session, Section.name, [v[1] for v in sub_rows.values()])
    custs = await _name_map(session, Customer.name,
                            [i.customer_id for i in info.values()] + [v[2] for v in sub_rows.values()])
    out = []
    for r in page:
        i = info.get(uuid.UUID(r["ip_address_id"]))
        sr = sub_rows.get(i.subnet_id) if i is not None else None
        out.append({
            **r,
            "state": i.state if i else None, "effective_status": i.effective_status if i else None,
            "device_kind": i.device_kind if i else None, "os_family": i.os_family if i else None,
            "subnet": str(sr[0]) if sr else None, "section": secs.get(sr[1]) if sr else None,
            "customer": custs.get(i.customer_id or (sr[2] if sr else None)) if i else None,
            "wazuh_agent_status": agent_status.get(uuid.UUID(r["ip_address_id"])),
        })
    return {
        "scope": scope_label,          # 回答時必須說明涵蓋範圍
        "missing_count": len(rows),    # 此範圍內的總數（非全站）
        **_page(len(rows), len(out)),
        "missing": out,
    }



async def list_attack_surface(
    session: AsyncSession, *, user: User, fqdn: str | None = None,
    ip: str | None = None, limit: int = 200,
) -> dict[str, Any]:
    """對外開放服務清單（從外面可達的 IP:port，每項配 IPAM 身分與 DNS 名稱）。

    支援兩種問法，因為人記得的常是名字不是位址：
    - `fqdn="meet.example.net"` → 先由 DNS 記錄對應到 IP，再列該 IP 的對外開口
    - `ip="198.51.100.7"` → 直接列該位址的開口
    兩者都不給就是全部（回傳的 `scope` 會標明）。
    2026-10-15：服務層以前先截 300 筆 NAT／500 項才讓這裡篩選，排在後面的目標就不見了 —— 現在全部取回再篩。
    """
    from app.services.fw_lookup import attack_surface

    items = await attack_surface(session, limit=None, nat_limit=None)
    scope = "all"
    if fqdn:
        want = fqdn.strip().rstrip(".").lower()
        items = [i for i in items
                 if any(f.lower() == want for f in (i["identity"].get("fqdns") or []))]
        scope = f"fqdn:{want}"
    elif ip:
        want_ip = ip.strip()
        items = [i for i in items if str(i["identity"].get("ip") or "").split("/")[0] == want_ip]
        scope = f"ip:{want_ip}"
    n = _clamp_limit(limit)
    shown = items[:n]
    from app.services.surface_service import enrich
    shown = await enrich(session, shown)   # 服務判定附證據等級：只有埠號推測的不可以當事實講
    return {
        "scope": scope,
        "count": len(items),
        **_page_meta(len(items), len(shown)),
        # 未登錄的目標本身就是警訊，一併帶出來讓對話端可以指出來
        "items": [{
            "ip": _inet_host(i["identity"].get("ip")),
            "registered": i["identity"].get("registered"),
            "hostname": i["identity"].get("hostname"),
            "fqdns": i["identity"].get("fqdns") or [],
            "port": i.get("port"), "protocol": i.get("protocol"),
            "via": i.get("via"), "source": i.get("source"),
            "firewall": i.get("firewall"), "name": i.get("name"), "descr": i.get("descr"),
            "customer": i["identity"].get("customer"),
            "subnet": i["identity"].get("subnet"),
            "device_kind": i["identity"].get("device_kind"),
            "device_model": i["identity"].get("device_model"),
            "wazuh": i["identity"].get("wazuh"),
            "status": i["identity"].get("status"),
            "service": i.get("service"),
        } for i in shown],
        # 外部 DNS：這些名稱從網際網路上查到什麼、那個位址是誰的、跟內部一不一致（設定了才有，2026-10-19）
        "external_dns": await _external_dns_for(session, shown),
    }


async def _external_dns_for(session: AsyncSession, items: list[dict[str, Any]]) -> dict[str, Any] | None:
    from app.services import external_dns as xdns
    if not xdns.configured(await xdns.get_settings(session)):
        return None
    want = {str(f).lower().rstrip(".") for i in items for f in (i["identity"].get("fqdns") or [])}
    if not want:
        return {}
    names = (await xdns.view(session))["names"]
    return {f: {k: names[f][k] for k in ("status", "addresses", "owners", "checked_at")}
            for f in sorted(want) if f in names}


async def get_customer_summary(
    session: AsyncSession, *, user: User,
    customer_id: str | None = None, name: str | None = None,
) -> dict[str, Any]:
    """單一客戶/單位的完整資料（名稱、顯示名稱、說明、聯絡人、Email、電話、地址）＋掛載統計
    （區段／子網路／裝置／IP／地點）。"""
    cust: Customer | None = None
    if customer_id:
        cust = await session.get(Customer, _as_uuid(customer_id, "customer_id"))
    elif name:
        cust = (await session.execute(
            select(Customer).where(Customer.name == name)
        )).scalars().first()
    if cust is None:
        raise IPAMToolError("customer not found")
    # RBAC：客戶不在可見範圍 → 當作查無，不洩漏
    vis = await visible_ids(session, user=user, object_type="customer")
    if vis is not None and cust.id not in vis:
        raise IPAMToolError("customer not found")
    from sqlalchemy import func as _f
    n_sec = await session.scalar(select(_f.count()).select_from(Section).where(Section.customer_id == cust.id))
    n_sub = await session.scalar(select(_f.count()).select_from(Subnet).where(Subnet.customer_id == cust.id))
    n_dev = await session.scalar(select(_f.count()).select_from(Device).where(Device.customer_id == cust.id))
    n_ip = await session.scalar(select(_f.count()).select_from(IPAddress).where(IPAddress.customer_id == cust.id))
    n_loc = await session.scalar(select(_f.count()).select_from(Location).where(Location.customer_id == cust.id))
    return {
        # 單位編輯表單的每個欄位
        "id": str(cust.id), "name": cust.name, "title": cust.title, "description": cust.description,
        "contact": cust.contact, "email": cust.email, "phone": cust.phone, "address": cust.address,
        **await _timestamps(session, Customer, cust.id),
        "sections": int(n_sec or 0), "subnets": int(n_sub or 0),
        "devices": int(n_dev or 0), "ips": int(n_ip or 0), "locations": int(n_loc or 0),
    }


async def list_vms(
    session: AsyncSession, *, user: User, limit: int = 200,
    subnet_cidr: str | None = None, subnet_id: str | None = None,
) -> dict[str, Any]:
    """虛擬機清單（Proxmox VE、VMware、Hyper-V、CubeCOS／OpenStack 同步回來）：VMID、叢集、平台、節點、規格、作業系統、
    主要 IP、對應裝置、租戶、說明（OpenStack 寫的是 project）、網卡。
    問某網段有哪些 VM 要帶 subnet_cidr。"""
    from app.models.advanced import Tenant
    from app.models.virt import VirtCluster, VirtualMachine, VMInterface
    scope_ids, scope = await _scope_subnet(
        session, user=user, subnet_cidr=subnet_cidr, subnet_id=subnet_id)
    stmt = select(VirtualMachine)
    if scope_ids is not None:
        stmt = stmt.where(VirtualMachine.primary_ip_id.in_(
            select(IPAddress.id).where(in_values(IPAddress.subnet_id, scope_ids))))
    total = await _count_rows(session, stmt)
    rows = list((await session.execute(stmt.order_by(VirtualMachine.name).limit(_clamp_limit(limit)))).scalars().all())
    # 一次撈齊（以前每台 VM 各查網卡、IP、裝置 —— 兩百台就是六百次查詢）
    ifaces: dict[Any, list[Any]] = {}
    if rows:
        for i in (await session.execute(select(VMInterface).where(
                in_values(VMInterface.vm_id, [v.id for v in rows])).order_by(VMInterface.name))).scalars().all():
            ifaces.setdefault(i.vm_id, []).append(i)
    ip_ids = {v.primary_ip_id for v in rows if v.primary_ip_id}
    ips = {i: _inet_host(x) for i, x in (await session.execute(
        select(IPAddress.id, IPAddress.ip).where(in_values(IPAddress.id, ip_ids)))).all()} if ip_ids else {}
    devs = await _names_for_ids(session, Device, {v.device_id for v in rows})
    clusters = await _names_for_ids(session, VirtCluster, {v.cluster_id for v in rows})
    # 平台（proxmox／vmware／hyper-v／openstack）：「這台是哪個平台的 VM」不必從叢集名稱猜
    platforms = await _names_for_ids(session, VirtCluster, {v.cluster_id for v in rows}, attr="type")
    tenants = await _names_for_ids(session, Tenant, {v.tenant_id for v in rows})
    vlans = await _names_for_ids(session, VLAN, {i.vlan_id for lst in ifaces.values() for i in lst}, attr="number")
    out = [{
        "id": str(v.id), "name": v.name,
        # Proxmox 的 VMID（畫面上的「VMID」欄）
        "vmid": v.legacy_vmid, "external_id": v.external_id,
        "cluster": clusters.get(v.cluster_id), "platform": platforms.get(v.cluster_id),
        # 節點：OpenStack 沒有權限看主機欄位時是 null（不知道，不是沒有）
        "node": v.node, "kind": v.kind, "status": v.status,
        "vcpus": v.vcpus, "memory_mb": v.memory_mb, "disk_gb": v.disk_gb,
        # 客體作業系統（Hyper-V 的 KVP、SCVMM；0222）
        "os": v.os_name,
        "is_template": v.is_template,
        "primary_ip": ips.get(v.primary_ip_id),
        "device": devs.get(v.device_id), "tenant": tenants.get(v.tenant_id),
        "description": v.description,
        "interfaces": [{
            "name": i.name, "mac": str(i.mac) if i.mac else None, "primary_ip": _inet_host(i.primary_ip),
            "bridge": i.bridge, "vlan": vlans.get(i.vlan_id),
        } for i in ifaces.get(v.id, [])],
    } for v in rows]
    return {"scope": scope, "count": total, **_page_meta(total, len(out)), "vms": out}


async def list_wireless_links(session: AsyncSession, *, user: User, limit: int = 200) -> dict[str, Any]:
    """無線連線（point-to-point）：兩端裝置、SSID、距離、說明。"""
    from app.models.advanced import WirelessLink
    total = await _count_rows(session, select(WirelessLink))
    rows = (await session.execute(select(WirelessLink).order_by(WirelessLink.name).limit(_clamp_limit(limit)))).scalars().all()
    devs = await _names_for_ids(session, Device, {d for w in rows for d in (w.a_device_id, w.b_device_id)})
    return {**_page_meta(total, len(rows)), "wireless_links": [{
        "id": str(w.id), "name": w.name, "ssid": w.ssid,
        "a_device": devs.get(w.a_device_id), "b_device": devs.get(w.b_device_id),
        "distance_m": w.distance_m, "description": w.description,
    } for w in rows]}


async def list_ssids(session: AsyncSession, *, user: User, limit: int = 200) -> dict[str, Any]:
    """無線 SSID 清單（認證方式 / VLAN / 租戶 / 說明）。"""
    from app.models.advanced import Tenant, WirelessSSID
    total = await _count_rows(session, select(WirelessSSID))
    rows = (await session.execute(select(WirelessSSID).order_by(WirelessSSID.ssid).limit(_clamp_limit(limit)))).scalars().all()
    vlans: dict[Any, tuple[Any, Any]] = {}
    vids = {s.vlan_id for s in rows if s.vlan_id}
    if vids:
        vlans = {i: (num, nm) for i, num, nm in (await session.execute(
            select(VLAN.id, VLAN.number, VLAN.name).where(in_values(VLAN.id, vids)))).all()}
    tenants = await _names_for_ids(session, Tenant, {s.tenant_id for s in rows})
    return {"count": total, **_page_meta(total, len(rows)), "ssids": [{
        "id": str(s.id), "ssid": s.ssid, "auth_type": s.auth_type,
        "vlan": vlans.get(s.vlan_id, (None, None))[0], "vlan_name": vlans.get(s.vlan_id, (None, None))[1],
        "tenant": tenants.get(s.tenant_id), "description": s.description,
    } for s in rows]}


def _date_only(v: Any) -> str | None:
    """畫面上是「選日期」的欄位（存成 UTC 午夜）→ 只給日期，模型才不會拿時區去換算出前一天。"""
    if v is None:
        return None
    return (v.astimezone(UTC) if getattr(v, "tzinfo", None) else v).date().isoformat()


async def list_circuits(session: AsyncSession, *, user: User, limit: int = 200) -> dict[str, Any]:
    """電路 / 線路清單：畫面「進階 → 電路」編輯對話框的每個欄位都要回傳。

    2026-10-15 以前少了開通日、合約到期、DNS、租戶 —— 使用者問到期日記在哪，AI 就說電路沒有這個欄位。"""
    from app.models.advanced import Circuit, CircuitType, Provider, Tenant
    total = (await session.execute(select(func.count()).select_from(Circuit))).scalar_one()
    rows = list((await session.execute(
        select(Circuit).order_by(Circuit.cid).limit(min(int(limit), 500))
    )).scalars().all())
    today = datetime.now(UTC).date()
    extra = await _circuit_extras(session, user, rows)
    out = []
    for c in rows:
        prov = await session.get(Provider, c.provider_id) if c.provider_id else None
        ctype = await session.get(CircuitType, c.type_id) if c.type_id else None
        dev = await session.get(Device, c.device_id) if c.device_id else None
        tenant = await session.get(Tenant, c.tenant_id) if c.tenant_id else None
        end = _date_only(c.contract_end_date)
        out.append({
            "id": str(c.id), "cid": c.cid, "provider": prov.name if prov else None,
            "type": ctype.name if ctype else None, "status": c.status,
            "up_kbps": c.up_kbps, "down_kbps": c.down_kbps, "commit_rate_kbps": c.commit_rate_kbps,
            "install_date": _date_only(c.install_date), "contract_end_date": end,
            "contract_days_left": (datetime.fromisoformat(end).date() - today).days if end else None,
            "ip_address": c.ip_address, "gateway": c.gateway, "netmask": c.netmask,
            "dns_servers": c.dns_servers, "device": dev.name if dev else None,
            "tenant": tenant.name if tenant else None,
            "monthly_fee_cents": c.monthly_fee_cents, "description": c.description,
            **extra.get(c.id, {"wan_addresses": [], "contacts": [], "leased_devices": []}),
        })
    return {"circuits": out, "count": len(out), "total": total, "truncated": total > len(out)}


async def _circuit_extras(session: AsyncSession, user: User, rows: list[Any]) -> dict[Any, dict[str, Any]]:
    """電路詳細資料的 WAN 位址（照子網路權限）、聯絡人（電路與供應商的）、租用設備（照裝置權限）。"""
    from app.models.advanced import (
        CircuitAddress,
        Contact,
        ContactAssignment,
        ContactRole,
        DeviceLease,
    )
    from app.models.subnet import Subnet
    from app.services.permission import visible_ids
    if not rows:
        return {}
    ids = [c.id for c in rows]
    out: dict[Any, dict[str, Any]] = {c.id: {"wan_addresses": [], "contacts": [], "leased_devices": []} for c in rows}
    vis = await visible_ids(session, user=user, object_type="subnet", required="read")
    for link, cidr, ip, ip_sub in (await session.execute(
            select(CircuitAddress, Subnet.cidr, IPAddress.ip, IPAddress.subnet_id)
            .outerjoin(Subnet, Subnet.id == CircuitAddress.subnet_id)
            .outerjoin(IPAddress, IPAddress.id == CircuitAddress.address_id)
            .where(in_values(CircuitAddress.circuit_id, ids)))).all():
        sub_id = link.subnet_id or ip_sub
        if vis is not None and sub_id not in vis:
            continue
        addr = str(cidr) if cidr is not None else (str(ip).split("/")[0] if ip is not None else None)
        if addr:
            out[link.circuit_id]["wan_addresses"].append({"role": link.role, "address": addr})
    prov_of = {c.provider_id: [] for c in rows}
    for c in rows:
        prov_of[c.provider_id].append(c.id)
    for asg, who, role in (await session.execute(
            select(ContactAssignment, Contact, ContactRole)
            .join(Contact, Contact.id == ContactAssignment.contact_id)
            .outerjoin(ContactRole, ContactRole.id == ContactAssignment.role_id)
            .where(((ContactAssignment.object_type == "circuit") & in_values(ContactAssignment.object_id, ids))
                   | ((ContactAssignment.object_type == "provider")
                      & in_values(ContactAssignment.object_id, list(prov_of))))
            .order_by(ContactAssignment.priority.desc(), Contact.name))).all():
        item = {"name": who.name, "role": (role.builtin_key or role.name) if role else None,
                "phone": who.phone, "email": who.email, "of": asg.object_type}
        targets = [asg.object_id] if asg.object_type == "circuit" else prov_of.get(asg.object_id, [])
        for cid in targets:
            if cid in out:
                out[cid]["contacts"].append(item)
    dev_vis = await visible_ids(session, user=user, object_type="device", required="read")
    for lease, name in (await session.execute(
            select(DeviceLease, Device.name).join(Device, Device.id == DeviceLease.device_id)
            .where(in_values(DeviceLease.circuit_id, ids)).order_by(Device.name))).all():
        if dev_vis is not None and lease.device_id not in dev_vis:
            continue
        out[lease.circuit_id]["leased_devices"].append(name)
    return out


async def list_domains(session: AsyncSession, *, user: User, limit: int = 200) -> dict[str, Any]:
    """網域清單（畫面「進階 → 網域」的每個欄位）：註冊商（手填或 RDAP）、註冊商網址與帳號名稱（不存密碼）、
    DNS 託管（手填或由 NS 推測）、NS、到期日與剩餘天數、自動續約、綁著的服務。"""
    from app.models.domain import Domain
    from app.services.domains import summary_for_ai
    total = (await session.execute(select(func.count()).select_from(Domain))).scalar_one()
    rows = list((await session.execute(select(Domain).order_by(Domain.name).limit(min(int(limit), 500)))).scalars())
    out = [summary_for_ai(d) for d in rows]
    return {"domains": out, "count": len(out), "total": total, "truncated": total > len(out)}


async def list_maintenance(session: AsyncSession, *, user: User, status: str = "all",
                           limit: int = 100) -> dict[str, Any]:
    """維護排程（畫面「維護排程」）：進行中、即將開始、已結束；只列看得到範圍的（與 REST 同一套可見性）。"""
    from app.api.v1.endpoints.maintenance import list_windows
    if status not in ("active", "upcoming", "past", "all"):
        raise IPAMToolError("status 只能是 active、upcoming、past 或 all")
    res = await list_windows(user=user, session=session, status=status,  # type: ignore[arg-type]
                             limit=max(1, min(int(limit), 500)))
    return {"maintenance": res["items"], "count": len(res["items"])}


async def list_providers(session: AsyncSession, *, user: User, limit: int = 200) -> dict[str, Any]:
    """電信 / 線路供應商清單（ASN、帳號、入口網址、NOC 聯絡方式、說明、電路數）。"""
    from app.models.advanced import Circuit, Provider
    total = await _count_rows(session, select(Provider))
    rows = (await session.execute(select(Provider).order_by(Provider.name).limit(_clamp_limit(limit)))).scalars().all()
    circuits: dict[Any, int] = {}
    if rows:
        circuits = {pid: int(n) for pid, n in (await session.execute(
            select(Circuit.provider_id, func.count()).where(in_values(Circuit.provider_id, [p.id for p in rows]))
            .group_by(Circuit.provider_id))).all()}
    return {**_page_meta(total, len(rows)), "providers": [{
        "id": str(p.id), "name": p.name, "asn": p.asn, "account_number": p.account_number,
        "portal_url": p.portal_url, "noc_contact": p.noc_contact, "description": p.description,
        "circuit_count": circuits.get(p.id, 0),
    } for p in rows]}


async def list_asns(session: AsyncSession, *, user: User, limit: int = 200) -> dict[str, Any]:
    """自治系統號碼（ASN）清單（RIR、歸屬租戶、說明）。"""
    from app.models.advanced import ASN, Tenant
    total = await _count_rows(session, select(ASN))
    rows = (await session.execute(select(ASN).order_by(ASN.asn).limit(_clamp_limit(limit)))).scalars().all()
    tenants = await _names_for_ids(session, Tenant, {a.tenant_id for a in rows})
    return {**_page_meta(total, len(rows)), "asns": [{
        "id": str(a.id), "asn": a.asn, "rir": a.rir, "tenant": tenants.get(a.tenant_id),
        "description": a.description,
    } for a in rows]}


async def list_tenants(session: AsyncSession, *, user: User, limit: int = 200) -> dict[str, Any]:
    """租戶（Tenant）清單（代稱、租戶群組、說明）。"""
    from app.models.advanced import Tenant, TenantGroup
    total = await _count_rows(session, select(Tenant))
    rows = (await session.execute(select(Tenant).order_by(Tenant.name).limit(_clamp_limit(limit)))).scalars().all()
    groups = await _names_for_ids(session, TenantGroup, {t.group_id for t in rows})
    return {**_page_meta(total, len(rows)), "tenants": [{
        "id": str(t.id), "name": t.name, "slug": t.slug, "group": groups.get(t.group_id),
        "description": t.description,
    } for t in rows]}


async def list_contacts(session: AsyncSession, *, user: User, limit: int = 200) -> dict[str, Any]:
    """聯絡人清單（名稱 / 職稱 / 電話 / Email / 地址 / 聯絡人群組 / 租戶 / 說明）。"""
    from app.models.advanced import Contact, ContactGroup, Tenant
    total = await _count_rows(session, select(Contact))
    rows = (await session.execute(select(Contact).order_by(Contact.name).limit(_clamp_limit(limit)))).scalars().all()
    groups = await _names_for_ids(session, ContactGroup, {c.group_id for c in rows})
    tenants = await _names_for_ids(session, Tenant, {c.tenant_id for c in rows})
    return {**_page_meta(total, len(rows)), "contacts": [{
        "id": str(c.id), "name": c.name, "title": c.title, "phone": c.phone,
        "email": c.email, "address": c.address, "group": groups.get(c.group_id),
        "tenant": tenants.get(c.tenant_id), "description": c.description,
    } for c in rows]}


async def list_cables(session: AsyncSession, *, user: User, limit: int = 200) -> dict[str, Any]:
    """佈線 / 纜線清單：線標、類型、顏色、長度、狀態、說明，以及兩端接到哪台裝置的哪個埠。"""
    from app.models.physical import Cable
    total = await _count_rows(session, select(Cable))
    rows = list((await session.execute(
        select(Cable).order_by(Cable.created_at.desc()).limit(_clamp_limit(limit)))).scalars().all())
    ends = await _cable_ends(session, [c.id for c in rows])
    return {"count": total, **_page_meta(total, len(rows)), "cables": [{
        "id": str(c.id), "label": c.label, "type": c.type, "color": c.color,
        "length_m": float(c.length_m) if c.length_m is not None else None, "status": c.status,
        "description": c.description, "terminations": ends.get(c.id, []),
    } for c in rows]}


async def _cable_ends(session: AsyncSession, cable_ids: list[Any]) -> dict[Any, list[dict[str, Any]]]:
    """纜線兩端 → 可讀的裝置／埠。畫面建立的纜線端點是 `device_port`（埠 → 所屬裝置），舊資料可能直接是
    `device`（埠名在 port_label）。批次查詢：一頁幾百條纜線也只查三次。"""
    from app.models.physical import CableTermination, DevicePort
    if not cable_ids:
        return {}
    terms = list((await session.execute(select(CableTermination).where(
        in_values(CableTermination.cable_id, cable_ids)))).scalars().all())
    ports = {p.id: p for p in (await session.execute(select(DevicePort).where(in_values(
        DevicePort.id, [t.object_id for t in terms if t.object_type == "device_port"])))).scalars().all()}
    devs = await _names_for_ids(session, Device, {t.object_id for t in terms if t.object_type == "device"}
                              | {p.device_id for p in ports.values()})
    out: dict[Any, list[dict[str, Any]]] = {}
    for t in sorted(terms, key=lambda x: x.side):
        end: dict[str, Any] = {"side": t.side, "object_type": t.object_type}
        if t.object_type == "device_port" and t.object_id in ports:
            p = ports[t.object_id]
            end.update({"device": devs.get(p.device_id), "port": p.name, "port_type": p.type})
        elif t.object_type == "device":
            end.update({"device": devs.get(t.object_id), "port": t.port_label})
        else:
            end.update({"object_id": str(t.object_id), "port": t.port_label})
        out.setdefault(t.cable_id, []).append(end)
    return out


async def cable_trace(session: AsyncSession, *, user: User, cable_id: str) -> dict[str, Any]:
    """追蹤一條纜線：兩端接到哪台裝置的哪個埠，以及多跳路徑（沿纜線走、穿過跳接面板的前後埠，
    直到兩邊的終點；與畫面「追蹤」同一套走法 —— 直接呼叫 /ports/{id}/trace 的實作）。"""
    from fastapi import HTTPException

    from app.api.v1.endpoints.physical import trace_port
    from app.models.physical import Cable, CableTermination
    c = await session.get(Cable, _as_uuid(cable_id, "cable_id"))
    if c is None:
        raise IPAMToolError("cable not found")
    terms = {t.side: t for t in (await session.execute(
        select(CableTermination).where(CableTermination.cable_id == c.id))).scalars().all()}

    async def _trace(side: str) -> dict[str, Any] | None:
        t = terms.get(side)
        if t is None or t.object_type != "device_port":
            return None
        try:
            return await trace_port(t.object_id, user, session)
        except HTTPException:
            return None

    def _node(n: dict[str, Any]) -> dict[str, Any]:
        if "device_name" in n:
            return {"device": n.get("device_name"), "port": n.get("port_name"), "port_type": n.get("port_type")}
        return {"object_type": n.get("object_type"), "port": n.get("port_name")}

    def _via(h: dict[str, Any]) -> str:
        if h.get("internal"):
            return "internal (front/rear or bridge pass-through)"
        return " ".join(str(x) for x in (h.get("cable_label") or "cable", h.get("cable_type"), h.get("cable_color"))
                        if x)

    ta, tb = await _trace("A"), await _trace("B")
    path: list[dict[str, Any]] = []
    if ta:
        a_nodes = [_node(n) for n in ta["nodes"]]
        a_vias = [None] + [_via(h) for h in ta["hops"]]
        if tb and len(tb["nodes"]) > 1 and tb["nodes"][1].get("port_id") == ta["nodes"][0].get("port_id"):
            # B 端往回走會先經過這條纜線到 A，再穿過 A 背後 —— 反轉接在前面，就是從頭到尾的完整路徑
            b_nodes = [_node(n) for n in tb["nodes"]]
            b_vias = [None] + [_via(h) for h in tb["hops"]]
            for i in range(len(b_nodes) - 1, -1, -1):
                path.append({**b_nodes[i], "via": b_vias[i + 1] if i + 1 < len(b_nodes) else None})
            path.extend({**n, "via": v} for n, v in zip(a_nodes[2:], a_vias[2:], strict=False))
        else:
            path = [{**n, "via": v} for n, v in zip(a_nodes, a_vias, strict=False)]
    elif tb:
        path = [{**_node(n), "via": v} for n, v in zip(
            tb["nodes"], [None] + [_via(h) for h in tb["hops"]], strict=False)]
    ends = await _cable_ends(session, [c.id])
    return {
        "cable": {"id": str(c.id), "label": c.label, "type": c.type, "color": c.color,
                  "length_m": float(c.length_m) if c.length_m is not None else None,
                  "status": c.status, "description": c.description},
        "terminations": ends.get(c.id, []),
        "path": path[:60], "path_truncated": len(path) > 60,
    }


async def list_power(
    session: AsyncSession, *, user: User, limit: int = 200, rack_id: str | None = None,
) -> dict[str, Any]:
    """電力清單：配電盤（所在地點）、饋線（屬於哪個配電盤、供哪個機櫃，電壓/電流/相位）、插座（掛在哪條饋線）、
    裝置電源埠（裝置的 PSU 接到哪個插座 —— 畫面「設備接電」用的是這個關聯，不是插座上的 device）。

    問「某機櫃的電力」要帶 rack_id，否則回的是全站電力。
    """
    from app.models.physical import DevicePowerPort, PowerFeed, PowerOutlet, PowerPanel
    lim = _clamp_limit(limit)
    pstmt, fstmt = select(PowerPanel), select(PowerFeed)
    ostmt, ppstmt = select(PowerOutlet), select(DevicePowerPort)
    scope = "all"
    if rack_id:
        rid = _as_uuid(rack_id, "rack_id")
        vis = await visible_ids(session, user=user, object_type="rack")
        if vis is not None and rid not in vis:
            raise IPAMToolError("rack not visible to this user")
        scope = f"rack:{rid}"
        rack_feeds = select(PowerFeed.id).where(PowerFeed.rack_id == rid)
        fstmt = fstmt.where(PowerFeed.rack_id == rid)
        pstmt = pstmt.where(PowerPanel.id.in_(select(PowerFeed.panel_id).where(PowerFeed.rack_id == rid)))
        # 插座掛在饋線上，或直接標在這個機櫃
        in_rack = or_(PowerOutlet.feed_id.in_(rack_feeds), PowerOutlet.rack_id == rid)
        ostmt = ostmt.where(in_rack)
        ppstmt = ppstmt.where(or_(DevicePowerPort.outlet_id.in_(select(PowerOutlet.id).where(in_rack)),
                                  DevicePowerPort.device_id.in_(select(Device.id).where(Device.rack_id == rid))))
    totals = [await _count_rows(session, s) for s in (pstmt, fstmt, ostmt, ppstmt)]
    panels = list((await session.execute(pstmt.order_by(PowerPanel.name).limit(lim))).scalars().all())
    feeds = list((await session.execute(fstmt.order_by(PowerFeed.name).limit(lim))).scalars().all())
    outlets = list((await session.execute(ostmt.order_by(PowerOutlet.label).limit(lim))).scalars().all())
    pports = list((await session.execute(ppstmt.order_by(DevicePowerPort.name).limit(lim))).scalars().all())
    # 插座上接了哪些裝置電源埠（含這一頁沒列到的）
    by_outlet: dict[Any, list[Any]] = {}
    if outlets:
        for pp in (await session.execute(select(DevicePowerPort).where(
                in_values(DevicePowerPort.outlet_id, [o.id for o in outlets])).order_by(DevicePowerPort.name))).scalars():
            by_outlet.setdefault(pp.outlet_id, []).append(pp)
    linked_outlets = {o.id: o for o in outlets}
    extra_outlets = {pp.outlet_id for pp in pports if pp.outlet_id and pp.outlet_id not in linked_outlets}
    if extra_outlets:
        linked_outlets.update({o.id: o for o in (await session.execute(select(PowerOutlet).where(
            in_values(PowerOutlet.id, extra_outlets)))).scalars().all()})
    panel_ids = {f.panel_id for f in feeds}
    feed_ids = {f.id for f in feeds} | {o.feed_id for o in linked_outlets.values() if o.feed_id}
    feed_names = await _names_for_ids(session, PowerFeed, feed_ids)
    panel_names = await _names_for_ids(session, PowerPanel, panel_ids | {p.id for p in panels})
    loc_names = await _names_for_ids(session, Location, {p.location_id for p in panels})
    rack_names = await _names_for_ids(session, Rack, {f.rack_id for f in feeds} | {o.rack_id for o in outlets})
    dev_names = await _names_for_ids(session, Device, {pp.device_id for pp in pports}
                                   | {pp.device_id for lst in by_outlet.values() for pp in lst}
                                   | {o.device_id for o in outlets})
    return {
        "scope": scope, "truncated": any(t > n for t, n in zip(
            totals, (len(panels), len(feeds), len(outlets), len(pports)), strict=True)),
        "panel_count": totals[0], "feed_count": totals[1], "outlet_count": totals[2], "power_port_count": totals[3],
        "feeds_returned": len(feeds), "outlets_returned": len(outlets),
        "panels": [{"id": str(p.id), "name": p.name, "location": loc_names.get(p.location_id),
                    "description": p.description} for p in panels],
        "feeds": [{
            "id": str(f.id), "name": f.name, "panel": panel_names.get(f.panel_id), "rack": rack_names.get(f.rack_id),
            "voltage_v": f.voltage_v, "amperage_a": f.amperage_a, "phase": f.phase, "supply_type": f.supply_type,
            "description": f.description,
        } for f in feeds],
        "outlets": [{
            "id": str(o.id), "label": o.label, "feed": feed_names.get(o.feed_id), "rack": rack_names.get(o.rack_id),
            "description": o.description,
            **({"device": dev_names.get(o.device_id)} if o.device_id else {}),
            "power_ports": [{"device": dev_names.get(pp.device_id), "name": pp.name, "max_watts": pp.max_watts}
                            for pp in by_outlet.get(o.id, [])],
        } for o in outlets],
        "device_power_ports": [{
            "device": dev_names.get(pp.device_id), "name": pp.name, "max_watts": pp.max_watts,
            "outlet": linked_outlets[pp.outlet_id].label if pp.outlet_id in linked_outlets else None,
            "feed": feed_names.get(linked_outlets[pp.outlet_id].feed_id) if pp.outlet_id in linked_outlets else None,
            "description": pp.description,
        } for pp in pports],
    }


async def list_wazuh_agents(
    session: AsyncSession, *, user: User, limit: int = 200,
    subnet_cidr: str | None = None, subnet_id: str | None = None,
) -> dict[str, Any]:
    """Wazuh 代理清單（狀態、OS、代理版本、群組、節點、最後連線，以及 SCA 資安組態評估分數）。
    問某網段時要帶 subnet_cidr。

    沒有漏洞（CVE）數：那兩個欄位 0138 已移除（Wazuh 4.8 起 manager API 沒有漏洞端點），不可以說成「沒有漏洞」。
    """
    from app.models.wazuh import WazuhAgent, WazuhInstance
    scope_ids, scope = await _scope_subnet(
        session, user=user, subnet_cidr=subnet_cidr, subnet_id=subnet_id)
    stmt = select(WazuhAgent)
    if scope_ids is not None:
        # agent 以 jt_ipam_address_id 對映 IP → 用該 IP 的子網路限縮
        stmt = stmt.where(WazuhAgent.jt_ipam_address_id.in_(
            select(IPAddress.id).where(in_values(IPAddress.subnet_id, scope_ids))))
    total = await _count_rows(session, stmt)
    rows = (await session.execute(stmt.order_by(WazuhAgent.name).limit(_clamp_limit(limit)))).scalars().all()
    servers = await _names_for_ids(session, WazuhInstance, {a.instance_id for a in rows})
    return {"scope": scope, "count": total, **_page_meta(total, len(rows)), "agents": [{
        "id": str(a.id), "agent_id": a.agent_id, "name": a.name, "server": servers.get(a.instance_id),
        "ip": _inet_host(a.ip), "register_ip": _inet_host(a.register_ip),
        "status": a.status, "os_platform": a.os_platform, "os_version": a.os_version, "os_name": a.os_name,
        "agent_version": a.agent_version, "group": a.group, "node_name": a.node_name,
        "last_keep_alive": _iso_ts(a.last_keep_alive), "last_seen_at": _iso_ts(a.last_seen_at),
        # SCA：存的是分數最差的那個政策（一台可能跑多個基準）
        "sca_policy": a.sca_policy, "sca_score": a.sca_score, "sca_pass": a.sca_pass, "sca_fail": a.sca_fail,
        "sca_policy_count": a.sca_policy_count, "sca_scanned_at": _iso_ts(a.sca_scanned_at),
    } for a in rows]}


async def list_ocs_computers(
    session: AsyncSession, *, user: User, limit: int = 200,
    subnet_cidr: str | None = None, subnet_id: str | None = None,
    stale_days: int | None = None,
) -> dict[str, Any]:
    """OCS Inventory 盤點到的電腦：一台一筆（依 OCS 的電腦編號合併），列出它的 IP、OS、資產標籤、
    代理版本、盤點時間、備註與硬體摘要（系統／主機板／BIOS／CPU／記憶體／磁碟／顯示卡）。

    OCS 沒有自己的每台記錄表 —— 它是**透過網卡 MAC** 比對到既有 IP，再把資料補上去。2026-10-15 以前
    一個 IP 一筆、count 數的是 IP（多網卡的電腦被算成好幾台）。問某網段時要帶 subnet_cidr（列在該網段
    有 IP 的電腦，ips 只列範圍內的）。stale_days=N 只列「超過 N 天沒被盤點」的（找資產盤點缺口用）。
    """
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import cast, or_

    scope_ids, scope = await _scope_subnet(
        session, user=user, subnet_cidr=subnet_cidr, subnet_id=subnet_id)
    conds: list[Any] = [or_(IPAddress.ocs_id.isnot(None), IPAddress.last_seen_ocs.isnot(None))]
    if scope_ids is not None:
        conds.append(in_values(IPAddress.subnet_id, scope_ids))
    # 一台電腦＝一個 OCS 電腦編號；沒有編號的舊資料一個 IP 當一台
    key = func.coalesce(cast(IPAddress.ocs_id, String), cast(IPAddress.id, String))
    last = func.max(IPAddress.last_seen_ocs)
    grouped = select(key.label("k"), last.label("ls")).where(*conds).group_by(key)
    if stale_days is not None:
        cutoff = datetime.now(UTC) - timedelta(days=max(0, int(stale_days)))
        grouped = grouped.having(or_(last.is_(None), last < cutoff))
    total = await _count_rows(session, grouped)
    keys = [k for (k, _ls) in (await session.execute(
        grouped.order_by(last.desc().nullslast(), key).limit(_clamp_limit(limit)))).all()]
    by_key: dict[str, list[Any]] = {k: [] for k in keys}
    if keys:
        for a in (await session.execute(select(IPAddress).where(
                *conds, in_values(key, keys, type_=String())).order_by(IPAddress.ip))).scalars().all():
            k = str(a.ocs_id) if a.ocs_id is not None else str(a.id)
            by_key.setdefault(k, []).append(a)
    cidrs = await _subnet_cidr_map(session, {a.subnet_id for lst in by_key.values() for a in lst})

    def _first(lst: list[Any], attr: str) -> Any:
        # 最近盤點的那筆優先（同一台的各張網卡由同一次盤點寫入，通常相同）
        for a in sorted(lst, key=lambda x: x.last_seen_ocs or datetime.min.replace(tzinfo=UTC), reverse=True):
            v = getattr(a, attr)
            if v not in (None, "", [], {}):
                return v
        return None

    computers = []
    for k in keys:
        lst = by_key.get(k) or []
        if not lst:
            continue
        seen = [a.last_seen_ocs for a in lst if a.last_seen_ocs]
        computers.append({
            "ocs_id": _first(lst, "ocs_id"), "hostname": _first(lst, "hostname"),
            "os": _first(lst, "os_ocs"), "tag": _first(lst, "ocs_tag"), "agent_version": _first(lst, "ocs_agent"),
            "last_inventory": _iso_ts(max(seen)) if seen else None,
            "notes": _first(lst, "ocs_notes") or [], "hardware": _first(lst, "ocs_hw"),
            "ips": [{"ip": _inet_host(a.ip), "mac": str(a.mac) if a.mac else None, "hostname": a.hostname,
                     "subnet": cidrs.get(str(a.subnet_id))} for a in lst],
        })
    return {"scope": scope, "count": total, **_page_meta(total, len(computers)), "computers": computers}


async def _rustdesk_brief(session: AsyncSession, address_id: Any) -> dict[str, Any] | None:
    from app.services.rustdesk import for_address
    rd = await for_address(session, address_id)
    if rd is None:
        return None
    return {"id": rd["id"], "online": rd["online"], "last_online_at": rd["last_online_at"],
            "server": rd["server_name"]}


async def list_rustdesk_peers(
    session: AsyncSession, *, user: User, limit: int = 200,
    subnet_cidr: str | None = None, subnet_id: str | None = None,
    online: bool | None = None, q: str | None = None,
) -> dict[str, Any]:
    """RustDesk Server（開源版）上註冊的裝置：ID、是否上線、最後上線、登記 IP、對應到的 IP 記錄，
    以及受控端客戶端自己回報的主機名稱／OS／登入帳號／CPU／記憶體／版本／心跳（有開回報才有）。

    `hostname` 是對應到的 jt-ipam IP 記錄的；`reported_hostname` 是 RustDesk 客戶端回報的。
    問某網段時要帶 subnet_cidr —— 帶了就只列「對應到該網段 IP」的裝置（沒對應到的不知道在哪個網段）。
    """
    from sqlalchemy import or_

    from app.models.rustdesk import RustDeskPeer, RustDeskServer

    scope_ids, scope = await _scope_subnet(
        session, user=user, subnet_cidr=subnet_cidr, subnet_id=subnet_id)
    stmt = (select(RustDeskPeer, RustDeskServer.name, IPAddress.ip, IPAddress.hostname)
            .join(RustDeskServer, RustDeskServer.id == RustDeskPeer.server_id)
            .outerjoin(IPAddress, IPAddress.id == RustDeskPeer.address_id))
    if scope_ids is not None:
        stmt = stmt.where(in_values(IPAddress.subnet_id, scope_ids))
    if online is not None:
        stmt = stmt.where(RustDeskPeer.online.is_(bool(online)))
    if q and str(q).strip():
        like = f"%{str(q).strip()[:100]}%"
        stmt = stmt.where(or_(RustDeskPeer.rustdesk_id.ilike(like), IPAddress.hostname.ilike(like),
                              RustDeskPeer.hostname.ilike(like), RustDeskPeer.username.ilike(like),
                              func.host(RustDeskPeer.registered_ip).ilike(like)))
    total = await _count_rows(session, stmt)
    rows = (await session.execute(
        stmt.order_by(RustDeskPeer.online.desc(), RustDeskPeer.last_online_at.desc().nullslast())
        .limit(_clamp_limit(limit)))).all()
    return {"scope": scope, "count": total, **_page_meta(total, len(rows)), "peers": [{
        "rustdesk_id": p.rustdesk_id, "server": server, "online": p.online,
        "last_online_at": _iso_ts(p.last_online_at), "first_registered_at": _iso_ts(p.first_registered_at),
        "registered_ip": _inet_host(p.registered_ip),
        "match_status": p.match_status, "match_evidence": p.match_evidence or [],
        "ip": _inet_host(ip), "hostname": hostname,
        # 客戶端回報（受控端開著回報才有）
        "reported_hostname": p.hostname, "os_name": p.os_name, "username": p.username,
        "cpu": p.cpu, "memory": p.memory, "client_version": p.client_version,
        "sysinfo_at": _iso_ts(p.sysinfo_at), "last_heartbeat_at": _iso_ts(p.last_heartbeat_at),
        "active_conns": p.active_conns, "report_ip": _inet_host(p.report_ip), "report_ip_at": _iso_ts(p.report_ip_at),
        # 受控端的伺服器公鑰（Key）錯誤：網頁連不上但程式可以時先看這裡
        "key_fail_at": _iso_ts(p.key_fail_at), "key_fail_scope": p.key_fail_scope,
        "key_fail_count": p.key_fail_count, "key_ok_at": _iso_ts(p.key_ok_at),
    } for p, server, ip, hostname in rows]}


async def list_rustdesk_audit(
    session: AsyncSession, *, user: User, limit: int = 100, hours: int = 168,
    kind: str | None = None, q: str | None = None, subnet_cidr: str | None = None, subnet_id: str | None = None,
) -> dict[str, Any]:
    """RustDesk 客戶端回報的稽核：誰（對方 ID、名稱、IP）在什麼時候連進哪台、傳了什麼檔案、告警（密碼錯太多次等）。

    受控端要開著回報（客戶端 API 伺服器沒填時會自動送到 ID 伺服器的 21114，代理在那裡收）才會有資料。
    問某網段時帶 subnet_cidr：只列受控端對應到該網段 IP 的紀錄。
    """
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import and_, or_

    from app.models.rustdesk import RustDeskAuditEvent, RustDeskPeer, RustDeskServer

    scope_ids, scope = await _scope_subnet(session, user=user, subnet_cidr=subnet_cidr, subnet_id=subnet_id)
    E = RustDeskAuditEvent
    since = datetime.now(UTC) - timedelta(hours=max(1, min(int(hours), 24 * 400)))
    stmt = (select(E, IPAddress.ip, IPAddress.hostname, RustDeskPeer.hostname, RustDeskServer.name)
            .join(RustDeskServer, RustDeskServer.id == E.server_id)
            .outerjoin(RustDeskPeer, and_(RustDeskPeer.server_id == E.server_id,
                                          RustDeskPeer.rustdesk_id == E.rustdesk_id))
            .outerjoin(IPAddress, IPAddress.id == RustDeskPeer.address_id)
            .where(E.occurred_at >= since))
    if kind in ("conn", "file", "alarm", "note"):
        stmt = stmt.where(E.kind == kind)
    if scope_ids is not None:
        stmt = stmt.where(in_values(IPAddress.subnet_id, scope_ids))
    if q and str(q).strip():
        like = f"%{str(q).strip()[:100]}%"
        stmt = stmt.where(or_(E.rustdesk_id.ilike(like), E.peer_id.ilike(like), E.peer_name.ilike(like),
                              IPAddress.hostname.ilike(like), RustDeskPeer.hostname.ilike(like)))
    total = await _count_rows(session, stmt)
    rows = (await session.execute(stmt.order_by(E.occurred_at.desc()).limit(_clamp_limit(limit, 100)))).all()
    return {"scope": scope, "since": _iso_ts(since), "count": total, **_page_meta(total, len(rows)), "events": [{
        "at": _iso_ts(e.occurred_at), "kind": e.kind, "action": e.action, "server": server,
        "device_rustdesk_id": e.rustdesk_id,
        "device_ip": _inet_host(ip), "device_hostname": host or rhost,
        "peer_id": e.peer_id, "peer_name": e.peer_name, "peer_ip": _inet_host(e.ip),
        "conn_type": e.conn_type, "alarm_type": e.alarm_type, "detail": e.detail, "verified": e.verified,
    } for e, ip, host, rhost, server in rows]}


# ── 寫入類（一律 ADMIN ONLY，與 allocate_ip 同模式） ──
# 2026-10-15 MCP 工具盤點：每一支都改走 REST 同一份服務函式（services/address_write、subnet_write、
# device_write、ip_request.approve_per_policy），權限守門、驗證 schema、物件稽核都與 REST 相同。

async def update_ip(
    session: AsyncSession, *, user: User, ip: str,
    subnet_cidr: str | None = None, ip_id: str | None = None,
    hostname: str | None = None, state: str | None = None, owner: str | None = None,
    description: str | None = None, mac: str | None = None, note: str | None = None,
    customer: str | None = None, device: str | None = None, switch_port: str | None = None,
    liveness_from_device: bool | None = None, exclude_from_ping: bool | None = None,
    excluded_probes: list[str] | None = None, ptr_ignore: bool | None = None,
    is_dhcp_server: bool | None = None,
) -> dict[str, Any]:
    """ADMIN ONLY。更新某 IP 的欄位（畫面 IP 編輯表單裡適合由助理改的那些）。

    - 找記錄不猜：重疊網段要帶 ip_id 或分得出來的 subnet_cidr（以前 `.first()` 任取一筆）
    - 與 REST PATCH /addresses 同一份規則（services/address_write.update_address_checked）：主機名稱走
      人工觀測＋優先序（以前直接寫欄位，下一輪同步就被蓋回去）、MAC 來源標手動、探測雙向同步、
      write_guards 守門、逐欄異動記錄、與 REST 相同的物件稽核
    - 字串給空字串＝清空
    """
    from fastapi import HTTPException
    from pydantic import ValidationError

    from app.schemas.address import IPAddressUpdate
    from app.services.address_write import update_address_checked
    if not user.is_admin:
        raise IPAMToolError("update_ip requires admin")
    obj = await _resolve_ip_record(session, user=user, ip=ip, subnet_cidr=subnet_cidr, ip_id=ip_id)

    raw: dict[str, Any] = {}
    for key, value in (("hostname", hostname), ("owner", owner), ("description", description),
                       ("note", note), ("switch_port", switch_port)):
        if value is not None:
            raw[key] = _blank_to_none(value)
    if mac is not None:
        from app.services.mac_history import normalize_mac
        m = _blank_to_none(mac)
        if m is not None and normalize_mac(m) is None:
            raise IPAMToolError(f"invalid mac: {m[:40]!r} (expected a MAC address such as aa:bb:cc:dd:ee:ff)")
        raw["mac"] = normalize_mac(m) if m else None
    if state is not None:
        raw["state"] = _check_ip_state(state)
    if customer is not None:
        raw["customer_id"] = (await _customer_arg(session, customer)).id if customer.strip() else None
    if device is not None:
        raw["device_id"] = (await _device_arg(session, device)).id if device.strip() else None
    for key, flag in (("liveness_from_device", liveness_from_device), ("exclude_from_ping", exclude_from_ping),
                      ("ptr_ignore", ptr_ignore), ("is_dhcp_server", is_dhcp_server)):
        if flag is not None:
            raw[key] = bool(flag)
    if excluded_probes is not None:
        raw["excluded_probes"] = _check_probes(excluded_probes, "excluded_probes")
    if not raw:
        raise IPAMToolError("nothing to update: give at least one field to change")
    try:
        changes = IPAddressUpdate.model_validate(raw).model_dump(exclude_unset=True)
    except ValidationError as exc:
        raise _invalid_args(exc) from exc

    try:
        before, applied = await update_address_checked(session, user=user, obj=obj, changes=changes)
    except HTTPException as exc:
        raise _rest_rule_error(exc) from exc
    await _audit_tool_write(session, user=user, object_type="ip_address", object_id=obj.id,
                            action="update", diff={"before": before, "changes": applied})
    await session.flush()
    updated = set(applied)
    if "hostname" in changes and (changes["hostname"] or None) != (before["hostname"] or None):
        updated.add("hostname")
    return {"ip": str(obj.ip).split("/")[0], "ip_id": str(obj.id), "updated": sorted(updated),
            # 有效主機名稱（依來源優先序；手動輸入的被別的來源蓋過時會自動固定為手動）
            "hostname": obj.hostname, "hostname_source_pin": obj.hostname_source_pin}


async def _vlan_arg(session: AsyncSession, value: str) -> Any:
    """VLAN：UUID、VLAN 號碼或名稱。同一個號碼在不同 VLAN 網域都有時不猜，列出候選。"""
    from app.models.vlan import VLANDomain
    raw = (value or "").strip()
    if raw.isdigit():
        rows = list((await session.execute(
            select(VLAN, VLANDomain.name).join(VLANDomain, VLANDomain.id == VLAN.domain_id)
            .where(VLAN.number == int(raw)).limit(11))).all())
        if not rows:
            raise IPAMToolError(f"VLAN not found: {raw}")
        if len(rows) > 1:
            listed = "; ".join(f"VLAN {v.number} {v.name} in domain {d} (id={v.id})" for v, d in rows[:10])
            raise IPAMToolError(f"VLAN {raw} exists in {len(rows)} domains; call again with the id: {listed}")
        return rows[0][0]
    return await _lookup_by_name(session, VLAN, raw, label="VLAN", name_cols=(VLAN.name,),
                                 describe=lambda v: f"VLAN {v.number} {v.name} (id={v.id})")


async def create_subnet(
    session: AsyncSession, *, user: User, cidr: str,
    section_id: str | None = None, section_name: str | None = None,
    description: str | None = None, gateway: str | None = None,
    vlan: str | None = None, vrf: str | None = None, customer: str | None = None,
    location: str | None = None, dns_servers: str | None = None, threshold_pct: int | None = None,
    is_pool: bool | None = None, is_full: bool | None = None,
    anomaly_enabled: bool | None = None, ai_audit_enabled: bool | None = None,
    scan_enabled: bool | None = None, scan_method: list[str] | None = None, scan_scope: str | None = None,
    allow_overlap: bool | None = None,
) -> dict[str, Any]:
    """ADMIN ONLY。在某區段建立子網路，與 REST POST /subnets 同一份規則（services/subnet_write）：

    重疊檢查（同 VRF 內重疊要 allow_overlap）、算出上層子網路、沒指定單位時繼承上層的單位、
    schema 驗證（閘道、門檻、掃描項目）、物件稽核；建立後照 REST 重算階層、索引說明、送出 subnet.created。
    """
    from fastapi import HTTPException
    from pydantic import ValidationError

    from app.models.location import Location
    from app.models.vrf import VRF
    from app.schemas.subnet import SubnetCreate
    from app.services.subnet import rebuild_subnet_hierarchy
    from app.services.subnet_write import create_subnet_checked
    if not user.is_admin:
        raise IPAMToolError("create_subnet requires admin")
    if not (section_id or section_name):
        raise IPAMToolError("section not found (provide section_id or section_name)")
    sec = await _lookup_by_name(session, Section, section_id or section_name or "", label="section",
                                name_cols=(Section.name,))

    fields: dict[str, Any] = {"section_id": sec.id, "cidr": cidr}
    for key, value in (("description", description), ("gateway", gateway), ("dns_servers", dns_servers)):
        if value is not None and value.strip():
            fields[key] = value.strip()
    if vlan is not None and vlan.strip():
        fields["vlan_id"] = (await _vlan_arg(session, vlan)).id
    if vrf is not None and vrf.strip():
        fields["vrf_id"] = (await _lookup_by_name(session, VRF, vrf, label="VRF", name_cols=(VRF.name,))).id
    if customer is not None and customer.strip():
        fields["customer_id"] = (await _customer_arg(session, customer)).id
    if location is not None and location.strip():
        fields["location_id"] = (await _lookup_by_name(session, Location, location, label="location",
                                                       name_cols=(Location.name,))).id
    if threshold_pct is not None:
        fields["threshold_pct"] = threshold_pct
    for key, flag in (("is_pool", is_pool), ("is_full", is_full), ("anomaly_enabled", anomaly_enabled),
                      ("ai_audit_enabled", ai_audit_enabled), ("scan_enabled", scan_enabled),
                      ("allow_overlap", allow_overlap)):
        if flag is not None:
            fields[key] = bool(flag)
    if scan_method is not None:
        fields["scan_method"] = _check_probes(scan_method, "scan_method")
    if scan_scope is not None and scan_scope.strip():
        fields["scan_scope"] = scan_scope.strip()
    try:
        payload = SubnetCreate.model_validate(fields)
    except ValidationError as exc:
        raise _invalid_args(exc) from exc

    try:
        sub = await create_subnet_checked(session, user=user, payload=payload)
    except HTTPException as exc:
        raise _rest_rule_error(exc) from exc
    await _audit_tool_write(session, user=user, object_type="subnet", object_id=sub.id,
                            action="create", diff={"after": payload.model_dump(mode="json")})
    # 與 REST 相同的建立後連動：重算階層（既有子網段歸位）、索引說明（失敗不擋）、通知
    await rebuild_subnet_hierarchy(session)
    if sub.description:
        import contextlib

        from app.services import ai as ai_service
        with contextlib.suppress(Exception):
            async with session.begin_nested():
                await ai_service.index_subnet(session, str(sub.id), sub.description)
    from app.services.notification import deliver_event
    await deliver_event(session, event="subnet.created", payload={
        "id": str(sub.id), "cidr": str(sub.cidr), "section_id": str(sub.section_id), "actor": str(user.id),
    })
    await session.flush()
    await session.refresh(sub)
    master = await session.get(Subnet, sub.master_subnet_id) if sub.master_subnet_id else None
    cust = await session.get(Customer, sub.customer_id) if sub.customer_id else None
    return {"id": str(sub.id), "cidr": str(sub.cidr), "section": sec.name,
            "master_subnet": str(master.cidr) if master else None,
            "customer": cust.name if cust else None,
            "vlan_id": str(sub.vlan_id) if sub.vlan_id else None,
            "vrf_id": str(sub.vrf_id) if sub.vrf_id else None,
            "location_id": str(sub.location_id) if sub.location_id else None}


async def create_device(
    session: AsyncSession, *, user: User, name: str,
    type: str = "other", fqdn: str | None = None,
    vendor: str | None = None, model: str | None = None,
    serial: str | None = None, description: str | None = None,
    location: str | None = None, rack: str | None = None,
    u_position: int | None = None, u_size: int | None = None, rack_face: str | None = None,
    customer: str | None = None, primary_ip: str | None = None, primary_ip_id: str | None = None,
) -> dict[str, Any]:
    """ADMIN ONLY。建立裝置（畫面裝置編輯表單的欄位），與 REST POST /devices 同一份規則
    （services/device_write）：類型允許清單、機櫃 U 位防呆、主要 IP 雙向連結、物件稽核。

    選了機櫃就不必再給地點（從機櫃推）；兩個都給而且矛盾才擋。"""
    from pydantic import ValidationError

    from app.models.location import Location
    from app.schemas.device import DeviceCreate
    from app.services.custom_field import CustomFieldError
    from app.services.device_write import PlacementError, create_device_checked
    if not user.is_admin:
        raise IPAMToolError("create_device requires admin")

    fields: dict[str, Any] = {"name": (name or "").strip()}
    if type is not None:
        fields["type"] = (type or "").strip()
    for key, value in (("fqdn", fqdn), ("vendor", vendor), ("model", model), ("serial", serial),
                       ("description", description), ("rack_face", rack_face)):
        if value is not None and value.strip():
            fields[key] = value.strip()
    for key, num in (("u_position", u_position), ("u_size", u_size)):
        if num is not None:
            fields[key] = num
    loc = None
    if location is not None and location.strip():
        loc = await _lookup_by_name(session, Location, location, label="location", name_cols=(Location.name,))
        fields["location_id"] = loc.id
    if rack is not None and rack.strip():
        rk = await _rack_arg(session, rack, location=loc)
        if loc is not None and rk.location_id is not None and rk.location_id != loc.id:
            raise IPAMToolError(f"rack {rk.name!r} is not in location {loc.name!r}")
        fields["rack_id"] = rk.id
        if loc is None and rk.location_id is not None:
            fields["location_id"] = rk.location_id
    if customer is not None and customer.strip():
        fields["customer_id"] = (await _customer_arg(session, customer)).id
    if primary_ip or primary_ip_id:
        fields["primary_ip_id"] = (await _resolve_ip_record(
            session, user=user, ip=primary_ip, ip_id=primary_ip_id)).id
    try:
        payload = DeviceCreate.model_validate(fields)
    except ValidationError as exc:
        raise _invalid_args(exc) from exc

    try:
        dev = await create_device_checked(session, payload)
    except (PlacementError, CustomFieldError) as exc:
        raise _ui_error(exc) from exc
    await _audit_tool_write(session, user=user, object_type="device", object_id=dev.id,
                            action="create", diff={"after": payload.model_dump(mode="json")})
    await session.flush()
    return {"id": str(dev.id), "name": dev.name, "type": dev.type,
            "location_id": str(dev.location_id) if dev.location_id else None,
            "rack_id": str(dev.rack_id) if dev.rack_id else None,
            "u_position": dev.u_position, "u_size": dev.u_size, "rack_face": dev.rack_face,
            "customer_id": str(dev.customer_id) if dev.customer_id else None,
            "primary_ip_id": str(dev.primary_ip_id) if dev.primary_ip_id else None}


async def _rack_arg(session: AsyncSession, value: str, *, location: Any = None) -> Any:
    """機櫃：UUID 或名稱。機櫃名稱只在同一個地點內唯一 → 同名多個時先用給的地點縮小，還分不出來就列候選。"""
    from app.models.location import Location, Rack
    raw = (value or "").strip()
    try:
        rid: uuid.UUID | None = uuid.UUID(raw)
    except ValueError:
        rid = None
    if rid is not None:
        obj = await session.get(Rack, rid)
        if obj is None:
            raise IPAMToolError(f"rack not found: {raw}")
        return obj
    rows = list((await session.execute(
        select(Rack, Location.name).outerjoin(Location, Location.id == Rack.location_id)
        .where(func.lower(Rack.name) == raw.lower()).limit(11))).all())
    if location is not None and len(rows) > 1:
        rows = [r for r in rows if r[0].location_id == location.id] or rows
    if not rows:
        raise IPAMToolError(f"rack not found: {raw[:80]!r}")
    if len(rows) > 1:
        listed = "; ".join(f"{ln or '-'} / {r.name} (id={r.id})" for r, ln in rows[:10])
        raise IPAMToolError(f"{len(rows)} racks are named {raw[:80]!r}; give location or the rack id: {listed}")
    return rows[0][0]


async def _require_ip_request_approver(session: AsyncSession, user: User, req: Any) -> None:
    """與 REST 的 _can_approve 同一個判斷（審核政策：模式、關卡、能不能核准自己的申請）。

    authorize_tool 已經只放管理員進來；管理員在政策裡也可能不能核准（申請人是自己、多關卡輪不到）。
    擋下來時講清楚是哪一條。"""
    from app.services.ip_request_policy import can_approve, get_policy
    if await can_approve(session, user, req):
        return
    pol = await get_policy(session)
    if req.requester_user_id == user.id and not pol.get("allow_self_approve"):
        raise IPAMToolError("the approval policy does not allow approving or rejecting your own request "
                            "(self-approval is off); another approver has to do it")
    if req.status != "pending":
        raise IPAMToolError(f"request is {req.status}, not pending")
    raise IPAMToolError("you are not an approver for the current step of this request "
                        f"(approval mode: {pol.get('approver_mode')})")


async def approve_ip_request(
    session: AsyncSession, *, user: User, request_id: str, ip: str | None = None,
) -> dict[str, Any]:
    """ADMIN ONLY。依審核政策核准 IP 申請（與 REST POST /ip-requests/{id}/approve 相同）。

    單關卡模式：核准即配發。多關卡（parallel / stages）：只核准此人目前可審的那一關，全部通過才配發
    （2026-10-15 以前第一關就直接配發）。`ip`＝審核人改配發的 IP（畫面的「將配發的 IP」），只在這次核准
    讓申請完成時用得到。"""
    from app.models.ip_request import IPRequest
    from app.services.ip_request import IPRequestError, NoActionableStep, approve_per_policy
    from app.services.ip_request_policy import stage_progress
    if not user.is_admin:
        raise IPAMToolError("approve_ip_request requires admin")
    req = await session.get(IPRequest, _as_uuid(request_id, "request_id"))
    if req is None:
        raise IPAMToolError("request not found")
    await _require_ip_request_approver(session, user, req)
    sub = await session.get(Subnet, req.subnet_id)
    if sub is None:
        raise IPAMToolError("subnet no longer exists")
    try:
        fulfilled = await approve_per_policy(session, request=req, subnet=sub, approver=user,
                                             override_ip=(ip or "").strip() or None)
    except NoActionableStep as exc:
        raise IPAMToolError("no approval step is actionable for you") from exc
    except IPRequestError as exc:
        raise IPAMToolError(f"approve failed: {exc}") from exc
    await _audit_tool_write(session, user=user, object_type="ip_request", object_id=req.id, action="approve",
                            diff={"allocated_ip_id": str(req.allocated_ip_id) if req.allocated_ip_id else None})
    await session.flush()
    allocated = await session.get(IPAddress, req.allocated_ip_id) if req.allocated_ip_id else None
    return {"id": str(req.id), "status": req.status, "fulfilled": bool(fulfilled),
            "allocated_ip_id": str(req.allocated_ip_id) if req.allocated_ip_id else None,
            "allocated_ip": str(allocated.ip).split("/")[0] if allocated else None,
            # 多關卡進度：[{index, name, approved, is_current}]；單關卡模式為空
            "stages": await stage_progress(session, req)}


async def reject_ip_request(
    session: AsyncSession, *, user: User, request_id: str, reason: str,
) -> dict[str, Any]:
    """ADMIN ONLY。駁回 IP 申請（與 REST 相同：理由 3–1024 字、審核政策判斷、物件稽核）。"""
    from pydantic import ValidationError

    from app.models.ip_request import IPRequest
    from app.schemas.ip_request import IPRequestReject
    from app.services.ip_request import IPRequestError, reject_request
    if not user.is_admin:
        raise IPAMToolError("reject_ip_request requires admin")
    try:
        reason = IPRequestReject.model_validate({"reason": reason}).reason
    except ValidationError as exc:
        raise _invalid_args(exc) from exc
    req = await session.get(IPRequest, _as_uuid(request_id, "request_id"))
    if req is None:
        raise IPAMToolError("request not found")
    await _require_ip_request_approver(session, user, req)
    try:
        res = await reject_request(session, request=req, approver=user, reason=reason)
    except IPRequestError as exc:
        raise IPAMToolError(f"reject failed: {exc}") from exc
    await _audit_tool_write(session, user=user, object_type="ip_request", object_id=req.id, action="reject",
                            diff={"reason": reason})
    await session.flush()
    return {"id": str(res.id), "status": res.status}


# ─────────────────── 網路工具（純運算，對應「網路工具」頁）───────────────────
# 邏輯共用 app/services/nettools.py；這裡只是 MCP fn 簽章包裝（session/user 不用）。
# 失敗時把 NetToolError 翻成 IPAMToolError，讓 LLM 收到人讀錯誤而非 500。

async def calc_ip_info(session: AsyncSession, *, user: User, ip: str) -> dict[str, Any]:
    from app.services import nettools
    try:
        return nettools.ip_info(ip)
    except nettools.NetToolError as exc:
        raise IPAMToolError(str(exc)) from exc


async def calc_cidr_info(session: AsyncSession, *, user: User, cidr: str) -> dict[str, Any]:
    from app.services import nettools
    try:
        return nettools.cidr_info(cidr)
    except nettools.NetToolError as exc:
        raise IPAMToolError(str(exc)) from exc


async def calc_cidr_split(
    session: AsyncSession, *, user: User, cidr: str, new_prefix: int,
) -> dict[str, Any]:
    from app.services import nettools
    try:
        return nettools.cidr_split(cidr, int(new_prefix))
    except nettools.NetToolError as exc:
        raise IPAMToolError(str(exc)) from exc


async def calc_eui64(
    session: AsyncSession, *, user: User, mac: str, prefix: str,
) -> dict[str, Any]:
    from app.services import nettools
    try:
        return nettools.eui64(mac, prefix)
    except nettools.NetToolError as exc:
        raise IPAMToolError(str(exc)) from exc


async def calc_ip_in_cidr(
    session: AsyncSession, *, user: User, ip: str, cidr: str,
) -> dict[str, Any]:
    from app.services import nettools
    try:
        return nettools.ip_in_cidr(ip, cidr)
    except nettools.NetToolError as exc:
        raise IPAMToolError(str(exc)) from exc


async def calc_cidr_relation(
    session: AsyncSession, *, user: User, a: str, b: str,
) -> dict[str, Any]:
    from app.services import nettools
    try:
        return nettools.cidr_relation(a, b)
    except nettools.NetToolError as exc:
        raise IPAMToolError(str(exc)) from exc


async def calc_range_to_cidr(
    session: AsyncSession, *, user: User, start: str, end: str,
) -> dict[str, Any]:
    from app.services import nettools
    try:
        return nettools.range_to_cidr(start, end)
    except nettools.NetToolError as exc:
        raise IPAMToolError(str(exc)) from exc


async def calc_cidr_to_range(
    session: AsyncSession, *, user: User, cidr: str,
) -> dict[str, Any]:
    from app.services import nettools
    try:
        return nettools.cidr_to_range(cidr)
    except nettools.NetToolError as exc:
        raise IPAMToolError(str(exc)) from exc


async def calc_aggregate(
    session: AsyncSession, *, user: User, cidrs: str,
) -> dict[str, Any]:
    from app.services import nettools
    try:
        return nettools.aggregate(cidrs)
    except nettools.NetToolError as exc:
        raise IPAMToolError(str(exc)) from exc


async def calc_netmask(
    session: AsyncSession, *, user: User, value: str,
) -> dict[str, Any]:
    from app.services import nettools
    try:
        return nettools.netmask(value)
    except nettools.NetToolError as exc:
        raise IPAMToolError(str(exc)) from exc


async def calc_mac_format(
    session: AsyncSession, *, user: User, mac: str,
) -> dict[str, Any]:
    from app.services import nettools
    try:
        return nettools.mac_format(mac)
    except nettools.NetToolError as exc:
        raise IPAMToolError(str(exc)) from exc


async def calc_fqdn(
    session: AsyncSession, *, user: User, name: str,
) -> dict[str, Any]:
    from app.services import nettools
    return nettools.fqdn_parse(name)


async def dns_resolve(
    session: AsyncSession, *, user: User, name: str, type: str = "ANY",
) -> dict[str, Any]:
    from app.services import nettools
    try:
        return await nettools.dns_lookup_live(name, type)
    except nettools.NetToolError as exc:
        raise IPAMToolError(str(exc)) from exc


async def dns_mail_check(
    session: AsyncSession, *, user: User, domain: str, dkim_selector: str = "",
) -> dict[str, Any]:
    from app.services import nettools
    try:
        return await nettools.dns_mail(domain, dkim_selector)
    except nettools.NetToolError as exc:
        raise IPAMToolError(str(exc)) from exc


async def geoip_locate(
    session: AsyncSession, *, user: User, ip: str,
) -> dict[str, Any]:
    from app.services import nettools
    from app.services.geoip import geoip_lookup
    try:
        addr = nettools.parse_addr(ip)
    except nettools.NetToolError as exc:
        raise IPAMToolError(str(exc)) from exc
    return await geoip_lookup(session, str(addr))


async def power_calc(
    session: AsyncSession, *, user: User,
    volts: float = 220, amps: float = 16, phase: str = "1", pf: float = 0.95,
    heat_watts: float | None = None, batt_wh: float | None = None,
    load_w: float | None = None, pdu_a: float | None = None,
) -> dict[str, Any]:
    from app.services import nettools
    try:
        return nettools.power_calc(
            volts=volts, amps=amps, phase=str(phase), pf=pf, heat_watts=heat_watts,
            batt_wh=batt_wh, load_w=load_w, pdu_a=pdu_a,
        )
    except nettools.NetToolError as exc:
        raise IPAMToolError(str(exc)) from exc


# ─────────────────── 工具註冊表（給 MCP / chat 共用）───────────────────


# 每個 tool entry：name → (callable, description, json schema for parameters)
_CONSOLE_KINDS = ("ssh", "sftp", "rdp", "vnc", "novnc", "bmc", "rustdesk")
# 寫入工具參數的列舉值：與 REST schema 同一份來源（改允許清單時工具說明跟著變）
from app.core.scan_probes import VALID_PROBES as _PROBE_ENUM  # noqa: E402
from app.schemas.device import _VALID_TYPES as _DEVICE_TYPE_ENUM  # noqa: E402

_IP_STATE_ENUM = _writable_ip_states()


async def list_connection_targets(
    session: AsyncSession, *, user: User, protocol: str | None = None, limit: int = 200,
) -> dict[str, Any]:
    """瀏覽器遠端連線（SSH／SFTP／RDP／VNC／PVE noVNC／BMC／RustDesk 網頁）已啟用、且呼叫者可連線的 IP。唯讀，絕不回帳密。

    篩選與 REST 連線管理頁共用 services/connection_targets（2026-10-15 以前只查 SSH／RDP／VNC，只開了
    noVNC、BMC、RustDesk 的位址整列消失）。另外列出只開 SFTP 的位址（連線管理頁沒有 SFTP 欄）。
    """
    from app.services.connection_targets import usable_connection_targets
    from app.services.os_precedence import effective_os
    from app.services.pve_console import resolve_pve_target
    limit = max(1, min(int(limit), 500))
    proto = (protocol or "").lower().strip()
    if proto and proto not in _CONSOLE_KINDS:
        raise IPAMToolError(f"invalid protocol {proto[:16]!r}; allowed: {', '.join(_CONSOLE_KINDS)}")
    targets, rd_web, _vis = await usable_connection_targets(session, user=user, include_sftp=True)
    field = {"ssh": "ssh_enabled", "sftp": "sftp_enabled", "rdp": "rdp_enabled", "vnc": "vnc_enabled",
             "novnc": "novnc_enabled", "bmc": "bmc_enabled"}
    pve: dict[Any, Any] = {}
    if proto == "rustdesk":
        targets = [ip for ip in targets if ip.id in rd_web]
    elif proto == "novnc":
        # PVE 主控台要對應得到 Proxmox 的 VM/CT 才算可用（與 REST 的 novnc_available 相同）
        kept = []
        for ip in targets:
            if ip.novnc_enabled and (tgt := await resolve_pve_target(session, ip)) is not None:
                pve[ip.id] = tgt
                kept.append(ip)
        targets = kept
    elif proto:
        targets = [ip for ip in targets if getattr(ip, field[proto])]

    def _key(ip: IPAddress) -> tuple[int, int]:
        a = ipaddress.ip_address(str(ip.ip).split("/")[0])
        return (a.version, int(a))
    targets.sort(key=_key)
    total = len(targets)
    page = targets[:limit]

    sub_ids = {ip.subnet_id for ip in page}
    subs = {r[0]: r for r in (await session.execute(
        select(Subnet.id, Subnet.cidr, Subnet.customer_id).where(in_values(Subnet.id, sub_ids)))).all()} \
        if sub_ids else {}
    dev_ids = {ip.device_id for ip in page if ip.device_id}
    devs = {r[0]: r for r in (await session.execute(
        select(Device.id, Device.name, Device.type).where(in_values(Device.id, dev_ids)))).all()} \
        if dev_ids else {}
    cust_ids = {ip.customer_id or (subs[ip.subnet_id][2] if ip.subnet_id in subs else None) for ip in page}
    cust_ids.discard(None)
    custs = {i: n for i, n in (await session.execute(
        select(Customer.id, Customer.name).where(in_values(Customer.id, cust_ids)))).all()} if cust_ids else {}

    items = []
    for ip in page:
        if ip.novnc_enabled and ip.id not in pve:
            pve[ip.id] = await resolve_pve_target(session, ip)
        sub = subs.get(ip.subnet_id)
        dev = devs.get(ip.device_id) if ip.device_id else None
        cust_id = ip.customer_id or (sub[2] if sub else None)
        os_ = await effective_os(session, ip)     # 與 IP 詳細資料頁一致：依來源優先序
        items.append({
            "ip": str(ip.ip).split("/")[0], "ip_id": str(ip.id),
            "subnet": str(sub[1]) if sub else None,
            "hostname": ip.hostname,
            "device": dev[1] if dev else None, "device_type": dev[2] if dev else None,
            "customer": custs.get(cust_id) if cust_id else None,
            "status": ip.effective_status, "state": ip.state,
            "device_kind": ip.device_kind, "os": os_["os_guess"], "os_family": os_["os_family"],
            "mac": str(ip.mac) if ip.mac else None,
            # 這個位址可用的連線方式（noVNC 要對應得到 PVE VM/CT；RustDesk 要伺服器開放網頁連線）
            "ssh": bool(ip.ssh_enabled), "sftp": bool(ip.sftp_enabled), "rdp": bool(ip.rdp_enabled),
            "vnc": bool(ip.vnc_enabled), "novnc": bool(ip.novnc_enabled and pve.get(ip.id) is not None),
            "bmc": bool(ip.bmc_enabled), "rustdesk": ip.id in rd_web,
        })
    return {"items": items, "count": len(items), "total": total, "truncated": total > len(items),
            "protocol": proto or "any"}


async def list_ai_findings(
    session: AsyncSession, user: User, *, severity: str | None = None, limit: int = 20,
    status: str | None = None,
) -> dict[str, Any]:
    """AI 巡檢的發現（預設只列未處理的；status=dismissed／all 看已忽略的）。

    這些是模型自己的推測，不是查核過的事實 —— 一併回傳 `evidence` 與產生它的 `model`，
    讓對話端有依據可以轉述，而不是把推測講成結論。
    """
    from app.models.ai_finding import AIFinding
    st = (status or "open").strip().lower()
    stmt = select(AIFinding)
    if st in ("open", "dismissed"):
        stmt = stmt.where(AIFinding.status == st)
    if severity in ("low", "medium", "high"):
        stmt = stmt.where(AIFinding.severity == severity)
    total = await _count_rows(session, stmt)
    rows = (await session.execute(
        stmt.order_by(AIFinding.created_at.desc()).limit(_clamp_limit(limit, 20, 100))
    )).scalars().all()
    return {"note": "These are AI inferences, not verified facts.",
            "status": st if st in ("open", "dismissed") else "all", **_page_meta(total, len(rows)),
            "findings": [{
                "id": str(f.id), "severity": f.severity, "category": f.category, "title": f.title,
                "detail": f.detail, "recommendation": f.recommendation,
                "evidence": f.evidence, "model": f.model, "status": f.status,
                "object_type": f.object_type, "object_id": str(f.object_id) if f.object_id else None,
                "found_at": _iso_ts(f.created_at), "dismissed_at": _iso_ts(f.dismissed_at),
            } for f in rows]}

async def list_anomalies(
    session: AsyncSession, user: User, *, kind: str | None = None, limit: int = 20,
) -> dict[str, Any]:
    """異常偵測結果（IP 衝突／MAC 變動／失聯 IP／未授權 IP／非法 DHCP）。

    與 AI 巡檢不同：這裡是量到的事實（ARP 真的看到兩個 MAC），不是模型的推測。
    對話端可以直接轉述，不必加「可能」。

    偵測是即時算出來的（沒有結果表）。這裡**逐條呼叫偵測函式，不走 run_detection** ——
    那支除了發通知（使用者在對話裡問一句，不該讓全體管理員收到信）之外，結尾還會無條件
    session.commit()，會把同一個 session 裡其他未定的異動一起送出去。查詢就只該查詢。
    """
    from app.services import anomaly as _an
    detectors = {
        "ip_conflicts": _an.detect_ip_conflicts,
        # 2026-10-09：同一台主機多張網卡回應同一個 IP（從 IP 衝突分出來）、兩個子網段混在同一個二層
        "arp_flux": _an.detect_arp_flux,
        "l2_subnet_bleed": _an.detect_l2_subnet_bleed,
        "mac_drifts": _an.detect_mac_drifts,
        "ghost_ips": _an.detect_ghost_ips,
        "unauthorized_ips": _an.detect_unauthorized_ips,
        "rogue_dhcp": _an.detect_rogue_dhcp,
        "external_exposure": _an.detect_external_exposure,
        "dangling_dns": _an.detect_dangling_dns,
        "dns_compare_mismatch": _an.detect_dns_compare_mismatch,   # 2026-10-10：DNS 比對群組各台不一樣
        "duplicate_ip_records": _an.detect_duplicate_ip_records,
        "suspicious_changes": _an.detect_suspicious_changes,
        "fw_rule_rot": _an.detect_fw_rule_rot,      # 原本漏掉 → AI 問不到規則劣化
        # 這三類也曾經漏掉（2026-10-01 補）：畫面上有、AI 問不到
        "arp_only_liveness": _an.detect_arp_only_liveness,
        "stale_device_links": _an.detect_stale_device_links,
        "mac_flapping": _an.detect_mac_flapping,
        "identity_changes": _an.detect_identity_changes,
    }
    n = max(1, min(int(limit), 100))
    if kind:
        key = kind.strip().lower()
        if key not in detectors:
            return {"error": f"unknown kind: {kind}", "available": sorted(detectors)}
        detectors = {key: detectors[key]}
    buckets = {k: list(await fn(session)) for k, fn in detectors.items()}
    total = sum(len(v) for v in buckets.values())
    # 一疊 0 對小模型來說不夠清楚 —— GitHub issue #34：查無結果時模型自己編了兩個
    # 根本不在這套 IPAM 裡的位址（連 MAC 都是示範用的 VMware 前綴）。事實由查詢決定、
    # 模型只負責敘述，所以把「沒有就是沒有」寫進**工具輸出**，不要指望提示詞。
    note = (
        "No anomalies were detected. Say exactly that. There is nothing to list — "
        "do NOT invent example IPs, MACs, hostnames or subnets to illustrate."
        if total == 0 else
        "Report only the items listed here, copied verbatim. Every IP, MAC and hostname "
        "in your answer must appear in this result."
    )
    out: dict[str, Any] = {
        "total": total,
        # 每一類最多列 limit 筆：被截掉時要說（模型才不會把列出來的當成全部）
        "truncated": any(len(v) > n for v in buckets.values()),
        "counts": {k: len(v) for k, v in buckets.items()},
        # 指定單一 kind 時 items 直接是陣列（附 kind）；沒指定時是 {kind: [...]}。以前一律是物件，
        # 用戶端以為是陣列、拿到長度 1 或 0，就誤判成「有統計沒明細」（2026-10-09 回饋）
        "items": {k: v[:n] for k, v in buckets.items()},
    }
    if kind:
        only = next(iter(buckets))
        out["kind"] = only
        out["items"] = buckets[only][:n]
    if "ip_conflicts" in buckets:
        # GitHub issue #41：偵測器沒有資料時，模型把空結果講成「系統中沒有任何已記錄的 IP 衝突」。
        # 「沒有依據」與「看過了、沒有衝突」要分得開，而且結果是**此刻**的狀態、不是歷史。
        cov = await _an.ip_conflict_coverage(session)
        out["coverage"] = {"ip_conflicts": cov}
        scope = (f"IP conflicts are current state only: ARP observations from the last "
                 f"{cov['window_minutes']} minutes and MAC changes from the last "
                 f"{cov['flip_window_hours']} hours. They are not a history of past conflicts.")
        if cov["observations"] == 0 and not buckets["ip_conflicts"]:
            blind = ("There was no ARP evidence at all in that window (no LibreNMS, scan agent "
                     "or firewall ARP observations), so whether any IP conflict exists cannot be "
                     "determined. Say exactly that — do NOT say there are no IP conflicts.")
            if total == 0:
                # 「沒有偵測到異常，照這樣講」與「不可以說沒有衝突」不能同時出現
                others = [k for k in buckets if k != "ip_conflicts"]
                note = ((f"No anomalies were detected in: {', '.join(others)}. " if others else "")
                        + "Do NOT invent example IPs, MACs, hostnames or subnets. "
                        + blind + " " + scope)
            else:
                note = f"{note} {blind} {scope}"
        else:
            note = f"{note} {scope}"
    out["note"] = note
    return out


async def investigate_ip(
    session: AsyncSession, user: User, *, ip: str,
) -> dict[str, Any]:
    """把一個位址散落在各處的線索收成一份檔案（只回事實，不做推論）。

    可見性由 collect_dossier 依子網路授權處理；全域基礎設施那幾段（NAT／防火牆／DNS／非法 DHCP）
    只有具全域讀取權限者才會拿到，探測、異常、AI 巡檢與主控台連線只有管理員才會拿到
    （與對應的 REST 端點同一層，不會因為走 AI 對話就鬆一級）。
    """
    from app.services.investigate import collect_dossier
    d = await collect_dossier(session, user=user, ip=str(ip).strip())
    if not d.get("found"):
        try:
            ipaddress.ip_address(d.get("ip") or "")
        except ValueError:
            return d
        # 沒有記錄不等於系統沒看過（同 get_ip_detail）；只加在 AI 工具這邊，REST 的調查頁不變
        d["unregistered"] = await _unregistered(session, user, d["ip"])
    return d



async def check_ip_exposure(session: AsyncSession, user: User, ip: str) -> dict[str, Any]:
    """這個位址對外開了什麼 —— NAT 轉發與防火牆放行規則。

    「調查」畫面本來就把這些湊在一起，但只有人點進去才看得到。使用者真正會問的是
    「192.0.2.10 有對外開放嗎、開了哪些埠」，那是一句話的問題，不該要人先知道
    要去哪一頁、再自己讀四張表。

    查的是（與調查檔案同一份資料）：
    1. 各家防火牆同步回來的 NAT 規則中、目的（轉發目標）是這個位址的 —— 含來源限制、埠範圍、說明、來自哪台
    2. OPNsense 過濾規則中、目的「就是」這個位址的放行規則
    3. 六家防火牆裡、目的涵蓋這個位址（直接寫或經由別名／位址物件）的放行規則
    不評估規則順序、介面方向或上游設備；沒列出來不代表外面一定打不到。

    只回事實：有哪些 NAT 轉發、哪些防火牆規則允許進入。**不下結論說它安不安全**
    —— 那取決於這台機器本來就該不該對外，而那件事只有人知道。
    """
    from app.services.investigate import collect_dossier

    d = await collect_dossier(session, user=user, ip=ip)
    if not d.get("found"):
        return {"found": False, "ip": ip}
    nat = d.get("nat") or []
    # 檔案裡的鍵是 firewall_rules、主機名稱在 address 底下（以前讀 firewall／hostname，永遠是空的）
    fw = [r for r in (d.get("firewall_rules") or []) if str(r.get("action", "")).lower() == "pass"]
    # 其他廠牌（與 OPNsense 經別名命中的）：目的端涵蓋這個位址、動作是放行的
    refs = d.get("firewall_refs") or {}
    other = [r for r in (refs.get("rules") or [])
             if (r.get("match") or {}).get("side") == "dst"
             and str(r.get("action", "")).lower() in ("pass", "accept", "allow")]
    ports = sorted({str(n.get("port")) for n in nat if n.get("port") and not n.get("disabled")}
                   | {str(r.get("port")) for r in fw if r.get("port")}
                   | {str(r.get("dst_port")) for r in other if r.get("dst_port")})
    return {
        "found": True,
        "ip": ip,
        "hostname": (d.get("address") or {}).get("hostname"),
        # 有生效中的 NAT 轉發＝從外網打得到；沒有不代表安全（可能走反向代理或另一條路徑）
        "reachable_from_wan": any(not n.get("disabled") for n in nat),
        "open_ports": ports,
        "nat_rules": nat,
        "firewall_allow_rules": fw,
        "other_firewall_allow_rules": other,
        "aliases_containing_ip": refs.get("aliases") or [],
        "checked": ["NAT rules (all vendors) whose target is this address",
                    "OPNsense filter rules whose destination is exactly this address (pass)",
                    "allow rules on OPNsense, pfSense, FortiGate, Palo Alto, Check Point and MikroTik whose "
                    "destination covers this address directly or through an alias / address object"],
        "note": ("Facts only. NAT forwards and pass rules are listed; whether that is"
                 " appropriate depends on what this host is meant to do. Rule order, interface direction"
                 " and upstream devices are not evaluated."),
    }


TOOLS: dict[str, dict[str, Any]] = {
    "search_ip": {
        "fn": search_ip,
        "description": (
            "IPAM record(s) of one IP (several in overlapping subnets) with subnet, hostname, MAC, state, "
            "liveness, owner, device, customer, switch port, device type, OS, last seen and flags; none: "
            "unregistered. More: get_ip_detail."),
        "parameters": {
            "type": "object",
            "properties": {"ip": {"type": "string", "description": "IPv4 or IPv6"}},
            "required": ["ip"],
        },
    },
    "find_free_ip": {
        "fn": find_free_ip,
        "description": (
            "Get the FIRST free IP in a subnet (by id or CIDR). For multiple or "
            "consecutive free IPs, use find_free_ips instead. Addresses seen in use without an IP record "
            "are skipped and listed in skipped_in_use."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "subnet_id": {"type": "string"},
                "subnet_cidr": {"type": "string", "description": "e.g. 10.0.0.0/24"},
            },
        },
    },
    "find_free_ips": {
        "fn": find_free_ips,
        "description": (
            "Find multiple free IPs in a subnet (by id or CIDR). Set count for how "
            "many; set consecutive=true to require a contiguous run. Returns only "
            "genuinely unallocated IPs — never invent or extend the list yourself. Addresses seen in use "
            "(scan agent or ARP) without an IP record are skipped and listed in skipped_in_use."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "subnet_id": {"type": "string"},
                "subnet_cidr": {"type": "string", "description": "e.g. 10.0.0.0/24"},
                "count": {"type": "integer", "minimum": 1, "maximum": 256},
                "consecutive": {"type": "boolean"},
            },
            "required": ["count"],
        },
    },
    "list_subnets": {
        "fn": list_subnets,
        "description": (
            "List subnets with usage and the subnet-list columns (section, parent, VLAN, VRF, customer, location,"
            " gateway, threshold, scanning). Archived subnets only with archived=true. More: get_subnet_detail."),
        "parameters": {
            "type": "object",
            "properties": {
                "section_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                "archived": {"type": "boolean", "description": "true = list the archive instead (default false)"},
            },
        },
    },
    "get_subnet_usage": {
        "fn": get_subnet_usage,
        "description": ("Get used/total/free/used_pct for a subnet by id, plus threshold_pct (the usage "
                        "alert threshold), is_full (manually marked full) and over_threshold."),
        "parameters": {
            "type": "object",
            "properties": {"subnet_id": {"type": "string"}},
            "required": ["subnet_id"],
        },
    },
    "trace_mac": {
        "fn": trace_mac,
        "description": (
            "Who a MAC is and where it is now: vms (VM/CT with this NIC MAC: name, node), ipam_records, "
            "device_ports, dhcp_reservations, ARP and FDB sightings (role access/uplink; 'fdb' = likely access "
            "port). History: mac_history."),
        "parameters": {
            "type": "object",
            "properties": {"mac": {"type": "string", "description": "MAC address"}},
            "required": ["mac"],
        },
    },
    "mac_history": {
        "fn": mac_history,
        "description": (
            "History of one MAC: IPs it used (first/last seen), replacements, switch ports, DHCP reservations, "
            "its device or VM, and MACs that are probably the same device (random MAC rotation)."),
        "parameters": {
            "type": "object",
            "properties": {"mac": {"type": "string", "description": "MAC address in any common format"}},
            "required": ["mac"],
        },
    },
    "list_vlans": {
        "fn": list_vlans,
        "description": ("List VLANs (VID, name, description, domain, customer, section, subnet_count); "
                        "optional exact number lookup. Replies carry total and truncated."),
        "parameters": {
            "type": "object",
            "properties": {
                "number": {"type": "integer", "minimum": 1, "maximum": 4094},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
        },
    },
    "check_dns_consistency": {
        "fn": check_dns_consistency,
        "description": (
            "DNS↔IPAM consistency summary for A/AAAA/PTR: count per state (consistent, dns_only, ipam_only, "
            "mismatch), per server, and up to 20 examples."),
        "parameters": {"type": "object", "properties": {}},
    },
    "stats_overview": {
        "fn": stats_overview,
        "description": (
            "Counts of each entity, scaled to what the user may see; admins / global readers also get VLAN, VRF, "
            "VPN, NAT, VM, circuit and cable counts. Use for 'how many X' questions."),
        "parameters": {"type": "object", "properties": {}},
    },
    "list_racks": {
        "fn": list_racks,
        "description": (
            "Racks and shelving units with location, device count, used/free rows and each device's position "
            "(u_position/u_size, rack_slot/rack_vslot on a 60-cell grid). Shelf kinds (shelf, wire_shelf, "
            "wood_shelf, angle_shelf, kallax) count LEVELS not U: use rows_label and rows_with_space before "
            "saying a row is full."),
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                "location_id": {"type": "string", "description": "Restrict to one location (機房); otherwise all racks"},
            },
        },
    },
    "list_locations": {
        "fn": list_locations,
        "description": ("List locations (地點／機房): address, latitude/longitude, customer, has_floor_plan, "
                        "description, rack_count, device_count. Replies carry total and truncated."),
        "parameters": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500}},
        },
    },
    "list_devices": {
        "fn": list_devices,
        "description": (
            "List or search devices (name substring, type). Rows have the device-list columns (primary IP, type, "
            "vendor, model, serial, location, rack and position, customer). Ports, power, VLANs and integrations "
            "are in get_device; for shelving units call list_racks (rows are levels, not U)."),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "type": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                "location_id": {"type": "string", "description": "Restrict to one location (機房)"},
                "rack_id": {"type": "string", "description": "Restrict to one rack"},
            },
        },
    },
    "get_device": {
        "fn": get_device,
        "description": (
            "One device by id or name: every field of the device page, IPs, ports and peers, power ports, VLANs, "
            "rack position, LibreNMS / Wazuh / VM / OCS / RustDesk data, hosted_vms, circuits, vpn_tunnels, lease,"
            " maintenance. Name shared: ambiguous=true with candidates; ask, or pass device_id."),
        "parameters": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string"},
                "name": {"type": "string"},
            },
        },
    },
    "list_customers": {
        "fn": list_customers,
        "description": ("List customers / management units (客戶 / 管理單位): name, title, description, "
                        "contact, email, phone, address, subnet_count. Replies carry total and truncated."),
        "parameters": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500}},
        },
    },
    "list_nat": {
        "fn": list_nat,
        "description": (
            "NAT rules with every field of the NAT form (type, firewall, interface, protocol, source/destination "
            "with *_not = 'anything except', port ranges and aliases, redirect, reflection, disabled, "
            "description)."),
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                "subnet_cidr": {"type": "string"},
                "subnet_id": {"type": "string"},
            },
        },
    },
    "switch_port_for_ip": {
        "fn": switch_port_for_ip,
        "description": (
            "Which switch and port an IP is connected to (FDB), most likely access port first, with the "
            "switch_port recorded on the IP. Switches you cannot see are omitted. An IP in several subnets "
            "returns candidates: call again with ip_id."),
        "parameters": {
            "type": "object",
            "properties": {
                "ip": {"type": "string"},
                "subnet_cidr": {"type": "string",
                                "description": "subnet the IP is in, when the same IP exists in several"},
                "ip_id": {"type": "string", "description": "exact IP record id (from an overlap error or search)"},
            },
            "required": ["ip"],
        },
    },
    "list_sections": {
        "fn": list_sections,
        "description": ("List sections (區段): name, description, parent section, customer, display_order, "
                        "strict_mode, subnet_count. Replies carry total and truncated."),
        "parameters": {"type": "object", "properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_vrfs": {
        "fn": list_vrfs,
        "description": ("List VRFs: name, rd, description, allow_overlap (may subnets inside it overlap), "
                        "subnet_count. Replies carry total and truncated."),
        "parameters": {"type": "object", "properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "recent_ip_changes": {
        "fn": recent_ip_changes,
        "description": (
            "Recent IP change-log entries, newest first (optional ip): subnet, field, old/new value, source, "
            "actor, note."),
        "parameters": {"type": "object", "properties": {
            "ip": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100}}},
    },
    "list_vpn_tunnels": {
        "fn": list_vpn_tunnels,
        "description": (
            "VPN tunnels (WireGuard / IPsec / OpenVPN) from firewalls or entered by hand: both ends, algorithms, "
            "status. site_to_site pairing is reliable only when pairing_method=wireguard_pubkey; ipsec_endpoint is best "
            "effort. contacts, and "
            "carried_by_circuits (circuits whose WAN addresses contain an endpoint)."),
        "parameters": {"type": "object", "properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "dns_lookup": {
        "fn": dns_lookup,
        "description": ("Look up DNS records by hostname / FQDN substring (any record type): value, TTL, zone, "
                        "DNS server, consistency state and the matching IPAM address. At most 50 rows; "
                        "'total' / 'truncated' tell whether there are more."),
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}},
                       "required": ["name"]},
    },
    "global_search": {
        "fn": global_search,
        "description": (
            "Global search (IP, CIDR, MAC, VLAN number, text) across section, subnet, IP, device, vlan, vm, vpn, "
            "customer, rack, location, nat, dns, firewall and IP request records, like the search box."),
        "parameters": {"type": "object", "properties": {"q": {"type": "string"}},
                       "required": ["q"]},
    },
    "list_connection_targets": {
        "fn": list_connection_targets,
        "description": (
            "IPs with a browser console you may open (SSH, SFTP, RDP, VNC, Proxmox novnc, BMC, RustDesk). Never "
            "returns credentials. Optional protocol filter."),
        "parameters": {"type": "object", "properties": {
            "protocol": {"type": "string", "enum": list(_CONSOLE_KINDS)},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "oui_lookup": {
        "fn": oui_lookup,
        "description": "Look up the hardware vendor for a single, complete MAC address (OUI).",
        "parameters": {"type": "object", "properties": {"mac": {"type": "string"}},
                       "required": ["mac"]},
    },
    "oui_search": {
        "fn": oui_search,
        "description": (
            "Search the OUI registry by MAC prefix and/or vendor name (several vendors). One full MAC: "
            "oui_lookup."),
        "parameters": {"type": "object", "properties": {
            "prefix": {"type": "string", "description": "partial OUI hex, e.g. '22', '00:11', '0011aa'"},
            "name": {"type": "string", "description": "vendor name substring"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "allocate_ip": {
        "fn": allocate_ip,
        "description": (
            "ADMIN ONLY. Allocate an IP in a subnet (subnet_id or subnet_cidr): requested_ip or the first free "
            "one, with the new-IP form fields. IPs in cooldown are refused."),
        "parameters": {
            "type": "object",
            "properties": {
                "subnet_id": {"type": "string"},
                "subnet_cidr": {"type": "string"},
                "requested_ip": {"type": "string", "description": "exact IP to allocate"},
                "hostname": {"type": "string"},
                "state": {"type": "string", "enum": sorted(_IP_STATE_ENUM)},
                "owner": {"type": "string"},
                "customer": {"type": "string", "description": "customer/unit name or id"},
                "mac": {"type": "string"},
                "description": {"type": "string"},
                "note": {"type": "string"},
                "device": {"type": "string", "description": "device name, FQDN or id to link the IP to"},
                "switch_port": {"type": "string", "description": "e.g. 'sw-01 / Gi1/0/5'"},
            },
        },
    },
    "check_ip_exposure": {
        "fn": check_ip_exposure,
        "description": (
            "Is an IP reachable from the internet and on which ports: NAT rules targeting it and allow rules of "
            "OPNsense, pfSense, FortiGate, Palo Alto, Check Point and MikroTik covering it (also via aliases). It"
            " does not evaluate rule order, direction or upstream devices: an empty result is not proof."),
        "parameters": {"type": "object", "properties": {"ip": {"type": "string"}},
                       "required": ["ip"]},
    },
    "get_ip_history": {
        "fn": get_ip_history,
        "description": (
            "Forensic timeline of one IP: change log (actor, note), ARP MAC bindings, hostname observations, DHCP"
            " sightings. 'truncated' names sections that hit their cap."),
        "parameters": {"type": "object", "properties": {
            "ip": {"type": "string"},
            "days": {"type": "integer", "description": "lookback window, default 30, max 365"}},
            "required": ["ip"]},
    },
    "get_ip_detail": {
        "fn": get_ip_detail,
        "description": (
            "One IP: every field of the IP form and detail page (state, hostname, MAC/vendor, "
            "owner, device, customer, switch port, OS, last seen per source, flags (ptr_ignore…), DHCP/gateway tags, consoles, "
            "OCS, RustDesk), vms_with_this_mac, vms_reporting_this_ip, arp_macs (for IP conflicts), maintenance, "
            "overlapping records. No record: unregistered."),
        "parameters": {"type": "object", "properties": {"ip": {"type": "string"}}, "required": ["ip"]},
    },
    "get_subnet_detail": {
        "fn": get_subnet_detail,
        "description": (
            "Full subnet info: every field of the subnet form (gateway, DNS, VLAN, VRF, section, customer, "
            "location, parent, pool/full, usage threshold, scan settings, anomaly / AI review, console exit), "
            "usage and its address ranges (DHCP pool / reserved / other). In a DHCP range, used counts records "
            "in use (lease, reservation, or not offline); stale = offline records with no lease, not counted; "
            "first_free = next address with no record. Provide subnet_id or subnet_cidr."),
        "parameters": {"type": "object", "properties": {
            "subnet_id": {"type": "string"}, "subnet_cidr": {"type": "string"}}},
    },
    "list_subnet_ips": {
        "fn": list_subnet_ips,
        "description": (
            "Registered IPs of a subnet with the IP-list columns (hostname, state, liveness, MAC/vendor, owner, "
            "device, switch port, type, OS, last seen); offset 0 adds unmanaged (in use, no record). Give "
            "subnet_id or subnet_cidr; next page: offset=next_offset."),
        "parameters": {"type": "object", "properties": {
            "subnet_id": {"type": "string"}, "subnet_cidr": {"type": "string"},
            "state": {"type": "string", "description": "optional filter e.g. active"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
            "offset": {"type": "integer", "minimum": 0, "description": "skip N rows; use next_offset from a previous call for the next batch"}}},
    },
    "list_firewalls": {
        "fn": list_firewalls,
        "description": (
            "All firewalls of the six vendors (OPNsense, pfSense, FortiGate, Palo Alto, Check Point, MikroTik) "
            "with their settings (no secrets): URL, enabled, TLS check, sync interval and items, subnet scope, "
            "last sync and error, vendor extras (VDOMs, vsys, domains, RouterOS version)."),
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_dhcp_ranges": {
        "fn": list_dhcp_ranges,
        "description": (
            "DHCP pool ranges from every source (OPNsense, pfSense, FortiGate, Palo Alto, Check Point, MikroTik, "
            "Windows DHCP, Kea, ISC, Technitium, ISOinsight, manual pools) with exclusions; rows of one pool "
            "share pool_start/pool_end. Use it to tell whether an address is in a DHCP pool; never guess."),
        "parameters": {"type": "object", "properties": {"subnet_cidr": {"type": "string"}, "subnet_id": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_fortigate_policies": {
        "fn": list_fortigate_policies,
        "description": "List FortiGate firewall policies. Filter by firewall_name and/or vdom.",
        "parameters": {"type": "object", "properties": {
            "firewall_name": {"type": "string"}, "vdom": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_fortigate_addresses": {
        "fn": list_fortigate_addresses,
        "description": ("List FortiGate address objects and address groups. "
                        "Filter by firewall_name and/or vdom."),
        "parameters": {"type": "object", "properties": {
            "firewall_name": {"type": "string"}, "vdom": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_paloalto_policies": {
        "fn": list_paloalto_policies,
        "description": ("List Palo Alto (PAN-OS) security policies in evaluation order. "
                        "Includes the App-ID, which is what a PAN-OS rule actually matches on. "
                        "Filter by firewall_name and/or vsys."),
        "parameters": {"type": "object", "properties": {
            "firewall_name": {"type": "string"}, "vsys": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_paloalto_addresses": {
        "fn": list_paloalto_addresses,
        "description": ("List Palo Alto address objects and address groups. "
                        "Filter by firewall_name and/or vsys."),
        "parameters": {"type": "object", "properties": {
            "firewall_name": {"type": "string"}, "vsys": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_checkpoint_rules": {
        "fn": list_checkpoint_rules,
        "description": ("List Check Point access rules (from the management server) in evaluation order. "
                        "When source_negate/destination_negate is true the field means 'anything except these'. "
                        "Filter by server_name and/or layer."),
        "parameters": {"type": "object", "properties": {
            "server_name": {"type": "string"}, "layer": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_checkpoint_objects": {
        "fn": list_checkpoint_objects,
        "description": ("List Check Point network objects (hosts, networks, ranges, groups, groups with exclusion). "
                        "Filter by server_name and/or a name fragment."),
        "parameters": {"type": "object", "properties": {
            "server_name": {"type": "string"}, "name": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_mikrotik_rules": {
        "fn": list_mikrotik_rules,
        "description": ("List MikroTik RouterOS firewall rules in evaluation order "
                        "(RouterOS matches top-down; the order is the semantics). "
                        "Filter by router_name and/or table (filter|nat|mangle)."),
        "parameters": {"type": "object", "properties": {
            "router_name": {"type": "string"},
            "table": {"type": "string", "enum": ["filter", "nat", "mangle"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_mikrotik_address_lists": {
        "fn": list_mikrotik_address_lists,
        "description": ("List MikroTik address-list entries (RouterOS's equivalent of "
                        "aliases; one row per address). Filter by router_name and/or list_name."),
        "parameters": {"type": "object", "properties": {
            "router_name": {"type": "string"}, "list_name": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_firewall_rules": {
        "fn": list_firewall_rules,
        "description": ("List synced OPNsense filter rules (each row names its firewall). Filter by firewall_id or "
                        "firewall_name. The reply carries 'total' and 'truncated'."),
        "parameters": {"type": "object", "properties": {
            "firewall_id": {"type": "string"}, "firewall_name": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_firewall_aliases": {
        "fn": list_firewall_aliases,
        "description": (
            "Firewall aliases: IPAM→OPNsense alias mappings and the aliases that exist on OPNsense / pfSense with"
            " members; every row names its firewall."),
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "get_topology": {
        "fn": get_topology,
        "description": (
            "Network topology: nodes (devices, subnets, VPN sites) and edges (cable, wireless, vpn, l3, l2, "
            "l2_uplink, vm_host) with evidence and ports. Size-limited: if truncated, call again with "
            "subnet_cidr."),
        "parameters": {"type": "object", "properties": {
            "subnet_cidr": {"type": "string"},
            "include_l3": {"type": "boolean"}, "include_vpn": {"type": "boolean"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 1000,
                      "description": "max nodes and max edges (each) before the size budget"}}},
    },
    "list_dns_servers": {
        "fn": list_dns_servers,
        "description": (
            "DNS servers (PowerDNS, BIND9, UCS, Unbound, Windows DNS, Technitium) with settings (no secrets), "
            "compare group, zones, last sync and error."),
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_dns_zones": {
        "fn": list_dns_zones,
        "description": "List DNS zones with their DNS server, type, associated subnets and record count.",
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_dns_records": {
        "fn": list_dns_records,
        "description": (
            "DNS records of integrated servers, every type. Filter by name/value substring and rtype; "
            "missing_ip=true = A/AAAA whose IP is not in IPAM. Rows carry ttl, zone, server, consistency."),
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"}, "rtype": {"type": "string"},
            "missing_ip": {"type": "boolean"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_ip_requests": {
        "fn": list_ip_requests,
        "description": (
            "IP allocation requests: status, subnet, requested / allocated IP, hostname, purpose, requester, "
            "approver, rejected reason, multi-step approval progress. Approvers see all, others their own. "
            "Optional status."),
        "parameters": {"type": "object", "properties": {
            "status": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_scan_agents": {
        "fn": list_scan_agents,
        "description": (
            "Scan agents with status and settings (version, last seen, subnets, probes and intervals, tools, "
            "load, relay). Never returns keys."),
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 200}}},
    },
    "investigate_ip": {
        "fn": investigate_ip,
        "description": (
            "Everything known about one IP in one place: the record, other records in overlapping subnets, "
            "hostname and OS per source, device type, monitoring and endpoint agents, VM, DHCP, switch ports, "
            "firewall evidence, last seen per source, ARP history, recent changes and contradictions. Facts only."),
        "parameters": {
            "type": "object",
            "properties": {"ip": {"type": "string", "description": "IPv4 or IPv6"}},
            "required": ["ip"],
        },
    },
    "list_anomalies": {
        "fn": list_anomalies,
        "description": (
            "Anomaly detection results (measured facts, not AI inference): IP conflicts, ARP flux, two subnets on"
            " one L2 segment, MAC drifts, ghost IPs, unauthorised IPs, rogue DHCP servers, exposed hosts. Pass "
            "kind for one category. MACs marked suspect (corrupt / stale_cache) are listed but not counted as "
            "machines."),
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "description":
                         "ip_conflicts | arp_flux | l2_subnet_bleed | mac_drifts | ghost_ips | unauthorized_ips | "
                         "rogue_dhcp | external_exposure | dangling_dns | dns_compare_mismatch | duplicate_ip_records | suspicious_changes | "
                         "fw_rule_rot | arp_only_liveness | stale_device_links | mac_flapping | "
                         "identity_changes (device type or OS family changed)"},
                "limit": {"type": "integer", "description": "max items per kind (default 20)"},
            },
        },
    },
    "list_ai_findings": {
        "fn": list_ai_findings,
        "description": (
            "Findings of the scheduled AI inventory review (default open; status=dismissed or all). These are "
            "model inferences, not verified facts: present them as such and cite the evidence."),
        "parameters": {"type": "object", "properties": {
            "severity": {"type": "string", "enum": ["low", "medium", "high"]},
            "status": {"type": "string", "enum": ["open", "dismissed", "all"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100}}},
    },
    "list_certificates": {
        "fn": list_certificates,
        "description": (
            "Managed TLS certificates (metadata only, never private keys): domains, fingerprint, issuer, subject,"
            " validity (not_before, not_after, days left), warning days, auto-fetch settings. "
            "expiring_within_days=N lists only those expiring within N days."),
        "parameters": {"type": "object", "properties": {
            "expiring_within_days": {"type": "integer", "minimum": 0, "maximum": 3650},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_cert_distribution": {
        "fn": list_cert_distribution,
        "description": (
            "Certificate distribution agents: linked device, certificates they may fetch, what each deployed, "
            "whether up to date, validity, version, shared enrollment keys."),
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_arp": {
        "fn": list_arp,
        "description": ("ARP entries (IP↔MAC): which device reported it (device name), on which interface/VRF, "
                        "source (librenms, scanner, arp:<firewall vendor>), subnet, first and last seen. Filter by "
                        "ip or mac. The reply carries 'total' and 'truncated'."),
        "parameters": {"type": "object", "properties": {
            "ip": {"type": "string"}, "mac": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_fdb": {
        "fn": list_fdb,
        "description": "Switch FDB entries (MAC, switch, port, VLAN, source, first/last seen). Filter by mac.",
        "parameters": {"type": "object", "properties": {"subnet_cidr": {"type": "string"}, "subnet_id": {"type": "string"},
            "mac": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "wazuh_missing_agents": {
        "fn": wazuh_missing_agents,
        "description": (
            "IPs with a hostname but no active Wazuh agent (security coverage gap), with status, device type, OS,"
            " subnet, section, customer and whether an inactive agent exists."),
        "parameters": {"type": "object", "properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            "subnet_cidr": {"type": "string"},
            "subnet_id": {"type": "string"}}},
    },
    "list_attack_surface": {
        "fn": list_attack_surface,
        "description": (
            "Externally reachable services (IP:port) with their IPAM identity and the DNS names resolving to "
            "them. Ask by fqdn= or ip=. registered=false means IPAM does not know the host: report it as a "
            "finding. external_dns (when set up) gives each FQDN's public answer, who owns that IP and a status "
            "(consistent, not_public, foreign, mismatch, no_path, query_failed)."),
        "parameters": {"type": "object", "properties": {
            "fqdn": {"type": "string", "description": "Restrict to services reachable via this FQDN"},
            "ip": {"type": "string", "description": "Restrict to this address"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "get_customer_summary": {
        "fn": get_customer_summary,
        "description": ("One customer / management unit: name, title, description, contact, email, phone, "
                        "address, plus counts of sections/subnets/devices/IPs/locations. Provide customer_id or name."),
        "parameters": {"type": "object", "properties": {
            "customer_id": {"type": "string"}, "name": {"type": "string"}}},
    },
    "list_vms": {
        "fn": list_vms,
        "description": (
            "Virtual machines (Proxmox VE, VMware, Hyper-V, CubeCOS/OpenStack): VMID, cluster, platform, node, kind, status, "
            "resources, guest OS, primary IP, description (OpenStack: project), "
            "linked device, NICs (MAC, IP, bridge, VLAN)."),
        "parameters": {"type": "object", "properties": {"subnet_cidr": {"type": "string"}, "subnet_id": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_wireless_links": {
        "fn": list_wireless_links,
        "description": "List wireless point-to-point links: both end devices, SSID, distance and description.",
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "update_ip": {
        "fn": update_ip,
        "description": (
            "ADMIN ONLY. Change fields of one IP record (same rules as the IP form); only passed fields change, "
            "empty string clears. An IP in several subnets returns candidates: pass ip_id."),
        "parameters": {"type": "object", "properties": {
            "ip": {"type": "string"},
            "subnet_cidr": {"type": "string",
                            "description": "subnet the IP is in, when the same IP exists in several"},
            "ip_id": {"type": "string", "description": "exact IP record id (from an overlap error or search)"},
            "hostname": {"type": "string"},
            "state": {"type": "string", "enum": sorted(_IP_STATE_ENUM)},
            "owner": {"type": "string"}, "description": {"type": "string"}, "mac": {"type": "string"},
            "note": {"type": "string"},
            "customer": {"type": "string", "description": "customer/unit name or id; empty string clears"},
            "device": {"type": "string", "description": "device name, FQDN or id; empty string unlinks"},
            "switch_port": {"type": "string"},
            "liveness_from_device": {
                "type": "boolean",
                "description": "online status follows the linked device (gateways that do not answer pings)"},
            "exclude_from_ping": {"type": "boolean"},
            "excluded_probes": {"type": "array", "items": {"type": "string", "enum": sorted(_PROBE_ENUM)},
                                "description": "probes to skip for this IP (icmp = do not ping)"},
            "ptr_ignore": {"type": "boolean", "description": "ignore this IP in PTR / reverse DNS checks"},
            "is_dhcp_server": {"type": "boolean"}},
            "required": ["ip"]},
    },
    "create_subnet": {
        "fn": create_subnet,
        "description": (
            "ADMIN ONLY. Create a subnet (same rules as the subnet form): cidr plus section_id or section_name. "
            "Overlaps are refused unless allow_overlap=true; parent and its customer are found automatically."),
        "parameters": {"type": "object", "properties": {
            "cidr": {"type": "string"}, "section_id": {"type": "string"}, "section_name": {"type": "string"},
            "description": {"type": "string"}, "gateway": {"type": "string"},
            "vlan": {"type": "string", "description": "VLAN number, name or id"},
            "vrf": {"type": "string", "description": "VRF name or id"},
            "customer": {"type": "string", "description": "customer/unit name or id"},
            "location": {"type": "string", "description": "location name or id"},
            "dns_servers": {"type": "string", "description": "comma separated"},
            "threshold_pct": {"type": "integer", "minimum": 0, "maximum": 100,
                              "description": "usage alert threshold in percent"},
            "is_pool": {"type": "boolean"}, "is_full": {"type": "boolean"},
            "anomaly_enabled": {"type": "boolean"}, "ai_audit_enabled": {"type": "boolean"},
            "scan_enabled": {"type": "boolean"},
            "scan_method": {"type": "array", "items": {"type": "string", "enum": sorted(_PROBE_ENUM)}},
            "scan_scope": {"type": "string", "enum": ["auto", "all", "known", "ranges"]},
            "allow_overlap": {"type": "boolean"}}, "required": ["cidr"]},
    },
    "create_device": {
        "fn": create_device,
        "description": (
            "ADMIN ONLY. Create a device (same fields as the device form). Rack placement needs rack, u_position "
            "and u_size; occupied units are refused. primary_ip links an existing IP."),
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"}, "type": {"type": "string", "enum": sorted(_DEVICE_TYPE_ENUM)},
            "fqdn": {"type": "string"}, "vendor": {"type": "string"}, "model": {"type": "string"},
            "serial": {"type": "string"}, "description": {"type": "string"},
            "location": {"type": "string", "description": "location name or id"},
            "rack": {"type": "string", "description": "rack name or id"},
            "u_position": {"type": "integer", "minimum": 1, "maximum": 99},
            "u_size": {"type": "integer", "minimum": 1, "maximum": 99},
            "rack_face": {"type": "string", "enum": ["front", "rear"]},
            "customer": {"type": "string", "description": "customer/unit name or id"},
            "primary_ip": {"type": "string"}, "primary_ip_id": {"type": "string"}},
            "required": ["name"]},
    },
    "approve_ip_request": {
        "fn": approve_ip_request,
        "description": (
            "ADMIN ONLY. Approve an IP request per the approval policy: multi-step approval approves only your "
            "step and allocates when all steps pass. Optional ip overrides the address."),
        "parameters": {"type": "object", "properties": {
            "request_id": {"type": "string"},
            "ip": {"type": "string", "description": "IP to allocate instead of the requested / first free one"}},
            "required": ["request_id"]},
    },
    "reject_ip_request": {
        "fn": reject_ip_request,
        "description": "ADMIN ONLY. Reject an IP request with a reason (3 to 1024 characters).",
        "parameters": {"type": "object", "properties": {
            "request_id": {"type": "string"}, "reason": {"type": "string"}}, "required": ["request_id", "reason"]},
    },
    # ─── 網路工具（純運算，不碰資料庫）───
    "calc_ip_info": {
        "fn": calc_ip_info,
        "description": (
            "Analyse a single IP address: version, private/global/reserved/multicast/"
            "loopback/link-local flags, decimal/hex/binary, reverse DNS pointer. Pure "
            "calculation, no lookup against IPAM records."
        ),
        "parameters": {"type": "object", "properties": {
            "ip": {"type": "string", "description": "IPv4 or IPv6"}}, "required": ["ip"]},
    },
    "calc_cidr_info": {
        "fn": calc_cidr_info,
        "description": (
            "Analyse a CIDR/network: network & broadcast address, netmask, hostmask, "
            "prefix length, total addresses, usable host count, first/last host."
        ),
        "parameters": {"type": "object", "properties": {
            "cidr": {"type": "string", "description": "e.g. 192.168.0.0/24"}}, "required": ["cidr"]},
    },
    "calc_cidr_split": {
        "fn": calc_cidr_split,
        "description": "Split a CIDR into equal-sized smaller subnets of new_prefix length.",
        "parameters": {"type": "object", "properties": {
            "cidr": {"type": "string"},
            "new_prefix": {"type": "integer", "minimum": 0, "maximum": 128}},
            "required": ["cidr", "new_prefix"]},
    },
    "calc_eui64": {
        "fn": calc_eui64,
        "description": "Generate the EUI-64 IPv6 address from a MAC and an IPv6 prefix (RFC 4291).",
        "parameters": {"type": "object", "properties": {
            "mac": {"type": "string"}, "prefix": {"type": "string", "description": "e.g. 2001:db8::/64"}},
            "required": ["mac", "prefix"]},
    },
    "calc_ip_in_cidr": {
        "fn": calc_ip_in_cidr,
        "description": "Check whether an IP falls inside a CIDR; also flags network/broadcast address.",
        "parameters": {"type": "object", "properties": {
            "ip": {"type": "string"}, "cidr": {"type": "string"}}, "required": ["ip", "cidr"]},
    },
    "calc_cidr_relation": {
        "fn": calc_cidr_relation,
        "description": (
            "Relationship between two CIDRs: equal / a_contains_b / a_within_b / overlap / disjoint."
        ),
        "parameters": {"type": "object", "properties": {
            "a": {"type": "string"}, "b": {"type": "string"}}, "required": ["a", "b"]},
    },
    "calc_range_to_cidr": {
        "fn": calc_range_to_cidr,
        "description": "Summarise an IP range (start..end) into the minimal set of CIDR blocks.",
        "parameters": {"type": "object", "properties": {
            "start": {"type": "string"}, "end": {"type": "string"}}, "required": ["start", "end"]},
    },
    "calc_cidr_to_range": {
        "fn": calc_cidr_to_range,
        "description": "Convert a CIDR to its first/last address and total address count.",
        "parameters": {"type": "object", "properties": {"cidr": {"type": "string"}}, "required": ["cidr"]},
    },
    "calc_aggregate": {
        "fn": calc_aggregate,
        "description": "Collapse/aggregate multiple CIDRs (comma- or space-separated) into the minimal set.",
        "parameters": {"type": "object", "properties": {
            "cidrs": {"type": "string", "description": "e.g. '192.168.0.0/24, 198.51.100.0/24'"}},
            "required": ["cidrs"]},
    },
    "calc_netmask": {
        "fn": calc_netmask,
        "description": "Convert between prefix length (24 or /24) and dotted netmask (255.255.255.0); returns wildcard/hostmask.",
        "parameters": {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]},
    },
    "calc_mac_format": {
        "fn": calc_mac_format,
        "description": "Normalise a MAC into colon/dash/cisco-dot/bare forms; returns OUI, locally-administered & multicast bits.",
        "parameters": {"type": "object", "properties": {"mac": {"type": "string"}}, "required": ["mac"]},
    },
    "calc_fqdn": {
        "fn": calc_fqdn,
        "description": "Parse/validate an FQDN (RFC 1123): labels, host, domain, TLD, validity.",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    },
    "dns_resolve": {
        "fn": dns_resolve,
        "description": (
            "Live DNS resolution via the system resolver: A / AAAA / PTR. Use this to "
            "resolve a hostname to IPs or do reverse lookup. (Differs from dns_lookup, "
            "which searches IPAM's own DNS records.)"
        ),
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"}, "type": {"type": "string", "enum": ["A", "AAAA", "PTR", "ANY"]}},
            "required": ["name"]},
    },
    "dns_mail_check": {
        "fn": dns_mail_check,
        "description": "Mail-related DNS diagnostics for a domain: MX, SPF, DMARC, and DKIM (if a selector is given).",
        "parameters": {"type": "object", "properties": {
            "domain": {"type": "string"}, "dkim_selector": {"type": "string"}}, "required": ["domain"]},
    },
    "geoip_locate": {
        "fn": geoip_locate,
        "description": "Geolocate an IP (MaxMind GeoLite2 web service). Requires GeoIP credentials configured in system settings.",
        "parameters": {"type": "object", "properties": {"ip": {"type": "string"}}, "required": ["ip"]},
    },
    "power_calc": {
        "fn": power_calc,
        "description": (
            "Datacenter power/cooling calculations: load watts (V×A×PF, ×√3 for 3-phase), "
            "BTU/hr heat, UPS runtime minutes (batt_wh / load_w), and PDU 80% safe amps. "
            "Provide whichever inputs are relevant."
        ),
        "parameters": {"type": "object", "properties": {
            "volts": {"type": "number"}, "amps": {"type": "number"},
            "phase": {"type": "string", "enum": ["1", "3"]}, "pf": {"type": "number"},
            "heat_watts": {"type": "number"}, "batt_wh": {"type": "number"},
            "load_w": {"type": "number"}, "pdu_a": {"type": "number"}}},
    },
    # ─── 進階資源 / 實體層 / 整合（全域基礎設施）───
    "list_circuits": {
        "fn": list_circuits,
        "description": (
            "Circuits / WAN links: provider, type, status, bandwidth, fee, install_date, contract_end_date with "
            "contract_days_left, wan_addresses linked in IPAM (role wan / gateway / peer), contacts and leased_devices."
            " Use for contract expiry and WAN questions."),
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_domains": {
        "fn": list_domains,
        "description": (
            "Registered domains: registrar, DNS hosting (entered or inferred from nameservers), expiry with "
            "days_left, auto_renew, bound services. No passwords are stored. Use for domain expiry and DNS hosting."),
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_maintenance": {
        "fn": list_maintenance,
        "description": (
            "Maintenance windows (維護排程): alerts for objects in scope are muted or only marked. Use for what is "
            "under maintenance, why no alert was sent, and when maintenance ends."),
        "parameters": {"type": "object", "properties": {
            "status": {"type": "string", "enum": ["active", "upcoming", "past", "all"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_providers": {
        "fn": list_providers,
        "description": "List circuit/transit providers (供應商：ASN、帳號、入口網址、NOC、說明、電路數).",
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_asns": {
        "fn": list_asns,
        "description": "List autonomous system numbers (ASN) with RIR, owning tenant and description.",
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_tenants": {
        "fn": list_tenants,
        "description": "List tenants (租戶) with slug, tenant group and description.",
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_contacts": {
        "fn": list_contacts,
        "description": "List contacts (聯絡人：職稱 / 電話 / Email / 地址 / 聯絡人群組 / 租戶 / 說明).",
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_ssids": {
        "fn": list_ssids,
        "description": "List wireless SSIDs (auth type, VLAN, tenant, description).",
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_cables": {
        "fn": list_cables,
        "description": "List cables (佈線：線標、類型、顏色、長度、狀態、說明) with both ends resolved to device name and port name. For the full path through patch panels use cable_trace.",
        "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "cable_trace": {
        "fn": cable_trace,
        "description": "Trace one cable: label, type, color, length, status, description, which device/port each end connects to, and the multi-hop path end to end (following further cables and passing through patch panel front/rear ports). Provide cable_id.",
        "parameters": {"type": "object", "properties": {"cable_id": {"type": "string"}}, "required": ["cable_id"]},
    },
    "list_power": {
        "fn": list_power,
        "description": (
            "Power: panels, feeds, outlets with the device power supplies plugged in, and device power ports. "
            "rack_id for one rack."),
        "parameters": {"type": "object", "properties": {"rack_id": {"type": "string", "description": "Restrict to one rack"}, "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_ocs_computers": {
        "fn": list_ocs_computers,
        "description": (
            "Computers inventoried by OCS Inventory, one entry per computer with its IPs, OS, asset tag, agent "
            "version, last inventory and hardware summary. stale_days=N finds assets not inventoried for N days. "
            "count is the number of computers."),
        "parameters": {"type": "object", "properties": {"subnet_cidr": {"type": "string"}, "subnet_id": {"type": "string"}, "stale_days": {"type": "integer", "minimum": 0, "description": "Only computers not inventoried for this many days"}, "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_rustdesk_audit": {
        "fn": list_rustdesk_audit,
        "description": (
            "RustDesk connection audit from the clients: who connected to which device and when (conn), file "
            "transfers (file) and alarms (alarm: 1/2 = wrong passwords, 6 = IPv6 prefix flood, 0/10 = allowlist "
            "violation). Default window 168 hours; state 'since'."),
        "parameters": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["conn", "file", "alarm", "note"]}, "q": {"type": "string", "description": "Search device or peer RustDesk ID, peer name, hostname"}, "hours": {"type": "integer", "minimum": 1, "maximum": 9600}, "subnet_cidr": {"type": "string"}, "subnet_id": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_rustdesk_peers": {
        "fn": list_rustdesk_peers,
        "description": (
            "Devices registered on the RustDesk server: ID, online, last seen, server-seen IP, the matched IPAM "
            "record and how it matched, plus what the client reports (hostname, OS, user, version, heartbeat, key"
            " errors)."),
        "parameters": {"type": "object", "properties": {"subnet_cidr": {"type": "string"}, "subnet_id": {"type": "string"}, "online": {"type": "boolean", "description": "Only online (true) or offline (false) devices"}, "q": {"type": "string", "description": "Search RustDesk ID, hostname (record or client-reported), user or IP"}, "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
    "list_wazuh_agents": {
        "fn": list_wazuh_agents,
        "description": (
            "Wazuh agents: server, status, IP, OS, version, group, last keep-alive and the worst SCA policy "
            "result. No vulnerability counts. For the coverage gap use wazuh_missing_agents."),
        "parameters": {"type": "object", "properties": {"subnet_cidr": {"type": "string"}, "subnet_id": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 500}}},
    },
}


# ─────────────────── AI 對話：異動類工具需使用者確認 ───────────────────
# 這些工具會新增 / 修改 / 刪除資料；AI 對話中不直接執行，先回前端請使用者按「確認」。
from app.mcp.impact_tools import IMPACT_TOOLS  # noqa: E402 -- 工具字典建好之後才併入

TOOLS.update(IMPACT_TOOLS)
# ISOinsight 來源租約（唯讀、逐物件權限：工具內依可見子網路過濾）
from app.mcp.isoinsight_tools import ISOINSIGHT_TOOLS  # noqa: E402

TOOLS.update(ISOINSIGHT_TOOLS)
# 畫面地圖：功能／頁籤／欄位在網頁的哪裡（不碰資料）
from app.mcp.ui_tools import UI_TOOLS  # noqa: E402

TOOLS.update(UI_TOOLS)

MUTATING_TOOLS: frozenset[str] = frozenset({
    "allocate_ip", "update_ip", "create_subnet", "create_device",
    "approve_ip_request", "reject_ip_request",
    # IP 變更評估：建立計畫、開始分析、存 AI 草擬的待辦（都不改來源資料，但會寫入計畫）
    "impact_create_plan", "impact_start_run", "impact_accept_task_draft",
})


# 純計算 / 外部查詢類工具（不碰 IPAM 資料）→ 不受 RBAC 可見範圍限制
UTILITY_TOOLS: frozenset[str] = frozenset({
    "calc_ip_info", "calc_cidr_info", "calc_cidr_split", "calc_eui64",
    "calc_ip_in_cidr", "calc_cidr_relation", "calc_range_to_cidr",
    "calc_cidr_to_range", "calc_aggregate", "calc_netmask", "calc_mac_format",
    "calc_fqdn", "dns_resolve", "dns_mail_check", "geoip_locate", "power_calc",
    "oui_lookup", "oui_search",
    # 畫面地圖只讀前端原始碼的選單與欄位名稱，不碰任何 IPAM 資料
    "find_ui_location",
})


# 全域基礎設施工具（無法逐物件授權）→ 僅 admin 或具萬用讀取權限者可呼叫。
# 對應 CLAUDE.md 的 require_global_read 分類；只被指派特定物件的部門帳號一律擋。


GLOBAL_READ_TOOLS: frozenset[str] = frozenset({
    "list_vlans", "list_vrfs", "list_nat", "list_firewalls", "list_firewall_rules",
    "list_firewall_aliases", "list_dns_servers", "list_dns_zones", "list_dns_records",
    "check_dns_consistency", "dns_lookup",
    "list_vms", "list_wireless_links", "list_vpn_tunnels",
    "list_arp", "list_fdb", "list_circuits", "list_domains", "list_providers", "list_asns",
    "list_tenants", "list_contacts", "list_ssids", "list_cables", "cable_trace",
    "list_power",
    # get_topology 不在這裡（2026-10-09）：與 REST /topology 相同，只被授權部分物件的帳號也看得到
    # 自己範圍內的那一塊（build_topology 的 scope_limited）
    "list_attack_surface",
    "list_dhcp_ranges", "list_fortigate_policies", "list_fortigate_addresses",
    "list_paloalto_policies", "list_paloalto_addresses",
    "list_checkpoint_rules", "list_checkpoint_objects",
    "list_mikrotik_rules", "list_mikrotik_address_lists",
    # NAT 與防火牆規則是全域基礎設施資料 —— 與 list_nat / list_firewall_rules 同一層，
    # 不能因為它是「以 IP 為單位查」就鬆一級（改端點權限時要同步收 MCP，這裡踩過）。
    "check_ip_exposure",
})


# 僅管理員可呼叫的唯讀工具。
# 對應 CLAUDE.md 的「純管理資料」分類：AI 巡檢結論與異常偵測清單本身就是一份跨部門的
# 弱點盤點（哪些網段沒監測、哪裡有未授權裝置），REST 端已是 require_admin。
# MCP 是同一份資料的另一道門，鎖不一樣就等於沒鎖 —— 這個專案在 get_topology 踩過一次。
ADMIN_TOOLS: frozenset[str] = frozenset({
    "list_ai_findings", "list_anomalies",
    # 這幾個讀的資料在 REST 上都只給 admin（掃描代理、憑證與派送、Wazuh 代理與缺口、OCS 電腦）；
    # 以前放在全域讀取，網頁打不開的資料在 AI 對話裡問得到（2026-09-30 盤點 API 手冊時發現）。
    # tests/test_mcp_tools_match_rest_permissions.py 守著：工具不可以比對應的 REST 端點寬
    "list_scan_agents", "list_certificates", "list_cert_distribution",
    "list_wazuh_agents", "wazuh_missing_agents", "list_ocs_computers",
    # RustDesk 整合頁（REST /rustdesk）只給 admin
    "list_rustdesk_peers", "list_rustdesk_audit",
})


async def has_no_visibility(session: AsyncSession, user: User) -> bool:
    """非管理員且對所有物件類型都無可見範圍 → AI 對話不該回任何 IPAM 資料。"""
    if getattr(user, "is_admin", False):
        return False
    for ot in ("subnet", "device", "customer", "section", "rack", "location"):
        v = await visible_ids(session, user=user, object_type=ot)
        if v is None or v:   # None=全部可見、或非空集合 → 有可見範圍
            return False
    return True


async def has_global_read(session: AsyncSession, user: User) -> bool:
    """admin 或任一物件類型有「萬用」授權（visible_ids 回 None）→ 可讀全域基礎設施。"""
    if getattr(user, "is_admin", False):
        return True
    for ot in ("subnet", "device", "customer", "section", "rack", "location"):
        if await visible_ids(session, user=user, object_type=ot) is None:
            return True
    return False


async def authorize_tool(session: AsyncSession, user: User, name: str) -> str | None:
    """單一 RBAC 閘（HTTP MCP 與 NL chat 共用）。回 None=放行；回字串=拒絕原因。

    - 純計算工具：永遠放行
    - 異動工具：需 admin
    - 管理資料唯讀工具（巡檢／異常）：需 admin
    - 全域基礎設施工具：需 admin 或萬用讀取
    - 其餘（逐物件資料）：需至少有可見範圍；工具內部再依 visible_ids 過濾
    """
    if name in UTILITY_TOOLS:
        return None
    if name.startswith("impact_"):
        # IP 變更評估關閉時，工具清單裡也不出現（少佔小模型的提示詞）
        from app.services.change_impact.config import get_config
        if not (await get_config(session))["enabled"]:
            return "feature_disabled: IP 變更評估尚未啟用。"
    if name in MUTATING_TOOLS and not getattr(user, "is_admin", False):
        return "permission_denied: 此操作需要管理員權限。"
    if name in ADMIN_TOOLS and not getattr(user, "is_admin", False):
        return "permission_denied: 此為管理資料，僅限管理員檢視。"
    if await has_no_visibility(session, user):
        return "permission_denied: 你目前沒有可檢視的資源權限，請聯絡管理員指派。"
    if name in GLOBAL_READ_TOOLS and not await has_global_read(session, user):
        return "permission_denied: 此為全域基礎設施資料，僅限管理員或具全域讀取權限者檢視。"
    return None


# ── 共用呼叫入口（AI 對話、MCP、AI 對話的「確認」都走這裡）──────────────────────────────
# 2026-10-13 事故：AI 對話問某網段的 DHCP 範圍，list_dhcp_ranges 的 SQL 型別錯誤 → 交易中止 →
# 同一輪後面的工具全部 InFailedSQLTransactionError → 連存對話紀錄都失敗，畫面什麼都沒出來。
# 模型亂填的參數（IP 欄位給 "abc"）也會在資料庫轉型時出同樣的錯。所以：
#   1. 依參數名稱先檢查格式、轉成標準寫法，不合法就是看得懂的 IPAMToolError（模型可以改了再試）
#   2. 每個工具在自己的 SAVEPOINT 裡跑：出錯只還原這個工具，交易還能用
#   3. 不認得的參數名稱要講出來 —— 默默丟掉會讓「subnet」這種寫錯的範圍變成全站資料
# tests/test_ai_tools_every_param.py 用每個工具的每個參數實際跑過一次，另有守門確保沒有地方繞過這裡。

_IP_ARGS = frozenset({"ip", "target_ip", "new_ip", "requested_ip", "gateway"})
_NETWORK_ARGS = frozenset({"subnet_cidr", "cidr"})
# 這幾個工具的 MAC 本來就收不完整或各種寫法（查廠商只給前幾碼、格式換算），由工具自己判斷
_LOOSE_MAC_TOOLS = frozenset({"oui_lookup", "calc_mac_format", "calc_eui64"})


def _bad_arg(name: str, value: Any, expected: str) -> IPAMToolError:
    return IPAMToolError(f"invalid {name}: {str(value)[:80]!r} (expected {expected})")


def validate_tool_args(name: str, args: Any) -> dict[str, Any]:
    """依工具的參數定義檢查並整理模型給的參數；回傳可以直接傳給工具的 dict。"""
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise IPAMToolError("arguments must be a JSON object")
    props: dict[str, Any] = TOOLS[name]["parameters"].get("properties", {})
    unknown = sorted(set(args) - set(props))
    if unknown:
        raise IPAMToolError(f"unknown argument(s) for {name}: {', '.join(unknown)}; "
                            f"allowed: {', '.join(props) or '(none)'}")
    from app.services.mac_history import normalize_mac
    out: dict[str, Any] = {}
    for key, value in args.items():
        schema = props[key]
        kind = schema.get("type")
        if value is None:
            out[key] = None
            continue
        if kind == "integer":
            if isinstance(value, bool):
                raise _bad_arg(key, value, "an integer")
            if isinstance(value, float) and value.is_integer():
                value = int(value)
            elif isinstance(value, str) and value.strip().lstrip("-").isdigit():
                value = int(value.strip())
            if not isinstance(value, int):
                raise _bad_arg(key, value, "an integer")
        elif kind == "number":
            try:
                value = float(value) if not isinstance(value, bool) else float("nan")
            except (TypeError, ValueError):
                value = float("nan")
            if value != value:  # NaN
                raise _bad_arg(key, args[key], "a number")
        elif kind == "boolean":
            if isinstance(value, str) and value.strip().lower() in ("true", "false", "1", "0", "yes", "no"):
                value = value.strip().lower() in ("true", "1", "yes")
            if not isinstance(value, bool):
                raise _bad_arg(key, value, "true or false")
        elif kind == "string":
            if not isinstance(value, str):
                value = str(value)
            value = value.strip()
            # 列舉值不在這裡擋：工具自己回結構化的錯誤代碼（例如 impact_invalid_scenario），前端與模型靠它判斷
            if value and key in _IP_ARGS:
                try:
                    value = str(ipaddress.ip_interface(value).ip)
                except ValueError:
                    raise _bad_arg(key, value, "an IPv4 or IPv6 address") from None
            elif value and key in _NETWORK_ARGS:
                try:
                    ipaddress.ip_network(value, strict=False)
                except ValueError:
                    raise _bad_arg(key, value, "a CIDR such as 192.0.2.0/24") from None
            elif value and key == "mac" and name not in _LOOSE_MAC_TOOLS:
                mac = normalize_mac(value)
                if mac is None:
                    raise _bad_arg(key, value, "a MAC address such as aa:bb:cc:dd:ee:ff")
                value = mac
            elif value and key.endswith("_id"):
                value = str(_as_uuid(value, key))
        out[key] = value
    return out


async def call_tool(session: AsyncSession, user: User, name: str, args: Any) -> Any:
    """檢查參數 → 在 SAVEPOINT 裡執行工具。出錯只還原這個工具的變動、同一個交易後面照常可用。

    RBAC 不在這裡（呼叫端各自先過 authorize_tool，拒絕的訊息各有各的呈現方式）。
    `UiError`（服務層給人看的錯誤）轉成 IPAMToolError，三個呼叫端都把它當成「工具回報的錯誤」。
    """
    import contextlib

    from sqlalchemy.exc import InvalidRequestError

    from app.core.ui_error import UiError
    if name not in TOOLS:
        raise IPAMToolError(f"unknown tool: {name}")
    clean = validate_tool_args(name, args)
    savepoint = await session.begin_nested()
    try:
        result = await TOOLS[name]["fn"](session, user=user, **clean)
    except Exception as exc:
        # flush 失敗後 SAVEPOINT 是「待還原」狀態（is_active 已經是 False）也要還原；
        # 工具自己 commit 過的話 SAVEPOINT 已經關閉，還原會丟 InvalidRequestError
        with contextlib.suppress(InvalidRequestError):
            await savepoint.rollback()
        if isinstance(exc, UiError):
            raise IPAMToolError(str(exc)) from exc
        raise
    if savepoint.is_active:
        await savepoint.commit()
    return result


async def allowed_tool_names(session: AsyncSession, user: User) -> set[str]:
    """此使用者實際可呼叫的工具名稱集合（給 LLM 工具清單與 MCP tools/list 過濾用）。"""
    allowed: set[str] = set()
    for name in TOOLS:
        if await authorize_tool(session, user, name) is None:
            allowed.add(name)
    return allowed


def summarize_action(name: str, args: dict[str, Any]) -> str:
    """給前端確認卡用的人類可讀摘要（繁中）。"""
    a = args or {}
    if name == "allocate_ip":
        base = f"配發 IP {a.get('requested_ip') or '（自動取第一個空位）'}"
        if a.get("hostname"):
            base += f"，主機名稱「{a['hostname']}」"
        if a.get("owner"):
            base += f"，擁有者「{a['owner']}」"
        return base
    if name == "update_ip":
        # 確認卡要看得出會改哪些欄位（不然使用者等於盲按確認）
        fields = [k for k in a if k not in ("ip", "ip_id", "subnet_cidr")]
        base = f"修改 IP {a.get('ip') or a.get('ip_address_id') or ''} 的資料"
        return base + (f"（{', '.join(fields)}）" if fields else "")
    if name == "create_subnet":
        return f"建立子網路 {a.get('cidr') or ''}"
    if name == "create_device":
        return f"建立裝置「{a.get('name') or ''}」"
    if name == "approve_ip_request":
        return "核准一筆 IP 申請" + (f"（配發 {a['ip']}）" if a.get("ip") else "")
    if name == "reject_ip_request":
        return "駁回一筆 IP 申請"
    if name == "impact_create_plan":
        return f"建立 IP 變更評估計畫「{a.get('title') or ''}」（只建立計畫，不改任何設備）"
    if name == "impact_start_run":
        return "開始一次 IP 變更評估的分析（唯讀）"
    if name == "impact_accept_task_draft":
        return f"把 {len(a.get('indices') or [])} 個 AI 草擬的待辦存進計畫"
    return f"執行 {name}"
