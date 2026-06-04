"""讀 system_settings table（admin UI 設定）+ env 預設值合併。

對 ai.py 之類消費者：呼叫 get_llm_config(session) → 拿到完整 dict，
DB 有設就用 DB，否則用 env。

有簡單 60s in-process cache 避免每次 LLM call 都 hit DB；改寫時主動 bump 版本。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.system_setting import SystemSetting

LLM_KEY = "llm"
_TTL_SEC = 60.0


@dataclass
class LLMConfig:
    enabled: bool
    url: str
    embedding_model: str
    chat_model: str
    timeout: float


_cache: dict[str, tuple[float, LLMConfig]] = {}


def _bust() -> None:
    _cache.pop(LLM_KEY, None)


async def get_llm_config(session: AsyncSession) -> LLMConfig:
    now = time.monotonic()
    cached = _cache.get(LLM_KEY)
    if cached and now - cached[0] < _TTL_SEC:
        return cached[1]

    s = get_settings()
    # env 預設
    cfg = LLMConfig(
        enabled=s.ollama_enabled,
        url=s.ollama_url,
        embedding_model=s.ollama_embedding_model,
        chat_model=s.ollama_chat_model,
        timeout=s.ollama_timeout,
    )
    row = await session.get(SystemSetting, LLM_KEY)
    if row and isinstance(row.value, dict):
        v = row.value
        if "enabled" in v and isinstance(v["enabled"], bool):
            cfg.enabled = v["enabled"]
        if v.get("url"):
            cfg.url = str(v["url"])
        if v.get("embedding_model"):
            cfg.embedding_model = str(v["embedding_model"])
        if v.get("chat_model"):
            cfg.chat_model = str(v["chat_model"])
        if v.get("timeout") is not None:
            try:
                cfg.timeout = float(v["timeout"])
            except (ValueError, TypeError):
                pass

    _cache[LLM_KEY] = (now, cfg)
    return cfg


async def set_llm_config(
    session: AsyncSession,
    *,
    enabled: bool | None = None,
    url: str | None = None,
    embedding_model: str | None = None,
    chat_model: str | None = None,
    timeout: float | None = None,
    updated_by_user_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    row = await session.get(SystemSetting, LLM_KEY)
    if row is None:
        row = SystemSetting(key=LLM_KEY, value={}, updated_by=updated_by_user_id)
        session.add(row)
    current: dict[str, Any] = dict(row.value or {})
    if enabled is not None: current["enabled"] = bool(enabled)
    if url is not None: current["url"] = str(url).strip().rstrip("/")
    if embedding_model is not None: current["embedding_model"] = embedding_model.strip()
    if chat_model is not None: current["chat_model"] = chat_model.strip()
    if timeout is not None: current["timeout"] = float(timeout)
    row.value = current
    row.updated_by = updated_by_user_id
    # JSONB 變更 SQLAlchemy 對 dict in-place 不會偵測 — flag_modified 保險
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(row, "value")
    await session.commit()
    _bust()
    return current


# ─────────────────── AI chat 歷程保留設定 ───────────────────
AI_CHAT_KEY = "ai_chat"
_DEFAULT_RETENTION_DAYS = 90


async def get_ai_chat_retention_days(session: AsyncSession) -> int:
    """AI chat 歷程保留天數；0 = 永久保留。預設 90 天。"""
    row = await session.get(SystemSetting, AI_CHAT_KEY)
    if row and isinstance(row.value, dict):
        v = row.value.get("retention_days")
        if isinstance(v, int) and v >= 0:
            return v
    return _DEFAULT_RETENTION_DAYS


async def set_ai_chat_retention_days(
    session: AsyncSession, *, days: int, updated_by_user_id: uuid.UUID | None = None,
) -> int:
    days = max(0, int(days))
    row = await session.get(SystemSetting, AI_CHAT_KEY)
    if row is None:
        row = SystemSetting(key=AI_CHAT_KEY, value={}, updated_by=updated_by_user_id)
        session.add(row)
    current = dict(row.value or {})
    current["retention_days"] = days
    row.value = current
    row.updated_by = updated_by_user_id
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(row, "value")
    await session.commit()
    return days


# ─────────────────── Graylog DSV 查表（lookup table adapter）───────────────────

GRAYLOG_DSV_KEY = "graylog_dsv"


async def get_graylog_dsv(session: AsyncSession) -> dict[str, Any]:
    """Graylog DSV 查表設定：enabled / token / fmt(csv|tsv) / path(URL slug)。"""
    row = await session.get(SystemSetting, GRAYLOG_DSV_KEY)
    v = dict(row.value) if (row and isinstance(row.value, dict)) else {}
    return {
        "enabled": bool(v.get("enabled", False)),
        "token": str(v.get("token") or ""),
        "fmt": v.get("fmt") if v.get("fmt") in ("csv", "tsv") else "csv",
        "path": str(v.get("path") or "ip-fqdn"),
    }


async def set_graylog_dsv(
    session: AsyncSession, *, enabled: bool, fmt: str, path: str,
    regenerate_token: bool = False, updated_by_user_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    import re
    import secrets

    from sqlalchemy.orm.attributes import flag_modified

    row = await session.get(SystemSetting, GRAYLOG_DSV_KEY)
    if row is None:
        row = SystemSetting(key=GRAYLOG_DSV_KEY, value={}, updated_by=updated_by_user_id)
        session.add(row)
    cur = dict(row.value or {})
    cur["enabled"] = bool(enabled)
    cur["fmt"] = fmt if fmt in ("csv", "tsv") else "csv"
    # path 限英數 / 連字號 / 底線，避免亂跑路由
    slug = re.sub(r"[^A-Za-z0-9_-]", "", path or "").strip("-") or "ip-fqdn"
    cur["path"] = slug[:48]
    if regenerate_token or not cur.get("token"):
        cur["token"] = secrets.token_urlsafe(24)
    row.value = cur
    row.updated_by = updated_by_user_id
    flag_modified(row, "value")
    await session.commit()
    return await get_graylog_dsv(session)
