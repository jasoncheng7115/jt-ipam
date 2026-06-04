"""異常偵測（規格書 §6.9）。

偵測規則：
- IP 衝突：同 IP 在短時間（1h）內 ARP 看到不同 MAC
- MAC 漂移：同 MAC 在多個 switch+port 跳動（1h 內）
- 失聯 IP：IPAM 有 IP 紀錄但 ARP/FDB 從未看過超過 N 天
- 未授權設備：ARP 出現的 IP 但 IPAM 沒有

每次偵測結果寫站內通知 + Webhook 事件 + audit。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.address import IPAddress
from app.models.librenms import ARPEntry, FDBEntry
from app.models.user import User
from app.services.notification import deliver_event, push_notification


@dataclass
class AnomalyReport:
    ip_conflicts: list[dict[str, Any]] = field(default_factory=list)
    mac_drifts: list[dict[str, Any]] = field(default_factory=list)
    ghost_ips: list[dict[str, Any]] = field(default_factory=list)
    unauthorized_ips: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ip_conflicts": self.ip_conflicts,
            "mac_drifts": self.mac_drifts,
            "ghost_ips": self.ghost_ips,
            "unauthorized_ips": self.unauthorized_ips,
            "total": (
                len(self.ip_conflicts) + len(self.mac_drifts)
                + len(self.ghost_ips) + len(self.unauthorized_ips)
            ),
        }


async def detect_ip_conflicts(
    session: AsyncSession, *, window: timedelta = timedelta(hours=1),
) -> list[dict[str, Any]]:
    """ARP 中同一 IP 在短時間內看到 ≥2 個 MAC。"""
    cutoff = datetime.now(UTC) - window
    rows = (
        await session.execute(
            select(ARPEntry.ip, ARPEntry.mac, func.count(), func.max(ARPEntry.last_seen_at))
            .where(ARPEntry.last_seen_at >= cutoff)
            .group_by(ARPEntry.ip, ARPEntry.mac)
        )
    ).all()
    by_ip: dict[str, list[tuple[str, datetime]]] = defaultdict(list)
    for ip, mac, _cnt, last in rows:
        by_ip[ip].append((mac, last))

    out: list[dict[str, Any]] = []
    for ip, pairs in by_ip.items():
        if len({m for m, _ in pairs}) < 2:
            continue
        out.append({
            "ip": ip,
            "macs": [
                {"mac": m, "last_seen_at": dt.isoformat()}
                for m, dt in sorted(pairs, key=lambda x: x[1], reverse=True)
            ],
        })
    return out


async def detect_mac_drifts(
    session: AsyncSession, *, window: timedelta = timedelta(hours=1),
) -> list[dict[str, Any]]:
    """同一 MAC 出現在 ≥2 個 (device, port) 組合（1h 內）。"""
    cutoff = datetime.now(UTC) - window
    rows = (
        await session.execute(
            select(FDBEntry.mac, FDBEntry.device_id, FDBEntry.port_name,
                   func.max(FDBEntry.last_seen_at))
            .where(FDBEntry.last_seen_at >= cutoff)
            .group_by(FDBEntry.mac, FDBEntry.device_id, FDBEntry.port_name)
        )
    ).all()
    by_mac: dict[str, list[tuple[str | None, str | None, datetime]]] = defaultdict(list)
    for mac, did, port, last in rows:
        by_mac[mac].append((str(did) if did else None, port, last))

    out: list[dict[str, Any]] = []
    for mac, locs in by_mac.items():
        unique = {(d, p) for d, p, _ in locs}
        if len(unique) < 2:
            continue
        out.append({
            "mac": mac,
            "locations": [
                {
                    "device_id": d, "port": p, "last_seen_at": dt.isoformat(),
                }
                for d, p, dt in sorted(locs, key=lambda x: x[2], reverse=True)
            ],
        })
    return out


async def detect_ghost_ips(
    session: AsyncSession, *, days: int = 30,
) -> list[dict[str, Any]]:
    """IPAM 有的 IP，但 ARP 從未看過或上次看到 > days 天前。"""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    # 取所有有寫進 IPAM 但其實沒 last_seen_scanner / last_seen_librenms 的
    rows = (
        await session.execute(
            select(IPAddress)
            .where(
                (
                    (IPAddress.last_seen_scanner.is_(None))
                    | (IPAddress.last_seen_scanner < cutoff)
                )
                & (
                    (IPAddress.last_seen_librenms.is_(None))
                    | (IPAddress.last_seen_librenms < cutoff)
                )
            )
            .limit(500)
        )
    ).scalars().all()
    return [
        {
            "ip_address_id": str(r.id),
            "ip": str(r.ip).split("/")[0],
            "hostname": r.hostname,
            "last_seen_scanner": r.last_seen_scanner.isoformat() if r.last_seen_scanner else None,
            "last_seen_librenms": r.last_seen_librenms.isoformat() if r.last_seen_librenms else None,
        }
        for r in rows
    ]


async def detect_unauthorized_ips(session: AsyncSession) -> list[dict[str, Any]]:
    """ARP 看到但 IPAM 沒紀錄的 IP。"""
    arp_ips_rows = (
        await session.execute(
            select(ARPEntry.ip).group_by(ARPEntry.ip).limit(2000)
        )
    ).all()
    arp_ips = {str(r[0]) for r in arp_ips_rows}
    if not arp_ips:
        return []

    ipam_ips_rows = (
        await session.execute(select(IPAddress.ip))
    ).all()
    ipam_ips = {str(r[0]).split("/")[0] for r in ipam_ips_rows}

    unauthorized = sorted(arp_ips - ipam_ips)
    return [{"ip": ip} for ip in unauthorized[:200]]


async def run_detection(
    session: AsyncSession, *, notify_admins: bool = True,
) -> AnomalyReport:
    """一次跑所有偵測規則；命中時發通知 + webhook event。"""
    report = AnomalyReport(
        ip_conflicts=await detect_ip_conflicts(session),
        mac_drifts=await detect_mac_drifts(session),
        ghost_ips=await detect_ghost_ips(session),
        unauthorized_ips=await detect_unauthorized_ips(session),
    )

    if notify_admins:
        admins = (
            await session.execute(
                select(User).where(User.is_admin.is_(True), User.is_active.is_(True))
            )
        ).scalars().all()

        for category, items in (
            ("IP conflicts", report.ip_conflicts),
            ("MAC drifts", report.mac_drifts),
            ("Ghost IPs", report.ghost_ips),
            ("Unauthorized IPs", report.unauthorized_ips),
        ):
            if not items:
                continue
            for admin in admins:
                await push_notification(
                    session,
                    user_id=admin.id,
                    severity="warning",
                    title=f"{category}: {len(items)} new finding(s)",
                    body="See /anomalies for details.",
                    link="/anomalies",
                    object_type="anomaly",
                )
        await deliver_event(session, event="anomaly.detected", payload=report.to_dict())

    await session.commit()
    return report
