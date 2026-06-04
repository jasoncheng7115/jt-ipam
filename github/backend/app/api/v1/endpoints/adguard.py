"""AdGuard Home 整合 endpoints（admin only）。"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin
from app.core.audit import append_audit
from app.core.db import get_session
from app.models.adguard import AdGuardInstance
from app.schemas.adguard import AdGuardCreate, AdGuardRead, AdGuardUpdate
from app.schemas.base import Paginated
from app.services import adguard as svc
from app.services.background_tasks import spawn_task

router = APIRouter(prefix="/adguard", tags=["adguard"], dependencies=[Depends(require_admin)])


@router.get("/instances", response_model=Paginated[AdGuardRead])
async def list_instances(
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=500),
) -> Paginated[AdGuardRead]:
    stmt = select(AdGuardInstance).order_by(AdGuardInstance.name).offset((page - 1) * page_size).limit(page_size)
    rows = list((await session.execute(stmt)).scalars().all())
    total = int(await session.scalar(select(func.count()).select_from(AdGuardInstance)) or 0)
    return Paginated[AdGuardRead](
        items=[AdGuardRead.model_validate(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.post("/instances", response_model=AdGuardRead, status_code=status.HTTP_201_CREATED)
async def create_instance(
    payload: AdGuardCreate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AdGuardRead:
    inst = AdGuardInstance(
        name=payload.name,
        api_url=str(payload.api_url).rstrip("/"),
        api_user=payload.api_user,
        enabled=payload.enabled,
        verify_tls=payload.verify_tls,
        sync_clients=payload.sync_clients,
        sync_rewrites=payload.sync_rewrites,
        sync_interval_seconds=payload.sync_interval_seconds,
        description=payload.description,
        api_password_enc=b"placeholder",
        api_password_nonce=b"placeholder",
    )
    session.add(inst)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="Name already exists") from exc
    creds = svc.encrypt_password(inst.id, payload.api_password)
    inst.api_password_enc = creds["api_password_enc"]
    inst.api_password_nonce = creds["api_password_nonce"]
    await session.flush()
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="adguard_instance", object_id=str(inst.id),
        action="create",
        diff={"name": inst.name, "api_url": inst.api_url},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(inst)
    return AdGuardRead.model_validate(inst)


@router.patch("/instances/{instance_id}", response_model=AdGuardRead)
async def update_instance(
    instance_id: uuid.UUID,
    payload: AdGuardUpdate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AdGuardRead:
    inst = await session.get(AdGuardInstance, instance_id)
    if inst is None:
        raise HTTPException(404, detail="Not found")
    data = payload.model_dump(exclude_unset=True)
    new_password = data.pop("api_password", None)
    for k, v in data.items():
        if k == "api_url" and v is not None:
            v = str(v).rstrip("/")
        setattr(inst, k, v)
    if new_password:
        creds = svc.encrypt_password(inst.id, new_password)
        inst.api_password_enc = creds["api_password_enc"]
        inst.api_password_nonce = creds["api_password_nonce"]
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="adguard_instance", object_id=str(inst.id),
        action="update", diff=data,
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(inst)
    return AdGuardRead.model_validate(inst)


@router.delete("/instances/{instance_id}", status_code=204)
async def delete_instance(
    instance_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    inst = await session.get(AdGuardInstance, instance_id)
    if inst is None:
        raise HTTPException(404, detail="Not found")
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="adguard_instance", object_id=str(inst.id),
        action="delete", diff={"name": inst.name},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.delete(inst)
    await session.commit()


@router.post("/instances/{instance_id}/test")
async def test_instance(
    instance_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    inst = await session.get(AdGuardInstance, instance_id)
    if inst is None:
        raise HTTPException(404, detail="Not found")
    try:
        info = await svc.healthcheck(inst)
    except svc.AdGuardError as exc:
        raise HTTPException(502, detail=str(exc)) from exc
    return info


@router.post("/instances/{instance_id}/sync")
async def trigger_sync(
    instance_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """非同步 — 立刻回 task_id，sync 在背景跑。"""
    inst = await session.get(AdGuardInstance, instance_id)
    if inst is None:
        raise HTTPException(404, detail="Not found")

    actor_user_id = user.id
    actor_ip = request.client.host if request.client else None
    actor_ua = request.headers.get("user-agent")
    request_id = getattr(request.state, "request_id", None)
    inst_name = inst.name
    inst_id = inst.id

    async def _runner(sess: AsyncSession, _task) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        ag = await sess.get(AdGuardInstance, inst_id)
        if ag is None:
            raise RuntimeError("AdGuard instance disappeared")
        summary = await svc.sync_instance(sess, ag)
        await append_audit(
            sess, actor_user_id=str(actor_user_id),
            actor_ip=actor_ip, actor_user_agent=actor_ua,
            object_type="adguard_instance", object_id=str(ag.id),
            action="sync", diff=summary,
            request_id=request_id,
        )
        await sess.commit()
        return summary

    task = await spawn_task(
        session=session,
        kind="adguard.sync",
        target_type="adguard_instance",
        target_id=inst_id,
        target_label=inst_name,
        actor_user_id=actor_user_id,
        runner=_runner,
    )
    return {"task_id": str(task.id), "status": task.status,
            "queued_at": task.queued_at.isoformat()}
