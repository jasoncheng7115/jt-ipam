"""API Token 管理：每個 user 管理自己的 token；admin 可看 / 撤銷他人的。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser
from app.core.audit import append_audit
from app.core.config import get_settings
from app.core.db import get_session
from app.core.security import generate_api_token
from app.models.user import APIToken
from app.schemas.api_token import APITokenCreate, APITokenCreateResponse, APITokenRead
from app.schemas.base import Paginated

router = APIRouter(prefix="/api-tokens", tags=["api-tokens"])


@router.get("", response_model=Paginated[APITokenRead])
async def list_my_tokens(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=200),
    user_id: uuid.UUID | None = Query(None, description="admin only: list other user's tokens"),
) -> Paginated[APITokenRead]:
    target_uid = user.id
    if user_id is not None and user_id != user.id:
        if not user.is_admin:
            raise HTTPException(403, detail="Admin required to list others' tokens")
        target_uid = user_id

    stmt = (
        select(APIToken)
        .where(APIToken.user_id == target_uid)
        .order_by(APIToken.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    total = int(
        await session.scalar(
            select(func.count()).select_from(APIToken).where(APIToken.user_id == target_uid)
        )
        or 0
    )
    return Paginated[APITokenRead](
        items=[APITokenRead.model_validate(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.post("", response_model=APITokenCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_token(
    payload: APITokenCreate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> APITokenCreateResponse:
    """建立屬於當前 user 的 API Token。

    安全（A07 / A08）：
    - 明文 token 僅此 response 出現一次
    - DB 只存 sha256(raw)
    - 寫入 audit log（不含明文）
    """
    settings = get_settings()
    raw, prefix, digest = generate_api_token(
        env_label="prod" if settings.is_production else settings.app_env,
    )
    expires_at = datetime.now(UTC) + timedelta(days=payload.expires_in_days)

    token = APIToken(
        user_id=user.id,
        name=payload.name,
        token_hash=digest,
        token_prefix=prefix,
        scopes=payload.scopes,
        object_filters=payload.object_filters,
        expires_at=expires_at,
    )
    session.add(token)
    await session.flush()

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="api_token",
        object_id=str(token.id),
        action="create",
        diff={
            "name": payload.name,
            "expires_at": expires_at.isoformat(),
            "scopes": payload.scopes,
            "prefix": prefix,
        },
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(token)

    return APITokenCreateResponse(
        id=token.id,
        name=token.name,
        token=raw,
        token_prefix=prefix,
        expires_at=expires_at,
        scopes=list(token.scopes or []),
    )


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_token(
    token_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    token = await session.get(APIToken, token_id)
    if token is None:
        raise HTTPException(404, detail="Token not found")
    # A01：本人或 admin 可撤銷
    if token.user_id != user.id and not user.is_admin:
        raise HTTPException(404, detail="Token not found")  # 不洩漏存在性

    if token.revoked_at is None:
        token.revoked_at = datetime.now(UTC)

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="api_token",
        object_id=str(token.id),
        action="revoke",
        diff={"name": token.name, "prefix": token.token_prefix},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
