"""IP 申請審核政策：can_approve 依模式（admin/designated）+ 職責分離。"""

from __future__ import annotations

import uuid

import pytest

from app.models.ip_request import IPRequest
from app.models.section import Section
from app.models.subnet import Subnet
from app.models.user import User
from app.services.ip_request_policy import (
    can_approve,
    is_global_approver,
    set_policy,
)


async def _user(db_session, *, admin=False) -> User:
    from app.core.security import hash_password
    u = User(username=f"u-{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:6]}@t.local",
             display_name="U", password_hash=hash_password("TestPassword2026!"),
             auth_provider="local", is_active=True, is_admin=admin)
    db_session.add(u)
    await db_session.flush()
    return u


async def _req(db_session, requester: User) -> IPRequest:
    sec = Section(name=f"s-{uuid.uuid4().hex[:6]}")
    db_session.add(sec)
    await db_session.flush()
    sub = Subnet(section_id=sec.id, cidr="10.20.0.0/24")
    db_session.add(sub)
    await db_session.flush()
    r = IPRequest(status="pending", requester_user_id=requester.id, subnet_id=sub.id,
                  purpose="test")
    db_session.add(r)
    await db_session.flush()
    return r


@pytest.mark.anyio
async def test_admin_mode(db_session):
    admin = await _user(db_session, admin=True)
    await set_policy(db_session, data={"approver_mode": "admin"}, updated_by_user_id=admin.id)
    dept = await _user(db_session)
    requester = await _user(db_session)
    r = await _req(db_session, requester)
    assert await can_approve(db_session, admin, r) is True
    assert await can_approve(db_session, dept, r) is False
    assert await is_global_approver(db_session, dept) is False


@pytest.mark.anyio
async def test_designated_user(db_session):
    approver = await _user(db_session)
    other = await _user(db_session)
    requester = await _user(db_session)
    await set_policy(db_session, data={
        "approver_mode": "designated", "designated_user_ids": [str(approver.id)],
    }, updated_by_user_id=approver.id)
    r = await _req(db_session, requester)
    assert await can_approve(db_session, approver, r) is True
    assert await can_approve(db_session, other, r) is False
    assert await is_global_approver(db_session, approver) is True


@pytest.mark.anyio
async def test_self_approval_gate(db_session):
    approver = await _user(db_session)
    await set_policy(db_session, data={
        "approver_mode": "designated", "designated_user_ids": [str(approver.id)],
        "allow_self_approve": False,
    }, updated_by_user_id=approver.id)
    own = await _req(db_session, approver)   # approver 自己送的
    assert await can_approve(db_session, approver, own) is False
    # 開放自核後可以
    await set_policy(db_session, data={
        "approver_mode": "designated", "designated_user_ids": [str(approver.id)],
        "allow_self_approve": True,
    }, updated_by_user_id=approver.id)
    assert await can_approve(db_session, approver, own) is True


@pytest.mark.anyio
async def test_sequential_stages_flow(db_session):
    """依序多關卡：step0 通過才輪到 step1；最後一關通過才 fulfilled。"""
    from app.models.subnet import Subnet
    from app.services.ip_request import record_step_approval
    from app.services.ip_request_policy import actionable_steps, get_policy

    a = await _user(db_session)
    b = await _user(db_session)
    requester = await _user(db_session)
    await set_policy(db_session, data={
        "approver_mode": "stages",
        "stages": [
            {"name": "一審", "user_ids": [str(a.id)], "group_ids": []},
            {"name": "二審", "user_ids": [str(b.id)], "group_ids": []},
        ],
    }, updated_by_user_id=a.id)
    r = await _req(db_session, requester)
    sub = await db_session.get(Subnet, r.subnet_id)

    assert await can_approve(db_session, a, r) is True     # step0 輪到 a
    assert await can_approve(db_session, b, r) is False    # 還沒輪到 b

    done = await record_step_approval(db_session, request=r, subnet=sub, approver=a, step_index=0)
    assert done is False
    assert await can_approve(db_session, a, r) is False    # a 那關過了
    assert await can_approve(db_session, b, r) is True     # 換 b

    steps_b = await actionable_steps(db_session, b, r, await get_policy(db_session))
    done2 = await record_step_approval(db_session, request=r, subnet=sub, approver=b, step_index=steps_b[0])
    assert done2 is True
    assert r.status == "fulfilled"
    assert r.allocated_ip_id is not None


@pytest.mark.anyio
async def test_parallel_signoff_flow(db_session):
    """會簽：兩關不分先後，全通過才 fulfilled。"""
    from app.models.subnet import Subnet
    from app.services.ip_request import record_step_approval

    a = await _user(db_session)
    b = await _user(db_session)
    requester = await _user(db_session)
    await set_policy(db_session, data={
        "approver_mode": "parallel",
        "stages": [
            {"name": "主管", "user_ids": [str(a.id)], "group_ids": []},
            {"name": "資安", "user_ids": [str(b.id)], "group_ids": []},
        ],
    }, updated_by_user_id=a.id)
    r = await _req(db_session, requester)
    sub = await db_session.get(Subnet, r.subnet_id)

    assert await can_approve(db_session, a, r) is True
    assert await can_approve(db_session, b, r) is True
    assert await record_step_approval(db_session, request=r, subnet=sub, approver=a, step_index=0) is False
    assert await can_approve(db_session, a, r) is False     # a 已簽，無待簽關卡
    assert await record_step_approval(db_session, request=r, subnet=sub, approver=b, step_index=1) is True
    assert r.status == "fulfilled"
