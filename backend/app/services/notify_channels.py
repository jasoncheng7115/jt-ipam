"""通知管道發送器：Telegram / Slack / Teams / Nextcloud Talk / Zulip。

這些是「團隊頻道」型通知（一則事件推一次到設定好的頻道），與 Email（逐收件者）並行。
目標為管理者設定的外部端點（等同 SMTP 主機的信任模型：admin-only、直接連出、不套 SSRF 白名單，
以支援自架 Nextcloud/Zulip 內網位址）。每個 send_* 成功回 None、失敗丟例外（供測試端點回報）；
broadcast_channels 逐管道 best-effort，單一失敗不影響其他管道或主流程。
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets as _secrets
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger("notify_channels")

# webhook 型通知的管道鍵（email 另外由 email_users 處理）
WEBHOOK_CHANNELS = ("telegram", "slack", "teams", "nextcloud", "zulip")

_TIMEOUT = httpx.Timeout(12.0)


def _msg(subject: str, text: str | None) -> str:
    return f"{subject}\n{text}" if text else subject


async def _post(url: str, *, json: dict | None = None, data: dict | None = None,
                headers: dict | None = None, auth: tuple[str, str] | None = None) -> None:
    # follow_redirects=False：避免以重導繞過設定的目標。admin-only 設定，等同 SMTP 信任模型。
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as client:
        r = await client.post(url, json=json, data=data, headers=headers, auth=auth)
        if r.status_code >= 300:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")


async def send_telegram(cfg: dict[str, Any], subject: str, text: str | None) -> None:
    token, chat = cfg.get("telegram_token"), cfg.get("telegram_chat_id")
    if not (token and chat):
        raise RuntimeError("Telegram bot token / chat id not set")
    await _post(f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat, "text": _msg(subject, text), "disable_web_page_preview": True})


async def send_slack(cfg: dict[str, Any], subject: str, text: str | None) -> None:
    url = cfg.get("slack_webhook")
    if not url:
        raise RuntimeError("Slack webhook URL not set")
    await _post(url, json={"text": f"*{subject}*\n{text}" if text else f"*{subject}*"})


async def send_teams(cfg: dict[str, Any], subject: str, text: str | None) -> None:
    url = cfg.get("teams_webhook")
    if not url:
        raise RuntimeError("Teams webhook URL not set")
    # 相容 Office365 connector 與 Workflows：純 text（Teams 以 markdown 呈現）
    await _post(url, json={"text": f"**{subject}**\n\n{text}" if text else f"**{subject}**"})


async def send_zulip(cfg: dict[str, Any], subject: str, text: str | None) -> None:
    site = (cfg.get("zulip_site") or "").rstrip("/")
    email, api_key = cfg.get("zulip_bot_email"), cfg.get("zulip_api_key")
    stream, topic = cfg.get("zulip_stream"), cfg.get("zulip_topic") or "jt-ipam"
    if not (site and email and api_key and stream):
        raise RuntimeError("Zulip site / bot email / API key / stream not set")
    await _post(f"{site}/api/v1/messages", auth=(email, api_key),
                data={"type": "stream", "to": stream, "topic": topic,
                      "content": f"**{subject}**\n{text}" if text else f"**{subject}**"})


async def send_nextcloud(cfg: dict[str, Any], subject: str, text: str | None) -> None:
    # Nextcloud Talk bot：對話 token + bot 密鑰（HMAC-SHA256 簽章 random+message）
    site = (cfg.get("nextcloud_url") or "").rstrip("/")
    token, secret = cfg.get("nextcloud_token"), cfg.get("nextcloud_secret")
    if not (site and token and secret):
        raise RuntimeError("Nextcloud URL / conversation token / bot secret not set")
    message = _msg(subject, text)
    rnd = _secrets.token_hex(32)
    sig = hmac.new(secret.encode(), (rnd + message).encode(), hashlib.sha256).hexdigest()
    await _post(
        f"{site}/ocs/v2.php/apps/spreed/api/v1/bot/{token}/message",
        json={"message": message},
        headers={
            "OCS-APIRequest": "true",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Nextcloud-Talk-Bot-Random": rnd,
            "X-Nextcloud-Talk-Bot-Signature": sig,
        },
    )


_SENDERS = {
    "telegram": send_telegram,
    "slack": send_slack,
    "teams": send_teams,
    "zulip": send_zulip,
    "nextcloud": send_nextcloud,
}


async def send_one(cfg: dict[str, Any], channel: str, subject: str, text: str | None) -> None:
    """發送到單一管道（供測試端點用）。失敗丟例外。"""
    fn = _SENDERS.get(channel)
    if fn is None:
        raise RuntimeError(f"unknown channel: {channel}")
    await fn(cfg, subject, text)


async def broadcast_channels(session: AsyncSession, *, subject: str, text: str | None = None) -> None:
    """把一則事件推到所有『已啟用』的 webhook 型管道（各自 best-effort，不影響主流程）。"""
    from app.services.system_config import get_notification_channels
    try:
        cfg = await get_notification_channels(session)
    except Exception:
        return
    for ch in WEBHOOK_CHANNELS:
        if not cfg.get(f"{ch}_enabled"):
            continue
        try:
            await _SENDERS[ch](cfg, subject, text)
        except Exception as exc:  # noqa: BLE001 — 單一管道失敗不可中斷其他管道/主流程
            log.warning("notify channel %s failed: %s: %s", ch, type(exc).__name__, exc)
