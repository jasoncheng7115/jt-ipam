"""RBAC 權限服務（OWASP A01：deny-by-default）。

物件級權限存於 `permissions` 表：(object_type, object_id, principal[user/group], level)。
- object_id = NULL → 「全部」該類型（wildcard 授權）
- 層級：none < read < write < admin
- 階層繼承（cascade）：授權上層 → 自動涵蓋下層
    customer → section → subnet → ip   （customer 也直接涵蓋掛該 customer 的 subnet/device/ip）
    location → rack → device
- is_admin=True → superuser，一律 admin
- 計算取 user 自身 + 所屬 group 的所有相關權限的最高層級
- 沒設定 = none = 拒絕

對外：
- get_object_permission(...)：單一物件的有效層級（含 cascade + wildcard）
- visible_ids(...)：批次回該 user 對某類型可見的 id 集合（None = 全部），給 list/dropdown 過濾用
- filter_visible(...)：從候選 id 篩出可見的
- seed_default_roles(session)：建立 5 個內建角色（群組 + wildcard 授權），冪等
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any, Literal

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.permission import Permission
from app.models.user import User, UserGroupMember

PermLevel = Literal["none", "read", "write", "admin"]
ObjectType = Literal["customer", "section", "subnet", "ip", "device", "rack", "location"]

_LEVEL_RANK: dict[str, int] = {"none": 0, "read": 1, "write": 2, "admin": 3}

# 各類型「會被哪些上層類型向下涵蓋」（判 wildcard 與範圍展開用）
_ANCESTOR_TYPES: dict[str, list[str]] = {
    "customer": [],
    "location": [],
    "section": ["customer"],
    "subnet": ["section", "customer"],
    "ip": ["subnet", "section", "customer"],
    "rack": ["location"],
    "device": ["rack", "location", "customer"],
}


def _max(a: str, b: str) -> str:
    return a if _LEVEL_RANK.get(a, 0) >= _LEVEL_RANK.get(b, 0) else b


def has_permission(actual: str, required: str) -> bool:
    return _LEVEL_RANK.get(actual, 0) >= _LEVEL_RANK.get(required, 0)


async def _user_group_ids(session: AsyncSession, user_id: uuid.UUID) -> list[uuid.UUID]:
    stmt = select(UserGroupMember.group_id).where(UserGroupMember.user_id == user_id)
    return [row[0] for row in (await session.execute(stmt)).all()]


def _principal_conds(user: User, group_ids: list[uuid.UUID]) -> list[Any]:
    principals: list[tuple[str, uuid.UUID]] = [("user", user.id)]
    principals.extend(("group", gid) for gid in group_ids)
    return [
        and_(Permission.principal_type == p, Permission.principal_id == i)
        for p, i in principals
    ]


async def _parents(session: AsyncSession, otype: str, oid: uuid.UUID) -> list[tuple[str, uuid.UUID]]:
    """單一物件的直系上層 (type, id)。"""
    from app.models.address import IPAddress
    from app.models.device import Device
    from app.models.location import Rack
    from app.models.section import Section
    from app.models.subnet import Subnet

    out: list[tuple[str, uuid.UUID]] = []
    if otype == "ip":
        r = (await session.execute(
            select(IPAddress.subnet_id, IPAddress.customer_id).where(IPAddress.id == oid)
        )).first()
        if r:
            if r[0]: out.append(("subnet", r[0]))
            if r[1]: out.append(("customer", r[1]))
    elif otype == "subnet":
        r = (await session.execute(
            select(Subnet.section_id, Subnet.customer_id).where(Subnet.id == oid)
        )).first()
        if r:
            if r[0]: out.append(("section", r[0]))
            if r[1]: out.append(("customer", r[1]))
    elif otype == "section":
        r = (await session.execute(select(Section.customer_id).where(Section.id == oid))).first()  # type: ignore[assignment]
        if r and r[0]: out.append(("customer", r[0]))
    elif otype == "device":
        r = (await session.execute(  # type: ignore[assignment]
            select(Device.rack_id, Device.location_id, Device.customer_id).where(Device.id == oid)
        )).first()
        if r:
            if r[0]: out.append(("rack", r[0]))
            if r[1]: out.append(("location", r[1]))
            if r[2]: out.append(("customer", r[2]))
    elif otype == "rack":
        r = (await session.execute(select(Rack.location_id).where(Rack.id == oid))).first()  # type: ignore[assignment]
        if r and r[0]: out.append(("location", r[0]))
    return out


async def _ancestor_chain(
    session: AsyncSession, otype: str, oid: uuid.UUID,
) -> list[tuple[str, uuid.UUID]]:
    """完整祖先鏈（不含自己），遞迴展開。"""
    seen: set[tuple[str, uuid.UUID]] = set()
    queue = await _parents(session, otype, oid)
    out: list[tuple[str, uuid.UUID]] = []
    while queue:
        t, i = queue.pop()
        if (t, i) in seen:
            continue
        seen.add((t, i))
        out.append((t, i))
        queue.extend(await _parents(session, t, i))
    return out


async def get_object_permission(
    session: AsyncSession, *, user: User, object_type: ObjectType, object_id: uuid.UUID,
) -> PermLevel:
    """user 對單一 object 的有效層級（含 wildcard + 階層繼承）。"""
    if user.is_admin:
        return "admin"
    conds = _principal_conds(user, await _user_group_ids(session, user.id))

    chain: list[tuple[str, uuid.UUID]] = [(object_type, object_id)]
    chain.extend(await _ancestor_chain(session, object_type, object_id))

    best = "none"
    for t, i in chain:
        stmt = select(Permission.level).where(
            Permission.object_type == t,
            or_(Permission.object_id == i, Permission.object_id.is_(None)),
            or_(*conds),
        )
        for (lvl,) in (await session.execute(stmt)).all():
            best = _max(best, lvl)
            if best == "admin":
                return "admin"
    return best  # type: ignore[return-value]


async def has_any_write(session: AsyncSession, *, user: User) -> bool:
    """使用者（或其群組）是否擁有任一 write/admin 授權。
    前端用來決定是否顯示/啟用「新增 / 編輯 / 刪除」等異動按鈕（純唯讀帳號 → False）。"""
    if user.is_admin:
        return True
    conds = _principal_conds(user, await _user_group_ids(session, user.id))
    row = (await session.execute(
        select(Permission.id)
        .where(or_(*conds), Permission.level.in_(("write", "admin")))
        .limit(1)
    )).first()
    return row is not None


async def can_use_ssh(session: AsyncSession, *, user: User, ip: Any) -> bool:
    """是否可對此 IP 開 SSH 終端機（deny-by-default）。

    條件：IP 已啟用 SSH，且使用者為 (a) admin、(b) 對該 IP 所屬子網路有 write、
    或 (c) 具獨立「連線管理權限」(can_ssh) 且至少對該子網路有 read。
    看不到該 IP（子網路權限為 none）一律不可用。
    """
    if not getattr(ip, "ssh_enabled", False):
        return False
    if user.is_admin:
        return True
    level = await get_object_permission(
        session, user=user, object_type="subnet", object_id=ip.subnet_id
    )
    if level == "none":
        return False
    if has_permission(level, "write"):
        return True
    return bool(getattr(user, "can_ssh", False))


async def can_use_rdp(session: AsyncSession, *, user: User, ip: Any) -> bool:
    """是否可對此 IP 開 RDP 連線（deny-by-default）。

    與 can_use_ssh 完全相同的授權模型，唯一差別是檢查 rdp_enabled；
    連線管理權限沿用同一個 can_ssh（泛用遠端主控）。
    """
    if not getattr(ip, "rdp_enabled", False):
        return False
    if user.is_admin:
        return True
    level = await get_object_permission(
        session, user=user, object_type="subnet", object_id=ip.subnet_id
    )
    if level == "none":
        return False
    if has_permission(level, "write"):
        return True
    return bool(getattr(user, "can_ssh", False))


async def can_use_vnc(session: AsyncSession, *, user: User, ip: Any) -> bool:
    """是否可對此 IP 開 VNC 連線（deny-by-default）。

    與 can_use_ssh/can_use_rdp 相同授權模型，唯一差別是檢查 vnc_enabled。
    """
    if not getattr(ip, "vnc_enabled", False):
        return False
    if user.is_admin:
        return True
    level = await get_object_permission(
        session, user=user, object_type="subnet", object_id=ip.subnet_id
    )
    if level == "none":
        return False
    if has_permission(level, "write"):
        return True
    return bool(getattr(user, "can_ssh", False))


async def can_use_bmc(session: AsyncSession, *, user: User, ip: Any) -> bool:
    """是否可對此 IP 開 BMC OOB主控台（IPMI SOL；deny-by-default）。

    與 can_use_ssh 相同授權模型（同等級），唯一差別是檢查 bmc_enabled。
    """
    if not getattr(ip, "bmc_enabled", False):
        return False
    if user.is_admin:
        return True
    level = await get_object_permission(
        session, user=user, object_type="subnet", object_id=ip.subnet_id
    )
    if level == "none":
        return False
    if has_permission(level, "write"):
        return True
    return bool(getattr(user, "can_ssh", False))


async def can_use_novnc(session: AsyncSession, *, user: User, ip: Any) -> bool:
    """是否可對此 IP 開 PVE 主控台（noVNC/xterm，deny-by-default）。

    與 can_use_vnc 相同的 jt-ipam 物件層級授權，外加：此 IP 必須對應到 Proxmox VE 的 VM/CT。
    （連線當下仍以使用者輸入的 PVE 帳密 + PVE 端權限把關，這裡只是 jt-ipam 這側的閘門。）
    """
    if not getattr(ip, "novnc_enabled", False):
        return False
    from app.services.pve_console import resolve_pve_target
    if await resolve_pve_target(session, ip) is None:
        return False
    if user.is_admin:
        return True
    level = await get_object_permission(
        session, user=user, object_type="subnet", object_id=ip.subnet_id
    )
    if level == "none":
        return False
    if has_permission(level, "write"):
        return True
    return bool(getattr(user, "can_ssh", False))


async def visible_ids(
    session: AsyncSession, *, user: User, object_type: ObjectType, required: PermLevel = "read",
) -> set[uuid.UUID] | None:
    """user 對某類型可見的 object id 集合。None = 全部可見（admin 或有 wildcard）。"""
    if user.is_admin:
        return None
    conds = _principal_conds(user, await _user_group_ids(session, user.id))
    rows = (await session.execute(
        select(Permission.object_type, Permission.object_id, Permission.level).where(or_(*conds))
    )).all()
    req = _LEVEL_RANK[required]

    wildcard_types: set[str] = set()
    granted: dict[str, set[uuid.UUID]] = defaultdict(set)
    for otype, oid, lvl in rows:
        if _LEVEL_RANK.get(lvl, 0) < req:
            continue
        if oid is None:
            wildcard_types.add(otype)
        else:
            granted[otype].add(oid)

    if object_type in wildcard_types or (set(_ANCESTOR_TYPES[object_type]) & wildcard_types):
        return None
    return await _resolve_visible(session, object_type, granted)


async def _resolve_visible(
    session: AsyncSession, object_type: str, granted: dict[str, set[uuid.UUID]],
) -> set[uuid.UUID]:
    """依已授權的各層具體 id，展開出 object_type 的可見 id 集合。"""
    from app.models.address import IPAddress
    from app.models.device import Device
    from app.models.location import Rack
    from app.models.section import Section
    from app.models.subnet import Subnet

    async def ids_of(model_id_col: Any, *conds: Any) -> set[uuid.UUID]:
        if not conds:
            return set()
        rows = (await session.execute(select(model_id_col).where(or_(*conds)))).all()
        return {r[0] for r in rows}

    if object_type == "customer":
        return set(granted.get("customer", set()))
    if object_type == "location":
        return set(granted.get("location", set()))
    if object_type == "section":
        conds = []
        if granted.get("section"): conds.append(Section.id.in_(granted["section"]))
        if granted.get("customer"): conds.append(Section.customer_id.in_(granted["customer"]))
        return await ids_of(Section.id, *conds)
    if object_type == "rack":
        conds = []
        if granted.get("rack"): conds.append(Rack.id.in_(granted["rack"]))
        if granted.get("location"): conds.append(Rack.location_id.in_(granted["location"]))
        return await ids_of(Rack.id, *conds)
    if object_type == "subnet":
        vis_sections = await _resolve_visible(session, "section", granted)  # 含 customer→section
        conds = []
        if granted.get("subnet"): conds.append(Subnet.id.in_(granted["subnet"]))
        if vis_sections: conds.append(Subnet.section_id.in_(vis_sections))
        if granted.get("customer"): conds.append(Subnet.customer_id.in_(granted["customer"]))
        return await ids_of(Subnet.id, *conds)
    if object_type == "ip":
        vis_subnets = await _resolve_visible(session, "subnet", granted)
        conds = []
        if granted.get("ip"): conds.append(IPAddress.id.in_(granted["ip"]))
        if vis_subnets: conds.append(IPAddress.subnet_id.in_(vis_subnets))
        if granted.get("customer"): conds.append(IPAddress.customer_id.in_(granted["customer"]))
        return await ids_of(IPAddress.id, *conds)
    if object_type == "device":
        vis_racks = await _resolve_visible(session, "rack", granted)
        conds = []
        if granted.get("device"): conds.append(Device.id.in_(granted["device"]))
        if vis_racks: conds.append(Device.rack_id.in_(vis_racks))
        if granted.get("location"): conds.append(Device.location_id.in_(granted["location"]))
        if granted.get("customer"): conds.append(Device.customer_id.in_(granted["customer"]))
        return await ids_of(Device.id, *conds)
    return set()


async def filter_visible(
    session: AsyncSession, *, user: User, object_type: ObjectType,
    object_ids: list[uuid.UUID], required: PermLevel = "read",
) -> list[uuid.UUID]:
    """從候選 id 篩出可見的（給 list endpoint 用）。"""
    vis = await visible_ids(session, user=user, object_type=object_type, required=required)
    if vis is None:
        return object_ids
    return [oid for oid in object_ids if oid in vis]


# ─────────────────── 內建角色 ───────────────────
_ALL_TYPES: list[str] = ["customer", "section", "subnet", "ip", "device", "rack", "location"]

# name → {object_type: level}（object_id=NULL wildcard）；部門管理員無 wildcard（依指派）
DEFAULT_ROLES: dict[str, dict[str, str]] = {
    "系統管理員": dict.fromkeys(_ALL_TYPES, "admin"),
    "唯讀檢視者": dict.fromkeys(_ALL_TYPES, "read"),
    "網路操作員": {**dict.fromkeys(_ALL_TYPES, "read"),
                   "subnet": "write", "ip": "write", "device": "write"},
    "稽核員": dict.fromkeys(_ALL_TYPES, "read"),
    "部門管理員": {},  # 無 wildcard；由管理員指派特定單位/區段為 admin
}


async def seed_default_roles(session: AsyncSession) -> int:
    """建立 5 個內建角色（群組 + wildcard 授權），冪等。回傳新建角色數。"""
    from app.models.user import Group

    created = 0
    for role_name, grants in DEFAULT_ROLES.items():
        grp = (await session.execute(
            select(Group).where(Group.name == role_name)
        )).scalar_one_or_none()
        if grp is None:
            grp = Group(name=role_name, is_builtin=True)
            session.add(grp)
            await session.flush()
            created += 1
        for otype, level in grants.items():
            exists = (await session.execute(
                select(Permission.id).where(
                    Permission.object_type == otype,
                    Permission.object_id.is_(None),
                    Permission.principal_type == "group",
                    Permission.principal_id == grp.id,
                )
            )).scalar_one_or_none()
            if exists is None:
                session.add(Permission(
                    object_type=otype, object_id=None,
                    principal_type="group", principal_id=grp.id, level=level,
                ))
    await session.commit()
    return created
