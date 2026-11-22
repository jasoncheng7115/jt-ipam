"""IP 變更評估 M2：服務與依賴的登錄（規格 §6.1、§6.2）。

服務只為呈現業務影響，不是 CMDB：名稱、重要性、負責人、端點、依賴群組（每群組都必要，k-of-n）。
服務是跨單位的共享基礎設施 → 看要全域讀取、改只有管理員（規格 §11.1：現有權限模型表達不了跨單位依賴，
就不讓單位帳號自己登錄服務去引用別人的物件）。依賴群組的 required_count 一定要明確設定，
兩條線、兩張網卡不會自動變成 1-of-2。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import Field
from sqlalchemy import case, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin
from app.core.audit import append_audit
from app.core.db import get_session
from app.core.sqlin import in_values
from app.core.ui_error import ui_detail
from app.models.change_impact import (
    DEP_MEMBER_TYPES,
    DEP_RELATIONS,
    NON_PROPAGATING,
    SERVICE_CRITICALITY,
    SERVICE_STATUS,
    ImpactDependencyGroup,
    ImpactDependencyMember,
    ImpactService,
    ImpactServiceEndpoint,
)
from app.models.user import User
from app.schemas.base import StrictModel
from app.services.change_impact.config import get_config

router = APIRouter(tags=["change-impact"])
Session = Annotated[AsyncSession, Depends(get_session)]

_LABEL_SQL = {
    "device": "SELECT id, name AS label FROM devices WHERE id = ANY(CAST(:ids AS uuid[]))",
    "vm": "SELECT vm.id, c.name || '/' || vm.name AS label FROM virtual_machines vm "
          "JOIN virt_clusters c ON c.id = vm.cluster_id WHERE vm.id = ANY(CAST(:ids AS uuid[]))",
    "ip": "SELECT id, host(ip) || COALESCE(' ' || hostname, '') AS label FROM ip_addresses "
          "WHERE id = ANY(CAST(:ids AS uuid[]))",
    "subnet": "SELECT id, cidr::text || COALESCE(' ' || description, '') AS label FROM subnets "
              "WHERE id = ANY(CAST(:ids AS uuid[]))",
    "service": "SELECT id, name AS label FROM impact_services WHERE id = ANY(CAST(:ids AS uuid[]))",
}


class EndpointIn(StrictModel):
    object_type: Literal["ip", "device", "vm"] | None = None
    object_id: uuid.UUID | None = None
    hostname: Annotated[str | None, Field(max_length=255)] = None
    port: Annotated[int | None, Field(ge=1, le=65535)] = None
    protocol: Annotated[str | None, Field(max_length=8)] = None


class MemberIn(StrictModel):
    object_type: str
    object_id: uuid.UUID
    relation_type: str
    note: Annotated[str | None, Field(max_length=500)] = None


class GroupIn(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    required_count: Annotated[int, Field(ge=1, le=100)]
    purpose: Annotated[str | None, Field(max_length=1000)] = None
    confirmed: bool = False
    members: Annotated[list[MemberIn], Field(max_length=100)] = []


class ServiceIn(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=160)]
    customer_id: uuid.UUID | None = None
    description: Annotated[str | None, Field(max_length=4000)] = None
    owner_user_id: uuid.UUID | None = None
    owner_group_id: uuid.UUID | None = None
    criticality: str = "normal"
    status: str = "active"
    maintenance_notes: Annotated[str | None, Field(max_length=4000)] = None
    endpoints: Annotated[list[EndpointIn], Field(max_length=50)] = []
    groups: Annotated[list[GroupIn], Field(max_length=30)] = []
    expected_version: int | None = None


async def _enabled(session: AsyncSession) -> None:
    if not (await get_config(session))["enabled"]:
        raise HTTPException(403, detail=ui_detail("impact_feature_disabled", "IP change assessment is turned off"))


async def _require_reader(session: AsyncSession, user: User) -> None:
    from app.mcp.tools import has_global_read
    if not user.is_admin and not await has_global_read(session, user):
        raise HTTPException(403, detail=ui_detail("impact_services_global_read",
                                                  "Services are shared infrastructure; global read is required"))


async def _labels(session: AsyncSession, refs: list[tuple[str, uuid.UUID]]) -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], str] = {}
    by_type: dict[str, list[str]] = {}
    for t, i in refs:
        by_type.setdefault(t, []).append(str(i))
    for t, ids in by_type.items():
        if t not in _LABEL_SQL:
            continue
        for r in (await session.execute(text(_LABEL_SQL[t]), {"ids": ids})).all():
            out[(t, str(r.id))] = r.label
    return out


async def _out(session: AsyncSession, svc: ImpactService, *, detail: bool) -> dict[str, Any]:
    o: dict[str, Any] = {
        "id": str(svc.id), "name": svc.name, "customer_id": str(svc.customer_id) if svc.customer_id else None,
        "description": svc.description, "owner_user_id": str(svc.owner_user_id) if svc.owner_user_id else None,
        "owner_group_id": str(svc.owner_group_id) if svc.owner_group_id else None, "criticality": svc.criticality,
        "status": svc.status, "maintenance_notes": svc.maintenance_notes, "version": svc.version,
        "updated_at": svc.updated_at.isoformat() if svc.updated_at else None,
    }
    groups = list((await session.execute(select(ImpactDependencyGroup).where(
        ImpactDependencyGroup.service_id == svc.id).order_by(ImpactDependencyGroup.position))).scalars())
    o["group_count"] = len(groups)
    if not detail:
        return o
    members = list((await session.execute(select(ImpactDependencyMember).where(
        in_values(ImpactDependencyMember.group_id, [g.id for g in groups])))).scalars()) if groups else []
    endpoints = list((await session.execute(select(ImpactServiceEndpoint).where(
        ImpactServiceEndpoint.service_id == svc.id).order_by(ImpactServiceEndpoint.created_at))).scalars())
    refs = [(m.object_type, m.object_id) for m in members] + \
           [(e.object_type, e.object_id) for e in endpoints if e.object_type and e.object_id]
    labels = await _labels(session, refs)
    o["endpoints"] = [{"id": str(e.id), "object_type": e.object_type,
                       "object_id": str(e.object_id) if e.object_id else None,
                       "label": labels.get((e.object_type or "", str(e.object_id))) if e.object_id else None,
                       "hostname": e.hostname, "port": e.port, "protocol": e.protocol} for e in endpoints]
    o["groups"] = [{
        "id": str(g.id), "name": g.name, "required_count": g.required_count, "purpose": g.purpose,
        "confirmed": g.confirmed_at is not None, "confirmed_at": g.confirmed_at.isoformat() if g.confirmed_at else None,
        "members": [{"object_type": m.object_type, "object_id": str(m.object_id), "relation_type": m.relation_type,
                     "note": m.note,
                     # 指向的物件被刪掉了：留著讓人看到、自己決定要不要拿掉
                     "label": labels.get((m.object_type, str(m.object_id))), "missing":
                         (m.object_type, str(m.object_id)) not in labels}
                    for m in members if m.group_id == g.id],
    } for g in groups]
    return o


async def _validate(session: AsyncSession, body: ServiceIn, service_id: uuid.UUID | None) -> None:
    if body.criticality not in SERVICE_CRITICALITY or body.status not in SERVICE_STATUS:
        raise HTTPException(422, detail=ui_detail("impact_service_invalid", "Unknown criticality or status"))
    refs: list[tuple[str, uuid.UUID]] = []
    for g in body.groups:
        propagating = 0
        for m in g.members:
            if m.object_type not in DEP_MEMBER_TYPES or m.relation_type not in DEP_RELATIONS:
                raise HTTPException(422, detail=ui_detail("impact_service_invalid", "Unknown member type or relation"))
            if m.object_type == "service" and service_id is not None and m.object_id == service_id:
                raise HTTPException(422, detail=ui_detail("impact_service_self_dependency",
                                                          "A service cannot depend on itself"))
            if m.relation_type not in NON_PROPAGATING:
                propagating += 1
            refs.append((m.object_type, m.object_id))
        # 需要的數量比會影響可用性的成員還多：這個群組永遠不會滿足，多半是打錯
        if propagating and g.required_count > propagating:
            raise HTTPException(422, detail=ui_detail("impact_group_required_exceeds_members",
                                                      "required_count is larger than the members",
                                                      group=g.name, required=g.required_count, members=propagating))
    for e in body.endpoints:
        if (e.object_type is None) != (e.object_id is None) or (e.object_id is None and not e.hostname):
            raise HTTPException(422, detail=ui_detail("impact_service_invalid", "An endpoint needs an object or a hostname"))
        if e.object_type and e.object_id:
            refs.append((e.object_type, e.object_id))
    labels = await _labels(session, refs)
    missing = [f"{t}:{i}" for t, i in refs if (t, str(i)) not in labels]
    if missing:
        raise HTTPException(422, detail=ui_detail("impact_service_object_missing", "Referenced objects do not exist",
                                                  objects=", ".join(missing[:5])))


async def _write_children(session: AsyncSession, svc: ImpactService, body: ServiceIn, user: User) -> None:
    now = datetime.now(UTC)
    for i, g in enumerate(body.groups):
        grp = ImpactDependencyGroup(service_id=svc.id, name=g.name, required_count=g.required_count,
                                    purpose=g.purpose, position=i,
                                    confirmed_by=user.id if g.confirmed else None,
                                    confirmed_at=now if g.confirmed else None)
        session.add(grp)
        await session.flush()
        seen: set[tuple[str, uuid.UUID]] = set()
        for m in g.members:
            if (m.object_type, m.object_id) in seen:
                continue
            seen.add((m.object_type, m.object_id))
            session.add(ImpactDependencyMember(group_id=grp.id, object_type=m.object_type, object_id=m.object_id,
                                               relation_type=m.relation_type, note=m.note))
    for e in body.endpoints:
        session.add(ImpactServiceEndpoint(service_id=svc.id, object_type=e.object_type, object_id=e.object_id,
                                          hostname=e.hostname, port=e.port, protocol=e.protocol,
                                          confirmed_by=user.id, confirmed_at=now))


async def _audit(session: AsyncSession, request: Request, user: User, action: str, svc_id: uuid.UUID,
                 diff: dict[str, Any]) -> None:
    await append_audit(session, actor_user_id=str(user.id),
                       actor_ip=request.client.host if request.client else None,
                       actor_user_agent=request.headers.get("user-agent"), object_type="impact_service",
                       object_id=str(svc_id), action=action, diff=diff,
                       request_id=getattr(request.state, "request_id", None))


def _summary(body: ServiceIn) -> dict[str, Any]:
    return {"name": body.name, "criticality": body.criticality, "status": body.status,
            "groups": [{"name": g.name, "required": g.required_count, "members": len(g.members)} for g in body.groups],
            "endpoints": len(body.endpoints)}


@router.get("/change-impact/services")
async def list_services(user: CurrentUser, session: Session, q: str | None = Query(None, max_length=100),
                        page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
                        sort: Literal["name", "criticality", "status", "group_count", "updated_at"] | None = None,
                        order: Literal["asc", "desc"] = "asc") -> dict[str, Any]:
    await _enabled(session)
    await _require_reader(session, user)
    stmt = select(ImpactService)
    if q:
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        stmt = stmt.where(or_(ImpactService.name.ilike(like, escape="\\"),
                              ImpactService.description.ilike(like, escape="\\")))
    total = int(await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    # 點標題排序（後端分頁：分頁前排）；嚴重度照等級（critical 在前），沒指定照名稱
    name = func.lower(ImpactService.name)
    if sort == "criticality":
        col: Any = case({c: i for i, c in enumerate(SERVICE_CRITICALITY)}, value=ImpactService.criticality, else_=99)
    elif sort == "status":
        col = ImpactService.status
    elif sort == "group_count":
        col = (select(func.count(ImpactDependencyGroup.id)).where(ImpactDependencyGroup.service_id == ImpactService.id)
               .scalar_subquery())
    elif sort == "updated_at":
        col = ImpactService.updated_at
    else:
        col = name
    ordering = [(col.desc() if order == "desc" else col.asc()).nulls_last(), name, ImpactService.id]
    rows = (await session.execute(stmt.order_by(*ordering)
                                  .offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return {"items": [await _out(session, s, detail=False) for s in rows], "total": total,
            "page": page, "page_size": page_size, "can_edit": bool(user.is_admin)}


_SEARCH_SQL = {
    "device": "SELECT x.id, x.name AS label FROM devices x WHERE (x.name ILIKE :q OR x.fqdn ILIKE :q){scope} "
              "ORDER BY x.name LIMIT 20",
    "vm": "SELECT x.id, c.name || '/' || x.name AS label FROM virtual_machines x "
          "JOIN virt_clusters c ON c.id = x.cluster_id WHERE x.name ILIKE :q AND NOT x.is_template{scope} "
          "ORDER BY x.name LIMIT 20",
    "ip": "SELECT x.id, host(x.ip) || COALESCE(' ' || x.hostname, '') AS label FROM ip_addresses x "
          "JOIN subnets s ON s.id = x.subnet_id WHERE s.archived_at IS NULL "
          "AND (host(x.ip) LIKE :p OR x.hostname ILIKE :q){scope} ORDER BY x.ip LIMIT 20",
    "subnet": "SELECT x.id, x.cidr::text || COALESCE(' ' || x.description, '') AS label FROM subnets x "
              "WHERE x.archived_at IS NULL AND (x.cidr::text LIKE :p OR x.description ILIKE :q){scope} "
              "ORDER BY x.cidr LIMIT 20",
    "service": "SELECT x.id, x.name AS label FROM impact_services x WHERE x.name ILIKE :q{scope} "
               "ORDER BY lower(x.name) LIMIT 20",
}


@router.get("/change-impact/services/object-search")
async def search_objects(user: CurrentUser, session: Session,
                         type: Literal["device", "vm", "ip", "subnet", "service"] = Query(...),
                         q: str = Query("", max_length=100)) -> dict[str, Any]:
    """服務的成員與端點選擇器：依類型搜尋，只回這個人看得到的（裝置、IP、子網路照物件權限）。"""
    from app.services.permission import visible_ids
    await _enabled(session)
    await _require_reader(session, user)
    term = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    params: dict[str, Any] = {"q": f"%{term}%", "p": f"{term}%"}
    scope = ""
    if not user.is_admin and type in ("device", "ip", "subnet"):
        vis = await visible_ids(session, user=user, object_type=type)  # type: ignore[arg-type]
        if vis is not None:
            if not vis:
                return {"items": []}
            scope = " AND x.id = ANY(CAST(:ids AS uuid[]))"
            params["ids"] = [str(i) for i in vis]
    rows = (await session.execute(text(_SEARCH_SQL[type].replace("{scope}", scope)), params)).all()
    return {"items": [{"id": str(r.id), "label": r.label} for r in rows]}


@router.get("/change-impact/services/{service_id}")
async def get_service(service_id: uuid.UUID, user: CurrentUser, session: Session) -> dict[str, Any]:
    await _enabled(session)
    await _require_reader(session, user)
    svc = await session.get(ImpactService, service_id)
    if svc is None:
        raise HTTPException(404, detail="Service not found")
    return await _out(session, svc, detail=True)


@router.post("/change-impact/services", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_admin)])
async def create_service(body: ServiceIn, user: CurrentUser, request: Request, session: Session) -> dict[str, Any]:
    await _enabled(session)
    await _validate(session, body, None)
    svc = ImpactService(name=body.name.strip(), customer_id=body.customer_id, description=body.description,
                        owner_user_id=body.owner_user_id, owner_group_id=body.owner_group_id,
                        criticality=body.criticality, status=body.status, maintenance_notes=body.maintenance_notes,
                        created_by=user.id)
    session.add(svc)
    try:
        await session.flush()
    except Exception as exc:
        await session.rollback()
        raise HTTPException(409, detail=ui_detail("impact_service_name_taken", "A service with this name exists",
                                                  name=body.name)) from exc
    await _write_children(session, svc, body, user)
    await _audit(session, request, user, "impact_service_create", svc.id, _summary(body))
    await session.commit()
    await session.refresh(svc)
    return await _out(session, svc, detail=True)


@router.put("/change-impact/services/{service_id}", dependencies=[Depends(require_admin)])
async def update_service(service_id: uuid.UUID, body: ServiceIn, user: CurrentUser, request: Request,
                         session: Session) -> dict[str, Any]:
    """整份取代（端點與依賴群組重寫）。帶 expected_version 時有人先改過就回 409，不蓋掉別人的修改。"""
    await _enabled(session)
    svc = await session.get(ImpactService, service_id)
    if svc is None:
        raise HTTPException(404, detail="Service not found")
    if body.expected_version is not None and body.expected_version != svc.version:
        raise HTTPException(409, detail=ui_detail("impact_service_version_conflict",
                                                  "The service was changed by someone else; reload it"))
    await _validate(session, body, svc.id)
    before = (await _out(session, svc, detail=True))
    for k in ("customer_id", "description", "owner_user_id", "owner_group_id", "criticality", "status",
              "maintenance_notes"):
        setattr(svc, k, getattr(body, k))
    svc.name = body.name.strip()
    svc.version = svc.version + 1
    await session.execute(text("DELETE FROM impact_dependency_groups WHERE service_id = :s"), {"s": svc.id})
    await session.execute(text("DELETE FROM impact_service_endpoints WHERE service_id = :s"), {"s": svc.id})
    try:
        await session.flush()
    except Exception as exc:
        await session.rollback()
        raise HTTPException(409, detail=ui_detail("impact_service_name_taken", "A service with this name exists",
                                                  name=body.name)) from exc
    await _write_children(session, svc, body, user)
    await _audit(session, request, user, "impact_service_update", svc.id,
                 {"before": {"name": before["name"], "criticality": before["criticality"],
                             "groups": [{"name": g["name"], "required": g["required_count"],
                                         "members": len(g["members"])} for g in before["groups"]]},
                  "after": _summary(body)})
    await session.commit()
    await session.refresh(svc)
    return await _out(session, svc, detail=True)


@router.delete("/change-impact/services/{service_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(require_admin)])
async def delete_service(service_id: uuid.UUID, user: CurrentUser, request: Request, session: Session) -> None:
    await _enabled(session)
    svc = await session.get(ImpactService, service_id)
    if svc is None:
        raise HTTPException(404, detail="Service not found")
    # 別的服務依賴它：一併拿掉那些成員（否則會指向不存在的服務）。稽核記下名稱
    await session.execute(text("DELETE FROM impact_dependency_members WHERE object_type = 'service' AND object_id = :s"),
                          {"s": svc.id})
    await _audit(session, request, user, "impact_service_delete", svc.id, {"name": svc.name})
    await session.delete(svc)
    await session.commit()
