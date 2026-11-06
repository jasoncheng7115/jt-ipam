"""M2：交換器維護、虛擬化節點停機、服務依賴（規格 §6）。

只用明確的資料下判斷：
- 網路：實體佈線（device_ports ＋ cable_terminations，跳接面板的 front/rear 會穿透）是明確的連線。
  一台裝置所有已知的連線都經過停機的裝置 → 模型內中斷；還有別條連線 → 備援待驗證（不知道 bond／LAG／VLAN
  的健康狀態，兩條線不等於 1-of-2）；別條連線的對端本身也只接到停機的裝置 → 共用上游，不算獨立備援。
  交換器 MAC 表（FDB）只能推定，最多是「可能中斷」。
- 工作負載：節點上的虛擬機以節點名稱對應裝置名稱（推定，跟 M1 的除役一樣）；執行中的停機時跟著停，
  已停止的只列關聯。HA 只代表「有設定」，容量、quorum、儲存、網路沒有資料就是備援待驗證，不保證會恢復。
- 服務：每個依賴群組都必要，群組內 required_count 個成員可用才滿足；三值邏輯（可用／不可用／未知），
  不硬轉成布林。服務之間的依賴有循環時，循環裡的服務一律未知並回報，不無限遞迴。
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import text

from app.services.change_impact.context import Ctx
from app.services.change_impact.matching import text_mentions
from app.services.change_impact.model import Evidence, Finding

UNAVAILABLE, UNKNOWN, AVAILABLE = "unavailable", "unknown", "available"
_MAX_HOPS = 8              # 跳接面板穿透的上限
_MAX_DEPENDENTS = 2000     # 一次維護最多分析幾台相連的裝置
_FDB_UPLINK_MACS = 32      # 一個埠學到超過這麼多 MAC 視為上行埠（後面是整段網路，不是一台機器）
_PASS_THROUGH = ("front", "rear")
# 依賴成員的類型 → 發現主體的類型（關係圖的節點用同一套命名）
_SUBJECT = {"device": "device", "vm": "virtual_machine", "ip": "ip_address", "subnet": "subnet", "service": "service"}


def _ref(kind: str, oid: Any) -> str:
    return f"{kind}:{oid}"


def _worse(a: str | None, b: str) -> str:
    rank = {AVAILABLE: 0, UNKNOWN: 1, UNAVAILABLE: 2}
    return b if a is None or rank[b] > rank[a] else a


def _mark(ctx: Ctx, ref: str, status: str) -> None:
    ctx.status[ref] = _worse(ctx.status.get(ref), status)


# ─────────────────── 實體佈線 ───────────────────

class _Cabling:
    """沿纜線找到對端裝置的埠；跳接面板的 front/rear 互指（peer_port_id）會穿透。結果快取。"""

    def __init__(self, ctx: Ctx) -> None:
        self.ctx = ctx
        self.cache: dict[uuid.UUID, tuple[uuid.UUID, str, uuid.UUID, str, list[str]] | None] = {}
        self.ports_of: dict[uuid.UUID, list[Any]] = {}
        self.dev_names: dict[uuid.UUID, str] = {}

    async def ports(self, device_id: uuid.UUID) -> list[Any]:
        if device_id not in self.ports_of:
            self.ports_of[device_id] = list((await self.ctx.session.execute(text("""
                SELECT id, name, type, peer_port_id FROM device_ports
                 WHERE device_id = :d AND type NOT IN ('power', 'console') ORDER BY name
            """), {"d": device_id})).all())
        return self.ports_of[device_id]

    async def name(self, device_id: uuid.UUID) -> str:
        if device_id not in self.dev_names:
            self.dev_names[device_id] = (await self.ctx.session.execute(
                text("SELECT name FROM devices WHERE id = :d"), {"d": device_id})).scalar() or str(device_id)[:8]
        return self.dev_names[device_id]

    async def far_end(self, port_id: uuid.UUID) -> tuple[uuid.UUID, str, uuid.UUID, str, list[str]] | None:
        """(對端裝置, 對端裝置名稱, 對端埠, 對端埠名稱, 經過的路徑文字)；沒有接線或走不到裝置埠回 None。"""
        if port_id in self.cache:
            return self.cache[port_id]
        session = self.ctx.session
        path: list[str] = []
        current = port_id
        seen = {port_id}
        result = None
        for _ in range(_MAX_HOPS):
            far = (await session.execute(text("""
                SELECT o.object_id AS port_id, c.label
                  FROM cable_terminations t
                  JOIN cable_terminations o ON o.cable_id = t.cable_id AND o.id <> t.id
                  JOIN cables c ON c.id = t.cable_id
                 WHERE t.object_type = 'device_port' AND t.object_id = :p AND o.object_type = 'device_port'
                   AND c.status = 'connected'
                 LIMIT 1
            """), {"p": current})).first()
            if far is None:
                break
            port = (await session.execute(text(
                "SELECT id, name, type, peer_port_id, device_id FROM device_ports WHERE id = :p"),
                {"p": far.port_id})).first()
            if port is None or port.id in seen:
                break
            seen.add(port.id)
            dname = await self.name(port.device_id)
            path.append(f"{far.label or 'cable'} → {dname}/{port.name}")
            # 跳接面板：從 front 進、rear 出（或反過來），沿著另一面的纜線繼續走
            if port.type in _PASS_THROUGH and port.peer_port_id and port.peer_port_id not in seen:
                seen.add(port.peer_port_id)
                current = port.peer_port_id
                continue
            result = (port.device_id, dname, port.id, port.name, path)
            break
        self.cache[port_id] = result
        return result

    async def neighbours(self, device_id: uuid.UUID) -> dict[uuid.UUID, list[tuple[str, str, list[str]]]]:
        """這台裝置每條已知連線的對端：對端裝置 → [(本機埠, 對端埠, 路徑)]。"""
        out: dict[uuid.UUID, list[tuple[str, str, list[str]]]] = defaultdict(list)
        for p in await self.ports(device_id):
            if p.type in _PASS_THROUGH:
                continue
            far = await self.far_end(p.id)
            if far is not None and far[0] != device_id:
                out[far[0]].append((p.name, far[3], far[4]))
        return out


# ─────────────────── 交換器維護：誰的網路跟著斷 ───────────────────

async def network_dependents(ctx: Ctx) -> None:
    sc = ctx.scenario
    down = {d for d, _ in sc.down_devices}
    for d in down:
        _mark(ctx, _ref("device", d), UNAVAILABLE)
    if sc.scenario_type != "switch_maintenance":
        return
    cab = _Cabling(ctx)
    beyond_cache: dict[uuid.UUID, bool] = {}

    async def reaches_beyond(start: uuid.UUID) -> bool:
        """拿掉停機的裝置後，從這台出發能不能走到「不直接接在停機裝置上」的裝置。

        走不到＝另一條路最後也只回到停機的裝置（共用上游，規格 §6.3、T30）；兩台互相接著、
        卻都只接到停機交換器的情況也算。走太遠（超過上限）就當作走得出去，交給備援待驗證。"""
        if start in beyond_cache:
            return beyond_cache[start]
        seen = {start}
        queue = [start]
        result = False
        while queue and not result:
            cur = queue.pop(0)
            neigh = await cab.neighbours(cur)
            if cur != start and not (set(neigh) & down):
                result = True
                break
            for n in neigh:
                if n not in down and n not in seen:
                    seen.add(n)
                    queue.append(n)
            if len(seen) > 300:
                result = True
        if not result:      # 整個連通塊都走不出去：塊裡每一台的答案都一樣
            for d in seen:
                beyond_cache[d] = False
        beyond_cache[start] = result
        return result

    dependents: dict[uuid.UUID, list[tuple[str, str, str, list[str]]]] = defaultdict(list)
    via_ids: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for target in sorted(down, key=str):
        tname = await cab.name(target)
        for far_dev, links in (await cab.neighbours(target)).items():
            if far_dev in down:
                continue
            via_ids[far_dev].add(target)
            for tport, fport, path in links:
                dependents[far_dev].append((tname, tport, fport, path))
    if len(dependents) > _MAX_DEPENDENTS:
        ctx.gap("network", "dependents_truncated", limit=_MAX_DEPENDENTS, affected="network")
    cabled: set[uuid.UUID] = set()
    for dev in sorted(dependents, key=str)[:_MAX_DEPENDENTS]:
        cabled.add(dev)
        dname = await cab.name(dev)
        neigh = await cab.neighbours(dev)
        others = [o for o in neigh if o not in down]
        via = dependents[dev]
        path = [{"from": dname, "port": fport, "to": f"{tname}/{tport}", "via": p} for tname, tport, fport, p in via]
        key = ctx.add(Evidence(key=f"cabling:{dev}", source_type="physical", object_type="device", object_id=dev,
                               label=dname, payload={"links_to_down": [f"{fp} → {t}/{tp}" for t, tp, fp, _ in via],
                                                     "other_neighbours": [await cab.name(o) for o in others]},
                               freshness="fresh", visibility=("device", dev)))
        params = {"device": dname, "via": ", ".join(sorted({t for t, _, _, _ in via})),
                  "links": len(via), "others": ", ".join(sorted([await cab.name(o) for o in others]))}
        if not others:
            rule, status = "network.single_homed", UNAVAILABLE
        elif not await reaches_beyond(dev):
            rule, status = "network.shared_upstream", UNAVAILABLE
        else:
            rule, status = "network.redundancy_unverified", UNKNOWN
            ctx.gap("network", "link_health_unknown", affected="network")
        _mark(ctx, _ref("device", dev), status)
        ctx.find(Finding(rule, "device", dname, [key], subject_id=dev, match_kind="cabling",
                         params=params, path=path, visibility=("device", dev),
                         links=[(f"device:{t}", "requires_network") for t in sorted(via_ids[dev], key=str)]))
    await _fdb_inferred(ctx, down, cabled, cab)
    # 沒有 VLAN／STP／LAG／路由狀態：不宣稱完整的可達性（規格 §6.3）
    ctx.gap("network", "network_state_unknown", affected="network")


async def _fdb_inferred(ctx: Ctx, down: set[uuid.UUID], cabled: set[uuid.UUID], cab: _Cabling) -> None:
    """交換器 MAC 表學到、但沒有接線記錄的機器：只能推定「可能中斷」。上行埠（MAC 太多）略過。"""
    if not ctx.allowed("librenms"):
        return
    from app.core.config import get_settings
    from app.services.librenms import current_fdb_cutoff
    # FDB 留一年的歷史；只有「目前仍有效」的條目（預設 24 小時內看到）才代表機器現在接在這裡
    cutoff = current_fdb_cutoff(now=ctx.now, max_age_hours=get_settings().fdb_current_max_age_hours)
    rows = (await ctx.session.execute(text("""
        SELECT COALESCE(ld.jt_ipam_device_id, f.switch_device_id) AS sw, f.port_name, f.mac::text AS mac
          FROM fdb_entries f
          LEFT JOIN librenms_devices ld ON ld.id = f.device_id
         WHERE (ld.jt_ipam_device_id = ANY(CAST(:ids AS uuid[])) OR f.switch_device_id = ANY(CAST(:ids AS uuid[])))
           AND f.last_seen_at >= :cutoff
    """), {"ids": [str(d) for d in down], "cutoff": cutoff})).all()
    # 上行埠以「哪台交換器的哪個埠」判斷：兩台一起維護、剛好同名的埠不可以加在一起
    by_port: dict[tuple[str, str], set[str]] = defaultdict(set)
    for r in rows:
        by_port[(str(r.sw), r.port_name or "?")].add(r.mac.lower())
    macs: set[str] = set()
    for (sw, port), ms in by_port.items():
        if len(ms) > _FDB_UPLINK_MACS:
            name = await cab.name(uuid.UUID(sw)) if sw != "None" else ""
            ctx.gap("network", "fdb_uplink_skipped", port=f"{name} {port}".strip(), macs=len(ms), affected="network")
            continue
        macs |= ms
    if not macs:
        return
    hits = (await ctx.session.execute(text("""
        SELECT a.id, host(a.ip) AS ip, a.device_id, a.hostname, lower(a.mac::text) AS mac
          FROM ip_addresses a JOIN subnets s ON s.id = a.subnet_id
         WHERE s.archived_at IS NULL AND lower(a.mac::text) = ANY(:macs)
    """), {"macs": sorted(macs)})).all()
    for h in hits:
        if h.device_id in down or h.device_id in cabled:
            continue
        ref = _ref("device", h.device_id) if h.device_id else _ref("ip", h.id)
        label = (await cab.name(h.device_id)) if h.device_id else f"{h.ip} {h.hostname or ''}".strip()
        key = ctx.add(Evidence(key=f"fdb:{h.mac}", source_type="librenms", object_type="fdb_entry",
                               label=f"{h.mac} → {label}", payload={"mac": h.mac, "ip": h.ip}, freshness="unknown"))
        _mark(ctx, ref, UNKNOWN)
        ctx.find(Finding("network.fdb_inferred", "device" if h.device_id else "ip_address", label, [key],
                         subject_id=h.device_id or h.id, match_kind="fdb",
                         params={"mac": h.mac, "address": h.ip},
                         visibility=("device", h.device_id) if h.device_id else ("ip", h.id),
                         links=[(f"device:{d}", "observed_on") for d in sorted(down, key=str)]))


# ─────────────────── 工作負載：節點上的虛擬機 ───────────────────

async def workloads(ctx: Ctx) -> None:
    sc = ctx.scenario
    # 節點停機：停機的就是節點；交換器維護：網路斷掉（或待驗證）的裝置如果是節點，上面的虛擬機跟著受影響
    nodes = {uuid.UUID(r.split(":", 1)[1]): st for r, st in ctx.status.items()
             if r.startswith("device:") and st in (UNAVAILABLE, UNKNOWN)}
    if not nodes or not ctx.allowed("virt"):
        return
    names: dict[str, uuid.UUID] = {}
    for d, fqdn, name in (await ctx.session.execute(text(
            "SELECT id, fqdn, name FROM devices WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": [str(n) for n in nodes]})).all():
        for n in (name, (fqdn or "").split(".")[0]):
            if n:
                names[n.lower()] = d
    vms = (await ctx.session.execute(text("""
        SELECT vm.id, vm.name, vm.status, vm.kind, vm.node, vm.primary_ip_id, vm.is_template, c.name AS cluster
          FROM virtual_machines vm JOIN virt_clusters c ON c.id = vm.cluster_id
         WHERE lower(vm.node) = ANY(:names)
    """), {"names": sorted(names)})).all()
    mode = sc.mode or "direct"
    any_running = False
    for v in vms:
        if v.is_template:
            continue
        node_dev = names.get((v.node or "").lower())
        node_status = nodes.get(node_dev) if node_dev else None
        label = f"{v.cluster}/{v.name}"[:300]
        key = ctx.add(Evidence(key=f"vm:{v.id}", source_type="virt", object_type="virtual_machine", object_id=v.id,
                               label=label, payload={"status": v.status, "kind": v.kind, "node": v.node},
                               freshness="unknown"))
        params = {"node": v.node or "", "status": v.status, "mode": mode}
        links = [(f"device:{node_dev}", "hosted_on")] if node_dev else []
        if v.status != "running":
            ctx.find(Finding("workload.vm_stopped", "virtual_machine", label, [key], subject_id=v.id, params=params,
                             strength="inferred", links=links))
            continue
        any_running = True
        if node_status == UNKNOWN:
            rule, status = "workload.vm_network_unverified", UNKNOWN
        elif sc.scenario_type == "node_downtime" and mode == "ha_failure":
            rule, status = "workload.vm_ha_unverified", UNKNOWN
        else:
            rule, status = "workload.vm_stops", UNAVAILABLE
        _mark(ctx, _ref("vm", v.id), status)
        if v.primary_ip_id:
            _mark(ctx, _ref("ip", v.primary_ip_id), status)
        ctx.find(Finding(rule, "virtual_machine", label, [key], subject_id=v.id, params=params, strength="inferred",
                         links=links))
    if sc.scenario_type == "node_downtime" and any_running:
        if mode == "ha_failure":
            ctx.gap("workload", "ha_conditions_unknown", affected="workloads")
        elif mode == "migrate_first":
            # 只有「已驗證完成的遷移」能當證據；計畫要遷移不等於已經離開這個節點
            ctx.gap("workload", "migration_not_verified", affected="workloads")


# ─────────────────── 服務 ───────────────────

async def _device_ip_status(ctx: Ctx) -> None:
    """停機或網路斷掉的裝置，它名下的 IP 跟著同一個狀態（服務常以 IP 登錄依賴）。

    裝置群組的共用 IP：整組成員都在這次受影響的裝置裡才算（停一台堆疊成員，管理 IP 還在）。"""
    devs = {r.split(":", 1)[1]: st for r, st in ctx.status.items() if r.startswith("device:")}
    if not devs:
        return
    for ip_id, dev in (await ctx.session.execute(text("""
        SELECT a.id, a.device_id FROM ip_addresses a WHERE a.device_id = ANY(CAST(:ids AS uuid[]))
          AND NOT EXISTS (SELECT 1 FROM devices m WHERE m.group_id = a.device_group_id
                          AND NOT (m.id = ANY(CAST(:ids AS uuid[]))))
        UNION
        SELECT d.primary_ip_id, d.id FROM devices d WHERE d.id = ANY(CAST(:ids AS uuid[])) AND d.primary_ip_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM ip_addresses a JOIN devices m ON m.group_id = a.device_group_id
                          WHERE a.id = d.primary_ip_id AND NOT (m.id = ANY(CAST(:ids AS uuid[]))))
    """), {"ids": list(devs)})).all():
        _mark(ctx, _ref("ip", ip_id), devs[str(dev)])


def _sccs(nodes: list[str], edges: dict[str, set[str]]) -> list[list[str]]:
    """Tarjan（迭代版）：強連通元件。服務依賴有循環時用來找出整個循環，不遞迴到底。"""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    out: list[list[str]] = []
    counter = 0
    for root in nodes:
        if root in index:
            continue
        work = [(root, iter(sorted(edges.get(root, ()))))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            v, it = work[-1]
            advanced = False
            for w in it:
                if w not in index:
                    index[w] = low[w] = counter
                    counter += 1
                    stack.append(w)
                    on_stack.add(w)
                    work.append((w, iter(sorted(edges.get(w, ())))))
                    advanced = True
                    break
                if w in on_stack:
                    low[v] = min(low[v], index[w])
            if advanced:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[v])
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp.append(w)
                    if w == v:
                        break
                out.append(comp)
    return out


def group_result(statuses: list[str], required: int) -> str:
    """k-of-n 三值邏輯（規格 §6.2）：可用數 ≥ k 滿足；可用＋未知 < k 不滿足；其他未知。"""
    avail = sum(1 for s in statuses if s == AVAILABLE)
    unknown = sum(1 for s in statuses if s == UNKNOWN)
    if avail >= required:
        return AVAILABLE
    if avail + unknown < required:
        return UNAVAILABLE
    return UNKNOWN


async def services(ctx: Ctx, *, report_unmodeled: bool = True) -> None:
    from app.models.change_impact import NON_PROPAGATING
    await _device_ip_status(ctx)
    if not ctx.status:
        return
    if not ctx.allowed("service"):
        return
    svc_rows = (await ctx.session.execute(text("""
        SELECT id, name, criticality FROM impact_services WHERE status = 'active' ORDER BY name LIMIT 2000
    """))).all()
    if not svc_rows:
        if report_unmodeled:
            ctx.gap("service", "no_services_modeled", affected="services")
        return
    svcs = {str(r.id): r for r in svc_rows}
    groups: dict[str, list[Any]] = defaultdict(list)
    for g in (await ctx.session.execute(text("""
        SELECT id, service_id, name, required_count, confirmed_at FROM impact_dependency_groups
         WHERE service_id = ANY(CAST(:ids AS uuid[])) ORDER BY position, name
    """), {"ids": list(svcs)})).all():
        groups[str(g.service_id)].append(g)
    members: dict[str, list[Any]] = defaultdict(list)
    gids = [str(g.id) for gs in groups.values() for g in gs]
    if gids:
        for m in (await ctx.session.execute(text("""
            SELECT group_id, object_type, object_id, relation_type FROM impact_dependency_members
             WHERE group_id = ANY(CAST(:ids AS uuid[]))
        """), {"ids": gids})).all():
            members[str(m.group_id)].append(m)

    edges: dict[str, set[str]] = defaultdict(set)
    for sid, gs in groups.items():
        for g in gs:
            for m in members[str(g.id)]:
                if m.object_type == "service" and m.relation_type not in NON_PROPAGATING and str(m.object_id) in svcs:
                    edges[sid].add(str(m.object_id))
    result: dict[str, str] = {}
    in_cycle: set[str] = set()
    # Tarjan 依「被依賴的先完成」的順序回傳元件：照順序算，依賴的服務一定已經有結果
    for comp in _sccs(sorted(svcs), edges):
        if len(comp) > 1 or comp[0] in edges.get(comp[0], set()):
            for s in comp:
                result[s] = UNKNOWN
                in_cycle.add(s)
            ctx.gap("service", "service_dependency_cycle", services=", ".join(sorted(svcs[s].name for s in comp)),
                    affected="services")
            continue
        sid = comp[0]
        outcome = AVAILABLE
        touched = False
        failing: list[str] = []
        unconfirmed = False
        for g in groups.get(sid, []):
            sts = []
            for m in members[str(g.id)]:
                if m.relation_type in NON_PROPAGATING:
                    continue
                st = result.get(str(m.object_id), AVAILABLE) if m.object_type == "service" \
                    else ctx.status.get(_ref(m.object_type, m.object_id), AVAILABLE)
                touched = touched or st != AVAILABLE
                sts.append(st)
            if not sts:
                continue
            gr = group_result(sts, int(g.required_count))
            unconfirmed = unconfirmed or g.confirmed_at is None
            if gr == UNAVAILABLE:
                outcome = UNAVAILABLE
                failing.append(g.name)
            elif gr == UNKNOWN and outcome != UNAVAILABLE:
                outcome = UNKNOWN
                failing.append(g.name)
        result[sid] = outcome
        if not touched:
            continue
        links = []
        for g in groups.get(sid, []):
            for m in members[str(g.id)]:
                if m.relation_type in NON_PROPAGATING:
                    continue
                st = result.get(str(m.object_id), AVAILABLE) if m.object_type == "service" \
                    else ctx.status.get(_ref(m.object_type, m.object_id), AVAILABLE)
                if st != AVAILABLE:
                    links.append((f"{_SUBJECT[m.object_type]}:{m.object_id}", m.relation_type))
        svc = svcs[sid]
        key = ctx.add(Evidence(key=f"service:{sid}", source_type="service", object_type="service",
                               object_id=uuid.UUID(sid), label=svc.name,
                               payload={"criticality": svc.criticality,
                                        "groups": [{"name": g.name, "required": g.required_count}
                                                   for g in groups.get(sid, [])]},
                               freshness="fresh"))
        rule = {UNAVAILABLE: "service.modeled_disruption", UNKNOWN: "service.redundancy_unverified",
                AVAILABLE: "service.dependencies_hold"}[outcome]
        ctx.find(Finding(rule, "service", svc.name, [key], subject_id=uuid.UUID(sid),
                         params={"service": svc.name, "criticality": svc.criticality,
                                 "groups": ", ".join(failing)},
                         strength="manual_assumption" if unconfirmed else None, links=links))
    for sid in sorted(in_cycle):
        key = ctx.add(Evidence(key=f"service:{sid}", source_type="service", object_type="service",
                               object_id=uuid.UUID(sid), label=svcs[sid].name,
                               payload={"criticality": svcs[sid].criticality}, freshness="fresh"))
        ctx.find(Finding("service.cycle", "service", svcs[sid].name, [key], subject_id=uuid.UUID(sid),
                         params={"service": svcs[sid].name}))


# ─────────────────── 改址、除役也要看服務 ───────────────────

async def service_refs(ctx: Ctx) -> None:
    """M1 情境（改址、除役）的服務影響。

    - 除役：裝置、它名下的 IP、它上面執行中的虛擬機視為永久停機，照 M2 同一套 k-of-n 判定哪些服務會中斷
    - 改址：服務依賴這個位址 → 改址期間服務會受影響；服務端點寫了這個位址 → 連這個服務的用戶端要改
    沒有登錄任何服務時不記資料不足：服務登錄是選用的，不是每個站台都用。
    """
    sc = ctx.scenario
    if sc.scenario_type == "device_decommission" and sc.device_id is not None:
        _mark(ctx, _ref("device", sc.device_id), UNAVAILABLE)
        names = [n.lower() for n in (await ctx.session.execute(text(
            "SELECT name, split_part(fqdn, '.', 1) FROM devices WHERE id = :d"), {"d": sc.device_id})).one() if n]
        for vm_id in (await ctx.session.execute(text("""
            SELECT id FROM virtual_machines WHERE lower(node) = ANY(:n) AND status = 'running' AND NOT is_template
        """), {"n": names})).scalars().all() if names else []:
            _mark(ctx, _ref("vm", vm_id), UNAVAILABLE)
        await services(ctx, report_unmodeled=False)
    elif not ctx.allowed("service"):
        return
    if not ctx.global_read:
        return
    ip_ids = [str(r.ip_id) for r in ctx.roots]
    dev = str(sc.device_id) if sc.device_id else None
    if sc.scenario_type == "ip_renumber":
        for m in (await ctx.session.execute(text("""
            SELECT s.id, s.name, s.criticality, g.name AS grp, m.object_id, m.relation_type
              FROM impact_dependency_members m
              JOIN impact_dependency_groups g ON g.id = m.group_id
              JOIN impact_services s ON s.id = g.service_id
             WHERE s.status = 'active' AND m.object_type = 'ip' AND m.object_id = ANY(CAST(:ids AS uuid[]))
               AND m.relation_type NOT IN ('references', 'observed_on')
             ORDER BY s.name
        """), {"ids": ip_ids})).all():
            key = ctx.add(Evidence(key=f"service:{m.id}", source_type="service", object_type="service",
                                   object_id=m.id, label=m.name, payload={"criticality": m.criticality},
                                   freshness="fresh"))
            ctx.find(Finding("service.depends_on_address", "service", m.name, [key], subject_id=m.id,
                             subject_key=f"{m.grp}:{m.object_id}",
                             params={"service": m.name, "group": m.grp, "criticality": m.criticality,
                                     "address": next((r.ip_text for r in ctx.roots if str(r.ip_id) == str(m.object_id)),
                                                     "")},
                             links=[(f"ip_address:{m.object_id}", m.relation_type)]))
    # 服務端點：指到根位址或（除役時）這台裝置，或主機欄寫了根位址
    likes = [f"%{r.ip_text}%" for r in ctx.roots]
    for e in (await ctx.session.execute(text("""
        SELECT e.id, e.service_id, s.name, e.object_type, e.object_id, e.hostname, e.port, e.protocol
          FROM impact_service_endpoints e JOIN impact_services s ON s.id = e.service_id
         WHERE s.status = 'active' AND (
               (e.object_type = 'ip' AND e.object_id = ANY(CAST(:ids AS uuid[])))
            OR (e.object_type = 'device' AND CAST(:dev AS uuid) IS NOT NULL AND e.object_id = CAST(:dev AS uuid))
            OR e.hostname ILIKE ANY(:likes))
         ORDER BY s.name
    """), {"ids": ip_ids, "dev": dev, "likes": likes})).all():
        if e.object_type == "ip" and str(e.object_id) in ip_ids:
            addr = next(r.ip_text for r in ctx.roots if str(r.ip_id) == str(e.object_id))
        elif e.object_type == "device" and dev and str(e.object_id) == dev:
            addr = sc.device_name or ""
        else:
            hits = text_mentions(e.hostname or "", [r.aip for r in ctx.roots])
            if not hits:
                continue
            addr = str(hits[0])
        # 沒填埠號：送代碼 any，前端翻成「未指定埠」
        endpoint = f"{e.protocol or 'tcp'}/{e.port}" if e.port else (e.protocol or "any")
        key = ctx.add(Evidence(key=f"service_endpoint:{e.id}", source_type="service", object_type="service",
                               object_id=e.service_id, label=e.name,
                               payload={"hostname": e.hostname, "port": e.port, "protocol": e.protocol},
                               freshness="fresh"))
        ctx.find(Finding("service.endpoint_address", "service", e.name, [key], subject_id=e.service_id,
                         subject_key=f"endpoint:{e.id}",
                         params={"service": e.name, "address": addr, "endpoint": endpoint}))
