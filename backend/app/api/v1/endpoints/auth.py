"""認證端點：login / refresh / logout / me。"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser
from app.core.config import get_settings
from app.core.db import get_session
from app.core.rate_limit import limit_per_ip
from app.models.user import User
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    RefreshRequest,
    TokenResponse,
)
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


@router.get("/realms")
async def list_realms(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """登入頁可選的領域（本機一律有；LDAP/AD 啟用時才列出）+ 已啟用的 SSO 供應商。

    `sso.oidc` / `sso.saml` 讓登入頁只在該供應商真的設定好時才顯示對應 SSO 按鈕，
    避免使用者點了未啟用的按鈕跳出 `{"detail":"... is disabled"}` 的原始錯誤。
    """
    from app.services.system_config import (
        get_ldap_config,
        get_oidc_config,
        get_saml_config,
    )
    realms: list[dict[str, str]] = [{"value": "local", "label": "本機"}]
    try:
        cfg = await get_ldap_config(session)
        if cfg.enabled:
            realms.append({"value": "ldap", "label": "LDAP / AD"})
    except Exception:
        pass

    sso = {"oidc": False, "saml": False}
    try:
        sso["oidc"] = bool((await get_oidc_config(session)).enabled)
    except Exception:
        pass
    try:
        sso["saml"] = bool((await get_saml_config(session)).enabled)
    except Exception:
        pass
    return {"realms": realms, "sso": sso}


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
            realm=payload.realm,
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
async def me(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserMe:
    out = UserMe.model_validate(user)
    # RDP 是否可用（後端是否裝了 aardwolf 選用相依）
    from app.api.v1.endpoints.rdp_console import RDP_AVAILABLE
    from app.api.v1.endpoints.vnc_console import VNC_AVAILABLE
    out.rdp_supported = RDP_AVAILABLE
    out.vnc_supported = VNC_AVAILABLE
    from app.services.bmc import bmc_available
    out.bmc_supported = bmc_available()
    # 全域 LLM/AI 是否啟用 → 前端據此決定要不要顯示 AI 對話小工具（未設定就別讓人輸入/送出）
    from app.services.system_config import get_llm_config
    out.ai_enabled = (await get_llm_config(session)).enabled
    # has_visibility：任一類型有可見範圍即 True（零權限→False）
    # has_global_read：管理員或任一類型有「萬用」授權（visible_ids 回 None）→ True
    if user.is_admin:
        out.has_visibility = True
        out.has_global_read = True
        out.can_edit = True
    else:
        from app.services.permission import has_any_write, visible_ids
        has_vis = False
        has_global = False
        for ot in ("subnet", "device", "customer", "section", "rack", "location"):
            v = await visible_ids(session, user=user, object_type=ot)
            if v is None:
                has_global = True
                has_vis = True
            elif v:
                has_vis = True
        out.has_visibility = has_vis
        out.has_global_read = has_global
        out.can_edit = await has_any_write(session, user=user)
    return out


# ─────────────────── LDAP admin test ───────────────────
from app.api.v1.dependencies import require_admin as _require_admin


@router.get("/ldap/test", dependencies=[Depends(_require_admin)])
async def ldap_test(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    """從伺服器以目前設定（DB 覆蓋 env）連線 LDAP，驗證設定是否正確。"""
    from app.services.system_config import get_ldap_config
    cfg = await get_ldap_config(session)
    try:
        return await ldap_auth.test_connection(cfg)
    except ldap_auth.LDAPNotConfigured as exc:
        raise HTTPException(503, detail=str(exc)) from exc
    except ldap_auth.LDAPAuthError as exc:
        raise HTTPException(502, detail=f"LDAP error: {exc}") from exc


@router.post("/change-password", status_code=204)
async def change_password(
    payload: ChangePasswordRequest,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """本機帳號自助變更密碼：需驗證目前密碼；外部認證帳號不適用。"""
    from app.core.security import hash_password, verify_password

    # 僅本機帳號（password_hash 存在且 auth_provider=local）可在此改密碼
    if user.auth_provider != "local" or not user.password_hash:
        raise HTTPException(status_code=400, detail="external_auth")
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="current_password_incorrect")
    if payload.new_password == payload.current_password:
        raise HTTPException(status_code=400, detail="same_password")

    user.password_hash = hash_password(payload.new_password)
    from app.core.audit import append_audit
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="user",
        object_id=str(user.id),
        action="password_changed",
        diff=None,
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
