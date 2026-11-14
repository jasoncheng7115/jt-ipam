"""戰情室 11 AI 巡檢結果（2026-10-23 新增主題；規格見使用者交付的 JT-IPAM-Warroom-Expansion SPEC.md 第 8 節）。

只讀已保存的結果：切頁、輪播、重新整理都**不呼叫 LLM、不開始巡檢、不改巡檢狀態**。

資料來源
- 發現：ai_findings（已經是結構化的：嚴重度、分類、標題、敘述、建議、evidence、指紋、處理狀態）。
  巡檢每次對齊「未處理」清單（reconcile_findings），所以未處理的就是目前的判讀；畫面再依指紋去重一次
  （舊版留下的重複列、同一件事跨巡檢都只算一次）
- 巡檢狀態：system_settings 的 ai_audit_state（ai_audit._finish 記下 completed／partial／failed、
  錯誤代碼、取樣範圍；running_since＋背景作業判斷進行中）。沒有結果欄位的舊紀錄標「未記錄」，不猜
- 下次排程：只在 LLM 與 AI 巡檢排程都啟用時才算（schedule.next_occurrence）；沒有排程就不給時間

指標（三者可重疊，畫面要寫明不能相加）
- 高優先＝嚴重度 high 的未處理發現；待確認＝未處理的發現（模型建議不等於已處理，只有人按忽略才離開）；
  資料不足＝依據裡至少有一個位址／名稱對不到現有記錄（未驗證），或根本沒有可對應的依據
- 沒有可用結果（未執行、失敗且之前沒有完成過、全部不能給這個觀看者看）時指標是「—」，不是 0

可見範圍（多租戶，規格 8.4）
- 完整權限（管理員、戰情室檢視者）：全部；設了顯示範圍時只列依據都落在範圍內的發現（對不到記錄的不列）
- 其他帳號：依據每一個都要對得到、而且看得到；再加上**這次巡檢送進模型的全部物件他都看得到**
  （ai_audit_runs 記的 id）—— 否則模型寫的標題與敘述可能夾著他看不到的資料，整筆不給。
  不給的只說「有發現未顯示」，不說幾筆
- 子網路涵蓋數同理：看不到整次取樣範圍的帳號不給整體規模
"""
from __future__ import annotations

import ipaddress
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import String, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sqlin import in_values
from app.models.address import IPAddress
from app.models.ai_finding import AIFinding
from app.models.device import Device
from app.models.subnet import Subnet
from app.services.warroom.common import full_access, paginate, scope_subnet_ids

PER_PAGE = 3
#: 排定時刻過後這麼久還沒有新的結果才算過期（timer 約 5 分鐘一輪，加上收集與第一批的時間）
FRESH_GRACE = timedelta(minutes=15)
SEV_RANK = {"high": 0, "medium": 1, "low": 2}
#: IP 記錄上「誰在什麼時候看到它」的欄位 → 來源代碼（畫面翻成名稱）
SEEN_FIELDS = (("last_seen_scanner", "scanner"), ("last_seen_librenms", "librenms"), ("last_seen_arp", "arp"),
               ("last_seen_dns", "dns"), ("last_seen_wazuh", "wazuh"), ("last_seen_zabbix", "zabbix"),
               ("last_seen_checkmk", "checkmk"),
               ("last_seen_ocs", "ocs"))
MAX_FACTS = 3
DASH = "—"


def _plain(text: str | None, limit: int) -> str:
    """模型寫的敘述轉成純文字（不帶 markdown 記號），長度設上限；畫面再依行數截斷。"""
    t = re.sub(r"(\*\*|__|`)", "", text or "")
    t = re.sub(r"\s+", " ", t).strip()
    return t[:limit]


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def _parse(v: Any) -> datetime | None:
    if not isinstance(v, str):
        return None
    try:
        return datetime.fromisoformat(v)
    except ValueError:
        return None


