"""BMC OOB主控台（IPMI SOL）：ticket 換發 + WebSocket↔SOL 橋接（xterm.js 前端）。

比照 SSH 連線管理：
- A01：ticket 與 WS 兩處都重查 can_use_bmc（deny-by-default）。
- A07/A09：ticket 單次用 + 60s TTL + 綁 user×ip；發放限流；session 開/關都寫稽核（不記密碼）。
- 傳輸：後端起 `ipmitool sol activate` 子行程（pty），雙向中繼 master_fd ↔ WebSocket（binary）。
- 非破壞：只做 SOL（鍵盤 + 文字畫面），不含電源/感測/開機覆寫。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import secrets
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser
from app.core.audit import append_audit
from app.core.db import SessionLocal, get_session
from app.core.rate_limit import _redis_client
from app.core.security import envelope_decrypt
from app.models.address import IPAddress
from app.models.ssh_credential import SSHCredential
from app.models.user import User
from app.services import bmc as bmc_svc
from app.services.permission import can_use_bmc

router = APIRouter(prefix="/addresses", tags=["bmc"])

_TICKET_TTL = 60


def _ticket_key(ticket: str) -> str:
    return f"bmc:tk:{ticket}"


@router.post("/{address_id}/bmc/ticket")
async def issue_bmc_ticket(
    address_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """換發短期一次性 ticket；之後用它開 WebSocket。"""
    from app.core.rate_limit import limit_per_ip

    await limit_per_ip(request, name="bmc")
    if not bmc_svc.bmc_available():
        raise HTTPException(status_code=503, detail="後端未安裝 IPMI 工具（ipmitool）")
    ip = await session.get(IPAddress, address_id)
    if ip is None:
        raise HTTPException(status_code=404, detail="Address not found")
    if not await can_use_bmc(session, user=user, ip=ip):
        raise HTTPException(status_code=403, detail="無 BMC 連線權限")

    ticket = secrets.token_urlsafe(32)
    payload = json.dumps({"user_id": str(user.id), "ip_id": str(ip.id)})
    await _redis_client().set(_ticket_key(ticket), payload, ex=_TICKET_TTL)
    return {
        "ticket": ticket,
        "ws_path": f"/api/v1/addresses/{ip.id}/bmc/ws",
        "ttl": _TICKET_TTL,
    }


async def _redeem_ticket(ticket: str, address_id: uuid.UUID) -> uuid.UUID | None:
    if not ticket:
        return None
    raw = await _redis_client().getdel(_ticket_key(ticket))
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if str(data.get("ip_id")) != str(address_id):
            return None
        return uuid.UUID(str(data["user_id"]))
    except (ValueError, KeyError, TypeError):
        return None


async def _audit_bmc(action: str, *, user_id: uuid.UUID, ip_id: uuid.UUID, actor_ip: str | None) -> None:
    async with SessionLocal() as s:
        with contextlib.suppress(Exception):
            await append_audit(
                s, actor_user_id=str(user_id), actor_ip=actor_ip, actor_user_agent=None,
                object_type="ip", object_id=str(ip_id), action=action, diff=None,
                request_id=None,
            )
            await s.commit()


@router.websocket("/{address_id}/bmc/ws")
async def bmc_ws(websocket: WebSocket, address_id: uuid.UUID, ticket: str = "") -> None:
    user_id = await _redeem_ticket(ticket, address_id)
    if user_id is None:
        await websocket.close(code=4401)
        return
    # 重查權限 + 取 BMC IP 字串
    async with SessionLocal() as s:
        ip = await s.get(IPAddress, address_id)
        user = await s.get(User, user_id)
        if ip is None or user is None:
            await websocket.close(code=4403)
            return
        if not await can_use_bmc(s, user=user, ip=ip):
            await websocket.close(code=4403)
            return
        bmc_ip = str(ip.ip).split("/")[0]

    if not bmc_svc.bmc_available():
        await websocket.close(code=1011)
        return

    await websocket.accept()
    actor_ip = websocket.client.host if websocket.client else None

    async def send(obj: dict[str, Any]) -> None:
        await websocket.send_text(json.dumps(obj))

    # 1) 收設定（憑證：金庫 credential_id 或臨時 username/password）
    try:
        cfg = json.loads(await websocket.receive_text())
    except (WebSocketDisconnect, json.JSONDecodeError):
        await websocket.close()
        return
    if cfg.get("type") != "config":
        await send({"type": "error", "code": "bad_config", "message": "缺少連線設定"})
        await websocket.close()
        return

    username = (cfg.get("username") or "").strip()
    password = ""
    credential_id = cfg.get("credential_id")
    if credential_id:
        from app.api.v1.endpoints.ssh_credentials import cred_aad
        async with SessionLocal() as s:
            try:
                cred = await s.get(SSHCredential, uuid.UUID(str(credential_id)))
            except ValueError:
                cred = None
            if (cred is None or cred.owner_user_id != user_id
                    or (cred.target_ip_id is not None and str(cred.target_ip_id) != str(address_id))):
                await send({"type": "error", "code": "cred_not_found", "message": "找不到可用的已存帳密"})
                await websocket.close()
                return
            username = cred.username
            try:
                password = envelope_decrypt(dict(cred.secrets_enc or {})["password"],
                                            aad=cred_aad(user_id, "password"))
            except Exception:
                await send({"type": "error", "code": "bad_key", "message": "已存帳密解密失敗"})
                await websocket.close()
                return
            cred.last_used_at = datetime.now(UTC)
            await s.commit()
    else:
        password = cfg.get("password") or ""
    if not username or not password:
        await send({"type": "error", "code": "bad_config", "message": "帳號與密碼必填"})
        await websocket.close()
        return

    # 2) 自我檢查（cipher 回退 / SOL 是否啟用）
    await send({"type": "status", "state": "connecting"})
    chk = await bmc_svc.self_check(bmc_ip, username, password)
    if not chk["ok"]:
        await send({"type": "error", "code": "connect_failed", "message": f"連線失敗：{chk['error']}"})
        await websocket.close()
        return
    if not chk["sol_enabled"]:
        await send({"type": "error", "code": "sol_disabled",
                    "message": "此 BMC 的 SOL 未啟用（請在 BMC/BIOS 啟用 Serial-over-LAN）"})
        await websocket.close()
        return
    cipher = int(cfg.get("cipher") or chk["cipher"])

    # 3) 釋放殘留 SOL session（單一 session）後起 pty 子行程
    await bmc_svc.deactivate_sol(bmc_ip, username, password, cipher)
    proc, master = bmc_svc.spawn_sol(bmc_ip, username, password, cipher)
    del password
    await _audit_bmc("bmc.session_open", user_id=user_id, ip_id=address_id, actor_ip=actor_ip)
    await send({"type": "status", "state": "connected", "cipher": cipher, "vendor": chk["vendor"]})

    loop = asyncio.get_event_loop()

    async def pump_out() -> None:
        while True:
            data = await loop.run_in_executor(None, _safe_read, master)
            if not data:
                break
            await websocket.send_bytes(data)

    async def pump_in() -> None:
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            b = msg.get("bytes")
            if b is None and msg.get("text") is not None:
                b = msg["text"].encode("utf-8", "replace")
            if b:
                os.write(master, b)

    try:
        _done, pending = await asyncio.wait(
            {asyncio.create_task(pump_out()), asyncio.create_task(pump_in())},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
    except WebSocketDisconnect:
        pass
    finally:
        with contextlib.suppress(Exception):
            proc.terminate()
        with contextlib.suppress(Exception):
            os.close(master)
        with contextlib.suppress(Exception):
            proc.wait(timeout=3)
        with contextlib.suppress(Exception):
            proc.kill()
        await _audit_bmc("bmc.session_close", user_id=user_id, ip_id=address_id, actor_ip=actor_ip)
        with contextlib.suppress(Exception):
            await websocket.close()


def _safe_read(fd: int) -> bytes:
    try:
        return os.read(fd, 4096)
    except OSError:
        return b""
