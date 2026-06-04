"""異常偵測 endpoint：trigger run + read latest report。"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin
from app.core.audit import append_audit
from app.core.db import get_session
from app.services.anomaly import run_detection

router = APIRouter(prefix="/anomalies", tags=["anomalies"])


@router.post("/scan", dependencies=[Depends(require_admin)])
async def scan(
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """執行所有偵測規則。Phase 2 的排程版（Celery beat）會週期觸發此邏輯。"""
    report = await run_detection(session, notify_admins=True)
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="anomaly",
        object_id=None,
        action="scan",
        diff={
            "ip_conflicts": len(report.ip_conflicts),
            "mac_drifts": len(report.mac_drifts),
            "ghost_ips": len(report.ghost_ips),
            "unauthorized_ips": len(report.unauthorized_ips),
        },
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return report.to_dict()
