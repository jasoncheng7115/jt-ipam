"""IPAddress CRUD + first_free 配發。"""

from __future__ import annotations

import uuid
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
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser
from app.core.audit import append_audit
from app.core.db import get_session
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
from app.services.address import (
    IPAlreadyExists,
    IPNotInSubnet,
    SubnetFull,
    allocate_first_free,
    create_ip,
)
from app.services.csv_io import (
    export_addresses_csv,
    import_addresses_csv,
)
from app.services.custom_field import CustomFieldError, validate_custom_fields
from app.services.hostname import (
    apply_observation,
    recompute_effective,
    seed_observation,
)
from app.services.ip_history import log_change, log_field_diffs
from app.services.oui import mac_prefix, vendor_for_mac, vendor_map
from app.services.permission import (
    filter_visible,
    get_object_permission,
    has_permission,
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
    _SORT_COLS = {
        "ip": IPAddress.ip, "hostname": IPAddress.hostname, "mac": IPAddress.mac,
        "state": IPAddress.state, "owner": IPAddress.owner,
        "switch_port": IPAddress.switch_port, "note": IPAddress.note,
        "discovery_source": IPAddress.discovery_source,
    }
    sort_col = _SORT_COLS.get(sort or "", IPAddress.ip)
    stmt = stmt.order_by(sort_col.desc() if order == "desc" else sort_col.asc())
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
                select(Subnet.id, Subnet.scan_enabled).where(Subnet.id.in_(scan_ids))
            )).all()
        )
    # 批次帶上關聯裝置名稱（清單「裝置」欄用）
    dev_ids = list({r.device_id for r in rows if r.device_id})
    dev_map: dict[uuid.UUID, str] = {}
    if dev_ids:
        from app.models.device import Device
        dev_map = dict(
            (await session.execute(
                select(Device.id, Device.name).where(Device.id.in_(dev_ids))
            )).all()
        )
    for it, r in zip(items, rows, strict=False):
        p = mac_prefix(r.mac)
        if p:
            it.mac_vendor = vmap.get(p)
        it.subnet_scan_enabled = scan_map.get(r.subnet_id)
        if r.device_id:
            it.device_name = dev_map.get(r.device_id)
    total = int(await session.scalar(count_stmt) or 0)
    return Paginated[IPAddressRead](items=items, total=total, page=page, page_size=page_size)


# 注意：此路由必須宣告在 /{address_id} 之前，否則 "export.csv" 會被當成 address_id（UUID 驗證 422）
@router.get("/export.csv")
async def export_csv(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    subnet_id: uuid.UUID = Query(..., description="必要：限定要匯出的 subnet"),
) -> Response:
    """以 CSV 匯出某個 subnet 的所有 IP（須對該 subnet 有 read 權限）。Excel 友善：UTF-8 + BOM。"""
    await _require_subnet_perm(session, user, subnet_id, "read")
    rows = list((await session.execute(
        select(IPAddress).where(IPAddress.subnet_id == subnet_id).order_by(IPAddress.ip)
    )).scalars().all())
    body = export_addresses_csv(rows)
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="addresses-{subnet_id}.csv"',
            "Cache-Control": "no-store",
        },
    )


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
    # 算出此 IP 實際會被執行的探測（子網路要跑 − IP 略過 ∩ 代理能力）給詳情頁顯示
    out.effective_probes = await _effective_probes_for(session, obj)
    # OS 依來源優先序（scanner/librenms/wazuh）解析有效值 + 來源
    from app.services.os_precedence import effective_os
    _os = await effective_os(session, obj)
    out.os_guess = _os["os_guess"]; out.os_family = _os["os_family"]; out.os_source = _os["os_source"]
    return out


