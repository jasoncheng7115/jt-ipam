"""Hostname 多來源優先序解析（feature A）。

- 每個來源對一個 IP 各存一筆觀測（ip_hostname_observations）
- IPAddress.hostname = 解析後的有效值：
    1. 若該 IP 有 hostname_source_pin 且該來源有觀測 → 用它
    2. 否則依全域優先序（system_settings.hostname_precedence）取第一個有值的來源
- 有效值變動時，順手寫一筆 feature B 的 hostname_changed 異動記錄

全域優先序透過 set_precedence 改，存 system_settings；有 60s in-process cache。
"""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ip_hostname import HOSTNAME_SOURCES, IPHostnameObservation
from app.models.system_setting import SystemSetting
from app.services.ip_history import log_change

if TYPE_CHECKING:
    from app.models.address import IPAddress

HOSTNAME_KEY = "hostname_precedence"
# 預設：人工最優先，其次 DNS、LibreNMS、OPNsense、掃描、Proxmox
DEFAULT_ORDER: list[str] = ["manual", "dns", "librenms", "opnsense", "pfsense", "scanner", "netbios", "mdns", "proxmox", "wazuh", "adguard"]
_TTL_SEC = 60.0
_cache: dict[str, tuple[float, list[str]]] = {}


def _bust() -> None:
    _cache.pop(HOSTNAME_KEY, None)


def _sanitize_order(raw: object) -> list[str]:
    """把任意輸入清成合法、去重、補齊的來源順序。"""
    out: list[str] = []
    if isinstance(raw, list):
        for s in raw:
            if isinstance(s, str) and s in HOSTNAME_SOURCES and s not in out:
                out.append(s)
    # 補上沒列到的來源（維持 DEFAULT_ORDER 的相對次序）
    for s in DEFAULT_ORDER:
        if s not in out:
            out.append(s)
    # 保險：HOSTNAME_SOURCES 內若有未列到的（如新增的 wazuh/adguard）也補上
    for s in HOSTNAME_SOURCES:
        if s not in out:
            out.append(s)
    return out


async def _load(session: AsyncSession) -> tuple[list[str], list[str]]:
    """讀 (order, disabled)，60s cache。"""
    now = time.monotonic()
    cached = _cache.get(HOSTNAME_KEY)
    if cached and now - cached[0] < _TTL_SEC:
        return cached[1], cached[2]  # type: ignore[misc]
    row = await session.get(SystemSetting, HOSTNAME_KEY)
    val = row.value if row and isinstance(row.value, dict) else {}
    order = _sanitize_order(val.get("order"))
    disabled = [s for s in (val.get("disabled") or []) if isinstance(s, str) and s in HOSTNAME_SOURCES]
    _cache[HOSTNAME_KEY] = (now, order, disabled)  # type: ignore[assignment]
    return order, disabled


async def get_precedence(session: AsyncSession) -> list[str]:
    order, _ = await _load(session)
    return order


async def get_disabled(session: AsyncSession) -> list[str]:
    _, disabled = await _load(session)
    return disabled


async def set_precedence(
    session: AsyncSession, *, order: list[str],
    disabled: list[str] | None = None, updated_by_user_id: uuid.UUID | None = None,
) -> tuple[list[str], list[str]]:
    clean = _sanitize_order(order)
    # 不能把所有來源都停用（至少留 manual 可用），且 disabled 必須是合法來源
    clean_disabled = [s for s in (disabled or []) if s in HOSTNAME_SOURCES and s != "manual"]
    row = await session.get(SystemSetting, HOSTNAME_KEY)
    if row is None:
        row = SystemSetting(key=HOSTNAME_KEY, value={}, updated_by=updated_by_user_id)
        session.add(row)
    row.value = {"order": clean, "disabled": clean_disabled}
    row.updated_by = updated_by_user_id
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(row, "value")
    await session.commit()
    _bust()
    return clean, clean_disabled


