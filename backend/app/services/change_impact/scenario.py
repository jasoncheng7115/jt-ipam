"""情境解析：根目標、命名空間、改址參數的合法性（規格 §3.1、§5.1、§5.4）。

- 根目標一律是物件 id（IP 物件或裝置），不接受只給位址字串；同一位址有好幾筆時由畫面讓使用者選
- 改址：同 family、落在目標子網路內、不是網路／廣播（/31、/32 例外）、多播、未指定、迴路、
  沒有 scope 的 IPv6 link-local；跟舊位址相同也不行
- 目標子網路沒給：在舊位址同一個 VRF、使用者看得到的子網路裡找最小的那個；找不到、或同一層有好幾個 → 要使用者選
"""

from __future__ import annotations

import ipaddress
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.change_impact.matching import special_reason
from app.services.change_impact.model import TargetAddr


class ScenarioError(Exception):
    """建案或分析參數不合法：code 對應前端 errors.<code>，params 給訊息帶入。"""

    def __init__(self, code: str, message: str, status: int = 422, **params: Any) -> None:
        super().__init__(message)
        self.code, self.status, self.params = code, status, params


@dataclass
class Scenario:
    scenario_type: str
    target_type: str
    target_id: uuid.UUID
    target_label: str
    customer_id: uuid.UUID | None
    roots: list[TargetAddr]
    device_id: uuid.UUID | None = None
    device_name: str | None = None
    new_ip: str | None = None
    new_aip: ipaddress.IPv4Address | ipaddress.IPv6Address | None = None
    target_subnet_id: uuid.UUID | None = None
    target_subnet_cidr: str | None = None
    target_vrf_id: uuid.UUID | None = None
    cross_subnet: bool = False
    # M2：同時停機／維護的其他裝置（多目標一次合併計算，規格 §6.4），與節點停機的情境模式
    also_down: list[tuple[uuid.UUID, str]] = field(default_factory=list)
    mode: str | None = None

    def payload(self) -> dict[str, Any]:
        """寫進計畫版本與 scenario_hash 的正規化內容（不含顯示用文字以外的推導值）。"""
        if self.scenario_type in M2_SCENARIOS:
            # M1 情境的內容維持原樣：已核准計畫的雜湊不可以因為升級而改變
            return {"scenario_type": self.scenario_type, "target_type": self.target_type,
                    "target_id": str(self.target_id),
                    "parameters": {"mode": self.mode,
                                   "also_down": sorted(str(i) for i, _ in self.also_down)}}
        return {"scenario_type": self.scenario_type, "target_type": self.target_type,
                "target_id": str(self.target_id),
                "parameters": {"new_ip": self.new_ip,
                               "target_subnet_id": str(self.target_subnet_id) if self.target_subnet_id else None}}

    @property
    def down_devices(self) -> list[tuple[uuid.UUID, str]]:
        """這次一起維護／停機的所有裝置（根目標在第一個）。"""
        return ([(self.device_id, self.device_name or "")] if self.device_id else []) + self.also_down


M2_SCENARIOS = ("switch_maintenance", "node_downtime")
NODE_MODES = ("direct", "migrate_first", "ha_failure")
MAX_TARGETS = 20


async def _addr_row(session: AsyncSession, ip_id: uuid.UUID) -> TargetAddr | None:
    row = (await session.execute(text("""
        SELECT a.id, host(a.ip) AS ip, a.subnet_id, s.cidr::text AS cidr, s.vrf_id,
               COALESCE(a.customer_id, s.customer_id) AS customer_id, a.hostname, a.mac::text AS mac, a.state
          FROM ip_addresses a JOIN subnets s ON s.id = a.subnet_id
         WHERE a.id = :id
    """), {"id": ip_id})).first()
    if row is None:
        return None
    return TargetAddr(ip_id=row.id, ip_text=row.ip, aip=ipaddress.ip_address(row.ip), subnet_id=row.subnet_id,
                      subnet_cidr=row.cidr, vrf_id=row.vrf_id, customer_id=row.customer_id,
                      hostname=row.hostname, mac=row.mac, state=row.state)


async def _perm(session: AsyncSession, user: Any, object_type: str, object_id: uuid.UUID) -> str:
    from app.services.permission import get_object_permission
    return await get_object_permission(session, user=user, object_type=object_type, object_id=object_id)  # type: ignore[arg-type]


