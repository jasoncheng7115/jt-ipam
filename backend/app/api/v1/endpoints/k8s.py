"""Kubernetes 整合 endpoints（照 OpenStack／Zabbix 的分法）。

- 設定（叢集 CRUD／測試／同步／唯讀帳號的 manifest）＝純管理資料 → `require_admin`
- 節點、Service 佔用的位址、集區、網段、網域名稱、衝突＝全域基礎設施資料 → `require_global_read`
- IP 詳細資料的「Kubernetes」卡片 → 對那筆 IP 有讀取權
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import (
    CurrentUser,
    require_admin,
    require_global_read,
    require_object_perm,
)
from app.core.audit import append_audit
from app.core.db import get_session
from app.core.ui_error import detail_of, ui_detail
from app.models.k8s import K8sCluster, K8sIPPool, K8sNode, K8sServiceIP
from app.schemas.base import Paginated
from app.schemas.k8s import K8sCreate, K8sRead, K8sUpdate
from app.services import k8s as svc
from app.services.background_tasks import spawn_task

router = APIRouter(prefix="/k8s", tags=["kubernetes"], dependencies=[Depends(require_admin)])
view_router = APIRouter(prefix="/k8s", tags=["kubernetes"], dependencies=[Depends(require_global_read)])
ip_router = APIRouter(prefix="/k8s", tags=["kubernetes"])

MANIFEST = Path(__file__).resolve().parents[5] / "deploy" / "k8s" / "jt-ipam-readonly.yaml"
_SECRETS = ("token", "client_key")


def _read(c: K8sCluster) -> K8sRead:
    out = K8sRead.model_validate(c)
    out.has_ca_cert = bool((c.ca_cert or "").strip())
    out.has_token = bool(c.token_enc)
    out.has_client_key = bool(c.client_key_enc)
    return out


async def _get_or_404(session: AsyncSession, cid: uuid.UUID) -> K8sCluster:
    c = await session.get(K8sCluster, cid)
    if c is None:
        raise HTTPException(404, detail="Not found")
    return c


def _check_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme != "https" or not parts.hostname:
        raise HTTPException(422, detail=ui_detail("k8s_url_https", "API 位址要是 https://主機:埠", url=url))
    return url.strip().rstrip("/")


def _connection(data: dict[str, Any]) -> dict[str, Any]:
    """kubeconfig 有給就以它為準：拆成 API 位址、CA、驗證方式與機密。"""
    kc = data.pop("kubeconfig", None)
    ctx = data.pop("kubeconfig_context", None)
    if kc:
        try:
            parsed = svc.parse_kubeconfig(kc, ctx)
        except svc.K8sError as exc:
            raise HTTPException(422, detail=detail_of(exc, "k8s_kubeconfig_invalid")) from exc
        data.update(parsed)
    return data


def _apply_secrets(c: K8sCluster, secrets: dict[str, str | None]) -> None:
    for field in _SECRETS:
        raw = secrets.get(field)
        if raw:
            enc, nonce = svc.encrypt_field(c.id, field, raw)
            setattr(c, f"{field}_enc", enc)
            setattr(c, f"{field}_nonce", nonce)


def _audit_kwargs(user: Any, request: Request) -> dict[str, Any]:
    return {"actor_user_id": str(user.id), "actor_ip": request.client.host if request.client else None,
            "actor_user_agent": request.headers.get("user-agent"),
            "request_id": getattr(request.state, "request_id", None)}


@view_router.get("", response_model=Paginated[K8sRead])
async def list_clusters(
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(1, ge=1, le=10_000), page_size: int = Query(50, ge=1, le=500),
) -> Paginated[K8sRead]:
    rows = list((await session.execute(select(K8sCluster).order_by(K8sCluster.name)
                                       .offset((page - 1) * page_size).limit(page_size))).scalars())
    total = int(await session.scalar(select(func.count()).select_from(K8sCluster)) or 0)
    return Paginated[K8sRead](items=[_read(r) for r in rows], total=total, page=page, page_size=page_size)


@router.get("/readonly-manifest", response_class=PlainTextResponse)
async def readonly_manifest() -> PlainTextResponse:
    """給客戶 `kubectl apply` 的唯讀帳號（ServiceAccount＋ClusterRole，只有 get／list）。"""
    return PlainTextResponse(MANIFEST.read_text(encoding="utf-8"), media_type="application/yaml",
                             headers={"Content-Disposition": 'attachment; filename="jt-ipam-readonly.yaml"'})


@router.post("", response_model=K8sRead, status_code=status.HTTP_201_CREATED)
async def create_cluster(payload: K8sCreate, user: CurrentUser, request: Request,
                         session: Annotated[AsyncSession, Depends(get_session)]) -> K8sRead:
    data = _connection(payload.model_dump())
    if not data.get("api_url"):
        raise HTTPException(422, detail=ui_detail("k8s_need_url", "需要 API 位址，或貼上 kubeconfig"))
    data["api_url"] = _check_url(data["api_url"])
    secrets = {f: data.pop(f, None) for f in _SECRETS}
    if data["auth_method"] == "token" and not secrets["token"]:
        raise HTTPException(422, detail=ui_detail("k8s_need_token", "需要 ServiceAccount 的 token"))
    if data["auth_method"] == "client_cert" and not (secrets["client_key"] and data.get("client_cert")):
        raise HTTPException(422, detail=ui_detail("k8s_need_client_cert", "需要用戶端憑證與私鑰"))
    data["scope_subnet_ids"] = [str(s) for s in (data.get("scope_subnet_ids") or [])] or None
    c = K8sCluster(**data)
    session.add(c)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="Name already exists") from exc
    _apply_secrets(c, secrets)        # 機密的 AAD 綁 id，要先 flush 拿到 id
    await session.flush()
    await append_audit(session, object_type="k8s_cluster", object_id=str(c.id), action="create",
                       diff={"name": c.name, "api_url": c.api_url, "auth_method": c.auth_method},
                       **_audit_kwargs(user, request))
    await session.commit()
    await session.refresh(c)
    return _read(c)


@router.patch("/{cid}", response_model=K8sRead)
async def update_cluster(cid: uuid.UUID, payload: K8sUpdate, user: CurrentUser, request: Request,
                         session: Annotated[AsyncSession, Depends(get_session)]) -> K8sRead:
    c = await _get_or_404(session, cid)
    data = _connection(payload.model_dump(exclude_unset=True))
    secrets = {f: data.pop(f, None) for f in _SECRETS}
    if data.get("api_url") is not None:
        data["api_url"] = _check_url(data["api_url"])
    if "ca_cert" in data:
        data["ca_cert"] = (data["ca_cert"] or "").strip() or None
    if "scope_subnet_ids" in data:
        data["scope_subnet_ids"] = [str(s) for s in (data["scope_subnet_ids"] or [])] or None
    for k, v in data.items():
        setattr(c, k, v)
    _apply_secrets(c, secrets)
    await append_audit(session, object_type="k8s_cluster", object_id=str(c.id), action="update",
                       diff={k: str(v) for k, v in data.items() if k not in ("ca_cert", "client_cert")}
                       | {f"{f}_changed": True for f in _SECRETS if secrets.get(f)},
                       **_audit_kwargs(user, request))
    await session.commit()
    await session.refresh(c)
    return _read(c)


@router.delete("/{cid}", status_code=204)
async def delete_cluster(cid: uuid.UUID, user: CurrentUser, request: Request,
                         session: Annotated[AsyncSession, Depends(get_session)]) -> None:
    c = await _get_or_404(session, cid)
    # 鏡像表走外鍵 cascade；它建的位址範圍（集區）與主機名稱一併收回
    from app.services.integration_cleanup import forget_instance
    await forget_instance(session, source="k8s", source_id=c.id)
    await session.delete(c)
    await append_audit(session, object_type="k8s_cluster", object_id=str(cid), action="delete", diff={},
                       **_audit_kwargs(user, request))
    await session.commit()


@router.post("/{cid}/test")
async def test_cluster(cid: uuid.UUID, session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, Any]:
    c = await _get_or_404(session, cid)
    try:
        return await svc.healthcheck(c)
    except svc.K8sError as exc:
        raise HTTPException(502, detail=detail_of(exc, "k8s_error")) from exc


@router.post("/{cid}/sync")
async def trigger_sync(cid: uuid.UUID, user: CurrentUser, request: Request,
                       session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, Any]:
    c = await _get_or_404(session, cid)
    audit = _audit_kwargs(user, request)

    async def _runner(sess: AsyncSession, _task: Any) -> dict[str, Any]:
        obj = await sess.get(K8sCluster, cid)
        if obj is None:
            raise RuntimeError("Kubernetes cluster disappeared")
        summary = await svc.sync_cluster(sess, obj)
        await append_audit(sess, object_type="k8s_cluster", object_id=str(cid), action="sync",
                           diff={k: str(v) for k, v in summary.items()}, **audit)
        await sess.commit()
        return summary

    task = await spawn_task(session=session, kind="k8s.sync", target_type="k8s_cluster", target_id=cid,
                            target_label=c.name, actor_user_id=user.id, runner=_runner)
    return {"task_id": str(task.id), "status": task.status, "queued_at": task.queued_at.isoformat()}


# ─────────────────── 唯讀檢視 ───────────────────

def _iso(v: Any) -> str | None:
    return v.isoformat() if v else None


@view_router.get("/{cid}/nodes")
async def list_nodes(cid: uuid.UUID, session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, Any]:
    from app.models.virt import VirtualMachine
    await _get_or_404(session, cid)
    rows = (await session.execute(select(K8sNode, VirtualMachine.name).outerjoin(
        VirtualMachine, VirtualMachine.id == K8sNode.virtual_machine_id)
        .where(K8sNode.cluster_id == cid).order_by(K8sNode.name))).all()
    return {"items": [{
        "id": str(n.id), "name": n.name, "ip": str(n.ip).split("/")[0] if n.ip else None, "addresses": n.addresses,
        "roles": n.roles, "ready": n.ready, "unschedulable": n.unschedulable, "kubelet_version": n.kubelet_version,
        "os_image": n.os_image, "kernel": n.kernel, "runtime": n.runtime, "provider_id": n.provider_id,
        "pod_cidrs": n.pod_cidrs, "ip_address_id": str(n.jt_ipam_address_id) if n.jt_ipam_address_id else None,
        "virtual_machine_id": str(n.virtual_machine_id) if n.virtual_machine_id else None, "vm_name": vm,
        "last_heartbeat": _iso(n.last_heartbeat), "synced_at": _iso(n.synced_at),
    } for n, vm in rows]}


@view_router.get("/{cid}/service-ips")
async def list_service_ips(cid: uuid.UUID, session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, Any]:
    await _get_or_404(session, cid)
    rows = (await session.execute(select(K8sServiceIP).where(K8sServiceIP.cluster_id == cid)
                                  .order_by(K8sServiceIP.ip))).scalars().all()
    return {"items": [{
        "id": str(r.id), "namespace": r.namespace, "name": r.name, "kind": r.kind,
        "ip": str(r.ip).split("/")[0], "ports": r.ports, "node": r.node,
        "ip_address_id": str(r.jt_ipam_address_id) if r.jt_ipam_address_id else None, "synced_at": _iso(r.synced_at),
    } for r in rows]}


@view_router.get("/{cid}/pools")
async def list_pools(cid: uuid.UUID, session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, Any]:
    await _get_or_404(session, cid)
    rows = (await session.execute(select(K8sIPPool).where(K8sIPPool.cluster_id == cid)
                                  .order_by(K8sIPPool.provider, K8sIPPool.name))).scalars().all()
    return {"items": [{
        "id": str(r.id), "provider": r.provider, "use": r.use, "name": r.name, "namespace": r.namespace,
        "blocks": r.blocks,
        "auto_assign": r.auto_assign, "disabled": r.disabled, "synced_at": _iso(r.synced_at),
    } for r in rows]}


@view_router.get("/{cid}/cidrs")
async def list_cidrs(cid: uuid.UUID, session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, Any]:
    """叢集網段；可直接路由的附上對應的子網路（不可路由的重疊在「衝突」頁籤）。"""
    await _get_or_404(session, cid)
    return {"items": await svc.cidr_rows(session, cid)}


@view_router.get("/{cid}/hostnames")
async def list_hostnames(cid: uuid.UUID, session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, Any]:
    """Ingress／Gateway／HTTPRoute／GRPCRoute／OpenShift Route 的名稱與位址，附上 IPAM 同步的 DNS 怎麼解析。"""
    await _get_or_404(session, cid)
    return {"items": await svc.hostnames(session, cid)}


@view_router.get("/{cid}/conflicts")
async def list_conflicts(cid: uuid.UUID, session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, Any]:
    await _get_or_404(session, cid)
    items = await svc.conflicts(session, cluster_id=cid)
    return {"items": items, "count": len(items)}


@ip_router.get("/by-ip/{ip_id}", dependencies=[Depends(require_object_perm("ip", "read", path_param="ip_id"))])
async def by_ip(ip_id: uuid.UUID, _user: CurrentUser,
                session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, Any]:
    """這筆 IP 在 Kubernetes 裡是什麼：哪個叢集的節點、哪個 Service／Pod 在用、哪些網域名稱指向它，
    或落在哪個叢集可直接路由的 Pod 網段裡（掃描看到的可能是某個 Pod）。"""
    return await svc.by_ip(session, ip_id)
