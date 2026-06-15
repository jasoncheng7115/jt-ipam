"""憑證集中保管 + 版本上傳（管理面,全部 require_admin — 屬純管理/機敏資料）。

agent 拉取協定(check/bundle/report,key 認證)放 cert_agents.py。
私鑰:上傳即 AES-GCM 加密存,API 一律不回傳明文。
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin
from app.core.audit import append_audit
from app.core.db import get_session
from app.core.security import decrypt_secret, encrypt_secret
from app.models.certificate import CertAgent, Certificate, CertVersion
from app.schemas.base import Paginated
from app.schemas.certificate import (
    CertificateCreate,
    CertificateRead,
    CertificateUpdate,
    CertSourceUpdate,
    CertVersionRead,
    SelfSignedRequest,
)
from app.services.cert_fetch import (
    FetchError,
    fetch_certificate,
    generate_source_ssh_keypair,
    install_public_key_sftp,
    load_cert_secret,
    probe_source_connection,
    save_cert_secret,
)
from app.services.cert_service import (
    CertError,
    CertInfo,
    analyze_chain,
    export_cert_file,
    generate_self_signed,
    validate_bundle,
)

router = APIRouter(prefix="/certificates", tags=["certificates"],
                   dependencies=[Depends(require_admin)])


def _key_aad(certificate_id: uuid.UUID, fingerprint: str) -> bytes:
    return f"cert_version:{certificate_id}:{fingerprint}".encode()


async def _store_version(
    session: AsyncSession, *, cert: Certificate, cert_pem: str, key_pem: str,
    chain_pem: str | None, info: CertInfo, user: CurrentUser, request: Request, action: str,
) -> CertVersion:
    """把驗證過的 bundle 存成新版本（加密私鑰、設為 current、寫稽核）。upload 與 self-signed 共用。"""
    if (await session.execute(
        select(CertVersion).where(
            CertVersion.certificate_id == cert.id,
            CertVersion.fingerprint_sha256 == info.fingerprint_sha256,
        ).limit(1)
    )).scalar_one_or_none() is not None:
        raise HTTPException(409, detail="這張憑證(相同 fingerprint)已經上傳過")

    key_enc, key_nonce = encrypt_secret(key_pem, aad=_key_aad(cert.id, info.fingerprint_sha256))
    await session.execute(
        update(CertVersion).where(CertVersion.certificate_id == cert.id).values(is_current=False)
    )
    v = CertVersion(
        certificate_id=cert.id,
        fingerprint_sha256=info.fingerprint_sha256, serial=info.serial,
        subject=info.subject, issuer=info.issuer,
        not_before=info.not_before, not_after=info.not_after, domains=info.domains,
        cert_pem=cert_pem, chain_pem=chain_pem,
        key_enc=key_enc, key_nonce=key_nonce,
        is_current=True, uploaded_by=user.id,
    )
    session.add(v)
    cert.domains = info.domains
    await session.flush()
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="certificate", object_id=str(cert.id), action=action,
        diff={"fingerprint": info.fingerprint_sha256, "not_after": info.not_after.isoformat(),
              "domains": info.domains},  # 不含 key
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return v


def _common_name_of(subject: str | None) -> str | None:
    """從 rfc4514 subject（如 `CN=foo.example,O=bar`）取出 CN。"""
    if not subject:
        return None
    m = re.search(r"CN=((?:[^,\\]|\\.)+)", subject)
    if not m:
        return None
    return m.group(1).replace("\\,", ",").replace("\\=", "=").strip()


async def _to_read(session: AsyncSession, cert: Certificate) -> CertificateRead:
    cur = (await session.execute(
        select(CertVersion).where(
            CertVersion.certificate_id == cert.id, CertVersion.is_current.is_(True)
        ).limit(1)
    )).scalar_one_or_none()
    count = int((await session.execute(
        select(func.count()).select_from(CertVersion).where(CertVersion.certificate_id == cert.id)
    )).scalar_one())
    m = CertificateRead.model_validate(cert, from_attributes=True)
    m.version_count = count
    if cur is not None:
        m.current_fingerprint = cur.fingerprint_sha256
        m.current_not_after = cur.not_after
        m.current_days_remaining = (cur.not_after - datetime.now(UTC)).days
        # 自簽＝subject==issuer；自簽才提供「續簽」並帶出 CN/SAN 重簽一張
        m.current_is_self_signed = bool(cur.subject and cur.issuer and cur.subject == cur.issuer)
        if m.current_is_self_signed:
            m.current_common_name = _common_name_of(cur.subject)
            m.current_sans = list(cur.domains or [])
    return m


@router.get("", response_model=Paginated[CertificateRead])
async def list_certificates(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Paginated[CertificateRead]:
    rows = list((await session.execute(
        select(Certificate).order_by(Certificate.name)
    )).scalars().all())
    items = [await _to_read(session, c) for c in rows]
    return Paginated[CertificateRead](items=items, total=len(items), page=1, page_size=len(items) or 1)


@router.post("", response_model=CertificateRead, status_code=201)
async def create_certificate(
    payload: CertificateCreate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CertificateRead:
    if (await session.execute(
        select(Certificate).where(Certificate.name == payload.name).limit(1)
    )).scalar_one_or_none() is not None:
        raise HTTPException(409, detail="Certificate name already exists")
    obj = Certificate(name=payload.name, description=payload.description)
    session.add(obj)
    await session.flush()
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="certificate", object_id=str(obj.id), action="cert_create",
        diff={"name": obj.name}, request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return await _to_read(session, obj)


@router.get("/{cert_id}", response_model=CertificateRead)
async def get_certificate(
    cert_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CertificateRead:
    obj = await session.get(Certificate, cert_id)
    if obj is None:
        raise HTTPException(404, detail="Not found")
    return await _to_read(session, obj)


@router.patch("/{cert_id}", response_model=CertificateRead)
async def update_certificate(
    cert_id: uuid.UUID,
    payload: CertificateUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CertificateRead:
    obj = await session.get(Certificate, cert_id)
    if obj is None:
        raise HTTPException(404, detail="Not found")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(obj, k, v)
    await session.commit()
    await session.refresh(obj)  # commit 後 updated_at(onupdate)過期 → refresh 免 model_validate 同步 lazy IO 500
    return await _to_read(session, obj)


@router.delete("/{cert_id}", status_code=204)
async def delete_certificate(
    cert_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    obj = await session.get(Certificate, cert_id)
    if obj is None:
        raise HTTPException(404, detail="Not found")
    # 防止刪除仍被派送代理選用的憑證（否則代理的 scope_cert_ids 會殘留孤兒 UUID）
    used_by = (await session.execute(
        select(CertAgent.name).where(CertAgent.scope_cert_ids.contains([str(cert_id)]))
        .order_by(CertAgent.name)
    )).scalars().all()
    if used_by:
        raise HTTPException(
            409,
            detail=f"此憑證仍被派送代理使用（{'、'.join(used_by)}），"
                   f"請先到這些代理的「可取憑證」移除它，再刪除憑證。",
        )
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="certificate", object_id=str(obj.id), action="cert_delete",
        diff={"name": obj.name}, request_id=getattr(request.state, "request_id", None),
    )
    await session.delete(obj)  # cert_versions cascade
    await session.commit()


@router.get("/{cert_id}/versions", response_model=list[CertVersionRead])
async def list_versions(
    cert_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[CertVersionRead]:
    if await session.get(Certificate, cert_id) is None:
        raise HTTPException(404, detail="Not found")
    rows = (await session.execute(
        select(CertVersion).where(CertVersion.certificate_id == cert_id)
        .order_by(CertVersion.created_at.desc())
    )).scalars().all()
    out: list[CertVersionRead] = []
    for r in rows:
        m = CertVersionRead.model_validate(r, from_attributes=True)
        a = analyze_chain(r.cert_pem, r.chain_pem)
        m.chain_has_root = a["has_root"]
        m.chain_complete = a["complete"]
        m.chain_can_rebuild = a["can_rebuild"]
        out.append(m)
    return out


@router.post("/{cert_id}/versions/{version_id}/rebuild-chain", response_model=CertVersionRead)
async def rebuild_chain(
    cert_id: uuid.UUID,
    version_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CertVersionRead:
    """把可組合的完整鏈（中繼 + 根 CA）組好、就地修正此版本的 chain（給 Zimbra/PDM 等嚴格驗鏈服務）。

    僅當分析判定 can_rebuild（根藏在 cert 欄或順序不對、可從現有憑證組出）才允許；指紋不變。
    """
    from app.services.cert_service import _split_pem_certs
    cert = await session.get(Certificate, cert_id)
    if cert is None:
        raise HTTPException(404, detail="Not found")
    ver = await session.get(CertVersion, version_id)
    if ver is None or ver.certificate_id != cert_id:
        raise HTTPException(404, detail="Version not found")
    a = analyze_chain(ver.cert_pem, ver.chain_pem)
    if not a["can_rebuild"]:
        raise HTTPException(409, detail="此版本無法自動組合完整鏈（可用憑證中沒有根 CA，或已是完整鏈）。")
    leaf_pem = _split_pem_certs(ver.cert_pem)[0]
    ver.cert_pem = leaf_pem if leaf_pem.endswith("\n") else leaf_pem + "\n"
    ver.chain_pem = a["built_chain_pem"]
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="certificate", object_id=str(cert.id), action="cert_rebuild_chain",
        diff={"version": str(ver.id), "fingerprint": ver.fingerprint_sha256, "chain_rebuilt": True},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(ver)
    m = CertVersionRead.model_validate(ver, from_attributes=True)
    na = analyze_chain(ver.cert_pem, ver.chain_pem)
    m.chain_has_root, m.chain_complete, m.chain_can_rebuild = na["has_root"], na["complete"], na["can_rebuild"]
    return m


@router.get("/{cert_id}/versions/{version_id}/file")
async def download_version_file(
    cert_id: uuid.UUID,
    version_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    fmt: Annotated[str, Query(description="cert|key|chain|fullchain|combined|der|pfx")] = "fullchain",
    password: Annotated[str, Query(description="pfx 加密密碼，選填")] = "",
) -> Response:
    """下載某版本憑證檔（多格式匯出）。含私鑰的格式（key/combined/pfx）逐次稽核。"""
    cert = await session.get(Certificate, cert_id)
    if cert is None:
        raise HTTPException(404, detail="Not found")
    ver = await session.get(CertVersion, version_id)
    if ver is None or ver.certificate_id != cert_id:
        raise HTTPException(404, detail="version not found")
    key_pem = decrypt_secret(ver.key_enc, ver.key_nonce,
                             aad=_key_aad(cert.id, ver.fingerprint_sha256)).decode("utf-8")
    try:
        data, media_type, filename = export_cert_file(
            ver.cert_pem, key_pem, ver.chain_pem, fmt, name=cert.name, pfx_password=password)
    except CertError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    if fmt in ("key", "combined", "pfx"):
        await append_audit(
            session, actor_user_id=str(user.id),
            actor_ip=request.client.host if request.client else None,
            actor_user_agent=request.headers.get("user-agent"),
            object_type="certificate", object_id=str(cert.id), action="cert_export",
            diff={"fmt": fmt, "fingerprint": ver.fingerprint_sha256},
            request_id=getattr(request.state, "request_id", None),
        )
        await session.commit()
    return Response(content=data, media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/{cert_id}/versions", response_model=CertVersionRead, status_code=201)
async def upload_version(
    cert_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    cert_file: Annotated[UploadFile, File()],
    key_file: Annotated[UploadFile, File()],
    chain_file: Annotated[UploadFile | None, File()] = None,
    allow_expired: Annotated[bool, Form()] = False,
) -> CertVersionRead:
    """上傳新版憑證 bundle(crt + key [+ chain])。驗證後加密私鑰、設為目前版本。"""
    cert = await session.get(Certificate, cert_id)
    if cert is None:
        raise HTTPException(404, detail="Not found")

    cert_pem = (await cert_file.read()).decode("utf-8-sig", errors="replace")
    key_pem = (await key_file.read()).decode("utf-8-sig", errors="replace")
    chain_pem = (await chain_file.read()).decode("utf-8-sig", errors="replace") if chain_file else None

    try:
        info = validate_bundle(cert_pem, key_pem, chain_pem)
    except CertError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    if info.is_expired and not allow_expired:
        raise HTTPException(400, detail=f"憑證已於 {info.not_after.date()} 過期;如確定要上傳請勾選 allow_expired")

    v = await _store_version(
        session, cert=cert, cert_pem=cert_pem, key_pem=key_pem, chain_pem=chain_pem,
        info=info, user=user, request=request, action="cert_version_upload",
    )
    return CertVersionRead.model_validate(v, from_attributes=True)


@router.put("/{cert_id}/source", response_model=CertificateRead)
async def set_source(
    cert_id: uuid.UUID,
    payload: CertSourceUpdate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CertificateRead:
    """設定 / 更新自動抓取來源(URL / SFTP)。密碼/私鑰 AES-GCM 加密儲存,不回明文。"""
    cert = await session.get(Certificate, cert_id)
    if cert is None:
        raise HTTPException(404, detail="Not found")
    cert.source_type = payload.source_type
    cert.source_config = payload.source_config or {}
    cert.fetch_interval_seconds = payload.fetch_interval_seconds
    if payload.source_password:
        await save_cert_secret(session, cert_id, "source_password", payload.source_password)
    if payload.source_private_key:
        await save_cert_secret(session, cert_id, "source_private_key", payload.source_private_key)
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="certificate", object_id=str(cert_id), action="cert_source_set",
        diff={"source_type": payload.source_type},  # 不含機敏
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    # commit 後 ORM 欄位可能被 autoflush 標記過期（server-side updated_at）；
    # 重新載入避免 _to_read 內 model_validate 在同步情境觸發 lazy IO → MissingGreenlet 500。
    await session.refresh(cert)
    return await _to_read(session, cert)


@router.post("/{cert_id}/fetch-now")
async def fetch_now(
    cert_id: uuid.UUID,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    """立即從來源抓一次(手動)。fingerprint 不同才會存新版。"""
    cert = await session.get(Certificate, cert_id)
    if cert is None:
        raise HTTPException(404, detail="Not found")
    if cert.source_type == "none":
        raise HTTPException(400, detail="此憑證未設定自動來源")
    return await fetch_certificate(session, cert, actor_user_id=user.id)


@router.post("/{cert_id}/source/test")
async def test_source(
    cert_id: uuid.UUID,
    payload: CertSourceUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    """以表單目前內容測試來源連線（不存檔）。密碼/私鑰留空時沿用已存的。"""
    cert = await session.get(Certificate, cert_id)
    if cert is None:
        raise HTTPException(404, detail="Not found")
    password = payload.source_password or await load_cert_secret(session, cert_id, "source_password")
    private_key = payload.source_private_key or await load_cert_secret(
        session, cert_id, "source_private_key")
    try:
        message = await probe_source_connection(
            payload.source_config, source_type=payload.source_type,
            password=password, private_key=private_key)
    except FetchError as exc:
        return {"ok": False, "message": str(exc)}
    return {"ok": True, "message": message}


@router.post("/{cert_id}/source/ssh-keypair")
async def gen_source_ssh_keypair(
    cert_id: uuid.UUID,
    payload: CertSourceUpdate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    """產生並儲存 jt-ipam 端登入 SFTP 來源用的 SSH 金鑰對。

    若是 SFTP 且有登入密碼（表單填的或已存的）,直接用密碼登入主機把公鑰寫進 authorized_keys
    （免使用者手動貼）。回傳公鑰 + 是否已自動安裝 + 訊息。私鑰加密存,不回明文。
    """
    cert = await session.get(Certificate, cert_id)
    if cert is None:
        raise HTTPException(404, detail="Not found")
    priv, pub = generate_source_ssh_keypair(comment=f"jt-ipam:{cert.name}")
    await save_cert_secret(session, cert_id, "source_private_key", priv)
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="certificate", object_id=str(cert_id), action="cert_source_keygen",
        diff={"public_key": pub}, request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    # 有密碼就直接幫忙安裝公鑰到主機（免手動貼）。失敗不影響金鑰已產生,回 installed=false + 原因。
    installed, message = False, ""
    if payload.source_type == "sftp":
        password = payload.source_password or await load_cert_secret(
            session, cert_id, "source_password")
        try:
            message = await install_public_key_sftp(
                payload.source_config, password=password or "", public_key=pub)
            installed = True
        except FetchError as exc:
            message = str(exc)
    return {"public_key": pub, "installed": installed, "message": message}


@router.post("/{cert_id}/self-signed", response_model=CertVersionRead, status_code=201)
async def create_self_signed(
    cert_id: uuid.UUID,
    payload: SelfSignedRequest,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CertVersionRead:
    """方便小工具:產生自簽憑證(自訂 CN/SAN/天數)並存成此憑證的目前版本(可直接派送)。"""
    cert = await session.get(Certificate, cert_id)
    if cert is None:
        raise HTTPException(404, detail="Not found")
    try:
        cert_pem, key_pem = generate_self_signed(payload.common_name, payload.sans, payload.days)
        info = validate_bundle(cert_pem, key_pem)
    except CertError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    v = await _store_version(
        session, cert=cert, cert_pem=cert_pem, key_pem=key_pem, chain_pem=None,
        info=info, user=user, request=request, action="cert_self_signed",
    )
    return CertVersionRead.model_validate(v, from_attributes=True)
