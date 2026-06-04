"""裝置型號 (model) 來源優先序。

多個來源（手動 / LibreNMS hardware / Proxmox / OPNsense）可能都替同一台 device 提供
型號字串。本模組決定採用誰：排越前面優先序越高；可停用個別來源（manual 不可停用）。
設定存 system_settings.device_model_precedence，與 hostname/arp/devname 同套路（60s cache）。

`resolve_device_model(candidates)` 給 sync 流程呼叫：傳入 {source: model}，回傳依優先序
應採用的型號。
"""
from __future__ import annotations

import time
import uuid

from sqlalchemy import select  # noqa: F401  (對齊其它 precedence 模組的 import 形狀)
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system_setting import SystemSetting

MODEL_KEY = "device_model_precedence"
MODEL_SOURCES = ("manual", "librenms", "proxmox", "opnsense")
# 預設：手動最優先，其次 LibreNMS hardware；Proxmox / OPNsense 為未來來源預留位
DEFAULT_MODEL_ORDER: list[str] = ["manual", "librenms", "proxmox", "opnsense"]
_TTL = 60.0
_cache: dict[str, tuple[float, list[str], list[str]]] = {}


def _bust() -> None:
    _cache.pop(MODEL_KEY, None)


def _sanitize(raw: object) -> list[str]:
    out: list[str] = []
    if isinstance(raw, list):
        for s in raw:
            if isinstance(s, str) and s in MODEL_SOURCES and s not in out:
                out.append(s)
    for s in DEFAULT_MODEL_ORDER:
        if s not in out:
            out.append(s)
    return out


async def _load(session: AsyncSession) -> tuple[list[str], list[str]]:
    now = time.monotonic()
    cached = _cache.get(MODEL_KEY)
    if cached and now - cached[0] < _TTL:
        return cached[1], cached[2]
    row = await session.get(SystemSetting, MODEL_KEY)
    val = row.value if row and isinstance(row.value, dict) else {}
    order = _sanitize(val.get("order"))
    disabled = [
        s for s in (val.get("disabled") or [])
        if isinstance(s, str) and s in MODEL_SOURCES and s != "manual"
    ]
    _cache[MODEL_KEY] = (now, order, disabled)
    return order, disabled


async def get_model_precedence(session: AsyncSession) -> list[str]:
    order, _ = await _load(session)
    return order


async def get_model_disabled(session: AsyncSession) -> list[str]:
    _, disabled = await _load(session)
    return disabled


async def set_model_precedence(
    session: AsyncSession, *, order: list[str],
    disabled: list[str] | None = None, updated_by_user_id: uuid.UUID | None = None,
) -> tuple[list[str], list[str]]:
    clean = _sanitize(order)
    clean_disabled = [s for s in (disabled or []) if s in MODEL_SOURCES and s != "manual"]
    row = await session.get(SystemSetting, MODEL_KEY)
    if row is None:
        row = SystemSetting(key=MODEL_KEY, value={}, updated_by=updated_by_user_id)
        session.add(row)
    row.value = {"order": clean, "disabled": clean_disabled}
    row.updated_by = updated_by_user_id
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(row, "value")
    await session.commit()
    _bust()
    return clean, clean_disabled


def pick_model(candidates: dict[str, str], order: list[str], disabled: list[str]) -> str | None:
    for src in order:
        if src in disabled:
            continue
        v = candidates.get(src)
        if v and v.strip():
            return v.strip()
    return None


async def resolve_device_model(
    session: AsyncSession, candidates: dict[str, str],
) -> str | None:
    """sync 流程用：傳入 {source: model}，回傳依目前優先序應採用的型號。"""
    order, disabled = await _load(session)
    return pick_model(candidates, order, disabled)
