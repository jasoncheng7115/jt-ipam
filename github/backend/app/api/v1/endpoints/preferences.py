"""User preferences endpoints（屬於本人）。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser
from app.core.db import get_session
from app.models.user import UserPreference
from app.schemas.preferences import UserPreferenceRead, UserPreferenceUpdate

router = APIRouter(prefix="/me", tags=["me"])


def _defaults() -> UserPreferenceRead:
    return UserPreferenceRead(
        locale="zh-TW",
        theme="auto",
        timezone="Asia/Taipei",
        calendar="gregorian",
        page_size=50,
        table_columns=None,
        pinned_subnet_ids=None,
    )


@router.get("/preferences", response_model=UserPreferenceRead)
async def get_preferences(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserPreferenceRead:
    pref = await session.get(UserPreference, user.id)
    if pref is None:
        return _defaults()
    return UserPreferenceRead.model_validate(pref)


@router.patch("/preferences", response_model=UserPreferenceRead)
async def update_preferences(
    payload: UserPreferenceUpdate,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserPreferenceRead:
    pref = await session.get(UserPreference, user.id)
    # mode="json" → UUID / datetime 等型別 dump 成 JSON-safe 字串；
    # pinned_subnet_ids 是 JSONB column，需要 list[str] 才存得進去
    changes = payload.model_dump(exclude_unset=True, mode="json")

    if pref is None:
        defaults = _defaults().model_dump()
        defaults.update(changes)
        pref = UserPreference(user_id=user.id, **defaults)
        session.add(pref)
    else:
        for k, v in changes.items():
            setattr(pref, k, v)

    await session.commit()
    await session.refresh(pref)
    return UserPreferenceRead.model_validate(pref)
