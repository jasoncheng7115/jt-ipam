"""把稽核事件轉送到 Graylog（syslog / CEF / GELF；TCP / UDP）。

best-effort：任何錯誤都吞掉，不影響主流程（登入 / 異動）。實際送出走 thread executor，
不阻塞 event loop。設定在管理區「稽核轉送」卡，存於 system_settings。
"""

from __future__ import annotations

import asyncio
import json
import socket
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.system_config import AuditForwardConfig, get_audit_forward

_PRI = 13  # facility=user(1)*8 + severity=notice(5)


def _short(ev: dict[str, Any]) -> str:
    obj = ev.get("object_type") or ""
    oid = ev.get("object_id")
    return f"audit {ev.get('action')} {obj}{('/' + oid) if oid else ''}".strip()


def _gelf(host: str, ev: dict[str, Any]) -> bytes:
    msg: dict[str, Any] = {
        "version": "1.1",
        "host": host,
        "short_message": _short(ev),
        "level": 6,
        "_app": "jt-ipam",
        "_action": ev.get("action"),
        "_object_type": ev.get("object_type"),
        "_object_id": ev.get("object_id"),
        "_actor_user_id": ev.get("actor_user_id"),
        "_actor_ip": ev.get("actor_ip"),
        "_request_id": ev.get("request_id"),
        "_ts": ev.get("ts"),
    }
    if ev.get("diff"):
        msg["_diff"] = json.dumps(ev["diff"], ensure_ascii=False)
    return json.dumps({k: v for k, v in msg.items() if v is not None},
                      ensure_ascii=False).encode("utf-8")


def _kv(ev: dict[str, Any]) -> str:
    parts = [
        f"action={ev.get('action')}",
        f"object={ev.get('object_type')}/{ev.get('object_id')}",
        f"actor={ev.get('actor_user_id')}",
        f"src={ev.get('actor_ip')}",
        f"requestId={ev.get('request_id')}",
    ]
    return " ".join(parts)


def _syslog(host: str, ev: dict[str, Any]) -> bytes:
    # RFC5424: <PRI>1 TIMESTAMP HOST APP PROCID MSGID STRUCTURED-DATA MSG
    return (f"<{_PRI}>1 {ev.get('ts')} {host} jt-ipam - audit - {_kv(ev)}").encode()


def _cef(host: str, ev: dict[str, Any]) -> bytes:
    act = str(ev.get("action") or "event")
    ext = (f"rt={ev.get('ts')} suser={ev.get('actor_user_id')} src={ev.get('actor_ip')} "
           f"requestId={ev.get('request_id')} "
           f"cs1Label=object cs1={ev.get('object_type')}/{ev.get('object_id')}")
    body = f"CEF:0|JasonTools|jt-ipam|1|{act}|{act}|3|{ext}"
    return (f"<{_PRI}>1 {ev.get('ts')} {host} jt-ipam - audit - {body}").encode()


def _format(cfg: AuditForwardConfig, ev: dict[str, Any], host: str) -> bytes:
    if cfg.fmt == "gelf":
        return _gelf(host, ev)
    if cfg.fmt == "cef":
        return _cef(host, ev)
    return _syslog(host, ev)


def _send_sync(cfg: AuditForwardConfig, data: bytes) -> None:
    try:
        if cfg.protocol == "udp":
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(2.0)
            try:
                s.sendto(data, (cfg.host, cfg.port))
            finally:
                s.close()
        else:
            s = socket.create_connection((cfg.host, cfg.port), timeout=2.0)
            try:
                # GELF over TCP 以 NULL 分隔；syslog/cef 以換行
                s.sendall(data + (b"\x00" if cfg.fmt == "gelf" else b"\n"))
            finally:
                s.close()
    except Exception:
        pass  # best-effort


async def _do(cfg: AuditForwardConfig, ev: dict[str, Any]) -> None:
    if not cfg.enabled or not cfg.host:
        return
    try:
        host = socket.gethostname()
        await asyncio.to_thread(_send_sync, cfg, _format(cfg, ev, host))
    except Exception:
        pass


async def maybe_forward(session: AsyncSession, event: dict[str, Any]) -> None:
    """從 append_audit 呼叫：讀（快取的）設定，啟用就 fire-and-forget 送出。"""
    try:
        cfg = await get_audit_forward(session)
    except Exception:
        return
    if not cfg.enabled or not cfg.host:
        return
    try:
        asyncio.get_running_loop().create_task(_do(cfg, dict(event)))
    except RuntimeError:
        await _do(cfg, dict(event))


async def send_test(cfg: AuditForwardConfig) -> None:
    """管理區「測試」按鈕：送一筆測試事件（直接送、回報錯誤）。"""
    host = socket.gethostname()
    data = _format(cfg, {
        "ts": "test", "action": "audit_forward_test", "object_type": "system",
        "object_id": None, "actor_user_id": None, "actor_ip": None, "request_id": None,
    }, host)
    await asyncio.to_thread(_send_sync, cfg, data)
