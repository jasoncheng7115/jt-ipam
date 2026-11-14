"""09 IP 物件關係：以一筆 IP 記錄為焦點，畫出它的實體歸屬、虛擬化對應與位址歸屬（規格 SPEC.md 第 6 節）。

畫面分三列，每列只畫真的存在的關係（個人電腦、印表機沒有 VM、機櫃就不畫）：
- 實體歸屬：地點 → 機櫃 → 裝置 → IP。裝置是這筆 IP 連到的裝置；IP 沒有連到裝置、但它是某台 VM 的位址時，
  改放那台 VM 所在的實體主機（它的機櫃與地點才是這個位址的實體位置）
- 虛擬化對應：實體主機 → VM／CT → 網卡 → IP（虛擬化來源回報）
- 位址歸屬：區段 → 子網路 → IP（區段沒有 CIDR，只顯示名稱）

規則：
- 同一個物件只有一個節點（key＝類型:記錄 ID）；焦點 IP 是一個節點，多條有名稱的邊接到它
- 每條邊帶關係種類、來源與時間（來源最後同步，或記錄最後更新）；直接來源是實線，推導（名稱比對、
  只靠位址字串對到這筆記錄）是虛線
- 來源互相矛盾（機櫃在 A 地點、裝置登記在 B 地點；兩台裝置都說這是自己的 IP；兩台 VM 都回報這個位址；
  位址對到但 MAC 對不上）時兩邊都畫、邊標「待確認」，不挑一條
- 重疊的私有網段：焦點與計數一律用 IP 記錄 ID；只靠位址字串對到的 VM 網卡，還要通過單位檢查
  （子網路與叢集都有單位且不同 → 不連），並畫成推導

焦點輪播：範圍內「關係種類最多」的 IP 優先（裝置、機櫃、地點、VM 主要 IP、VM 網卡位址、VM 所在主機各算一種），同分依位址、
再依記錄 ID 排序，所以每次重新整理都是同一個順序；每個子頁一個焦點，最多 MAX_FOCUS 個。沒有任何裝置或 VM
關係的 IP 不當焦點（只有子網路的圖沒有資訊量）。

右欄「關聯待補」只算適用的物件：
- ip_unlinked：IP 沒連到裝置，但已知某台裝置在用它（裝置的主要 IP 就是這筆記錄；或 MAC 只對到一台裝置的
  連接埠；或主機名稱只對到一台裝置的名稱／FQDN），與 IP 頁「建議關聯裝置」同一套依據
- vm_ip_unmapped：VM 網卡回報了位址，但 IPAM 沒有對應的 IP 記錄
- vm_no_report：執行中、網卡沒有回報任何位址的 VM（多半是沒有客體代理）；不代表 VM 沒有 IP
- device_no_place：通常裝在機櫃的類型（伺服器、交換器、儲存、防火牆、路由器）既沒有機櫃也沒有地點；
  其實是 VM 的裝置（名稱等於某台 VM，或它的 IP 是 VM 的位址）不算；個人電腦、印表機等類型不算
- conflicts：來源不一致的關係（裝置與機櫃的地點不同；裝置的主要 IP 連到別台裝置）
虛擬化資料是全域基礎設施資料（與 06 資產同一套）：沒有全域讀取的帳號，VM 相關的數字回 None（不是 0）。
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Integer, Text, and_, case, cast, exists, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.sqlin import in_values
from app.models.address import IPAddress
from app.models.device import Device
from app.models.location import Location, Rack
from app.models.section import Section
from app.models.subnet import Subnet
from app.models.virt import VirtCluster, VirtualMachine, VMInterface
from app.services.warroom.common import scope_subnet_ids, section, subnet_visibility

MAX_FOCUS = 50
MAX_VMS = 2
RACKED_TYPES = ("server", "switch", "storage", "firewall", "router")
#: IP 記錄上「某個來源最近一次看到它」的欄位（焦點來源）
SEEN_FIELDS = ("scanner", "arp", "librenms", "wazuh", "zabbix", "checkmk", "ocs", "dns")
ROWS = ("physical", "virtual", "address")


def _iso(t: datetime | None) -> str | None:
    return t.isoformat() if t else None


def _host(ip: Any) -> str:
    return str(ip).split("/")[0]


# ─────────────────────────── 範圍 ───────────────────────────

async def _ip_subnets(session: AsyncSession, visible: set[Any] | None) -> Any:
    """看得到、範圍內、沒封存的子網路（子查詢）。"""
    stmt = select(Subnet.id).where(Subnet.archived_at.is_(None))
    if visible is not None:
        stmt = stmt.where(in_values(Subnet.id, list(visible)))
    return stmt


async def _virt_scope(session: AsyncSession, cfg: dict[str, Any]) -> set[Any] | None:
    """顯示範圍內的叢集（與 06 資產同一個判斷）。"""
    from app.services.warroom.assets import _scope_clusters
    return await _scope_clusters(session, cfg)


def _within(col: Any, ip_sub: Any) -> Any:
    """col 落在範圍子網路裡（ip_sub 是子查詢，不是 Python 清單）。"""
    return col.in_(ip_sub)  # bounded: ip_sub is a select() subquery, not a Python list


def _vm_filter(clusters: set[Any] | None) -> list[Any]:
    conds: list[Any] = [VirtualMachine.is_template.is_(False)]
    if clusters is not None:
        conds.append(in_values(VirtualMachine.cluster_id, list(clusters)))
    return conds


async def _vm_ip_hosts(session: AsyncSession) -> list[str]:
    """VM 網卡回報的位址（字串）。數量是 VM 網卡數，跟 IP 記錄數無關。"""
    rows = (await session.execute(
        select(func.host(VMInterface.primary_ip)).join(VirtualMachine, VirtualMachine.id == VMInterface.vm_id)
        .where(VMInterface.primary_ip.is_not(None), VirtualMachine.is_template.is_(False)).distinct())).all()
    return [r[0] for r in rows]


# ─────────────────────────── 計數 ───────────────────────────

async def _counts(session: AsyncSession, *, user: Any, cfg: dict[str, Any], ip_sub: Any, scoped: bool,
                  visible: set[Any] | None, global_read: bool, clusters: set[Any] | None) -> dict[str, Any]:
    from app.services.permission import visible_ids

    ips = int(await session.scalar(select(func.count()).select_from(IPAddress)
                                   .where(_within(IPAddress.subnet_id, ip_sub))) or 0)
    dev_vis = await visible_ids(session, user=user, object_type="device")
    rack_vis = await visible_ids(session, user=user, object_type="rack")
    loc_vis = await visible_ids(session, user=user, object_type="location")

    def limit(col: Any, vis: set[Any] | None) -> list[Any]:
        return [] if vis is None else [in_values(col, list(vis))]

    if scoped:
        scoped_ips = select(IPAddress.id).where(_within(IPAddress.subnet_id, ip_sub))
        dev_ids = select(IPAddress.device_id).where(_within(IPAddress.subnet_id, ip_sub), IPAddress.device_id.is_not(None))
        dev_cond = [or_(Device.id.in_(dev_ids), Device.primary_ip_id.in_(scoped_ips)), *limit(Device.id, dev_vis)]
        devices = int(await session.scalar(select(func.count()).select_from(Device).where(*dev_cond)) or 0)
        racks = int(await session.scalar(select(func.count(func.distinct(Device.rack_id))).where(
            *dev_cond, Device.rack_id.is_not(None), *limit(Device.rack_id, rack_vis))) or 0)
        loc_ids = {r[0] for r in (await session.execute(
            select(func.coalesce(Device.location_id, Rack.location_id))
            .join(Rack, Rack.id == Device.rack_id, isouter=True).where(*dev_cond))).all() if r[0]}
        loc_ids |= {r[0] for r in (await session.execute(
            select(Subnet.location_id).where(_within(Subnet.id, ip_sub), Subnet.location_id.is_not(None)))).all()}
        if loc_vis is not None:
            loc_ids &= set(loc_vis)
        locations = len(loc_ids)
    else:
        dev_cond = limit(Device.id, dev_vis)
        devices = int(await session.scalar(select(func.count()).select_from(Device).where(*dev_cond)) or 0)
        racks = int(await session.scalar(select(func.count()).select_from(Rack).where(*limit(Rack.id, rack_vis))) or 0)
        locations = int(await session.scalar(
            select(func.count()).select_from(Location).where(*limit(Location.id, loc_vis))) or 0)
    subnets = int(await session.scalar(select(func.count()).select_from(Subnet).where(_within(Subnet.id, ip_sub))) or 0)
    if not scoped and visible is None:
        sections = int(await session.scalar(select(func.count()).select_from(Section)) or 0)
    else:
        sections = int(await session.scalar(select(func.count(func.distinct(Subnet.section_id)))
                                            .where(_within(Subnet.id, ip_sub))) or 0)
    virt: int | None = None
    if global_read:
        virt = int(await session.scalar(select(func.count()).select_from(VirtualMachine)
                                        .where(*_vm_filter(clusters))) or 0)
    return {"ips": ips, "devices": devices, "virt": virt, "locations": locations, "racks": racks,
            "subnets": subnets, "sections": sections, "_dev_cond": dev_cond}


async def _pending(session: AsyncSession, *, ip_sub: Any, dev_cond: list[Any], global_read: bool,
                   clusters: set[Any] | None) -> dict[str, int | None]:
    from app.models.physical import DevicePort

    unlinked = IPAddress.device_id.is_(None)
    in_scope = _within(IPAddress.subnet_id, ip_sub)
    # (1) 已知裝置在用、但 IP 沒連到裝置：主要 IP／唯一的連接埠 MAC／唯一的名稱（與 IP 頁「建議關聯裝置」同一套）
    by_primary = select(Device.primary_ip_id).where(Device.primary_ip_id.is_not(None))
    port_mac = (select(func.lower(DevicePort.mac_address).label("m")).where(DevicePort.mac_address.is_not(None))
                .group_by(func.lower(DevicePort.mac_address)).having(func.count(func.distinct(DevicePort.device_id)) == 1))
    dev_names = (select(func.lower(Device.name).label("k")).group_by(func.lower(Device.name))
                 .having(func.count() == 1))
    dev_fqdns = (select(func.lower(Device.fqdn).label("k")).where(Device.fqdn.is_not(None))
                 .group_by(func.lower(Device.fqdn)).having(func.count() == 1))
    host = func.lower(IPAddress.hostname)
    label = func.split_part(host, ".", 1)
    ip_unlinked = int(await session.scalar(select(func.count()).select_from(IPAddress).where(
        in_scope, unlinked, or_(
            IPAddress.id.in_(by_primary),
            and_(IPAddress.mac.is_not(None), func.lower(cast(IPAddress.mac, Text)).in_(port_mac)),
            and_(IPAddress.hostname.is_not(None), or_(host.in_(dev_names), label.in_(dev_names), host.in_(dev_fqdns))),
        ))) or 0)

    # (5) 來源不一致：裝置與所在機櫃的地點不同；裝置的主要 IP 連到另一台裝置
    loc_mismatch = int(await session.scalar(
        select(func.count()).select_from(Device).join(Rack, Rack.id == Device.rack_id).where(
            *dev_cond, Device.location_id.is_not(None), Rack.location_id.is_not(None),
            Device.location_id != Rack.location_id)) or 0)
    other = aliased(IPAddress)
    primary_mismatch = int(await session.scalar(
        select(func.count()).select_from(Device).join(other, other.id == Device.primary_ip_id).where(
            *dev_cond, _within(other.subnet_id, ip_sub), other.device_id.is_not(None), other.device_id != Device.id)) or 0)

    # (4) 應上架設備沒有機櫃也沒有地點（其實是 VM 的裝置不算）
    vm_names = select(func.lower(VirtualMachine.name))
    vm_ips = select(IPAddress.device_id).join(VirtualMachine, VirtualMachine.primary_ip_id == IPAddress.id).where(
        IPAddress.device_id.is_not(None))
    device_no_place = int(await session.scalar(select(func.count()).select_from(Device).where(
        *dev_cond, Device.type.in_(RACKED_TYPES), Device.rack_id.is_(None), Device.location_id.is_(None),
        func.lower(Device.name).not_in(vm_names), Device.id.not_in(vm_ips))) or 0)

    out: dict[str, int | None] = {"ip_unlinked": ip_unlinked, "vm_ip_unmapped": None, "vm_no_report": None,
                                  "device_no_place": device_no_place, "conflicts": loc_mismatch + primary_mismatch}
    if global_read:
        nic_ip = exists().where(VMInterface.vm_id == VirtualMachine.id, VMInterface.primary_ip.is_not(None))
        known = select(func.host(IPAddress.ip)).where(
            IPAddress.subnet_id.in_(select(Subnet.id).where(Subnet.archived_at.is_(None))))
        nic_known = exists().where(VMInterface.vm_id == VirtualMachine.id,
                                   func.host(VMInterface.primary_ip).in_(known))
        out["vm_ip_unmapped"] = int(await session.scalar(select(func.count()).select_from(VirtualMachine).where(
            *_vm_filter(clusters), VirtualMachine.primary_ip_id.is_(None), nic_ip, ~nic_known)) or 0)
        out["vm_no_report"] = int(await session.scalar(select(func.count()).select_from(VirtualMachine).where(
            *_vm_filter(clusters), VirtualMachine.status == "running", VirtualMachine.primary_ip_id.is_(None),
            ~nic_ip)) or 0)
    return out


# ─────────────────────────── 焦點 ───────────────────────────

async def _candidates(session: AsyncSession, *, ip_sub: Any, dev_vis: set[Any] | None,
                      global_read: bool) -> list[Any]:
    """關係種類最多的 IP 記錄 ID（同分依位址、再依 ID），最多 MAX_FOCUS 筆。"""
    dev = aliased(Device)
    rk = aliased(Rack)
    dev_ok = dev.id.is_not(None) if dev_vis is None else in_values(dev.id, list(dev_vis))
    has_dev = case((dev_ok, 1), else_=0)
    has_rack = case((and_(dev_ok, dev.rack_id.is_not(None)), 1), else_=0)
    has_loc = case((and_(dev_ok, or_(dev.location_id.is_not(None), rk.location_id.is_not(None))), 1), else_=0)
    score: Any = has_dev + has_rack + has_loc
    if global_read:
        vm_primary = exists().where(VirtualMachine.primary_ip_id == IPAddress.id, VirtualMachine.is_template.is_(False))
        nic_hosts = await _vm_ip_hosts(session)
        vm_nic = in_values(func.host(IPAddress.ip), nic_hosts, type_=Text()) if nic_hosts else literal(False)
        score = score + case((vm_primary, 1), else_=0) + case((vm_nic, 1), else_=0)
        hosted = exists().where(VirtualMachine.primary_ip_id == IPAddress.id, VirtualMachine.is_template.is_(False),
                                VirtualMachine.node.is_not(None))
        score = score + case((hosted, 1), else_=0)
    score = cast(score, Integer).label("score")
    rows = (await session.execute(
        select(IPAddress.id, score)
        .join(dev, dev.id == IPAddress.device_id, isouter=True)
        .join(rk, rk.id == dev.rack_id, isouter=True)
        .where(_within(IPAddress.subnet_id, ip_sub))
        .order_by(score.desc(), func.family(IPAddress.ip), IPAddress.ip, IPAddress.id)
        .limit(MAX_FOCUS))).all()
    return [r[0] for r in rows if r[1] > 0]


async def _cluster_sync(session: AsyncSession, cluster_id: Any) -> datetime | None:
    """叢集的虛擬化整合最後成功同步時間（同一個叢集可能有好幾個連線，取最新）。"""
    from app.services.integration_registry import last_success, models

    best: datetime | None = None
    for kind, _label, model in models():
        if kind not in ("proxmox", "esxi", "hyperv", "openstack") or not hasattr(model, "cluster_id"):
            continue
        for row in (await session.execute(select(model).where(model.cluster_id == cluster_id))).scalars().all():
            t = last_success(row)
            if t and (best is None or t > best):
                best = t
    return best


class _Graph:
    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: list[dict[str, Any]] = []

    def node(self, key: str, **data: Any) -> str:
        if key not in self.nodes:                    # 同一個物件只放一次（第一次放的位置為準）
            self.nodes[key] = {"key": key, **data}
        return key

    def edge(self, a: str, b: str, kind: str, source: str, at: datetime | None, *, derived: bool = False,
             pending: str | None = None) -> dict[str, Any]:
        e = {"from": a, "to": b, "kind": kind, "source": source, "at": _iso(at),
             "line": "dashed" if derived or pending else "solid",
             "state": "pending" if pending else "ok", "reason": pending}
        self.edges.append(e)
        return e


def _place_text(rack: Any, loc: Any) -> str | None:
    parts = [x.name for x in (rack, loc) if x is not None]
    return " · ".join(parts) or None


async def _focus(session: AsyncSession, *, user: Any, ip_id: Any, global_read: bool,
                 virt_configured: bool) -> dict[str, Any]:
    ip = await session.get(IPAddress, ip_id)
    assert ip is not None
    subnet = await session.get(Subnet, ip.subnet_id)
    g = _Graph()
    ipk = g.node(f"ip:{ip.id}", type="ip", id=str(ip.id), label=_host(ip.ip), sub=ip.hostname,
                 mac=str(ip.mac) if ip.mac else None, row=None, col=3, slot=0)
    rows: dict[str, dict[str, Any]] = {r: {"kind": r, "state": "none", "more": 0} for r in ROWS}
    reasons: list[str] = []

    # ── 位址歸屬：區段 → 子網路 → IP
    if subnet is not None:
        rows["address"]["state"] = "ok"
        sk = g.node(f"subnet:{subnet.id}", type="subnet", id=str(subnet.id), label=str(subnet.cidr),
                    sub=subnet.description, row="address", col=2, slot=0)
        g.edge(sk, ipk, "subnet_ip", "ipam", ip.updated_at)
        sec = await session.get(Section, subnet.section_id) if subnet.section_id else None
        if sec is not None:
            g.node(f"section:{sec.id}", type="section", id=str(sec.id), label=sec.name, sub=None,
                   row="address", col=1, slot=0)
            g.edge(f"section:{sec.id}", sk, "section_subnet", "ipam", subnet.updated_at)

    # ── 虛擬化：先找出對到這個位址的 VM（實體列要知道有沒有 VM 主機）
    vms: list[dict[str, Any]] = []
    if global_read:
        vms = await _vms_for(session, ip, subnet)

    # ── 實體歸屬：IP 連到的裝置，以及說「這是我的主要 IP」的其他裝置
    linked = await session.get(Device, ip.device_id) if ip.device_id else None
    claimants = list((await session.execute(
        select(Device).where(Device.primary_ip_id == ip.id, *([Device.id != linked.id] if linked else []))
        .order_by(Device.name, Device.id).limit(3))).scalars().all())
    devices: list[tuple[Any, str]] = []                       # (裝置, 關係種類)
    if linked is not None:
        devices.append((linked, "device_ip"))
    devices.extend((d, "device_primary_ip") for d in claimants)
    dev_conflict = len(devices) > 1
    if dev_conflict:
        reasons.append("multiple_devices")

    host_dev = None                                           # IP 沒有裝置時，改放 VM 所在的實體主機
    if not devices and len(vms) == 1 and vms[0]["host"] is not None:
        host_dev = vms[0]["host"]
    main = devices[0][0] if devices else host_dev
    if main is not None:
        rack = await session.get(Rack, main.rack_id) if main.rack_id else None
        rack_loc = await session.get(Location, rack.location_id) if rack is not None and rack.location_id else None
        dev_loc = await session.get(Location, main.location_id) if main.location_id else None
        mk = g.node(f"device:{main.id}", type="device", id=str(main.id), label=main.name, sub=main.type,
                    dtype=main.type, row="physical", col=2, slot=0)
        if rack is not None:
            rkk = g.node(f"rack:{rack.id}", type="rack", id=str(rack.id), label=rack.name,
                         sub=None, row="physical", col=1, slot=0)
            g.edge(rkk, mk, "rack_device", "ipam", main.updated_at)
            if rack_loc is not None:
                g.node(f"location:{rack_loc.id}", type="location", id=str(rack_loc.id), label=rack_loc.name,
                       sub=None, row="physical", col=0, slot=0)
                g.edge(f"location:{rack_loc.id}", rkk, "location_rack", "ipam", rack.updated_at)
        if dev_loc is not None and (rack_loc is None or dev_loc.id != rack_loc.id):
            mismatch = rack_loc is not None
            g.node(f"location:{dev_loc.id}", type="location", id=str(dev_loc.id), label=dev_loc.name, sub=None,
                   row="physical", col=0, slot=1 if mismatch else 0)
            g.edge(f"location:{dev_loc.id}", mk, "location_device", "ipam", main.updated_at,
                   pending="location_mismatch" if mismatch else None)
            if mismatch:
                reasons.append("location_mismatch")
        for i, (d, kind) in enumerate(devices[:2]):
            dk = g.node(f"device:{d.id}", type="device", id=str(d.id), label=d.name, sub=d.type, dtype=d.type,
                        row="physical", col=2, slot=i)
            g.edge(dk, ipk, kind, "ipam", ip.updated_at if kind == "device_ip" else d.updated_at,
                   pending="multiple_devices" if dev_conflict else None)
        rows["physical"]["more"] = max(len(devices) - 2, 0)
        rows["physical"]["state"] = "ok"

    # ── 虛擬化對應：實體主機 → VM／CT → 網卡 → IP
    if not global_read:
        rows["virtual"]["state"] = "forbidden"
    elif vms:
        rows["virtual"]["state"] = "ok"
        rows["virtual"]["more"] = max(len(vms) - MAX_VMS, 0)
        multi = len(vms) > 1
        if multi:
            reasons.append("multiple_vms")
        for i, m in enumerate(vms[:MAX_VMS]):
            vm, cl, at = m["vm"], m["cluster"], m["at"]
            plat = (cl.type if cl is not None else None) or "other"
            pending = "multiple_vms" if multi else ("mac_mismatch" if m["mac_mismatch"] else None)
            if m["mac_mismatch"] and "mac_mismatch" not in reasons:
                reasons.append("mac_mismatch")
            nic = m["nic"]
            vm_col = 1 if nic is not None else 2
            vk = g.node(f"vm:{vm.id}", type="vm", id=str(vm.id), label=vm.name, sub=(vm.kind or "vm"),
                        platform=plat, row="virtual", col=vm_col, slot=i)
            if nic is not None:
                nk = g.node(f"nic:{nic.id}", type="nic", id=str(nic.id), label=nic.name,
                            sub=str(nic.mac) if nic.mac else None, row="virtual", col=2, slot=i)
                g.edge(vk, nk, "vm_nic", plat, at)
                # 網卡回報的是位址字串；對到「這一筆」記錄要有記錄層級的證據（VM 主要 IP 就是它，或 MAC 相同）
                confirmed = m["primary"] or (ip.mac is not None and nic.mac is not None
                                             and str(ip.mac).lower() == str(nic.mac).lower())
                g.edge(nk, ipk, "nic_ip", plat, at, derived=not confirmed, pending=pending)
            elif m["primary"]:
                g.edge(vk, ipk, "vm_ip", plat, at, pending=pending)
            else:
                g.edge(vk, ipk, "vm_name", "name", at, derived=True, pending=pending)
            host, how = m["host"], m["host_how"]
            if host is not None:
                hk = f"device:{host.id}"
                if hk not in g.nodes:
                    rack = await session.get(Rack, host.rack_id) if host.rack_id else None
                    loc_id = host.location_id or (rack.location_id if rack is not None else None)
                    loc = await session.get(Location, loc_id) if loc_id else None
                    g.node(hk, type="device", id=str(host.id), label=host.name, sub=_place_text(rack, loc),
                           dtype=host.type, host=True, row="virtual", col=vm_col - 1, slot=i)
                g.edge(hk, vk, "host_vm", plat, at, derived=how != "linked")
            elif vm.node:
                hk = g.node(f"host:{vm.cluster_id}:{vm.node}", type="host", id=None, label=vm.node,
                            sub=cl.name if cl is not None else None, platform=plat, row="virtual",
                            col=vm_col - 1, slot=i)
                g.edge(hk, vk, "host_vm", plat, at)
    elif not virt_configured:
        rows["virtual"]["state"] = "not_configured"

    # ── 權限：看不到的物件整個節點拿掉（與 IP 頁、裝置頁的關係圖同一個 visible_chain）
    from app.services.permission import visible_chain
    keep = {n["key"] for n in await visible_chain(session, user, [
        {"type": n["type"] if n["type"] != "host" else "vmnode", "id": n["id"] or n["key"], "key": n["key"]}
        for n in g.nodes.values()])}
    keep.add(ipk)
    nodes = [n for n in g.nodes.values() if n["key"] in keep]
    edges = [e for e in g.edges if e["from"] in keep and e["to"] in keep]
    for r in ("physical", "virtual"):
        if rows[r]["state"] == "ok" and not any(n["row"] == r for n in nodes):
            rows[r]["state"] = "hidden"

    seen = {f: getattr(ip, f"last_seen_{f}") for f in SEEN_FIELDS}
    sources = sorted(({"source": f, "at": _iso(t)} for f, t in seen.items() if t), key=lambda s: s["at"],
                     reverse=True)
    for m in vms[:MAX_VMS]:
        if m["at"] and global_read:
            plat = (m["cluster"].type if m["cluster"] is not None else None) or "other"
            if not any(s["source"] == plat for s in sources):
                sources.append({"source": plat, "at": _iso(m["at"])})
    sources.sort(key=lambda s: s["at"] or "", reverse=True)
    last = max((t for t in seen.values() if t), default=None)
    return {
        "ip": {"key": ipk, "id": str(ip.id), "ip": _host(ip.ip), "hostname": ip.hostname,
               "mac": str(ip.mac) if ip.mac else None, "state": ip.state, "status": ip.effective_status,
               "subnet": str(subnet.cidr) if subnet is not None else None},
        "rows": [rows[r] for r in ROWS],
        "nodes": nodes, "edges": edges,
        "conflicts": reasons,
        "registered": {"source": ip.discovery_source, "at": _iso(ip.created_at)},
        "sources": sources[:5], "last_confirmed": _iso(last),
    }


async def _vms_for(session: AsyncSession, ip: Any, subnet: Any) -> list[dict[str, Any]]:
    """對到這筆 IP 的 VM：主要 IP 是這筆記錄、網卡回報這個位址（同一單位）、都沒有時才看名稱（只對到一台才算）。"""
    found: dict[Any, dict[str, Any]] = {}
    for vm in (await session.execute(select(VirtualMachine).where(
            VirtualMachine.primary_ip_id == ip.id, VirtualMachine.is_template.is_(False))
            .order_by(VirtualMachine.name, VirtualMachine.id))).scalars().all():
        found[vm.id] = {"vm": vm, "primary": True, "nic": None}
    for nic, vm in (await session.execute(
            select(VMInterface, VirtualMachine).join(VirtualMachine, VirtualMachine.id == VMInterface.vm_id)
            .where(func.host(VMInterface.primary_ip) == _host(ip.ip), VirtualMachine.is_template.is_(False))
            .order_by(VirtualMachine.name, VirtualMachine.id, VMInterface.name))).all():
        m = found.setdefault(vm.id, {"vm": vm, "primary": False, "nic": None})
        if m["nic"] is None:
            m["nic"] = nic
    by_name = False
    if not found:
        dev = await session.get(Device, ip.device_id) if ip.device_id else None
        names = sorted({n.lower() for n in (ip.hostname, dev.name if dev else None) if n})
        if names:
            hits = list((await session.execute(select(VirtualMachine).where(
                func.lower(VirtualMachine.name).in_(names), VirtualMachine.is_template.is_(False))  # bounded: names of one IP
                .order_by(VirtualMachine.id).limit(2))).scalars().all())
            if len(hits) == 1:                                  # 名稱對到好幾台就不猜
                found[hits[0].id] = {"vm": hits[0], "primary": False, "nic": None}
                by_name = True
    from app.services.relation_chain import vm_host_device

    out = []
    for m in found.values():
        vm = m["vm"]
        cl = await session.get(VirtCluster, vm.cluster_id) if vm.cluster_id else None
        # 只連同單位的 VM（子網路與叢集都有單位且不同 → 重疊網段的另一邊，不是這筆）
        if subnet is not None and subnet.customer_id is not None and cl is not None \
                and cl.customer_id is not None and cl.customer_id != subnet.customer_id:
            continue
        macs = {str(r[0]).lower() for r in (await session.execute(
            select(VMInterface.mac).where(VMInterface.vm_id == vm.id, VMInterface.mac.is_not(None)))).all()}
        mac_mismatch = not by_name and bool(ip.mac) and bool(macs) and str(ip.mac).lower() not in macs
        host, how = await vm_host_device(session, vm)
        out.append({**m, "cluster": cl, "mac_mismatch": mac_mismatch, "host": host, "host_how": how,
                    "at": (await _cluster_sync(session, vm.cluster_id) if vm.cluster_id else None) or vm.updated_at})
    out.sort(key=lambda m: (not m["primary"], m["vm"].name, str(m["vm"].id)))
    return out


# ─────────────────────────── 組裝 ───────────────────────────

async def build(session: AsyncSession, *, user: Any, cfg: dict[str, Any], page: int = 1,
                now: datetime | None = None) -> dict[str, Any]:
    from app.mcp.tools import has_global_read
    from app.services.permission import visible_ids

    now = now or datetime.now(UTC)
    base = {"key": "relations", "data_at": now.isoformat(), "per_page": 1}
    own = await subnet_visibility(session, user)
    if own is not None and not own:
        # 一個子網路都看不到：沒有權限，不是「0 筆」
        return {**base, "page": 1, "pages": 1, "total": 0,
                "metrics": {"ips": None, "devices": None, "virt": None},
                "counts": section("forbidden"), "pending": section("forbidden"), "focus": section("forbidden")}
    scope = await scope_subnet_ids(session, cfg)
    visible: set[Any] | None = own if scope is None else (scope if own is None else scope & set(own))
    global_read = await has_global_read(session, user)
    clusters = await _virt_scope(session, cfg) if global_read else None
    ip_sub = await _ip_subnets(session, visible)
    c = await _counts(session, user=user, cfg=cfg, ip_sub=ip_sub, scoped=scope is not None, visible=visible,
                      global_read=global_read, clusters=clusters)
    pending = await _pending(session, ip_sub=ip_sub, dev_cond=c.pop("_dev_cond"), global_read=global_read,
                             clusters=clusters)
    virt_configured = bool(await session.scalar(select(func.count()).select_from(VirtCluster)))
    # 沒有設定任何虛擬化整合：VM 數是「未設定」，不是 0
    metrics = {"ips": c["ips"], "devices": c["devices"], "virt": c["virt"] if virt_configured else None}
    metrics_state = {"virt": "forbidden" if not global_read else ("not_configured" if not virt_configured else "ok")}
    counts = section("ok", locations=c["locations"], racks=c["racks"], subnets=c["subnets"], sections=c["sections"])
    candidates = await _candidates(session, ip_sub=ip_sub,
                                   dev_vis=await visible_ids(session, user=user, object_type="device"),
                                   global_read=global_read)
    if not virt_configured:
        pending["vm_ip_unmapped"] = pending["vm_no_report"] = None
    out = {**base, "metrics": metrics, "metrics_state": metrics_state, "counts": counts,
           "pending": section("ok", **pending, vm_state=metrics_state["virt"])}
    if not candidates:
        return {**out, "page": 1, "pages": 1, "total": 0, "focus": section("empty", ips=c["ips"])}
    pages = len(candidates)
    page = min(max(1, page), pages)
    focus = await _focus(session, user=user, ip_id=candidates[page - 1], global_read=global_read,
                         virt_configured=virt_configured)
    return {**out, "page": page, "pages": pages, "total": pages, "focus": section("ok", **focus)}