_RANK = {"none": 0, "read": 1, "write": 2, "admin": 3}


async def require_target_access(session: AsyncSession, user: Any, target_type: str, target_id: uuid.UUID,
                                need: str = "read") -> None:
    """看不到就當作不存在（404，不洩漏存在與否）；看得到但權限不夠 → 403。"""
    ot = "ip" if target_type == "ip_address" else "device"
    level = await _perm(session, user, ot, target_id)
    if _RANK[level] < 1:
        raise ScenarioError("impact_target_not_found", "Target not found", status=404)
    if _RANK[level] < _RANK[need]:
        raise ScenarioError("impact_target_forbidden", "Not enough permission on the target", status=403)


async def device_addresses(session: AsyncSession, device_id: uuid.UUID) -> list[TargetAddr]:
    """裝置的所有 IP：掛在這台裝置上的，加上設為主要 IP 的那筆（可能沒掛 device_id）。

    裝置群組的共用 IP（堆疊管理 IP、HA 虛擬 IP）：群組裡還有別台成員就不算這台的 —— 停掉一台，
    別台還在提供這個位址。"""
    ids = [r[0] for r in (await session.execute(text("""
        SELECT a.id FROM ip_addresses a WHERE a.device_id = :d
          AND NOT EXISTS (SELECT 1 FROM devices m WHERE m.group_id = a.device_group_id AND m.id <> :d)
        UNION
        SELECT d.primary_ip_id FROM devices d WHERE d.id = :d AND d.primary_ip_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM ip_addresses a JOIN devices m ON m.group_id = a.device_group_id
                          WHERE a.id = d.primary_ip_id AND m.id <> :d)
    """), {"d": device_id})).all()]
    out = []
    for i in ids:
        t = await _addr_row(session, i)
        if t is not None:
            out.append(t)
    out.sort(key=lambda t: (t.aip.version, int(t.aip)))
    return out


async def build(session: AsyncSession, user: Any, *, scenario_type: str, target_type: str,
                target_id: uuid.UUID, parameters: dict[str, Any] | None = None,
                need: str = "write") -> Scenario:
    """建案、修改與每次分析都走這裡：權限、根目標、參數驗證一次到位。"""
    from app.models.change_impact import SCENARIOS
    parameters = parameters or {}
    if scenario_type not in SCENARIOS:
        raise ScenarioError("impact_invalid_scenario", "Unknown scenario", scenario=scenario_type)
    expected_target = "ip_address" if scenario_type == "ip_renumber" else "device"
    if target_type != expected_target:
        raise ScenarioError("impact_invalid_scenario", "Target type does not fit the scenario",
                            scenario=scenario_type)
    await require_target_access(session, user, target_type, target_id, need=need)

    if scenario_type in ("device_decommission", *M2_SCENARIOS):
        from app.models.device import Device
        dev = await session.get(Device, target_id)
        if dev is None:
            raise ScenarioError("impact_target_not_found", "Target not found", status=404)
        roots = await device_addresses(session, dev.id)
        sc = Scenario(scenario_type, target_type, dev.id, dev.name, dev.customer_id, roots,
                      device_id=dev.id, device_name=dev.name)
        if scenario_type in M2_SCENARIOS:
            await _validate_m2(session, user, sc, parameters, need=need)
        return sc

    root = await _addr_row(session, target_id)
    if root is None:
        raise ScenarioError("impact_target_not_found", "Target not found", status=404)
    sc = Scenario(scenario_type, target_type, root.ip_id, root.ip_text, root.customer_id, [root])
    await _validate_renumber(session, user, sc, root, parameters)
    return sc


