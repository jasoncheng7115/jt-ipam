"""寫入時引用其他物件的權限守門（2026-10-07 全站 RBAC 稽核）。

端點本身只檢查「對這個物件有沒有寫入權」，但請求內容可以指向別的物件：
- 掃描代理、主控台出口、跳板：代理會照這個設定掃描、替人開主控台到別的網段 → 只有管理員能指派
- 子網路的區段／單位、區段的單位、IP 的單位是權限繼承的上層：搬過去可能換來更高的權限，
  或把資料推進／移出別的單位的可見範圍 → 對物件本身要 admin、對目的地要寫入權
- 上層子網路、上層區段、IP 的裝置只影響顯示或關聯 → 至少要看得到（看不到跟不存在一樣回 404）
- 關掉異常偵測、AI 巡檢：集中設定是管理員限定，逐筆關掉不可以繞過去
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ui_error import ui_detail
from app.services.permission import get_object_permission, has_permission

INFRA_FIELDS = ("scan_agent_id", "console_agent_id", "jump_host_id")
MONITORING_FIELDS = ("anomaly_enabled", "ai_audit_enabled")


def _changed(changes: dict[str, Any], current: Any, field: str, default: Any) -> bool:
    if field not in changes:
        return False
    before = getattr(current, field) if current is not None else default
    return changes[field] != before


def require_admin_for_infra(user: Any, changes: dict[str, Any], current: Any = None) -> None:
    """表單每次都會送全部欄位：只有值真的變了才擋（建立時跟預設值比）。"""
    if getattr(user, "is_admin", False):
        return
    if any(_changed(changes, current, f, None) for f in INFRA_FIELDS):
        raise HTTPException(status_code=403, detail=ui_detail(
            "infra_assignment_admin_only", "Only admins can assign scan agents, console egress or jump hosts"))


def require_admin_for_monitoring(user: Any, changes: dict[str, Any], current: Any = None) -> None:
    if getattr(user, "is_admin", False):
        return
    if any(_changed(changes, current, f, True) for f in MONITORING_FIELDS):
        raise HTTPException(status_code=403, detail=ui_detail(
            "subnet_monitoring_admin_only", "Only admins can turn off anomaly detection or AI audit"))


async def require_move(session: AsyncSession, user: Any, *, object_type: str, object_id: uuid.UUID,
                       dest_type: str, dest_id: uuid.UUID | None) -> None:
    """搬到別的權限上層：對物件本身要 admin，對目的地（有指定時）要寫入權。"""
    if getattr(user, "is_admin", False):
        return
    own = await get_object_permission(session, user=user, object_type=object_type, object_id=object_id)  # type: ignore[arg-type]
    ok = own == "admin"
    if ok and dest_id is not None:
        dest = await get_object_permission(session, user=user, object_type=dest_type, object_id=dest_id)  # type: ignore[arg-type]
        ok = has_permission(dest, "write")
    if not ok:
        raise HTTPException(status_code=403, detail=ui_detail(
            "move_needs_admin", "Moving to another parent needs admin on this object and write on the destination"))


async def require_visible(session: AsyncSession, user: Any, object_type: str, object_id: uuid.UUID | None,
                          not_found: str) -> None:
    """引用的物件至少要看得到；看不到跟不存在一樣回 404（不透露它在不在）。"""
    if object_id is None or getattr(user, "is_admin", False):
        return
    level = await get_object_permission(session, user=user, object_type=object_type, object_id=object_id)  # type: ignore[arg-type]
    if not has_permission(level, "read"):
        raise HTTPException(status_code=404, detail=not_found)


async def require_parent_write(session: AsyncSession, user: Any, object_type: str, object_id: uuid.UUID | None) -> None:
    """建立時指定的權限上層（例如單位）要有寫入權，否則等於把資料塞進別人的範圍。"""
    if object_id is None or getattr(user, "is_admin", False):
        return
    level = await get_object_permission(session, user=user, object_type=object_type, object_id=object_id)  # type: ignore[arg-type]
    if not has_permission(level, "write"):
        raise HTTPException(status_code=403, detail=ui_detail(
            "parent_write_required", "Write permission on the chosen parent is required"))


async def can_write(session: AsyncSession, user: Any, object_type: str, object_id: uuid.UUID) -> bool:
    if getattr(user, "is_admin", False):
        return True
    level = await get_object_permission(session, user=user, object_type=object_type, object_id=object_id)  # type: ignore[arg-type]
    return has_permission(level, "write")


async def require_create(session: AsyncSession, user: Any, object_type: str, *,
                         parents: dict[str, uuid.UUID | None] | None = None,
                         tenant: tuple[str, uuid.UUID | None] | None = None,
                         wildcard_only: bool = False) -> None:
    """建立物件：照角色授權判斷，不是只看「管理員」開關（客戶 2026-11-12：掛了「系統管理員」角色仍不能新增機櫃）。

    - `parents`：只影響放在哪裡的上層（機櫃的機房、裝置的機櫃）→ 至少要看得到
    - `tenant`：權限繼承的單位 → 要寫入權（同 require_parent_write，不能把資料塞進別人的範圍）
    - 本身：這個類型有萬用寫入權（例如「系統管理員」「網路操作員」角色），或對其中一個上層有寫入權
      （例如部門管理員被指派了某個機房，就能在那個機房新增機櫃）
    - `wildcard_only`：頂層命名空間（區段）只看萬用寫入權，上層的授權不算
    """
    if getattr(user, "is_admin", False):
        return
    from app.services.permission import visible_ids
    refs = {k: v for k, v in (parents or {}).items() if v is not None}
    for t, i in refs.items():
        await require_visible(session, user, t, i, not_found=f"{t} not found")
    if tenant is not None and tenant[1] is not None:
        await require_parent_write(session, user, tenant[0], tenant[1])
        refs[tenant[0]] = tenant[1]
    if await visible_ids(session, user=user, object_type=object_type, required="write") is None:  # type: ignore[arg-type]
        return
    for t, i in ([] if wildcard_only else refs.items()):
        if await can_write(session, user, t, i):
            return
    raise HTTPException(status_code=403, detail=ui_detail(
        "create_needs_write", "You need write permission for this kind of object, or on the parent you chose",
        type=object_type))
