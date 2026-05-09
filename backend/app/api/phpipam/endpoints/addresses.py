"""phpIPAM `/addresses/`：唯讀 + 第一空閒查詢。"""

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
)
from app.core.db import get_session
from app.models.address import IPAddress
from app.models.subnet import Subnet
from app.services.permission import get_object_permission, has_permission
from app.services.subnet import find_first_free_address

router = APIRouter()


async def _ensure_subnet_read(session: AsyncSession, user, subnet_id: uuid.UUID) -> Subnet:
    s = await session.get(Subnet, subnet_id)
    if s is None:
        raise HTTPException(404, detail="Not found")
    level = await get_object_permission(
        session, user=user, object_type="subnet", object_id=s.id
    )
    if not has_permission(level, "read"):
        raise HTTPException(404, detail="Not found")
    return s


@router.get("/{app_id}/addresses/{address_id}/")
async def get_address(
    app_id: str,
    address_id: uuid.UUID,
    user=Depends(phpipam_current_user),
    session: Annotated[AsyncSession, Depends(get_session)] = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    a = await session.get(IPAddress, address_id)
    if a is None:
        raise HTTPException(404, detail="Address not found")
    await _ensure_subnet_read(session, user, a.subnet_id)
    return phpipam_response(success=True, data=address_to_phpipam(a), started=started)


@router.get("/{app_id}/addresses/{ip}/{subnet_id}/")
async def get_address_by_ip_subnet(
    app_id: str,
    ip: str,
    subnet_id: uuid.UUID,
    user=Depends(phpipam_current_user),
    session: Annotated[AsyncSession, Depends(get_session)] = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    await _ensure_subnet_read(session, user, subnet_id)
    a = (
        await session.execute(
            select(IPAddress).where(
                IPAddress.subnet_id == subnet_id,
                IPAddress.ip == ip,
            )
        )
    ).scalar_one_or_none()
    if a is None:
        raise HTTPException(404, detail="Address not found")
    return phpipam_response(success=True, data=address_to_phpipam(a), started=started)


@router.get("/{app_id}/addresses/search/{ip}/")
async def search_by_ip(
    app_id: str,
    ip: str,
    user=Depends(phpipam_current_user),
    session: Annotated[AsyncSession, Depends(get_session)] = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    rows = list(
        (await session.execute(select(IPAddress).where(IPAddress.ip == ip))).scalars().all()
    )
    # 過濾 user 對 subnet 有權的
    out = []
    for r in rows:
        try:
            await _ensure_subnet_read(session, user, r.subnet_id)
        except HTTPException:
            continue
        out.append(address_to_phpipam(r))
    return phpipam_response(success=True, data=out, started=started)


@router.get("/{app_id}/addresses/search_hostname/{hostname}/")
async def search_by_hostname(
    app_id: str,
    hostname: str,
    user=Depends(phpipam_current_user),
    session: Annotated[AsyncSession, Depends(get_session)] = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    rows = list(
        (
            await session.execute(
                select(IPAddress).where(IPAddress.hostname == hostname)
            )
        ).scalars().all()
    )
    out = []
    for r in rows:
        try:
            await _ensure_subnet_read(session, user, r.subnet_id)
        except HTTPException:
            continue
        out.append(address_to_phpipam(r))
    return phpipam_response(success=True, data=out, started=started)


@router.get("/{app_id}/addresses/first_free/{subnet_id}/")
async def first_free(
    app_id: str,
    subnet_id: uuid.UUID,
    user=Depends(phpipam_current_user),
    session: Annotated[AsyncSession, Depends(get_session)] = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    s = await _ensure_subnet_read(session, user, subnet_id)
    ip = await find_first_free_address(session, s)
    if ip is None:
        return phpipam_response(success=False, code=404, message="No free address", started=started)
    return phpipam_response(success=True, data=ip, started=started)
