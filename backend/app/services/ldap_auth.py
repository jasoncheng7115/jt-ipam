"""LDAP / AD 認證。

phpIPAM 缺點：LDAP 設定散落、錯誤訊息不清、不支援 LDAPS+StartTLS 同時誤用。
jt-ipam：設定走 DB（管理區 UI）覆蓋 env；bind password 應用層加密；test endpoint。

OWASP 對應：
- A02：bind password 加密存 DB（AES-GCM）；TLS 預設啟用；不接受 verify=False
- A03：username 透過 ldap3.utils.conv.escape_filter_chars 跳脫 LDAP filter
- A07：認證失敗都回統一訊息；rate limit 沿用 /auth/login 的 auth bucket
"""

from __future__ import annotations

import asyncio
import ssl
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ldap3 import (
    ALL,
    SUBTREE,
    Connection,
    Server,
    Tls,
)
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars

if TYPE_CHECKING:
    from app.services.system_config import LdapConfig


class LDAPAuthError(Exception):
    pass


class LDAPNotConfigured(LDAPAuthError):
    pass


class LDAPInvalidCredentials(LDAPAuthError):
    pass


@dataclass
class LDAPUserInfo:
    dn: str
    username: str
    email: str | None
    display_name: str | None
    is_admin: bool   # 透過 admin_groups 判定
    raw_attrs: dict[str, Any]


def _build_server(cfg: LdapConfig) -> Server:
    if not cfg.enabled or not cfg.server:
        raise LDAPNotConfigured("LDAP not configured")

    tls = Tls(validate=ssl.CERT_REQUIRED, version=ssl.PROTOCOL_TLS_CLIENT)
    return Server(
        host=cfg.server,
        port=cfg.port,
        use_ssl=cfg.use_ssl,
        tls=tls,
        get_info=ALL,
        connect_timeout=int(cfg.timeout),
    )


def _bind_admin_sync(cfg: LdapConfig) -> Connection:
    server = _build_server(cfg)
    conn = Connection(
        server,
        user=cfg.bind_dn,
        password=cfg.bind_password,
        auto_bind=False,
        receive_timeout=int(cfg.timeout),
        raise_exceptions=True,
    )
    try:
        if cfg.use_starttls and not cfg.use_ssl:
            conn.open()
            conn.start_tls()
        ok = conn.bind()
    except LDAPException as exc:
        # ldap3 在 raise_exceptions=True 時對 bind 失敗丟原生例外（含 AD 帳密錯誤）；轉成我們的型別
        msg = str(exc)
        if "invalidCredentials" in msg or "data 52e" in msg or "InvalidCredentials" in exc.__class__.__name__:
            raise LDAPInvalidCredentials(f"bind rejected (帳號或密碼錯誤): {msg}") from exc
        raise LDAPAuthError(f"LDAP bind failed: {exc.__class__.__name__}: {msg}") from exc
    if not ok:
        raise LDAPAuthError(f"LDAP admin bind failed: {conn.last_error}")
    return conn


def _authenticate_sync(cfg: LdapConfig, username: str, password: str) -> LDAPUserInfo:
    if not cfg.search_base:
        raise LDAPNotConfigured("LDAP search base not set")

    safe_username = escape_filter_chars(username)
    user_filter = cfg.user_filter.format(username=safe_username)

    # 1. admin bind 找出使用者 DN + 屬性
    conn = _bind_admin_sync(cfg)
    try:
        conn.search(
            search_base=cfg.search_base,
            search_filter=user_filter,
            search_scope=SUBTREE,
            attributes=[cfg.attr_email, cfg.attr_display_name,
                        cfg.attr_member_of, "cn"],
            time_limit=int(cfg.timeout),
        )
        if not conn.entries:
            raise LDAPInvalidCredentials("user not found")
        if len(conn.entries) > 1:
            raise LDAPAuthError("multiple users matched filter")
        entry = conn.entries[0]
        user_dn = entry.entry_dn
        attrs = entry.entry_attributes_as_dict
    finally:
        try:
            conn.unbind()
        except LDAPException:
            pass

    # 2. 用 user DN + 密碼 bind 驗證
    server = _build_server(cfg)
    user_conn = Connection(
        server,
        user=user_dn,
        password=password,
        auto_bind=False,
        receive_timeout=int(cfg.timeout),
        raise_exceptions=False,
    )
    if cfg.use_starttls and not cfg.use_ssl:
        if not user_conn.open() or not user_conn.start_tls():
            raise LDAPAuthError("LDAP user StartTLS failed")
    bound = user_conn.bind()
    try:
        if not bound:
            raise LDAPInvalidCredentials("invalid password")
    finally:
        try:
            user_conn.unbind()
        except LDAPException:
            pass

    # 3. 解析屬性
    email = (attrs.get(cfg.attr_email) or [None])[0]
    display_name = (attrs.get(cfg.attr_display_name) or [None])[0]
    groups = attrs.get(cfg.attr_member_of) or []
    is_admin = any(g in cfg.admin_groups for g in groups)

    return LDAPUserInfo(
        dn=user_dn,
        username=username,
        email=email,
        display_name=display_name,
        is_admin=is_admin,
        raw_attrs=attrs,
    )


async def authenticate(cfg: LdapConfig, username: str, password: str) -> LDAPUserInfo:
    """非同步入口；ldap3 同步呼叫包進 thread executor。"""
    if not cfg.enabled:
        raise LDAPNotConfigured("LDAP is disabled")
    return await asyncio.to_thread(_authenticate_sync, cfg, username, password)


async def test_connection(cfg: LdapConfig) -> dict[str, Any]:
    """admin bind 測試 — 不需要任何使用者密碼。"""
    if not cfg.enabled:
        raise LDAPNotConfigured("LDAP is disabled")

    def _go() -> dict[str, Any]:
        conn = _bind_admin_sync(cfg)
        try:
            return {
                "bound": True,
                "server": cfg.server,
                "port": cfg.port,
                "tls": "ssl" if cfg.use_ssl else "starttls" if cfg.use_starttls else "none",
                "who_am_i": conn.extend.standard.who_am_i(),
            }
        finally:
            try:
                conn.unbind()
            except LDAPException:
                pass

    return await asyncio.to_thread(_go)
