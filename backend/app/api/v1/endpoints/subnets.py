"""Subnet CRUD + first_free_address + usage（Phase 1 重點）。"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_object_perm
from app.core.audit import append_audit
from app.core.db import get_session
from app.models.section import Section
from app.models.subnet import Subnet
from app.schemas.base import Paginated, StrictModel
from app.schemas.subnet import (
    FirstFreeAddress,
    SubnetCreate,
    SubnetRead,
    SubnetUpdate,
    SubnetUsage,
)
from app.services import ai as ai_service
from app.services.custom_field import CustomFieldError, validate_custom_fields
from app.services.notification import deliver_event
from app.services.permission import (
    filter_visible,
    get_object_permission,
    has_permission,
)
from app.services.subnet import (
    SubnetOverlap,
    assert_no_overlap,
    compute_master_subnet,
    find_first_free_address,
    get_usage,
    rebuild_subnet_hierarchy,
)

router = APIRouter(prefix="/subnets", tags=["subnets"])


@router.get("", response_model=Paginated[SubnetRead])
async def list_subnets(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    section_id: uuid.UUID | None = Query(None),
    archived: bool = Query(False),  # 預設只列未歸檔；archived=true 看歸檔區
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=500),
) -> Paginated[SubnetRead]:
    stmt = select(Subnet)
    count_stmt = select(func.count()).select_from(Subnet)
    arch_clause = Subnet.archived_at.is_not(None) if archived else Subnet.archived_at.is_(None)
    stmt = stmt.where(arch_clause)
    count_stmt = count_stmt.where(arch_clause)
    if section_id is not None:
        stmt = stmt.where(Subnet.section_id == section_id)
        count_stmt = count_stmt.where(Subnet.section_id == section_id)

    stmt = stmt.order_by(Subnet.cidr).offset((page - 1) * page_size).limit(page_size)
    rows = list((await session.execute(stmt)).scalars().all())

    # A01：篩出 user 有 read 權限的
    visible_ids = await filter_visible(
        session,
        user=user,
        object_type="subnet",
        object_ids=[r.id for r in rows],
        required="read",
    )
    visible_set = set(visible_ids)
    vis_rows = [r for r in rows if r.id in visible_set]
    # 批次帶出單位名稱：非管理員載不到 customers 清單，前端樹狀分組才不會只剩 UUID
    cust_ids = {r.customer_id for r in vis_rows if r.customer_id}
    cust_name: dict[uuid.UUID, str] = {}
    if cust_ids:
        from app.models.customer import Customer
        cust_name = {c.id: c.name for c in (await session.execute(
            select(Customer).where(Customer.id.in_(cust_ids))
        )).scalars().all()}
    items = []
    for r in vis_rows:
        item = SubnetRead.model_validate(r)
        if r.customer_id:
            item.customer_name = cust_name.get(r.customer_id)
        items.append(item)

    total = int(await session.scalar(count_stmt) or 0)
    return Paginated[SubnetRead](items=items, total=total, page=page, page_size=page_size)


@router.get(
    "/{subnet_id}",
    response_model=SubnetRead,
    dependencies=[Depends(require_object_perm("subnet", "read", path_param="subnet_id"))],
)
async def get_subnet(
    subnet_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SubnetRead:
    subnet = await session.get(Subnet, subnet_id)
    if subnet is None:
        raise HTTPException(status_code=404, detail="Subnet not found")
    out = SubnetRead.model_validate(subnet)
    if subnet.customer_id:
        from app.models.customer import Customer
        cust = await session.get(Customer, subnet.customer_id)
        out.customer_name = cust.name if cust else None
    return out


@router.get(
    "/{subnet_id}/usage",
    response_model=SubnetUsage,
    dependencies=[Depends(require_object_perm("subnet", "read", path_param="subnet_id"))],
)
async def subnet_usage(
    subnet_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SubnetUsage:
    subnet = await session.get(Subnet, subnet_id)
    if subnet is None:
        raise HTTPException(status_code=404, detail="Subnet not found")
    total, used, free, pct = await get_usage(session, subnet)
    return SubnetUsage(
        subnet_id=subnet.id,
        cidr=str(subnet.cidr),
        total=total,
        used=used,
        free=free,
        used_pct=pct,
    )


@router.get(
    "/{subnet_id}/first_free_address",
    response_model=FirstFreeAddress,
    dependencies=[Depends(require_object_perm("subnet", "read", path_param="subnet_id"))],
)
async def first_free_address(
    subnet_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FirstFreeAddress:
    subnet = await session.get(Subnet, subnet_id)
    if subnet is None:
        raise HTTPException(status_code=404, detail="Subnet not found")
    ip = await find_first_free_address(session, subnet)
    return FirstFreeAddress(subnet_id=subnet.id, cidr=str(subnet.cidr), ip=ip)


@router.post("", response_model=SubnetRead, status_code=status.HTTP_201_CREATED)
async def create_subnet(
    payload: SubnetCreate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SubnetRead:
    # A01：要在指定 section 有 write 權限
    section = await session.get(Section, payload.section_id)
    if section is None:
        raise HTTPException(status_code=400, detail="Invalid section_id")
    level = await get_object_permission(
        session, user=user, object_type="section", object_id=section.id
    )
    if not has_permission(level, "write"):
        raise HTTPException(status_code=403, detail="Forbidden")

    try:
        await assert_no_overlap(
            session, cidr=payload.cidr, vrf_id=payload.vrf_id,
            allow_overlap=payload.allow_overlap,
        )
    except SubnetOverlap as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    try:
        validated_cf = await validate_custom_fields(
            session, object_type="subnet", payload=payload.custom_fields
        )
    except CustomFieldError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    master_id = await compute_master_subnet(
        session, cidr=payload.cidr, vrf_id=payload.vrf_id
    )

    data = payload.model_dump()
    data.pop("allow_overlap", None)   # 僅建立時的旗標，非欄位
    data["master_subnet_id"] = master_id
    data["custom_fields"] = validated_cf or None
    subnet = Subnet(**data)
    session.add(subnet)
    await session.flush()

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="subnet",
        object_id=str(subnet.id),
        action="create",
        diff={"after": payload.model_dump(mode="json")},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    # 新建（尤其是父網段）後重算階層，讓既有子網段自動歸位
    await rebuild_subnet_hierarchy(session)
    await session.commit()
    await session.refresh(subnet)
    # Phase 2：自動 index description（失敗不擋主流程）
    if subnet.description:
        try:
            await ai_service.index_subnet(session, str(subnet.id), subnet.description)
            await session.commit()
        except Exception:
            pass
    await deliver_event(
        session,
        event="subnet.created",
        payload={
            "id": str(subnet.id),
            "cidr": str(subnet.cidr),
            "section_id": str(subnet.section_id),
            "actor": str(user.id),
        },
    )
    await session.commit()
    return SubnetRead.model_validate(subnet)


@router.patch(
    "/{subnet_id}",
    response_model=SubnetRead,
    dependencies=[Depends(require_object_perm("subnet", "write", path_param="subnet_id"))],
)
async def update_subnet(
    subnet_id: uuid.UUID,
    payload: SubnetUpdate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SubnetRead:
    subnet = await session.get(Subnet, subnet_id)
    if subnet is None:
        raise HTTPException(status_code=404, detail="Subnet not found")

    before = {
        "section_id": str(subnet.section_id),
        "vrf_id": str(subnet.vrf_id) if subnet.vrf_id else None,
        "vlan_id": str(subnet.vlan_id) if subnet.vlan_id else None,
        "is_pool": subnet.is_pool,
        "is_full": subnet.is_full,
    }
    changes = payload.model_dump(exclude_unset=True)
    if "custom_fields" in changes:
        try:
            changes["custom_fields"] = await validate_custom_fields(
                session, object_type="subnet", payload=changes["custom_fields"]
            ) or None
        except CustomFieldError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    for key, value in changes.items():
        setattr(subnet, key, value)

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="subnet",
        object_id=str(subnet.id),
        action="update",
        diff={"before": before, "changes": changes},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(subnet)
    return SubnetRead.model_validate(subnet)


@router.post(
    "/{subnet_id}/archive",
    response_model=SubnetRead,
    dependencies=[Depends(require_object_perm("subnet", "write", path_param="subnet_id"))],
)
async def archive_subnet(
    subnet_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SubnetRead:
    """歸檔子網路：保留資料但不顯示於一般清單、停止掃描。重疊檢查會忽略已歸檔。"""
    from datetime import UTC, datetime
    subnet = await session.get(Subnet, subnet_id)
    if subnet is None:
        raise HTTPException(status_code=404, detail="Subnet not found")
    subnet.archived_at = datetime.now(UTC)
    subnet.scan_enabled = False  # 歸檔即停掃
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="subnet", object_id=str(subnet.id), action="archive",
        diff={"cidr": str(subnet.cidr)},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await rebuild_subnet_hierarchy(session)
    await session.commit()
    await session.refresh(subnet)
    return SubnetRead.model_validate(subnet)


@router.post(
    "/{subnet_id}/unarchive",
    response_model=SubnetRead,
    dependencies=[Depends(require_object_perm("subnet", "write", path_param="subnet_id"))],
)
async def unarchive_subnet(
    subnet_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SubnetRead:
    """還原歸檔子網路。若期間已有相同 CIDR 的未歸檔子網路會擋下（避免重疊）。"""
    subnet = await session.get(Subnet, subnet_id)
    if subnet is None:
        raise HTTPException(status_code=404, detail="Subnet not found")
    if subnet.archived_at is None:
        return SubnetRead.model_validate(subnet)
    try:
        await assert_no_overlap(
            session, cidr=str(subnet.cidr), vrf_id=subnet.vrf_id, exclude_id=subnet.id
        )
    except SubnetOverlap as exc:
        raise HTTPException(
            status_code=409,
            detail=f"無法還原：已有相同/重疊的使用中子網路（{exc}）",
        ) from exc
    subnet.archived_at = None
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="subnet", object_id=str(subnet.id), action="unarchive",
        diff={"cidr": str(subnet.cidr)},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await rebuild_subnet_hierarchy(session)
    await session.commit()
    await session.refresh(subnet)
    return SubnetRead.model_validate(subnet)


@router.delete(
    "/{subnet_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_object_perm("subnet", "admin", path_param="subnet_id"))],
)
async def delete_subnet(
    subnet_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    subnet = await session.get(Subnet, subnet_id)
    if subnet is None:
        raise HTTPException(status_code=404, detail="Subnet not found")

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="subnet",
        object_id=str(subnet.id),
        action="delete",
        diff={"before": {"cidr": str(subnet.cidr), "section_id": str(subnet.section_id)}},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.delete(subnet)
    await session.commit()
    # 刪父網段後，子網段重新歸位到上一層
    await rebuild_subnet_hierarchy(session)
    await session.commit()


class _BulkDeletePayload(StrictModel):
    ids: list[uuid.UUID]


@router.post("/bulk-delete")
async def bulk_delete_subnets(
    payload: _BulkDeletePayload,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    if not payload.ids:
        return {"deleted": 0, "failed": 0, "errors": []}
    if len(payload.ids) > 500:
        raise HTTPException(400, detail="too many ids (max 500)")
    from app.services.permission import get_object_permission, has_permission
    deleted = 0
    errors: list[dict[str, str]] = []
    actor_ip = request.client.host if request.client else None
    actor_ua = request.headers.get("user-agent")
    request_id = getattr(request.state, "request_id", None)
    for sid in payload.ids:
        s = await session.get(Subnet, sid)
        if s is None:
            errors.append({"id": str(sid), "error": "not_found"}); continue
        level = await get_object_permission(session, user=user, object_type="subnet", object_id=s.id)
        if not has_permission(level, "admin"):
            errors.append({"id": str(sid), "error": "no_permission"}); continue
        await append_audit(
            session, actor_user_id=str(user.id),
            actor_ip=actor_ip, actor_user_agent=actor_ua,
            object_type="subnet", object_id=str(s.id), action="delete",
            diff={"before": {"cidr": str(s.cidr), "section_id": str(s.section_id)},
                  "bulk": True},
            request_id=request_id,
        )
        await session.delete(s)
        deleted += 1
    await session.commit()
    return {"deleted": deleted, "failed": len(errors), "errors": errors[:50]}
