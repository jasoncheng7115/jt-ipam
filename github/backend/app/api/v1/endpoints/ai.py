"""AI endpoints：語意搜尋 + 自然語言 chat（Phase 4）+ reindex。

POST /api/v1/ai/reindex          (admin)：全表重算 embedding
GET  /api/v1/ai/semantic-search  (auth)：以自然語言查詢；用 pgvector
POST /api/v1/ai/chat             (auth)：自然語言 + jt-ipam tools（Ollama）
"""

from __future__ import annotations

import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin
from app.core.audit import append_audit
from app.core.db import get_session
from app.core.rate_limit import limit_per_ip
from app.schemas.base import StrictModel
from app.services import ai as ai_service
from app.services import ai_chat_store, system_config
from app.services.ai_guard import AIInputRejected, screen_user_messages

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


class ChatContext(StrictModel):
    """前端帶來的「目前所在頁面」提示（讓 AI 在子網路頁問 IP 時自動帶入該網段）。"""
    subnet_id: str | None = None
    subnet_cidr: str | None = None
    device_id: str | None = None
    section_id: str | None = None


class ChatRequest(StrictModel):
    messages: Annotated[list[ChatMessage], Field(min_length=1, max_length=20)]
    max_iterations: Annotated[int, Field(ge=1, le=8)] = 4
    context: ChatContext | None = None
    conversation_id: str | None = None


def _last_user_text(msgs: list[dict[str, Any]]) -> str:
    for m in reversed(msgs):
        if m.get("role") == "user":
            return str(m.get("content") or "")
    return ""


def _parse_uuid(s: str | None) -> uuid.UUID | None:
    if not s:
        return None
    try:
        return uuid.UUID(s)
    except ValueError:
        return None


