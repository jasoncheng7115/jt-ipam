"""IP 位址 SSH 連線管理：ticket 換發 + WebSocket↔SSH 橋接（xterm.js 前端）。

安全設計（OWASP）：
- A01：ticket 與 WS 兩處都重查 `can_use_ssh`（deny-by-default）；看不到的 IP 不能連。
- A07/A09：ticket 單次用 + 60s TTL + 綁 user×ip；發放限流；session 開/關都寫稽核。
- 憑證（密碼/私鑰）只在連線過程存記憶體，用完即丟，**絕不寫 DB / 不記錄**。
- 目標主機固定為該 IP 記錄上的位址（不接受使用者指定 host）→ 防被當成通用 SSH/SSRF proxy。
- A02：host key 採 TOFU 信任後釘選（存 ip.ssh_host_key）；日後不符即警告 MITM。

WS 無法帶 Authorization header → 改用「先以 JWT 打 POST .../ssh/ticket 換 ticket，
再用 ?ticket= 開 WS」。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

import asyncssh
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser
from app.core.audit import append_audit
from app.core.db import SessionLocal, get_session
from app.core.rate_limit import _redis_client
from app.core.security import envelope_decrypt
from app.models.address import IPAddress
from app.models.device import Device
from app.models.ssh_credential import SSHCredential
from app.models.user import User
from app.schemas.address import IPAddressRead
from app.services.permission import (
    can_use_ssh,
    get_object_permission,
    has_permission,
    visible_ids,
)
from app.services.ssh_tunnel import (
    SSHHostKeyMismatch,
    _parse_pubkey_line,
    fetch_host_key,
    server_key_fingerprint_sha256,
)

router = APIRouter(prefix="/addresses", tags=["ssh"])

_TICKET_TTL = 60              # 秒；ticket 單次用、短壽
_CONNECT_TIMEOUT = 15.0       # SSH 連線逾時
_CLIENT_IDLE_TIMEOUT = 60.0   # WS 端 60s 無任何訊息（含 heartbeat）視為斷線
_READ_CHUNK = 4096


def _ticket_key(ticket: str) -> str:
    return f"ssh:tk:{ticket}"


@router.get("/ssh/targets", response_model=list[IPAddressRead])
async def list_ssh_targets(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[IPAddressRead]:
    """列出所有已啟用 SSH 且目前使用者可連線的 IP（連線管理頁用）。

    與 can_use_ssh 一致的 deny-by-default：admin 全部；否則限可見子網路，
    再依「對該子網路有 write」或「具 can_ssh 能力且至少 read」逐筆放行。
    """
    stmt = select(IPAddress).where(IPAddress.ssh_enabled.is_(True))
    if not user.is_admin:
        vis = await visible_ids(session, user=user, object_type="subnet")
        if vis is not None:
            if not vis:
                return []
            stmt = stmt.where(IPAddress.subnet_id.in_(vis))
    rows = (await session.execute(stmt)).scalars().all()

    # 逐 IP 過可連線（per-subnet 權限快取，避免重複查）
    perm_cache: dict[uuid.UUID, str] = {}
    kept: list[IPAddress] = []
    for ip in rows:
        if user.is_admin:
            kept.append(ip)
            continue
        lvl = perm_cache.get(ip.subnet_id)
        if lvl is None:
            lvl = await get_object_permission(
                session, user=user, object_type="subnet", object_id=ip.subnet_id
            )
            perm_cache[ip.subnet_id] = lvl
        if lvl == "none":
            continue
        if has_permission(lvl, "write") or user.can_ssh:
            kept.append(ip)

    # device 名稱批次帶上（清單顯示用）
    dev_ids = {ip.device_id for ip in kept if ip.device_id}
    dev_names: dict[uuid.UUID, str] = {}
    if dev_ids:
        drows = (await session.execute(
            select(Device.id, Device.name).where(Device.id.in_(dev_ids))
        )).all()
        dev_names = {d[0]: d[1] for d in drows}

    from app.services.os_precedence import effective_os
    out: list[IPAddressRead] = []
    for ip in kept:
        r = IPAddressRead.model_validate(ip)
        r.ssh_available = True
        r.device_name = dev_names.get(ip.device_id) if ip.device_id else None
        # OS 與 IP 詳細資料頁一致：依來源優先序解析有效值
        _os = await effective_os(session, ip)
        r.os_guess = _os["os_guess"]; r.os_family = _os["os_family"]; r.os_source = _os["os_source"]
        out.append(r)
    return out


@router.post("/{address_id}/ssh/ticket")
async def issue_ssh_ticket(
    address_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """換發短期一次性 ticket；之後用它開 WebSocket。"""
    from app.core.rate_limit import limit_per_ip

    await limit_per_ip(request, name="ssh")

    ip = await session.get(IPAddress, address_id)
    if ip is None:
        raise HTTPException(status_code=404, detail="Address not found")
    if not await can_use_ssh(session, user=user, ip=ip):
        # A01：不洩漏存在性差異 — 一律 403
        raise HTTPException(status_code=403, detail="無 SSH 連線權限")

    ticket = secrets.token_urlsafe(32)
    payload = json.dumps({"user_id": str(user.id), "ip_id": str(ip.id)})
    await _redis_client().set(_ticket_key(ticket), payload, ex=_TICKET_TTL)

    return {
        "ticket": ticket,
        "ws_path": f"/api/v1/addresses/{ip.id}/ssh/ws",
        "host_key_pinned": bool(ip.ssh_host_key),
        "default_port": 22,
        "ttl": _TICKET_TTL,
    }


async def _redeem_ticket(ticket: str, address_id: uuid.UUID) -> uuid.UUID | None:
    """單次取出 ticket（getdel）；回傳通過驗證的 user_id，否則 None。"""
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


async def _audit_ssh(
    *, actor_user_id: str, actor_ip: str | None, object_id: str,
    action: str, diff: dict[str, Any],
) -> None:
    """以獨立短交易寫一筆 SSH 稽核（不含任何憑證）。"""
    async with SessionLocal() as s:
        await append_audit(
            s,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            actor_user_agent=None,
            object_type="ip",
            object_id=object_id,
            action=action,
            diff=diff,
            request_id=None,
        )
        await s.commit()


async def _pin_host_key(address_id: uuid.UUID, known_host: str, *, actor_user_id: str, actor_ip: str | None) -> None:
    async with SessionLocal() as s:
        ip = await s.get(IPAddress, address_id)
        if ip is not None:
            ip.ssh_host_key = known_host
            await append_audit(
                s,
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                actor_user_agent=None,
                object_type="ip",
                object_id=str(address_id),
                action="ssh.hostkey_pin",
                diff={"fingerprint": server_key_fingerprint_sha256(_parse_pubkey_line(known_host))},
                request_id=None,
            )
            await s.commit()


def _strict_client_factory(known_host: str) -> type[asyncssh.SSHClient]:
    """回傳一個會嚴格比對釘選 host key 的 SSHClient（不符 → SSHHostKeyMismatch）。"""
    expected_fp = server_key_fingerprint_sha256(_parse_pubkey_line(known_host))

    class _StrictClient(asyncssh.SSHClient):
        def validate_host_public_key(self, host, addr, port, key):  # type: ignore[no-untyped-def]
            actual = key.export_public_key("openssh").decode("ascii").split()
            actual_fp = server_key_fingerprint_sha256(_parse_pubkey_line(f"{actual[0]} {actual[1]}"))
            if actual_fp != expected_fp:
                raise SSHHostKeyMismatch(expected_fp, actual_fp)
            return True

    return _StrictClient


@router.websocket("/{address_id}/ssh/ws")
async def ssh_ws(websocket: WebSocket, address_id: uuid.UUID, ticket: str = "") -> None:
    # 1) 驗 ticket（單次取出）
    user_id = await _redeem_ticket(ticket, address_id)
    if user_id is None:
        await websocket.close(code=4401)
        return

    # 2) 載入 user + ip，縱深重查權限
    async with SessionLocal() as s:
        user = await s.get(User, user_id)
        ip = await s.get(IPAddress, address_id)
        if user is None or not user.is_active or ip is None:
            await websocket.close(code=4403)
            return
        allowed = await can_use_ssh(s, user=user, ip=ip)
        host = str(ip.ip).split("/")[0]
        pinned = ip.ssh_host_key
    if not allowed:
        await websocket.close(code=4403)
        return

    await websocket.accept()
    actor_ip = websocket.client.host if websocket.client else None

    async def send(obj: dict[str, Any]) -> None:
        await websocket.send_text(json.dumps(obj))

    try:
        # 3) 收第一個設定訊息
        cfg = json.loads(await websocket.receive_text())
        if cfg.get("type") != "config":
            await send({"type": "error", "code": "bad_config", "message": "缺少連線設定"})
            await websocket.close()
            return
        username = (cfg.get("username") or "").strip()
        port = int(cfg.get("port") or 22)
        auth = cfg.get("auth")
        cols = int(cfg.get("cols") or 80)
        rows = int(cfg.get("rows") or 24)
        credential_id = cfg.get("credential_id")
        if not (1 <= port <= 65535):
            await send({"type": "error", "code": "bad_config", "message": "連接埠須為 1–65535"})
            await websocket.close()
            return

        # 4) 認證憑證（明文只在記憶體存活，用完即丟；前端只持 credential_id reference）
        from app.api.v1.endpoints.ssh_credentials import cred_aad
        connect_kw: dict[str, Any] = {}
        used_cred_id: uuid.UUID | None = None
        if credential_id:
            # 以已存憑證連線：owner-only + 目標相符；明文不離後端
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
                used_cred_id = cred.id
                username = cred.username
                auth = cred.auth_type
                secrets_enc = dict(cred.secrets_enc or {})
            try:
                if auth == "password":
                    connect_kw["password"] = envelope_decrypt(secrets_enc["password"], aad=cred_aad(user_id, "password"))
                else:
                    pk = envelope_decrypt(secrets_enc["private_key"], aad=cred_aad(user_id, "private_key"))
                    pp = (envelope_decrypt(secrets_enc["passphrase"], aad=cred_aad(user_id, "passphrase"))
                          if "passphrase" in secrets_enc else None)
                    connect_kw["client_keys"] = [asyncssh.import_private_key(pk, passphrase=pp)]
                    connect_kw["preferred_auth"] = ("publickey",)
                    del pk, pp
            except Exception:
                await send({"type": "error", "code": "bad_key", "message": "已存帳密解密 / 解析失敗"})
                await websocket.close()
                return
            # 標記最近使用
            async with SessionLocal() as s:
                c2 = await s.get(SSHCredential, used_cred_id)
                if c2 is not None:
                    c2.last_used_at = datetime.now(UTC)
                    await s.commit()
        else:
            if not username:
                await send({"type": "error", "code": "bad_config", "message": "帳號必填"})
                await websocket.close()
                return
            if auth == "password":
                connect_kw["password"] = cfg.get("password") or ""
            elif auth == "key":
                try:
                    connect_kw["client_keys"] = [
                        asyncssh.import_private_key(
                            cfg.get("private_key") or "", passphrase=cfg.get("passphrase") or None
                        )
                    ]
                except Exception:  # 私鑰格式 / passphrase 錯
                    await send({"type": "error", "code": "bad_key", "message": "私鑰無法解析（格式或 passphrase 錯誤）"})
                    await websocket.close()
                    return
                connect_kw["preferred_auth"] = ("publickey",)
            else:
                await send({"type": "error", "code": "bad_config", "message": "不支援的認證方式"})
                await websocket.close()
                return

        # 5) host key — TOFU：未釘選先取指紋給使用者確認再釘選
        known_host = pinned
        if not known_host:
            try:
                hk = await fetch_host_key(host, port=port)
            except Exception as exc:
                await send({"type": "error", "code": "connect_failed", "message": f"無法連線取得主機金鑰：{exc}"})
                await websocket.close()
                return
            await send({"type": "hostkey", "fingerprint": hk["fingerprint"]})
            ans = json.loads(await websocket.receive_text())
            if ans.get("type") != "hostkey_accept":
                await send({"type": "error", "code": "hostkey_rejected", "message": "已取消（未信任主機金鑰）"})
                await websocket.close()
                return
            known_host = hk["known_host"]
            await _pin_host_key(address_id, known_host, actor_user_id=str(user_id), actor_ip=actor_ip)

        # 6) 連線（嚴格比對已釘選的 host key）
        await send({"type": "status", "state": "connecting"})
        try:
            async with asyncio.timeout(_CONNECT_TIMEOUT):
                conn = await asyncssh.connect(
                    host,
                    port=port,
                    username=username,
                    client_factory=_strict_client_factory(known_host),
                    known_hosts=None,
                    agent_path=None,
                    # keepalive：目標端靜默斷線（斷電/拔線）約 45s 內偵測 → bridge 結束 → 前端顯示已斷
                    keepalive_interval=15,
                    keepalive_count_max=3,
                    **connect_kw,
                )
        except SSHHostKeyMismatch:
            await send({"type": "error", "code": "hostkey_mismatch",
                        "message": "主機金鑰與先前釘選不符，可能遭中間人攻擊（連線中止）"})
            await websocket.close()
            return
        except asyncssh.PermissionDenied:
            await send({"type": "error", "code": "auth_failed", "message": "認證失敗（帳號 / 密碼 / 金鑰錯誤）"})
            await websocket.close()
            return
        except (TimeoutError, asyncssh.Error, OSError) as exc:
            await send({"type": "error", "code": "connect_failed", "message": f"連線失敗：{exc}"})
            await websocket.close()
            return

        # 7) 開互動 shell + 雙向橋接
        started = datetime.now(UTC)
        await _audit_ssh(
            actor_user_id=str(user_id), actor_ip=actor_ip, object_id=str(address_id),
            action="ssh.session_open",
            diff={"host": host, "port": port, "username": username, "auth": auth,
                  "credential_id": str(used_cred_id) if used_cred_id else None},
        )
        async with conn:
            await send({"type": "status", "state": "connected"})
            async with conn.create_process(
                term_type="xterm-256color", term_size=(cols, rows),
                encoding="utf-8", errors="replace",
            ) as proc:
                await _bridge(websocket, proc, send)

        dur = (datetime.now(UTC) - started).total_seconds()
        await _audit_ssh(
            actor_user_id=str(user_id), actor_ip=actor_ip, object_id=str(address_id),
            action="ssh.session_close", diff={"host": host, "duration_seconds": round(dur, 1)},
        )
        with contextlib.suppress(Exception):
            await send({"type": "status", "state": "disconnected"})
            await websocket.close()

    except WebSocketDisconnect:
        return
    except Exception:  # 任何未預期錯誤都不可洩漏堆疊給前端
        with contextlib.suppress(Exception):
            await send({"type": "error", "code": "internal", "message": "連線發生未預期錯誤"})
            await websocket.close()


async def _bridge(websocket: WebSocket, proc: Any, send: Any) -> None:
    """雙向 pump：proc.stdout→ws、ws→proc.stdin / resize。任一端結束即收掉另一端。"""

    async def pump_out() -> None:
        # shell 輸出 → ws；任一端斷線/EOF 即結束（吞例外，避免未取回的 task 例外噪音）
        with contextlib.suppress(Exception):
            while True:
                data = await proc.stdout.read(_READ_CHUNK)
                if not data:
                    break
                await send({"type": "data", "data": data})

    async def pump_in() -> None:
        with contextlib.suppress(WebSocketDisconnect, Exception):
            while True:
                # 客戶端每 ~20s 會送 heartbeat（ping）；60s 內完全沒訊息＝客戶端已斷
                # → 結束 bridge、連帶關掉到目標的 SSH（不留 orphan session）
                try:
                    raw = await asyncio.wait_for(websocket.receive_text(), timeout=_CLIENT_IDLE_TIMEOUT)
                except TimeoutError:
                    break
                msg = json.loads(raw)
                t = msg.get("type")
                if t == "data":
                    proc.stdin.write(msg.get("data", ""))
                elif t == "resize":
                    proc.change_terminal_size(int(msg.get("cols", 80)), int(msg.get("rows", 24)))
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
