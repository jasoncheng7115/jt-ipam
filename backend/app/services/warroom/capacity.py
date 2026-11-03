"""02 位址容量：每頁四個子網路／DHCP 集區，位址方格＋配置率（集區是有效租約使用率）。

- 子網路 ≤ 256 個位址：一格一個位址，照位址順序排（哪一段滿了看得出來）
- 更大的子網路與集區：一格代表 unit 個位址，依狀態按比例分格（common.waffle），畫面標「每格約 N 個」
- 配置率≥警戒門檻是 warn、≥危險門檻是 crit：警示另外標，不改變格子的顏色（配置狀態與風險狀態分開）
- 掃描代理逾時的子網路整塊畫空心格，不讓舊資料看起來像即時
- IPv6 子網路不畫（容量是天文數字），只在總數裡算已登記的位址
"""

from __future__ import annotations

import ipaddress
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.address import IPAddress
from app.services.warroom import subnets as sn
from app.services.warroom.common import MAX_CELLS, paginate, unit_for, waffle

PER_PAGE = 4


def _level(pct: float | None, warn: int, crit: int) -> str:
    if pct is None:
        return "ok"
    return "crit" if pct >= crit else "warn" if pct >= warn else "ok"


async def _address_cells(session: AsyncSession, f: sn.SubnetFacts) -> list[str]:
    net = ipaddress.ip_network(f.cidr, strict=False)
    first = int(net.network_address) + (0 if net.prefixlen >= 31 else 1)
    states = ["free"] * f.capacity
    for ip, state in (await session.execute(
            select(IPAddress.ip, IPAddress.state).where(IPAddress.subnet_id == f.id))).all():
        try:
            i = int(ipaddress.ip_address(str(ip).split("/")[0])) - first
        except ValueError:
            continue
        if 0 <= i < f.capacity:
            states[i] = "reserved" if state == "reserved" else "allocated"
    return states


async def pool_items(session: AsyncSession, visible: set[Any] | None, warn: int, crit: int,
                     names: dict[Any, str] | None = None) -> list[dict[str, Any]]:
    """DHCP 集區卡片。標題跟子網路卡片一樣用子網路名稱（10-22 使用者：「有的是顯示子網路名稱、有的是顯示閘道名稱」），
    發放的 DHCP 伺服器放在 server。names：子網路 id → 顯示名稱（沒有就用集區所在的 CIDR）。"""
    from app.services.dhcp_usage import pool_subnet, pool_usage

    out: list[dict[str, Any]] = []
    for pool, used, size in await pool_usage(session):
        if not size:
            continue
        sid = await pool_subnet(session, pool)
        if sid is None or (visible is not None and sid not in visible):
            continue                       # 不知道屬於哪個子網路、或看不到那個子網路：不列
        pct = round(used * 100 / size, 1)
        unit = unit_for(size)
        segs = getattr(pool, "segments", None) or [(pool.start_ip, pool.end_ip)]
        out.append({
            "kind": "pool", "id": str(getattr(pool, "id", "") or f"{pool.start_ip}-{pool.end_ip}"),
            "label": getattr(pool, "subnet_cidr", None) or "DHCP", "_sid": sid,
            "server": getattr(pool, "source_name", None) or getattr(pool, "name", None),
            "ident": f"{segs[0][0]} - {segs[-1][1]}", "cidr": getattr(pool, "subnet_cidr", None),
            "capacity": size, "used": used, "reserved": 0, "free": max(size - used, 0), "pct": pct,
            "level": _level(pct, warn, crit), "scan": "ok", "unit": unit, "order": "state",
            "cells": waffle([("allocated", used), ("free", max(size - used, 0))], -(-size // unit)),
        })
    if names is None and out:
        from app.core.sqlin import in_values
        from app.models.subnet import Subnet
        sids = list({it["_sid"] for it in out})
        names = {sid: (desc or "").strip() for sid, desc in (await session.execute(
            select(Subnet.id, Subnet.description).where(in_values(Subnet.id, sids)))).all()}
    for it in out:
        name = (names or {}).get(it.pop("_sid"))
        if name:
            it["label"] = name
    return out


async def build(session: AsyncSession, *, user: Any, cfg: dict[str, Any], page: int = 1,
                now: datetime | None = None) -> dict[str, Any]:
    from app.services.warroom.common import scoped_visibility

    now = now or datetime.now(UTC)
    warn, crit = cfg["capacity_warn"], cfg["capacity_crit"]
    visible = await scoped_visibility(session, user, cfg)
    facts = await sn.load(session, visible, now=now)
    v4 = [f for f in facts if f.version == 4 and f.capacity > 0]

    items: list[dict[str, Any]] = [{
        "kind": "subnet", "id": str(f.id), "label": f.label, "ident": f.cidr, "cidr": f.cidr,
        "capacity": f.capacity, "used": f.used, "reserved": f.reserved,
        "free": max(f.capacity - f.used, 0), "pct": f.pct, "level": _level(f.pct, warn, crit),
        "scan": f.scan, "unit": unit_for(f.capacity),
        "order": "address" if f.capacity <= MAX_CELLS else "state", "_facts": f,
    } for f in v4]
    items += await pool_items(session, visible, warn, crit, {f.id: f.label for f in facts})
    # 最需要注意的先出現：使用率高的在前；同使用率依名稱
    items.sort(key=lambda it: (-(it["pct"] or 0), it["label"]))

    shown, page, pages = paginate(items, PER_PAGE, page)
    for it in shown:
        f = it.pop("_facts", None)
        if it["kind"] == "subnet" and f is not None:
            if it["order"] == "address":
                it["cells"] = await _address_cells(session, f)
            else:
                allocated = max(f.used - f.reserved, 0)
                it["cells"] = waffle([("allocated", allocated), ("reserved", f.reserved),
                                      ("free", it["free"])], -(-f.capacity // it["unit"]))
    for it in items:
        it.pop("_facts", None)

    return {
        "key": "capacity", "data_at": now.isoformat(), "page": page, "pages": pages, "per_page": PER_PAGE,
        "metrics": {
            "registered_ips": sum(f.used for f in facts),
            "high_util_subnets": sum(1 for f in v4 if (f.pct or 0) >= warn),
            "pools_alert": sum(1 for it in items if it["kind"] == "pool" and it["level"] == "crit"),
            # 資訊展示模式的標題列用（info_mode）
            "subnets": len(facts), "pools": sum(1 for it in items if it["kind"] == "pool"),
        },
        "thresholds": {"warn": warn, "crit": crit},
        "items": shown, "total": len(items),
        "ipv6_subnets": sum(1 for f in facts if f.version == 6),
    }
