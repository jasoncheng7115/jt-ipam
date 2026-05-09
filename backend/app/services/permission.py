"""RBAC 權限服務（OWASP A01：deny-by-default）。

模型：
- 物件級權限儲存於 `permissions` 表（object_type, object_id, principal, level）
- 層級遞增：none < read < write < admin
- 計算規則：取 user 自身與所屬 group 的所有相關權限，取最高
- `is_admin=True` 一律放行（superuser）
- Subnet 權限可繼承自 Section（取較高者）
- 沒設定 = none = 拒絕

可用於：
    Depends(require_section_perm("read", section_id_path_param="section_id"))
"""

from __future__ import annotations

import uuid
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.permission import Permission
from app.models.subnet import Subnet
from app.models.user import User, UserGroupMember

PermLevel = Literal["none", "read", "write", "admin"]
ObjectType = Literal["section", "subnet"]

_LEVEL_RANK: dict[str, int] = {"none": 0, "read": 1, "write": 2, "admin": 3}


def _max(a: str, b: str) -> str:
    return a if _LEVEL_RANK.get(a, 0) >= _LEVEL_RANK.get(b, 0) else b


def has_permission(actual: str, required: str) -> bool:
    return _LEVEL_RANK.get(actual, 0) >= _LEVEL_RANK.get(required, 0)


async def _user_group_ids(session: AsyncSession, user_id: uuid.UUID) -> list[uuid.UUID]:
    stmt = select(UserGroupMember.group_id).where(UserGroupMember.user_id == user_id)
    return [row[0] for row in (await session.execute(stmt)).all()]


async def get_object_permission(
    session: AsyncSession,
    *,
    user: User,
    object_type: ObjectType,
    object_id: uuid.UUID,
) -> PermLevel:
    """取得 user 對單一 object 的有效層級。"""
    if user.is_admin:
        return "admin"

    group_ids = await _user_group_ids(session, user.id)

    # 一次撈出 user + groups 在此 object 的所有授權
    principals: list[tuple[str, uuid.UUID]] = [("user", user.id)]
    principals.extend(("group", gid) for gid in group_ids)

    if not principals:
        return "none"

    # 在 SQL 中以 (principal_type, principal_id) IN ... 篩選
    # SQLAlchemy 對 tuple_ IN 支援不夠成熟；改用 OR 串接
    from sqlalchemy import and_, or_

    conds = [
        and_(Permission.principal_type == ptype, Permission.principal_id == pid)
        for ptype, pid in principals
    ]

    stmt = select(Permission.level).where(
        Permission.object_type == object_type,
        Permission.object_id == object_id,
        or_(*conds),
    )
    levels = [row[0] for row in (await session.execute(stmt)).all()]

    if not levels:
        # Subnet 沒授權時，繼承 Section（A01：明確繼承，不靜默放行）
        if object_type == "subnet":
            section_id = await session.scalar(
                select(Subnet.section_id).where(Subnet.id == object_id)
            )
            if section_id is not None:
                return await get_object_permission(
                    session,
                    user=user,
                    object_type="section",
                    object_id=section_id,
                )
        return "none"

    best: str = "none"
    for level in levels:
        best = _max(best, level)
    return best  # type: ignore[return-value]


async def filter_visible(
    session: AsyncSession,
    *,
    user: User,
    object_type: ObjectType,
    object_ids: list[uuid.UUID],
    required: PermLevel = "read",
) -> list[uuid.UUID]:
    """篩出 user 對哪些 object 達到 required 層級。

    給 list endpoint 用：先撈所有候選，再用此 helper 過濾。
    """
    if user.is_admin:
        return object_ids
    visible: list[uuid.UUID] = []
    for oid in object_ids:
        level = await get_object_permission(
            session, user=user, object_type=object_type, object_id=oid
        )
        if has_permission(level, required):
            visible.append(oid)
    return visible
