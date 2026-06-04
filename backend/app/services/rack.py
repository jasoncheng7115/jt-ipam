"""機櫃 U 位放置的防呆驗證（共用給 device 建立/更新、rack 改 U 高）。

規則：
- 裝置 U 位不可越界（1 ≤ u_position，且 u_position + u_size - 1 ≤ rack.u_height）
- 同一機櫃、同一安裝方向（front/rear）內，U 區間不可與其他裝置重疊
- 縮小機櫃 U 高時，不可低於既有裝置的最高 U（否則那台會越界）

失敗時 raise RackPlacementError（人讀訊息），endpoint 翻成 HTTP 400/409。
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import Device
from app.models.location import Rack


class RackPlacementError(ValueError):
    """U 位放置不合法（越界 / 重疊 / 縮櫃衝突）。"""


def _face(v: str | None) -> str:
    return v or "front"


def _side(v: str | None) -> str:
    return v or "full"


def _sides_conflict(a: str | None, b: str | None) -> bool:
    """半 U 占寬是否衝突：full 與任何都衝突；left↔left、right↔right 衝突；left↔right 不衝突。"""
    sa, sb = _side(a), _side(b)
    if sa == "full" or sb == "full":
        return True
    return sa == sb


async def assert_placement_ok(
    session: AsyncSession,
    *,
    rack_id: uuid.UUID,
    u_position: int,
    u_size: int,
    rack_face: str | None,
    rack_side: str | None = None,
    exclude_device_id: uuid.UUID | None = None,
) -> None:
    """驗證某裝置放在 rack_id 的 [u_position, u_position+u_size-1] 是否合法。"""
    if u_size < 1:
        raise RackPlacementError("u_size 必須 ≥ 1")
    rack = await session.get(Rack, rack_id)
    if rack is None:
        raise RackPlacementError("機櫃不存在")

    top = u_position + u_size - 1
    if u_position < 1 or top > rack.u_height:
        raise RackPlacementError(
            f"U 位超出機櫃範圍：裝置占 U{u_position}–U{top}，"
            f"但「{rack.name}」只有 {rack.u_height}U"
        )

    face = _face(rack_face)
    others = (await session.execute(
        select(Device).where(
            Device.rack_id == rack_id,
            Device.u_position.is_not(None),
            Device.u_size.is_not(None),
        )
    )).scalars().all()
    want = set(range(u_position, top + 1))
    for d in others:
        if exclude_device_id is not None and d.id == exclude_device_id:
            continue
        if _face(d.rack_face) != face:
            continue  # 不同安裝方向（前/後）不算重疊
        if d.u_position is None or d.u_size is None:
            continue
        if not _sides_conflict(rack_side, d.rack_side):
            continue  # 半 U 一左一右，同 U 不算重疊
        d_range = set(range(d.u_position, d.u_position + d.u_size))
        clash = sorted(want & d_range)
        if clash:
            us = ", ".join(f"U{u}" for u in clash)
            raise RackPlacementError(
                f"與「{d.name}」的 U 位重疊（{us}）；請改放空的 U 位或調整 U 數"
            )


async def assert_rack_height_ok(
    session: AsyncSession, *, rack_id: uuid.UUID, new_height: int,
) -> None:
    """縮小機櫃 U 高前，確認不會把既有裝置擠到範圍外。"""
    rows = (await session.execute(
        select(Device.name, Device.u_position, Device.u_size).where(
            Device.rack_id == rack_id,
            Device.u_position.is_not(None),
            Device.u_size.is_not(None),
        )
    )).all()
    offenders = [
        (name, pos, size) for (name, pos, size) in rows
        if pos is not None and size is not None and (pos + size - 1) > new_height
    ]
    if offenders:
        names = "、".join(
            f"{n}(U{p}–U{p + s - 1})" for (n, p, s) in offenders[:5]
        )
        raise RackPlacementError(
            f"無法縮小到 {new_height}U：以下裝置會超出範圍 → {names}。"
            "請先移走或下移這些裝置。"
        )
