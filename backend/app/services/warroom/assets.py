"""06 資產與影響：虛擬化叢集的 VM 方格、監控覆蓋缺口、IP 變更評估。

- 叢集（Proxmox／VMware／Hyper-V）每頁 PER_PAGE 個，VM 多的在前；一格一台 VM，依狀態排序
  （執行中、暫停或移轉中、已停止、不明）；太多時一格代表 N 台並標示。範本不算
- 「實體主機」＝叢集裡不重複的節點（沒有獨立的主機表）
- 虛擬化是全域基礎設施資料（REST 上也只給有全域讀取的人）：只被指派部分物件的帳號回 forbidden
- 監控覆蓋（管理員限定）：執行中、而且對得到 IPAM 位址的 VM，有沒有被各套已設定的監控／盤點系統涵蓋
  （Wazuh：有 active 代理；Zabbix：有監控中的主機；Checkmk：有這台主機；OCS：盤點過）。對不到位址的 VM 另外算，不當成缺口也不當成涵蓋
- IP 變更評估（管理員限定；功能關閉時回 disabled）：還在進行中的計畫，依最近一次評估的結論分
  有阻擋項／需要覆核／證據不完整
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sqlin import in_values
from app.models.virt import VirtCluster, VirtualMachine
from app.services.warroom.common import paginate, section, unit_for, waffle

PER_PAGE = 2
VM_CELLS = 120                   # 每個叢集最多幾格（20 欄 × 6 列）
COVER_CELLS = 40
VM_STATES = ("running", "other", "stopped", "unknown")
OPEN_LIFECYCLE = ("draft", "in_review", "approved", "in_progress")


def _vm_state(status: str | None) -> str:
    if status == "running":
        return "running"
    if status == "stopped":
        return "stopped"
    if status in ("paused", "migrating"):
        return "other"
    return "unknown"


async def _scope_clusters(session: AsyncSession, cfg: dict[str, Any]) -> set[Any] | None:
    """顯示範圍內的叢集：叢集掛的單位在範圍內，或有 VM 的主要位址落在範圍內的子網路。None＝沒有設範圍。"""
    from app.models.address import IPAddress
    from app.services.warroom.common import scope_subnet_ids

    subnets = await scope_subnet_ids(session, cfg)
    if subnets is None:
        return None
    out = {r[0] for r in (await session.execute(
        select(VirtualMachine.cluster_id).join(IPAddress, IPAddress.id == VirtualMachine.primary_ip_id)
        .where(in_values(IPAddress.subnet_id, list(subnets))).distinct()))} if subnets else set()
    customers = (cfg.get("scope") or {}).get("customers") or []
    if customers:
        import uuid
        out |= {r[0] for r in (await session.execute(select(VirtCluster.id).where(
            in_values(VirtCluster.customer_id, [uuid.UUID(c) for c in customers]))))}
    return out


async def _clusters(session: AsyncSession, page: int, only: set[Any] | None) -> tuple[dict[str, Any], int, int, int]:
    """(段落, 實體主機數, VM 數, 共幾頁)"""
    rows = (await session.execute(
        select(VirtualMachine.cluster_id, VirtualMachine.node, VirtualMachine.status, func.count())
        .where(VirtualMachine.is_template.is_(False),
               *([in_values(VirtualMachine.cluster_id, list(only))] if only is not None else []))
        .group_by(VirtualMachine.cluster_id, VirtualMachine.node, VirtualMachine.status))).all()
    clusters = {c.id: c for c in (await session.execute(select(VirtCluster))).scalars().all()}
    per: dict[Any, dict[str, Any]] = {}
    for cid, node, status, n in rows:
        c = per.setdefault(cid, {"counts": dict.fromkeys(VM_STATES, 0), "nodes": set()})
        c["counts"][_vm_state(status)] += int(n)
        if node:
            c["nodes"].add(node)
    items = []
    for cid, c in per.items():
        cl = clusters.get(cid)
        if cl is None:
            continue
        total = sum(c["counts"].values())
        unit = unit_for(total, VM_CELLS)
        items.append({
            "id": str(cid), "name": cl.name, "platform": cl.type, "standalone": cl.is_standalone,
            "hosts": len(c["nodes"]), "vms": total, "counts": c["counts"], "unit": unit,
            "cells": waffle([(s, c["counts"][s]) for s in VM_STATES], math.ceil(total / unit)),
        })
    items.sort(key=lambda i: (-i["vms"], i["name"]))
    hosts = sum(i["hosts"] for i in items)
    vms = sum(i["vms"] for i in items)
    shown, page, pages = paginate(items, PER_PAGE, page)
    return (section("ok" if items else "empty", items=shown, total=len(items), page=page, pages=pages),
            hosts, vms, pages)


async def _coverage(session: AsyncSession, only: set[Any] | None) -> tuple[dict[str, Any], int | None]:
    """(段落, 監控缺口：至少一套系統沒涵蓋的執行中 VM 數)"""
    from app.models.address import IPAddress
    from app.models.checkmk import CheckmkHost, CheckmkInstance
    from app.models.ocs import OcsServer
    from app.models.wazuh import WazuhAgent, WazuhInstance
    from app.models.zabbix import ZabbixHost, ZabbixInstance

    vm_ips = dict((await session.execute(
        select(VirtualMachine.id, VirtualMachine.primary_ip_id)
        .where(VirtualMachine.is_template.is_(False), VirtualMachine.status == "running",
               *([in_values(VirtualMachine.cluster_id, list(only))] if only is not None else [])))).all())
    with_ip = {vid: ip for vid, ip in vm_ips.items() if ip}
    no_ip = len(vm_ips) - len(with_ip)
    ip_ids = list(set(with_ip.values()))

    systems: list[tuple[str, set[Any]]] = []
    if await session.scalar(select(func.count()).select_from(WazuhInstance).where(WazuhInstance.enabled.is_(True))):
        covered = {r[0] for r in (await session.execute(select(WazuhAgent.jt_ipam_address_id).where(
            WazuhAgent.status == "active", in_values(WazuhAgent.jt_ipam_address_id, ip_ids)))).all()} if ip_ids else set()
        systems.append(("wazuh", covered))
    if await session.scalar(select(func.count()).select_from(ZabbixInstance).where(ZabbixInstance.enabled.is_(True))):
        covered = {r[0] for r in (await session.execute(select(ZabbixHost.jt_ipam_address_id).where(
            ZabbixHost.status == "monitored", in_values(ZabbixHost.jt_ipam_address_id, ip_ids)))).all()} if ip_ids else set()
        systems.append(("zabbix", covered))
    if await session.scalar(select(func.count()).select_from(CheckmkInstance).where(CheckmkInstance.enabled.is_(True))):
        covered = {r[0] for r in (await session.execute(select(CheckmkHost.jt_ipam_address_id).where(
            in_values(CheckmkHost.jt_ipam_address_id, ip_ids)))).all()} if ip_ids else set()
        systems.append(("checkmk", covered))
    if await session.scalar(select(func.count()).select_from(OcsServer).where(OcsServer.enabled.is_(True))):
        covered = {r[0] for r in (await session.execute(select(IPAddress.id).where(
            in_values(IPAddress.id, ip_ids),
            (IPAddress.ocs_id.is_not(None)) | (IPAddress.last_seen_ocs.is_not(None))))).all()} if ip_ids else set()
        systems.append(("ocs", covered))
    if not systems:
        return section("empty", systems=[], vms=len(vm_ips), no_ip=no_ip), None
    total = len(with_ip)
    out = []
    gap_vms: set[Any] = set()
    for kind, covered in systems:
        hit = sum(1 for ip in with_ip.values() if ip in covered)
        gap_vms |= {vid for vid, ip in with_ip.items() if ip not in covered}
        unit = max(1, math.ceil(total / COVER_CELLS))
        out.append({"kind": kind, "covered": hit, "gap": total - hit, "total": total, "unit": unit,
                    "cells": waffle([("covered", hit), ("gap", total - hit)], math.ceil(total / unit)) if total else []})
    return section("ok", systems=out, vms=len(vm_ips), with_ip=total, no_ip=no_ip), len(gap_vms)


async def _change(session: AsyncSession) -> dict[str, Any]:
    from app.models.change_impact import ChangePlan, ImpactRun
    from app.services.change_impact.config import get_config

    if not (await get_config(session)).get("enabled"):
        return section("disabled")
    rows = (await session.execute(
        select(ChangePlan.id, ImpactRun.decision_status, ImpactRun.completeness)
        .join(ImpactRun, ImpactRun.id == ChangePlan.latest_run_id, isouter=True)
        .where(ChangePlan.archived_at.is_(None), ChangePlan.lifecycle.in_(OPEN_LIFECYCLE)))).all()
    counts = {"blocked": 0, "needs_review": 0, "incomplete": 0, "not_run": 0}
    for _pid, decision, completeness in rows:
        if decision is None:
            counts["not_run"] += 1
            continue
        if decision == "blocked":
            counts["blocked"] += 1
        elif decision == "needs_review":
            counts["needs_review"] += 1
        if completeness in ("partial", "failed"):
            counts["incomplete"] += 1
    return section("ok" if rows else "empty", counts=counts, open=len(rows))


async def build(session: AsyncSession, *, user: Any, cfg: dict[str, Any], page: int = 1,
                now: datetime | None = None) -> dict[str, Any]:
    from app.mcp.tools import has_global_read

    now = now or datetime.now(UTC)
    from app.services.warroom.common import full_access
    admin = await full_access(session, user)
    if not await has_global_read(session, user):
        return {"key": "assets", "data_at": now.isoformat(), "page": 1, "pages": 1, "per_page": PER_PAGE,
                "metrics": {"hosts": None, "vms": None, "gaps": None}, "clusters_state": "forbidden",
                "clusters": [], "coverage": section("forbidden"), "change": section("forbidden")}
    only = await _scope_clusters(session, cfg)
    clusters, hosts, vms, pages = await _clusters(session, page, only)
    coverage, gaps = await _coverage(session, only) if admin else (section("forbidden"), None)
    return {
        "key": "assets", "data_at": now.isoformat(), "page": clusters["page"], "pages": pages,
        "per_page": PER_PAGE, "total": clusters["total"],
        "metrics": {"hosts": hosts, "vms": vms, "gaps": gaps},
        "clusters_state": clusters["state"], "clusters": clusters["items"],
        "coverage": coverage, "change": await _change(session) if admin else section("forbidden"),
    }