@router.post("/chat")
async def chat(
    payload: ChatRequest,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """自然語言查詢；本地推論不外送（規格 §11.2）。"""
    await limit_per_ip(request, name="ai")
    msgs = [{"role": m.role, "content": m.content} for m in payload.messages]
    try:
        screen_user_messages(msgs)  # type: ignore[arg-type]
    except AIInputRejected as exc:
        raise HTTPException(status_code=400, detail=f"input_rejected:{exc.reason}") from exc
    # 抓使用者偏好語言（zh-TW / en-US 等）→ system prompt 強制 LLM 用這語言回
    from app.models.user import UserPreference
    pref = await session.get(UserPreference, user.id)
    user_locale = pref.locale if pref else "zh-TW"
    try:
        result = await ai_service.chat(
            session, user=user, messages=msgs,
            locale=user_locale,
            max_iterations=payload.max_iterations,
            page_context=payload.context.model_dump() if payload.context else None,
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
    # 持久化這一輪對話（每位 user 看自己的）
    conv = await ai_chat_store.save_turn(
        session,
        user_id=user.id,
        conversation_id=_parse_uuid(payload.conversation_id),
        user_text=_last_user_text(msgs),
        assistant_text=result.get("answer") or "",
        model=result.get("model"),
        elapsed_ms=result.get("elapsed_ms"),
    )
    await session.commit()
    return {
        "answer": result.get("answer"),
        "trace_messages": result.get("messages", []),
        "model": result.get("model"),
        "elapsed_ms": result.get("elapsed_ms"),
        "conversation_id": str(conv.id),
    }


@router.post("/chat/stream")
async def chat_stream(
    payload: ChatRequest,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StreamingResponse:
    """chat 的 SSE 串流版：逐 token 回最終答案，降低感知延遲（本地推論不外送）。

    回傳 text/event-stream，每筆 `data: {json}\\n\\n`，type 見 ai_service.chat_stream。
    """
    await limit_per_ip(request, name="ai")
    msgs = [{"role": m.role, "content": m.content} for m in payload.messages]
    try:
        screen_user_messages(msgs)  # type: ignore[arg-type]
    except AIInputRejected as exc:
        raise HTTPException(status_code=400, detail=f"input_rejected:{exc.reason}") from exc
    from app.models.user import UserPreference
    pref = await session.get(UserPreference, user.id)
    user_locale = pref.locale if pref else "zh-TW"

    # config 未開 → 開串流前先以 503 擋掉（串流中途無法改 status）
    from app.services.system_config import get_llm_config
    cfg = await get_llm_config(session)
    if not cfg.enabled:
        raise HTTPException(status_code=503, detail="Ollama is disabled")

    async def event_gen() -> Any:
        iterations = 0
        try:
            async for ev in ai_service.chat_stream(
                session, user=user, messages=msgs,
                locale=user_locale, max_iterations=payload.max_iterations,
                page_context=payload.context.model_dump() if payload.context else None,
            ):
                if ev.get("type") == "done":
                    iterations = len(ev.get("trace_messages") or [])
                    conv = await ai_chat_store.save_turn(
                        session,
                        user_id=user.id,
                        conversation_id=_parse_uuid(payload.conversation_id),
                        user_text=_last_user_text(msgs),
                        assistant_text=ev.get("answer") or "",
                        model=ev.get("model"),
                        elapsed_ms=ev.get("elapsed_ms"),
                    )
                    ev = {**ev, "conversation_id": str(conv.id)}
                yield f"data: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"
        except Exception as exc:
            yield f'data: {json.dumps({"type": "error", "detail": f"stream failed: {exc.__class__.__name__}"})}\n\n'
            return
        # 串流正常結束才寫 audit（與非串流 chat 對齊）
        await append_audit(
            session,
            actor_user_id=str(user.id),
            actor_ip=request.client.host if request.client else None,
            actor_user_agent=request.headers.get("user-agent"),
            object_type="ai", object_id=None, action="chat",
            diff={
                "first_user_msg": (msgs[0]["content"][:200] if msgs else None),
                "iterations_used": iterations,
                "stream": True,
            },
            request_id=getattr(request.state, "request_id", None),
        )
        await session.commit()

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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


# ─────────────────── AI chat 歷程 ───────────────────

def _conv_summary(conv: Any, n_msgs: int | None = None, *, with_user: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(conv.id),
        "title": conv.title,
        "created_at": conv.created_at,
        "updated_at": conv.updated_at,
    }
    if n_msgs is not None:
        out["message_count"] = n_msgs
    if with_user:
        out["user_id"] = str(conv.user_id)
    return out


@router.get("/chat/conversations")
async def list_my_conversations(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """目前使用者自己的對話清單。"""
    convs = await ai_chat_store.list_conversations(session, user_id=user.id)
    counts = await ai_chat_store.message_counts(session, [c.id for c in convs])
    return {"items": [_conv_summary(c, counts.get(c.id, 0)) for c in convs]}


@router.get("/chat/conversations/{conversation_id}")
async def get_conversation_detail(
    conversation_id: uuid.UUID,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """一段對話的逐則訊息。只能看自己的；admin 可看任何人的。"""
    conv = await ai_chat_store.get_conversation(session, conversation_id=conversation_id)
    if conv is None or (conv.user_id != user.id and not user.is_admin):
        raise HTTPException(status_code=404, detail="Not found")
    msgs = await ai_chat_store.get_messages(session, conversation_id=conversation_id)
    return {
        **_conv_summary(conv, len(msgs), with_user=True),
        "messages": [
            {
                "role": m.role, "content": m.content,
                "model": m.model, "elapsed_ms": m.elapsed_ms,
                "created_at": m.created_at,
            }
            for m in msgs
        ],
    }


@router.delete("/chat/conversations/{conversation_id}", status_code=204)
async def delete_my_conversation(
    conversation_id: uuid.UUID,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    conv = await ai_chat_store.get_conversation(session, conversation_id=conversation_id)
    if conv is None or (conv.user_id != user.id and not user.is_admin):
        raise HTTPException(status_code=404, detail="Not found")
    await ai_chat_store.delete_conversation(session, conversation_id=conversation_id)
    await session.commit()


@router.get("/chat/admin/conversations", dependencies=[Depends(require_admin)])
async def list_all_conversations(
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = Query(500, ge=1, le=2000),
) -> dict[str, Any]:
    """admin：所有使用者的對話清單。"""
    from app.models.user import User
    convs = await ai_chat_store.list_all_conversations(session, limit=limit)
    counts = await ai_chat_store.message_counts(session, [c.id for c in convs])
    # 帶上 username 方便顯示
    uids = {c.user_id for c in convs}
    unames: dict[str, str] = {}
    if uids:
        from sqlalchemy import select
        for uid, uname in (await session.execute(
            select(User.id, User.username).where(User.id.in_(uids))
        )).all():
            unames[str(uid)] = uname
    items = []
    for c in convs:
        d = _conv_summary(c, counts.get(c.id, 0), with_user=True)
        d["username"] = unames.get(str(c.user_id))
        items.append(d)
    return {"items": items}


@router.get("/chat/retention", dependencies=[Depends(require_admin)])
async def get_chat_retention(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, int]:
    return {"retention_days": await system_config.get_ai_chat_retention_days(session)}


class RetentionRequest(StrictModel):
    retention_days: Annotated[int, Field(ge=0, le=3650)]


@router.put("/chat/retention", dependencies=[Depends(require_admin)])
async def set_chat_retention(
    payload: RetentionRequest,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, int]:
    days = await system_config.set_ai_chat_retention_days(
        session, days=payload.retention_days, updated_by_user_id=user.id,
    )
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="ai", object_id=None, action="set_chat_retention",
        diff={"retention_days": days},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return {"retention_days": days}


@router.post("/chat/purge", dependencies=[Depends(require_admin)])
async def purge_chat_history(
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, int]:
    """依保留天數立即清除舊對話。"""
    days = await system_config.get_ai_chat_retention_days(session)
    removed = await ai_chat_store.purge_old(session, retention_days=days)
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="ai", object_id=None, action="purge_chat_history",
        diff={"removed": removed, "retention_days": days},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return {"removed": removed, "retention_days": days}


@router.get("/model-info")
async def model_info(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    model: str | None = None,
) -> dict[str, Any]:
    """回傳 Ollama 模型的參數摘要（給 chat badge tooltip 顯示「名稱與參數」）。"""
    import httpx as _httpx

    from app.core.safe_http import UnsafeOutboundURL, safe_request
    from app.services.system_config import get_llm_config
    cfg = await get_llm_config(session)
    name = model or cfg.chat_model
    url = f"{cfg.url.rstrip('/')}/api/show"
    try:
        resp = await safe_request("POST", url, headers={"Content-Type": "application/json"},
                                  json={"name": name}, timeout=10.0)
    except (UnsafeOutboundURL, _httpx.HTTPError) as exc:
        return {"model": name, "error": exc.__class__.__name__}
    if resp.status_code != 200:
        return {"model": name, "error": f"HTTP {resp.status_code}"}
    data = resp.json() or {}
    det = data.get("details") or {}
    mi = data.get("model_info") or {}
    ctx = next((v for k, v in mi.items() if isinstance(k, str) and k.endswith(".context_length")), None)
    return {
        "model": name,
        "family": det.get("family"),
        "parameter_size": det.get("parameter_size"),
        "quantization": det.get("quantization_level"),
        "context_length": ctx,
    }
