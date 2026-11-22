"""機房的有效位置與聚合（交接包 physical-v1.2 SPEC.md 第 8、9 節，CABLING.md 機房統計，2026-11-21）。

有效位置（清單、總覽、裝置、電力、佈線共用這一套）：
1. 裝置在機櫃裡、機櫃有地點 → 算機櫃的地點
2. 裝置沒入櫃 → 算裝置自己的地點
3. 機櫃沒有地點 → 不靜默改用裝置欄位，列「機櫃地點未設定」
4. 裝置自己的地點跟機櫃不同 → 只算機櫃的地點一次，兩邊都列位置衝突（不自動改資料）
5. 看不到的機櫃或裝置 → 不計數、不列名，也不退回用裝置欄位（不可藉統計洩漏位置）

計算都在後端、批次查詢；不對前端已分頁的資料加總。
"""
from __future__ import annotations

import ipaddress
import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sqlin import in_values
from app.models.address import IPAddress
from app.models.device import Device
from app.models.location import Location, Rack
from app.models.physical import (
    Cable,
    CableTermination,
    DevicePort,
    PowerFeed,
    PowerPanel,
)
from app.models.user import User
from app.services.permission import visible_ids


def _ok(vis: set[uuid.UUID] | None, oid: uuid.UUID | None) -> bool:
    return oid is not None and (vis is None or oid in vis)


@dataclass
class Placed:
    id: uuid.UUID
    name: str
    type: str
    rack_id: uuid.UUID | None
    rack_name: str | None
    recorded_location_id: uuid.UUID | None
    status: str          # racked / unracked / conflict（算在這裡但記錄的地點不同）


@dataclass
class Scope:
    location_id: uuid.UUID
    racks: list[Any]
    counted: list[Placed]                 # 有效位置在這個機房的裝置
    conflicts_elsewhere: list[Placed]     # 記在這個機房、但機櫃在別的機房（算在那邊）
    rack_location_unset: list[Placed]     # 記在這個機房、機櫃卻沒有地點
    vis_dev: set[uuid.UUID] | None
    vis_rack: set[uuid.UUID] | None


async def scope(session: AsyncSession, user: User, location_id: uuid.UUID) -> Scope:
    vis_dev = await visible_ids(session, user=user, object_type="device")
    vis_rack = await visible_ids(session, user=user, object_type="rack")
    racks = [r for r in (await session.execute(
        select(Rack.id, Rack.name).where(Rack.location_id == location_id).order_by(Rack.name))).all()
        if _ok(vis_rack, r.id)]
    rack_ids = [r.id for r in racks]
    rack_name = {r.id: r.name for r in racks}
    counted: list[Placed] = []
    if rack_ids:
        for d in (await session.execute(select(Device.id, Device.name, Device.type, Device.rack_id, Device.location_id)
                                        .where(in_values(Device.rack_id, rack_ids)).order_by(Device.name))).all():
            if _ok(vis_dev, d.id):
                st = "conflict" if d.location_id is not None and d.location_id != location_id else "racked"
                counted.append(Placed(d.id, d.name, d.type, d.rack_id, rack_name.get(d.rack_id), d.location_id, st))
    for d in (await session.execute(select(Device.id, Device.name, Device.type, Device.location_id).where(
            Device.rack_id.is_(None), Device.location_id == location_id).order_by(Device.name))).all():
        if _ok(vis_dev, d.id):
            counted.append(Placed(d.id, d.name, d.type, None, None, d.location_id, "unracked"))
    elsewhere, unset = [], []
    for d in (await session.execute(
            select(Device.id, Device.name, Device.type, Device.rack_id, Rack.name.label("rack_name"),
                   Rack.location_id.label("rack_loc"))
            .join(Rack, Rack.id == Device.rack_id)
            .where(Device.location_id == location_id,
                   or_(Rack.location_id.is_(None), Rack.location_id != location_id))
            .order_by(Device.name))).all():
        if not (_ok(vis_dev, d.id) and _ok(vis_rack, d.rack_id)):
            continue
        p = Placed(d.id, d.name, d.type, d.rack_id, d.rack_name, location_id,
                   "rack_location_unset" if d.rack_loc is None else "conflict")
        (unset if d.rack_loc is None else elsewhere).append(p)
    counted.sort(key=lambda p: p.name.lower())
    return Scope(location_id, racks, counted, elsewhere, unset, vis_dev, vis_rack)


