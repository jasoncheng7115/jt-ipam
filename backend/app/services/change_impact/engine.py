"""確定性分析引擎：情境 → 收集證據 → 規則判定 → 去重、排序 → 一次寫入。

同樣的快照、情境、引擎與規則版本 → 同樣的發現、同樣的順序（規格 §5.6、T27）。AI 不參與這裡的任何判定。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.change_impact import adapters_ipam as ai
from app.services.change_impact import adapters_m2 as m2
from app.services.change_impact import adapters_net as an
from app.services.change_impact.access import viewer
from app.services.change_impact.context import Ctx, LimitExceeded
from app.services.change_impact.model import (
    Evidence,
    Finding,
    Gap,
    stable_hash,
)
from app.services.change_impact.scenario import M2_SCENARIOS, Scenario, build
from app.services.change_impact.sources import (
    ADMIN_CATEGORIES,
    GLOBAL_CATEGORIES,
    collect_sources,
    watermarks,
)

# 收集的順序固定（結果排序另外以 sort_key 決定，這裡的順序只影響取消檢查點）
ADAPTERS: tuple[tuple[str, Callable[[Ctx], Awaitable[None]]], ...] = (
    ("ipam", ai.new_ip),
    ("activity", ai.new_ip_activity),
    ("activity", ai.root_activity),
    ("config", ai.config_refs),
    ("dns", an.dns),
    ("dhcp", an.dhcp),
    ("dhcp", an.dhcp_observed),
    ("firewall", an.firewall),
    ("nat", an.nat),
    ("monitoring", an.monitoring),
    ("virt", an.virt),
    ("virt", an.kubernetes),
    ("cert", an.certs),
    ("physical", ai.device_relations),
    ("service", m2.service_refs),
    ("text", ai.text_hints),
)

# M2 情境（交換器維護、節點停機）不找舊位址的引用：看的是誰跟著停、服務還剩不剩所需的依賴
M2_ADAPTERS: tuple[tuple[str, Callable[[Ctx], Awaitable[None]]], ...] = (
    ("network", m2.network_dependents),
    ("workload", m2.workloads),
    ("service", m2.services),
)

# 來源狀態造成的缺口：讓結果不能算「完整」
_PARTIAL_REASONS = frozenset({"permission_limited", "source_stale", "source_never_synced", "source_failed",
                              "source_partial", "fw_object_cycle", "fw_depth_exceeded", "analysis_truncated"})
_OBSERVED_CATEGORIES = ("dns", "dhcp", "firewall", "nat", "librenms", "virt", "monitoring")


@dataclass
class Result:
    scenario: Scenario
    evidence: list[Evidence]
    findings: list[Finding]
    gaps: list[Gap]
    manifest: dict[str, Any]
    watermarks: dict[str, Any]
    completeness: str
    decision: str
    snapshot_hash: str
    scenario_hash: str
    scope_hash: str
    truncated: dict[str, Any] | None = None
    counts: dict[str, Any] = field(default_factory=dict)


async def analyze(session: AsyncSession, *, user: Any, scenario_type: str, target_type: str, target_id: Any,
                  parameters: dict[str, Any], cfg: dict[str, Any],
                  checkpoint: Callable[[str], Awaitable[None]] | None = None,
                  now: datetime | None = None) -> Result:
    now = now or datetime.now(UTC)
    # 分析開始時重新驗證：排隊期間權限被拿掉，就不可以用舊授權分析（規格 §11.1、T19）
    sc = await build(session, user, scenario_type=scenario_type, target_type=target_type, target_id=target_id,
                     parameters=parameters, need="write")
    v = await viewer(session, user)
    sources = await collect_sources(session, now=now, default_stale_hours=int(cfg.get("default_stale_hours", 24)))
    ctx = Ctx(session=session, user=user, scenario=sc, now=now, sources=sources, is_admin=v.is_admin,
              global_read=v.global_read, vis=v.vis, limits=cfg.get("limits", {}), checkpoint=checkpoint)
    await ai.compute_overlap(ctx)
    truncated: dict[str, Any] | None = None
    try:
        for stage, fn in (M2_ADAPTERS if sc.scenario_type in M2_SCENARIOS else ADAPTERS):
            await ctx.check(stage)
            await fn(ctx)
    except LimitExceeded as exc:
        truncated = {"what": exc.what, "limit": exc.limit, "stage": stage}
        ctx.gap("engine", "analysis_truncated", what=exc.what, limit=exc.limit, stage=stage)
    _source_gaps(ctx)

    findings = _dedupe(ctx.findings)
    findings.sort(key=lambda f: f.sort_key())
    # 只保留被發現引用的證據（其餘是過程中查到、但沒有形成任何發現的）
    used = {k for f in findings for k in f.evidence_keys}
    evidence = sorted((e for e in ctx.evidence.values() if e.key in used), key=lambda e: e.key)
    gaps = sorted(ctx.gaps.values(), key=lambda g: g.key())

    completeness = "partial" if truncated or any(g.reason_code in _PARTIAL_REASONS for g in gaps) else "complete"
    if any(f.rule.disposition == "blocker" for f in findings):
        decision = "blocked"
    elif completeness == "partial" or any(f.rule.disposition == "review" for f in findings):
        decision = "needs_review"
    else:
        decision = "no_known_blocker"
    counts = {
        "findings": len(findings),
        "blockers": sum(1 for f in findings if f.rule.disposition == "blocker"),
        "review": sum(1 for f in findings if f.rule.disposition == "review"),
        "informational": sum(1 for f in findings if f.rule.disposition == "informational"),
        "change_required": sum(1 for f in findings if f.rule.impact == "change_required"),
        "potential_disruption": sum(1 for f in findings if f.rule.impact == "potential_disruption"),
        "evidence": len(evidence), "gaps": len(gaps), "roots": len(sc.roots),
    }
    wm = watermarks(sources)
    return Result(
        scenario=sc, evidence=evidence, findings=findings, gaps=gaps,
        manifest={"sources": [s.manifest() for s in sources if _visible_source(ctx, s.categories)],
                  "permission_limited": sorted(ctx.skipped), "roots": [r.ip_text for r in sc.roots],
                  "new_ip": sc.new_ip, "target_subnet": sc.target_subnet_cidr, "cross_subnet": sc.cross_subnet,
                  "observed_from": _iso(min((e.observed_at for e in evidence if e.observed_at), default=None)),
                  "observed_to": _iso(max((e.observed_at for e in evidence if e.observed_at), default=None))},
        watermarks=wm, completeness=completeness, decision=decision,
        # 只看證據與缺口，不看各來源的同步時間（每幾分鐘就變）：核准之後證據變了才算過期（規格 §9、T18）
        snapshot_hash=stable_hash({"e": [(e.key, e.payload_hash()) for e in evidence],
                                   "g": [g.key() for g in gaps]}),
        scenario_hash=stable_hash(sc.payload()), scope_hash=v.scope_hash(), truncated=truncated, counts=counts)


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def _visible_source(ctx: Ctx, categories: tuple[str, ...]) -> bool:
    for c in categories:
        if c in ADMIN_CATEGORIES and not ctx.is_admin:
            return False
        if c in GLOBAL_CATEGORIES and not ctx.global_read:
            return False
    return True


def _source_gaps(ctx: Ctx) -> None:
    """每類觀測來源的狀態：沒設定、過期、從沒同步成功、失敗、部分成功（規格 §4.2、T14、T26）。"""
    seen: set[str] = set()
    for cat in _OBSERVED_CATEGORIES:
        if (cat in GLOBAL_CATEGORIES and not ctx.global_read) or (cat in ADMIN_CATEGORIES and not ctx.is_admin):
            if cat not in ctx.skipped:
                ctx.skipped.add(cat)
                ctx.gap(cat, "permission_limited", affected=cat)
            continue
        srcs = ctx.sources_of(cat)
        if not srcs:
            ctx.gap(cat, "not_configured", affected=cat)
            continue
        for s in srcs:
            # 一個整合可能同時餵好幾類（防火牆、DHCP、NAT）：同一個來源的時效只列一次，記在它的第一個類別
            if s.freshness in ("stale", "never_synced", "failed", "partial") and s.ref not in seen:
                seen.add(s.ref)
                ctx.gap(cat, f"source_{s.freshness}", scope=s.ref, source=s.name, kind=s.kind,
                        last_sync_at=s.last_sync_at.isoformat() if s.last_sync_at else None)


def _dedupe(findings: list[Finding]) -> list[Finding]:
    by_fp: dict[str, Finding] = {}
    for f in findings:
        fp = f.fingerprint()
        if fp in by_fp:
            keep = by_fp[fp]
            for k in f.evidence_keys:
                if k not in keep.evidence_keys:
                    keep.evidence_keys.append(k)
        else:
            by_fp[fp] = f
    for f in by_fp.values():
        f.evidence_keys.sort()
    return list(by_fp.values())


async def persist(session: AsyncSession, run_id: Any, res: Result) -> dict[str, Any]:
    """一次寫入同一個交易。重試前先清掉這個 run 的中間列（以 run_id 為冪等鍵，規格 §8.4）。"""
    from app.models.change_impact import ImpactEvidence, ImpactFinding, ImpactGap, ImpactRelation
    for model in (ImpactFinding, ImpactEvidence, ImpactGap, ImpactRelation):
        await session.execute(delete(model).where(model.run_id == run_id))
    for e in res.evidence:
        vt, vid = e.visibility
        session.add(ImpactEvidence(
            run_id=run_id, key=e.key, source_type=e.source_type, integration_ref=e.integration_ref,
            source_object_type=e.object_type, source_object_id=e.object_id, source_object_key=e.object_key,
            label=e.label[:300], observed_at=e.observed_at, collected_at=e.collected_at,
            sanitized_payload=e.payload, payload_hash=e.payload_hash(), freshness=e.freshness,
            visibility_type=vt, visibility_id=vid))
    root_refs = [f"ip_address:{r.ip_id}" for r in res.scenario.roots] or [f"device:{res.scenario.target_id}"]
    for f in res.findings:
        r = f.rule
        vt, vid = f.visibility
        session.add(ImpactFinding(
            run_id=run_id, rule_id=f.rule_id, rule_version=r.version, subject_type=f.subject_type,
            subject_id=f.subject_id, subject_key=f.subject_key, subject_label=f.subject_label[:300],
            category=r.category, match_kind=f.match_kind, impact=r.impact, severity=r.severity,
            disposition=r.disposition, evidence_strength=f.strength or r.strength, reason_code=r.reason,
            params=_jsonable(f.params), evidence_keys=f.evidence_keys, path_refs=f.path,
            suggested_action=f.suggested_action or {"kind": "manual_review", "reason_code": f"REVIEW_{r.reason}"},
            fingerprint=f.fingerprint(), sort_key=f.sort_key()[:120], visibility_type=vt, visibility_id=vid))
        subj = f"{f.subject_type}:{f.subject_id or f.subject_key}"
        if f.links:
            # M2：明確知道連到誰（接在哪台停機的交換器、在哪個節點上、服務依賴哪些受影響的物件）
            for to, rel in f.links:
                session.add(ImpactRelation(run_id=run_id, from_ref=subj[:160], to_ref=to[:160], relation_type=rel,
                                           namespace=None, evidence_keys=f.evidence_keys,
                                           strength=f.strength or r.strength))
            continue
        addr = f.params.get("address")
        to = next((ref for ref, root in zip(root_refs, res.scenario.roots, strict=False)
                   if root.ip_text == addr), root_refs[0])
        session.add(ImpactRelation(run_id=run_id, from_ref=subj[:160], to_ref=to[:160],
                                   relation_type="references" if r.category not in ("virt", "physical", "activity")
                                   else "observed_on", namespace=None, evidence_keys=f.evidence_keys,
                                   strength=f.strength or r.strength))
    for g in res.gaps:
        session.add(ImpactGap(run_id=run_id, category=g.category, source_scope=g.source_scope,
                              reason_code=g.reason_code, params=_jsonable(g.params),
                              affected_analysis=g.affected_analysis, remediation_hint=g.remediation_hint,
                              sort_key=g.key()[:120]))
    return res.counts


def _jsonable(d: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        elif v is None or isinstance(v, (str, int, float, bool, list, dict)):
            out[k] = v
        else:
            out[k] = str(v)
    return out
