"""DNS 整合 endpoints（admin）。"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import String, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin, require_global_read
from app.core.audit import append_audit
from app.core.db import get_session
from app.core.security import encrypt_secret
from app.core.sqlin import in_values
from app.core.ui_error import detail_of, ui_detail
from app.models.dns import DNSRecord, DNSServer, DNSZone
from app.models.dns_compare_group import DNSCompareGroup, DNSCompareGroupDiff
from app.models.encrypted_secret import EncryptedSecret
from app.schemas.base import Paginated
from app.schemas.dns import (
    ConsistencyReportItem,
    DNSCompareGroupCreate,
    DNSCompareGroupDiffRead,
    DNSCompareGroupMember,
    DNSCompareGroupRead,
    DNSCompareGroupUpdate,
    DNSRecordRead,
    DNSRecordTypeCount,
    DNSServerCreate,
    DNSServerRead,
    DNSServerRef,
    DNSServerUpdate,
    DNSZoneRead,
    InconsistentRecord,
)
from app.services.dns import DNSAdapterError, get_adapter
from app.services.dns.factory import explicit_winrm_transport
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
        extra_config=explicit_winrm_transport(payload.type, payload.extra_config),
        enabled=payload.enabled,
        sync_interval_seconds=payload.sync_interval_seconds,
        scope_subnet_ids=payload.scope_subnet_ids,
        compare_group_id=await _valid_group_id(session, payload.compare_group_id),
    )
    await _check_group_kinds(session, obj.compare_group_id, obj.type)
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
        diff={"name": obj.name, "type": obj.type, "api_url": obj.api_url,
              "compare_group_id": str(obj.compare_group_id) if obj.compare_group_id else None},
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
    obj.extra_config = explicit_winrm_transport(obj.type, obj.extra_config)
    if payload.enabled is not None:
        obj.enabled = payload.enabled
    if payload.sync_interval_seconds is not None:
        obj.sync_interval_seconds = payload.sync_interval_seconds
    if payload.scope_subnet_ids is not None:
        obj.scope_subnet_ids = payload.scope_subnet_ids
    group_change: dict[str, Any] = {}
    if "compare_group_id" in payload.model_fields_set and payload.compare_group_id != obj.compare_group_id:
        group_change = {"compare_group_id": {"from": str(obj.compare_group_id) if obj.compare_group_id else None,
                                          "to": str(payload.compare_group_id) if payload.compare_group_id else None}}
        obj.compare_group_id = await _valid_group_id(session, payload.compare_group_id)
    if group_change or payload.type is not None:
        await _check_group_kinds(session, obj.compare_group_id, obj.type, exclude_server_id=obj.id)
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
        diff={"rotated_secrets": rotated, **group_change},
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
    # 它寫進共用表的主機名稱／租約／固定分配／NAT／VPN 通道一併收回（沒有外鍵會跟著刪）
    from app.services.integration_cleanup import forget_instance
    await forget_instance(session, source="dns", source_id=obj.id)
    group_id = obj.compare_group_id
    await session.delete(obj)
    await session.commit()
    # 它原本所在的比對群組要重新比對：差異清單裡還記著這台
    if group_id is not None:
        from app.services.dns_compare import check_group
        g = await session.get(DNSCompareGroup, group_id)
        if g is not None:
            await check_group(session, g)
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
        raise HTTPException(400, detail=detail_of(exc, "dns_adapter_error")) from exc
    try:
        info = await adapter.healthcheck()
    except DNSAdapterError as exc:
        raise HTTPException(502, detail=detail_of(exc, "dns_adapter_error")) from exc
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


# ── 比對群組（互相同步的 DNS 伺服器；services/dns_compare）──────────────────

async def _valid_group_id(session: AsyncSession, gid: uuid.UUID | None) -> uuid.UUID | None:
    if gid is not None and await session.get(DNSCompareGroup, gid) is None:
        raise HTTPException(404, detail=ui_detail("dns_compare_group_not_found", "DNS comparison group not found"))
    return gid


async def _group_out(session: AsyncSession, g: DNSCompareGroup, counts: dict[uuid.UUID, int] | None = None
                     ) -> DNSCompareGroupRead:
    from app.services.dns_compare import diff_counts
    members = (await session.execute(select(DNSServer).where(DNSServer.compare_group_id == g.id)
                                     .order_by(DNSServer.name))).scalars().all()
    counts = counts if counts is not None else await diff_counts(session)
    out = DNSCompareGroupRead.model_validate(g)
    out.members = [DNSCompareGroupMember.model_validate(m) for m in members]
    out.diff_count = counts.get(g.id, 0)
    if members:
        from app.services.dns_compare import normalize_zone_list
        out.zones = normalize_zone_list(list((await session.execute(
            select(DNSZone.name).where(in_values(DNSZone.server_id, [m.id for m in members])).distinct())).scalars().all()))
    return out


async def _recheck(session: AsyncSession, g: DNSCompareGroup) -> None:
    """成員或不比對的 zone 改了：馬上重新比對，差異清單才對得起來（不等下一次拉取）。"""
    from app.services.dns_compare import check_group
    await check_group(session, g)
    await session.commit()
    await session.refresh(g)


def _raise_mixed_kinds() -> None:
    raise HTTPException(422, detail=ui_detail(
        "dns_compare_group_mixed_kinds",
        "Unbound (OPNsense) holds host overrides rather than full zones; it can only share a comparison group "
        "with other Unbound servers"))


async def _check_group_kinds(session: AsyncSession, group_id: uuid.UUID | None, server_type: str,
                             *, exclude_server_id: uuid.UUID | None = None) -> None:
    """伺服器加入群組（或已在組裡的伺服器改類型）時：不能混搭的組合直接擋下（services/dns_compare）。"""
    if group_id is None:
        return
    from app.services.dns_compare import incompatible_types
    q = select(DNSServer.type).where(DNSServer.compare_group_id == group_id)
    if exclude_server_id is not None:
        q = q.where(DNSServer.id != exclude_server_id)
    others = list((await session.execute(q)).scalars().all())
    if incompatible_types([*others, server_type]):
        _raise_mixed_kinds()


async def _set_members(session: AsyncSession, g: DNSCompareGroup, server_ids: list[uuid.UUID]) -> dict[str, list[str]]:
    from app.services.dns_compare import incompatible_types
    want = set(server_ids)
    found_rows = (await session.execute(select(DNSServer.id, DNSServer.type).where(
        in_values(DNSServer.id, list(want))))).all() if want else []
    found = {sid for sid, _t in found_rows}
    if found != want:
        raise HTTPException(404, detail=ui_detail("dns_server_not_found", "DNS server not found"))
    if incompatible_types([t for _sid, t in found_rows]):
        _raise_mixed_kinds()
    current = set((await session.execute(select(DNSServer.id).where(DNSServer.compare_group_id == g.id))).scalars().all())
    for sid in current - want:
        (await session.get(DNSServer, sid)).compare_group_id = None  # type: ignore[union-attr]
    for sid in want - current:
        (await session.get(DNSServer, sid)).compare_group_id = g.id  # type: ignore[union-attr]
    return {"added": sorted(str(x) for x in want - current), "removed": sorted(str(x) for x in current - want)}


def _audit_kw(user: Any, request: Request) -> dict[str, Any]:
    return {"actor_user_id": str(user.id), "actor_ip": request.client.host if request.client else None,
            "actor_user_agent": request.headers.get("user-agent"),
            "request_id": getattr(request.state, "request_id", None)}


@router.get("/compare-groups", response_model=list[DNSCompareGroupRead], dependencies=[Depends(require_admin)])
async def list_compare_groups(session: Annotated[AsyncSession, Depends(get_session)]) -> list[DNSCompareGroupRead]:
    from app.services.dns_compare import diff_counts
    counts = await diff_counts(session)
    groups = (await session.execute(select(DNSCompareGroup).order_by(DNSCompareGroup.name))).scalars().all()
    return [await _group_out(session, g, counts) for g in groups]


@router.post("/compare-groups", response_model=DNSCompareGroupRead, status_code=201, dependencies=[Depends(require_admin)])
async def create_compare_group(
    payload: DNSCompareGroupCreate, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DNSCompareGroupRead:
    from app.services.dns_compare import normalize_zone_list
    g = DNSCompareGroup(name=payload.name.strip(), description=payload.description,
                     notify_enabled=payload.notify_enabled, grace_minutes=payload.grace_minutes,
                     excluded_zones=normalize_zone_list(payload.excluded_zones))
    session.add(g)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(409, detail=ui_detail("dns_compare_group_name_taken", "DNS comparison group name already exists")
                            ) from exc
    members = await _set_members(session, g, payload.server_ids)
    await append_audit(session, object_type="dns_compare_group", object_id=str(g.id), action="create",
                       diff={"name": g.name, "grace_minutes": g.grace_minutes, "notify_enabled": g.notify_enabled,
                             "members": members["added"], "excluded_zones": g.excluded_zones}, **_audit_kw(user, request))
    await session.commit()
    await session.refresh(g)
    if len(members["added"]) >= 2:
        await _recheck(session, g)
    return await _group_out(session, g)


@router.patch("/compare-groups/{group_id}", response_model=DNSCompareGroupRead, dependencies=[Depends(require_admin)])
async def update_compare_group(
    group_id: uuid.UUID, payload: DNSCompareGroupUpdate, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DNSCompareGroupRead:
    g = await session.get(DNSCompareGroup, group_id)
    if g is None:
        raise HTTPException(404, detail=ui_detail("dns_compare_group_not_found", "DNS comparison group not found"))
    diff: dict[str, Any] = {}
    for field in ("name", "description", "notify_enabled", "grace_minutes"):
        v = getattr(payload, field)
        if field in payload.model_fields_set and v is not None and v != getattr(g, field):
            diff[field] = {"from": getattr(g, field), "to": v}
            setattr(g, field, v.strip() if isinstance(v, str) and field == "name" else v)
    if payload.server_ids is not None:
        members = await _set_members(session, g, payload.server_ids)
        if members["added"] or members["removed"]:
            diff["members"] = members
    if payload.excluded_zones is not None:
        from app.services.dns_compare import normalize_zone_list
        zones = normalize_zone_list(payload.excluded_zones)
        if zones != list(g.excluded_zones or []):
            diff["excluded_zones"] = {"from": list(g.excluded_zones or []), "to": zones}
            g.excluded_zones = zones
    await append_audit(session, object_type="dns_compare_group", object_id=str(g.id), action="update",
                       diff=diff, **_audit_kw(user, request))
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(409, detail=ui_detail("dns_compare_group_name_taken", "DNS comparison group name already exists")
                            ) from exc
    await session.refresh(g)
    if "members" in diff or "excluded_zones" in diff:
        await _recheck(session, g)
    return await _group_out(session, g)


@router.delete("/compare-groups/{group_id}", status_code=204, dependencies=[Depends(require_admin)])
async def delete_compare_group(
    group_id: uuid.UUID, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    g = await session.get(DNSCompareGroup, group_id)
    if g is None:
        raise HTTPException(404, detail=ui_detail("dns_compare_group_not_found", "DNS comparison group not found"))
    members = await _set_members(session, g, [])
    await append_audit(session, object_type="dns_compare_group", object_id=str(g.id), action="delete",
                       diff={"name": g.name, "members": members["removed"]}, **_audit_kw(user, request))
    await session.delete(g)
    await session.commit()


@router.post("/compare-groups/{group_id}/check", dependencies=[Depends(require_admin)])
async def check_compare_group(
    group_id: uuid.UUID, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """立即比對（用已經拉取下來的紀錄，不連 DNS）。要拿最新資料先按各台的「拉取」。"""
    from app.services.dns_compare import check_group
    g = await session.get(DNSCompareGroup, group_id)
    if g is None:
        raise HTTPException(404, detail=ui_detail("dns_compare_group_not_found", "DNS comparison group not found"))
    result = await check_group(session, g)
    await append_audit(session, object_type="dns_compare_group", object_id=str(g.id), action="check",
                       diff={k: result.get(k) for k in ("status", "diffs", "confirmed", "alerted")},
                       **_audit_kw(user, request))
    await session.commit()
    return result


@router.get("/compare-groups/{group_id}/diffs", response_model=list[DNSCompareGroupDiffRead],
            dependencies=[Depends(require_admin)])
async def list_compare_group_diffs(
    group_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = Query(1000, ge=1, le=10_000),
) -> list[DNSCompareGroupDiffRead]:
    g = await session.get(DNSCompareGroup, group_id)
    if g is None:
        raise HTTPException(404, detail=ui_detail("dns_compare_group_not_found", "DNS comparison group not found"))
    names = {str(i): n for i, n in (await session.execute(select(DNSServer.id, DNSServer.name))).all()}
    rows = (await session.execute(select(DNSCompareGroupDiff).where(DNSCompareGroupDiff.group_id == g.id)
                                  .order_by(DNSCompareGroupDiff.zone, DNSCompareGroupDiff.kind.desc(),
                                            DNSCompareGroupDiff.name, DNSCompareGroupDiff.type)
                                  .limit(limit))).scalars().all()

    def refs(ids: list[Any]) -> list[DNSServerRef]:
        return [DNSServerRef(id=str(i), name=names.get(str(i), str(i))) for i in ids]
    return [DNSCompareGroupDiffRead(id=d.id, kind=d.kind, zone=d.zone, name=d.name, type=d.type, value=d.value,
                                 present_on=refs(d.present_on), missing_on=refs(d.missing_on),
                                 first_seen_at=d.first_seen_at, last_seen_at=d.last_seen_at,
                                 confirmed=d.confirmed_at is not None) for d in rows]


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
    merge: bool = Query(False, description="同一個比對群組裡相同的紀錄合併成一筆（列出哪幾台都有）"),
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

    merged_servers: dict[uuid.UUID, list[str]] = {}
    if merge:
        # 視窗函數：每組相同紀錄挑一筆代表（伺服器名稱最前面的那台），另外帶出整組的伺服器名稱；
        # 分頁與總數都在合併後算（不能先抓全部再在 Python 合併：紀錄可能有十萬筆）
        part = _merge_partition()
        base = _apply(
            select(DNSRecord.id.label("rid"),
                   func.row_number().over(partition_by=part, order_by=(DNSServer.name, DNSRecord.id)).label("rn"),
                   func.array_agg(DNSServer.name).over(partition_by=part).label("servers"))
            .join(DNSZone, DNSZone.id == DNSRecord.zone_id).join(DNSServer, DNSServer.id == DNSZone.server_id)
        ).subquery()
        res = (await session.execute(
            select(DNSRecord, base.c.servers).join(base, base.c.rid == DNSRecord.id).where(base.c.rn == 1)
            .order_by(DNSRecord.name, DNSRecord.type).offset((page - 1) * page_size).limit(page_size))).all()
        rows = [r for r, _sv in res]
        merged_servers = {r.id: sorted(sv or []) for r, sv in res}
        total = int(await session.scalar(select(func.count()).select_from(base).where(base.c.rn == 1)) or 0)
    else:
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
            select(_IPA.id, func.host(_IPA.ip)).where(in_values(func.host(_IPA.ip), ip_vals, type_=String()))
        )).all():
            val_to_id[str(host)] = rid
    # zone → 來源 DNS 伺服器（名稱 / id）對照（來源欄顯示用）
    zone_ids = {r.zone_id for r in rows if r.zone_id}
    zone_to_srv: dict[uuid.UUID, tuple[uuid.UUID, str]] = {}
    zone_to_group: dict[uuid.UUID, str | None] = {}
    if zone_ids:
        for zid, sid, sname, gname in (await session.execute(
            select(DNSZone.id, DNSServer.id, DNSServer.name, DNSCompareGroup.name)
            .join(DNSServer, DNSServer.id == DNSZone.server_id)
            .outerjoin(DNSCompareGroup, DNSCompareGroup.id == DNSServer.compare_group_id)
            .where(in_values(DNSZone.id, zone_ids))
        )).all():
            zone_to_srv[zid] = (sid, sname)
            zone_to_group[zid] = gname
    items = []
    for r in rows:
        out = DNSRecordRead.model_validate(r)
        out.matched_ip_id = val_to_id.get(r.value) if r.type in ("A", "AAAA") else None
        srv = zone_to_srv.get(r.zone_id)
        if srv:
            out.server_id, out.server_name = srv
        if merge:
            out.servers = merged_servers.get(r.id) or ([srv[1]] if srv else [])
            out.compare_group_name = zone_to_group.get(r.zone_id)
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
    merge: bool = Query(False),
) -> list[DNSRecordTypeCount]:
    """各記錄型別的筆數（套用除「型別」外的相同篩選），供型別下拉顯示 A (12) 統計。
    merge=true：同一個比對群組裡相同的紀錄算一筆（與清單的合併一致）。"""
    from sqlalchemy import or_ as _or

    if merge:
        o, n, _t, v = _merge_partition()
        s = (select(DNSRecord.type, func.count(func.distinct(o + "|" + n + "|" + v))).select_from(DNSRecord)
             .join(DNSZone, DNSZone.id == DNSRecord.zone_id).join(DNSServer, DNSServer.id == DNSZone.server_id))
    else:
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


def _merge_partition():  # type: ignore[no-untyped-def]
    """合併鍵：同一組（沒分組＝自己一台一組）＋正規化後的名稱、型別、值（還沒正規化的舊紀錄退回小寫）。
    與 services/dns_compare.merge_key 同一個規則。"""
    from sqlalchemy import String as _S
    from sqlalchemy import cast as _cast
    owner = func.coalesce("g:" + _cast(DNSServer.compare_group_id, _S), "s:" + _cast(DNSServer.id, _S))
    return (owner, func.coalesce(DNSRecord.name_norm, func.lower(DNSRecord.name)), DNSRecord.type,
            func.coalesce(DNSRecord.value_norm, func.lower(DNSRecord.value)))


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
