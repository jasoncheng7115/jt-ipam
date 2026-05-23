"""SAML 2.0 Service Provider（透過 python3-saml / OneLogin_Saml2）。

支援典型 SAML IdP：AD FS、Azure AD（Entra ID）、Keycloak、Okta、Authentik、Shibboleth…

Flow：
  1. /auth/saml/login           → 302 帶 SAMLRequest 到 IdP
  2. /auth/saml/acs (POST)      → IdP 回 SAMLResponse；驗章 → upsert user → 簽 jt-ipam token → 重導前端
  3. /auth/saml/metadata        → 回 SP metadata XML（給 IdP 註冊）
  4. /auth/saml/sls (GET/POST)  → Single Logout（選填）

OWASP A04 / A07：
- python3-saml 預設要求 assertion 簽章；可選擇加密 assertion
- 我方驗 InResponseTo / Destination / Conditions / Audience；防 replay
- relay_state 包到 short-lived JWT cookie，回呼必須驗
- IdP metadata 透過 safe_request 拉（SSRF allowlist）
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.safe_http import UnsafeOutboundURL, safe_request
from app.models.user import User


class SAMLNotConfigured(RuntimeError):
    pass


class SAMLError(RuntimeError):
    pass


# ─────────────────── Settings 構建 ───────────────────


@dataclass
class _IdPInfo:
    entity_id: str
    sso_url: str
    sso_binding: str
    slo_url: str | None
    slo_binding: str | None
    x509_cert: str
    fetched_at: datetime


_idp_cache: dict[str, _IdPInfo] = {}


def _public_base_url() -> str:
    s = get_settings()
    return str(s.api_public_url).rstrip("/")


def _resolve_sp_endpoints() -> dict[str, str]:
    s = get_settings()
    base = _public_base_url()
    return {
        "entity_id": s.saml_sp_entity_id or s.saml_entity_id or f"{base}/api/v1/auth/saml/metadata",
        "acs_url": s.saml_sp_acs_url or s.saml_acs_url or f"{base}/api/v1/auth/saml/acs",
        "sls_url": s.saml_sp_sls_url or f"{base}/api/v1/auth/saml/sls",
    }


async def _fetch_idp_metadata() -> _IdPInfo:
    """從 saml_idp_metadata_url（safe_request）或 saml_idp_metadata_xml 取 IdP info。"""
    s = get_settings()
    if not s.saml_enabled:
        raise SAMLNotConfigured("SAML is disabled")

    url = s.saml_idp_metadata_url or s.saml_metadata_url
    inline_xml = s.saml_idp_metadata_xml

    cache_key = url or "<inline>"
    if cache_key in _idp_cache:
        info = _idp_cache[cache_key]
        # 24h cache
        if (datetime.now(UTC) - info.fetched_at).total_seconds() < 86400:
            return info

    if not url and not inline_xml:
        raise SAMLNotConfigured("SAML_IDP_METADATA_URL 或 SAML_IDP_METADATA_XML 必填")

    if url:
        try:
            resp = await safe_request("GET", url, timeout=15.0)
        except UnsafeOutboundURL as exc:
            raise SAMLError(f"SSRF guard rejected metadata URL: {exc}") from exc
        except httpx.HTTPError as exc:
            raise SAMLError(f"transport: {exc.__class__.__name__}") from exc
        if resp.status_code != 200:
            raise SAMLError(f"SAML metadata HTTP {resp.status_code}")
        xml = resp.text
    else:
        xml = inline_xml or ""

    info = _parse_idp_metadata(xml)
    _idp_cache[cache_key] = info
    return info


def _parse_idp_metadata(xml: str) -> _IdPInfo:
    """用 python3-saml 的 IdPMetadataParser 解 IdP metadata。"""
    try:
        from onelogin.saml2.idp_metadata_parser import OneLogin_Saml2_IdPMetadataParser
    except ImportError as exc:
        raise SAMLError("python3-saml not installed") from exc

    try:
        parsed = OneLogin_Saml2_IdPMetadataParser.parse(xml)
    except Exception as exc:  # noqa: BLE001 — parser 會丟各種 lxml/xmlsec 錯
        raise SAMLError(f"failed to parse IdP metadata: {exc}") from exc

    idp = parsed.get("idp") or {}
    if not idp.get("entityId") or not idp.get("singleSignOnService", {}).get("url"):
        raise SAMLError("IdP metadata missing entityId / SSO URL")

    sso = idp["singleSignOnService"]
    slo = idp.get("singleLogoutService") or {}
    cert = idp.get("x509cert") or ""
    if not cert:
        raise SAMLError("IdP metadata missing x509cert")

    return _IdPInfo(
        entity_id=idp["entityId"],
        sso_url=sso["url"],
        sso_binding=sso.get("binding", "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"),
        slo_url=slo.get("url"),
        slo_binding=slo.get("binding"),
        x509_cert=cert,
        fetched_at=datetime.now(UTC),
    )


async def build_settings() -> dict[str, Any]:
    """組成 OneLogin_Saml2_Auth 要的 settings dict。"""
    s = get_settings()
    if not s.saml_enabled:
        raise SAMLNotConfigured("SAML is disabled")

    sp = _resolve_sp_endpoints()
    idp = await _fetch_idp_metadata()

    settings_dict: dict[str, Any] = {
        "strict": True,
        "debug": False,
        "sp": {
            "entityId": sp["entity_id"],
            "assertionConsumerService": {
                "url": sp["acs_url"],
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
            },
            "singleLogoutService": {
                "url": sp["sls_url"],
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
            },
            "NameIDFormat": "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
            "x509cert": s.saml_sp_x509_cert or "",
            "privateKey": (
                s.saml_sp_private_key.get_secret_value() if s.saml_sp_private_key else ""
            ),
        },
        "idp": {
            "entityId": idp.entity_id,
            "singleSignOnService": {
                "url": idp.sso_url,
                "binding": idp.sso_binding,
            },
            "x509cert": idp.x509_cert,
        },
        "security": {
            "wantAssertionsSigned": s.saml_want_assertions_signed,
            "wantAssertionsEncrypted": s.saml_want_assertions_encrypted,
            "wantNameIdEncrypted": s.saml_want_name_id_encrypted,
            "authnRequestsSigned": s.saml_authn_requests_signed,
            "logoutRequestSigned": s.saml_authn_requests_signed,
            "logoutResponseSigned": s.saml_authn_requests_signed,
            "wantMessagesSigned": False,
            "signatureAlgorithm": "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256",
            "digestAlgorithm": "http://www.w3.org/2001/04/xmlenc#sha256",
            "requestedAuthnContext": False,
        },
    }
    if idp.slo_url:
        settings_dict["idp"]["singleLogoutService"] = {
            "url": idp.slo_url,
            "binding": idp.slo_binding or "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
        }
    return settings_dict


def _request_dict_from_starlette(req: Any, post_data: dict[str, Any] | None = None) -> dict[str, Any]:
    """python3-saml 期望的 request 結構。"""
    url = urlparse(str(req.url))
    return {
        "https": "on" if url.scheme == "https" else "off",
        "http_host": req.headers.get("host") or url.netloc,
        "server_port": str(url.port or (443 if url.scheme == "https" else 80)),
        "script_name": url.path,
        "get_data": dict(req.query_params),
        "post_data": post_data or {},
    }


# ─────────────────── 核心 API ───────────────────


async def build_auth_url(request: Any, *, return_to: str | None = None) -> str:
    """產生 SP-initiated SSO 的 IdP 重導 URL。"""
    from onelogin.saml2.auth import OneLogin_Saml2_Auth

    settings_dict = await build_settings()
    auth = OneLogin_Saml2_Auth(_request_dict_from_starlette(request), settings_dict)
    return auth.login(return_to=return_to or "/")


async def process_acs(request: Any, post_data: dict[str, Any]) -> dict[str, Any]:
    """處理 IdP 送回的 SAMLResponse；回傳 normalized claims。"""
    from onelogin.saml2.auth import OneLogin_Saml2_Auth

    settings_dict = await build_settings()
    auth = OneLogin_Saml2_Auth(
        _request_dict_from_starlette(request, post_data=post_data),
        settings_dict,
    )
    auth.process_response()
    errors = auth.get_errors()
    if errors:
        reason = auth.get_last_error_reason() or ",".join(errors)
        raise SAMLError(f"SAML response invalid: {reason}")
    if not auth.is_authenticated():
        raise SAMLError("SAML response not authenticated")

    attrs = auth.get_attributes() or {}
    name_id = auth.get_nameid()
    return {
        "name_id": name_id,
        "session_index": auth.get_session_index(),
        "attributes": {k: list(v) if isinstance(v, list) else [v] for k, v in attrs.items()},
        "relay_state": post_data.get("RelayState"),
    }


async def metadata_xml() -> str:
    """產生 SP metadata（給 IdP 註冊用）。"""
    from onelogin.saml2.settings import OneLogin_Saml2_Settings

    settings_dict = await build_settings()
    saml_settings = OneLogin_Saml2_Settings(settings=settings_dict, sp_validation_only=True)
    metadata = saml_settings.get_sp_metadata()
    errors = saml_settings.validate_metadata(metadata)
    if errors:
        raise SAMLError(f"metadata validation: {errors}")
    if isinstance(metadata, bytes):
        return metadata.decode("utf-8")
    return metadata


async def build_logout_url(request: Any, *, name_id: str | None, session_index: str | None) -> str:
    """產生 SP-initiated SLO 的 IdP 重導 URL。"""
    from onelogin.saml2.auth import OneLogin_Saml2_Auth

    settings_dict = await build_settings()
    auth = OneLogin_Saml2_Auth(_request_dict_from_starlette(request), settings_dict)
    return auth.logout(name_id=name_id, session_index=session_index)


# ─────────────────── User mapping ───────────────────


def _first(attrs: dict[str, list[Any]], key: str) -> str | None:
    v = attrs.get(key)
    if isinstance(v, list) and v:
        return str(v[0])
    if isinstance(v, str):
        return v
    return None


async def upsert_user_from_saml(
    session: AsyncSession, claims: dict[str, Any], *, actor_ip: str | None,
) -> User:
    """以 SAML claims 建立或更新 User。external_subject = NameID。"""
    settings = get_settings()
    attrs: dict[str, list[Any]] = claims.get("attributes") or {}
    name_id = claims.get("name_id")
    if not name_id:
        raise SAMLError("SAML response missing NameID")

    username = _first(attrs, settings.saml_attr_username) or name_id
    email = _first(attrs, settings.saml_attr_email) or (name_id if "@" in name_id else None)
    display_name = _first(attrs, settings.saml_attr_displayname) or username

    groups_raw = attrs.get(settings.saml_attr_groups) or []
    if isinstance(groups_raw, str):
        groups = [g.strip() for g in groups_raw.split(",") if g.strip()]
    else:
        groups = [str(g) for g in groups_raw]
    is_admin = any(g in settings.saml_admin_groups for g in groups)

    user = (
        await session.execute(
            select(User).where(
                User.auth_provider == "saml",
                User.external_subject == name_id,
            )
        )
    ).scalar_one_or_none()

    if user is None:
        existing = (
            await session.execute(select(User).where(User.username == username))
        ).scalar_one_or_none()
        if existing is not None:
            raise SAMLError(
                f"username {username!r} already exists with provider "
                f"{existing.auth_provider}; reconcile manually"
            )
        user = User(
            username=username,
            email=email or f"{username}@saml.local",
            display_name=display_name,
            auth_provider="saml",
            external_subject=name_id,
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
