"""裝置群組 API（堆疊／HA／叢集，見 models/device_group.py、services/device_groups.py）。

讀：登入即可，只列看得到的成員與共用 IP（一台都看不到的群組不列）。寫：管理員，逐筆稽核。
"""
from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin
from app.core.audit import append_audit
from app.core.db import get_session
from app.core.sqlin import in_values
from app.core.ui_error import ui_detail
from app.models.address import IPAddress
from app.models.device_group import DeviceGroup
from app.schemas.base import StrictModel
from app.services import device_groups as svc

router = APIRouter(prefix="/device-groups", tags=["device-groups"])


class MemberIn(StrictModel):
    device_id: uuid.UUID
    role: Annotated[str, Field(max_length=16)] = "member"
    order: Annotated[int | None, Field(ge=1, le=999)] = None


class GroupCreate(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    kind: Annotated[str, Field(max_length=24)] = "stack"
    description: Annotated[str | None, Field(max_length=2000)] = None
    members: Annotated[list[MemberIn], Field(max_length=64)]
    representative_id: uuid.UUID | None = None
    shared_ip_ids: Annotated[list[uuid.UUID], Field(max_length=64)] = []


class GroupUpdate(StrictModel):
    name: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    kind: Annotated[str | None, Field(max_length=24)] = None
    description: Annotated[str | None, Field(max_length=2000)] = None
    members: Annotated[list[MemberIn] | None, Field(max_length=64)] = None
    representative_id: uuid.UUID | None = None
    shared_ip_ids: Annotated[list[uuid.UUID] | None, Field(max_length=64)] = None


def _err(exc: svc.GroupError) -> HTTPException:
    return HTTPException(exc.status, detail=ui_detail(exc.code, str(exc), **exc.params))


def _audit_kw(user: Any, request: Request) -> dict[str, Any]:
    return {"actor_user_id": str(user.id), "actor_ip": request.client.host if request.client else None,
            "actor_user_agent": request.headers.get("user-agent"),
            "request_id": getattr(request.state, "request_id", None)}


async def _visible(session: AsyncSession, user: Any, object_type: str) -> set[uuid.UUID] | None:
    if getattr(user, "is_admin", False):
        return None
    from app.services.permission import visible_ids
    return await visible_ids(session, user=user, object_type=object_type)


async def _out(session: AsyncSession, g: DeviceGroup, vis_dev: set[uuid.UUID] | None = None) -> dict[str, Any]:
    members = await svc.members_of(session, g.id)
    if vis_dev is not None:
        members = [m for m in members if m.id in vis_dev]
    from app.models.location import Rack
    racks = {r.id: r.name for r in (await session.execute(select(Rack).where(
        in_values(Rack.id, [m.rack_id for m in members if m.rack_id])))).scalars().all()} if any(
        m.rack_id for m in members) else {}
    ips = list((await session.execute(select(IPAddress).where(IPAddress.device_group_id == g.id)
                                      .order_by(IPAddress.ip))).scalars().all())
    return {
        "id": str(g.id), "name": g.name, "kind": g.kind, "description": g.description,
        "representative_id": str(g.representative_id) if g.representative_id else None,
        "members": [{"device_id": str(m.id), "name": m.name, "type": m.type, "role": m.group_role,
                     "order": m.group_order, "rack_id": str(m.rack_id) if m.rack_id else None,
                     "rack_name": racks.get(m.rack_id), "u_position": m.u_position} for m in members],
        "shared_ips": [{"id": str(ip.id), "ip": str(ip.ip).split("/")[0], "subnet_id": str(ip.subnet_id)}
                       for ip in ips],
        "created_at": g.created_at, "updated_at": g.updated_at,
    }


@router.get("")
async def list_groups(user: CurrentUser, session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, Any]:
    vis = await _visible(session, user, "device")
    groups = (await session.execute(select(DeviceGroup).order_by(DeviceGroup.name))).scalars().all()
    items = [await _out(session, g, vis) for g in groups]
    items = [i for i in items if vis is None or i["members"]]
    return {"items": items, "total": len(items)}


@router.get("/{group_id}")
async def get_group(group_id: uuid.UUID, user: CurrentUser,
                    session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, Any]:
    g = await session.get(DeviceGroup, group_id)
    vis = await _visible(session, user, "device")
    out = await _out(session, g, vis) if g is not None else None
    if out is None or (vis is not None and not out["members"]):
        raise HTTPException(404, detail=ui_detail("device_group_not_found", "Device group not found"))
    return out


@router.post("", status_code=201, dependencies=[Depends(require_admin)])
async def create_group(payload: GroupCreate, user: CurrentUser, request: Request,
                       session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, Any]:
    try:
        svc.check_kind(payload.kind)
        g = DeviceGroup(name=payload.name.strip(), kind=payload.kind, description=payload.description)
        session.add(g)
        await session.flush()
        members = await svc.apply_members(session, g, [m.model_dump() for m in payload.members])
        await svc.set_representative(session, g, payload.representative_id)
        ips = await svc.set_shared_ips(session, g, list(payload.shared_ip_ids))
    except svc.GroupError as exc:
        await session.rollback()
        raise _err(exc) from exc
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(409, detail=ui_detail("device_group_name_taken", "Device group name already exists")) from exc
    await append_audit(session, object_type="device_group", object_id=str(g.id), action="create",
                       diff={"name": g.name, "kind": g.kind, "members": members["added"],
                             "representative_id": str(g.representative_id) if g.representative_id else None,
                             "shared_ips": ips["added"]}, **_audit_kw(user, request))
    await session.commit()
    await session.refresh(g)
    return await _out(session, g)


@router.patch("/{group_id}", dependencies=[Depends(require_admin)])
async def update_group(group_id: uuid.UUID, payload: GroupUpdate, user: CurrentUser, request: Request,
                       session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, Any]:
    g = await session.get(DeviceGroup, group_id)
    if g is None:
        raise HTTPException(404, detail=ui_detail("device_group_not_found", "Device group not found"))
    diff: dict[str, Any] = {}
    try:
        for field in ("name", "kind", "description"):
            if field in payload.model_fields_set:
                v = getattr(payload, field)
                if field == "kind" and v is not None:
                    svc.check_kind(v)
                if v is not None or field == "description":
                    v = v.strip() if isinstance(v, str) and field == "name" else v
                    if v != getattr(g, field):
                        diff[field] = {"from": getattr(g, field), "to": v}
                        setattr(g, field, v)
        if payload.members is not None:
            ch = await svc.apply_members(session, g, [m.model_dump() for m in payload.members])
            if ch["added"] or ch["removed"]:
                diff["members"] = ch
        before_rep = g.representative_id
        await svc.set_representative(session, g, payload.representative_id
                                     if "representative_id" in payload.model_fields_set else None)
        if g.representative_id != before_rep:
            diff["representative_id"] = {"from": str(before_rep) if before_rep else None,
                                         "to": str(g.representative_id) if g.representative_id else None}
        if payload.shared_ip_ids is not None:
            ch = await svc.set_shared_ips(session, g, list(payload.shared_ip_ids))
            if ch["added"] or ch["removed"]:
                diff["shared_ips"] = ch
    except svc.GroupError as exc:
        await session.rollback()
        raise _err(exc) from exc
    await append_audit(session, object_type="device_group", object_id=str(g.id), action="update",
                       diff=diff, **_audit_kw(user, request))
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(409, detail=ui_detail("device_group_name_taken", "Device group name already exists")) from exc
    await session.refresh(g)
    return await _out(session, g)


@router.delete("/{group_id}", status_code=204, dependencies=[Depends(require_admin)])
async def delete_group(group_id: uuid.UUID, user: CurrentUser, request: Request,
                       session: Annotated[AsyncSession, Depends(get_session)]) -> None:
    """刪除群組：成員回到一般設備；共用 IP 留在原本的代表裝置上。"""
    g = await session.get(DeviceGroup, group_id)
    if g is None:
        raise HTTPException(404, detail=ui_detail("device_group_not_found", "Device group not found"))
    members = await svc.members_of(session, g.id)
    for m in members:
        m.group_id, m.group_role, m.group_order = None, None, None
    for ip in (await session.execute(select(IPAddress).where(IPAddress.device_group_id == g.id))).scalars().all():
        ip.device_group_id = None
    await session.flush()
    await append_audit(session, object_type="device_group", object_id=str(g.id), action="delete",
                       diff={"name": g.name, "members": [m.name for m in members]}, **_audit_kw(user, request))
    await session.delete(g)
    await session.commit()

