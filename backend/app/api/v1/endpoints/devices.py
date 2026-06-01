"""Device endpoints。"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin
from app.core.audit import append_audit
from app.core.db import get_session
from app.models.device import Device
from app.models.librenms import LibreNMSDevice
from app.models.vlan import VLAN, DeviceVLAN
from app.schemas.base import Paginated, StrictModel
from app.schemas.device import DeviceCreate, DeviceRead, DeviceUpdate
from app.services.custom_field import CustomFieldError, validate_custom_fields

router = APIRouter(prefix="/devices", tags=["devices"])


class DeviceVLANRead(StrictModel):
    vlan_id: uuid.UUID
    number: int
    name: str
    source: str
    last_seen_at: Any


@router.get("/{device_id}/librenms")
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
    return {
        "hostname": r.hostname, "sysname": r.sysname, "primary_ip": str(r.primary_ip) if r.primary_ip else None,
        "hardware": r.hardware, "os": r.os, "version": r.version, "serial": r.serial,
        "uptime": r.uptime, "status": r.status,
        "last_seen_at": r.last_seen_at.isoformat() if r.last_seen_at else None,
    }


@router.get("/{device_id}/integrations")
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
    out: dict[str, Any] = {"wazuh": None, "vm": None}
    if not ip_ids:
        return out
    wa = (await session.execute(
        select(WazuhAgent).where(WazuhAgent.jt_ipam_address_id.in_(ip_ids)).limit(1)
    )).scalar_one_or_none()
    if wa is None and ip_strs:
        wa = (await session.execute(
            select(WazuhAgent).where(WazuhAgent.ip.in_(ip_strs)).limit(1)
        )).scalar_one_or_none()
    if wa is not None:
        inst = await session.get(WazuhInstance, wa.instance_id)
        out["wazuh"] = {
            "agent_id": wa.agent_id, "name": wa.name,
            "ip": str(wa.ip) if wa.ip else None, "status": wa.status,
            "os_platform": wa.os_platform, "os_version": wa.os_version,
            "agent_version": wa.agent_version, "group": wa.group,
            "cve_critical": wa.cve_critical_count, "cve_high": wa.cve_high_count,
            "instance": inst.name if inst else None,
            "last_keep_alive": wa.last_keep_alive.isoformat() if wa.last_keep_alive else None,
        }
    vm = (await session.execute(
        select(VirtualMachine).where(VirtualMachine.primary_ip_id.in_(ip_ids)).limit(1)
    )).scalar_one_or_none()
    if vm is not None:
        cl = await session.get(VirtCluster, vm.cluster_id)
        out["vm"] = {
            "name": vm.name, "node": vm.node, "status": vm.status,
            "vcpus": vm.vcpus, "memory_mb": vm.memory_mb,
            "cluster": cl.name if cl else None,
        }
    return out


@router.get("/{device_id}/vlans", response_model=list[DeviceVLANRead])
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


@router.get("", response_model=Paginated[DeviceRead])
async def list_devices(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    type: str | None = Query(None),
    location_id: uuid.UUID | None = Query(None),
    rack_id: uuid.UUID | None = Query(None),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=500),
) -> Paginated[DeviceRead]:
    stmt = select(Device)
    cstmt = select(func.count()).select_from(Device)
    if type is not None:
        stmt = stmt.where(Device.type == type); cstmt = cstmt.where(Device.type == type)
    if location_id is not None:
        stmt = stmt.where(Device.location_id == location_id)
        cstmt = cstmt.where(Device.location_id == location_id)
    if rack_id is not None:
        stmt = stmt.where(Device.rack_id == rack_id); cstmt = cstmt.where(Device.rack_id == rack_id)
    # RBAC：只回該 user 可見的裝置（admin / wildcard → vis is None → 不過濾）
    from app.services.permission import visible_ids
    vis = await visible_ids(session, user=_user, object_type="device")
    if vis is not None:
        stmt = stmt.where(Device.id.in_(vis)); cstmt = cstmt.where(Device.id.in_(vis))
    stmt = stmt.order_by(Device.name).offset((page - 1) * page_size).limit(page_size)
    rows = list((await session.execute(stmt)).scalars().all())
    total = int(await session.scalar(cstmt) or 0)
    # 批次解析每台 device 的管理 IP（primary_ip_id → ip 字串）供清單顯示
    pip_ids = {r.primary_ip_id for r in rows if r.primary_ip_id}
    ip_map: dict[uuid.UUID, str] = {}
    if pip_ids:
        from app.models.address import IPAddress
        for pid, ip in (await session.execute(
            select(IPAddress.id, IPAddress.ip).where(IPAddress.id.in_(pip_ids))
        )).all():
            ip_map[pid] = str(ip).split("/")[0]
    items = []
    for r in rows:
        d = DeviceRead.model_validate(r)
        if r.primary_ip_id:
            d.ip = ip_map.get(r.primary_ip_id)
        items.append(d)
    return Paginated[DeviceRead](
        items=items, total=total, page=page, page_size=page_size,
    )


@router.get("/{device_id}", response_model=DeviceRead)
async def get_device(
    device_id: uuid.UUID,
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DeviceRead:
    obj = await session.get(Device, device_id)
    if obj is None:
        raise HTTPException(404, detail="Device not found")
    return DeviceRead.model_validate(obj)


@router.post("", response_model=DeviceRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_device(
    payload: DeviceCreate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DeviceRead:
    try:
        cf = await validate_custom_fields(
            session, object_type="device", payload=payload.custom_fields
        )
    except CustomFieldError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    data = payload.model_dump()
    data["custom_fields"] = cf or None
    obj = Device(**data)
    session.add(obj)
    await session.flush()
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
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    for k, v in changes.items():
        setattr(obj, k, v)
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
