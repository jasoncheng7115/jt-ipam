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
            k: ("***REDACTED***" if k.lower() in _REDACTED_KEYS or k.lower().endswith("_api_key")
                else _redact(v))
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


def _pending_last_hash(session: AsyncSession) -> bytes | None:
    """同一交易內尚未 flush 的稽核記錄中，最後寫入的那一筆的 this_hash。

    正式 session 是 `autoflush=False`，所以同一交易連寫多筆時（批次刪除是典型），
    後面幾筆用 SELECT 是看不到前面幾筆的 —— 於是它們全部接到同一個前置雜湊，
    鏈就此斷掉。prod 上因此累積了 28 個斷點。改成先看自己交易裡的待寫入項目，
    比強制 flush 安全：不會連帶把呼叫端還沒準備好的其他物件一起寫出去。
    """
    from app.models.audit import AuditLog

    pending = [o for o in session.new if isinstance(o, AuditLog)]
    if not pending:
        return None
    return max(pending, key=lambda o: getattr(o, "_chain_seq", 0)).this_hash


async def _get_prev_hash(session: AsyncSession) -> bytes:
    """取出最後一筆的 this_hash；空表時用 genesis。

    先看同一交易內待寫入的（`_pending_last_hash`），再看資料庫。
    """
    inflight = _pending_last_hash(session)
    if inflight is not None:
        return inflight
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
    # 交易內序號：同一交易可能連寫多筆，`_pending_last_hash` 靠它決定誰是最後一筆
    seq = session.info.get("_audit_seq", 0) + 1
    session.info["_audit_seq"] = seq
    entry._chain_seq = seq        # 只在同一交易內用的暫時屬性
    session.add(entry)

    # best-effort 轉送到 Graylog（syslog / CEF / GELF）；任何錯誤都不影響主交易。
    # 連同雜湊一起送：外部的記錄就是一份獨立的鏈，可以拿來比對資料庫裡的（2026-10-09 起）
    try:
        from app.services.audit_forward import maybe_forward
        await maybe_forward(session, {**record, "this_hash": this_hash.hex(), "prev_hash": prev.hex()})
    except Exception:
        pass


async def verify_chain(
    session: AsyncSession, *, limit: int | None = None,
    after_id: int | None = None, expected_prev: str | None = None,
) -> tuple[bool, int | None]:
    """驗證鏈；回傳 (是否完整, 第一個出錯的 audit id)。

    管理 / 排程定期執行；發現 false 立即發告警。

    `after_id` + `expected_prev`：**增量驗證**。從上次錨定的位置往後驗即可，
    不必每輪重走整條鏈（實機已有數千筆，且只會愈長）。兩者要一起給——
    只給 after_id 卻不給起始雜湊，等於從一個未經驗證的點開始信任。
    """
    from app.models.audit import AuditLog

    expected_prev = expected_prev or _genesis_hash()
    # 一批一批往後驗（2026-09-30 大量資料測試）：以前一次把整條鏈載進記憶體 —— 第一次驗證或錨定檔
    # 遺失時，跑了幾年、上百萬筆帶 JSONB 的稽核記錄會一口氣吃掉好幾 GB
    last = after_id if after_id is not None else None
    remaining = limit
    while True:
        size = _VERIFY_BATCH if remaining is None else min(_VERIFY_BATCH, remaining)
        if size <= 0:
            return True, None
        stmt = select(AuditLog).order_by(AuditLog.id.asc()).limit(size)
        if last is not None:
            stmt = stmt.where(AuditLog.id > last)
        rows = list((await session.execute(stmt)).scalars())
        if not rows:
            return True, None
        ok, bad, expected_prev = _verify_rows(rows, expected_prev)
        if not ok:
            return False, bad
        last = rows[-1].id
        for r in rows:
            session.expunge(r)
        if remaining is not None:
            remaining -= len(rows)
        if len(rows) < size:
            return True, None


#: verify_chain 每批驗幾筆
_VERIFY_BATCH = 5000


def _verify_rows(rows: list[Any], expected_prev: Any) -> tuple[bool, int | None, Any]:
    for row in rows:
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
            return False, row.id, expected_prev
        if _hash(expected_prev, canonical) != row.this_hash:
            return False, row.id, expected_prev
        expected_prev = row.this_hash
    return True, None, expected_prev