@router.get("/{address_id}/relations")
async def get_address_relations(
    address_id: uuid.UUID,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """IP 的上下關係鏈：區段 → 子網路 → 位址 → 裝置 → 機櫃 → 機房。
    每個節點 {type,id,label,sub}；缺的環節省略。前端橫向串成關係圖。"""
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

    async def _device_tail(dev: Device, *, sub: str | None = None) -> None:
        """把 device → rack → 機房 接到鏈尾。"""
        chain.append({"type": "device", "id": str(dev.id), "label": dev.name, "sub": sub})
        if dev.rack_id:
            rk = await session.get(Rack, dev.rack_id)
            if rk is not None:
                chain.append({"type": "rack", "id": str(rk.id), "label": rk.name})
        if dev.location_id:
            loc = await session.get(Location, dev.location_id)
            if loc is not None:
                chain.append({"type": "location", "id": str(loc.id), "label": loc.name})

    if obj.device_id:
        dev = await session.get(Device, obj.device_id)
        if dev is not None:
            await _device_tail(dev)
    else:
        # 沒有直接關聯裝置 → 若這個 IP 是某台 VM（Proxmox 整合）的位址，就補上
        # 「虛擬機」這一層；再透過 VM 的 PVE node 串到實體裝置 → 機櫃 → 機房。
        # 不是 VM 就沒有這一層。
        from app.models.virt import VirtualMachine, VMInterface
        ipstr = str(obj.ip).split("/")[0]
        # 多重比對找出這個 IP 屬於哪台 VM（Proxmox 同步未必會設 primary_ip_id）：
        # 1) VM 主要 IP 直接指向 → 2) VM 介面 IP 相符 → 3) VM 名稱 = 主機名稱
        vm = (await session.execute(
            select(VirtualMachine).where(VirtualMachine.primary_ip_id == obj.id).limit(1)
        )).scalar_one_or_none()
        if vm is None:
            vm = (await session.execute(
                select(VirtualMachine)
                .join(VMInterface, VMInterface.vm_id == VirtualMachine.id)
                .where(func.host(VMInterface.primary_ip) == ipstr).limit(1)
            )).scalar_one_or_none()
        if vm is None and obj.hostname:
            vm = (await session.execute(
                select(VirtualMachine)
                .where(func.lower(VirtualMachine.name) == obj.hostname.lower()).limit(1)
            )).scalar_one_or_none()
        # 只連「同單位」的 VM：IP 所屬單位（取自子網路）與 VM 叢集所屬單位都有設定且不同 → 不連
        if vm is not None and subnet is not None and subnet.customer_id is not None:
            from app.models.virt import VirtCluster
            cluster = await session.get(VirtCluster, vm.cluster_id)
            if cluster is not None and cluster.customer_id is not None \
                    and cluster.customer_id != subnet.customer_id:
                vm = None
        if vm is not None:
            chain.append({"type": "vm", "id": str(vm.id), "label": vm.name, "sub": vm.node})
            node_dev: Device | None = None
            if vm.device_id:
                node_dev = await session.get(Device, vm.device_id)
            elif vm.node:
                # PVE node host 名稱 → 對到 jt-ipam 的實體裝置（比對 name，再比對 fqdn）
                node_dev = (await session.execute(
                    select(Device).where(func.lower(Device.name) == vm.node.lower()).limit(1)
                )).scalar_one_or_none()
                if node_dev is None:
                    node_dev = (await session.execute(
                        select(Device).where(func.lower(Device.fqdn) == vm.node.lower()).limit(1)
                    )).scalar_one_or_none()
            if node_dev is not None:
                await _device_tail(node_dev, sub="PVE node")
    return {"chain": chain}


@router.get("/{address_id}/history", response_model=list[IPChangeLogRead])
async def get_address_history(
    address_id: uuid.UUID,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> list[IPChangeLogRead]:
    """單一 IP 的異動記錄（feature B），時間倒序；offset 分頁（前端「載入更多」）。"""
    obj = await session.get(IPAddress, address_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Address not found")
    await _require_subnet_perm(session, user, obj.subnet_id, "read")

    rows = list((await session.execute(
        select(IPChangeLog)
        .where(IPChangeLog.ip_id == address_id)
        .order_by(IPChangeLog.created_at.desc())
        .offset(offset)
        .limit(limit)
    )).scalars().all())

    actor_ids = list({r.actor_user_id for r in rows if r.actor_user_id is not None})
    name_map: dict[uuid.UUID, str] = {}
    if actor_ids:
        for uid, uname in (await session.execute(
            select(User.id, User.username).where(User.id.in_(actor_ids))
        )).all():
            name_map[uid] = uname

    out: list[IPChangeLogRead] = []
    for r in rows:
        m = IPChangeLogRead.model_validate(r)
        m.actor_username = name_map.get(r.actor_user_id) if r.actor_user_id else None
        out.append(m)
    return out


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
        return await switch_port_for_ip(session, user=user, ip=str(obj.ip).split("/")[0])
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
    rows = (await session.execute(
        select(IPHostnameObservation)
        .where(IPHostnameObservation.ip_id == address_id)
    )).scalars().all()
    return {
        "effective": obj.hostname,
        "pin": obj.hostname_source_pin,
        "order": await get_precedence(session),
        "observations": [
            {"source": r.source, "hostname": r.hostname,
             "observed_at": r.observed_at.isoformat()}
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
    from app.services.hostname import recompute_effective

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

    try:
        obj = await create_ip(
            session,
            subnet=subnet,
            ip=payload.ip,
            hostname=payload.hostname,
            description=payload.description,
            mac=payload.mac,
            state=payload.state,
        )
    except IPNotInSubnet as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IPAlreadyExists as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # 應用後續欄位
    try:
        cf = await validate_custom_fields(
            session, object_type="ip", payload=payload.custom_fields
        )
    except CustomFieldError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    obj.owner = payload.owner
    obj.device_id = payload.device_id
    obj.switch_port = payload.switch_port
    obj.exclude_from_ping = payload.exclude_from_ping
    obj.ptr_ignore = payload.ptr_ignore
    obj.note = payload.note
    obj.custom_fields = cf or None

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
    await log_change(session, ip=obj, event_type="created",
                     source="manual", actor_user_id=str(user.id))
    # feature A：把建立時填的 hostname 記成 manual 觀測
    await seed_observation(session, ip=obj, source="manual", hostname=payload.hostname)
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
        raise HTTPException(status_code=409, detail=str(exc)) from exc

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

    before = {
        "hostname": obj.hostname,
        "state": obj.state,
        "mac": str(obj.mac) if obj.mac else None,
        "description": obj.description,
        "owner": obj.owner,
        "switch_port": obj.switch_port,
        "device_id": str(obj.device_id) if obj.device_id else None,
        "note": obj.note,
        "customer_id": str(obj.customer_id) if obj.customer_id else None,
    }
    changes = payload.model_dump(exclude_unset=True)
    if "custom_fields" in changes:
        try:
            changes["custom_fields"] = await validate_custom_fields(
                session, object_type="ip", payload=changes["custom_fields"]
            ) or None
        except CustomFieldError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # feature A：hostname 不直接設，改走 observation + 優先序解析
    _UNSET = object()
    hostname_change = changes.pop("hostname", _UNSET)
    pin_changed = "hostname_source_pin" in changes
    if pin_changed and not changes["hostname_source_pin"]:
        changes["hostname_source_pin"] = None  # "" → 取消 pin

    for key, value in changes.items():
        setattr(obj, key, value)

    # MAC 與 hostname 同屬「多來源優先序」欄位：人工編輯的 MAC 要標記 mac_source="manual"
    # （ARP 優先序中 manual rank 最高），否則下一次掃描/ARP 同步會用 scanner 等來源把它蓋掉。
    # 清空 MAC 時一併清掉來源。
    if "mac" in changes:
        if obj.mac:
            obj.mac_source = "manual"
        else:
            obj.mac_source = None

    # 同步 excluded_probes ⇄ exclude_from_ping：icmp 視為同一件事，保留既有「不掃 ping」行為
    if "excluded_probes" in changes or "exclude_from_ping" in changes:
        from app.core.scan_probes import normalize_probes as _norm_probes
        if "excluded_probes" in changes:
            obj.excluded_probes = _norm_probes(obj.excluded_probes)
            obj.exclude_from_ping = "icmp" in obj.excluded_probes
        if "exclude_from_ping" in changes:
            s = set(obj.excluded_probes or [])
            s.add("icmp") if obj.exclude_from_ping else s.discard("icmp")
            obj.excluded_probes = _norm_probes(list(s))

    # 把這個 IP 指派給某裝置 → 若該裝置還沒設主要 IP，順手補上（雙向連結方便）
    if changes.get("device_id"):
        from app.models.device import Device as _Device
        dev = await session.get(_Device, changes["device_id"])
        if dev is not None and dev.primary_ip_id is None:
            dev.primary_ip_id = obj.id

    # feature B：逐欄記錄人為編輯（hostname 改走 apply_observation，這裡不含它）
    await log_field_diffs(
        session, ip=obj, before=before, changes=changes,
        source="manual", actor_user_id=str(user.id),
    )

    # feature A：套用 hostname 觀測 / pin 變更後重算有效 hostname（內含 hostname_changed 記錄）
    if hostname_change is not _UNSET:
        await apply_observation(
            session, ip=obj, source="manual",
            hostname=hostname_change, actor_user_id=str(user.id),
        )
    elif pin_changed:
        await recompute_effective(
            session, ip=obj, source="manual", actor_user_id=str(user.id),
        )

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
    out = IPAddressRead.model_validate(obj); out.mac_vendor = await vendor_for_mac(session, obj.mac); return out


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

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="ip_address",
        object_id=str(obj.id),
        action="delete",
        diff={"before": {"ip": str(obj.ip), "subnet_id": str(obj.subnet_id), "hostname": obj.hostname}},
        request_id=getattr(request.state, "request_id", None),
    )
    # feature B：刪除前記一筆（ip_id 之後會被 SET NULL，但 ip_text 快照保留）
    await log_change(session, ip=obj, event_type="deleted",
                     source="manual", actor_user_id=str(user.id),
                     old=str(obj.ip))
    await session.delete(obj)
    await session.commit()


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
        await append_audit(
            session,
            actor_user_id=str(user.id),
            actor_ip=actor_ip,
            actor_user_agent=actor_ua,
            object_type="ip_address",
            object_id=str(obj.id),
            action="delete",
            diff={"before": {"ip": str(obj.ip), "subnet_id": str(obj.subnet_id),
                              "hostname": obj.hostname}, "bulk": True},
            request_id=request_id,
        )
        await session.delete(obj)
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


class NotifyStalePayload(StrictModel):
    subnet_id: uuid.UUID
    ids: list[uuid.UUID]
    days: int


@router.post("/notify-stale", status_code=status.HTTP_200_OK)
async def notify_stale(
    payload: NotifyStalePayload,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """對選定的失聯 IP 發提醒：在通知中心推一則摘要給所有管理者。"""
    from app.services.notification import push_notification

    await _require_subnet_perm(session, user, payload.subnet_id, "read")
    n = len(payload.ids)
    if n == 0:
        return {"notified_admins": 0, "ip_count": 0}

    subnet = await session.get(Subnet, payload.subnet_id)
    cidr = str(subnet.cidr) if subnet else str(payload.subnet_id)

    admins = list(
        (await session.execute(select(User).where(User.is_admin.is_(True)))).scalars().all()
    )
    title = f"失聯 IP 提醒：{cidr}"
    body = f"子網路 {cidr} 有 {n} 個 IP 失聯超過 {payload.days} 天（由 {user.username} 提出）。"
    for admin in admins:
        await push_notification(
            session,
            user_id=admin.id,
            title=title,
            body=body,
            severity="warning",
            link=f"/subnets/{payload.subnet_id}",
            object_type="subnet",
            object_id=payload.subnet_id,
        )
    await session.commit()
    return {"notified_admins": len(admins), "ip_count": n}


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
