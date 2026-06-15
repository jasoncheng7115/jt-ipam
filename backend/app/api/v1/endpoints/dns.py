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
    DNSRecordTypeCount,
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
        scope_subnet_ids=payload.scope_subnet_ids,
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
    if payload.scope_subnet_ids is not None:
        obj.scope_subnet_ids = payload.scope_subnet_ids
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
    except Exception as exc:
        # 安全網：任何 adapter 漏接的連線例外（winrm/dnspython/json…）都轉成可懂的 502，
        # 不讓連線測試變成無訊息的 500。
        raise HTTPException(502, detail=f"{exc.__class__.__name__}: {exc}") from exc
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
    server_id: uuid.UUID | None = Query(None, description="只列此 DNS 伺服器的記錄"),
    rtype: str | None = Query(None, description="只列此型別的記錄（A/AAAA/CNAME/PTR/...）"),
    consistency: str | None = Query(None),
    q: str | None = Query(None, description="模糊搜尋 name / value"),
    ip: str | None = Query(None, description="找對應此 IP 的記錄：A/AAAA value 相符或該 IP 的 PTR"),
    missing_ip: bool = Query(False, description="只列『沒有對應 IPAM IP』的 A/AAAA 記錄"),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(200, ge=1, le=1000),
) -> Paginated[DNSRecordRead]:
    from sqlalchemy import or_ as _or

    def _apply(s):  # type: ignore[no-untyped-def]
        if zone_id is not None:
            s = s.where(DNSRecord.zone_id == zone_id)
        if server_id is not None:
            zsub = select(DNSZone.id).where(DNSZone.server_id == server_id)
            s = s.where(DNSRecord.zone_id.in_(zsub))
        if rtype:
            s = s.where(DNSRecord.type == rtype.strip().upper())
        if consistency is not None:
            s = s.where(DNSRecord.consistency_state == consistency)
        if q:
            pat = f"%{q.strip()}%"
            s = s.where(_or(DNSRecord.name.ilike(pat), DNSRecord.value.ilike(pat)))
        if missing_ip:
            # 「沒有對應 IP」＝ A/AAAA 記錄的 value（IP 值）在 ip_addresses 中查不到
            from app.models.address import IPAddress as _IPA
            ip_here = (
                select(_IPA.id)
                .where(func.host(_IPA.ip) == DNSRecord.value)
                .correlate(DNSRecord).exists()
            )
            s = s.where(DNSRecord.type.in_(("A", "AAAA")), ~ip_here)
        if ip:
            ipx = ip.strip()
            conds = [(DNSRecord.type.in_(("A", "AAAA"))) & (DNSRecord.value == ipx)]
            ptr = _reverse_ptr(ipx)
            if ptr:
                conds.append((DNSRecord.type == "PTR") & (DNSRecord.name == ptr))
            s = s.where(_or(*conds))
        return s

    stmt = _apply(select(DNSRecord))
    cstmt = _apply(select(func.count()).select_from(DNSRecord))
    rows = list(
        (await session.execute(
            stmt.order_by(DNSRecord.name, DNSRecord.type)
            .offset((page - 1) * page_size).limit(page_size)
        )).scalars().all()
    )
    total = int(await session.scalar(cstmt) or 0)
    # 依「IP 值」實查 ip_addresses，標記每筆 A/AAAA 記錄是否真的有對應位址
    from app.models.address import IPAddress as _IPA
    ip_vals = {r.value for r in rows if r.type in ("A", "AAAA") and r.value}
    val_to_id: dict[str, uuid.UUID] = {}
    if ip_vals:
        for rid, host in (await session.execute(
            select(_IPA.id, func.host(_IPA.ip)).where(func.host(_IPA.ip).in_(ip_vals))
        )).all():
            val_to_id[str(host)] = rid
    # zone → 來源 DNS 伺服器（名稱 / id）對照（來源欄顯示用）
    zone_ids = {r.zone_id for r in rows if r.zone_id}
    zone_to_srv: dict[uuid.UUID, tuple[uuid.UUID, str]] = {}
    if zone_ids:
        for zid, sid, sname in (await session.execute(
            select(DNSZone.id, DNSServer.id, DNSServer.name)
            .join(DNSServer, DNSServer.id == DNSZone.server_id)
            .where(DNSZone.id.in_(zone_ids))
        )).all():
            zone_to_srv[zid] = (sid, sname)
    items = []
    for r in rows:
        out = DNSRecordRead.model_validate(r)
        out.matched_ip_id = val_to_id.get(r.value) if r.type in ("A", "AAAA") else None
        srv = zone_to_srv.get(r.zone_id)
        if srv:
            out.server_id, out.server_name = srv
        items.append(out)
    return Paginated[DNSRecordRead](items=items, total=total, page=page, page_size=page_size)


@router.get("/records/type-counts", response_model=list[DNSRecordTypeCount])
async def record_type_counts(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    server_id: uuid.UUID | None = Query(None),
    q: str | None = Query(None),
    ip: str | None = Query(None),
    missing_ip: bool = Query(False),
) -> list[DNSRecordTypeCount]:
    """各記錄型別的筆數（套用除「型別」外的相同篩選），供型別下拉顯示 A (12) 統計。"""
    from sqlalchemy import or_ as _or

    s = select(DNSRecord.type, func.count()).select_from(DNSRecord)
    if server_id is not None:
        zsub = select(DNSZone.id).where(DNSZone.server_id == server_id)
        s = s.where(DNSRecord.zone_id.in_(zsub))
    if q:
        pat = f"%{q.strip()}%"
        s = s.where(_or(DNSRecord.name.ilike(pat), DNSRecord.value.ilike(pat)))
    if missing_ip:
        from app.models.address import IPAddress as _IPA
        ip_here = (
            select(_IPA.id)
            .where(func.host(_IPA.ip) == DNSRecord.value)
            .correlate(DNSRecord).exists()
        )
        s = s.where(DNSRecord.type.in_(("A", "AAAA")), ~ip_here)
    if ip:
        ipx = ip.strip()
        conds = [(DNSRecord.type.in_(("A", "AAAA"))) & (DNSRecord.value == ipx)]
        ptr = _reverse_ptr(ipx)
        if ptr:
            conds.append((DNSRecord.type == "PTR") & (DNSRecord.name == ptr))
        s = s.where(_or(*conds))
    rows = (await session.execute(s.group_by(DNSRecord.type).order_by(DNSRecord.type))).all()
    return [DNSRecordTypeCount(type=t, count=int(c)) for t, c in rows]


def _reverse_ptr(ip: str) -> str | None:
    """IPv4/IPv6 → 反解 PTR 名稱（如 1.1.168.192.in-addr.arpa）。無效 IP 回 None。"""
    import ipaddress as _ipa
    try:
        return _ipa.ip_address(ip.strip()).reverse_pointer
    except ValueError:
        return None


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
