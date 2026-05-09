"""phpIPAM `/user/` — 取得 / 撤銷 / 延長 token。

phpIPAM 流程：
  POST /api/<app>/user/    + Basic Auth   →  發 token
  DELETE /api/<app>/user/  + token        →  撤銷
  PATCH /api/<app>/user/   + token        →  延長

我們用 jt-ipam 內部的 API Token 機制：登入時用 username/password 換出
一個有效期 30 天的 API Token，回傳格式包成 phpIPAM 風格。
"""

from __future__ import annotations

import base64
import time
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.phpipam.helpers import phpipam_current_user, phpipam_response
from app.core.audit import append_audit
from app.core.db import get_session
from app.core.security import generate_api_token
from app.models.user import APIToken
from app.services.auth import (
    AccountInactive,
    AccountLocked,
    InvalidCredentials,
    authenticate,
)

router = APIRouter()


def _parse_basic_auth(authorization: str | None) -> tuple[str, str] | None:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "basic":
        return None
    try:
        decoded = base64.b64decode(parts[1], validate=True).decode("utf-8")
    except Exception:  # noqa: BLE001
        return None
    if ":" not in decoded:
        return None
    user, _, pw = decoded.partition(":")
    return user, pw


@router.post("/{app_id}/user/")
async def login_via_basic(
    app_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """phpIPAM 登入：HTTP Basic 帶 username:password。"""
    started = time.perf_counter()
    creds = _parse_basic_auth(request.headers.get("authorization"))
    if creds is None:
        raise HTTPException(401, detail="Basic auth required")
    username, password = creds

    try:
        user = await authenticate(
            session,
            username=username,
            password=password,
            actor_ip=request.client.host if request.client else None,
            actor_user_agent=request.headers.get("user-agent"),
            request_id=getattr(request.state, "request_id", None),
        )
    except (InvalidCredentials, AccountLocked, AccountInactive):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    # phpIPAM 風格 token = jt-ipam API token；30 天
    raw, prefix, digest = generate_api_token(env_label=app_id)
    expires_at = datetime.now(UTC) + timedelta(days=30)
    api_token = APIToken(
        user_id=user.id,
        name=f"phpipam-compat:{app_id}",
        token_hash=digest,
        token_prefix=prefix,
        scopes=["phpipam:*"],
        expires_at=expires_at,
    )
    session.add(api_token)
    await session.flush()

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="api_token",
        object_id=str(api_token.id),
        action="phpipam_login",
        diff={"app_id": app_id, "prefix": prefix},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()

    return phpipam_response(
        success=True,
        data={
            "id": str(user.id),
            "username": user.username,
            "expires": expires_at.strftime("%Y-%m-%d %H:%M:%S"),
            "token": raw,
        },
        started=started,
    )


@router.delete("/{app_id}/user/")
async def logout(
    app_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    user=Depends(phpipam_current_user),
) -> dict[str, Any]:
    started = time.perf_counter()
    raw = request.headers.get("token") or request.headers.get("phpipam-token")
    if raw is None:
        raise HTTPException(401, detail="Missing token")

    from app.core.security import hash_api_token
    from sqlalchemy import select
    digest = hash_api_token(raw)
    token = (
        await session.execute(select(APIToken).where(APIToken.token_hash == digest))
    ).scalar_one_or_none()
    if token is not None and token.revoked_at is None:
        token.revoked_at = datetime.now(UTC)
        await session.commit()

    return phpipam_response(success=True, message="Token revoked", started=started)


@router.patch("/{app_id}/user/")
async def extend(
    app_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    request: Request,
    user=Depends(phpipam_current_user),
) -> dict[str, Any]:
    started = time.perf_counter()
    raw = request.headers.get("token") or request.headers.get("phpipam-token")
    from app.core.security import hash_api_token
    from sqlalchemy import select
    if raw is None:
        raise HTTPException(401, detail="Missing token")
    digest = hash_api_token(raw)
    token = (
        await session.execute(select(APIToken).where(APIToken.token_hash == digest))
    ).scalar_one_or_none()
    if token is None:
        raise HTTPException(401, detail="Invalid token")

    token.expires_at = datetime.now(UTC) + timedelta(days=30)
    await session.commit()

    return phpipam_response(
        success=True,
        data={"expires": token.expires_at.strftime("%Y-%m-%d %H:%M:%S")},
        started=started,
    )


@router.get("/{app_id}/user/")
async def whoami(
    app_id: str,
    user=Depends(phpipam_current_user),
) -> dict[str, Any]:
    started = time.perf_counter()
    return phpipam_response(
        success=True,
        data={
            "id": str(user.id),
            "username": user.username,
            "email": user.email,
            "role": "admin" if user.is_admin else "user",
        },
        started=started,
    )
