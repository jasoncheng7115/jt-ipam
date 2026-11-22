"""規劃電力的計算（交接包 physical-v1.2 SPEC.md 第 5 節；issue #54 重做，2026-11-21）。

三件事分開，不互相代替：
- PSU 額定容量（device_power_ports.max_watts）：不是負載，也不加總
- 整機規劃負載（device_power_profiles.planning_input_w）：整台只算一次；null＝未知，0＝明確填零
- 迴路規劃容量（power_feeds.planning_capacity_w）：人工確認；沒設就不算使用率

分配：整機負載 × 各顆電源的分攤比例。只有一顆電源時就是 100%；兩顆以上要管理員設好、加總剛好 100%，
否則整機負載列「分配未知」，不自動均分。接不到迴路（沒電源埠、沒接插座、插座沒迴路）列「未接線」。
已知總量守恆：known_total = assigned + unassigned + allocation_unknown。每台裝置內最後一顆吸收進位尾差，
所以分出去的加總剛好等於整機負載（結果可重現）。

資料不完整時不給「安全」：使用率標成「至少」（partial），狀態不會是 ok。
計算全部在後端、批次查詢（裝置數不限），不對前端已分頁的資料加總。
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sqlin import in_values
from app.models.device import Device
from app.models.physical import (
    DevicePowerPort,
    DevicePowerProfile,
    PowerFeed,
    PowerOutlet,
    PowerPanel,
)

CENT = Decimal("0.01")
HUNDRED = Decimal(100)
# 沒建規劃資料時預設不納入的被動元件（跳接面板不耗電）；管理員建了規劃資料就照設定
PASSIVE_TYPES = frozenset({"patch_panel"})


def num(d: Decimal | None) -> float | int | None:
    """輸出用：整數就給 int，否則兩位小數。內部一律 Decimal。"""
    if d is None:
        return None
    q = d.quantize(CENT, rounding=ROUND_HALF_UP)
    return int(q) if q == q.to_integral_value() else float(q)


@dataclass
class PortAlloc:
    port_id: uuid.UUID
    port: str
    feed_id: uuid.UUID | None
    outlet_rack_id: uuid.UUID | None
    watts: Decimal | None          # None＝這顆的分配未知


@dataclass
class DeviceAlloc:
    id: uuid.UUID
    name: str
    rack_id: uuid.UUID | None
    location_id: uuid.UUID | None
    included: bool
    exclude_reason: str | None
    load: Decimal | None
    basis: str | None
    ports: list[PortAlloc] = field(default_factory=list)
    assigned: Decimal = Decimal(0)
    unassigned: Decimal = Decimal(0)
    allocation_unknown: Decimal = Decimal(0)
    issues: list[str] = field(default_factory=list)


async def allocate(session: AsyncSession, device_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, DeviceAlloc]:
    """一批裝置的分配結果（批次查三次：裝置、規劃資料、電源埠＋插座）。"""
    ids = list(dict.fromkeys(device_ids))
    if not ids:
        return {}
    devs = (await session.execute(select(Device.id, Device.name, Device.type, Device.rack_id, Device.location_id)
                                  .where(in_values(Device.id, ids)))).all()
    profs = {p.device_id: p for p in (await session.execute(
        select(DevicePowerProfile).where(in_values(DevicePowerProfile.device_id, ids)))).scalars()}
    ports: dict[uuid.UUID, list[Any]] = {}
    for row in (await session.execute(
            select(DevicePowerPort.id, DevicePowerPort.device_id, DevicePowerPort.name, DevicePowerPort.normal_share_pct,
                   DevicePowerPort.max_watts, DevicePowerPort.rating_confirmed_at,
                   PowerOutlet.feed_id, PowerOutlet.rack_id.label("outlet_rack_id"))
            .outerjoin(PowerOutlet, PowerOutlet.id == DevicePowerPort.outlet_id)
            .where(in_values(DevicePowerPort.device_id, ids))
            .order_by(DevicePowerPort.name))).all():
        ports.setdefault(row.device_id, []).append(row)

    out: dict[uuid.UUID, DeviceAlloc] = {}
    for d in devs:
        prof = profs.get(d.id)
        included = prof.include_in_planning if prof else d.type not in PASSIVE_TYPES
        load = prof.planning_input_w if prof else None
        a = DeviceAlloc(id=d.id, name=d.name, rack_id=d.rack_id, location_id=d.location_id, included=included,
                        exclude_reason=(prof.exclude_reason if prof else None) or (None if included else "passive"),
                        load=load, basis=prof.value_basis if prof else None)
        plist = ports.get(d.id, [])
        if any(p.max_watts is not None and p.rating_confirmed_at is None for p in plist):
            a.issues.append("legacy_rating")
        if not plist:
            a.issues.append("no_power_ports")
        if any(p.feed_id is None for p in plist):
            a.issues.append("unconnected")
        feeds = {p.feed_id for p in plist if p.feed_id}
        if len(plist) >= 2 and len(feeds) == 1 and all(p.feed_id for p in plist):
            a.issues.append("single_feed")
        if load is None:
            a.issues.append("load_unknown")
            a.ports = [PortAlloc(p.id, p.name, p.feed_id, p.outlet_rack_id, None) for p in plist]
        else:
            shares = [Decimal(100)] if len(plist) == 1 else [p.normal_share_pct for p in plist]
            valid = bool(plist) and all(s is not None for s in shares) and sum(shares) == HUNDRED  # type: ignore[arg-type]
            if not plist:
                a.unassigned = load
            elif not valid:
                a.issues.append("share_unknown")
                a.allocation_unknown = load
                a.ports = [PortAlloc(p.id, p.name, p.feed_id, p.outlet_rack_id, None) for p in plist]
            else:
                given = Decimal(0)
                for i, (p, s) in enumerate(zip(plist, shares, strict=True)):
                    w = (load - given) if i == len(plist) - 1 else (load * s / HUNDRED).quantize(CENT, ROUND_HALF_UP)  # type: ignore[operator]
                    given += w
                    a.ports.append(PortAlloc(p.id, p.name, p.feed_id, p.outlet_rack_id, w))
                    if p.feed_id:
                        a.assigned += w
                    else:
                        a.unassigned += w
        out[d.id] = a
    return out


@dataclass
class FeedTotal:
    allocated: Decimal = Decimal(0)
    devices: set[uuid.UUID] = field(default_factory=set)
    racks: set[uuid.UUID] = field(default_factory=set)
    unknown_devices: set[uuid.UUID] = field(default_factory=set)   # 接在這一路但負載或分攤未知


async def feed_totals(session: AsyncSession, feed_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, FeedTotal]:
    """整條迴路的已知分配（所有機櫃、未入櫃的裝置都算），以及哪些裝置的份額未知。"""
    fids = list(dict.fromkeys(feed_ids))
    if not fids:
        return {}
    dev_ids = (await session.execute(
        select(DevicePowerPort.device_id).join(PowerOutlet, PowerOutlet.id == DevicePowerPort.outlet_id)
        .where(in_values(PowerOutlet.feed_id, fids)).distinct())).scalars().all()
    allocs = await allocate(session, dev_ids)
    want = set(fids)
    out = {f: FeedTotal() for f in fids}
    for a in allocs.values():
        if not a.included:
            continue
        for p in a.ports:
            if p.feed_id not in want:
                continue
            t = out[p.feed_id]  # type: ignore[index]
            t.devices.add(a.id)
            if a.rack_id:
                t.racks.add(a.rack_id)
            if p.watts is None:
                t.unknown_devices.add(a.id)
            else:
                t.allocated += p.watts
    return out


def feed_status(allocated: Decimal, capacity: Decimal | None, warning_pct: int, partial: bool) -> tuple[float | None, str]:
    """(使用率 %, 狀態)。狀態：no_capacity / over / warn / incomplete / ok —— 不完整就不會是 ok。"""
    if capacity is None or capacity <= 0:
        return None, "no_capacity"
    pct = (allocated / capacity * HUNDRED).quantize(CENT, ROUND_HALF_UP)
    if pct >= HUNDRED:
        return float(pct), "over"
    if pct >= warning_pct:
        return float(pct), "warn"
    return float(pct), "incomplete" if partial else "ok"


def feed_view(f: PowerFeed, panel: str | None, total: FeedTotal) -> dict[str, Any]:
    partial = bool(total.unknown_devices)
    pct, status = feed_status(total.allocated, f.planning_capacity_w, f.warning_pct, partial)
    return {
        "id": str(f.id), "name": f.name, "panel": panel, "rack_id": str(f.rack_id) if f.rack_id else None,
        "voltage_v": f.voltage_v, "amperage_a": f.amperage_a, "phase": f.phase, "supply_type": f.supply_type,
        "planning_capacity_w": num(f.planning_capacity_w), "warning_pct": f.warning_pct,
        "capacity_basis": f.capacity_basis,
        "full_allocated_w": num(total.allocated), "partial": partial, "unknown_devices": len(total.unknown_devices),
        "devices": len(total.devices), "racks": len(total.racks),
        "utilization_pct": pct, "status": status,
        # 三相沒有逐相資料：只能整體看，不能宣稱各相平衡
        "phase_balance_checked": False if f.phase == "three" else None,
    }


def device_view(a: DeviceAlloc, feed_names: dict[uuid.UUID, str]) -> dict[str, Any]:
    return {
        "id": str(a.id), "name": a.name, "included": a.included, "exclude_reason": a.exclude_reason,
        "load_w": num(a.load), "basis": a.basis,
        "allocations": [{"port": p.port, "feed_id": str(p.feed_id) if p.feed_id else None,
                         "feed": feed_names.get(p.feed_id) if p.feed_id else None, "watts": num(p.watts),
                         "cross_rack": bool(p.outlet_rack_id and a.rack_id and p.outlet_rack_id != a.rack_id)}
                        for p in a.ports],
        "unassigned_w": num(a.unassigned), "allocation_unknown_w": num(a.allocation_unknown),
        "issues": a.issues if a.included else [i for i in a.issues if i == "legacy_rating"],
    }


def totals(allocs: Iterable[DeviceAlloc]) -> dict[str, Any]:
    inc = [a for a in allocs if a.included]
    known = [a for a in inc if a.load is not None]
    return {
        "known_total_w": num(sum((a.load for a in known), Decimal(0))),   # type: ignore[misc]
        "assigned_w": num(sum((a.assigned for a in known), Decimal(0))),
        "unassigned_w": num(sum((a.unassigned for a in known), Decimal(0))),
        "allocation_unknown_w": num(sum((a.allocation_unknown for a in known), Decimal(0))),
        "completeness": {"known": len(known), "included": len(inc)} if inc else None,
        "unknown_devices": len(inc) - len(known),
        "partial": len(known) < len(inc) or any(a.allocation_unknown for a in known),
    }


async def _feeds(session: AsyncSession, ids: Iterable[uuid.UUID]) -> list[tuple[PowerFeed, str]]:
    fids = list(dict.fromkeys(ids))
    if not fids:
        return []
    return [(f, p) for f, p in (await session.execute(
        select(PowerFeed, PowerPanel.name).join(PowerPanel, PowerPanel.id == PowerFeed.panel_id)
        .where(in_values(PowerFeed.id, fids)).order_by(PowerPanel.name, PowerFeed.name))).all()]


async def rack_summary(session: AsyncSession, rack_id: uuid.UUID) -> dict[str, Any]:
    """機櫃視角：本櫃裝置的負載（依 device_id 只算一次，跟正背面、U 數、電源數無關）與各迴路的本櫃份額 / 全迴路。"""
    dev_ids = (await session.execute(select(Device.id).where(Device.rack_id == rack_id).order_by(Device.name))).scalars().all()
    allocs = await allocate(session, dev_ids)
    feed_ids = {p.feed_id for a in allocs.values() if a.included for p in a.ports if p.feed_id}
    feed_ids |= set((await session.execute(select(PowerFeed.id).where(PowerFeed.rack_id == rack_id))).scalars())
    feed_ids |= {f for f in (await session.execute(select(PowerOutlet.feed_id).where(
        PowerOutlet.rack_id == rack_id, PowerOutlet.feed_id.is_not(None)))).scalars()}
    feeds = await _feeds(session, feed_ids)
    full = await feed_totals(session, [f.id for f, _ in feeds])
    rack_part: dict[uuid.UUID, Decimal] = {}
    for a in allocs.values():
        if a.included:
            for p in a.ports:
                if p.feed_id and p.watts is not None:
                    rack_part[p.feed_id] = rack_part.get(p.feed_id, Decimal(0)) + p.watts
    names = {f.id: f.name for f, _ in feeds}
    rows = sorted(allocs.values(), key=lambda a: (not a.included, a.name.lower()))
    return {
        "rack_id": str(rack_id), "computed_at": datetime.now(UTC).isoformat(),
        **totals(allocs.values()),
        "feeds": [{**feed_view(f, panel, full.get(f.id, FeedTotal())),
                   "rack_allocated_w": num(rack_part.get(f.id, Decimal(0)))} for f, panel in feeds],
        "devices": [device_view(a, names) for a in rows],
    }


async def feed_summaries(session: AsyncSession, feeds: list[PowerFeed]) -> dict[uuid.UUID, dict[str, Any]]:
    """迴路清單用：每一路整條的已知分配、使用率與狀態。"""
    full = await feed_totals(session, [f.id for f in feeds])
    return {f.id: feed_view(f, None, full.get(f.id, FeedTotal())) for f in feeds}


async def feed_detail(session: AsyncSession, feed: PowerFeed) -> dict[str, Any]:
    """單一迴路：整條的負載，以及接在上面的每台裝置（含各機櫃、未入櫃）。"""
    dev_ids = (await session.execute(
        select(DevicePowerPort.device_id).join(PowerOutlet, PowerOutlet.id == DevicePowerPort.outlet_id)
        .where(PowerOutlet.feed_id == feed.id).distinct())).scalars().all()
    allocs = await allocate(session, dev_ids)
    panel = await session.get(PowerPanel, feed.panel_id)
    total = (await feed_totals(session, [feed.id])).get(feed.id, FeedTotal())
    rows = []
    for a in sorted(allocs.values(), key=lambda x: x.name.lower()):
        mine = [p for p in a.ports if p.feed_id == feed.id]
        known = [p.watts for p in mine if p.watts is not None]
        rows.append({"id": str(a.id), "name": a.name, "rack_id": str(a.rack_id) if a.rack_id else None,
                     "included": a.included, "ports": [p.port for p in mine],
                     "watts": num(sum(known, Decimal(0))) if len(known) == len(mine) else None})
    return {**feed_view(feed, panel.name if panel else None, total), "devices_detail": rows}
