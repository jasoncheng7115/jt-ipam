"""Hostname 多來源優先序解析（feature A）。

- 每個來源對一個 IP 各存一筆觀測（ip_hostname_observations）
- IPAddress.hostname = 解析後的有效值：
    1. 若該 IP 有 hostname_source_pin 且該來源有觀測 → 用它
    2. 否則依全域優先序（system_settings.hostname_precedence）取第一個有值的來源
- 有效值變動時，順手寫一筆 feature B 的 hostname_changed 異動記錄

全域優先序透過 set_precedence 改，存 system_settings；有 60s in-process cache。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ip_hostname import HOSTNAME_SOURCES, IPHostnameObservation
from app.services.ip_history import log_change
from app.services.precedence import Precedence

if TYPE_CHECKING:
    from app.models.address import IPAddress

HOSTNAME_KEY = "hostname_precedence"
# 預設：人工最優先，其次 DNS、LibreNMS、OPNsense、掃描、Proxmox
DEFAULT_ORDER: list[str] = ["manual", "dns", "librenms", "opnsense", "pfsense", "fortigate", "paloalto", "checkpoint", "mikrotik", "windows_dhcp", "kea_dhcp", "isc_dhcp", "isoinsight", "technitium", "scanner", "netbios", "mdns", "proxmox", "hyperv", "openstack", "zabbix", "checkmk", "wazuh", "adguard", "ocs", "rustdesk"]

# 排序／停用／快取的共通機制在 services/precedence.py；
# 這裡只留 hostname 特有的部分：觀測表、pin、重算與異動記錄。
_P = Precedence(key=HOSTNAME_KEY, sources=tuple(HOSTNAME_SOURCES),
                default_order=tuple(DEFAULT_ORDER))


def _bust() -> None:
    _P.bust()


async def _load(session: AsyncSession) -> tuple[list[str], list[str]]:
    return await _P.load(session)


async def get_precedence(session: AsyncSession) -> list[str]:
    return await _P.get_order(session)


async def get_disabled(session: AsyncSession) -> list[str]:
    return await _P.get_disabled(session)


async def set_precedence(
    session: AsyncSession, *, order: list[str],
    disabled: list[str] | None = None, updated_by_user_id: uuid.UUID | None = None,
) -> tuple[list[str], list[str]]:
    return await _P.save(session, order=order, disabled=disabled,
                         updated_by_user_id=updated_by_user_id)


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


#: 「跟著設備走」的名稱來源：設備自己報的（NetBIOS、mDNS）或裝在設備上的代理回報的。IP 換給另一台設備
#: （MAC 變了）時，這些名稱描述的是上一台，要清掉；新設備有回報時會再寫進來。DNS、防火牆租約、人填的跟著
#: 位址走，不清（租約會跟著新租約重報）。2026-10-05：DHCP 位址換給 Windows 筆電之後，NetBIOS 還掛著一個月前
#: 那台 Mac 的名稱，新筆電擋了 NetBIOS，舊值永遠不會被蓋掉。
DEVICE_BOUND_SOURCES = ("netbios", "mdns", "wazuh", "ocs", "rustdesk")


async def forget_device_names(session: AsyncSession, *, ip: IPAddress, source: str | None = None) -> None:
    """IP 換了一台設備：清掉上一台自己報的名稱，再重算有效主機名稱。

    逐來源的回報（ip_hostname_reports）也要清（2026-10-12）：以前只清觀測，回報表留著上一台的名稱，
    另一台代理回報或某個來源被移除重算時（HostnameRun._derive 取所有回報），舊名稱又會冒回來。
    """
    if ip.id is None:
        return
    from app.models.ip_hostname import IPHostnameReport
    await session.execute(delete(IPHostnameObservation).where(
        IPHostnameObservation.ip_id == ip.id, IPHostnameObservation.source.in_(DEVICE_BOUND_SOURCES)))
    await session.execute(delete(IPHostnameReport).where(
        IPHostnameReport.ip_id == ip.id, IPHostnameReport.source.in_(DEVICE_BOUND_SOURCES)))
    await session.flush()
    await recompute_effective(session, ip=ip, source=source or "system")


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
        # 以前這裡把不認得的來源一律當成 manual：MikroTik 不在清單裡，它的租約名稱就被寫成
        # 「手動輸入」，蓋過使用者真正填的值、而且永遠不會被清（2026-09-26 稽核）。寧可不寫。
        import structlog
        structlog.get_logger("hostname").warning("unknown hostname source ignored", source=source)
        return False
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


async def apply_observations_bulk(
    session: AsyncSession, *, source: str, wants: dict[uuid.UUID, str | None],
    ips: dict[uuid.UUID, IPAddress] | None = None,
) -> int:
    """apply_observation 的整批版：同一個來源、很多 IP（HostnameRun 用）。回傳有效值有變的 IP 數。

    結果與逐筆呼叫 apply_observation 相同，但查詢次數不隨 IP 數成長 —— 逐筆每個 IP 約 7 次查詢
    （觀測、重算、優先序、異動記錄…），第一次接上一個 3 萬個代理的 Wazuh 或 10 萬筆的 DNS
    就是幾十萬次（2026-09-30 大量資料測試）。
    """
    if source not in HOSTNAME_SOURCES:
        import structlog
        structlog.get_logger("hostname").warning("unknown hostname source ignored", source=source)
        return 0
    if not wants:
        return 0
    from datetime import UTC, datetime

    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.core.sqlin import in_values
    from app.models.address import IPAddress
    from app.services.ip_history import latest_changes

    clean = {ip_id: ((h or "").strip() or None) for ip_id, h in wants.items()}
    ids = list(clean)
    gone = [i for i, h in clean.items() if h is None]
    if gone:
        await session.execute(delete(IPHostnameObservation).where(
            IPHostnameObservation.source == source, in_values(IPHostnameObservation.ip_id, gone)))
    now = datetime.now(UTC)
    rows = [{"ip_id": i, "source": source, "hostname": h, "observed_at": now} for i, h in clean.items() if h]
    ins = pg_insert(IPHostnameObservation)
    upsert = ins.on_conflict_do_update(
        constraint="uq_ip_hostname_obs_ip_source",
        set_={"hostname": ins.excluded.hostname, "observed_at": ins.excluded.observed_at})
    for k in range(0, len(rows), 10000):
        await session.execute(upsert, rows[k:k + 10000])

    obs: dict[uuid.UUID, dict[str, str]] = {}
    for ip_id, src, hn in (await session.execute(select(
            IPHostnameObservation.ip_id, IPHostnameObservation.source, IPHostnameObservation.hostname)
            .where(in_values(IPHostnameObservation.ip_id, ids)))).all():
        obs.setdefault(ip_id, {})[src] = hn
    have = dict(ips or {})
    missing = [i for i in ids if i not in have]
    if missing:
        have.update({i.id: i for i in (await session.execute(
            select(IPAddress).where(in_values(IPAddress.id, missing)))).scalars()})
    order, disabled = await _load(session)
    eff_order = [x for x in order if x not in disabled]

    changes: list[tuple[IPAddress, str | None, str | None]] = []
    for ip_id in ids:
        ip = have.get(ip_id)
        if ip is None:
            continue
        new = _resolve(obs.get(ip_id, {}), ip.hostname_source_pin, eff_order)
        if (ip.hostname or None) != (new or None):
            changes.append((ip, ip.hostname, new))
    if not changes:
        return 0
    latest = await latest_changes(session, [ip.id for ip, _o, _n in changes], "hostname")
    for ip, old, new in changes:
        ip.hostname = new
        await log_change(session, ip=ip, event_type="hostname_changed", field="hostname",
                         old=old, new=new, source=source, latest=latest)
    # 逐筆版每個 IP 都 flush 過；這裡一次 flush，呼叫端接著 refresh／查詢才看得到
    await session.flush()
    return len(changes)

