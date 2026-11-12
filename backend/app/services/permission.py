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

from sqlalchemy import and_, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sqlin import in_values
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


# 建立時可以掛的上層（write_guards.require_create 的 parents／tenant）；區段、單位、機房只看萬用授權
_CREATE_PARENTS: dict[str, list[str]] = {
    "customer": [], "section": [], "location": [],
    "rack": ["location"],
    "device": ["rack", "location", "customer"],
    "subnet": ["section", "customer"],
    "ip": ["subnet", "section", "customer"],
}


async def creatable_types(session: AsyncSession, *, user: User) -> list[str]:
    """使用者可以新增哪些類型（前端決定「新增」按鈕能不能按）。

    跟 require_create 同一套規則：這個類型（或它的上層類型）有萬用寫入權，或對某個可以掛的上層
    （含那個上層的上層）有寫入權。只是決定按鈕，真正的判斷在端點。
    """
    if user.is_admin:
        return list(_ALL_TYPES)
    conds = _principal_conds(user, await _user_group_ids(session, user.id))
    rows = (await session.execute(
        select(Permission.object_type, Permission.object_id)
        .where(or_(*conds), Permission.level.in_(("write", "admin")))
    )).all()
    wildcard = {t for t, oid in rows if oid is None}
    scoped = {t for t, oid in rows if oid is not None}
    out = []
    for t in _ALL_TYPES:
        if t in wildcard or set(_ANCESTOR_TYPES[t]) & wildcard:
            out.append(t)
            continue
        reach: set[str] = set()
        for p in _CREATE_PARENTS[t]:
            reach.add(p)
            reach.update(_ANCESTOR_TYPES[p])
        if reach & scoped:
            out.append(t)
    return out


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


async def can_use_rustdesk(session: AsyncSession, *, user: User, ip: Any) -> bool:
    """是否給「以 RustDesk 連線」的網址。比照 VNC：這個 IP 要勾了 rustdesk_enabled，再看遠端主控台權限
    （admin、子網路 write、或 can_ssh 且看得到）。連線本身在 RustDesk 客戶端與對方之間，對方的 RustDesk 密碼仍是最後一道。"""
    if not getattr(ip, "rustdesk_enabled", False):
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


async def can_use_sftp(session: AsyncSession, *, user: User, ip: Any) -> bool:
    """是否可對此 IP 開 SFTP 檔案瀏覽器（deny-by-default）。

    與 can_use_ssh 相同的授權模型，唯一差別是檢查 `sftp_enabled` —— 傳檔與開終端機
    是兩個獨立的開關（有些主機只想開放送設定檔／取 log）。授權強度刻意與 SSH 相同：
    能讀寫遠端檔案的人，實質能力與能開 shell 的人是同一級，不該因為「只是傳檔」而放寬。
    """
    if not getattr(ip, "sftp_enabled", False):
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
        if granted.get("section"): conds.append(in_values(Section.id, granted["section"]))
        if granted.get("customer"): conds.append(in_values(Section.customer_id, granted["customer"]))
        return await ids_of(Section.id, *conds)
    if object_type == "rack":
        conds = []
        if granted.get("rack"): conds.append(in_values(Rack.id, granted["rack"]))
        if granted.get("location"): conds.append(in_values(Rack.location_id, granted["location"]))
        return await ids_of(Rack.id, *conds)
    if object_type == "subnet":
        vis_sections = await _resolve_visible(session, "section", granted)  # 含 customer→section
        conds = []
        if granted.get("subnet"): conds.append(in_values(Subnet.id, granted["subnet"]))
        if vis_sections: conds.append(in_values(Subnet.section_id, vis_sections))
        if granted.get("customer"): conds.append(in_values(Subnet.customer_id, granted["customer"]))
        return await ids_of(Subnet.id, *conds)
    if object_type == "ip":
        vis_subnets = await _resolve_visible(session, "subnet", granted)
        conds = []
        if granted.get("ip"): conds.append(in_values(IPAddress.id, granted["ip"]))
        if vis_subnets: conds.append(in_values(IPAddress.subnet_id, vis_subnets))
        if granted.get("customer"): conds.append(in_values(IPAddress.customer_id, granted["customer"]))
        return await ids_of(IPAddress.id, *conds)
    if object_type == "device":
        vis_racks = await _resolve_visible(session, "rack", granted)
        conds = []
        if granted.get("device"): conds.append(in_values(Device.id, granted["device"]))
        if vis_racks: conds.append(in_values(Device.rack_id, vis_racks))
        if granted.get("location"): conds.append(in_values(Device.location_id, granted["location"]))
        if granted.get("customer"): conds.append(in_values(Device.customer_id, granted["customer"]))
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
    # 戰情室檢視者（2026-10-22）：所有物件唯讀，另外在戰情室（唯讀看板）看得到管理員限定的統計
    # （異常、DNS 比對、憑證、監控覆蓋、IP 變更評估、採集）。只放寬戰情室：那些功能自己的頁面、
    # REST 與 MCP 照舊只給管理員（見 services/warroom/common.full_access）。給電視用的帳號就掛這個
    "戰情室檢視者": dict.fromkeys(_ALL_TYPES, "read"),
}
WARROOM_ROLE = "戰情室檢視者"
ROLE_DESCRIPTIONS: dict[str, str] = {
    WARROOM_ROLE: "戰情室看得到全部主題（含異常、憑證、採集等統計）；其他頁面唯讀、不能修改",
}


