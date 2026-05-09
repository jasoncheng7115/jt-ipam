"""Cabling / Power / VPN endpoints。"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin
from app.core.audit import append_audit
from app.core.db import get_session
from app.models.physical import (
    Cable,
    CableTermination,
    PowerFeed,
    PowerOutlet,
    PowerPanel,
    VPNTunnel,
)
from app.schemas.base import Paginated, StrictModel

router = APIRouter(tags=["physical"])


async def _audit(
    session: AsyncSession, *, user: CurrentUser, request: Request,
    object_type: str, object_id: str | None, action: str, diff: dict | None,
) -> None:
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type=object_type, object_id=object_id, action=action,
        diff=diff,
        request_id=getattr(request.state, "request_id", None),
    )


# ─────────────────── Cables ───────────────────


class CableRead(StrictModel):
    id: uuid.UUID
    label: str | None
    type: str | None
    color: str | None
    length_m: float | None
    description: str | None
    status: str


class CableWrite(StrictModel):
    label: Annotated[str | None, Field(max_length=128)] = None
    type: Annotated[str | None, Field(max_length=32)] = None
    color: Annotated[str | None, Field(max_length=16)] = None
    length_m: Annotated[float | None, Field(ge=0, le=10_000)] = None
    description: Annotated[str | None, Field(max_length=1024)] = None
    status: str = "connected"


class CableTerminationRead(StrictModel):
    id: uuid.UUID
    cable_id: uuid.UUID
    side: str
    object_type: str
    object_id: uuid.UUID
    port_label: str | None


class CableTerminationWrite(StrictModel):
    cable_id: uuid.UUID
    side: Annotated[str, Field(pattern=r"^[AB]$")]
    object_type: Annotated[str, Field(min_length=1, max_length=32)]
    object_id: uuid.UUID
    port_label: Annotated[str | None, Field(max_length=64)] = None


@router.get("/cables", response_model=Paginated[CableRead])
async def list_cables(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(1, ge=1, le=10_000), page_size: int = Query(50, ge=1, le=500),
) -> Paginated[CableRead]:
    rows = list((await session.execute(
        select(Cable).order_by(Cable.created_at.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    total = int(await session.scalar(select(func.count()).select_from(Cable)) or 0)
    return Paginated[CableRead](
        items=[CableRead.model_validate(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.post("/cables", response_model=CableRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_cable(
    payload: CableWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CableRead:
    obj = Cable(**payload.model_dump())
    session.add(obj)
    await session.flush()
    await _audit(session, user=user, request=request, object_type="cable",
                 object_id=str(obj.id), action="create",
                 diff=payload.model_dump(mode="json"))
    await session.commit()
    await session.refresh(obj)
    return CableRead.model_validate(obj)


@router.post("/cable-terminations", response_model=CableTerminationRead,
             status_code=201, dependencies=[Depends(require_admin)])
async def add_termination(
    payload: CableTerminationWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CableTerminationRead:
    obj = CableTermination(**payload.model_dump())
    session.add(obj)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="Side already terminated for this cable") from exc
    await _audit(session, user=user, request=request, object_type="cable_termination",
                 object_id=str(obj.id), action="create",
                 diff=payload.model_dump(mode="json"))
    await session.commit()
    await session.refresh(obj)
    return CableTerminationRead.model_validate(obj)


@router.get("/cables/{cable_id}/trace")
async def trace_cable(
    cable_id: uuid.UUID,
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:  # type: ignore[type-arg]
    """傳回 cable 的 A/B 端 termination。Phase 3.5 將擴展為 multi-cable hop trace。"""
    cable = await session.get(Cable, cable_id)
    if cable is None:
        raise HTTPException(404, detail="Cable not found")
    terms = list((await session.execute(
        select(CableTermination).where(CableTermination.cable_id == cable_id)
    )).scalars().all())
    return {
        "cable": {"id": str(cable.id), "label": cable.label, "type": cable.type,
                  "status": cable.status},
        "terminations": [
            {
                "side": t.side,
                "object_type": t.object_type,
                "object_id": str(t.object_id),
                "port_label": t.port_label,
            }
            for t in sorted(terms, key=lambda x: x.side)
        ],
    }


# ─────────────────── Power ───────────────────


class PowerPanelRead(StrictModel):
    id: uuid.UUID
    name: str
    location_id: uuid.UUID | None
    description: str | None


class PowerPanelWrite(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    location_id: uuid.UUID | None = None
    description: Annotated[str | None, Field(max_length=1024)] = None


class PowerFeedRead(StrictModel):
    id: uuid.UUID
    panel_id: uuid.UUID
    name: str
    voltage_v: int
    amperage_a: int
    phase: str
    supply_type: str
    rack_id: uuid.UUID | None
    description: str | None


class PowerFeedWrite(StrictModel):
    panel_id: uuid.UUID
    name: Annotated[str, Field(min_length=1, max_length=64)]
    voltage_v: Annotated[int, Field(ge=12, le=600)] = 220
    amperage_a: Annotated[int, Field(ge=1, le=400)] = 20
    phase: str = "single"
    supply_type: str = "ac"
    rack_id: uuid.UUID | None = None
    description: Annotated[str | None, Field(max_length=1024)] = None


class PowerOutletRead(StrictModel):
    id: uuid.UUID
    feed_id: uuid.UUID | None
    rack_id: uuid.UUID | None
    label: str
    device_id: uuid.UUID | None
    description: str | None


class PowerOutletWrite(StrictModel):
    feed_id: uuid.UUID | None = None
    rack_id: uuid.UUID | None = None
    label: Annotated[str, Field(min_length=1, max_length=64)]
    device_id: uuid.UUID | None = None
    description: Annotated[str | None, Field(max_length=1024)] = None


@router.get("/power-panels", response_model=Paginated[PowerPanelRead])
async def list_power_panels(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(1, ge=1, le=10_000), page_size: int = Query(50, ge=1, le=500),
) -> Paginated[PowerPanelRead]:
    rows = list((await session.execute(
        select(PowerPanel).order_by(PowerPanel.name)
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    total = int(await session.scalar(select(func.count()).select_from(PowerPanel)) or 0)
    return Paginated[PowerPanelRead](
        items=[PowerPanelRead.model_validate(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.post("/power-panels", response_model=PowerPanelRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_power_panel(
    payload: PowerPanelWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PowerPanelRead:
    obj = PowerPanel(**payload.model_dump())
    session.add(obj)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="Panel name+location conflict") from exc
    await _audit(session, user=user, request=request, object_type="power_panel",
                 object_id=str(obj.id), action="create",
                 diff=payload.model_dump(mode="json"))
    await session.commit()
    await session.refresh(obj)
    return PowerPanelRead.model_validate(obj)


@router.get("/power-feeds", response_model=Paginated[PowerFeedRead])
async def list_power_feeds(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    panel_id: uuid.UUID | None = Query(None),
    page: int = Query(1, ge=1, le=10_000), page_size: int = Query(100, ge=1, le=500),
) -> Paginated[PowerFeedRead]:
    stmt = select(PowerFeed)
    cstmt = select(func.count()).select_from(PowerFeed)
    if panel_id is not None:
        stmt = stmt.where(PowerFeed.panel_id == panel_id)
        cstmt = cstmt.where(PowerFeed.panel_id == panel_id)
    rows = list((await session.execute(
        stmt.order_by(PowerFeed.name).offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    total = int(await session.scalar(cstmt) or 0)
    return Paginated[PowerFeedRead](
        items=[PowerFeedRead.model_validate(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.post("/power-feeds", response_model=PowerFeedRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_power_feed(
    payload: PowerFeedWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PowerFeedRead:
    obj = PowerFeed(**payload.model_dump())
    session.add(obj)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="Feed conflict") from exc
    await _audit(session, user=user, request=request, object_type="power_feed",
                 object_id=str(obj.id), action="create",
                 diff=payload.model_dump(mode="json"))
    await session.commit()
    await session.refresh(obj)
    return PowerFeedRead.model_validate(obj)


@router.post("/power-outlets", response_model=PowerOutletRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_power_outlet(
    payload: PowerOutletWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PowerOutletRead:
    obj = PowerOutlet(**payload.model_dump())
    session.add(obj)
    await session.flush()
    await _audit(session, user=user, request=request, object_type="power_outlet",
                 object_id=str(obj.id), action="create",
                 diff=payload.model_dump(mode="json"))
    await session.commit()
    await session.refresh(obj)
    return PowerOutletRead.model_validate(obj)


# ─────────────────── VPN ───────────────────


class VPNTunnelRead(StrictModel):
    id: uuid.UUID
    name: str
    type: str
    status: str
    a_device_id: uuid.UUID | None
    b_device_id: uuid.UUID | None
    a_endpoint: str | None
    b_endpoint: str | None
    encryption_algo: str | None
    auth_algo: str | None
    description: str | None


class VPNTunnelWrite(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    type: str
    status: str = "active"
    a_device_id: uuid.UUID | None = None
    b_device_id: uuid.UUID | None = None
    a_endpoint: Annotated[str | None, Field(max_length=255)] = None
    b_endpoint: Annotated[str | None, Field(max_length=255)] = None
    encryption_algo: Annotated[str | None, Field(max_length=32)] = None
    auth_algo: Annotated[str | None, Field(max_length=32)] = None
    description: Annotated[str | None, Field(max_length=1024)] = None


@router.get("/vpn-tunnels", response_model=Paginated[VPNTunnelRead])
async def list_vpn(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(1, ge=1, le=10_000), page_size: int = Query(50, ge=1, le=500),
) -> Paginated[VPNTunnelRead]:
    rows = list((await session.execute(
        select(VPNTunnel).order_by(VPNTunnel.name)
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    total = int(await session.scalar(select(func.count()).select_from(VPNTunnel)) or 0)
    return Paginated[VPNTunnelRead](
        items=[VPNTunnelRead.model_validate(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.post("/vpn-tunnels", response_model=VPNTunnelRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_vpn(
    payload: VPNTunnelWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> VPNTunnelRead:
    obj = VPNTunnel(**payload.model_dump())
    session.add(obj)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="VPN tunnel name conflict") from exc
    await _audit(session, user=user, request=request, object_type="vpn_tunnel",
                 object_id=str(obj.id), action="create",
                 diff=payload.model_dump(mode="json"))
    await session.commit()
    await session.refresh(obj)
    return VPNTunnelRead.model_validate(obj)
