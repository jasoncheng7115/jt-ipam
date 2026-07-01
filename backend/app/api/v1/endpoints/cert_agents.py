"""憑證派送代理（cert-agents）。

兩種呼叫者：
- 管理面（admin，JWT）：CRUD + key 輪替 + 看各站台回報狀態。
- agent（X-Agent-Key 認證）：`check`（我負責的憑證有沒有新版）/ `bundle`（下載 crt/key/chain）/
  `report`（回報套用結果）。`bundle` 會回傳**私鑰明文**（即時解密）→ 強制 TLS、scope 限定、逐次稽核。

下載 `agent.sh` / `installer.sh` 為純程式碼、無密鑰，公開可取（同掃描代理）。
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import PlainTextResponse
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin, require_global_read
from app.core.audit import append_audit
from app.core.db import get_session
from app.core.security import decrypt_secret, encrypt_secret
from app.models.address import IPAddress
from app.models.certificate import CertAgent, Certificate, CertVersion
from app.models.device import Device
from app.models.encrypted_secret import EncryptedSecret
from app.schemas.base import Paginated
from app.schemas.certificate import (
    CertAgentCreate,
    CertAgentCreated,
    CertAgentRead,
    CertAgentUpdate,
)
from app.services.cert_service import export_cert_file

router = APIRouter(prefix="/cert-agents", tags=["cert-agents"])

_AGENT_DIR = Path(__file__).resolve().parents[5] / "agent"
_AGENT_SH = _AGENT_DIR / "jt_ipam_cert_agent.sh"  # 純 bash 派送代理（curl + coreutils,無 Python）


def _agent_sha() -> str:
    """目前 server 上派送代理程式的 sha256（給 agent 自動更新比對用）。"""
    try:
        return hashlib.sha256(_AGENT_SH.read_bytes()).hexdigest()
    except OSError:
        return ""


def _server_agent_version() -> str | None:
    """從 server 端 agent.sh 解析 AGENT_VERSION，給 UI 標示「代理版本落後」。"""
    try:
        import re
        m = re.search(r'^AGENT_VERSION=["\']?([0-9][^"\'\s]*)', _AGENT_SH.read_text(), re.M)
        return m.group(1) if m else None
    except OSError:
        return None


def _key_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# enroll key 除了存 hash（給比對認證），另把明文 AES-GCM 加密存 encrypted_secret，讓管理員之後可再檢視。
def _agent_key_aad(agent_id: uuid.UUID) -> bytes:
    return f"cert_agent:{agent_id}:enroll_key".encode()


async def _save_agent_key(session: AsyncSession, agent_id: uuid.UUID, raw_key: str) -> None:
    enc, nonce = encrypt_secret(raw_key, aad=_agent_key_aad(agent_id))
    existing = (await session.execute(select(EncryptedSecret).where(
        EncryptedSecret.object_type == "cert_agent", EncryptedSecret.object_id == agent_id,
        EncryptedSecret.field == "enroll_key",
    ))).scalar_one_or_none()
    if existing is None:
        session.add(EncryptedSecret(object_type="cert_agent", object_id=agent_id,
                                    field="enroll_key", ciphertext=enc, nonce=nonce))
    else:
        existing.ciphertext = enc
        existing.nonce = nonce


async def _load_agent_key(session: AsyncSession, agent_id: uuid.UUID) -> str | None:
    row = (await session.execute(select(EncryptedSecret).where(
        EncryptedSecret.object_type == "cert_agent", EncryptedSecret.object_id == agent_id,
        EncryptedSecret.field == "enroll_key",
    ))).scalar_one_or_none()
    if row is None:
        return None
    return decrypt_secret(row.ciphertext, row.nonce, aad=_agent_key_aad(agent_id)).decode("utf-8")


def _new_key() -> str:
    return secrets.token_urlsafe(32)


# 近期回報來源 IP 視窗（同把 Key 被多台主機共用時用來警告）。
_SOURCE_WINDOW = timedelta(days=7)
_SOURCE_CAP = 10


def _record_source_ip(agent: CertAgent, ip: str | None) -> None:
    """把這次回報的來源 IP 併入 recent_sources（去重保留最新、上限 _SOURCE_CAP、淘汰過期）。"""
    if not ip:
        return
    now = datetime.now(UTC)
    cutoff = now - _SOURCE_WINDOW
    out: list[dict[str, str]] = []
    for e in (agent.recent_sources or []):
        try:
            eip = e["ip"]
            eat = datetime.fromisoformat(e["at"])
        except (KeyError, TypeError, ValueError):
            continue
        if eip == ip or eat < cutoff:
            continue  # 目前這顆 IP 等下重新加在最前面；過期的丟掉
        out.append({"ip": eip, "at": e["at"]})
    out.insert(0, {"ip": ip, "at": now.isoformat()})
    agent.recent_sources = out[:_SOURCE_CAP]


def _recent_source_ips(agent: CertAgent) -> list[str]:
    """視窗內去重的來源 IP（最新在前）。"""
    cutoff = datetime.now(UTC) - _SOURCE_WINDOW
    ips: list[str] = []
    for e in (agent.recent_sources or []):
        try:
            if datetime.fromisoformat(e["at"]) >= cutoff and e["ip"] not in ips:
                ips.append(e["ip"])
        except (KeyError, TypeError, ValueError):
            continue
    return ips


def _to_read(obj: CertAgent) -> CertAgentRead:
    m = CertAgentRead.model_validate(obj, from_attributes=True)
    m.has_key = bool(obj.enroll_key_hash)
    m.server_agent_version = _server_agent_version()
    ips = _recent_source_ips(obj)
    m.recent_source_ips = ips
    m.multi_source_recent = len(ips) > 1
    return m


async def _resolve_links(
    session: AsyncSession, agents: list[CertAgent],
) -> tuple[dict[str, str], dict[str, uuid.UUID]]:
    """批次解析代理的關聯：device_id → 裝置名稱、last_source_ip → 對應 IPAddress.id。
    回 (device_names{device_id字串: name}, source_ip_ids{agent_id字串: ip uuid})。
    重疊網段同 IP 多筆時，優先取「掛在該代理對應裝置上」的那筆，否則取第一筆。"""
    dev_ids = {a.device_id for a in agents if a.device_id}
    device_names: dict[str, str] = {}
    if dev_ids:
        rows = (await session.execute(
            select(Device.id, Device.name).where(Device.id.in_(dev_ids))
        )).all()
        device_names = {str(i): n for i, n in rows}

    src_ips = {a.last_source_ip for a in agents if a.last_source_ip}
    by_ip: dict[str, list[tuple[uuid.UUID, Any]]] = {}
    if src_ips:
        rows = (await session.execute(
            select(IPAddress.id, IPAddress.ip, IPAddress.device_id)
            .where(func.host(IPAddress.ip).in_(list(src_ips)))
        )).all()
        for ip_id, ip_val, did in rows:
            by_ip.setdefault(str(ip_val), []).append((ip_id, did))

    source_ip_ids: dict[str, uuid.UUID] = {}
    for a in agents:
        cands = by_ip.get(a.last_source_ip or "", [])
        if not cands:
            continue
        chosen = next(
            (iid for iid, did in cands if a.device_id and did == a.device_id), None,
        ) or cands[0][0]
        source_ip_ids[str(a.id)] = chosen
    return device_names, source_ip_ids


async def _to_reads(session: AsyncSession, agents: list[CertAgent]) -> list[CertAgentRead]:
    """list/詳情用：_to_read + 補 device_name / source_ip_id。"""
    items = [_to_read(a) for a in agents]
    device_names, source_ip_ids = await _resolve_links(session, agents)
    for it, a in zip(items, agents, strict=True):
        it.device_name = device_names.get(str(a.device_id)) if a.device_id else None
        it.source_ip_id = source_ip_ids.get(str(a.id))
    return items


def _key_aad(certificate_id: uuid.UUID, fingerprint: str) -> bytes:
    return f"cert_version:{certificate_id}:{fingerprint}".encode()


def _scope_ids(agent: CertAgent) -> set[str]:
    return {str(x) for x in (agent.scope_cert_ids or [])}


async def _agent_from_key(session: AsyncSession, key: str | None) -> CertAgent:
    if not key:
        raise HTTPException(401, detail="missing agent key")
    obj = (await session.execute(
        select(CertAgent).where(CertAgent.enroll_key_hash == _key_hash(key))
    )).scalar_one_or_none()
    if obj is None or not obj.enabled:
        raise HTTPException(401, detail="invalid agent key")
    return obj


# ─────────────────── 下載（公開，無密鑰）───────────────────

@router.get("/installer.sh", include_in_schema=False)
async def download_installer() -> PlainTextResponse:
    p = _AGENT_DIR / "jt-ipam-cert-agent-installer.sh"
    if not p.exists():
        raise HTTPException(404, detail="installer not found")
    return PlainTextResponse(p.read_text(), media_type="text/x-shellscript")


@router.get("/agent.sh", include_in_schema=False)
async def download_agent() -> PlainTextResponse:
    if not _AGENT_SH.exists():
        raise HTTPException(404, detail="agent not found")
    return PlainTextResponse(_AGENT_SH.read_text(), media_type="text/x-shellscript")


@router.get("/server-version", dependencies=[Depends(require_admin)])
async def server_agent_version_endpoint() -> dict[str, str | None]:
    """server 端目前派送代理程式的版本（管理頁顯示「最新代理版本」）。"""
    return {"version": _server_agent_version()}


# ─────────────────── 唯讀現況（global-read：admin 或唯讀檢視者）───────────────────

@router.get("/status", dependencies=[Depends(require_global_read)])
async def agents_status(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """各代理的憑證派送現況（最後更新 / 有效日 / 到期日 / 剩餘天數 / 是否最新）。
    屬全域基礎設施檢視：非管理員但具萬用讀取（唯讀檢視者）亦可看;掛在「進階」。"""
    cur_rows = (await session.execute(
        select(Certificate, CertVersion)
        .join(CertVersion, CertVersion.certificate_id == Certificate.id)
        .where(CertVersion.is_current.is_(True))
    )).all()
    cur = {cert.name: ver for cert, ver in cur_rows}
    now = datetime.now(UTC)
    server_ver = _server_agent_version()
    agents = (await session.execute(select(CertAgent).order_by(CertAgent.name))).scalars().all()
    device_names, source_ip_ids = await _resolve_links(session, list(agents))

    out: list[dict[str, Any]] = []
    for a in agents:
        deps: list[dict[str, Any]] = []
        for d in (a.reported or []):
            if not isinstance(d, dict):
                continue
            ver = cur.get(d.get("cert"))
            na = ver.not_after if ver else None
            deps.append({
                "cert": d.get("cert"), "profile": d.get("profile"), "status": d.get("status"),
                "applied_at": d.get("applied_at"), "dry_run": d.get("dry_run"),
                "reported_fingerprint": d.get("fingerprint"),
                "current_fingerprint": ver.fingerprint_sha256 if ver else None,
                "up_to_date": bool(ver and d.get("fingerprint") == ver.fingerprint_sha256),
                "not_before": ver.not_before.isoformat() if ver and ver.not_before else None,
                "not_after": na.isoformat() if na else None,
                "days_remaining": (na - now).days if na else None,
            })
        recent_ips = _recent_source_ips(a)
        out.append({
            "agent": a.name, "enabled": a.enabled,
            "device_id": str(a.device_id) if a.device_id else None,
            "device_name": device_names.get(str(a.device_id)) if a.device_id else None,
            "source_ip_id": str(source_ip_ids[str(a.id)]) if str(a.id) in source_ip_ids else None,
            "last_seen_at": a.last_seen_at.isoformat() if a.last_seen_at else None,
            "last_source_ip": a.last_source_ip,
            "recent_source_ips": recent_ips,
            "multi_source_recent": len(recent_ips) > 1,
            "agent_version": a.agent_version, "server_agent_version": server_ver,
            "deployments": deps,
        })
    return {"agents": out}


# ─────────────────── 管理面（admin）───────────────────

@router.get("", response_model=Paginated[CertAgentRead], dependencies=[Depends(require_admin)])
async def list_agents(
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=200),
) -> Paginated[CertAgentRead]:
    total = int((await session.execute(select(func.count()).select_from(CertAgent))).scalar_one())
    rows = (await session.execute(
        select(CertAgent).order_by(CertAgent.name).offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    return Paginated[CertAgentRead](
        items=await _to_reads(session, list(rows)), total=total, page=page, page_size=page_size,
    )


@router.post("", response_model=CertAgentCreated, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_agent(
    payload: CertAgentCreate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CertAgentCreated:
    if (await session.execute(
        select(CertAgent).where(CertAgent.name == payload.name).limit(1)
    )).scalar_one_or_none() is not None:
        raise HTTPException(409, detail="Agent name already exists")
    raw_key = _new_key()
    obj = CertAgent(
        name=payload.name, description=payload.description, enabled=payload.enabled,
        enroll_key_hash=_key_hash(raw_key),
        scope_cert_ids=[str(c) for c in payload.scope_cert_ids],
        device_id=payload.device_id,
    )
    session.add(obj)
    await session.flush()
    await _save_agent_key(session, obj.id, raw_key)
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="cert_agent", object_id=str(obj.id), action="cert_agent_create",
        diff={"name": obj.name}, request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return CertAgentCreated(**_to_read(obj).model_dump(), enroll_key=raw_key)


@router.patch("/{agent_id}", response_model=CertAgentRead, dependencies=[Depends(require_admin)])
async def update_agent(
    agent_id: uuid.UUID,
    payload: CertAgentUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CertAgentRead:
    obj = await session.get(CertAgent, agent_id)
    if obj is None:
        raise HTTPException(404, detail="Not found")
    data = payload.model_dump(exclude_unset=True)
    if "scope_cert_ids" in data and data["scope_cert_ids"] is not None:
        data["scope_cert_ids"] = [str(c) for c in data["scope_cert_ids"]]
    for k, v in data.items():
        setattr(obj, k, v)
    await session.commit()
    await session.refresh(obj)  # commit 後 updated_at(onupdate)過期 → refresh 免 model_validate 同步 lazy IO 500
    return _to_read(obj)


@router.post("/{agent_id}/rotate-key", response_model=CertAgentCreated,
             dependencies=[Depends(require_admin)])
async def rotate_key(
    agent_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CertAgentCreated:
    obj = await session.get(CertAgent, agent_id)
    if obj is None:
        raise HTTPException(404, detail="Not found")
    raw_key = _new_key()
    obj.enroll_key_hash = _key_hash(raw_key)
    await _save_agent_key(session, obj.id, raw_key)
    await session.commit()
    await session.refresh(obj)  # commit 後 updated_at(onupdate)過期 → refresh 免 model_validate 同步 lazy IO 500
    return CertAgentCreated(**_to_read(obj).model_dump(), enroll_key=raw_key)


@router.get("/{agent_id}/key", dependencies=[Depends(require_admin)])
async def get_agent_key(
    agent_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    """再次取得此代理的 enroll key（管理員）。金鑰 AES-GCM 加密保存，僅 admin 可解。

    舊版建立、未保存明文的代理回 404，請改用輪替金鑰取得新的。"""
    if await session.get(CertAgent, agent_id) is None:
        raise HTTPException(404, detail="Not found")
    key = await _load_agent_key(session, agent_id)
    if key is None:
        raise HTTPException(404, detail="此代理未保存金鑰（可能建立於舊版），請輪替金鑰取得新的")
    return {"enroll_key": key}


@router.delete("/{agent_id}", status_code=204, dependencies=[Depends(require_admin)])
async def delete_agent(
    agent_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    obj = await session.get(CertAgent, agent_id)
    if obj is None:
        raise HTTPException(404, detail="Not found")
    await session.execute(delete(EncryptedSecret).where(
        EncryptedSecret.object_type == "cert_agent", EncryptedSecret.object_id == agent_id))
    await session.delete(obj)
    await session.commit()


# ─────────────────── agent 協定（X-Agent-Key 認證）───────────────────

async def _current_versions_for_scope(session: AsyncSession, agent: CertAgent) -> list[dict[str, Any]]:
    scope = _scope_ids(agent)
    if not scope:
        return []
    rows = (await session.execute(
        select(Certificate, CertVersion)
        .join(CertVersion, CertVersion.certificate_id == Certificate.id)
        .where(CertVersion.is_current.is_(True), Certificate.id.in_([uuid.UUID(s) for s in scope]))
    )).all()
    return [{
        "cert": cert.name, "cert_id": str(cert.id),
        "fingerprint": ver.fingerprint_sha256,
        "not_after": ver.not_after.isoformat(),
        "domains": ver.domains or [],
    } for cert, ver in rows]


@router.get("/check")
async def agent_check(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    fmt: Annotated[str, Query(alias="format")] = "json",
    x_agent_key: Annotated[str | None, Header()] = None,
    x_agent_version: Annotated[str | None, Header()] = None,
):
    """agent poll：回傳此 agent scope 內各憑證的「目前版本」指紋 + 到期日。

    `?format=text` 給純 bash 代理用,回逐行 `agent_sha=<sha>` + `<name>\\t<fp>\\t<not_after>`,
    免在 bash 裡解 JSON。預設仍回 JSON。
    """
    agent = await _agent_from_key(session, x_agent_key)
    agent.last_seen_at = datetime.now(UTC)
    agent.last_source_ip = request.client.host if request.client else None
    _record_source_ip(agent, agent.last_source_ip)
    if x_agent_version:
        agent.agent_version = x_agent_version[:32]
    certs = await _current_versions_for_scope(session, agent)
    await session.commit()
    sha = _agent_sha()  # server 上派送代理程式的 sha256；agent 比對不同就自我更新
    if fmt == "text":
        lines = [f"agent_sha={sha}"]
        lines += [f"{c['cert']}\t{c['fingerprint']}\t{c['not_after']}" for c in certs]
        return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain")
    return {"certificates": certs, "agent_sha": sha}


async def _resolve_current_version(
    session: AsyncSession, agent: CertAgent, cert: str,
) -> tuple[Certificate, CertVersion]:
    """依名稱或 id 找憑證 + 目前版本,並做 scope 限定（不在 scope 與不存在回相同 404）。"""
    obj = None
    try:
        obj = await session.get(Certificate, uuid.UUID(cert))
    except ValueError:
        obj = (await session.execute(
            select(Certificate).where(Certificate.name == cert).limit(1)
        )).scalar_one_or_none()
    if obj is None or str(obj.id) not in _scope_ids(agent):
        raise HTTPException(404, detail="certificate not found or not in agent scope")
    ver = (await session.execute(
        select(CertVersion).where(
            CertVersion.certificate_id == obj.id, CertVersion.is_current.is_(True)
        ).limit(1)
    )).scalar_one_or_none()
    if ver is None:
        raise HTTPException(404, detail="no current version")
    return obj, ver


async def _audit_bundle(session: AsyncSession, request: Request, agent: CertAgent,
                        obj: Certificate, ver: CertVersion, extra: str = "") -> None:
    await append_audit(
        session, actor_user_id=None,
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=f"cert-agent/{agent.name}",
        object_type="certificate", object_id=str(obj.id), action="cert_bundle_download",
        diff={"agent": agent.name, "fingerprint": ver.fingerprint_sha256, **({"part": extra} if extra else {})},
        request_id=getattr(request.state, "request_id", None),
    )
    agent.last_seen_at = datetime.now(UTC)


@router.get("/bundle")
async def agent_bundle(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    cert: Annotated[str, Query(description="憑證名稱或 id")],
    x_agent_key: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """agent 下載某憑證目前版本的 crt / key / chain（JSON；私鑰即時解密）。scope 限定 + 逐次稽核。"""
    agent = await _agent_from_key(session, x_agent_key)
    obj, ver = await _resolve_current_version(session, agent, cert)
    key_pem = decrypt_secret(ver.key_enc, ver.key_nonce,
                             aad=_key_aad(obj.id, ver.fingerprint_sha256)).decode("utf-8")
    await _audit_bundle(session, request, agent, obj, ver)
    await session.commit()
    return {
        "cert": obj.name, "fingerprint": ver.fingerprint_sha256,
        "not_after": ver.not_after.isoformat(),
        "cert_pem": ver.cert_pem, "chain_pem": ver.chain_pem, "key_pem": key_pem,
    }


@router.get("/bundle/raw")
async def agent_bundle_raw(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    cert: Annotated[str, Query(description="憑證名稱或 id")],
    part: Annotated[str, Query(description="cert|key|chain|fullchain|combined|pkcs12")] = "fullchain",
    x_agent_key: Annotated[str | None, Header()] = None,
) -> Response:
    """純 bash 代理用：直接回某一段的原始檔（text/plain 或 binary），代理 `curl -o` 直接寫檔免解 JSON。

    part：cert=葉、chain=中繼、fullchain=cert+chain、key=私鑰、combined=cert+chain+key、pkcs12=PKCS#12 keystore（jetty 用）。
    scope 限定 + 每次下載稽核（key/combined/pkcs12 視為取私鑰）。
    """
    if part not in ("cert", "key", "chain", "fullchain", "combined", "pkcs12"):
        raise HTTPException(400, detail="invalid part")
    agent = await _agent_from_key(session, x_agent_key)
    obj, ver = await _resolve_current_version(session, agent, cert)
    chain = ver.chain_pem or ""

    def _nl(s: str) -> str:
        return s if not s or s.endswith("\n") else s + "\n"

    body: bytes
    media = "application/x-pem-file"
    if part == "cert":
        body = _nl(ver.cert_pem).encode()
    elif part == "chain":
        body = _nl(chain).encode()
    elif part == "fullchain":
        body = (_nl(ver.cert_pem) + _nl(chain)).encode()
    else:  # key / combined / pkcs12 → 需解密私鑰
        key_pem = decrypt_secret(ver.key_enc, ver.key_nonce,
                                 aad=_key_aad(obj.id, ver.fingerprint_sha256)).decode("utf-8")
        if part == "key":
            body = _nl(key_pem).encode()
        elif part == "combined":
            body = (_nl(ver.cert_pem) + _nl(chain) + _nl(key_pem)).encode()
        else:  # pkcs12
            body, media, _ = export_cert_file(ver.cert_pem, key_pem, chain, "pfx", name=obj.name)

    await _audit_bundle(session, request, agent, obj, ver, extra=part)
    await session.commit()
    resp = Response(content=body, media_type=media)
    resp.headers["X-Cert-Fingerprint"] = ver.fingerprint_sha256
    resp.headers["X-Cert-Not-After"] = ver.not_after.isoformat()
    return resp


def _parse_report_body(raw: bytes, content_type: str) -> list[dict[str, Any]]:
    """解析 /report body：JSON({deployments:[...]}) 或 TSV(每行 cert\\tprofile\\tstatus\\tfingerprint\\tnot_after\\tdry_run\\tmessage)。"""
    fields = ("cert", "profile", "status", "fingerprint", "not_after", "dry_run", "message")
    if "json" in content_type:
        import json
        try:
            data = json.loads(raw or b"{}")
        except ValueError as exc:
            raise HTTPException(400, detail="invalid json") from exc
        deployments = data.get("deployments")
        if not isinstance(deployments, list):
            raise HTTPException(400, detail="deployments must be a list")
        return [{
            k: d.get(k) for k in
            ("cert", "profile", "fingerprint", "not_after", "applied_at", "status", "message", "dry_run")
        } for d in deployments if isinstance(d, dict)][:200]
    # TSV（bash 代理）
    out: list[dict[str, Any]] = []
    for line in raw.decode("utf-8", "replace").splitlines():
        if not line.strip():
            continue
        cols = line.split("\t")
        row = dict(zip(fields, cols, strict=False))
        if "dry_run" in row:
            row["dry_run"] = str(row["dry_run"]).lower() in ("1", "true", "yes")
        out.append(row)
    return out[:200]


@router.post("/report")
async def agent_report(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    x_agent_key: Annotated[str | None, Header()] = None,
    x_agent_version: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """agent 回報各 deployment 套用結果（給後台看站台健康度 / 飄移）。接受 JSON 或 TSV(bash 代理)。"""
    agent = await _agent_from_key(session, x_agent_key)
    clean = _parse_report_body(await request.body(), request.headers.get("content-type", ""))
    # 偵測「新憑證更換成功」：同一 (cert,profile) 的指紋由舊值換成新值且 status=ok（非首次、非未變）
    prev_fp: dict[tuple[Any, Any], str] = {}
    for d in (agent.reported or []):
        if isinstance(d, dict) and d.get("status") == "ok" and d.get("fingerprint"):
            prev_fp[(d.get("cert"), d.get("profile"))] = d.get("fingerprint")
    agent.reported = clean
    agent.last_seen_at = datetime.now(UTC)
    agent.last_source_ip = request.client.host if request.client else None
    _record_source_ip(agent, agent.last_source_ip)
    if x_agent_version:
        agent.agent_version = x_agent_version[:32]
    await session.commit()

    # 換新成功 → 通知（依通知矩陣）；每張憑證一次，best-effort 不影響回報結果
    from app.services.notification import notify_admins_event
    changed: list[tuple[str, str]] = []
    seen: set[str] = set()
    for d in clean:
        if not isinstance(d, dict) or d.get("status") != "ok":
            continue
        fp, cert, profile = d.get("fingerprint"), d.get("cert"), d.get("profile")
        if not (fp and cert):
            continue
        old = prev_fp.get((cert, profile))
        if old and old != fp and cert not in seen:
            seen.add(cert)
            changed.append((str(cert), str(profile or "")))
    for cert, profile in changed:
        await notify_admins_event(
            session, event="cert.deployed",
            title=f"憑證已更換成功:{cert}",
            body=f"代理「{agent.name}」已在 {profile or '目標服務'} 套用「{cert}」的新版憑證。",
            severity="info", link="/certificates",
        )
    if changed:
        await session.commit()
    return {"ok": True, "received": len(clean)}
