"""清單工具的範圍與總數：問某網段不可以拿全站資料回答。

真實事故：使用者問「198.51.100.0/24 裡哪些主機沒裝 Wazuh 代理」，工具沒有子網路
參數 → 回全站 256 筆，答案裡混進 203.0.113.x / 192.0.2.x。這類錯誤每個數字
單獨看都是真的，最難察覺，所以用測試釘死：

- 帶 subnet_cidr → 只回該網段，且 scope 標出範圍
- 不帶 → scope="all"（模型才知道自己拿到的是全站資料）
- 清單被 limit 截斷時，count 是「範圍內總數」而不是「這頁筆數」
"""
from __future__ import annotations

import uuid

import pytest

from app.mcp.tools import TOOLS
from app.models.address import IPAddress
from app.models.section import Section
from app.models.subnet import Subnet
from app.models.wazuh import WazuhAgent


async def _two_subnets(session):
    sec = Section(name=f"s-{uuid.uuid4().hex[:6]}")
    session.add(sec)
    await session.flush()
    a = Subnet(section_id=sec.id, cidr="198.51.100.0/24")
    b = Subnet(section_id=sec.id, cidr="203.0.113.0/24")
    session.add_all([a, b])
    await session.flush()
    ips = [
        IPAddress(subnet_id=a.id, ip="198.51.100.10", hostname="in-scope-1", state="used"),
        IPAddress(subnet_id=a.id, ip="198.51.100.11", hostname="in-scope-2", state="used"),
        IPAddress(subnet_id=b.id, ip="203.0.113.10", hostname="other-subnet", state="used"),
    ]
    session.add_all(ips)
    await session.flush()
    return a, b, ips


@pytest.mark.anyio
async def test_missing_agents_scoped_to_subnet(db_session, admin_user) -> None:
    """帶 subnet_cidr 只回該網段；不帶才是全站。"""
    a, _b, _ips = await _two_subnets(db_session)
    fn = TOOLS["wazuh_missing_agents"]["fn"]

    scoped = await fn(db_session, user=admin_user, subnet_cidr="198.51.100.0/24")
    names = {r["hostname"] for r in scoped["missing"]}
    assert names == {"in-scope-1", "in-scope-2"}, "跑出範圍外的主機 —— 就是這次的事故"
    assert scoped["scope"] == "198.51.100.0/24"
    assert scoped["missing_count"] == 2, "count 必須是該範圍的數字，不是全站"

    everything = await fn(db_session, user=admin_user)
    assert everything["scope"] == "all", "沒帶範圍要明講是全站，模型才不會當成某網段的答案"
    assert {"other-subnet"} <= {r["hostname"] for r in everything["missing"]}


@pytest.mark.anyio
async def test_wazuh_agents_scoped(db_session, admin_user) -> None:
    a, b, ips = await _two_subnets(db_session)
    from app.models.wazuh import WazuhInstance
    inst = WazuhInstance(name=f"wz-{uuid.uuid4().hex[:6]}", api_url="https://192.0.2.5:55000",
                         api_user="ro", api_password_enc=b"x", api_password_nonce=b"y")
    db_session.add(inst)
    await db_session.flush()
    db_session.add_all([
        WazuhAgent(instance_id=inst.id, agent_id="001", name="agent-in", status="active",
                   jt_ipam_address_id=ips[0].id),
        WazuhAgent(instance_id=inst.id, agent_id="002", name="agent-out", status="active",
                   jt_ipam_address_id=ips[2].id),
    ])
    await db_session.flush()
    fn = TOOLS["list_wazuh_agents"]["fn"]
    scoped = await fn(db_session, user=admin_user, subnet_cidr="198.51.100.0/24")
    assert {x["name"] for x in scoped["agents"]} == {"agent-in"}
    assert scoped["count"] == 1 and scoped["scope"] == "198.51.100.0/24"


