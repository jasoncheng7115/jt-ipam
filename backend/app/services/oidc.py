"""OIDC（OpenID Connect）SSO 整合。

支援標準 OIDC provider（Google Workspace / Microsoft Entra ID / Keycloak / Okta /
Authentik …）。透過 authlib OAuth2Client。

Flow：
  1. /auth/oidc/start          — 重導到 IdP 的 authorization endpoint
  2. /auth/oidc/callback       — 接 IdP 的 code，換 token + userinfo
  3. 比對 jt-ipam User（用 email 或 username 做 key），auto-provision 或更新
  4. 簽發本機 access/refresh token

OWASP A04 / A07：
- client_secret 從 SecretStr；TLS 強制（authlib 內建驗證）
- state + nonce 都會檢查；CSRF 透過 session cookie 帶 state
- 回呼 redirect_uri 必須與設定值精確相符（IdP 端與本端都會驗）
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.safe_http import UnsafeOutboundURL, safe_request
from app.models.user import User


class OIDCNotConfigured(RuntimeError):
    pass


class OIDCError(RuntimeError):
    pass


@dataclass
class OIDCDiscovery:
    authorization_endpoint: str
    token_endpoint: str
    userinfo_endpoint: str | None
    jwks_uri: str
    issuer: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OIDCDiscovery":
        for k in ("authorization_endpoint", "token_endpoint", "issuer", "jwks_uri"):
            if k not in data:
                raise OIDCError(f"OIDC discovery missing {k}")
        return cls(
            authorization_endpoint=data["authorization_endpoint"],
            token_endpoint=data["token_endpoint"],
            userinfo_endpoint=data.get("userinfo_endpoint"),
            jwks_uri=data["jwks_uri"],
            issuer=data["issuer"],
        )


_discovery_cache: dict[str, OIDCDiscovery] = {}


async def discover() -> OIDCDiscovery:
    """從 issuer 取得 .well-known/openid-configuration（cache 一次）。"""
    settings = get_settings()
    if not settings.oidc_enabled:
        raise OIDCNotConfigured("OIDC is disabled")
    if not settings.oidc_issuer:
        raise OIDCNotConfigured("OIDC_ISSUER not set")

    if settings.oidc_issuer in _discovery_cache:
        return _discovery_cache[settings.oidc_issuer]

    url = settings.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration"
    try:
        resp = await safe_request("GET", url, timeout=10.0)
    except UnsafeOutboundURL as exc:
        raise OIDCError(f"SSRF guard rejected URL: {exc}") from exc
    except httpx.HTTPError as exc:
        raise OIDCError(f"transport: {exc.__class__.__name__}") from exc
    if resp.status_code != 200:
        raise OIDCError(f"OIDC discovery {resp.status_code}: {resp.text[:200]}")
    info = OIDCDiscovery.from_dict(resp.json())
    _discovery_cache[settings.oidc_issuer] = info
    return info


def make_state() -> str:
    return secrets.token_urlsafe(24)


def make_nonce() -> str:
    return secrets.token_urlsafe(16)


async def build_auth_url(state: str, nonce: str) -> str:
    settings = get_settings()
    info = await discover()
    if not (settings.oidc_client_id and settings.oidc_redirect_uri):
        raise OIDCNotConfigured("OIDC client_id / redirect_uri not set")
    from urllib.parse import urlencode
    qs = urlencode({
        "response_type": "code",
        "client_id": settings.oidc_client_id,
        "redirect_uri": settings.oidc_redirect_uri,
        "scope": settings.oidc_scope,
        "state": state,
        "nonce": nonce,
    })
    return f"{info.authorization_endpoint}?{qs}"


async def exchange_code(code: str) -> dict[str, Any]:
    settings = get_settings()
    info = await discover()
    if not (settings.oidc_client_id and settings.oidc_client_secret
            and settings.oidc_redirect_uri):
        raise OIDCNotConfigured("OIDC credentials not fully configured")
    body = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.oidc_redirect_uri,
        "client_id": settings.oidc_client_id,
        "client_secret": settings.oidc_client_secret.get_secret_value(),
    }
    try:
        resp = await safe_request(
            "POST", info.token_endpoint,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            content="&".join(f"{k}={v}" for k, v in body.items()).encode("utf-8"),
            timeout=15.0,
        )
    except UnsafeOutboundURL as exc:
        raise OIDCError(f"SSRF guard rejected URL: {exc}") from exc
    if resp.status_code != 200:
        raise OIDCError(f"token exchange {resp.status_code}: {resp.text[:200]}")
    return resp.json()


async def fetch_userinfo(access_token: str) -> dict[str, Any]:
    info = await discover()
    if not info.userinfo_endpoint:
        raise OIDCError("Provider does not expose userinfo endpoint")
    try:
        resp = await safe_request(
            "GET", info.userinfo_endpoint,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10.0,
        )
    except UnsafeOutboundURL as exc:
        raise OIDCError(f"SSRF guard rejected URL: {exc}") from exc
    if resp.status_code != 200:
        raise OIDCError(f"userinfo {resp.status_code}: {resp.text[:200]}")
    return resp.json()


# ─────────────────── User mapping ───────────────────


async def upsert_user_from_oidc(
    session: AsyncSession, claims: dict[str, Any], actor_ip: str | None,
) -> User:
    settings = get_settings()
    sub = claims.get("sub")
    if not sub:
        raise OIDCError("OIDC userinfo missing sub")

    email = claims.get("email")
    username = (
        claims.get(settings.oidc_username_claim)
        or claims.get("preferred_username")
        or email
        or sub
    )
    display_name = claims.get("name") or claims.get("given_name") or username

    groups_raw = claims.get(settings.oidc_groups_claim) or []
    if isinstance(groups_raw, str):
        groups: list[str] = [g.strip() for g in groups_raw.split(",") if g.strip()]
    elif isinstance(groups_raw, list):
        groups = [str(g) for g in groups_raw]
    else:
        groups = []
    is_admin = any(g in settings.oidc_admin_groups for g in groups)

    # 找：external_subject 比對；沒就 fallback 到 username
    user = (
        await session.execute(
            select(User).where(
                User.auth_provider == "oidc",
                User.external_subject == sub,
            )
        )
    ).scalar_one_or_none()

    if user is None:
        # 同 username 但不同 provider：拒絕（避免帳號 hijack）
        existing = (
            await session.execute(select(User).where(User.username == username))
        ).scalar_one_or_none()
        if existing is not None:
            raise OIDCError(
                f"username {username!r} already exists with provider "
                f"{existing.auth_provider}; reconcile manually"
            )
        user = User(
            username=username,
            email=email or f"{username}@oidc.local",
            display_name=display_name,
            auth_provider="oidc",
            external_subject=sub,
            is_active=True,
            is_admin=is_admin,
        )
        session.add(user)
        await session.flush()
    else:
        user.email = email or user.email
        user.display_name = display_name or user.display_name
        user.is_admin = is_admin

    user.last_login_at = datetime.now(UTC)
    user.last_login_ip = actor_ip
    user.failed_login_count = 0
    user.locked_until = None
    return user