# 啟動時 seed 用的 advisory lock 鍵（任意常數，只要各 seed 不同）
SEED_LOCK_ROLES = 0x6A74_0001


async def seed_default_roles(session: AsyncSession) -> int:
    """建立 5 個內建角色（群組 + wildcard 授權），冪等。回傳新建角色數。

    ⚠️ 冪等（重跑安全）不等於**並行安全**：多個 uvicorn worker 是同時啟動的，會同時看到
    空表、同時 INSERT，輸的那幾個吃到 UniqueViolation。功能沒壞（贏的那個已經建好），但
    每次全新安裝的 journal 都會噴一整段紅色 SQL 例外，客戶第一次看 log 就先被嚇到。
    因此在這裡拿 advisory lock 讓呼叫端排隊 —— 鎖要跟它保護的不變式放在一起，任何
    呼叫路徑都受保護。鎖在同一個交易裡，函式結尾 commit 時自動釋放；後面的 worker 在
    READ COMMITTED 下每個 statement 取新快照，看得到前一個已建好的資料。
    """
    from app.models.user import Group

    await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": SEED_LOCK_ROLES})
    created = 0
    for role_name, grants in DEFAULT_ROLES.items():
        grp = (await session.execute(
            select(Group).where(Group.name == role_name)
        )).scalar_one_or_none()
        if grp is None:
            grp = Group(name=role_name, is_builtin=True, description=ROLE_DESCRIPTIONS.get(role_name))
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


async def purge_permissions_for_object(
    session: AsyncSession, *, object_type: str, object_id: Any,
) -> int:
    """物件被刪掉時，一併清掉指向它的授權。回傳清掉幾筆。

    `permissions.object_id` 沒有外鍵（它指向七種表其中之一），所以沒有人會自動清。
    留著雖然不會讓誰多拿到權限（物件已經不存在，比對永遠不會命中），但那是**看不見的
    垃圾**：權限頁列得出來卻點不進去，稽核時也解釋不了。與 principal 側同一套處理。
    """
    from sqlalchemy import delete as _delete

    from app.models.permission import Permission

    rows = (await session.execute(
        _delete(Permission).where(
            Permission.object_type == object_type,
            Permission.object_id == object_id,
        ).returning(Permission.id)
    )).scalars().all()
    return len(rows)


_CHAIN_TYPES = {"location": "location", "rack": "rack", "device": "device", "vmnode": "device",
                "ip": "ip", "subnet": "subnet", "section": "section"}


async def visible_chain(session: AsyncSession, user: User, chain: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """關係鏈（IP 頁、裝置頁的上下關係圖）依讀者逐一過濾：看不到的物件整個節點拿掉。

    以前只檢查起點，鏈上串出來的裝置、機櫃、地點、子網路、區段名稱都直接回（2026-10-07 稽核）。
    虛擬機與只有名稱的 PVE 節點來自虛擬化整合，屬於全域資料，要全域讀取。
    """
    if user.is_admin:
        return chain
    from app.mcp.tools import has_global_read
    global_read: bool | None = None
    out = []
    for node in chain:
        t, nid = node.get("type"), str(node.get("id") or "")
        otype = _CHAIN_TYPES.get(str(t))
        try:
            oid = uuid.UUID(nid) if otype else None
        except ValueError:
            oid = None
        if otype and oid is not None:
            ok = has_permission(await get_object_permission(session, user=user, object_type=otype, object_id=oid),  # type: ignore[arg-type]
                                "read")
        else:
            if global_read is None:
                global_read = await has_global_read(session, user)
            ok = global_read
        if ok:
            out.append(node)
    return out