def _evidence_refs(ev: Any) -> tuple[list[str], list[str], list[str], list[str], list[str]]:
    """evidence → (IP, 子網路, 裝置名稱, 主機名稱, 認不出來的字樣)。都是模型寫的，之後一律要對到記錄才算數。"""
    ips: list[str] = []
    subnets: list[str] = []
    devices: list[str] = []
    hosts: list[str] = []
    other: list[str] = []
    if not isinstance(ev, dict):
        return ips, subnets, devices, hosts, other

    def _strs(key: str) -> list[str]:
        v = ev.get(key)
        return [str(x).strip() for x in v if str(x).strip()][:20] if isinstance(v, list) else []

    for raw in _strs("ips"):
        if "/" in raw:
            subnets.append(raw)
            continue
        try:
            ips.append(str(ipaddress.ip_address(raw)))
        except ValueError:
            other.append(raw)
    subnets += _strs("subnets")
    devices += _strs("devices")
    hosts += _strs("hostnames")
    norm_subnets = []
    for raw in subnets:
        try:
            norm_subnets.append(str(ipaddress.ip_network(raw, strict=False)))
        except ValueError:
            other.append(raw)
    return (list(dict.fromkeys(ips)), list(dict.fromkeys(norm_subnets)), list(dict.fromkeys(devices)),
            list(dict.fromkeys(hosts)), list(dict.fromkeys(other)))


def _unique(rows: list[Any], audited: Any) -> Any | None:
    """同一個字樣對到幾筆記錄：只有一筆才算對到；多筆（重疊網段、同名）時先看有勾 AI 巡檢的那幾筆，
    還是不只一筆就算對不到 —— 不因為同 IP 或同名稱就自己挑一筆。"""
    if len(rows) == 1:
        return rows[0]
    narrowed = [r for r in rows if audited(r)]
    return narrowed[0] if len(narrowed) == 1 else None


class _Resolver:
    """把所有發現的依據一次對到記錄（每種一個查詢）。"""

    def __init__(self) -> None:
        self.ip: dict[str, Any] = {}
        self.host: dict[str, Any] = {}
        self.dev: dict[str, Any] = {}
        self.sub: dict[str, Any] = {}

    async def load(self, session: AsyncSession, findings: list[AIFinding]) -> None:
        ips: set[str] = set()
        subs: set[str] = set()
        devs: set[str] = set()
        hosts: set[str] = set()
        for f in findings:
            i, s, d, h, _o = _evidence_refs(f.evidence)
            ips.update(i)
            subs.update(s)
            devs.update(d)
            hosts.update(h)
        cols = (IPAddress, Subnet.ai_audit_enabled)
        if ips:
            by: dict[str, list[Any]] = {}
            for row in (await session.execute(
                    select(*cols).join(Subnet, Subnet.id == IPAddress.subnet_id)
                    .where(in_values(func.host(IPAddress.ip), list(ips), type_=String())))).all():
                by.setdefault(str(ipaddress.ip_address(str(row[0].ip).split("/")[0])), []).append(row)
            self.ip = {k: u[0] for k, v in by.items() if (u := _unique(v, lambda r: r[1])) is not None}
        if hosts:
            byh: dict[str, list[Any]] = {}
            for row in (await session.execute(
                    select(*cols).join(Subnet, Subnet.id == IPAddress.subnet_id)
                    .where(in_values(IPAddress.hostname, list(hosts))))).all():
                byh.setdefault(row[0].hostname, []).append(row)
            self.host = {k: u[0] for k, v in byh.items() if (u := _unique(v, lambda r: r[1])) is not None}
        if devs:
            byd: dict[str, list[Any]] = {}
            for d in (await session.execute(select(Device).where(in_values(Device.name, list(devs))))).scalars():
                byd.setdefault(d.name, []).append(d)
            self.dev = {k: v[0] for k, v in byd.items() if len(v) == 1}
        if subs:
            bys: dict[str, list[Any]] = {}
            for s in (await session.execute(
                    select(Subnet).where(in_values(cast(Subnet.cidr, String), list(subs), type_=String())))).scalars():
                bys.setdefault(str(s.cidr), []).append(s)
            self.sub = {k: u for k, v in bys.items() if (u := _unique(v, lambda s: s.ai_audit_enabled)) is not None}

    def subjects(self, f: AIFinding) -> tuple[list[tuple[str, Any]], list[str]]:
        """(對到的 [(種類, 記錄)], 對不到的字樣)。同一筆記錄只列一次。"""
        ips, subs, devs, hosts, other = _evidence_refs(f.evidence)
        mapped: list[tuple[str, Any]] = []
        seen: set[Any] = set()
        missing: list[str] = list(other)

        def _add(kind: str, obj: Any, text: str) -> None:
            if obj is None:
                missing.append(text)
            elif obj.id not in seen:
                seen.add(obj.id)
                mapped.append((kind, obj))

        for t in ips:
            _add("ip", self.ip.get(t), t)
        for t in hosts:
            _add("ip", self.host.get(t), t)
        for t in devs:
            _add("device", self.dev.get(t), t)
        for t in subs:
            _add("subnet", self.sub.get(t), t)
        return mapped, list(dict.fromkeys(missing))


