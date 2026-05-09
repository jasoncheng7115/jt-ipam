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


from fastapi import Body, Request
from app.core.audit import append_audit
from app.services.address import (
    IPAlreadyExists,
    IPNotInSubnet,
    SubnetFull,
    allocate_first_free,
    create_ip,
)
from app.services.permission import has_permission as _has_perm
from app.services.permission import get_object_permission as _get_perm


def _bool(v: object) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return v != 0
    if isinstance(v, str):
        return v.lower() in {"1", "true", "yes", "on"}
    return False


async def _require_subnet_write(session, user, subnet_id):  # type: ignore[no-untyped-def]
    s = await session.get(Subnet, subnet_id)
    if s is None:
        raise HTTPException(404, detail="Subnet not found")
    level = await _get_perm(session, user=user, object_type="subnet", object_id=s.id)
    if not _has_perm(level, "write"):
        raise HTTPException(404, detail="Subnet not found")
    return s


@router.post("/{app_id}/addresses/")
async def create_address(
    app_id: str,
    request: Request,
    payload: Annotated[dict[str, object], Body()],
    user=Depends(phpipam_current_user),
    session: Annotated[AsyncSession, Depends(get_session)] = None,
) -> dict[str, object]:
    started = time.perf_counter()
    raw_subnet = payload.get("subnetId")
    raw_ip = payload.get("ip")
    if not raw_subnet:
        raise HTTPException(400, detail="subnetId is required")
    try:
        subnet_id = uuid.UUID(str(raw_subnet))
    except ValueError as exc:
        raise HTTPException(400, detail="Invalid subnetId") from exc
    subnet = await _require_subnet_write(session, user, subnet_id)

    try:
        if not raw_ip:
            obj = await allocate_first_free(
                session, subnet=subnet,
                hostname=payload.get("hostname"),  # type: ignore[arg-type]
                description=payload.get("description"),  # type: ignore[arg-type]
                mac=payload.get("mac"),  # type: ignore[arg-type]
                state=str(payload.get("tag") or "active"),
            )
        else:
            obj = await create_ip(
                session, subnet=subnet, ip=str(raw_ip),
                hostname=payload.get("hostname"),  # type: ignore[arg-type]
                description=payload.get("description"),  # type: ignore[arg-type]
                mac=payload.get("mac"),  # type: ignore[arg-type]
                state=str(payload.get("tag") or "active"),
            )
    except IPNotInSubnet as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    except IPAlreadyExists as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    except SubnetFull as exc:
        raise HTTPException(409, detail=str(exc)) from exc

    if "owner" in payload:
        obj.owner = payload["owner"]  # type: ignore[assignment]
    if "port" in payload:
        obj.switch_port = payload["port"]  # type: ignore[assignment]
    if "note" in payload:
        obj.note = payload["note"]  # type: ignore[assignment]
    if "excludePing" in payload:
        obj.exclude_from_ping = _bool(payload["excludePing"])
    if "PTRignore" in payload:
        obj.ptr_ignore = _bool(payload["PTRignore"])

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="ip_address",
        object_id=str(obj.id),
        action="create",
        diff={"after": {"ip": str(obj.ip), "subnet_id": str(obj.subnet_id)}, "via": "phpipam"},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return phpipam_response(
        success=True, code=201, message="Address created",
        data={"id": str(obj.id), "ip": str(obj.ip).split("/")[0]}, started=started,
    )


@router.patch("/{app_id}/addresses/{address_id}/")
async def update_address(
    app_id: str,
    address_id: uuid.UUID,
    request: Request,
    payload: Annotated[dict[str, object], Body()],
    user=Depends(phpipam_current_user),
    session: Annotated[AsyncSession, Depends(get_session)] = None,
) -> dict[str, object]:
    started = time.perf_counter()
    a = await session.get(IPAddress, address_id)
    if a is None:
        raise HTTPException(404, detail="Address not found")
    await _require_subnet_write(session, user, a.subnet_id)

    before = {"hostname": a.hostname, "tag": a.state, "description": a.description}
    if "hostname" in payload:
        a.hostname = payload["hostname"]  # type: ignore[assignment]
    if "description" in payload:
        a.description = payload["description"]  # type: ignore[assignment]
    if "tag" in payload:
        a.state = str(payload["tag"])
    if "owner" in payload:
        a.owner = payload["owner"]  # type: ignore[assignment]
    if "port" in payload:
        a.switch_port = payload["port"]  # type: ignore[assignment]
    if "note" in payload:
        a.note = payload["note"]  # type: ignore[assignment]
    if "mac" in payload:
        a.mac = payload["mac"]  # type: ignore[assignment]

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="ip_address",
        object_id=str(a.id),
        action="update",
        diff={"before": before, "via": "phpipam"},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return phpipam_response(success=True, message="Address updated", started=started)


@router.delete("/{app_id}/addresses/{address_id}/")
async def delete_address(
    app_id: str,
    address_id: uuid.UUID,
    request: Request,
    user=Depends(phpipam_current_user),
    session: Annotated[AsyncSession, Depends(get_session)] = None,
) -> dict[str, object]:
    started = time.perf_counter()
    a = await session.get(IPAddress, address_id)
    if a is None:
        raise HTTPException(404, detail="Address not found")
    await _require_subnet_write(session, user, a.subnet_id)

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="ip_address",
        object_id=str(a.id),
        action="delete",
        diff={"before": {"ip": str(a.ip)}, "via": "phpipam"},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.delete(a)
    await session.commit()
    return phpipam_response(success=True, message="Address deleted", started=started)