async def _validate_m2(session: AsyncSession, user: Any, sc: Scenario, parameters: dict[str, Any],
                       need: str) -> None:
    """M2：一起停機的其他裝置（每台都要同樣的權限、總數上限 20）與節點停機的情境模式。"""
    from app.models.device import Device
    mode = parameters.get("mode") or ("direct" if sc.scenario_type == "node_downtime" else None)
    if sc.scenario_type == "node_downtime" and mode not in NODE_MODES:
        raise ScenarioError("impact_invalid_scenario", "Unknown downtime mode", scenario=str(mode)[:32])
    sc.mode = mode
    raw = parameters.get("also_down") or []
    if not isinstance(raw, list):
        raise ScenarioError("impact_invalid_scenario", "also_down must be a list", scenario=sc.scenario_type)
    seen = {sc.device_id}
    for v in raw:
        try:
            did = uuid.UUID(str(v))
        except ValueError as exc:
            raise ScenarioError("impact_invalid_scenario", "Bad device id", scenario=sc.scenario_type) from exc
        if did in seen:
            continue
        seen.add(did)
        if len(seen) > MAX_TARGETS:
            raise ScenarioError("impact_too_many_targets", "Too many targets", limit=MAX_TARGETS)
        await require_target_access(session, user, "device", did, need=need)
        dev = await session.get(Device, did)
        if dev is None:
            raise ScenarioError("impact_target_not_found", "Target not found", status=404)
        sc.also_down.append((dev.id, dev.name))


async def _validate_renumber(session: AsyncSession, user: Any, sc: Scenario, root: TargetAddr,
                             parameters: dict[str, Any]) -> None:
    raw = str(parameters.get("new_ip") or "").strip()
    if not raw:
        raise ScenarioError("impact_invalid_target_address", "New IP is required", reason="missing")
    if "%" in raw:
        raise ScenarioError("impact_invalid_target_address", "Scoped addresses are not supported",
                            address=raw[:64], reason="scoped")
    try:
        new = ipaddress.ip_address(raw)
    except ValueError as exc:
        raise ScenarioError("impact_invalid_target_address", "Not an IP address", address=raw[:64],
                            reason="syntax") from exc
    if new.version != root.aip.version:
        # 跨 family 不是改址，是遷移方案（規格 §5.3）
        raise ScenarioError("impact_invalid_target_address", "IPv4/IPv6 cross-family change is not a renumber",
                            address=str(new), reason="cross_family")
    if new == root.aip:
        raise ScenarioError("impact_invalid_target_address", "New IP equals the current IP",
                            address=str(new), reason="same_as_old")

    from app.services.permission import visible_ids
    vis_subnets = await visible_ids(session, user=user, object_type="subnet")
    subnet_id_raw = parameters.get("target_subnet_id")
    if subnet_id_raw:
        try:
            sid = uuid.UUID(str(subnet_id_raw))
        except ValueError as exc:
            raise ScenarioError("impact_target_scope_mismatch", "Bad subnet id") from exc
        row = (await session.execute(text(
            "SELECT id, cidr::text AS cidr, vrf_id FROM subnets WHERE id = :id AND archived_at IS NULL"),
            {"id": sid})).first()
        if row is None or (vis_subnets is not None and row.id not in vis_subnets):
            raise ScenarioError("impact_target_scope_mismatch", "Target subnet not found", status=404)
        net = ipaddress.ip_network(row.cidr, strict=False)
        if new not in net:
            raise ScenarioError("impact_target_scope_mismatch", "New IP is outside the target subnet",
                                address=str(new), subnet=row.cidr)
    else:
        # 同一個 VRF（含都沒有 VRF）裡、包含新位址的子網路，取最小的一層
        every, best = await _containing_subnets(session, user, new, vrf=root.vrf_id, any_vrf=False)
        if not best:
            if every:
                raise ScenarioError("impact_new_ip_no_permission", "No permission on the subnet of the new IP",
                                    status=403, address=str(new))
            raise ScenarioError("impact_new_ip_unmanaged", "No managed subnet contains the new IP",
                                address=str(new))
        if len(best) > 1:
            raise ScenarioError("impact_ambiguous_target", "Several overlapping subnets contain the new IP",
                                status=409, address=str(new), subnets=", ".join(r.cidr for r in best),
                                candidates=[str(r.id) for r in best])
        row = best[0]
        net = ipaddress.ip_network(row.cidr, strict=False)
    why = special_reason(new, net)
    if why:
        raise ScenarioError("impact_invalid_target_address", "This address cannot be assigned",
                            address=str(new), reason=why)
    sc.new_ip, sc.new_aip = str(new), new
    sc.target_subnet_id, sc.target_subnet_cidr, sc.target_vrf_id = row.id, row.cidr, row.vrf_id
    sc.cross_subnet = row.id != root.subnet_id


