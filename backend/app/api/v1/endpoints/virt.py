"""Virtualization + Proxmox endpoints。"""

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
from app.models.virt import (
    ProxmoxInstance,
    VirtCluster,
    VirtualMachine,
    VMInterface,
)
from app.schemas.base import Paginated, StrictModel
from app.services import proxmox as proxmox_service

router = APIRouter(prefix="/virt", tags=["virtualization"])


class ClusterRead(StrictModel):
    id: uuid.UUID
    name: str
    type: str
    description: str | None


class ClusterWrite(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    type: str = "proxmox"
    description: Annotated[str | None, Field(max_length=1024)] = None


class VMRead(StrictModel):
    id: uuid.UUID
    cluster_id: uuid.UUID
    legacy_vmid: int | None
    name: str
    status: str
    vcpus: int | None
    memory_mb: int | None
    disk_gb: int | None
    primary_ip_id: uuid.UUID | None
    is_template: bool


class VMInterfaceRead(StrictModel):
    id: uuid.UUID
    vm_id: uuid.UUID
    name: str
    mac: str | None
    primary_ip: str | None
    bridge: str | None


class ProxmoxInstanceCreate(StrictModel):
    cluster_id: uuid.UUID
    api_url: HttpUrl
    auth_username: Annotated[str, Field(min_length=1, max_length=128)]
    auth_token_id: Annotated[str, Field(min_length=1, max_length=64)]
    token_secret: Annotated[str, Field(min_length=8, max_length=512)]
    enabled: bool = True
    sync_interval_seconds: Annotated[int, Field(ge=60, le=86400)] = 600


class ProxmoxInstanceRead(StrictModel):
    id: uuid.UUID
    cluster_id: uuid.UUID
    api_url: str
    auth_username: str
    auth_token_id: str
    enabled: bool
    sync_interval_seconds: int
    last_sync_at: Any
    last_error: str | None


# ─────────────────── Clusters ───────────────────


@router.get("/clusters", response_model=Paginated[ClusterRead])
async def list_clusters(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(1, ge=1, le=10_000), page_size: int = Query(50, ge=1, le=500),
) -> Paginated[ClusterRead]:
    rows = list((await session.execute(
        select(VirtCluster).order_by(VirtCluster.name)
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    total = int(await session.scalar(select(func.count()).select_from(VirtCluster)) or 0)
    return Paginated[ClusterRead](
        items=[ClusterRead.model_validate(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.post("/clusters", response_model=ClusterRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_cluster(
    payload: ClusterWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ClusterRead:
    obj = VirtCluster(**payload.model_dump())
    session.add(obj)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="Cluster name conflict") from exc
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="virt_cluster", object_id=str(obj.id), action="create",
        diff=payload.model_dump(mode="json"),
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return ClusterRead.model_validate(obj)


# ─────────────────── VMs（唯讀，由 sync 進來）───────────────────


@router.get("/vms", response_model=Paginated[VMRead])
async def list_vms(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    cluster_id: uuid.UUID | None = Query(None),
    page: int = Query(1, ge=1, le=10_000), page_size: int = Query(100, ge=1, le=500),
) -> Paginated[VMRead]:
    stmt = select(VirtualMachine)
    cstmt = select(func.count()).select_from(VirtualMachine)
    if cluster_id is not None:
        stmt = stmt.where(VirtualMachine.cluster_id == cluster_id)
        cstmt = cstmt.where(VirtualMachine.cluster_id == cluster_id)
    rows = list((await session.execute(
        stmt.order_by(VirtualMachine.name).offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    total = int(await session.scalar(cstmt) or 0)
    return Paginated[VMRead](
        items=[VMRead.model_validate(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.get("/vms/{vm_id}/interfaces", response_model=list[VMInterfaceRead])
async def list_vm_interfaces(
    vm_id: uuid.UUID,
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[VMInterfaceRead]:
    rows = list((await session.execute(
        select(VMInterface).where(VMInterface.vm_id == vm_id)
        .order_by(VMInterface.name)
    )).scalars().all())
    return [VMInterfaceRead.model_validate(r) for r in rows]


# ─────────────────── Proxmox instance CRUD + sync ───────────────────


@router.post("/proxmox", response_model=ProxmoxInstanceRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_proxmox(
    payload: ProxmoxInstanceCreate, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProxmoxInstanceRead:
    cluster = await session.get(VirtCluster, payload.cluster_id)
    if cluster is None:
        raise HTTPException(400, detail="Invalid cluster_id")
    obj = ProxmoxInstance(
        cluster_id=payload.cluster_id,
        api_url=str(payload.api_url),
        auth_username=payload.auth_username,
        auth_token_id=payload.auth_token_id,
        enabled=payload.enabled,
        sync_interval_seconds=payload.sync_interval_seconds,
    )
    session.add(obj)
    await session.flush()

    enc, nonce = proxmox_service.encrypt_instance_secret(obj.id, payload.token_secret)
    from app.models.encrypted_secret import EncryptedSecret
    session.add(EncryptedSecret(
        object_type="proxmox_instance",
        object_id=obj.id,
        field="token_secret",
        ciphertext=enc, nonce=nonce,
    ))

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="proxmox_instance", object_id=str(obj.id), action="create",
        diff={"api_url": obj.api_url, "auth_username": obj.auth_username,
              "auth_token_id": obj.auth_token_id},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return ProxmoxInstanceRead.model_validate(obj)


@router.get("/proxmox", response_model=Paginated[ProxmoxInstanceRead],
            dependencies=[Depends(require_admin)])
async def list_proxmox(
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(1, ge=1, le=10_000), page_size: int = Query(50, ge=1, le=200),
) -> Paginated[ProxmoxInstanceRead]:
    rows = list((await session.execute(
        select(ProxmoxInstance).order_by(ProxmoxInstance.api_url)
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    total = int(await session.scalar(select(func.count()).select_from(ProxmoxInstance)) or 0)
    return Paginated[ProxmoxInstanceRead](
        items=[ProxmoxInstanceRead.model_validate(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.post("/proxmox/{instance_id}/test",
             dependencies=[Depends(require_admin)])
async def test_proxmox(
    instance_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    obj = await session.get(ProxmoxInstance, instance_id)
    if obj is None:
        raise HTTPException(404, detail="Not found")
    try:
        info = await proxmox_service.healthcheck(session, obj)
    except proxmox_service.ProxmoxError as exc:
        raise HTTPException(502, detail=str(exc)) from exc
    return {"ok": True, "version": info}


@router.post("/proxmox/{instance_id}/sync",
             dependencies=[Depends(require_admin)])
async def sync_proxmox(
    instance_id: uuid.UUID, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    obj = await session.get(ProxmoxInstance, instance_id)
    if obj is None:
        raise HTTPException(404, detail="Not found")
    summary = await proxmox_service.sync_instance(session, obj)
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="proxmox_instance", object_id=str(obj.id), action="sync",
        diff=summary.to_dict(),
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return summary.to_dict()
