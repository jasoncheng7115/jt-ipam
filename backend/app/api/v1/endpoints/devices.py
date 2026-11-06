"""Device endpoints。"""

from __future__ import annotations

import ipaddress
import re
import uuid
from typing import Annotated, Any
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import String, func, or_, select
from sqlalchemy import false as sa_false
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin, require_object_perm
from app.core.audit import append_audit
from app.core.db import get_session
from app.core.os_fingerprint import wazuh_os_display
from app.core.sqlin import in_values
from app.core.ui_error import detail_of, ui_detail
from app.models.device import Device
from app.models.librenms import LibreNMSDevice
from app.models.vlan import VLAN, DeviceVLAN
from app.schemas.base import Paginated, StrictModel
from app.schemas.device import DeviceCreate, DeviceRead, DeviceUpdate
from app.services.custom_field import CustomFieldError, validate_custom_fields

#: BMC 的主機名稱（同 device_kind_knowledge 的判讀）：關係鏈沒設主要 IP 時排在後面
_BMC_HOST = re.compile(r"\b(?:idrac|ilo|ipmi|bmc|drac|xcc|imm|cimc|irmc)\b", re.I)

router = APIRouter(prefix="/devices", tags=["devices"])


def _web_base(api_url: str | None) -> str | None:
    """把整合的 API 網址整理成 web 主控台的基底：去掉 /api2/json、/api/v0、/api 與結尾斜線。"""
    if not api_url:
        return None
    base = api_url.rstrip("/")
    for suf in ("/api2/json", "/api/v0", "/api"):
        if base.endswith(suf):
            base = base[: -len(suf)]
            break
    return base.rstrip("/") or None


def _drop_port(url: str | None) -> str | None:
    """去掉網址的埠（Wazuh：API 在 :55000，儀表板在同主機的 443）。"""
    if not url:
        return None
    parts = urlsplit(url)
    host = parts.hostname or ""
    if not host:
        return None
    netloc = f"[{host}]" if ":" in host else host
    return urlunsplit((parts.scheme or "https", netloc, "", "", "")).rstrip("/")


class DeviceVLANRead(StrictModel):
    vlan_id: uuid.UUID
    number: int
    name: str
    source: str
    last_seen_at: Any


@router.get(
    "/{device_id}/librenms",
    dependencies=[Depends(require_object_perm("device", "read", path_param="device_id"))],
)
async def get_device_librenms(
    device_id: uuid.UUID,
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any] | None:
    """連結到此裝置的 LibreNMS 資料（os/hardware/serial/version/uptime/status）。"""
    if await session.get(Device, device_id) is None:
        raise HTTPException(404, detail="Device not found")
    r = (await session.execute(
        select(LibreNMSDevice).where(LibreNMSDevice.jt_ipam_device_id == device_id).limit(1)
    )).scalar_one_or_none()
    if r is None:
        return None
    from app.models.librenms import LibreNMSInstance
    inst = await session.get(LibreNMSInstance, r.instance_id)
    web = _web_base(inst.api_url) if inst else None
    url = f"{web}/device/device={r.legacy_device_id}" if web else None
    return {
        "hostname": r.hostname, "sysname": r.sysname, "primary_ip": str(r.primary_ip) if r.primary_ip else None,
        "hardware": r.hardware, "os": r.os, "version": r.version, "serial": r.serial,
        "uptime": r.uptime, "status": r.status,
        "last_seen_at": r.last_seen_at.isoformat() if r.last_seen_at else None,
        "url": url,
    }


