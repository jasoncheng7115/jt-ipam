"""SMTP Email 投遞。

Phase 1：env-based SMTP 設定 + 用 stdlib smtplib 在 thread pool 跑（避免引入 aiosmtplib）。
Phase 2 再考慮 per-user opt-in、模板、ICU 多語言等。

OWASP 對應：
- A02：smtp_password 從 SecretStr 取出，TLS 強制（none 模式僅給隔離 LAN testing）
- A05：超時必填，避免阻塞 worker
- A09：成功 / 失敗都應該被呼叫端寫入 audit
"""

from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage
from typing import Final

from app.core.config import get_settings


class EmailNotConfigured(RuntimeError):
    pass


class EmailSendError(RuntimeError):
    pass


def is_configured() -> bool:
    s = get_settings()
    return bool(s.smtp_host)


def _build_message(*, to: str, subject: str, body_text: str, body_html: str | None) -> EmailMessage:
    s = get_settings()
    msg = EmailMessage()
    msg["From"] = s.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body_text)
    if body_html:
        msg.add_alternative(body_html, subtype="html")
    return msg


def _send_sync(msg: EmailMessage) -> None:
    s = get_settings()
    if not s.smtp_host:
        raise EmailNotConfigured("SMTP_HOST not set")

    timeout = s.smtp_timeout
    try:
        if s.smtp_tls_mode == "tls":
            client = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=timeout)
        else:
            client = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=timeout)
        try:
            client.ehlo()
            if s.smtp_tls_mode == "starttls":
                client.starttls()
                client.ehlo()
            if s.smtp_username and s.smtp_password:
                client.login(s.smtp_username, s.smtp_password.get_secret_value())
            client.send_message(msg)
        finally:
            try:
                client.quit()
            except Exception:  # noqa: BLE001
                pass
    except (smtplib.SMTPException, OSError, TimeoutError) as exc:
        raise EmailSendError(str(exc)) from exc


async def send_email(
    *,
    to: str,
    subject: str,
    body_text: str,
    body_html: str | None = None,
) -> None:
    """非同步發信入口（內部走 thread executor，避免 stdlib smtplib 阻塞 event loop）。

    A02：to 由呼叫端控制，但 SMTP 連線資訊一律從 settings 取 — 不接受呼叫端覆寫
    來源 server，避免被當成 open relay。
    """
    if not is_configured():
        raise EmailNotConfigured("SMTP is not configured (set SMTP_HOST in env)")
    msg = _build_message(to=to, subject=subject, body_text=body_text, body_html=body_html)
    await asyncio.to_thread(_send_sync, msg)
