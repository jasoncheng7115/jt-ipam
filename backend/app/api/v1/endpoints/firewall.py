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
from app.schemas.base import Paginated
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
        description=payload.description,
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
    data = payload.model_dump(exclude_unset=True)
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


@router.post("/{fw_id}/sync")
async def sync_firewall(
    fw_id: uuid.UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    fw = (
        await session.execute(select(OPNsenseFirewall).where(OPNsenseFirewall.id == fw_id))
    ).scalar_one_or_none()
    if fw is None:
        raise HTTPException(404, detail="firewall not found")
    try:
        results = await fw_service.sync_all_for_firewall(session, fw)
    except fw_service.OPNsenseError as exc:
        fw.last_error = str(exc)
        await session.commit()
        raise HTTPException(502, detail=str(exc)) from exc
    await append_audit(
        session,
        actor_user_id=str(getattr(request.state, "user_id", "")) or None,
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="opnsense_firewall", object_id=str(fw.id),
        action="sync", diff={"results": results},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return {"firewall": fw.name, "results": results}


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