@router.get(
    "/{device_id}/integrations",
    dependencies=[Depends(require_object_perm("device", "read", path_param="device_id"))],
)
async def get_device_integrations(
    device_id: uuid.UUID,
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """此裝置在其他整合系統的資料：Wazuh agent / Proxmox VM（依裝置的 IP 比對）。"""
    from app.models.address import IPAddress
    from app.models.virt import VirtCluster, VirtualMachine
    from app.models.wazuh import WazuhAgent, WazuhInstance
    dev = await session.get(Device, device_id)
    if dev is None:
        raise HTTPException(404, detail="Device not found")
    ip_ids: list[Any] = []
    ip_strs: list[str] = []
    for ipid, ipv in (await session.execute(
        select(IPAddress.id, IPAddress.ip).where(IPAddress.device_id == device_id)
    )).all():
        ip_ids.append(ipid)
        ip_strs.append(str(ipv).split("/")[0])
    if dev.primary_ip_id and dev.primary_ip_id not in ip_ids:
        pr = await session.get(IPAddress, dev.primary_ip_id)
        if pr:
            ip_ids.append(pr.id)
            ip_strs.append(str(pr.ip).split("/")[0])
    out: dict[str, Any] = {"wazuh": None, "vm": None, "ocs": None, "rustdesk": None}
    if not ip_ids:
        return out
    # RustDesk：這台裝置的某個 IP 對應到 RustDesk 裝置（客戶端裝在這台機器上，跟 Wazuh／OCS 代理同一類）。
    # 連線按鈕在 IP 頁（逐 IP 開關＋權限），這裡只顯示資訊並連回那個 IP
    from app.services.rustdesk import for_address as _rustdesk_for_address
    for _ipid in ip_ids:                      # bounded: IPs of one device
        _rd = await _rustdesk_for_address(session, _ipid)
        if _rd is not None:
            _ip = await session.get(IPAddress, _ipid)
            _rd.pop("connect_uri", None)
            out["rustdesk"] = {**_rd, "address_id": str(_ipid),
                               "ip": str(_ip.ip).split("/")[0] if _ip is not None else None}
            break
    wa = (await session.execute(
        select(WazuhAgent).where(WazuhAgent.jt_ipam_address_id.in_(ip_ids)).limit(1)  # bounded: IPs of one device
    )).scalar_one_or_none()
    if wa is None and ip_strs:
        wa = (await session.execute(
            select(WazuhAgent).where(WazuhAgent.ip.in_(ip_strs)).limit(1)  # bounded: IPs of one device
        )).scalar_one_or_none()
    if wa is not None:
        # 位址換給另一台設備之後就沒再回報的 agent 說的是上一台，不放進這台裝置（2026-10-12）。
        # 只看換手：卡片顯示的是 agent 本身的狀態（含「離線」），「離線後位址又被看到活著」那條
        # 是 OS／弱點判讀用的（agent_represents_ip），拿來藏卡片會讓離線的 agent 整張消失。
        from app.services.wazuh import agent_predates_device
        wa_ip = await session.get(IPAddress, wa.jt_ipam_address_id) if wa.jt_ipam_address_id else None
        if wa_ip is not None and agent_predates_device(wa, wa_ip):
            wa = None
    if wa is not None:
        inst = await session.get(WazuhInstance, wa.instance_id)
        # Wazuh 儀表板與 API 是不同服務：API 在 :55000，儀表板在同主機的 443。去掉埠即得。
        dash = _drop_port(inst.api_url) if inst else None
        wz_url = (f"{dash}/app/endpoints-summary#/agents?tab=welcome&agent={wa.agent_id}"
                  if dash and wa.agent_id else None)
        out["wazuh"] = {
            "agent_id": wa.agent_id, "name": wa.name, "url": wz_url,
            "ip": str(wa.ip) if wa.ip else None, "status": wa.status,
            "os_platform": wa.os_platform, "os_version": wa.os_version, "os_name": wa.os_name,
            "os": wazuh_os_display(wa.os_name, wa.os_platform, wa.os_version),
            "agent_version": wa.agent_version, "group": wa.group,
            # 資安組態評估（SCA）—— 目前唯一拿得到的資安體質指標
            "sca_policy": wa.sca_policy, "sca_score": wa.sca_score,
            "sca_pass": wa.sca_pass, "sca_fail": wa.sca_fail,
            "sca_policy_count": wa.sca_policy_count,
            "sca_scanned_at": wa.sca_scanned_at.isoformat() if wa.sca_scanned_at else None,
            "instance": inst.name if inst else None,
            "last_keep_alive": wa.last_keep_alive.isoformat() if wa.last_keep_alive else None,
        }
    vm = (await session.execute(
        select(VirtualMachine).where(VirtualMachine.primary_ip_id.in_(ip_ids)).limit(1)  # bounded: IPs of one device
    )).scalar_one_or_none()
    if vm is not None:
        cl = await session.get(VirtCluster, vm.cluster_id)
        # Proxmox 深連結：叢集任一節點的 web UI（:8006）都能選到該 guest。
        vm_url = None
        if vm.legacy_vmid and (cl is None or (cl.type or "proxmox") == "proxmox"):
            from app.models.virt import ProxmoxInstance
            pinst = (await session.execute(
                select(ProxmoxInstance).where(ProxmoxInstance.cluster_id == vm.cluster_id).limit(1)
            )).scalar_one_or_none()
            pbase = _web_base(pinst.api_url) if pinst else None
            if pbase:
                kind = "lxc" if (vm.kind == "ct") else "qemu"
                vm_url = f"{pbase}/#v1:0:={kind}%2F{vm.legacy_vmid}"
        out["vm"] = {
            "name": vm.name, "node": vm.node, "status": vm.status,
            "vcpus": vm.vcpus, "memory_mb": vm.memory_mb,
            "cluster": cl.name if cl else None, "url": vm_url,
        }
    # OCS Inventory：OCS 沒有自己的每台記錄表，是**透過網卡 MAC** 比對到既有 IP，再把資料補進
    # 該 IP（作業系統／盤點時間／標籤／代理版本／備註／systemid）與裝置（序號／型號／廠牌）。
    # 以「有 IP 被 OCS 盤點過」（ocs_id / os_ocs / last_seen_ocs 任一非空）當作此裝置有 OCS 資料。
    ocs_row = (await session.execute(
        select(IPAddress.os_ocs, IPAddress.last_seen_ocs, IPAddress.ocs_id,
               IPAddress.ocs_tag, IPAddress.ocs_agent, IPAddress.ocs_notes, IPAddress.ocs_hw)
        .where(IPAddress.id.in_(ip_ids),  # bounded: IPs of one device
               or_(IPAddress.os_ocs.isnot(None), IPAddress.last_seen_ocs.isnot(None),
                   IPAddress.ocs_id.isnot(None)))
        .order_by(IPAddress.last_seen_ocs.desc().nullslast())
        .limit(1)
    )).first()
    if ocs_row is not None:
        from app.models.ocs import OcsServer
        srv = (await session.execute(
            select(OcsServer).where(OcsServer.base_url.isnot(None))
            .order_by(OcsServer.enabled.desc()).limit(1)
        )).scalar_one_or_none()
        ocs_url = None
        if srv and srv.base_url and ocs_row.ocs_id is not None:
            ocs_url = (f"{srv.base_url.rstrip('/')}/ocsreports/index.php"
                       f"?function=computer&systemid={ocs_row.ocs_id}")
        # 製造商／型號／序號顯示 **OCS 自己回報的**（ocs_hw），不是裝置欄位 —— 裝置欄位可能是
        # 別的來源寫的（LibreNMS 建立 Windows 裝置時填「windows／Intel x64」，2026-09-27 實機）。
        # 系統序號是出廠佔位時改顯示主機板序號，並標出來。
        hw = ocs_row.ocs_hw or {}
        sysd, board = hw.get("system") or {}, hw.get("board") or {}
        serial, from_board = sysd.get("serial"), False
        if not serial and board.get("serial"):
            serial, from_board = board.get("serial"), True
        out["ocs"] = {
            "os": ocs_row.os_ocs,
            "last_inventory": ocs_row.last_seen_ocs.isoformat() if ocs_row.last_seen_ocs else None,
            "vendor": sysd.get("vendor") or board.get("vendor"),
            "model": sysd.get("model") or board.get("model"),
            "serial": serial, "serial_from_board": from_board,
            "tag": ocs_row.ocs_tag, "agent": ocs_row.ocs_agent,
            "notes": ocs_row.ocs_notes or [], "url": ocs_url,
            "hw": ocs_row.ocs_hw,
        }
    return out


@router.get(
    "/{device_id}/vlans",
    response_model=list[DeviceVLANRead],
    dependencies=[Depends(require_object_perm("device", "read", path_param="device_id"))],
)
async def get_device_vlans(
    device_id: uuid.UUID,
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[DeviceVLANRead]:
    """裝置的 VLAN 清單（feature C）。

    VLAN 對應掛在 LibreNMS 裝置；這裡透過 librenms_devices.jt_ipam_device_id 連結
    到此 jt-ipam Device 來解析（裝置未連結 LibreNMS 時會是空清單）。
    """
    if await session.get(Device, device_id) is None:
        raise HTTPException(404, detail="Device not found")
    rows = (await session.execute(
        select(VLAN.id, VLAN.number, VLAN.name, DeviceVLAN.source,
               func.max(DeviceVLAN.last_seen_at).label("last_seen_at"))
        .join(DeviceVLAN, DeviceVLAN.vlan_id == VLAN.id)
        .join(LibreNMSDevice, LibreNMSDevice.id == DeviceVLAN.librenms_device_id)
        .where(LibreNMSDevice.jt_ipam_device_id == device_id)
        .group_by(VLAN.id, VLAN.number, VLAN.name, DeviceVLAN.source)
        .order_by(VLAN.number)
    )).all()
    return [
        DeviceVLANRead(vlan_id=r.id, number=r.number, name=r.name,
                       source=r.source, last_seen_at=r.last_seen_at)
        for r in rows
    ]


async def _resolve_device_ips(session: AsyncSession, devices: list[Any]) -> dict[Any, Any]:
    """解析每台 device 的「有效管理 IP」供清單/明細顯示。
    優先序：primary_ip_id → LibreNMS 已知管理 IP（primary_ip/hostname）→ 裝置名稱本身是 IP。
    （多數 device 沒有連 IPAddress，但 LibreNMS 知道、或名稱就是 IP，否則 IP 欄會整排空白。）
    """
    import ipaddress as _ip

    from app.models.address import IPAddress
    from app.models.librenms import LibreNMSDevice

    pip_ids = {d.primary_ip_id for d in devices if d.primary_ip_id}
    pip_map: dict[Any, Any] = {}
    if pip_ids:
        for pid, ip in (await session.execute(
            select(IPAddress.id, IPAddress.ip).where(in_values(IPAddress.id, pip_ids))
        )).all():
            pip_map[pid] = str(ip).split("/")[0]
    dev_ids = [d.id for d in devices]
    ln_map: dict[Any, Any] = {}
    if dev_ids:
        for jid, pip, host in (await session.execute(
            select(LibreNMSDevice.jt_ipam_device_id, LibreNMSDevice.primary_ip,
                   LibreNMSDevice.hostname).where(in_values(LibreNMSDevice.jt_ipam_device_id, dev_ids))
        )).all():
            for cand in (pip, host):
                if not cand:
                    continue
                try:
                    ln_map[jid] = str(_ip.ip_address(str(cand).split("/")[0].strip()))
                    break
                except ValueError:
                    continue
    out: dict[Any, Any] = {}
    for d in devices:
        ip = pip_map.get(d.primary_ip_id) if d.primary_ip_id else None
        if not ip:
            ip = ln_map.get(d.id)
        if not ip:
            try:
                ip = str(_ip.ip_address((d.name or "").strip()))
            except ValueError:
                ip = None
        if ip:
            out[d.id] = ip
    return out


@router.get("", response_model=Paginated[DeviceRead])
async def list_devices(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    type: str | None = Query(None),
    location_id: uuid.UUID | None = Query(None),
    rack_id: uuid.UUID | None = Query(None),
    subnet_id: uuid.UUID | None = Query(None),
    q: Annotated[str | None, Query(max_length=128)] = None,
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=500),
) -> Paginated[DeviceRead]:
    """裝置清單。

    `q` 是**伺服器端**搜尋（名稱／FQDN／型號／序號／說明，以及連到這台的 IP，不分大小寫）。
    IP 只比對看得到的位址，不然搜一個看不到的 IP 就能知道它掛在哪台裝置上。
    這一支一定要有：畫面只載得下前幾百筆，若搜尋只在已載入的那幾筆上做，
    使用者新增的裝置只要排序落在載入範圍之外，就會「列表看不到、用名字也搜不到」，
    但從機櫃點進去卻看得到 —— 客戶就是這樣回報的。
    """
    stmt = select(Device)
    cstmt = select(func.count()).select_from(Device)
    if q:
        from app.models.address import IPAddress as _QIP
        from app.services.permission import visible_ids as _vis_ids
        like = f"%{q.strip()}%"
        ip_hit = select(_QIP.id).where(_QIP.device_id == Device.id, func.host(_QIP.ip).ilike(like))
        q_vis_ip = await _vis_ids(session, user=_user, object_type="ip")
        if q_vis_ip is not None:
            ip_hit = ip_hit.where(in_values(_QIP.id, q_vis_ip) if q_vis_ip else sa_false())
        cond = or_(
            Device.name.ilike(like),
            Device.fqdn.ilike(like),
            Device.model.ilike(like),
            Device.serial.ilike(like),
            Device.description.ilike(like),
            ip_hit.exists(),
        )
        stmt = stmt.where(cond); cstmt = cstmt.where(cond)
    if type is not None:
        stmt = stmt.where(Device.type == type); cstmt = cstmt.where(Device.type == type)
    if location_id is not None:
        stmt = stmt.where(Device.location_id == location_id)
        cstmt = cstmt.where(Device.location_id == location_id)
    if rack_id is not None:
        stmt = stmt.where(Device.rack_id == rack_id); cstmt = cstmt.where(Device.rack_id == rack_id)
    if subnet_id is not None:
        # 「這個網段裡有哪些裝置」——「某某網段要停電維護，會影響誰」這種問題的第一步。
        # 用 EXISTS 而非 JOIN：一台裝置在同一個網段可能有多個 IP，JOIN 會讓它重複出現。
        from app.models.address import IPAddress as _IPA
        cond_sub = select(_IPA.id).where(
            _IPA.device_id == Device.id, _IPA.subnet_id == subnet_id).exists()
        stmt = stmt.where(cond_sub); cstmt = cstmt.where(cond_sub)
    # RBAC：只回該 user 可見的裝置（admin / wildcard → vis is None → 不過濾）
    from app.services.permission import visible_ids
    vis = await visible_ids(session, user=_user, object_type="device")
    if vis is not None:
        stmt = stmt.where(in_values(Device.id, vis)); cstmt = cstmt.where(in_values(Device.id, vis))
    stmt = stmt.order_by(Device.name).offset((page - 1) * page_size).limit(page_size)
    rows = list((await session.execute(stmt)).scalars().all())
    total = int(await session.scalar(cstmt) or 0)
    # 批次解析每台 device 的有效管理 IP（primary_ip → LibreNMS → 名稱是 IP）
    ip_map = await _resolve_device_ips(session, rows)
    # 找出與裝置有效 IP 相符、但還沒連到本裝置的 IPAddress → 提供「一鍵關聯」按鈕
    from sqlalchemy import func as _func

    from app.models.address import IPAddress
    eff_ips = {v for v in ip_map.values() if v}
    addr_by_ip: dict[str, tuple[Any, ...]] = {}
    vis_ip = await visible_ids(session, user=_user, object_type="ip")
    if eff_ips and (vis_ip is None or vis_ip):
        q = (select(IPAddress.id, _func.host(IPAddress.ip), IPAddress.device_id)
             .where(in_values(_func.host(IPAddress.ip), eff_ips, type_=String())))
        if vis_ip is not None:   # 看不到的 IP 不給 id（不能點進去，也不該知道它存在；2026-10-07 稽核）
            q = q.where(in_values(IPAddress.id, vis_ip))
        for aid, ahost, adev in (await session.execute(q)).all():
            addr_by_ip.setdefault(str(ahost), (aid, adev))
    # 虛擬 / 實體：一次撈出所有 VM 名稱，避免逐台查
    from app.models.virt import VirtualMachine as _VM
    vm_names = {
        (n or "").strip().lower()
        for n in (await session.execute(select(_VM.name))).scalars().all() if n
    }
    items = []
    for r in rows:
        d = DeviceRead.model_validate(r)
        d.is_virtual = (r.name or "").strip().lower() in vm_names
        d.ip = ip_map.get(r.id)
        if d.ip and d.ip in addr_by_ip:
            aid, adev = addr_by_ip[d.ip]
            d.ip_address_id = str(aid)   # 有對應的 IPAddress → IP 欄可點進該位址
            if adev != r.id:   # 還沒連到本裝置 → 可一鍵關聯
                d.ip_match_id = str(aid)
        items.append(d)
    # 裝置群組（堆疊／HA／叢集）摘要：清單的「群組」欄
    from app.services.device_groups import summaries as _group_summaries
    gs = await _group_summaries(session, list(rows))
    for d, r in zip(items, rows, strict=False):
        d.group = gs.get(r.id)
    return Paginated[DeviceRead](
        items=items, total=total, page=page, page_size=page_size,
    )


@router.get(
    "/{device_id}/relations",
    dependencies=[Depends(require_object_perm("device", "read", path_param="device_id"))],
)
async def get_device_relations(
    device_id: uuid.UUID,
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """裝置的上下關係鏈：機房 → 機櫃 → 裝置 → 主要 IP → 子網路 → 區段。"""
    from app.models.address import IPAddress
    from app.models.location import Location, Rack
    from app.models.section import Section
    from app.models.subnet import Subnet

    dev = await session.get(Device, device_id)
    if dev is None:
        raise HTTPException(404, detail="Device not found")
    chain: list[dict[str, Any]] = []
    if dev.location_id:
        loc = await session.get(Location, dev.location_id)
        if loc is not None:
            chain.append({"type": "location", "id": str(loc.id), "label": loc.name})
    if dev.rack_id:
        rk = await session.get(Rack, dev.rack_id)
        if rk is not None:
            chain.append({"type": "rack", "id": str(rk.id), "label": rk.name})
    # 若這台「裝置」其實是某台 Proxmox VM 的客體（無機櫃/機房），補上它所在的 PVE 節點
    # （節點的機房 / 機櫃 → 節點 → 本客體）。
    if not dev.location_id and not dev.rack_id:
        from sqlalchemy import func as _func

        from app.models.virt import VirtCluster, VirtualMachine
        vm = (await session.execute(
            select(VirtualMachine).where(_func.lower(VirtualMachine.name) == dev.name.lower()).limit(1)
        )).scalar_one_or_none()
        if vm is None:
            ip_ids = [r for (r,) in (await session.execute(
                select(IPAddress.id).where(IPAddress.device_id == dev.id)
            )).all()]
            if ip_ids:
                vm = (await session.execute(
                    select(VirtualMachine).where(VirtualMachine.primary_ip_id.in_(ip_ids)).limit(1)  # bounded: IPs of one device
                )).scalar_one_or_none()
        if vm is not None:
            from app.services.relation_chain import vm_host_device
            node_dev, _how = await vm_host_device(session, vm)
            cluster = await session.get(VirtCluster, vm.cluster_id) if vm.cluster_id else None
            csub = cluster.name if cluster is not None else None
            # 平台要跟著節點走：關係圖原本一律標成「PVE 節點」並連到 PVE 那一頁，
            # 但 VMware 的 VM 掛的是 ESXi 主機 —— 標錯名字、也連錯地方。
            plat = (cluster.type if cluster is not None else None) or "proxmox"
            if node_dev is not None and node_dev.id != dev.id:
                if node_dev.location_id:
                    nloc = await session.get(Location, node_dev.location_id)
                    if nloc is not None:
                        chain.append({"type": "location", "id": str(nloc.id), "label": nloc.name})
                if node_dev.rack_id:
                    nrk = await session.get(Rack, node_dev.rack_id)
                    if nrk is not None:
                        chain.append({"type": "rack", "id": str(nrk.id), "label": nrk.name})
                chain.append({"type": "vmnode", "id": str(node_dev.id), "label": node_dev.name,
                              "sub": csub, "platform": plat})
            elif vm.node:
                chain.append({"type": "vmnode", "id": "host:" + vm.node, "label": vm.node,
                              "sub": csub, "platform": plat})
            # 虛擬機本身也要畫（實體節點 → 虛擬機 → 這台裝置）。
            # 少了它，同一台機器在 IP 詳細資料頁看得到虛擬機、在裝置詳細資料頁卻看不到，
            # 兩頁講的是同一件事卻長得不一樣。
            chain.append({"type": "vm", "id": str(vm.id), "label": vm.name, "sub": csub,
                          "platform": plat})
    chain.append({"type": "device", "id": str(dev.id), "label": dev.name})
    # 主要 IP（沒設就抓任一連到本裝置的 IP）→ 子網路 → 區段
    ip = None
    linked = list((await session.execute(
        select(IPAddress).where(IPAddress.device_id == dev.id)
    )).scalars().all())
    if dev.primary_ip_id:
        ip = await session.get(IPAddress, dev.primary_ip_id)
    if ip is None and linked:
        # 沒設主要 IP：主機名稱像 BMC（iDRAC／iLO／IPMI…）的排後面，其餘照位址排（10-23 客戶：
        # 伺服器的作業系統 IP 與 iDRAC 都連到同一台，以前 limit(1) 沒排序，關係圖抓到 iDRAC）
        ip = min(linked, key=lambda a: (bool(_BMC_HOST.search(re.sub(r"[-_.]", " ", a.hostname or ""))),
                                        ipaddress.ip_address(str(a.ip).split("/")[0])))
    if ip is not None:
        # 這台所有的 IP（客戶 11-02：一台伺服器常有好幾個網段＋iDRAC 的管理網段）—— 卡片點下去列出來，
        # 以前是拿第一個 IP 去清單模糊搜尋（.21 連 .214 都出來）。只列看得到的網段，「另 N 個」也只算這些。
        from app.services.permission import visible_ids
        vis = await visible_ids(session, user=_user, object_type="subnet")
        shown = [a for a in linked if vis is None or a.subnet_id in vis]
        if ip.id not in {a.id for a in shown} and (vis is None or ip.subnet_id in vis):
            shown.append(ip)   # 主要 IP 沒有連到本裝置（device_id 是別的）時也列在第一個
        shown.sort(key=lambda a: (a.id != ip.id,
                                  bool(_BMC_HOST.search(re.sub(r"[-_.]", " ", a.hostname or ""))),
                                  ipaddress.ip_address(str(a.ip).split("/")[0])))
        cidrs = {sid: str(c) for sid, c in (await session.execute(
            select(Subnet.id, Subnet.cidr).where(Subnet.id.in_({a.subnet_id for a in shown if a.subnet_id})))).all()}  # bounded: subnets of one device's IPs
        ips = [{"id": str(a.id), "ip": str(a.ip).split("/")[0], "hostname": a.hostname,
                "subnet": cidrs.get(a.subnet_id), "subnet_id": str(a.subnet_id) if a.subnet_id else None}
               for a in shown]
        more = max(len(ips) - 1, 0)
        chain.append({"type": "ip", "id": str(ip.id),
                      "label": str(ip.ip).split("/")[0], "sub": ip.hostname,
                      **({"more": more} if more else {}), **({"ips": ips} if ips else {})})
        if ip.subnet_id:
            sn = await session.get(Subnet, ip.subnet_id)
            if sn is not None:
                chain.append({"type": "subnet", "id": str(sn.id),
                              "label": str(sn.cidr), "sub": sn.description})
                if sn.section_id:
                    sec = await session.get(Section, sn.section_id)
                    if sec is not None:
                        chain.append({"type": "section", "id": str(sec.id), "label": sec.name})
    from app.services.permission import visible_chain
    return {"chain": await visible_chain(session, _user, chain)}


@router.get(
    "/{device_id}",
    response_model=DeviceRead,
    dependencies=[Depends(require_object_perm("device", "read", path_param="device_id"))],
)
async def get_device(
    device_id: uuid.UUID,
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DeviceRead:
    obj = await session.get(Device, device_id)
    if obj is None:
        raise HTTPException(404, detail="Device not found")
    d = DeviceRead.model_validate(obj)
    ips = await _resolve_device_ips(session, [obj])
    d.ip = ips.get(obj.id)
    # 虛擬 / 實體：詳細資料頁也要看得到（與清單同一套判斷）
    from app.models.virt import VirtualMachine as _VM
    d.is_virtual = bool(await session.scalar(
        select(_VM.id).where(func.lower(_VM.name) == (obj.name or "").strip().lower()).limit(1)
    ))
    # 虛擬化對應明細：名稱比對之外，再用主 IP 與連接埠 MAC 對 VM 網卡 ——
    # 改過名的 VM 名稱比對不到，IP/MAC 仍然對得到
    from app.models.physical import DevicePort
    from app.services.fw_lookup import vm_match_for
    macs = [str(m) for (m,) in (await session.execute(
        select(DevicePort.mac_address).where(DevicePort.device_id == obj.id,
                                             DevicePort.mac_address.is_not(None)).limit(10))).all()]
    d.virt_vm = await vm_match_for(session, ip=d.ip, macs=macs or None)
    if d.virt_vm:
        d.is_virtual = True
    from app.services.device_groups import summaries as _group_summaries
    d.group = (await _group_summaries(session, [obj])).get(obj.id)
    return d


@router.post("", response_model=DeviceRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_device(
    payload: DeviceCreate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DeviceRead:
    # 自訂欄位、類型來源、機櫃 U 位防呆、主要 IP 雙向連結：與 AI 工具 create_device 共用（services/device_write）
    from app.services.device_groups import GroupError
    from app.services.device_write import PlacementError, create_device_checked
    try:
        obj = await create_device_checked(session, payload)
    except CustomFieldError as exc:
        raise HTTPException(status_code=400, detail=detail_of(exc, "custom_field_error")) from exc
    except PlacementError as exc:
        raise HTTPException(status_code=409, detail=detail_of(exc, "rack_placement_error")) from exc
    except GroupError as exc:
        await session.rollback()
        raise HTTPException(exc.status, detail=ui_detail(exc.code, str(exc), **exc.params)) from exc
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="device", object_id=str(obj.id), action="create",
        diff={"after": payload.model_dump(mode="json")},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return DeviceRead.model_validate(obj)


@router.patch("/{device_id}", response_model=DeviceRead,
              dependencies=[Depends(require_admin)])
async def update_device(
    device_id: uuid.UUID,
    payload: DeviceUpdate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DeviceRead:
    obj = await session.get(Device, device_id)
    if obj is None:
        raise HTTPException(404, detail="Device not found")
    before = {"name": obj.name, "type": obj.type, "vendor": obj.vendor, "model": obj.model}
    changes = payload.model_dump(exclude_unset=True)
    if "custom_fields" in changes:
        try:
            changes["custom_fields"] = await validate_custom_fields(
                session, object_type="device", payload=changes["custom_fields"]
            ) or None
        except CustomFieldError as exc:
            raise HTTPException(status_code=400, detail=detail_of(exc, "custom_field_error")) from exc
    for k, v in changes.items():
        setattr(obj, k, v)
    # 人改過類型 → 之後的自動判斷（services/device_type_auto）不再碰它。編輯表單每次都會送 type，所以要比對有沒有真的改
    if "type" in changes and changes["type"] != before["type"]:
        obj.type_source = "manual"
    # 放進機櫃時先防呆：U 位不可越界或與其他裝置（同安裝方向）重疊（與裝置匯入共用 services/device_write）
    from app.services.device_write import PlacementError, check_placement, link_primary_ip
    try:
        await check_placement(session, obj, exclude_device_id=obj.id)
    except PlacementError as exc:
        raise HTTPException(status_code=409, detail=detail_of(exc, "rack_placement_error")) from exc
    # 設了主要 IP → 同時把該 IP 的 device_id 指回本裝置（雙向連結，IP 清單/拓樸才接得起來）
    if changes.get("primary_ip_id"):
        from app.services.device_groups import GroupError
        try:
            await link_primary_ip(session, obj)
        except GroupError as exc:
            await session.rollback()
            raise HTTPException(exc.status, detail=ui_detail(exc.code, str(exc), **exc.params)) from exc
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="device", object_id=str(obj.id), action="update",
        diff={"before": before, "changes": changes},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return DeviceRead.model_validate(obj)


@router.delete("/{device_id}", status_code=204, dependencies=[Depends(require_admin)])
async def delete_device(
    device_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    obj = await session.get(Device, device_id)
    if obj is None:
        raise HTTPException(404, detail="Device not found")
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="device", object_id=str(obj.id), action="delete",
        diff={"before": {"name": obj.name, "type": obj.type}},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.delete(obj)
    # 物件沒了，指向它的授權也不該留著（permissions.object_id 沒有外鍵，沒有人會自動清）
    from app.services.permission import purge_permissions_for_object
    await purge_permissions_for_object(session, object_type="device", object_id=device_id)

    await session.commit()


class _DeviceBulkDeletePayload(StrictModel):
    ids: list[uuid.UUID]


@router.post("/bulk-delete", dependencies=[Depends(require_admin)])
async def bulk_delete_devices(
    payload: _DeviceBulkDeletePayload,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    if not payload.ids:
        return {"deleted": 0, "failed": 0, "errors": []}
    if len(payload.ids) > 500:
        raise HTTPException(400, detail="too many ids (max 500)")
    deleted = 0
    errors: list[dict[str, str]] = []
    actor_ip = request.client.host if request.client else None
    actor_ua = request.headers.get("user-agent")
    request_id = getattr(request.state, "request_id", None)
    for did in payload.ids:
        obj = await session.get(Device, did)
        if obj is None:
            errors.append({"id": str(did), "error": "not_found"}); continue
        await append_audit(
            session, actor_user_id=str(user.id),
            actor_ip=actor_ip, actor_user_agent=actor_ua,
            object_type="device", object_id=str(obj.id), action="delete",
            diff={"before": {"name": obj.name, "type": obj.type}, "bulk": True},
            request_id=request_id,
        )
        await session.delete(obj)
        deleted += 1
    await session.commit()
    return {"deleted": deleted, "failed": len(errors), "errors": errors[:50]}


@router.get(
    "/{device_id}/uptime",
    dependencies=[Depends(require_object_perm("device", "read", path_param="device_id"))],
)
async def get_device_uptime(
    device_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    days: int = Query(90, ge=7, le=365),
) -> dict[str, Any]:
    """裝置的每日存活狀態，由它名下所有 IP 的狀態轉換合併而成。

    多個 IP 時：**當天任一 IP 曾中斷就標中斷**。與單一 IP 的每日規則一致，
    且傾向浮現問題而非掩蓋（某個介面斷了仍然值得看見）。
    重建規則與「沒資料不得算成正常」等原則見 `app/services/uptime.py`。
    """
    from app.models.address import IPAddress
    from app.services.uptime import uptime_for_ips

    ip_ids = list((await session.execute(
        select(IPAddress.id).where(IPAddress.device_id == device_id)
    )).scalars().all())
    return await uptime_for_ips(session, ip_ids, days=days)
