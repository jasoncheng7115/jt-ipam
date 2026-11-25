"""SNMP 收集的寫入層：代理回報解析好的結果，比對與寫入全在伺服器（0217）。

代理只讀設備、只送結構化的列（不送原始輸出）；這裡決定每一列落到哪裡：

| 區段 | 寫到哪裡 | 規則 |
|---|---|---|
| ARP | 既有 IP 記錄的 MAC（依 MAC 來源優先序）、`arp_seen` 證據 | 唯一才算（重疊網段不猜，fw_sightings.SightingBatch）；`arp:snmp` 不會過期，`arp:snmp_live`（ipNetToPhysicalState＝reachable）會；選用：依 ARP 自動建立（預設關，arp_autocreate 的同一套把關） |
| FDB | `fdb_entries`（source='snmp'、switch_device_id＝目標裝置、snmp_target_id） | 完整讀到才刪不再出現的列；部分失敗只新增／更新 |
| 介面 | 目標裝置的 `device_ports`（來源 `snmp:<目標 id>`）；介面位址＝`iface:snmp` 上線證據 | 完整讀到才清掉不再回報、沒接線的埠（reconcile_librenms_ports 同一套） |
| 鄰居 | `snmp_neighbors` | 完整讀到才換掉 |

「完整」＝代理回報該區段 status 是 ok（或 absent：設備明確回答沒有這張表）；partial／error 一律不清任何東西
（memory: project_stale_integration_data —— 上游不再回報的資料只在完整讀到時才清）。

大規模：一台路由器的 ARP 可能五萬列、交換器的 MAC 表十萬列 —— 一律整批（SightingBatch、陣列參數、一次 INSERT）。
"""

from __future__ import annotations

import ipaddress
import time
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sqlin import in_values
from app.models.snmp import (
    RUN_KEEP_DAYS,
    RUN_KEEP_PER_TARGET,
    SECTIONS,
    SnmpNeighbor,
    SnmpRun,
    SnmpTarget,
)

#: 每個區段最多收幾列（代理端另有自己的上限；超過的直接丟掉並標成未完成）
MAX_ROWS = 200_000
#: 交換器位置（switch_port）的推導要掃全部 FDB：每台交換器每 15 分鐘回報一次就各跑一遍太重 → 一個程序內至少隔這麼久
DERIVE_MIN_GAP_S = 600.0
_DERIVE_STATE = {"at": 0.0}
#: 寫進 device_ports 的介面類型（IF-MIB ifType）：乙太網路各種寫法、無線、LAG。VLAN 介面（l2vlan 135／l3ipvlan 136）、
#: loopback（24）、通道（131）、propVirtual（53）這些是設定出來的邏輯介面，放進「連接埠」只會淹掉真正接線的那幾個。
#: 回報沒有 ifType 時不過濾（舊設備）
PORT_IFTYPES = frozenset({6, 7, 62, 69, 71, 117, 161})
#: 狀態代碼（IP-MIB）
_ST_REACHABLE = 1
_ST_INCOMPLETE = 7
_TY_OTHER, _TY_INVALID, _TY_DYNAMIC, _TY_STATIC, _TY_LOCAL = 1, 2, 3, 4, 5
_STATUSES = ("ok", "partial", "error", "absent")


def _norm_mac(v: object) -> str | None:
    """`aa:bb:cc:dd:ee:ff`；全零、廣播、群播不算。"""
    hexs = "".join(c for c in str(v or "").lower() if c in "0123456789abcdef")
    if len(hexs) != 12 or hexs in ("000000000000", "ffffffffffff") or int(hexs[:2], 16) & 1:
        return None
    return ":".join(hexs[i:i + 2] for i in range(0, 12, 2))


def _ip(v: object) -> str | None:
    s = str(v or "").strip().split("/", 1)[0]
    if not s:
        return None
    try:
        return ipaddress.ip_address(s).compressed
    except ValueError:
        return None


