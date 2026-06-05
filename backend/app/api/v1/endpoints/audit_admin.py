"""稽核轉送到 Graylog — 管理區設定 + 測試。

  GET  /api/v1/system/audit-forward
  PUT  /api/v1/system/audit-forward
  POST /api/v1/system/audit-forward/test
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin
from app.core.audit import append_audit
from app.core.db import get_session
from app.schemas.base import StrictModel
from app.services import audit_forward
from app.services.system_config import (
    AuditForwardConfig,
    get_audit_forward,
    set_audit_forward,
)

admin_router = APIRouter(
    prefix="/system", tags=["system"], dependencies=[Depends(require_admin)]
)


class AuditForwardOut(StrictModel):
    enabled: bool
    host: str | None
    port: int
    protocol: str
    fmt: str


class AuditForwardPatch(StrictModel):
    enabled: bool = False
    host: str | None = None
    port: Annotated[int, Field(ge=1, le=65535)] = 12201
    protocol: Literal["tcp", "udp"] = "udp"
    fmt: Literal["gelf", "syslog", "cef"] = "gelf"


def _out(cfg: AuditForwardConfig) -> dict[str, Any]:
    return {"enabled": cfg.enabled, "host": cfg.host, "port": cfg.port,
            "protocol": cfg.protocol, "fmt": cfg.fmt}


@admin_router.get("/audit-forward", response_model=AuditForwardOut)
async def get_audit_forward_ep(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    return _out(await get_audit_forward(session))


@admin_router.put("/audit-forward", response_model=AuditForwardOut)
async def put_audit_forward_ep(
    payload: AuditForwardPatch, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    cfg = await set_audit_forward(
        session, data=payload.model_dump(), updated_by_user_id=uuid.UUID(str(user.id)))
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="system_setting", object_id=None, action="update",
        diff={"setting": "audit_forward", "enabled": cfg.enabled,
              "host": cfg.host, "port": cfg.port, "protocol": cfg.protocol, "fmt": cfg.fmt},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return _out(cfg)


@admin_router.post("/audit-forward/test")
async def audit_forward_test(
    payload: AuditForwardPatch, _user: CurrentUser,
    _session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """以本次送出的設定試送一筆事件（不必先儲存）。"""
    cfg = AuditForwardConfig(
        enabled=True, host=payload.host, port=payload.port,
        protocol=payload.protocol, fmt=payload.fmt)
    if not cfg.host:
        raise HTTPException(400, detail="host required")
    try:
        await audit_forward.send_test(cfg)
    except Exception as exc:
        raise HTTPException(502, detail=f"send failed: {exc.__class__.__name__}") from exc
    return {"ok": True, "sent_to": f"{cfg.protocol}://{cfg.host}:{cfg.port}", "fmt": cfg.fmt}
