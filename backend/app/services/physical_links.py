"""實體連結的寫入規則：跳接面板配對、埠的纜線占用、插座的 PSU 占用（2026-11-21）。

交接包 physical-v1.2 CABLING.md 指出、查證屬實的問題：
- 改配對只補寫新對端的反向指標，舊對端還指著自己 → 追線可能走到不存在的配對
- 一個埠可以被兩條「已接線」的纜線占用、一個插座可以接好幾顆 PSU，資料庫沒有擋
- 前端接一條線要打三支 API（纜線、A 端、B 端），一端失敗就留下半條線

做法：寫入前用 `SELECT ... FOR UPDATE` 鎖住被占用的那一列（埠、插座），再檢查。兩個人同時把同一個埠接線時，
後到的會等前一筆提交、再看到它的端點而被拒絕（409）。**不加資料庫唯一約束**：升級前就有的重複占用要原樣保留、
由管理員在資料檢查頁確認（交接包 UPGRADE.md：不自動刪除、合併或改接），加約束會讓有這種資料的站台升級失敗。
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ui_error import ui_detail
from app.models.device import Device
from app.models.physical import Cable, CableTermination, DevicePort, DevicePowerPort, PowerOutlet


async def lock_ports(session: AsyncSession, ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, DevicePort]:
    """依 id 排序鎖住這些埠（固定順序，兩筆交易互鎖時不會死結）。"""
    out: dict[uuid.UUID, DevicePort] = {}
    for pid in sorted({i for i in ids if i}, key=str):
        p = (await session.execute(
            select(DevicePort).where(DevicePort.id == pid).with_for_update())).scalar_one_or_none()
        if p is not None:
            out[pid] = p
    return out


async def ensure_port_free(session: AsyncSession, port: DevicePort, *, exclude_cable_id: uuid.UUID | None = None) -> None:
    """這個埠已經有「已接線」的纜線 → 409 port_occupied。規劃中、已除役的線不占用。呼叫前要先 lock_ports。"""
    q = (select(Cable.id, Cable.label).join(CableTermination, CableTermination.cable_id == Cable.id)
         .where(CableTermination.object_type == "device_port", CableTermination.object_id == port.id,
                Cable.status == "connected"))
    if exclude_cable_id is not None:
        q = q.where(Cable.id != exclude_cable_id)
    hit = (await session.execute(q.limit(1))).first()
    if hit is None:
        return
    dev = await session.get(Device, port.device_id)
    raise HTTPException(409, detail=ui_detail(
        "port_occupied", f"{dev.name if dev else ''} {port.name} 已經接了另一條纜線",
        port=port.name, device=dev.name if dev else "", cable=hit.label or ""))


async def ensure_cable_ports_free(session: AsyncSession, cable_id: uuid.UUID) -> None:
    """規劃中 / 已除役的線改成已接線時：它兩端的埠要沒被別條已接線的線占用。"""
    pids = (await session.execute(select(CableTermination.object_id).where(
        CableTermination.cable_id == cable_id, CableTermination.object_type == "device_port"))).scalars().all()
    for port in (await lock_ports(session, pids)).values():
        await ensure_port_free(session, port, exclude_cable_id=cable_id)


async def set_peer(session: AsyncSession, port: DevicePort, new_peer_id: uuid.UUID | None, *, replace: bool) -> None:
    """設定（或清除）跳接面板的穿透配對，兩邊一起改。

    - 舊對端若還指著自己，清掉
    - 新對端已經配給第三個埠：沒有 replace 就 409 port_peer_taken（不靜默搶走）；有 replace 才清掉那一對
    - 配對只能是同一台裝置的另一個埠
    """
    old_peer_id = port.peer_port_id
    locked = await lock_ports(session, [port.id, old_peer_id, new_peer_id])  # type: ignore[list-item]
    new_peer = locked.get(new_peer_id) if new_peer_id else None
    if new_peer_id is not None:
        if new_peer_id == port.id:
            raise HTTPException(422, detail=ui_detail("port_peer_self", "不能跟自己配對"))
        if new_peer is None:
            raise HTTPException(422, detail=ui_detail("port_peer_not_found", "找不到要配對的埠"))
        if new_peer.device_id != port.device_id:
            raise HTTPException(422, detail=ui_detail(
                "port_peer_other_device", "配對只能是同一台裝置的埠（面板正面對背面）"))
        third_id = new_peer.peer_port_id
        if third_id is not None and third_id != port.id:
            third = (await lock_ports(session, [third_id])).get(third_id)
            if third is not None and not replace:
                raise HTTPException(409, detail=ui_detail(
                    "port_peer_taken", f"{new_peer.name} 已經跟 {third.name} 配對",
                    port=new_peer.name, other=third.name))
            if third is not None and third.peer_port_id == new_peer.id:
                third.peer_port_id = None
    if old_peer_id is not None and old_peer_id != new_peer_id:
        old = locked.get(old_peer_id)
        if old is not None and old.peer_port_id == port.id:
            old.peer_port_id = None
    port.peer_port_id = new_peer_id
    if new_peer is not None:
        new_peer.peer_port_id = port.id


async def ensure_outlet_free(session: AsyncSession, outlet_id: uuid.UUID, *, psu_id: uuid.UUID | None) -> None:
    """一個插座只接一顆 PSU。鎖住插座那一列再查，併發時後到的會看到前一筆而被拒絕。"""
    outlet = (await session.execute(
        select(PowerOutlet).where(PowerOutlet.id == outlet_id).with_for_update())).scalar_one_or_none()
    if outlet is None:
        raise HTTPException(422, detail=ui_detail("outlet_not_found", "找不到這個插座"))
    q = select(DevicePowerPort.name, Device.name).join(Device, Device.id == DevicePowerPort.device_id).where(
        DevicePowerPort.outlet_id == outlet_id)
    if psu_id is not None:
        q = q.where(DevicePowerPort.id != psu_id)
    hit = (await session.execute(q.limit(1))).first()
    if hit is not None:
        raise HTTPException(409, detail=ui_detail(
            "outlet_occupied", f"插座 {outlet.label} 已經接了 {hit[1]} 的 {hit[0]}",
            outlet=outlet.label, device=hit[1], port=hit[0]))
