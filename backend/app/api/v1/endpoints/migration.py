"""phpIPAM 遷移 admin endpoints。"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field, SecretStr, model_validator
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
    """支援兩種輸入：
    1) 個別欄位（推薦）：host / port / username / password / database
    2) 或 mysql_url 直接給整條（向後相容）

    至少要提供 host 或 mysql_url。"""

    # 拆開的（推薦）— UI 用這組
    host: Annotated[str | None, Field(max_length=255)] = None
    port: Annotated[int, Field(ge=1, le=65535)] = 3306
    username: Annotated[str | None, Field(max_length=128)] = None
    password: SecretStr | None = None
    database: Annotated[str, Field(max_length=128)] = "phpipam"

    # 或一條 URL（CLI / 自動化）
    mysql_url: Annotated[str | None, Field(min_length=10, max_length=512)] = None

    on_conflict: Literal["skip", "overwrite"] = "skip"
    dry_run: bool = False

    @model_validator(mode="after")
    def _build_url(self) -> "SyncRequest":
        # 如果 mysql_url 沒給，就從個別欄位組
        if not self.mysql_url:
            if not self.host:
                raise ValueError("必須提供 host 或 mysql_url 其一")
            pwd = quote(self.password.get_secret_value(), safe="") if self.password else ""
            user = quote(self.username, safe="") if self.username else ""
            auth = ""
            if user:
                auth = f"{user}:{pwd}@" if pwd else f"{user}@"
            self.mysql_url = f"mysql://{auth}{self.host}:{self.port}/{self.database}"
        return self


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
