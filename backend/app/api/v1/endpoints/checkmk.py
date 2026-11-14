"""Checkmk 整合 endpoints（照 Zabbix 的分法）。

分兩個 router，對應三種資料分類：
- 設定（實例 CRUD／測試／同步）＝純管理資料 → `require_admin`
- 主機鏡像與涵蓋缺口＝全域基礎設施資料 → `require_global_read`

缺口查詢帶 `subnet_ids` 才不會用全站資料回答某網段的問題（v0.5.194 的教訓）。
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin, require_global_read
from app.core.audit import append_audit
from app.core.db import get_session
from app.core.ui_error import detail_of
from app.models.checkmk import CheckmkHost, CheckmkInstance
from app.schemas.base import Paginated
from app.schemas.checkmk import CheckmkCreate, CheckmkRead, CheckmkUpdate
from app.services import checkmk as svc
from app.services.background_tasks import spawn_task

router = APIRouter(prefix="/checkmk", tags=["checkmk"],
                   dependencies=[Depends(require_admin)])
view_router = APIRouter(prefix="/checkmk", tags=["checkmk"],
                        dependencies=[Depends(require_global_read)])


def _read(inst: CheckmkInstance) -> CheckmkRead:
    out = CheckmkRead.model_validate(inst)
    out.has_api_secret = bool(inst.api_secret_enc)
    return out


async def _get_or_404(session: AsyncSession, inst_id: uuid.UUID) -> CheckmkInstance:
    inst = await session.get(CheckmkInstance, inst_id)
    if inst is None:
        raise HTTPException(404, detail="Not found")
    return inst


@view_router.get("", response_model=Paginated[CheckmkRead])
async def list_instances(
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=500),
) -> Paginated[CheckmkRead]:
    stmt = (select(CheckmkInstance).order_by(CheckmkInstance.name)
            .offset((page - 1) * page_size).limit(page_size))
    rows = list((await session.execute(stmt)).scalars().all())
    total = int(await session.scalar(select(func.count()).select_from(CheckmkInstance)) or 0)
    return Paginated[CheckmkRead](items=[_read(r) for r in rows],
                                 total=total, page=page, page_size=page_size)


def _check_url(url: str) -> None:
    """網址少了網站名稱：存檔就擋下來（不要等到同步才失敗）。"""
    try:
        svc.api_base(url)
    except svc.CheckmkError as exc:
        raise HTTPException(422, detail=detail_of(exc, "checkmk_error")) from exc


@router.post("", response_model=CheckmkRead, status_code=status.HTTP_201_CREATED)
async def create_instance(
    payload: CheckmkCreate, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CheckmkRead:
    data = payload.model_dump(exclude={"api_secret"})
    data["api_url"] = str(data["api_url"]).rstrip("/")
    _check_url(data["api_url"])
    data["scope_subnet_ids"] = [str(s) for s in (data.get("scope_subnet_ids") or [])] or None
    inst = CheckmkInstance(**data)
    session.add(inst)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="Name already exists") from exc
    # 機密的 AAD 綁 id，所以要先 flush 拿到 id 才能加密
    inst.api_secret_enc, inst.api_secret_nonce = svc.encrypt_api_secret(inst.id, payload.api_secret)
    await session.flush()
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="checkmk_instance", object_id=str(inst.id), action="create",
        diff={"name": inst.name, "api_url": inst.api_url},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(inst)
    return _read(inst)


@router.patch("/{inst_id}", response_model=CheckmkRead)
async def update_instance(
    inst_id: uuid.UUID, payload: CheckmkUpdate, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CheckmkRead:
    inst = await _get_or_404(session, inst_id)
    data = payload.model_dump(exclude_unset=True)
    secret = data.pop("api_secret", None)
    for k, v in data.items():
        if k == "api_url" and v is not None:
            v = str(v).rstrip("/")
            _check_url(v)
        if k == "scope_subnet_ids":
            v = [str(s) for s in (v or [])] or None
        setattr(inst, k, v)
    if secret:
        inst.api_secret_enc, inst.api_secret_nonce = svc.encrypt_api_secret(inst.id, secret)
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="checkmk_instance", object_id=str(inst.id), action="update",
        diff={k: str(v) for k, v in data.items()},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(inst)
    return _read(inst)


@router.delete("/{inst_id}", status_code=204)
async def delete_instance(
    inst_id: uuid.UUID, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    inst = await _get_or_404(session, inst_id)
    # 主機鏡像走外鍵 cascade；它寫進共用表的主機名稱一併收回（沒有外鍵會跟著刪）
    from app.services.integration_cleanup import forget_instance
    await forget_instance(session, source="checkmk", source_id=inst.id)
    await session.delete(inst)
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="checkmk_instance", object_id=str(inst_id), action="delete", diff={},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()


@router.post("/{inst_id}/test")
async def test_instance(
    inst_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """測試連線：回 Checkmk 版本、版本別、網站、能否讀主機、可讀主機數。"""
    inst = await _get_or_404(session, inst_id)
    try:
        return await svc.healthcheck(inst)
    except svc.CheckmkError as exc:
        raise HTTPException(502, detail=detail_of(exc, "checkmk_error")) from exc


@router.post("/{inst_id}/sync")
async def trigger_sync(
    inst_id: uuid.UUID, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    inst = await _get_or_404(session, inst_id)
    actor_user_id, inst_name = user.id, inst.name
    actor_ip = request.client.host if request.client else None
    actor_ua = request.headers.get("user-agent")
    request_id = getattr(request.state, "request_id", None)

    async def _runner(sess: AsyncSession, _task: Any) -> dict[str, Any]:
        obj = await sess.get(CheckmkInstance, inst_id)
        if obj is None:
            raise RuntimeError("Checkmk instance disappeared")
        summary = await svc.sync_instance(sess, obj)
        await append_audit(
            sess, actor_user_id=str(actor_user_id), actor_ip=actor_ip, actor_user_agent=actor_ua,
            object_type="checkmk_instance", object_id=str(inst_id), action="sync",
            diff={k: str(v) for k, v in summary.items()}, request_id=request_id,
        )
        await sess.commit()
        return summary

    task = await spawn_task(
        session=session, kind="checkmk.sync", target_type="checkmk_instance",
        target_id=inst_id, target_label=inst_name, actor_user_id=actor_user_id, runner=_runner,
    )
    return {"task_id": str(task.id), "status": task.status,
            "queued_at": task.queued_at.isoformat()}


# ─────────────────── 唯讀檢視 ───────────────────
@view_router.get("/{inst_id}/hosts")
async def list_hosts(
    inst_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    q: str | None = Query(None, max_length=128),
) -> dict[str, Any]:
    stmt = select(CheckmkHost).where(CheckmkHost.instance_id == inst_id)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(CheckmkHost.host_name.ilike(like) | CheckmkHost.alias.ilike(like))
    rows = (await session.execute(stmt.order_by(CheckmkHost.host_name).limit(2000))).scalars().all()
    return {"items": [{
        "id": str(r.id), "host_name": r.host_name, "alias": r.alias, "site": r.site,
        "state": r.state, "checks_enabled": r.checks_enabled, "downtime": r.downtime,
        "ip": str(r.ip) if r.ip else None, "addresses": r.addresses, "folder": r.folder,
        "groups": r.groups, "labels": r.labels, "output": r.output,
        "last_check": r.last_check.isoformat() if r.last_check else None,
        "ip_address_id": str(r.jt_ipam_address_id) if r.jt_ipam_address_id else None,
        "synced_at": r.synced_at.isoformat() if r.synced_at else None,
    } for r in rows]}


@view_router.get("/{inst_id}/coverage-gap")
async def coverage_gap(
    inst_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    subnet_ids: Annotated[list[uuid.UUID] | None, Query()] = None,
) -> dict[str, Any]:
    """IPAM 有主機名稱、但沒有被這台 Checkmk 監控的位址。

    `subnet_ids` 沒帶＝全站；帶了就只看那些網段（避免用全域資料回答局部問題）。
    """
    await _get_or_404(session, inst_id)
    rows = await svc.coverage_gap(session, instance_id=inst_id,
                                  subnet_ids=list(subnet_ids) if subnet_ids else None)
    return {"items": rows, "count": len(rows),
            "scope": "subnets" if subnet_ids else "global"}
