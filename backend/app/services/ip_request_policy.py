"""IP 申請審核政策（可在管理頁設定的「審核關卡」）。

存 system_settings.ip_request_policy（JSONB，60s cache）。支援四種模式（客戶各取所需）：
  - admin      ：僅系統管理員可審（單關卡，一人核准即配發）
  - designated ：管理員 + 指定使用者/群組（單關卡，任一人核准即配發）
  - parallel   ：多組會簽——設多個 step，不分先後，全部 step 都核准才配發
  - stages     ：依序多關卡——設多個 step，須 step 0→1→2… 逐關通過，最後一關核准才配發

parallel / stages 的每個 step = {name, user_ids[], group_ids[]}；逐 step 核准記錄存
ip_request_stage_approvals 表。allow_self_approve 控制申請人本人是否能核准自己的申請。
"""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ip_request import IPRequestStageApproval
from app.models.system_setting import SystemSetting
from app.models.user import Group, User, UserGroupMember

if TYPE_CHECKING:
    from app.models.ip_request import IPRequest

POLICY_KEY = "ip_request_policy"
MODES = ("admin", "designated", "parallel", "stages")
MULTI_STEP_MODES = ("parallel", "stages")
_TTL_SEC = 60.0
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _default_policy() -> dict[str, Any]:
    return {
        "approver_mode": "admin",
        "designated_user_ids": [],
        "designated_group_ids": [],
        "allow_self_approve": False,
        "stages": [],   # [{name, user_ids:[], group_ids:[]}]
    }


def _bust() -> None:
    _cache.pop(POLICY_KEY, None)


def _clean_steps(raw: object) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return out
    for i, s in enumerate(raw):
        if not isinstance(s, dict):
            continue
        out.append({
            "name": (str(s.get("name") or "").strip() or f"關卡 {i + 1}")[:64],
            "user_ids": [str(x) for x in (s.get("user_ids") or []) if x],
            "group_ids": [str(x) for x in (s.get("group_ids") or []) if x],
        })
    return out


async def get_policy(session: AsyncSession) -> dict[str, Any]:
    now = time.monotonic()
    c = _cache.get(POLICY_KEY)
    if c and now - c[0] < _TTL_SEC:
        return dict(c[1])
    pol = _default_policy()
    row = await session.get(SystemSetting, POLICY_KEY)
    if row and isinstance(row.value, dict):
        v = row.value
        if v.get("approver_mode") in MODES:
            pol["approver_mode"] = v["approver_mode"]
        if isinstance(v.get("allow_self_approve"), bool):
            pol["allow_self_approve"] = v["allow_self_approve"]
        for k in ("designated_user_ids", "designated_group_ids"):
            if isinstance(v.get(k), list):
                pol[k] = [str(x) for x in v[k] if x]
        pol["stages"] = _clean_steps(v.get("stages"))
    _cache[POLICY_KEY] = (now, dict(pol))
    return pol


async def set_policy(
    session: AsyncSession, *, data: dict[str, Any], updated_by_user_id: uuid.UUID,
) -> dict[str, Any]:
    from sqlalchemy.orm.attributes import flag_modified
    row = await session.get(SystemSetting, POLICY_KEY)
    if row is None:
        row = SystemSetting(key=POLICY_KEY, value={}, updated_by=updated_by_user_id)
        session.add(row)
    val = dict(row.value or {})
    if data.get("approver_mode") in MODES:
        val["approver_mode"] = data["approver_mode"]
    if isinstance(data.get("allow_self_approve"), bool):
        val["allow_self_approve"] = data["allow_self_approve"]
    for k in ("designated_user_ids", "designated_group_ids"):
        if isinstance(data.get(k), list):
            val[k] = [str(x) for x in data[k] if x]
    if "stages" in data:
        val["stages"] = _clean_steps(data.get("stages"))
    row.value = val
    row.updated_by = updated_by_user_id
    flag_modified(row, "value")
    await session.commit()
    _bust()
    return await get_policy(session)


# ─────────────────── 成員判定 ───────────────────
async def _user_group_ids(session: AsyncSession, user: User) -> set[str]:
    rows = (await session.execute(
        select(UserGroupMember.group_id).where(UserGroupMember.user_id == user.id)
    )).all()
    return {str(g[0]) for g in rows}


async def _in_set(session: AsyncSession, user: User, user_ids, group_ids) -> bool:
    if str(user.id) in set(user_ids or []):
        return True
    gids = set(group_ids or [])
    if gids and (await _user_group_ids(session, user)) & gids:
        return True
    return False


async def _is_designated(session: AsyncSession, user: User, pol: dict[str, Any]) -> bool:
    return await _in_set(session, user, pol.get("designated_user_ids"), pol.get("designated_group_ids"))


def _steps(pol: dict[str, Any]) -> list[dict[str, Any]]:
    return pol.get("stages") or []


