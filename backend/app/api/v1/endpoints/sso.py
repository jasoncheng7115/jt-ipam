"""OIDC / SAML SSO endpoints。

OIDC flow：
  GET  /auth/oidc/login           → 302 到 IdP（state + nonce 寫進 short-lived JWT cookie）
  GET  /auth/oidc/callback?code=  → 換 token、抓 userinfo、auto-provision user
  GET  /auth/oidc/test            (admin) → 連線測試（discover）

SAML flow：
  GET  /auth/saml/metadata        → 回 SP metadata XML（給 IdP 註冊）
  GET  /auth/saml/login           → 302 帶 SAMLRequest 到 IdP
  POST /auth/saml/acs             → 接 IdP SAMLResponse、auto-provision、簽 token、重導前端
  GET  /auth/saml/sls             → SLO（Single Logout）
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin
from app.core.audit import append_audit
from app.core.config import get_settings
from app.core.db import get_session
from app.core.security import create_access_token, decode_access_token
from app.services import oidc as oidc_service
from app.services import saml as saml_service
from app.services.auth import issue_access_token, issue_refresh_token
from app.services.system_config import (
    get_oidc_config,
    get_saml_config,
    set_oidc_config,
    set_saml_config,
)

router = APIRouter(prefix="/auth", tags=["sso"])


# ─────────────────── OIDC 設定（webui 管理；admin only）───────────────────
class OidcConfigOut(BaseModel):
    enabled: bool
    issuer: str | None
    client_id: str | None
    client_secret_set: bool   # 不回傳明文，只說有沒有設
    redirect_uri: str | None
    scope: str
    groups_claim: str
    username_claim: str
    admin_groups: list[str]
    default_group_id: str | None


class OidcConfigIn(BaseModel):
    enabled: bool = False
    issuer: str | None = None
    client_id: str | None = None
    # 留空 = 不變更既有密鑰；明確傳空字串需用獨立旗標，這裡語意：None/不送=不動，有值=更新
    client_secret: str | None = None
    redirect_uri: str | None = None
    scope: str = "openid profile email"
    groups_claim: str = "groups"
    username_claim: str = "preferred_username"
    admin_groups: list[str] = Field(default_factory=list)
    default_group_id: str | None = None


@router.get("/oidc/config", response_model=OidcConfigOut,
            dependencies=[Depends(require_admin)])
async def get_oidc_config_endpoint(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OidcConfigOut:
    cfg = await get_oidc_config(session)
    return OidcConfigOut(
        enabled=cfg.enabled, issuer=cfg.issuer, client_id=cfg.client_id,
        client_secret_set=bool(cfg.client_secret),
        redirect_uri=cfg.redirect_uri, scope=cfg.scope,
        groups_claim=cfg.groups_claim, username_claim=cfg.username_claim,
        admin_groups=cfg.admin_groups, default_group_id=cfg.default_group_id,
    )


@router.put("/oidc/config", response_model=OidcConfigOut,
            dependencies=[Depends(require_admin)])
async def put_oidc_config_endpoint(
    payload: OidcConfigIn, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OidcConfigOut:
    data: dict[str, Any] = {
        "enabled": payload.enabled,
        "issuer": payload.issuer,
        "client_id": payload.client_id,
        "redirect_uri": payload.redirect_uri,
        "scope": payload.scope,
        "groups_claim": payload.groups_claim,
        "username_claim": payload.username_claim,
        "admin_groups": payload.admin_groups,
        "default_group_id": payload.default_group_id,
    }
    # 只有實際送了 client_secret(非 None) 才更新；空字串=清除
    if payload.client_secret is not None:
        data["client_secret"] = payload.client_secret
    await set_oidc_config(session, data=data, updated_by_user_id=user.id)
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="system_setting", object_id=None, action="update",
        diff={"oidc": {k: v for k, v in data.items() if k != "client_secret"}},
        request_id=getattr(request.state, "request_id", None),
    )
    cfg = await get_oidc_config(session)
    return OidcConfigOut(
        enabled=cfg.enabled, issuer=cfg.issuer, client_id=cfg.client_id,
        client_secret_set=bool(cfg.client_secret),
        redirect_uri=cfg.redirect_uri, scope=cfg.scope,
        groups_claim=cfg.groups_claim, username_claim=cfg.username_claim,
        admin_groups=cfg.admin_groups, default_group_id=cfg.default_group_id,
    )


# ─────────────────── SAML 設定（webui 管理；admin only）───────────────────
class SamlConfigOut(BaseModel):
    enabled: bool
    idp_metadata_url: str | None
    idp_metadata_xml: str | None
    sp_entity_id: str | None
    sp_acs_url: str | None
    sp_sls_url: str | None
    sp_x509_cert: str | None
    sp_private_key_set: bool
    want_assertions_signed: bool
    want_assertions_encrypted: bool
    want_name_id_encrypted: bool
    authn_requests_signed: bool
    attr_username: str
    attr_email: str
    attr_displayname: str
    attr_groups: str
    admin_groups: list[str]
    default_group_id: str | None


class SamlConfigIn(BaseModel):
    enabled: bool = False
    idp_metadata_url: str | None = None
    idp_metadata_xml: str | None = None
    sp_entity_id: str | None = None
    sp_acs_url: str | None = None
    sp_sls_url: str | None = None
    sp_x509_cert: str | None = None
    sp_private_key: str | None = None   # None/不送=不變更；空字串=清除
    want_assertions_signed: bool = True
    want_assertions_encrypted: bool = False
    want_name_id_encrypted: bool = False
    authn_requests_signed: bool = False
    attr_username: str = "uid"
    attr_email: str = "email"
    attr_displayname: str = "displayName"
    attr_groups: str = "groups"
    admin_groups: list[str] = Field(default_factory=list)
    default_group_id: str | None = None


def _saml_out(cfg: Any) -> SamlConfigOut:
    return SamlConfigOut(
        enabled=cfg.enabled, idp_metadata_url=cfg.idp_metadata_url,
        idp_metadata_xml=cfg.idp_metadata_xml, sp_entity_id=cfg.sp_entity_id,
        sp_acs_url=cfg.sp_acs_url, sp_sls_url=cfg.sp_sls_url, sp_x509_cert=cfg.sp_x509_cert,
        sp_private_key_set=bool(cfg.sp_private_key),
        want_assertions_signed=cfg.want_assertions_signed,
        want_assertions_encrypted=cfg.want_assertions_encrypted,
        want_name_id_encrypted=cfg.want_name_id_encrypted,
        authn_requests_signed=cfg.authn_requests_signed,
        attr_username=cfg.attr_username, attr_email=cfg.attr_email,
        attr_displayname=cfg.attr_displayname, attr_groups=cfg.attr_groups,
        admin_groups=cfg.admin_groups, default_group_id=cfg.default_group_id,
    )


@router.get("/saml/config", response_model=SamlConfigOut,
            dependencies=[Depends(require_admin)])
async def get_saml_config_endpoint(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SamlConfigOut:
    return _saml_out(await get_saml_config(session))


@router.put("/saml/config", response_model=SamlConfigOut,
            dependencies=[Depends(require_admin)])
async def put_saml_config_endpoint(
    payload: SamlConfigIn, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SamlConfigOut:
    data: dict[str, Any] = payload.model_dump(exclude={"sp_private_key"})
    if payload.sp_private_key is not None:
        data["sp_private_key"] = payload.sp_private_key
    await set_saml_config(session, data=data, updated_by_user_id=user.id)
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="system_setting", object_id=None, action="update",
        diff={"saml": {k: v for k, v in data.items()
                       if k not in ("sp_private_key", "idp_metadata_xml", "sp_x509_cert")}},
        request_id=getattr(request.state, "request_id", None),
    )
    return _saml_out(await get_saml_config(session))


def _state_token(state: str, nonce: str) -> str:
    """state + nonce 包成短期 JWT，cookie 帶到 callback；防 CSRF + replay。"""
    return create_access_token(
        subject="oidc-flow",
        extra_claims={"state": state, "nonce": nonce, "type": "oidc_flow"},
        expires_in_minutes=10,
    )


def _decode_state_token(token: str) -> dict[str, Any]:
    payload = decode_access_token(token)
    if payload.get("type") != "oidc_flow":
        raise ValueError("not an oidc flow token")
    return payload


@router.get("/oidc/login")
async def oidc_login(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Any:
    settings = get_settings()
    cfg = await get_oidc_config(session)
    if not cfg.enabled:
        raise HTTPException(503, detail="OIDC is disabled")
    try:
        state = oidc_service.make_state()
        nonce = oidc_service.make_nonce()
        url = await oidc_service.build_auth_url(cfg, state, nonce)
    except oidc_service.OIDCNotConfigured as exc:
        raise HTTPException(503, detail=str(exc)) from exc
    except oidc_service.OIDCError as exc:
        raise HTTPException(502, detail=str(exc)) from exc

    flow_token = _state_token(state, nonce)
    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie(
        "jt_oidc_flow", flow_token,
        max_age=600,
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite=settings.session_cookie_samesite,
    )
    return resp


@router.get("/oidc/callback")
async def oidc_callback(
    request: Request,
    code: Annotated[str, Query(min_length=4, max_length=4096)],
    state: Annotated[str, Query(min_length=4, max_length=512)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Any:
    settings = get_settings()
    cfg = await get_oidc_config(session)
    if not cfg.enabled:
        raise HTTPException(503, detail="OIDC is disabled")

    flow_token = request.cookies.get("jt_oidc_flow")
    if not flow_token:
        raise HTTPException(400, detail="Missing OIDC flow cookie")
    try:
        payload = _decode_state_token(flow_token)
    except Exception as exc:
        raise HTTPException(400, detail="Invalid OIDC flow cookie") from exc
    if payload.get("state") != state:
        raise HTTPException(400, detail="State mismatch")

    try:
        token_data = await oidc_service.exchange_code(cfg, code)
    except oidc_service.OIDCError as exc:
        raise HTTPException(502, detail=str(exc)) from exc

    access_token = token_data.get("access_token")
    if not access_token:
        raise HTTPException(502, detail="OIDC: no access_token returned")

    # 用 userinfo 取代 id_token 解析（簡化；id_token 簽章驗證由 IdP 之後 phase 3.5 補）
    try:
        claims = await oidc_service.fetch_userinfo(cfg, access_token)
    except oidc_service.OIDCError as exc:
        raise HTTPException(502, detail=str(exc)) from exc

    # 合併 ID Token 的 claims，補 userinfo 沒有的欄位 —— 例如 Microsoft Entra ID 的
    # graph userinfo 不含 groups，groups 只在 ID Token 裡。userinfo 已有的鍵不覆蓋。
    # 安全性（OWASP A07）：ID Token 的 claims（尤其 groups → admin 提權）必須先用 JWKS
    # 驗簽 + 檢 aud/iss/nonce 才可信任；驗證失敗則只用 userinfo（不合併未驗 claims），
    # 既擋掉偽造 groups 的提權，又不因 IdP 偶發問題把使用者鎖在登入頁外。
    id_token_raw = token_data.get("id_token") or ""
    if id_token_raw:
        try:
            id_claims = await oidc_service.verify_id_token(
                cfg, id_token_raw, nonce=payload.get("nonce"),
            )
            for k, v in id_claims.items():
                claims.setdefault(k, v)
        except oidc_service.OIDCError as exc:
            logging.getLogger("sso").warning(
                "OIDC ID Token verification failed, using userinfo only: %s", exc,
            )

    try:
        user = await oidc_service.upsert_user_from_oidc(
            session, cfg, claims,
            actor_ip=request.client.host if request.client else None,
        )
    except oidc_service.OIDCError as exc:
        raise HTTPException(409, detail=str(exc)) from exc

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="auth", object_id=str(user.id),
        action="oidc_login",
        diff={"sub": claims.get("sub"), "email": claims.get("email")},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()

    access = issue_access_token(user)
    refresh = issue_refresh_token(user)

    # 重導到前端，把 token 透過 fragment 傳遞（避免 query 進 referrer）
    target = settings.app_public_url
    redir = f"{str(target).rstrip('/')}/login#access_token={access}&refresh_token={refresh}"
    resp = RedirectResponse(redir, status_code=302)
    resp.delete_cookie("jt_oidc_flow")
    return resp


@router.get("/oidc/test", dependencies=[Depends(require_admin)])
async def oidc_test(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    cfg = await get_oidc_config(session)
    try:
        info = await oidc_service.discover(cfg)
    except oidc_service.OIDCNotConfigured as exc:
        raise HTTPException(503, detail=str(exc)) from exc
    except oidc_service.OIDCError as exc:
        raise HTTPException(502, detail=str(exc)) from exc
    return {
        "issuer": info.issuer,
        "authorization_endpoint": info.authorization_endpoint,
        "token_endpoint": info.token_endpoint,
        "userinfo_endpoint": info.userinfo_endpoint,
    }


# ─────────────────── SAML 2.0 ───────────────────


def _saml_state_token(relay_state: str) -> str:
    """relay state 包成 short-lived JWT；A07：防 IdP-initiated 攻擊串接。"""
    return create_access_token(
        subject="saml-flow",
        extra_claims={"relay_state": relay_state, "type": "saml_flow"},
        expires_in_minutes=10,
    )


def _decode_saml_state(token: str) -> str:
    payload = decode_access_token(token)
    if payload.get("type") != "saml_flow":
        raise ValueError("not a saml flow token")
    return payload.get("relay_state") or "/"


@router.get("/saml/metadata")
async def saml_metadata(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """SP metadata XML — 給 IdP 註冊用。"""
    cfg = await get_saml_config(session)
    if not cfg.enabled:
        raise HTTPException(503, detail="SAML is disabled")
    try:
        xml = await saml_service.metadata_xml(cfg)
    except saml_service.SAMLNotConfigured as exc:
        raise HTTPException(503, detail=str(exc)) from exc
    except saml_service.SAMLError as exc:
        raise HTTPException(502, detail=str(exc)) from exc
    return Response(content=xml, media_type="application/samlmetadata+xml")


@router.get("/saml/login")
async def saml_login(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    return_to: Annotated[str | None, Query(min_length=1, max_length=512)] = None,
) -> Any:
    """SP-initiated：建 AuthnRequest → 重導 IdP。"""
    settings = get_settings()
    cfg = await get_saml_config(session)
    if not cfg.enabled:
        raise HTTPException(503, detail="SAML is disabled")

    # return_to 限本機路徑（A01）
    safe_return_to = "/"
    if return_to and return_to.startswith("/") and not return_to.startswith("//"):
        safe_return_to = return_to

    try:
        url = await saml_service.build_auth_url(request, cfg, return_to=safe_return_to)
    except saml_service.SAMLNotConfigured as exc:
        raise HTTPException(503, detail=str(exc)) from exc
    except saml_service.SAMLError as exc:
        raise HTTPException(502, detail=str(exc)) from exc

    flow = _saml_state_token(safe_return_to)
    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie(
        "jt_saml_flow", flow,
        max_age=600,
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite=settings.session_cookie_samesite,
    )
    return resp


@router.post("/saml/acs")
async def saml_acs(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    SAMLResponse: Annotated[str, Form(min_length=4, max_length=200_000)],
    RelayState: Annotated[str | None, Form(max_length=512)] = None,
) -> Any:
    """AssertionConsumerService — 收 IdP 回的 SAMLResponse。"""
    settings = get_settings()
    cfg = await get_saml_config(session)
    if not cfg.enabled:
        raise HTTPException(503, detail="SAML is disabled")

    post_data = {"SAMLResponse": SAMLResponse}
    if RelayState:
        post_data["RelayState"] = RelayState

    try:
        claims = await saml_service.process_acs(request, cfg, post_data)
    except saml_service.SAMLError as exc:
        raise HTTPException(401, detail=str(exc)) from exc

    try:
        user = await saml_service.upsert_user_from_saml(
            session, claims, cfg,
            actor_ip=request.client.host if request.client else None,
        )
    except saml_service.SAMLError as exc:
        raise HTTPException(409, detail=str(exc)) from exc

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="auth", object_id=str(user.id),
        action="saml_login",
        diff={
            "name_id": claims.get("name_id"),
            "session_index": claims.get("session_index"),
        },
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()

    access = issue_access_token(user)
    refresh = issue_refresh_token(user)

    # 驗 RelayState（同 OIDC：透過 cookie 驗一次）
    return_to = "/"
    flow_token = request.cookies.get("jt_saml_flow")
    if flow_token:
        try:
            return_to = _decode_saml_state(flow_token) or "/"
        except Exception:
            return_to = "/"

    target = settings.app_public_url
    redir = (
        f"{str(target).rstrip('/')}{return_to.rstrip('/') or ''}/login"
        f"#access_token={access}&refresh_token={refresh}"
    )
    resp = RedirectResponse(redir, status_code=302)
    resp.delete_cookie("jt_saml_flow")
    return resp


@router.get("/saml/sls")
async def saml_sls(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Any:
    """SP-initiated SLO；前端登出時導到這。"""
    settings = get_settings()
    cfg = await get_saml_config(session)
    if not cfg.enabled:
        raise HTTPException(503, detail="SAML is disabled")

    name_id = request.query_params.get("name_id")
    session_index = request.query_params.get("session_index")
    try:
        url = await saml_service.build_logout_url(
            request, cfg, name_id=name_id, session_index=session_index,
        )
    except saml_service.SAMLNotConfigured as exc:
        raise HTTPException(503, detail=str(exc)) from exc
    except saml_service.SAMLError as exc:
        raise HTTPException(502, detail=str(exc)) from exc

    if not url:
        # IdP 沒提供 SLO endpoint — 本地登出即可
        target = str(settings.app_public_url).rstrip("/") + "/login"
        return RedirectResponse(target, status_code=302)
    return RedirectResponse(url, status_code=302)


@router.get("/saml/test", dependencies=[Depends(require_admin)])
async def saml_test(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """連線測試：確認可以解 IdP metadata。"""
    try:
        cfg = await get_saml_config(session)
        idp = await saml_service._fetch_idp_metadata(cfg)
    except saml_service.SAMLNotConfigured as exc:
        raise HTTPException(503, detail=str(exc)) from exc
    except saml_service.SAMLError as exc:
        raise HTTPException(502, detail=str(exc)) from exc
    return {
        "entity_id": idp.entity_id,
        "sso_url": idp.sso_url,
        "slo_url": idp.slo_url,
    }
