"""Users / Groups admin CRUD（admin only）。

Local users 才能在 UI 建；外部 IdP（OIDC / SAML / LDAP / Radius）的使用者
是首次登入自動 provision；admin 可以鎖/解鎖、加 admin、加群組。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import field_validator
from sqlalchemy import delete, desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import require_admin
from app.core.audit import append_audit
from app.core.db import get_session
from app.core.security import hash_password
from app.models.permission import Permission
from app.models.user import Group, User, UserGroupMember
from app.schemas.base import Paginated, StrictModel
from app.services import sessions as sessions_service

router = APIRouter(
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)


# ─────────────────── Schemas ───────────────────


class UserRead(StrictModel):
    id: uuid.UUID
    username: str
    email: str
    display_name: str | None
    auth_provider: str
    is_active: bool
    is_admin: bool
    can_ssh: bool = False
    last_login_at: datetime | None
    last_login_ip: str | None
    failed_login_count: int
    locked_until: datetime | None
    created_at: datetime
    updated_at: datetime

    @field_validator("last_login_ip", mode="before")
    @classmethod
    def _ip_str(cls, v: object) -> str | None:
        # asyncpg INET → IPv4Address；Pydantic strict 不收，這裡先 str 化
        if v is None:
            return None
        return str(v).split("/", 1)[0]


# 網域不要求有「.」：安裝程式建的 admin 是 admin@localhost，內部也常用 .local / .lan 這類單段網域
# （2026-11-11 客戶：改密碼時預設 email 被擋，只好清空，結果登不進去）
_EMAIL_RE = __import__("re").compile(r"^[^@\s]+@[^@\s.][^@\s]*$")


def _clean_email(v: str | None) -> str:
    """Email 是選用的（2026-10-22：給電視之類的帳號不需要信箱）：空白＝沒有；填了要像個 email。
    email 不是身分（username 才是），只拿來寄通知信。"""
    v = (v or "").strip()
    if v and not _EMAIL_RE.match(v):
        from app.core.ui_error import ui_detail
        raise HTTPException(422, detail=ui_detail("user_email_invalid", "email address is not valid", value=v[:80]))
    return v


class UserCreate(StrictModel):
    username: str
    email: str | None = None
    display_name: str | None = None
    password: str
    is_admin: bool = False
    can_ssh: bool = False


class UserUpdate(StrictModel):
    username: str | None = None
    email: str | None = None
    display_name: str | None = None
    is_active: bool | None = None
    is_admin: bool | None = None
    can_ssh: bool | None = None
    password: str | None = None
    unlock: bool = False    # 設 true → locked_until=None, failed_login_count=0


class GroupRead(StrictModel):
    id: uuid.UUID
    name: str
    description: str | None
    is_builtin: bool
    member_count: int = 0
    created_at: datetime
    updated_at: datetime


class GroupCreate(StrictModel):
    name: str
    description: str | None = None


class GroupUpdate(StrictModel):
    description: str | None = None


# ─────────────────── Users ───────────────────


@router.get("/users", response_model=Paginated[UserRead])
async def list_users(
    session: Annotated[AsyncSession, Depends(get_session)],
    q: str | None = Query(None, description="search by username/email"),
    auth_provider: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Any:
    base = select(User)
    if q:
        like = f"%{q}%"
        base = base.where((User.username.ilike(like)) | (User.email.ilike(like)))
    if auth_provider:
        base = base.where(User.auth_provider == auth_provider)
    total = (
        await session.execute(select(func.count()).select_from(base.subquery()))
    ).scalar_one()
    rows = (
        await session.execute(
            base.order_by(desc(User.created_at)).offset(offset).limit(limit)
        )
    ).scalars().all()
    return {"items": rows, "total": total, "page": offset // limit + 1, "page_size": limit}


@router.post("/users", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreate,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Any:
    if len(payload.password) < 12:
        raise HTTPException(400, detail="password too short (≥ 12 chars)")
    user = User(
        username=payload.username,
        email=_clean_email(payload.email),
        display_name=payload.display_name,
        password_hash=hash_password(payload.password),
        auth_provider="local",
        is_active=True,
        is_admin=payload.is_admin,
        can_ssh=payload.can_ssh,
    )
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="username or email already exists") from exc
    await append_audit(
        session,
        actor_user_id=str(getattr(request.state, "user_id", "")) or None,
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="user", object_id=str(user.id),
        action="create",
        diff={"username": user.username, "email": user.email, "is_admin": user.is_admin},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(user)
    return user


@router.patch("/users/{user_id}", response_model=UserRead)
async def update_user(
    user_id: uuid.UUID,
    payload: UserUpdate,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Any:
    user = (
        await session.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(404, detail="user not found")
    data = payload.model_dump(exclude_unset=True)

    # 不能把最後一個「有效的 admin」降權或停用 —— 否則全系統再也沒有人能進管理區
    # （稽核 / 使用者 / 整合 / 系統設定全鎖死），只能靠伺服器 shell 跑 CLI 救回來。
    # DELETE 早就有這道保護，PATCH 漏了；這裡補齊，兩條路徑一致。
    demoting = data.get("is_admin") is False or data.get("is_active") is False
    if user.is_admin and user.is_active and demoting:
        admin_count = (
            await session.execute(
                select(func.count()).select_from(User).where(
                    User.is_admin.is_(True), User.is_active.is_(True)
                )
            )
        ).scalar_one()
        if admin_count <= 1:
            raise HTTPException(409, detail="cannot demote or deactivate the last active admin")

    if "email" in data:
        data["email"] = _clean_email(data["email"])
    new_pwd = data.pop("password", None)
    unlock = data.pop("unlock", False)
    new_username = data.pop("username", None)
    if new_username is not None and new_username != user.username:
        # 帳號名只允許本機帳號改（外部帳號的 username 由領域決定，例如 jason@ldap）
        if user.auth_provider != "local":
            raise HTTPException(400, detail="cannot rename external account")
        user.username = new_username.strip()
    # 權限變更要在覆寫之前記下舊值 —— 這是整個系統最重要的事實變化之一，
    # 事後才從稽核翻出來太慢（`security.privilege_changed`）
    was_admin = bool(user.is_admin)
    was_active = bool(user.is_active)
    for k, v in data.items():
        setattr(user, k, v)
    if new_pwd is not None:
        if user.auth_provider != "local":
            raise HTTPException(400, detail="cannot set password on non-local user")
        if len(new_pwd) < 12:
            raise HTTPException(400, detail="password too short (≥ 12 chars)")
        user.password_hash = hash_password(new_pwd)
    if unlock:
        user.locked_until = None
        user.failed_login_count = 0
    # 停用帳號、管理員重設密碼：這個人所有的登入立即失效（含已開著的主控台，見 core/console_guard）。
    # 停用時 API 權杖一併撤銷 —— 以前只是暫時被擋，帳號重新啟用時舊權杖就全部回來了
    revoked_sessions = 0
    revoked_tokens = 0
    if (was_active and user.is_active is False) or new_pwd is not None:
        revoked_sessions = await sessions_service.revoke_all(
            session, user, reason="deactivated" if not user.is_active else "password_reset", cutoff=True)
    if was_active and user.is_active is False:
        revoked_tokens = await _revoke_api_tokens(session, user.id)
    await append_audit(
        session,
        actor_user_id=str(getattr(request.state, "user_id", "")) or None,
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="user", object_id=str(user.id),
        action="update",
        diff={**data, "username": new_username, "password_changed": new_pwd is not None, "unlocked": unlock,
              **({"sessions_revoked": revoked_sessions} if revoked_sessions else {}),
              **({"api_tokens_revoked": revoked_tokens} if revoked_tokens else {})},
        request_id=getattr(request.state, "request_id", None),
    )
    if bool(user.is_admin) != was_admin:
        from app.services.security_alert import notify_privilege_change
        # 操作者要查得出來：`request.state` 只有 user_id，沒有 username ——
        # 直接寫死「admin」會讓通知在不是 admin 操作時說謊。
        actor_id = str(getattr(request.state, "user_id", "") or "")
        actor_name = actor_id or "未知"
        if actor_id:
            actor = (await session.execute(
                select(User).where(User.id == uuid.UUID(actor_id)))).scalar_one_or_none()
            if actor is not None:
                actor_name = actor.username
        await notify_privilege_change(
            session, actor=actor_name, target=user.username,
            change="被授予管理權限" if user.is_admin else "被收回管理權限",
        )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(409, detail="username or email already exists") from exc
    await session.refresh(user)
    return user


async def _revoke_api_tokens(session: AsyncSession, user_id: uuid.UUID) -> int:
    from datetime import UTC
    from datetime import datetime as _dt

    from sqlalchemy import update

    from app.models.user import APIToken
    res = await session.execute(
        update(APIToken).where(APIToken.user_id == user_id, APIToken.revoked_at.is_(None))
        .values(revoked_at=_dt.now(UTC)))
    return int(res.rowcount or 0)


async def _user_or_404(session: AsyncSession, user_id: uuid.UUID) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(404, detail="user not found")
    return user


async def _audit_admin(session: AsyncSession, request: Request, user: User, action: str,
                       diff: dict[str, Any]) -> None:
    await append_audit(
        session,
        actor_user_id=str(getattr(request.state, "user_id", "")) or None,
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="user", object_id=str(user.id), action=action,
        diff={"username": user.username, **diff},
        request_id=getattr(request.state, "request_id", None),
    )


class SessionSummary(StrictModel):
    id: uuid.UUID
    created_at: datetime
    last_used_at: datetime
    expires_at: datetime
    ip: str | None
    user_agent: str | None
    method: str
    mfa: bool


@router.get("/users/{user_id}/sessions", response_model=list[SessionSummary])
async def list_user_sessions(
    user_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[Any]:
    """這個人目前登入中的工作階段（管理員檢視）。"""
    await _user_or_404(session, user_id)
    return list(await sessions_service.list_active(session, user_id))


@router.post("/users/{user_id}/revoke-sessions")
async def revoke_user_sessions(
    user_id: uuid.UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, int]:
    """強制登出：撤銷這個人所有的工作階段，連已發出的存取權杖與開著的主控台一起失效。

    API 權杖不在這裡處理（那是給程式用的，要撤銷請到 API 權杖頁或停用帳號）。"""
    user = await _user_or_404(session, user_id)
    n = await sessions_service.revoke_all(session, user, reason="admin_revoked", cutoff=True)
    await _audit_admin(session, request, user, "sessions_revoked", {"count": n, "by_admin": True})
    await session.commit()
    return {"revoked": n}


@router.post("/users/{user_id}/reset-mfa")
async def reset_user_mfa(
    user_id: uuid.UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """重設雙因素驗證（裝置遺失又沒有復原碼時）：清掉 TOTP 與復原碼、撤銷所有登入。

    政策要求 MFA 的話，這個人下次登入時會被要求重新設定。"""
    from app.services import totp as totp_service

    user = await _user_or_404(session, user_id)
    had = totp_service.is_enabled(user)
    user.totp_secret_enc = None
    user.totp_nonce = None
    user.totp_last_step = None
    from app.services.mfa import clear_recovery_codes
    await clear_recovery_codes(session, user.id)
    n = await sessions_service.revoke_all(session, user, reason="mfa_reset", cutoff=True)
    await _audit_admin(session, request, user, "mfa_reset", {"had_totp": had, "sessions_revoked": n})
    await session.commit()
    return {"had_totp": had, "sessions_revoked": n}


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(
    user_id: uuid.UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    user = (
        await session.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(404, detail="user not found")
    # 不能砍最後一個 admin
    if user.is_admin:
        admin_count = (
            await session.execute(
                select(func.count()).select_from(User).where(
                    User.is_admin.is_(True), User.is_active.is_(True)
                )
            )
        ).scalar_one()
        if admin_count <= 1:
            raise HTTPException(409, detail="cannot delete the last active admin")
    # 權限的 principal_id 同時可能指向 user 或 group，所以建不了外鍵 ——
    # 也就是說沒有人會幫我們清。不清就會留下孤兒授權：權限頁列得出來、卻對不到任何人，
    # 稽核時看到一列「不知道是誰」的授權，只能靠猜。（prod 上已經有這種列）
    removed_perms = (await session.execute(
        delete(Permission).where(
            Permission.principal_type == "user", Permission.principal_id == user.id,
        ).returning(Permission.id)
    )).scalars().all()
    await session.delete(user)
    await append_audit(
        session,
        actor_user_id=str(getattr(request.state, "user_id", "")) or None,
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="user", object_id=str(user_id),
        action="delete",
        # 一併清掉幾筆授權要寫進稽核 —— 那是權限異動，不是刪帳號的附帶效果
        diff={"username": user.username, "permissions_removed": len(removed_perms)},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()


# ─────────────────── Groups ───────────────────


@router.get("/groups", response_model=Paginated[GroupRead])
async def list_groups(
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Any:
    total = (
        await session.execute(select(func.count()).select_from(Group))
    ).scalar_one()
    rows = (
        await session.execute(
            select(Group).order_by(Group.name).offset(offset).limit(limit)
        )
    ).scalars().all()
    items: list[dict[str, Any]] = []
    for g in rows:
        cnt = (
            await session.execute(
                select(func.count()).select_from(UserGroupMember)
                .where(UserGroupMember.group_id == g.id)
            )
        ).scalar_one()
        items.append({
            "id": g.id, "name": g.name, "description": g.description,
            "is_builtin": g.is_builtin, "member_count": int(cnt),
            "created_at": g.created_at, "updated_at": g.updated_at,
        })
    return {"items": items, "total": total, "page": offset // limit + 1, "page_size": limit}


@router.post("/groups", response_model=GroupRead, status_code=status.HTTP_201_CREATED)
async def create_group(
    payload: GroupCreate,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Any:
    g = Group(name=payload.name, description=payload.description, is_builtin=False)
    session.add(g)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="group name already exists") from exc
    await append_audit(
        session,
        actor_user_id=str(getattr(request.state, "user_id", "")) or None,
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="group", object_id=str(g.id),
        action="create", diff={"name": g.name},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(g)
    return {
        "id": g.id, "name": g.name, "description": g.description,
        "is_builtin": g.is_builtin, "member_count": 0,
        "created_at": g.created_at, "updated_at": g.updated_at,
    }


@router.patch("/groups/{group_id}", response_model=GroupRead)
async def update_group(
    group_id: uuid.UUID,
    payload: GroupUpdate,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Any:
    g = (
        await session.execute(select(Group).where(Group.id == group_id))
    ).scalar_one_or_none()
    if g is None:
        raise HTTPException(404, detail="group not found")
    if payload.description is not None:
        g.description = payload.description
    await append_audit(
        session,
        actor_user_id=str(getattr(request.state, "user_id", "")) or None,
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="group", object_id=str(g.id),
        action="update", diff=payload.model_dump(exclude_unset=True),
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(g)
    cnt = (
        await session.execute(
            select(func.count()).select_from(UserGroupMember)
            .where(UserGroupMember.group_id == g.id)
        )
    ).scalar_one()
    return {
        "id": g.id, "name": g.name, "description": g.description,
        "is_builtin": g.is_builtin, "member_count": int(cnt),
        "created_at": g.created_at, "updated_at": g.updated_at,
    }


@router.delete("/groups/{group_id}", status_code=204)
async def delete_group(
    group_id: uuid.UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    g = (
        await session.execute(select(Group).where(Group.id == group_id))
    ).scalar_one_or_none()
    if g is None:
        raise HTTPException(404, detail="group not found")
    if g.is_builtin:
        raise HTTPException(409, detail="cannot delete builtin group")
    # 同上：群組的授權沒有外鍵可以連帶刪除，這裡自己清
    removed_perms = (await session.execute(
        delete(Permission).where(
            Permission.principal_type == "group", Permission.principal_id == g.id,
        ).returning(Permission.id)
    )).scalars().all()
    await session.delete(g)
    await append_audit(
        session,
        actor_user_id=str(getattr(request.state, "user_id", "")) or None,
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="group", object_id=str(group_id),
        action="delete",
        diff={"name": g.name, "permissions_removed": len(removed_perms)},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()


@router.get("/groups/{group_id}/members", response_model=list[UserRead])
async def list_members(
    group_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Any:
    g = (await session.execute(select(Group).where(Group.id == group_id))).scalar_one_or_none()
    if g is None:
        raise HTTPException(404, detail="group not found")
    rows = (
        await session.execute(
            select(User).join(
                UserGroupMember, UserGroupMember.user_id == User.id
            ).where(UserGroupMember.group_id == group_id).order_by(User.username)
        )
    ).scalars().all()
    return rows


@router.get("/users/{user_id}/groups", response_model=list[GroupRead])
async def list_user_groups(
    user_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Any:
    """某使用者所屬的角色 / 群組（給「使用者 → 指派角色」UI 用）。"""
    u = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if u is None:
        raise HTTPException(404, detail="user not found")
    rows = (
        await session.execute(
            select(Group).join(
                UserGroupMember, UserGroupMember.group_id == Group.id
            ).where(UserGroupMember.user_id == user_id).order_by(Group.name)
        )
    ).scalars().all()
    return rows


async def _member_audit(
    session: AsyncSession, request: Request, *, group: Group, target: User, action: str,
) -> None:
    """群組成員異動的稽核。物件記成**群組**，內容帶被異動的使用者 —— 查「這個群組
    什麼時候多了誰」與「這個人什麼時候被加進哪個群組」都找得到。"""
    await append_audit(
        session,
        actor_user_id=str(getattr(request.state, "user_id", "")) or None,
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="group", object_id=str(group.id), action=action,
        diff={"group": group.name, "user_id": str(target.id), "username": target.username},
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/groups/{group_id}/members/{user_id}", status_code=204)
async def add_member(
    group_id: uuid.UUID, user_id: uuid.UUID, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    g = (await session.execute(select(Group).where(Group.id == group_id))).scalar_one_or_none()
    u = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if g is None or u is None:
        raise HTTPException(404, detail="group or user not found")
    exists = (
        await session.execute(
            select(UserGroupMember).where(
                UserGroupMember.group_id == group_id,
                UserGroupMember.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if exists is None:
        session.add(UserGroupMember(user_id=user_id, group_id=group_id))
        # 群組成員＝權限（群組上掛著 Permission），加人進群組就是提權，一定要留紀錄。
        # 原本這裡連 request 都沒有 —— 不只沒記做了什麼，連是誰做的都不知道。
        await _member_audit(session, request, group=g, target=u, action="group_member_add")
    await session.commit()


@router.delete("/groups/{group_id}/members/{user_id}", status_code=204)
async def remove_member(
    group_id: uuid.UUID, user_id: uuid.UUID, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    row = (
        await session.execute(
            select(UserGroupMember).where(
                UserGroupMember.group_id == group_id,
                UserGroupMember.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if row is not None:
        g = (await session.execute(
            select(Group).where(Group.id == group_id))).scalar_one_or_none()
        u = (await session.execute(
            select(User).where(User.id == user_id))).scalar_one_or_none()
        await session.delete(row)
        if g is not None and u is not None:
            await _member_audit(session, request, group=g, target=u,
                                action="group_member_remove")
        await session.commit()
