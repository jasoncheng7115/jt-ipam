"""上線狀態跟著所屬設備（客戶 2026-10-14）。

多 LAN 路由器的閘道是路由器自己的介面位址，常被政策擋掉 ICMP、或掃描代理不在那個網段 → 探測沒回應、紅燈，
實際上一直在用。勾了 `liveness_from_device` 的 IP，每輪（重算上線狀態之前）把**同一台設備其他位址**最新的
會過期證據、或 LibreNMS 回報這台設備 up 的時間，搬到 `arp_seen["device"]`：

- 時間是原本那筆證據的時間，不是現在 → 設備掛了，證據一起過期、一起變紅（不可以永遠綠）
- 只搬會過期的來源（evidence.is_aging）：DHCP 租約、LibreNMS 的 ARP 這種不會過期的不算
- 不從別的「跟著設備」的位址搬（會互相撐住，變成永遠綠）
- 沒掛設備時不寫證據；上線判斷把它當成「沒在探測」→ 不算離線（agent_scope.classify_liveness）

寫進 arp_seen 而不是另開一套判斷：燈號、失聯回收、異常偵測、可用性長條圖讀的都是同一份證據，
多一個來源它們就全部跟上，不必逐處改。
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sqlin import in_values
from app.models.address import IPAddress
from app.services import arp_seen as arp_seen_svc
from app.services.evidence import is_aging

KEY = "device"
_COLUMNS = ("last_seen_scanner", "last_seen_librenms", "last_seen_wazuh", "last_seen_zabbix", "last_seen_checkmk")


def _ts(v: Any) -> datetime | None:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=UTC)
    return arp_seen_svc._parse(v)


def _newest_aging(row: Any) -> datetime | None:
    best: datetime | None = None
    for col in _COLUMNS:
        t = _ts(getattr(row, col, None))
        if t and (best is None or t > best):
            best = t
    for key, raw in (getattr(row, "arp_seen", None) or {}).items():
        if key == KEY or not is_aging(key):
            continue
        t = _ts(raw)
        if t and (best is None or t > best):
            best = t
    return best


def _clear(ipa: IPAddress) -> bool:
    changed = False
    if KEY in (ipa.arp_seen or {}):
        ipa.arp_seen = {k: v for k, v in (ipa.arp_seen or {}).items() if k != KEY}
        changed = True
    if ipa.device_evidence_ip is not None:
        ipa.device_evidence_ip = None
        changed = True
    return changed


async def materialize(session: AsyncSession) -> dict[str, int]:
    from app.models.librenms import LibreNMSDevice

    await session.flush()
    flagged = list((await session.execute(
        select(IPAddress).where(IPAddress.liveness_from_device.is_(True)))).scalars().all())
    device_ids = {f.device_id for f in flagged if f.device_id is not None}

    # 每台設備：同設備（沒勾「跟著設備」的）位址裡最新的會過期證據，與它是哪個位址
    best: dict[Any, tuple[datetime, str | None]] = {}
    if device_ids:
        rows = (await session.execute(
            select(IPAddress.device_id, IPAddress.ip, IPAddress.arp_seen,
                   *[getattr(IPAddress, c) for c in _COLUMNS])
            .where(in_values(IPAddress.device_id, device_ids), IPAddress.liveness_from_device.is_(False)))).all()
        for r in rows:
            t = _newest_aging(r)
            if t and (r.device_id not in best or t > best[r.device_id][0]):
                best[r.device_id] = (t, str(r.ip).split("/")[0])
        # LibreNMS 回報這台設備 up（輪詢得到）：設備層的狀態，沒有對應的位址
        for dev_id, seen in (await session.execute(
                select(LibreNMSDevice.jt_ipam_device_id, func.max(LibreNMSDevice.last_seen_at))
                .where(in_values(LibreNMSDevice.jt_ipam_device_id, device_ids), LibreNMSDevice.status == "up")
                .group_by(LibreNMSDevice.jt_ipam_device_id))).all():
            t = _ts(seen)
            if t and (dev_id not in best or t > best[dev_id][0]):
                best[dev_id] = (t, None)

    stamped = cleared = 0
    for ipa in flagged:
        hit = best.get(ipa.device_id) if ipa.device_id is not None else None
        if hit is None:
            # 沒掛設備（或設備上沒有任何會過期的證據）：不寫證據。舊的「device」留著會自然過期；
            # 沒掛設備時清掉，免得顯示一個跟現在無關的依據
            if ipa.device_id is None and _clear(ipa):
                cleared += 1
            continue
        arp_seen_svc.stamp(ipa, KEY, hit[0])
        if (str(ipa.device_evidence_ip).split("/")[0] if ipa.device_evidence_ip else None) != hit[1]:
            ipa.device_evidence_ip = hit[1]
        stamped += 1

    # 取消勾選的：把搬過來的證據拿掉
    for ipa in (await session.execute(select(IPAddress).where(
            IPAddress.liveness_from_device.is_(False),
            or_(IPAddress.arp_seen.has_key(KEY), IPAddress.device_evidence_ip.is_not(None))))).scalars().all():
        if _clear(ipa):
            cleared += 1
    await session.flush()
    return {"stamped": stamped, "cleared": cleared}
