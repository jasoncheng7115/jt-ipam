"""FastAPI dependency：取得當前使用者與權限檢查。

OWASP A01 / A07：
- 每個受保護 endpoint 都要 Depends(get_current_user) 或 require_*
- API Token 經過 hash 比對，常數時間
- JWT 拒絕 type 不符或過期
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import hash_api_token
from app.models.user import APIToken, User
from app.services.auth import TokenInvalid, decode_token
from app.services.permission import (
    ObjectType,
    PermLevel,
    get_object_permission,
    has_permission,
)

_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    """JWT access token 或 API Token 認證。

    優先順序：
      1. Authorization: Bearer <jwt>          — 一般 web user
      2. Authorization: Bearer jt_xxx         — API Token（首碼 jt_）
    兩者都沒提供 → 401。
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    raw = credentials.credentials

    # ── API Token ──
    if raw.startswith("jt_"):
        digest = hash_api_token(raw)
        stmt = select(APIToken).where(APIToken.token_hash == digest)
        token = (await session.execute(stmt)).scalar_one_or_none()
        if token is None or token.revoked_at is not None:
            raise HTTPException(status_code=401, detail="Invalid token")
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        if token.expires_at <= now:
            raise HTTPException(status_code=401, detail="Token expired")
        # 更新 last_used（不阻塞請求）
        token.last_used_at = now
        if request.client:
            token.last_used_ip = request.client.host
        await session.commit()

        user = await session.get(User, token.user_id)
        if user is None or not user.is_active:
            raise HTTPException(status_code=401, detail="Account inactive")
        # 把 token 暫存給 endpoint 用（scope 檢查）
        request.state.api_token = token
        return user

    # ── JWT ──
    try:
        payload = decode_token(raw, expected_type="access")
    except TokenInvalid as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired token") from exc

    sub = payload.get("sub")
    if not isinstance(sub, str):
        raise HTTPException(status_code=401, detail="Invalid token subject")
    try:
        user_id = uuid.UUID(sub)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid token subject") from exc

    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Account inactive")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_admin(user: CurrentUser) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin required")
    return user


async def forbid_zero_visibility(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """全域基礎設施類端點（NAT / 防火牆 / 進階 / 實體）讀取門檻：
    非管理員且對所有物件類型皆無可見範圍（零權限帳號）→ 403。
    有任一可見範圍（含唯讀檢視者的 wildcard）或管理員則放行。"""
    if user.is_admin:
        return
    from app.services.permission import visible_ids
    for ot in ("subnet", "device", "customer", "section", "rack", "location"):
        v = await visible_ids(session, user=user, object_type=ot)
        if v is None or v:
            return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No visible resources")


async def require_global_read(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """全域基礎設施（VLAN / VRF / NAT / 防火牆 / DNS / 虛擬化 / 站對站 VPN / 佈線…）
    這些不屬於 7 種可逐物件授權的類型，無法依物件範圍過濾。
    僅「管理員」或「具萬用(全部)讀取權限者（如唯讀檢視者）」可讀；
    只被指派特定物件（部門範圍）的帳號 → 403，不得窺見全域資料。"""
    if user.is_admin:
        return
    from app.services.permission import visible_ids
    for ot in ("subnet", "device", "customer", "section", "rack", "location"):
        v = await visible_ids(session, user=user, object_type=ot)
        if v is None:  # None = 萬用授權（全部可見）
            return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Global resource requires full visibility",
    )


def require_object_perm(
    object_type: ObjectType,
    required: PermLevel,
    *,
    path_param: str,
) -> Any:
    """產生 dependency：檢查當前 user 對 path_param 指定的 object 是否達到 required。

    用法：
        @router.get("/sections/{section_id}",
                    dependencies=[Depends(require_object_perm("section", "read", path_param="section_id"))])
        async def get_section(...): ...
    """

    async def _dep(
        request: Request,
        user: CurrentUser,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> None:
        raw = request.path_params.get(path_param)
        if raw is None:
            raise HTTPException(
                status_code=500,
                detail=f"require_object_perm: path_param '{path_param}' not in route",
            )
        try:
            oid = uuid.UUID(str(raw))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid object id") from exc

        level = await get_object_permission(
            session, user=user, object_type=object_type, object_id=oid
        )
        if not has_permission(level, required):
            # A01：不洩漏「物件存在但無權限」與「不存在」的差異
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    return _dep
