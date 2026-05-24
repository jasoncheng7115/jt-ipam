"""phpIPAM 遷移 admin endpoints。

phpIPAM 預設 MySQL 只 listen 127.0.0.1，所以我們支援透過 **SSH tunnel** 連過去。
私鑰跟 known_host 都不存 DB — 由 UI 一次性提供，request 結束即丟。

OWASP：
- A04：private_key / mysql_password 都用 SecretStr 處理；不寫 audit
- A06：known_host 必填（除非 TOFU preview 模式）；SSH 不允許 password / agent fallback
- A09：操作寫 audit，mysql_url / private_key / password 不寫進 diff
- A10：SSH timeout、tunnel context manager 保證關閉
"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field, SecretStr, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin
from app.core.audit import append_audit
from app.core.db import get_session
from app.models.migration_mapping import PhpIPAMMigrationMapping
from app.schemas.base import StrictModel
from app.services.phpipam_migration import run_migration
from app.services.ssh_tunnel import (
    SSHHostKeyMismatch,
    SSHTunnelError,
    TunnelConfig,
    fetch_host_key,
    open_tunnel,
)

router = APIRouter(prefix="/migration/phpipam", tags=["migration"])


class SyncRequest(StrictModel):
    """MySQL 連線目標 + 可選 SSH tunnel。

    兩種模式：
    1. 直連（MySQL 對外）：填 host / port / username / password / database
    2. SSH tunnel（推薦）：上面 host 填 127.0.0.1（或 phpIPAM 內網看到的 MySQL IP），
       另外填 ssh_host / ssh_username / ssh_private_key / ssh_known_host
    """

    # MySQL 端
    host: Annotated[str, Field(min_length=1, max_length=255)] = "127.0.0.1"
    port: Annotated[int, Field(ge=1, le=65535)] = 3306
    username: Annotated[str | None, Field(max_length=128)] = None
    password: SecretStr | None = None
    database: Annotated[str, Field(max_length=128)] = "phpipam"

    # 或一條 URL（CLI / 自動化；個別欄位優先）
    mysql_url: Annotated[str | None, Field(min_length=10, max_length=512)] = None

    # SSH tunnel（選填）
    ssh_host: Annotated[str | None, Field(max_length=255)] = None
    ssh_port: Annotated[int, Field(ge=1, le=65535)] = 22
    ssh_username: Annotated[str | None, Field(max_length=128)] = None
    ssh_private_key: SecretStr | None = None
    ssh_known_host: Annotated[str | None, Field(max_length=2048)] = None  # 一行 ssh-keyscan 輸出

    on_conflict: Literal["skip", "overwrite"] = "skip"
    dry_run: bool = False

    @model_validator(mode="after")
    def _check(self) -> "SyncRequest":
        if self.ssh_host:
            if not self.ssh_username:
                raise ValueError("ssh_host 給了就要給 ssh_username")
            if not self.ssh_private_key:
                raise ValueError("ssh_host 給了就要給 ssh_private_key")
        return self


class FingerprintRequest(StrictModel):
    """探測 SSH host fingerprint（TOFU 第一步）。"""

    ssh_host: Annotated[str, Field(min_length=1, max_length=255)]
    ssh_port: Annotated[int, Field(ge=1, le=65535)] = 22


class FingerprintResponse(StrictModel):
    key_type: str
    key_b64: str
    known_host: str
    fingerprint: str


class MappingStat(StrictModel):
    object_type: str
    count: int


def _compose_mysql_url(host: str, port: int, user: str | None,
                       pwd: SecretStr | None, db: str) -> str:
    u = quote(user, safe="") if user else ""
    p = quote(pwd.get_secret_value(), safe="") if pwd else ""
    auth = ""
    if u:
        auth = f"{u}:{p}@" if p else f"{u}@"
    return f"mysql://{auth}{host}:{port}/{db}"


# ─────────────────── endpoints ───────────────────


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


@router.post("/ssh-fingerprint",
             response_model=FingerprintResponse,
             dependencies=[Depends(require_admin)])
async def ssh_fingerprint(
    payload: FingerprintRequest,
) -> FingerprintResponse:
    """TOFU 第一步：拿 SSH server 的 fingerprint 給 user 確認。
    回傳的 `known_host` 字串可直接帶入後續 /sync 呼叫的 ssh_known_host 欄位。
    """
    try:
        info = await fetch_host_key(payload.ssh_host, payload.ssh_port)
    except SSHTunnelError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return FingerprintResponse(**info)


@router.post("/sync",
             dependencies=[Depends(require_admin)])
async def sync(
    payload: SyncRequest,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """執行 phpIPAM → jt-ipam 同步。

    A09：操作寫 audit；私鑰 / DB 密碼 / mysql_url 不寫進 audit。
    A04：dry_run=true 不影響資料；建議第一次先用 dry-run。
    A06：若 ssh_host 給了但沒給 ssh_known_host → 拒絕（避免 MITM）；
         例外是 dry_run + 顯式 trust（不在 API 提供，要先打 /ssh-fingerprint）。
    """

    # 組 mysql_url（個別欄位優先；否則用使用者提供的 mysql_url）
    mysql_url = payload.mysql_url
    if not mysql_url:
        mysql_url = _compose_mysql_url(
            payload.host, payload.port,
            payload.username, payload.password, payload.database,
        )

    # ── 走 SSH tunnel ──
    if payload.ssh_host:
        if not payload.ssh_known_host:
            raise HTTPException(
                status_code=422,
                detail="ssh_known_host 必填（先呼叫 /migration/phpipam/ssh-fingerprint 取得）",
            )

        tunnel_cfg = TunnelConfig(
            host=payload.ssh_host,
            port=payload.ssh_port,
            username=payload.ssh_username or "",
            private_key_pem=payload.ssh_private_key.get_secret_value() if payload.ssh_private_key else "",
            known_host=payload.ssh_known_host,
            remote_host=payload.host,
            remote_port=payload.port,
        )

        try:
            async with open_tunnel(tunnel_cfg) as local_port:
                # 重組 mysql_url 指到 tunnel 出口
                effective_url = _compose_mysql_url(
                    "127.0.0.1", local_port,
                    payload.username, payload.password, payload.database,
                )
                report = await run_migration(
                    session,
                    mysql_url=effective_url,
                    on_conflict=payload.on_conflict,
                    dry_run=payload.dry_run,
                )
        except SSHHostKeyMismatch as exc:
            raise HTTPException(status_code=409, detail={
                "error": "host_key_mismatch",
                "expected": exc.expected,
                "actual": exc.actual,
                "hint": "重新呼叫 /ssh-fingerprint 確認新 fingerprint 並再次提交",
            }) from exc
        except SSHTunnelError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    else:
        # ── 直連 ──
        report = await run_migration(
            session,
            mysql_url=mysql_url,
            on_conflict=payload.on_conflict,
            dry_run=payload.dry_run,
        )

    # A09：audit diff 不含敏感欄位
    redacted = {
        "mysql_host": payload.host,
        "mysql_port": payload.port,
        "mysql_db": payload.database,
        "via_ssh": bool(payload.ssh_host),
        "ssh_host": payload.ssh_host,
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
