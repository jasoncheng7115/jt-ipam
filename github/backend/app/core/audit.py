"""SHA-256 異動鏈（OWASP A08 / A09）。

每筆 audit_log 含 prev_hash + this_hash；this_hash = sha256(prev_hash || canonical_json(record))。
寫入時取 PostgreSQL advisory lock 序列化，避免併發插入造成鏈錯亂。
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings

_AUDIT_LOCK_KEY: int = 0x4A_54_49_50_41_4D_4C_47  # ASCII "JTIPAMLG"

_REDACTED_KEYS: frozenset[str] = frozenset(
    {
        "password",
        "password_hash",
        "totp_secret",
        "totp_secret_enc",
        "api_key",
        "api_token",
        "secret",
        "client_secret",
        "private_key",
        "credentials",
        "snmp_community",
        "tsig_key",
        "encryption_key",
        "ciphertext",
        "nonce",
    }
)


def _redact(obj: Any) -> Any:
    """A09：避免敏感欄位寫入 audit log。"""
    if isinstance(obj, dict):
        return {
            k: ("***REDACTED***" if k.lower() in _REDACTED_KEYS else _redact(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact(x) for x in obj]
    return obj


def _canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
        ensure_ascii=False,
    ).encode("utf-8")


def _hash(prev: bytes, canonical: bytes) -> bytes:
    h = hashlib.sha256()
    h.update(prev)
    h.update(canonical)
    return h.digest()


def _genesis_hash() -> bytes:
    raw = get_settings().audit_chain_genesis.get_secret_value()
    return hashlib.sha256(raw.encode("utf-8")).digest()


async def _get_prev_hash(session: AsyncSession) -> bytes:
    """取出最後一筆的 this_hash；空表時用 genesis。"""
    from app.models.audit import AuditLog  # local import 避免循環

    stmt = select(AuditLog.this_hash).order_by(AuditLog.id.desc()).limit(1)
    result = await session.execute(stmt)
    last = result.scalar_one_or_none()
    return last if last is not None else _genesis_hash()


async def append_audit(
    session: AsyncSession,
    *,
    actor_user_id: str | None,
    actor_ip: str | None,
    actor_user_agent: str | None,
    object_type: str,
    object_id: str | None,
    action: str,
    diff: dict[str, Any] | None,
    request_id: str | None,
) -> None:
    """寫入一筆 audit_log；自動串接 SHA-256 鏈。

    呼叫方須保證已在交易中（外層 endpoint 的 unit-of-work）。
    """
    from app.models.audit import AuditLog  # local import 避免循環

    # 取 advisory lock 序列化鏈寫入（同一交易內釋放）
    await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _AUDIT_LOCK_KEY})

    prev = await _get_prev_hash(session)

    safe_diff = _redact(diff) if diff else None
    ts = datetime.now(UTC)
    record = {
        "ts": ts.isoformat(),
        "actor_user_id": actor_user_id,
        "actor_ip": actor_ip,
        "actor_user_agent": actor_user_agent,
        "object_type": object_type,
        "object_id": object_id,
        "action": action,
        "diff": safe_diff,
        "request_id": request_id,
    }
    canonical = _canonical_json(record)
    this_hash = _hash(prev, canonical)

    entry = AuditLog(
        ts=ts,
        actor_user_id=actor_user_id,
        actor_ip=actor_ip,
        actor_user_agent=actor_user_agent,
        object_type=object_type,
        object_id=object_id,
        action=action,
        diff=safe_diff,
        request_id=request_id,
        prev_hash=prev,
        this_hash=this_hash,
    )
    session.add(entry)


async def verify_chain(session: AsyncSession, *, limit: int | None = None) -> tuple[bool, int | None]:
    """驗證整條鏈；回傳 (是否完整, 第一個出錯的 audit id)。

    管理 / 排程定期執行；發現 false 立即發告警。
    """
    from app.models.audit import AuditLog

    stmt = select(AuditLog).order_by(AuditLog.id.asc())
    if limit is not None:
        stmt = stmt.limit(limit)
    result = await session.execute(stmt)

    expected_prev = _genesis_hash()
    for row in result.scalars():
        record = {
            "ts": row.ts.isoformat(),
            "actor_user_id": str(row.actor_user_id) if row.actor_user_id else None,
            "actor_ip": str(row.actor_ip) if row.actor_ip else None,
            "actor_user_agent": row.actor_user_agent,
            "object_type": row.object_type,
            "object_id": str(row.object_id) if row.object_id else None,
            "action": row.action,
            "diff": row.diff,
            "request_id": str(row.request_id) if row.request_id else None,
        }
        canonical = _canonical_json(record)
        if row.prev_hash != expected_prev:
            return False, row.id
        if _hash(expected_prev, canonical) != row.this_hash:
            return False, row.id
        expected_prev = row.this_hash
    return True, None