def _int(v: object) -> int | None:
    try:
        return int(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _txt(v: object, limit: int) -> str | None:
    if v is None:
        return None
    s = "".join(c for c in str(v) if c >= " " or c == "\t").strip()
    return s[:limit] or None


def _status(sec: dict[str, Any]) -> str:
    s = str(sec.get("status") or "").lower()
    return s if s in _STATUSES else "error"


def complete(sec: dict[str, Any]) -> bool:
    """這一區段是否完整讀到（才可以清掉不再出現的舊資料）。"""
    return _status(sec) in ("ok", "absent") and not sec.get("truncated")


def _rows(sec: dict[str, Any], key: str = "rows") -> list[Any]:
    rows = sec.get(key)
    if not isinstance(rows, list):
        return []
    if len(rows) > MAX_ROWS:
        sec["truncated"] = True
        return rows[:MAX_ROWS]
    return rows


# ─────────────────── ARP ───────────────────
def arp_rows(rows: list[Any]) -> list[dict[str, Any]]:
    """代理的 ARP 列 `[ip, mac, ifIndex, state, type]` → 正規化。

    state 只有 ipNetToPhysicalTable 有（1 reachable … 7 incomplete）；ipNetToMediaTable 沒有（None）。
    type：1 other、2 invalid、3 dynamic、4 static、5 local（設備自己的位址）。
    """
    out: list[dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, (list, tuple)) or len(r) < 2:
            continue
        ip, mac = _ip(r[0]), _norm_mac(r[1])
        state = _int(r[3]) if len(r) > 3 else None
        typ = _int(r[4]) if len(r) > 4 else None
        if ip is None or typ == _TY_INVALID or state == _ST_INCOMPLETE:
            continue
        permanent = typ in (_TY_STATIC, _TY_LOCAL)
        live = state == _ST_REACHABLE and typ in (_TY_DYNAMIC, _TY_OTHER, None)
        out.append({"ip": ip, "mac": mac, "if_index": _int(r[2]) if len(r) > 2 else None,
                    "permanent": permanent, "live": live})
    return out


async def ingest_arp(session: AsyncSession, target: SnmpTarget, device_name: str,
                     sec: dict[str, Any]) -> dict[str, Any]:
    from app.services.fw_sightings import SightingBatch

    recs = arp_rows(_rows(sec))
    now = datetime.now(UTC)
    out: dict[str, Any] = {"rows": len(recs)}
    if target.auto_create_ips and recs:
        from app.services.arp_autocreate import create_from_arp
        pairs = [(r["ip"], r["mac"]) for r in recs if r["mac"] and not r["permanent"]]
        live = {r["ip"] for r in recs if r["live"]}
        adapter = SimpleNamespace(name=device_name, auto_create_from_arp=True,
                                  arp_create_require_fdb=True, arp_create_skip_dhcp=True)
        res = await create_from_arp(
            session, adapter, pairs, now=now, fresh_ips=live, discovery_source="snmp_arp",
            change_source="snmp", who=f"SNMP 收集（裝置「{device_name}」）",
            change_note="依 SNMP 讀到的 ARP 表自動建立")
        out["created"] = res["created"]
        if res["skipped"]:
            out["create_skipped"] = res["skipped"]
    # 重疊網段：不限定範圍，靠「唯一才算」（同一個位址在好幾個子網路都有記錄＝不寫）
    batch = SightingBatch(session, source="snmp", subnet_ids=None)
    for r in recs:
        batch.add(r["ip"], evidence="arp:snmp_live" if r["live"] else "arp:snmp", mac=r["mac"],
                  permanent=r["permanent"], seen_at=now)
    out["matched"] = sum(1 for found, _e in await batch.flush() if found)
    out["live"] = sum(1 for r in recs if r["live"])
    return out


# ─────────────────── FDB ───────────────────
def fdb_rows(rows: list[Any]) -> list[tuple[str, str, int | None]]:
    """代理的 FDB 列 `[mac, 埠名, vlan]` → [(MAC, 埠, VLAN)]（去重、排序）。"""
    out: set[tuple[str, str, int | None]] = set()
    for r in rows:
        if not isinstance(r, (list, tuple)) or len(r) < 2:
            continue
        mac, port = _norm_mac(r[0]), _txt(r[1], 64)
        if not mac or not port:
            continue
        vlan = _int(r[2]) if len(r) > 2 else None
        out.add((mac, port, vlan if vlan is not None and 0 < vlan < 4096 else None))
    return sorted(out, key=lambda x: (x[1], x[0], x[2] or 0))


async def ingest_fdb(session: AsyncSession, target: SnmpTarget, sec: dict[str, Any]) -> dict[str, Any]:
    from app.models.librenms import FDBEntry

    entries = fdb_rows(_rows(sec))
    full = complete(sec)
    now = datetime.now(UTC)
    existing = {(str(m), p, v): i for i, m, p, v in (await session.execute(
        select(FDBEntry.id, FDBEntry.mac, FDBEntry.port_name, FDBEntry.vlan_id_num)
        .where(FDBEntry.snmp_target_id == target.id))).all()}
    seen_ids = [existing[k] for k in entries if k in existing]
    new = [{"mac": m, "port_name": p, "vlan_id_num": v, "source": "snmp", "switch_device_id": target.device_id,
            "snmp_target_id": target.id, "first_seen_at": now, "last_seen_at": now}
           for (m, p, v) in entries if (m, p, v) not in existing]
    if seen_ids:
        await session.execute(update(FDBEntry).where(in_values(FDBEntry.id, seen_ids))
                              .values(last_seen_at=now, switch_device_id=target.device_id))
    if new:
        await session.execute(insert(FDBEntry), new)
    gone: list[Any] = []
    if full:
        wanted = set(entries)
        gone = [i for k, i in existing.items() if k not in wanted]
        if gone:
            await session.execute(delete(FDBEntry).where(in_values(FDBEntry.id, gone)))
    out: dict[str, Any] = {"rows": len(entries), "new": len(new), "removed": len(gone)}
    # IP 的「交換器位置」：與 LibreNMS／MikroTik 同一套推導（各來源的 FDB 一起投票）
    if time.monotonic() - _DERIVE_STATE["at"] >= DERIVE_MIN_GAP_S:
        from app.services.librenms import derive_switch_ports
        await session.flush()
        _DERIVE_STATE["at"] = time.monotonic()
        out["switch_ports"] = await derive_switch_ports(session, SimpleNamespace(scope_subnet_ids=None),
                                                        source="snmp")
    return out


# ─────────────────── 介面 ───────────────────
def port_origin(target_id: uuid.UUID) -> str:
    return f"snmp:{target_id}"


def interface_ports(rows: list[Any]) -> dict[str, dict[str, Any]]:
    """介面列（dict：if_index／name／descr／alias／type／mac／admin／oper）→ {埠名: {mac, description}}。

    埠名用 ifName（短、跟 FDB 與 LLDP 講的是同一個），沒有才用 ifDescr。
    ifAlias 只有真的是描述才寫（有些設備的 ifAlias 等於介面名稱，照抄只會塞滿說明欄）。
    """
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        name = _txt(r.get("name"), 255) or _txt(r.get("descr"), 255)
        if not name:
            continue
        typ = _int(r.get("type"))
        if typ is not None and typ not in PORT_IFTYPES:
            continue
        alias = _txt(r.get("alias"), 500)
        descr = _txt(r.get("descr"), 255)
        if alias and alias in (name, descr):
            alias = None
        out[name] = {"mac": _norm_mac(r.get("mac")), "description": alias}
    return out


def up_addresses(rows: list[Any], addresses: list[Any]) -> list[str]:
    """介面位址中、所在介面 ifOperStatus＝up(1) 的那些（iface:snmp 上線證據）。"""
    from app.services.iface_evidence import _clean, _dedupe
    up = {_int(r.get("if_index")) for r in rows if isinstance(r, dict) and _int(r.get("oper")) == 1}
    picked: list[str | None] = []
    for a in addresses:
        if isinstance(a, dict) and _int(a.get("if_index")) in up:
            picked.append(_clean(a.get("ip")))
    return _dedupe(picked)


async def ingest_interfaces(session: AsyncSession, target: SnmpTarget, sec: dict[str, Any]) -> dict[str, Any]:
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.models.physical import DevicePort
    from app.services import iface_evidence
    from app.services.librenms import reconcile_librenms_ports

    rows = _rows(sec)
    out: dict[str, Any] = {"rows": len(rows)}
    addrs = up_addresses(rows, _rows(sec, "addresses"))
    if addrs:
        res = await iface_evidence.stamp(session, "iface:snmp", addrs, scope_ids=None,
                                         instance=(target.id, await _device_name(session, target.device_id)))
        out["addresses"] = res["matched"]
    ports = interface_ports(rows)
    if not ports:
        out["ports"] = 0
        return out
    origin = port_origin(target.id)
    dev_id = target.device_id
    have = {n for (n,) in (await session.execute(
        select(DevicePort.name).where(DevicePort.device_id == dev_id))).all()}
    ins = pg_insert(DevicePort)
    new_mac = func.coalesce(ins.excluded.mac_address, DevicePort.mac_address)
    new_descr = func.coalesce(ins.excluded.description, DevicePort.description)
    await session.execute(ins.on_conflict_do_update(
        index_elements=["device_id", "name"], set_={"mac_address": new_mac, "description": new_descr},
        where=DevicePort.mac_address.is_distinct_from(new_mac) | DevicePort.description.is_distinct_from(new_descr),
    ), [{"device_id": dev_id, "name": n, "type": "network", "mac_address": v["mac"],
         "description": v["description"], "source_origin": origin} for n, v in sorted(ports.items())])
    await session.flush()
    out["ports"] = len(ports)
    out["new"] = sum(1 for n in ports if n not in have)
    if complete(sec):
        # 認領沒有來源的舊埠、清掉這台不再回報（而且沒接線）的 —— 與 LibreNMS／MikroTik 同一套規則
        out["removed"] = await reconcile_librenms_ports(session, dev_id, origin, set(ports))
    return out


# ─────────────────── 鄰居 ───────────────────
def neighbor_rows(rows: list[Any]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    out: list[dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        proto = str(r.get("protocol") or "").lower()
        if proto not in ("lldp", "cdp"):
            continue
        rec = {
            "protocol": proto,
            "local_port": _txt(r.get("local_port"), 255),
            "remote_sysname": _txt(r.get("remote_sysname"), 255),
            "remote_port": _txt(r.get("remote_port"), 255),
            "remote_chassis_id": _txt(r.get("remote_chassis_id"), 255),
            "remote_mac": _norm_mac(r.get("remote_mac")),
            "remote_mgmt_address": _ip(r.get("remote_mgmt_address")),
            "remote_platform": _txt(r.get("remote_platform"), 255),
        }
        if not (rec["remote_sysname"] or rec["remote_chassis_id"] or rec["remote_mgmt_address"]):
            continue
        key = (proto, rec["local_port"], rec["remote_chassis_id"] or rec["remote_sysname"], rec["remote_port"])
        if key in seen:
            continue
        seen.add(key)
        out.append(rec)
    return out


def _nkey(n: Any) -> tuple[Any, ...]:
    get = n.get if isinstance(n, dict) else lambda k: getattr(n, k)
    return (get("protocol"), get("local_port"), get("remote_chassis_id") or get("remote_sysname"), get("remote_port"))


async def ingest_neighbors(session: AsyncSession, target: SnmpTarget, sec: dict[str, Any]) -> dict[str, Any]:
    recs = neighbor_rows(_rows(sec))
    now = datetime.now(UTC)
    existing = {_nkey(n): n for n in (await session.execute(
        select(SnmpNeighbor).where(SnmpNeighbor.target_id == target.id))).scalars().all()}
    keep: set[tuple[Any, ...]] = set()
    new = 0
    for r in recs:
        key = _nkey(r)
        keep.add(key)
        row = existing.get(key)
        if row is None:
            session.add(SnmpNeighbor(target_id=target.id, device_id=target.device_id,
                                     first_seen_at=now, last_seen_at=now, **r))
            new += 1
        else:
            for k, v in r.items():
                setattr(row, k, v)
            row.device_id = target.device_id
            row.last_seen_at = now
    removed = 0
    if complete(sec):
        for key, row in existing.items():
            if key not in keep:
                await session.delete(row)
                removed += 1
    return {"rows": len(recs), "new": new, "removed": removed}


# ─────────────────── 整份回報 ───────────────────
_SYSTEM_LIMITS = {"sys_name": 255, "sys_descr": 1000, "sys_object_id": 255, "sys_contact": 255, "sys_location": 255}


def clean_system(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    out: dict[str, Any] = {k: _txt(raw.get(k), n) for k, n in _SYSTEM_LIMITS.items() if raw.get(k) is not None}
    up = _int(raw.get("sys_uptime"))
    if up is not None:
        out["sys_uptime"] = up
    return out or None


async def _device_name(session: AsyncSession, device_id: uuid.UUID) -> str:
    from app.models.device import Device
    return str(await session.scalar(select(Device.name).where(Device.id == device_id)) or device_id)


async def ingest_report(session: AsyncSession, agent: Any, target: SnmpTarget,
                        payload: dict[str, Any]) -> dict[str, Any]:
    """代理的一份回報（可能只含這一輪到期的幾個區段）。寫完更新目標狀態並留一筆收集記錄；不 commit。"""
    now = datetime.now(UTC)
    sections_in = payload.get("sections") if isinstance(payload.get("sections"), dict) else {}
    handlers = {"arp": ingest_arp, "fdb": ingest_fdb, "interfaces": ingest_interfaces,
                "neighbors": ingest_neighbors}
    name = await _device_name(session, target.device_id)
    summary: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for sname in SECTIONS:
        sec = sections_in.get(sname)
        if not isinstance(sec, dict) or not getattr(target, f"collect_{sname}", False):
            continue
        st = _status(sec)
        item: dict[str, Any] = {"status": st, "elapsed_ms": _int(sec.get("elapsed_ms")),
                                "at": now.isoformat()}
        if sec.get("error"):
            item["error"] = _txt(sec.get("error"), 300)
        if st != "error":
            try:
                if sname == "arp":
                    item.update(await ingest_arp(session, target, name, sec))
                else:
                    item.update(await handlers[sname](session, target, sec))
            except Exception as exc:    # 單一區段寫入失敗不可以讓其他區段一起失敗
                import logging
                logging.getLogger(__name__).exception("snmp ingest %s %s", target.id, sname)
                st = item["status"] = "error"
                item["error"] = f"{type(exc).__name__}: {exc}"[:300]
        if sec.get("truncated"):
            item["truncated"] = True
        if st in ("error", "partial"):
            errors[sname] = item.get("error") or st
        summary[sname] = item

    whole_error = _txt(payload.get("error"), 500)
    system = clean_system(payload.get("system"))
    last = dict(target.last_summary or {})
    if system:
        last["system"] = system
    secs = dict(last.get("sections") or {})
    secs.update(summary)
    last["sections"] = secs
    target.last_summary = last
    target.last_run_at = now
    if whole_error:
        target.last_error = whole_error
        target.last_error_code = _txt(payload.get("error_code"), 64) or "snmp_collect_failed"
        result = "failed"
    elif errors:
        target.last_error = "；".join(f"{k}: {v}" for k, v in errors.items())[:2000]
        target.last_error_code = "snmp_sections_failed"
        result = "partial" if len(errors) < len(summary) else "failed"
    else:
        target.last_error = None
        target.last_error_code = None
        target.last_ok_at = now
        result = "success"

    run = await _run_for(session, target, payload, agent)
    run.result = result
    run.finished_at = now
    run.duration_ms = _int(payload.get("elapsed_ms"))
    run.sections = summary or None
    run.info = {"system": system} if system else None
    run.error = whole_error or (target.last_error if errors else None)
    run.error_code = target.last_error_code if result != "success" else None
    await prune_runs(session, target.id)
    return {"result": result, "sections": summary}


async def _run_for(session: AsyncSession, target: SnmpTarget, payload: dict[str, Any], agent: Any) -> SnmpRun:
    """立即收集（帶 job_id）沿用按下按鈕時建立的那一筆；排程收集新增一筆。"""
    job_id = payload.get("job_id")
    if job_id:
        try:
            run = (await session.execute(select(SnmpRun).where(
                SnmpRun.job_id == uuid.UUID(str(job_id)), SnmpRun.target_id == target.id))).scalars().first()
        except ValueError:
            run = None
        if run is not None:
            return run
    run = SnmpRun(target_id=target.id, agent_id=agent.id, kind="collect",
                  trigger="manual" if job_id else "scheduled", result="running", started_at=datetime.now(UTC))
    session.add(run)
    return run


async def prune_runs(session: AsyncSession, target_id: uuid.UUID) -> None:
    """收集記錄只留最近一段：14 天內、而且每個目標最多 RUN_KEEP_PER_TARGET 筆（ARP 每 5 分鐘一筆，量很快就上來）。"""
    cutoff = datetime.now(UTC) - timedelta(days=RUN_KEEP_DAYS)
    await session.execute(delete(SnmpRun).where(SnmpRun.target_id == target_id, SnmpRun.started_at < cutoff))
    edge = (await session.execute(
        select(SnmpRun.started_at).where(SnmpRun.target_id == target_id)
        .order_by(SnmpRun.started_at.desc()).offset(RUN_KEEP_PER_TARGET).limit(1))).scalar()
    if edge is not None:
        await session.execute(delete(SnmpRun).where(SnmpRun.target_id == target_id, SnmpRun.started_at <= edge))


#: 立即收集／測試按下去之後一直沒有結果（代理離線、工作過期）：多久之後記成失敗
STUCK_AFTER = timedelta(minutes=10)


async def expire_stuck_runs(session: AsyncSession) -> None:
    await session.execute(update(SnmpRun).where(
        SnmpRun.result == "running", SnmpRun.started_at < datetime.now(UTC) - STUCK_AFTER,
    ).values(result="failed", finished_at=datetime.now(UTC), error_code="snmp_job_expired",
             error="代理沒有在時限內回報（可能離線）"))


# ─────────────────── 測試結果（代理工作通道）───────────────────
_SECTION_KEYS = ("status", "rows", "error", "elapsed_ms", "table", "truncated")


def clean_test_result(raw: Any) -> list[dict[str, Any]]:
    """代理回報的逐組憑證測試結果 → 只留認得的欄位（長度有上限；不會有機密：代理不回傳憑證內容）。"""
    items = raw.get("items") if isinstance(raw, dict) else raw
    out: list[dict[str, Any]] = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        rec: dict[str, Any] = {
            "credential_id": _txt(it.get("credential_id"), 64),
            "ok": bool(it.get("ok")),
            "elapsed_ms": _int(it.get("elapsed_ms")),
            "error": _txt(it.get("error"), 500),
            "system": clean_system(it.get("system")),
        }
        secs: dict[str, Any] = {}
        for s in SECTIONS:
            v = (it.get("sections") or {}).get(s) if isinstance(it.get("sections"), dict) else None
            if isinstance(v, dict):
                secs[s] = {k: (_txt(v.get(k), 300) if k in ("error", "status", "table") else v.get(k))
                           for k in _SECTION_KEYS if v.get(k) is not None}
        rec["sections"] = secs
        out.append(rec)
        if len(out) >= 10:
            break
    return out


async def on_job_finished(session: AsyncSession, job: Any) -> None:
    """代理回報工作結果：更新對應的收集記錄（測試的逐組結果、立即收集沒能回報資料時的失敗原因）。"""
    run = (await session.execute(select(SnmpRun).where(SnmpRun.job_id == job.id))).scalars().first()
    if run is None:
        return
    now = datetime.now(UTC)
    if job.kind == "snmp_test":
        items = clean_test_result(job.result)
        from app.models.snmp import SnmpCredential
        names = dict((await session.execute(select(SnmpCredential.id, SnmpCredential.name).where(
            in_values(SnmpCredential.id, [uuid.UUID(i["credential_id"]) for i in items
                                          if _is_uuid(i.get("credential_id"))])))).all()) if items else {}
        for i in items:
            if _is_uuid(i.get("credential_id")):
                i["credential_name"] = names.get(uuid.UUID(i["credential_id"]))
        job.result = {"items": items}
        run.info = {"credentials": items}
        run.result = "success" if any(i["ok"] for i in items) else "failed"
        if job.error:
            run.result = "failed"
            run.error = _txt(job.error, 1000)
            run.error_code = "snmp_test_failed"
        elif run.result == "failed":
            run.error_code = "snmp_test_failed"
            run.error = next((i["error"] for i in items if i.get("error")), None)
        run.finished_at = now
        run.duration_ms = int((now - run.started_at).total_seconds() * 1000) if run.started_at else None
        best = next((i for i in items if i["ok"]), None)
        if best and best.get("sections"):
            run.sections = best["sections"]
    elif run.result == "running" and job.error:
        # 立即收集：代理連回報都沒送出來（設備不通、憑證錯）→ 這一筆記成失敗
        run.result = "failed"
        run.finished_at = now
        run.error = _txt(job.error, 1000)
        run.error_code = "snmp_collect_failed"
        target = await session.get(SnmpTarget, run.target_id)
        if target is not None:
            target.last_run_at = now
            target.last_error = run.error
            target.last_error_code = "snmp_collect_failed"


def _is_uuid(v: Any) -> bool:
    try:
        uuid.UUID(str(v))
        return True
    except ValueError:
        return False


# ─────────────────── 鄰居 → 它是哪一台裝置 ───────────────────
async def match_neighbors(session: AsyncSession, rows: list[Any]) -> dict[Any, dict[str, str | None]]:
    """鄰居 → jt-ipam 裝置：先看宣告的管理位址與 chassis MAC（mikrotik.match_neighbor_devices 同一套），
    對不到再看系統名稱（去掉網域後與裝置名稱相同、而且只有一台）。對到多台就不填 —— 猜錯比不畫更難查。
    回傳 {鄰居列 id: {"remote_device_id", "remote_device_name"}}。"""
    from app.models.device import Device
    from app.services.mikrotik import match_neighbor_devices

    if not rows:
        return {}
    probes = [SimpleNamespace(id=r.id, address=r.remote_mgmt_address, mac=r.remote_mac) for r in rows]
    picked: dict[Any, dict[str, str | None]] = {
        rid: {"remote_device_id": v["device_id"], "remote_device_name": v.get("device_name")}
        for rid, v in (await match_neighbor_devices(session, probes)).items()}
    rest = {r.id: (r.remote_sysname or "").split(".")[0].strip().lower() for r in rows
            if r.id not in picked and r.remote_sysname}
    wanted = {n for n in rest.values() if n}
    if wanted:
        by_name: dict[str, list[tuple[Any, str]]] = {}
        for did, dname in (await session.execute(select(Device.id, Device.name).where(
                in_values(func.lower(Device.name), wanted, type_=Device.name.type)))).all():
            by_name.setdefault(str(dname).strip().lower(), []).append((did, dname))
        for rid, n in rest.items():
            cand = by_name.get(n) or []
            if len(cand) == 1:
                picked[rid] = {"remote_device_id": str(cand[0][0]), "remote_device_name": cand[0][1]}
    return picked
