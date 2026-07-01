"""對「每一個 AI chat 工具」做 dispatch 煙霧測試。

AI chat 實際呼叫路徑就是： await TOOLS[name]["fn"](session, user=user, **args)
（見 app/services/ai.py）。本測試對 *每一個* 註冊工具用合理參數呼叫一次，斷言：
  - 回傳必為 dict（JSON-serialisable）
  - 唯一允許的例外是 IPAMToolError（查無資料 / admin 守門 / 輸入不合法的人讀錯誤）
任何其他例外（簽章不符、KeyError、AttributeError…）= 該工具對 AI 不可用 → 測試失敗。

另外針對使用者實際踩到的情境（OUI 首碼搜尋）與純運算工具做精確值斷言。
"""

from __future__ import annotations

import uuid

import pytest
from app.mcp.tools import TOOLS, IPAMToolError

# 每個工具一組合理參數。查無資料的工具會 raise IPAMToolError（預期、允許）。
_U = str(uuid.uuid4())
SAMPLE_ARGS: dict[str, dict] = {
    # ── 純運算 / 網路工具 ──
    "calc_ip_info": {"ip": "8.8.8.8"},
    "calc_cidr_info": {"cidr": "192.168.0.0/24"},
    "calc_cidr_split": {"cidr": "192.168.0.0/24", "new_prefix": 26},
    "calc_eui64": {"mac": "00:11:22:33:44:55", "prefix": "2001:db8::/64"},
    "calc_ip_in_cidr": {"ip": "192.168.0.9", "cidr": "192.168.0.0/24"},
    "calc_cidr_relation": {"a": "10.1.0.0/16", "b": "10.0.0.0/8"},
    "calc_range_to_cidr": {"start": "192.168.1.0", "end": "192.168.1.255"},
    "calc_cidr_to_range": {"cidr": "192.168.1.0/24"},
    "calc_aggregate": {"cidrs": "192.168.0.0/24, 192.168.1.0/24"},
    "calc_netmask": {"value": "255.255.255.0"},
    "calc_mac_format": {"mac": "0011.2233.4455"},
    "calc_fqdn": {"name": "sw1.dc.example.com"},
    "dns_resolve": {"name": "localhost", "type": "A"},
    "dns_mail_check": {"domain": "localhost"},
    "geoip_locate": {"ip": "8.8.8.8"},
    "power_calc": {"volts": 220, "amps": 16, "phase": "1", "pf": 0.95},
    # ── OUI ──
    "oui_lookup": {"mac": "22:11:22:33:44:55"},
    "oui_search": {"prefix": "22"},
    # ── 唯讀清單（空 DB 也可回） ──
    "stats_overview": {}, "check_dns_consistency": {}, "get_topology": {},
    "list_subnets": {}, "list_vlans": {}, "list_racks": {}, "list_locations": {},
    "list_devices": {}, "list_customers": {}, "list_nat": {}, "list_sections": {},
    "list_vrfs": {}, "list_vpn_tunnels": {}, "list_firewalls": {}, "list_firewall_rules": {},
    "list_firewall_aliases": {}, "list_dns_servers": {}, "list_dns_zones": {},
    "list_dns_records": {},
    "list_ip_requests": {}, "list_scan_agents": {}, "list_arp": {}, "list_fdb": {},
    "list_certificates": {}, "list_cert_distribution": {},
    "list_connection_targets": {},
    "wazuh_missing_agents": {}, "list_vms": {}, "list_wireless_links": {},
    "list_circuits": {}, "list_providers": {}, "list_asns": {}, "list_tenants": {},
    "list_contacts": {}, "list_ssids": {}, "list_cables": {}, "list_power": {},
    "list_wazuh_agents": {}, "cable_trace": {"cable_id": _U},
    "recent_ip_changes": {}, "list_subnet_ips": {"subnet_cidr": "10.0.0.0/24"},
    "get_subnet_detail": {"subnet_cidr": "10.0.0.0/24"},
    "get_customer_summary": {"name": "nope"},
    "get_device": {"name": "nope"},
    # ── 查無資料 → 預期 IPAMToolError 或空 dict ──
    "search_ip": {"ip": "10.0.0.1"},
    "get_ip_detail": {"ip": "10.0.0.1"},
    "get_subnet_usage": {"subnet_id": _U},
    "trace_mac": {"mac": "00:11:22:33:44:55"},
    "global_search": {"q": "abc"},
    "dns_lookup": {"name": "example"},
    "find_free_ip": {"subnet_cidr": "10.0.0.0/24"},
    "find_free_ips": {"count": 1, "subnet_cidr": "10.0.0.0/24"},
    "switch_port_for_ip": {"ip": "10.0.0.1"},
    # ── 管理 / 變更（用查無資料的參數，預期 IPAMToolError，不污染 DB） ──
    "allocate_ip": {"subnet_cidr": "10.255.255.0/24", "requested_ip": "10.255.255.9"},
    "create_subnet": {"cidr": "10.255.255.0/24", "section_name": "no-such-section"},
    "create_device": {"name": "zz-ai-smoke-device"},
    "update_ip": {"ip": "10.255.255.1", "hostname": "x"},
    "approve_ip_request": {"request_id": _U},
    "reject_ip_request": {"request_id": _U, "reason": "x"},
}


