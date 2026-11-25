"""SMTP Email 投遞。

Phase 1：env-based SMTP 設定 + 用 stdlib smtplib 在 thread pool 跑（避免引入 aiosmtplib）。
Phase 2 再考慮 per-user opt-in、模板、ICU 多語言等。

OWASP 對應：
- A02：smtp_password 從 SecretStr 取出，TLS 強制（none 模式僅給隔離 LAN testing）；
  STARTTLS / SMTPS 預設驗證伺服器憑證（見 `_tls_context`）
- A05：超時必填，避免阻塞 worker
- A09：成功 / 失敗都應該被呼叫端寫入 audit
"""

from __future__ import annotations

import asyncio
import smtplib
import ssl
from email.message import EmailMessage

from app.core.config import get_settings
from app.core.ui_error import UiError


class EmailNotConfigured(RuntimeError):
    pass


class EmailSendError(UiError, RuntimeError):
    pass


def _tls_context(verify: bool, ca_pem: str | None = None) -> ssl.SSLContext:
    """STARTTLS / SMTPS 用的 context。

    smtplib 沒給 context 時用 `ssl._create_stdlib_context()`：不驗憑證、不比主機名稱，
    中間人拿任意憑證就能接住連線並看到 SMTP 帳密。所以一律自己給，預設驗證；
    內部轉信主機用自簽憑證或企業 CA 時，貼上那張憑證：只信任它，主機名稱照樣要比對。
    管理員明確關掉「驗證伺服器憑證」才不驗（仍然加密，只是不防中間人）。
    """
    pem = (ca_pem or "").strip()
    ctx = ssl.create_default_context(cadata=pem) if (verify and pem) else ssl.create_default_context()
    if not verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _cert_names(host: str, port: int, mode: str, timeout: float) -> list[str]:
    """名稱比對失敗時，再連一次（不驗證）讀出憑證上的名稱：SAN 的 DNS / IP，沒有就用 CN。只讀、不登入、不寄信。"""
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    ctx = _tls_context(False)
    try:
        if mode == "tls":
            client = smtplib.SMTP_SSL(host, port, timeout=timeout, context=ctx)
        else:
            client = smtplib.SMTP(host, port, timeout=timeout)  # type: ignore[assignment]
            client.ehlo()
            client.starttls(context=ctx)
        der = client.sock.getpeercert(binary_form=True) if client.sock else None
        try:
            client.quit()
        except Exception:
            pass
        if not der:
            return []
        cert = x509.load_der_x509_certificate(der)
        try:
            san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            names = [str(n) for n in san.get_values_for_type(x509.DNSName)]
            names += [str(n) for n in san.get_values_for_type(x509.IPAddress)]
        except x509.ExtensionNotFound:
            names = []
        if not names:
            names = [str(a.value) for a in cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)]
        return names[:10]
    except Exception:
        return []


def _smtp_send(*, host: str, port: int, mode: str, verify: bool, timeout: float,
               username: str | None, password: str | None, msg: EmailMessage,
               ca_pem: str | None = None) -> None:
    _guard(host, port)
    try:
        if mode == "tls":
            client = smtplib.SMTP_SSL(host, port, timeout=timeout, context=_tls_context(verify, ca_pem))
        else:
            client = smtplib.SMTP(host, port, timeout=timeout)  # type: ignore[assignment]
        try:
            client.ehlo()
            if mode == "starttls":
                client.starttls(context=_tls_context(verify, ca_pem))
                client.ehlo()
            if username and password:
                client.login(username, password)
            client.send_message(msg)
        finally:
            try:
                client.quit()
            except Exception:
                pass
    except ssl.SSLCertVerificationError as exc:
        if "mismatch" in str(exc).lower():
            # 憑證本身沒問題、只是名稱對不上（最常見：憑證發給主機名稱，設定卻填 IP）。
            # 講出憑證上的名稱，使用者才知道要改填什麼（11-25 正式站台升級後就是這樣）
            names = _cert_names(host, port, mode, timeout)
            raise EmailSendError(str(exc), code="smtp_cert_name_mismatch", reason=str(exc), host=host,
                                 names="、".join(names) or "?") from exc
        raise EmailSendError(str(exc), code="smtp_cert_verify_failed", reason=str(exc)) from exc
    except (smtplib.SMTPException, OSError, TimeoutError) as exc:
        raise EmailSendError(str(exc)) from exc


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


def _guard(host: str, port: int) -> None:
    """連線前檢查 SMTP 主機（core/net_guard：擋雲端中繼資料、link-local；本機轉送允許）。"""
    from app.core.net_guard import check_target
    from app.core.safe_http import UnsafeOutboundURL
    try:
        check_target(host, port)
    except UnsafeOutboundURL as exc:
        raise EmailSendError(str(exc)) from exc


def _send_sync(msg: EmailMessage) -> None:
    s = get_settings()
    if not s.smtp_host:
        raise EmailNotConfigured("SMTP_HOST not set")
    _smtp_send(host=s.smtp_host, port=s.smtp_port, mode=s.smtp_tls_mode, verify=s.smtp_verify_tls,
               timeout=s.smtp_timeout, username=s.smtp_username,
               password=s.smtp_password.get_secret_value() if s.smtp_password else None, msg=msg)


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


# ─────────────────── DB 設定版（管理頁「通知發送設定」用）───────────────────
def _send_sync_cfg(cfg: dict, msg: EmailMessage) -> None:
    host = cfg.get("smtp_host")
    if not host:
        raise EmailNotConfigured("SMTP host not configured")
    _smtp_send(host=host, port=int(cfg.get("smtp_port") or 587), mode=cfg.get("smtp_tls") or "starttls",
               verify=cfg.get("smtp_verify_tls") is not False, timeout=15.0,
               username=cfg.get("smtp_username"), password=cfg.get("smtp_password"), msg=msg,
               ca_pem=cfg.get("smtp_ca_cert_pem"))


async def send_email_via_config(
    cfg: dict, *, to: str, subject: str, body_text: str, body_html: str | None = None,
) -> None:
    """用「通知發送設定」DB 設定寄信。cfg 來自 system_config.get_notification_channels。"""
    if not cfg.get("email_enabled"):
        raise EmailNotConfigured("email channel disabled")
    msg = EmailMessage()
    msg["From"] = cfg.get("smtp_from") or cfg.get("smtp_username") or "jt-ipam@localhost"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body_text)
    if body_html:
        msg.add_alternative(body_html, subtype="html")
    await asyncio.to_thread(_send_sync_cfg, cfg, msg)
