"""統一搜尋端點。"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser
from app.core.db import get_session
from app.services.search import search as run_search

router = APIRouter(prefix="/search", tags=["search"])


@router.get("")
async def search_endpoint(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    q: Annotated[str, Query(min_length=1, max_length=128)],
    limit_per_type: Annotated[int, Query(ge=1, le=50)] = 8,
) -> dict[str, Any]:
    return await run_search(session, user=user, q=q, limit_per_type=limit_per_type)