def _sources(ip: IPAddress) -> list[str]:
    out = [src for field, src in SEEN_FIELDS if getattr(ip, field, None)]
    if ip.in_dhcp_lease or ip.dhcp_reserved:
        out.append("dhcp")
    return out


def _facts(mapped: list[tuple[str, Any]]) -> list[dict[str, Any]]:
    """右側證據表：IP 記錄上可以核對的事實（現在的記錄值，不是巡檢當下的快照；畫面有標明）。"""
    rows: list[dict[str, Any]] = []
    for kind, obj in mapped:
        if kind != "ip":
            continue
        ip = str(obj.ip).split("/")[0]
        seen = sorted(((getattr(obj, field), src) for field, src in SEEN_FIELDS if getattr(obj, field, None)),
                      key=lambda x: x[0], reverse=True)
        if not seen:
            rows.append({"at": None, "source": "ipam", "ip": ip, "kind": "never_seen"})
        for at, src in seen[:2]:
            rows.append({"at": at.isoformat(), "source": src, "ip": ip, "kind": "last_seen"})
        if obj.mac:
            rows.append({"at": _iso(obj.last_seen_arp) if obj.mac_source == "arp" else None,
                         "source": obj.mac_source or "ipam", "ip": ip, "kind": "mac", "value": str(obj.mac)})
        if len(rows) >= MAX_FACTS:
            break
    return rows[:MAX_FACTS]


def _subject_out(kind: str, obj: Any) -> dict[str, Any]:
    if kind == "ip":
        return {"kind": "ip", "ip": str(obj.ip).split("/")[0], "hostname": obj.hostname, "verified": True}
    if kind == "device":
        return {"kind": "device", "name": obj.name, "verified": True}
    return {"kind": "subnet", "cidr": str(obj.cidr), "verified": True}


async def _running(session: AsyncSession, state: dict[str, Any], now: datetime) -> datetime | None:
    """正在跑的巡檢從什麼時候開始（排程行程的標記，或手動的背景作業）；沒有回 None。"""
    from app.models.background_task import BackgroundTask
    from app.services.ai_audit import RUN_STALE_AFTER
    since = _parse(state.get("running_since"))
    if since is not None and now - since < RUN_STALE_AFTER:
        return since
    row = (await session.execute(
        select(BackgroundTask.started_at, BackgroundTask.queued_at).where(
            BackgroundTask.kind == "ai_audit.run", BackgroundTask.status.in_(("pending", "running")))
        .limit(1))).first()
    if row is not None:
        return row[0] or row[1] or now
    return None


async def _schedule(session: AsyncSession, state: dict[str, Any], now: datetime,
                    running: bool) -> tuple[dict[str, Any], Any]:
    from app.services.schedule import due, next_occurrence
    from app.services.system_config import get_llm_config
    cfg = await get_llm_config(session)
    enabled = bool(cfg.enabled and cfg.ai_audit_enabled)
    kw = {"frequency": cfg.ai_audit_frequency, "weekdays": cfg.ai_audit_weekdays,
          "month_day": cfg.ai_audit_month_day}
    out: dict[str, Any] = {"enabled": enabled, "next_at": None, "due_now": False}
    if enabled:
        local = now.astimezone()
        nxt = next_occurrence(cfg.ai_audit_times, local, **kw)
        out["next_at"] = _iso(nxt)
        out["due_now"] = (not running) and due(_parse(state.get("last_run_at")), cfg.ai_audit_times, local, **kw)

    def stale(snapshot_at: datetime | None) -> bool | None:
        if not enabled or snapshot_at is None:
            return None
        return due(snapshot_at, cfg.ai_audit_times, now.astimezone() - FRESH_GRACE, **kw)
    return out, stale


def _covered(run: dict[str, set[str]] | None, vis: dict[str, set[str] | None]) -> bool:
    """觀看者看不看得到這次巡檢送進模型的全部物件（看得到才不會從敘述裡讀到別人的資料）。"""
    if run is None:
        return False
    for key, ids in (("subnet", run["subnet_ids"]), ("ip", run["ip_ids"]), ("device", run["device_ids"])):
        allowed = vis[key]
        if allowed is not None and not ids <= allowed:
            return False
    return True


