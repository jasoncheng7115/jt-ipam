"""IPAddress CRUD + first_free 配發。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import Response
from pydantic import Field
from sqlalchemy import String, Text, cast, func, or_, select
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_global_read
from app.api.v1.write_guards import (
    require_visible,
)
from app.core.audit import append_audit
from app.core.db import get_session
from app.core.sqlin import in_values
from app.core.ui_error import detail_of, ui_detail
from app.models.address import IPAddress
from app.models.ip_change_log import IPChangeLog
from app.models.subnet import Subnet
from app.models.user import User
from app.schemas.address import (
    IPAddressAllocate,
    IPAddressCreate,
    IPAddressRead,
    IPAddressUpdate,
)
from app.schemas.base import Paginated, StrictModel
from app.schemas.ip_change_log import IPChangeLogRead
from app.services import ip_lifecycle
from app.services.address import (
    IPAlreadyExists,
    IPNotInSubnet,
    SubnetFull,
    allocate_first_free,
)
from app.services.address_write import create_address_checked, update_address_checked
from app.services.csv_io import (
    export_addresses_csv,
    import_addresses_csv,
)
from app.services.custom_field import CustomFieldError
from app.services.hostname import (
    recompute_effective,
    seed_observation,
)
from app.services.ip_history import log_change
from app.services.oui import mac_prefix, vendor_for_mac, vendor_map
from app.services.permission import (
    filter_visible,
    get_object_permission,
    has_permission,
    visible_chain,
    visible_ids,
)

router = APIRouter(prefix="/addresses", tags=["addresses"])


async def _require_subnet_perm(
    session: AsyncSession,
    user: User,
    subnet_id: uuid.UUID,
    required: str,
) -> Subnet:
    subnet = await session.get(Subnet, subnet_id)
    if subnet is None:
        raise HTTPException(status_code=404, detail="Subnet not found")
    level = await get_object_permission(
        session, user=user, object_type="subnet", object_id=subnet.id
    )
    if not has_permission(level, required):
        # A01：不洩漏存在性
        raise HTTPException(status_code=404, detail="Subnet not found")
    return subnet


async def _enrich_special_flags(
    session: AsyncSession, items: list[Any], rows: list[Any]
) -> None:
    """批次推導清單視覺化用的特殊角色旗標：閘道 / 在 DHCP 範圍 / DHCP 伺服器（整合中的 DHCP 來源）。
    來源資料（整合 / DHCP pool）本身受權限管控，但這裡只回旗標與整合名稱，故所有可見此 IP 的使用者都看得到標記。
    """
    import ipaddress as _ip

    from app.models.dhcp import DHCPPoolRange
    from app.models.dhcp_sighting import DHCPSighting

    if not rows:
        return
    subnet_ids = list({r.subnet_id for r in rows})
    sub_rows = (await session.execute(
        select(Subnet.id, Subnet.gateway, Subnet.cidr).where(in_values(Subnet.id, subnet_ids))
    )).all()
    gw_map = {sid: gw for sid, gw, _c in sub_rows}
    nets: dict[Any, Any] = {}
    for sid, _gw, cidr in sub_rows:
        try:
            nets[sid] = _ip.ip_network(str(cidr), strict=False)
        except ValueError:
            continue
    # 每個子網路只看屬於自己的 DHCP 集區：重疊網段（兩個單位同一段位址）時，另一個單位的集區
    # 不可以標到這邊的 IP。手動定義的（issue #40）本來就掛在子網路上；整合同步回來的只有 CIDR，
    # 先粗篩「範圍落在清單裡某個子網路內」，再判斷屬於哪一個（判斷不了就不標，不猜）
    from app.services.dhcp_usage import pool_subnet
    from app.services.ip_ranges import manual_dhcp_pools
    ranges: dict[Any, list[tuple[int, int]]] = {}

    def _add(sid: Any, start: Any, end: Any) -> None:
        try:
            ranges.setdefault(sid, []).append((int(_ip.ip_address(str(start))), int(_ip.ip_address(str(end)))))
        except ValueError:
            pass
    for mp in await manual_dhcp_pools(session, subnet_ids):
        _add(mp.source_id, mp.start_ip, mp.end_ip)
    for pool in (await session.execute(select(DHCPPoolRange))).scalars().all():
        try:
            lo, hi = _ip.ip_address(str(pool.start_ip)), _ip.ip_address(str(pool.end_ip))
        except ValueError:
            continue
        if not any(lo.version == n.version and lo in n and hi in n for n in nets.values()):
            continue
        sid = await pool_subnet(session, pool)
        if sid in nets:
            _add(sid, lo, hi)
    # DHCP 伺服器的排除區間（0207）：發放範圍寫入前已經扣掉（上面不會標成在範圍內），這裡做正面標示。
    # 歸屬判斷跟發放範圍同一套（重疊網段判斷不了就不標）
    from app.models.dhcp import DHCPExclusion
    excl: dict[Any, list[tuple[int, int, dict[str, Any]]]] = {}
    for ex in (await session.execute(select(DHCPExclusion))).scalars().all():
        try:
            elo, ehi = _ip.ip_address(str(ex.start_ip)), _ip.ip_address(str(ex.end_ip))
        except ValueError:
            continue
        if not any(elo.version == n.version and elo in n and ehi in n for n in nets.values()):
            continue
        sid = await pool_subnet(session, ex)
        if sid in nets:
            excl.setdefault(sid, []).append((int(elo), int(ehi), {
                "source_type": ex.source_type, "source_name": ex.source_name, "engine": ex.source,
                "start": str(elo), "end": str(ehi)}))
    # 整合中的 DHCP 來源指向的主機＝「DHCP 伺服器（自動）」（清單與異常偵測共用 services/dhcp_known）。
    # 沒有觀測佐證時只標在它所屬的子網路：有設範圍就限範圍內，沒設時只在唯一包含它的子網路標；
    # 重疊網段裡有好幾個一樣的就不標（不猜），免得另一個單位同一個位址的 IP 也被當成 DHCP 伺服器
    from app.services.dhcp_known import load_known_dhcp_servers
    known = await load_known_dhcp_servers(session)
    fw_ips: dict[str, dict[Any, str]] = {}
    unique_cache: dict[str, bool] = {}
    for src in known.sources:
        if not src.ip:
            continue
        hip = _ip.ip_address(src.ip)
        cands = {sid for sid, n in nets.items() if hip.version == n.version and hip in n}
        if not cands:
            continue
        if src.scope:
            keep = {sid for sid in cands if str(sid) in src.scope}
        else:
            if src.ip not in unique_cache:
                total = await session.scalar(select(func.count()).select_from(Subnet).where(
                    Subnet.archived_at.is_(None), Subnet.cidr.op(">>=")(cast(src.ip, INET))))
                unique_cache[src.ip] = total == 1
            keep = cands if unique_cache[src.ip] else set()
        for sid in keep:
            fw_ips.setdefault(src.ip, {}).setdefault(sid, src.label)
    # 掃描代理實際觀測到「這個位址在回應 DHCP」的時間。設定與實測是兩件事：
    # 標記了不代表真的在發，沒標記也不代表沒有在發 —— 兩個都要看得到。
    # 只看異常偵測同一個時間窗內的：以前沒有界線，私接的路由器拔掉一個月了清單上還是紅的
    from datetime import UTC, datetime, timedelta

    from app.services.anomaly import ROGUE_DHCP_WINDOW_DAYS
    observed_since = datetime.now(UTC) - timedelta(days=ROGUE_DHCP_WINDOW_DAYS)
    observed: dict[tuple[Any, str], Any] = {}
    for sub_id, srv_ip, seen in (await session.execute(
        select(DHCPSighting.subnet_id, DHCPSighting.server_ip, DHCPSighting.last_seen_at)
        .where(in_values(DHCPSighting.subnet_id, subnet_ids), DHCPSighting.last_seen_at >= observed_since,
               # 經由中繼轉送來的回應不算（異常偵測同一條規則）
               DHCPSighting.via_relay.is_(False))
    )).all():
        key = (sub_id, str(srv_ip))
        if key not in observed or seen > observed[key]:
            observed[key] = seen

    # DHCP 固定分配的明細（綁哪張網卡、哪台 DHCP）。清單頁靠 ip_addresses.dhcp_reserved
    # 這個旗標就夠了，但詳細資料要講得出「綁給誰」，否則使用者只知道有、不知道是什麼。
    from app.models.dhcp import DHCPReservation
    resv: dict[Any, dict[str, Any]] = {}
    for rr in (await session.execute(
        select(DHCPReservation).where(in_values(DHCPReservation.ip_address_id, [r.id for r in rows]))
    )).scalars().all():
        resv.setdefault(rr.ip_address_id, {
            "mac": rr.mac, "hostname": rr.hostname, "description": rr.description,
            "source_name": rr.source_name, "source_type": rr.source_type,
            "engine": rr.source,
        })

    for it, r in zip(items, rows, strict=False):
        ipstr = str(r.ip)
        it.dhcp_reservation = resv.get(r.id)
        gw = gw_map.get(r.subnet_id)
        it.is_gateway = bool(gw) and ipstr == str(gw)
        seen_at = observed.get((r.subnet_id, ipstr))
        auto = fw_ips.get(ipstr, {}).get(r.subnet_id)
        if auto is None:
            # 確實觀測到它在這個子網路發 DHCP：對得到整合就是那台（跨網段、名稱對應都算，與異常偵測一致）；
            # 沒有觀測佐證時只認完整名稱相同
            m = known.match(ipstr if seen_at else None, r.hostname, exact_names_only=not seen_at)
            auto = m[0].label if m else None
        it.dhcp_server_auto = auto is not None
        it.dhcp_server_integration = auto
        it.dhcp_observed_at = seen_at.isoformat() if seen_at else None
        try:
            n = int(_ip.ip_address(ipstr))
            it.in_dhcp_range = any(a <= n <= b for a, b in ranges.get(r.subnet_id, ()))
            it.dhcp_exclusion = next((info for a, b, info in excl.get(r.subnet_id, ()) if a <= n <= b), None)
        except ValueError:
            it.in_dhcp_range = False


@router.get("", response_model=Paginated[IPAddressRead])
async def list_addresses(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    subnet_id: uuid.UUID | None = Query(None),
    section_id: uuid.UUID | None = Query(None, description="篩選某區段（含其所有子網路的 IP）"),
    customer_id: uuid.UUID | None = Query(None, description="篩選某客戶 / 管理單位"),
    device_id: uuid.UUID | None = Query(None),
    q: str | None = Query(None, max_length=128,
                          description="模糊搜尋所有文字欄位（IP / hostname / MAC / "
                                      "description / owner / switch_port / note / state）"),
    exact: bool = Query(False, description="完全符合：IP / hostname 須與 q 完全相等"),
    sort: str | None = Query(None, description="排序欄位：ip/hostname/mac/state/owner/switch_port/note/discovery_source"),
    order: str = Query("asc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(100, ge=1, le=1000),
) -> Paginated[IPAddressRead]:
    stmt = select(IPAddress)
    count_stmt = select(func.count()).select_from(IPAddress)
    if subnet_id is not None:
        # 須對該 subnet 有 read 權限
        await _require_subnet_perm(session, user, subnet_id, "read")
        stmt = stmt.where(IPAddress.subnet_id == subnet_id)
        count_stmt = count_stmt.where(IPAddress.subnet_id == subnet_id)
    if section_id is not None:
        # 區段篩選：該區段底下所有子網路的 IP（逐筆 subnet 權限在最後把關）
        sub_ids = select(Subnet.id).where(Subnet.section_id == section_id)
        stmt = stmt.where(IPAddress.subnet_id.in_(sub_ids))
        count_stmt = count_stmt.where(IPAddress.subnet_id.in_(sub_ids))
    if customer_id is not None:
        stmt = stmt.where(IPAddress.customer_id == customer_id)
        count_stmt = count_stmt.where(IPAddress.customer_id == customer_id)
    if device_id is not None:
        # 跨 subnet 取 device 的 IP 清單；底下還會逐筆檢查 subnet 權限
        stmt = stmt.where(IPAddress.device_id == device_id)
        count_stmt = count_stmt.where(IPAddress.device_id == device_id)
    if subnet_id is None:
        # 全域/跨子網路清單：排除「已歸檔子網路」的 IP（直接檢視某子網路時不套用，供檢視/還原）
        active_subnets = select(Subnet.id).where(Subnet.archived_at.is_(None))
        stmt = stmt.where(IPAddress.subnet_id.in_(active_subnets))
        count_stmt = count_stmt.where(IPAddress.subnet_id.in_(active_subnets))
        # A01：可見性要在查詢階段就套進去（含 count_stmt），不能只在分頁後篩 rows。
        # 只篩 rows 的話 total 會是「全系統未歸檔子網路的 IP 總數」→ 受限帳號看到遠大於
        # 自己可見的筆數（洩漏規模），而且每頁不足 page_size、後面幾頁可能整頁空白。
        vis_subnets = await visible_ids(session, user=user, object_type="subnet", required="read")
        if vis_subnets is not None:      # None＝全部可見（admin 或萬用授權）
            if not vis_subnets:          # 空 set＝沒有任何可見子網路
                return Paginated[IPAddressRead](
                    items=[], total=0, page=page, page_size=page_size,
                )
            stmt = stmt.where(in_values(IPAddress.subnet_id, vis_subnets))
            count_stmt = count_stmt.where(in_values(IPAddress.subnet_id, vis_subnets))

    if q:
        if exact:
            # 完全符合：IP 完全相等（host() 去掉 /prefix）或 hostname 完全相等
            search_clause = or_(
                func.host(IPAddress.ip) == q,
                IPAddress.hostname == q,
            )
        else:
            # 跳脫 LIKE 萬用字元，避免使用者輸入 % / _ 造成大範圍掃描
            escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            search_clause = or_(
                cast(IPAddress.ip, String).ilike(pattern, escape="\\"),
                IPAddress.hostname.ilike(pattern, escape="\\"),
                cast(IPAddress.mac, String).ilike(pattern, escape="\\"),
                IPAddress.description.ilike(pattern, escape="\\"),
                IPAddress.owner.ilike(pattern, escape="\\"),
                IPAddress.switch_port.ilike(pattern, escape="\\"),
                IPAddress.note.ilike(pattern, escape="\\"),
                IPAddress.state.ilike(pattern, escape="\\"),
            )
        stmt = stmt.where(search_clause)
        count_stmt = count_stmt.where(search_clause)

    # 伺服器端排序（remote 表格用）；未指定 → 預設依 IP
    from app.models.device import Device
    from app.models.oui import OUIVendor
    _seen = func.greatest(IPAddress.last_seen_scanner, IPAddress.last_seen_librenms, IPAddress.last_seen_arp)
    # 失聯天數只看掃描代理與 LibreNMS（跟子網路頁一致）：天數少＝最近看過的那個時間大
    _probe_seen = func.greatest(IPAddress.last_seen_scanner, IPAddress.last_seen_librenms)
    _SORT_COLS: dict[str, Any] = {
        "ip": IPAddress.ip, "hostname": IPAddress.hostname, "mac": IPAddress.mac,
        "state": IPAddress.state, "owner": IPAddress.owner,
        "switch_port": IPAddress.switch_port, "note": IPAddress.note,
        "discovery_source": IPAddress.discovery_source,
        "device_kind": IPAddress.device_kind,
        # 補上子網路頁有的欄位（2026-10-12）
        "description": IPAddress.description,
        "last_seen": _seen,
        "device": select(Device.name).where(Device.id == IPAddress.device_id).scalar_subquery(),
        "mac_vendor": select(func.coalesce(OUIVendor.short_name, OUIVendor.name)).where(
            OUIVendor.prefix == func.upper(func.replace(func.left(cast(IPAddress.mac, String), 8), ":", ""))
        ).scalar_subquery(),
    }
    if sort == "stale_days":
        # 天數少的在前＝時間新的在前；從沒看過的最久（升冪排最後、降冪排最前）
        stmt = stmt.order_by(_probe_seen.asc().nulls_first() if order == "desc" else _probe_seen.desc().nulls_last(),
                             IPAddress.ip)
    else:
        sort_col = _SORT_COLS.get(sort or "", IPAddress.ip)
        # 沒有值的列一律排最後（升冪降冪都一樣，跟其他清單頁一致）
        stmt = stmt.order_by((sort_col.desc() if order == "desc" else sort_col.asc()).nulls_last(), IPAddress.ip)
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    rows = list((await session.execute(stmt)).scalars().all())

    if subnet_id is None:
        # 跨 subnet 列表 — 必須逐筆檢查 subnet 權限
        candidate_subnet_ids = list({r.subnet_id for r in rows})
        visible_subnet_ids = set(
            await filter_visible(
                session,
                user=user,
                object_type="subnet",
                object_ids=candidate_subnet_ids,
                required="read",
            )
        )
        rows = [r for r in rows if r.subnet_id in visible_subnet_ids]

    items = [IPAddressRead.model_validate(r) for r in rows]
    # 批次 lookup OUI vendor，一次 SQL 處理整頁
    vmap = await vendor_map(session, [r.mac for r in rows])
    # 批次帶上所屬 subnet 的 scan_enabled（沒掃描的網段，前端不該把 IP 標離線）
    scan_ids = list({r.subnet_id for r in rows})
    scan_map: dict[uuid.UUID, bool] = {}
    if scan_ids:
        scan_map = dict(
            (await session.execute(  # type: ignore[arg-type]
                select(Subnet.id, Subnet.scan_enabled).where(in_values(Subnet.id, scan_ids))
            )).all()
        )
    # 批次帶上關聯裝置名稱（清單「裝置」欄用）
    dev_ids = list({r.device_id for r in rows if r.device_id})
    vis_dev = await visible_ids(session, user=user, object_type="device")
    if vis_dev is not None:      # 看不到的裝置不帶名稱（2026-10-07 稽核）
        dev_ids = [i for i in dev_ids if i in vis_dev]
    dev_map: dict[uuid.UUID, str] = {}
    if dev_ids:
        from app.models.device import Device
        dev_map = dict(
            (await session.execute(
                select(Device.id, Device.name).where(in_values(Device.id, dev_ids))
            )).all()
        )
    for it, r in zip(items, rows, strict=False):
        p = mac_prefix(r.mac)
        if p:
            it.mac_vendor = vmap.get(p)
        it.subnet_scan_enabled = scan_map.get(r.subnet_id)
        if r.device_id:
            it.device_name = dev_map.get(r.device_id)
    # 共用給裝置群組的 IP：設備欄顯示群組名稱
    await _fill_group_names(session, items, rows)
    # 特殊角色旗標（閘道 / DHCP 範圍 / DHCP 伺服器）→ 清單視覺化
    await _enrich_special_flags(session, items, rows)
    total = int(await session.scalar(count_stmt) or 0)
    return Paginated[IPAddressRead](items=items, total=total, page=page, page_size=page_size)


# 注意：此路由必須宣告在 /{address_id} 之前，否則 "export.csv" 會被當成 address_id（UUID 驗證 422）
@router.get("/unmanaged")
async def list_unmanaged(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    subnet_id: uuid.UUID = Query(...),
) -> list[dict[str, Any]]:
    """這個子網路裡沒有 IP 記錄、但最近看得到在用的位址（掃描代理目擊＋LibreNMS ARP）：指示計的「未納管」格子。

    權限跟 IP 清單一樣（子網路的讀取權限；沒有就回 404，不洩漏存在性）。
    """
    await _require_subnet_perm(session, user, subnet_id, "read")
    from app.services.unmanaged import for_subnet
    rows = await for_subnet(session, subnet_id)
    macs = [r["mac"] for r in rows if r.get("mac")]
    vendors = await vendor_map(session, macs) if macs else {}
    for r in rows:
        r["vendor"] = vendors.get(mac_prefix(r["mac"]) or "") if r.get("mac") else None
    return rows


@router.get("/unmanaged-summary")
async def unmanaged_summary(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """掃描代理最近看到、但 IPAM 沒有記錄的位址有幾個（看得到的子網路裡），給「IP 位址」總表的提示用。

    客戶 2026-11-11：新站台開了掃描，IP 位址頁卻是空的 —— 掃描代理預設不自動收錄（未授權 IP 偵測才有意義），
    掃到的只記成「未納管」，只在子網路頁出現，畫面上沒有任何地方說要去哪裡開。
    一個分組查詢（不逐子網路算）；管理員另外附上有幾台代理開了自動收錄（一般帳號不給代理設定的資訊）。
    """
    from sqlalchemy import exists, func

    from app.models.scan_agent import ScanAgent
    from app.models.subnet import Subnet
    from app.models.unmanaged_sighting import UnmanagedSighting
    from app.services.permission import visible_ids
    from app.services.unmanaged import GRID_WINDOW

    since = datetime.now(UTC) - GRID_WINDOW
    vis = await visible_ids(session, user=user, object_type="subnet", required="read")
    host = func.host(UnmanagedSighting.ip)
    registered = exists().where(IPAddress.subnet_id == UnmanagedSighting.subnet_id, func.host(IPAddress.ip) == host)
    n = func.count(func.distinct(host))
    stmt = (select(UnmanagedSighting.subnet_id, Subnet.cidr, n.label("n"))
            .join(Subnet, Subnet.id == UnmanagedSighting.subnet_id)
            .where(UnmanagedSighting.source == "scanner", UnmanagedSighting.last_seen_at >= since,
                   Subnet.archived_at.is_(None), ~registered)
            .group_by(UnmanagedSighting.subnet_id, Subnet.cidr)
            .order_by(n.desc(), Subnet.cidr))
    if vis is not None:
        if not vis:
            return {"count": 0, "subnets": [], "agents_total": None, "agents_auto_create": None}
        stmt = stmt.where(in_values(UnmanagedSighting.subnet_id, vis))
    rows = (await session.execute(stmt)).all()
    agents_total = agents_auto = None
    if user.is_admin:
        agents_total = int(await session.scalar(select(func.count()).select_from(ScanAgent)) or 0)
        agents_auto = int(await session.scalar(
            select(func.count()).select_from(ScanAgent).where(ScanAgent.auto_create_ips.is_(True))) or 0)
    return {
        "count": sum(int(r.n) for r in rows),
        "subnets": [{"subnet_id": str(r.subnet_id), "cidr": str(r.cidr), "count": int(r.n)} for r in rows[:5]],
        "agents_total": agents_total,
        "agents_auto_create": agents_auto,
    }


@router.get("/export.csv")
async def export_csv(
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    subnet_id: uuid.UUID = Query(..., description="必要：限定要匯出的 subnet"),
) -> Response:
    """以 CSV 匯出某個 subnet 的所有 IP（須對該 subnet 有 read 權限）。Excel 友善：UTF-8 + BOM。

    整個子網路的位址清單被帶走，要留下是誰、什麼時候、幾筆（稽核）。"""
    await _require_subnet_perm(session, user, subnet_id, "read")
    rows = list((await session.execute(
        select(IPAddress).where(IPAddress.subnet_id == subnet_id).order_by(IPAddress.ip)
    )).scalars().all())
    body = export_addresses_csv(rows)
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="subnet", object_id=str(subnet_id), action="export_csv",
        diff={"rows": len(rows)},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="addresses-{subnet_id}.csv"',
            "Cache-Control": "no-store",
        },
    )


async def _fill_pve_console(session: AsyncSession, obj: IPAddress, out: Any, user: Any) -> None:
    """填 out.novnc_available + out.pve（此 IP 對應的 Proxmox VE VM/CT；非 PVE 則 pve=None）。"""
    from app.services.permission import can_use_bmc, can_use_novnc
    from app.services.pve_console import resolve_pve_target
    out.novnc_available = await can_use_novnc(session, user=user, ip=obj)
    out.bmc_available = await can_use_bmc(session, user=user, ip=obj)
    tgt = await resolve_pve_target(session, obj)
    if tgt is not None:
        from app.schemas.address import PveConsoleTarget
        out.pve = PveConsoleTarget(kind=tgt.kind, node=tgt.node, vmid=tgt.vmid, cluster=tgt.cluster_name)


async def _effective_probes_for(session: AsyncSession, obj: IPAddress) -> list[str]:
    """此 IP 實際會被執行的探測 = 子網路 scan_method − IP excluded ∩ 代理 enabled。
    子網路未開掃描 → 空。沒指派代理 → 無能力上限。"""
    from app.core.scan_probes import effective_probes
    from app.models.scan_agent import ScanAgent
    sub = await session.get(Subnet, obj.subnet_id)
    if sub is None or not sub.scan_enabled:
        return []
    agent_enabled: list[str] | None = None
    if sub.scan_agent_id:
        ag = await session.get(ScanAgent, sub.scan_agent_id)
        if ag is not None:
            agent_enabled = list(ag.enabled_probes or [])
    return effective_probes(list(sub.scan_method or []), list(obj.excluded_probes or []), agent_enabled)


@router.get("/{address_id}/firewall", dependencies=[Depends(require_global_read)])
async def get_address_firewall(
    address_id: uuid.UUID,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """這個 IP 被哪些防火牆規則／NAT／別名管到（反向查詢）。

    權限雙閘：IP 本身要可讀（物件級），且防火牆規則屬**全域基礎設施資料**，
    依 RBAC 分類需 require_global_read —— 部門帳號看得到自己的 IP，
    但規則內容不屬於它的可見範圍。
    """
    obj = await session.get(IPAddress, address_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Address not found")
    await _require_subnet_perm(session, user, obj.subnet_id, "read")
    from app.services.fw_lookup import rules_touching_ip
    return await rules_touching_ip(session, str(obj.ip), ip_id=obj.id)


class SiblingIP(StrictModel):
    """同主機名稱、尚未關聯裝置的另一筆 IP —— **候選，不是結論。**"""

    id: uuid.UUID
    ip: str
    mac: str | None = None
    mac_vendor: str | None = None
    #: MAC 與本 IP 相同 → 幾乎可以確定是同一張網卡
    same_mac: bool = False


class DeviceSuggestion(StrictModel):
    """「這個 IP 應該屬於哪一台裝置」的建議。**只是建議，不會自己動手。**"""

    suggested_name: str | None = None
    existing_device_id: uuid.UUID | None = None
    existing_device_name: str | None = None
    #: 為什麼認為是這一台：name / fqdn / ip / mac
    match_reason: str | None = None
    #: 用同一個主機名稱、尚未關聯裝置的其他 IP。
    #:
    #: ⚠️ **同主機名稱不等於同一台機器。** DHCP 把位址回收給別台之後，IP 記錄上的舊
    #: 主機名稱還留著 —— 實機上一台筆電的名字散在九筆 IP 上，其中有 Proxmox 的 VM
    #: 和一顆 ESP32。所以這裡回的是**候選與它們的證據**（MAC、廠商），由人決定要掛哪些，
    #: 而不是給一個「全部掛上」的開關。
    siblings: list[SiblingIP] = Field(default_factory=list)
    #: 這個使用者能不能建立裝置（建立裝置是管理員限定）
    can_create: bool = False


class DeviceSuggestionApply(StrictModel):
    """套用建議。二擇一：關聯到既有裝置，或建立一台新的。"""

    device_id: uuid.UUID | None = None
    create_name: str | None = Field(default=None, max_length=200)
    #: 要一併關聯的其他 IP —— **由使用者逐筆勾選**，不是一個「全部」開關
    link_ip_ids: list[uuid.UUID] = Field(default_factory=list)


def _first_label(hostname: str | None) -> str | None:
    """`laptop-07.local` → `laptop-07`。裝置名稱慣例用第一段。"""
    h = (hostname or "").strip()
    return h.split(".")[0] or None if h else None


async def _sibling_rows(
    session: AsyncSession, *, user: User, hostname: str, exclude: uuid.UUID,
) -> list[Any]:
    """同主機名稱、尚未關聯裝置、且**這個使用者能寫**的其他 IP。

    可見性一定要推進 SQL：先取再過濾會讓數量算在使用者看不到的資料上。
    """
    q = (
        select(IPAddress.id, IPAddress.ip, IPAddress.mac)
        .join(Subnet, IPAddress.subnet_id == Subnet.id)
        .where(
            func.lower(IPAddress.hostname) == hostname.strip().lower(),
            IPAddress.device_id.is_(None),
            IPAddress.id != exclude,
            Subnet.archived_at.is_(None),
        )
    )
    vis = await visible_ids(session, user=user, object_type="subnet", required="write")
    if vis is not None:
        if not vis:
            return []
        q = q.where(in_values(IPAddress.subnet_id, vis))
    return list((await session.execute(q.limit(50))).all())


class SameMacAddressRead(IPAddressRead):
    #: 這個 MAC 最早什麼時候出現在那個位址（ARP 記錄）—— 分辨「搬走了」與「同一台的另一個介面」
    mac_since: datetime | None = None


@router.get("/{address_id}/same-mac", response_model=list[SameMacAddressRead])
async def same_mac_addresses(
    address_id: uuid.UUID,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[SameMacAddressRead]:
    """同一個 MAC 的其他位址（看得到的子網路才列，最多 20 筆）。

    給「上線狀態跟著設備」的提示用（客戶 2026-10-14）：多 LAN 路由器各網段的閘道常是同一個 MAC，
    這一個不回應探測、同 MAC 的另一個在線 → 多半是同一台設備的介面位址。只回事實，套不套用由人決定。
    """
    obj = await session.get(IPAddress, address_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Address not found")
    await _require_subnet_perm(session, user, obj.subnet_id, "read")
    if not obj.mac:
        return []
    rows = list((await session.execute(
        select(IPAddress).where(IPAddress.mac == obj.mac, IPAddress.id != obj.id)
        .order_by(IPAddress.ip).limit(20))).scalars().all())
    visible = set(await filter_visible(session, user=user, object_type="subnet",
                                       object_ids=list({r.subnet_id for r in rows}), required="read"))
    rows = [r for r in rows if r.subnet_id in visible]
    # 主機重灌換了位址（正式環境 2026-10-20）時，舊位址不該被建議「跟著設備」：給前端比對時間用
    from app.models.librenms import ARPEntry
    since: dict[str, datetime] = {}
    if rows:
        for ip, first in (await session.execute(
                select(func.host(ARPEntry.ip), func.min(ARPEntry.first_seen_at))
                .where(ARPEntry.mac == obj.mac,
                       in_values(func.host(ARPEntry.ip), [str(r.ip).split("/")[0] for r in rows], type_=Text()))
                .group_by(func.host(ARPEntry.ip)))).all():
            since[str(ip)] = first
    return [SameMacAddressRead.model_validate(r).model_copy(
        update={"mac_since": since.get(str(r.ip).split("/")[0])}) for r in rows]


async def _match_existing_device(session: AsyncSession, obj: IPAddress) -> tuple[uuid.UUID, str, str] | None:
    """這個 IP 看起來屬於哪一台既有裝置：(id, 名稱, 依據 name／mac／ip)。對到多台就不猜（同 ip_device_link）。"""
    from app.models.device import Device
    from app.models.physical import DevicePort
    hostname = (obj.hostname or "").strip()
    ip_host = str(obj.ip).split("/")[0]
    if hostname:
        row = (await session.execute(
            select(Device.id, Device.name).where(
                or_(func.lower(Device.name) == hostname.lower(),
                    func.lower(Device.name) == (_first_label(hostname) or "").lower(),
                    func.lower(Device.fqdn) == hostname.lower())
            ).limit(2)
        )).all()
        if len(row) == 1:
            return row[0][0], row[0][1], "name"
    if obj.mac:
        row = (await session.execute(
            select(DevicePort.device_id, Device.name)
            .join(Device, Device.id == DevicePort.device_id)
            .where(func.lower(DevicePort.mac_address) == str(obj.mac).lower())
            .limit(2)
        )).all()
        if len(row) == 1:
            return row[0][0], row[0][1], "mac"
    # 精確比對位址本身。用字串前綴比對會把 10.0.0.1 命中成 10.0.0.10／10.0.0.100 ——
    # 掛錯的裝置關聯比沒有關聯更難發現。
    row = (await session.execute(
        select(Device.id, Device.name)
        .join(IPAddress, IPAddress.id == Device.primary_ip_id)
        .where(func.host(IPAddress.ip) == ip_host).limit(2)
    )).all()
    if len(row) == 1:
        return row[0][0], row[0][1], "ip"
    return None


@router.get("/{address_id}/device-suggestion", response_model=DeviceSuggestion)
async def device_suggestion(
    address_id: uuid.UUID,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DeviceSuggestion:
    """這個 IP 看起來屬於哪一台裝置 —— 或者該建立哪一台。

    **只回建議，不會建立也不會關聯任何東西。** DHCP 的筆電會散在十幾個 IP 上，
    每一筆都手動建裝置、手動關聯是純粹的苦工；但要不要建立仍然是人決定。
    """

    obj = await session.get(IPAddress, address_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Address not found")
    await _require_subnet_perm(session, user, obj.subnet_id, "read")

    out = DeviceSuggestion(can_create=bool(user.is_admin))
    if obj.device_id is not None:
        return out                      # 已經有裝置就沒什麼好建議的

    hostname = (obj.hostname or "").strip()
    str(obj.ip).split("/")[0]
    out.suggested_name = _first_label(hostname)

    # ── 先找既有裝置：名稱 / FQDN / 主要 IP / 連接埠 MAC（批次建立裝置也用同一套，_match_existing_device）
    hit = await _match_existing_device(session, obj)
    dev, reason = ((hit[0], hit[1]), hit[2]) if hit else (None, None)

    # 對得上的裝置看不到就不建議（以前會回看不到的裝置 id 與名稱）
    if dev is not None and not user.is_admin and not has_permission(
            await get_object_permission(session, user=user, object_type="device", object_id=dev[0]), "read"):
        dev = None
    if dev is not None:
        out.existing_device_id, out.existing_device_name = dev
        out.match_reason = reason
    if hostname:
        my_mac = str(obj.mac).lower() if obj.mac else None
        for sid, sip, smac in await _sibling_rows(
            session, user=user, hostname=hostname, exclude=obj.id,
        ):
            mac = str(smac) if smac else None
            out.siblings.append(SiblingIP(
                id=sid, ip=str(sip).split("/")[0], mac=mac,
                mac_vendor=await vendor_for_mac(session, smac),
                same_mac=bool(my_mac and mac and mac.lower() == my_mac),
            ))
    return out


@router.post("/{address_id}/device-suggestion/apply", response_model=IPAddressRead)
async def apply_device_suggestion(
    address_id: uuid.UUID,
    payload: DeviceSuggestionApply,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> IPAddressRead:
    """把建議套用下去：關聯到既有裝置，或建立一台再關聯。"""
    from app.models.device import Device

    obj = await session.get(IPAddress, address_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Address not found")
    await _require_subnet_perm(session, user, obj.subnet_id, "write")

    if bool(payload.device_id) == bool(payload.create_name):
        raise HTTPException(status_code=400,
                            detail=ui_detail("addr_device_pick_one",
                                            "請二擇一：關聯到既有裝置，或建立一台新的"))

    if payload.create_name:
        # 建立裝置是管理員限定（與 POST /devices 同一條線）
        if not user.is_admin:
            raise HTTPException(status_code=403, detail=ui_detail("addr_device_create_admin_only", "只有管理員能建立裝置"))
        name = payload.create_name.strip()
        if not name:
            raise HTTPException(status_code=400, detail=ui_detail("addr_device_name_empty", "裝置名稱不可為空"))
        hostname = (obj.hostname or "").strip()
        device = Device(name=name, type="other",
                        fqdn=hostname if "." in hostname else None)
        session.add(device)
        await session.flush()
        await append_audit(
            session,
            actor_user_id=str(user.id),
            actor_ip=request.client.host if request.client else None,
            actor_user_agent=request.headers.get("user-agent"),
            object_type="device", object_id=str(device.id), action="create",
            diff={"name": name, "from_ip": str(obj.ip)},
            request_id=getattr(request.state, "request_id", None),
        )
    else:
        device = await session.get(Device, payload.device_id)
        if device is None:
            raise HTTPException(status_code=404, detail="Device not found")
        await require_visible(session, user, "device", device.id, "Device not found")

    linked = 0
    if obj.device_id is None:
        await log_change(
            session, ip=obj, event_type="edited", field="device_id",
            old=None, new=str(device.id), source="user", actor_user_id=user.id,
            note="applied device suggestion",
        )
        obj.device_id = device.id
        linked += 1
    if payload.create_name:
        # 剛從 IP 建出來的裝置：照這筆 IP 的作業系統證據先判斷類型（以前一律寫死 other）
        from app.services.device_type_auto import refresh_auto_types
        await session.flush()
        await refresh_auto_types(session, [device.id])

    if payload.link_ip_ids and (obj.hostname or "").strip():
        # 客戶端送來的 id 不能直接相信：只接受「這個使用者能寫、且確實同主機名稱、
        # 且還沒關聯」的那一批 —— 以伺服器自己算出來的集合為準。
        allowed = {
            row[0] for row in await _sibling_rows(
                session, user=user, hostname=obj.hostname or "", exclude=obj.id,
            )
        }
        for sid in payload.link_ip_ids:
            if sid not in allowed:
                continue
            sib = await session.get(IPAddress, sid)
            if sib is None or sib.device_id is not None:
                continue
            await log_change(
                session, ip=sib, event_type="edited", field="device_id",
                old=None, new=str(device.id), source="user", actor_user_id=user.id,
                note="applied device suggestion (same hostname)",
            )
            sib.device_id = device.id
            linked += 1

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="ip_address", object_id=str(obj.id), action="update",
        diff={"device_id": str(device.id), "linked_ips": linked},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    out = IPAddressRead.model_validate(obj)
    out.mac_vendor = await vendor_for_mac(session, obj.mac)
    return out


async def _seen_integrations(session: AsyncSession) -> dict[str, bool]:
    """「各來源最後出現」裡依整合而來的來源，有沒有設定整合（LibreNMS 也供 ARP 那一列）。EXISTS，不數筆數。"""
    from app.models.adguard import AdGuardInstance
    from app.models.librenms import LibreNMSInstance
    return {key: bool(await session.scalar(select(select(model.id).limit(1).exists())))
            for key, model in (("librenms", LibreNMSInstance), ("adguard", AdGuardInstance))}


async def _fill_group_names(session: AsyncSession, items: list[Any], rows: list[Any]) -> None:
    """共用給裝置群組的 IP 帶上群組名稱（設備欄顯示「群組（共用）」；設備欄本身是群組的代表裝置）。"""
    gids = {r.device_group_id for r in rows if getattr(r, "device_group_id", None)}
    if not gids:
        return
    from app.models.device_group import DeviceGroup
    names = dict((await session.execute(
        select(DeviceGroup.id, DeviceGroup.name).where(in_values(DeviceGroup.id, list(gids))))).all())
    for it, r in zip(items, rows, strict=False):
        if getattr(r, "device_group_id", None):
            it.device_group_name = names.get(r.device_group_id)


@router.get("/{address_id}", response_model=IPAddressRead)
async def get_address(
    address_id: uuid.UUID,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> IPAddressRead:
    obj = await session.get(IPAddress, address_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Address not found")
    await _require_subnet_perm(session, user, obj.subnet_id, "read")
    out = IPAddressRead.model_validate(obj)
    out.mac_vendor = await vendor_for_mac(session, obj.mac)
    await _fill_group_names(session, [out], [obj])
    # 閘道／DHCP 範圍／固定分配明細／排除區間：跟清單同一套推導（以前詳細資料沒有，兩邊標示不一致）
    await _enrich_special_flags(session, [out], [obj])
    # 虛擬化對應：IP 或 MAC 命中 VM 網卡 → 標示為虛擬機（顯示用，不寫回資料）
    from app.services.fw_lookup import vm_match_for
    out.virt_vm = await vm_match_for(session, ip=str(obj.ip).split("/")[0],
                                     macs=[str(obj.mac)] if obj.mac else None)
    from app.services.system_config import get_liveness_config
    out.liveness_rule = await get_liveness_config(session)
    out.seen_integrations = await _seen_integrations(session)
    # SSH 連線管理：是否可對此 IP 開終端機（依權限算好給前端顯示按鈕）
    from app.services.permission import can_use_rdp, can_use_sftp, can_use_ssh, can_use_vnc
    out.ssh_available = await can_use_ssh(session, user=user, ip=obj)
    out.sftp_available = await can_use_sftp(session, user=user, ip=obj)
    out.rdp_available = await can_use_rdp(session, user=user, ip=obj)
    out.vnc_available = await can_use_vnc(session, user=user, ip=obj)
    await _fill_pve_console(session, obj, out, user)
    from app.services.permission import can_use_rustdesk
    from app.services.rustdesk import elsewhere_on_device
    from app.services.rustdesk import for_address as rustdesk_for_address
    out.rustdesk = await rustdesk_for_address(session, obj.id)
    if out.rustdesk is None:
        out.rustdesk_elsewhere = await elsewhere_on_device(session, obj, user=user)
    if out.rustdesk is not None and not await can_use_rustdesk(session, user=user, ip=obj):
        out.rustdesk["connect_uri"] = None
        out.rustdesk["web_available"] = False
        out.rustdesk["file_available"] = False
    # 算出此 IP 實際會被執行的探測（子網路要跑 − IP 略過 ∩ 代理能力）給詳細資料頁顯示
    out.effective_probes = await _effective_probes_for(session, obj)
    # OS 依來源優先序（scanner/librenms/wazuh）解析有效值 + 來源
    from app.services.os_precedence import effective_os
    _os = await effective_os(session, obj)
    out.os_guess = _os["os_guess"]; out.os_family = _os["os_family"]; out.os_source = _os["os_source"]
    return out


@router.post("/{address_id}/rustdesk/local-open", status_code=204)
async def rustdesk_local_open(
    address_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """使用者按「用本機的 RustDesk 客戶端軟體開啟」時記一筆稽核（使用者 2026-10-05）。

    rustdesk:// 網址交給使用者電腦上的客戶端，連線在客戶端與對方之間、jt-ipam 看不到；至少記下誰、什麼時候、
    對哪個 IP（哪個 RustDesk ID）開了 —— 「調查」的遠端連線記錄以前只列得出網頁連線。
    權限與 IP 詳細資料給 connect_uri 的條件相同（can_use_rustdesk）；網址本身照舊由詳細資料提供，這裡不回傳。"""
    from app.core.rate_limit import limit_per_ip
    from app.services import rustdesk as rustdesk_svc
    from app.services.permission import can_use_rustdesk

    await limit_per_ip(request, name="rustdesk")
    ip = await session.get(IPAddress, address_id)
    if ip is None:
        raise HTTPException(status_code=404, detail="Address not found")
    if not await can_use_rustdesk(session, user=user, ip=ip):
        raise HTTPException(status_code=403, detail=ui_detail(
            "rd_not_permitted", "沒有權限，或這個 IP 沒有開啟 RustDesk 連線"))
    row = await rustdesk_svc.matched_peer(session, ip.id)
    if row is None:
        raise HTTPException(status_code=404, detail=ui_detail("rd_no_peer", "這個 IP 沒有對應到 RustDesk 裝置"))
    peer, srv = row
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="ip", object_id=str(ip.id), action="rustdesk.local_client_open",
        diff={"ip": str(ip.ip).split("/")[0], "peer_id": peer.rustdesk_id, "server": srv.name},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return Response(status_code=204)


@router.get("/{address_id}/relations")
async def get_address_relations(
    address_id: uuid.UUID,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """IP 的上下關係鏈：機房 → 機櫃 →（主機 → 虛擬機 →）裝置 → 位址 → 子網路 → 區段。
    每個節點 {type,id,label,sub}；缺的環節省略。前端橫向串成關係圖。

    方向跟裝置頁、儀表板一致：實體在左、邏輯在右。下面由 IP 往外接比較好寫，
    所以最後整條反轉 —— 以前沒有反轉，IP 頁跟另外兩處左右顛倒（使用者回報）。"""
    from app.models.device import Device
    from app.models.location import Location, Rack
    from app.models.section import Section

    obj = await session.get(IPAddress, address_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Address not found")
    await _require_subnet_perm(session, user, obj.subnet_id, "read")

    chain: list[dict[str, Any]] = []
    subnet = await session.get(Subnet, obj.subnet_id) if obj.subnet_id else None
    if subnet is not None and subnet.section_id:
        sec = await session.get(Section, subnet.section_id)
        if sec is not None:
            chain.append({"type": "section", "id": str(sec.id), "label": sec.name})
    if subnet is not None:
        chain.append({"type": "subnet", "id": str(subnet.id),
                      "label": str(subnet.cidr), "sub": subnet.description})
    chain.append({"type": "ip", "id": str(obj.id),
                  "label": str(obj.ip).split("/")[0], "sub": obj.hostname})

    async def _device_tail(dev: Device, *, sub: str | None = None, node_type: str = "device",
                           platform: str | None = None) -> None:
        """把 device → rack → 機房 接到鏈尾（node_type=vmnode 時該裝置代表虛擬化主機）。"""
        node: dict[str, Any] = {"type": node_type, "id": str(dev.id), "label": dev.name, "sub": sub}
        if platform:
            node["platform"] = platform
        chain.append(node)
        # 地點優先用裝置自身的 location_id；裝置沒設但有掛機櫃時，繼承機櫃所在地點
        loc_id = dev.location_id
        if dev.rack_id:
            rk = await session.get(Rack, dev.rack_id)
            if rk is not None:
                chain.append({"type": "rack", "id": str(rk.id), "label": rk.name})
                if loc_id is None:
                    loc_id = rk.location_id
        if loc_id:
            loc = await session.get(Location, loc_id)
            if loc is not None:
                chain.append({"type": "location", "id": str(loc.id), "label": loc.name})

    async def _find_vm(device_name: str | None):
        """這個 IP 對應到的 Proxmox VM：主要 IP → 介面 IP → 名稱（主機名稱 / 裝置名稱）。"""
        from app.models.virt import VirtCluster, VirtualMachine, VMInterface
        ipstr = str(obj.ip).split("/")[0]
        vm = (await session.execute(
            select(VirtualMachine).where(VirtualMachine.primary_ip_id == obj.id).limit(1)
        )).scalar_one_or_none()
        if vm is None:
            vm = (await session.execute(
                select(VirtualMachine).join(VMInterface, VMInterface.vm_id == VirtualMachine.id)
                .where(func.host(VMInterface.primary_ip) == ipstr).limit(1)
            )).scalar_one_or_none()
        from app.services.fw_lookup import vm_mac_conflict
        if vm is not None and await vm_mac_conflict(session, vm.id, obj.mac):
            vm = None      # 只有 IP 對到、MAC 不同：DHCP 位址換了主人，不是這台 VM（2026-10-12）
        if vm is None:
            names = {n.lower() for n in (obj.hostname, device_name) if n}
            if names:
                vm = (await session.execute(
                    select(VirtualMachine).where(func.lower(VirtualMachine.name).in_(names)).limit(1)  # bounded: hostname variants of one IP
                )).scalar_one_or_none()
        # 只連「同單位」的 VM：IP 所屬單位（取自子網路）與 VM 叢集所屬單位都有設定且不同 → 不連
        if vm is not None and subnet is not None and subnet.customer_id is not None:
            cluster = await session.get(VirtCluster, vm.cluster_id)
            if cluster is not None and cluster.customer_id is not None \
                    and cluster.customer_id != subnet.customer_id:
                return None
        return vm

    async def _append_pve_node(vm, *, skip_id: uuid.UUID | None = None) -> None:
        """把 VM 所在的 PVE 節點接到鏈尾：對得到實體裝置就連裝置（含其機櫃/機房），否則只顯示節點名稱。"""
        from app.models.virt import VirtCluster
        cluster = await session.get(VirtCluster, vm.cluster_id) if vm.cluster_id else None
        csub = cluster.name if cluster is not None else None
        plat = (cluster.type if cluster is not None else None) or "proxmox"
        # PVE node host 名稱 → 對到 jt-ipam 的實體裝置（VM 直接連結，否則比對 name，再比對 fqdn）
        from app.services.relation_chain import vm_host_device
        node_dev, _how = await vm_host_device(session, vm)
        if node_dev is not None and node_dev.id != skip_id:
            await _device_tail(node_dev, sub=csub, node_type="vmnode", platform=plat)
        elif vm.node:
            chain.append({"type": "vmnode", "id": "host:" + vm.node, "label": vm.node,
                              "sub": csub, "platform": plat})

    # 直接關聯的裝置（這台主機本身）；無論是否為 VM 都先接上
    dev_name: str | None = None
    if obj.device_id:
        dev = await session.get(Device, obj.device_id)
        if dev is not None:
            dev_name = dev.name
            await _device_tail(dev)
    # 若這個 IP 屬於某台虛擬機，補上虛擬機與它所在的實體節點。
    #
    # **有沒有對應到裝置，虛擬機節點都要畫。** 原本「有裝置就不畫」，於是同樣是虛擬機的
    # 兩個 IP，一個看得到虛擬機、一個看不到 —— 使用者無從理解規則（實機回報）。
    # 就算裝置與虛擬機是同一台機器的兩筆紀錄，那也是兩個不同層級的物件，關係圖本來
    # 就是在表達層級。
    vm = await _find_vm(dev_name)
    if vm is not None:
        chain.append({"type": "vm", "id": str(vm.id), "label": vm.name, "sub": None,
                      "platform": None})
        await _append_pve_node(vm, skip_id=obj.device_id)
    return {"chain": await visible_chain(session, user, chain[::-1])}


@router.get("/{address_id}/history")
async def get_address_history(
    address_id: uuid.UUID,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    event_type: str | None = Query(None),
    source: str | None = Query(None),
) -> dict[str, Any]:
    """單一 IP 的異動記錄（時間倒序）。

    回傳帶 **總數與可用篩選值**，不只是一頁資料：實機單一 IP 最多有 1,838 筆，
    只回一個陣列的話使用者無從得知自己看到的是全部還是冰山一角（與清單靜默截斷同一類問題）。
    """
    obj = await session.get(IPAddress, address_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Address not found")
    await _require_subnet_perm(session, user, obj.subnet_id, "read")

    base = select(IPChangeLog).where(IPChangeLog.ip_id == address_id)
    if event_type:
        base = base.where(IPChangeLog.event_type == event_type)
    if source:
        base = base.where(IPChangeLog.source == source)

    total = int(await session.scalar(
        select(func.count()).select_from(base.subquery())) or 0)

    # 篩選選項（含筆數）一律以「未篩選」為母體算，否則選了之後其他選項就消失了
    facet_rows = (await session.execute(
        select(IPChangeLog.event_type, IPChangeLog.source, func.count())
        .where(IPChangeLog.ip_id == address_id)
        .group_by(IPChangeLog.event_type, IPChangeLog.source)
    )).all()
    ev_counts: dict[str, int] = {}
    src_counts: dict[str, int] = {}
    for ev, src, c in facet_rows:
        ev_counts[ev] = ev_counts.get(ev, 0) + int(c)
        src_counts[src] = src_counts.get(src, 0) + int(c)

    rows = list((await session.execute(
        base.order_by(IPChangeLog.created_at.desc()).offset(offset).limit(limit)
    )).scalars().all())

    actor_ids = list({r.actor_user_id for r in rows if r.actor_user_id is not None})
    name_map: dict[uuid.UUID, str] = {}
    if actor_ids:
        for uid, uname in (await session.execute(
            select(User.id, User.username).where(User.id.in_(actor_ids))  # bounded: actors on one page
        )).all():
            name_map[uid] = uname

    out: list[IPChangeLogRead] = []
    for r in rows:
        m = IPChangeLogRead.model_validate(r)
        m.actor_username = name_map.get(r.actor_user_id) if r.actor_user_id else None
        out.append(m)
    return {
        "items": out,
        "total": total,          # 目前篩選條件下的總數
        "returned": len(out),
        "event_types": [{"value": k, "count": v} for k, v in sorted(ev_counts.items())],
        "sources": [{"value": k, "count": v} for k, v in sorted(src_counts.items())],
    }


@router.get("/{address_id}/switch-port")
async def get_address_switch_port(
    address_id: uuid.UUID,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """此 IP 在 FDB 裡對到的 switch + port（access port = 該 port MAC 數最少者）。"""
    obj = await session.get(IPAddress, address_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Address not found")
    await _require_subnet_perm(session, user, obj.subnet_id, "read")
    from app.mcp.tools import switch_port_for_ip
    try:
        # 帶 ip_id：重疊網段（同一個 IP 在好幾個子網路）時回的是「這一筆」的 MAC 與埠，不是任一筆
        return await switch_port_for_ip(session, user=user, ip=str(obj.ip).split("/")[0], ip_id=str(obj.id))
    except Exception:
        return {"ip": str(obj.ip), "mac": obj.mac and str(obj.mac), "locations": []}


@router.get("/{address_id}/hostname-sources")
async def get_address_hostname_sources(
    address_id: uuid.UUID,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """單一 IP 的各來源 hostname 觀測 + 目前 pin + 全域優先序（feature A，給 pin UI）。"""
    obj = await session.get(IPAddress, address_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Address not found")
    await _require_subnet_perm(session, user, obj.subnet_id, "read")

    from app.models.ip_hostname import IPHostnameObservation
    from app.services.hostname import get_precedence
    from app.services.hostname_reports import reports_by_source
    rows = (await session.execute(
        select(IPHostnameObservation)
        .where(IPHostnameObservation.ip_id == address_id)
    )).scalars().all()
    # 逐台回報（客戶 2026-10-11：要看得出名字是哪一台代理／哪個整合給的）。observed_at 只在名稱改變時才動，
    # 「最後一次有人回報」要看 reports 的 last_seen_at；沒有逐台記錄的（手動）退回 observed_at
    reports = await reports_by_source(session, address_id)
    return {
        "effective": obj.hostname,
        "pin": obj.hostname_source_pin,
        "order": await get_precedence(session),
        "observations": [
            {"source": r.source, "hostname": r.hostname,
             "observed_at": r.observed_at.isoformat(),
             "last_seen_at": max((x["last_seen_at"] for x in reports.get(r.source, []) if x["last_seen_at"]),
                                 default=r.observed_at.isoformat()),
             "reports": reports.get(r.source, [])}
            for r in rows
        ],
    }


@router.delete("/{address_id}/hostname-sources/{source}", status_code=204)
async def clear_address_hostname_source(
    address_id: uuid.UUID,
    source: str,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """清掉某 IP 某來源的 hostname 觀測（例如過時的「手動: tp-link-c7」），
    然後依優先序重算有效 hostname。"""
    from sqlalchemy import delete as _delete

    from app.models.ip_hostname import IPHostnameObservation

    obj = await session.get(IPAddress, address_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Address not found")
    await _require_subnet_perm(session, user, obj.subnet_id, "write")
    await session.execute(
        _delete(IPHostnameObservation).where(
            IPHostnameObservation.ip_id == address_id,
            IPHostnameObservation.source == source,
        )
    )
    # 若這個來源剛好是目前 pin，順手取消 pin
    if obj.hostname_source_pin == source:
        obj.hostname_source_pin = None
    await recompute_effective(session, ip=obj)
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="ip_address", object_id=str(obj.id), action="update",
        diff={"cleared_hostname_source": source},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()


@router.post("", response_model=IPAddressRead, status_code=status.HTTP_201_CREATED)
async def create_address(
    payload: IPAddressCreate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> IPAddressRead:
    subnet = await _require_subnet_perm(session, user, payload.subnet_id, "write")
    # 守門、冷卻期、建立、表單欄位、異動記錄與手動主機名稱觀測：與 AI 工具 allocate_ip 共用（services/address_write）
    try:
        obj = await create_address_checked(session, user=user, subnet=subnet, payload=payload)
    except IPNotInSubnet as exc:
        raise HTTPException(status_code=400, detail=detail_of(exc, "ip_not_in_subnet")) from exc
    except IPAlreadyExists as exc:
        raise HTTPException(status_code=409, detail=detail_of(exc, "ip_already_exists")) from exc
    except CustomFieldError as exc:
        raise HTTPException(status_code=400, detail=detail_of(exc, "custom_field_error")) from exc

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="ip_address",
        object_id=str(obj.id),
        action="create",
        diff={"after": payload.model_dump(mode="json")},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    out = IPAddressRead.model_validate(obj); out.mac_vendor = await vendor_for_mac(session, obj.mac); return out


@router.post("/first_free", response_model=IPAddressRead, status_code=status.HTTP_201_CREATED)
async def allocate_first_free_address(
    payload: IPAddressAllocate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> IPAddressRead:
    subnet = await _require_subnet_perm(session, user, payload.subnet_id, "write")

    try:
        obj = await allocate_first_free(
            session,
            subnet=subnet,
            hostname=payload.hostname,
            description=payload.description,
            mac=payload.mac,
            state=payload.state,
        )
    except SubnetFull as exc:
        raise HTTPException(status_code=409, detail=detail_of(exc, "subnet_full")) from exc

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="ip_address",
        object_id=str(obj.id),
        action="allocate_first_free",
        diff={"after": {"subnet_id": str(subnet.id), "ip": str(obj.ip), "hostname": obj.hostname}},
        request_id=getattr(request.state, "request_id", None),
    )
    await log_change(session, ip=obj, event_type="created",
                     source="manual", actor_user_id=str(user.id),
                     note="first_free 配發")
    await seed_observation(session, ip=obj, source="manual", hostname=payload.hostname)
    await session.commit()
    await session.refresh(obj)
    out = IPAddressRead.model_validate(obj); out.mac_vendor = await vendor_for_mac(session, obj.mac); return out


@router.patch("/{address_id}", response_model=IPAddressRead)
async def update_address(
    address_id: uuid.UUID,
    payload: IPAddressUpdate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> IPAddressRead:
    obj = await session.get(IPAddress, address_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Address not found")
    await _require_subnet_perm(session, user, obj.subnet_id, "write")

    # 主機名稱走觀測＋優先序、MAC 來源、探測同步、守門、逐欄異動記錄：與 AI 工具 update_ip 共用
    try:
        before, changes = await update_address_checked(
            session, user=user, obj=obj, changes=payload.model_dump(exclude_unset=True))
    except CustomFieldError as exc:
        raise HTTPException(status_code=400, detail=detail_of(exc, "custom_field_error")) from exc

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="ip_address",
        object_id=str(obj.id),
        action="update",
        diff={"before": before, "changes": changes},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    out = IPAddressRead.model_validate(obj); out.mac_vendor = await vendor_for_mac(session, obj.mac)
    # 與 get_address 一致：算 ssh/rdp/vnc_available，否則存檔後前端拿不到、按鈕要等重整才出現
    from app.services.permission import can_use_rdp, can_use_sftp, can_use_ssh, can_use_vnc
    out.ssh_available = await can_use_ssh(session, user=user, ip=obj)
    out.sftp_available = await can_use_sftp(session, user=user, ip=obj)
    out.rdp_available = await can_use_rdp(session, user=user, ip=obj)
    out.vnc_available = await can_use_vnc(session, user=user, ip=obj)
    await _fill_pve_console(session, obj, out, user)
    return out


@router.delete("/{address_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_address(
    address_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    obj = await session.get(IPAddress, address_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Address not found")
    await _require_subnet_perm(session, user, obj.subnet_id, "admin")
    await _delete_ip(session, obj, user=user, request=request, bulk=False)
    await session.commit()


async def _delete_ip(session: AsyncSession, obj: IPAddress, *, user: Any, request: Request,
                     bulk: bool, via: str | None = None) -> None:
    """刪除一筆 IP 的完整步驟：稽核、異動記錄、冷卻期、刪除。單筆與批次刪除共用 ——
    以前批次刪除只寫稽核就刪，沒有進冷卻期、異動記錄也沒有「已刪除」（2026-09-28）。
    不 commit，交易邊界由呼叫端決定。"""
    diff: dict[str, Any] = {"before": {"ip": str(obj.ip), "subnet_id": str(obj.subnet_id),
                                       "hostname": obj.hostname}}
    if bulk:
        diff["bulk"] = True
    if via:
        diff["via"] = via
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="ip_address",
        object_id=str(obj.id),
        action="delete",
        diff=diff,
        request_id=getattr(request.state, "request_id", None),
    )
    # feature B：刪除前記一筆（ip_id 之後會被 SET NULL，但 ip_text 快照保留）
    await log_change(session, ip=obj, event_type="deleted",
                     source="manual", actor_user_id=str(user.id),
                     old=str(obj.ip))
    # 釋放 → 進冷卻期。外面還有 DNS 快取／防火牆規則／ACL 指著這個位址，
    # 立刻配給別人會造成最難查的那種故障（見 services/ip_lifecycle）。
    await ip_lifecycle.start_cooldown(session, ip=obj, actor_user_id=user.id,
                                      reason="deleted")
    await session.delete(obj)


class CooldownClearIn(StrictModel):
    reason: Annotated[str | None, Field(max_length=500)] = None


@router.get("/cooldowns/{subnet_id}")
async def list_cooldowns(
    subnet_id: uuid.UUID,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """這個子網路裡仍在冷卻期的位址（剛釋放、暫時不配發）。"""
    await _require_subnet_perm(session, user, subnet_id, "read")
    rows = await ip_lifecycle.active_cooldowns(session, subnet_id)
    return {"items": [{
        "ip": ip,
        "released_at": r.released_at.isoformat() if r.released_at else None,
        "until": r.until.isoformat() if r.until else None,
        "previous_hostname": r.previous_hostname,
        "previous_mac": r.previous_mac,
        "reason": r.reason,
    } for ip, r in sorted(rows.items())], "count": len(rows)}


@router.post("/cooldowns/{subnet_id}/{ip}/clear")
async def clear_cooldown(
    subnet_id: uuid.UUID, ip: str, payload: CooldownClearIn,
    user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """提前解除冷卻。**紀錄不刪**，留下誰／何時／為什麼。"""
    await _require_subnet_perm(session, user, subnet_id, "admin")
    row = await ip_lifecycle.clear_cooldown(
        session, subnet_id=subnet_id, ip=ip,
        actor_user_id=user.id, reason=payload.reason,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Not in cooldown")
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="ip_address", object_id=None, action="cooldown_clear",
        diff={"subnet_id": str(subnet_id), "ip": ip, "reason": payload.reason or ""},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return {"ok": True, "ip": ip}


class BulkDeletePayload(StrictModel):
    ids: list[uuid.UUID]


@router.post("/bulk-delete", status_code=status.HTTP_200_OK)
async def bulk_delete(
    payload: BulkDeletePayload,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """批次刪除 IP；逐筆檢查 subnet admin 權限，失敗的計入 failed。"""
    if not payload.ids:
        return {"deleted": 0, "failed": 0, "errors": []}
    if len(payload.ids) > 5000:
        raise HTTPException(400, detail="too many ids in one batch (max 5000)")

    deleted = 0
    errors: list[dict[str, str]] = []

    for aid in payload.ids:
        obj = await session.get(IPAddress, aid)
        if obj is None:
            errors.append({"id": str(aid), "error": "not_found"})
            continue
        try:
            await _require_subnet_perm(session, user, obj.subnet_id, "admin")
        except HTTPException:
            errors.append({"id": str(aid), "error": "no_permission"})
            continue
        await _delete_ip(session, obj, user=user, request=request, bulk=True)
        deleted += 1

    await session.commit()
    return {"deleted": deleted, "failed": len(errors), "errors": errors[:50]}


_BULK_STATES = {"active", "reserved", "offline", "dhcp", "used"}


class BulkStatePayload(StrictModel):
    ids: list[uuid.UUID]
    state: str


@router.post("/bulk-state", status_code=status.HTTP_200_OK)
async def bulk_set_state(
    payload: BulkStatePayload,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """批次設定 IP 狀態（如把失聯 IP 標為 reserved 待回收）；逐筆檢查 subnet admin 權限。"""
    if payload.state not in _BULK_STATES:
        raise HTTPException(422, detail=f"invalid state (allowed: {sorted(_BULK_STATES)})")
    if not payload.ids:
        return {"updated": 0, "failed": 0, "errors": []}
    if len(payload.ids) > 5000:
        raise HTTPException(400, detail="too many ids in one batch (max 5000)")

    updated = 0
    errors: list[dict[str, str]] = []
    actor_ip = request.client.host if request.client else None
    actor_ua = request.headers.get("user-agent")
    request_id = getattr(request.state, "request_id", None)

    for aid in payload.ids:
        obj = await session.get(IPAddress, aid)
        if obj is None:
            errors.append({"id": str(aid), "error": "not_found"})
            continue
        try:
            await _require_subnet_perm(session, user, obj.subnet_id, "admin")
        except HTTPException:
            errors.append({"id": str(aid), "error": "no_permission"})
            continue
        if obj.state == payload.state:
            updated += 1
            continue
        before = obj.state
        obj.state = payload.state
        await append_audit(
            session,
            actor_user_id=str(user.id),
            actor_ip=actor_ip,
            actor_user_agent=actor_ua,
            object_type="ip_address",
            object_id=str(obj.id),
            action="update",
            diff={"before": {"state": before}, "after": {"state": payload.state}, "bulk": True},
            request_id=request_id,
        )
        updated += 1

    await session.commit()
    return {"updated": updated, "failed": len(errors), "errors": errors[:50]}


class BulkCreateDevicesPayload(StrictModel):
    ids: Annotated[list[uuid.UUID], Field(max_length=2000)]


@router.post("/bulk-create-devices", status_code=status.HTTP_200_OK)
async def bulk_create_devices(
    payload: BulkCreateDevicesPayload,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """批次建立裝置（使用者 2026-10-20）：勾選的 IP 已經有裝置就跳過；沒有的，對得到唯一一台既有裝置
    （主機名稱／MAC／主要 IP，同單筆的「建議裝置」）就關聯過去，否則建一台再關聯。同一批裡主機名稱相同的共用一台。
    建立裝置是管理員限定（與 POST /devices、單筆套用同一條線）。"""
    from app.models.device import Device
    from app.services.device_type_auto import refresh_auto_types

    if not user.is_admin:
        raise HTTPException(status_code=403, detail=ui_detail("addr_device_create_admin_only", "只有管理員能建立裝置"))
    actor = {"actor_user_id": str(user.id), "actor_ip": request.client.host if request.client else None,
             "actor_user_agent": request.headers.get("user-agent"),
             "request_id": getattr(request.state, "request_id", None)}
    created_ids: list[uuid.UUID] = []
    by_host: dict[str, Any] = {}             # 這一批裡主機名稱 → 剛建的裝置
    created = linked_existing = skipped = 0
    errors: list[dict[str, str]] = []
    devices: dict[uuid.UUID, dict[str, Any]] = {}
    for aid in dict.fromkeys(payload.ids):
        obj = await session.get(IPAddress, aid)
        if obj is None:
            errors.append({"id": str(aid), "error": "not_found"})
            continue
        if obj.device_id is not None:
            skipped += 1
            continue
        hostname = (obj.hostname or "").strip()
        key = hostname.lower()
        device = by_host.get(key) if key else None
        how = "batch"
        if device is None:
            hit = await _match_existing_device(session, obj)
            if hit is not None:
                device, how = await session.get(Device, hit[0]), "existing"
        if device is None:
            name = _first_label(hostname) or str(obj.ip).split("/")[0]
            device = Device(name=name[:255], type="other", fqdn=hostname if "." in hostname else None)
            session.add(device)
            await session.flush()
            created += 1
            created_ids.append(device.id)
            how = "created"
            await append_audit(session, object_type="device", object_id=str(device.id), action="create",
                               diff={"name": device.name, "from_ip": str(obj.ip), "bulk": True}, **actor)
        if key and how != "existing":
            by_host[key] = device
        if how == "existing":
            linked_existing += 1
        await log_change(session, ip=obj, event_type="edited", field="device_id", old=None, new=str(device.id),
                         source="user", actor_user_id=user.id, note="bulk create devices")
        obj.device_id = device.id
        await append_audit(session, object_type="ip_address", object_id=str(obj.id), action="update",
                           diff={"device_id": str(device.id), "bulk": True, "how": how}, **actor)
        d = devices.setdefault(device.id, {"id": str(device.id), "name": device.name, "how": how, "ips": []})
        d["ips"].append(str(obj.ip).split("/")[0])
    if created_ids:
        # 剛從 IP 建出來的裝置：照 IP 的作業系統證據先判斷類型（同單筆套用）
        await session.flush()
        await refresh_auto_types(session, created_ids)
    await session.commit()
    return {"created": created, "linked_existing": linked_existing, "skipped": skipped,
            "failed": len(errors), "errors": errors[:50], "devices": list(devices.values())[:200]}


class NotifyStalePayload(StrictModel):
    subnet_id: uuid.UUID
    ids: Annotated[list[uuid.UUID], Field(max_length=65536)]
    days: Annotated[int, Field(ge=0, le=36500)]


@router.post("/notify-stale", status_code=status.HTTP_200_OK)
async def notify_stale(
    payload: NotifyStalePayload,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """對選定的失聯 IP 發提醒：在通知中心推一則摘要給所有管理者。"""
    from app.services.notification import push_notification
    from app.services.system_config import get_notification_matrix

    # 發提醒會推給所有管理員、也會送到外部通知管道：要寫入權（以前唯讀就能發）；
    # 筆數只算真的在這個子網路裡的 IP，不照送來的清單長度（2026-10-07 稽核）
    await _require_subnet_perm(session, user, payload.subnet_id, "write")
    n = int(await session.scalar(select(func.count()).select_from(IPAddress).where(
        IPAddress.subnet_id == payload.subnet_id, in_values(IPAddress.id, payload.ids))) or 0) if payload.ids else 0
    if n == 0:
        return {"notified_admins": 0, "ip_count": 0}

    subnet = await session.get(Subnet, payload.subnet_id)
    cidr = str(subnet.cidr) if subnet else str(payload.subnet_id)

    admins = list(
        (await session.execute(select(User).where(User.is_admin.is_(True)))).scalars().all()
    )
    title = f"失聯 IP 提醒：{cidr}"
    body = f"子網路 {cidr} 有 {n} 個 IP 失聯超過 {payload.days} 天（由 {user.username} 提出）。"
    _gp = {"cidr": cidr, "n": n, "days": payload.days, "user": user.username}
    # 通知發送設定裡可以關掉（`ip.stale`）。原本是直接推 —— 收得到卻關不掉。
    ch = (await get_notification_matrix(session)).get(
        "ip.stale", {"in_app": True, "email": False})
    from app.services.system_config import notify_wanted
    if not notify_wanted(ch):
        return {"notified_admins": 0, "ip_count": n}

    for admin in admins if ch.get("in_app") else []:
        await push_notification(
            session,
            user_id=admin.id,
            title=title,
            body=body,
            severity="warning",
            link=f"/subnets/{payload.subnet_id}",
            object_type="subnet",
            object_id=payload.subnet_id,
            title_key="notif.ghost_reminder",
            body_key="notif.ghost_reminder_body",
            params=_gp,
        )
    from app.services.notify_channels import broadcast_channels
    await broadcast_channels(session, subject=title, text=body, link=f"/subnets/{payload.subnet_id}", emoji="⏰",
                             event="ip.stale")
    await session.commit()
    return {"notified_admins": len(admins) if ch.get("in_app") else 0, "ip_count": n}


# ─────────────────── CSV 匯出 / 匯入 ───────────────────


@router.post("/import")
async def import_csv(
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    subnet_id: Annotated[uuid.UUID, Form()],
    file: Annotated[UploadFile, File()],
    dry_run: Annotated[bool, Form()] = False,
) -> dict[str, object]:
    """匯入 CSV 至指定 subnet。

    比 phpIPAM 改進：
    - header-driven（欄位順序不重要，只要 header 含 ip）
    - 容忍 BOM、自動偵測 delimiter
    - dry_run=true 只回傳預覽與錯誤，不寫 DB
    - idempotent：已存在的 (subnet_id, ip) 自動 skip
    """
    subnet = await _require_subnet_perm(session, user, subnet_id, "write")

    # 檔案大小限制（A04）：16 MB。實際匯入走背景作業，大檔不卡請求。
    raw = await file.read()
    if len(raw) > 16_777_216:
        raise HTTPException(413, detail="CSV file too large (max 16 MB)")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(400, detail=f"CSV must be UTF-8: {exc}") from exc

    # dry-run：同步回預覽（不寫 DB），讓 UI 立刻看結果
    if dry_run:
        result = await import_addresses_csv(
            session, subnet=subnet, csv_text=text, dry_run=True,
        )
        return {"dry_run": True, **result.to_dict()}

    # 實際匯入：走背景作業，回 task_id；前端到「作業」頁看進度。
    from app.models.subnet import Subnet
    from app.services.background_tasks import spawn_task

    csv_text = text
    subnet_id_val = subnet.id
    # subnet.cidr 是 asyncpg 回傳的 IPv4Network 物件（CIDR 欄位），不是 str；
    # spawn_task 的 target_label 是 VARCHAR，直接塞會 asyncpg DataError（CSV 實際匯入 500）。
    subnet_label = str(subnet.cidr)
    filename = file.filename
    actor_user_id_str = str(user.id)
    actor_ip = request.client.host if request.client else None
    actor_ua = request.headers.get("user-agent")
    request_id = getattr(request.state, "request_id", None)

    async def _runner(sess: AsyncSession, _task) -> dict[str, object]:  # type: ignore[no-untyped-def]
        sub = await sess.get(Subnet, subnet_id_val)
        if sub is None:
            raise RuntimeError("subnet no longer exists")
        result = await import_addresses_csv(sess, subnet=sub, csv_text=csv_text, dry_run=False)
        await append_audit(
            sess,
            actor_user_id=actor_user_id_str,
            actor_ip=actor_ip, actor_user_agent=actor_ua,
            object_type="subnet", object_id=str(subnet_id_val),
            action="ip_csv_import",
            diff={
                "inserted": result.inserted, "skipped": result.skipped,
                "errored": result.errored, "filename": filename,
            },
            request_id=request_id,
        )
        await sess.commit()
        return {"dry_run": False, **result.to_dict()}

    task = await spawn_task(
        session=session,
        kind="ip.csv_import",
        target_type="subnet",
        target_id=subnet_id_val,
        target_label=subnet_label,
        actor_user_id=user.id,
        runner=_runner,
    )
    return {"task_id": str(task.id), "status": task.status, "dry_run": False}


@router.get("/{address_id}/uptime")
async def get_address_uptime(
    address_id: uuid.UUID,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    days: int = Query(90, ge=7, le=365),
) -> dict[str, Any]:
    """單一 IP 的每日存活狀態（status page 式長條圖用）。

    重建規則與「沒資料不得算成正常」等原則見 `app/services/uptime.py`。
    """
    obj = await session.get(IPAddress, address_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Address not found")
    await _require_subnet_perm(session, user, obj.subnet_id, "read")

    from app.services.uptime import uptime_for_ips
    return await uptime_for_ips(session, [address_id], days=days)


class _UptimeBatchIn(StrictModel):
    # 上限 30：儀表板區塊的設計上限，也避免一次算太多天 × 太多 IP
    ip_ids: Annotated[list[uuid.UUID], Field(max_length=30)]
    days: Annotated[int, Field(ge=7, le=365)] = 90


@router.post("/uptime/batch")
async def uptime_batch_endpoint(
    payload: _UptimeBatchIn,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """一次取多個 IP 的每日存活狀態，每個 IP 各一條（儀表板區塊用）。

    A01：只回使用者看得見的 IP —— 不可因為對方把 id 塞進清單就吐出來。
    看不到的直接略過（不報錯），這樣儀表板不會因為權限變動就整個壞掉。
    """
    from app.services.uptime import uptime_batch

    if not payload.ip_ids:
        return {"items": []}

    # 先縮到可見子網路，再取這些 IP —— 與清單端點同一套規則
    vis = await visible_ids(session, user=user, object_type="subnet", required="read")
    stmt = select(IPAddress.id).where(in_values(IPAddress.id, payload.ip_ids))
    if vis is not None:
        if not vis:
            return {"items": []}
        stmt = stmt.where(in_values(IPAddress.subnet_id, vis))
    allowed = set((await session.execute(stmt)).scalars().all())
    ordered = [i for i in payload.ip_ids if i in allowed]   # 保留使用者排的順序

    return {"items": await uptime_batch(session, ordered, days=payload.days)}


@router.get("/{address_id}/console-route")
async def address_console_route(
    address_id: uuid.UUID,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """主控台會走哪條路（直連／跳板／掃描代理）、設定在哪裡、現在走不走得通 —— 連線表單顯示用。"""
    obj = await session.get(IPAddress, address_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Address not found")
    await _require_subnet_perm(session, user, obj.subnet_id, "read")
    from app.services.console_route import describe_route
    out = await describe_route(session, obj)
    out["subnet_id"] = str(obj.subnet_id) if obj.subnet_id else None   # 「變更」連結到子網路頁用
    return out
