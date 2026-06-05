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
from typing import Annotated

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
from app.services.permission import filter_visible, visible_ids
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
    customer_id: str | None = None
    customer_label: str | None = None  # 客戶 / 管理單位顯示名
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


class TypeCount(StrictModel):
    type: str
    count: int


class CustomerResource(StrictModel):
    customer_id: str | None
    label: str
    subnets: int
    ips: int
    devices: int


class TrendPoint(StrictModel):
    day: str          # YYYY-MM-DD
    audit: int
    ip_changes: int


class RackUsage(StrictModel):
    rack_id: str
    name: str
    used_u: int
    total_u: int
    pct: float


class DashboardOverview(StrictModel):
    sections: int
    subnets: int
    addresses: int
    total_capacity: int     # 加總所有 visible subnet 的可用 host 數
    used: int               # 加總已配發 IP 數
    used_pct: float
    status: StatusCounts
    top_full_subnets: list[TopSubnet]
    pinned_subnets: list[TopSubnet]  # 使用者釘選的子網路（依 user_preferences.pinned_subnet_ids）
    section_heat: list[SectionHeat]
    audit_24h: int          # 最近 24 小時 audit_log 條數
    # 上下關係鏈各層總數（給儀表板關係圖；機房→機櫃→裝置→虛擬機→IP→子網路→區段）
    devices: int
    racks: int
    locations: int
    vms: int
    device_types: list[TypeCount]        # 裝置類型分布
    customer_resources: list[CustomerResource]   # 各單位資源占比
    activity_trend: list[TrendPoint]     # 近 14 日 稽核 / IP 異動 趨勢
    rack_usage: list[RackUsage]          # 機櫃 U 使用率


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

    # ── 各物件類型可見範圍（None = 萬用/全部；set = 限定）。儀表板所有全域統計都要依此縮放，
    #     避免只被指派單一子網路的帳號看到全機構的裝置/機櫃/單位/稽核等彙總。──
    dev_vis = await visible_ids(session, user=user, object_type="device")
    rack_vis = await visible_ids(session, user=user, object_type="rack")
    loc_vis = await visible_ids(session, user=user, object_type="location")
    cust_vis = await visible_ids(session, user=user, object_type="customer")

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

    # ── 先把所有用到的客戶名抓出來建 map ──
    from app.models.customer import Customer
    cust_rows = (await session.execute(select(Customer.id, Customer.name, Customer.title))).all()
    cust_label = {str(r[0]): (r[2] or r[1]) for r in cust_rows}

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
        cid = str(s.customer_id) if s.customer_id else None
        subnet_pcts.append(TopSubnet(
            subnet_id=str(s.id),
            cidr=str(s.cidr),
            description=s.description,
            section_id=str(s.section_id),
            customer_id=cid,
            customer_label=cust_label.get(cid) if cid else None,
            used=u,
            total=cap,
            used_pct=round(u / cap * 100, 2),
        ))
    subnet_pcts.sort(key=lambda x: x.used_pct, reverse=True)
    top_full = subnet_pcts[:8]

    # ── pinned subnets（依使用者 preference）──
    pinned: list[TopSubnet] = []
    from app.models.user import UserPreference
    pref = await session.get(UserPreference, user.id)
    if pref and pref.pinned_subnet_ids:
        pinned_ids = [str(x) for x in pref.pinned_subnet_ids]
        by_id = {p.subnet_id: p for p in subnet_pcts}
        # 依使用者排序保留順序
        for pid in pinned_ids:
            if pid in by_id:
                pinned.append(by_id[pid])

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
        sec = section_by_id.get(uuid.UUID(sid))  # type: ignore[assignment]
        if sec is None:
            continue
        pct = round(bucket["used"] / bucket["total_hosts"] * 100, 2) if bucket["total_hosts"] else 0.0
        section_heat.append(SectionHeat(
            section_id=sid,
            name=sec.name,  # type: ignore[attr-defined]
            subnet_count=bucket["subnet_count"],
            total_hosts=bucket["total_hosts"],
            used=bucket["used"],
            used_pct=pct,
        ))
    section_heat.sort(key=lambda x: x.used_pct, reverse=True)

    # ── audit 24h（稽核屬全域安全記錄，僅管理員可見彙總）──
    cutoff = datetime.now(UTC) - timedelta(hours=24)
    audit_24h = 0
    if user.is_admin:
        audit_24h = int(
            await session.scalar(
                select(func.count()).select_from(AuditLog).where(AuditLog.ts >= cutoff)
            )
            or 0
        )

    # ── 關係鏈各層總數（依可見範圍縮放；None=全部）──
    from app.models.device import Device
    from app.models.location import Location, Rack
    from app.models.virt import VirtualMachine

    async def _scoped_count(model: type, vis: set[uuid.UUID] | None) -> int:
        if vis is None:
            return int(await session.scalar(select(func.count()).select_from(model)) or 0)
        return len(vis)
    devices_n = await _scoped_count(Device, dev_vis)
    racks_n = await _scoped_count(Rack, rack_vis)
    locations_n = await _scoped_count(Location, loc_vis)
    # VM 無逐物件授權，掛在單位下；非萬用單位權限者只計自己單位的 VM
    if cust_vis is None:
        vms_n = int(await session.scalar(select(func.count()).select_from(VirtualMachine)) or 0)
    elif cust_vis:
        vms_n = int(await session.scalar(
            select(func.count()).select_from(VirtualMachine)
            .where(VirtualMachine.customer_id.in_(cust_vis))) or 0)
    else:
        vms_n = 0

    # ── 裝置類型分布（依可見裝置範圍）──
    dtype_stmt = select(Device.type, func.count()).group_by(Device.type)
    if dev_vis is not None:
        dtype_stmt = dtype_stmt.where(Device.id.in_(dev_vis)) if dev_vis else dtype_stmt.where(False)
    dtype_rows = (await session.execute(dtype_stmt)).all()
    device_types = sorted(
        [TypeCount(type=str(t or "other"), count=int(c)) for t, c in dtype_rows],
        key=lambda x: x.count, reverse=True,
    )

    # ── 各單位資源占比（subnets / ips / devices）──
    sub_by_cust = {str(r[0]): int(r[1]) for r in (await session.execute(
        select(Subnet.customer_id, func.count()).where(Subnet.customer_id.is_not(None))
        .group_by(Subnet.customer_id))).all()}
    ip_by_cust = {str(r[0]): int(r[1]) for r in (await session.execute(
        select(IPAddress.customer_id, func.count()).where(IPAddress.customer_id.is_not(None))
        .group_by(IPAddress.customer_id))).all()}
    dev_by_cust = {str(r[0]): int(r[1]) for r in (await session.execute(
        select(Device.customer_id, func.count()).where(Device.customer_id.is_not(None))
        .group_by(Device.customer_id))).all()}
    cust_ids = set(sub_by_cust) | set(ip_by_cust) | set(dev_by_cust)
    if cust_vis is not None:  # 非萬用單位權限 → 只留自己可見的單位
        allowed_cust = {str(x) for x in cust_vis}
        cust_ids &= allowed_cust
    customer_resources = sorted(
        [CustomerResource(
            customer_id=cid,
            label=cust_label.get(cid, cid[:8]),
            subnets=sub_by_cust.get(cid, 0),
            ips=ip_by_cust.get(cid, 0),
            devices=dev_by_cust.get(cid, 0),
        ) for cid in cust_ids],
        key=lambda x: (x.ips + x.subnets + x.devices), reverse=True,
    )[:10]

    # ── 近 14 日 稽核 / IP 異動 趨勢 ──
    from app.models.ip_change_log import IPChangeLog
    # 稽核趨勢僅管理員可見；IP 異動趨勢依可見子網路縮放
    audit_by_day: dict[str, int] = {}
    if user.is_admin:
        day = func.date_trunc("day", AuditLog.ts)
        audit_by_day = {str(r[0].date()): int(r[1]) for r in (await session.execute(
            select(day, func.count()).where(AuditLog.ts >= datetime.now(UTC) - timedelta(days=14))
            .group_by(day))).all()}
    day2 = func.date_trunc("day", IPChangeLog.created_at)
    ipc_stmt = (select(day2, func.count())
                .where(IPChangeLog.created_at >= datetime.now(UTC) - timedelta(days=14)))
    if visible_subnet_ids:
        ipc_stmt = ipc_stmt.where(IPChangeLog.subnet_id.in_(visible_subnet_ids))
    else:
        ipc_stmt = ipc_stmt.where(False)
    ipc_by_day = {str(r[0].date()): int(r[1]) for r in (await session.execute(
        ipc_stmt.group_by(day2))).all()}
    today = datetime.now(UTC).date()
    activity_trend = []
    for i in range(13, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        activity_trend.append(TrendPoint(day=d, audit=audit_by_day.get(d, 0), ip_changes=ipc_by_day.get(d, 0)))

    # ── 機櫃 U 使用率（依可見機櫃範圍）──
    rack_stmt = select(Rack.id, Rack.name, Rack.u_height)
    if rack_vis is not None:
        rack_stmt = rack_stmt.where(Rack.id.in_(rack_vis)) if rack_vis else rack_stmt.where(False)
    rack_rows = (await session.execute(rack_stmt)).all()
    # 半 U（左/右兩台同列）只算一列 → 以實際佔用的 U 列數計，避免 sum(u_size) 重複累加
    dev_rows = (await session.execute(
        select(Device.rack_id, Device.u_position, Device.u_size)
        .where(Device.rack_id.is_not(None), Device.u_position.is_not(None))
    )).all()
    occ_by_rack: dict[str, set[int]] = {}
    for rid, pos, sz in dev_rows:
        rows_set = occ_by_rack.setdefault(str(rid), set())
        for u in range(int(pos), int(pos) + int(sz or 1)):
            rows_set.add(u)
    used_u_by_rack = {k: len(v) for k, v in occ_by_rack.items()}
    rack_usage = sorted(
        [RackUsage(
            rack_id=str(rid), name=name,
            used_u=min(used_u_by_rack.get(str(rid), 0), uh or 0),
            total_u=uh or 0,
            pct=round(min(100.0, used_u_by_rack.get(str(rid), 0) / uh * 100), 1) if uh else 0.0,
        ) for rid, name, uh in rack_rows],
        key=lambda x: x.pct, reverse=True,
    )[:8]

    return DashboardOverview(
        sections=len(visible_sections),
        subnets=len(visible_subnets),
        addresses=used,
        total_capacity=total_capacity,
        used=used,
        used_pct=used_pct,
        status=status_counts,
        top_full_subnets=top_full,
        pinned_subnets=pinned,
        section_heat=section_heat,
        audit_24h=audit_24h,
        devices=devices_n,
        racks=racks_n,
        locations=locations_n,
        vms=vms_n,
        device_types=device_types,
        customer_resources=customer_resources,
        activity_trend=activity_trend,
        rack_usage=rack_usage,
    )
