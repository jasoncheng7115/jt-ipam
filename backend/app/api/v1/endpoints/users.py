"""Users / Groups admin CRUD（admin only）。

Local users 才能在 UI 建；外部 IdP（OIDC / SAML / LDAP / Radius）的使用者
是首次登入自動 provision；admin 可以鎖/解鎖、加 admin、加群組。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import EmailStr, field_validator
from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import require_admin
from app.core.audit import append_audit
from app.core.db import get_session
from app.core.security import hash_password
from app.models.user import Group, User, UserGroupMember
from app.schemas.base import Paginated, StrictModel

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


class UserCreate(StrictModel):
    username: str
    email: EmailStr | str
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
        email=str(payload.email),
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
    new_pwd = data.pop("password", None)
    unlock = data.pop("unlock", False)
    new_username = data.pop("username", None)
    if new_username is not None and new_username != user.username:
        # 帳號名只允許本機帳號改（外部帳號的 username 由領域決定，例如 jason@ldap）
        if user.auth_provider != "local":
            raise HTTPException(400, detail="cannot rename external account")
        user.username = new_username.strip()
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
    await append_audit(
        session,
        actor_user_id=str(getattr(request.state, "user_id", "")) or None,
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="user", object_id=str(user.id),
        action="update",
        diff={**data, "username": new_username, "password_changed": new_pwd is not None, "unlocked": unlock},
        request_id=getattr(request.state, "request_id", None),
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(409, detail="username or email already exists") from exc
    await session.refresh(user)
    return user


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
    await session.delete(user)
    await append_audit(
        session,
        actor_user_id=str(getattr(request.state, "user_id", "")) or None,
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="user", object_id=str(user_id),
        action="delete", diff={"username": user.username},
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
    await session.delete(g)
    await append_audit(
        session,
        actor_user_id=str(getattr(request.state, "user_id", "")) or None,
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="group", object_id=str(group_id),
        action="delete", diff={"name": g.name},
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


@router.post("/groups/{group_id}/members/{user_id}", status_code=204)
async def add_member(
    group_id: uuid.UUID, user_id: uuid.UUID,
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
    await session.commit()


@router.delete("/groups/{group_id}/members/{user_id}", status_code=204)
async def remove_member(
    group_id: uuid.UUID, user_id: uuid.UUID,
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
        await session.delete(row)
        await session.commit()
