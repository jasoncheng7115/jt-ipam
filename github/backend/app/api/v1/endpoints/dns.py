"""DNS 整合 endpoints（admin）。"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin, require_global_read
from app.core.audit import append_audit
from app.core.db import get_session
from app.core.security import encrypt_secret
from app.models.dns import DNSRecord, DNSServer, DNSZone
from app.models.encrypted_secret import EncryptedSecret
from app.schemas.base import Paginated
from app.schemas.dns import (
    ConsistencyReportItem,
    DNSRecordRead,
    DNSServerCreate,
    DNSServerRead,
    DNSServerUpdate,
    DNSZoneRead,
    InconsistentRecord,
)
from app.services.dns import DNSAdapterError, get_adapter
from app.services.dns_sync import pull_server

router = APIRouter(prefix="/dns", tags=["dns"], dependencies=[Depends(require_global_read)])

_SECRET_FIELDS = ("api_key", "api_secret", "tsig_key", "password")


def _aad(server_id: uuid.UUID, field: str) -> bytes:
    return f"dns_server:{server_id}:{field}".encode()


async def _store_secret(
    session: AsyncSession, server: DNSServer, field: str, value: str
) -> None:
    enc, nonce = encrypt_secret(value, aad=_aad(server.id, field))
    existing = (
        await session.execute(
            select(EncryptedSecret).where(
                EncryptedSecret.object_type == "dns_server",
                EncryptedSecret.object_id == server.id,
                EncryptedSecret.field == field,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        session.add(EncryptedSecret(
            object_type="dns_server",
            object_id=server.id,
            field=field,
            ciphertext=enc, nonce=nonce,
        ))
    else:
        existing.ciphertext = enc
        existing.nonce = nonce


# ─────────────────── DNS Servers CRUD ───────────────────


@router.get("/servers",
            response_model=Paginated[DNSServerRead],
            dependencies=[Depends(require_admin)])
async def list_servers(
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=200),
) -> Paginated[DNSServerRead]:
    rows = list(
        (await session.execute(
            select(DNSServer).order_by(DNSServer.name)
            .offset((page - 1) * page_size).limit(page_size)
        )).scalars().all()
    )
    total = int(await session.scalar(select(func.count()).select_from(DNSServer)) or 0)
    return Paginated[DNSServerRead](
        items=[DNSServerRead.model_validate(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.post("/servers",
             response_model=DNSServerRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_server(
    payload: DNSServerCreate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DNSServerRead:
    obj = DNSServer(
        name=payload.name,
        type=payload.type,
        api_url=str(payload.api_url).rstrip("/") if payload.api_url else None,
        server_address=payload.server_address,
        extra_config=payload.extra_config,
        enabled=payload.enabled,
        sync_interval_seconds=payload.sync_interval_seconds,
    )
    session.add(obj)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="DNS server name conflict") from exc

    for field in _SECRET_FIELDS:
        v = getattr(payload, field, None)
        if v:
            await _store_secret(session, obj, field, v)

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="dns_server",
        object_id=str(obj.id),
        action="create",
        diff={"name": obj.name, "type": obj.type, "api_url": obj.api_url},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return DNSServerRead.model_validate(obj)


@router.patch("/servers/{server_id}",
              response_model=DNSServerRead,
              dependencies=[Depends(require_admin)])
async def update_server(
    server_id: uuid.UUID,
    payload: DNSServerUpdate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DNSServerRead:
    obj = await session.get(DNSServer, server_id)
    if obj is None:
        raise HTTPException(404, detail="Not found")

    rotated: list[str] = []
    if payload.name is not None:
        obj.name = payload.name
    if payload.type is not None:
        obj.type = payload.type
    if payload.api_url is not None:
        obj.api_url = str(payload.api_url).rstrip("/")
    if payload.server_address is not None:
        obj.server_address = payload.server_address
    if payload.extra_config is not None:
        obj.extra_config = payload.extra_config
    if payload.enabled is not None:
        obj.enabled = payload.enabled
    if payload.sync_interval_seconds is not None:
        obj.sync_interval_seconds = payload.sync_interval_seconds
    for field in _SECRET_FIELDS:
        v = getattr(payload, field, None)
        if v:
            await _store_secret(session, obj, field, v)
            rotated.append(field)

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="dns_server",
        object_id=str(obj.id),
        action="update",
        diff={"rotated_secrets": rotated},
        request_id=getattr(request.state, "request_id", None),
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(409, detail="DNS server name conflict") from exc
    await session.refresh(obj)
    return DNSServerRead.model_validate(obj)


@router.delete("/servers/{server_id}", status_code=204,
               dependencies=[Depends(require_admin)])
async def delete_server(
    server_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    obj = await session.get(DNSServer, server_id)
    if obj is None:
        raise HTTPException(404, detail="Not found")
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="dns_server",
        object_id=str(obj.id),
        action="delete",
        diff={"name": obj.name},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.delete(obj)
    await session.commit()


@router.post("/servers/{server_id}/test", dependencies=[Depends(require_admin)])
async def test_server(
    server_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    obj = await session.get(DNSServer, server_id)
    if obj is None:
        raise HTTPException(404, detail="Not found")
    try:
        adapter = await get_adapter(session, obj)
    except DNSAdapterError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    try:
        info = await adapter.healthcheck()
    except DNSAdapterError as exc:
        raise HTTPException(502, detail=str(exc)) from exc
    finally:
        await adapter.close()
    return {"ok": True, "server": info}


@router.post("/servers/{server_id}/sync",
             dependencies=[Depends(require_admin)])
async def sync_server(
    server_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """非同步：立刻回 task_id，pull 在背景跑。"""
    from app.services.background_tasks import spawn_task

    obj = await session.get(DNSServer, server_id)
    if obj is None:
        raise HTTPException(404, detail="Not found")

    actor_user_id = user.id
    actor_ip = request.client.host if request.client else None
    actor_ua = request.headers.get("user-agent")
    request_id = getattr(request.state, "request_id", None)
    server_name = obj.name
    server_id_uuid = obj.id

    async def _runner(sess: AsyncSession, _task) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        srv = await sess.get(DNSServer, server_id_uuid)
        if srv is None:
            raise RuntimeError("DNS server disappeared")
        summary = await pull_server(sess, srv)
        await append_audit(
            sess, actor_user_id=str(actor_user_id),
            actor_ip=actor_ip, actor_user_agent=actor_ua,
            object_type="dns_server", object_id=str(srv.id),
            action="sync", diff=summary, request_id=request_id,
        )
        await sess.commit()
        return summary

    task = await spawn_task(
        session=session, kind="dns.sync",
        target_type="dns_server", target_id=server_id_uuid, target_label=server_name,
        actor_user_id=actor_user_id, runner=_runner,
    )
    return {"task_id": str(task.id), "status": task.status,
            "queued_at": task.queued_at.isoformat()}


# ─────────────────── Zones / Records 唯讀 ───────────────────


@router.get("/zones", response_model=Paginated[DNSZoneRead])
async def list_zones(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    server_id: uuid.UUID | None = Query(None),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(100, ge=1, le=500),
) -> Paginated[DNSZoneRead]:
    stmt = select(DNSZone)
    cstmt = select(func.count()).select_from(DNSZone)
    if server_id is not None:
        stmt = stmt.where(DNSZone.server_id == server_id)
        cstmt = cstmt.where(DNSZone.server_id == server_id)
    rows = list(
        (await session.execute(
            stmt.order_by(DNSZone.name).offset((page - 1) * page_size).limit(page_size)
        )).scalars().all()
    )
    total = int(await session.scalar(cstmt) or 0)
    return Paginated[DNSZoneRead](
        items=[DNSZoneRead.model_validate(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.get("/records", response_model=Paginated[DNSRecordRead])
async def list_records(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    zone_id: uuid.UUID | None = Query(None),
    consistency: str | None = Query(None),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(200, ge=1, le=1000),
) -> Paginated[DNSRecordRead]:
    stmt = select(DNSRecord)
    cstmt = select(func.count()).select_from(DNSRecord)
    if zone_id is not None:
        stmt = stmt.where(DNSRecord.zone_id == zone_id)
        cstmt = cstmt.where(DNSRecord.zone_id == zone_id)
    if consistency is not None:
        stmt = stmt.where(DNSRecord.consistency_state == consistency)
        cstmt = cstmt.where(DNSRecord.consistency_state == consistency)
    rows = list(
        (await session.execute(
            stmt.order_by(DNSRecord.name, DNSRecord.type)
            .offset((page - 1) * page_size).limit(page_size)
        )).scalars().all()
    )
    total = int(await session.scalar(cstmt) or 0)
    return Paginated[DNSRecordRead](
        items=[DNSRecordRead.model_validate(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


# ─────────────────── 不一致報表 ───────────────────


@router.get("/consistency",
            response_model=list[ConsistencyReportItem])
async def consistency_summary(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[ConsistencyReportItem]:
    rows = (
        await session.execute(
            select(DNSRecord.consistency_state, func.count())
            .group_by(DNSRecord.consistency_state)
        )
    ).all()
    return [ConsistencyReportItem(state=r[0], count=int(r[1])) for r in rows]


@router.get("/consistency/inconsistent",
            response_model=list[InconsistentRecord])
async def list_inconsistent(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = Query(200, ge=1, le=2000),
) -> list[InconsistentRecord]:
    rows = (
        await session.execute(
            select(DNSRecord, DNSZone, DNSServer)
            .join(DNSZone, DNSZone.id == DNSRecord.zone_id)
            .join(DNSServer, DNSServer.id == DNSZone.server_id)
            .where(DNSRecord.consistency_state != "consistent")
            .limit(limit)
        )
    ).all()
    return [
        InconsistentRecord(
            zone_id=z.id, zone_name=z.name, server_name=s.name,
            name=r.name, type=r.type, value=r.value,
            state=r.consistency_state,
        )
        for r, z, s in rows
    ]
