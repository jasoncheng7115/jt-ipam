"""IP 申請工作流端點。"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin
from app.core.audit import append_audit
from app.core.db import get_session
from app.models.ip_request import IPRequest, IPRequestEvent
from app.models.subnet import Subnet
from app.schemas.base import Paginated
from app.schemas.ip_request import (
    IPApprove,
    IPRequestCreate,
    IPRequestDetail,
    IPRequestEventRead,
    IPRequestPolicyModel,
    IPRequestRead,
    IPRequestReject,
)
from app.services.ip_request import (
    InvalidStateTransition,
    IPRequestError,
    approve_request,
    cancel_request,
    create_request,
    record_step_approval,
    reject_request,
)
from app.services.ip_request_policy import (
    actionable_steps,
    get_policy,
    is_global_approver,
    set_policy,
    stage_progress,
)
from app.services.ip_request_policy import (
    can_approve as _can_approve,
)
from app.services.permission import (
    get_object_permission,
    has_permission,
)

router = APIRouter(prefix="/ip-requests", tags=["ip-requests"])


async def _read_with_flag(session: AsyncSession, user: CurrentUser, obj: IPRequest) -> IPRequestRead:
    out = IPRequestRead.model_validate(obj)
    out.can_approve = obj.status == "pending" and await _can_approve(session, user, obj)
    # 清單「子網路」欄要顯示 CIDR 而非 UUID（session.get 對同一頁重複子網路會走快取）
    sub = await session.get(Subnet, obj.subnet_id)
    out.subnet_cidr = str(sub.cidr) if sub else None
    return out


async def _load_request(session: AsyncSession, rid: uuid.UUID) -> IPRequest:
    obj = await session.get(IPRequest, rid)
    if obj is None:
        raise HTTPException(404, detail="Request not found")
    return obj


@router.get("", response_model=Paginated[IPRequestRead])
async def list_requests(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    status: str | None = Query(None),
    mine: bool = Query(False, description="只看我自己提出的"),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=200),
) -> Paginated[IPRequestRead]:
    stmt = select(IPRequest)
    cstmt = select(func.count()).select_from(IPRequest)
    if status is not None:
        stmt = stmt.where(IPRequest.status == status)
        cstmt = cstmt.where(IPRequest.status == status)
    # 審核人（admin 或指定審核人）可看全部；其餘只看自己的。mine 開關可強制只看自己。
    approver = await is_global_approver(session, user)
    if mine or not approver:
        stmt = stmt.where(IPRequest.requester_user_id == user.id)
        cstmt = cstmt.where(IPRequest.requester_user_id == user.id)
    stmt = stmt.order_by(IPRequest.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    rows = list((await session.execute(stmt)).scalars().all())
    total = int(await session.scalar(cstmt) or 0)
    return Paginated[IPRequestRead](
        items=[await _read_with_flag(session, user, r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.get("/{request_id}", response_model=IPRequestDetail)
async def get_request_detail(
    request_id: uuid.UUID,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> IPRequestDetail:
    obj = await _load_request(session, request_id)
    # A01：requester 看自己；審核人可看；其他人需對該 subnet 有 read 權限或 admin
    if obj.requester_user_id != user.id and not user.is_admin:
        if not await is_global_approver(session, user):
            level = await get_object_permission(
                session, user=user, object_type="subnet", object_id=obj.subnet_id
            )
            if not has_permission(level, "read"):
                raise HTTPException(404, detail="Request not found")

    events = list(
        (
            await session.execute(
                select(IPRequestEvent)
                .where(IPRequestEvent.request_id == obj.id)
                .order_by(IPRequestEvent.created_at)
            )
        ).scalars().all()
    )
    sub = await session.get(Subnet, obj.subnet_id)
    subnet_cidr = str(sub.cidr) if sub else None
    target_ip: str | None = None
    target_auto = False
    if obj.status == "pending" and sub is not None:
        if obj.requested_ip:
            target_ip = str(obj.requested_ip).split("/")[0]
        else:
            # 系統自動配發時會挑的第一個空位 — 讓審核人先看到
            from app.services.subnet import find_first_free_address
            try:
                target_ip = await find_first_free_address(session, sub)
                target_auto = True
            except Exception:
                target_ip = None
    allocated_ip: str | None = None
    if obj.allocated_ip_id:
        from app.models.address import IPAddress
        ipa = await session.get(IPAddress, obj.allocated_ip_id)
        allocated_ip = str(ipa.ip).split("/")[0] if ipa else None
    return IPRequestDetail(
        request=await _read_with_flag(session, user, obj),
        events=[IPRequestEventRead.model_validate(e) for e in events],
        subnet_cidr=subnet_cidr,
        target_ip=target_ip,
        target_auto=target_auto,
        allocated_ip=allocated_ip,
        stages=await stage_progress(session, obj),
    )


@router.post("", response_model=IPRequestRead, status_code=201)
async def create(
    payload: IPRequestCreate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> IPRequestRead:
    subnet = await session.get(Subnet, payload.subnet_id)
    if subnet is None:
        raise HTTPException(400, detail="Invalid subnet_id")
    # 申請者要對 subnet 至少有 read 權限
    level = await get_object_permission(
        session, user=user, object_type="subnet", object_id=subnet.id
    )
    if not has_permission(level, "read"):
        raise HTTPException(404, detail="Subnet not found")

    try:
        req = await create_request(
            session,
            requester=user,
            subnet=subnet,
            purpose=payload.purpose,
            hostname=payload.hostname,
            description=payload.description,
            requested_ip=payload.requested_ip,
            expires_at=payload.expires_at,
        )
    except IPRequestError as exc:
        raise HTTPException(400, detail=str(exc)) from exc

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="ip_request",
        object_id=str(req.id),
        action="create",
        diff={"after": payload.model_dump(mode="json")},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(req)
    return IPRequestRead.model_validate(req)


@router.post("/{request_id}/approve", response_model=IPRequestRead)
async def approve(
    request_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    payload: IPApprove | None = None,
) -> IPRequestRead:
    obj = await _load_request(session, request_id)
    if not await _can_approve(session, user, obj):
        raise HTTPException(403, detail="You are not an approver for this request")
    subnet = await session.get(Subnet, obj.subnet_id)
    if subnet is None:
        raise HTTPException(409, detail="Subnet no longer exists")

    override_ip = (payload.ip if payload else None) or None
    pol = await get_policy(session)
    try:
        if pol["approver_mode"] in ("parallel", "stages"):
            # 多關卡：核准「此使用者目前可審的最前面那一關」；全通過才配發
            steps = await actionable_steps(session, user, obj, pol)
            if not steps:
                raise HTTPException(403, detail="No approval step is actionable for you")
            await record_step_approval(
                session, request=obj, subnet=subnet, approver=user,
                step_index=steps[0], override_ip=override_ip,
            )
        else:
            await approve_request(session, request=obj, subnet=subnet, approver=user,
                                  override_ip=override_ip)
    except InvalidStateTransition as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    except IPRequestError as exc:
        raise HTTPException(409, detail=str(exc)) from exc

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="ip_request",
        object_id=str(obj.id),
        action="approve",
        diff={"allocated_ip_id": str(obj.allocated_ip_id) if obj.allocated_ip_id else None},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return IPRequestRead.model_validate(obj)


@router.post("/{request_id}/reject", response_model=IPRequestRead)
async def reject(
    request_id: uuid.UUID,
    payload: IPRequestReject,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> IPRequestRead:
    obj = await _load_request(session, request_id)
    if not await _can_approve(session, user, obj):
        raise HTTPException(403, detail="You are not an approver for this request")
    try:
        await reject_request(session, request=obj, approver=user, reason=payload.reason)
    except InvalidStateTransition as exc:
        raise HTTPException(409, detail=str(exc)) from exc

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="ip_request",
        object_id=str(obj.id),
        action="reject",
        diff={"reason": payload.reason},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return IPRequestRead.model_validate(obj)


@router.post("/{request_id}/cancel", response_model=IPRequestRead)
async def cancel(
    request_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> IPRequestRead:
    obj = await _load_request(session, request_id)
    try:
        await cancel_request(session, request=obj, actor=user)
    except InvalidStateTransition as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    except IPRequestError as exc:
        raise HTTPException(403, detail=str(exc)) from exc

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="ip_request",
        object_id=str(obj.id),
        action="cancel",
        diff=None,
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return IPRequestRead.model_validate(obj)


# ─────────────────── 審核政策（管理頁設定）───────────────────
def _policy_out(pol: dict) -> IPRequestPolicyModel:  # type: ignore[type-arg]
    return IPRequestPolicyModel(
        approver_mode=pol["approver_mode"],
        designated_user_ids=[uuid.UUID(x) for x in pol["designated_user_ids"]],
        designated_group_ids=[uuid.UUID(x) for x in pol["designated_group_ids"]],
        allow_self_approve=pol["allow_self_approve"],
        stages=[{
            "name": s.get("name", ""),
            "user_ids": [uuid.UUID(x) for x in s.get("user_ids", [])],
            "group_ids": [uuid.UUID(x) for x in s.get("group_ids", [])],
        } for s in pol.get("stages", [])],
    )


@router.get("/policy/config", response_model=IPRequestPolicyModel,
            dependencies=[Depends(require_admin)])
async def get_request_policy(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> IPRequestPolicyModel:
    return _policy_out(await get_policy(session))


@router.put("/policy/config", response_model=IPRequestPolicyModel,
            dependencies=[Depends(require_admin)])
async def put_request_policy(
    payload: IPRequestPolicyModel,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> IPRequestPolicyModel:
    pol = await set_policy(
        session,
        data={
            "approver_mode": payload.approver_mode,
            "designated_user_ids": [str(x) for x in payload.designated_user_ids],
            "designated_group_ids": [str(x) for x in payload.designated_group_ids],
            "allow_self_approve": payload.allow_self_approve,
            "stages": [{
                "name": s.name,
                "user_ids": [str(x) for x in s.user_ids],
                "group_ids": [str(x) for x in s.group_ids],
            } for s in payload.stages],
        },
        updated_by_user_id=user.id,
    )
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="system_setting", object_id=None,
        action="update", diff={"ip_request_policy": pol},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return _policy_out(pol)
