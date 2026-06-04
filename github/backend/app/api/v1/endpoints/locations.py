"""Location + Rack endpoints。"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_admin
from app.core.audit import append_audit
from app.core.config import get_settings
from app.core.db import get_session
from app.models.location import Location, Rack
from app.schemas.base import Paginated, StrictModel
from app.schemas.location import (
    LocationCreate,
    LocationRead,
    LocationUpdate,
    RackCreate,
    RackPositionsUpdate,
    RackRead,
    RackUpdate,
)

router = APIRouter(tags=["locations"])

# 機房平面圖上傳限制（A03/A08：限類型 + 限大小 + 驗 magic bytes，禁 SVG 避免 XSS）
_MAX_FLOORPLAN_BYTES = 8 * 1024 * 1024
_IMG_MEDIA = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}


def _detect_image_ext(data: bytes) -> str | None:
    """只認可信任的點陣圖格式；用 magic bytes 而非副檔名/Content-Type。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def _floorplan_dir() -> Path:
    return Path(get_settings().upload_dir) / "floorplans"


# ─────────────────── Locations ───────────────────
@router.get("/locations", response_model=Paginated[LocationRead])
async def list_locations(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=500),
) -> Paginated[LocationRead]:
    stmt = select(Location)
    cstmt = select(func.count()).select_from(Location)
    from app.services.permission import visible_ids
    vis = await visible_ids(session, user=_user, object_type="location")
    if vis is not None:
        stmt = stmt.where(Location.id.in_(vis)); cstmt = cstmt.where(Location.id.in_(vis))
    rows = list(
        (await session.execute(
            stmt.order_by(Location.name).offset((page - 1) * page_size).limit(page_size)
        )).scalars().all()
    )
    total = int(await session.scalar(cstmt) or 0)
    # 本頁各機房的機櫃數 / 裝置數
    loc_ids = [r.id for r in rows]
    rack_counts: dict[Any, int] = {}
    dev_counts: dict[Any, int] = {}
    if loc_ids:
        from app.models.device import Device
        from app.models.location import Rack
        rack_counts = dict((await session.execute(  # type: ignore[arg-type]
            select(Rack.location_id, func.count()).where(Rack.location_id.in_(loc_ids))
            .group_by(Rack.location_id)
        )).all())
        dev_counts = dict((await session.execute(  # type: ignore[arg-type]
            select(Device.location_id, func.count()).where(Device.location_id.in_(loc_ids))
            .group_by(Device.location_id)
        )).all())
    # 所屬單位名稱
    cust_names: dict[Any, str] = {}
    cust_ids = [r.customer_id for r in rows if r.customer_id]
    if cust_ids:
        from app.models.customer import Customer
        cust_names = dict((await session.execute(  # type: ignore[arg-type]
            select(Customer.id, Customer.name).where(Customer.id.in_(cust_ids))
        )).all())
    items = []
    for r in rows:
        m = LocationRead.model_validate(r)
        m.rack_count = int(rack_counts.get(r.id, 0))
        m.device_count = int(dev_counts.get(r.id, 0))
        m.customer_name = cust_names.get(r.customer_id) if r.customer_id else None
        items.append(m)
    return Paginated[LocationRead](
        items=items, total=total, page=page, page_size=page_size,
    )


