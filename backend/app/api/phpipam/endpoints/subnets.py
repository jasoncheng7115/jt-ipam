"""phpIPAM `/subnets/`：唯讀 + 常用查詢 endpoint。"""

from __future__ import annotations

import time
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.phpipam.helpers import (
    address_to_phpipam,
    phpipam_current_user,
    phpipam_response,
    subnet_to_phpipam,
)
from app.core.db import get_session
from app.models.address import IPAddress
from app.models.subnet import Subnet
from app.services.permission import (
    filter_visible,
    get_object_permission,
    has_permission,
)
from app.services.subnet import find_first_free_address, get_usage

router = APIRouter()


async def _check(session: AsyncSession, user, subnet_id: uuid.UUID) -> Subnet:
    s = await session.get(Subnet, subnet_id)
    if s is None:
        raise HTTPException(404, detail="Subnet not found")
    level = await get_object_permission(
        session, user=user, object_type="subnet", object_id=s.id
    )
    if not has_permission(level, "read"):
        raise HTTPException(404, detail="Subnet not found")
    return s


@router.get("/{app_id}/subnets/{subnet_id}/")
async def get_subnet(
    app_id: str,
    subnet_id: uuid.UUID,
    user=Depends(phpipam_current_user),
    session: Annotated[AsyncSession, Depends(get_session)] = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    s = await _check(session, user, subnet_id)
    return phpipam_response(success=True, data=subnet_to_phpipam(s), started=started)


@router.get("/{app_id}/subnets/cidr/{cidr:path}/")
async def find_by_cidr(
    app_id: str,
    cidr: str,
    user=Depends(phpipam_current_user),
    session: Annotated[AsyncSession, Depends(get_session)] = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    rows = list(
        (
            await session.execute(select(Subnet).where(Subnet.cidr == cidr))
        ).scalars().all()
    )
    visible = set(
        await filter_visible(
            session, user=user, object_type="subnet",
            object_ids=[r.id for r in rows], required="read",
        )
    )
    data = [subnet_to_phpipam(r) for r in rows if r.id in visible]
    return phpipam_response(success=True, data=data, started=started)


@router.get("/{app_id}/subnets/{subnet_id}/usage/")
async def usage(
    app_id: str,
    subnet_id: uuid.UUID,
    user=Depends(phpipam_current_user),
    session: Annotated[AsyncSession, Depends(get_session)] = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    s = await _check(session, user, subnet_id)
    total, used, free, pct = await get_usage(session, s)
    return phpipam_response(
        success=True,
        data={
            "used": str(used),
            "maxhosts": str(total),
            "freehosts": str(free),
            "freehosts_percent": f"{(free / total * 100):.2f}" if total else "0.00",
            "Used_percent": f"{pct:.2f}",
        },
        started=started,
    )


@router.get("/{app_id}/subnets/{subnet_id}/first_free/")
async def first_free(
    app_id: str,
    subnet_id: uuid.UUID,
    user=Depends(phpipam_current_user),
    session: Annotated[AsyncSession, Depends(get_session)] = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    s = await _check(session, user, subnet_id)
    ip = await find_first_free_address(session, s)
    if ip is None:
        return phpipam_response(success=False, code=404, message="No free address", started=started)
    return phpipam_response(success=True, data=ip, started=started)


@router.get("/{app_id}/subnets/{subnet_id}/addresses/")
async def list_addresses(
    app_id: str,
    subnet_id: uuid.UUID,
    user=Depends(phpipam_current_user),
    session: Annotated[AsyncSession, Depends(get_session)] = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    await _check(session, user, subnet_id)
    rows = list(
        (
            await session.execute(
                select(IPAddress).where(IPAddress.subnet_id == subnet_id).order_by(IPAddress.ip)
            )
        ).scalars().all()
    )
    return phpipam_response(
        success=True, data=[address_to_phpipam(r) for r in rows], started=started
    )
