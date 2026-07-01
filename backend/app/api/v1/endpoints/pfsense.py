"""pfSense 防火牆整合 endpoints（與 OPNsense 分開的獨立模組 / 設定頁）。

走 pfSense-pkg-RESTAPI（/api/v2、X-API-Key）。僅 admin 操作；API key 寫入即加密、永不回傳。
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin
from app.core.audit import append_audit
from app.core.db import get_session
from app.models.pfsense import PfSenseFirewall, PfSenseSyncedAlias
from app.schemas.base import StrictModel
from app.services import pfsense as svc

router = APIRouter(
    prefix="/pfsense", tags=["pfsense"], dependencies=[Depends(require_admin)],
)


class PfSenseRead(StrictModel):
    id: uuid.UUID
    name: str
    api_url: str
    enabled: bool
    verify_tls: bool
    has_key: bool = False
    sync_interval_seconds: int
    sync_dhcp: bool
    sync_arp: bool
    sync_aliases: bool
    sync_rules: bool
    expose_dsv: bool
    scope_subnet_ids: list[uuid.UUID] | None = None
    description: str | None = None
    alias_count: int = 0
    rule_count: int = 0
    last_sync_at: Any = None
    last_error: str | None = None
    created_at: Any = None
    updated_at: Any = None


class PfSenseCreate(StrictModel):
    name: str
    api_url: str
    api_key: str
    verify_tls: bool = True
    enabled: bool = True
    sync_interval_seconds: int = 300
    sync_dhcp: bool = False
    sync_arp: bool = True
    sync_aliases: bool = False
    sync_rules: bool = False
    expose_dsv: bool = False
    scope_subnet_ids: list[uuid.UUID] | None = None
    description: str | None = None


class PfSenseUpdate(StrictModel):
    name: str | None = None
    api_url: str | None = None
    api_key: str | None = None   # 非空才更新；不給/空＝保留
    verify_tls: bool | None = None
    enabled: bool | None = None
    sync_interval_seconds: int | None = None
    sync_dhcp: bool | None = None
    sync_arp: bool | None = None
    sync_aliases: bool | None = None
    sync_rules: bool | None = None
    expose_dsv: bool | None = None
    scope_subnet_ids: list[uuid.UUID] | None = None
    description: str | None = None


def _to_read(fw: PfSenseFirewall, alias_count: int = 0) -> PfSenseRead:
    m = PfSenseRead.model_validate(fw)
    m.has_key = bool(fw.api_key_enc)
    m.alias_count = alias_count
    m.rule_count = len(fw.rules or [])
    return m


async def _get_or_404(session: AsyncSession, fw_id: uuid.UUID) -> PfSenseFirewall:
    fw = await session.get(PfSenseFirewall, fw_id)
    if fw is None:
        raise HTTPException(404, detail="pfSense firewall not found")
    return fw


@router.get("")
async def list_firewalls(
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    total = (await session.execute(select(func.count(PfSenseFirewall.id)))).scalar_one()
    rows = (await session.execute(
        select(PfSenseFirewall).order_by(PfSenseFirewall.name).limit(limit).offset(offset)
    )).scalars().all()
    counts = dict((await session.execute(
        select(PfSenseSyncedAlias.firewall_id, func.count(PfSenseSyncedAlias.id))
        .group_by(PfSenseSyncedAlias.firewall_id)
    )).all())
    return {"items": [_to_read(fw, int(counts.get(fw.id, 0))) for fw in rows], "total": total}


@router.post("", status_code=201)
async def create_firewall(
    payload: PfSenseCreate, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PfSenseRead:
    fw_id = uuid.uuid4()
    enc, nonce = svc.encrypt_api_key(fw_id, payload.api_key)
    fw = PfSenseFirewall(
        id=fw_id, name=payload.name, api_url=payload.api_url,
        api_key_enc=enc, api_key_nonce=nonce,
        verify_tls=payload.verify_tls, enabled=payload.enabled,
        sync_interval_seconds=payload.sync_interval_seconds,
        sync_dhcp=payload.sync_dhcp, sync_arp=payload.sync_arp, sync_aliases=payload.sync_aliases,
        scope_subnet_ids=payload.scope_subnet_ids, description=payload.description,
    )
    session.add(fw)
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="pfsense_firewall", object_id=fw_id, action="create",
        diff={"name": payload.name, "api_url": payload.api_url},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(fw)
    return _to_read(fw)


@router.get("/{fw_id}")
async def get_firewall(
    fw_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_session)],
) -> PfSenseRead:
    fw = await _get_or_404(session, fw_id)
    n = (await session.execute(
        select(func.count(PfSenseSyncedAlias.id)).where(PfSenseSyncedAlias.firewall_id == fw_id)
    )).scalar_one()
    return _to_read(fw, int(n))


@router.patch("/{fw_id}")
async def update_firewall(
    fw_id: uuid.UUID, payload: PfSenseUpdate, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PfSenseRead:
    fw = await _get_or_404(session, fw_id)
    data = payload.model_dump(exclude_unset=True)
    if "api_key" in data:
        key = data.pop("api_key")
        if key:
            fw.api_key_enc, fw.api_key_nonce = svc.encrypt_api_key(fw.id, key)
    for k, v in data.items():
        setattr(fw, k, v)
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="pfsense_firewall", object_id=fw.id, action="update",
        diff={k: v for k, v in data.items()},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(fw)
    return _to_read(fw)


@router.delete("/{fw_id}", status_code=204)
async def delete_firewall(
    fw_id: uuid.UUID, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    fw = await _get_or_404(session, fw_id)
    await session.delete(fw)
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="pfsense_firewall", object_id=fw_id, action="delete", diff={},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()


@router.post("/{fw_id}/test")
async def test_firewall(
    fw_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    fw = await _get_or_404(session, fw_id)
    try:
        info = await svc.test_connection(fw)
    except svc.PfSenseError as exc:
        raise HTTPException(502, detail=str(exc)) from exc
    return {"ok": True, "version": info}


@router.post("/{fw_id}/sync")
async def sync_firewall(
    fw_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    fw = await _get_or_404(session, fw_id)
    try:
        counts = await svc.sync_instance(session, fw)
        await session.commit()
    except Exception as exc:  # 失敗寫 last_error 後回 502
        await session.rollback()
        fw = await _get_or_404(session, fw_id)
        fw.last_error = str(exc)[:500]
        await session.commit()
        raise HTTPException(502, detail=str(exc)[:300]) from exc
    return {"ok": True, "counts": counts}


@router.get("/{fw_id}/rules")
async def list_rules(
    fw_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """已同步的防火牆規則（需開 sync_rules）。"""
    fw = await _get_or_404(session, fw_id)
    return {"items": fw.rules or []}


@router.get("/{fw_id}/nat")
async def get_nat(
    fw_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """即時抓 NAT（port forward + outbound mappings）供檢視。"""
    fw = await _get_or_404(session, fw_id)
    try:
        return await svc.fetch_nat(fw)
    except svc.PfSenseError as exc:
        raise HTTPException(502, detail=str(exc)) from exc


@router.get("/{fw_id}/aliases")
async def list_aliases(
    fw_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    await _get_or_404(session, fw_id)
    rows = (await session.execute(
        select(PfSenseSyncedAlias).where(PfSenseSyncedAlias.firewall_id == fw_id)
        .order_by(PfSenseSyncedAlias.name)
    )).scalars().all()
    return {"items": [
        {"name": a.name, "type": a.alias_type, "members": a.members or [], "descr": a.descr}
        for a in rows
    ]}
