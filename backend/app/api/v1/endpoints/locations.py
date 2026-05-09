"""Location + Rack endpoints。"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin
from app.core.audit import append_audit
from app.core.db import get_session
from app.models.location import Location, Rack
from app.schemas.base import Paginated
from app.schemas.location import (
    LocationCreate,
    LocationRead,
    LocationUpdate,
    RackCreate,
    RackRead,
    RackUpdate,
)

router = APIRouter(tags=["locations"])


# ─────────────────── Locations ───────────────────
@router.get("/locations", response_model=Paginated[LocationRead])
async def list_locations(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=500),
) -> Paginated[LocationRead]:
    rows = list(
        (await session.execute(
            select(Location).order_by(Location.name).offset((page - 1) * page_size).limit(page_size)
        )).scalars().all()
    )
    total = int(await session.scalar(select(func.count()).select_from(Location)) or 0)
    return Paginated[LocationRead](
        items=[LocationRead.model_validate(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.get("/locations/{location_id}", response_model=LocationRead)
async def get_location(
    location_id: uuid.UUID,
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LocationRead:
    obj = await session.get(Location, location_id)
    if obj is None:
        raise HTTPException(404, detail="Location not found")
    return LocationRead.model_validate(obj)


@router.post("/locations", response_model=LocationRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_location(
    payload: LocationCreate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LocationRead:
    obj = Location(**payload.model_dump())
    session.add(obj)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="Location name conflict") from exc
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="location", object_id=str(obj.id), action="create",
        diff={"after": payload.model_dump(mode="json")},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return LocationRead.model_validate(obj)


@router.patch("/locations/{location_id}", response_model=LocationRead,
              dependencies=[Depends(require_admin)])
async def update_location(
    location_id: uuid.UUID,
    payload: LocationUpdate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LocationRead:
    obj = await session.get(Location, location_id)
    if obj is None:
        raise HTTPException(404, detail="Location not found")
    before = {"name": obj.name, "address": obj.address}
    changes = payload.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(obj, k, v)
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="location", object_id=str(obj.id), action="update",
        diff={"before": before, "changes": changes},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return LocationRead.model_validate(obj)


@router.delete("/locations/{location_id}", status_code=204,
               dependencies=[Depends(require_admin)])
async def delete_location(
    location_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    obj = await session.get(Location, location_id)
    if obj is None:
        raise HTTPException(404, detail="Location not found")
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="location", object_id=str(obj.id), action="delete",
        diff={"before": {"name": obj.name}},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.delete(obj)
    await session.commit()


# ─────────────────── Racks ───────────────────
@router.get("/racks", response_model=Paginated[RackRead])
async def list_racks(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    location_id: uuid.UUID | None = Query(None),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=500),
) -> Paginated[RackRead]:
    stmt = select(Rack)
    cstmt = select(func.count()).select_from(Rack)
    if location_id is not None:
        stmt = stmt.where(Rack.location_id == location_id)
        cstmt = cstmt.where(Rack.location_id == location_id)
    stmt = stmt.order_by(Rack.name).offset((page - 1) * page_size).limit(page_size)
    rows = list((await session.execute(stmt)).scalars().all())
    total = int(await session.scalar(cstmt) or 0)
    return Paginated[RackRead](
        items=[RackRead.model_validate(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.post("/racks", response_model=RackRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_rack(
    payload: RackCreate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RackRead:
    obj = Rack(**payload.model_dump())
    session.add(obj)
    await session.flush()
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="rack", object_id=str(obj.id), action="create",
        diff={"after": payload.model_dump(mode="json")},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return RackRead.model_validate(obj)


@router.patch("/racks/{rack_id}", response_model=RackRead,
              dependencies=[Depends(require_admin)])
async def update_rack(
    rack_id: uuid.UUID,
    payload: RackUpdate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RackRead:
    obj = await session.get(Rack, rack_id)
    if obj is None:
        raise HTTPException(404, detail="Rack not found")
    before = {"name": obj.name, "u_height": obj.u_height,
              "location_id": str(obj.location_id) if obj.location_id else None}
    changes = payload.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(obj, k, v)
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="rack", object_id=str(obj.id), action="update",
        diff={"before": before, "changes": changes},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return RackRead.model_validate(obj)


@router.delete("/racks/{rack_id}", status_code=204,
               dependencies=[Depends(require_admin)])
async def delete_rack(
    rack_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    obj = await session.get(Rack, rack_id)
    if obj is None:
        raise HTTPException(404, detail="Rack not found")
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="rack", object_id=str(obj.id), action="delete",
        diff={"before": {"name": obj.name}},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.delete(obj)
    await session.commit()
