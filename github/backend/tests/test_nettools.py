"""網路工具 service 純運算 + MCP/AI 工具註冊與 dispatch。"""

from __future__ import annotations

import pytest
from app.services import nettools
from app.services.nettools import NetToolError

# ─────────────────── 純運算 ───────────────────


def test_ip_info_ipv4() -> None:
    r = nettools.ip_info("8.8.8.8")
    assert r["version"] == 4
    assert r["is_global"] is True
    assert r["decimal"] == "134744072"
    assert r["binary"] is not None
    assert len(r["binary"]) == 32


def test_cidr_info_usable_hosts() -> None:
    r = nettools.cidr_info("192.168.0.0/24")
    assert r["host_count"] == "254"
    assert r["broadcast_address"] == "192.168.0.255"
    assert r["first_host"] == "192.168.0.1"


def test_cidr_split_and_guard() -> None:
    r = nettools.cidr_split("192.168.0.0/24", 26)
    assert r["count"] == 4
    with pytest.raises(NetToolError):
        nettools.cidr_split("10.0.0.0/8", 28)  # bits delta > 16 → 拒絕


def test_eui64_known_vector() -> None:
    r = nettools.eui64("00:11:22:33:44:55", "2001:db8::/64")
    assert r["address"] == "2001:db8::211:22ff:fe33:4455"


def test_ip_in_cidr_family_mismatch() -> None:
    assert nettools.ip_in_cidr("192.168.1.5", "192.168.1.0/24")["contains"] is True
    with pytest.raises(NetToolError):
        nettools.ip_in_cidr("::1", "192.168.1.0/24")


def test_cidr_relation() -> None:
    assert nettools.cidr_relation("10.1.0.0/16", "10.0.0.0/8")["relation"] == "a_within_b"
    assert nettools.cidr_relation("10.0.0.0/8", "10.1.0.0/16")["relation"] == "a_contains_b"


def test_range_and_aggregate() -> None:
    assert nettools.range_to_cidr("192.168.1.0", "192.168.1.255")["cidrs"] == ["192.168.1.0/24"]
    agg = nettools.aggregate("192.168.0.0/24, 192.168.1.0/24")
    assert agg["aggregated"] == ["192.168.0.0/23"]


def test_netmask_and_mac() -> None:
    assert nettools.netmask("255.255.255.0")["prefixlen"] == 24
    assert nettools.netmask("/24")["netmask"] == "255.255.255.0"
    mac = nettools.mac_format("0011.2233.4455")
    assert mac["colon"] == "00:11:22:33:44:55"
    assert mac["oui"] == "001122"


def test_fqdn_parse() -> None:
    r = nettools.fqdn_parse("sw1.dc.example.com")
    assert r["valid"] is True
    assert r["host"] == "sw1"
    assert r["tld"] == "com"


def test_power_calc() -> None:
    r = nettools.power_calc(volts=220, amps=16, phase="3", pf=0.95, batt_wh=1500, load_w=500, pdu_a=16)
    assert r["load_watts"] == 5792
    assert r["ups_minutes"] == 180
    assert r["pdu_safe_amps"] == 12.8


# ─────────────────── MCP / AI 工具註冊 ───────────────────

NEW_TOOLS = [
    "calc_ip_info", "calc_cidr_info", "calc_cidr_split", "calc_eui64",
    "calc_ip_in_cidr", "calc_cidr_relation", "calc_range_to_cidr", "calc_cidr_to_range",
    "calc_aggregate", "calc_netmask", "calc_mac_format", "calc_fqdn",
    "dns_resolve", "dns_mail_check", "geoip_locate", "power_calc",
]


def test_all_net_tools_registered() -> None:
    from app.mcp.tools import TOOLS
    for name in NEW_TOOLS:
        assert name in TOOLS, name
        spec = TOOLS[name]
        assert callable(spec["fn"])
        assert spec["description"]
        assert spec["parameters"]["type"] == "object"


@pytest.mark.anyio
async def test_calc_tool_dispatch_no_db(db_session, admin_user) -> None:
    """純運算工具可在不碰 DB 的情況下 dispatch（session/user 雖傳入但不使用）。"""
    from app.mcp.tools import TOOLS
    r = await TOOLS["calc_cidr_info"]["fn"](db_session, user=admin_user, cidr="10.0.0.0/24")
    assert r["host_count"] == "254"
    r2 = await TOOLS["calc_ip_in_cidr"]["fn"](db_session, user=admin_user, ip="10.0.0.9", cidr="10.0.0.0/24")
    assert r2["contains"] is True