def test_every_tool_has_sample_args() -> None:
    """新增工具卻忘了補測試 → 這裡先擋下來。"""
    missing = [n for n in TOOLS if n not in SAMPLE_ARGS]
    assert not missing, f"these tools have no AI-dispatch test args: {missing}"


@pytest.mark.anyio
async def test_all_tools_dispatch_via_ai_path(db_session, admin_user) -> None:
    """逐一以 AI chat 的 dispatch 形式呼叫每個工具。"""
    # 先種一筆 OUI，讓 oui_lookup / oui_search 有資料可回
    from app.models.oui import OUIVendor
    db_session.add(OUIVendor(prefix="221122", short_name="JTTV", name="JT Test Vendor", source="manual"))
    await db_session.commit()

    failures: list[str] = []
    for name in sorted(TOOLS):
        args = SAMPLE_ARGS[name]
        try:
            result = await TOOLS[name]["fn"](db_session, user=admin_user, **args)
            if not isinstance(result, dict):
                failures.append(f"{name}: returned {type(result).__name__}, expected dict")
        except IPAMToolError:
            pass  # 預期：查無資料 / admin 守門 / 不合法輸入
        except Exception as exc:  # 測試要捕捉所有非預期例外
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
    assert not failures, "AI-dispatch failures:\n" + "\n".join(failures)


@pytest.mark.anyio
async def test_oui_search_by_prefix_finds_vendor(db_session, admin_user) -> None:
    """使用者情境：『mac 22: 開頭有哪些製造商』→ oui_search(prefix='22')。"""
    from app.models.oui import OUIVendor
    db_session.add(OUIVendor(prefix="221122", short_name="JTTV", name="JT Test Vendor", source="manual"))
    db_session.add(OUIVendor(prefix="2200AA", short_name="Foo", name="Foo Networks", source="manual"))
    db_session.add(OUIVendor(prefix="AB00CD", short_name="Bar", name="Bar Inc", source="manual"))
    await db_session.commit()

    res = await TOOLS["oui_search"]["fn"](db_session, user=admin_user, prefix="22")
    prefixes = {v["prefix"] for v in res["vendors"]}
    assert res["count"] == 2
    assert "22:11:22" in prefixes
    assert "22:00:AA" in prefixes
    assert "AB:00:CD" not in prefixes

    # 依廠商名搜尋
    by_name = await TOOLS["oui_search"]["fn"](db_session, user=admin_user, name="Foo")
    assert by_name["count"] == 1
    assert by_name["vendors"][0]["name"] == "Foo Networks"
