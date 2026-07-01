"""Phase 3 advanced modules — CRUD endpoints。

每個 module 的 CRUD 都是基本 GET list / GET one / POST create / PATCH / DELETE
模式；admin only write；audit；其他 RBAC（per-tenant scoping）留 Phase 3.5。
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin, require_global_read
from app.core.audit import append_audit
from app.core.db import get_session
from app.models.advanced import (
    ASN,
    Circuit,
    CircuitType,
    Contact,
    ContactAssignment,
    ContactGroup,
    Provider,
    Tenant,
    TenantGroup,
    WirelessLink,
    WirelessSSID,
)
from app.schemas.base import Paginated, StrictModel

router = APIRouter(tags=["advanced"], dependencies=[Depends(require_global_read)])


# ─────────────────── 共用 helper ───────────────────


def _slugify(name: str) -> str:
    """由顯示名稱產生 slug：小寫、非英數轉 -、去頭尾 -；空字串退回 'tenant'。"""
    s = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return (s[:64] or "tenant")


async def _patch_resource(
    session: AsyncSession,
    *,
    model: type,
    obj_id: uuid.UUID,
    payload: Any,
    object_type: str,
    user: CurrentUser,
    request: Request,
):
    """進階資源通用 PATCH：套用有送來的欄位、寫稽核、commit、回 ORM 物件。"""
    obj = await session.get(model, obj_id)
    if obj is None:
        raise HTTPException(404, detail="Not found")
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(obj, key, value)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="Conflict") from exc
    await _audit(session, user=user, request=request, object_type=object_type,
                 object_id=str(obj.id), action="update",
                 diff=payload.model_dump(mode="json", exclude_unset=True))
    await session.commit()
    await session.refresh(obj)
    return obj


async def _delete_resource(
    session: AsyncSession,
    *,
    model: type,
    obj_id: uuid.UUID,
    object_type: str,
    user: CurrentUser,
    request: Request,
) -> None:
    """進階資源通用 DELETE：找不到回 404、寫稽核、commit。"""
    obj = await session.get(model, obj_id)
    if obj is None:
        raise HTTPException(404, detail="Not found")
    label = getattr(obj, "name", None) or getattr(obj, "ssid", None) or str(obj.id)
    await _audit(session, user=user, request=request, object_type=object_type,
                 object_id=str(obj.id), action="delete", diff={"name": label})
    await session.delete(obj)
    await session.commit()


async def _audit(
    session: AsyncSession,
    *,
    user: CurrentUser,
    request: Request,
    object_type: str,
    object_id: str | None,
    action: str,
    diff: dict[str, Any] | None,
) -> None:
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type=object_type,
        object_id=object_id,
        action=action,
        diff=diff,
        request_id=getattr(request.state, "request_id", None),
    )


def _list_endpoint(model_cls, schema_cls):  # type: ignore[no-untyped-def]
    """產生通用 list endpoint handler。"""

    async def handler(
        _user: CurrentUser,
        session: Annotated[AsyncSession, Depends(get_session)],
        page: int = Query(1, ge=1, le=10_000),
        page_size: int = Query(50, ge=1, le=500),
    ) -> Paginated:  # type: ignore[type-arg]
        rows = list(
            (await session.execute(
                select(model_cls).order_by(model_cls.id)
                .offset((page - 1) * page_size).limit(page_size)
            )).scalars().all()
        )
        total = int(
            await session.scalar(select(func.count()).select_from(model_cls)) or 0
        )
        return Paginated(
            items=[schema_cls.model_validate(r) for r in rows],
            total=total, page=page, page_size=page_size,
        )

    return handler


# ════════════════════════════════════════════════════════════
# Tenancy
# ════════════════════════════════════════════════════════════


class TenantGroupRead(StrictModel):
    id: uuid.UUID
    name: str
    description: str | None
    parent_id: uuid.UUID | None


class TenantGroupWrite(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    description: Annotated[str | None, Field(max_length=1024)] = None
    parent_id: uuid.UUID | None = None


class TenantRead(StrictModel):
    id: uuid.UUID
    name: str
    slug: str
    group_id: uuid.UUID | None
    description: str | None


class TenantWrite(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    # slug 可不填，後端會由 name 自動產生（小寫、非英數轉 -）
    slug: Annotated[str | None, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")] = None
    group_id: uuid.UUID | None = None
    description: Annotated[str | None, Field(max_length=1024)] = None


@router.get("/tenant-groups", response_model=Paginated[TenantGroupRead])
async def list_tenant_groups(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(1, ge=1, le=10_000), page_size: int = Query(50, ge=1, le=500),
) -> Paginated[TenantGroupRead]:
    rows = list((await session.execute(
        select(TenantGroup).order_by(TenantGroup.name)
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    total = int(await session.scalar(select(func.count()).select_from(TenantGroup)) or 0)
    return Paginated[TenantGroupRead](
        items=[TenantGroupRead.model_validate(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.post("/tenant-groups", response_model=TenantGroupRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_tenant_group(
    payload: TenantGroupWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TenantGroupRead:
    obj = TenantGroup(**payload.model_dump())
    session.add(obj)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="Conflict") from exc
    await _audit(session, user=user, request=request, object_type="tenant_group",
                 object_id=str(obj.id), action="create",
                 diff=payload.model_dump(mode="json"))
    await session.commit()
    await session.refresh(obj)
    return TenantGroupRead.model_validate(obj)


@router.delete("/tenant-groups/{tg_id}", status_code=204,
               dependencies=[Depends(require_admin)])
async def delete_tenant_group(
    tg_id: uuid.UUID, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    obj = await session.get(TenantGroup, tg_id)
    if obj is None:
        raise HTTPException(404, detail="Not found")
    await _audit(session, user=user, request=request, object_type="tenant_group",
                 object_id=str(obj.id), action="delete", diff={"name": obj.name})
    await session.delete(obj)
    await session.commit()


@router.get("/tenants", response_model=Paginated[TenantRead])
async def list_tenants(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(1, ge=1, le=10_000), page_size: int = Query(50, ge=1, le=500),
) -> Paginated[TenantRead]:
    rows = list((await session.execute(
        select(Tenant).order_by(Tenant.name)
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    total = int(await session.scalar(select(func.count()).select_from(Tenant)) or 0)
    return Paginated[TenantRead](
        items=[TenantRead.model_validate(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.post("/tenants", response_model=TenantRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_tenant(
    payload: TenantWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TenantRead:
    data = payload.model_dump()
    if not data.get("slug"):
        data["slug"] = _slugify(data["name"])
    obj = Tenant(**data)
    session.add(obj)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="Tenant slug/name conflict") from exc
    await _audit(session, user=user, request=request, object_type="tenant",
                 object_id=str(obj.id), action="create",
                 diff=payload.model_dump(mode="json"))
    await session.commit()
    await session.refresh(obj)
    return TenantRead.model_validate(obj)


@router.delete("/tenants/{t_id}", status_code=204,
               dependencies=[Depends(require_admin)])
async def delete_tenant(
    t_id: uuid.UUID, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    obj = await session.get(Tenant, t_id)
    if obj is None:
        raise HTTPException(404, detail="Not found")
    await _audit(session, user=user, request=request, object_type="tenant",
                 object_id=str(obj.id), action="delete", diff={"name": obj.name})
    await session.delete(obj)
    await session.commit()


# ════════════════════════════════════════════════════════════
# Contacts
# ════════════════════════════════════════════════════════════


class ContactRead(StrictModel):
    id: uuid.UUID
    name: str
    title: str | None
    phone: str | None
    email: str | None
    address: str | None
    description: str | None
    group_id: uuid.UUID | None
    tenant_id: uuid.UUID | None


class ContactWrite(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    title: Annotated[str | None, Field(max_length=128)] = None
    phone: Annotated[str | None, Field(max_length=64)] = None
    email: EmailStr | None = None
    address: Annotated[str | None, Field(max_length=1024)] = None
    description: Annotated[str | None, Field(max_length=1024)] = None
    group_id: uuid.UUID | None = None
    tenant_id: uuid.UUID | None = None


class ContactAssignmentRead(StrictModel):
    id: uuid.UUID
    contact_id: uuid.UUID
    role_id: uuid.UUID | None
    object_type: str
    object_id: uuid.UUID
    priority: int


class ContactAssignmentWrite(StrictModel):
    contact_id: uuid.UUID
    role_id: uuid.UUID | None = None
    object_type: Annotated[str, Field(min_length=1, max_length=32)]
    object_id: uuid.UUID
    priority: Annotated[int, Field(ge=0, le=100)] = 0


@router.get("/contacts", response_model=Paginated[ContactRead])
async def list_contacts(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(1, ge=1, le=10_000), page_size: int = Query(50, ge=1, le=500),
) -> Paginated[ContactRead]:
    rows = list((await session.execute(
        select(Contact).order_by(Contact.name)
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    total = int(await session.scalar(select(func.count()).select_from(Contact)) or 0)
    return Paginated[ContactRead](
        items=[ContactRead.model_validate(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.post("/contacts", response_model=ContactRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_contact(
    payload: ContactWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ContactRead:
    data = payload.model_dump()
    if data.get("email") is not None:
        data["email"] = str(data["email"])
    obj = Contact(**data)
    session.add(obj)
    await session.flush()
    await _audit(session, user=user, request=request, object_type="contact",
                 object_id=str(obj.id), action="create",
                 diff=payload.model_dump(mode="json"))
    await session.commit()
    await session.refresh(obj)
    return ContactRead.model_validate(obj)


@router.delete("/contacts/{c_id}", status_code=204,
               dependencies=[Depends(require_admin)])
async def delete_contact(
    c_id: uuid.UUID, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    obj = await session.get(Contact, c_id)
    if obj is None:
        raise HTTPException(404, detail="Not found")
    await _audit(session, user=user, request=request, object_type="contact",
                 object_id=str(obj.id), action="delete", diff={"name": obj.name})
    await session.delete(obj)
    await session.commit()


@router.post("/contact-assignments", response_model=ContactAssignmentRead,
             status_code=201, dependencies=[Depends(require_admin)])
async def assign_contact(
    payload: ContactAssignmentWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ContactAssignmentRead:
    obj = ContactAssignment(**payload.model_dump())
    session.add(obj)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="Assignment already exists") from exc
    await _audit(session, user=user, request=request,
                 object_type="contact_assignment",
                 object_id=str(obj.id), action="create",
                 diff=payload.model_dump(mode="json"))
    await session.commit()
    await session.refresh(obj)
    return ContactAssignmentRead.model_validate(obj)


@router.get("/contact-assignments", response_model=list[ContactAssignmentRead])
async def list_assignments(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    object_type: str | None = Query(None),
    object_id: uuid.UUID | None = Query(None),
) -> list[ContactAssignmentRead]:
    stmt = select(ContactAssignment)
    if object_type:
        stmt = stmt.where(ContactAssignment.object_type == object_type)
    if object_id is not None:
        stmt = stmt.where(ContactAssignment.object_id == object_id)
    rows = list((await session.execute(stmt.limit(500))).scalars().all())
    return [ContactAssignmentRead.model_validate(r) for r in rows]


# ════════════════════════════════════════════════════════════
# ASN
# ════════════════════════════════════════════════════════════


class ASNRead(StrictModel):
    id: uuid.UUID
    asn: int
    rir: str | None
    description: str | None
    tenant_id: uuid.UUID | None


class ASNWrite(StrictModel):
    asn: Annotated[int, Field(ge=1, le=4_294_967_294)]
    rir: Annotated[str | None, Field(max_length=16)] = None
    description: Annotated[str | None, Field(max_length=1024)] = None
    tenant_id: uuid.UUID | None = None


@router.get("/asns", response_model=Paginated[ASNRead])
async def list_asns(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(1, ge=1, le=10_000), page_size: int = Query(50, ge=1, le=500),
) -> Paginated[ASNRead]:
    rows = list((await session.execute(
        select(ASN).order_by(ASN.asn)
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    total = int(await session.scalar(select(func.count()).select_from(ASN)) or 0)
    return Paginated[ASNRead](
        items=[ASNRead.model_validate(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.post("/asns", response_model=ASNRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_asn(
    payload: ASNWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ASNRead:
    obj = ASN(**payload.model_dump())
    session.add(obj)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="ASN conflict") from exc
    await _audit(session, user=user, request=request, object_type="asn",
                 object_id=str(obj.id), action="create",
                 diff=payload.model_dump(mode="json"))
    await session.commit()
    await session.refresh(obj)
    return ASNRead.model_validate(obj)


@router.delete("/asns/{a_id}", status_code=204,
               dependencies=[Depends(require_admin)])
async def delete_asn(
    a_id: uuid.UUID, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    obj = await session.get(ASN, a_id)
    if obj is None:
        raise HTTPException(404, detail="Not found")
    await _audit(session, user=user, request=request, object_type="asn",
                 object_id=str(obj.id), action="delete", diff={"asn": obj.asn})
    await session.delete(obj)
    await session.commit()


# ════════════════════════════════════════════════════════════
# Circuits
# ════════════════════════════════════════════════════════════


class ProviderRead(StrictModel):
    id: uuid.UUID
    name: str
    asn: int | None
    account_number: str | None
    portal_url: str | None
    noc_contact: str | None
    description: str | None


class ProviderWrite(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    asn: Annotated[int | None, Field(ge=1, le=4_294_967_294)] = None
    account_number: Annotated[str | None, Field(max_length=128)] = None
    portal_url: Annotated[str | None, Field(max_length=512)] = None
    noc_contact: Annotated[str | None, Field(max_length=512)] = None
    description: Annotated[str | None, Field(max_length=1024)] = None


class CircuitRead(StrictModel):
    id: uuid.UUID
    cid: str
    provider_id: uuid.UUID
    type_id: uuid.UUID | None
    status: str
    monthly_fee_cents: int | None
    commit_rate_kbps: int | None
    up_kbps: int | None = None
    down_kbps: int | None = None
    install_date: datetime | None = None
    contract_end_date: datetime | None = None
    ip_address: str | None = None
    gateway: str | None = None
    netmask: str | None = None
    dns_servers: str | None = None
    device_id: uuid.UUID | None = None
    description: str | None


class CircuitWrite(StrictModel):
    cid: Annotated[str, Field(min_length=1, max_length=128)]
    provider_id: uuid.UUID
    type_id: uuid.UUID | None = None
    status: str = "active"
    monthly_fee_cents: Annotated[int | None, Field(ge=0)] = None
    commit_rate_kbps: Annotated[int | None, Field(ge=0)] = None
    up_kbps: Annotated[int | None, Field(ge=0)] = None
    down_kbps: Annotated[int | None, Field(ge=0)] = None
    install_date: datetime | None = None
    contract_end_date: datetime | None = None
    ip_address: Annotated[str | None, Field(max_length=64)] = None
    gateway: Annotated[str | None, Field(max_length=64)] = None
    netmask: Annotated[str | None, Field(max_length=64)] = None
    dns_servers: Annotated[str | None, Field(max_length=1024)] = None
    device_id: uuid.UUID | None = None
    description: Annotated[str | None, Field(max_length=1024)] = None


class CircuitTypeRead(StrictModel):
    id: uuid.UUID
    name: str
    description: str | None


class ContactGroupRead(StrictModel):
    id: uuid.UUID
    name: str
    description: str | None
    parent_id: uuid.UUID | None


@router.get("/circuit-types", response_model=Paginated[CircuitTypeRead])
async def list_circuit_types(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(1, ge=1, le=10_000), page_size: int = Query(50, ge=1, le=500),
) -> Paginated[CircuitTypeRead]:
    rows = list((await session.execute(
        select(CircuitType).order_by(CircuitType.name)
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    total = int(await session.scalar(select(func.count()).select_from(CircuitType)) or 0)
    return Paginated[CircuitTypeRead](
        items=[CircuitTypeRead.model_validate(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


class CircuitTypeWrite(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=64)]
    description: Annotated[str | None, Field(max_length=1024)] = None


@router.post("/circuit-types", response_model=CircuitTypeRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_circuit_type(
    payload: CircuitTypeWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CircuitTypeRead:
    obj = CircuitType(**payload.model_dump())
    session.add(obj)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="Conflict") from exc
    await _audit(session, user=user, request=request, object_type="circuit_type",
                 object_id=str(obj.id), action="create",
                 diff=payload.model_dump(mode="json"))
    await session.commit()
    await session.refresh(obj)
    return CircuitTypeRead.model_validate(obj)


@router.delete("/circuit-types/{ct_id}", status_code=204,
               dependencies=[Depends(require_admin)])
async def delete_circuit_type(
    ct_id: uuid.UUID, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    obj = await session.get(CircuitType, ct_id)
    if obj is None:
        raise HTTPException(404, detail="Not found")
    # 既有電路若用到此類型，FK 設了 ON DELETE SET NULL，會自動清成「未分類」
    await _audit(session, user=user, request=request, object_type="circuit_type",
                 object_id=str(obj.id), action="delete", diff={"name": obj.name})
    await session.delete(obj)
    await session.commit()


# 內建電路類型（啟動時冪等 seed；表為空才塞，之後使用者可自行增刪）
DEFAULT_CIRCUIT_TYPES: list[tuple[str, str]] = [
    ("光纖專線", "Dedicated fiber / 點對點專線"),
    ("FTTH 光纖到府", "Fiber to the home"),
    ("Metro Ethernet", "都會乙太網路 (EPL/EVPL)"),
    ("MPLS", "MPLS VPN 專線"),
    ("專線 (T1/E1)", "傳統數據專線"),
    ("DSL / VDSL", "電話線寬頻"),
    ("Cable 纜線", "同軸纜線寬頻"),
    ("行動網路 (4G/5G)", "行動 / 無線寬頻"),
    ("衛星", "衛星連線"),
    ("暗光纖", "Dark fiber 裸光纖"),
]


async def seed_default_circuit_types(session: AsyncSession) -> int:
    """表為空時塞入內建電路類型；回傳新增筆數（冪等）。"""
    existing = int(await session.scalar(select(func.count()).select_from(CircuitType)) or 0)
    if existing:
        return 0
    for name, desc in DEFAULT_CIRCUIT_TYPES:
        session.add(CircuitType(name=name, description=desc))
    await session.commit()
    return len(DEFAULT_CIRCUIT_TYPES)


@router.get("/contact-groups", response_model=Paginated[ContactGroupRead])
async def list_contact_groups(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(1, ge=1, le=10_000), page_size: int = Query(50, ge=1, le=500),
) -> Paginated[ContactGroupRead]:
    rows = list((await session.execute(
        select(ContactGroup).order_by(ContactGroup.name)
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    total = int(await session.scalar(select(func.count()).select_from(ContactGroup)) or 0)
    return Paginated[ContactGroupRead](
        items=[ContactGroupRead.model_validate(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


class ContactGroupWrite(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    description: Annotated[str | None, Field(max_length=1024)] = None
    parent_id: uuid.UUID | None = None


@router.post("/contact-groups", response_model=ContactGroupRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_contact_group(
    payload: ContactGroupWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ContactGroupRead:
    obj = ContactGroup(**payload.model_dump())
    session.add(obj)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="Conflict") from exc
    await _audit(session, user=user, request=request, object_type="contact_group",
                 object_id=str(obj.id), action="create",
                 diff=payload.model_dump(mode="json"))
    await session.commit()
    await session.refresh(obj)
    return ContactGroupRead.model_validate(obj)


@router.patch("/contact-groups/{g_id}", response_model=ContactGroupRead,
              dependencies=[Depends(require_admin)])
async def update_contact_group(
    g_id: uuid.UUID, payload: ContactGroupWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ContactGroupRead:
    obj = await session.get(ContactGroup, g_id)
    if obj is None:
        raise HTTPException(404, detail="Not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="Conflict") from exc
    await _audit(session, user=user, request=request, object_type="contact_group",
                 object_id=str(obj.id), action="update",
                 diff=payload.model_dump(mode="json", exclude_unset=True))
    await session.commit()
    await session.refresh(obj)
    return ContactGroupRead.model_validate(obj)


@router.delete("/contact-groups/{g_id}", status_code=204,
               dependencies=[Depends(require_admin)])
async def delete_contact_group(
    g_id: uuid.UUID, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    obj = await session.get(ContactGroup, g_id)
    if obj is None:
        raise HTTPException(404, detail="Not found")
    await _audit(session, user=user, request=request, object_type="contact_group",
                 object_id=str(obj.id), action="delete", diff={"name": obj.name})
    await session.delete(obj)
    await session.commit()


@router.get("/providers", response_model=Paginated[ProviderRead])
async def list_providers(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(1, ge=1, le=10_000), page_size: int = Query(50, ge=1, le=500),
) -> Paginated[ProviderRead]:
    rows = list((await session.execute(
        select(Provider).order_by(Provider.name)
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    total = int(await session.scalar(select(func.count()).select_from(Provider)) or 0)
    return Paginated[ProviderRead](
        items=[ProviderRead.model_validate(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.post("/providers", response_model=ProviderRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_provider(
    payload: ProviderWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProviderRead:
    obj = Provider(**payload.model_dump())
    session.add(obj)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="Provider conflict") from exc
    await _audit(session, user=user, request=request, object_type="provider",
                 object_id=str(obj.id), action="create",
                 diff=payload.model_dump(mode="json"))
    await session.commit()
    await session.refresh(obj)
    return ProviderRead.model_validate(obj)


@router.get("/circuits", response_model=Paginated[CircuitRead])
async def list_circuits(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    provider_id: uuid.UUID | None = Query(None),
    page: int = Query(1, ge=1, le=10_000), page_size: int = Query(50, ge=1, le=500),
) -> Paginated[CircuitRead]:
    stmt = select(Circuit)
    cstmt = select(func.count()).select_from(Circuit)
    if provider_id is not None:
        stmt = stmt.where(Circuit.provider_id == provider_id)
        cstmt = cstmt.where(Circuit.provider_id == provider_id)
    rows = list((await session.execute(
        stmt.order_by(Circuit.cid).offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    total = int(await session.scalar(cstmt) or 0)
    return Paginated[CircuitRead](
        items=[CircuitRead.model_validate(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.post("/circuits", response_model=CircuitRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_circuit(
    payload: CircuitWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CircuitRead:
    obj = Circuit(**payload.model_dump())
    session.add(obj)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="Circuit conflict") from exc
    await _audit(session, user=user, request=request, object_type="circuit",
                 object_id=str(obj.id), action="create",
                 diff=payload.model_dump(mode="json"))
    await session.commit()
    await session.refresh(obj)
    return CircuitRead.model_validate(obj)


# ════════════════════════════════════════════════════════════
# Wireless
# ════════════════════════════════════════════════════════════


class WirelessSSIDRead(StrictModel):
    id: uuid.UUID
    ssid: str
    description: str | None
    auth_type: str | None
    vlan_id: uuid.UUID | None
    tenant_id: uuid.UUID | None


class WirelessSSIDWrite(StrictModel):
    ssid: Annotated[str, Field(min_length=1, max_length=64)]
    description: Annotated[str | None, Field(max_length=1024)] = None
    auth_type: Annotated[str | None, Field(max_length=32)] = None
    vlan_id: uuid.UUID | None = None
    tenant_id: uuid.UUID | None = None


class WirelessLinkRead(StrictModel):
    id: uuid.UUID
    name: str
    a_device_id: uuid.UUID | None
    b_device_id: uuid.UUID | None
    ssid: str | None
    distance_m: int | None
    description: str | None


class WirelessLinkWrite(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    a_device_id: uuid.UUID | None = None
    b_device_id: uuid.UUID | None = None
    ssid: Annotated[str | None, Field(max_length=64)] = None
    distance_m: Annotated[int | None, Field(ge=0, le=100_000)] = None
    description: Annotated[str | None, Field(max_length=1024)] = None


@router.get("/wireless/ssids", response_model=Paginated[WirelessSSIDRead])
async def list_ssids(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(1, ge=1, le=10_000), page_size: int = Query(50, ge=1, le=500),
) -> Paginated[WirelessSSIDRead]:
    rows = list((await session.execute(
        select(WirelessSSID).order_by(WirelessSSID.ssid)
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    total = int(await session.scalar(select(func.count()).select_from(WirelessSSID)) or 0)
    return Paginated[WirelessSSIDRead](
        items=[WirelessSSIDRead.model_validate(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.post("/wireless/ssids", response_model=WirelessSSIDRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_ssid(
    payload: WirelessSSIDWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> WirelessSSIDRead:
    obj = WirelessSSID(**payload.model_dump())
    session.add(obj)
    await session.flush()
    await _audit(session, user=user, request=request, object_type="wireless_ssid",
                 object_id=str(obj.id), action="create",
                 diff=payload.model_dump(mode="json"))
    await session.commit()
    await session.refresh(obj)
    return WirelessSSIDRead.model_validate(obj)


@router.get("/wireless/links", response_model=Paginated[WirelessLinkRead])
async def list_wlinks(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(1, ge=1, le=10_000), page_size: int = Query(50, ge=1, le=500),
) -> Paginated[WirelessLinkRead]:
    rows = list((await session.execute(
        select(WirelessLink).order_by(WirelessLink.name)
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    total = int(await session.scalar(select(func.count()).select_from(WirelessLink)) or 0)
    return Paginated[WirelessLinkRead](
        items=[WirelessLinkRead.model_validate(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.post("/wireless/links", response_model=WirelessLinkRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_wlink(
    payload: WirelessLinkWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> WirelessLinkRead:
    obj = WirelessLink(**payload.model_dump())
    session.add(obj)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="Wireless link name conflict") from exc
    await _audit(session, user=user, request=request, object_type="wireless_link",
                 object_id=str(obj.id), action="create",
                 diff=payload.model_dump(mode="json"))
    await session.commit()
    await session.refresh(obj)
    return WirelessLinkRead.model_validate(obj)


# ════════════════════════════════════════════════════════════
# PATCH（編輯）— 各進階資源；admin only。送來的欄位才更新。
# ════════════════════════════════════════════════════════════


@router.patch("/tenant-groups/{tg_id}", response_model=TenantGroupRead,
              dependencies=[Depends(require_admin)])
async def update_tenant_group(
    tg_id: uuid.UUID, payload: TenantGroupWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TenantGroupRead:
    obj = await _patch_resource(session, model=TenantGroup, obj_id=tg_id,
                                payload=payload, object_type="tenant_group", user=user, request=request)
    return TenantGroupRead.model_validate(obj)


@router.patch("/tenants/{t_id}", response_model=TenantRead,
              dependencies=[Depends(require_admin)])
async def update_tenant(
    t_id: uuid.UUID, payload: TenantWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TenantRead:
    obj = await _patch_resource(session, model=Tenant, obj_id=t_id,
                                payload=payload, object_type="tenant", user=user, request=request)
    return TenantRead.model_validate(obj)


@router.patch("/contacts/{c_id}", response_model=ContactRead,
              dependencies=[Depends(require_admin)])
async def update_contact(
    c_id: uuid.UUID, payload: ContactWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ContactRead:
    obj = await _patch_resource(session, model=Contact, obj_id=c_id,
                                payload=payload, object_type="contact", user=user, request=request)
    return ContactRead.model_validate(obj)


@router.patch("/asns/{a_id}", response_model=ASNRead,
              dependencies=[Depends(require_admin)])
async def update_asn(
    a_id: uuid.UUID, payload: ASNWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ASNRead:
    obj = await _patch_resource(session, model=ASN, obj_id=a_id,
                                payload=payload, object_type="asn", user=user, request=request)
    return ASNRead.model_validate(obj)


@router.patch("/providers/{p_id}", response_model=ProviderRead,
              dependencies=[Depends(require_admin)])
async def update_provider(
    p_id: uuid.UUID, payload: ProviderWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProviderRead:
    obj = await _patch_resource(session, model=Provider, obj_id=p_id,
                                payload=payload, object_type="provider", user=user, request=request)
    return ProviderRead.model_validate(obj)


@router.patch("/circuits/{c_id}", response_model=CircuitRead,
              dependencies=[Depends(require_admin)])
async def update_circuit(
    c_id: uuid.UUID, payload: CircuitWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CircuitRead:
    obj = await _patch_resource(session, model=Circuit, obj_id=c_id,
                                payload=payload, object_type="circuit", user=user, request=request)
    return CircuitRead.model_validate(obj)


@router.patch("/circuit-types/{ct_id}", response_model=CircuitTypeRead,
              dependencies=[Depends(require_admin)])
async def update_circuit_type(
    ct_id: uuid.UUID, payload: CircuitTypeWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CircuitTypeRead:
    obj = await _patch_resource(session, model=CircuitType, obj_id=ct_id,
                                payload=payload, object_type="circuit_type", user=user, request=request)
    return CircuitTypeRead.model_validate(obj)


@router.patch("/wireless/ssids/{s_id}", response_model=WirelessSSIDRead,
              dependencies=[Depends(require_admin)])
async def update_ssid(
    s_id: uuid.UUID, payload: WirelessSSIDWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> WirelessSSIDRead:
    obj = await _patch_resource(session, model=WirelessSSID, obj_id=s_id,
                                payload=payload, object_type="wireless_ssid", user=user, request=request)
    return WirelessSSIDRead.model_validate(obj)


@router.patch("/wireless/links/{l_id}", response_model=WirelessLinkRead,
              dependencies=[Depends(require_admin)])
async def update_wlink(
    l_id: uuid.UUID, payload: WirelessLinkWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> WirelessLinkRead:
    obj = await _patch_resource(session, model=WirelessLink, obj_id=l_id,
                                payload=payload, object_type="wireless_link", user=user, request=request)
    return WirelessLinkRead.model_validate(obj)


# 補上原本缺的 DELETE（前端「進階」各資源都有刪除鈕，缺端點會回 405 Method Not Allowed）
@router.delete("/providers/{p_id}", status_code=204, dependencies=[Depends(require_admin)])
async def delete_provider(
    p_id: uuid.UUID, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    await _delete_resource(session, model=Provider, obj_id=p_id,
                           object_type="provider", user=user, request=request)


@router.delete("/circuits/{c_id}", status_code=204, dependencies=[Depends(require_admin)])
async def delete_circuit(
    c_id: uuid.UUID, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    await _delete_resource(session, model=Circuit, obj_id=c_id,
                           object_type="circuit", user=user, request=request)


@router.delete("/wireless/ssids/{s_id}", status_code=204, dependencies=[Depends(require_admin)])
async def delete_ssid(
    s_id: uuid.UUID, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    await _delete_resource(session, model=WirelessSSID, obj_id=s_id,
                           object_type="wireless_ssid", user=user, request=request)


@router.delete("/wireless/links/{l_id}", status_code=204, dependencies=[Depends(require_admin)])
async def delete_wlink(
    l_id: uuid.UUID, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    await _delete_resource(session, model=WirelessLink, obj_id=l_id,
                           object_type="wireless_link", user=user, request=request)
