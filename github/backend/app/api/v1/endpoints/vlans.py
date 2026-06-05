"""VLAN Domain + VLAN endpoints。

Phase 1：read 須認證，write/delete 限 admin。後續可加群組級權限。
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import String, cast, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin, require_global_read
from app.core.audit import append_audit
from app.core.db import get_session
from app.models.librenms import LibreNMSDevice
from app.models.vlan import VLAN, DeviceVLAN, VLANDomain
from app.schemas.base import Paginated, StrictModel
from app.schemas.vlan import (
    VLANCreate,
    VLANDomainCreate,
    VLANDomainRead,
    VLANDomainUpdate,
    VLANRead,
    VLANUpdate,
)

router = APIRouter(tags=["vlans"], dependencies=[Depends(require_global_read)])


# ─────────────────── VLAN Domains ───────────────────
@router.get("/vlan-domains", response_model=Paginated[VLANDomainRead])
async def list_domains(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=500),
) -> Paginated[VLANDomainRead]:
    rows = list(
        (
            await session.execute(
                select(VLANDomain)
                .order_by(VLANDomain.name)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    total = int(await session.scalar(select(func.count()).select_from(VLANDomain)) or 0)
    return Paginated[VLANDomainRead](
        items=[VLANDomainRead.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/vlan-domains", response_model=VLANDomainRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_domain(
    payload: VLANDomainCreate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> VLANDomainRead:
    obj = VLANDomain(**payload.model_dump())
    session.add(obj)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="VLAN domain conflict") from exc
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="vlan_domain",
        object_id=str(obj.id),
        action="create",
        diff={"after": payload.model_dump(mode="json")},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return VLANDomainRead.model_validate(obj)


@router.patch("/vlan-domains/{domain_id}", response_model=VLANDomainRead,
              dependencies=[Depends(require_admin)])
async def update_domain(
    domain_id: uuid.UUID,
    payload: VLANDomainUpdate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> VLANDomainRead:
    obj = await session.get(VLANDomain, domain_id)
    if obj is None:
        raise HTTPException(404, detail="Not found")
    before = {"name": obj.name, "description": obj.description}
    changes = payload.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(obj, k, v)
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="vlan_domain",
        object_id=str(obj.id),
        action="update",
        diff={"before": before, "changes": changes},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return VLANDomainRead.model_validate(obj)


@router.delete("/vlan-domains/{domain_id}", status_code=204,
               dependencies=[Depends(require_admin)])
async def delete_domain(
    domain_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    obj = await session.get(VLANDomain, domain_id)
    if obj is None:
        raise HTTPException(404, detail="Not found")
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="vlan_domain",
        object_id=str(obj.id),
        action="delete",
        diff={"before": {"name": obj.name}},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.delete(obj)
    await session.commit()


# ─────────────────── VLANs ───────────────────
@router.get("/vlans", response_model=Paginated[VLANRead])
async def list_vlans(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    domain_id: uuid.UUID | None = Query(None),
    number: int | None = Query(None, ge=1, le=4094),
    customer_id: uuid.UUID | None = Query(None),
    section_id: uuid.UUID | None = Query(None),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=500),
) -> Paginated[VLANRead]:
    stmt = select(VLAN)
    cstmt = select(func.count()).select_from(VLAN)
    if domain_id is not None:
        stmt = stmt.where(VLAN.domain_id == domain_id)
        cstmt = cstmt.where(VLAN.domain_id == domain_id)
    if number is not None:
        stmt = stmt.where(VLAN.number == number)
        cstmt = cstmt.where(VLAN.number == number)
    if customer_id is not None:
        stmt = stmt.where(VLAN.customer_id == customer_id)
        cstmt = cstmt.where(VLAN.customer_id == customer_id)
    if section_id is not None:
        stmt = stmt.where(VLAN.section_id == section_id)
        cstmt = cstmt.where(VLAN.section_id == section_id)
    stmt = stmt.order_by(VLAN.number).offset((page - 1) * page_size).limit(page_size)
    rows = list((await session.execute(stmt)).scalars().all())
    total = int(await session.scalar(cstmt) or 0)

    # feature C：一次撈本頁各 VLAN 的裝置數
    vlan_ids = [r.id for r in rows]
    count_map: dict[uuid.UUID, int] = {}
    if vlan_ids:
        for vid, cnt in (await session.execute(
            select(DeviceVLAN.vlan_id, func.count())
            .where(DeviceVLAN.vlan_id.in_(vlan_ids))
            .group_by(DeviceVLAN.vlan_id)
        )).all():
            count_map[vid] = cnt

    # 連接埠數（FDB 依 VLAN 號碼，不重複 device+port）與 IP 數（此 VLAN 下子網路）
    port_map: dict[int, int] = {}
    ip_map: dict[uuid.UUID, int] = {}
    if vlan_ids:
        from app.models.address import IPAddress
        from app.models.librenms import FDBEntry
        from app.models.subnet import Subnet
        numbers = [r.number for r in rows]
        for num, cnt in (await session.execute(
            select(FDBEntry.vlan_id_num, func.count(func.distinct(
                func.concat(cast(FDBEntry.device_id, String), "|", FDBEntry.port_name))))
            .where(FDBEntry.vlan_id_num.in_(numbers))
            .group_by(FDBEntry.vlan_id_num)
        )).all():
            if num is not None:
                port_map[int(num)] = int(cnt)
        for vid, cnt in (await session.execute(
            select(Subnet.vlan_id, func.count(IPAddress.id))
            .join(IPAddress, IPAddress.subnet_id == Subnet.id)
            .where(Subnet.vlan_id.in_(vlan_ids))
            .group_by(Subnet.vlan_id)
        )).all():
            if vid is not None:
                ip_map[vid] = int(cnt)

    items = []
    for r in rows:
        m = VLANRead.model_validate(r)
        m.device_count = count_map.get(r.id, 0)
        m.port_count = port_map.get(r.number, 0)
        m.ip_count = ip_map.get(r.id, 0)
        items.append(m)
    return Paginated[VLANRead](
        items=items, total=total, page=page, page_size=page_size,
    )


@router.get("/vlans/{vlan_id}/members")
async def vlan_members(
    vlan_id: uuid.UUID,
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """此 VLAN 的成員明細：哪些裝置的哪些連接埠（FDB）+ 子網路 + IP。"""
    from app.models.address import IPAddress
    from app.models.device import Device
    from app.models.librenms import FDBEntry, LibreNMSDevice
    from app.models.subnet import Subnet

    vlan = await session.get(VLAN, vlan_id)
    if vlan is None:
        raise HTTPException(404, detail="VLAN not found")

    # FDB：依 VLAN 號碼，列出 (裝置, 連接埠, MAC) — 裝置名走 LibreNMS sysName
    fdb_rows = list((await session.execute(
        select(FDBEntry.port_name, FDBEntry.mac, LibreNMSDevice.sysname, LibreNMSDevice.hostname)
        .join(LibreNMSDevice, LibreNMSDevice.id == FDBEntry.device_id)
        .where(FDBEntry.vlan_id_num == vlan.number)
        .order_by(LibreNMSDevice.sysname, FDBEntry.port_name)
        .limit(2000)
    )).all())
    ports = [
        {"device": sn or hn or "—", "port": pn, "mac": str(m) if m else None}
        for pn, m, sn, hn in fdb_rows
    ]
    # 此 VLAN 的子網路 + IP 數
    subnets = list((await session.execute(
        select(Subnet.id, Subnet.cidr).where(Subnet.vlan_id == vlan_id)
    )).all())
    subnet_out = []
    for sid, cidr in subnets:
        ipn = int(await session.scalar(
            select(func.count()).select_from(IPAddress).where(IPAddress.subnet_id == sid)
        ) or 0)
        subnet_out.append({"id": str(sid), "cidr": str(cidr), "ip_count": ipn})
    # 掛此 VLAN 的（已連結 jt-ipam）裝置
    devs = list((await session.execute(
        select(Device.id, Device.name)
        .join(LibreNMSDevice, LibreNMSDevice.jt_ipam_device_id == Device.id)
        .join(DeviceVLAN, DeviceVLAN.librenms_device_id == LibreNMSDevice.id)
        .where(DeviceVLAN.vlan_id == vlan_id).distinct()
    )).all())
    return {
        "vlan": {"id": str(vlan.id), "number": vlan.number, "name": vlan.name},
        "ports": ports,
        "subnets": subnet_out,
        "devices": [{"id": str(i), "name": n} for i, n in devs],
    }


class VLANDeviceRead(StrictModel):
    librenms_device_id: uuid.UUID
    hostname: str | None
    primary_ip: str | None
    source: str


@router.get("/vlans/{vlan_id}/devices", response_model=list[VLANDeviceRead])
async def list_vlan_devices(
    vlan_id: uuid.UUID,
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[VLANDeviceRead]:
    """掛在此 VLAN 的 LibreNMS 裝置清單（feature C）。"""
    if await session.get(VLAN, vlan_id) is None:
        raise HTTPException(404, detail="VLAN not found")
    rows = (await session.execute(
        select(LibreNMSDevice.id, LibreNMSDevice.hostname,
               LibreNMSDevice.primary_ip, DeviceVLAN.source)
        .join(DeviceVLAN, DeviceVLAN.librenms_device_id == LibreNMSDevice.id)
        .where(DeviceVLAN.vlan_id == vlan_id)
        .order_by(LibreNMSDevice.hostname)
    )).all()
    return [
        VLANDeviceRead(librenms_device_id=r.id, hostname=r.hostname,
                       primary_ip=str(r.primary_ip) if r.primary_ip else None,
                       source=r.source)
        for r in rows
    ]


@router.post("/vlans", response_model=VLANRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_vlan(
    payload: VLANCreate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> VLANRead:
    obj = VLAN(**payload.model_dump())
    session.add(obj)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="VLAN conflict (duplicate domain+number?)") from exc
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="vlan",
        object_id=str(obj.id),
        action="create",
        diff={"after": payload.model_dump(mode="json")},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return VLANRead.model_validate(obj)


@router.patch("/vlans/{vlan_id}", response_model=VLANRead,
              dependencies=[Depends(require_admin)])
async def update_vlan(
    vlan_id: uuid.UUID,
    payload: VLANUpdate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> VLANRead:
    obj = await session.get(VLAN, vlan_id)
    if obj is None:
        raise HTTPException(404, detail="Not found")
    before = {"name": obj.name, "description": obj.description}
    changes = payload.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(obj, k, v)
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="vlan",
        object_id=str(obj.id),
        action="update",
        diff={"before": before, "changes": changes},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return VLANRead.model_validate(obj)


@router.delete("/vlans/{vlan_id}", status_code=204,
               dependencies=[Depends(require_admin)])
async def delete_vlan(
    vlan_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    obj = await session.get(VLAN, vlan_id)
    if obj is None:
        raise HTTPException(404, detail="Not found")
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="vlan",
        object_id=str(obj.id),
        action="delete",
        diff={"before": {"number": obj.number, "name": obj.name}},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.delete(obj)
    await session.commit()


# ─────────────────── bulk-delete ───────────────────


class _BulkDeletePayload(StrictModel):
    ids: list[uuid.UUID]


async def _bulk_delete_helper(
    *, session: AsyncSession, model_cls: type[Any], object_type: str,
    user: Any, ids: list[uuid.UUID], actor_ip: str | None, actor_ua: str | None, request_id: str | None,
) -> dict[str, object]:
    if not ids:
        return {"deleted": 0, "failed": 0, "errors": []}
    if len(ids) > 500:
        raise HTTPException(400, detail="too many ids (max 500)")
    deleted = 0
    errors: list[dict[str, str]] = []
    for oid in ids:
        obj = await session.get(model_cls, oid)
        if obj is None:
            errors.append({"id": str(oid), "error": "not_found"}); continue
        await append_audit(
            session, actor_user_id=str(user.id),
            actor_ip=actor_ip, actor_user_agent=actor_ua,
            object_type=object_type, object_id=str(obj.id), action="delete",
            diff={"before": {"name": getattr(obj, "name", None)}, "bulk": True},
            request_id=request_id,
        )
        await session.delete(obj)
        deleted += 1
    await session.commit()
    return {"deleted": deleted, "failed": len(errors), "errors": errors[:50]}


@router.post("/vlans/bulk-delete", dependencies=[Depends(require_admin)])
async def bulk_delete_vlans(
    payload: _BulkDeletePayload, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    return await _bulk_delete_helper(
        session=session, model_cls=VLAN, object_type="vlan",
        user=user, ids=payload.ids,
        actor_ip=request.client.host if request.client else None,
        actor_ua=request.headers.get("user-agent"),
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/vlan-domains/bulk-delete", dependencies=[Depends(require_admin)])
async def bulk_delete_vlan_domains(
    payload: _BulkDeletePayload, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    return await _bulk_delete_helper(
        session=session, model_cls=VLANDomain, object_type="vlan_domain",
        user=user, ids=payload.ids,
        actor_ip=request.client.host if request.client else None,
        actor_ua=request.headers.get("user-agent"),
        request_id=getattr(request.state, "request_id", None),
    )
