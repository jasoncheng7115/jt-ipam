"""電路詳細資料：聯絡人角色與指派、WAN 位址（關聯到 IPAM）、租用設備（客戶許願清單 2026-10-28）。

電路屬全域基礎設施：讀取要全域讀取、寫入只限管理員（跟 `advanced.py` 的電路 CRUD 一致）。
WAN 位址指到的子網路與 IP 仍照子網路權限過濾：看不到的那一筆只回 `hidden`，不透露位址。
租用設備掛在裝置上：讀取照裝置權限，寫入只限管理員（內容是供應商與電路，屬全域資料）。
"""
from __future__ import annotations

import ipaddress
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import Field
from sqlalchemy import case, func, select
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
from app.core.sqlin import in_values
from app.core.ui_error import ui_detail
from app.models.address import IPAddress
from app.models.advanced import (
    Circuit,
    CircuitAddress,
    CircuitType,
    Contact,
    ContactAssignment,
    ContactRole,
    DeviceLease,
    Provider,
)
from app.models.device import Device
from app.models.nat import NATTranslation
from app.models.subnet import Subnet
from app.schemas.base import Paginated, StrictModel
from app.services.circuit_links import circuits_carrying

router = APIRouter(tags=["advanced"], dependencies=[Depends(require_global_read)])
device_router = APIRouter(tags=["devices"])

# 「最近使用」的時間窗與來源欄位：跟 IP 變更評估的「舊位址仍有設備在用」一致
ACTIVITY_WINDOW = timedelta(days=7)
_SEEN_FIELDS = ("last_seen_scanner", "last_seen_librenms", "last_seen_arp", "last_seen_wazuh",
                "last_seen_zabbix", "last_seen_checkmk", "last_seen_ocs")
# 一段子網路最多逐一列出幾個已登記的位址（ISP 給的多半是 /32～/27；誤掛大網段時不要整片撈）
MAX_IPS_PER_SUBNET = 256


async def _audit(session: AsyncSession, *, user: Any, request: Request, object_type: str,
                 object_id: str | None, action: str, diff: dict[str, Any] | None) -> None:
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type=object_type, object_id=object_id, action=action, diff=diff,
        request_id=getattr(request.state, "request_id", None),
    )


# ════════════════════════════════════════════════════════════
# 聯絡人角色
# ════════════════════════════════════════════════════════════


class ContactRoleRead(StrictModel):
    id: uuid.UUID
    name: str
    description: str | None
    builtin_key: str | None


class ContactRoleWrite(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=64)]
    description: Annotated[str | None, Field(max_length=1024)] = None


BUILTIN_ROLES = ("sales", "onsite", "noc", "billing")


async def ensure_builtin_roles(session: AsyncSession) -> None:
    """內建角色缺了就補（migration 0232 建過，但系統匯入、測試清表之後可能不在）。"""
    have = set((await session.execute(
        select(ContactRole.builtin_key).where(ContactRole.builtin_key.is_not(None)))).scalars())
    missing = [k for k in BUILTIN_ROLES if k not in have]
    if not missing:
        return
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    for k in missing:
        await session.execute(pg_insert(ContactRole).values(name=k, builtin_key=k)
                              .on_conflict_do_update(index_elements=[ContactRole.name], set_={"builtin_key": k}))
    await session.commit()


@router.get("/contact-roles", response_model=Paginated[ContactRoleRead])
async def list_contact_roles(
    _user: CurrentUser, session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(1, ge=1, le=10_000), page_size: int = Query(200, ge=1, le=500),
) -> Paginated[ContactRoleRead]:
    await ensure_builtin_roles(session)
    rows = list((await session.execute(
        select(ContactRole).order_by(ContactRole.builtin_key.is_(None), ContactRole.name)
        .offset((page - 1) * page_size).limit(page_size))).scalars().all())
    total = int(await session.scalar(select(func.count()).select_from(ContactRole)) or 0)
    return Paginated[ContactRoleRead](items=[ContactRoleRead.model_validate(r) for r in rows],
                                      total=total, page=page, page_size=page_size)


