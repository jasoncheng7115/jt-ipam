"""ARP / MAC 來源優先序。

多個來源 (scanner / LibreNMS / OPNsense / AdGuard / Proxmox / 手動) 可能都替同一個
IP 回報 MAC。本模組決定誰能覆寫誰：排在越前面的來源優先序越高。

設定存 system_settings.arp_precedence，與 hostname_precedence 同套路（60s cache）。
"""
from __future__ import annotations

import time
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.address import IPAddress
from app.models.system_setting import SystemSetting

ARP_KEY = "arp_precedence"
ARP_SOURCES = ("manual", "scanner", "opnsense", "pfsense", "librenms", "adguard", "proxmox")
# 預設：手動最優先，其次主動掃描、防火牆 ARP、LibreNMS、AdGuard、Proxmox
DEFAULT_ARP_ORDER: list[str] = ["manual", "scanner", "opnsense", "pfsense", "librenms", "adguard", "proxmox"]
_TTL = 60.0
_cache: dict[str, tuple[float, list[str], list[str]]] = {}


def _bust() -> None:
    _cache.pop(ARP_KEY, None)


def _sanitize(raw: object) -> list[str]:
    out: list[str] = []
    if isinstance(raw, list):
        for s in raw:
            if isinstance(s, str) and s in ARP_SOURCES and s not in out:
                out.append(s)
    for s in DEFAULT_ARP_ORDER:
        if s not in out:
            out.append(s)
    return out


async def _load(session: AsyncSession) -> tuple[list[str], list[str]]:
    """讀 (order, disabled)，60s cache。"""
    now = time.monotonic()
    cached = _cache.get(ARP_KEY)
    if cached and now - cached[0] < _TTL:
        return cached[1], cached[2]
    row = await session.get(SystemSetting, ARP_KEY)
    val = row.value if row and isinstance(row.value, dict) else {}
    order = _sanitize(val.get("order"))
    disabled = [s for s in (val.get("disabled") or []) if isinstance(s, str) and s in ARP_SOURCES and s != "manual"]
    _cache[ARP_KEY] = (now, order, disabled)
    return order, disabled


async def get_arp_precedence(session: AsyncSession) -> list[str]:
    order, _ = await _load(session)
    return order


async def get_arp_disabled(session: AsyncSession) -> list[str]:
    _, disabled = await _load(session)
    return disabled


async def set_arp_precedence(
    session: AsyncSession, *, order: list[str],
    disabled: list[str] | None = None, updated_by_user_id: uuid.UUID | None = None,
) -> tuple[list[str], list[str]]:
    clean = _sanitize(order)
    # 不能停用 manual（至少留人工可用）；disabled 必須是合法來源
    clean_disabled = [s for s in (disabled or []) if s in ARP_SOURCES and s != "manual"]
    row = await session.get(SystemSetting, ARP_KEY)
    if row is None:
        row = SystemSetting(key=ARP_KEY, value={}, updated_by=updated_by_user_id)
        session.add(row)
    row.value = {"order": clean, "disabled": clean_disabled}
    row.updated_by = updated_by_user_id
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(row, "value")
    await session.commit()
    _bust()
    return clean, clean_disabled


async def consider_mac(
    session: AsyncSession, *, ip: IPAddress, mac: str | None, source: str,
) -> bool:
    """依優先序決定是否用此來源的 MAC 覆寫 ip.mac。回傳是否有更新。

    規則：
      - 沒 MAC（清空）→ 不動
      - ip 目前沒 MAC → 直接寫，記來源
      - ip 已有 MAC 但來源未知（legacy）→ 保留，不覆寫（避免蓋掉人工/舊資料）
      - ip 已有 MAC 且已知來源 → 只有新來源優先序更高（index 更小）才覆寫
    """
    if not mac:
        return False
    mac = mac.strip().lower()
    if source not in ARP_SOURCES:
        source = "scanner"
    order, disabled = await _load(session)
    if source in disabled:
        return False   # 該來源已停用 → 不參與 MAC 覆寫
    if ip.mac is None:
        ip.mac = mac
        ip.mac_source = source
        return True
    if ip.mac_source is None:
        return False

    def rank(s: str) -> int:
        return order.index(s) if s in order else len(order)

    if rank(source) < rank(ip.mac_source) or (
        rank(source) == rank(ip.mac_source) and str(ip.mac).lower() != mac
    ):
        ip.mac = mac
        ip.mac_source = source
        return True
    return False
