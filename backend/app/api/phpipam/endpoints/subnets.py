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


from fastapi import Body, Request
from sqlalchemy.exc import IntegrityError
from app.core.audit import append_audit
from app.services.subnet import (
    SubnetOverlap,
    assert_no_overlap,
    compute_master_subnet,
)


def _require_admin(user) -> None:  # type: ignore[no-untyped-def]
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin required")


def _bool(v: object) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return v != 0
    if isinstance(v, str):
        return v.lower() in {"1", "true", "yes", "on"}
    return False


@router.post("/{app_id}/subnets/")
async def create_subnet(
    app_id: str,
    request: Request,
    payload: Annotated[dict[str, object], Body()],
    user=Depends(phpipam_current_user),
    session: Annotated[AsyncSession, Depends(get_session)] = None,
) -> dict[str, object]:
    started = time.perf_counter()
    _require_admin(user)

    raw_subnet = payload.get("subnet")
    raw_mask = payload.get("mask")
    raw_section = payload.get("sectionId")
    if not raw_subnet or raw_mask is None or not raw_section:
        raise HTTPException(400, detail="subnet, mask, sectionId are required")

    cidr = f"{raw_subnet}/{raw_mask}"
    try:
        section_id = uuid.UUID(str(raw_section))
    except ValueError as exc:
        raise HTTPException(400, detail="Invalid sectionId") from exc

    vlan_id: uuid.UUID | None = None
    if payload.get("vlanId") and str(payload["vlanId"]) != "0":
        try:
            vlan_id = uuid.UUID(str(payload["vlanId"]))
        except ValueError as exc:
            raise HTTPException(400, detail="Invalid vlanId") from exc

    vrf_id: uuid.UUID | None = None
    if payload.get("vrfId") and str(payload["vrfId"]) != "0":
        try:
            vrf_id = uuid.UUID(str(payload["vrfId"]))
        except ValueError as exc:
            raise HTTPException(400, detail="Invalid vrfId") from exc

    try:
        await assert_no_overlap(session, cidr=cidr, vrf_id=vrf_id)
    except SubnetOverlap as exc:
        raise HTTPException(409, detail=str(exc)) from exc

    master_id = await compute_master_subnet(session, cidr=cidr, vrf_id=vrf_id)
    sub = Subnet(
        section_id=section_id,
        cidr=cidr,
        description=payload.get("description"),
        vlan_id=vlan_id,
        vrf_id=vrf_id,
        master_subnet_id=master_id,
        is_pool=_bool(payload.get("isFolder", False)),
        is_full=_bool(payload.get("isFull", False)),
        scan_enabled=_bool(payload.get("pingSubnet", False)),
    )
    session.add(sub)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="Subnet conflict") from exc

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="subnet",
        object_id=str(sub.id),
        action="create",
        diff={"after": {"cidr": cidr, "section_id": str(section_id), "via": "phpipam"}},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(sub)
    return phpipam_response(
        success=True, code=201, message="Subnet created",
        data={"id": str(sub.id)}, started=started,
    )


@router.patch("/{app_id}/subnets/{subnet_id}/")
async def update_subnet(
    app_id: str,
    subnet_id: uuid.UUID,
    request: Request,
    payload: Annotated[dict[str, object], Body()],
    user=Depends(phpipam_current_user),
    session: Annotated[AsyncSession, Depends(get_session)] = None,
) -> dict[str, object]:
    started = time.perf_counter()
    _require_admin(user)
    sub = await session.get(Subnet, subnet_id)
    if sub is None:
        raise HTTPException(404, detail="Subnet not found")

    before = {"description": sub.description, "is_full": sub.is_full, "is_pool": sub.is_pool}
    if "description" in payload:
        sub.description = payload["description"]  # type: ignore[assignment]
    if "isFolder" in payload:
        sub.is_pool = _bool(payload["isFolder"])
    if "isFull" in payload:
        sub.is_full = _bool(payload["isFull"])
    if "pingSubnet" in payload:
        sub.scan_enabled = _bool(payload["pingSubnet"])

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="subnet",
        object_id=str(sub.id),
        action="update",
        diff={"before": before, "via": "phpipam"},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return phpipam_response(success=True, message="Subnet updated", started=started)


@router.delete("/{app_id}/subnets/{subnet_id}/")
async def delete_subnet(
    app_id: str,
    subnet_id: uuid.UUID,
    request: Request,
    user=Depends(phpipam_current_user),
    session: Annotated[AsyncSession, Depends(get_session)] = None,
) -> dict[str, object]:
    started = time.perf_counter()
    _require_admin(user)
    sub = await session.get(Subnet, subnet_id)
    if sub is None:
        raise HTTPException(404, detail="Subnet not found")

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="subnet",
        object_id=str(sub.id),
        action="delete",
        diff={"before": {"cidr": str(sub.cidr)}, "via": "phpipam"},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.delete(sub)
    await session.commit()
    return phpipam_response(success=True, message="Subnet deleted", started=started)
