"""AI endpoints：語意搜尋 + 自然語言 chat（Phase 4）+ reindex。

POST /api/v1/ai/reindex          (admin)：全表重算 embedding
GET  /api/v1/ai/semantic-search  (auth)：以自然語言查詢；用 pgvector
POST /api/v1/ai/chat             (auth)：自然語言 + jt-ipam tools（Ollama）
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin
from app.core.audit import append_audit
from app.core.db import get_session
from app.schemas.base import StrictModel
from app.services import ai as ai_service

router = APIRouter(prefix="/ai", tags=["ai"])


@router.get("/semantic-search")
async def semantic_search(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    q: Annotated[str, Query(min_length=2, max_length=512)],
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    try:
        return await ai_service.semantic_search(session, query=q, limit=limit)
    except ai_service.AINotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ai_service.AIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


class ChatMessage(StrictModel):
    role: Annotated[str, Field(pattern=r"^(user|assistant|system)$")]
    content: Annotated[str, Field(min_length=1, max_length=4096)]


class ChatRequest(StrictModel):
    messages: Annotated[list[ChatMessage], Field(min_length=1, max_length=20)]
    max_iterations: Annotated[int, Field(ge=1, le=8)] = 4


@router.post("/chat")
async def chat(
    payload: ChatRequest,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """自然語言查詢；本地推論不外送（規格 §11.2）。"""
    msgs = [{"role": m.role, "content": m.content} for m in payload.messages]
    try:
        result = await ai_service.chat(
            session, user=user, messages=msgs,
            max_iterations=payload.max_iterations,
        )
    except ai_service.AINotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ai_service.AIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="ai", object_id=None, action="chat",
        diff={
            "first_user_msg": (msgs[0]["content"][:200] if msgs else None),
            "iterations_used": len(result.get("messages") or []),
        },
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return {
        "answer": result.get("answer"),
        "trace_messages": result.get("messages", []),
    }


@router.post("/reindex", dependencies=[Depends(require_admin)])
async def reindex(
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, int]:
    try:
        stats = await ai_service.reindex_all(session)
    except ai_service.AINotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="ai", object_id=None, action="reindex_all",
        diff=stats,
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return stats
