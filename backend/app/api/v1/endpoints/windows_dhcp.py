"""Windows DHCP Server 整合 endpoints（Beta，admin only）。

與 OPNsense / pfSense 的 DHCP 各自獨立：這裡只管 Windows DHCP 自己的設定與同步。
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin
from app.core.audit import append_audit
from app.core.db import get_session
from app.models.windows_dhcp import WindowsDhcpServer
from app.schemas.base import Paginated
from app.schemas.windows_dhcp import (
    WindowsDhcpCreate,
    WindowsDhcpRead,
    WindowsDhcpUpdate,
)
from app.services import windows_dhcp as svc
from app.services.background_tasks import spawn_task

router = APIRouter(prefix="/windows-dhcp", tags=["windows-dhcp"],
                   dependencies=[Depends(require_admin)])


async def _get_or_404(session: AsyncSession, sid: uuid.UUID) -> WindowsDhcpServer:
    inst = await session.get(WindowsDhcpServer, sid)
    if inst is None:
        raise HTTPException(404, detail="Not found")
    return inst


@router.get("/servers", response_model=Paginated[WindowsDhcpRead])
async def list_servers(
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=500),
) -> Paginated[WindowsDhcpRead]:
    stmt = (select(WindowsDhcpServer).order_by(WindowsDhcpServer.name)
            .offset((page - 1) * page_size).limit(page_size))
    rows = list((await session.execute(stmt)).scalars().all())
    total = int(await session.scalar(select(func.count()).select_from(WindowsDhcpServer)) or 0)
    return Paginated[WindowsDhcpRead](
        items=[WindowsDhcpRead.model_validate(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.post("/servers", response_model=WindowsDhcpRead, status_code=status.HTTP_201_CREATED)
async def create_server(
    payload: WindowsDhcpCreate, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> WindowsDhcpRead:
    data = payload.model_dump(exclude={"password"})
    inst = WindowsDhcpServer(**data, password_enc=b"placeholder", password_nonce=b"placeholder")
    session.add(inst)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="Name already exists") from exc
    inst.password_enc, inst.password_nonce = svc.encrypt_password(inst.id, payload.password)
    await session.flush()
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="windows_dhcp_server", object_id=str(inst.id), action="create",
        diff={"name": inst.name, "host": inst.host},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(inst)
    return WindowsDhcpRead.model_validate(inst)


@router.patch("/servers/{server_id}", response_model=WindowsDhcpRead)
async def update_server(
    server_id: uuid.UUID, payload: WindowsDhcpUpdate, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> WindowsDhcpRead:
    inst = await _get_or_404(session, server_id)
    data = payload.model_dump(exclude_unset=True)
    new_password = data.pop("password", None)
    for k, v in data.items():
        setattr(inst, k, v)
    if new_password:
        inst.password_enc, inst.password_nonce = svc.encrypt_password(inst.id, new_password)
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="windows_dhcp_server", object_id=str(inst.id), action="update",
        diff=data, request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(inst)
    return WindowsDhcpRead.model_validate(inst)


@router.delete("/servers/{server_id}", status_code=204)
async def delete_server(
    server_id: uuid.UUID, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    inst = await _get_or_404(session, server_id)
    # dhcp_pool_ranges 無外鍵 cascade → 自行清掉這台寫的列（不碰其他來源）
    from app.models.dhcp import DHCPPoolRange
    await session.execute(delete(DHCPPoolRange).where(
        DHCPPoolRange.source_type == "windows_dhcp", DHCPPoolRange.source_id == server_id,
    ))
    await session.delete(inst)
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="windows_dhcp_server", object_id=str(server_id), action="delete",
        diff={}, request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()


@router.post("/servers/{server_id}/test")
async def test_server(
    server_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    inst = await _get_or_404(session, server_id)
    try:
        return await svc.healthcheck(inst)
    except svc.WindowsDhcpError as exc:
        raise HTTPException(502, detail=str(exc)) from exc


@router.post("/servers/{server_id}/sync")
async def trigger_sync(
    server_id: uuid.UUID, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """非同步 —— 立刻回 task_id，同步在背景跑。"""
    inst = await _get_or_404(session, server_id)
    actor_user_id, inst_name = user.id, inst.name
    actor_ip = request.client.host if request.client else None
    actor_ua = request.headers.get("user-agent")
    request_id = getattr(request.state, "request_id", None)

    async def _runner(sess: AsyncSession, _task: Any) -> dict[str, Any]:
        obj = await sess.get(WindowsDhcpServer, server_id)
        if obj is None:
            raise RuntimeError("Windows DHCP server disappeared")
        summary = await svc.sync_instance(sess, obj)
        await append_audit(
            sess, actor_user_id=str(actor_user_id), actor_ip=actor_ip, actor_user_agent=actor_ua,
            object_type="windows_dhcp_server", object_id=str(server_id), action="sync",
            diff=summary, request_id=request_id,
        )
        await sess.commit()
        return summary

    task = await spawn_task(
        session=session, kind="windows_dhcp.sync", target_type="windows_dhcp_server",
        target_id=server_id, target_label=inst_name, actor_user_id=actor_user_id, runner=_runner,
    )
    return {"task_id": str(task.id), "status": task.status,
            "queued_at": task.queued_at.isoformat()}
