"""認證端點：login / refresh / logout / me。"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser
from app.core.config import get_settings
from app.core.db import get_session
from app.core.rate_limit import limit_per_ip
from app.models.user import User
from app.schemas.auth import LoginRequest, RefreshRequest, TokenResponse
from app.schemas.totp import ConfirmRequest, EnrollResponse, VerifyRequest
from app.schemas.user import UserMe
from app.services import ldap_auth
from app.services import totp as totp_service
from app.services.auth import (
    AccountInactive,
    AccountLocked,
    InvalidCredentials,
    TokenInvalid,
    authenticate,
    decode_token,
    issue_access_token,
    issue_refresh_token,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TokenResponse:
    # A04 / A07：登入端點較嚴格的限流
    await limit_per_ip(request, name="auth")

    try:
        user = await authenticate(
            session,
            username=payload.username,
            password=payload.password,
            actor_ip=request.client.host if request.client else None,
            actor_user_agent=request.headers.get("user-agent"),
            request_id=getattr(request.state, "request_id", None),
        )
    except (InvalidCredentials, AccountLocked, AccountInactive) as exc:
        # A07：所有 4xx 都統一回 401，不區分原因（防 enumeration）
        # AccountLocked 例外 — 給 retry-after 提示
        if isinstance(exc, AccountLocked):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=exc.public_message,
                headers={"Retry-After": "900"},
            ) from exc
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials") from exc

    # A07：若使用者啟用了 TOTP，發 challenge 而非直接發 token
    if totp_service.is_enabled(user):
        return TokenResponse(
            mfa_required=True,
            mfa_token=totp_service.issue_mfa_challenge(user),
        )

    settings = get_settings()
    return TokenResponse(
        access_token=issue_access_token(user),
        refresh_token=issue_refresh_token(user),
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.post("/mfa/verify", response_model=TokenResponse)
async def mfa_verify(
    payload: VerifyRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TokenResponse:
    """登入第二步：用 mfa_token + 6-digit code 換 access/refresh token。"""
    await limit_per_ip(request, name="auth")

    try:
        claims = decode_token(payload.mfa_token, expected_type="mfa_challenge")
    except TokenInvalid as exc:
        raise HTTPException(status_code=401, detail="Invalid MFA challenge") from exc

    sub = claims.get("sub")
    if not isinstance(sub, str):
        raise HTTPException(status_code=401, detail="Invalid token subject")
    try:
        user_id = uuid.UUID(sub)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid token subject") from exc

    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Account inactive")

    if not await totp_service.verify_code(user, payload.code):
        # 失敗時也記入 audit（A09）
        from app.core.audit import append_audit
        await append_audit(
            session,
            actor_user_id=str(user.id),
            actor_ip=request.client.host if request.client else None,
            actor_user_agent=request.headers.get("user-agent"),
            object_type="auth",
            object_id=str(user.id),
            action="mfa_failed",
            diff={"reason": "invalid_code"},
            request_id=getattr(request.state, "request_id", None),
        )
        await session.commit()
        raise HTTPException(status_code=401, detail="Invalid MFA code")

    settings = get_settings()
    return TokenResponse(
        access_token=issue_access_token(user),
        refresh_token=issue_refresh_token(user),
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.post("/totp/enroll", response_model=EnrollResponse)
async def totp_enroll(user: CurrentUser) -> EnrollResponse:
    """產生新 secret 與 otpauth URI；client 顯示 QR；下一步 /totp/confirm。

    注意：尚未寫入 DB；client 需 confirm 才生效。
    """
    secret = totp_service.begin_enrollment()
    uri = totp_service.provisioning_uri(secret, account=user.username)
    return EnrollResponse(secret=secret, otpauth_uri=uri)


@router.post("/totp/confirm", status_code=204)
async def totp_confirm(
    payload: ConfirmRequest,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """用 enroll 拿到的 secret + 第一筆 6-digit code 確認；正確才寫入 DB。"""
    if totp_service.is_enabled(user):
        raise HTTPException(status_code=409, detail="TOTP already enabled")

    ok = await totp_service.confirm_enrollment(
        session, user=user, secret=payload.secret, code=payload.code
    )
    if not ok:
        raise HTTPException(status_code=400, detail="Invalid TOTP code")

    from app.core.audit import append_audit
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="user",
        object_id=str(user.id),
        action="totp_enabled",
        diff=None,
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()


@router.post("/totp/disable", status_code=204)
async def totp_disable(
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    if not totp_service.is_enabled(user):
        raise HTTPException(status_code=409, detail="TOTP not enabled")
    await totp_service.disable(session, user=user)

    from app.core.audit import append_audit
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="user",
        object_id=str(user.id),
        action="totp_disabled",
        diff=None,
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    payload: RefreshRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TokenResponse:
    await limit_per_ip(request, name="auth")
    try:
        claims = decode_token(payload.refresh_token, expected_type="refresh")
    except TokenInvalid as exc:
        raise HTTPException(status_code=401, detail="Invalid refresh token") from exc

    sub = claims.get("sub")
    if not isinstance(sub, str):
        raise HTTPException(status_code=401, detail="Invalid token subject")
    try:
        user_id = uuid.UUID(sub)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid token subject") from exc

    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Account inactive")

    settings = get_settings()
    return TokenResponse(
        access_token=issue_access_token(user),
        refresh_token=issue_refresh_token(user),
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(_user: CurrentUser) -> None:
    """JWT 無狀態；client 端自行清除即可。

    若日後需要伺服器端撤銷，改用 token blacklist + Redis（A07）。
    """
    return None


@router.get("/me", response_model=UserMe)
async def me(user: CurrentUser) -> UserMe:
    return UserMe.model_validate(user)


# ─────────────────── LDAP admin test ───────────────────
from app.api.v1.dependencies import require_admin as _require_admin


@router.get("/ldap/test", dependencies=[Depends(_require_admin)])
async def ldap_test() -> dict[str, object]:
    """從伺服器以設定的 bind DN 連線 LDAP，驗證設定是否正確。"""
    try:
        return await ldap_auth.test_connection()
    except ldap_auth.LDAPNotConfigured as exc:
        raise HTTPException(503, detail=str(exc)) from exc
    except ldap_auth.LDAPAuthError as exc:
        raise HTTPException(502, detail=f"LDAP error: {exc}") from exc
