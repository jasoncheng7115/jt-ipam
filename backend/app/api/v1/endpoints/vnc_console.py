"""IP 位址 VNC 連線管理：ticket 換發 + WebSocket↔VNC 橋接（瀏覽器 canvas 前端）。

完全比照 SSH/RDP（ssh_console / rdp_console）的安全架構：ticket 單次用 + WS 兩處重查
can_use_vnc（deny-by-default）、密碼用完即丟不落 DB、目標 host 鎖死該 IP（防 SSRF）、稽核開關場。

相依：與 RDP 同一個選用 aardwolf（`VNCConnection` 介面與 RDP 相同）。未安裝 → VNC_AVAILABLE=False。

aardwolf 0.2.13 的 VNC `send_mouse` 有 bug（按鍵 mask 反向、無滾輪、無 steps 參數）→ 本模組於
import 時 monkeypatch 一個正確的 RFB PointerEvent 實作（維護累積 button mask；滾輪用 button 4/5
一次按放）。VNC 桌面尺寸由「伺服器」決定（connect 後讀 conn.width/height），非用戶端指定解析度。
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import secrets
import uuid
from datetime import UTC, datetime
from struct import pack
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
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
from app.services.permission import can_use_vnc

try:  # 與 RDP 同一個選用 aardwolf
    from aardwolf.commons.factory import RDPConnectionFactory
    from aardwolf.commons.iosettings import RDPIOSettings
    from aardwolf.commons.queuedata import RDPDATATYPE
    from aardwolf.commons.queuedata.constants import MOUSEBUTTON, VIDEO_FORMAT
    from aardwolf.vncconnection import VNCConnection

    VNC_AVAILABLE = True
except Exception:  # 任何 import 問題都視為未安裝
    VNC_AVAILABLE = False

router = APIRouter(prefix="/addresses", tags=["vnc"])

_TICKET_TTL = 60
_CONNECT_TIMEOUT = 20.0
_CLIENT_IDLE_TIMEOUT = 60.0
_DEFAULT_PORT = 5900

# VNC（RFB）鍵盤用 X11 keysym（非 PC scancode）。特殊鍵對應表：
_VNC_KEYSYMS: dict[str, int] = {
    "Enter": 0xFF0D, "Backspace": 0xFF08, "Tab": 0xFF09, "Escape": 0xFF1B,
    "Delete": 0xFFFF, "Home": 0xFF50, "End": 0xFF57, "PageUp": 0xFF55,
    "PageDown": 0xFF56, "Insert": 0xFF63, "ArrowUp": 0xFF52, "ArrowDown": 0xFF54,
    "ArrowLeft": 0xFF51, "ArrowRight": 0xFF53,
    "Control": 0xFFE3, "Shift": 0xFFE1, "Alt": 0xFFE9, "Meta": 0xFFEB, " ": 0x20,  # Meta = Super_L (Win/⌘)
    "F1": 0xFFBE, "F2": 0xFFBF, "F3": 0xFFC0, "F4": 0xFFC1, "F5": 0xFFC2, "F6": 0xFFC3,
    "F7": 0xFFC4, "F8": 0xFFC5, "F9": 0xFFC6, "F10": 0xFFC7, "F11": 0xFFC8, "F12": 0xFFC9,
}

_active_sessions = 0


def _ticket_key(ticket: str) -> str:
    return f"vnc:tk:{ticket}"


# ── 修正 aardwolf VNC send_mouse（正確 RFB PointerEvent）──────────────────────
if VNC_AVAILABLE:
    _VNC_BTN_BITS = {
        MOUSEBUTTON.MOUSEBUTTON_LEFT: 1,
        MOUSEBUTTON.MOUSEBUTTON_MIDDLE: 2,
        MOUSEBUTTON.MOUSEBUTTON_RIGHT: 4,
    }

    async def _vnc_send_mouse(self, button, x_pos, y_pos, is_pressed, steps=0):  # type: ignore[no-untyped-def]
        try:
            if x_pos < 0 or y_pos < 0:
                return True, None
            writer = getattr(self, "_VNCConnection__writer", None)
            if writer is None:
                return True, None
            mask = getattr(self, "_jt_btnmask", 0)
            if button in (MOUSEBUTTON.MOUSEBUTTON_WHEEL_UP, MOUSEBUTTON.MOUSEBUTTON_WHEEL_DOWN):
                wbit = 8 if button == MOUSEBUTTON.MOUSEBUTTON_WHEEL_UP else 16
                await writer.write(pack("!BBHH", 5, mask | wbit, x_pos, y_pos))
                await writer.write(pack("!BBHH", 5, mask, x_pos, y_pos))
                return True, None
            bit = _VNC_BTN_BITS.get(button)
            if bit is None:  # HOVER / move：只更新座標、維持目前 mask
                await writer.write(pack("!BBHH", 5, mask, x_pos, y_pos))
                return True, None
            mask = (mask | bit) if is_pressed else (mask & ~bit)
            self._jt_btnmask = mask
            await writer.write(pack("!BBHH", 5, mask, x_pos, y_pos))
            return True, None
        except Exception as e:
            return None, e

    async def _vnc_send_keysym(self, keysym, is_pressed):  # type: ignore[no-untyped-def]
        """正確的 RFB KeyEvent（msg 4 + down flag + u32 keysym）。"""
        try:
            writer = getattr(self, "_VNCConnection__writer", None)
            if writer is None:
                return True, None
            await writer.write(pack("!BBxxI", 4, 1 if is_pressed else 0, int(keysym) & 0xFFFFFFFF))
            return True, None
        except Exception as e:
            return None, e

    async def _vnc_send_key_char(self, char, is_pressed):  # type: ignore[no-untyped-def]
        """字元→X11 keysym（Latin-1 直接用碼位；其餘走 0x01000000+unicode）。"""
        if not char:
            return True, None
        cp = ord(char[0])
        keysym = cp if cp < 0x100 else (0x01000000 + cp)
        return await _vnc_send_keysym(self, keysym, is_pressed)

    if not getattr(VNCConnection, "_jt_patched", False):
        VNCConnection.send_mouse = _vnc_send_mouse  # type: ignore[method-assign]
        VNCConnection.send_keysym = _vnc_send_keysym  # type: ignore[attr-defined]
        VNCConnection.send_key_char = _vnc_send_key_char  # type: ignore[method-assign]
        VNCConnection._jt_patched = True


def _mouse_button(b: int) -> Any:
    return {0: MOUSEBUTTON.MOUSEBUTTON_LEFT, 1: MOUSEBUTTON.MOUSEBUTTON_RIGHT,
            2: MOUSEBUTTON.MOUSEBUTTON_MIDDLE}.get(int(b), MOUSEBUTTON.MOUSEBUTTON_LEFT)


@router.post("/{address_id}/vnc/ticket")
async def issue_vnc_ticket(
    address_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    if not VNC_AVAILABLE:
        raise HTTPException(status_code=503, detail="VNC 功能未安裝（缺 aardwolf 選用相依）")
    from app.core.rate_limit import limit_per_ip

    await limit_per_ip(request, name="vnc")

    ip = await session.get(IPAddress, address_id)
    if ip is None:
        raise HTTPException(status_code=404, detail="Address not found")
    if not await can_use_vnc(session, user=user, ip=ip):
        raise HTTPException(status_code=403, detail="無 VNC 連線權限")

    saved = (await session.execute(
        select(SSHCredential.id).where(
            SSHCredential.owner_user_id == user.id,
            SSHCredential.protocol == "vnc",
            (SSHCredential.target_ip_id == ip.id) | (SSHCredential.target_ip_id.is_(None)),
        ).limit(1)
    )).first()

    ticket = secrets.token_urlsafe(32)
    payload = json.dumps({"user_id": str(user.id), "ip_id": str(ip.id)})
    await _redis_client().set(_ticket_key(ticket), payload, ex=_TICKET_TTL)

    return {
        "ticket": ticket,
        "ws_path": f"/api/v1/addresses/{ip.id}/vnc/ws",
        "default_port": _DEFAULT_PORT,
        "has_saved_creds": saved is not None,
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
        if data.get("ip_id") != str(address_id):
            return None
        return uuid.UUID(data["user_id"])
    except (ValueError, KeyError, TypeError):
        return None


async def _audit_vnc(
    *, actor_user_id: str, actor_ip: str | None, object_id: str,
    action: str, diff: dict[str, Any],
) -> None:
    async with SessionLocal() as s:
        await append_audit(
            s, actor_user_id=actor_user_id, actor_ip=actor_ip, actor_user_agent=None,
            object_type="ip", object_id=object_id, action=action, diff=diff, request_id=None,
        )
        await s.commit()


@router.websocket("/{address_id}/vnc/ws")
async def vnc_ws(websocket: WebSocket, address_id: uuid.UUID, ticket: str = "") -> None:
    global _active_sessions

    if not VNC_AVAILABLE:
        await websocket.close(code=4503)
        return

    user_id = await _redeem_ticket(ticket, address_id)
    if user_id is None:
        await websocket.close(code=4401)
        return

    async with SessionLocal() as s:
        user = await s.get(User, user_id)
        ip = await s.get(IPAddress, address_id)
        if user is None or not user.is_active or ip is None:
            await websocket.close(code=4403)
            return
        allowed = await can_use_vnc(s, user=user, ip=ip)
        host = str(ip.ip).split("/")[0]
    if not allowed:
        await websocket.close(code=4403)
        return

    await websocket.accept()
    actor_ip = websocket.client.host if websocket.client else None

    async def send(obj: dict[str, Any]) -> None:
        await websocket.send_text(json.dumps(obj))

    cap = get_settings().rdp_max_sessions
    if cap and _active_sessions >= cap:
        await send({"type": "error", "code": "too_many", "message": f"連線已達上限（{cap}）"})
        await websocket.close()
        return

    conn = None
    counted = False
    started: datetime | None = None
    try:
        cfg = json.loads(await websocket.receive_text())
        if cfg.get("type") != "config":
            await send({"type": "error", "code": "bad_config", "message": "缺少連線設定"})
            await websocket.close()
            return
        port = int(cfg.get("port") or _DEFAULT_PORT)
        if not (1 <= port <= 65535):
            await send({"type": "error", "code": "bad_config", "message": "連接埠須為 1–65535"})
            await websocket.close()
            return
        password = cfg.get("password") or ""
        credential_id = cfg.get("credential_id")

        used_cred_id: uuid.UUID | None = None
        if credential_id:
            from app.api.v1.endpoints.ssh_credentials import cred_aad
            async with SessionLocal() as s:
                try:
                    cred = await s.get(SSHCredential, uuid.UUID(str(credential_id)))
                except ValueError:
                    cred = None
                if (cred is None or cred.owner_user_id != user_id or cred.protocol != "vnc"
                        or (cred.target_ip_id is not None and str(cred.target_ip_id) != str(address_id))):
                    await send({"type": "error", "code": "cred_not_found", "message": "找不到可用的已存密碼"})
                    await websocket.close()
                    return
                used_cred_id = cred.id
                secrets_enc = dict(cred.secrets_enc or {})
            try:
                password = envelope_decrypt(secrets_enc["password"], aad=cred_aad(user_id, "password"))
            except Exception:
                await send({"type": "error", "code": "bad_key", "message": "已存密碼解密失敗"})
                await websocket.close()
                return
            async with SessionLocal() as s:
                c2 = await s.get(SSHCredential, used_cred_id)
                if c2 is not None:
                    c2.last_used_at = datetime.now(UTC)
                    await s.commit()

        await send({"type": "status", "state": "connecting"})
        io = RDPIOSettings()
        io.video_out_format = VIDEO_FORMAT.PNG
        io.clipboard_use_pyperclip = False

        if password:
            url = f"vnc+plain-password://{quote(password, safe='')}@{host}:{port}/?timeout={int(_CONNECT_TIMEOUT)}"
        else:
            url = f"vnc://{host}:{port}/?timeout={int(_CONNECT_TIMEOUT)}"
        del password

        factory = RDPConnectionFactory.from_url(url, io)
        conn = factory.create_connection_newtarget(host, io)
        try:
            async with asyncio.timeout(_CONNECT_TIMEOUT):
                _result, err = await conn.connect()
        except TimeoutError:
            await send({"type": "error", "code": "connect_failed", "message": "連線逾時"})
            await websocket.close()
            return
        if err is not None:
            await send({"type": "error", "code": "auth_failed",
                        "message": "連線/認證失敗（密碼錯誤或 VNC 設定）"})
            await websocket.close()
            return

        _active_sessions += 1
        counted = True
        started = datetime.now(UTC)
        # VNC 桌面尺寸由伺服器決定（ServerInit）→ 告知前端 canvas 尺寸
        width = int(getattr(conn, "width", 0) or 1024)
        height = int(getattr(conn, "height", 0) or 768)
        await _audit_vnc(
            actor_user_id=str(user_id), actor_ip=actor_ip, object_id=str(address_id),
            action="vnc.session_open",
            diff={"host": host, "port": port, "size": f"{width}x{height}",
                  "credential_id": str(used_cred_id) if used_cred_id else None},
        )
        await send({"type": "status", "state": "connected", "width": width, "height": height})

        await _bridge(websocket, conn, send)

    except WebSocketDisconnect:
        pass
    except Exception:
        with contextlib.suppress(Exception):
            await send({"type": "error", "code": "internal", "message": "連線發生未預期錯誤"})
    finally:
        if conn is not None:
            with contextlib.suppress(Exception):
                await conn.terminate()
        if counted:
            _active_sessions -= 1
            if started is not None:
                dur = (datetime.now(UTC) - started).total_seconds()
                with contextlib.suppress(Exception):
                    await _audit_vnc(
                        actor_user_id=str(user_id), actor_ip=actor_ip, object_id=str(address_id),
                        action="vnc.session_close", diff={"host": host, "duration_seconds": round(dur, 1)},
                    )
        with contextlib.suppress(Exception):
            await send({"type": "status", "state": "disconnected"})
            await websocket.close()


async def _bridge(websocket: WebSocket, conn: Any, send: Any) -> None:
    """雙向 pump：VNC 視訊→ws（PNG tile）、ws→send_mouse/send_key（已修正的 VNC 實作）。"""

    async def pump_out() -> None:
        with contextlib.suppress(Exception):
            while True:
                data = await conn.ext_out_queue.get()
                if data is None:
                    break
                if getattr(data, "type", None) == RDPDATATYPE.VIDEO and data.data:
                    await send({
                        "type": "img", "x": data.x, "y": data.y,
                        "w": data.width, "h": data.height,
                        "d": base64.b64encode(data.data).decode("ascii"),
                    })

    async def pump_in() -> None:
        with contextlib.suppress(WebSocketDisconnect, Exception):
            while True:
                try:
                    raw = await asyncio.wait_for(websocket.receive_text(), timeout=_CLIENT_IDLE_TIMEOUT)
                except TimeoutError:
                    break
                msg = json.loads(raw)
                t = msg.get("type")
                if t == "m":
                    x, y = int(msg.get("x", 0)), int(msg.get("y", 0))
                    if msg.get("wheel"):
                        btn = (MOUSEBUTTON.MOUSEBUTTON_WHEEL_UP if int(msg.get("dir", -1)) > 0
                               else MOUSEBUTTON.MOUSEBUTTON_WHEEL_DOWN)
                        await conn.send_mouse(btn, x, y, False)
                    elif msg.get("move"):
                        await conn.send_mouse(MOUSEBUTTON.MOUSEBUTTON_HOVER, x, y, False)
                    else:
                        await conn.send_mouse(_mouse_button(msg.get("b", 0)), x, y, bool(msg.get("p")))
                elif t == "k":
                    pressed = bool(msg.get("p"))
                    key = msg.get("key", "")
                    if key in _VNC_KEYSYMS:
                        await conn.send_keysym(_VNC_KEYSYMS[key], pressed)
                    else:
                        ch = msg.get("ch", "")
                        if len(ch) == 1:
                            await conn.send_key_char(ch, pressed)
                elif t == "ping":
                    await send({"type": "pong"})
                elif t == "close":
                    break

    out_task = asyncio.create_task(pump_out())
    in_task = asyncio.create_task(pump_in())
    _done, pending = await asyncio.wait({out_task, in_task}, return_when=asyncio.FIRST_COMPLETED)
    for p in pending:
        p.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
