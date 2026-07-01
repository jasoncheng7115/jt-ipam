"""OPNsense 防火牆 / alias mapping 管理 endpoints。

僅 admin 操作；同步動作另有專屬 endpoint（避免一般 GET 觸發長時間 IO）。
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import require_admin
from app.core.audit import append_audit
from app.core.db import get_session
from app.models.firewall import OPNsenseAliasMapping, OPNsenseFirewall
from app.schemas.base import Paginated, StrictModel
from app.schemas.firewall import (
    OPNsenseAliasMappingCreate,
    OPNsenseAliasMappingRead,
    OPNsenseAliasMappingUpdate,
    OPNsenseFirewallCreate,
    OPNsenseFirewallRead,
    OPNsenseFirewallUpdate,
)
from app.services import opnsense_firewall as fw_service

router = APIRouter(
    prefix="/firewalls/opnsense",
    tags=["firewall"],
    dependencies=[Depends(require_admin)],
)


# ─────────────────── Firewalls CRUD ───────────────────


@router.get("", response_model=Paginated[OPNsenseFirewallRead])
async def list_firewalls(
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Any:
    total = (
        await session.execute(select(func.count()).select_from(OPNsenseFirewall))
    ).scalar_one()
    rows = (
        await session.execute(
            select(OPNsenseFirewall).order_by(OPNsenseFirewall.created_at.desc())
            .offset(offset).limit(limit)
        )
    ).scalars().all()
    return {"items": rows, "total": total, "page": offset // limit + 1, "page_size": limit}


@router.post("", response_model=OPNsenseFirewallRead, status_code=status.HTTP_201_CREATED)
async def create_firewall(
    payload: OPNsenseFirewallCreate,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Any:
    fw = OPNsenseFirewall(
        name=payload.name,
        api_url=str(payload.api_url).rstrip("/"),
        enabled=payload.enabled,
        verify_tls=payload.verify_tls,
        sync_interval_seconds=payload.sync_interval_seconds,
        sync_dhcp=payload.sync_dhcp,
        sync_arp=payload.sync_arp,
        sync_openvpn=payload.sync_openvpn,
        sync_rules=payload.sync_rules,
        sync_nat=payload.sync_nat,
        sync_aliases=payload.sync_aliases,
        expose_dsv=payload.expose_dsv,
        description=payload.description,
        scope_location_id=payload.scope_location_id,
        scope_customer_id=payload.scope_customer_id,
        scope_subnet_ids=payload.scope_subnet_ids,
        iface_subnet_map=(
            {k: str(v) for k, v in payload.iface_subnet_map.items()}
            if payload.iface_subnet_map else None
        ),
        api_key_enc=b"placeholder", api_key_nonce=b"placeholder",
        api_secret_enc=b"placeholder", api_secret_nonce=b"placeholder",
    )
    session.add(fw)
    await session.flush()
    creds = fw_service.encrypt_credentials(fw.id, payload.api_key, payload.api_secret)
    fw.api_key_enc = creds["api_key_enc"]
    fw.api_key_nonce = creds["api_key_nonce"]
    fw.api_secret_enc = creds["api_secret_enc"]
    fw.api_secret_nonce = creds["api_secret_nonce"]
    await session.flush()
    await append_audit(
        session,
        actor_user_id=str(getattr(request.state, "user_id", "")) or None,
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="opnsense_firewall", object_id=str(fw.id),
        action="create",
        diff={"name": fw.name, "api_url": fw.api_url},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(fw)
    return fw


@router.patch("/{fw_id}", response_model=OPNsenseFirewallRead)
async def update_firewall(
    fw_id: uuid.UUID,
    payload: OPNsenseFirewallUpdate,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Any:
    fw = (
        await session.execute(select(OPNsenseFirewall).where(OPNsenseFirewall.id == fw_id))
    ).scalar_one_or_none()
    if fw is None:
        raise HTTPException(404, detail="firewall not found")
    # mode="json" 確保 HttpUrl 之類 pydantic 特殊型別 → 純 JSON 可序列化值（audit 用 JSONB 存）
    data = payload.model_dump(exclude_unset=True, mode="json")
    new_key = data.pop("api_key", None)
    new_secret = data.pop("api_secret", None)
    for k, v in data.items():
        if k == "api_url" and v is not None:
            v = str(v).rstrip("/")
        setattr(fw, k, v)
    if new_key is not None or new_secret is not None:
        # 雙欄都要重寫；如果只給一個就拒絕（避免不一致）
        if new_key is None or new_secret is None:
            raise HTTPException(400, detail="Provide both api_key and api_secret to rotate")
        creds = fw_service.encrypt_credentials(fw.id, new_key, new_secret)
        fw.api_key_enc = creds["api_key_enc"]
        fw.api_key_nonce = creds["api_key_nonce"]
        fw.api_secret_enc = creds["api_secret_enc"]
        fw.api_secret_nonce = creds["api_secret_nonce"]
    await append_audit(
        session,
        actor_user_id=str(getattr(request.state, "user_id", "")) or None,
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="opnsense_firewall", object_id=str(fw.id),
        action="update", diff=data,
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(fw)
    return fw


@router.delete("/{fw_id}", status_code=204)
async def delete_firewall(
    fw_id: uuid.UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    fw = (
        await session.execute(select(OPNsenseFirewall).where(OPNsenseFirewall.id == fw_id))
    ).scalar_one_or_none()
    if fw is None:
        raise HTTPException(404, detail="firewall not found")
    await session.delete(fw)
    await append_audit(
        session,
        actor_user_id=str(getattr(request.state, "user_id", "")) or None,
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="opnsense_firewall", object_id=str(fw_id),
        action="delete", diff={"name": fw.name},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()


@router.post("/{fw_id}/test")
async def test_firewall(
    fw_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    fw = (
        await session.execute(select(OPNsenseFirewall).where(OPNsenseFirewall.id == fw_id))
    ).scalar_one_or_none()
    if fw is None:
        raise HTTPException(404, detail="firewall not found")
    try:
        info = await fw_service.healthcheck(fw)
    except fw_service.OPNsenseError as exc:
        raise HTTPException(502, detail=str(exc)) from exc
    return {"ok": True, "alias_count": len(info.get("alias", {}).get("aliases", {}).get("alias", {}) or {})}


@router.get("/dhcp-ranges")
async def list_dhcp_ranges(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[dict[str, Any]]:
    """所有從 DHCP server 同步回來的發放範圍（給 IP 清單標示 DHCP 用）。"""
    from app.models.dhcp import DHCPPoolRange
    from app.models.firewall import OPNsenseFirewall
    rows = (await session.execute(select(DHCPPoolRange))).scalars().all()
    fw_names = dict(
        (await session.execute(select(OPNsenseFirewall.id, OPNsenseFirewall.name))).all()
    )
    return [{
        "id": str(r.id), "firewall_id": str(r.firewall_id), "subnet_cidr": r.subnet_cidr,
        "start_ip": r.start_ip, "end_ip": r.end_ip, "family": r.family, "source": r.source,
        "firewall_name": fw_names.get(r.firewall_id),
    } for r in rows]


@router.get("/{fw_id}/aliases")
async def list_synced_aliases(
    fw_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[dict[str, Any]]:
    """從 OPNsense 同步回來的 alias 定義（唯讀）。"""
    from app.models.firewall import OPNsenseSyncedAlias
    rows = (await session.execute(
        select(OPNsenseSyncedAlias)
        .where(OPNsenseSyncedAlias.firewall_id == fw_id)
        .order_by(OPNsenseSyncedAlias.name)
    )).scalars().all()
    return [{
        "id": str(a.id), "name": a.name, "alias_type": a.alias_type,
        "description": a.description, "enabled": a.enabled,
        "content": a.content or [], "member_count": a.member_count,
        "last_synced_at": a.last_synced_at.isoformat() if a.last_synced_at else None,
    } for a in rows]


@router.post("/{fw_id}/sync")
async def sync_firewall(
    fw_id: uuid.UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """非同步 — 立刻回 task_id，sync 在背景跑（會做 alias mappings + DHCP/ARP/OpenVPN）。"""
    from app.services.background_tasks import spawn_task

    fw = (
        await session.execute(select(OPNsenseFirewall).where(OPNsenseFirewall.id == fw_id))
    ).scalar_one_or_none()
    if fw is None:
        raise HTTPException(404, detail="firewall not found")

    actor_ip = request.client.host if request.client else None
    actor_ua = request.headers.get("user-agent")
    request_id = getattr(request.state, "request_id", None)
    fw_name = fw.name
    fw_id_uuid = fw.id

    async def _runner(sess: AsyncSession, _task) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        inst = await sess.get(OPNsenseFirewall, fw_id_uuid)
        if inst is None:
            raise RuntimeError("OPNsense firewall disappeared")
        try:
            results = await fw_service.sync_all_for_firewall(sess, inst)
        except fw_service.OPNsenseError as exc:
            inst.last_error = str(exc)
            await sess.commit()
            raise
        await append_audit(
            sess,
            actor_user_id=str(getattr(request.state, "user_id", "")) or None,
            actor_ip=actor_ip,
            actor_user_agent=actor_ua,
            object_type="opnsense_firewall", object_id=str(inst.id),
            action="sync", diff={"results": results},
            request_id=request_id,
        )
        await sess.commit()
        # 摘要：把每個 task 的 matched 數加起來
        return {
            "firewall": inst.name,
            "tasks": len(results),
            "details": results,
        }

    task = await spawn_task(
        session=session,
        kind="opnsense.sync",
        target_type="opnsense_firewall",
        target_id=fw_id_uuid,
        target_label=fw_name,
        runner=_runner,
    )
    return {"task_id": str(task.id), "status": task.status,
            "queued_at": task.queued_at.isoformat()}


# ─────────────────── Alias mappings CRUD ───────────────────


@router.get("/mappings", response_model=Paginated[OPNsenseAliasMappingRead])
async def list_mappings(
    session: Annotated[AsyncSession, Depends(get_session)],
    firewall_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Any:
    base = select(OPNsenseAliasMapping)
    if firewall_id is not None:
        base = base.where(OPNsenseAliasMapping.firewall_id == firewall_id)
    total = (
        await session.execute(select(func.count()).select_from(base.subquery()))
    ).scalar_one()
    rows = (
        await session.execute(
            base.order_by(OPNsenseAliasMapping.alias_name).offset(offset).limit(limit)
        )
    ).scalars().all()
    return {"items": rows, "total": total, "page": offset // limit + 1, "page_size": limit}


@router.post("/mappings", response_model=OPNsenseAliasMappingRead,
             status_code=status.HTTP_201_CREATED)
async def create_mapping(
    payload: OPNsenseAliasMappingCreate,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Any:
    fw = (
        await session.execute(
            select(OPNsenseFirewall).where(OPNsenseFirewall.id == payload.firewall_id)
        )
    ).scalar_one_or_none()
    if fw is None:
        raise HTTPException(404, detail="firewall not found")
    raw = payload.model_dump(mode="json")
    obj = OPNsenseAliasMapping(
        firewall_id=payload.firewall_id,
        alias_name=payload.alias_name,
        alias_type=payload.alias_type,
        selector=raw["selector"],
        direction=payload.direction,
    )
    session.add(obj)
    await session.flush()
    await append_audit(
        session,
        actor_user_id=str(getattr(request.state, "user_id", "")) or None,
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="opnsense_alias_mapping", object_id=str(obj.id),
        action="create", diff=raw,
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return obj


@router.patch("/mappings/{mapping_id}", response_model=OPNsenseAliasMappingRead)
async def update_mapping(
    mapping_id: uuid.UUID,
    payload: OPNsenseAliasMappingUpdate,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Any:
    obj = (
        await session.execute(
            select(OPNsenseAliasMapping).where(OPNsenseAliasMapping.id == mapping_id)
        )
    ).scalar_one_or_none()
    if obj is None:
        raise HTTPException(404, detail="mapping not found")
    data = payload.model_dump(exclude_unset=True, mode="json")
    for k, v in data.items():
        setattr(obj, k, v)
    await append_audit(
        session,
        actor_user_id=str(getattr(request.state, "user_id", "")) or None,
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="opnsense_alias_mapping", object_id=str(obj.id),
        action="update", diff=data,
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return obj


@router.delete("/mappings/{mapping_id}", status_code=204)
async def delete_mapping(
    mapping_id: uuid.UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    obj = (
        await session.execute(
            select(OPNsenseAliasMapping).where(OPNsenseAliasMapping.id == mapping_id)
        )
    ).scalar_one_or_none()
    if obj is None:
        raise HTTPException(404, detail="mapping not found")
    await session.delete(obj)
    await append_audit(
        session,
        actor_user_id=str(getattr(request.state, "user_id", "")) or None,
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="opnsense_alias_mapping", object_id=str(mapping_id),
        action="delete", diff={"alias_name": obj.alias_name},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()


@router.post("/mappings/{mapping_id}/sync")
async def sync_one_mapping(
    mapping_id: uuid.UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    obj = (
        await session.execute(
            select(OPNsenseAliasMapping).where(OPNsenseAliasMapping.id == mapping_id)
        )
    ).scalar_one_or_none()
    if obj is None:
        raise HTTPException(404, detail="mapping not found")
    try:
        summary = await fw_service.sync_mapping(session, obj)
    except fw_service.OPNsenseError as exc:
        await session.commit()  # 紀錄 last_error
        raise HTTPException(502, detail=str(exc)) from exc
    await append_audit(
        session,
        actor_user_id=str(getattr(request.state, "user_id", "")) or None,
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="opnsense_alias_mapping", object_id=str(obj.id),
        action="sync", diff=summary,
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return summary


# ─────────────────── 防火牆規則（從 OPNsense 拉的快取，唯讀）───────────────────


class _RuleOut(StrictModel):
    id: uuid.UUID
    firewall_id: uuid.UUID
    legacy_uuid: str
    enabled: bool
    sequence: int | None
    action: str | None
    interface: str | None
    direction: str | None
    protocol: str | None
    source_net: str | None
    source_port: str | None
    destination_net: str | None
    destination_port: str | None
    description: str | None
    last_synced_at: Any


@router.get("/{fw_id}/rules", response_model=Paginated[_RuleOut])
async def list_rules(
    fw_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(200, ge=1, le=1000),
) -> Paginated[_RuleOut]:
    from app.models.firewall_rule import OPNsenseRule
    stmt = (
        select(OPNsenseRule)
        .where(OPNsenseRule.firewall_id == fw_id)
        .order_by(OPNsenseRule.sequence.nulls_last(), OPNsenseRule.legacy_uuid)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    total = int(await session.scalar(
        select(func.count()).select_from(OPNsenseRule).where(OPNsenseRule.firewall_id == fw_id)
    ) or 0)
    return Paginated[_RuleOut](
        items=[_RuleOut.model_validate(r, from_attributes=True) for r in rows],
        total=total, page=page, page_size=page_size,
    )
