"""網域管理 API（客戶許願清單 2026-10-28）：清單、新增（單筆與批次）、詳細資料、修改、刪除、重新查 RDAP。

網域屬全域資料：讀取要全域讀取；寫入與查 RDAP（會對外連線）只限管理員。註冊商的帳號只記名稱，不存密碼。
詳細資料列出相關的 DNS 區域（全域資料）與憑證（只給管理員，跟憑證頁一致）。
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import Field, field_validator
from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin, require_global_read
from app.core.audit import append_audit
from app.core.db import get_session
from app.core.ui_error import ui_detail
from app.models.domain import Domain
from app.schemas.base import Paginated, StrictModel
from app.services import domains as svc

router = APIRouter(prefix="/domains", tags=["domains"], dependencies=[Depends(require_global_read)])


class DomainOut(StrictModel):
    id: uuid.UUID
    name: str
    registrar: str | None
    registrar_effective: str | None
    registrar_url: str | None
    registrar_account: str | None
    dns_provider: str | None
    dns_provider_effective: str | None
    dns_provider_inferred: bool
    services: str | None
    description: str | None
    expires_at: date | None          # 手填的
    expires: date | None             # 實際採用的（RDAP 優先）
    expires_source: str | None       # rdap / manual
    days_left: int | None
    auto_renew: bool | None
    nameservers: list[str]
    status: list[str]
    registered_at: datetime | None
    rdap_checked_at: datetime | None
    rdap_error: str | None


def _out(d: Domain) -> DomainOut:
    inferred = svc.infer_dns_provider(d.rdap_nameservers)
    return DomainOut(
        id=d.id, name=d.name, registrar=d.registrar, registrar_effective=d.registrar or d.rdap_registrar,
        registrar_url=d.registrar_url, registrar_account=d.registrar_account, dns_provider=d.dns_provider,
        dns_provider_effective=d.dns_provider or inferred, dns_provider_inferred=not d.dns_provider and bool(inferred),
        services=d.services, description=d.description, expires_at=d.expires_at, expires=svc.effective_expiry(d),
        expires_source="rdap" if d.rdap_expires_at else ("manual" if d.expires_at else None),
        days_left=svc.days_left(d), auto_renew=d.auto_renew, nameservers=list(d.rdap_nameservers or []),
        status=list(d.rdap_status or []), registered_at=d.rdap_registered_at, rdap_checked_at=d.rdap_checked_at,
        rdap_error=d.rdap_error,
    )


def _url_ok(v: str | None) -> str | None:
    """註冊商網址會在畫面上變成連結：只收 http / https（擋 javascript: 之類）。"""
    if v is None or not v.strip():
        return None
    s = v.strip()
    if not s.lower().startswith(("http://", "https://")):
        raise ValueError("registrar_url must start with http:// or https://")
    return s


class DomainFields(StrictModel):
    registrar: Annotated[str | None, Field(max_length=128)] = None
    registrar_url: Annotated[str | None, Field(max_length=2048)] = None
    registrar_account: Annotated[str | None, Field(max_length=128)] = None
    dns_provider: Annotated[str | None, Field(max_length=128)] = None
    services: Annotated[str | None, Field(max_length=4096)] = None
    description: Annotated[str | None, Field(max_length=4096)] = None
    expires_at: date | None = None
    auto_renew: bool | None = None

    @field_validator("registrar_url")
    @classmethod
    def _check_url(cls, v: str | None) -> str | None:
        return _url_ok(v)


class DomainCreate(DomainFields):
    name: Annotated[str, Field(min_length=1, max_length=300)]
    lookup: bool = True           # 新增後馬上查一次 RDAP


class DomainBulk(StrictModel):
    names: Annotated[list[Annotated[str, Field(max_length=300)]], Field(min_length=1, max_length=500)]


async def _audit(session: AsyncSession, user: Any, request: Request, oid: str | None, action: str,
                 diff: dict[str, Any] | None) -> None:
    await append_audit(
        session, actor_user_id=str(user.id), actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"), object_type="domain", object_id=oid, action=action,
        diff=diff, request_id=getattr(request.state, "request_id", None))


def _name_or_422(raw: str) -> str:
    try:
        return svc.normalize_domain(raw)
    except svc.DomainInputError as exc:
        raise HTTPException(422, detail=ui_detail(exc.code or "domain_invalid", str(exc), **exc.params)) from exc


async def _get_or_404(session: AsyncSession, domain_id: uuid.UUID) -> Domain:
    d = await session.get(Domain, domain_id)
    if d is None:
        raise HTTPException(404, detail=ui_detail("domain_not_found", "找不到這個網域"))
    return d


@router.get("", response_model=Paginated[DomainOut])
async def list_domains(
    _user: CurrentUser, session: Annotated[AsyncSession, Depends(get_session)],
    q: str | None = Query(None, max_length=200),
    page: int = Query(1, ge=1, le=10_000), page_size: int = Query(200, ge=1, le=500),
) -> Paginated[DomainOut]:
    stmt = select(Domain)
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(Domain.name.ilike(like), Domain.registrar.ilike(like),
                              Domain.rdap_registrar.ilike(like), Domain.dns_provider.ilike(like),
                              Domain.services.ilike(like), Domain.description.ilike(like)))
    total = int(await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = list((await session.execute(
        stmt.order_by(Domain.name).offset((page - 1) * page_size).limit(page_size))).scalars())
    return Paginated[DomainOut](items=[_out(d) for d in rows], total=total, page=page, page_size=page_size)


@router.post("", response_model=DomainOut, status_code=201, dependencies=[Depends(require_admin)])
async def create_domain(
    payload: DomainCreate, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DomainOut:
    name = _name_or_422(payload.name)
    d = Domain(name=name, **payload.model_dump(exclude={"name", "lookup"}))
    session.add(d)
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail=ui_detail("domain_exists", "這個網域已經在清單裡", name=name)) from exc
    if payload.lookup:
        await svc.refresh(session, d)
    await _audit(session, user, request, str(d.id), "create", payload.model_dump(mode="json") | {"name": name})
    await session.commit()
    await session.refresh(d)
    return _out(d)


@router.post("/bulk", dependencies=[Depends(require_admin)])
async def bulk_create(
    payload: DomainBulk, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """一次貼上很多網域；不在這裡查 RDAP（每筆之間要停一下），畫面逐筆查並顯示進度，或等每天的排程。"""
    have = set((await session.execute(select(Domain.name))).scalars())
    created: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in payload.names:
        if not raw.strip():
            continue
        try:
            name = svc.normalize_domain(raw)
        except svc.DomainInputError:
            skipped.append({"value": raw.strip(), "reason": "invalid"})
            continue
        if name in have:
            skipped.append({"value": name, "reason": "exists"})
            continue
        if name in seen:
            skipped.append({"value": name, "reason": "duplicate"})
            continue
        seen.add(name)
        d = Domain(name=name)
        session.add(d)
        await session.flush()
        created.append({"id": str(d.id), "name": name})
    if created:
        await _audit(session, user, request, None, "create", {"bulk": [x["name"] for x in created]})
    await session.commit()
    return {"created": created, "skipped": skipped}


@router.get("/{domain_id}")
async def get_domain(
    domain_id: uuid.UUID, user: CurrentUser, session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    d = await _get_or_404(session, domain_id)
    zones = [{"id": str(r.id), "name": r.name, "server": r.server, "type": r.type}
             for r in (await session.execute(text("""
                 SELECT z.id, z.name, z.type, s.name AS server FROM dns_zones z
                   JOIN dns_servers s ON s.id = z.server_id
                  WHERE lower(rtrim(z.name, '.')) = :n OR lower(rtrim(z.name, '.')) LIKE '%.' || :n
                  ORDER BY z.name, s.name LIMIT 100
             """), {"n": d.name})).all()]
    certs: list[dict[str, Any]] = []
    if user.is_admin:      # 憑證頁只給管理員
        certs = [{"id": str(r.id), "name": r.name, "domains": list(r.domains or [])}
                 for r in (await session.execute(text("""
                     SELECT c.id, c.name, c.domains FROM certificates c
                      WHERE EXISTS (SELECT 1 FROM unnest(c.domains) x
                                     WHERE lower(x) = :n OR lower(x) LIKE '%.' || :n)
                      ORDER BY c.name LIMIT 100
                 """), {"n": d.name})).all()]
    return {**_out(d).model_dump(mode="json"), "zones": zones, "certificates": certs}


@router.patch("/{domain_id}", response_model=DomainOut, dependencies=[Depends(require_admin)])
async def update_domain(
    domain_id: uuid.UUID, payload: DomainFields, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DomainOut:
    d = await _get_or_404(session, domain_id)
    data = payload.model_dump(exclude_unset=True)
    before = {k: getattr(d, k) for k in data}
    for k, v in data.items():
        setattr(d, k, v)
    await _audit(session, user, request, str(d.id), "update",
                 {"before": {k: (v.isoformat() if isinstance(v, date) else v) for k, v in before.items()},
                  "after": payload.model_dump(mode="json", exclude_unset=True)})
    await session.commit()
    await session.refresh(d)
    return _out(d)


@router.delete("/{domain_id}", status_code=204, dependencies=[Depends(require_admin)])
async def delete_domain(
    domain_id: uuid.UUID, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    d = await _get_or_404(session, domain_id)
    await _audit(session, user, request, str(d.id), "delete", {"name": d.name})
    await session.delete(d)
    await session.commit()


@router.post("/{domain_id}/rdap", response_model=DomainOut, dependencies=[Depends(require_admin)])
async def refresh_domain(
    domain_id: uuid.UUID, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DomainOut:
    """立刻重新查一次 RDAP。查不到不算錯誤：結果裡的 rdap_error 會寫原因，上次查到的資料保留。"""
    d = await _get_or_404(session, domain_id)
    ok = await svc.refresh(session, d)
    await _audit(session, user, request, str(d.id), "rdap_refresh", {"ok": ok, "error": d.rdap_error})
    await session.commit()
    await session.refresh(d)
    return _out(d)
