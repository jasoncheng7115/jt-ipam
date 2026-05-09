"""phpIPAM 遷移 admin endpoints。"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin
from app.core.audit import append_audit
from app.core.db import get_session
from app.models.migration_mapping import PhpIPAMMigrationMapping
from app.schemas.base import StrictModel
from app.services.phpipam_migration import run_migration

router = APIRouter(prefix="/migration/phpipam", tags=["migration"])


class SyncRequest(StrictModel):
    mysql_url: Annotated[str, Field(min_length=10, max_length=512)]
    on_conflict: Literal["skip", "overwrite"] = "skip"
    dry_run: bool = False


class MappingStat(StrictModel):
    object_type: str
    count: int


@router.get("/status",
            response_model=list[MappingStat],
            dependencies=[Depends(require_admin)])
async def status(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[MappingStat]:
    """每個物件類型已建立 mapping 的筆數。"""
    rows = (
        await session.execute(
            select(
                PhpIPAMMigrationMapping.object_type,
                func.count().label("c"),
            ).group_by(PhpIPAMMigrationMapping.object_type)
        )
    ).all()
    return [MappingStat(object_type=r.object_type, count=int(r.c)) for r in rows]


@router.post("/sync",
             dependencies=[Depends(require_admin)])
async def sync(
    payload: SyncRequest,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """執行 phpIPAM → jt-ipam 同步。

    A09：操作寫 audit；mysql_url 不寫入 audit（含密碼）。
    A04：dry_run=true 不影響資料；建議第一次先用 dry-run 看報告。
    """
    report = await run_migration(
        session,
        mysql_url=payload.mysql_url,
        on_conflict=payload.on_conflict,
        dry_run=payload.dry_run,
    )

    # 不把 mysql_url 寫進 diff（含密碼）
    redacted = {
        "on_conflict": payload.on_conflict,
        "dry_run": payload.dry_run,
        "tables": {k: v.to_dict() for k, v in report.tables.items()},
        "error": report.error,
    }
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="phpipam_migration",
        object_id=None,
        action="sync",
        diff=redacted,
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return report.to_dict()