@router.post("/contact-roles", response_model=ContactRoleRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_contact_role(
    payload: ContactRoleWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ContactRoleRead:
    obj = ContactRole(name=payload.name.strip(), description=payload.description)
    session.add(obj)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail=ui_detail("contact_role_exists", "已經有同名的角色",
                                                  name=payload.name)) from exc
    await _audit(session, user=user, request=request, object_type="contact_role", object_id=str(obj.id),
                 action="create", diff=payload.model_dump(mode="json"))
    await session.commit()
    await session.refresh(obj)
    return ContactRoleRead.model_validate(obj)


@router.delete("/contact-roles/{role_id}", status_code=204, dependencies=[Depends(require_admin)])
async def delete_contact_role(
    role_id: uuid.UUID, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    obj = await session.get(ContactRole, role_id)
    if obj is None:
        raise HTTPException(404, detail="Not found")
    if obj.builtin_key:
        raise HTTPException(409, detail=ui_detail("contact_role_builtin", "內建角色不能刪除"))
    await _audit(session, user=user, request=request, object_type="contact_role", object_id=str(obj.id),
                 action="delete", diff={"name": obj.name})
    await session.delete(obj)
    await session.commit()


# ════════════════════════════════════════════════════════════
# 聯絡人指派（修改、刪除；新增在 advanced.py）
# ════════════════════════════════════════════════════════════


class ContactAssignmentPatch(StrictModel):
    role_id: uuid.UUID | None = None
    priority: Annotated[int | None, Field(ge=0, le=100)] = None


class _AssignmentOut(StrictModel):
    id: uuid.UUID
    contact_id: uuid.UUID
    role_id: uuid.UUID | None
    object_type: str
    object_id: uuid.UUID
    priority: int


@router.patch("/contact-assignments/{asg_id}", response_model=_AssignmentOut,
              dependencies=[Depends(require_admin)])
async def patch_contact_assignment(
    asg_id: uuid.UUID, payload: ContactAssignmentPatch, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> _AssignmentOut:
    obj = await session.get(ContactAssignment, asg_id)
    if obj is None:
        raise HTTPException(404, detail="Not found")
    data = payload.model_dump(exclude_unset=True)
    if "role_id" in data and data["role_id"] is not None and await session.get(ContactRole, data["role_id"]) is None:
        raise HTTPException(404, detail=ui_detail("contact_role_not_found", "找不到這個角色"))
    before = {"role_id": str(obj.role_id) if obj.role_id else None, "priority": obj.priority}
    for k, v in data.items():
        setattr(obj, k, v if k != "priority" or v is not None else obj.priority)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail=ui_detail("contact_assignment_exists", "這位聯絡人已經用這個角色指派過")) from exc
    await _audit(session, user=user, request=request, object_type="contact_assignment", object_id=str(obj.id),
                 action="update", diff={"before": before, "after": payload.model_dump(mode="json", exclude_unset=True)})
    await session.commit()
    await session.refresh(obj)
    return _AssignmentOut.model_validate(obj)


@router.delete("/contact-assignments/{asg_id}", status_code=204, dependencies=[Depends(require_admin)])
async def delete_contact_assignment(
    asg_id: uuid.UUID, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    obj = await session.get(ContactAssignment, asg_id)
    if obj is None:
        raise HTTPException(404, detail="Not found")
    await _audit(session, user=user, request=request, object_type="contact_assignment", object_id=str(obj.id),
                 action="delete", diff={"contact_id": str(obj.contact_id), "object_type": obj.object_type,
                                        "object_id": str(obj.object_id)})
    await session.delete(obj)
    await session.commit()


async def _contacts_of(session: AsyncSession, object_type: str, object_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (await session.execute(
        select(ContactAssignment, Contact, ContactRole)
        .join(Contact, Contact.id == ContactAssignment.contact_id)
        .outerjoin(ContactRole, ContactRole.id == ContactAssignment.role_id)
        .where(ContactAssignment.object_type == object_type, ContactAssignment.object_id == object_id)
        .order_by(ContactAssignment.priority.desc(), Contact.name).limit(200))).all()
    return [{
        "id": str(a.id), "priority": a.priority,
        "contact": {"id": str(c.id), "name": c.name, "title": c.title, "phone": c.phone, "email": c.email,
                    "address": c.address, "description": c.description},
        "role": ({"id": str(r.id), "name": r.name, "builtin_key": r.builtin_key} if r is not None else None),
    } for a, c, r in rows]


# ════════════════════════════════════════════════════════════
# WAN 位址
# ════════════════════════════════════════════════════════════


class CircuitAddressWrite(StrictModel):
    # IP（例如 203.0.113.2）或 CIDR（例如 203.0.113.0/29）；要是 IPAM 裡已登記的
    target: Annotated[str, Field(min_length=1, max_length=64)]
    role: Literal["wan", "gateway", "peer"] = "wan"
    note: Annotated[str | None, Field(max_length=1024)] = None


class CircuitAddressRead(StrictModel):
    id: uuid.UUID
    circuit_id: uuid.UUID
    subnet_id: uuid.UUID | None
    address_id: uuid.UUID | None
    role: str
    note: str | None


async def _circuit_or_404(session: AsyncSession, cid: uuid.UUID) -> Circuit:
    c = await session.get(Circuit, cid)
    if c is None:
        raise HTTPException(404, detail=ui_detail("circuit_not_found", "找不到這條電路"))
    return c


def _parse_target(raw: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | ipaddress.IPv4Network | \
        ipaddress.IPv6Network:
    v = raw.strip()
    try:
        if "/" in v:
            return ipaddress.ip_network(v, strict=False)
        return ipaddress.ip_address(v)
    except ValueError as exc:
        raise HTTPException(422, detail=ui_detail("circuit_address_invalid", "不是 IP 位址或 CIDR",
                                                  value=raw)) from exc


async def _resolve_target(session: AsyncSession, raw: str) -> tuple[str, uuid.UUID]:
    """IP → 已登記的 IP 紀錄；CIDR → 相同網段的子網路。重疊網段有兩筆以上時回 409 要使用者分辨。"""
    t = _parse_target(raw)
    if isinstance(t, ipaddress.IPv4Network | ipaddress.IPv6Network):
        ids = list((await session.execute(
            select(Subnet.id).where(Subnet.cidr == str(t), Subnet.archived_at.is_(None)).limit(3))).scalars())
        kind = "subnet"
    else:
        ids = list((await session.execute(
            select(IPAddress.id).where(func.host(IPAddress.ip) == str(t)).limit(3))).scalars())
        kind = "address"
    if not ids:
        raise HTTPException(404, detail=ui_detail("circuit_address_not_in_ipam",
                                                  "IPAM 裡沒有這個位址或子網路，請先登記", value=str(t)))
    if len(ids) > 1:
        raise HTTPException(409, detail=ui_detail("circuit_address_ambiguous",
                                                  "重疊網段裡有好幾筆同樣的位址，無法判斷是哪一筆", value=str(t)))
    return kind, ids[0]


async def _add_link(session: AsyncSession, circuit: Circuit, kind: str, target_id: uuid.UUID,
                    role: str, note: str | None) -> CircuitAddress:
    link = CircuitAddress(circuit_id=circuit.id, role=role, note=note,
                          subnet_id=target_id if kind == "subnet" else None,
                          address_id=target_id if kind == "address" else None)
    session.add(link)
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail=ui_detail("circuit_address_exists", "這條電路已經關聯這個位址")) from exc
    return link


@router.post("/circuits/{circuit_id}/addresses", response_model=CircuitAddressRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def add_circuit_address(
    circuit_id: uuid.UUID, payload: CircuitAddressWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CircuitAddressRead:
    circuit = await _circuit_or_404(session, circuit_id)
    kind, target_id = await _resolve_target(session, payload.target)
    link = await _add_link(session, circuit, kind, target_id, payload.role, payload.note)
    await _audit(session, user=user, request=request, object_type="circuit_address", object_id=str(link.id),
                 action="create", diff={"circuit": circuit.cid, **payload.model_dump(mode="json")})
    await session.commit()
    await session.refresh(link)
    return CircuitAddressRead.model_validate(link)


@router.delete("/circuits/{circuit_id}/addresses/{link_id}", status_code=204,
               dependencies=[Depends(require_admin)])
async def delete_circuit_address(
    circuit_id: uuid.UUID, link_id: uuid.UUID, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    link = await session.get(CircuitAddress, link_id)
    if link is None or link.circuit_id != circuit_id:
        raise HTTPException(404, detail="Not found")
    await _audit(session, user=user, request=request, object_type="circuit_address", object_id=str(link.id),
                 action="delete", diff={"circuit_id": str(circuit_id), "role": link.role,
                                        "subnet_id": str(link.subnet_id) if link.subnet_id else None,
                                        "address_id": str(link.address_id) if link.address_id else None})
    await session.delete(link)
    await session.commit()


@router.post("/circuits/{circuit_id}/addresses/from-legacy", dependencies=[Depends(require_admin)])
async def convert_legacy_addresses(
    circuit_id: uuid.UUID, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """把舊的文字欄（固定 IP / CIDR、閘道）轉成 IPAM 關聯；文字欄本身不動。

    「203.0.113.2/29」→ 優先關聯那一段子網路（ISP 給的是一段），沒有那段子網路才關聯那個 IP。
    """
    circuit = await _circuit_or_404(session, circuit_id)
    created: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    plans: list[tuple[str, str, list[str]]] = []
    if circuit.ip_address:
        raw = circuit.ip_address.strip()
        cands = []
        if "/" in raw:
            try:
                cands.append(str(ipaddress.ip_network(raw, strict=False)))
            except ValueError:
                pass
        cands.append(raw.split("/")[0])
        plans.append(("wan", raw, cands))
    if circuit.gateway:
        plans.append(("gateway", circuit.gateway.strip(), [circuit.gateway.strip().split("/")[0]]))
    for role, raw, cands in plans:
        done = False
        last_reason = "not_in_ipam"
        for cand in cands:
            try:
                kind, tid = await _resolve_target(session, cand)
            except HTTPException as exc:
                code = exc.detail.get("code") if isinstance(exc.detail, dict) else ""
                last_reason = {"circuit_address_ambiguous": "ambiguous",
                               "circuit_address_invalid": "invalid"}.get(code, "not_in_ipam")
                continue
            exists = (await session.execute(select(CircuitAddress.id).where(
                CircuitAddress.circuit_id == circuit.id,
                (CircuitAddress.subnet_id == tid) if kind == "subnet" else (CircuitAddress.address_id == tid),
            ))).first()
            if exists:
                last_reason = "exists"
                done = True
                skipped.append({"value": raw, "role": role, "reason": "exists"})
                break
            link = await _add_link(session, circuit, kind, tid, role, None)
            created.append({"id": str(link.id), "role": role, "subnet_id": str(tid) if kind == "subnet" else None,
                            "address_id": str(tid) if kind == "address" else None, "value": cand})
            done = True
            break
        if not done:
            skipped.append({"value": raw, "role": role, "reason": last_reason})
    if created:
        await _audit(session, user=user, request=request, object_type="circuit_address", object_id=str(circuit.id),
                     action="create", diff={"circuit": circuit.cid, "from_legacy": created})
    await session.commit()
    return {"created": created, "skipped": skipped}


def _usable(net: ipaddress.IPv4Network | ipaddress.IPv6Network) -> int:
    if net.version == 4 and net.prefixlen <= 30:
        return net.num_addresses - 2
    return net.num_addresses


async def _ip_rows(session: AsyncSession, where: Any, limit: int) -> list[dict[str, Any]]:
    from app.services.arp_seen import _parse
    from app.services.evidence import is_aging
    rows = list((await session.execute(
        select(IPAddress).where(where).order_by(IPAddress.ip).limit(limit))).scalars().all())
    if not rows:
        return []
    ids = [r.id for r in rows]
    dev_ids = {r.device_id for r in rows if r.device_id}
    devs = {d.id: d.name for d in (await session.execute(
        select(Device).where(in_values(Device.id, dev_ids)))).scalars()} if dev_ids else {}
    nat: dict[uuid.UUID, int] = {}
    for col in (NATTranslation.src_ip_id, NATTranslation.dst_ip_id):
        for iid, n in (await session.execute(
                select(col, func.count()).where(in_values(col, ids)).group_by(col))).all():
            nat[iid] = nat.get(iid, 0) + int(n)
    since = datetime.now(UTC) - ACTIVITY_WINDOW
    out = []
    for r in rows:
        seen: dict[str, datetime] = {}
        for f in _SEEN_FIELDS:
            v = getattr(r, f)
            if v is not None and v >= since:
                seen[f.removeprefix("last_seen_")] = v
        for src, raw in (r.arp_seen or {}).items():
            ts = _parse(raw)
            if ts is not None and ts >= since and is_aging(src):
                seen[src] = ts
        newest = max(seen.items(), key=lambda kv: kv[1]) if seen else None
        n_nat = nat.get(r.id, 0)
        signals = sum([bool(r.device_id), n_nat > 0, newest is not None,
                       str(r.effective_status or "").startswith("online")])
        out.append({
            "id": str(r.id), "ip": str(r.ip).split("/")[0], "state": r.state, "hostname": r.hostname,
            "description": r.description, "effective_status": r.effective_status,
            "device": ({"id": str(r.device_id), "name": devs.get(r.device_id)} if r.device_id else None),
            "nat_count": n_nat,
            "last_seen": newest[1].isoformat() if newest else None,
            "last_seen_source": newest[0] if newest else None,
            "signals": signals,
        })
    return out


@router.get("/circuits/{circuit_id}/detail")
async def circuit_detail(
    circuit_id: uuid.UUID, user: CurrentUser, session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    from app.services.permission import visible_ids
    c = await _circuit_or_404(session, circuit_id)
    prov = await session.get(Provider, c.provider_id)
    ctype = await session.get(CircuitType, c.type_id) if c.type_id else None
    dev = await session.get(Device, c.device_id) if c.device_id else None
    vis = await visible_ids(session, user=user, object_type="subnet", required="read")

    def _visible(subnet_id: uuid.UUID | None) -> bool:
        return vis is None or (subnet_id is not None and subnet_id in vis)

    addresses: list[dict[str, Any]] = []
    links = list((await session.execute(
        select(CircuitAddress).where(CircuitAddress.circuit_id == c.id)
        .order_by(case({"wan": 0, "gateway": 1}, value=CircuitAddress.role, else_=2),
                  CircuitAddress.created_at))).scalars().all())
    for link in links:
        item: dict[str, Any] = {"id": str(link.id), "role": link.role, "note": link.note, "hidden": False,
                                "subnet": None, "ips": [], "usable": None, "registered": None}
        if link.subnet_id is not None:
            sub = await session.get(Subnet, link.subnet_id)
            if sub is None or not _visible(sub.id):
                item["hidden"] = True
            else:
                net = ipaddress.ip_network(str(sub.cidr), strict=False)
                item["subnet"] = {"id": str(sub.id), "cidr": str(sub.cidr), "description": sub.description}
                item["usable"] = _usable(net)
                item["registered"] = int(await session.scalar(
                    select(func.count()).select_from(IPAddress).where(IPAddress.subnet_id == sub.id)) or 0)
                item["ips"] = await _ip_rows(session, IPAddress.subnet_id == sub.id, MAX_IPS_PER_SUBNET)
        else:
            ipa = await session.get(IPAddress, link.address_id)
            if ipa is None or not _visible(ipa.subnet_id):
                item["hidden"] = True
            else:
                item["ips"] = await _ip_rows(session, IPAddress.id == ipa.id, 1)
        addresses.append(item)

    dev_vis = await visible_ids(session, user=user, object_type="device", required="read")
    leases = []
    for lease, d in (await session.execute(
            select(DeviceLease, Device).join(Device, Device.id == DeviceLease.device_id)
            .where(DeviceLease.circuit_id == c.id).order_by(Device.name).limit(200))).all():
        if dev_vis is not None and d.id not in dev_vis:
            continue
        leases.append({"id": str(lease.id), "device": {"id": str(d.id), "name": d.name},
                       "return_required": lease.return_required,
                       "return_due": lease.return_due.isoformat() if lease.return_due else None,
                       "note": lease.note})
    return {
        "circuit": {
            "id": str(c.id), "cid": c.cid, "status": c.status, "description": c.description,
            "install_date": c.install_date.isoformat() if c.install_date else None,
            "contract_end_date": c.contract_end_date.isoformat() if c.contract_end_date else None,
            "monthly_fee_cents": c.monthly_fee_cents, "commit_rate_kbps": c.commit_rate_kbps,
            "up_kbps": c.up_kbps, "down_kbps": c.down_kbps,
            "ip_address": c.ip_address, "gateway": c.gateway, "netmask": c.netmask, "dns_servers": c.dns_servers,
        },
        "provider": ({"id": str(prov.id), "name": prov.name, "asn": prov.asn, "account_number": prov.account_number,
                      "portal_url": prov.portal_url, "noc_contact": prov.noc_contact} if prov else None),
        "type": ({"id": str(ctype.id), "name": ctype.name} if ctype else None),
        "device": ({"id": str(dev.id), "name": dev.name}
                   if dev and (dev_vis is None or dev.id in dev_vis) else None),
        "contacts": {"circuit": await _contacts_of(session, "circuit", c.id),
                     "provider": await _contacts_of(session, "provider", c.provider_id)},
        "addresses": addresses,
        "leases": leases,
    }


# ════════════════════════════════════════════════════════════
# 租用設備（掛在裝置上）
# ════════════════════════════════════════════════════════════


class DeviceLeaseWrite(StrictModel):
    provider_id: uuid.UUID | None = None
    circuit_id: uuid.UUID | None = None
    return_required: bool = True
    return_due: date | None = None
    note: Annotated[str | None, Field(max_length=2048)] = None


def _lease_out(lease: DeviceLease, prov: Provider | None, circ: Circuit | None) -> dict[str, Any]:
    return {"id": str(lease.id), "device_id": str(lease.device_id),
            "provider": {"id": str(prov.id), "name": prov.name} if prov else None,
            "circuit": {"id": str(circ.id), "cid": circ.cid} if circ else None,
            "return_required": lease.return_required,
            "return_due": lease.return_due.isoformat() if lease.return_due else None,
            "note": lease.note}


@device_router.get("/devices/{device_id}/lease",
                   dependencies=[Depends(require_object_perm("device", "read", path_param="device_id"))])
async def get_device_lease(
    device_id: uuid.UUID, _user: CurrentUser, session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any] | None:
    lease = (await session.execute(select(DeviceLease).where(DeviceLease.device_id == device_id))).scalar_one_or_none()
    if lease is None:
        return None
    prov = await session.get(Provider, lease.provider_id) if lease.provider_id else None
    circ = await session.get(Circuit, lease.circuit_id) if lease.circuit_id else None
    return _lease_out(lease, prov, circ)


@device_router.put("/devices/{device_id}/lease", dependencies=[Depends(require_admin)])
async def put_device_lease(
    device_id: uuid.UUID, payload: DeviceLeaseWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    if await session.get(Device, device_id) is None:
        raise HTTPException(404, detail="Device not found")
    prov = await session.get(Provider, payload.provider_id) if payload.provider_id else None
    if payload.provider_id and prov is None:
        raise HTTPException(404, detail=ui_detail("provider_not_found", "找不到這個供應商"))
    circ = await session.get(Circuit, payload.circuit_id) if payload.circuit_id else None
    if payload.circuit_id and circ is None:
        raise HTTPException(404, detail=ui_detail("circuit_not_found", "找不到這條電路"))
    if circ is not None and prov is None:   # 選了電路沒選供應商：就是電路的供應商
        prov = await session.get(Provider, circ.provider_id)
    lease = (await session.execute(select(DeviceLease).where(DeviceLease.device_id == device_id))).scalar_one_or_none()
    if lease is None:
        lease = DeviceLease(device_id=device_id)
        session.add(lease)
    lease.provider_id = prov.id if prov else None
    lease.circuit_id = circ.id if circ else None
    lease.return_required = payload.return_required
    lease.return_due = payload.return_due
    lease.note = payload.note
    await session.flush()
    await _audit(session, user=user, request=request, object_type="device_lease", object_id=str(device_id),
                 action="update", diff=payload.model_dump(mode="json"))
    await session.commit()
    await session.refresh(lease)
    return _lease_out(lease, prov, circ)


@device_router.delete("/devices/{device_id}/lease", status_code=204, dependencies=[Depends(require_admin)])
async def delete_device_lease(
    device_id: uuid.UUID, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    lease = (await session.execute(select(DeviceLease).where(DeviceLease.device_id == device_id))).scalar_one_or_none()
    if lease is None:
        raise HTTPException(404, detail="Not found")
    await _audit(session, user=user, request=request, object_type="device_lease", object_id=str(device_id),
                 action="delete", diff={"provider_id": str(lease.provider_id) if lease.provider_id else None,
                                        "circuit_id": str(lease.circuit_id) if lease.circuit_id else None})
    await session.delete(lease)
    await session.commit()


# ════════════════════════════════════════════════════════════
# 站對站 VPN（跟電路一樣有聯絡人；看得出跑在哪條電路上）
# ════════════════════════════════════════════════════════════


@router.get("/vpn-tunnels/{tunnel_id}/detail")
async def vpn_detail(
    tunnel_id: uuid.UUID, user: CurrentUser, session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    from app.models.physical import VPNTunnel
    from app.services.permission import visible_ids
    t = await session.get(VPNTunnel, tunnel_id)
    if t is None:
        raise HTTPException(404, detail=ui_detail("vpn_not_found", "找不到這條 VPN 通道"))
    dev_vis = await visible_ids(session, user=user, object_type="device", required="read")

    async def _dev(did: uuid.UUID | None) -> dict[str, str] | None:
        if did is None or (dev_vis is not None and did not in dev_vis):
            return None
        d = await session.get(Device, did)
        return {"id": str(d.id), "name": d.name} if d else None

    carriers = []
    for side, ep in (("a", t.a_endpoint), ("b", t.b_endpoint)):
        for c in await circuits_carrying(session, ep):
            prov = await session.get(Provider, c.provider_id)
            carriers.append({"side": side, "endpoint": ep,
                             "circuit": {"id": str(c.id), "cid": c.cid, "status": c.status},
                             "provider": prov.name if prov else None})
    return {
        "tunnel": {"id": str(t.id), "name": t.name, "type": t.type, "status": t.status,
                   "a_endpoint": t.a_endpoint, "b_endpoint": t.b_endpoint, "encryption_algo": t.encryption_algo,
                   "auth_algo": t.auth_algo, "pairing_method": t.pairing_method, "description": t.description,
                   "source_origin": t.source_origin},
        "a_device": await _dev(t.a_device_id),
        "b_device": await _dev(t.b_device_id),
        "contacts": await _contacts_of(session, "vpn_tunnel", t.id),
        "carriers": carriers,
    }