async def effective_device_counts(session: AsyncSession, user: User, location_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    """機房清單的裝置數：跟詳情頁同一套有效位置（機櫃的地點優先）。"""
    if not location_ids:
        return {}
    vis_dev = await visible_ids(session, user=user, object_type="device")
    vis_rack = await visible_ids(session, user=user, object_type="rack")
    out: dict[uuid.UUID, int] = {}
    rows = (await session.execute(
        select(Device.id, Rack.id.label("rack_id"), Rack.location_id.label("rack_loc"), Device.location_id)
        .outerjoin(Rack, Rack.id == Device.rack_id)
        .where(or_(in_values(Rack.location_id, location_ids),
                   (Device.rack_id.is_(None)) & in_values(Device.location_id, location_ids))))).all()
    for d in rows:
        if not _ok(vis_dev, d.id):
            continue
        if d.rack_id is not None:
            if not _ok(vis_rack, d.rack_id):
                continue
            loc = d.rack_loc
        else:
            loc = d.location_id
        out[loc] = out.get(loc, 0) + 1
    return out


async def _location_names(session: AsyncSession, ids: set[uuid.UUID | None]) -> dict[uuid.UUID, str]:
    ids2 = [i for i in ids if i]
    if not ids2:
        return {}
    return {i: n for i, n in (await session.execute(select(Location.id, Location.name).where(in_values(Location.id, ids2)))).all()}


def _natural(s: str | None) -> list[Any]:
    """自然排序（sw-2 在 sw-10 前面）。"""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s or "")]


async def device_rows(session: AsyncSession, user: User, sc: Scope, *, filt: str, q: str | None,
                      page: int, page_size: int, sort: str | None = None, order: str = "asc") -> dict[str, Any]:
    if filt == "conflict":
        rows = [p for p in sc.counted if p.status == "conflict"] + sc.conflicts_elsewhere
    elif filt == "rack_location_unset":
        rows = list(sc.rack_location_unset)
    elif filt in ("racked", "unracked"):
        rows = [p for p in sc.counted if (p.rack_id is not None) == (filt == "racked")]
    else:
        rows = list(sc.counted)
    if q:
        ql = q.lower()
        rows = [p for p in rows if ql in p.name.lower()]
    total = len(rows)
    ips: dict[uuid.UUID, list[dict[str, str]]] = {}
    first_ip: dict[uuid.UUID, Any] = {}

    async def load_ips(dev_ids: list[uuid.UUID]) -> None:
        if not dev_ids:
            return
        vis_sub = await visible_ids(session, user=user, object_type="subnet")
        for ip in (await session.execute(select(IPAddress.id, IPAddress.ip, IPAddress.device_id, IPAddress.subnet_id)
                                         .where(in_values(IPAddress.device_id, dev_ids)).order_by(IPAddress.ip))).all():
            if _ok(vis_sub, ip.subnet_id):
                ips.setdefault(ip.device_id, []).append({"id": str(ip.id), "ip": str(ip.ip)})
                first_ip.setdefault(ip.device_id, ipaddress.ip_interface(str(ip.ip)).ip)

    # 排序在分頁前做（後端分頁：只排畫面那一頁的話，換頁順序就亂了）；空值一律排最後
    if sort == "ips":
        await load_ips([p.id for p in rows])
    if sort:
        def key(p: Any) -> tuple[Any, ...]:
            if sort == "rack":
                v: Any = _natural(p.rack_name) if p.rack_name else None
            elif sort == "status":
                v = p.status
            elif sort == "ips":
                ip0 = first_ip.get(p.id)
                v = (ip0.version, int(ip0)) if ip0 is not None else None
            else:
                v = _natural(p.name)
            return (v is None, v if v is not None else 0, _natural(p.name))
        present = sorted([p for p in rows if key(p)[0] is False], key=key, reverse=order == "desc")
        rows = present + [p for p in rows if key(p)[0] is True]
    page_rows = rows[(page - 1) * page_size: page * page_size]
    ids = [p.id for p in page_rows]
    if sort != "ips":
        await load_ips(ids)
    names = await _location_names(session, {p.recorded_location_id for p in page_rows})
    # 機櫃在別的機房的那幾筆：標出算在哪裡
    other_rack_loc: dict[uuid.UUID, str] = {}
    ex_racks = [p.rack_id for p in page_rows if p in sc.conflicts_elsewhere and p.rack_id]
    if ex_racks:
        for rid, lname in (await session.execute(select(Rack.id, Location.name).join(Location, Location.id == Rack.location_id)
                                                 .where(in_values(Rack.id, ex_racks)))).all():
            other_rack_loc[rid] = lname
    return {"total": total, "page": page, "page_size": page_size, "items": [{
        "id": str(p.id), "name": p.name, "type": p.type, "status": p.status,
        "rack_id": str(p.rack_id) if p.rack_id else None, "rack": p.rack_name,
        "recorded_location": names.get(p.recorded_location_id) if p.recorded_location_id else None,
        "counted_location": other_rack_loc.get(p.rack_id) if p in sc.conflicts_elsewhere else None,
        "ips": ips.get(p.id, []),
    } for p in page_rows]}


