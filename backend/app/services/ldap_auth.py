"""LDAP / AD 認證。

phpIPAM 缺點：LDAP 設定散落、錯誤訊息不清、不支援 LDAPS+StartTLS 同時誤用。
jt-ipam：env 統一設定 + 啟動時驗證 + test endpoint。

OWASP 對應：
- A02：bind password 從 SecretStr 取；TLS 預設啟用；不接受 verify=False
- A03：username 透過 ldap3.utils.conv.escape_filter_chars 跳脫 LDAP filter
- A07：認證失敗都回統一訊息；rate limit 沿用 /auth/login 的 auth bucket
"""

from __future__ import annotations

import asyncio
import ssl
from dataclasses import dataclass
from typing import Any

from ldap3 import (
    ALL,
    SUBTREE,
    Connection,
    Server,
    Tls,
)
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars

from app.core.config import get_settings


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


def _build_server() -> Server:
    s = get_settings()
    if not s.ldap_enabled or not s.ldap_server:
        raise LDAPNotConfigured("LDAP not configured")

    tls = Tls(validate=ssl.CERT_REQUIRED, version=ssl.PROTOCOL_TLS_CLIENT)
    return Server(
        host=s.ldap_server,
        port=s.ldap_port,
        use_ssl=s.ldap_use_ssl,
        tls=tls,
        get_info=ALL,
        connect_timeout=int(s.ldap_timeout),
    )


def _bind_admin_sync() -> Connection:
    s = get_settings()
    server = _build_server()
    conn = Connection(
        server,
        user=s.ldap_bind_dn,
        password=s.ldap_bind_password.get_secret_value() if s.ldap_bind_password else None,
        auto_bind=False,
        receive_timeout=int(s.ldap_timeout),
        raise_exceptions=True,
    )
    if s.ldap_use_starttls and not s.ldap_use_ssl:
        if not conn.open():
            raise LDAPAuthError("LDAP open failed")
        if not conn.start_tls():
            raise LDAPAuthError("LDAP StartTLS failed")
    if not conn.bind():
        raise LDAPAuthError(f"LDAP admin bind failed: {conn.last_error}")
    return conn


def _authenticate_sync(username: str, password: str) -> LDAPUserInfo:
    s = get_settings()
    if not s.ldap_search_base:
        raise LDAPNotConfigured("LDAP_SEARCH_BASE not set")

    safe_username = escape_filter_chars(username)
    user_filter = s.ldap_user_filter.format(username=safe_username)

    # 1. admin bind 找出使用者 DN + 屬性
    conn = _bind_admin_sync()
    try:
        conn.search(
            search_base=s.ldap_search_base,
            search_filter=user_filter,
            search_scope=SUBTREE,
            attributes=[s.ldap_attr_email, s.ldap_attr_display_name,
                        s.ldap_attr_member_of, "cn"],
            time_limit=int(s.ldap_timeout),
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
    server = _build_server()
    user_conn = Connection(
        server,
        user=user_dn,
        password=password,
        auto_bind=False,
        receive_timeout=int(s.ldap_timeout),
        raise_exceptions=False,
    )
    if s.ldap_use_starttls and not s.ldap_use_ssl:
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
    email = (attrs.get(s.ldap_attr_email) or [None])[0]
    display_name = (attrs.get(s.ldap_attr_display_name) or [None])[0]
    groups = attrs.get(s.ldap_attr_member_of) or []
    is_admin = any(g in s.ldap_admin_groups for g in groups)

    return LDAPUserInfo(
        dn=user_dn,
        username=username,
        email=email,
        display_name=display_name,
        is_admin=is_admin,
        raw_attrs=attrs,
    )


async def authenticate(username: str, password: str) -> LDAPUserInfo:
    """非同步入口；ldap3 同步呼叫包進 thread executor。"""
    settings = get_settings()
    if not settings.ldap_enabled:
        raise LDAPNotConfigured("LDAP is disabled")
    return await asyncio.to_thread(_authenticate_sync, username, password)


async def test_connection() -> dict[str, Any]:
    """admin bind 測試 — 不需要任何使用者密碼。"""
    settings = get_settings()
    if not settings.ldap_enabled:
        raise LDAPNotConfigured("LDAP is disabled")

    def _go() -> dict[str, Any]:
        conn = _bind_admin_sync()
        try:
            return {
                "bound": True,
                "server": settings.ldap_server,
                "port": settings.ldap_port,
                "tls": "ssl" if settings.ldap_use_ssl else "starttls" if settings.ldap_use_starttls else "none",
                "who_am_i": conn.extend.standard.who_am_i(),
            }
        finally:
            try:
                conn.unbind()
            except LDAPException:
                pass

    return await asyncio.to_thread(_go)
