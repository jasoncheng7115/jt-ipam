"""憑證推送目標（0227）：伺服器主動把憑證推到防火牆。管理員限定。

- 帳密（API 權杖／金鑰）只寫不讀：回應只說「有沒有設定」，修改時留空＝沿用。
- 「立即推送」走背景作業（右下角面板追蹤）；推送本身由 services/cert_push/engine 記錄結果、留稽核、發通知。
"""
from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin
from app.core.audit import append_audit
from app.core.db import get_session
from app.core.ui_error import ui_detail
from app.models.cert_push_target import PUSH_KINDS, CertPushTarget
from app.models.certificate import Certificate, CertVersion
from app.schemas.base import StrictModel
from app.services.cert_push import engine
from app.services.cert_push.base import PushError

router = APIRouter(prefix="/cert-push-targets", tags=["certificates"], dependencies=[Depends(require_admin)])

#: 各家 options 允許的鍵（其他一律拒絕：options 會原樣交給 driver）
_OPTION_KEYS: dict[str, set[str]] = {
    "fortigate": {"bind", "vdom", "verify"},
    "opnsense": {"cert_uuid", "restart_webgui", "verify"},
    "paloalto": {"profile", "commit", "commit_admin", "verify"},
    "pfsense": {"bind_webgui", "verify"},
}


class Secret(StrictModel):
    api_key: str = Field("", max_length=1024)
    api_secret: str = Field("", max_length=1024)


class TargetIn(StrictModel):
    name: str = Field(..., min_length=1, max_length=128)
    certificate_id: uuid.UUID
    kind: str
    api_url: str = Field(..., min_length=8, max_length=512)
    verify_tls: bool = True
    options: dict[str, Any] = Field(default_factory=dict)
    verify_endpoint: str | None = Field(None, max_length=300)
    auto_push: bool = False
    enabled: bool = True
    secret: Secret | None = None


class TargetPatch(StrictModel):
    name: str | None = Field(None, min_length=1, max_length=128)
    certificate_id: uuid.UUID | None = None
    api_url: str | None = Field(None, min_length=8, max_length=512)
    verify_tls: bool | None = None
    options: dict[str, Any] | None = None
    verify_endpoint: str | None = Field(None, max_length=300)
    auto_push: bool | None = None
    enabled: bool | None = None
    secret: Secret | None = None


def _check(kind: str, api_url: str, options: dict[str, Any]) -> None:
    if kind not in PUSH_KINDS:
        raise HTTPException(422, detail=ui_detail("certpush_unknown_kind", "unsupported device kind", kind=kind))
    if not api_url.lower().startswith("https://"):
        raise HTTPException(422, detail=ui_detail("certpush_https_required", "the API URL must use https://"))
    extra = set(options) - _OPTION_KEYS[kind]
    if extra:
        raise HTTPException(422, detail=ui_detail("certpush_bad_option", "unknown option", option=sorted(extra)[0]))
    if kind == "paloalto" and options.get("commit_admin"):
        # partial commit 的管理員帳號：放進 XML 指令（會 escape），仍只收帳號該有的字元
        import re
        if not re.fullmatch(r"[A-Za-z0-9._@-]{1,63}", str(options["commit_admin"])):
            raise HTTPException(422, detail=ui_detail("certpush_bad_option", "unknown option", option="commit_admin"))
    if kind == "fortigate":
        bind = options.get("bind") or []
        if not isinstance(bind, list) or set(bind) - {"admin", "sslvpn"}:
            raise HTTPException(422, detail=ui_detail("certpush_bad_option", "unknown option", option="bind"))