async def summary(session: AsyncSession, user: User, sc: Scope) -> dict[str, Any]:
    from app.services.power_summary import allocate, totals
    ids = [p.id for p in sc.counted]
    n_ips = 0
    if ids:
        vis_sub = await visible_ids(session, user=user, object_type="subnet")
        q = select(func.count(IPAddress.id)).where(in_values(IPAddress.device_id, ids))
        if vis_sub is not None:
            q = q.where(in_values(IPAddress.subnet_id, vis_sub))
        n_ips = int((await session.execute(q)).scalar_one())
    allocs = await allocate(session, ids)
    pw = totals(allocs.values())
    loc = await session.get(Location, sc.location_id)
    return {
        "location_id": str(sc.location_id),
        "racks": len(sc.racks),
        "devices": {"total": len(ids), "racked": sum(1 for p in sc.counted if p.rack_id),
                    "unracked": sum(1 for p in sc.counted if not p.rack_id)},
        "ips": n_ips,
        "power": pw,
        "pending": {
            "unracked": sum(1 for p in sc.counted if not p.rack_id),
            "load_unknown": pw["unknown_devices"],
            "location_conflicts": sum(1 for p in sc.counted if p.status == "conflict") + len(sc.conflicts_elsewhere),
            "rack_location_unset": len(sc.rack_location_unset),
        },
        "has_floor_plan": bool(loc and loc.floor_plan_path),
        "rack_list": [{"id": str(r.id), "name": r.name,
                       "devices": sum(1 for p in sc.counted if p.rack_id == r.id),
                       **_rack_power(allocs, r.id)} for r in sc.racks],
    }


def _rack_power(allocs: dict[uuid.UUID, Any], rack_id: uuid.UUID) -> dict[str, Any]:
    from app.services.power_summary import totals
    t = totals([a for a in allocs.values() if a.rack_id == rack_id])
    return {"known_total_w": t["known_total_w"], "completeness": t["completeness"]}


async def power(session: AsyncSession, sc: Scope) -> dict[str, Any]:
    """機房內裝置的規劃負載，以及這些裝置用到的迴路（本機房份額 vs 全迴路）。"""
    from decimal import Decimal

    from app.services.power_summary import (
        FeedTotal,
        allocate,
        device_view,
        feed_totals,
        feed_view,
        num,
        totals,
    )
    ids = [p.id for p in sc.counted]
    allocs = await allocate(session, ids)
    feed_ids = {p.feed_id for a in allocs.values() if a.included for p in a.ports if p.feed_id}
    feed_ids |= set((await session.execute(select(PowerFeed.id).join(PowerPanel, PowerPanel.id == PowerFeed.panel_id)
                                           .where(PowerPanel.location_id == sc.location_id))).scalars())
    feeds = []
    if feed_ids:
        feeds = (await session.execute(select(PowerFeed, PowerPanel.name, PowerPanel.location_id)
                                       .join(PowerPanel, PowerPanel.id == PowerFeed.panel_id)
                                       .where(in_values(PowerFeed.id, feed_ids)).order_by(PowerPanel.name, PowerFeed.name))).all()
    full = await feed_totals(session, [f.id for f, *_ in feeds])
    mine: dict[uuid.UUID, Decimal] = {}
    for a in allocs.values():
        if a.included:
            for p in a.ports:
                if p.feed_id and p.watts is not None:
                    mine[p.feed_id] = mine.get(p.feed_id, Decimal(0)) + p.watts
    names = {f.id: f.name for f, *_ in feeds}
    return {
        **totals(allocs.values()),
        "feeds": [{**feed_view(f, panel, full.get(f.id, FeedTotal())), "location_allocated_w": num(mine.get(f.id, Decimal(0))),
                   # 配電盤在別的機房（或沒設）：跨機房供電，保留來源
                   "cross_location": panel_loc != sc.location_id} for f, panel, panel_loc in feeds],
        "devices": [device_view(a, names) for a in sorted(allocs.values(), key=lambda x: (not x.included, x.name.lower()))],
    }