# ─────────────────── 多關卡進度 ───────────────────
async def approved_step_indices(session: AsyncSession, request: IPRequest) -> set[int]:
    rows = (await session.execute(
        select(IPRequestStageApproval.step_index)
        .where(IPRequestStageApproval.request_id == request.id)
    )).all()
    return {int(r[0]) for r in rows}


async def actionable_steps(
    session: AsyncSession, user: User, request: IPRequest, pol: dict[str, Any],
) -> list[int]:
    """此使用者「現在」可核准的 step 索引清單（已含模式/順序/自核判定）。"""
    if request.requester_user_id == user.id and not pol.get("allow_self_approve"):
        return []
    steps = _steps(pol)
    if not steps:
        return []
    approved = await approved_step_indices(session, request)
    pending = [i for i in range(len(steps)) if i not in approved]
    if not pending:
        return []
    is_admin = bool(getattr(user, "is_admin", False))
    if pol["approver_mode"] == "stages":
        cur = pending[0]   # 依序：只有最前面那一關可核准
        if is_admin or await _in_set(session, user, steps[cur]["user_ids"], steps[cur]["group_ids"]):
            return [cur]
        return []
    # parallel：所有未核准且該使用者是審核人的 step 都可核准
    out = []
    for i in pending:
        if is_admin or await _in_set(session, user, steps[i]["user_ids"], steps[i]["group_ids"]):
            out.append(i)
    return out


async def is_global_approver(session: AsyncSession, user: User) -> bool:
    """是否為審核人（可看待審清單）——不看是否申請人本人。"""
    if getattr(user, "is_admin", False):
        return True
    pol = await get_policy(session)
    mode = pol["approver_mode"]
    if mode == "designated":
        return await _is_designated(session, user, pol)
    if mode in MULTI_STEP_MODES:
        for s in _steps(pol):
            if await _in_set(session, user, s["user_ids"], s["group_ids"]):
                return True
    return False


async def can_approve(session: AsyncSession, user: User, request: IPRequest) -> bool:
    """此使用者能否核准/駁回此申請（目前這一步）。"""
    pol = await get_policy(session)
    mode = pol["approver_mode"]
    if request.requester_user_id == user.id and not pol.get("allow_self_approve"):
        return False
    if mode in ("admin", "designated"):
        if getattr(user, "is_admin", False):
            return True
        return mode == "designated" and await _is_designated(session, user, pol)
    # parallel / stages
    return bool(await actionable_steps(session, user, request, pol))


async def approver_users(session: AsyncSession) -> list[User]:
    """所有可能的審核人（管理員 + 各模式設定的人/群組），active 去重——給建立時通知。"""
    pol = await get_policy(session)
    out: dict[uuid.UUID, User] = {}
    admins = (await session.execute(
        select(User).where(User.is_admin.is_(True), User.is_active.is_(True))
    )).scalars().all()
    for u in admins:
        out[u.id] = u
    uids: set[uuid.UUID] = set()
    gids: set[uuid.UUID] = set()
    mode = pol["approver_mode"]
    if mode == "designated":
        uids |= {uuid.UUID(x) for x in pol.get("designated_user_ids") or [] if _is_uuid(x)}
        gids |= {uuid.UUID(x) for x in pol.get("designated_group_ids") or [] if _is_uuid(x)}
    elif mode in MULTI_STEP_MODES:
        # stages：建立時只通知第一關；parallel：通知所有關卡
        steps = _steps(pol)
        targets = steps[:1] if mode == "stages" else steps
        for s in targets:
            uids |= {uuid.UUID(x) for x in s["user_ids"] if _is_uuid(x)}
            gids |= {uuid.UUID(x) for x in s["group_ids"] if _is_uuid(x)}
    if gids:
        member_rows = (await session.execute(
            select(UserGroupMember.user_id).where(UserGroupMember.group_id.in_(gids))
        )).all()
        uids.update(r[0] for r in member_rows)
    if uids:
        for u in (await session.execute(
            select(User).where(User.id.in_(uids), User.is_active.is_(True))
        )).scalars().all():
            out[u.id] = u
    return list(out.values())


async def stage_progress(session: AsyncSession, request: IPRequest) -> list[dict[str, Any]]:
    """給前端顯示：每一關的名稱 + 是否已核准 + 是否為目前待審關卡。非多關卡模式回空。"""
    pol = await get_policy(session)
    if pol["approver_mode"] not in MULTI_STEP_MODES:
        return []
    steps = _steps(pol)
    approved = await approved_step_indices(session, request)
    pending = [i for i in range(len(steps)) if i not in approved]
    cur = pending[0] if (pol["approver_mode"] == "stages" and pending) else None
    out = []
    for i, s in enumerate(steps):
        out.append({
            "index": i, "name": s["name"],
            "approved": i in approved,
            "is_current": (i == cur) if cur is not None else (i in pending),
        })
    return out


def _is_uuid(s: Any) -> bool:
    try:
        uuid.UUID(str(s))
        return True
    except (ValueError, TypeError):
        return False


async def list_groups(session: AsyncSession) -> list[Group]:
    return list((await session.execute(select(Group).order_by(Group.name))).scalars().all())
