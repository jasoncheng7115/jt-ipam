"""裝置名稱來源優先序。

多個來源（手動 / LibreNMS / DNS / Proxmox VM 名稱 / OPNsense / SNMP sysName）可能
都替同一台 device 提供名稱。本模組決定採用誰：排越前面優先序越高；可停用個別來源
（manual 不可停用）。設定存 system_settings.device_name_precedence，與 arp/hostname
同套路（60s cache）。

`resolve_device_name(candidates)` 給 sync 流程呼叫：傳入 {source: name}，回傳依優先序
應採用的名稱。
"""
from __future__ import annotations

import time
import uuid

from sqlalchemy import select  # noqa: F401  (對齊其它 precedence 模組的 import 形狀)
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system_setting import SystemSetting

DEVNAME_KEY = "device_name_precedence"
DEVNAME_SOURCES = ("manual", "librenms", "dns", "proxmox", "opnsense", "snmp")
# 預設：手動最優先，其次 SNMP sysName（人類可讀），再來才是 LibreNMS hostname
# （常常就是 IP，當名稱不佳）、DNS、Proxmox VM 名稱、OPNsense。
DEFAULT_DEVNAME_ORDER: list[str] = ["manual", "snmp", "librenms", "dns", "proxmox", "opnsense"]
_TTL = 60.0
_cache: dict[str, tuple[float, list[str], list[str]]] = {}


def _bust() -> None:
    _cache.pop(DEVNAME_KEY, None)


def _sanitize(raw: object) -> list[str]:
    out: list[str] = []
    if isinstance(raw, list):
        for s in raw:
            if isinstance(s, str) and s in DEVNAME_SOURCES and s not in out:
                out.append(s)
    for s in DEFAULT_DEVNAME_ORDER:
        if s not in out:
            out.append(s)
    return out


async def _load(session: AsyncSession) -> tuple[list[str], list[str]]:
    now = time.monotonic()
    cached = _cache.get(DEVNAME_KEY)
    if cached and now - cached[0] < _TTL:
        return cached[1], cached[2]
    row = await session.get(SystemSetting, DEVNAME_KEY)
    val = row.value if row and isinstance(row.value, dict) else {}
    order = _sanitize(val.get("order"))
    disabled = [
        s for s in (val.get("disabled") or [])
        if isinstance(s, str) and s in DEVNAME_SOURCES and s != "manual"
    ]
    _cache[DEVNAME_KEY] = (now, order, disabled)
    return order, disabled


async def get_devname_precedence(session: AsyncSession) -> list[str]:
    order, _ = await _load(session)
    return order


async def get_devname_disabled(session: AsyncSession) -> list[str]:
    _, disabled = await _load(session)
    return disabled


async def set_devname_precedence(
    session: AsyncSession, *, order: list[str],
    disabled: list[str] | None = None, updated_by_user_id: uuid.UUID | None = None,
) -> tuple[list[str], list[str]]:
    clean = _sanitize(order)
    clean_disabled = [s for s in (disabled or []) if s in DEVNAME_SOURCES and s != "manual"]
    row = await session.get(SystemSetting, DEVNAME_KEY)
    if row is None:
        row = SystemSetting(key=DEVNAME_KEY, value={}, updated_by=updated_by_user_id)
        session.add(row)
    row.value = {"order": clean, "disabled": clean_disabled}
    row.updated_by = updated_by_user_id
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(row, "value")
    await session.commit()
    _bust()
    return clean, clean_disabled


def pick_name(candidates: dict[str, str], order: list[str], disabled: list[str]) -> str | None:
    """純函式：依優先序從 candidates 挑名稱（跳過停用來源與空字串）。"""
    for src in order:
        if src in disabled:
            continue
        name = candidates.get(src)
        if name and name.strip():
            return name.strip()
    return None


async def resolve_device_name(
    session: AsyncSession, candidates: dict[str, str],
) -> str | None:
    """sync 流程用：傳入 {source: name}，回傳依目前優先序應採用的名稱。"""
    order, disabled = await _load(session)
    return pick_name(candidates, order, disabled)
