"""RBAC：物件級權限 cascade / wildcard / 群組 / deny-by-default / 內建角色。"""

from __future__ import annotations

import uuid

from app.models.address import IPAddress
from app.models.customer import Customer
from app.models.device import Device
from app.models.location import Location, Rack
from app.models.section import Section
from app.models.subnet import Subnet
from app.models.permission import Permission
from app.models.user import Group, User, UserGroupMember
from app.services import permission as P


async def _user(db_session, *, admin=False) -> User:
    from app.core.security import hash_password
    u = User(username=f"u-{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@t.local",
             display_name="U", password_hash=hash_password("TestPassword2026!"),
             auth_provider="local", is_active=True, is_admin=admin)
    db_session.add(u); await db_session.flush()
    return u


async def _hierarchy(db_session):
    c = Customer(name=f"c-{uuid.uuid4().hex[:6]}")
    db_session.add(c); await db_session.flush()
    s = Section(name=f"s-{uuid.uuid4().hex[:6]}", strict_mode=False, display_order=0, customer_id=c.id)
    db_session.add(s); await db_session.flush()
    sn = Subnet(cidr="10.80.0.0/24", section_id=s.id)
    db_session.add(sn); await db_session.flush()
    ip = IPAddress(subnet_id=sn.id, ip="10.80.0.5", state="active")
    db_session.add(ip); await db_session.flush()
    loc = Location(name=f"loc-{uuid.uuid4().hex[:6]}")
    db_session.add(loc); await db_session.flush()
    rack = Rack(name=f"r-{uuid.uuid4().hex[:6]}", location_id=loc.id, u_height=42)
    db_session.add(rack); await db_session.flush()
    dev = Device(name=f"d-{uuid.uuid4().hex[:6]}", type="switch", rack_id=rack.id)
    db_session.add(dev); await db_session.flush()
    return c, s, sn, ip, loc, rack, dev


def _grant(db_session, *, otype, oid, principal_type, principal_id, level):
    db_session.add(Permission(object_type=otype, object_id=oid,
                              principal_type=principal_type, principal_id=principal_id, level=level))


async def test_deny_by_default(db_session):
    u = await _user(db_session)
    c, s, sn, ip, loc, rack, dev = await _hierarchy(db_session)
    assert await P.get_object_permission(db_session, user=u, object_type="subnet", object_id=sn.id) == "none"
    assert await P.visible_ids(db_session, user=u, object_type="subnet") == set()


async def test_admin_sees_all(db_session):
    u = await _user(db_session, admin=True)
    assert await P.visible_ids(db_session, user=u, object_type="device") is None
    c, s, sn, *_ = await _hierarchy(db_session)
    assert await P.get_object_permission(db_session, user=u, object_type="subnet", object_id=sn.id) == "admin"


async def test_cascade_customer_to_ip(db_session):
    u = await _user(db_session)
    c, s, sn, ip, loc, rack, dev = await _hierarchy(db_session)
    _grant(db_session, otype="customer", oid=c.id, principal_type="user", principal_id=u.id, level="read")
    await db_session.flush()
    # customer → section → subnet → ip 全部繼承 read
    assert P.has_permission(await P.get_object_permission(db_session, user=u, object_type="subnet", object_id=sn.id), "read")
    assert P.has_permission(await P.get_object_permission(db_session, user=u, object_type="ip", object_id=ip.id), "read")
    assert sn.id in (await P.visible_ids(db_session, user=u, object_type="subnet"))
    assert ip.id in (await P.visible_ids(db_session, user=u, object_type="ip"))


async def test_cascade_location_to_device(db_session):
    u = await _user(db_session)
    c, s, sn, ip, loc, rack, dev = await _hierarchy(db_session)
    _grant(db_session, otype="location", oid=loc.id, principal_type="user", principal_id=u.id, level="write")
    await db_session.flush()
    assert P.has_permission(await P.get_object_permission(db_session, user=u, object_type="device", object_id=dev.id), "write")
    assert dev.id in (await P.visible_ids(db_session, user=u, object_type="device"))
    assert rack.id in (await P.visible_ids(db_session, user=u, object_type="rack"))


async def test_group_grant_and_wildcard(db_session):
    u = await _user(db_session)
    g = Group(name=f"g-{uuid.uuid4().hex[:6]}")
    db_session.add(g); await db_session.flush()
    db_session.add(UserGroupMember(user_id=u.id, group_id=g.id))
    c, s, sn, ip, *_ = await _hierarchy(db_session)
    # 群組 wildcard read on subnet → 全部 subnet 可見
    _grant(db_session, otype="subnet", oid=None, principal_type="group", principal_id=g.id, level="read")
    await db_session.flush()
    assert await P.visible_ids(db_session, user=u, object_type="subnet") is None
    assert P.has_permission(await P.get_object_permission(db_session, user=u, object_type="subnet", object_id=sn.id), "read")


async def test_seed_default_roles_idempotent(db_session):
    n1 = await P.seed_default_roles(db_session)
    assert n1 == 5
    n2 = await P.seed_default_roles(db_session)
    assert n2 == 0
    # 系統管理員 角色有 7 個 wildcard admin 授權
    from sqlalchemy import select
    admin_grp = (await db_session.execute(select(Group).where(Group.name == "系統管理員"))).scalar_one()
    grants = (await db_session.execute(
        select(Permission).where(Permission.principal_id == admin_grp.id, Permission.object_id.is_(None))
    )).scalars().all()
    assert len(grants) == 7 and all(x.level == "admin" for x in grants)
