"""IP 變更評估 API（docs/SPEC_CHANGE_IMPACT_zh-TW.md §8；M0 §13.3 的落地）。

權限（M0 §13.7，使用者 2026-10-07 開工時採保守版本）：
- 看計畫與結果：對根目標（IP／裝置）有讀取權
- 建立、修改、分析：對根目標有寫入權（會規劃改這個 IP 的人，本來就要能改它）
- 覆核：寫入權且不是建立者（設定可放寬），或管理員；阻擋項目不可核准
- 結果每次讀取都依目前權限重新過濾；功能預設關閉，在網頁開

專案慣例：錯誤是 {code, params, message}、清單是 page/page_size、樂觀鎖衝突回 409（不用 412）。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from pydantic import Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin
from app.core.audit import append_audit
from app.core.db import get_session
from app.core.ui_error import ui_detail
from app.models.change_impact import (
    JOB_ACTIVE,
    ChangePlan,
    ChangePlanRevision,
    ChangeTask,
    ImpactAIArtifact,
    ImpactEvidence,
    ImpactFinding,
    ImpactRelation,
    ImpactReview,
    ImpactRun,
)
from app.models.user import User
from app.schemas.base import StrictModel
from app.services.change_impact import ai as impact_ai
from app.services.change_impact import jobs, plans, review_policy, reviewers
from app.services.change_impact.access import (
    Viewer,
    review_items_hidden,
    same_scope,
    viewer,
    visible_counts,
    visible_manifest,
    visible_task_findings,
)
from app.services.change_impact.config import ai_available, get_config, set_config
from app.services.change_impact.model import stable_hash
from app.services.change_impact.scenario import (
    ScenarioError,
    candidates_for_address,
    require_target_access,
)
from app.services.change_impact.scenario import target_subnets as list_target_subnets

router = APIRouter(tags=["change-impact"])
Session = Annotated[AsyncSession, Depends(get_session)]


def _err(exc: ScenarioError) -> HTTPException:
    params = {k: v for k, v in exc.params.items() if not isinstance(v, (list, dict))}
    detail = ui_detail(exc.code, str(exc), **params)
    if "candidates" in exc.params:
        detail["candidates"] = exc.params["candidates"]
    return HTTPException(status_code=exc.status, detail=detail)


async def _enabled(session: AsyncSession) -> dict[str, Any]:
    cfg = await get_config(session)
    if not cfg["enabled"]:
        raise HTTPException(403, detail=ui_detail("impact_feature_disabled", "IP change assessment is turned off"))
    return cfg


async def _audit(session: AsyncSession, request: Request, user: User, action: str, plan_id: uuid.UUID | None,
                 diff: dict[str, Any]) -> None:
    await append_audit(session, actor_user_id=str(user.id),
                       actor_ip=request.client.host if request.client else None,
                       actor_user_agent=request.headers.get("user-agent"), object_type="change_plan",
                       object_id=str(plan_id) if plan_id else None, action=action, diff=diff,
                       request_id=getattr(request.state, "request_id", None))


async def _plan(session: AsyncSession, user: User, plan_id: uuid.UUID, need: str = "read") -> ChangePlan:
    """看不到就當作不存在。根目標已經刪掉的計畫只給建立者與管理員。"""
    plan = await session.get(ChangePlan, plan_id)
    if plan is None:
        raise HTTPException(404, detail="Plan not found")
    if user.is_admin:
        return plan
    try:
        await require_target_access(session, user, plan.target_type, plan.target_id, need=need)
    except ScenarioError as exc:
        # 看不到也回 404，所以要另外確認「目標真的不在了」才放行建立者；權限被收回時跟別人一樣
        if exc.status == 404 and plan.created_by == user.id and need == "read" and not await _target_exists(session, plan):
            return plan
        if exc.status == 404:
            raise HTTPException(404, detail="Plan not found") from exc
        raise _err(exc) from exc
    return plan


async def _target_exists(session: AsyncSession, plan: ChangePlan) -> bool:
    from app.models.address import IPAddress
    from app.models.device import Device
    model = IPAddress if plan.target_type == "ip_address" else Device
    return (await session.execute(select(model.id).where(model.id == plan.target_id))).first() is not None


async def _run(session: AsyncSession, user: User, run_id: uuid.UUID) -> tuple[ImpactRun, ChangePlan]:
    run = await session.get(ImpactRun, run_id)
    if run is None:
        raise HTTPException(404, detail="Run not found")
    plan = await _plan(session, user, run.plan_id)
    return run, plan


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def _plan_out(p: ChangePlan) -> dict[str, Any]:
    return {"id": str(p.id), "title": p.title, "scenario_type": p.scenario_type, "target_type": p.target_type,
            "target_id": str(p.target_id), "target_label": p.target_label, "parameters": p.parameters,
            "planned_start": _iso(p.planned_start), "planned_end": _iso(p.planned_end),
            "created_by": str(p.created_by) if p.created_by else None,
            "owner_user_id": str(p.owner_user_id) if p.owner_user_id else None,
            "reviewer_user_id": str(p.reviewer_user_id) if p.reviewer_user_id else None,
            "revision": p.revision, "lifecycle": p.lifecycle,
            "latest_run_id": str(p.latest_run_id) if p.latest_run_id else None,
            "archived_at": _iso(p.archived_at), "created_at": _iso(p.created_at), "updated_at": _iso(p.updated_at)}


def _run_out(r: ImpactRun, v: Viewer, counts: dict[str, Any] | None = None) -> dict[str, Any]:
    """數量與資料來源都依讀者：範圍跟分析當時一樣才直接用存下來的數量，不一樣就用呼叫端重算的
    （清單類端點不重算，回 None，畫面顯示「—」）。不可以讓看不到的發現從數字透露出來。"""
    out = {"id": str(r.id), "plan_id": str(r.plan_id), "plan_revision": r.plan_revision, "job_status": r.job_status,
           "stage": r.stage, "decision_status": r.decision_status, "completeness": r.completeness,
           "scope_manifest": visible_manifest(r.scope_manifest, v),
           "counts": r.counts if same_scope(r, v) else counts, "snapshot_hash": r.snapshot_hash,
           "scenario_hash": r.scenario_hash, "engine_version": r.engine_version, "rules_version": r.rules_version,
           "attempt": r.attempt, "started_at": _iso(r.started_at), "completed_at": _iso(r.completed_at),
           "expires_at": _iso(r.expires_at), "truncated": r.truncated, "truncation": r.truncation,
           "error_code": r.error_code, "created_at": _iso(r.created_at),
           "cancel_requested": r.cancel_requested_at is not None,
           "requested_by": str(r.requested_by) if r.requested_by else None}
    out["permission_scope_changed"] = not same_scope(r, v)
    return out


# ─────────────────── 設定 ───────────────────

class SettingsIn(StrictModel):
    enabled: bool | None = None
    ai_enabled: bool | None = None
    allow_self_review: bool | None = None
    reviewer_user_ids: list[str] | None = Field(None, max_length=200)
    reviewer_group_ids: list[str] | None = Field(None, max_length=200)
    run_valid_hours: int | None = None
    default_stale_hours: int | None = None
    retention_days: int | None = None
    failed_retention_days: int | None = None
    limits: dict[str, int] | None = None


@router.get("/change-impact/settings")
async def get_settings(user: CurrentUser, session: Session) -> dict[str, Any]:
    cfg = await get_config(session)
    out: dict[str, Any] = {"enabled": cfg["enabled"], "ai_available": cfg["enabled"] and await ai_available(session, cfg)}
    if user.is_admin:
        out["config"] = cfg
    return out


@router.put("/change-impact/settings", dependencies=[Depends(require_admin)])
async def put_settings(body: SettingsIn, user: CurrentUser, request: Request, session: Session) -> dict[str, Any]:
    patch = body.model_dump(exclude_none=True)
    before = await get_config(session)
    cfg = await set_config(session, patch, updated_by=user.id)
    legacy = {"reviewer_user_ids", "reviewer_group_ids", "allow_self_review"} & set(patch)
    if legacy:
        # 舊 API 的審核人欄位：照舊可以用，寫進「申請審核設定」的審核關卡（那裡才是唯一的設定）
        pol = await review_policy.get_policy(session, cfg)
        if "allow_self_review" in patch:
            pol["allow_self_approve"] = bool(cfg.get("allow_self_review"))
        if {"reviewer_user_ids", "reviewer_group_ids"} & set(patch) and pol["approver_mode"] in ("editors", "designated"):
            legacy_pol = review_policy.from_legacy(cfg)
            pol.update({k: legacy_pol[k] for k in ("approver_mode", "designated_user_ids", "designated_group_ids")})
        await review_policy.set_policy(session, pol, updated_by=user.id)
    await _audit(session, request, user, "change_impact_settings", None,
                 {"before": {k: before.get(k) for k in patch}, "after": {k: cfg.get(k) for k in patch}})
    await session.commit()
    return {"enabled": cfg["enabled"], "ai_available": cfg["enabled"] and await ai_available(session, cfg),
            "config": cfg}


# ─────────────────── 審核關卡（「申請審核設定」的 IP 變更評估審核） ───────────────────

class ReviewStepIn(StrictModel):
    name: str = Field("", max_length=64)
    user_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    group_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)


class ReviewPolicyIn(StrictModel):
    approver_mode: Literal["editors", "admin", "designated", "parallel", "stages"]
    designated_user_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    designated_group_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    allow_self_approve: bool = False
    stages: list[ReviewStepIn] = Field(default_factory=list, max_length=20)


def _policy_out(pol: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in pol.items() if k != "saved"}


@router.get("/change-impact/review-policy", dependencies=[Depends(require_admin)])
async def get_review_policy(user: CurrentUser, session: Session) -> dict[str, Any]:
    return _policy_out(await review_policy.get_policy(session))


@router.put("/change-impact/review-policy", dependencies=[Depends(require_admin)])
async def put_review_policy(body: ReviewPolicyIn, user: CurrentUser, request: Request,
                            session: Session) -> dict[str, Any]:
    pol = review_policy.normalize(body.model_dump(mode="json"))
    code = review_policy.validate(pol)
    if code:
        raise HTTPException(422, detail=ui_detail(code, "Invalid review policy"))
    before = await review_policy.get_policy(session)
    pol = await review_policy.set_policy(session, pol, updated_by=user.id)
    await append_audit(session, actor_user_id=str(user.id),
                       actor_ip=request.client.host if request.client else None,
                       actor_user_agent=request.headers.get("user-agent"), object_type="system_setting",
                       object_id=None, action="update",
                       diff={"change_impact_review_policy": {"before": _policy_out(before), "after": _policy_out(pol)}},
                       request_id=getattr(request.state, "request_id", None))
    await session.commit()
    return _policy_out(pol)


# ─────────────────── 候選目標（同一個位址好幾筆） ───────────────────

@router.get("/change-impact/candidates")
async def candidates(ip: str, user: CurrentUser, session: Session,
                     subnet_id: uuid.UUID | None = Query(None)) -> dict[str, Any]:
    await _enabled(session)
    try:
        return {"items": await candidates_for_address(session, user, ip, subnet_id=subnet_id)}
    except ScenarioError as exc:
        raise _err(exc) from exc


@router.get("/change-impact/target-subnets")
async def target_subnets(user: CurrentUser, session: Session, q: str | None = Query(None, max_length=100),
                         section_id: uuid.UUID | None = Query(None),
                         customer_id: uuid.UUID | None = Query(None)) -> dict[str, Any]:
    """建立視窗的子網路下拉：只列可以修改的子網路，單位／區段選項也只從這些子網路推出來。"""
    await _enabled(session)
    return await list_target_subnets(session, user, q=q, section_id=section_id, customer_id=customer_id)


# ─────────────────── 計畫 ───────────────────

class PlanIn(StrictModel):
    title: str = Field(default="", max_length=200)
    scenario_type: str
    target_type: str
    target_id: uuid.UUID
    parameters: dict[str, Any] = Field(default_factory=dict)
    planned_start: datetime | None = None
    planned_end: datetime | None = None


class PlanPatch(StrictModel):
    title: str | None = Field(default=None, max_length=200)
    parameters: dict[str, Any] | None = None
    planned_start: datetime | None = None
    planned_end: datetime | None = None
    expected_revision: int | None = None


async def _awaiting_review_filter(session: AsyncSession, user: User, cfg: dict[str, Any], stmt: Any) -> Any:
    """「待我覆核」：送審中、不是自己建的（除非允許自己覆核）、而且依審核關卡輪得到我。

    單一關卡的模式全部推進 SQL；多關卡要看每份計畫走到第幾關，送審中的計畫不多，逐筆判斷後以 id 限定（分頁照樣對）。
    """
    from app.core.sqlin import in_values
    from app.services.permission import visible_ids
    stmt = stmt.where(ChangePlan.lifecycle == "in_review")
    if user.is_admin:
        return stmt
    pol = await reviewers.policy(session, cfg)
    if not pol["allow_self_approve"]:
        stmt = stmt.where(or_(ChangePlan.created_by.is_(None), ChangePlan.created_by != user.id))
    mode = pol["approver_mode"]
    if mode == "admin":
        return stmt.where(ChangePlan.id.is_(None))
    if mode == "designated":
        if not await reviewers.is_designated(session, user, cfg):
            return stmt.where(ChangePlan.id.is_(None))
        return stmt
    if mode in ("parallel", "stages"):
        ids = []
        for p in (await session.execute(stmt.limit(2000))).scalars().all():
            if (await reviewers.can_review(session, user, p, cfg))[0]:
                ids.append(p.id)
        return stmt.where(in_values(ChangePlan.id, ids)) if ids else stmt.where(ChangePlan.id.is_(None))
    w_ip = await visible_ids(session, user=user, object_type="ip", required="write")
    w_dev = await visible_ids(session, user=user, object_type="device", required="write")
    conds = []
    for ttype, wids in (("ip_address", w_ip), ("device", w_dev)):
        if wids is None:
            conds.append(ChangePlan.target_type == ttype)
        elif wids:
            conds.append((ChangePlan.target_type == ttype) & in_values(ChangePlan.target_id, list(wids)))
    return stmt.where(or_(*conds)) if conds else stmt.where(ChangePlan.id.is_(None))


@router.get("/change-plans")
async def list_plans(user: CurrentUser, session: Session, scenario_type: str | None = None,
                     lifecycle: str | None = None, q: str | None = None, include_archived: bool = False,
                     target_type: str | None = None, target_id: uuid.UUID | None = None,
                     awaiting_my_review: bool = False,
                     page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
                     sort: Literal["title", "scenario_type", "lifecycle", "planned_start", "created_at"] | None = None,
                     order: Literal["asc", "desc"] = "asc") -> dict[str, Any]:
    cfg = await _enabled(session)
    stmt = select(ChangePlan)
    if awaiting_my_review:
        stmt = await _awaiting_review_filter(session, user, cfg, stmt)
    if not user.is_admin:
        from app.core.sqlin import in_values
        from app.services.permission import visible_ids
        vis_ip = await visible_ids(session, user=user, object_type="ip")
        vis_dev = await visible_ids(session, user=user, object_type="device")
        conds = [ChangePlan.created_by == user.id]
        conds.append(ChangePlan.target_type == "ip_address" if vis_ip is None else
                     (ChangePlan.target_type == "ip_address") & in_values(ChangePlan.target_id, list(vis_ip)))
        conds.append(ChangePlan.target_type == "device" if vis_dev is None else
                     (ChangePlan.target_type == "device") & in_values(ChangePlan.target_id, list(vis_dev)))
        stmt = stmt.where(or_(*conds))
    if scenario_type:
        stmt = stmt.where(ChangePlan.scenario_type == scenario_type)
    if lifecycle:
        stmt = stmt.where(ChangePlan.lifecycle == lifecycle)
    if target_type and target_id:
        stmt = stmt.where(ChangePlan.target_type == target_type, ChangePlan.target_id == target_id)
    if not include_archived:
        stmt = stmt.where(ChangePlan.archived_at.is_(None))
    if q:
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        stmt = stmt.where(or_(ChangePlan.title.ilike(like, escape="\\"), ChangePlan.target_label.ilike(like, escape="\\")))
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    # 點標題排序（後端分頁：分頁前排）；沒指定照舊，最新建立的在前。文字不分大小寫、空值排最後
    if sort:
        col = {"title": func.lower(ChangePlan.title), "scenario_type": ChangePlan.scenario_type,
               "lifecycle": ChangePlan.lifecycle, "planned_start": ChangePlan.planned_start,
               "created_at": ChangePlan.created_at}[sort]
        ordering = [(col.desc() if order == "desc" else col.asc()).nulls_last(), ChangePlan.created_at.desc(), ChangePlan.id]
    else:
        ordering = [ChangePlan.created_at.desc(), ChangePlan.id]
    rows = (await session.execute(stmt.order_by(*ordering)
                                  .offset((page - 1) * page_size).limit(page_size))).scalars().all()
    runs = {}
    ids = [r.latest_run_id for r in rows if r.latest_run_id]
    if ids:
        runs = {r.id: r for r in (await session.execute(select(ImpactRun).where(ImpactRun.id.in_(ids)))).scalars()}  # bounded: one page
    items = []
    v = await viewer(session, user)
    for p in rows:
        o = _plan_out(p)
        lr = runs.get(p.latest_run_id) if p.latest_run_id else None
        o["latest_run"] = {"job_status": lr.job_status, "decision_status": lr.decision_status,
                           "completeness": lr.completeness, "counts": lr.counts if same_scope(lr, v) else None,
                           "completed_at": _iso(lr.completed_at)} if lr else None
        items.append(o)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.post("/change-plans", status_code=status.HTTP_201_CREATED)
async def create_plan(body: PlanIn, user: CurrentUser, request: Request, session: Session, response: Response,
                      idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None) -> dict[str, Any]:
    await _enabled(session)
    rh = stable_hash(body.model_dump(mode="json"))
    try:
        plan, created = await plans.create_plan(
            session, user, title=body.title, scenario_type=body.scenario_type, target_type=body.target_type,
            target_id=body.target_id, parameters=body.parameters, planned_start=body.planned_start,
            planned_end=body.planned_end, idempotency_key=(idempotency_key or None) and idempotency_key[:128],
            request_hash=rh)
    except ScenarioError as exc:
        raise _err(exc) from exc
    if created:
        await _audit(session, request, user, "change_plan_create", plan.id,
                     {"title": plan.title, "scenario": plan.scenario_type, "target": plan.target_label,
                      "parameters": plan.parameters})
        await session.commit()
        await session.refresh(plan)
    else:
        response.status_code = status.HTTP_200_OK
    return _plan_out(plan)


@router.get("/change-plans/{plan_id}")
async def get_plan(plan_id: uuid.UUID, user: CurrentUser, session: Session) -> dict[str, Any]:
    await _enabled(session)
    plan = await _plan(session, user, plan_id)
    out = _plan_out(plan)
    cur = await plans.current_run(session, plan)
    out["current_run_id"] = str(cur.id) if cur else None
    out["current_run_expired"] = plans.run_expired(cur) if cur else None
    out["can_edit"] = await plans.can_edit(session, user, plan)
    cfg = await get_config(session)
    out["can_review"] = plan.archived_at is None and (await reviewers.can_review(session, user, plan, cfg))[0]
    pool = await reviewers.pool(session, plan, cfg)
    out["reviewers"] = [{"id": str(u.id), "name": u.display_name or u.username} for u in pool[:20]]
    out["reviewers_total"] = len(pool)
    pol = await reviewers.policy(session, cfg)
    out["review_mode"] = pol["approver_mode"]
    out["reviewers_designated"] = pol["approver_mode"] in ("designated", "parallel", "stages")
    out["review_steps"] = await reviewers.steps_progress(session, plan, cfg)
    also = (plan.parameters or {}).get("also_down") or []
    if also:
        from app.models.device import Device
        names = {str(d.id): d.name for d in (await session.execute(select(Device).where(
            Device.id.in_([uuid.UUID(str(x)) for x in also])))).scalars()}  # bounded: at most 20 targets
        out["also_down_labels"] = [names.get(str(x), str(x)[:8]) for x in also]
    review = await plans.latest_review(session, plan)
    reviewer = await session.get(User, review.reviewer_id) if review and review.reviewer_id else None
    out["latest_review"] = _review_out(review, {reviewer.id: reviewer.display_name or reviewer.username}
                                       if reviewer else None) if review else None
    return out


@router.patch("/change-plans/{plan_id}")
async def patch_plan(plan_id: uuid.UUID, body: PlanPatch, user: CurrentUser, request: Request, session: Session,
                     if_match: Annotated[str | None, Header(alias="If-Match")] = None) -> dict[str, Any]:
    await _enabled(session)
    plan = await _plan(session, user, plan_id)
    expected = body.expected_revision
    if expected is None and if_match:
        try:
            expected = int(if_match.strip('"W/ '))
        except ValueError:
            expected = None
    patch = body.model_dump(exclude_unset=True, exclude={"expected_revision"})
    before = {"revision": plan.revision, "parameters": plan.parameters, "title": plan.title}
    try:
        await plans.update_plan(session, user, plan, patch, expected)
    except ScenarioError as exc:
        raise _err(exc) from exc
    await _audit(session, request, user, "change_plan_update", plan.id,
                 {"before": before, "after": {"revision": plan.revision, "parameters": plan.parameters,
                                              "title": plan.title}})
    await session.commit()
    await session.refresh(plan)       # updated_at 由資料庫更新，不重新讀就會在序列化時觸發延遲載入
    return _plan_out(plan)


@router.delete("/change-plans/{plan_id}")
async def archive_plan(plan_id: uuid.UUID, user: CurrentUser, request: Request, session: Session) -> dict[str, Any]:
    """一般使用者的刪除＝封存（規格 §7.3）；證據依保存期限清除。"""
    await _enabled(session)
    plan = await _plan(session, user, plan_id)
    if not await plans.can_edit(session, user, plan):
        raise HTTPException(403, detail=ui_detail("impact_plan_forbidden", "Not allowed"))
    from datetime import UTC
    plan.archived_at = datetime.now(UTC)
    # 指向這個計畫的通知（送審、覆核結果）一起清掉：計畫已刪除，留著只會讓人點進去才發現不能做事（2026-10-25）
    from sqlalchemy import delete as _delete

    from app.models.notification import Notification
    await session.execute(_delete(Notification).where(Notification.object_type == "change_plan",
                                                      Notification.object_id == plan.id))
    await _audit(session, request, user, "change_plan_archive", plan.id, {"title": plan.title})
    await session.commit()
    return {"ok": True}


@router.get("/change-plans/{plan_id}/revisions")
async def list_revisions(plan_id: uuid.UUID, user: CurrentUser, session: Session) -> dict[str, Any]:
    await _enabled(session)
    plan = await _plan(session, user, plan_id)
    rows = (await session.execute(select(ChangePlanRevision).where(ChangePlanRevision.plan_id == plan.id)
                                  .order_by(ChangePlanRevision.revision.desc()))).scalars().all()
    return {"items": [{"revision": r.revision, "payload": r.payload, "created_at": _iso(r.created_at),
                       "created_by": str(r.created_by) if r.created_by else None} for r in rows]}


class TransitionIn(StrictModel):
    action: str


@router.post("/change-plans/{plan_id}/transitions")
async def transition(plan_id: uuid.UUID, body: TransitionIn, user: CurrentUser, request: Request,
                     session: Session) -> dict[str, Any]:
    cfg = await _enabled(session)
    plan = await _plan(session, user, plan_id)
    before = plan.lifecycle
    try:
        await plans.transition(session, user, plan, body.action)
    except plans.PlanError as exc:
        if plan.lifecycle != before:      # 開始維護前發現證據變了：計畫退回草稿要寫進去
            await _audit(session, request, user, "change_plan_transition", plan.id,
                         {"action": body.action, "from": before, "to": plan.lifecycle, "reason": exc.code})
            await session.commit()
        raise _err(exc) from exc
    except ScenarioError as exc:
        raise _err(exc) from exc
    diff: dict[str, Any] = {"action": body.action, "from": before, "to": plan.lifecycle}
    if body.action == "submit":
        diff["notified"] = await reviewers.notify_submitted(session, plan, user, cfg)
    await _audit(session, request, user, "change_plan_transition", plan.id, diff)
    await session.commit()
    await session.refresh(plan)
    return _plan_out(plan)


# ─────────────────── 分析 ───────────────────

@router.post("/change-plans/{plan_id}/runs", status_code=status.HTTP_202_ACCEPTED)
async def start_run(plan_id: uuid.UUID, user: CurrentUser, request: Request, session: Session, response: Response,
                    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None) -> dict[str, Any]:
    await _enabled(session)
    plan = await _plan(session, user, plan_id, need="write")
    try:
        plans._ensure_active(plan)
        run, created = await jobs.create_run(session, plan=plan, user=user,
                                             idempotency_key=(idempotency_key or None) and idempotency_key[:128],
                                             request_hash=stable_hash({"plan": str(plan.id), "rev": plan.revision}))
    except ScenarioError as exc:
        raise _err(exc) from exc
    if not created:
        response.status_code = status.HTTP_200_OK
        return _run_out(run, await viewer(session, user))
    await _audit(session, request, user, "impact_run_start", plan.id,
                 {"run_id": str(run.id), "revision": plan.revision})
    await session.commit()
    await jobs.launch(run.id, label=plan.title, actor_user_id=user.id, plan_id=plan.id)
    await session.refresh(run)
    return _run_out(run, await viewer(session, user))


@router.get("/change-plans/{plan_id}/runs")
async def list_runs(plan_id: uuid.UUID, user: CurrentUser, session: Session) -> dict[str, Any]:
    await _enabled(session)
    plan = await _plan(session, user, plan_id)
    rows = (await session.execute(select(ImpactRun).where(ImpactRun.plan_id == plan.id)
                                  .order_by(ImpactRun.created_at.desc()).limit(100))).scalars().all()
    v = await viewer(session, user)
    return {"items": [_run_out(r, v) for r in rows]}


@router.get("/impact-runs/{run_id}")
async def get_run(run_id: uuid.UUID, user: CurrentUser, session: Session) -> dict[str, Any]:
    await _enabled(session)
    run, _plan_row = await _run(session, user, run_id)
    if run.job_status in JOB_ACTIVE:
        # 輪詢時順便回收沒有心跳的 run（worker 重啟、程序被殺；規格 §8.4）
        relaunch = await jobs.reclaim_stale(session)
        await session.commit()
        if relaunch:
            for rid in relaunch:
                r = await session.get(ImpactRun, rid)
                p = await session.get(ChangePlan, r.plan_id) if r else None
                if r and p:
                    await jobs.launch(rid, label=p.title, actor_user_id=r.requested_by, plan_id=p.id)
        await session.refresh(run)
    v = await viewer(session, user)
    counts = None
    if not same_scope(run, v) and run.job_status in ("completed", "partial"):
        counts = visible_counts(await impact_ai.visible_bundle(session, run, v), run.counts)
    out = _run_out(run, v, counts)
    out["review_items_hidden"] = (run.job_status in ("completed", "partial")
                                  and await review_items_hidden(session, run, v))
    return out


@router.get("/impact-runs/{run_id}/findings")
async def run_findings(run_id: uuid.UUID, user: CurrentUser, session: Session, disposition: str | None = None,
                       category: str | None = None, severity: str | None = None,
                       page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    await _enabled(session)
    run, _p = await _run(session, user, run_id)
    v = await viewer(session, user)
    bundle = await impact_ai.visible_bundle(session, run, v)
    items = [f for f in bundle["findings"]
             if (not disposition or f.disposition == disposition) and (not category or f.category == category)
             and (not severity or f.severity == severity)]
    ev_ids = {e.key: str(e.id) for e in bundle["evidence"]}
    page_items = items[(page - 1) * page_size: page * page_size]
    return {"items": [_finding_out(f, ev_ids) for f in page_items], "total": len(items), "page": page,
            "page_size": page_size, "permission_scope_changed": bool(run.visible_scope_hash and
                                                                      run.visible_scope_hash != v.scope_hash())}


_IMPACT_RANK = {"modeled_disruption": 0, "potential_disruption": 1, "redundancy_unverified": 2, "change_required": 3,
                "unknown": 4, "reference_only": 5}
# 節點類型 → 讀取時的可見性檢查（Viewer.can 的類型）
_REF_VIS = {"device": "device", "ip_address": "ip", "subnet": "subnet", "virtual_machine": "global", "service": "global"}
_REF_LABEL_SQL = {
    "device": "SELECT id, name AS label FROM devices WHERE id = ANY(CAST(:ids AS uuid[]))",
    "ip_address": "SELECT id, host(ip) AS label FROM ip_addresses WHERE id = ANY(CAST(:ids AS uuid[]))",
    "subnet": "SELECT id, cidr::text AS label FROM subnets WHERE id = ANY(CAST(:ids AS uuid[]))",
    "virtual_machine": "SELECT vm.id, c.name || '/' || vm.name AS label FROM virtual_machines vm "
                       "JOIN virt_clusters c ON c.id = vm.cluster_id WHERE vm.id = ANY(CAST(:ids AS uuid[]))",
    "service": "SELECT id, name AS label FROM impact_services WHERE id = ANY(CAST(:ids AS uuid[]))",
}


@router.get("/impact-runs/{run_id}/relations")
async def run_relations(run_id: uuid.UUID, user: CurrentUser, session: Session,
                        limit: int = Query(100, ge=10, le=500)) -> dict[str, Any]:
    """關係圖（規格 §3.4）：有界的子圖，節點是根目標與看得到的發現主體，邊是保存下來的關係。

    依讀者過濾：看不到的發現不出現，邊另一端的物件也要看得到；超過上限只畫前面的（依影響排序），
    完整結果仍在影響清單。AI 不會在圖上新增事實節點或邊。"""
    from sqlalchemy import text as _text
    await _enabled(session)
    run, plan = await _run(session, user, run_id)
    v = await viewer(session, user)
    bundle = await impact_ai.visible_bundle(session, run, v)
    from app.services.change_impact.labels import display_label
    ev_by_key = {e.key: e for e in bundle["evidence"]}
    nodes: dict[str, dict[str, Any]] = {}
    for f in bundle["findings"]:
        ref = f"{f.subject_type}:{f.subject_id or f.subject_key}"
        # 分類（dns、firewall…）：關係圖把同一類、掛在同一個物件下的葉節點收成一個群組
        n = nodes.setdefault(ref, {"id": ref, "type": f.subject_type,
                                   "label": display_label(f.subject_type, f.subject_label, f.params),
                                   "impact": f.impact, "category": f.category, "root": False,
                                   "subject_id": str(f.subject_id) if f.subject_id else None,
                                   "kind": (f.params or {}).get("kind"), "rules": [], "evidence": [],
                                   "blocker": False})
        # 阻擋項目在圖上要認得出來（2026-10-25 實測：跟其他「可能中斷」長得一樣，審核人看圖找不到）
        if f.disposition == "blocker":
            n["blocker"] = True
        if _IMPACT_RANK.get(f.impact, 9) < _IMPACT_RANK.get(n["impact"] or "", 9):
            n["impact"], n["category"] = f.impact, f.category
        # 明細面板：這個物件為什麼出現（規則＋原因參數）與證據來自哪裡、什麼時候看到的
        n["rules"].append({"rule_id": f.rule_id, "reason": f.reason_code, "params": f.params, "impact": f.impact})
        for k in f.evidence_keys:
            e = ev_by_key.get(k)
            if e is not None and len(n["evidence"]) < 5 and all(x["label"] != e.label for x in n["evidence"]):
                n["evidence"].append({"source_type": e.source_type, "object_type": e.source_object_type,
                                      "label": e.label, "freshness": e.freshness,
                                      "observed_at": e.observed_at.isoformat() if e.observed_at else None})
    roots = [f"{'ip_address' if plan.target_type == 'ip_address' else 'device'}:{plan.target_id}"]
    for also in (plan.parameters or {}).get("also_down") or []:
        roots.append(f"device:{also}")
    rels = (await session.execute(select(ImpactRelation).where(ImpactRelation.run_id == run.id))).scalars().all()

    def visible_ref(ref: str) -> bool:
        kind, _, oid = ref.partition(":")
        try:
            return v.can(_REF_VIS.get(kind, "global"), uuid.UUID(oid))
        except ValueError:
            return False

    edges = []
    for r in rels:
        if r.from_ref not in nodes:
            continue
        if r.to_ref not in nodes and r.to_ref not in roots and not visible_ref(r.to_ref):
            continue
        edges.append({"from": r.from_ref, "to": r.to_ref, "relation": r.relation_type, "strength": r.strength})
    # 邊另一端不是發現主體的（根目標、節點、服務依賴的物件）：補上名稱
    missing: dict[str, list[str]] = {}
    for ref in {e["to"] for e in edges} | set(roots):
        if ref not in nodes:
            kind, _, oid = ref.partition(":")
            missing.setdefault(kind, []).append(oid)
    for kind, ids in missing.items():
        labels = {}
        if kind in _REF_LABEL_SQL:
            labels = {str(r.id): r.label for r in (await session.execute(_text(_REF_LABEL_SQL[kind]), {"ids": ids})).all()}
        for oid in ids:
            ref = f"{kind}:{oid}"
            if ref in roots and not visible_ref(ref) and kind != "ip_address":
                continue
            nodes[ref] = {"id": ref, "type": kind, "label": labels.get(oid, oid[:8]), "impact": None, "category": None,
                          "root": False, "subject_id": oid, "kind": None, "rules": [], "evidence": [],
                          "blocker": False}
    for ref in roots:
        if ref in nodes:
            nodes[ref]["root"] = True
    ordered = sorted(nodes.values(), key=lambda n: (not n["root"], _IMPACT_RANK.get(n["impact"] or "", 9), n["label"]))
    kept = {n["id"] for n in ordered[:limit]}
    return {"nodes": [n for n in ordered if n["id"] in kept],
            "edges": [e for e in edges if e["from"] in kept and e["to"] in kept],
            "truncated": len(ordered) > limit, "total_nodes": len(ordered), "limit": limit}


def _finding_out(f: ImpactFinding, ev_ids: dict[str, str]) -> dict[str, Any]:
    from app.services.change_impact.labels import display_label
    return {"id": str(f.id), "rule_id": f.rule_id, "rule_version": f.rule_version, "category": f.category,
            "subject_type": f.subject_type, "subject_id": str(f.subject_id) if f.subject_id else None,
            "subject_key": f.subject_key, "subject_label": display_label(f.subject_type, f.subject_label, f.params),
            "match_kind": f.match_kind,
            "impact": f.impact, "severity": f.severity, "disposition": f.disposition,
            "evidence_strength": f.evidence_strength, "reason_code": f.reason_code, "params": f.params,
            "evidence_ids": [ev_ids[k] for k in f.evidence_keys if k in ev_ids], "relationship_path": f.path_refs,
            "suggested_action": f.suggested_action, "fingerprint": f.fingerprint}


@router.get("/impact-runs/{run_id}/evidence/{evidence_id}")
async def run_evidence(run_id: uuid.UUID, evidence_id: uuid.UUID, user: CurrentUser, session: Session) -> dict[str, Any]:
    await _enabled(session)
    run, _p = await _run(session, user, run_id)
    e = await session.get(ImpactEvidence, evidence_id)
    v = await viewer(session, user)
    # 證據一定要屬於這個 run，而且現在看得到（拿別的 run 的 id 來問一律 404，規格 T21）
    if e is None or e.run_id != run.id or not v.can(e.visibility_type, e.visibility_id):
        raise HTTPException(404, detail="Evidence not found")
    return {"id": str(e.id), "key": e.key, "source_type": e.source_type, "integration_ref": e.integration_ref,
            "object_type": e.source_object_type, "object_id": str(e.source_object_id) if e.source_object_id else None,
            "object_key": e.source_object_key, "label": e.label, "observed_at": _iso(e.observed_at),
            "collected_at": _iso(e.collected_at), "payload": e.sanitized_payload, "payload_hash": e.payload_hash,
            "freshness": e.freshness}


@router.get("/impact-runs/{run_id}/evidence")
async def run_evidence_list(run_id: uuid.UUID, user: CurrentUser, session: Session) -> dict[str, Any]:
    await _enabled(session)
    run, _p = await _run(session, user, run_id)
    bundle = await impact_ai.visible_bundle(session, run, await viewer(session, user))
    return {"items": [{"id": str(e.id), "key": e.key, "source_type": e.source_type,
                       "object_type": e.source_object_type, "label": e.label, "observed_at": _iso(e.observed_at),
                       "collected_at": _iso(e.collected_at), "freshness": e.freshness,
                       "integration_ref": e.integration_ref} for e in bundle["evidence"]]}


@router.get("/impact-runs/{run_id}/gaps")
async def run_gaps(run_id: uuid.UUID, user: CurrentUser, session: Session) -> dict[str, Any]:
    await _enabled(session)
    run, _p = await _run(session, user, run_id)
    bundle = await impact_ai.visible_bundle(session, run, await viewer(session, user))
    return {"items": [{"id": str(g.id), "category": g.category, "reason_code": g.reason_code, "params": g.params,
                       "source_scope": g.source_scope, "affected_analysis": g.affected_analysis}
                      for g in bundle["gaps"]]}


@router.post("/impact-runs/{run_id}/cancel")
async def cancel_run(run_id: uuid.UUID, user: CurrentUser, request: Request, session: Session) -> dict[str, Any]:
    await _enabled(session)
    run, plan = await _run(session, user, run_id)
    if not (user.is_admin or run.requested_by == user.id):
        raise HTTPException(403, detail=ui_detail("impact_plan_forbidden", "Only the requester or an admin can cancel"))
    changed = await jobs.request_cancel(session, run)
    if changed:
        await _audit(session, request, user, "impact_run_cancel", plan.id, {"run_id": str(run.id)})
    await session.commit()
    await session.refresh(run)
    return _run_out(run, await viewer(session, user))


@router.get("/impact-runs/{run_id}/export")
async def export_run(run_id: uuid.UUID, user: CurrentUser, request: Request, session: Session,
                     format: str = Query("md", pattern="^(md|json)$")) -> Response:
    import json as _json

    from app.services.change_impact import export as ex
    await _enabled(session)
    run, plan = await _run(session, user, run_id)
    if run.job_status not in ("completed", "partial"):
        raise HTTPException(409, detail=ui_detail("impact_run_not_complete", "The analysis is not finished"))
    v = await viewer(session, user)
    bundle = await impact_ai.visible_bundle(session, run, v)
    all_tasks = list((await session.execute(select(ChangeTask).where(ChangeTask.plan_id == plan.id)
                                            .order_by(ChangeTask.phase, ChangeTask.position))).scalars())
    seen = await visible_task_findings(session, all_tasks, v)
    tasks = [t for t in all_tasks if not t.finding_ids or seen[t.id]]
    changed = not same_scope(run, v)
    await _audit(session, request, user, "impact_export", plan.id, {"run_id": str(run.id), "format": format})
    await session.commit()
    stamp = (run.completed_at or run.created_at).strftime("%Y%m%d-%H%M")
    if format == "json":
        body = _json.dumps(ex.as_json(plan, run, bundle, tasks, scope_changed=changed), ensure_ascii=False, indent=2)
        media, ext = "application/json", "json"
    else:
        body = ex.as_markdown(plan, run, bundle, tasks, scope_changed=changed)
        media, ext = "text/markdown; charset=utf-8", "md"
    return Response(content=body.encode("utf-8"), media_type=media, headers={
        "Content-Disposition": f'attachment; filename="change-impact-{stamp}.{ext}"', "Cache-Control": "no-store"})


# ─────────────────── 覆核 ───────────────────

class ReviewIn(StrictModel):
    decision: str
    rationale: str = Field(default="", max_length=4000)
    dispositions: dict[str, dict[str, str]] = Field(default_factory=dict)
    run_id: uuid.UUID | None = None


def _review_out(r: ImpactReview, names: dict[Any, str] | None = None) -> dict[str, Any]:
    return {"id": str(r.id), "revision": r.revision, "run_id": str(r.run_id) if r.run_id else None,
            "reviewer_name": (names or {}).get(r.reviewer_id),
            "snapshot_hash": r.snapshot_hash, "reviewer_id": str(r.reviewer_id) if r.reviewer_id else None,
            "decision": r.decision, "rationale": r.rationale, "dispositions": r.dispositions,
            "step_index": r.step_index, "created_at": _iso(r.created_at)}


@router.post("/change-plans/{plan_id}/reviews", status_code=status.HTTP_201_CREATED)
async def create_review(plan_id: uuid.UUID, body: ReviewIn, user: CurrentUser, request: Request,
                        session: Session) -> dict[str, Any]:
    cfg = await _enabled(session)
    plan = await _plan(session, user, plan_id)
    try:
        rv = await plans.review(session, user, plan, decision=body.decision, rationale=body.rationale,
                                dispositions=body.dispositions, run_id=body.run_id, cfg=cfg)
    except ScenarioError as exc:
        raise _err(exc) from exc
    await session.flush()
    if plan.lifecycle == "in_review":
        # 多關卡、還有關卡沒過：依序模式通知下一關（會簽在送審時已經通知所有組）
        if (await reviewers.policy(session, cfg))["approver_mode"] == "stages":
            await reviewers.notify_submitted(session, plan, user, cfg)
    else:
        await reviewers.notify_reviewed(session, plan, user, body.decision, body.rationale or "")
    await _audit(session, request, user, "impact_review", plan.id,
                 {"decision": body.decision, "revision": plan.revision, "run_id": str(rv.run_id),
                  "snapshot_hash": rv.snapshot_hash, "lifecycle": plan.lifecycle, "step_index": rv.step_index})
    await session.commit()
    return _review_out(rv)


@router.get("/change-plans/{plan_id}/reviews")
async def list_reviews(plan_id: uuid.UUID, user: CurrentUser, session: Session) -> dict[str, Any]:
    await _enabled(session)
    plan = await _plan(session, user, plan_id)
    rows = (await session.execute(select(ImpactReview).where(ImpactReview.plan_id == plan.id)
                                  .order_by(ImpactReview.created_at.desc()))).scalars().all()
    from app.core.sqlin import in_values
    from app.models.user import User as _User
    ids = {r.reviewer_id for r in rows if r.reviewer_id}
    names = {u.id: (u.display_name or u.username) for u in (await session.execute(
        select(_User).where(in_values(_User.id, list(ids))))).scalars()} if ids else {}
    return {"items": [_review_out(r, names) for r in rows]}


# ─────────────────── 待辦 ───────────────────

class TaskIn(StrictModel):
    phase: str
    title: str = Field(max_length=300)
    instruction: str = Field(default="", max_length=8000)
    depends_on: list[str] = Field(default_factory=list)
    assignee_user_id: uuid.UUID | None = None


class TaskPatch(StrictModel):
    state: str | None = None
    completion_note: str | None = Field(default=None, max_length=4000)
    assignee_user_id: uuid.UUID | None = None
    depends_on: list[str] | None = None
    expected_version: int


class AcceptDraftIn(StrictModel):
    artifact_id: uuid.UUID
    indices: list[int]


def _task_out(t: ChangeTask, finding_ids: list[str] | None = None) -> dict[str, Any]:
    """finding_ids 是依讀者過濾過的；模板參數裡的筆數跟著改成看得到的數量。"""
    params = dict(t.template_params or {})
    ids = t.finding_ids if finding_ids is None else finding_ids
    if finding_ids is not None and "count" in params:
        params["count"] = len(ids)
    return {"id": str(t.id), "phase": t.phase, "position": t.position, "title": t.title,
            "instruction": t.instruction, "template_code": t.template_code, "template_params": params,
            "origin": t.origin, "finding_ids": ids, "depends_on": t.depends_on,
            "assignee_user_id": str(t.assignee_user_id) if t.assignee_user_id else None, "state": t.state,
            "version": t.version, "completed_by": str(t.completed_by) if t.completed_by else None,
            "completed_at": _iso(t.completed_at), "completion_note": t.completion_note}


@router.get("/change-plans/{plan_id}/tasks")
async def list_tasks(plan_id: uuid.UUID, user: CurrentUser, session: Session) -> dict[str, Any]:
    await _enabled(session)
    plan = await _plan(session, user, plan_id)
    rows = list((await session.execute(select(ChangeTask).where(ChangeTask.plan_id == plan.id)
                                       .order_by(ChangeTask.position))).scalars().all())
    seen = await visible_task_findings(session, rows, await viewer(session, user))
    return {"items": [_task_out(t, seen[t.id]) for t in rows if not t.finding_ids or seen[t.id]]}


@router.post("/change-plans/{plan_id}/tasks", status_code=status.HTTP_201_CREATED)
async def create_task(plan_id: uuid.UUID, body: TaskIn, user: CurrentUser, request: Request,
                      session: Session) -> dict[str, Any]:
    await _enabled(session)
    plan = await _plan(session, user, plan_id)
    try:
        t = await plans.add_task(session, user, plan, phase=body.phase, title=body.title,
                                 instruction=body.instruction, depends_on=body.depends_on,
                                 assignee_user_id=body.assignee_user_id)
    except ScenarioError as exc:
        raise _err(exc) from exc
    await _audit(session, request, user, "change_task_create", plan.id,
                 {"task_id": str(t.id), "phase": t.phase, "title": t.title, "origin": t.origin})
    await session.commit()
    return _task_out(t)


@router.post("/change-plans/{plan_id}/tasks/from-ai", status_code=status.HTTP_201_CREATED)
async def accept_ai_tasks(plan_id: uuid.UUID, body: AcceptDraftIn, user: CurrentUser, request: Request,
                          session: Session) -> dict[str, Any]:
    """接受 AI 草擬的待辦：只有使用者勾選的才存（規格 §10.2「確認選取後才存成待辦」）。"""
    await _enabled(session)
    plan = await _plan(session, user, plan_id)
    art = await session.get(ImpactAIArtifact, body.artifact_id)
    run = await session.get(ImpactRun, art.run_id) if art else None
    if art is None or run is None or run.plan_id != plan.id or art.status != "completed":
        raise HTTPException(404, detail="Draft not found")
    drafts = (art.output_json or {}).get("suggested_tasks") or []
    created = []
    try:
        for i in sorted(set(body.indices)):
            if not 0 <= i < len(drafts):
                raise plans.PlanError("impact_invalid_task", "No such draft item", index=i)
            d = drafts[i]
            created.append(await plans.add_task(session, user, plan, phase=d["phase"], title=d["text"][:300],
                                                instruction=d["text"], depends_on=[], assignee_user_id=None,
                                                origin="ai_draft", finding_ids=[str(x) for x in d.get("finding_ids") or []]))
    except ScenarioError as exc:
        raise _err(exc) from exc
    await _audit(session, request, user, "change_task_create", plan.id,
                 {"origin": "ai_draft", "artifact_id": str(art.id), "count": len(created)})
    await session.commit()
    return {"items": [_task_out(t) for t in created]}


@router.patch("/change-tasks/{task_id}")
async def patch_task(task_id: uuid.UUID, body: TaskPatch, user: CurrentUser, request: Request,
                     session: Session) -> dict[str, Any]:
    await _enabled(session)
    task = await session.get(ChangeTask, task_id)
    if task is None:
        raise HTTPException(404, detail="Task not found")
    plan = await _plan(session, user, task.plan_id)
    before = {"state": task.state, "version": task.version}
    try:
        await plans.update_task(session, user, plan, task, body.model_dump(exclude_unset=True,
                                                                          exclude={"expected_version"}),
                                body.expected_version)
    except ScenarioError as exc:
        raise _err(exc) from exc
    await _audit(session, request, user, "change_task_update", plan.id,
                 {"task_id": str(task.id), "before": before, "after": {"state": task.state, "version": task.version}})
    await session.commit()
    return _task_out(task)


# ─────────────────── AI ───────────────────

class AIIn(StrictModel):
    artifact_type: str = Field(pattern="^(summary|checklist|explanation)$")


class QuestionIn(StrictModel):
    question: str = Field(min_length=1, max_length=1000)


def _artifact_out(a: ImpactAIArtifact, v: Viewer) -> dict[str, Any]:
    hidden = bool(a.scope_hash and a.scope_hash != v.scope_hash())
    return {"id": str(a.id), "artifact_type": a.artifact_type, "status": a.status, "stage": a.stage,
            "question": a.question,
            "model": a.model, "prompt_version": a.prompt_version, "validation_state": a.validation_state,
            "truncated": a.truncated, "error_code": a.error_code, "generated_at": _iso(a.generated_at),
            "created_at": _iso(a.created_at),
            # 用不同權限範圍產生的解說整份隱藏：不能只遮 id 卻留下名稱與數量（規格 §11.1 第 4 點）
            "hidden_scope_changed": hidden, "output": None if hidden else a.output_json}


async def _create_artifact(session: AsyncSession, user: User, request: Request, run: ImpactRun, plan: ChangePlan,
                           kind: str, question: str | None) -> ImpactAIArtifact:
    cfg = await get_config(session)
    if not await ai_available(session, cfg):
        raise HTTPException(409, detail=ui_detail("impact_ai_unavailable", "AI is not available"))
    if run.job_status not in ("completed", "partial"):
        raise HTTPException(409, detail=ui_detail("impact_run_not_complete", "The analysis is not finished"))
    busy = (await session.execute(select(func.count()).select_from(ImpactAIArtifact).where(
        ImpactAIArtifact.requested_by == user.id, ImpactAIArtifact.status.in_(("pending", "running"))))).scalar() or 0
    if busy >= int(cfg["limits"]["per_user_ai_jobs"]):
        raise HTTPException(409, detail=ui_detail("impact_concurrency_limit", "An AI request is already running",
                                                  limit=int(cfg["limits"]["per_user_ai_jobs"])))
    if question:
        from app.services.ai_guard import screen_text
        try:
            screen_text(question)
        except Exception as exc:
            raise HTTPException(400, detail=ui_detail("impact_question_rejected", "The question was rejected")) from exc
    from app.services.ai import user_locale
    art = ImpactAIArtifact(run_id=run.id, artifact_type=kind, status="pending", question=question,
                           prompt_version=impact_ai.PROMPT_VERSION, language=(await user_locale(session, user)) or "zh-TW",
                           requested_by=user.id)
    session.add(art)
    await session.flush()
    await _audit(session, request, user, "impact_ai_request", plan.id,
                 {"run_id": str(run.id), "artifact_id": str(art.id), "type": kind})
    await session.commit()
    await impact_ai.launch(art.id, actor_user_id=user.id, plan_id=plan.id, label=plan.title)
    await session.refresh(art)
    return art


@router.post("/impact-runs/{run_id}/ai-artifacts", status_code=status.HTTP_202_ACCEPTED)
async def request_ai(run_id: uuid.UUID, body: AIIn, user: CurrentUser, request: Request,
                     session: Session) -> dict[str, Any]:
    await _enabled(session)
    run, plan = await _run(session, user, run_id)
    art = await _create_artifact(session, user, request, run, plan, body.artifact_type, None)
    return _artifact_out(art, await viewer(session, user))


@router.post("/impact-runs/{run_id}/questions", status_code=status.HTTP_202_ACCEPTED)
async def ask(run_id: uuid.UUID, body: QuestionIn, user: CurrentUser, request: Request,
              session: Session) -> dict[str, Any]:
    await _enabled(session)
    run, plan = await _run(session, user, run_id)
    art = await _create_artifact(session, user, request, run, plan, "answer", body.question.strip())
    return _artifact_out(art, await viewer(session, user))


@router.get("/impact-runs/{run_id}/ai-artifacts")
async def list_ai(run_id: uuid.UUID, user: CurrentUser, session: Session) -> dict[str, Any]:
    await _enabled(session)
    run, _p = await _run(session, user, run_id)
    v = await viewer(session, user)
    rows = (await session.execute(select(ImpactAIArtifact).where(ImpactAIArtifact.run_id == run.id)
                                  .order_by(ImpactAIArtifact.created_at.desc()).limit(50))).scalars().all()
    # 追問的內容只給問的人看（其他人看得到這份結果，但不是他問的問題）
    return {"items": [_artifact_out(a, v) for a in rows
                      if a.artifact_type != "answer" or a.requested_by == user.id or user.is_admin]}
