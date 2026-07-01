"""OS 來源優先序（比照 hostname 優先序）。

OS 資訊有三個來源，各自存在不同地方：
  - scanner  : 掃描代理 nmap 偵測 → ip_addresses.os_guess
  - librenms : LibreNMS 裝置 → devices.os（IP 經 device_id 關聯）
  - wazuh    : Wazuh 代理 → wazuh_agents.os_platform / os_version（以 IP 對映）

依 system_settings.os_precedence 的順序，取第一個有值的來源當作此 IP 的「有效 OS」。
順序透過 set_order 改，存 system_settings；60s in-process cache。compute-on-read：
不另存欄位，由 effective_os() 即時彙整（OS 不常變，且免 migration / sync hook）。
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.os_fingerprint import normalize_os
from app.models.system_setting import SystemSetting

OS_KEY = "os_precedence"
OS_SOURCES: list[str] = ["scanner", "librenms", "wazuh"]
DEFAULT_ORDER: list[str] = ["librenms", "wazuh", "scanner"]
_TTL_SEC = 60.0
_cache: dict[str, tuple[float, list[str]]] = {}


def _bust() -> None:
    _cache.pop(OS_KEY, None)


def _sanitize_order(raw: object) -> list[str]:
    out: list[str] = []
    if isinstance(raw, list):
        for s in raw:
            if isinstance(s, str) and s in OS_SOURCES and s not in out:
                out.append(s)
    for s in DEFAULT_ORDER:
        if s not in out:
            out.append(s)
    return out


async def get_order(session: AsyncSession) -> list[str]:
    now = time.monotonic()
    cached = _cache.get(OS_KEY)
    if cached and now - cached[0] < _TTL_SEC:
        return cached[1]
    row = await session.get(SystemSetting, OS_KEY)
    val = row.value if row and isinstance(row.value, dict) else {}
    order = _sanitize_order(val.get("order"))
    _cache[OS_KEY] = (now, order)
    return order


async def set_order(
    session: AsyncSession, *, order: list[str], updated_by_user_id: uuid.UUID | None = None,
) -> list[str]:
    clean = _sanitize_order(order)
    row = await session.get(SystemSetting, OS_KEY)
    if row is None:
        row = SystemSetting(key=OS_KEY, value={}, updated_by=updated_by_user_id)
        session.add(row)
    row.value = {"order": clean}
    row.updated_by = updated_by_user_id
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(row, "value")
    await session.commit()
    _bust()
    return clean


async def _candidates(session: AsyncSession, ip: Any) -> dict[str, str]:
    """彙整此 IP 各來源的原始 OS 字串（有值才放）。"""
    out: dict[str, str] = {}
    if ip.os_guess:
        out["scanner"] = ip.os_guess
    if ip.device_id:
        from app.models.device import Device
        dev = await session.get(Device, ip.device_id)
        if dev is not None and getattr(dev, "os", None):
            ver = getattr(dev, "version", None)
            out["librenms"] = f"{dev.os}{' ' + ver if ver else ''}"
    # Wazuh 代理以 IP 對映
    from app.models.wazuh import WazuhAgent
    wa = (await session.execute(
        select(WazuhAgent).where(WazuhAgent.ip == str(ip.ip)).limit(1)
    )).scalars().first()
    if wa is not None and wa.os_platform:
        ver = wa.os_version
        out["wazuh"] = f"{wa.os_platform}{' ' + ver if ver else ''}"
    return out


async def effective_os(session: AsyncSession, ip: Any) -> dict[str, Any]:
    """依優先序回傳此 IP 的有效 OS：{os_guess, os_family, os_source}（皆可能為 None）。"""
    cand = await _candidates(session, ip)
    if not cand:
        return {"os_guess": None, "os_family": None, "os_source": None}
    order = await get_order(session)
    for src in order:
        raw = cand.get(src)
        if raw:
            return {"os_guess": raw, "os_family": normalize_os(raw), "os_source": src}
    return {"os_guess": None, "os_family": None, "os_source": None}
