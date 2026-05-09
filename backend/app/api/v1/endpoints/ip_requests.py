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
    IPRequestCreate,
    IPRequestDetail,
    IPRequestEventRead,
    IPRequestRead,
    IPRequestReject,
)
from app.services.ip_request import (
    InvalidStateTransition,
    IPRequestError,
    approve_request,
    cancel_request,
    create_request,
    reject_request,
)
from app.services.permission import (
    get_object_permission,
    has_permission,
)

router = APIRouter(prefix="/ip-requests", tags=["ip-requests"])


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
    if mine or not user.is_admin:
        # 非 admin 只能看自己的
        stmt = stmt.where(IPRequest.requester_user_id == user.id)
        cstmt = cstmt.where(IPRequest.requester_user_id == user.id)
    stmt = stmt.order_by(IPRequest.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    rows = list((await session.execute(stmt)).scalars().all())
    total = int(await session.scalar(cstmt) or 0)
    return Paginated[IPRequestRead](
        items=[IPRequestRead.model_validate(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.get("/{request_id}", response_model=IPRequestDetail)
async def get_request_detail(
    request_id: uuid.UUID,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> IPRequestDetail:
    obj = await _load_request(session, request_id)
    # A01：requester 看自己；其他人需要對該 subnet 有 read 權限或 admin
    if obj.requester_user_id != user.id and not user.is_admin:
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
    return IPRequestDetail(
        request=IPRequestRead.model_validate(obj),
        events=[IPRequestEventRead.model_validate(e) for e in events],
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


@router.post("/{request_id}/approve", response_model=IPRequestRead,
             dependencies=[Depends(require_admin)])
async def approve(
    request_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> IPRequestRead:
    obj = await _load_request(session, request_id)
    subnet = await session.get(Subnet, obj.subnet_id)
    if subnet is None:
        raise HTTPException(409, detail="Subnet no longer exists")

    try:
        await approve_request(session, request=obj, subnet=subnet, approver=user)
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


@router.post("/{request_id}/reject", response_model=IPRequestRead,
             dependencies=[Depends(require_admin)])
async def reject(
    request_id: uuid.UUID,
    payload: IPRequestReject,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> IPRequestRead:
    obj = await _load_request(session, request_id)
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
