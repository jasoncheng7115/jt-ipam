"""Customer / 管理單位 endpoints。"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin, require_object_perm
from app.core.audit import append_audit
from app.core.db import get_session
from app.models.customer import Customer
from app.schemas.base import Paginated
from app.schemas.customer import CustomerCreate, CustomerRead, CustomerUpdate

router = APIRouter(prefix="/customers", tags=["customers"])


@router.get("", response_model=Paginated[CustomerRead])
async def list_customers(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    q: str | None = Query(None, description="搜尋 name / title"),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=500),
) -> Paginated[CustomerRead]:
    stmt = select(Customer)
    cstmt = select(func.count()).select_from(Customer)
    if q:
        like = f"%{q}%"
        cond = Customer.name.ilike(like) | Customer.title.ilike(like)
        stmt = stmt.where(cond)
        cstmt = cstmt.where(cond)
    from app.services.permission import visible_ids
    vis = await visible_ids(session, user=_user, object_type="customer")
    if vis is not None:
        stmt = stmt.where(Customer.id.in_(vis)); cstmt = cstmt.where(Customer.id.in_(vis))
    stmt = stmt.order_by(Customer.name).offset((page - 1) * page_size).limit(page_size)
    rows = list((await session.execute(stmt)).scalars().all())
    total = int(await session.scalar(cstmt) or 0)

    # 每個客戶的子網路數（一次 group by 撈本頁）
    from app.models.subnet import Subnet
    cust_ids = [r.id for r in rows]
    counts: dict[Any, Any] = {}
    if cust_ids:
        for cid, n in (await session.execute(
            select(Subnet.customer_id, func.count())
            .where(Subnet.customer_id.in_(cust_ids))
            .group_by(Subnet.customer_id)
        )).all():
            counts[cid] = n

    items = []
    for r in rows:
        m = CustomerRead.model_validate(r)
        m.subnet_count = counts.get(r.id, 0)
        items.append(m)
    return Paginated[CustomerRead](
        items=items, total=total, page=page, page_size=page_size,
    )


@router.get(
    "/{cid}",
    response_model=CustomerRead,
    dependencies=[Depends(require_object_perm("customer", "read", path_param="cid"))],
)
async def get_customer(
    cid: uuid.UUID,
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CustomerRead:
    obj = await session.get(Customer, cid)
    if obj is None:
        raise HTTPException(404, detail="Customer not found")
    return CustomerRead.model_validate(obj)


@router.get(
    "/{cid}/summary",
    dependencies=[Depends(require_object_perm("customer", "read", path_param="cid"))],
)
async def customer_summary(
    cid: uuid.UUID,
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    """客戶旗下所有資源彙整：sections / subnets / devices / IPs 數量 + 列表（前 50）。"""
    from app.models.address import IPAddress
    from app.models.device import Device
    from app.models.section import Section
    from app.models.subnet import Subnet

    obj = await session.get(Customer, cid)
    if obj is None:
        raise HTTPException(404, detail="Customer not found")

    secs = (await session.execute(
        select(Section).where(Section.customer_id == cid).order_by(Section.name).limit(50)
    )).scalars().all()
    subs = (await session.execute(
        select(Subnet).where(Subnet.customer_id == cid).order_by(Subnet.cidr).limit(50)
    )).scalars().all()
    devs = (await session.execute(
        select(Device).where(Device.customer_id == cid).order_by(Device.name).limit(50)
    )).scalars().all()
    ips = (await session.execute(
        select(IPAddress).where(IPAddress.customer_id == cid).order_by(IPAddress.ip).limit(50)
    )).scalars().all()

    # counts
    sec_count = int(await session.scalar(
        select(func.count()).select_from(Section).where(Section.customer_id == cid)
    ) or 0)
    sub_count = int(await session.scalar(
        select(func.count()).select_from(Subnet).where(Subnet.customer_id == cid)
    ) or 0)
    dev_count = int(await session.scalar(
        select(func.count()).select_from(Device).where(Device.customer_id == cid)
    ) or 0)
    ip_count = int(await session.scalar(
        select(func.count()).select_from(IPAddress).where(IPAddress.customer_id == cid)
    ) or 0)

    return {
        "customer": CustomerRead.model_validate(obj).model_dump(mode="json"),
        "counts": {
            "sections": sec_count, "subnets": sub_count,
            "devices": dev_count, "ip_addresses": ip_count,
        },
        "sections": [{"id": str(s.id), "name": s.name, "description": s.description} for s in secs],
        "subnets": [{"id": str(s.id), "cidr": str(s.cidr), "description": s.description} for s in subs],
        "devices": [{"id": str(d.id), "name": d.name, "type": d.type} for d in devs],
        "ip_addresses": [
            {"id": str(i.id), "ip": str(i.ip).split("/")[0], "hostname": i.hostname,
             "subnet_id": str(i.subnet_id)}
            for i in ips
        ],
    }


@router.post("", response_model=CustomerRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_customer(
    payload: CustomerCreate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CustomerRead:
    obj = Customer(**payload.model_dump())
    session.add(obj)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, detail="customer name already exists") from None
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="customer", object_id=str(obj.id), action="create",
        diff={"after": payload.model_dump(mode="json")},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return CustomerRead.model_validate(obj)


@router.patch("/{cid}", response_model=CustomerRead,
              dependencies=[Depends(require_admin)])
async def update_customer(
    cid: uuid.UUID,
    payload: CustomerUpdate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CustomerRead:
    obj = await session.get(Customer, cid)
    if obj is None:
        raise HTTPException(404, detail="Customer not found")
    before = {k: getattr(obj, k) for k in payload.model_dump(exclude_unset=True)}
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, detail="customer name already exists") from None
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="customer", object_id=str(obj.id), action="update",
        diff={"before": before, "after": payload.model_dump(exclude_unset=True, mode="json")},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return CustomerRead.model_validate(obj)


@router.delete("/{cid}", status_code=204,
               dependencies=[Depends(require_admin)])
async def delete_customer(
    cid: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    obj = await session.get(Customer, cid)
    if obj is None:
        raise HTTPException(404, detail="Customer not found")
    snapshot = {"name": obj.name, "title": obj.title}
    await session.delete(obj)
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="customer", object_id=str(cid), action="delete",
        diff={"before": snapshot},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
