"""Virtualization + Proxmox endpoints。"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import Field, HttpUrl, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin, require_global_read
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

router = APIRouter(prefix="/virt", tags=["virtualization"], dependencies=[Depends(require_global_read)])


class ClusterRead(StrictModel):
    id: uuid.UUID
    name: str
    type: str
    is_standalone: bool = False
    description: str | None
    customer_id: uuid.UUID | None = None       # 所屬單位
    customer_name: str | None = None            # 單位名稱（清單顯示用）


class ClusterWrite(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    type: str = "proxmox"
    description: Annotated[str | None, Field(max_length=1024)] = None
    customer_id: uuid.UUID | None = None


class ClusterUpdate(StrictModel):
    name: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    description: Annotated[str | None, Field(max_length=1024)] = None
    customer_id: uuid.UUID | None = None


class VMRead(StrictModel):
    id: uuid.UUID
    cluster_id: uuid.UUID
    legacy_vmid: int | None
    name: str
    node: str | None = None
    kind: str | None = None
    status: str
    vcpus: int | None
    memory_mb: int | None
    disk_gb: int | None
    primary_ip_id: uuid.UUID | None
    is_template: bool
    # 從 VMInterface 帶上來（list_vms 批次填）
    ips: list[str] = []
    macs: list[str] = []
    bridges: list[str] = []


class VMInterfaceRead(StrictModel):
    id: uuid.UUID
    vm_id: uuid.UUID
    name: str
    mac: str | None
    primary_ip: str | None
    bridge: str | None


class ProxmoxInstanceCreate(StrictModel):
    cluster_id: uuid.UUID | None = None   # 留空 → 同步時以 PVE 叢集名稱自動建立/指派
    api_url: HttpUrl
    extra_api_urls: list[HttpUrl] = []
    auth_username: Annotated[str, Field(min_length=1, max_length=128)]
    auth_token_id: Annotated[str, Field(min_length=1, max_length=64)]
    token_secret: Annotated[str, Field(min_length=8, max_length=512)]
    verify_tls: bool = False
    enabled: bool = True
    sync_interval_seconds: Annotated[int, Field(ge=60, le=86400)] = 600


class ProxmoxInstanceUpdate(StrictModel):
    api_url: HttpUrl | None = None
    extra_api_urls: list[HttpUrl] | None = None
    auth_username: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    auth_token_id: Annotated[str | None, Field(min_length=1, max_length=64)] = None
    token_secret: Annotated[str | None, Field(min_length=8, max_length=512)] = None
    verify_tls: bool | None = None
    enabled: bool | None = None
    sync_interval_seconds: Annotated[int | None, Field(ge=60, le=86400)] = None


class ProxmoxInstanceRead(StrictModel):
    id: uuid.UUID
    cluster_id: uuid.UUID | None = None
    api_url: str
    extra_api_urls: list[str] = []
    auth_username: str
    auth_token_id: str
    verify_tls: bool = False
    enabled: bool
    sync_interval_seconds: int
    last_sync_at: Any
    last_error: str | None

    @field_validator("extra_api_urls", mode="before")
    @classmethod
    def _split_urls(cls, v: Any) -> list[str]:
        if v is None:
            return []
        if isinstance(v, list):
            return v
        return [s.strip() for s in str(v).replace(",", "\n").splitlines() if s.strip()]


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
    cust_names: dict[Any, str] = {}
    cust_ids = [r.customer_id for r in rows if r.customer_id]
    if cust_ids:
        from app.models.customer import Customer
        cust_names = dict((await session.execute(  # type: ignore[arg-type]
            select(Customer.id, Customer.name).where(Customer.id.in_(cust_ids))
        )).all())
    items = []
    for r in rows:
        m = ClusterRead.model_validate(r)
        m.customer_name = cust_names.get(r.customer_id) if r.customer_id else None
        items.append(m)
    return Paginated[ClusterRead](
        items=items, total=total, page=page, page_size=page_size,
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


@router.patch("/clusters/{cluster_id}", response_model=ClusterRead,
              dependencies=[Depends(require_admin)])
async def update_cluster(
    cluster_id: uuid.UUID, payload: ClusterUpdate, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ClusterRead:
    obj = await session.get(VirtCluster, cluster_id)
    if obj is None:
        raise HTTPException(404, detail="Cluster not found")
    changes = payload.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(obj, k, v)
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="virt_cluster", object_id=str(obj.id), action="update",
        diff=payload.model_dump(mode="json", exclude_unset=True),
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
    items = [VMRead.model_validate(r) for r in rows]

    # 批次帶上每台 VM/CT 的網卡 IP / MAC / bridge
    vm_ids = [r.id for r in rows]
    if vm_ids:
        ifaces = (await session.execute(
            select(VMInterface).where(VMInterface.vm_id.in_(vm_ids))
            .order_by(VMInterface.name)
        )).scalars().all()
        by_vm: dict[uuid.UUID, dict[str, list[str]]] = {}
        for nic in ifaces:
            d = by_vm.setdefault(nic.vm_id, {"ips": [], "macs": [], "bridges": []})
            if nic.primary_ip and str(nic.primary_ip) not in d["ips"]:
                d["ips"].append(str(nic.primary_ip))
            if nic.mac and str(nic.mac) not in d["macs"]:
                d["macs"].append(str(nic.mac))
            if nic.bridge and nic.bridge not in d["bridges"]:
                d["bridges"].append(nic.bridge)
        for it in items:
            d = by_vm.get(it.id)  # type: ignore[assignment]
            if d:
                it.ips, it.macs, it.bridges = d["ips"], d["macs"], d["bridges"]

    return Paginated[VMRead](items=items, total=total, page=page, page_size=page_size)


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
    if payload.cluster_id is not None:
        cluster = await session.get(VirtCluster, payload.cluster_id)
        if cluster is None:
            raise HTTPException(400, detail="Invalid cluster_id")
    obj = ProxmoxInstance(
        cluster_id=payload.cluster_id,
        api_url=str(payload.api_url).rstrip("/"),
        extra_api_urls=("\n".join(str(u).rstrip("/") for u in payload.extra_api_urls)
                        or None),
        auth_username=payload.auth_username,
        auth_token_id=payload.auth_token_id,
        verify_tls=payload.verify_tls,
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
    page: int = Query(1, ge=1, le=10_000), page_size: int = Query(50, ge=1, le=500),
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


@router.patch("/proxmox/{instance_id}", response_model=ProxmoxInstanceRead,
              dependencies=[Depends(require_admin)])
async def update_proxmox(
    instance_id: uuid.UUID, payload: ProxmoxInstanceUpdate,
    user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProxmoxInstanceRead:
    obj = await session.get(ProxmoxInstance, instance_id)
    if obj is None:
        raise HTTPException(404, detail="Not found")
    data = payload.model_dump(exclude_unset=True)
    secret = data.pop("token_secret", None)
    if "api_url" in data and data["api_url"] is not None:
        obj.api_url = str(data["api_url"]).rstrip("/")
    if "extra_api_urls" in data:
        urls = data["extra_api_urls"] or []
        obj.extra_api_urls = "\n".join(str(u).rstrip("/") for u in urls) or None
    for k in ("auth_username", "auth_token_id", "verify_tls", "enabled",
              "sync_interval_seconds"):
        if k in data and data[k] is not None:
            setattr(obj, k, data[k])

    if secret:  # 重設 token secret → 重新加密覆寫
        from sqlalchemy import delete as _delete

        from app.models.encrypted_secret import EncryptedSecret
        await session.execute(_delete(EncryptedSecret).where(
            EncryptedSecret.object_type == "proxmox_instance",
            EncryptedSecret.object_id == obj.id,
            EncryptedSecret.field == "token_secret",
        ))
        enc, nonce = proxmox_service.encrypt_instance_secret(obj.id, secret)
        session.add(EncryptedSecret(
            object_type="proxmox_instance", object_id=obj.id,
            field="token_secret", ciphertext=enc, nonce=nonce,
        ))

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="proxmox_instance", object_id=str(obj.id), action="update",
        diff={"api_url": obj.api_url, "secret_rotated": bool(secret)},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return ProxmoxInstanceRead.model_validate(obj)


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
    """非同步觸發 — 立刻回 task_id，實際 sync 在背景跑（可在「作業」頁看進度）。"""
    obj = await session.get(ProxmoxInstance, instance_id)
    if obj is None:
        raise HTTPException(404, detail="Not found")

    from app.services.background_tasks import spawn_task

    actor_user_id = user.id
    actor_ip = request.client.host if request.client else None
    actor_ua = request.headers.get("user-agent")
    request_id = getattr(request.state, "request_id", None)
    inst_id = obj.id
    inst_label = obj.api_url

    async def _runner(sess: AsyncSession, _task) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        inst = await sess.get(ProxmoxInstance, inst_id)
        if inst is None:
            raise RuntimeError("Proxmox instance disappeared")
        summary = await proxmox_service.sync_instance(sess, inst)
        await append_audit(
            sess,
            actor_user_id=str(actor_user_id),
            actor_ip=actor_ip, actor_user_agent=actor_ua,
            object_type="proxmox_instance", object_id=str(inst.id), action="sync",
            diff=summary.to_dict(), request_id=request_id,
        )
        await sess.commit()
        return summary.to_dict()

    task = await spawn_task(
        session=session,
        kind="proxmox.sync",
        target_type="proxmox_instance",
        target_id=inst_id,
        target_label=inst_label,
        actor_user_id=actor_user_id,
        runner=_runner,
    )
    return {
        "task_id": str(task.id),
        "status": task.status,
        "queued_at": task.queued_at.isoformat(),
    }


@router.delete("/proxmox/{instance_id}", status_code=204,
               dependencies=[Depends(require_admin)])
async def delete_proxmox(
    instance_id: uuid.UUID, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    obj = await session.get(ProxmoxInstance, instance_id)
    if obj is None:
        raise HTTPException(404, detail="Not found")
    from sqlalchemy import delete as _delete

    from app.models.encrypted_secret import EncryptedSecret
    await session.execute(_delete(EncryptedSecret).where(
        EncryptedSecret.object_type == "proxmox_instance",
        EncryptedSecret.object_id == instance_id,
    ))
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="proxmox_instance", object_id=str(obj.id), action="delete",
        diff={"api_url": obj.api_url}, request_id=getattr(request.state, "request_id", None),
    )
    await session.delete(obj)
    await session.commit()
