"""機櫃 U 位放置防呆：越界 / 重疊（同方向）/ 縮櫃衝突。"""

from __future__ import annotations

import pytest
from app.services.rack import (
    RackPlacementError,
    assert_placement_ok,
    assert_rack_height_ok,
)


async def _mk_rack(session, *, u_height=18, name="R"):  # type: ignore[no-untyped-def]
    from app.models.location import Rack
    rk = Rack(name=name, u_height=u_height)
    session.add(rk)
    await session.flush()
    return rk


async def _mk_device(session, *, rack_id, u_position, u_size=1, face=None, name="d"):  # type: ignore[no-untyped-def]
    from app.models.device import Device
    d = Device(name=name, type="server", rack_id=rack_id,
               u_position=u_position, u_size=u_size, rack_face=face)
    session.add(d)
    await session.flush()
    return d


@pytest.mark.anyio
async def test_placement_ok_on_empty_slot(db_session, admin_user) -> None:
    rk = await _mk_rack(db_session)
    await assert_placement_ok(db_session, rack_id=rk.id, u_position=5, u_size=2, rack_face=None)


@pytest.mark.anyio
async def test_placement_rejects_out_of_bounds(db_session, admin_user) -> None:
    rk = await _mk_rack(db_session, u_height=12)
    with pytest.raises(RackPlacementError):
        await assert_placement_ok(db_session, rack_id=rk.id, u_position=13, u_size=1, rack_face=None)
    with pytest.raises(RackPlacementError):
        await assert_placement_ok(db_session, rack_id=rk.id, u_position=11, u_size=3, rack_face=None)


@pytest.mark.anyio
async def test_placement_rejects_overlap_same_face(db_session, admin_user) -> None:
    rk = await _mk_rack(db_session)
    await _mk_device(db_session, rack_id=rk.id, u_position=10, u_size=2, name="host-a")  # U10-11
    with pytest.raises(RackPlacementError):
        await assert_placement_ok(db_session, rack_id=rk.id, u_position=11, u_size=1, rack_face=None)


@pytest.mark.anyio
async def test_placement_allows_overlap_different_face(db_session, admin_user) -> None:
    rk = await _mk_rack(db_session)
    await _mk_device(db_session, rack_id=rk.id, u_position=10, u_size=2, face="front", name="front-a")
    # 同 U 但後側 → 允許（落地機櫃前後各掛一台）
    await assert_placement_ok(db_session, rack_id=rk.id, u_position=10, u_size=2, rack_face="rear")


@pytest.mark.anyio
async def test_placement_excludes_self(db_session, admin_user) -> None:
    rk = await _mk_rack(db_session)
    d = await _mk_device(db_session, rack_id=rk.id, u_position=10, u_size=2)
    # 重新驗證自己（例如只改 face）不應視為與自己重疊
    await assert_placement_ok(db_session, rack_id=rk.id, u_position=10, u_size=2,
                              rack_face=None, exclude_device_id=d.id)


@pytest.mark.anyio
async def test_rack_shrink_blocked_by_device(db_session, admin_user) -> None:
    rk = await _mk_rack(db_session, u_height=18)
    await _mk_device(db_session, rack_id=rk.id, u_position=13, u_size=1, name="host-top")
    with pytest.raises(RackPlacementError):
        await assert_rack_height_ok(db_session, rack_id=rk.id, new_height=12)
    # 縮到 13 剛好容得下
    await assert_rack_height_ok(db_session, rack_id=rk.id, new_height=13)
