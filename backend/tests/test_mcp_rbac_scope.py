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


# 每次新增全域基礎設施類的工具都要記得掛 GLOBAL_READ_TOOLS。忘了掛不會有任何錯誤訊息，
# 只會安靜地讓部門帳號透過 AI 對話看到整個環境 —— 這個專案踩過（get_topology）。
# 這裡把「哪些工具屬於全域基礎設施」寫死成清單：新增工具時測試會逼你分類。
_PER_OBJECT_TOOLS = frozenset({
    "list_subnets", "list_sections", "list_devices", "list_customers", "list_racks",
    "list_locations", "list_subnet_ips", "get_subnet_detail", "get_subnet_usage",
    "get_device", "get_ip_detail", "get_ip_history", "get_customer_summary", "search_ip", "global_search",
    "find_free_ip", "find_free_ips", "recent_ip_changes", "stats_overview",
    "list_ip_requests", "switch_port_for_ip", "trace_mac", "mac_history", "allocate_ip", "update_ip",
    "create_subnet", "create_device", "approve_ip_request", "reject_ip_request",
    "list_connection_targets", "investigate_ip",
    # 拓樸：與 REST /topology 相同，部分範圍帳號只看得到自己範圍內的節點與兩端都在範圍內的連線（2026-10-09）
    "get_topology",
    # 維護排程：與 REST /maintenance 同一套可見性（範圍內有看得到的物件、或自己建的）
    "list_maintenance",
    # ISOinsight 來源租約：依可見子網路過濾，配對不到子網路的只給管理員（services/isoinsight/queries.py）
    "list_isoinsight_leases",
    # IP 變更評估：計畫依根目標的可見性、結果依 Viewer 逐筆過濾（services/change_impact/access.py）
    "impact_list_plans", "impact_get_run", "impact_list_findings", "impact_get_evidence",
    "impact_prepare_scenario", "impact_create_plan", "impact_start_run", "impact_accept_task_draft",
})
# 純計算 / 外部查詢，不碰本站資料 → 不需要可見範圍
_STATELESS_TOOLS = frozenset({
    "calc_aggregate", "calc_cidr_info", "calc_cidr_relation", "calc_cidr_split",
    "calc_cidr_to_range", "calc_eui64", "calc_fqdn", "calc_ip_in_cidr", "calc_ip_info",
    "calc_mac_format", "calc_netmask", "calc_range_to_cidr", "oui_lookup", "oui_search",
    "power_calc", "geoip_locate", "dns_resolve", "dns_mail_check",
    # 畫面地圖：只讀前端原始碼的選單與欄位名稱，不碰資料
    "find_ui_location",
})


def test_every_tool_is_classified():
    """新工具必須明確歸類，不能什麼都沒掛就上線。"""
    from app.mcp.tools import ADMIN_TOOLS, GLOBAL_READ_TOOLS, TOOLS
    unclassified = (
        set(TOOLS) - ADMIN_TOOLS - GLOBAL_READ_TOOLS - _PER_OBJECT_TOOLS - _STATELESS_TOOLS
    )
    assert not unclassified, (
        f"這些 MCP 工具沒有歸類：{sorted(unclassified)}。"
        "純管理資料（稽核／巡檢／異常等）要加進 ADMIN_TOOLS；全域基礎設施資料要加進 "
        "GLOBAL_READ_TOOLS；逐物件資料要自己過 visible_ids 並登記在 _PER_OBJECT_TOOLS；"
        "純計算的登記在 _STATELESS_TOOLS。"
    )


@pytest.mark.anyio
async def test_trace_mac_shows_the_switch_port_to_users_who_can_see_the_switch(db_session, admin_user):
    """FDB 的 device_id 是 **LibreNMS 的裝置**：以前直接拿它比對使用者看得到的 jt-ipam 裝置，
    永遠對不上 → 非管理員用 AI 追 MAC 一律看不到交換器埠（2026-09-30 研究）。"""
    from app.mcp.tools import trace_mac
    from app.models.device import Device
    from app.models.librenms import FDBEntry, LibreNMSDevice, LibreNMSInstance

    sw = Device(name="sw-floor3", type="switch")
    other = Device(name="sw-other", type="switch")
    db_session.add_all([sw, other])
    inst = LibreNMSInstance(name=f"lnms-{uuid.uuid4().hex[:6]}", api_url="https://librenms.example",
                            api_token_enc=b"x", api_token_nonce=b"y")
    db_session.add(inst)
    await db_session.flush()
    ln = LibreNMSDevice(instance_id=inst.id, legacy_device_id=5, hostname="sw-floor3", sysname="sw-floor3",
                        jt_ipam_device_id=sw.id)
    db_session.add(ln)
    await db_session.flush()
    db_session.add(FDBEntry(mac="00:00:5e:00:53:77", device_id=ln.id, instance_id=inst.id,
                            port_name="ge-0/0/7", vlan_id_num=20))
    can, cannot = await _nonadmin(db_session), await _nonadmin(db_session)
    db_session.add(Permission(object_type="device", object_id=sw.id, principal_type="user",
                              principal_id=can.id, level="read"))
    db_session.add(Permission(object_type="device", object_id=other.id, principal_type="user",
                              principal_id=cannot.id, level="read"))
    await db_session.commit()

    got = await trace_mac(db_session, user=can, mac="00-00-5E-00-53-77")
    assert got["fdb"]["port_name"] == "ge-0/0/7"
    assert got["fdb"]["switch"] == "sw-floor3"
    assert got["fdb"]["switch_device_id"] == str(sw.id)
    assert (await trace_mac(db_session, user=cannot, mac="00:00:5e:00:53:77"))["fdb"] is None
    assert (await trace_mac(db_session, user=admin_user, mac="00:00:5e:00:53:77"))["fdb"]["switch"] == "sw-floor3"
