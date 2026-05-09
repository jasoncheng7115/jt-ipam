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

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import require_admin
from app.core.audit import append_audit
from app.core.config import get_settings
from app.core.db import get_session
from app.core.security import create_access_token, decode_access_token
from app.services import oidc as oidc_service
from app.services import saml as saml_service
from app.services.auth import issue_access_token, issue_refresh_token

router = APIRouter(prefix="/auth", tags=["sso"])


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
async def oidc_login(request: Request) -> Any:
    settings = get_settings()
    if not settings.oidc_enabled:
        raise HTTPException(503, detail="OIDC is disabled")
    try:
        state = oidc_service.make_state()
        nonce = oidc_service.make_nonce()
        url = await oidc_service.build_auth_url(state, nonce)
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
    if not settings.oidc_enabled:
        raise HTTPException(503, detail="OIDC is disabled")

    flow_token = request.cookies.get("jt_oidc_flow")
    if not flow_token:
        raise HTTPException(400, detail="Missing OIDC flow cookie")
    try:
        payload = _decode_state_token(flow_token)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, detail="Invalid OIDC flow cookie") from exc
    if payload.get("state") != state:
        raise HTTPException(400, detail="State mismatch")

    try:
        token_data = await oidc_service.exchange_code(code)
    except oidc_service.OIDCError as exc:
        raise HTTPException(502, detail=str(exc)) from exc

    access_token = token_data.get("access_token")
    if not access_token:
        raise HTTPException(502, detail="OIDC: no access_token returned")

    # 用 userinfo 取代 id_token 解析（簡化；id_token 簽章驗證由 IdP 之後 phase 3.5 補）
    try:
        claims = await oidc_service.fetch_userinfo(access_token)
    except oidc_service.OIDCError as exc:
        raise HTTPException(502, detail=str(exc)) from exc

    try:
        user = await oidc_service.upsert_user_from_oidc(
            session, claims,
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
async def oidc_test() -> dict[str, Any]:
    try:
        info = await oidc_service.discover()
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
async def saml_metadata() -> Response:
    """SP metadata XML — 給 IdP 註冊用。"""
    settings = get_settings()
    if not settings.saml_enabled:
        raise HTTPException(503, detail="SAML is disabled")
    try:
        xml = await saml_service.metadata_xml()
    except saml_service.SAMLNotConfigured as exc:
        raise HTTPException(503, detail=str(exc)) from exc
    except saml_service.SAMLError as exc:
        raise HTTPException(502, detail=str(exc)) from exc
    return Response(content=xml, media_type="application/samlmetadata+xml")


@router.get("/saml/login")
async def saml_login(
    request: Request,
    return_to: Annotated[str | None, Query(min_length=1, max_length=512)] = None,
) -> Any:
    """SP-initiated：建 AuthnRequest → 重導 IdP。"""
    settings = get_settings()
    if not settings.saml_enabled:
        raise HTTPException(503, detail="SAML is disabled")

    # return_to 限本機路徑（A01）
    safe_return_to = "/"
    if return_to and return_to.startswith("/") and not return_to.startswith("//"):
        safe_return_to = return_to

    try:
        url = await saml_service.build_auth_url(request, return_to=safe_return_to)
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
    if not settings.saml_enabled:
        raise HTTPException(503, detail="SAML is disabled")

    post_data = {"SAMLResponse": SAMLResponse}
    if RelayState:
        post_data["RelayState"] = RelayState

    try:
        claims = await saml_service.process_acs(request, post_data)
    except saml_service.SAMLError as exc:
        raise HTTPException(401, detail=str(exc)) from exc

    try:
        user = await saml_service.upsert_user_from_saml(
            session, claims,
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
        except Exception:  # noqa: BLE001
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
async def saml_sls(request: Request) -> Any:
    """SP-initiated SLO；前端登出時導到這。"""
    settings = get_settings()
    if not settings.saml_enabled:
        raise HTTPException(503, detail="SAML is disabled")

    name_id = request.query_params.get("name_id")
    session_index = request.query_params.get("session_index")
    try:
        url = await saml_service.build_logout_url(
            request, name_id=name_id, session_index=session_index,
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
async def saml_test() -> dict[str, Any]:
    """連線測試：確認可以解 IdP metadata。"""
    try:
        idp = await saml_service._fetch_idp_metadata()
    except saml_service.SAMLNotConfigured as exc:
        raise HTTPException(503, detail=str(exc)) from exc
    except saml_service.SAMLError as exc:
        raise HTTPException(502, detail=str(exc)) from exc
    return {
        "entity_id": idp.entity_id,
        "sso_url": idp.sso_url,
        "slo_url": idp.slo_url,
    }
