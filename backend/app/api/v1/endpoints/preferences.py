"""User preferences endpoints（屬於本人）。"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser
from app.core.db import get_session
from app.models.user import UserPreference
from app.schemas.preferences import UserPreferenceRead, UserPreferenceUpdate
from app.services.dashboard_subnet_usage import PIN_NAMESPACE as USAGE_PIN_NAMESPACE
from app.services.dashboard_subnet_usage import sanitize_pins, usable_subnet_ids

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
        pinned=None,
    )


@router.get("/preferences", response_model=UserPreferenceRead)
async def get_preferences(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserPreferenceRead:
    pref = await session.get(UserPreference, user.id)
    if pref is None:
        return _defaults()
    out = UserPreferenceRead.model_validate(pref)
    # 常用子網路只回傳還能用的：已刪除、已歸檔、看不到的不給（不寫回，權限恢復後就又出現）
    if pref.pinned_subnet_ids:
        out.pinned_subnet_ids = [uuid.UUID(x) for x in await usable_subnet_ids(session, user, pref.pinned_subnet_ids)]
    return out


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
    # 儀表板「IP 使用率」挑的子網路：存之前就拿掉看不到／不存在／已歸檔的，最多 4 個
    # （受限帳號不能把範圍外的子網路存進來；讀取端 GET /dashboard/subnet-usage 也會再擋一次）
    if changes.get("pinned_subnet_ids"):
        changes["pinned_subnet_ids"] = await usable_subnet_ids(session, user, changes["pinned_subnet_ids"])
    pinned = changes.get("pinned")
    if isinstance(pinned, dict) and USAGE_PIN_NAMESPACE in pinned:
        pinned[USAGE_PIN_NAMESPACE] = await sanitize_pins(session, user, pinned[USAGE_PIN_NAMESPACE] or [])

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
    return await get_preferences(user, session)