@router.get("/locations/{location_id}", response_model=LocationRead)
async def get_location(
    location_id: uuid.UUID,
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LocationRead:
    obj = await session.get(Location, location_id)
    if obj is None:
        raise HTTPException(404, detail="Location not found")
    return LocationRead.model_validate(obj)


@router.post("/locations", response_model=LocationRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_location(
    payload: LocationCreate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LocationRead:
    obj = Location(**payload.model_dump())
    session.add(obj)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="Location name conflict") from exc
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="location", object_id=str(obj.id), action="create",
        diff={"after": payload.model_dump(mode="json")},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return LocationRead.model_validate(obj)


@router.patch("/locations/{location_id}", response_model=LocationRead,
              dependencies=[Depends(require_admin)])
async def update_location(
    location_id: uuid.UUID,
    payload: LocationUpdate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LocationRead:
    obj = await session.get(Location, location_id)
    if obj is None:
        raise HTTPException(404, detail="Location not found")
    before = {"name": obj.name, "address": obj.address}
    changes = payload.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(obj, k, v)
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="location", object_id=str(obj.id), action="update",
        diff={"before": before, "changes": changes},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return LocationRead.model_validate(obj)


@router.delete("/locations/{location_id}", status_code=204,
               dependencies=[Depends(require_admin)])
async def delete_location(
    location_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    obj = await session.get(Location, location_id)
    if obj is None:
        raise HTTPException(404, detail="Location not found")
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="location", object_id=str(obj.id), action="delete",
        diff={"before": {"name": obj.name}},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.delete(obj)
    await session.commit()


# ─────────────────── 機房平面圖 ───────────────────
@router.post("/locations/{location_id}/floorplan", response_model=LocationRead,
             dependencies=[Depends(require_admin)])
async def upload_floorplan(
    location_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    file: Annotated[UploadFile, File()],
) -> LocationRead:
    obj = await session.get(Location, location_id)
    if obj is None:
        raise HTTPException(404, detail="Location not found")
    data = await file.read(_MAX_FLOORPLAN_BYTES + 1)
    if len(data) > _MAX_FLOORPLAN_BYTES:
        raise HTTPException(413, detail="floor plan too large (max 8 MB)")
    ext = _detect_image_ext(data)
    if ext is None:
        raise HTTPException(415, detail="unsupported image type (png / jpg / gif / webp only)")

    base = _floorplan_dir()
    base.mkdir(parents=True, exist_ok=True)
    rel = f"floorplans/{location_id}.{ext}"
    dest = base / f"{location_id}.{ext}"
    # 清掉同 id 但不同副檔名的舊圖（避免換格式後殘留）
    for old in base.glob(f"{location_id}.*"):
        if old != dest:
            old.unlink(missing_ok=True)
    dest.write_bytes(data)
    dest.chmod(0o640)

    obj.floor_plan_path = rel
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="location", object_id=str(obj.id), action="update",
        diff={"changes": {"floor_plan_path": rel}, "bytes": len(data)},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return LocationRead.model_validate(obj)


@router.get("/locations/{location_id}/floorplan")
async def get_floorplan(
    location_id: uuid.UUID,
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FileResponse:
    obj = await session.get(Location, location_id)
    if obj is None or not obj.floor_plan_path:
        raise HTTPException(404, detail="No floor plan")
    base = _floorplan_dir().resolve()
    path = (Path(get_settings().upload_dir) / obj.floor_plan_path).resolve()
    # path traversal 防護：解析後必須仍在 floorplans 目錄內
    if base != path.parent or not path.is_file():
        raise HTTPException(404, detail="No floor plan")
    media = _IMG_MEDIA.get(path.suffix.lstrip("."), "application/octet-stream")
    return FileResponse(path, media_type=media)


@router.delete("/locations/{location_id}/floorplan", status_code=204,
               dependencies=[Depends(require_admin)])
async def delete_floorplan(
    location_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    obj = await session.get(Location, location_id)
    if obj is None:
        raise HTTPException(404, detail="Location not found")
    if obj.floor_plan_path:
        for old in _floorplan_dir().glob(f"{location_id}.*"):
            old.unlink(missing_ok=True)
        obj.floor_plan_path = None
        await append_audit(
            session, actor_user_id=str(user.id),
            actor_ip=request.client.host if request.client else None,
            actor_user_agent=request.headers.get("user-agent"),
            object_type="location", object_id=str(obj.id), action="update",
            diff={"changes": {"floor_plan_path": None}},
            request_id=getattr(request.state, "request_id", None),
        )
        await session.commit()


@router.put("/locations/{location_id}/rack-positions",
            dependencies=[Depends(require_admin)])
async def set_rack_positions(
    location_id: uuid.UUID,
    payload: RackPositionsUpdate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, int]:
    """設定此機房內機櫃在平面圖上的座標（0..1）。positions 視為「完整擺放狀態」：
    清單內的設座標，清單外（此機房的其他機櫃）一律清空座標 → 對應前端的「移除擺放」。
    只動屬於此 location 的機櫃。"""
    racks = {
        r.id: r for r in (await session.execute(
            select(Rack).where(Rack.location_id == location_id)
        )).scalars().all()
    }
    pos_map = {p.id: p for p in payload.positions}
    updated = 0
    for rid, r in racks.items():
        p = pos_map.get(rid)
        if p is not None:
            if (r.pos_x != p.pos_x or r.pos_y != p.pos_y or r.pos_rot != p.pos_rot
                    or r.pos_w != p.pos_w or r.pos_h != p.pos_h):
                updated += 1
            r.pos_x, r.pos_y, r.pos_rot = p.pos_x, p.pos_y, p.pos_rot
            r.pos_w, r.pos_h = p.pos_w, p.pos_h
        elif r.pos_x is not None or r.pos_y is not None:
            r.pos_x, r.pos_y = None, None   # 清單外 → 清空擺放
            r.pos_rot = 0
            r.pos_w, r.pos_h = None, None
            updated += 1
    if updated:
        await append_audit(
            session, actor_user_id=str(user.id),
            actor_ip=request.client.host if request.client else None,
            actor_user_agent=request.headers.get("user-agent"),
            object_type="location", object_id=str(location_id), action="update",
            diff={"changes": {"rack_positions": updated}},
            request_id=getattr(request.state, "request_id", None),
        )
        await session.commit()
    return {"updated": updated}


# ─────────────────── Racks ───────────────────
@router.get("/racks", response_model=Paginated[RackRead])
async def list_racks(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    location_id: uuid.UUID | None = Query(None),
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=500),
) -> Paginated[RackRead]:
    stmt = select(Rack)
    cstmt = select(func.count()).select_from(Rack)
    if location_id is not None:
        stmt = stmt.where(Rack.location_id == location_id)
        cstmt = cstmt.where(Rack.location_id == location_id)
    from app.services.permission import visible_ids
    vis = await visible_ids(session, user=_user, object_type="rack")
    if vis is not None:
        stmt = stmt.where(Rack.id.in_(vis)); cstmt = cstmt.where(Rack.id.in_(vis))
    # 排序：編號 seq 小的在前（null 排最後），再依名稱
    stmt = stmt.order_by(
        Rack.seq.is_(None), Rack.seq, Rack.name,
    ).offset((page - 1) * page_size).limit(page_size)
    rows = list((await session.execute(stmt)).scalars().all())
    total = int(await session.scalar(cstmt) or 0)
    # 每櫃裝置數
    dev_counts: dict[uuid.UUID, int] = {}
    if rows:
        from app.models.device import Device
        for rid, cnt in (await session.execute(
            select(Device.rack_id, func.count())
            .where(Device.rack_id.in_([r.id for r in rows]))
            .group_by(Device.rack_id)
        )).all():
            dev_counts[rid] = int(cnt)
    # 所屬機房/地點名稱
    loc_names: dict[uuid.UUID, str] = {}
    loc_ids = [r.location_id for r in rows if r.location_id]
    if loc_ids:
        for lid, lname in (await session.execute(
            select(Location.id, Location.name).where(Location.id.in_(loc_ids))
        )).all():
            loc_names[lid] = lname
    items = []
    for r in rows:
        m = RackRead.model_validate(r)
        m.device_count = dev_counts.get(r.id, 0)
        m.location_name = loc_names.get(r.location_id) if r.location_id else None
        items.append(m)
    return Paginated[RackRead](
        items=items, total=total, page=page, page_size=page_size,
    )


@router.post("/racks", response_model=RackRead, status_code=201,
             dependencies=[Depends(require_admin)])
async def create_rack(
    payload: RackCreate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RackRead:
    obj = Rack(**payload.model_dump())
    session.add(obj)
    await session.flush()
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="rack", object_id=str(obj.id), action="create",
        diff={"after": payload.model_dump(mode="json")},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return RackRead.model_validate(obj)


@router.patch("/racks/{rack_id}", response_model=RackRead,
              dependencies=[Depends(require_admin)])
async def update_rack(
    rack_id: uuid.UUID,
    payload: RackUpdate,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RackRead:
    obj = await session.get(Rack, rack_id)
    if obj is None:
        raise HTTPException(404, detail="Rack not found")
    before = {"name": obj.name, "u_height": obj.u_height,
              "location_id": str(obj.location_id) if obj.location_id else None}
    changes = payload.model_dump(exclude_unset=True)
    # 縮小 U 高前防呆：不可低於既有裝置的最高 U
    if "u_height" in changes and changes["u_height"] < obj.u_height:
        from app.services.rack import RackPlacementError, assert_rack_height_ok
        try:
            await assert_rack_height_ok(session, rack_id=obj.id, new_height=changes["u_height"])
        except RackPlacementError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    for k, v in changes.items():
        setattr(obj, k, v)
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="rack", object_id=str(obj.id), action="update",
        diff={"before": before, "changes": payload.model_dump(exclude_unset=True, mode="json")},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(obj)
    return RackRead.model_validate(obj)


@router.delete("/racks/{rack_id}", status_code=204,
               dependencies=[Depends(require_admin)])
async def delete_rack(
    rack_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    obj = await session.get(Rack, rack_id)
    if obj is None:
        raise HTTPException(404, detail="Rack not found")
    await append_audit(
        session,
        actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="rack", object_id=str(obj.id), action="delete",
        diff={"before": {"name": obj.name}},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.delete(obj)
    await session.commit()


class _BulkDelete(StrictModel):
    ids: list[uuid.UUID]


async def _bulk(session: AsyncSession, model_cls: type[Any], object_type: str, user: Any, ids: list[uuid.UUID], actor_ip: str | None, actor_ua: str | None, request_id: str | None) -> dict[str, object]:
    if not ids: return {"deleted": 0, "failed": 0, "errors": []}
    if len(ids) > 500:
        raise HTTPException(400, detail="too many ids (max 500)")
    deleted, errors = 0, []
    for oid in ids:
        obj = await session.get(model_cls, oid)
        if obj is None:
            errors.append({"id": str(oid), "error": "not_found"}); continue
        await append_audit(
            session, actor_user_id=str(user.id),
            actor_ip=actor_ip, actor_user_agent=actor_ua,
            object_type=object_type, object_id=str(obj.id), action="delete",
            diff={"before": {"name": getattr(obj, "name", None)}, "bulk": True},
            request_id=request_id,
        )
        await session.delete(obj)
        deleted += 1
    await session.commit()
    return {"deleted": deleted, "failed": len(errors), "errors": errors[:50]}


@router.post("/locations/bulk-delete", dependencies=[Depends(require_admin)])
async def bulk_delete_locations(
    payload: _BulkDelete, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    return await _bulk(session, Location, "location", user, payload.ids,
        request.client.host if request.client else None,
        request.headers.get("user-agent"),
        getattr(request.state, "request_id", None))


@router.post("/racks/bulk-delete", dependencies=[Depends(require_admin)])
async def bulk_delete_racks(
    payload: _BulkDelete, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    return await _bulk(session, Rack, "rack", user, payload.ids,
        request.client.host if request.client else None,
        request.headers.get("user-agent"),
        getattr(request.state, "request_id", None))
