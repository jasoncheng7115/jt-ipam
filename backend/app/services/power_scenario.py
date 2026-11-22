"""雙路失效模擬（交接包 physical-v1.2 SPEC.md 第 6 節、P3，2026-11-21）。

唯讀計算：假設某幾條迴路整條斷電，重算所有接在上面的裝置（各機櫃、未入櫃都算），以及存活迴路的新負載。
每台裝置的結果只有四種：
- transferable：失效那顆本來就分到 0 W（照常運作）；或明確的雙路（兩顆電源）、管理員確認單顆可撐整機（confirmed_full_load）、另一顆接在存活的迴路上
  → 整機負載移到存活那一路
- loses_power：電源全在失效的迴路上（包括兩顆接同一路，P13）、或確認單顆撐不住（insufficient）
- insufficient_capacity：可轉移，但接手的迴路超過規劃容量
- undetermined：三顆以上、自訂模式、負載未知、沒確認能不能撐、另一顆沒接線、只有舊的插座直連（P14）

存活迴路的新負載從頭算：沒受影響的裝置照原本份額、可轉移的整機移過去、斷電的歸零、
無法判斷的不算進去（可能轉過來也可能斷電），所以只能當「至少」，狀態不會是 ok。
不同迴路只代表迴路不同，不代表上游 UPS / 配電盤獨立；規劃值也不是即時量測（assumptions 帶回去給畫面與 AI 講）。
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sqlin import in_values
from app.models.physical import (
    DevicePowerPort,
    DevicePowerProfile,
    PowerFeed,
    PowerOutlet,
    PowerPanel,
)
from app.services.power_summary import FeedTotal, allocate, feed_status, feed_totals, num

OUTCOMES = ("transferable", "loses_power", "insufficient_capacity", "undetermined")
ASSUMPTIONS = ("planning_values_not_measurements", "feeds_not_proven_independent", "whole_feed_fails")


@dataclass
class _After:
    watts: Decimal = Decimal(0)
    devices: set[uuid.UUID] = field(default_factory=set)
    unknown: set[uuid.UUID] = field(default_factory=set)
    incoming: set[uuid.UUID] = field(default_factory=set)   # 失效後移過來的裝置


async def preview_failure(session: AsyncSession, failed_ids: Iterable[uuid.UUID]) -> dict[str, Any] | None:
    """失效模擬；有不存在的迴路回 None。"""
    failed = set(failed_ids)
    feeds = {f.id: (f, p) for f, p in (await session.execute(
        select(PowerFeed, PowerPanel.name).join(PowerPanel, PowerPanel.id == PowerFeed.panel_id)
        .where(in_values(PowerFeed.id, failed)))).all()}
    if set(feeds) != failed:
        return None

    via_psu = set((await session.execute(
        select(DevicePowerPort.device_id).join(PowerOutlet, PowerOutlet.id == DevicePowerPort.outlet_id)
        .where(in_values(PowerOutlet.feed_id, failed)))).scalars())
    # 只有早期插座直連（outlet.device_id）、沒有電源埠接到失效迴路的：不知道是哪顆電源，無法判斷
    legacy: set[uuid.UUID] = {d for d in (await session.execute(
        select(PowerOutlet.device_id).where(in_values(PowerOutlet.feed_id, failed),
                                            PowerOutlet.device_id.is_not(None)))).scalars() if d} - via_psu
    allocs = await allocate(session, via_psu | legacy)
    profs = {p.device_id: p for p in (await session.execute(
        select(DevicePowerProfile).where(in_values(DevicePowerProfile.device_id, via_psu)))).scalars()}

    rows: list[dict[str, Any]] = []
    moves: dict[uuid.UUID, uuid.UUID] = {}      # 可轉移的裝置 → 接手的迴路
    stays: set[uuid.UUID] = set()               # 失效那顆本來就 0 W：負載不必移
    status_of: dict[uuid.UUID, tuple[str, str | None]] = {}
    for a in allocs.values():
        prof = profs.get(a.id)
        alive = [p for p in a.ports if p.feed_id not in failed]
        res: tuple[str, str | None]
        if a.id in legacy:
            res = ("undetermined", "legacy_outlet_link")
        elif not alive:
            res = ("loses_power", "single_supply" if len(a.ports) == 1 else "all_supplies_failed")
        elif len(a.ports) > 2:
            res = ("undetermined", "unsupported_multi_supply")
        elif prof is not None and prof.supply_mode == "custom":
            res = ("undetermined", "unsupported_mode")
        elif alive[0].feed_id is None:
            res = ("undetermined", "path_incomplete")
        elif a.load is not None and all(p.watts == 0 for p in a.ports if p.feed_id in failed):
            # 失效那顆本來就沒分到負載（例如主備的備援在失效那一路）：照常運作，負載原樣留在存活迴路
            res = ("transferable", "failed_supply_idle")
            stays.add(a.id)
        elif prof is None or prof.failover_capability == "unknown":
            res = ("undetermined", "failover_unconfirmed")
        elif prof.failover_capability == "insufficient":
            res = ("loses_power", "failover_insufficient")
        elif a.load is None:
            res = ("undetermined", "load_unknown")
        else:
            res = ("transferable", None)
            moves[a.id] = alive[0].feed_id
        status_of[a.id] = res

    # 存活迴路：接過裝置的、或有受影響裝置接在上面的；整條重算（包含沒受影響的裝置）
    survivors = {p.feed_id for a in allocs.values() for p in a.ports if p.feed_id and p.feed_id not in failed}
    before = await feed_totals(session, survivors)
    on_survivors = set((await session.execute(
        select(DevicePowerPort.device_id).join(PowerOutlet, PowerOutlet.id == DevicePowerPort.outlet_id)
        .where(in_values(PowerOutlet.feed_id, survivors)))).scalars()) if survivors else set()
    everyone = {**await allocate(session, on_survivors - set(allocs)), **allocs}
    after = {f: _After() for f in survivors}
    for a in everyone.values():
        if not a.included:
            continue
        outcome = "unaffected" if a.id in stays else status_of.get(a.id, ("unaffected", None))[0]
        for p in a.ports:
            if p.feed_id not in after:
                continue
            t = after[p.feed_id]
            t.devices.add(a.id)
            if outcome == "unaffected":
                if p.watts is None:
                    t.unknown.add(a.id)
                else:
                    t.watts += p.watts
            elif outcome == "transferable":
                t.watts += a.load  # type: ignore[operator]
                t.incoming.add(a.id)
            elif outcome == "undetermined":
                t.unknown.add(a.id)
            # loses_power：不再耗電

    survivor_feeds = {f.id: (f, p) for f, p in (await session.execute(
        select(PowerFeed, PowerPanel.name).join(PowerPanel, PowerPanel.id == PowerFeed.panel_id)
        .where(in_values(PowerFeed.id, survivors)))).all()} if survivors else {}
    feed_rows = []
    over: set[uuid.UUID] = set()
    for fid, t in after.items():
        f, panel = survivor_feeds[fid]
        partial = bool(t.unknown)
        pct, st = feed_status(t.watts, f.planning_capacity_w, f.warning_pct, partial)
        if st == "over":
            over.add(fid)
        b = before.get(fid, FeedTotal())
        feed_rows.append({
            "id": str(fid), "name": f.name, "panel": panel,
            "planning_capacity_w": num(f.planning_capacity_w), "warning_pct": f.warning_pct,
            "before_w": num(b.allocated), "before_partial": bool(b.unknown_devices),
            "after_w": num(t.watts), "added_w": num(t.watts - b.allocated),
            "partial": partial, "unknown_devices": len(t.unknown), "incoming_devices": len(t.incoming),
            "utilization_pct": pct, "status": st,
        })
    feed_rows.sort(key=lambda r: (r["panel"] or "", r["name"]))

    names = {fid: f.name for fid, (f, _p) in {**survivor_feeds, **feeds}.items()}
    counts = dict.fromkeys(OUTCOMES, 0)
    lost = Decimal(0)
    lost_unknown = 0
    for a in sorted(allocs.values(), key=lambda x: x.name.lower()):
        outcome, reason = status_of[a.id]
        alive = [p for p in a.ports if p.feed_id and p.feed_id not in failed]
        # 接手（或本來就在供電）的迴路超過規劃容量：整條會跳，這台也撐不住
        if outcome == "transferable" and ({moves[a.id]} if a.id in moves else {p.feed_id for p in alive}) & over:
            outcome, reason = "insufficient_capacity", "survivor_over_capacity"
        counts[outcome] += 1
        if outcome == "loses_power" and a.included:
            if a.load is None:
                lost_unknown += 1
            else:
                lost += a.load
        rows.append({
            "id": str(a.id), "name": a.name, "rack_id": str(a.rack_id) if a.rack_id else None,
            "included": a.included, "load_w": num(a.load), "outcome": outcome, "reason": reason,
            "supplies": len(a.ports),
            "failed_supplies": [p.port for p in a.ports if p.feed_id in failed],
            "to_feeds": [{"id": str(moves[a.id]), "name": names.get(moves[a.id])}] if a.id in moves else [],
            "surviving_feeds": sorted({names.get(p.feed_id, "") for p in alive}),
        })
    return {
        "computed_at": datetime.now(UTC).isoformat(),
        "failed_feeds": sorted(({"id": str(f.id), "name": f.name, "panel": p} for f, p in feeds.values()),
                               key=lambda r: (r["panel"] or "", r["name"])),
        "counts": counts, "lost_load_w": num(lost), "lost_unknown_devices": lost_unknown,
        "devices": rows, "feeds": feed_rows, "assumptions": list(ASSUMPTIONS),
    }