async def seed_observation(
    session: AsyncSession, *, ip: IPAddress, source: str, hostname: str | None,
) -> None:
    """IP 建立時用：hostname 已直接寫進 ip.hostname，這裡只補一筆觀測，不重算/不記異動。"""
    hostname = (hostname or "").strip() or None
    if hostname is None or source not in HOSTNAME_SOURCES:
        return
    from datetime import UTC, datetime
    session.add(IPHostnameObservation(
        ip_id=ip.id, source=source, hostname=hostname, observed_at=datetime.now(UTC),
    ))


def _resolve(observations: dict[str, str], pin: str | None, order: list[str]) -> str | None:
    """依 pin + 優先序挑出有效 hostname。"""
    if pin and observations.get(pin):
        return observations[pin]
    for src in order:
        v = observations.get(src)
        if v:
            return v
    return None


async def _observations_for(session: AsyncSession, ip_id) -> dict[str, str]:  # type: ignore[no-untyped-def]
    rows = (await session.execute(
        select(IPHostnameObservation.source, IPHostnameObservation.hostname)
        .where(IPHostnameObservation.ip_id == ip_id)
    )).all()
    return {src: hn for src, hn in rows}


async def recompute_effective(
    session: AsyncSession, *, ip: IPAddress, source: str | None = None,
    actor_user_id: str | None = None,
) -> bool:
    """依現有觀測重算 ip.hostname；有變就更新並寫異動記錄。回傳是否有變。"""
    obs = await _observations_for(session, ip.id)
    order, disabled = await _load(session)
    eff_order = [s for s in order if s not in disabled]   # 停用的來源不參與名稱比對
    new_hostname = _resolve(obs, ip.hostname_source_pin, eff_order)
    old_hostname = ip.hostname
    if (old_hostname or None) == (new_hostname or None):
        return False
    ip.hostname = new_hostname
    await log_change(
        session, ip=ip, event_type="hostname_changed", field="hostname",
        old=old_hostname, new=new_hostname,
        source=source or "system", actor_user_id=actor_user_id,
    )
    return True


async def apply_observation(
    session: AsyncSession, *, ip: IPAddress, source: str, hostname: str | None,
    actor_user_id: str | None = None, tiebreak_min: bool = False,
) -> bool:
    """記錄某來源對此 IP 的 hostname 觀測（None/空 → 清掉該來源），再重算有效值。

    回傳有效 hostname 是否因此變動。所有 sync / 人為編輯改 hostname 都走這裡。

    tiebreak_min：同一來源、同一 IP 已有不同主機名稱時，保留字典序較小者（穩定收斂）。
    給「多個來源實體可能指向同一 IP」的 sync 用（如多台 PVE guest 回報同一 IP），避免每次同步來回翻轉、洗版異動記錄。
    """
    if source not in HOSTNAME_SOURCES:
        source = "manual"
    hostname = (hostname or "").strip() or None

    existing = (await session.execute(
        select(IPHostnameObservation).where(
            IPHostnameObservation.ip_id == ip.id,
            IPHostnameObservation.source == source,
        )
    )).scalar_one_or_none()

    if hostname is None:
        if existing is not None:
            await session.execute(
                delete(IPHostnameObservation).where(IPHostnameObservation.id == existing.id)
            )
    elif existing is None:
        from datetime import UTC, datetime
        session.add(IPHostnameObservation(
            ip_id=ip.id, source=source, hostname=hostname, observed_at=datetime.now(UTC),
        ))
    elif existing.hostname != hostname:
        # 穩定收斂：多實體共用同一 IP 時，保留字典序較小者，避免每次同步來回翻轉
        if tiebreak_min and existing.hostname and hostname > existing.hostname:
            pass
        else:
            from datetime import UTC, datetime
            existing.hostname = hostname
            existing.observed_at = datetime.now(UTC)

    # observation 與下面 recompute 在同一交易；flush 讓上面的新增/刪除對 select 可見
    await session.flush()
    return await recompute_effective(
        session, ip=ip, source=source, actor_user_id=actor_user_id,
    )
