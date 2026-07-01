"""SSH 連線管理權限：can_use_ssh 各組合（deny-by-default）。"""

from __future__ import annotations

import uuid

from app.models.address import IPAddress
from app.models.permission import Permission
from app.models.section import Section
from app.models.subnet import Subnet
from app.models.user import User
from app.services.permission import can_use_ssh


async def _user(db_session, *, admin=False, can_ssh=False) -> User:
    from app.core.security import hash_password
    u = User(
        username=f"u-{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@t.local",
        display_name="U", password_hash=hash_password("TestPassword2026!"),
        auth_provider="local", is_active=True, is_admin=admin, can_ssh=can_ssh,
    )
    db_session.add(u)
    await db_session.flush()
    return u


async def _ip(db_session, *, ssh_enabled: bool) -> tuple[Subnet, IPAddress]:
    s = Section(name=f"s-{uuid.uuid4().hex[:6]}", strict_mode=False, display_order=0)
    db_session.add(s)
    await db_session.flush()
    sn = Subnet(cidr="10.90.0.0/24", section_id=s.id)
    db_session.add(sn)
    await db_session.flush()
    ip = IPAddress(subnet_id=sn.id, ip="10.90.0.5", state="active", ssh_enabled=ssh_enabled)
    db_session.add(ip)
    await db_session.flush()
    return sn, ip


def _grant(db_session, *, oid, user_id, level):
    db_session.add(Permission(
        object_type="subnet", object_id=oid,
        principal_type="user", principal_id=user_id, level=level,
    ))


async def test_disabled_blocks_everyone_including_admin(db_session):
    admin = await _user(db_session, admin=True)
    _sn, ip = await _ip(db_session, ssh_enabled=False)
    assert await can_use_ssh(db_session, user=admin, ip=ip) is False


async def test_admin_can_use_when_enabled(db_session):
    admin = await _user(db_session, admin=True)
    _sn, ip = await _ip(db_session, ssh_enabled=True)
    assert await can_use_ssh(db_session, user=admin, ip=ip) is True


async def test_write_on_subnet_can_use(db_session):
    u = await _user(db_session)
    sn, ip = await _ip(db_session, ssh_enabled=True)
    _grant(db_session, oid=sn.id, user_id=u.id, level="write")
    await db_session.flush()
    assert await can_use_ssh(db_session, user=u, ip=ip) is True


async def test_capability_plus_read_can_use(db_session):
    u = await _user(db_session, can_ssh=True)
    sn, ip = await _ip(db_session, ssh_enabled=True)
    _grant(db_session, oid=sn.id, user_id=u.id, level="read")
    await db_session.flush()
    assert await can_use_ssh(db_session, user=u, ip=ip) is True


async def test_read_only_without_capability_cannot_use(db_session):
    u = await _user(db_session, can_ssh=False)
    sn, ip = await _ip(db_session, ssh_enabled=True)
    _grant(db_session, oid=sn.id, user_id=u.id, level="read")
    await db_session.flush()
    assert await can_use_ssh(db_session, user=u, ip=ip) is False


async def test_capability_without_visibility_cannot_use(db_session):
    # 有連線管理權限，但對該子網路完全沒有授權（看不到）→ 不可用（deny-by-default）
    u = await _user(db_session, can_ssh=True)
    _sn, ip = await _ip(db_session, ssh_enabled=True)
    assert await can_use_ssh(db_session, user=u, ip=ip) is False
