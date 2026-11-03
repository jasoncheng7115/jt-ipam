"""01 維運總覽：依站台（地點／頂層區段／客戶）分組的子網路狀態矩陣＋優先處理事項。

- 一格一個子網路，依狀態排序（正常、容量警戒、異常、資料逾時、未掃描）；一個站台超過 MAX_GROUP_CELLS
  個子網路時改成一格代表多個（畫面標「每格約 N 個子網路」），旁邊的計數一律是精確數字
- 狀態優先順序：異常 > 容量警戒 > 資料逾時 > 未掃描 > 正常（一個子網路只算一種）
- 異常與優先事項是管理員限定的資料（與異常偵測頁同一條線）：非管理員那一段回 forbidden、不帶數量，
  子網路格子也不標異常
- 站台排序：需要注意的（異常＋警戒＋逾時多的）在前
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sqlin import in_values
from app.models.anomaly_observation import AnomalyObservation
from app.services.warroom import subnets as sn
from app.services.warroom.common import (
    actionable_observation,
    observation_in_subnets,
    paginate,
    scope_active,
    scoped_visibility,
    section,
    waffle,
)

PER_PAGE = 3
MAX_GROUP_CELLS = 48              # 12 欄 × 4 列
STATE_ORDER = ("ok", "warn", "anomaly", "stale", "unscanned")
#: 優先事項的嚴重度（其餘類別算 medium）
HIGH = {"ip_conflicts", "rogue_dhcp", "unauthorized_ips"}
CERT_SOON_DAYS = 14


def _state(f: sn.SubnetFacts, anomalies: int, warn: int) -> str:
    if anomalies:
        return "anomaly"
    if f.pct is not None and f.pct >= warn:
        return "warn"
    if f.scan == "stale":
        return "stale"
    if f.scan == "unscanned":
        return "unscanned"
    return "ok"


async def _anomalies_by_subnet(session: AsyncSession, ids: list[Any]) -> dict[Any, int]:
    """還沒消失的異常觀測，算到子網路上：有 subnet_id 的直接算；只有 IP 的算到最小的包含網段。"""
    if not ids:
        return {}
    out: dict[Any, int] = {}
    for sid, n in (await session.execute(
            select(AnomalyObservation.subnet_id, func.count()).where(
                AnomalyObservation.resolved_at.is_(None), actionable_observation(), in_values(AnomalyObservation.subnet_id, ids))
            .group_by(AnomalyObservation.subnet_id))).all():
        out[sid] = out.get(sid, 0) + int(n)
    rows = (await session.execute(text("""
        SELECT sid, count(*) FROM (
          SELECT DISTINCT ON (o.id) o.id, s.id AS sid
            FROM anomaly_observations o JOIN subnets s ON s.cidr >>= o.ip
           WHERE o.resolved_at IS NULL AND o.subnet_id IS NULL AND o.ip IS NOT NULL AND s.id = ANY(:ids)
           ORDER BY o.id, masklen(s.cidr) DESC) x
         GROUP BY sid
    """), {"ids": ids})).all()
    for sid, n in rows:
        out[sid] = out.get(sid, 0) + int(n)
    return out


async def _group_labels(session: AsyncSession, group_by: str,
                        facts: list[sn.SubnetFacts]) -> tuple[dict[Any, str], dict[Any, Any]]:
    """(分組 id → 名稱, 區段 id → 上層區段 id)；後者只有依區段分組時才有。"""
    if group_by == "location":
        from app.models.location import Location
        ids = {f.location_id for f in facts if f.location_id}
        return ({r[0]: r[1] for r in (await session.execute(
            select(Location.id, Location.name).where(in_values(Location.id, list(ids))))).all()} if ids else {}), {}
    if group_by == "customer":
        from app.models.customer import Customer
        ids = {f.customer_id for f in facts if f.customer_id}
        return ({r[0]: (r[2] or r[1]) for r in (await session.execute(
            select(Customer.id, Customer.name, Customer.title).where(in_values(Customer.id, list(ids))))).all()}
            if ids else {}), {}
    from app.models.section import Section
    rows = (await session.execute(select(Section.id, Section.name, Section.parent_id))).all()
    return {r[0]: r[1] for r in rows}, {r[0]: r[2] for r in rows}


def _group_key(group_by: str, f: sn.SubnetFacts, parents: dict[Any, Any]) -> Any:
    if group_by == "location":
        return f.location_id
    if group_by == "customer":
        return f.customer_id
    sid, guard = f.section_id, 0
    while parents.get(sid) is not None and guard < 64:   # 走到頂層區段
        sid, guard = parents[sid], guard + 1
    return sid


async def _priority(session: AsyncSession, now: datetime, scope_ids: set[Any] | None = None) -> dict[str, Any]:
    from app.models.certificate import Certificate, CertVersion
    from app.models.dns_compare_group import DNSCompareGroup

    items: list[dict[str, Any]] = []
    for o in (await session.execute(
            select(AnomalyObservation).where(AnomalyObservation.resolved_at.is_(None), actionable_observation(),
                                             *([observation_in_subnets(scope_ids)] if scope_ids is not None else []))
            .order_by(AnomalyObservation.first_seen_at.desc()).limit(200))).scalars().all():
        items.append({"kind": "anomaly", "category": o.category, "value": o.label or "",
                      "ip": str(o.ip) if o.ip else None,
                      "severity": "high" if o.category in HIGH else "medium",
                      "at": o.first_seen_at.isoformat()})
    soon = now + timedelta(days=CERT_SOON_DAYS)
    for name, not_after in (await session.execute(
            select(Certificate.name, CertVersion.not_after).join(CertVersion, CertVersion.certificate_id == Certificate.id)
            .where(CertVersion.is_current.is_(True), CertVersion.not_after <= soon))).all():
        days = math.floor((not_after - now).total_seconds() / 86400)
        items.append({"kind": "cert", "value": name, "days": days,
                      "severity": "high" if days <= 7 else "medium", "at": not_after.isoformat()})
    for name, checked in (await session.execute(
            select(DNSCompareGroup.name, DNSCompareGroup.last_checked_at)
            .where(DNSCompareGroup.last_status == "mismatch"))).all():
        items.append({"kind": "dns", "value": name, "severity": "medium",
                      "at": checked.isoformat() if checked else None})
    rank = {"high": 0, "medium": 1}
    items.sort(key=lambda i: (rank[i["severity"]], -(datetime.fromisoformat(i["at"]).timestamp() if i.get("at") else 0)))
    shown = items[:3]
    # 判斷線索（MAC 廠牌、VM、DNS 名稱）：只查畫面上這幾筆（hints.py）
    from app.services.warroom.hints import ip_hints
    hints = await ip_hints(session, [i["ip"] for i in shown if i.get("ip")])
    for i in shown:
        if i.get("ip") in hints:
            i["hints"] = hints[i["ip"]]
    return section("ok" if items else "empty", items=shown, total=len(items))


async def build(session: AsyncSession, *, user: Any, cfg: dict[str, Any], page: int = 1,
                now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    from app.services.warroom.common import full_access
    admin = await full_access(session, user)
    warn = cfg["capacity_warn"]
    group_by = cfg["group_by"]
    visible = await scoped_visibility(session, user, cfg)
    facts = await sn.load(session, visible, now=now)
    anomalies = await _anomalies_by_subnet(session, [f.id for f in facts]) if admin else {}
    labels, parents = await _group_labels(session, group_by, facts)

    groups: dict[Any, dict[str, Any]] = {}
    for f in facts:
        key = _group_key(group_by, f, parents)
        g = groups.setdefault(key, {"id": str(key) if key else None, "name": labels.get(key) if key else None,
                                    "counts": dict.fromkeys(STATE_ORDER, 0), "subnets": 0})
        g["counts"][_state(f, anomalies.get(f.id, 0), warn)] += 1
        g["subnets"] += 1

    out_groups = list(groups.values())
    for g in out_groups:
        c = g["counts"]
        g["attention"] = c["anomaly"] * 100 + c["warn"] * 10 + c["stale"]
        unit = max(1, math.ceil(g["subnets"] / MAX_GROUP_CELLS))
        g["unit"] = unit
        g["cells"] = waffle([(s, c[s]) for s in STATE_ORDER], math.ceil(g["subnets"] / unit))
    # 有名字的站台在前、「未指定」放最後；需要注意的先出現
    out_groups.sort(key=lambda g: (g["id"] is None, -g["attention"], g["name"] or ""))
    shown, page, pages = paginate(out_groups, PER_PAGE, page)
    for g in shown:
        g.pop("attention", None)

    scope_ids = {f.id for f in facts} if scope_active(cfg) else None
    open_total = int(await session.scalar(select(func.count()).select_from(AnomalyObservation).where(
        AnomalyObservation.resolved_at.is_(None), actionable_observation(),
        *([observation_in_subnets(scope_ids)] if scope_ids is not None else []))) or 0) if admin else None
    return {
        "key": "overview", "data_at": now.isoformat(), "page": page, "pages": pages, "per_page": PER_PAGE,
        "group_by": group_by,
        "metrics": {
            "registered_ips": sum(f.used for f in facts),
            "open_anomalies": open_total,                  # None＝無權限（畫面不顯示數字）
            "capacity_alerts": sum(1 for f in facts if f.pct is not None and f.pct >= warn),
            "subnets": len(facts),                          # 資訊展示模式的標題列用（info_mode）
        },
        "groups": shown, "total": len(out_groups),
        "priority": await _priority(session, now, scope_ids) if admin else section("forbidden"),
    }