async def candidates_for_address(session: AsyncSession, user: Any, ip_text: str,
                                 subnet_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
    """同一個位址有好幾筆（重疊網段）：列出使用者看得到的候選，給畫面選（規格 §3.1）。

    帶 subnet_id（建立視窗先選子網路，使用者 2026-10-07：只輸入 IP 萬一選錯）→ 只找那個子網路裡的記錄。
    """
    from app.models.address import IPAddress
    from app.services.permission import visible_ids
    try:
        a = ipaddress.ip_address(ip_text.strip())
    except ValueError as exc:
        raise ScenarioError("impact_invalid_target_address", "Not an IP address", address=ip_text[:64],
                            reason="syntax") from exc
    chosen = None
    if subnet_id is not None:
        chosen = (await session.execute(text(
            "SELECT id, cidr::text AS cidr FROM subnets WHERE id = :id AND archived_at IS NULL"),
            {"id": subnet_id})).first()
        vis_sub = await visible_ids(session, user=user, object_type="subnet")
        if chosen is None or (vis_sub is not None and chosen.id not in vis_sub):
            raise ScenarioError("impact_target_no_permission", "No permission on this subnet",
                                status=403, address=str(a))
        if a not in ipaddress.ip_network(chosen.cidr, strict=False):
            raise ScenarioError("impact_target_outside_subnet", "The address is outside the chosen subnet",
                                address=str(a), subnet=chosen.cidr)
    vis = await visible_ids(session, user=user, object_type="ip")
    q = select(IPAddress.id).where(IPAddress.ip == str(a))
    if chosen is not None:
        q = q.where(IPAddress.subnet_id == chosen.id)
    rows = (await session.execute(q)).scalars().all()
    out = []
    for i in rows:
        if vis is not None and i not in vis:
            continue
        t = await _addr_row(session, i)
        if t is not None:
            out.append({"id": str(t.ip_id), "ip": t.ip_text, "subnet_id": str(t.subnet_id), "subnet": t.subnet_cidr,
                        "vrf_id": str(t.vrf_id) if t.vrf_id else None, "hostname": t.hostname})
    if not out:
        has_record = await _has_live_record(session, rows)
        if chosen is not None:
            if has_record:      # 子網路看得到、這筆 IP 本身被另外限制
                raise ScenarioError("impact_target_no_permission", "No permission on this address",
                                    status=403, address=str(a))
            raise ScenarioError("impact_target_not_registered", "IPAM has no record of this address",
                                status=404, address=str(a), subnet=chosen.cidr)
        raise await _why_no_candidate(session, user, a, has_record=has_record)
    return out


async def target_subnets(session: AsyncSession, user: Any, *, q: str | None = None,
                         section_id: uuid.UUID | None = None, customer_id: uuid.UUID | None = None,
                         limit: int = 50) -> dict[str, Any]:
    """建立視窗的子網路下拉（使用者 2026-10-07）：只列這個人可以修改的子網路（建立評估要修改權限，
    列出唯讀的只會走到最後才被擋）。單位與區段的選項也從這些子網路推出來，看不到的不會出現在選單裡。

    q 是位址 → 找包含它的子網路（最小的一層排前面）；否則比對 CIDR、說明、區段與單位名稱。
    """
    from app.services.permission import visible_ids
    empty: dict[str, Any] = {"subnets": [], "sections": [], "customers": [], "truncated": False}
    vis = await visible_ids(session, user=user, object_type="subnet", required="write")
    if vis is not None and not vis:
        return empty
    base = ["s.archived_at IS NULL"]
    params: dict[str, Any] = {}
    if vis is not None:
        base.append("s.id = ANY(CAST(:ids AS uuid[]))")
        params["ids"] = [str(i) for i in vis]
    joins = """FROM subnets s JOIN sections sec ON sec.id = s.section_id
               LEFT JOIN customers c ON c.id = s.customer_id
               LEFT JOIN vrfs v ON v.id = s.vrf_id"""
    where = " AND ".join(base)
    sections = [{"id": str(r.id), "name": r.name} for r in (await session.execute(text(
        f"SELECT DISTINCT sec.id, sec.name {joins} WHERE {where} ORDER BY sec.name LIMIT 500"), params)).all()]
    customers = [{"id": str(r.id), "name": r.name} for r in (await session.execute(text(
        f"SELECT DISTINCT c.id, c.name {joins} WHERE {where} AND c.id IS NOT NULL ORDER BY c.name LIMIT 500"),
        params)).all()]

    cond = list(base)
    order = "s.cidr"
    if section_id is not None:
        cond.append("s.section_id = :sec")
        params["sec"] = section_id
    if customer_id is not None:
        cond.append("s.customer_id = :cust")
        params["cust"] = customer_id
    text_q = (q or "").strip()[:100]
    if text_q:
        try:
            params["qip"] = str(ipaddress.ip_address(text_q))
            cond.append("s.cidr >>= CAST(:qip AS inet)")
            order = "masklen(s.cidr) DESC, s.cidr"
        except ValueError:
            params["ql"] = "%" + text_q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            cond.append("(s.cidr::text ILIKE :ql OR COALESCE(s.description, '') ILIKE :ql"
                        " OR sec.name ILIKE :ql OR COALESCE(c.name, '') ILIKE :ql)")
    params["lim"] = limit + 1
    rows = (await session.execute(text(f"""
        SELECT s.id, s.cidr::text AS cidr, s.description, s.section_id, sec.name AS section_name,
               s.customer_id, c.name AS customer_name, v.name AS vrf_name
          {joins} WHERE {" AND ".join(cond)} ORDER BY {order} LIMIT :lim
    """), params)).all()
    return {
        "subnets": [{"id": str(r.id), "cidr": r.cidr, "description": r.description,
                     "section_id": str(r.section_id), "section_name": r.section_name,
                     "customer_id": str(r.customer_id) if r.customer_id else None,
                     "customer_name": r.customer_name, "vrf_name": r.vrf_name} for r in rows[:limit]],
        "sections": sections, "customers": customers, "truncated": len(rows) > limit,
    }


async def _has_live_record(session: AsyncSession, ip_ids: Sequence[uuid.UUID]) -> bool:
    """有沒有不在歸檔子網路裡的記錄（歸檔子網路的 IP 對誰都是隱藏的，不算「沒有權限」）。"""
    if not ip_ids:
        return False
    return (await session.execute(text("""
        SELECT 1 FROM ip_addresses a JOIN subnets s ON s.id = a.subnet_id
         WHERE a.id = ANY(CAST(:ids AS uuid[])) AND s.archived_at IS NULL LIMIT 1
    """), {"ids": [str(i) for i in ip_ids]})).first() is not None


async def _containing_subnets(session: AsyncSession, user: Any, addr: Any,
                              vrf: Any = None, any_vrf: bool = True) -> tuple[list[Any], list[Any]]:
    """包含這個位址的最小一層子網路：（全部, 使用者看得到的）。

    只看最小一層：看得到外層的 /16、看不到真正包含它的 /24 時，不可以拿 /16 來分析。
    """
    from app.services.permission import visible_ids
    rows = (await session.execute(text("""
        SELECT id, cidr::text AS cidr, vrf_id, masklen(cidr) AS ml FROM subnets
         WHERE archived_at IS NULL AND cidr >>= CAST(:ip AS inet)
           AND (CAST(:any_vrf AS boolean) OR vrf_id IS NOT DISTINCT FROM CAST(:vrf AS uuid))
         ORDER BY masklen(cidr) DESC
    """), {"ip": str(addr), "vrf": str(vrf) if vrf else None, "any_vrf": any_vrf})).all()
    best = [r for r in rows if r.ml == rows[0].ml] if rows else []
    vis = await visible_ids(session, user=user, object_type="subnet")
    return best, [r for r in best if vis is None or r.id in vis]


async def _why_no_candidate(session: AsyncSession, user: Any, addr: Any, *, has_record: bool) -> ScenarioError:
    """輸入的位址沒有看得到的記錄：說清楚是沒有權限、IPAM 沒登記，還是不在任何管理的子網路。

    使用者 2026-10-07 要求：輸入沒有權限的 IP，要講明是權限問題，不要只說找不到。
    多透露的只有「有子網路包含這個位址」，看不到的記錄與子網路內容一律不給。
    """
    best, seen = await _containing_subnets(session, user, addr)
    if has_record or (best and not seen):
        return ScenarioError("impact_target_no_permission", "No permission on this address or its subnet",
                             status=403, address=str(addr))
    if seen:
        return ScenarioError("impact_target_not_registered", "IPAM has no record of this address",
                             status=404, address=str(addr), subnet=seen[0].cidr)
    return ScenarioError("impact_target_unmanaged", "No managed subnet contains this address",
                         status=404, address=str(addr))
