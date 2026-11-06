"""裝置寫入時共用的檢查與連動（API 的新增／編輯、裝置匯入都走這裡）。

同一段規則寫兩份，改的時候通常只有一份被改到 —— 所以放在一起：
- 放進機櫃：U 位不可越界、不可與其他裝置（同安裝方向）重疊
- 設了主要 IP：該 IP 的 device_id 指回這台（雙向連結，IP 清單／拓樸才接得起來）
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.rack import RackPlacementError as PlacementError

__all__ = ["PlacementError", "check_placement", "create_device_checked", "link_primary_ip"]


async def check_placement(session: AsyncSession, obj: Any, *, exclude_device_id: uuid.UUID | None = None) -> None:
    """放進機櫃時的防呆；位置不完整（沒有機櫃或 U 位）就不檢查。不合法時丟 PlacementError。"""
    if obj.rack_id is None or obj.u_position is None or obj.u_size is None:
        return
    from app.services.rack import assert_placement_ok
    await assert_placement_ok(
        session, rack_id=obj.rack_id, u_position=obj.u_position, u_size=obj.u_size,
        rack_face=obj.rack_face, rack_slot=obj.rack_slot, rack_slot_span=obj.rack_slot_span,
        rack_vslot=obj.rack_vslot, rack_vslot_span=obj.rack_vslot_span,
        exclude_device_id=exclude_device_id,
    )


async def link_primary_ip(session: AsyncSession, obj: Any) -> None:
    """主要 IP 的 device_id 指回這台裝置。

    裝置群組的共用 IP：只有成員能把它設成主要 IP，而且 IP 仍屬群組、設備欄維持代表裝置；
    不是成員就丟 GroupError（409 ip_owned_by_group）。"""
    if not obj.primary_ip_id:
        return
    from app.models.address import IPAddress
    pip = await session.get(IPAddress, obj.primary_ip_id)
    if pip is not None and pip.device_group_id is not None:
        from app.services.device_groups import check_ip_device
        await check_ip_device(session, pip, obj.id)
        return
    if pip is not None and pip.device_id != obj.id:
        pip.device_id = obj.id


async def create_device_checked(session: AsyncSession, payload: Any) -> Any:
    """POST /devices 的本體（REST 端點與 AI 工具 create_device 共用）：自訂欄位、類型來源、機櫃防呆、
    主要 IP 雙向連結。`payload` 是 `DeviceCreate`；`CustomFieldError`／`PlacementError` 原樣往外丟。
    不寫稽核、不 commit（交易邊界與稽核的來源資訊由呼叫端決定）。

    2026-10-15 以前 AI 工具自己 `Device(...)`：類型不驗證（資料庫 CHECK 約束炸成看不懂的錯誤）、
    沒有機櫃 U 位防呆。
    """
    from app.models.device import Device
    from app.services.custom_field import validate_custom_fields
    cf = await validate_custom_fields(session, object_type="device", payload=payload.custom_fields)
    data = payload.model_dump()
    data["custom_fields"] = cf or None
    obj = Device(**data)
    # 建立時明確選了類型（不是表單預設的 other）就算人定的，自動判斷不碰
    if "type" in payload.model_fields_set and obj.type != "other":
        obj.type_source = "manual"
    # 放進機櫃時先防呆：U 位不可越界或與其他裝置重疊（規則與裝置匯入共用）
    await check_placement(session, obj)
    session.add(obj)
    await session.flush()
    await link_primary_ip(session, obj)
    return obj
