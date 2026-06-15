"""MCP / AI 工具的 RBAC 範圍守門測試。

對應 CLAUDE.md 三類資料分類：
  - 零權限帳號 → 連任何資料工具都不能呼叫（純計算工具除外）
  - 只被指派特定物件的部門帳號 → 不能呼叫全域基礎設施工具（NAT/VLAN/電路…）
  - 逐物件清單工具 → 只回可見範圍內的列；stats_overview 計數依範圍縮放、且不洩漏全域計數
"""

from __future__ import annotations

import uuid

import pytest

from app.mcp.tools import (
    TOOLS,
    authorize_tool,
    has_global_read,
    list_customers,
    list_locations,
    list_racks,
    stats_overview,
)
from app.models.location import Location, Rack
from app.models.permission import Permission
from app.models.subnet import Subnet
from app.models.section import Section
from app.models.customer import Customer
from app.models.user import User


async def _nonadmin(db_session) -> User:
    from app.core.security import hash_password
    u = User(username=f"na-{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@t.local",
             display_name="NA", password_hash=hash_password("TestPassword2026!"),
             auth_provider="local", is_active=True, is_admin=False)
    db_session.add(u)
    await db_session.flush()
    return u


@pytest.mark.anyio
async def test_zero_visibility_denied_everywhere(db_session, admin_user):
    """零權限非管理員：所有資料工具都被 authorize_tool 擋；純計算工具放行。"""
    u = await _nonadmin(db_session)
    await db_session.commit()
    # 資料工具 → 擋
    for name in ("list_subnets", "list_nat", "get_ip_detail", "list_racks", "stats_overview"):
        assert await authorize_tool(db_session, u, name) is not None, name
    # 純計算工具 → 放行
    for name in ("calc_ip_info", "oui_lookup"):
        assert await authorize_tool(db_session, u, name) is None, name


@pytest.mark.anyio
async def test_partial_visibility_blocks_global_infra(db_session):
    """只被指派一個 section 的部門帳號：可呼叫逐物件工具，但全域基礎設施工具一律擋。"""
    u = await _nonadmin(db_session)
    sec = Section(name=f"sec-{uuid.uuid4().hex[:6]}")
    db_session.add(sec)
    await db_session.flush()
    db_session.add(Permission(object_type="section", object_id=sec.id,
                              principal_type="user", principal_id=u.id, level="read"))
    await db_session.commit()

    assert await has_global_read(db_session, u) is False
    # 逐物件工具 → 放行
    assert await authorize_tool(db_session, u, "list_subnets") is None
    assert await authorize_tool(db_session, u, "list_sections") is None
    # 全域基礎設施工具 → 擋
    for name in ("list_nat", "list_vlans", "list_circuits", "list_firewalls",
                 "list_wazuh_agents", "dns_lookup"):
        assert await authorize_tool(db_session, u, name) is not None, name
    # 異動工具 → 需 admin
    assert await authorize_tool(db_session, u, "update_ip") is not None


@pytest.mark.anyio
async def test_list_tools_scope_rows_to_visible(db_session):
    """list_racks / list_locations / list_customers 只回可見範圍內的列。"""
    u = await _nonadmin(db_session)
    loc = Location(name=f"loc-{uuid.uuid4().hex[:6]}")
    db_session.add(loc)
    await db_session.flush()
    vis_rack = Rack(name=f"vr-{uuid.uuid4().hex[:6]}", location_id=loc.id, u_height=42)
    hid_rack = Rack(name=f"hr-{uuid.uuid4().hex[:6]}", location_id=loc.id, u_height=42)
    vis_cust = Customer(name=f"vc-{uuid.uuid4().hex[:6]}")
    hid_cust = Customer(name=f"hc-{uuid.uuid4().hex[:6]}")
    db_session.add_all([vis_rack, hid_rack, vis_cust, hid_cust])
    await db_session.flush()
    # 只授權看 vis_rack / vis_cust（location 不授權 → list_locations 應為空）
    db_session.add_all([
        Permission(object_type="rack", object_id=vis_rack.id,
                   principal_type="user", principal_id=u.id, level="read"),
        Permission(object_type="customer", object_id=vis_cust.id,
                   principal_type="user", principal_id=u.id, level="read"),
    ])
    await db_session.commit()

    racks = await list_racks(db_session, user=u)
    rack_names = {r["name"] for r in racks["racks"]}
    assert vis_rack.name in rack_names
    assert hid_rack.name not in rack_names

    custs = await list_customers(db_session, user=u)
    cust_names = {c["name"] for c in custs["customers"]}
    assert vis_cust.name in cust_names
    assert hid_cust.name not in cust_names

    locs = await list_locations(db_session, user=u)
    assert locs["locations"] == []   # 未授權任何 location


@pytest.mark.anyio
async def test_stats_overview_scoped_and_hides_global(db_session):
    """部門帳號的 stats_overview：逐物件計數縮放、且不含全域基礎設施計數鍵。"""
    u = await _nonadmin(db_session)
    sec = Section(name=f"sec-{uuid.uuid4().hex[:6]}")
    db_session.add(sec)
    await db_session.flush()
    db_session.add(Permission(object_type="section", object_id=sec.id,
                              principal_type="user", principal_id=u.id, level="read"))
    await db_session.commit()

    out = await stats_overview(db_session, user=u)
    # 逐物件鍵一定在
    for k in ("sections", "subnets", "ip_addresses", "devices", "racks", "locations", "customers"):
        assert k in out
    # 全域基礎設施計數鍵不可洩漏給無全域讀取者
    for k in ("vlans", "nat_rules", "vms", "circuits"):
        assert k not in out
    # 沒授權任何 rack/location/customer → 計數為 0
    assert out["racks"] == 0
    assert out["locations"] == 0


@pytest.mark.anyio
async def test_admin_stats_overview_has_global(db_session, admin_user):
    """admin 的 stats_overview 含全域計數鍵。"""
    out = await stats_overview(db_session, user=admin_user)
    for k in ("vlans", "nat_rules", "circuits"):
        assert k in out
