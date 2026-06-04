"""全域 IP 異動記錄查詢（feature B）：搜尋 / 篩選 / 分頁。

權限：依 subnet 可見性過濾（與 addresses 跨 subnet 列表一致）。admin 看全部。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser
from app.core.db import get_session
from app.models.ip_change_log import CHANGE_SOURCES, EVENT_TYPES, IPChangeLog
from app.models.user import User
from app.schemas.base import Paginated
from app.schemas.ip_change_log import IPChangeLogRead
from app.services.permission import filter_visible

router = APIRouter(prefix="/ip-changes", tags=["ip-changes"])


@router.get("", response_model=Paginated[IPChangeLogRead])
async def list_ip_changes(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    q: str | None = Query(None, max_length=128, description="模糊搜尋 IP / 欄位 / 新舊值"),
    ip_id: uuid.UUID | None = Query(None),
    subnet_id: uuid.UUID | None = Query(None),
    event_type: str | None = Query(None),
    source: str | None = Query(None),
    since: datetime | None = Query(None, description="起始時間（含）"),
    until: datetime | None = Query(None, description="結束時間（含）"),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=500),
) -> Paginated[IPChangeLogRead]:
    stmt = select(IPChangeLog)
    count_stmt = select(func.count()).select_from(IPChangeLog)

    def _apply(s: Any) -> Any:
        if ip_id is not None:
            s = s.where(IPChangeLog.ip_id == ip_id)
        if subnet_id is not None:
            s = s.where(IPChangeLog.subnet_id == subnet_id)
        if event_type in EVENT_TYPES:
            s = s.where(IPChangeLog.event_type == event_type)
        if source in CHANGE_SOURCES:
            s = s.where(IPChangeLog.source == source)
        if since is not None:
            s = s.where(IPChangeLog.created_at >= since)
        if until is not None:
            s = s.where(IPChangeLog.created_at <= until)
        if q:
            escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            s = s.where(or_(
                IPChangeLog.ip_text.ilike(pattern, escape="\\"),
                cast(IPChangeLog.field, String).ilike(pattern, escape="\\"),
                IPChangeLog.old_value.ilike(pattern, escape="\\"),
                IPChangeLog.new_value.ilike(pattern, escape="\\"),
                IPChangeLog.note.ilike(pattern, escape="\\"),
            ))
        return s

    stmt = _apply(stmt).order_by(IPChangeLog.created_at.desc())
    count_stmt = _apply(count_stmt)

    total = (await session.execute(count_stmt)).scalar_one()

    # 非 admin：多撈一些再依 subnet 可見性過濾（subnet_id 為 NULL 的——已刪除子網——只給 admin）
    fetch_limit = page_size if user.is_admin else page_size * 4
    stmt = stmt.offset((page - 1) * page_size).limit(fetch_limit)
    rows = list((await session.execute(stmt)).scalars().all())

    if not user.is_admin:
        candidate = list({r.subnet_id for r in rows if r.subnet_id is not None})
        visible = set(await filter_visible(
            session, user=user, object_type="subnet",
            object_ids=candidate, required="read",
        ))
        rows = [r for r in rows if r.subnet_id in visible][:page_size]

    # 補 actor 帳號名
    actor_ids = list({r.actor_user_id for r in rows if r.actor_user_id is not None})
    name_map: dict[uuid.UUID, str] = {}
    if actor_ids:
        for uid, uname in (await session.execute(
            select(User.id, User.username).where(User.id.in_(actor_ids))
        )).all():
            name_map[uid] = uname

    items = []
    for r in rows:
        m = IPChangeLogRead.model_validate(r)
        m.actor_username = name_map.get(r.actor_user_id) if r.actor_user_id else None
        items.append(m)

    return Paginated[IPChangeLogRead](
        items=items, total=total, page=page, page_size=page_size,
    )
