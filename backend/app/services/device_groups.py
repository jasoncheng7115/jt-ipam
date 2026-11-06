"""裝置群組：成員、代表裝置、共用 IP（方案 B，見 models/device_group.py）。

共用 IP 的 `device_id` 一律是群組的代表裝置：
- 明確的寫入（IP 編輯、設備的主要 IP）先用 `check_ip_device` 擋，回 409 ip_owned_by_group
- 沒經過檢查的寫入（自動對應、匯入、整合同步……）在存檔時由 `_before_flush` 改回代表裝置 —— 系統裡寫
  device_id 的地方很多，靠每一處都記得檢查遲早會漏
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.sqlin import in_values
from app.models.address import IPAddress
from app.models.device import Device
from app.models.device_group import GROUP_KINDS, GROUP_ROLES, DeviceGroup

log = logging.getLogger(__name__)


class GroupError(Exception):
    def __init__(self, status: int, code: str, message: str, **params: Any) -> None:
        super().__init__(message)
        self.status, self.code, self.params = status, code, params


def check_kind(kind: str) -> None:
    if kind not in GROUP_KINDS:
        raise GroupError(422, "device_group_bad_kind", f"unknown group kind: {kind}", kind=kind)


async def members_of(session: AsyncSession, group_id: Any) -> list[Device]:
    return list((await session.execute(
        select(Device).where(Device.group_id == group_id)
        .order_by(Device.group_order.asc().nulls_last(), Device.name))).scalars().all())


async def apply_members(session: AsyncSession, group: DeviceGroup, members: list[dict[str, Any]]) -> dict[str, list[str]]:
    """整組換成這些成員（device_id、role、order）。一台設備只能在一個群組。"""
    if not members:
        raise GroupError(422, "device_group_no_members", "a group needs at least one member")
    wanted: dict[uuid.UUID, dict[str, Any]] = {}
    for m in members:
        role = m.get("role") or "member"
        if role not in GROUP_ROLES:
            raise GroupError(422, "device_group_bad_role", f"unknown role: {role}", role=role)
        wanted[uuid.UUID(str(m["device_id"]))] = {"role": role, "order": m.get("order")}
    devices = {d.id: d for d in (await session.execute(
        select(Device).where(in_values(Device.id, list(wanted))))).scalars().all()}
    missing = [str(i) for i in wanted if i not in devices]
    if missing:
        raise GroupError(404, "device_not_found", "device not found", device_id=missing[0])
    taken = [d for d in devices.values() if d.group_id is not None and d.group_id != group.id]
    if taken:
        raise GroupError(409, "device_group_member_taken", f"{taken[0].name} is already in another group",
                         device=taken[0].name)
    current = await members_of(session, group.id)
    removed = [d for d in current if d.id not in wanted]
    for d in removed:
        d.group_id, d.group_role, d.group_order = None, None, None
    for did, spec in wanted.items():
        d = devices[did]
        d.group_id, d.group_role, d.group_order = group.id, spec["role"], spec["order"]
    await session.flush()
    return {"added": [devices[i].name for i in wanted if i not in {c.id for c in current}],
            "removed": [d.name for d in removed]}


async def set_representative(session: AsyncSession, group: DeviceGroup, rep_id: Any | None) -> None:
    """代表裝置一定是成員；沒指定或原本的代表已經不是成員 → 換成排序第一台。共用 IP 跟著轉。"""
    members = await members_of(session, group.id)
    ids = {m.id for m in members}
    if rep_id is not None:
        rid = uuid.UUID(str(rep_id))
        if rid not in ids:
            raise GroupError(422, "device_group_representative_not_member",
                             "the representative device must be a member of the group")
        group.representative_id = rid
    elif group.representative_id not in ids:
        group.representative_id = members[0].id if members else None
    await sync_shared(session, group)


async def sync_shared(session: AsyncSession, group: DeviceGroup) -> None:
    if group.representative_id is None:
        return
    await session.execute(update(IPAddress).where(IPAddress.device_group_id == group.id)
                          .values(device_id=group.representative_id)
                          .execution_options(synchronize_session="fetch"))


async def set_shared_ips(session: AsyncSession, group: DeviceGroup, ip_ids: list[Any]) -> dict[str, list[str]]:
    """整組換成這些共用 IP；拿掉的回到一般 IP（留在原本的代表裝置上）。"""
    want = {uuid.UUID(str(i)) for i in ip_ids}
    rows = {ip.id: ip for ip in (await session.execute(
        select(IPAddress).where(in_values(IPAddress.id, list(want))))).scalars().all()} if want else {}
    missing = [str(i) for i in want if i not in rows]
    if missing:
        raise GroupError(404, "ip_not_found", "IP address not found", ip_id=missing[0])
    other = [ip for ip in rows.values() if ip.device_group_id not in (None, group.id)]
    if other:
        raise GroupError(409, "ip_owned_by_group", f"{other[0].ip} is shared by another group", ip=str(other[0].ip))
    current = list((await session.execute(
        select(IPAddress).where(IPAddress.device_group_id == group.id))).scalars().all())
    for ip in current:
        if ip.id not in want:
            ip.device_group_id = None
    for ip in rows.values():
        ip.device_group_id = group.id
        if group.representative_id is not None:
            ip.device_id = group.representative_id
    await session.flush()
    return {"added": [str(ip.ip) for ip in rows.values() if ip not in current],
            "removed": [str(ip.ip) for ip in current if ip.id not in want]}


async def check_ip_device(session: AsyncSession, ip: IPAddress, device_id: Any) -> None:
    """明確的寫入要把共用 IP 指給某台設備：不是群組成員就擋（409）。成員則不改 IP（仍屬群組）。"""
    if ip.device_group_id is None or device_id is None:
        return
    dev = await session.get(Device, device_id)
    if dev is None or dev.group_id != ip.device_group_id:
        g = await session.get(DeviceGroup, ip.device_group_id)
        raise GroupError(409, "ip_owned_by_group",
                         f"{ip.ip} is shared by device group {g.name if g else ''}; change it from the group",
                         ip=str(ip.ip), group=g.name if g else "")


async def summaries(session: AsyncSession, devices: list[Device]) -> dict[uuid.UUID, dict[str, Any]]:
    """設備 → 群組摘要（id、name、kind、role、order、member_count、representative）。"""
    gids = {d.group_id for d in devices if d.group_id}
    if not gids:
        return {}
    groups = {g.id: g for g in (await session.execute(
        select(DeviceGroup).where(in_values(DeviceGroup.id, list(gids))))).scalars().all()}
    counts: dict[uuid.UUID, int] = {}
    for (gid,) in (await session.execute(select(Device.group_id).where(in_values(Device.group_id, list(gids))))).all():
        counts[gid] = counts.get(gid, 0) + 1
    out: dict[uuid.UUID, dict[str, Any]] = {}
    for d in devices:
        g = groups.get(d.group_id) if d.group_id else None
        if g is not None:
            out[d.id] = {"id": str(g.id), "name": g.name, "kind": g.kind, "role": d.group_role,
                         "order": d.group_order, "member_count": counts.get(g.id, 0),
                         "representative": g.representative_id == d.id}
    return out


# ── 存檔時的安全網 ──────────────────────────────────────────────────────

def _before_flush(session: Session, _ctx: Any, _instances: Any) -> None:
    for obj in list(session.new) + list(session.dirty):
        if not isinstance(obj, IPAddress) or obj.device_group_id is None:
            continue
        with session.no_autoflush:
            g = session.get(DeviceGroup, obj.device_group_id)
        if g is None or g.representative_id is None or obj.device_id == g.representative_id:
            continue
        log.warning("IP %s is shared by device group %s: keeping it on the representative device",
                    obj.ip, g.name)
        obj.device_id = g.representative_id


if not event.contains(Session, "before_flush", _before_flush):
    event.listen(Session, "before_flush", _before_flush)
