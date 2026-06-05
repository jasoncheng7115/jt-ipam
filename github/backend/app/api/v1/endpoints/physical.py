"""Cabling / Power / VPN endpoints。"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin, require_global_read
from app.core.audit import append_audit
from app.core.db import get_session
from app.models.device import Device
from app.models.librenms import FDBEntry, LibreNMSDevice
from app.models.physical import (
    Cable,
    CableTermination,
    DevicePort,
    PowerFeed,
    PowerOutlet,
    PowerPanel,
    VPNTunnel,
)
from app.schemas.base import Paginated, StrictModel

router = APIRouter(tags=["physical"], dependencies=[Depends(require_global_read)])


async def _audit(
    session: AsyncSession, *, user: CurrentUser, request: Request,
    object_type: str, object_id: str | None, action: str, diff: dict[str, Any] | None,
) -> None:
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type=object_type, object_id=object_id, action=action,
        diff=diff,
        request_id=getattr(request.state, "request_id", None),
    )


# ─────────────────── Cables ───────────────────


class CableRead(StrictModel):
    id: uuid.UUID
    label: str | None
    type: str | None
    color: str | None
    length_m: float | None
    description: str | None
    status: str
    a_end: str | None = None       # 「裝置 / 連接埠」可讀字串
    b_end: str | None = None
    a_device_id: uuid.UUID | None = None
    b_device_id: uuid.UUID | None = None


class CableWrite(StrictModel):
    label: Annotated[str | None, Field(max_length=128)] = None
    type: Annotated[str | None, Field(max_length=32)] = None
    color: Annotated[str | None, Field(max_length=16)] = None
    length_m: Annotated[float | None, Field(ge=0, le=10_000)] = None
    description: Annotated[str | None, Field(max_length=1024)] = None
    status: str = "connected"


class CableTerminationRead(StrictModel):
    id: uuid.UUID
    cable_id: uuid.UUID
    side: str
    object_type: str
    object_id: uuid.UUID
    port_label: str | None


class CableTerminationWrite(StrictModel):
    cable_id: uuid.UUID
    side: Annotated[str, Field(pattern=r"^[AB]$")]
    object_type: Annotated[str, Field(min_length=1, max_length=32)]
    object_id: uuid.UUID
    port_label: Annotated[str | None, Field(max_length=64)] = None


@router.get("/cables", response_model=Paginated[CableRead])
async def list_cables(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(1, ge=1, le=10_000), page_size: int = Query(50, ge=1, le=500),
) -> Paginated[CableRead]:
    rows = list((await session.execute(
        select(Cable).order_by(Cable.created_at.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    total = int(await session.scalar(select(func.count()).select_from(Cable)) or 0)

    # 兩端「裝置 / 連接埠」解析：把各 cable 的 A/B termination 還原成可讀字串
    cable_ids = [r.id for r in rows]
    ends: dict[uuid.UUID, dict[str, Any]] = {cid: {} for cid in cable_ids}
    if cable_ids:
        terms = list((await session.execute(
            select(CableTermination).where(CableTermination.cable_id.in_(cable_ids))
        )).scalars().all())
        # 預載相關 device_port → device 名稱
        port_ids = [t.object_id for t in terms if t.object_type == "device_port"]
        dev_ids = [t.object_id for t in terms if t.object_type == "device"]
        ports: dict[uuid.UUID, DevicePort] = {}
        if port_ids:
            ports = {p.id: p for p in (await session.execute(
                select(DevicePort).where(DevicePort.id.in_(port_ids))
            )).scalars().all()}
        dev_name: dict[uuid.UUID, str] = {}
        need_dev = set(dev_ids) | {p.device_id for p in ports.values()}
        if need_dev:
            dev_name = {d.id: d.name for d in (await session.execute(
                select(Device).where(Device.id.in_(need_dev))
            )).scalars().all()}
        for t in terms:
            label = None
            device_id = None
            if t.object_type == "device_port" and t.object_id in ports:
                p = ports[t.object_id]
                device_id = p.device_id
                label = f"{dev_name.get(p.device_id, '?')}@{p.name}"
            elif t.object_type == "device":
                device_id = t.object_id
                label = dev_name.get(t.object_id, str(t.object_id)[:8])
            else:
                label = t.port_label or t.object_type
            ends[t.cable_id][t.side] = {"label": label, "device_id": device_id}

    items = []
    for r in rows:
        e = ends.get(r.id, {})
        a, b = e.get("A") or {}, e.get("B") or {}
        items.append(CableRead.model_validate(r).model_copy(update={
            "a_end": a.get("label"), "b_end": b.get("label"),
            "a_device_id": a.get("device_id"), "b_device_id": b.get("device_id"),
        }))
    return Paginated[CableRead](items=items, total=total, page=page, page_size=page_size)


@router.post("/cables", response_model=CableRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_cable(
    payload: CableWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CableRead:
    obj = Cable(**payload.model_dump())
    session.add(obj)
    await session.flush()
    await _audit(session, user=user, request=request, object_type="cable",
                 object_id=str(obj.id), action="create",
                 diff=payload.model_dump(mode="json"))
    await session.commit()
    await session.refresh(obj)
    return CableRead.model_validate(obj)


@router.patch("/cables/{cable_id}", response_model=CableRead,
              dependencies=[Depends(require_admin)])
async def update_cable(
    cable_id: uuid.UUID, payload: CableWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CableRead:
    obj = await session.get(Cable, cable_id)
    if obj is None:
        raise HTTPException(404, detail="Cable not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    await session.flush()
    await _audit(session, user=user, request=request, object_type="cable",
                 object_id=str(obj.id), action="update",
                 diff=payload.model_dump(mode="json", exclude_unset=True))
    await session.commit()
    await session.refresh(obj)
    return CableRead.model_validate(obj)


@router.delete("/cables/{cable_id}", status_code=204,
               dependencies=[Depends(require_admin)])
async def delete_cable(
    cable_id: uuid.UUID, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    obj = await session.get(Cable, cable_id)
    if obj is None:
        raise HTTPException(404, detail="Cable not found")
    # 先刪兩端 termination 再刪 cable
    for t in (await session.execute(
        select(CableTermination).where(CableTermination.cable_id == cable_id)
    )).scalars().all():
        await session.delete(t)
    await session.delete(obj)
    await _audit(session, user=user, request=request, object_type="cable",
                 object_id=str(cable_id), action="delete", diff=None)
    await session.commit()


@router.post("/cable-terminations", response_model=CableTerminationRead,
             status_code=201, dependencies=[Depends(require_admin)])
async def add_termination(
    payload: CableTerminationWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CableTerminationRead:
    obj = CableTermination(**payload.model_dump())
    session.add(obj)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="Side already terminated for this cable") from exc
    await _audit(session, user=user, request=request, object_type="cable_termination",
                 object_id=str(obj.id), action="create",
                 diff=payload.model_dump(mode="json"))
    await session.commit()
    await session.refresh(obj)
    return CableTerminationRead.model_validate(obj)


@router.get("/cables/{cable_id}/trace")
async def trace_cable(
    cable_id: uuid.UUID,
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:  # type: ignore[type-arg]
    """傳回 cable 的 A/B 端 termination。Phase 3.5 將擴展為 multi-cable hop trace。"""
    cable = await session.get(Cable, cable_id)
    if cable is None:
        raise HTTPException(404, detail="Cable not found")
    terms = list((await session.execute(
        select(CableTermination).where(CableTermination.cable_id == cable_id)
    )).scalars().all())
    return {
        "cable": {"id": str(cable.id), "label": cable.label, "type": cable.type,
                  "status": cable.status},
        "terminations": [
            {
                "side": t.side,
                "object_type": t.object_type,
                "object_id": str(t.object_id),
                "port_label": t.port_label,
            }
            for t in sorted(terms, key=lambda x: x.side)
        ],
    }


# ─────────────────── Device ports + Cable Trace ───────────────────


class DevicePortRead(StrictModel):
    id: uuid.UUID
    device_id: uuid.UUID
    name: str
    type: str
    peer_port_id: uuid.UUID | None
    position: int | None
    description: str | None
    link: str | None = None   # 對端標籤（已接纜線時）：例「switch-003 · eth1/0/24」
    mac_address: str | None = None   # 此埠自身的實體 MAC（ifPhysAddress）


class DevicePortWrite(StrictModel):
    device_id: uuid.UUID
    name: Annotated[str, Field(min_length=1, max_length=64)]
    type: str = "network"
    peer_port_id: uuid.UUID | None = None
    position: int | None = None
    description: Annotated[str | None, Field(max_length=1024)] = None


class DevicePortUpdate(StrictModel):
    name: Annotated[str | None, Field(min_length=1, max_length=64)] = None
    type: str | None = None
    peer_port_id: uuid.UUID | None = None
    position: int | None = None
    description: Annotated[str | None, Field(max_length=1024)] = None


@router.get("/device-ports", response_model=list[DevicePortRead])
async def list_device_ports(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    device_id: uuid.UUID = Query(...),
) -> list[DevicePortRead]:
    rows = list((await session.execute(
        select(DevicePort).where(DevicePort.device_id == device_id)
        .order_by(DevicePort.position, DevicePort.name)
    )).scalars().all())
    out = [DevicePortRead.model_validate(r) for r in rows]
    if not rows:
        return out
    by_id = {r.id: r for r in out}
    port_ids = list(by_id)

    # ── 纜線對端（link）──
    terms = list((await session.execute(
        select(CableTermination).where(
            CableTermination.object_type == "device_port",
            CableTermination.object_id.in_(port_ids),
        )
    )).scalars().all())
    for t in terms:
        other = (await session.execute(
            select(CableTermination).where(
                CableTermination.cable_id == t.cable_id,
                CableTermination.id != t.id,
            ).limit(1)
        )).scalar_one_or_none()
        if other is None:
            continue
        label: str | None = None
        if other.object_type == "device_port":
            fp = await session.get(DevicePort, other.object_id)
            if fp is not None:
                fd = await session.get(Device, fp.device_id)
                label = f"{fd.name if fd else '?'} · {fp.name}"
        else:
            label = f"{other.object_type} {other.port_label or ''}".strip()
        if label and t.object_id in by_id:
            by_id[t.object_id].link = label

    # 埠自身 MAC（mac_address）已由 model_validate 直接帶出，不再用 FDB/ARP
    return out


@router.post("/device-ports", response_model=DevicePortRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_device_port(
    payload: DevicePortWrite,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DevicePortRead:
    obj = DevicePort(**payload.model_dump())
    session.add(obj)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="Port name already exists on this device") from exc
    await _audit(session, user=user, request=request, object_type="device_port",
                 object_id=str(obj.id), action="create", diff={"name": obj.name})
    await session.commit()
    await session.refresh(obj)
    return DevicePortRead.model_validate(obj)


@router.post("/device-ports/import", dependencies=[Depends(require_admin)])
async def import_device_ports(
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    device_id: uuid.UUID = Query(...),
) -> dict[str, Any]:
    """從整合來源把連接埠撈進來：優先 LibreNMS 介面清單(ifName，含 server/PVE 主機)，
    退回 FDB 學到的 port_name（交換器）。"""
    from app.models.librenms import LibreNMSInstance

    lns_devs = list((await session.execute(
        select(LibreNMSDevice).where(LibreNMSDevice.jt_ipam_device_id == device_id)
    )).scalars().all())

    names: set[str] = set()
    name_mac: dict[str, str | None] = {}
    sources: set[str] = set()

    # 1) LibreNMS 介面清單（ifName）— 對 server / PVE 主機 / switch 都有效
    for d in lns_devs:
        inst = await session.get(LibreNMSInstance, d.instance_id)
        if inst is None:
            continue
        try:
            from app.services.librenms import _api_get, _norm_mac
            pdata = await _api_get(
                inst, f"/api/v0/devices/{d.legacy_device_id}/ports?columns=ifName,ifType,ifPhysAddress",
                timeout=20.0,
            )
            for p in pdata.get("ports") or []:
                nm = (p.get("ifName") or "").strip()
                if nm and nm.lower() not in ("null", "unrouted vlan 1"):
                    names.add(nm)
                    name_mac[nm] = _norm_mac(p.get("ifPhysAddress"))
                    sources.add("librenms")
        except Exception as exc:
            # LibreNMS 不可達/回應異常：略過此來源，改用 FDB
            logging.getLogger(__name__).debug("librenms ports fetch failed: %s", exc)

    # 2) 退回 FDB（交換器學到的 port）
    if not names and lns_devs:
        rows = (await session.execute(
            select(FDBEntry.port_name).where(
                FDBEntry.device_id.in_([d.id for d in lns_devs]),
                FDBEntry.port_name.is_not(None),
            ).distinct()
        )).all()
        for r in rows:
            if r[0] and r[0].strip():
                names.add(r[0].strip())
                sources.add("librenms-fdb")

    existing = {p.name for p in (await session.execute(
        select(DevicePort).where(DevicePort.device_id == device_id)
    )).scalars().all()}

    created = 0
    for n in sorted(names):
        if n in existing:
            continue
        session.add(DevicePort(device_id=device_id, name=n, type="network"))
        created += 1

    if created:
        await _audit(session, user=user, request=request, object_type="device_port",
                     object_id=str(device_id), action="import",
                     diff={"imported": created, "sources": sorted(sources)})
        await session.commit()
    return {"imported": created, "found": len(names),
            "linked_librenms": len(lns_devs), "sources": sorted(sources)}


@router.patch("/device-ports/{port_id}", response_model=DevicePortRead,
              dependencies=[Depends(require_admin)])
async def update_device_port(
    port_id: uuid.UUID,
    payload: DevicePortUpdate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DevicePortRead:
    obj = await session.get(DevicePort, port_id)
    if obj is None:
        raise HTTPException(404, detail="Port not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="Port name already exists on this device") from exc
    # front↔rear pass-through 雙向綁定：設定 peer 時，對方也指回自己
    if payload.peer_port_id is not None:
        peer = await session.get(DevicePort, payload.peer_port_id)
        if peer is not None and peer.peer_port_id != obj.id:
            peer.peer_port_id = obj.id
    await _audit(session, user=user, request=request, object_type="device_port",
                 object_id=str(obj.id), action="update", diff=payload.model_dump(exclude_unset=True))
    await session.commit()
    await session.refresh(obj)
    return DevicePortRead.model_validate(obj)


@router.delete("/device-ports/{port_id}", status_code=204,
               dependencies=[Depends(require_admin)])
async def delete_device_port(
    port_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    obj = await session.get(DevicePort, port_id)
    if obj is None:
        raise HTTPException(404, detail="Port not found")
    await _audit(session, user=user, request=request, object_type="device_port",
                 object_id=str(obj.id), action="delete", diff={"name": obj.name})
    await session.delete(obj)
    await session.commit()


@router.get("/ports/{port_id}/trace")
async def trace_port(
    port_id: uuid.UUID,
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:  # type: ignore[type-arg]
    """從某連接埠做多跳 cable trace：沿纜線 → 對端埠 →（若為 front/rear pass-through）穿透 → 續走。"""
    start = await session.get(DevicePort, port_id)
    if start is None:
        raise HTTPException(404, detail="Port not found")

    dev_names: dict[uuid.UUID, str] = {}

    async def _port_node(p: DevicePort) -> dict[str, Any]:
        if p.device_id not in dev_names:
            d = await session.get(Device, p.device_id)
            dev_names[p.device_id] = d.name if d else str(p.device_id)[:8]
        return {"port_id": str(p.id), "port_name": p.name, "port_type": p.type,
                "device_id": str(p.device_id), "device_name": dev_names[p.device_id]}

    async def _term_for_port(pid: uuid.UUID) -> CableTermination | None:
        return (await session.execute(
            select(CableTermination).where(
                CableTermination.object_type == "device_port",
                CableTermination.object_id == pid,
            ).limit(1)
        )).scalar_one_or_none()

    def _internal_hop(to_node: dict[str, Any]) -> dict[str, Any]:
        return {"cable_id": None, "cable_label": None, "cable_type": "internal",
                "cable_color": None, "internal": True, "to": to_node}

    hops: list[dict[str, Any]] = []
    visited: set[uuid.UUID] = set()
    current: DevicePort | None = start
    nodes = [await _port_node(current)]

    # 每一輪最多前進一段（纜線或內部 peer），且每多一個 node 就配一個 hop（保持對齊）。
    # visited 同時防迴圈：雙向 peer（bridge↔NIC）不會來回繞。
    while current is not None and current.id not in visited:
        visited.add(current.id)

        # bridge：實體上行其實是對應 NIC(peer)。插入「bridge →(橋接) NIC」內部跳，
        # NIC 標記已訪、但 current 仍是 bridge（纜線掛在 bridge 上，由下方續走）。
        if (current.type == "bridge" and current.peer_port_id
                and current.peer_port_id not in visited):
            nic = await session.get(DevicePort, current.peer_port_id)
            if nic is not None and nic.device_id == current.device_id:
                visited.add(nic.id)
                nic_node = await _port_node(nic)
                hops.append(_internal_hop(nic_node))
                nodes.append(nic_node)

        # 1) 沿纜線到對端（對端未訪過才前進，避免回繞）
        term = await _term_for_port(current.id)
        if term is not None:
            cable = await session.get(Cable, term.cable_id)
            other = (await session.execute(
                select(CableTermination).where(
                    CableTermination.cable_id == term.cable_id,
                    CableTermination.id != term.id,
                ).limit(1)
            )).scalar_one_or_none()
            if other is not None:
                cable_hop: dict[str, Any] = {
                    "cable_id": str(cable.id) if cable else None,
                    "cable_label": cable.label if cable else None,
                    "cable_type": cable.type if cable else None,
                    "cable_color": cable.color if cable else None,
                    "to": None,
                }
                if other.object_type == "device_port":
                    far = await session.get(DevicePort, other.object_id)
                    if far is not None and far.id not in visited:
                        cable_hop["to"] = await _port_node(far)
                        hops.append(cable_hop)
                        nodes.append(cable_hop["to"])
                        current = far
                        continue
                    # 對端已訪過 → 此纜線不再前進
                else:
                    # 非 device_port 對端（外部標籤）→ 視為終點
                    cable_hop["to"] = {"object_type": other.object_type,
                                       "object_id": str(other.object_id),
                                       "port_name": other.port_label}
                    hops.append(cable_hop)
                    nodes.append(cable_hop["to"])
                    break

        # 2) 沒有可走的纜線 → 內部 peer 穿透（跳接面板 front/rear，或從 NIC 端）
        if current.peer_port_id and current.peer_port_id not in visited:
            pp = await session.get(DevicePort, current.peer_port_id)
            if pp is not None:
                pp_node = await _port_node(pp)
                hops.append(_internal_hop(pp_node))
                nodes.append(pp_node)
                current = pp
                continue
        break

    return {"start": nodes[0], "nodes": nodes, "hops": hops}


# ─────────────────── Power ───────────────────


class PowerPanelRead(StrictModel):
    id: uuid.UUID
    name: str
    location_id: uuid.UUID | None
    description: str | None


class PowerPanelWrite(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    location_id: uuid.UUID | None = None
    description: Annotated[str | None, Field(max_length=1024)] = None


class PowerFeedRead(StrictModel):
    id: uuid.UUID
    panel_id: uuid.UUID
    name: str
    voltage_v: int
    amperage_a: int
    phase: str
    supply_type: str
    rack_id: uuid.UUID | None
    description: str | None


class PowerFeedWrite(StrictModel):
    panel_id: uuid.UUID
    name: Annotated[str, Field(min_length=1, max_length=64)]
    voltage_v: Annotated[int, Field(ge=12, le=600)] = 220
    amperage_a: Annotated[int, Field(ge=1, le=400)] = 20
    phase: str = "single"
    supply_type: str = "ac"
    rack_id: uuid.UUID | None = None
    description: Annotated[str | None, Field(max_length=1024)] = None


class PowerOutletRead(StrictModel):
    id: uuid.UUID
    feed_id: uuid.UUID | None
    rack_id: uuid.UUID | None
    label: str
    device_id: uuid.UUID | None
    description: str | None


class PowerOutletWrite(StrictModel):
    feed_id: uuid.UUID | None = None
    rack_id: uuid.UUID | None = None
    label: Annotated[str, Field(min_length=1, max_length=64)]
    device_id: uuid.UUID | None = None
    description: Annotated[str | None, Field(max_length=1024)] = None


@router.get("/power-panels", response_model=Paginated[PowerPanelRead])
async def list_power_panels(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(1, ge=1, le=10_000), page_size: int = Query(50, ge=1, le=500),
) -> Paginated[PowerPanelRead]:
    rows = list((await session.execute(
        select(PowerPanel).order_by(PowerPanel.name)
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    total = int(await session.scalar(select(func.count()).select_from(PowerPanel)) or 0)
    return Paginated[PowerPanelRead](
        items=[PowerPanelRead.model_validate(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.post("/power-panels", response_model=PowerPanelRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_power_panel(
    payload: PowerPanelWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PowerPanelRead:
    obj = PowerPanel(**payload.model_dump())
    session.add(obj)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="Panel name+location conflict") from exc
    await _audit(session, user=user, request=request, object_type="power_panel",
                 object_id=str(obj.id), action="create",
                 diff=payload.model_dump(mode="json"))
    await session.commit()
    await session.refresh(obj)
    return PowerPanelRead.model_validate(obj)


@router.get("/power-feeds", response_model=Paginated[PowerFeedRead])
async def list_power_feeds(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    panel_id: uuid.UUID | None = Query(None),
    page: int = Query(1, ge=1, le=10_000), page_size: int = Query(100, ge=1, le=500),
) -> Paginated[PowerFeedRead]:
    stmt = select(PowerFeed)
    cstmt = select(func.count()).select_from(PowerFeed)
    if panel_id is not None:
        stmt = stmt.where(PowerFeed.panel_id == panel_id)
        cstmt = cstmt.where(PowerFeed.panel_id == panel_id)
    rows = list((await session.execute(
        stmt.order_by(PowerFeed.name).offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    total = int(await session.scalar(cstmt) or 0)
    return Paginated[PowerFeedRead](
        items=[PowerFeedRead.model_validate(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.post("/power-feeds", response_model=PowerFeedRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_power_feed(
    payload: PowerFeedWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PowerFeedRead:
    obj = PowerFeed(**payload.model_dump())
    session.add(obj)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="Feed conflict") from exc
    await _audit(session, user=user, request=request, object_type="power_feed",
                 object_id=str(obj.id), action="create",
                 diff=payload.model_dump(mode="json"))
    await session.commit()
    await session.refresh(obj)
    return PowerFeedRead.model_validate(obj)


@router.get("/power-outlets", response_model=Paginated[PowerOutletRead])
async def list_power_outlets(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(1, ge=1, le=10_000), page_size: int = Query(50, ge=1, le=500),
) -> Paginated[PowerOutletRead]:
    rows = list((await session.execute(
        select(PowerOutlet).order_by(PowerOutlet.label)
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    total = int(await session.scalar(select(func.count()).select_from(PowerOutlet)) or 0)
    return Paginated[PowerOutletRead](
        items=[PowerOutletRead.model_validate(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.post("/power-outlets", response_model=PowerOutletRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_power_outlet(
    payload: PowerOutletWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PowerOutletRead:
    obj = PowerOutlet(**payload.model_dump())
    session.add(obj)
    await session.flush()
    await _audit(session, user=user, request=request, object_type="power_outlet",
                 object_id=str(obj.id), action="create",
                 diff=payload.model_dump(mode="json"))
    await session.commit()
    await session.refresh(obj)
    return PowerOutletRead.model_validate(obj)


# ── 電力資源 編輯 / 刪除（admin）──
async def _power_update(session, model, obj_id, payload, otype, user, request):  # type: ignore[no-untyped-def]
    obj = await session.get(model, obj_id)
    if obj is None:
        raise HTTPException(404, detail="Not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    await session.flush()
    await _audit(session, user=user, request=request, object_type=otype,
                 object_id=str(obj_id), action="update",
                 diff=payload.model_dump(mode="json", exclude_unset=True))
    await session.commit()
    await session.refresh(obj)
    return obj


async def _power_delete(session, model, obj_id, otype, user, request):  # type: ignore[no-untyped-def]
    obj = await session.get(model, obj_id)
    if obj is None:
        raise HTTPException(404, detail="Not found")
    await session.delete(obj)
    await _audit(session, user=user, request=request, object_type=otype,
                 object_id=str(obj_id), action="delete", diff=None)
    await session.commit()


@router.patch("/power-panels/{pid}", response_model=PowerPanelRead, dependencies=[Depends(require_admin)])
async def update_power_panel(pid: uuid.UUID, payload: PowerPanelWrite, user: CurrentUser, request: Request,
                             session: Annotated[AsyncSession, Depends(get_session)]) -> PowerPanelRead:
    return PowerPanelRead.model_validate(await _power_update(session, PowerPanel, pid, payload, "power_panel", user, request))


@router.delete("/power-panels/{pid}", status_code=204, dependencies=[Depends(require_admin)])
async def delete_power_panel(pid: uuid.UUID, user: CurrentUser, request: Request,
                             session: Annotated[AsyncSession, Depends(get_session)]) -> None:
    await _power_delete(session, PowerPanel, pid, "power_panel", user, request)


@router.patch("/power-feeds/{fid}", response_model=PowerFeedRead, dependencies=[Depends(require_admin)])
async def update_power_feed(fid: uuid.UUID, payload: PowerFeedWrite, user: CurrentUser, request: Request,
                            session: Annotated[AsyncSession, Depends(get_session)]) -> PowerFeedRead:
    return PowerFeedRead.model_validate(await _power_update(session, PowerFeed, fid, payload, "power_feed", user, request))


@router.delete("/power-feeds/{fid}", status_code=204, dependencies=[Depends(require_admin)])
async def delete_power_feed(fid: uuid.UUID, user: CurrentUser, request: Request,
                            session: Annotated[AsyncSession, Depends(get_session)]) -> None:
    await _power_delete(session, PowerFeed, fid, "power_feed", user, request)


@router.patch("/power-outlets/{oid}", response_model=PowerOutletRead, dependencies=[Depends(require_admin)])
async def update_power_outlet(oid: uuid.UUID, payload: PowerOutletWrite, user: CurrentUser, request: Request,
                              session: Annotated[AsyncSession, Depends(get_session)]) -> PowerOutletRead:
    return PowerOutletRead.model_validate(await _power_update(session, PowerOutlet, oid, payload, "power_outlet", user, request))


@router.delete("/power-outlets/{oid}", status_code=204, dependencies=[Depends(require_admin)])
async def delete_power_outlet(oid: uuid.UUID, user: CurrentUser, request: Request,
                              session: Annotated[AsyncSession, Depends(get_session)]) -> None:
    await _power_delete(session, PowerOutlet, oid, "power_outlet", user, request)


# ─────────────────── VPN ───────────────────


class VPNTunnelRead(StrictModel):
    id: uuid.UUID
    name: str
    type: str
    status: str
    a_device_id: uuid.UUID | None
    b_device_id: uuid.UUID | None
    a_endpoint: str | None
    b_endpoint: str | None
    encryption_algo: str | None
    auth_algo: str | None
    description: str | None
    pairing_method: str | None = None   # wireguard_pubkey（可靠）/ ipsec_endpoint（best-effort）
    a_device_name: str | None = None
    b_device_name: str | None = None
    peered: bool = False   # 兩端對接成立（公鑰或端點比對）


class VPNTunnelWrite(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    type: str
    status: str = "active"
    a_device_id: uuid.UUID | None = None
    b_device_id: uuid.UUID | None = None
    a_endpoint: Annotated[str | None, Field(max_length=255)] = None
    b_endpoint: Annotated[str | None, Field(max_length=255)] = None
    encryption_algo: Annotated[str | None, Field(max_length=32)] = None
    auth_algo: Annotated[str | None, Field(max_length=32)] = None
    description: Annotated[str | None, Field(max_length=1024)] = None


@router.get("/vpn-tunnels", response_model=Paginated[VPNTunnelRead])
async def list_vpn(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(1, ge=1, le=10_000), page_size: int = Query(50, ge=1, le=500),
) -> Paginated[VPNTunnelRead]:
    rows = list((await session.execute(
        select(VPNTunnel).order_by(VPNTunnel.name)
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    total = int(await session.scalar(select(func.count()).select_from(VPNTunnel)) or 0)

    # 解析 a/b device 名稱供前端顯示對接關係
    dev_ids = {d for r in rows for d in (r.a_device_id, r.b_device_id) if d}
    names: dict[uuid.UUID, str] = {}
    if dev_ids:
        names = {
            did: nm for did, nm in (await session.execute(
                select(Device.id, Device.name).where(Device.id.in_(dev_ids))
            )).all()
        }

    items: list[VPNTunnelRead] = []
    for r in rows:
        item = VPNTunnelRead.model_validate(r)
        item.a_device_name = names.get(r.a_device_id) if r.a_device_id else None
        item.b_device_name = names.get(r.b_device_id) if r.b_device_id else None
        item.peered = r.b_device_id is not None
        items.append(item)
    return Paginated[VPNTunnelRead](
        items=items, total=total, page=page, page_size=page_size,
    )


@router.post("/vpn-tunnels", response_model=VPNTunnelRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_vpn(
    payload: VPNTunnelWrite, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> VPNTunnelRead:
    obj = VPNTunnel(**payload.model_dump())
    session.add(obj)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="VPN tunnel name conflict") from exc
    await _audit(session, user=user, request=request, object_type="vpn_tunnel",
                 object_id=str(obj.id), action="create",
                 diff=payload.model_dump(mode="json"))
    await session.commit()
    await session.refresh(obj)
    return VPNTunnelRead.model_validate(obj)
