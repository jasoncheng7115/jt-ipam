"""IP 位址的建立與編輯規則（REST 端點與 AI 工具共用）。

2026-10-15 MCP 工具盤點：AI 的 update_ip 自己寫一份「改欄位」—— 主機名稱直接寫進欄位、不走人工觀測，
下一輪同步重算有效名稱就被蓋回去；allocate_ip 也沒有冷卻期守門。同一段規則寫兩份，改的時候通常只有
一份被改到（feedback_derive_dont_ask），所以 REST 的 POST／PATCH 本體搬到這裡，兩邊呼叫同一份。

- 權限守門沿用 app/api/v1/write_guards（會丟 HTTPException，與 REST 完全相同的代碼與訊息）；
  AI 工具那一側把 HTTPException 轉成工具錯誤
- 不寫稽核、不 commit：稽核需要請求的來源資訊，交易邊界由呼叫端決定
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ui_error import ui_detail
from app.models.address import IPAddress
from app.models.subnet import Subnet
from app.schemas.address import IPAddressCreate
from app.services import ip_lifecycle
from app.services.address import create_ip
from app.services.custom_field import validate_custom_fields
from app.services.hostname import apply_observation, recompute_effective, seed_observation
from app.services.ip_history import log_change, log_field_diffs


async def create_address_checked(
    session: AsyncSession, *, user: Any, subnet: Subnet, payload: IPAddressCreate,
    change_note: str | None = None,
) -> IPAddress:
    """POST /addresses 的本體：守門、冷卻期、建立、套用表單欄位、異動記錄、手動主機名稱觀測。

    呼叫端要先確認對子網路有寫入權。`IPNotInSubnet`／`IPAlreadyExists`／`CustomFieldError` 原樣往外丟。
    """
    from app.api.v1.write_guards import (
        require_admin_for_infra,
        require_parent_write,
        require_visible,
    )
    requested = payload.model_dump(exclude_unset=True)
    require_admin_for_infra(user, requested)
    await require_parent_write(session, user, "customer", payload.customer_id)
    await require_visible(session, user, "device", payload.device_id, "Device not found")

    # 冷卻期守門：這個位址剛被釋放，外面的 DNS 快取／防火牆規則可能還指著它。
    # 擋下來而不是安靜放行 —— 管理員判斷沒問題可以先解除冷卻再建立。
    cd = await ip_lifecycle.cooldown_for(session, subnet_id=payload.subnet_id, ip=payload.ip)
    if cd is not None:
        raise HTTPException(status_code=409, detail=ui_detail(
            "ip_in_cooldown",
            "此位址剛被釋放，仍在冷卻期內（外部的 DNS 快取、防火牆規則可能"
            "還指著它）。確定要現在使用，請先解除冷卻。",
            ip=payload.ip,
            until=cd.until.isoformat() if cd.until else "",
            previous_hostname=cd.previous_hostname or "",
        ))

    obj = await create_ip(
        session,
        subnet=subnet,
        ip=payload.ip,
        hostname=payload.hostname,
        description=payload.description,
        mac=payload.mac,
        state=payload.state,
    )

    # 應用後續欄位
    cf = await validate_custom_fields(session, object_type="ip", payload=payload.custom_fields)
    obj.owner = payload.owner
    obj.device_id = payload.device_id
    obj.switch_port = payload.switch_port
    obj.exclude_from_ping = payload.exclude_from_ping
    obj.liveness_from_device = payload.liveness_from_device
    obj.ptr_ignore = payload.ptr_ignore
    obj.note = payload.note
    obj.custom_fields = cf or None
    if "customer_id" in requested:
        obj.customer_id = payload.customer_id

    await log_change(session, ip=obj, event_type="created",
                     source="manual", actor_user_id=str(user.id), note=change_note)
    # feature A：把建立時填的 hostname 記成 manual 觀測
    await seed_observation(session, ip=obj, source="manual", hostname=payload.hostname)
    return obj


def ip_snapshot(obj: IPAddress) -> dict[str, Any]:
    """編輯前的值（稽核 diff 的 before，也是逐欄異動記錄的比對基準）。"""
    return {
        "hostname": obj.hostname,
        "state": obj.state,
        "mac": str(obj.mac) if obj.mac else None,
        "description": obj.description,
        "owner": obj.owner,
        "switch_port": obj.switch_port,
        "device_id": str(obj.device_id) if obj.device_id else None,
        "note": obj.note,
        "customer_id": str(obj.customer_id) if obj.customer_id else None,
    }


async def update_address_checked(
    session: AsyncSession, *, user: Any, obj: IPAddress, changes: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """PATCH /addresses/{id} 的本體。回傳 (before, 實際套用的 changes)，給呼叫端寫稽核。

    `changes` 是 `IPAddressUpdate.model_dump(exclude_unset=True)`。呼叫端要先確認對子網路有寫入權。
    `CustomFieldError` 原樣往外丟；權限守門與主控台出口不合法丟 HTTPException。
    """
    before = ip_snapshot(obj)
    changes = dict(changes)
    if "custom_fields" in changes:
        changes["custom_fields"] = await validate_custom_fields(
            session, object_type="ip", payload=changes["custom_fields"]
        ) or None

    # feature A：hostname 不直接設，改走 observation + 優先序解析
    _UNSET = object()
    hostname_change = changes.pop("hostname", _UNSET)
    pin_changed = "hostname_source_pin" in changes
    if pin_changed and not changes["hostname_source_pin"]:
        changes["hostname_source_pin"] = None  # "" → 取消 pin
    # 表單每次都會把目前顯示的主機名稱、MAC、固定來源一起送出 —— 值沒變就不是使用者的編輯。
    # 以前照單全收：只改了說明，當時顯示的 DNS 名稱就被凍結成「手動」、MAC 來源被標成手動
    # （之後換機器的新 MAC 永遠寫不進來）。2026-09-26
    def _hn(v: Any) -> str | None:
        return (str(v or "")).strip() or None

    def _mac(v: Any) -> str:
        return "".join(ch for ch in str(v or "").lower() if ch in "0123456789abcdef")

    prev_hostname, prev_pin, prev_mac = obj.hostname, obj.hostname_source_pin, obj.mac
    if hostname_change is not _UNSET and _hn(hostname_change) == _hn(prev_hostname):
        hostname_change = _UNSET
    pin_explicit = pin_changed and (changes["hostname_source_pin"] or None) != (prev_pin or None)
    mac_edited = "mac" in changes and _mac(changes["mac"]) != _mac(prev_mac)
    if "mac" in changes and not mac_edited:
        changes.pop("mac")

    from app.api.v1.write_guards import (
        can_write,
        require_admin_for_infra,
        require_move,
        require_visible,
    )
    require_admin_for_infra(user, changes, obj)
    # 裝置群組的共用 IP（方案 B）：指派／退出群組只有管理員；共用 IP 不能被指給群組以外的設備
    if "device_group_id" in changes or ("device_id" in changes and obj.device_group_id):
        await _apply_device_group(session, user, obj, changes)
    if "customer_id" in changes and changes["customer_id"] != obj.customer_id:
        await require_move(session, user, object_type="ip", object_id=obj.id,
                           dest_type="customer", dest_id=changes["customer_id"])
    if changes.get("device_id") and changes["device_id"] != obj.device_id:
        await require_visible(session, user, "device", changes["device_id"], "Device not found")
    if "jump_host_id" in changes or "console_agent_id" in changes:
        from app.services.console_route import EgressError, normalize_egress
        parent = await session.get(Subnet, obj.subnet_id) if obj.subnet_id else None
        try:
            await normalize_egress(session, changes,
                                   scan_agent_id=parent.scan_agent_id if parent is not None else None)
        except EgressError as exc:
            raise HTTPException(status_code=422, detail=ui_detail(exc.code, str(exc), **exc.params)) from exc

    for key, value in changes.items():
        setattr(obj, key, value)

    # MAC 與 hostname 同屬「多來源優先序」欄位：人工編輯的 MAC 要標記 mac_source="manual"
    # （ARP 優先序中 manual rank 最高），否則下一次掃描/ARP 同步會用 scanner 等來源把它蓋掉。
    # 清空 MAC 時一併清掉來源。
    if mac_edited:
        if obj.mac:
            obj.mac_source = "manual"
        else:
            obj.mac_source = None

    # 同步 excluded_probes ⇄ exclude_from_ping：icmp 視為同一件事，保留既有「不掃 ping」行為
    if "excluded_probes" in changes or "exclude_from_ping" in changes:
        from app.core.scan_probes import normalize_probes as _norm_probes
        if "excluded_probes" in changes:
            obj.excluded_probes = _norm_probes(obj.excluded_probes)
            obj.exclude_from_ping = "icmp" in obj.excluded_probes
        if "exclude_from_ping" in changes:
            s = set(obj.excluded_probes or [])
            s.add("icmp") if obj.exclude_from_ping else s.discard("icmp")
            obj.excluded_probes = _norm_probes(list(s))

    # 把這個 IP 指派給某裝置 → 若該裝置還沒設主要 IP，順手補上（雙向連結方便）
    if changes.get("device_id"):
        from app.models.device import Device as _Device
        dev = await session.get(_Device, changes["device_id"])
        # 改裝置的主要 IP 等於改裝置：只看得到、不能寫那台的人，掛上去就好，不順手動它
        if dev is not None and dev.primary_ip_id is None and await can_write(session, user, "device", dev.id):
            dev.primary_ip_id = obj.id

    # feature B：逐欄記錄人為編輯（hostname 改走 apply_observation，這裡不含它）
    await log_field_diffs(
        session, ip=obj, before=before, changes=changes,
        source="manual", actor_user_id=str(user.id),
    )
    # 手動把 MAC 改成另一張網卡＝這個位址換了一台設備：上一台的辨識結果、OCS 盤點、自己報的名稱都清掉
    if mac_edited and prev_mac and obj.mac:
        from app.services.device_turnover import on_device_replaced
        await on_device_replaced(session, ip=obj, source="manual", actor_user_id=str(user.id))

    # feature A：套用 hostname 觀測 / pin 變更後重算有效 hostname（內含 hostname_changed 記錄）
    if hostname_change is not _UNSET:
        await apply_observation(
            session, ip=obj, source="manual",
            hostname=hostname_change, actor_user_id=str(user.id),
        )
        # 使用者明確輸入了新名稱，全域順序卻讓別的來源蓋過它（例如手動排在 DNS 之後）：
        # 以前存完馬上被蓋回去、畫面沒有任何提示。沒另外指定固定來源時，就固定用手動 ——
        # 輸入的會生效，表單上的「固定主機名稱來源」也看得出為什麼。
        typed = _hn(hostname_change)
        if typed and _hn(obj.hostname) != typed and not pin_explicit:
            obj.hostname_source_pin = "manual"
            await recompute_effective(session, ip=obj, source="manual", actor_user_id=str(user.id))
    elif pin_explicit:
        # 只有固定來源真的改了才重算：表單每次都會帶 hostname_source_pin（沒改也帶），
        # 以前一律重算 → 沒有任何來源觀測的 IP（舊資料、匯入）存一次表單主機名稱就被清空
        await recompute_effective(
            session, ip=obj, source="manual", actor_user_id=str(user.id),
        )
    return before, changes


async def _apply_device_group(session: AsyncSession, user: Any, obj: Any, changes: dict[str, Any]) -> None:
    from app.models.device_group import DeviceGroup
    from app.services.device_groups import GroupError, check_ip_device
    if "device_group_id" in changes:
        if not getattr(user, "is_admin", False):
            raise HTTPException(status_code=403, detail=ui_detail(
                "device_group_admin_only", "Only administrators can share an IP with a device group"))
        gid = changes.pop("device_group_id")
        if gid is None:
            obj.device_group_id = None                  # 退出群組：設備欄留在原本的代表裝置上
        else:
            g = await session.get(DeviceGroup, gid)
            if g is None:
                raise HTTPException(status_code=404, detail=ui_detail("device_group_not_found", "Device group not found"))
            obj.device_group_id = g.id
            if g.representative_id is not None and g.representative_id != obj.device_id:
                changes["device_id"] = g.representative_id
                return
    if "device_id" in changes and obj.device_group_id:
        try:
            await check_ip_device(session, obj, changes["device_id"])
        except GroupError as exc:
            raise HTTPException(status_code=exc.status, detail=ui_detail(exc.code, str(exc), **exc.params)) from exc
        changes.pop("device_id")                        # 成員：IP 仍屬群組，設備欄維持代表裝置
