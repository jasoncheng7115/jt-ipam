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
from app.schemas.user import UserMe
from app.services.auth import (
    AccountInactive,
    AccountLocked,
    AuthError,
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

    settings = get_settings()
    return TokenResponse(
        access_token=issue_access_token(user),
        refresh_token=issue_refresh_token(user),
        expires_in=settings.access_token_expire_minutes * 60,
    )


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