async def cabling(session: AsyncSession, user: User, sc: Scope, *, limit: int = 500) -> dict[str, Any]:
    """機房的已接線纜線：房內（兩端都在這裡）、跨房（另一端已知在別的機房）、未明（另一端查不到位置或看不到）。
    以纜線 id 去重；規劃中、已除役不算。"""
    ids = {p.id for p in sc.counted}
    if not ids:
        return {"total": 0, "in_room": 0, "cross_room": 0, "unknown": 0, "items": [], "by_rack": []}
    my_ports = (await session.execute(select(DevicePort.id).where(in_values(DevicePort.device_id, list(ids))))).scalars().all()
    if not my_ports:
        return {"total": 0, "in_room": 0, "cross_room": 0, "unknown": 0, "items": [], "by_rack": []}
    cable_ids = (await session.execute(
        select(CableTermination.cable_id).join(Cable, Cable.id == CableTermination.cable_id)
        .where(CableTermination.object_type == "device_port", in_values(CableTermination.object_id, my_ports),
               Cable.status == "connected").distinct())).scalars().all()
    ends = (await session.execute(
        select(CableTermination.cable_id, CableTermination.side, DevicePort.id.label("port_id"), DevicePort.name.label("port"),
               Device.id.label("dev_id"), Device.name.label("dev"), Device.rack_id, Device.location_id,
               Rack.location_id.label("rack_loc"), Cable.label)
        .join(Cable, Cable.id == CableTermination.cable_id)
        .outerjoin(DevicePort, (DevicePort.id == CableTermination.object_id) & (CableTermination.object_type == "device_port"))
        .outerjoin(Device, Device.id == DevicePort.device_id)
        .outerjoin(Rack, Rack.id == Device.rack_id)
        .where(in_values(CableTermination.cable_id, cable_ids)))).all()
    by_cable: dict[uuid.UUID, list[Any]] = {}
    for e in ends:
        by_cable.setdefault(e.cable_id, []).append(e)
    loc_names = await _location_names(session, {e.rack_loc if e.rack_id else e.location_id for e in ends})
    rack_name = {r.id: r.name for r in sc.racks}

    def where(e: Any) -> uuid.UUID | None:
        """這一端的有效位置；看不到或不是連接埠 → None（未明）。"""
        if e.dev_id is None or not _ok(sc.vis_dev, e.dev_id):
            return None
        if e.rack_id is not None:
            return e.rack_loc if _ok(sc.vis_rack, e.rack_id) else None
        return e.location_id

    def label(e: Any) -> str | None:
        if e.dev_id is None or not _ok(sc.vis_dev, e.dev_id):
            return None
        return f"{e.dev}@{e.port}"

    def end(e: Any, side: str) -> dict[str, Any]:
        """畫面要做成「裝置@埠」連結：看不到的裝置不給名稱與 id。"""
        seen = e is not None and e.dev_id is not None and _ok(sc.vis_dev, e.dev_id)
        return {f"{side}_device_id": str(e.dev_id) if seen else None, f"{side}_device": e.dev if seen else None,
                f"{side}_port": e.port if seen else None}

    items, counts, per_rack = [], {"in_room": 0, "cross_room": 0, "unknown": 0}, {}
    for cid, es in by_cable.items():
        mine = [e for e in es if e.dev_id in ids]
        if not mine:
            continue
        others = [e for e in es if e not in mine] if len(mine) < len(es) else []
        if len(mine) == 2 or (len(es) == 2 and all(where(e) == sc.location_id for e in es)):
            kind = "in_room"
        elif others and where(others[0]) is not None:
            kind = "cross_room"
        else:
            kind = "unknown"
        counts[kind] += 1
        a = next((e for e in es if e.side == "A"), None)
        b = next((e for e in es if e.side == "B"), None)
        o = others[0] if others else None
        items.append({"id": str(cid), "label": es[0].label, "kind": kind, "a": label(a) if a else None,
                      "b": label(b) if b else None, **end(a, "a"), **end(b, "b"),
                      "other_location": loc_names.get(where(o)) if o is not None and kind == "cross_room" else None})
        for e in mine:
            if e.rack_id in rack_name:
                per_rack[e.rack_id] = per_rack.get(e.rack_id, set()) | {cid}
    items.sort(key=lambda x: (x["kind"], x["label"] or ""))
    return {"total": sum(counts.values()), **counts, "items": items[:limit], "truncated": len(items) > limit,
            "by_rack": [{"id": str(r), "rack": rack_name[r], "cables": len(c)} for r, c in sorted(
                per_rack.items(), key=lambda kv: rack_name[kv[0]])]}