async def build(session: AsyncSession, *, user: Any, cfg: dict[str, Any], page: int = 1,
                now: datetime | None = None) -> dict[str, Any]:
    from app.services.system_config import get_ai_audit_run_scopes, get_ai_audit_state

    now = now or datetime.now(UTC)
    state = await get_ai_audit_state(session)
    running_since = await _running(session, state, now)
    schedule, stale_of = await _schedule(session, state, now, running_since is not None)

    full = await full_access(session, user)
    scope = await scope_subnet_ids(session, cfg)
    scope_s = {str(x) for x in scope} if scope is not None else None
    vis: dict[str, set[str] | None] = {"subnet": None, "ip": None, "device": None}
    if not full:
        from app.services.permission import visible_ids
        for key in vis:
            got = await visible_ids(session, user=user, object_type=key)   # type: ignore[arg-type]
            vis[key] = {str(x) for x in got} if got is not None else None
    run_scopes = await get_ai_audit_run_scopes(session)

    rows = list((await session.execute(
        select(AIFinding).order_by(AIFinding.updated_at.desc()).limit(2000))).scalars().all())
    resolver = _Resolver()
    await resolver.load(session, rows)

    # 顯示範圍下的裝置：有任何一筆 IP 落在範圍內的子網路才算在範圍內
    dev_in_scope: set[str] = set()
    if scope is not None and resolver.dev:
        dev_in_scope = {str(d) for d, in (await session.execute(
            select(IPAddress.device_id).where(in_values(IPAddress.device_id, [d.id for d in resolver.dev.values()]),
                                              in_values(IPAddress.subnet_id, list(scope))).distinct())).all()}

    def in_scope(kind: str, obj: Any) -> bool:
        if scope_s is None:
            return True
        if kind == "ip":
            return str(obj.subnet_id) in scope_s
        if kind == "subnet":
            return str(obj.id) in scope_s
        return str(obj.id) in dev_in_scope

    def visible(kind: str, obj: Any) -> bool:
        allowed = vis["subnet" if kind == "subnet" else kind]
        return allowed is None or str(obj.id) in allowed

    shown: list[tuple[AIFinding, list[tuple[str, Any]], list[str]]] = []
    dismissed = 0
    hidden = 0
    seen_fp: set[tuple[str, str]] = set()
    for f in rows:                                   # 新到舊：同指紋留最新那一列
        key = f.fingerprint or f"id:{f.id}"
        if (f.status, key) in seen_fp:
            continue
        seen_fp.add((f.status, key))
        mapped, missing = resolver.subjects(f)
        ok = True
        if scope_s is not None and (not mapped or not all(in_scope(k, o) for k, o in mapped)):
            ok = False
        if ok and not full:
            ok = (not missing and bool(mapped) and all(visible(k, o) for k, o in mapped)
                  and _covered(run_scopes.get(str(f.run_id)), vis))
        if not ok:
            hidden += f.status == "open"
            continue
        if f.status == "dismissed":
            dismissed += 1
        elif f.status == "open":
            shown.append((f, mapped, missing))

    # ── 巡檢狀態
    last_status = state.get("last_status")
    completed_at = _parse(state.get("completed_at"))
    legacy = not last_status and completed_at is None
    if running_since is not None:
        status = "running"
    elif last_status in ("completed", "partial", "failed"):
        status = last_status
    elif state.get("finished_at") or state.get("last_run_at") or rows:
        status = "unrecorded"                        # 舊版的巡檢沒記結果：不猜它成功還是失敗
    else:
        status = "not_run"
    has_result = completed_at is not None or (legacy and bool(rows))

    snapshot_at = _parse(state.get("completed_started_at"))
    stale = stale_of(snapshot_at) if completed_at is not None else None
    freshness = None if completed_at is None else (
        "no_schedule" if stale is None else "stale" if stale and status != "running" else "fresh")

    # 涵蓋範圍（依觀看者）：完整權限且沒設顯示範圍用整體數；否則用記下的 id 取交集，看不到整次取樣的不給
    counts = state.get("completed_scope") if isinstance(state.get("completed_scope"), dict) else None
    run = run_scopes.get(str(state.get("completed_run_id") or ""))
    cover: dict[str, Any] = {"subnets": None, "ips": None, "capped": False}
    if completed_at is not None and (full or _covered(run, vis)):
        if scope_s is None and counts:
            cover = {"subnets": counts.get("subnets"), "ips": counts.get("ips"),
                     "capped": bool((counts.get("capped") or {}).get("ips"))}
        elif run is not None:
            sub_n = len(run["subnet_ids"] & scope_s) if scope_s is not None else len(run["subnet_ids"])
            ip_n: int = len(run["ip_ids"])
            if scope is not None:
                import uuid as _uuid
                ip_n = int(await session.scalar(select(func.count()).select_from(IPAddress).where(
                    in_values(IPAddress.id, [_uuid.UUID(x) for x in run["ip_ids"]]),
                    in_values(IPAddress.subnet_id, list(scope)))) or 0) if run["ip_ids"] else 0
            cover = {"subnets": sub_n, "ips": ip_n, "capped": bool(counts and (counts.get("capped") or {}).get("ips"))}

    # ── 清單與指標
    shown.sort(key=lambda x: (SEV_RANK.get(x[0].severity, 3), -(x[0].updated_at or now).timestamp()))
    withheld_all = has_result and not shown and hidden > 0 and not full
    if not has_result or withheld_all:
        metrics: dict[str, Any] = {"high": DASH, "open": DASH, "insufficient": DASH}
    else:
        metrics = {"high": sum(1 for f, _m, _x in shown if f.severity == "high"), "open": len(shown),
                   "insufficient": sum(1 for _f, m, x in shown if x or not m)}

    part, page, pages = paginate(shown, PER_PAGE, page)
    items = []
    for n, (f, mapped, missing) in enumerate(part):
        subjects = [_subject_out(k, o) for k, o in mapped] + \
                   [{"kind": "unverified", "text": t, "verified": False} for t in missing]
        srcs: list[str] = []
        for k, o in mapped:
            if k == "ip":
                srcs += _sources(o)
        if not srcs and mapped:
            srcs = ["ipam"]
        ev = f.evidence if isinstance(f.evidence, dict) else {}
        items.append({
            "id": str(f.id), "rank": (page - 1) * PER_PAGE + n + 1,
            "priority": f.severity if f.severity in SEV_RANK else "low", "category": f.category,
            "review": "open", "title": _plain(f.title, 200), "subjects": subjects[:6],
            "more_subjects": max(len(subjects) - 6, 0), "unverified": len(missing),
            "sources": list(dict.fromkeys(srcs)), "facts": _facts(mapped),
            "detail": _plain(f.detail, 900), "note": _plain(ev.get("note") if isinstance(ev.get("note"), str) else "", 300),
            "recommendation": _plain(f.recommendation, 600) or None,
            "found_at": _iso(f.created_at), "seen_at": _iso(f.updated_at), "model": f.model,
        })

    if not has_result:
        fstate = "unavailable"
    elif withheld_all:
        fstate = "withheld"
    else:
        fstate = "ok" if shown else "empty"

    gaps: list[dict[str, Any]] = []
    if has_result and state.get("completed_status") == "partial":
        gaps.append({"code": "partial_run", "failed": state.get("completed_failed_batches"),
                     "total": state.get("completed_batches")})
    if cover.get("capped"):
        from app.services.ai_audit import MAX_SAMPLE
        gaps.append({"code": "sample_cap", "n": MAX_SAMPLE})
    not_included = await _not_included(session, vis["subnet"], scope)
    if not_included:
        gaps.append({"code": "not_included", "n": not_included})

    return {
        "key": "ai_audit", "data_at": now.isoformat(), "page": page, "pages": pages, "per_page": PER_PAGE,
        "total": len(shown), "metrics": metrics,
        "run": {
            "status": status, "running_since": _iso(running_since),
            "error_code": state.get("last_error_code") if status == "failed" else None,
            "last_attempt_at": state.get("finished_at"),
            "completed_at": _iso(completed_at), "completed_status": state.get("completed_status"),
            "snapshot_at": _iso(snapshot_at), "freshness": freshness, "scope": cover, "schedule": schedule,
        },
        "findings": {"state": fstate, "items": items},
        "withheld": ({"count": hidden if full else None} if hidden else None),
        "dismissed": dismissed, "gaps": gaps,
    }


async def _not_included(session: AsyncSession, vis_sub: set[str] | None, scope: set[Any] | None) -> int:
    """觀看者看得到（且在顯示範圍內）卻沒有勾「納入 AI 巡檢」的子網路數：那些網段模型根本沒看過。"""
    import uuid as _uuid
    q = select(func.count()).select_from(Subnet).where(Subnet.ai_audit_enabled.is_(False),
                                                       Subnet.archived_at.is_(None))
    if vis_sub is not None:
        if not vis_sub:
            return 0
        q = q.where(in_values(Subnet.id, [_uuid.UUID(x) for x in vis_sub]))
    if scope is not None:
        if not scope:
            return 0
        q = q.where(in_values(Subnet.id, list(scope)))
    return int(await session.scalar(q) or 0)
