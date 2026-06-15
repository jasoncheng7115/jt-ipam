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
import jwt
from jwt import PyJWK
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
    def from_dict(cls, data: dict[str, Any]) -> OIDCDiscovery:
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


async def discover(cfg: Any) -> OIDCDiscovery:
    """從 issuer 取得 .well-known/openid-configuration（cache 一次）。"""
    if not cfg.enabled:
        raise OIDCNotConfigured("OIDC is disabled")
    if not cfg.issuer:
        raise OIDCNotConfigured("OIDC_ISSUER not set")

    if cfg.issuer in _discovery_cache:
        return _discovery_cache[cfg.issuer]

    url = cfg.issuer.rstrip("/") + "/.well-known/openid-configuration"
    try:
        resp = await safe_request("GET", url, timeout=10.0)
    except UnsafeOutboundURL as exc:
        raise OIDCError(f"SSRF guard rejected URL: {exc}") from exc
    except httpx.HTTPError as exc:
        raise OIDCError(f"transport: {exc.__class__.__name__}") from exc
    if resp.status_code != 200:
        raise OIDCError(f"OIDC discovery {resp.status_code}: {resp.text[:200]}")
    info = OIDCDiscovery.from_dict(resp.json())
    _discovery_cache[cfg.issuer] = info
    return info


def make_state() -> str:
    return secrets.token_urlsafe(24)


def make_nonce() -> str:
    return secrets.token_urlsafe(16)


async def build_auth_url(cfg: Any, state: str, nonce: str) -> str:
    info = await discover(cfg)
    if not (cfg.client_id and cfg.redirect_uri):
        raise OIDCNotConfigured("OIDC client_id / redirect_uri not set")
    from urllib.parse import urlencode
    qs = urlencode({
        "response_type": "code",
        "client_id": cfg.client_id,
        "redirect_uri": cfg.redirect_uri,
        "scope": cfg.scope,
        "state": state,
        "nonce": nonce,
    })
    return f"{info.authorization_endpoint}?{qs}"


async def exchange_code(cfg: Any, code: str) -> dict[str, Any]:
    info = await discover(cfg)
    if not (cfg.client_id and cfg.client_secret and cfg.redirect_uri):
        raise OIDCNotConfigured("OIDC credentials not fully configured")
    body = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": cfg.redirect_uri,
        "client_id": cfg.client_id,
        "client_secret": cfg.client_secret,
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
    return resp.json()  # type: ignore[no-any-return]


async def fetch_userinfo(cfg: Any, access_token: str) -> dict[str, Any]:
    info = await discover(cfg)
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
    return resp.json()  # type: ignore[no-any-return]


async def verify_id_token(cfg: Any, id_token: str, *, nonce: str | None = None) -> dict[str, Any]:
    """驗證 ID Token 簽章並回傳其 claims。

    OWASP A07：ID Token 是 IdP 簽發的獨立 artifact，**不可**只 base64 解開就信任其
    claims（尤其 `groups` 會決定 admin 提權）。這裡用 provider 的 JWKS 驗簽 + 檢查
    aud/iss/nonce，驗過才回 claims。JWKS 透過 safe_request 取得（保留 SSRF 防護）。
    """
    info = await discover(cfg)
    try:
        resp = await safe_request("GET", info.jwks_uri, timeout=10.0)
    except UnsafeOutboundURL as exc:
        raise OIDCError(f"SSRF guard rejected JWKS URL: {exc}") from exc
    if resp.status_code != 200:
        raise OIDCError(f"jwks {resp.status_code}: {resp.text[:200]}")
    jwks = resp.json()

    try:
        header = jwt.get_unverified_header(id_token)
    except jwt.PyJWTError as exc:
        raise OIDCError(f"ID Token header invalid: {exc}") from exc
    kid = header.get("kid")
    alg = header.get("alg") or "RS256"

    signing_key = None
    for jwk in jwks.get("keys", []):
        if jwk.get("kid") == kid:
            try:
                signing_key = PyJWK.from_dict(jwk).key
            except Exception as exc:
                raise OIDCError(f"ID Token signing key parse failed: {exc}") from exc
            break
    if signing_key is None:
        raise OIDCError("ID Token signing key not found in provider JWKS")

    try:
        claims: dict[str, Any] = jwt.decode(
            id_token,
            signing_key,
            algorithms=[alg],
            audience=cfg.client_id,
            issuer=info.issuer,
            options={"require": ["exp", "iat"], "verify_aud": True, "verify_iss": True},
        )
    except jwt.PyJWTError as exc:
        raise OIDCError(f"ID Token signature verification failed: {exc}") from exc

    if nonce is not None and claims.get("nonce") != nonce:
        raise OIDCError("ID Token nonce mismatch")
    return claims


# ─────────────────── User mapping ───────────────────


async def upsert_user_from_oidc(
    session: AsyncSession, cfg: Any, claims: dict[str, Any], actor_ip: str | None,
) -> User:
    sub = claims.get("sub")
    if not sub:
        raise OIDCError("OIDC userinfo missing sub")

    email = claims.get("email")
    username = (
        claims.get(cfg.username_claim)
        or claims.get("preferred_username")
        or email
        or sub
    )
    display_name = claims.get("name") or claims.get("given_name") or username

    groups_raw = claims.get(cfg.groups_claim) or []
    if isinstance(groups_raw, str):
        groups: list[str] = [g.strip() for g in groups_raw.split(",") if g.strip()]
    elif isinstance(groups_raw, list):
        groups = [str(g) for g in groups_raw]
    else:
        groups = []
    is_admin = any(g in cfg.admin_groups for g in groups)

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
