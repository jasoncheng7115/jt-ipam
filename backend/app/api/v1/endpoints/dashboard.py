"""IP 狀態指示儀表板（IP 指示計）。

phpIPAM 缺點：dashboard 只是堆數字。
jt-ipam 設計：
  - 全系統 IP / Subnet 使用率
  - online / offline / unknown 比（取自 effective_status）
  - Top-N 最滿 subnet（給 capacity planning）
  - 最近 24h 異動數
  - 各 section 的使用熱度

OWASP A01：透過 RBAC `filter_visible` 限制 user 看到的範圍。admin 看全部。
"""

from __future__ import annotations

import ipaddress
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser
from app.core.db import get_session
from app.models.address import IPAddress
from app.models.audit import AuditLog
from app.models.section import Section
from app.models.subnet import Subnet
from app.schemas.base import StrictModel
from app.services.permission import filter_visible
from app.services.subnet import host_count

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


class StatusCounts(StrictModel):
    online: int
    offline: int
    unknown: int


class TopSubnet(StrictModel):
    subnet_id: str
    cidr: str
    description: str | None
    section_id: str
    used: int
    total: int
    used_pct: float


class SectionHeat(StrictModel):
    section_id: str
    name: str
    subnet_count: int
    total_hosts: int
    used: int
    used_pct: float


class DashboardOverview(StrictModel):
    sections: int
    subnets: int
    addresses: int
    total_capacity: int     # 加總所有 visible subnet 的可用 host 數
    used: int               # 加總已配發 IP 數
    used_pct: float
    status: StatusCounts
    top_full_subnets: list[TopSubnet]
    section_heat: list[SectionHeat]
    audit_24h: int          # 最近 24 小時 audit_log 條數


@router.get("/overview", response_model=DashboardOverview)
async def overview(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DashboardOverview:
    # ── 取所有 section / subnet（admin 全可見；非 admin 過濾）──
    all_sections = list((await session.execute(select(Section))).scalars().all())
    section_visible = set(
        await filter_visible(
            session, user=user, object_type="section",
            object_ids=[s.id for s in all_sections], required="read",
        )
    )
    visible_sections = [s for s in all_sections if s.id in section_visible]

    all_subnets = list((await session.execute(select(Subnet))).scalars().all())
    subnet_visible = set(
        await filter_visible(
            session, user=user, object_type="subnet",
            object_ids=[s.id for s in all_subnets], required="read",
        )
    )
    visible_subnets = [s for s in all_subnets if s.id in subnet_visible]
    visible_subnet_ids = {s.id for s in visible_subnets}

    # ── 計算 capacity / used / per-subnet usage ──
    total_capacity = 0
    per_subnet_used: dict[str, int] = {}

    if visible_subnet_ids:
        rows = (
            await session.execute(
                select(IPAddress.subnet_id, func.count())
                .where(IPAddress.subnet_id.in_(visible_subnet_ids))
                .group_by(IPAddress.subnet_id)
            )
        ).all()
        per_subnet_used = {str(r[0]): int(r[1]) for r in rows}

    for s in visible_subnets:
        try:
            total_capacity += host_count(ipaddress.ip_network(str(s.cidr), strict=False))
        except ValueError:
            continue

    used = sum(per_subnet_used.values())
    used_pct = round((used / total_capacity * 100), 2) if total_capacity else 0.0

    # ── status counts ──
    status_counts = StatusCounts(online=0, offline=0, unknown=0)
    if visible_subnet_ids:
        status_rows = (
            await session.execute(
                select(IPAddress.effective_status, func.count())
                .where(IPAddress.subnet_id.in_(visible_subnet_ids))
                .group_by(IPAddress.effective_status)
            )
        ).all()
        for st, cnt in status_rows:
            cnt = int(cnt or 0)
            if st in ("online", "Online (scanner)", "Online (via LibreNMS)", "Online (LibreNMS only)"):
                status_counts.online += cnt
            elif st == "offline":
                status_counts.offline += cnt
            else:
                status_counts.unknown += cnt

    # ── top-N 最滿 subnet（前 8）──
    subnet_pcts: list[TopSubnet] = []
    for s in visible_subnets:
        try:
            cap = host_count(ipaddress.ip_network(str(s.cidr), strict=False))
        except ValueError:
            continue
        if cap == 0:
            continue
        u = per_subnet_used.get(str(s.id), 0)
        subnet_pcts.append(TopSubnet(
            subnet_id=str(s.id),
            cidr=str(s.cidr),
            description=s.description,
            section_id=str(s.section_id),
            used=u,
            total=cap,
            used_pct=round(u / cap * 100, 2),
        ))
    subnet_pcts.sort(key=lambda x: x.used_pct, reverse=True)
    top_full = subnet_pcts[:8]

    # ── section heat ──
    section_by_id = {s.id: s for s in visible_sections}
    sect_buckets: dict[str, dict[str, int]] = {}
    for s in visible_subnets:
        sec = sect_buckets.setdefault(
            str(s.section_id),
            {"subnet_count": 0, "total_hosts": 0, "used": 0},
        )
        sec["subnet_count"] += 1
        try:
            cap = host_count(ipaddress.ip_network(str(s.cidr), strict=False))
        except ValueError:
            cap = 0
        sec["total_hosts"] += cap
        sec["used"] += per_subnet_used.get(str(s.id), 0)

    section_heat: list[SectionHeat] = []
    for sid, bucket in sect_buckets.items():
        sec = section_by_id.get(uuid.UUID(sid))
        if sec is None:
            continue
        pct = round(bucket["used"] / bucket["total_hosts"] * 100, 2) if bucket["total_hosts"] else 0.0
        section_heat.append(SectionHeat(
            section_id=sid,
            name=sec.name,
            subnet_count=bucket["subnet_count"],
            total_hosts=bucket["total_hosts"],
            used=bucket["used"],
            used_pct=pct,
        ))
    section_heat.sort(key=lambda x: x.used_pct, reverse=True)

    # ── audit 24h ──
    cutoff = datetime.now(UTC) - timedelta(hours=24)
    audit_24h = int(
        await session.scalar(
            select(func.count()).select_from(AuditLog).where(AuditLog.ts >= cutoff)
        )
        or 0
    )

    return DashboardOverview(
        sections=len(visible_sections),
        subnets=len(visible_subnets),
        addresses=used,
        total_capacity=total_capacity,
        used=used,
        used_pct=used_pct,
        status=status_counts,
        top_full_subnets=top_full,
        section_heat=section_heat,
        audit_24h=audit_24h,
    )
