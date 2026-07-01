"""通知與 Webhook 出站服務。

OWASP 對應：
- A02：Webhook secret 用 AES-GCM 加密儲存（aad 綁 webhook id）
- A09：每次 Webhook 出站結果寫 audit
- A10：對外請求一律走 safe_http；URL 經白名單驗證
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.safe_http import UnsafeOutboundURL, safe_request
from app.core.security import decrypt_secret, encrypt_secret
from app.models.notification import Notification, WebhookSubscription


def _aad(webhook_id: uuid.UUID) -> bytes:
    return f"webhook:{webhook_id}:secret".encode()


def generate_webhook_secret() -> str:
    return secrets.token_urlsafe(32)


def encrypt_webhook_secret(webhook_id: uuid.UUID, raw: str) -> tuple[bytes, bytes]:
    return encrypt_secret(raw, aad=_aad(webhook_id))


def decrypt_webhook_secret(webhook: WebhookSubscription) -> str:
    return decrypt_secret(
        webhook.secret_enc, webhook.secret_nonce, aad=_aad(webhook.id)
    ).decode("utf-8")


# ─────────────────── 站內通知 ───────────────────
async def push_notification(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    title: str,
    body: str | None = None,
    severity: str = "info",
    link: str | None = None,
    object_type: str | None = None,
    object_id: uuid.UUID | None = None,
    title_key: str | None = None,
    body_key: str | None = None,
    params: dict | None = None,
) -> Notification:
    # title/body 仍存（Email + 舊前端退路）；title_key/body_key/params 給前端依語言渲染
    n = Notification(
        user_id=user_id,
        severity=severity,
        title=title,
        body=body,
        title_key=title_key,
        body_key=body_key,
        params=params,
        link=link,
        object_type=object_type,
        object_id=object_id,
    )
    session.add(n)
    await session.flush()
    return n


# ─────────────────── 通知矩陣分派（站內 + Email，依矩陣設定）───────────────────
async def email_users(
    session: AsyncSession, recipients: list[str | None], subject: str, body_text: str,
) -> None:
    """對一組收件者寄信（best-effort；email 管道沒開或單筆失敗都不會中斷流程）。"""
    tos = [r for r in recipients if r]
    if not tos:
        return
    from app.services.email import send_email_via_config
    from app.services.system_config import get_notification_channels
    try:
        cfg = await get_notification_channels(session)
    except Exception:
        return
    if not cfg.get("email_enabled"):
        return
    for to in tos:
        try:
            await send_email_via_config(cfg, to=to, subject=subject, body_text=body_text)
        except Exception:  # 寄信失敗不影響主流程
            pass


async def notify_admins_event(
    session: AsyncSession, *, event: str, title: str, body: str | None = None,
    severity: str = "info", link: str | None = None,
    object_type: str | None = None, object_id: uuid.UUID | None = None,
    title_key: str | None = None, body_key: str | None = None, params: dict | None = None,
) -> int:
    """依通知矩陣把單一事件發給所有 admin（站內 + Email）。回傳收到站內通知的人數。"""
    from app.models.user import User
    from app.services.system_config import get_notification_matrix
    ch = (await get_notification_matrix(session)).get(event, {"in_app": True, "email": False})
    if not (ch.get("in_app") or ch.get("email")):
        return 0
    admins = (await session.execute(
        select(User).where(User.is_admin.is_(True), User.is_active.is_(True))
    )).scalars().all()
    sent = 0
    if ch.get("in_app"):
        for a in admins:
            await push_notification(
                session, user_id=a.id, title=title, body=body, severity=severity,
                link=link, object_type=object_type, object_id=object_id,
                title_key=title_key, body_key=body_key, params=params)
            sent += 1
    if ch.get("email"):
        await email_users(session, [a.email for a in admins], f"[jt-ipam] {title}", body or title)
    from app.services.notify_channels import broadcast_channels
    await broadcast_channels(session, subject=title, text=body)
    return sent


# ─────────────────── Webhook 出站 ───────────────────
def _sign(secret: str, body: bytes, ts: int) -> str:
    msg = f"{ts}.".encode("ascii") + body
    return hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


async def deliver_event(
    session: AsyncSession,
    *,
    event: str,
    payload: dict[str, Any],
) -> None:
    """把單一事件分派給所有有訂閱該 event（或 "*"）的 webhook。

    每個目標獨立發送與計入 failure 計數；不會因為某個目標掛掉影響其他。
    """
    rows = (
        await session.execute(
            select(WebhookSubscription).where(WebhookSubscription.enabled.is_(True))
        )
    ).scalars().all()

    if not rows:
        return

    body_obj = {
        "event": event,
        "data": payload,
        "delivered_at": datetime.now(UTC).isoformat(),
    }
    body_bytes = json.dumps(body_obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ts = int(time.time())

    for wh in rows:
        events_set = set(wh.events or [])
        if "*" not in events_set and event not in events_set:
            continue
        try:
            secret = decrypt_webhook_secret(wh)
        except Exception:
            wh.last_error = "secret decryption failed"
            wh.failure_count += 1
            wh.last_attempt_at = datetime.now(UTC)
            continue

        signature = _sign(secret, body_bytes, ts)
        headers = {
            "Content-Type": "application/json",
            "X-Jt-Signature": f"v1={signature}",
            "X-Jt-Timestamp": str(ts),
            "X-Jt-Event": event,
            "User-Agent": "jt-ipam-webhook/0.3",
        }
        if wh.headers:
            for k, v in wh.headers.items():
                if isinstance(k, str) and isinstance(v, str):
                    headers[k] = v

        wh.last_attempt_at = datetime.now(UTC)
        try:
            resp = await safe_request("POST", wh.target_url, headers=headers, content=body_bytes,
                                      timeout=10.0)
        except UnsafeOutboundURL as exc:
            wh.last_error = f"blocked by SSRF guard: {exc}"
            wh.failure_count += 1
            continue
        except (httpx.HTTPError, TimeoutError) as exc:
            wh.last_error = f"transport error: {exc.__class__.__name__}"
            wh.failure_count += 1
            continue

        if 200 <= resp.status_code < 300:
            wh.last_success_at = datetime.now(UTC)
            wh.last_error = None
            wh.failure_count = 0
        else:
            wh.last_error = f"HTTP {resp.status_code}"
            wh.failure_count += 1
