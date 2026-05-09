"""通知 + Webhook 訂閱 endpoints。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin
from app.core.audit import append_audit
from app.core.db import get_session
from app.core.safe_http import UnsafeOutboundURL, assert_url_safe
from app.models.notification import Notification, WebhookSubscription
from app.schemas.base import Paginated, StrictModel
from app.schemas.notification import (
    NotificationRead,
    WebhookCreate,
    WebhookCreateResponse,
    WebhookRead,
)
from app.services import email as email_service
from app.services.notification import encrypt_webhook_secret, generate_webhook_secret

router = APIRouter(tags=["notifications"])


# ─────────────────── 站內通知 ───────────────────
@router.get("/notifications", response_model=Paginated[NotificationRead])
async def list_my_notifications(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    unread_only: bool = Query(False),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=200),
) -> Paginated[NotificationRead]:
    stmt = select(Notification).where(Notification.user_id == user.id)
    cstmt = (
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == user.id)
    )
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
        cstmt = cstmt.where(Notification.read_at.is_(None))
    stmt = stmt.order_by(Notification.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    rows = list((await session.execute(stmt)).scalars().all())
    total = int(await session.scalar(cstmt) or 0)
    return Paginated[NotificationRead](
        items=[NotificationRead.model_validate(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.post("/notifications/{notification_id}/read", status_code=204)
async def mark_read(
    notification_id: uuid.UUID,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    n = await session.get(Notification, notification_id)
    if n is None or n.user_id != user.id:
        # A01：不洩漏其他人的通知存在
        raise HTTPException(404, detail="Notification not found")
    if n.read_at is None:
        n.read_at = datetime.now(UTC)
        await session.commit()


@router.post("/notifications/read-all", status_code=204)
async def mark_all_read(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    rows = (
        await session.execute(
            select(Notification).where(
                Notification.user_id == user.id,
                Notification.read_at.is_(None),
            )
        )
    ).scalars().all()
    now = datetime.now(UTC)
    for n in rows:
        n.read_at = now
    await session.commit()


# ─────────────────── Webhook 訂閱（admin only）───────────────────
@router.get("/webhooks", response_model=Paginated[WebhookRead],
            dependencies=[Depends(require_admin)])
async def list_webhooks(
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=200),
) -> Paginated[WebhookRead]:
    rows = list(
        (
            await session.execute(
                select(WebhookSubscription)
                .order_by(WebhookSubscription.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars().all()
    )
    total = int(await session.scalar(select(func.count()).select_from(WebhookSubscription)) or 0)
    return Paginated[WebhookRead](
        items=[WebhookRead.model_validate(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.post("/webhooks", response_model=WebhookCreateResponse, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_webhook(
    payload: WebhookCreate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> WebhookCreateResponse:
    target = str(payload.target_url)
    # A10：在儲存前先驗一次 URL，避免存進不可達的目標
    try:
        assert_url_safe(target)
    except UnsafeOutboundURL as exc:
        raise HTTPException(status_code=400, detail=f"Target URL rejected: {exc}") from exc

    secret = generate_webhook_secret()
    obj = WebhookSubscription(
        name=payload.name,
        target_url=target,
        events=payload.events,
        secret_enc=b"placeholder",
        secret_nonce=b"placeholder",
        headers=payload.headers,
    )
    session.add(obj)
    await session.flush()
    # 用真實 id 重新加密 secret，aad 才能綁住
    enc, nonce = encrypt_webhook_secret(obj.id, secret)
    obj.secret_enc = enc
    obj.secret_nonce = nonce

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="webhook",
        object_id=str(obj.id),
        action="create",
        diff={
            "name": payload.name,
            "target_url": target,
            "events": payload.events,
        },
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return WebhookCreateResponse(
        id=obj.id,
        name=obj.name,
        target_url=obj.target_url,
        events=list(obj.events or []),
        secret=secret,
        enabled=obj.enabled,
    )


@router.delete("/webhooks/{webhook_id}", status_code=204,
               dependencies=[Depends(require_admin)])
async def delete_webhook(
    webhook_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    obj = await session.get(WebhookSubscription, webhook_id)
    if obj is None:
        raise HTTPException(404, detail="Webhook not found")
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="webhook",
        object_id=str(obj.id),
        action="delete",
        diff={"name": obj.name, "target_url": obj.target_url},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.delete(obj)
    await session.commit()


# ─────────────────── Email channel ───────────────────
from typing import Annotated as _Ann
from pydantic import EmailStr as _EmailStr, Field as _Field


class EmailTestRequest(StrictModel):
    to: _EmailStr
    subject: _Ann[str, _Field(min_length=1, max_length=256)] = "jt-ipam test email"
    body: _Ann[str, _Field(min_length=1, max_length=4096)] = "This is a test from jt-ipam SMTP channel."


class EmailStatus(StrictModel):
    configured: bool
    host: str | None
    port: int
    tls_mode: str


@router.get("/notifications/email/status", response_model=EmailStatus,
            dependencies=[Depends(require_admin)])
async def email_status() -> EmailStatus:
    from app.core.config import get_settings
    s = get_settings()
    return EmailStatus(
        configured=email_service.is_configured(),
        host=s.smtp_host,
        port=s.smtp_port,
        tls_mode=s.smtp_tls_mode,
    )


@router.post("/notifications/email/test", status_code=200,
             dependencies=[Depends(require_admin)])
async def email_test(
    payload: EmailTestRequest,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    """從伺服器發一封測試信，驗證 SMTP 設定是否正確。"""
    try:
        await email_service.send_email(
            to=str(payload.to),
            subject=payload.subject,
            body_text=payload.body,
        )
    except email_service.EmailNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except email_service.EmailSendError as exc:
        # 失敗也記入 audit（A09）
        await append_audit(
            session,
            actor_user_id=str(user.id),
            actor_ip=request.client.host if request.client else None,
            actor_user_agent=request.headers.get("user-agent"),
            object_type="email",
            object_id=None,
            action="test_failed",
            diff={"to": str(payload.to), "error": str(exc)},
            request_id=getattr(request.state, "request_id", None),
        )
        await session.commit()
        raise HTTPException(status_code=502, detail=f"SMTP error: {exc}") from exc

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="email",
        object_id=None,
        action="test_sent",
        diff={"to": str(payload.to), "subject": payload.subject},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return {"sent": True, "to": str(payload.to)}
