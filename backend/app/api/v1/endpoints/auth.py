"""認證端點：login / MFA / refresh / logout / 工作階段 / me。"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser
from app.core.config import get_settings
from app.core.db import get_session
from app.core.rate_limit import limit_per_ip
from app.core.security import decode_access_token
from app.core.ui_error import detail_of, ui_detail
from app.models.user import User
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    ReauthRequest,
    SessionRead,
    TokenResponse,
    TotpDisableRequest,
)
from app.schemas.totp import (
    ConfirmRequest,
    EnrollResponse,
    MfaSetupConfirmRequest,
    MfaTokenRequest,
    RecoveryCodesResponse,
    VerifyRequest,
)
from app.schemas.user import UserMe
from app.services import ldap_auth, sessions
from app.services import mfa as mfa_service
from app.services import totp as totp_service
from app.services.auth import (
    AccountInactive,
    AccountLocked,
    InvalidCredentials,
    TokenInvalid,
    authenticate,
    decode_token,
    is_locked,
    issue_access_token,
    register_failure,
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
    # `label_key` 才是畫面上顯示的那一份：登入頁有語言切換，而且使用者還沒登入，
    # 後端無從得知要用哪個語言。`label` 留著給沒有 i18n 的用戶端（以及舊快取）。
    realms: list[dict[str, str]] = [
        {"value": "local", "label": "本機", "label_key": "login.realm_local"}]
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


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def _audit_auth(session: AsyncSession, request: Request, user_id: Any, action: str,
                      diff: dict[str, Any] | None = None, object_type: str = "auth") -> None:
    from app.core.audit import append_audit
    await append_audit(
        session, actor_user_id=str(user_id) if user_id else None, actor_ip=_client_ip(request),
        actor_user_agent=request.headers.get("user-agent"), object_type=object_type,
        object_id=str(user_id) if user_id else None, action=action, diff=diff,
        request_id=getattr(request.state, "request_id", None))


async def _start_session(session: AsyncSession, user: User, request: Request, response: Response, *,
                         method: str, mfa: bool,
                         recovery_codes: list[str] | None = None) -> TokenResponse:
    """建立伺服器端工作階段：更新權杖只放 HttpOnly Cookie，回應本文只有存取權杖。"""
    issued = await sessions.create_session(session, user, request, method=method, mfa=mfa)
    await session.commit()
    sessions.set_refresh_cookie(response, issued)
    return TokenResponse(
        access_token=issue_access_token(user, sid=issued.session.id),
        expires_in=get_settings().access_token_expire_minutes * 60,
        recovery_codes=recovery_codes,
    )


async def begin_second_step(session: AsyncSession, user: User, *, method: str) -> TokenResponse | None:
    """密碼（或 SSO）通過之後：需要 MFA → 回挑戰或「先設定」；不需要 → None（直接發工作階段）。"""
    policy = await mfa_service.get_policy(session)
    if not mfa_service.applies_to_method(policy, method):
        return None
    if totp_service.is_enabled(user):
        return TokenResponse(mfa_required=True,
                             mfa_token=totp_service.issue_mfa_challenge(user, method=method))
    if mfa_service.required_for(policy, user):
        return TokenResponse(mfa_setup_required=True,
                             mfa_token=totp_service.issue_mfa_challenge(user, purpose="mfa_setup", method=method))
    return None


def _login_method(user: User, realm: str) -> str:
    return user.auth_provider if user.auth_provider in ("local", "ldap", "radius") else (realm or "local")


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
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
            actor_ip=_client_ip(request),
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

    method = _login_method(user, payload.realm)
    # A07：已啟用 TOTP → 發挑戰；政策要求但還沒設定 → 先設定（不會被鎖在外面）
    second = await begin_second_step(session, user, method=method)
    if second is not None:
        return second
    return await _start_session(session, user, request, response, method=method, mfa=False)


async def _challenge_user(session: AsyncSession, token: str, purpose: str) -> tuple[User, str]:
    try:
        claims = decode_token(token, expected_type=purpose)
    except TokenInvalid as exc:
        raise HTTPException(status_code=401, detail="Invalid MFA challenge") from exc
    try:
        user_id = uuid.UUID(str(claims.get("sub")))
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid token subject") from exc
    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Account inactive")
    return user, str(claims.get("method") or "local")


@router.post("/mfa/verify", response_model=TokenResponse)
async def mfa_verify(
    payload: VerifyRequest,
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TokenResponse:
    """登入第二步：mfa_token + 6 位數驗證碼（或一組復原碼）→ 工作階段。

    錯誤會累計到帳號的失敗次數，達門檻就鎖定（以前不算，知道密碼的人可以一直試驗證碼）。"""
    await limit_per_ip(request, name="auth")
    user, method = await _challenge_user(session, payload.mfa_token, "mfa_challenge")
    if is_locked(user):
        raise HTTPException(status_code=401, detail="Account temporarily locked", headers={"Retry-After": "900"})

    code = payload.code.strip()
    used = "totp"
    ok = await totp_service.verify_code(user, code) if code.isdigit() else False
    if not ok and mfa_service.looks_like_recovery_code(code):
        ok = await mfa_service.use_recovery_code(session, user, code)
        used = "recovery_code"
    if not ok:
        await register_failure(session, user, actor_ip=_client_ip(request))
        await _audit_auth(session, request, user.id, "mfa_failed", {"reason": "invalid_code"})
        await session.commit()
        raise HTTPException(status_code=401, detail="Invalid MFA code")

    user.failed_login_count = 0
    diff: dict[str, Any] = {"via": used}
    if used == "recovery_code":
        diff["recovery_remaining"] = await mfa_service.remaining_recovery_codes(session, user.id)
    await _audit_auth(session, request, user.id, "mfa_verified", diff)
    return await _start_session(session, user, request, response, method=method, mfa=True)


@router.post("/mfa/setup/begin", response_model=EnrollResponse)
async def mfa_setup_begin(
    payload: MfaTokenRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> EnrollResponse:
    """政策要求 MFA、還沒設定的帳號：登入時產生 TOTP 金鑰（還沒寫入，確認驗證碼才生效）。"""
    await limit_per_ip(request, name="auth")
    user, _method = await _challenge_user(session, payload.mfa_token, "mfa_setup")
    secret = totp_service.begin_enrollment()
    return EnrollResponse(secret=secret, otpauth_uri=totp_service.provisioning_uri(secret, account=user.username))


@router.post("/mfa/setup/confirm", response_model=TokenResponse)
async def mfa_setup_confirm(
    payload: MfaSetupConfirmRequest,
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TokenResponse:
    """設定完成：寫入 TOTP、產生復原碼（只回這一次）、建立工作階段。"""
    await limit_per_ip(request, name="auth")
    user, method = await _challenge_user(session, payload.mfa_token, "mfa_setup")
    if totp_service.is_enabled(user):
        raise HTTPException(status_code=409, detail="TOTP already enabled")
    if not await totp_service.confirm_enrollment(session, user=user, secret=payload.secret, code=payload.code):
        raise HTTPException(status_code=400, detail="Invalid TOTP code")
    codes = await mfa_service.regenerate_recovery_codes(session, user)
    await _audit_auth(session, request, user.id, "totp_enabled", {"via": "login_setup"}, object_type="user")
    return await _start_session(session, user, request, response, method=method, mfa=True,
                                recovery_codes=codes)


@router.post("/totp/enroll", response_model=EnrollResponse)
async def totp_enroll(user: CurrentUser) -> EnrollResponse:
    """產生新 secret 與 otpauth URI；client 顯示 QR；下一步 /totp/confirm。

    注意：尚未寫入 DB；client 需 confirm 才生效。
    """
    secret = totp_service.begin_enrollment()
    uri = totp_service.provisioning_uri(secret, account=user.username)
    return EnrollResponse(secret=secret, otpauth_uri=uri)


@router.post("/totp/confirm", response_model=RecoveryCodesResponse)
async def totp_confirm(
    payload: ConfirmRequest,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RecoveryCodesResponse:
    """用 enroll 拿到的 secret + 第一筆 6-digit code 確認；正確才寫入 DB。回傳 10 組復原碼（只此一次）。"""
    if totp_service.is_enabled(user):
        raise HTTPException(status_code=409, detail="TOTP already enabled")

    ok = await totp_service.confirm_enrollment(
        session, user=user, secret=payload.secret, code=payload.code
    )
    if not ok:
        raise HTTPException(status_code=400, detail="Invalid TOTP code")

    codes = await mfa_service.regenerate_recovery_codes(session, user)
    await _audit_auth(session, request, user.id, "totp_enabled", None, object_type="user")
    await session.commit()
    return RecoveryCodesResponse(recovery_codes=codes)


async def _reauth(session: AsyncSession, user: User, password: str | None, code: str | None) -> None:
    """升級驗證：本機帳號給目前密碼；外部帳號（本地沒有密碼）給目前的 TOTP 驗證碼。"""
    from app.core.security import verify_password

    if password and user.auth_provider == "local" and user.password_hash:
        if not verify_password(password, user.password_hash):
            raise HTTPException(status_code=400, detail="current_password_incorrect")
        return
    if code:
        if not await totp_service.verify_code(user, code):
            raise HTTPException(status_code=400, detail="Invalid TOTP code")
        return
    raise HTTPException(status_code=400, detail="reauth_required")


@router.post("/totp/disable", status_code=204)
async def totp_disable(
    payload: TotpDisableRequest,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """停用 TOTP —— 需要升級驗證（A07）。

    光憑一個有效 session 就能關掉 2FA 的話，任何拿到 access token 的人
    （XSS / 竊取的權杖 / 未鎖的螢幕 / 一把不受限的 API 權杖）都能把帳號降回
    只有密碼。同一支檔案的變更密碼本來就要求現行密碼，這裡補上一致的要求。
    管理員的 MFA 政策要求這個帳號一定要開時不能停用。
    """
    if not totp_service.is_enabled(user):
        raise HTTPException(status_code=409, detail="TOTP not enabled")
    if mfa_service.required_for(await mfa_service.get_policy(session), user):
        raise HTTPException(status_code=409, detail=ui_detail(
            "mfa_required_by_policy", "管理員要求這個帳號一定要使用雙因素驗證，不能停用"))
    await _reauth(session, user, payload.password, payload.code)
    await totp_service.disable(session, user=user)
    await _audit_auth(session, request, user.id, "totp_disabled", None, object_type="user")
    await session.commit()


@router.post("/totp/recovery-codes", response_model=RecoveryCodesResponse)
async def regenerate_recovery_codes(
    payload: ReauthRequest,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RecoveryCodesResponse:
    """重新產生 10 組復原碼（舊的全部作廢）；需要升級驗證。"""
    if not totp_service.is_enabled(user):
        raise HTTPException(status_code=409, detail="TOTP not enabled")
    await _reauth(session, user, payload.password, payload.code)
    codes = await mfa_service.regenerate_recovery_codes(session, user)
    await _audit_auth(session, request, user.id, "recovery_codes_regenerated", None, object_type="user")
    await session.commit()
    return RecoveryCodesResponse(recovery_codes=codes)


def _need_csrf_header(request: Request) -> None:
    """用 Cookie 的端點（換發、登出）要多一個自訂標頭：跨站表單送不出自訂標頭（SameSite 之外的第二道）。"""
    if request.headers.get(sessions.CSRF_HEADER) != sessions.CSRF_VALUE:
        raise HTTPException(status_code=403, detail="Missing X-Requested-With header")


def _unauthorized_clearing_cookie(detail: str) -> JSONResponse:
    resp = JSONResponse({"detail": detail}, status_code=401)
    sessions.clear_refresh_cookie(resp)
    return resp


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Any:
    """用 HttpOnly Cookie 裡的更新權杖換一組新的（舊的作廢）。

    超過 60 秒寬限還拿舊的更新權杖來＝被偷用了：整個工作階段撤銷、留稽核、通知本人。"""
    await limit_per_ip(request, name="auth")
    _need_csrf_header(request)
    try:
        user, issued = await sessions.rotate(session, request.cookies.get(sessions.REFRESH_COOKIE), request)
    except sessions.SessionInvalid as exc:
        if exc.reason == "reuse":
            await _audit_auth(session, request, exc.user_id, "refresh_token_reuse",
                              {"session_revoked": True})
            from app.services.security_alert import notify_session_reuse
            await notify_session_reuse(session, user_id=exc.user_id, actor_ip=_client_ip(request))
        await session.commit()
        return _unauthorized_clearing_cookie("Invalid refresh token")
    await session.commit()
    sessions.set_refresh_cookie(response, issued)
    return TokenResponse(access_token=issue_access_token(user, sid=issued.session.id),
                         expires_in=get_settings().access_token_expire_minutes * 60)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """結束這個工作階段：伺服器端撤銷（存取權杖與更新權杖立即失效），並清掉 Cookie。

    不要求有效的存取權杖：權杖過期了也要能登出。工作階段由存取權杖的 sid 或 Cookie 決定。"""
    _need_csrf_header(request)
    sid: Any = None
    user_id: Any = None
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer ") and not auth[7:].startswith("jt_"):
        try:
            claims = decode_access_token(auth[7:])
            sid, user_id = claims.get("sid"), claims.get("sub")
        except Exception:
            sid = None
    if sid is None:
        raw = request.cookies.get(sessions.REFRESH_COOKIE) or ""
        sid = raw.split(".", 1)[0] if "." in raw else None
        # 只憑 Cookie 時要確認雜湊對得上，不能靠猜 sid 登出別人
        if sid is not None and not await sessions.cookie_matches(session, raw):
            sid = None
    if sid is not None and await sessions.revoke(session, sid, reason="logout"):
        if user_id is None:
            user_id = await sessions.owner_of(session, sid)
        await _audit_auth(session, request, user_id, "logout", {"session_id": str(sid)})
    await session.commit()
    resp = Response(status_code=status.HTTP_204_NO_CONTENT)
    sessions.clear_refresh_cookie(resp)
    return resp


@router.get("/sessions", response_model=list[SessionRead])
async def list_my_sessions(
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[SessionRead]:
    """自己目前登入中的工作階段（裝置、位址、最後使用時間）。"""
    current = str(getattr(request.state, "session_id", "") or "")
    return [SessionRead(
        id=str(r.id), created_at=r.created_at.isoformat(), last_used_at=r.last_used_at.isoformat(),
        expires_at=r.expires_at.isoformat(), ip=r.ip, user_agent=r.user_agent, method=r.method,
        mfa=r.mfa, current=str(r.id) == current) for r in await sessions.list_active(session, user.id)]


@router.delete("/sessions/{session_id}", status_code=204)
async def revoke_my_session(
    session_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    if not await sessions.revoke(session, session_id, reason="user_revoked", user_id=user.id):
        raise HTTPException(status_code=404, detail="Not found")
    await _audit_auth(session, request, user.id, "session_revoked", {"session_id": str(session_id)})
    await session.commit()


@router.post("/sessions/revoke-others")
async def revoke_my_other_sessions(
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, int]:
    """登出其他所有裝置（保留目前這一個）。"""
    keep = getattr(request.state, "session_id", None)
    n = await sessions.revoke_all(session, user, reason="user_revoked_others", keep_sid=keep)
    await _audit_auth(session, request, user.id, "sessions_revoked", {"count": n, "kept_current": bool(keep)})
    await session.commit()
    return {"revoked": n}


@router.get("/me", response_model=UserMe)
async def me(
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserMe:
    out = UserMe.model_validate(user)
    # TOTP 是否已啟用（totp_secret_enc 有值）→ 前端「安全」頁顯示狀態
    out.totp_enabled = totp_service.is_enabled(user)
    out.mfa_required_by_policy = mfa_service.required_for(await mfa_service.get_policy(session), user)
    if out.totp_enabled:
        out.recovery_codes_remaining = await mfa_service.remaining_recovery_codes(session, user.id)
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
    if user.is_admin:
        # 啟動時算好的（見 main.lifespan）—— /me 是每次載入都會打的，不適合在這裡
        # 讀 migration 目錄
        out.schema_behind = bool(getattr(request.app.state, "schema_behind", False))
    # has_visibility：任一類型有可見範圍即 True（零權限→False）
    # has_global_read：管理員或任一類型有「萬用」授權（visible_ids 回 None）→ True
    if user.is_admin:
        out.has_visibility = True
        out.has_global_read = True
        out.can_edit = True
        out.create_types = ["customer", "section", "subnet", "ip", "device", "rack", "location"]
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
        from app.services.permission import creatable_types
        out.create_types = await creatable_types(session, user=user)
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
        raise HTTPException(503, detail=detail_of(exc, "ldap_not_configured")) from exc
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
    # 改密碼通常是因為懷疑外洩：其他裝置的登入一律撤銷，只留現在這一個
    revoked = await sessions.revoke_all(session, user, reason="password_changed",
                                        keep_sid=getattr(request.state, "session_id", None))
    from app.core.audit import append_audit
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="user",
        object_id=str(user.id),
        action="password_changed",
        diff={"sessions_revoked": revoked},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
