"""新增 MCP 工具：完整覆蓋系統功能 + 寫入工具的 admin 閘門。"""

from __future__ import annotations

import uuid

import pytest

from app.models.address import IPAddress
from app.models.customer import Customer
from app.models.section import Section
from app.models.subnet import Subnet
from app.models.user import User
from app.mcp import tools as T
from app.mcp.tools import IPAMToolError


async def _mk_user(db_session, *, admin: bool) -> User:
    from app.core.security import hash_password
    u = User(
        username=f"u-{uuid.uuid4().hex[:8]}", email=f"u-{uuid.uuid4().hex[:8]}@t.local",
        display_name="U", password_hash=hash_password("TestPassword2026!"),
        auth_provider="local", is_active=True, is_admin=admin,
    )
    db_session.add(u)
    await db_session.flush()
    return u


async def _seed(db_session):
    sec = Section(name=f"sec-{uuid.uuid4().hex[:6]}", strict_mode=False, display_order=0)
    db_session.add(sec)
    await db_session.flush()
    sub = Subnet(cidr="10.55.0.0/24", section_id=sec.id, gateway="10.55.0.1", description="t")
    db_session.add(sub)
    await db_session.flush()
    ipa = IPAddress(subnet_id=sub.id, ip="10.55.0.10", state="active", hostname="host-a")
    db_session.add(ipa)
    await db_session.flush()
    return sec, sub, ipa


async def test_get_ip_and_subnet_detail(db_session, admin_user):
    _, sub, ipa = await _seed(db_session)
    d = await T.get_ip_detail(db_session, user=admin_user, ip="10.55.0.10")
    assert d["found"] is True and d["hostname"] == "host-a" and d["subnet"] == "10.55.0.0/24"
    missing = await T.get_ip_detail(db_session, user=admin_user, ip="10.55.0.99")
    assert missing["found"] is False
    sd = await T.get_subnet_detail(db_session, user=admin_user, subnet_cidr="10.55.0.0/24")
    assert sd["cidr"] == "10.55.0.0/24" and sd["gateway"] == "10.55.0.1" and "usage" in sd


async def test_list_subnet_ips(db_session, admin_user):
    _, sub, ipa = await _seed(db_session)
    r = await T.list_subnet_ips(db_session, user=admin_user, subnet_cidr="10.55.0.0/24")
    assert r["subnet"] == "10.55.0.0/24"
    ips = {x["ip"]: x for x in r["ips"]}
    assert "10.55.0.10" in ips and ips["10.55.0.10"]["hostname"] == "host-a"


async def test_readonly_lists_run(db_session, admin_user):
    # 無資料也要能正常回傳對應 key
    assert "firewalls" in await T.list_firewalls(db_session, user=admin_user)
    assert "rules" in await T.list_firewall_rules(db_session, user=admin_user)
    assert "aliases" in await T.list_firewall_aliases(db_session, user=admin_user)
    assert "servers" in await T.list_dns_servers(db_session, user=admin_user)
    assert "zones" in await T.list_dns_zones(db_session, user=admin_user)
    assert "agents" in await T.list_scan_agents(db_session, user=admin_user)
    assert "arp" in await T.list_arp(db_session, user=admin_user)
    assert "fdb" in await T.list_fdb(db_session, user=admin_user)
    assert "vms" in await T.list_vms(db_session, user=admin_user)
    assert "wireless_links" in await T.list_wireless_links(db_session, user=admin_user)
    assert "requests" in await T.list_ip_requests(db_session, user=admin_user)
    assert "missing" in await T.wazuh_missing_agents(db_session, user=admin_user)
    topo = await T.get_topology(db_session, user=admin_user)
    assert "edges" in topo and "node_count" in topo


async def test_customer_summary(db_session, admin_user):
    c = Customer(name=f"cust-{uuid.uuid4().hex[:6]}")
    db_session.add(c)
    await db_session.flush()
    s = await T.get_customer_summary(db_session, user=admin_user, customer_id=str(c.id))
    assert s["name"] == c.name and s["subnets"] == 0


async def test_write_tools_require_admin(db_session):
    _, sub, ipa = await _seed(db_session)
    nonadmin = await _mk_user(db_session, admin=False)
    with pytest.raises(IPAMToolError):
        await T.update_ip(db_session, user=nonadmin, ip="10.55.0.10", hostname="x")
    with pytest.raises(IPAMToolError):
        await T.create_subnet(db_session, user=nonadmin, cidr="10.66.0.0/24", section_name="x")
    with pytest.raises(IPAMToolError):
        await T.create_device(db_session, user=nonadmin, name="d1")


async def test_write_tools_admin(db_session, admin_user):
    sec, sub, ipa = await _seed(db_session)
    r = await T.update_ip(db_session, user=admin_user, ip="10.55.0.10", hostname="renamed", owner="ops")
    assert "hostname" in r["updated"]
    refreshed = await T.get_ip_detail(db_session, user=admin_user, ip="10.55.0.10")
    assert refreshed["hostname"] == "renamed" and refreshed["owner"] == "ops"

    cs = await T.create_subnet(db_session, user=admin_user, cidr="10.77.0.0/24", section_id=str(sec.id))
    assert cs["cidr"] == "10.77.0.0/24"
    cd = await T.create_device(db_session, user=admin_user, name="sw-test", type="switch")
    assert cd["name"] == "sw-test" and cd["type"] == "switch"
