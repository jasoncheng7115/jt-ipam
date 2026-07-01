"""IP 位址 PVE 主控台連線管理：ticket 換發 + WebSocket 位元組對接到 PVE vncwebsocket。

qemu VM → noVNC（瀏覽器 @novnc/novnc，RFB）；lxc CT → xterm（瀏覽器 xterm.js + PVE term 協定）。
後端只做「位元組對接」（不解析 RFB / term）：瀏覽器 WS ←→ 後端 ←→ PVE vncwebsocket WS。

安全（比照 ssh/rdp/vnc）：
- ticket 單次用（Redis getdel，TTL 60s）；WS 兩處重查 can_use_novnc（deny-by-default）。
- 鑄票時就用「使用者輸入或金庫存的 PVE 帳密」向 PVE 登入 + vncproxy/termproxy；PVE 端權限親自把關
  （權限不足→403→連不上）。帳密用完即丟、不落 DB/log/不回前端。
- PVE host 由 ProxmoxInstance.api_url 決定（管理員設定），非使用者提供 → 不是開放代理。
- 換到的 PVEAuthCookie + vncticket 短存 Redis（60s）僅供本次 WS 用。

相依：`websockets`（對 PVE 開 client WS；httpx 不支援 WS）。未安裝 → NOVNC_AVAILABLE=False。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser
from app.core.audit import append_audit
from app.core.config import get_settings
from app.core.db import SessionLocal, get_session
from app.core.rate_limit import _redis_client
from app.core.security import envelope_decrypt
from app.models.address import IPAddress
from app.models.ssh_credential import SSHCredential
from app.models.user import User
from app.services import pve_console as pvec
from app.services.permission import can_use_novnc

try:
    import websockets

    NOVNC_AVAILABLE = True
except Exception:
    NOVNC_AVAILABLE = False

router = APIRouter(prefix="/addresses", tags=["novnc"])

_TICKET_TTL = 60
_active_sessions = 0


def _ticket_key(t: str) -> str:
    return f"novnc:tk:{t}"


class NovncTicketIn(BaseModel):
    username: str | None = None
    password: str | None = None
    realm: str | None = None
    credential_id: uuid.UUID | None = None


async def _resolve_creds(
    session: AsyncSession, user: User, ip: IPAddress, payload: NovncTicketIn,
) -> tuple[str, str]:
    """回 (pve_username〔user@realm〕, password)。優先用金庫憑證，否則用輸入帳密。"""
    if payload.credential_id is not None:
        from app.api.v1.endpoints.ssh_credentials import cred_aad
        cred = await session.get(SSHCredential, payload.credential_id)
        if (cred is None or cred.owner_user_id != user.id or cred.protocol != "pve"
                or (cred.target_ip_id is not None and cred.target_ip_id != ip.id)):
            raise HTTPException(status_code=404, detail="找不到 PVE 憑證")
        secrets_enc = dict(cred.secrets_enc or {})
        password = envelope_decrypt(secrets_enc["password"], aad=cred_aad(user.id, "password"))
        return cred.username, password
    if not payload.username or not payload.password:
        raise HTTPException(status_code=400, detail="缺少 PVE 帳號或密碼")
    return pvec.normalize_username(payload.username, payload.realm), payload.password


@router.post("/{address_id}/novnc/ticket")
async def issue_novnc_ticket(
    address_id: uuid.UUID,
    payload: NovncTicketIn,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    if not NOVNC_AVAILABLE:
        raise HTTPException(status_code=503, detail="PVE 主控台功能未安裝（缺 websockets 相依）")
    from app.core.rate_limit import limit_per_ip

    await limit_per_ip(request, name="novnc")

    ip = await session.get(IPAddress, address_id)
    if ip is None:
        raise HTTPException(status_code=404, detail="Address not found")
    if not await can_use_novnc(session, user=user, ip=ip):
        raise HTTPException(status_code=403, detail="無 PVE 主控台連線權限")
    target = await pvec.resolve_pve_target(session, ip)
    if target is None:
        raise HTTPException(status_code=409, detail="此 IP 未對應到 Proxmox VE 的 VM/CT")

    pve_user, password = await _resolve_creds(session, user, ip, payload)
    # 用使用者帳密登入 PVE → vncproxy/termproxy（權限不足在這裡就擋下）
    try:
        pve_ticket, csrf = await pvec.pve_login(target.base_url, pve_user, password, target.verify_tls)
        vncticket, port = await pvec.pve_console_proxy(target, pve_ticket, csrf)
    except pvec.PveConsoleError as e:
        raise HTTPException(status_code=e.http_status, detail=str(e)) from e
    finally:
        del password

    # 可選擇記住帳密（比照 ssh/rdp/vnc，存進金庫 protocol='pve'）——透過既有 /ssh-credentials API 處理，
    # 這裡不再重複；ticket 端點只負責建立連線。

    saved = (await session.execute(
        select(SSHCredential.id).where(
            SSHCredential.owner_user_id == user.id,
            SSHCredential.protocol == "pve",
            (SSHCredential.target_ip_id == ip.id) | (SSHCredential.target_ip_id.is_(None)),
        ).limit(1)
    )).first()

    ticket = secrets.token_urlsafe(32)
    payload_json = json.dumps({
        "user_id": str(user.id), "ip_id": str(ip.id),
        "kind": target.kind, "base_url": target.base_url, "node": target.node,
        "vmid": target.vmid, "port": port, "vncticket": vncticket,
        "pve_cookie": pve_ticket, "verify_tls": target.verify_tls,
        "pve_user": pve_user,
    })
    await _redis_client().set(_ticket_key(ticket), payload_json, ex=_TICKET_TTL)

    return {
        "ticket": ticket,
        "ws_path": f"/api/v1/addresses/{ip.id}/novnc/ws",
        "kind": target.kind,                 # vm → noVNC / ct → xterm
        "vnc_password": vncticket,           # 瀏覽器 noVNC 的 RFB 密碼 / xterm term 認證用
        "pve_user": pve_user,                # xterm（lxc）term 認證 first message 用
        "has_saved_creds": saved is not None,
        "ttl": _TICKET_TTL,
    }


async def _redeem(ticket: str, address_id: uuid.UUID) -> dict[str, Any] | None:
    if not ticket:
        return None
    raw = await _redis_client().getdel(_ticket_key(ticket))
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if data.get("ip_id") != str(address_id):
            return None
        return data
    except (ValueError, TypeError):
        return None


async def _audit(*, user_id: str, actor_ip: str | None, ip_id: str, action: str, diff: dict[str, Any]) -> None:
    async with SessionLocal() as s:
        await append_audit(
            s, actor_user_id=user_id, actor_ip=actor_ip, actor_user_agent=None,
            object_type="ip", object_id=ip_id, action=action, diff=diff, request_id=None,
        )
        await s.commit()


@router.websocket("/{address_id}/novnc/ws")
async def novnc_ws(websocket: WebSocket, address_id: uuid.UUID, ticket: str = "") -> None:
    global _active_sessions

    if not NOVNC_AVAILABLE:
        await websocket.close(code=4503)
        return
    data = await _redeem(ticket, address_id)
    if data is None:
        await websocket.close(code=4401)
        return

    # WS 重查權限（防 ticket 簽發後權限被收回）
    async with SessionLocal() as s:
        user = await s.get(User, uuid.UUID(data["user_id"]))
        ip = await s.get(IPAddress, address_id)
        if user is None or not user.is_active or ip is None or not await can_use_novnc(s, user=user, ip=ip):
            await websocket.close(code=4403)
            return

    # 接受瀏覽器 WS（noVNC/xterm 都用 binary 子協定）
    offered = websocket.scope.get("subprotocols") or []
    await websocket.accept(subprotocol="binary" if "binary" in offered else None)
    actor_ip = websocket.client.host if websocket.client else None

    cap = get_settings().rdp_max_sessions
    if cap and _active_sessions >= cap:
        await websocket.close(code=4429)
        return

    pve_url = pvec.pve_vncwebsocket_url(
        pvec.PveTarget(kind=data["kind"], node=data["node"], vmid=int(data["vmid"]),
                       cluster_name=None, base_url=data["base_url"], verify_tls=bool(data["verify_tls"])),
        int(data["port"]), data["vncticket"],
    )
    started = datetime.now(UTC)
    counted = False
    pve_ws = None
    try:
        pve_ws = await websockets.connect(
            pve_url,
            additional_headers={"Cookie": f"PVEAuthCookie={data['pve_cookie']}"},
            ssl=pvec.pve_ssl_context(bool(data["verify_tls"])),
            subprotocols=["binary"],
            max_size=None,
            open_timeout=15,
        )
        _active_sessions += 1
        counted = True
        await _audit(user_id=data["user_id"], actor_ip=actor_ip, ip_id=str(address_id),
                     action="novnc.session_open",
                     diff={"kind": data["kind"], "node": data["node"], "vmid": data["vmid"]})

        async def browser_to_pve() -> None:
            while True:
                msg = await websocket.receive()
                if msg.get("type") == "websocket.disconnect":
                    return
                buf = msg.get("bytes")
                if buf is None and msg.get("text") is not None:
                    buf = msg["text"].encode()
                if buf:
                    await pve_ws.send(buf)

        async def pve_to_browser() -> None:
            async for frame in pve_ws:
                await websocket.send_bytes(frame if isinstance(frame, (bytes, bytearray)) else frame.encode())

        await asyncio.gather(browser_to_pve(), pve_to_browser(), return_exceptions=True)
    except Exception:
        pass
    finally:
        if pve_ws is not None:
            with contextlib.suppress(Exception):
                await pve_ws.close()
        with contextlib.suppress(Exception):
            await websocket.close()
        if counted:
            _active_sessions -= 1
        dur = (datetime.now(UTC) - started).total_seconds()
        with contextlib.suppress(Exception):
            await _audit(user_id=data["user_id"], actor_ip=actor_ip, ip_id=str(address_id),
                         action="novnc.session_close", diff={"duration_seconds": round(dur, 1)})
