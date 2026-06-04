"""LibreNMS 整合 endpoints（admin）。"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import Field, HttpUrl
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin
from app.core.audit import append_audit
from app.core.db import get_session
from app.models.librenms import (
    ARPEntry,
    FDBEntry,
    LibreNMSDevice,
    LibreNMSInstance,
)
from app.schemas.base import Paginated, StrictModel
from app.services import librenms as svc
from app.services.background_tasks import spawn_task

router = APIRouter(prefix="/librenms", tags=["librenms"])


class LibreNMSInstanceCreate(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    api_url: HttpUrl
    api_token: Annotated[str, Field(min_length=8, max_length=512)]
    enabled: bool = True
    sync_devices: bool = True
    sync_arp: bool = True
    sync_fdb: bool = True
    sync_vlans: bool = True
    use_for_status: bool = True
    auto_add_devices: bool = False
    sync_interval_seconds: Annotated[int, Field(ge=60, le=86400)] = 300
    scope_subnet_ids: list[str] | None = None


class LibreNMSInstanceUpdate(StrictModel):
    api_url: HttpUrl | None = None
    api_token: Annotated[str | None, Field(min_length=8, max_length=512)] = None
    enabled: bool | None = None
    sync_devices: bool | None = None
    sync_arp: bool | None = None
    sync_fdb: bool | None = None
    sync_vlans: bool | None = None
    use_for_status: bool | None = None
    auto_add_devices: bool | None = None
    sync_interval_seconds: Annotated[int | None, Field(ge=60, le=86400)] = None
    scope_subnet_ids: list[str] | None = None


class LibreNMSInstanceRead(StrictModel):
    id: uuid.UUID
    name: str
    api_url: str
    enabled: bool
    sync_devices: bool
    sync_arp: bool
    sync_fdb: bool
    sync_vlans: bool
    use_for_status: bool
    auto_add_devices: bool
    sync_interval_seconds: int
    scope_subnet_ids: list[str] | None = None
    last_sync_at: Any
    last_error: str | None
    created_at: Any
    updated_at: Any


class LibreNMSDeviceRead(StrictModel):
    id: uuid.UUID
    instance_id: uuid.UUID
    legacy_device_id: int
    hostname: str | None
    primary_ip: str | None
    hardware: str | None
    os: str | None
    version: str | None
    serial: str | None
    status: str | None
    uptime: int | None
    jt_ipam_device_id: uuid.UUID | None
    last_seen_at: Any


class ARPEntryRead(StrictModel):
    id: uuid.UUID
    ip: str
    mac: str
    interface: str | None
    vrf: str | None
    device_id: uuid.UUID | None
    first_seen_at: Any
    last_seen_at: Any


class FDBEntryRead(StrictModel):
    id: uuid.UUID
    mac: str
    vlan_id_num: int | None
    port_name: str | None
    device_id: uuid.UUID | None
    first_seen_at: Any
    last_seen_at: Any


# ─────────────────── Instance CRUD ───────────────────
@router.get("/instances",
            response_model=Paginated[LibreNMSInstanceRead],
            dependencies=[Depends(require_admin)])
async def list_instances(
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=200),
) -> Paginated[LibreNMSInstanceRead]:
    rows = list(
        (await session.execute(
            select(LibreNMSInstance).order_by(LibreNMSInstance.name)
            .offset((page - 1) * page_size).limit(page_size)
        )).scalars().all()
    )
    total = int(await session.scalar(select(func.count()).select_from(LibreNMSInstance)) or 0)
    return Paginated[LibreNMSInstanceRead](
        items=[LibreNMSInstanceRead.model_validate(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.post("/instances",
             response_model=LibreNMSInstanceRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_instance(
    payload: LibreNMSInstanceCreate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LibreNMSInstanceRead:
    obj = LibreNMSInstance(
        name=payload.name,
        api_url=str(payload.api_url).rstrip("/"),
        api_token_enc=b"placeholder", api_token_nonce=b"placeholder",
        enabled=payload.enabled,
        sync_devices=payload.sync_devices,
        sync_arp=payload.sync_arp,
        sync_fdb=payload.sync_fdb,
        sync_vlans=payload.sync_vlans,
        scope_subnet_ids=payload.scope_subnet_ids,
        use_for_status=payload.use_for_status,
        auto_add_devices=payload.auto_add_devices,
        sync_interval_seconds=payload.sync_interval_seconds,
    )
    session.add(obj)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="Instance name conflict") from exc

    enc, nonce = svc.encrypt_instance_token(obj.id, payload.api_token)
    obj.api_token_enc = enc
    obj.api_token_nonce = nonce

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="librenms_instance",
        object_id=str(obj.id),
        action="create",
        diff={"name": obj.name, "api_url": obj.api_url},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return LibreNMSInstanceRead.model_validate(obj)


@router.patch("/instances/{instance_id}",
              response_model=LibreNMSInstanceRead,
              dependencies=[Depends(require_admin)])
async def update_instance(
    instance_id: uuid.UUID,
    payload: LibreNMSInstanceUpdate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LibreNMSInstanceRead:
    obj = await session.get(LibreNMSInstance, instance_id)
    if obj is None:
        raise HTTPException(404, detail="Not found")
    rotated = False
    if payload.api_url is not None:
        obj.api_url = str(payload.api_url).rstrip("/")
    for field_name in ("enabled", "sync_devices", "sync_arp", "sync_fdb", "sync_vlans",
                       "use_for_status", "auto_add_devices", "sync_interval_seconds",
                       "scope_subnet_ids"):
        v = getattr(payload, field_name)
        if v is not None:
            setattr(obj, field_name, v)
    if payload.api_token is not None:
        enc, nonce = svc.encrypt_instance_token(obj.id, payload.api_token)
        obj.api_token_enc = enc
        obj.api_token_nonce = nonce
        rotated = True

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="librenms_instance",
        object_id=str(obj.id),
        action="update",
        diff={"rotated_token": rotated},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return LibreNMSInstanceRead.model_validate(obj)


@router.delete("/instances/{instance_id}", status_code=204,
               dependencies=[Depends(require_admin)])
async def delete_instance(
    instance_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    obj = await session.get(LibreNMSInstance, instance_id)
    if obj is None:
        raise HTTPException(404, detail="Not found")
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="librenms_instance",
        object_id=str(obj.id),
        action="delete",
        diff={"name": obj.name},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.delete(obj)
    await session.commit()


@router.post("/instances/{instance_id}/test",
             dependencies=[Depends(require_admin)])
async def test_instance(
    instance_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    obj = await session.get(LibreNMSInstance, instance_id)
    if obj is None:
        raise HTTPException(404, detail="Not found")
    try:
        info = await svc.healthcheck(obj)
    except svc.LibreNMSError as exc:
        raise HTTPException(502, detail=str(exc)) from exc
    return {"ok": True, "system": info}


@router.post("/instances/{instance_id}/sync",
             dependencies=[Depends(require_admin)])
async def trigger_sync(
    instance_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    """非同步觸發 — 立刻回 task_id，實際 sync 在背景跑。

    前端 UI 可改去 /api/v1/tasks 看進度，或 poll /tasks/{id}。
    """
    obj = await session.get(LibreNMSInstance, instance_id)
    if obj is None:
        raise HTTPException(404, detail="Not found")

    actor_user_id = user.id
    actor_ip = request.client.host if request.client else None
    actor_ua = request.headers.get("user-agent")
    request_id = getattr(request.state, "request_id", None)
    instance_name = obj.name
    instance_id_uuid = obj.id

    async def _runner(sess: AsyncSession, _task) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        # 重新拿 instance（這個 sess 跟 request 的 sess 不同）
        inst = await sess.get(LibreNMSInstance, instance_id_uuid)
        if inst is None:
            raise RuntimeError("LibreNMS instance disappeared")
        summary = await svc.sync_instance(sess, inst)
        await append_audit(
            sess,
            actor_user_id=str(actor_user_id),
            actor_ip=actor_ip,
            actor_user_agent=actor_ua,
            object_type="librenms_instance",
            object_id=str(inst.id),
            action="sync",
            diff=summary.to_dict(),
            request_id=request_id,
        )
        await sess.commit()
        return summary.to_dict()

    task = await spawn_task(
        session=session,
        kind="librenms.sync",
        target_type="librenms_instance",
        target_id=instance_id_uuid,
        target_label=instance_name,
        actor_user_id=actor_user_id,
        runner=_runner,
    )
    return {
        "task_id": str(task.id),
        "status": task.status,
        "queued_at": task.queued_at.isoformat(),
    }


@router.post("/instances/{instance_id}/link-devices",
             dependencies=[Depends(require_admin)])
async def link_devices(
    instance_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, int]:
    """把這個 instance 底下尚未連結的 LibreNMS 裝置 match-or-create 到 jt-ipam Device
    （feature D，手動觸發；不用等 sync、也不用開 auto_add_devices）。純讀 LibreNMS。"""
    if await session.get(LibreNMSInstance, instance_id) is None:
        raise HTTPException(404, detail="Not found")
    ldevs = list((await session.execute(
        select(LibreNMSDevice).where(
            LibreNMSDevice.instance_id == instance_id,
            LibreNMSDevice.jt_ipam_device_id.is_(None),
        )
    )).scalars().all())
    linked = created = 0
    for ldev in ldevs:
        dev_id, was_created = await svc.link_librenms_device(session, ldev, create=True)
        if dev_id is not None:
            linked += 1
            if was_created:
                created += 1
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="librenms_instance", object_id=str(instance_id),
        action="link_devices",
        diff={"linked": linked, "created": created, "candidates": len(ldevs)},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return {"candidates": len(ldevs), "linked": linked, "created": created}


# ─────────────────── Devices / ARP / FDB 唯讀 ───────────────────


@router.get("/devices", response_model=Paginated[LibreNMSDeviceRead])
async def list_devices(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    instance_id: uuid.UUID | None = Query(None),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(100, ge=1, le=500),
) -> Paginated[LibreNMSDeviceRead]:
    stmt = select(LibreNMSDevice)
    cstmt = select(func.count()).select_from(LibreNMSDevice)
    if instance_id is not None:
        stmt = stmt.where(LibreNMSDevice.instance_id == instance_id)
        cstmt = cstmt.where(LibreNMSDevice.instance_id == instance_id)
    rows = list((await session.execute(
        stmt.order_by(LibreNMSDevice.hostname).offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    total = int(await session.scalar(cstmt) or 0)
    return Paginated[LibreNMSDeviceRead](
        items=[LibreNMSDeviceRead.model_validate(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.get("/arp", response_model=Paginated[ARPEntryRead])
async def list_arp(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    ip: str | None = Query(None),
    mac: str | None = Query(None),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(200, ge=1, le=1000),
) -> Paginated[ARPEntryRead]:
    stmt = select(ARPEntry)
    cstmt = select(func.count()).select_from(ARPEntry)
    if ip:
        stmt = stmt.where(ARPEntry.ip == ip); cstmt = cstmt.where(ARPEntry.ip == ip)
    if mac:
        stmt = stmt.where(ARPEntry.mac == mac.lower()); cstmt = cstmt.where(ARPEntry.mac == mac.lower())
    rows = list((await session.execute(
        stmt.order_by(ARPEntry.last_seen_at.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    total = int(await session.scalar(cstmt) or 0)
    return Paginated[ARPEntryRead](
        items=[ARPEntryRead.model_validate(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.get("/fdb", response_model=Paginated[FDBEntryRead])
async def list_fdb(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    mac: str | None = Query(None),
    vlan: int | None = Query(None),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(200, ge=1, le=1000),
) -> Paginated[FDBEntryRead]:
    stmt = select(FDBEntry)
    cstmt = select(func.count()).select_from(FDBEntry)
    if mac:
        stmt = stmt.where(FDBEntry.mac == mac.lower()); cstmt = cstmt.where(FDBEntry.mac == mac.lower())
    if vlan is not None:
        stmt = stmt.where(FDBEntry.vlan_id_num == vlan); cstmt = cstmt.where(FDBEntry.vlan_id_num == vlan)
    rows = list((await session.execute(
        stmt.order_by(FDBEntry.last_seen_at.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    total = int(await session.scalar(cstmt) or 0)
    return Paginated[FDBEntryRead](
        items=[FDBEntryRead.model_validate(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


# ─────────────────── 推導：IP → MAC → switch port ───────────────────


@router.get("/trace/ip/{ip}")
async def trace_ip(
    ip: str,
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """根據 IP 找：最近 ARP 看到的 MAC + 推到 FDB 找 switch port。"""
    arp = (
        await session.execute(
            select(ARPEntry).where(ARPEntry.ip == ip)
            .order_by(ARPEntry.last_seen_at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    if arp is None:
        return {"ip": ip, "mac": None, "switch_port": None}
    fdb = (
        await session.execute(
            select(FDBEntry).where(FDBEntry.mac == arp.mac)
            .order_by(FDBEntry.last_seen_at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    return {
        "ip": ip,
        "mac": arp.mac,
        "arp": {
            "device_id": str(arp.device_id) if arp.device_id else None,
            "interface": arp.interface,
            "vrf": arp.vrf,
            "last_seen_at": arp.last_seen_at.isoformat(),
        },
        "switch_port": (
            {
                "device_id": str(fdb.device_id) if fdb.device_id else None,
                "port_name": fdb.port_name,
                "vlan": fdb.vlan_id_num,
                "last_seen_at": fdb.last_seen_at.isoformat(),
            }
            if fdb else None
        ),
    }
