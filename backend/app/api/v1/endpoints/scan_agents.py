"""Scan Agent CRUD（admin）。"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import Field, HttpUrl
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin
from app.core.audit import append_audit
from app.core.db import get_session
from app.core.safe_http import UnsafeOutboundURL, assert_url_safe
from app.core.security import encrypt_secret
from app.models.scan_agent import ScanAgent
from app.schemas.base import Paginated, StrictModel

router = APIRouter(prefix="/scan-agents", tags=["scan-agents"])


class ScanAgentCreate(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    description: Annotated[str | None, Field(max_length=1024)] = None
    agent_url: HttpUrl
    enabled: bool = True
    api_token: Annotated[str, Field(min_length=8, max_length=512)]


class ScanAgentUpdate(StrictModel):
    description: Annotated[str | None, Field(max_length=1024)] = None
    agent_url: HttpUrl | None = None
    enabled: bool | None = None
    api_token: Annotated[str | None, Field(min_length=8, max_length=512)] = None


class ScanAgentRead(StrictModel):
    id: uuid.UUID
    name: str
    description: str | None
    agent_url: str
    enabled: bool
    last_seen_at: Any
    last_error: str | None
    created_at: Any
    updated_at: Any


def _aad(agent_id: uuid.UUID) -> bytes:
    return f"scan_agent:{agent_id}:token".encode("utf-8")


@router.get("", response_model=Paginated[ScanAgentRead],
            dependencies=[Depends(require_admin)])
async def list_agents(
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=200),
) -> Paginated[ScanAgentRead]:
    rows = list(
        (await session.execute(
            select(ScanAgent).order_by(ScanAgent.name)
            .offset((page - 1) * page_size).limit(page_size)
        )).scalars().all()
    )
    total = int(await session.scalar(select(func.count()).select_from(ScanAgent)) or 0)
    return Paginated[ScanAgentRead](
        items=[ScanAgentRead.model_validate(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.post("", response_model=ScanAgentRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_agent(
    payload: ScanAgentCreate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ScanAgentRead:
    target = str(payload.agent_url)
    try:
        assert_url_safe(target)
    except UnsafeOutboundURL as exc:
        raise HTTPException(400, detail=f"agent_url rejected: {exc}") from exc

    obj = ScanAgent(
        name=payload.name,
        description=payload.description,
        agent_url=target,
        enabled=payload.enabled,
    )
    session.add(obj)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="Agent name conflict") from exc

    enc, nonce = encrypt_secret(payload.api_token, aad=_aad(obj.id))
    obj.api_token_enc = enc
    obj.api_token_nonce = nonce

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="scan_agent",
        object_id=str(obj.id),
        action="create",
        diff={"name": obj.name, "agent_url": obj.agent_url},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return ScanAgentRead.model_validate(obj)


@router.patch("/{agent_id}", response_model=ScanAgentRead,
              dependencies=[Depends(require_admin)])
async def update_agent(
    agent_id: uuid.UUID,
    payload: ScanAgentUpdate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ScanAgentRead:
    obj = await session.get(ScanAgent, agent_id)
    if obj is None:
        raise HTTPException(404, detail="Agent not found")

    before = {"agent_url": obj.agent_url, "enabled": obj.enabled}
    if payload.description is not None:
        obj.description = payload.description
    if payload.agent_url is not None:
        target = str(payload.agent_url)
        try:
            assert_url_safe(target)
        except UnsafeOutboundURL as exc:
            raise HTTPException(400, detail=f"agent_url rejected: {exc}") from exc
        obj.agent_url = target
    if payload.enabled is not None:
        obj.enabled = payload.enabled
    if payload.api_token is not None:
        enc, nonce = encrypt_secret(payload.api_token, aad=_aad(obj.id))
        obj.api_token_enc = enc
        obj.api_token_nonce = nonce

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="scan_agent",
        object_id=str(obj.id),
        action="update",
        diff={"before": before, "rotated_token": payload.api_token is not None},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return ScanAgentRead.model_validate(obj)


@router.delete("/{agent_id}", status_code=204,
               dependencies=[Depends(require_admin)])
async def delete_agent(
    agent_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    obj = await session.get(ScanAgent, agent_id)
    if obj is None:
        raise HTTPException(404, detail="Agent not found")

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="scan_agent",
        object_id=str(obj.id),
        action="delete",
        diff={"before": {"name": obj.name}},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.delete(obj)
    await session.commit()
