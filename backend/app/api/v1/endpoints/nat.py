"""NAT endpoints。"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin, require_global_read
from app.core.audit import append_audit
from app.core.db import get_session
from app.models.firewall import OPNsenseFirewall
from app.models.nat import NATTranslation
from app.schemas.base import Paginated, StrictModel
from app.schemas.nat import NATCreate, NATRead, NATUpdate


class _NATBulkDeletePayload(StrictModel):
    ids: list[uuid.UUID]

router = APIRouter(prefix="/nat", tags=["nat"], dependencies=[Depends(require_global_read)])


def _parse_origin(
    origin: str | None,
    fw_names: dict[uuid.UUID, str],
) -> tuple[str, uuid.UUID | None, str]:
    """source_origin → (kind, firewall_id, label)。"""
    if not origin:
        return "manual", None, "手動"
    if origin == "phpipam":
        return "phpipam", None, "phpIPAM"
    if origin.startswith("opnsense:"):
        try:
            fw_id = uuid.UUID(origin.split(":", 1)[1])
        except ValueError:
            return "opnsense", None, "OPNsense (unknown)"
        name = fw_names.get(fw_id) or "unknown"
        return "opnsense", fw_id, f"OPNsense: {name}"
    if origin.startswith("pfsense:"):
        try:
            fw_id = uuid.UUID(origin.split(":", 1)[1])
        except ValueError:
            return "pfsense", None, "pfSense (unknown)"
        name = fw_names.get(fw_id) or "unknown"
        return "pfsense", fw_id, f"pfSense: {name}"
    if origin.startswith("mikrotik:"):
        try:
            fw_id = uuid.UUID(origin.split(":", 1)[1])
        except ValueError:
            return "mikrotik", None, "MikroTik (unknown)"
        name = fw_names.get(fw_id) or "unknown"
        return "mikrotik", fw_id, f"MikroTik: {name}"
    if origin.startswith("paloalto:"):
        try:
            fw_id = uuid.UUID(origin.split(":", 1)[1])
        except ValueError:
            return "paloalto", None, "Palo Alto (unknown)"
        name = fw_names.get(fw_id) or "unknown"
        return "paloalto", fw_id, f"Palo Alto: {name}"
    if origin.startswith("checkpoint:"):
        try:
            fw_id = uuid.UUID(origin.split(":", 1)[1])
        except ValueError:
            return "checkpoint", None, "Check Point (unknown)"
        name = fw_names.get(fw_id) or "unknown"
        return "checkpoint", fw_id, f"Check Point: {name}"
    if origin.startswith("fortigate:"):
        try:
            fw_id = uuid.UUID(origin.split(":", 1)[1])
        except ValueError:
            return "fortigate", None, "FortiGate (unknown)"
        name = fw_names.get(fw_id) or "unknown"
        return "fortigate", fw_id, f"FortiGate: {name}"
    return origin, None, origin


@router.get("", response_model=Paginated[NATRead])
async def list_nat(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    type: str | None = Query(None),
    device_id: uuid.UUID | None = Query(None),
    ip_id: uuid.UUID | None = Query(None, description="篩選 src 或 dst 指向此 IP 的規則"),
    source_kind: list[str] | None = Query(None, description="可複選：opnsense | pfsense | fortigate | paloalto | checkpoint | mikrotik | phpipam | manual"),
    source_firewall_id: uuid.UUID | None = Query(None),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=500),
) -> Paginated[NATRead]:
    stmt = select(NATTranslation)
    cstmt = select(func.count()).select_from(NATTranslation)
    if type is not None:
        stmt = stmt.where(NATTranslation.type == type)
        cstmt = cstmt.where(NATTranslation.type == type)
    if device_id is not None:
        stmt = stmt.where(NATTranslation.device_id == device_id)
        cstmt = cstmt.where(NATTranslation.device_id == device_id)
    if ip_id is not None:
        from sqlalchemy import or_ as _or_ip
        ipc = _or_ip(NATTranslation.src_ip_id == ip_id, NATTranslation.dst_ip_id == ip_id)
        stmt = stmt.where(ipc)
        cstmt = cstmt.where(ipc)
    # 來源可複選：phpipam / manual / opnsense / pfsense（OR）
    kinds = {k for k in (source_kind or [])
             if k in ("phpipam", "manual", "opnsense", "pfsense", "fortigate", "paloalto",
                      "checkpoint", "mikrotik")}
    if kinds:
        from sqlalchemy import or_
        conds = []
        if "phpipam" in kinds:
            conds.append(NATTranslation.source_origin == "phpipam")
        if "manual" in kinds:
            conds.append(NATTranslation.source_origin.is_(None))
        if "opnsense" in kinds:
            if source_firewall_id is not None and kinds == {"opnsense"}:
                conds.append(NATTranslation.source_origin == f"opnsense:{source_firewall_id}")
            else:
                conds.append(NATTranslation.source_origin.like("opnsense:%"))
        if "pfsense" in kinds:
            if source_firewall_id is not None and kinds == {"pfsense"}:
                conds.append(NATTranslation.source_origin == f"pfsense:{source_firewall_id}")
            else:
                conds.append(NATTranslation.source_origin.like("pfsense:%"))
        if "paloalto" in kinds:
            if source_firewall_id is not None and kinds == {"paloalto"}:
                conds.append(NATTranslation.source_origin == f"paloalto:{source_firewall_id}")
            else:
                conds.append(NATTranslation.source_origin.like("paloalto:%"))
        if "checkpoint" in kinds:
            if source_firewall_id is not None and kinds == {"checkpoint"}:
                conds.append(NATTranslation.source_origin == f"checkpoint:{source_firewall_id}")
            else:
                conds.append(NATTranslation.source_origin.like("checkpoint:%"))
        if "mikrotik" in kinds:
            if source_firewall_id is not None and kinds == {"mikrotik"}:
                conds.append(NATTranslation.source_origin == f"mikrotik:{source_firewall_id}")
            else:
                conds.append(NATTranslation.source_origin.like("mikrotik:%"))
        if "fortigate" in kinds:
            if source_firewall_id is not None and kinds == {"fortigate"}:
                conds.append(NATTranslation.source_origin == f"fortigate:{source_firewall_id}")
            else:
                conds.append(NATTranslation.source_origin.like("fortigate:%"))
        clause = or_(*conds)
        stmt = stmt.where(clause)
        cstmt = cstmt.where(clause)
    stmt = stmt.order_by(NATTranslation.name).offset((page - 1) * page_size).limit(page_size)
    rows = list((await session.execute(stmt)).scalars().all())
    total = int(await session.scalar(cstmt) or 0)

    fw_rows = (await session.execute(select(OPNsenseFirewall.id, OPNsenseFirewall.name))).all()
    fw_names = {r[0]: r[1] for r in fw_rows}
    from app.models.pfsense import PfSenseFirewall
    pf_rows = (await session.execute(select(PfSenseFirewall.id, PfSenseFirewall.name))).all()
    fw_names.update({r[0]: r[1] for r in pf_rows})
    from app.models.fortigate import FortiGateFirewall
    fg_rows = (await session.execute(select(FortiGateFirewall.id, FortiGateFirewall.name))).all()
    fw_names.update({r[0]: r[1] for r in fg_rows})
    from app.models.paloalto import PaloAltoFirewall
    pa_rows = (await session.execute(select(PaloAltoFirewall.id, PaloAltoFirewall.name))).all()
    fw_names.update({r[0]: r[1] for r in pa_rows})
    from app.models.checkpoint import CheckPointServer
    cp_rows = (await session.execute(select(CheckPointServer.id, CheckPointServer.name))).all()
    fw_names.update({r[0]: r[1] for r in cp_rows})
    from app.models.mikrotik import MikroTikRouter
    mt_rows = (await session.execute(select(MikroTikRouter.id, MikroTikRouter.name))).all()
    fw_names.update({r[0]: r[1] for r in mt_rows})

    from app.core.sqlin import in_values
    from app.models.address import IPAddress
    ip_ids = {i for r in rows for i in (r.src_ip_id, r.dst_ip_id) if i is not None}
    ip_text: dict[uuid.UUID, str] = {}
    if ip_ids:
        for iid, ip in (await session.execute(
                select(IPAddress.id, IPAddress.ip).where(in_values(IPAddress.id, ip_ids)))).all():
            ip_text[iid] = str(ip).split("/")[0]

    items: list[NATRead] = []
    for r in rows:
        kind, fw_id, label = _parse_origin(r.source_origin, fw_names)
        m = NATRead.model_validate(r)
        m.src_ip = ip_text.get(r.src_ip_id) if r.src_ip_id else None
        m.dst_ip = ip_text.get(r.dst_ip_id) if r.dst_ip_id else None
        m.source_kind = kind
        m.source_firewall_id = fw_id
        m.source_label = label
        m.source_firewall_name = fw_names.get(fw_id) if fw_id else None
        items.append(m)
    return Paginated[NATRead](items=items, total=total, page=page, page_size=page_size)


@router.get("/{nat_id}", response_model=NATRead)
async def get_nat(
    nat_id: uuid.UUID,
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> NATRead:
    obj = await session.get(NATTranslation, nat_id)
    if obj is None:
        raise HTTPException(404, detail="NAT not found")
    return NATRead.model_validate(obj)


@router.post("", response_model=NATRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_nat(
    payload: NATCreate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> NATRead:
    obj = NATTranslation(**payload.model_dump())
    session.add(obj)
    await session.flush()
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="nat", object_id=str(obj.id), action="create",
        diff={"after": payload.model_dump(mode="json")},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return NATRead.model_validate(obj)


@router.patch("/{nat_id}", response_model=NATRead,
              dependencies=[Depends(require_admin)])
async def update_nat(
    nat_id: uuid.UUID,
    payload: NATUpdate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> NATRead:
    obj = await session.get(NATTranslation, nat_id)
    if obj is None:
        raise HTTPException(404, detail="NAT not found")
    before = {"name": obj.name, "type": obj.type, "protocol": obj.protocol}
    changes = payload.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(obj, k, v)
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="nat", object_id=str(obj.id), action="update",
        diff={"before": before, "changes": changes},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return NATRead.model_validate(obj)


@router.delete("/{nat_id}", status_code=204, dependencies=[Depends(require_admin)])
async def delete_nat(
    nat_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    obj = await session.get(NATTranslation, nat_id)
    if obj is None:
        raise HTTPException(404, detail="NAT not found")
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="nat", object_id=str(obj.id), action="delete",
        diff={"before": {"name": obj.name, "type": obj.type}},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.delete(obj)
    await session.commit()


@router.post("/bulk-delete", dependencies=[Depends(require_admin)])
async def bulk_delete_nat(
    payload: _NATBulkDeletePayload,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    if not payload.ids:
        return {"deleted": 0, "failed": 0, "errors": []}
    if len(payload.ids) > 500:
        raise HTTPException(400, detail="too many ids (max 500)")
    deleted = 0
    errors: list[dict[str, str]] = []
    actor_ip = request.client.host if request.client else None
    actor_ua = request.headers.get("user-agent")
    request_id = getattr(request.state, "request_id", None)
    for nid in payload.ids:
        obj = await session.get(NATTranslation, nid)
        if obj is None:
            errors.append({"id": str(nid), "error": "not_found"}); continue
        await append_audit(
            session, actor_user_id=str(user.id),
            actor_ip=actor_ip, actor_user_agent=actor_ua,
            object_type="nat", object_id=str(obj.id), action="delete",
            diff={"before": {"name": obj.name, "type": obj.type}, "bulk": True},
            request_id=request_id,
        )
        await session.delete(obj)
        deleted += 1
    await session.commit()
    return {"deleted": deleted, "failed": len(errors), "errors": errors[:50]}
