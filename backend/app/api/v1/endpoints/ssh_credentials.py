"""by-user SSH 憑證管理：CRUD（永不回傳明文）。

授權：一律 owner-only（owner_user_id == 目前使用者）。明文（密碼/私鑰/passphrase）
只在 POST 進來時收到，立即信封加密儲存；list/detail 只回遮罩資訊。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser
from app.core.audit import append_audit
from app.core.db import get_session
from app.core.security import envelope_encrypt
from app.models.address import IPAddress
from app.models.ssh_credential import SSHCredential
from app.schemas.base import StrictModel
from app.services.permission import can_use_ssh

router = APIRouter(prefix="/ssh-credentials", tags=["ssh"])


def cred_aad(owner_user_id: uuid.UUID, field: str) -> bytes:
    """信封 AAD 綁定擁有者 + 欄位，避免密文跨人/跨欄位搬遷。"""
    return f"ssh_cred:{owner_user_id}:{field}".encode()


class SSHCredentialCreate(StrictModel):
    label: Annotated[str, Field(min_length=1, max_length=128)]
    username: Annotated[str, Field(min_length=1, max_length=128)]
    auth_type: str  # password | key
    target_ip_id: uuid.UUID | None = None
    password: str | None = None
    private_key: str | None = None
    passphrase: str | None = None


class SSHCredentialRead(StrictModel):
    """遮罩讀取：不含任何明文 / 密文 / 指紋以外資訊。"""

    id: uuid.UUID
    label: str
    username: str
    auth_type: str
    target_ip_id: uuid.UUID | None
    has_password: bool
    has_private_key: bool
    last_used_at: datetime | None
    created_at: datetime


def _to_read(c: SSHCredential) -> SSHCredentialRead:
    fields = c.secrets_enc or {}
    return SSHCredentialRead(
        id=c.id, label=c.label, username=c.username, auth_type=c.auth_type,
        target_ip_id=c.target_ip_id,
        has_password="password" in fields,
        has_private_key="private_key" in fields,
        last_used_at=c.last_used_at, created_at=c.created_at,
    )


@router.get("", response_model=list[SSHCredentialRead])
async def list_ssh_credentials(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    target_ip_id: uuid.UUID | None = None,
) -> Any:
    """列出自己的憑證。帶 target_ip_id → 回該目標適用者（綁該 IP 的 + 個人預設）。"""
    stmt = select(SSHCredential).where(SSHCredential.owner_user_id == user.id)
    if target_ip_id is not None:
        stmt = stmt.where(
            (SSHCredential.target_ip_id == target_ip_id)
            | (SSHCredential.target_ip_id.is_(None))
        )
    stmt = stmt.order_by(SSHCredential.created_at.desc())
    rows = (await session.execute(stmt)).scalars().all()
    return [_to_read(c) for c in rows]


@router.post("", response_model=SSHCredentialRead, status_code=201)
async def create_ssh_credential(
    payload: SSHCredentialCreate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Any:
    if payload.auth_type not in ("password", "key"):
        raise HTTPException(400, detail="auth_type must be 'password' or 'key'")

    secrets_enc: dict[str, Any] = {}
    if payload.auth_type == "password":
        if not payload.password:
            raise HTTPException(400, detail="password required")
        secrets_enc["password"] = envelope_encrypt(payload.password, aad=cred_aad(user.id, "password"))
    else:
        if not payload.private_key:
            raise HTTPException(400, detail="private_key required")
        secrets_enc["private_key"] = envelope_encrypt(payload.private_key, aad=cred_aad(user.id, "private_key"))
        if payload.passphrase:
            secrets_enc["passphrase"] = envelope_encrypt(payload.passphrase, aad=cred_aad(user.id, "passphrase"))

    # 綁定目標時：確認該 IP 存在且使用者確實可對它連線（避免存到看不到的目標）
    if payload.target_ip_id is not None:
        ip = await session.get(IPAddress, payload.target_ip_id)
        if ip is None or not await can_use_ssh(session, user=user, ip=ip):
            raise HTTPException(403, detail="無此目標的 SSH 連線權限")

    cred = SSHCredential(
        owner_user_id=user.id, label=payload.label.strip(), username=payload.username.strip(),
        auth_type=payload.auth_type, target_ip_id=payload.target_ip_id, secrets_enc=secrets_enc,
    )
    session.add(cred)
    await session.flush()
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="ssh_credential", object_id=str(cred.id),
        action="create",
        diff={"label": cred.label, "username": cred.username, "auth_type": cred.auth_type,
              "target_ip_id": str(cred.target_ip_id) if cred.target_ip_id else None},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(cred)
    return _to_read(cred)


@router.delete("/{cred_id}", status_code=204)
async def delete_ssh_credential(
    cred_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    cred = await session.get(SSHCredential, cred_id)
    # owner-only；不洩漏存在性
    if cred is None or cred.owner_user_id != user.id:
        raise HTTPException(404, detail="not found")
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="ssh_credential", object_id=str(cred.id),
        action="delete", diff={"label": cred.label},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.delete(cred)
    await session.commit()