async def _out(session: AsyncSession, t: CertPushTarget) -> dict[str, Any]:
    cert = await session.get(Certificate, t.certificate_id)
    cur = (await session.execute(select(CertVersion.fingerprint_sha256).where(
        CertVersion.certificate_id == t.certificate_id, CertVersion.is_current.is_(True)))).scalar_one_or_none()
    has_secret = bool(await engine.load_secret(session, t.id))
    return {
        "id": str(t.id), "name": t.name, "enabled": t.enabled, "kind": t.kind,
        "certificate_id": str(t.certificate_id), "certificate_name": cert.name if cert else None,
        "api_url": t.api_url, "verify_tls": t.verify_tls, "options": t.options or {},
        "verify_endpoint": t.verify_endpoint, "auto_push": t.auto_push, "has_secret": has_secret,
        "last_push_at": t.last_push_at.isoformat() if t.last_push_at else None,
        "last_status": t.last_status, "last_error": t.last_error, "last_fingerprint": t.last_fingerprint,
        "last_steps": t.last_steps or [], "current_fingerprint": cur,
        "up_to_date": bool(cur and t.last_fingerprint == cur),
    }


async def _audit(session: AsyncSession, user: Any, request: Request, t: CertPushTarget, action: str,
                 diff: dict[str, Any]) -> None:
    await append_audit(
        session, actor_user_id=str(user.id), actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"), object_type="cert_push_target",
        object_id=str(t.id), action=action, diff=diff, request_id=getattr(request.state, "request_id", None))


async def _get(session: AsyncSession, target_id: uuid.UUID) -> CertPushTarget:
    t = await session.get(CertPushTarget, target_id)
    if t is None:
        raise HTTPException(404, detail=ui_detail("certpush_not_found", "push target not found"))
    return t


async def _require_cert(session: AsyncSession, cert_id: uuid.UUID) -> None:
    if await session.get(Certificate, cert_id) is None:
        raise HTTPException(422, detail=ui_detail("certpush_cert_not_found", "certificate not found"))


@router.get("")
async def list_targets(session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, Any]:
    rows = (await session.execute(select(CertPushTarget).order_by(CertPushTarget.name))).scalars().all()
    return {"items": [await _out(session, t) for t in rows], "kinds": list(PUSH_KINDS)}


@router.post("", status_code=201)
async def create_target(payload: TargetIn, user: CurrentUser, request: Request,
                        session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, Any]:
    _check(payload.kind, payload.api_url, payload.options)
    await _require_cert(session, payload.certificate_id)
    if (await session.execute(select(CertPushTarget.id).where(CertPushTarget.name == payload.name))).first():
        raise HTTPException(409, detail=ui_detail("certpush_name_taken", "name already used"))
    t = CertPushTarget(**payload.model_dump(exclude={"secret"}))
    session.add(t)
    await session.flush()
    if payload.secret and payload.secret.api_key:
        await engine.save_secret(session, t.id, payload.secret.model_dump())
    await _audit(session, user, request, t, "cert_push_target_create",
                 {"name": t.name, "kind": t.kind, "api_url": t.api_url, "auto_push": t.auto_push})
    await session.commit()
    return await _out(session, t)


@router.patch("/{target_id}")
async def update_target(target_id: uuid.UUID, payload: TargetPatch, user: CurrentUser, request: Request,
                        session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, Any]:
    t = await _get(session, target_id)
    data = payload.model_dump(exclude_unset=True, exclude={"secret"})
    _check(t.kind, data.get("api_url", t.api_url), data.get("options", t.options or {}))
    if data.get("certificate_id"):
        await _require_cert(session, data["certificate_id"])
    for k, v in data.items():
        setattr(t, k, v)
    secret_changed = bool(payload.secret and payload.secret.api_key)
    if secret_changed:
        await engine.save_secret(session, t.id, payload.secret.model_dump())       # type: ignore[union-attr]
    await _audit(session, user, request, t, "cert_push_target_update",
                 {"fields": sorted(data), "secret_changed": secret_changed})
    await session.commit()
    return await _out(session, t)


@router.delete("/{target_id}", status_code=204)
async def delete_target(target_id: uuid.UUID, user: CurrentUser, request: Request,
                        session: Annotated[AsyncSession, Depends(get_session)]) -> None:
    t = await _get(session, target_id)
    await _audit(session, user, request, t, "cert_push_target_delete", {"name": t.name})
    await engine.delete_secret(session, t.id)
    await session.delete(t)
    await session.commit()


