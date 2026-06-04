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

import base64
from typing import Annotated, Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field, SecretStr, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin
from app.core.audit import append_audit
from app.core.db import get_session
from app.core.security import decrypt_secret, encrypt_secret
from app.models.migration_mapping import PhpIPAMMigrationMapping
from app.models.system_setting import SystemSetting
from app.schemas.base import StrictModel

_MIG_CFG_KEY = "phpipam_migration"
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
    """MySQL 連線目標 + 選用 SSH tunnel。

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

    # 經 SSH tunnel 連 MySQL 的模式：
    # - "socket"：轉發到 phpIPAM 主機的 Unix socket（auth_socket plugin 可用；不用 MySQL 帳密）
    # - "tcp"：轉發 TCP 到 phpIPAM 主機的 127.0.0.1:3306（傳統，需 MySQL 帳密）
    mysql_via: Literal["socket", "tcp"] = "socket"
    mysql_socket_path: Annotated[str, Field(min_length=1, max_length=512)] = "/run/mysqld/mysqld.sock"

    on_conflict: Literal["skip", "overwrite"] = "skip"
    dry_run: bool = False

    @model_validator(mode="after")
    def _check(self) -> SyncRequest:
        if self.ssh_host:
            if not self.ssh_username:
                raise ValueError("ssh_host 給了就要給 ssh_username")
            # ssh_private_key 可留空 → 後端會改用已儲存的設定金鑰
        elif self.mysql_via == "socket":
            raise ValueError("mysql_via='socket' 必須搭配 SSH tunnel（要從遠端讀 socket）")
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


class MigrationConfig(StrictModel):
    """可儲存的 phpIPAM 連線設定（跨瀏覽器共用）。私鑰加密保存。"""
    mysql_via: Literal["socket", "tcp"] = "socket"
    mysql_socket_path: str = "/run/mysqld/mysqld.sock"
    host: str = "127.0.0.1"
    port: int = 3306
    username: str | None = None
    database: str = "phpipam"
    ssh_host: str | None = None
    ssh_port: int = 22
    ssh_username: str | None = None
    ssh_known_host: str | None = None
    # 寫入時可帶私鑰（加密存）；讀取時不回傳明文
    ssh_private_key: SecretStr | None = None


class MigrationConfigOut(StrictModel):
    mysql_via: str = "socket"
    mysql_socket_path: str = "/run/mysqld/mysqld.sock"
    host: str = "127.0.0.1"
    port: int = 3306
    username: str | None = None
    database: str = "phpipam"
    ssh_host: str | None = None
    ssh_port: int = 22
    ssh_username: str | None = None
    ssh_known_host: str | None = None
    has_private_key: bool = False


async def _load_cfg(session: AsyncSession) -> dict[str, Any]:
    row = await session.get(SystemSetting, _MIG_CFG_KEY)
    return dict(row.value) if row and isinstance(row.value, dict) else {}


async def _stored_private_key(session: AsyncSession) -> str | None:
    cfg = await _load_cfg(session)
    enc_b64 = cfg.get("key_enc")
    nonce_b64 = cfg.get("key_nonce")
    if not enc_b64 or not nonce_b64:
        return None
    try:
        return decrypt_secret(base64.b64decode(enc_b64), base64.b64decode(nonce_b64)).decode()
    except Exception:
        return None


@router.get("/config", response_model=MigrationConfigOut,
            dependencies=[Depends(require_admin)])
async def get_config(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MigrationConfigOut:
    cfg = await _load_cfg(session)
    return MigrationConfigOut(
        mysql_via=cfg.get("mysql_via", "socket"),
        mysql_socket_path=cfg.get("mysql_socket_path", "/run/mysqld/mysqld.sock"),
        host=cfg.get("host", "127.0.0.1"),
        port=cfg.get("port", 3306),
        username=cfg.get("username"),
        database=cfg.get("database", "phpipam"),
        ssh_host=cfg.get("ssh_host"),
        ssh_port=cfg.get("ssh_port", 22),
        ssh_username=cfg.get("ssh_username"),
        ssh_known_host=cfg.get("ssh_known_host"),
        has_private_key=bool(cfg.get("key_enc")),
    )


@router.put("/config", response_model=MigrationConfigOut,
            dependencies=[Depends(require_admin)])
async def put_config(
    payload: MigrationConfig,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MigrationConfigOut:
    row = await session.get(SystemSetting, _MIG_CFG_KEY)
    cur: dict[str, Any] = dict(row.value) if row and isinstance(row.value, dict) else {}
    cur.update({
        "mysql_via": payload.mysql_via,
        "mysql_socket_path": payload.mysql_socket_path,
        "host": payload.host, "port": payload.port,
        "username": payload.username, "database": payload.database,
        "ssh_host": payload.ssh_host, "ssh_port": payload.ssh_port,
        "ssh_username": payload.ssh_username, "ssh_known_host": payload.ssh_known_host,
    })
    # 私鑰：有給才更新（加密存）；沒給保留舊的
    if payload.ssh_private_key and payload.ssh_private_key.get_secret_value().strip():
        enc, nonce = encrypt_secret(payload.ssh_private_key.get_secret_value())
        cur["key_enc"] = base64.b64encode(enc).decode()
        cur["key_nonce"] = base64.b64encode(nonce).decode()
    if row is None:
        row = SystemSetting(key=_MIG_CFG_KEY, value=cur, updated_by=user.id)
        session.add(row)
    else:
        row.value = cur
        row.updated_by = user.id
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(row, "value")
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="migration", object_id=None, action="save_config",
        diff={"target": "phpipam_migration", "ssh_host": payload.ssh_host,
              "saved_key": bool(payload.ssh_private_key)},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return await get_config(session)


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


async def _execute_migration(
    sess: AsyncSession,
    payload: SyncRequest,
    *,
    actor_user_id_str: str,
    actor_ip: str | None,
    actor_ua: str | None,
    request_id: str | None,
) -> dict[str, Any]:
    """執行一次 migration（SSH tunnel 或直連）+ audit。回傳 report.to_dict()。"""
    # 組 mysql_url
    mysql_url = payload.mysql_url
    if not mysql_url:
        mysql_url = _compose_mysql_url(
            payload.host, payload.port,
            payload.username, payload.password, payload.database,
        )

    if payload.ssh_host:
        socket_mode = payload.mysql_via == "socket"
        tunnel_cfg = TunnelConfig(
            host=payload.ssh_host,
            port=payload.ssh_port,
            username=payload.ssh_username or "",
            private_key_pem=payload.ssh_private_key.get_secret_value() if payload.ssh_private_key else "",
            known_host=payload.ssh_known_host or "",
            remote_host=payload.host,
            remote_port=payload.port,
            remote_socket_path=payload.mysql_socket_path if socket_mode else None,
        )
        async with open_tunnel(tunnel_cfg) as local_port:
            effective_user = payload.username
            if socket_mode and not effective_user:
                effective_user = payload.ssh_username
            effective_url = _compose_mysql_url(
                "127.0.0.1", local_port,
                effective_user, payload.password, payload.database,
            )
            report = await run_migration(
                sess, mysql_url=effective_url,
                on_conflict=payload.on_conflict, dry_run=payload.dry_run,
            )
    else:
        report = await run_migration(
            sess, mysql_url=mysql_url,
            on_conflict=payload.on_conflict, dry_run=payload.dry_run,
        )

    redacted = {
        "mysql_host": payload.host,
        "mysql_port": payload.port,
        "mysql_db": payload.database,
        "via_ssh": bool(payload.ssh_host),
        "ssh_host": payload.ssh_host,
        "mysql_via": payload.mysql_via,
        "mysql_socket_path": (
            payload.mysql_socket_path if payload.mysql_via == "socket" else None
        ),
        "on_conflict": payload.on_conflict,
        "dry_run": payload.dry_run,
        "tables": {k: v.to_dict() for k, v in report.tables.items()},
        "error": report.error,
    }
    await append_audit(
        sess,
        actor_user_id=actor_user_id_str,
        actor_ip=actor_ip,
        actor_user_agent=actor_ua,
        object_type="phpipam_migration",
        object_id=None,
        action="sync",
        diff=redacted,
        request_id=request_id,
    )
    await sess.commit()
    return report.to_dict()


@router.post("/sync",
             dependencies=[Depends(require_admin)])
async def sync(
    payload: SyncRequest,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """執行 phpIPAM → jt-ipam 同步。

    - dry_run=True  → 同步等結果（讓 UI 立刻看到預覽）
    - dry_run=False → 走背景 task（不阻塞），回 task_id
    """
    from app.services.background_tasks import spawn_task

    actor_user_id_str = str(user.id)
    actor_user_id_uuid = user.id
    actor_ip = request.client.host if request.client else None
    actor_ua = request.headers.get("user-agent")
    request_id = getattr(request.state, "request_id", None)

    # 沒帶私鑰 / known_host → 用已儲存的設定補上（讓使用者免每次重貼）
    if payload.ssh_host:
        if not payload.ssh_private_key:
            stored = await _stored_private_key(session)
            if stored:
                payload.ssh_private_key = SecretStr(stored)
        if not payload.ssh_known_host:
            cfg = await _load_cfg(session)
            if cfg.get("ssh_known_host"):
                payload.ssh_known_host = cfg["ssh_known_host"]
        if not payload.ssh_private_key:
            raise HTTPException(
                status_code=422,
                detail="SSH private key missing: paste it in the form, or save it once in Settings.",
            )

    if payload.ssh_host and not payload.ssh_known_host:
        raise HTTPException(
            status_code=422,
            detail="ssh_known_host is required (call /migration/phpipam/ssh-fingerprint first to obtain it)",
        )

    # dry-run：同步等結果
    if payload.dry_run:
        try:
            return await _execute_migration(
                session, payload,
                actor_user_id_str=actor_user_id_str,
                actor_ip=actor_ip, actor_ua=actor_ua, request_id=request_id,
            )
        except SSHHostKeyMismatch as exc:
            raise HTTPException(status_code=409, detail={
                "error": "host_key_mismatch",
                "expected": exc.expected, "actual": exc.actual,
                "hint": "重新呼叫 /ssh-fingerprint 確認新 fingerprint 並再次提交",
            }) from exc
        except SSHTunnelError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    # 非 dry-run：走背景 task；payload 含 SecretStr 不能直接傳 closure 過 await 邊界？
    # 實際上 closure 會抓 outer scope 變數，asyncio.create_task 在同 process 內，
    # SecretStr 物件還活著沒問題（不跨 process）。
    payload_snapshot = payload

    async def _runner(sess: AsyncSession, _task) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        return await _execute_migration(
            sess, payload_snapshot,
            actor_user_id_str=actor_user_id_str,
            actor_ip=actor_ip, actor_ua=actor_ua, request_id=request_id,
        )

    task = await spawn_task(
        session=session,
        kind="phpipam.migration",
        target_type="phpipam",
        target_id=None,
        target_label=f"{payload.host}:{payload.port}/{payload.database}",
        actor_user_id=actor_user_id_uuid,
        runner=_runner,
    )
    return {"task_id": str(task.id), "status": task.status,
            "queued_at": task.queued_at.isoformat(),
            "dry_run": False,
            "hint": "phpIPAM 匯入已加入作業佇列，請至「作業」頁面查看進度"}