@pytest.mark.anyio
async def test_count_is_total_not_page(db_session, admin_user) -> None:
    """limit 截斷時 count 仍是總數 —— 否則模型會把一頁講成全部。"""
    a, _b, _ips = await _two_subnets(db_session)
    fn = TOOLS["wazuh_missing_agents"]["fn"]
    page = await fn(db_session, user=admin_user, subnet_cidr="198.51.100.0/24", limit=1)
    assert page["returned"] == 1
    assert page["missing_count"] == 2, "count 變成這頁筆數 → 靜默截斷，答案會少報"


@pytest.mark.anyio
async def test_scoped_tools_expose_subnet_param() -> None:
    """這批工具的 schema 一定要有 subnet_cidr，否則模型無從限縮（事故根因）。"""
    for name in ("wazuh_missing_agents", "list_wazuh_agents", "list_fdb",
                 "list_dhcp_ranges", "list_vms", "list_nat", "list_rustdesk_peers", "list_rustdesk_audit",
                 "list_isoinsight_leases", "list_k8s"):
        props = TOOLS[name]["parameters"]["properties"]
        assert "subnet_cidr" in props, f"{name} 沒有子網路參數 → 問某網段只能回全站"


@pytest.mark.anyio
async def test_attack_surface_tool_answers_by_fqdn(db_session, admin_user) -> None:
    """AI 要能用名字問：「meet.example.net 對外開了什麼」。

    對話裡人講的是主機名字，不是位址；沒有這個入口，模型只能要使用者自己先查 IP。
    """
    from app.mcp.tools import TOOLS
    from app.models.dns import DNSRecord, DNSServer, DNSZone
    from app.models.nat import NATTranslation

    a, _b, ips = await _two_subnets(db_session)
    db_session.add(NATTranslation(
        name="pf-web", type="port_forward", protocol="tcp",
        dst_ip_id=ips[0].id, dst_port=443, source_origin="pfsense:x", disabled=False))
    srv = DNSServer(name=f"dns-{uuid.uuid4().hex[:6]}", type="bind9",
                    server_address="192.0.2.53")
    db_session.add(srv)
    await db_session.flush()
    zone = DNSZone(server_id=srv.id, name="example.net", type="forward")
    db_session.add(zone)
    await db_session.flush()
    db_session.add(DNSRecord(zone_id=zone.id, name="meet", type="A",
                             value=str(ips[0].ip)))
    await db_session.flush()

    fn = TOOLS["list_attack_surface"]["fn"]
    by_name = await fn(db_session, user=admin_user, fqdn="meet.example.net")
    assert by_name["count"] == 1, "用 FQDN 問不到自己的對外開口"
    assert by_name["items"][0]["port"] == 443
    assert {"meet.example.net"} <= set(by_name["items"][0]["fqdns"])
    assert by_name["scope"] == "fqdn:meet.example.net"

    other = await fn(db_session, user=admin_user, fqdn="nope.example.net")
    assert other["count"] == 0, "不該把別人的開口算進來"


@pytest.mark.anyio
async def test_anomaly_tool_exposes_fw_rule_rot() -> None:
    """規則劣化要能經由 AI 問到（原本這一類從偵測工具裡漏掉）。"""
    import inspect

    from app.mcp.tools import TOOLS

    src = inspect.getsource(TOOLS["list_anomalies"]["fn"])
    assert "fw_rule_rot" in src, "AI 問不到防火牆規則劣化"


@pytest.mark.anyio
async def test_missing_agents_default_to_the_integration_scope(db_session, admin_user) -> None:
    """沒帶網段時跟畫面一致：Wazuh 整合限定了子網路，就只算這些子網路（2026-09-25）。"""
    from app.models.wazuh import WazuhInstance
    a, _b, _ips = await _two_subnets(db_session)
    db_session.add(WazuhInstance(
        name=f"wz-{uuid.uuid4().hex[:6]}", api_url="https://192.0.2.5:55000", api_user="ro",
        api_password_enc=b"x", api_password_nonce=b"y", scope_subnet_ids=[str(a.id)]))
    await db_session.flush()
    got = await TOOLS["wazuh_missing_agents"]["fn"](db_session, user=admin_user)
    assert got["scope"] == "integration_scope", "要讓模型知道這是整合範圍、不是全站"
    assert {r["hostname"] for r in got["missing"]} == {"in-scope-1", "in-scope-2"}
