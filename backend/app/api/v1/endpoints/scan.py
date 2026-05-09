"""掃描端點：手動觸發單一 subnet 的 ICMP 掃描。

Phase 1 設計為同步執行，受時間與規模限制。Phase 2 改成 Celery 背景任務。
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin
from app.core.audit import append_audit
from app.core.db import get_session
from app.models.subnet import Subnet
from app.schemas.base import StrictModel
from app.services.scanner import (
    ScannerNotAvailable,
    scan_subnet_icmp,
)

router = APIRouter(tags=["scanner"])


class ScanResult(StrictModel):
    subnet_id: uuid.UUID
    hosts: int
    online: int
    offline: int


@router.post(
    "/subnets/{subnet_id}/scan",
    response_model=ScanResult,
    dependencies=[Depends(require_admin)],
)
async def scan_subnet(
    subnet_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ScanResult:
    subnet = await session.get(Subnet, subnet_id)
    if subnet is None:
        raise HTTPException(404, detail="Subnet not found")

    try:
        result = await scan_subnet_icmp(session, subnet)
    except ScannerNotAvailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="subnet",
        object_id=str(subnet.id),
        action="scan",
        diff=result,
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return ScanResult(subnet_id=subnet.id, **result)
