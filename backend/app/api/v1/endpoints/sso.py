"""OIDC / SAML SSO endpoints。

OIDC flow：
  GET  /auth/oidc/login           → 302 到 IdP（state + nonce 寫進 short-lived JWT cookie）
  GET  /auth/oidc/callback?code=  → 換 token、抓 userinfo、auto-provision user
  GET  /auth/oidc/test            (admin) → 連線測試（discover）

SAML：留 stub（Phase 3.5 完整實作；OIDC 通常足夠）
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import require_admin
from app.core.audit import append_audit
from app.core.config import get_settings
from app.core.db import get_session
from app.core.security import create_access_token, decode_access_token
from app.services import oidc as oidc_service
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


# ─────────────────── SAML stub（Phase 3.5）───────────────────


@router.get("/saml/metadata")
async def saml_metadata() -> dict[str, str]:
    settings = get_settings()
    if not settings.saml_enabled:
        raise HTTPException(503, detail="SAML is disabled")
    raise HTTPException(501, detail="SAML metadata endpoint scheduled for Phase 3.5")


@router.post("/saml/acs")
async def saml_acs() -> dict[str, str]:
    settings = get_settings()
    if not settings.saml_enabled:
        raise HTTPException(503, detail="SAML is disabled")
    raise HTTPException(501, detail="SAML ACS scheduled for Phase 3.5")