@router.post("/{target_id}/push")
async def push_now(target_id: uuid.UUID, user: CurrentUser, request: Request,
                   session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, Any]:
    """立即推送（背景作業）。稽核由引擎寫（每次推送一筆 cert_push）。"""
    from app.services.background_tasks import spawn_task
    t = await _get(session, target_id)
    actor, rid = user.id, getattr(request.state, "request_id", None)

    async def _runner(sess: AsyncSession, _task: Any) -> dict[str, Any]:
        target = await sess.get(CertPushTarget, target_id)
        if target is None:
            return {"status": "error", "error": "certpush_not_found"}
        r = await engine.push_target(sess, target, actor_user_id=actor, request_id=rid, trigger="manual")
        await sess.commit()
        return {"target": target.name, "status": r["status"], "error": r["error"]}

    task = await spawn_task(session=session, kind="cert.push", target_type="cert_push_target",
                            target_id=t.id, target_label=t.name, actor_user_id=user.id, runner=_runner)
    # 誰按了推送（推送結果另由引擎記一筆 cert_push）
    await _audit(session, user, request, t, "cert_push_requested", {"name": t.name, "task_id": str(task.id)})
    await session.commit()
    return {"task_id": str(task.id), "status": task.status, "queued_at": task.queued_at.isoformat()}


def _opn() -> Any:
    from app.services.cert_push.drivers import OPNsenseDriver
    return OPNsenseDriver


def _pf() -> Any:
    from app.services.cert_push.drivers import PfSenseDriver
    return PfSenseDriver


#: 能讀設備上憑證清單的設備（OPNsense：選要替換哪一張；pfSense：唯讀看哪張在用、何時到期）
_LISTABLE = {"opnsense": _opn, "pfsense": _pf}


class DeviceCertsPreviewIn(StrictModel):
    kind: str
    api_url: str = Field(..., min_length=8, max_length=512)
    verify_tls: bool = True
    secret: Secret | None = None
    #: 編輯既有目標、帳密欄留空時，用這個目標已存的帳密
    target_id: uuid.UUID | None = None


@router.post("/device-certs/preview")
async def device_certs_preview(body: DeviceCertsPreviewIn,
                               session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, Any]:
    """還沒存檔就讀設備上的憑證（使用者 10-22：「替換設備上的哪一張不能選，正常嗎？」——這欄必填，
    以前卻要先存檔才讀得到）。用表單上的帳密連一次、只讀不寫，不保存任何東西，所以不留稽核。"""
    if body.kind not in _LISTABLE:
        raise HTTPException(400, detail=ui_detail("certpush_list_unsupported", "listing is only for OPNsense and pfSense"))
    _check(body.kind, body.api_url.strip(), {})
    secret = body.secret.model_dump() if body.secret and body.secret.api_key else None
    if secret is None and body.target_id is not None:
        saved = await _get(session, body.target_id)
        secret = await engine.load_secret(session, saved.id) or None
    if not secret:
        raise HTTPException(422, detail=ui_detail("certpush_secret_required", "API key and secret are required"))
    probe = CertPushTarget(name="preview", kind=body.kind, api_url=body.api_url.strip(), verify_tls=body.verify_tls,
                           options={})
    try:
        rows = await _LISTABLE[body.kind]()(engine.make_http(probe, secret), {}).list_device_certs()
    except PushError as exc:
        raise HTTPException(502, detail=ui_detail(exc.code, "device request failed", reason=exc.detail)) from exc
    return {"items": rows}


@router.get("/{target_id}/device-certs")
async def device_certs(target_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, Any]:
    """OPNsense：列出設備上的憑證，讓管理員選要替換哪一張（唯讀）。"""
    t = await _get(session, target_id)
    if t.kind not in _LISTABLE:
        raise HTTPException(400, detail=ui_detail("certpush_list_unsupported", "listing is only for OPNsense and pfSense"))
    try:
        rows = await _LISTABLE[t.kind]()(engine.make_http(t, await engine.load_secret(session, t.id)), {}).list_device_certs()
    except PushError as exc:
        raise HTTPException(502, detail=ui_detail(exc.code, "device request failed", reason=exc.detail)) from exc
    return {"items": rows}
