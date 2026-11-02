"""AI 工具要回傳畫面上看得到的欄位：網路、整合、進階資源這一批（使用者 2026-10-15）。

起因同 tests/test_mcp_tool_fields.py：工具少給一個欄位，模型就很有把握地說「系統沒有這個欄位」。
2026-10-15 盤點又發現同一類問題：
- 清單沒有 total／truncated，模型分不出「就這些」還是「被截掉了」
- 外鍵只給 UUID（防火牆、交換器、DNS 伺服器、纜線兩端、電源），模型只能照抄一串看不懂的編號
- 篩選套在截斷之後（憑證到期、攻擊面、防火牆清單）：符合條件的資料明明有，卻因為排在後面被丟掉
- 說明寫錯（Wazuh CVE 數早已移除、RustDesk 客戶端其實會回報主機名稱、DHCP 不只四種來源…）
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.mcp.tools import TOOLS

pytestmark = pytest.mark.anyio

_SECRET_HINTS = ("_enc", "_nonce", "password", "secret", "token", "key_hash", "private")


def _u() -> str:
    return uuid.uuid4().hex[:6]


def _walk_keys(obj):  # type: ignore[no-untyped-def]
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from _walk_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_keys(v)


async def _call(db, user, tool, **args):  # type: ignore[no-untyped-def]
    return await TOOLS[tool]["fn"](db, user=user, **args)


async def _subnet(db, cidr: str = "198.51.100.0/24"):  # type: ignore[no-untyped-def]
    from app.models.section import Section
    from app.models.subnet import Subnet
    sec = Section(name=f"s-{_u()}")
    db.add(sec)
    await db.flush()
    sub = Subnet(section_id=sec.id, cidr=cidr)
    db.add(sub)
    await db.flush()
    return sub


# ─────────────────── 每個清單工具都要有真正的總數與是否截斷 ───────────────────

_LIST_TOOLS = (
    "list_firewalls", "list_fortigate_policies", "list_fortigate_addresses", "list_paloalto_policies",
    "list_paloalto_addresses", "list_checkpoint_rules", "list_checkpoint_objects", "list_mikrotik_rules",
    "list_mikrotik_address_lists", "list_firewall_rules", "list_firewall_aliases", "list_nat",
    "list_vpn_tunnels", "list_dns_servers", "list_dns_zones", "list_dns_records", "list_dhcp_ranges",
    "list_arp", "list_fdb", "list_certificates", "list_cert_distribution", "list_attack_surface",
    "list_ai_findings", "list_vms", "list_wireless_links", "list_domains", "list_providers", "list_asns", "list_tenants",
    "list_contacts", "list_ssids", "list_cables", "list_wazuh_agents", "list_ocs_computers",
    "list_rustdesk_peers", "list_rustdesk_audit", "list_isoinsight_leases",
)


async def test_every_list_tool_reports_total_and_truncated(db_session, admin_user) -> None:
    missing = []
    for name in _LIST_TOOLS:
        out = await _call(db_session, admin_user, name)
        if "total" not in out or "truncated" not in out:
            missing.append(name)
    out = await _call(db_session, admin_user, "dns_lookup", name="x")
    if "total" not in out or "truncated" not in out:
        missing.append("dns_lookup")
    for name in ("get_topology", "list_power", "list_anomalies"):
        if "truncated" not in await _call(db_session, admin_user, name):
            missing.append(name)
    assert not missing, f"no total/truncated: {missing}"


# ─────────────────── 防火牆 ───────────────────

async def _six_firewalls(db):  # type: ignore[no-untyped-def]
    from app.models.checkpoint import CheckPointServer
    from app.models.device import Device
    from app.models.firewall import OPNsenseFirewall
    from app.models.fortigate import FortiGateFirewall
    from app.models.mikrotik import MikroTikRouter
    from app.models.paloalto import PaloAltoFirewall
    from app.models.pfsense import PfSenseFirewall
    sub = await _subnet(db)
    dev = Device(name=f"mt-dev-{_u()}", type="router")
    db.add(dev)
    await db.flush()
    b = b"x"
    opn = OPNsenseFirewall(name="opn-1", api_url="https://192.0.2.1", api_key_enc=b, api_key_nonce=b,
                           api_secret_enc=b, api_secret_nonce=b, verify_tls=False, sync_interval_seconds=600,
                           sync_nat=True, scope_subnet_ids=[sub.id], iface_subnet_map={"lan": str(sub.id)})
    pf = PfSenseFirewall(name="pf-1", api_url="https://192.0.2.2", api_key_enc=b, api_key_nonce=b)
    fg = FortiGateFirewall(name="fg-1", api_url="https://192.0.2.3", api_token_enc=b, api_token_nonce=b,
                           vdoms=["root", "dmz"])
    pa = PaloAltoFirewall(name="pa-1", api_url="https://192.0.2.4", api_key_enc=b, api_key_nonce=b,
                          vsys_list=["vsys1"])
    cp = CheckPointServer(name="cp-1", api_url="https://192.0.2.5", domains=["SMC User"], packages=["Standard"],
                          username="api-reader", secret_enc=b, secret_nonce=b)
    mt = MikroTikRouter(name="mt-1", api_url="https://192.0.2.6", api_username="admin", api_password_enc=b,
                        api_password_nonce=b, device_id=dev.id, routeros_version="7.16", board_name="RB5009")
    db.add_all([opn, pf, fg, pa, cp, mt])
    await db.flush()
    return {"sub": sub, "dev": dev, "opn": opn, "pf": pf, "fg": fg, "pa": pa, "cp": cp, "mt": mt}


async def test_list_firewalls_all_six_vendors_with_settings_and_real_total(db_session, admin_user) -> None:
    fw = await _six_firewalls(db_session)
    out = await _call(db_session, admin_user, "list_firewalls")
    by = {f["name"]: f for f in out["firewalls"]}
    assert {f["vendor"] for f in out["firewalls"]} == {"opnsense", "pfsense", "fortigate", "paloalto",
                                                        "checkpoint", "mikrotik"}
    assert out["total"] == 6 and out["truncated"] is False
    opn = by["opn-1"]
    assert opn["verify_tls"] is False and opn["sync_interval_seconds"] == 600
    assert opn["sync"]["nat"] is True
    assert opn["scope_subnets"] == ["198.51.100.0/24"]
    assert opn["iface_subnet_map"] == {"lan": "198.51.100.0/24"}
    assert by["fg-1"]["vdoms"] == ["root", "dmz"]
    assert by["pa-1"]["vsys"] == ["vsys1"]
    assert by["cp-1"]["domains"] == ["SMC User"] and by["cp-1"]["packages"] == ["Standard"]
    assert by["mt-1"]["device"] == fw["dev"].name and by["mt-1"]["routeros_version"] == "7.16"
    assert by["mt-1"]["board_name"] == "RB5009"
    bad = [k for k in _walk_keys(out) if any(h in k.lower() for h in _SECRET_HINTS)]
    assert not bad, f"secret-looking keys returned: {bad}"
    # 以前每家各取 limit 筆再 out[:limit]：被截掉了卻沒說
    small = await _call(db_session, admin_user, "list_firewalls", limit=2)
    assert small["total"] == 6 and small["truncated"] is True and len(small["firewalls"]) == 2
    desc = TOOLS["list_firewalls"]["description"]
    for v in ("Palo Alto", "Check Point", "MikroTik"):
        assert v in desc


async def test_firewall_rules_and_aliases_name_their_firewall(db_session, admin_user) -> None:
    from app.models.firewall import OPNsenseAliasMapping, OPNsenseSyncedAlias
    from app.models.firewall_rule import OPNsenseRule
    fw = await _six_firewalls(db_session)
    db_session.add(OPNsenseRule(firewall_id=fw["opn"].id, legacy_uuid="r1", sequence=1, action="pass",
                                destination_net="198.51.100.10", last_synced_at=datetime.now(UTC)))
    db_session.add(OPNsenseAliasMapping(firewall_id=fw["opn"].id, alias_name="ipam_web", alias_type="host",
                                        selector={"subnet_ids": [str(fw["sub"].id)]}))
    db_session.add(OPNsenseSyncedAlias(firewall_id=fw["opn"].id, name="blocklist", alias_type="network",
                                       content=["203.0.113.0/24"], member_count=1))
    await db_session.flush()
    rules = await _call(db_session, admin_user, "list_firewall_rules")
    assert rules["rules"][0]["firewall"] == "opn-1" and rules["total"] == 1
    al = await _call(db_session, admin_user, "list_firewall_aliases")
    m = al["aliases"][0]
    assert m["firewall"] == "opn-1" and m["selector"] == {"subnet_ids": [str(fw["sub"].id)]}
    assert any(s["name"] == "blocklist" and s["firewall"] == "opn-1" and s["members"] == ["203.0.113.0/24"]
               for s in al["synced_aliases"])


async def test_checkpoint_rules_carry_last_hit(db_session, admin_user) -> None:
    from app.models.checkpoint import CheckPointRule
    fw = await _six_firewalls(db_session)
    hit = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
    db_session.add(CheckPointRule(server_id=fw["cp"].id, package="Standard", layer="Network", uid="u1",
                                  rule_number=1, action="Accept", hits=5, last_hit_at=hit))
    await db_session.flush()
    out = await _call(db_session, admin_user, "list_checkpoint_rules")
    assert out["rules"][0]["last_hit_at"].startswith("2026-10-01") and out["total"] == 1


async def test_list_nat_names_firewall_and_device_and_every_form_field(db_session, admin_user) -> None:
    from app.models.address import IPAddress
    from app.models.nat import NATTranslation
    fw = await _six_firewalls(db_session)
    ip = IPAddress(subnet_id=fw["sub"].id, ip="198.51.100.10", state="used")
    db_session.add(ip)
    await db_session.flush()
    db_session.add(NATTranslation(
        name="web-in", type="port_forward", dst_ip_id=ip.id, dst_port=8000, dst_port_to=8010,
        src_port=1000, src_port_to=2000, protocol="tcp", device_id=fw["dev"].id, src_not=True, log=True,
        nat_reflection="enable", pool_options="round-robin", category="web", filter_rule="pass-web",
        src_port_alias="hi_ports", dst_port_alias="web_ports", source_origin=f"fortigate:{fw['fg'].id}"))
    await db_session.flush()
    out = await _call(db_session, admin_user, "list_nat")
    r = out["nat_rules"][0]
    assert r["firewall"] == "fg-1" and r["source"] == "fortigate"
    assert r["device"] == fw["dev"].name and r["dst_ip"] == "198.51.100.10"
    assert (r["dst_port_to"], r["src_port"], r["src_port_to"]) == (8010, 1000, 2000)
    assert r["src_not"] is True and r["dst_not"] is False and r["log"] is True
    assert (r["nat_reflection"], r["pool_options"], r["category"], r["filter_rule"]) == (
        "enable", "round-robin", "web", "pass-web")
    assert (r["src_port_alias"], r["dst_port_alias"]) == ("hi_ports", "web_ports")
    assert out["total"] == 1 and out["truncated"] is False


async def test_list_vpn_tunnels_pairing_method_is_reported(db_session, admin_user) -> None:
    from app.models.device import Device
    from app.models.physical import VPNTunnel
    fw = await _six_firewalls(db_session)
    a, b = Device(name="fw-a", type="firewall"), Device(name="fw-b", type="firewall")
    db_session.add_all([a, b])
    await db_session.flush()
    db_session.add(VPNTunnel(name="ipsec-x", type="ipsec_ikev2", a_device_id=a.id, b_device_id=b.id,
                             pairing_method="ipsec_endpoint", encryption_algo="aes256", auth_algo="sha256",
                             description="to branch", source_origin=f"opnsense:{fw['opn'].id}"))
    await db_session.flush()
    out = await _call(db_session, admin_user, "list_vpn_tunnels")
    t = out["vpn_tunnels"][0]
    assert t["pairing_method"] == "ipsec_endpoint" and t["site_to_site"] is True
    assert t["id"] and t["encryption_algo"] == "aes256" and t["description"] == "to branch"
    assert t["firewall"] == "opn-1"
    assert out["total"] == 1 and out["truncated"] is False
    desc = TOOLS["list_vpn_tunnels"]["description"].lower()
    assert "pairing_method" in desc and "best effort" in desc
    assert "confirmed tunnel" not in desc


# ─────────────────── DNS ───────────────────

async def _dns(db):  # type: ignore[no-untyped-def]
    from app.models.dns import DNSRecord, DNSServer, DNSZone
    from app.models.dns_compare_group import DNSCompareGroup
    sub = await _subnet(db)
    grp = DNSCompareGroup(name=f"grp-{_u()}")
    db.add(grp)
    await db.flush()
    srv = DNSServer(name="pdns-1", type="powerdns", api_url="https://192.0.2.53", sync_interval_seconds=900,
                    extra_config='{"username": "reader", "verify_tls": false, "password": "nope"}',
                    scope_subnet_ids=[str(sub.id)], compare_group_id=grp.id)
    db.add(srv)
    await db.flush()
    zone = DNSZone(server_id=srv.id, name="example.net", type="forward", associated_subnet_ids=[sub.id])
    db.add(zone)
    await db.flush()
    db.add_all([
        DNSRecord(zone_id=zone.id, name="www.example.net", type="A", value="198.51.100.10", ttl=300),
        DNSRecord(zone_id=zone.id, name="web.example.net", type="CNAME", value="www.example.net", ttl=60),
        DNSRecord(zone_id=zone.id, name="example.net", type="MX", value="10 mail.example.net", ttl=3600),
    ])
    await db.flush()
    return {"sub": sub, "grp": grp, "srv": srv, "zone": zone}


async def test_dns_servers_zones_records_and_lookup(db_session, admin_user) -> None:
    d = await _dns(db_session)
    srv = (await _call(db_session, admin_user, "list_dns_servers"))
    s = srv["servers"][0]
    assert s["compare_group"] == d["grp"].name and s["scope_subnets"] == ["198.51.100.0/24"]
    assert s["sync_interval_seconds"] == 900 and s["zone_count"] == 1
    assert s["config"] == {"username": "reader", "verify_tls": False}, "非機密設定要給、密碼不可以"
    zones = await _call(db_session, admin_user, "list_dns_zones")
    z = zones["zones"][0]
    assert z["server"] == "pdns-1" and z["associated_subnets"] == ["198.51.100.0/24"] and z["record_count"] == 3
    recs = await _call(db_session, admin_user, "list_dns_records")
    types = {r["type"]: r for r in recs["records"]}
    assert set(types) == {"A", "CNAME", "MX"}, "所有類型都要列"
    assert types["CNAME"]["ttl"] == 60 and types["A"]["zone"] == "example.net"
    assert recs["total"] == 3 and recs["truncated"] is False
    small = await _call(db_session, admin_user, "list_dns_records", limit=1)
    assert small["total"] == 3 and small["truncated"] is True
    assert "A/AAAA/PTR)" not in TOOLS["list_dns_records"]["description"]
    lk = await _call(db_session, admin_user, "dns_lookup", name="www")
    r = lk["records"][0]
    assert r["ttl"] == 300 and r["server"] == "pdns-1" and r["zone"] == "example.net"
    assert "consistency_state" in r and lk["total"] >= 1


async def test_check_dns_consistency_gives_total_and_per_server(db_session, admin_user) -> None:
    await _dns(db_session)
    out = await _call(db_session, admin_user, "check_dns_consistency")
    assert out["total"] == 1  # 只算 A/AAAA/PTR
    assert out["by_server"][0]["server"] == "pdns-1"


# ─────────────────── DHCP ───────────────────

async def test_dhcp_ranges_show_configured_pool_and_exclusions(db_session, admin_user) -> None:
    from app.models.dhcp import DHCPExclusion, DHCPPoolRange
    sub = await _subnet(db_session)
    src = uuid.uuid4()
    for s, e in (("198.51.100.100", "198.51.100.119"), ("198.51.100.131", "198.51.100.200")):
        db_session.add(DHCPPoolRange(source_type="mikrotik", source_id=src, source_name="mt-1",
                                     subnet_cidr="198.51.100.0/24", start_ip=s, end_ip=e,
                                     pool_start="198.51.100.100", pool_end="198.51.100.200", source="mikrotik"))
    db_session.add(DHCPExclusion(source_type="mikrotik", source_id=src, source_name="mt-1",
                                 subnet_cidr="198.51.100.0/24", start_ip="198.51.100.120",
                                 end_ip="198.51.100.130", source="mikrotik"))
    await db_session.flush()
    out = await _call(db_session, admin_user, "list_dhcp_ranges", subnet_cidr="198.51.100.0/24")
    assert {(r["pool_start"], r["pool_end"]) for r in out["ranges"]} == {("198.51.100.100", "198.51.100.200")}
    assert out["exclusions"][0]["start_ip"] == "198.51.100.120"
    assert out["total"] == 2 and out["truncated"] is False
    desc = TOOLS["list_dhcp_ranges"]["description"]
    for word in ("MikroTik", "Kea", "ISOinsight", "Technitium", "pool_start"):
        assert word in desc, word
    _ = sub


# ─────────────────── 拓樸 ───────────────────

async def test_get_topology_returns_nodes_and_edge_details(db_session, admin_user) -> None:
    from app.models.device import Device
    from app.models.physical import Cable, CableTermination
    a = Device(name="core-sw", type="switch", vendor="Cisco", model="C9300")
    b = Device(name="edge-sw", type="switch")
    db_session.add_all([a, b])
    await db_session.flush()
    c = Cable(label="C-001", type="cat6", status="connected")
    db_session.add(c)
    await db_session.flush()
    db_session.add_all([
        CableTermination(cable_id=c.id, side="A", object_type="device", object_id=a.id, port_label="Gi1/0/1"),
        CableTermination(cable_id=c.id, side="B", object_type="device", object_id=b.id, port_label="Gi0/1"),
    ])
    await db_session.flush()
    out = await _call(db_session, admin_user, "get_topology")
    nodes = {n["label"]: n for n in out["nodes"]}
    assert nodes["core-sw"]["type"] == "switch" and nodes["core-sw"]["vendor"] == "Cisco"
    assert nodes["core-sw"]["model"] == "C9300" and nodes["core-sw"]["id"]
    e = next(x for x in out["edges"] if x["kind"] == "cable")
    assert {e["from"], e["to"]} == {"core-sw", "edge-sw"}
    assert e["source_port"] == "Gi1/0/1" and e["target_port"] == "Gi0/1"
    assert e["status"] == "connected" and e["evidence"]
    assert out["truncated"] is False
    small = await _call(db_session, admin_user, "get_topology", limit=1)
    assert small["truncated"] is True and len(small["nodes"]) == 1 and small["node_count"] == 2


# ─────────────────── ARP／FDB ───────────────────

async def test_arp_and_fdb_name_the_switch(db_session, admin_user) -> None:
    from app.models.device import Device
    from app.models.librenms import ARPEntry, FDBEntry, LibreNMSDevice, LibreNMSInstance
    sw = Device(name="dist-sw-1", type="switch")
    mt = Device(name="mt-router", type="router")
    db_session.add_all([sw, mt])
    await db_session.flush()
    inst = LibreNMSInstance(name=f"lnms-{_u()}", api_url="https://192.0.2.9", api_token_enc=b"x",
                            api_token_nonce=b"x")
    db_session.add(inst)
    await db_session.flush()
    ln = LibreNMSDevice(instance_id=inst.id, legacy_device_id=7, sysname="dist-sw-1.lab", jt_ipam_device_id=sw.id)
    ln2 = LibreNMSDevice(instance_id=inst.id, legacy_device_id=8, sysname="unmapped-sw")
    db_session.add_all([ln, ln2])
    await db_session.flush()
    first = datetime(2026, 9, 1, tzinfo=UTC)
    db_session.add_all([
        ARPEntry(ip="198.51.100.10", mac="02:00:00:00:00:01", device_id=ln.id, interface="Vlan10",
                 first_seen_at=first),
        FDBEntry(mac="02:00:00:00:00:01", device_id=ln.id, port_name="Gi1/0/5", vlan_id_num=10, first_seen_at=first),
        FDBEntry(mac="02:00:00:00:00:02", device_id=ln2.id, port_name="Gi1/0/6", first_seen_at=first),
        FDBEntry(mac="02:00:00:00:00:03", source="mikrotik", switch_device_id=mt.id, port_name="ether2",
                 first_seen_at=first),
    ])
    await db_session.flush()
    arp = await _call(db_session, admin_user, "list_arp")
    a = arp["arp"][0]
    assert a["device"] == "dist-sw-1" and a["first_seen_at"].startswith("2026-09-01")
    assert arp["total"] == 1 and arp["truncated"] is False
    fdb = {f["mac"]: f for f in (await _call(db_session, admin_user, "list_fdb"))["fdb"]}
    assert fdb["02:00:00:00:00:01"]["switch"] == "dist-sw-1"
    assert fdb["02:00:00:00:00:02"]["switch"] == "unmapped-sw", "沒對到 jt-ipam 裝置時用 LibreNMS 的名稱"
    assert fdb["02:00:00:00:00:03"]["switch"] == "mt-router"
    assert fdb["02:00:00:00:00:01"]["first_seen_at"].startswith("2026-09-01")


# ─────────────────── 憑證 ───────────────────

async def test_certificates_filter_before_limit_and_full_metadata(db_session, admin_user) -> None:
    from app.models.certificate import CertAgent, Certificate, CertVersion
    from app.models.device import Device
    now = datetime.now(UTC)
    a = Certificate(name="aaa-long", source_type="none")
    z = Certificate(name="zzz-soon", description="portal", expiry_warn_days=14, source_type="sftp",
                    source_config={"host": "192.0.2.7", "username": "deploy", "cert_path": "/etc/x.pem"},
                    fetch_interval_seconds=3600)
    db_session.add_all([a, z])
    await db_session.flush()
    for cert, days in ((a, 300), (z, 5)):
        db_session.add(CertVersion(certificate_id=cert.id, fingerprint_sha256=("ab" * 32), serial="0A1B",
                                   subject="CN=x", issuer="CN=Lab CA", not_before=now - timedelta(days=1),
                                   not_after=now + timedelta(days=days), cert_pem="-", key_enc=b"k", key_nonce=b"n",
                                   is_current=True))
    dev = Device(name="web-01", type="server")
    db_session.add(dev)
    await db_session.flush()
    db_session.add(CertAgent(name="ag-1", description="iis box", scope_cert_ids=[str(z.id)], device_id=dev.id,
                             reported=[]))
    await db_session.flush()
    # 依名稱排序後 limit=1 只剩 aaa-long → 以前再篩到期就變成空的
    out = await _call(db_session, admin_user, "list_certificates", expiring_within_days=30, limit=1)
    assert [c["name"] for c in out["certificates"]] == ["zzz-soon"] and out["total"] == 1
    c = out["certificates"][0]
    assert c["issuer"] == "CN=Lab CA" and c["subject"] == "CN=x" and c["serial"] == "0A1B"
    assert c["fingerprint_sha256"] == "ab" * 32 and c["not_before"]
    assert c["description"] == "portal" and c["expiry_warn_days"] == 14 and c["fetch_interval_seconds"] == 3600
    assert c["source_config"] == {"host": "192.0.2.7", "username": "deploy", "cert_path": "/etc/x.pem"}
    dist = await _call(db_session, admin_user, "list_cert_distribution")
    ag = dist["agents"][0]
    assert ag["description"] == "iis box" and ag["device"] == "web-01" and ag["scope_certs"] == ["zzz-soon"]
    assert dist["total"] == 1


# ─────────────────── 攻擊面／曝險 ───────────────────

async def test_attack_surface_filters_before_truncation(db_session, admin_user) -> None:
    from app.models.address import IPAddress
    from app.models.nat import NATTranslation
    sub = await _subnet(db_session)
    ip = IPAddress(subnet_id=sub.id, ip="198.51.100.77", state="used", hostname="target")
    db_session.add(ip)
    await db_session.flush()
    # 服務層以前先取 300 筆 NAT 再讓工具篩選：目標排在後面就被丟掉
    for i in range(305):
        db_session.add(NATTranslation(name=f"dangling-{i:03d}", type="port_forward", dst_port=1000 + i))
    await db_session.flush()
    db_session.add(NATTranslation(name="zz-target", type="port_forward", dst_ip_id=ip.id, dst_port=443))
    await db_session.flush()
    out = await _call(db_session, admin_user, "list_attack_surface", ip="198.51.100.77")
    assert out["total"] == 1 and out["items"][0]["port"] == 443
    full = await _call(db_session, admin_user, "list_attack_surface", limit=10)
    assert full["total"] == 306 and full["truncated"] is True


async def test_check_ip_exposure_covers_nat_source_fields_and_other_vendors(db_session, admin_user) -> None:
    from app.models.address import IPAddress
    from app.models.fortigate import FortiGateAddressObject, FortiGatePolicy
    from app.models.nat import NATTranslation
    fw = await _six_firewalls(db_session)
    ip = IPAddress(subnet_id=fw["sub"].id, ip="198.51.100.10", state="used")
    src = IPAddress(subnet_id=fw["sub"].id, ip="198.51.100.20", state="used")
    db_session.add_all([ip, src])
    await db_session.flush()
    db_session.add(NATTranslation(name="pf-web", type="port_forward", dst_ip_id=ip.id, dst_port=443,
                                  src_ip_id=src.id, src_port=5000, description="from partner",
                                  source_origin=f"pfsense:{fw['pf'].id}"))
    db_session.add(FortiGateAddressObject(firewall_id=fw["fg"].id, name="web-host", value="198.51.100.10/32",
                                          obj_type="ipmask"))
    db_session.add(FortiGatePolicy(firewall_id=fw["fg"].id, policyid="9", name="allow-web", action="accept",
                                   status="enable", srcaddr="all", dstaddr="web-host", service="HTTPS"))
    await db_session.flush()
    out = await _call(db_session, admin_user, "check_ip_exposure", ip="198.51.100.10")
    n = out["nat_rules"][0]
    assert n["src_ip"] == "198.51.100.20" and n["src_port"] == 5000 and n["description"] == "from partner"
    assert n["firewall"] == "pf-1"
    assert any(r.get("source_type") == "fortigate" and r.get("firewall") == "fg-1"
               for r in out["other_firewall_allow_rules"])
    desc = TOOLS["check_ip_exposure"]["description"]
    assert "FortiGate" in desc and "does not" in desc


# ─────────────────── AI 巡檢 ───────────────────

async def test_ai_findings_model_status_and_total(db_session, admin_user) -> None:
    from app.models.ai_finding import AIFinding
    run = uuid.uuid4()
    db_session.add_all([
        AIFinding(run_id=run, severity="high", category="security", title="t1", detail="d", model="qwen3:32b"),
        AIFinding(run_id=run, severity="low", category="hygiene", title="t2", detail="d", status="dismissed"),
    ])
    await db_session.flush()
    out = await _call(db_session, admin_user, "list_ai_findings")
    assert [f["title"] for f in out["findings"]] == ["t1"] and out["findings"][0]["model"] == "qwen3:32b"
    assert out["total"] == 1 and out["truncated"] is False
    both = await _call(db_session, admin_user, "list_ai_findings", status="all")
    assert both["total"] == 2 and {f["status"] for f in both["findings"]} == {"open", "dismissed"}


# ─────────────────── 虛擬化與進階資源 ───────────────────

async def test_vms_carry_vmid_and_cluster(db_session, admin_user) -> None:
    from app.models.advanced import Tenant
    from app.models.virt import VirtCluster, VirtualMachine, VMInterface
    cl = VirtCluster(name=f"pve-{_u()}", type="proxmox")
    tn = Tenant(name=f"t-{_u()}", slug=f"t-{_u()}")
    db_session.add_all([cl, tn])
    await db_session.flush()
    vm = VirtualMachine(cluster_id=cl.id, name="db-01", legacy_vmid=105, node="pve1", kind="vm",
                        tenant_id=tn.id, description="primary db")
    db_session.add(vm)
    await db_session.flush()
    db_session.add(VMInterface(vm_id=vm.id, name="net0", mac="02:00:00:00:01:05", bridge="vmbr0"))
    await db_session.flush()
    out = await _call(db_session, admin_user, "list_vms")
    v = out["vms"][0]
    assert v["vmid"] == 105 and v["cluster"] == cl.name and v["tenant"] == tn.name
    assert v["description"] == "primary db" and v["interfaces"][0]["bridge"] == "vmbr0"
    assert out["total"] == 1 and out["truncated"] is False


async def test_advanced_resources_resolve_groups_and_tenants(db_session, admin_user) -> None:
    from app.models.advanced import ASN, Contact, ContactGroup, Tenant, TenantGroup, WirelessLink, WirelessSSID
    from app.models.device import Device
    tg, cg = TenantGroup(name=f"tg-{_u()}"), ContactGroup(name=f"cg-{_u()}")
    db_session.add_all([tg, cg])
    await db_session.flush()
    tn = Tenant(name=f"t-{_u()}", slug=f"s-{_u()}", group_id=tg.id, description="tenant d")
    db_session.add(tn)
    await db_session.flush()
    a, b = Device(name="ap-a", type="ap"), Device(name="ap-b", type="ap")
    db_session.add_all([a, b])
    await db_session.flush()
    db_session.add_all([
        Contact(name="Alice", group_id=cg.id, description="NOC lead", tenant_id=tn.id),
        ASN(asn=64512, rir="APNIC", tenant_id=tn.id),
        WirelessSSID(ssid="corp", tenant_id=tn.id),
        WirelessLink(name="ptp-1", a_device_id=a.id, b_device_id=b.id, description="roof link"),
    ])
    await db_session.flush()
    t = (await _call(db_session, admin_user, "list_tenants"))["tenants"][0]
    assert t["group"] == tg.name and t["description"] == "tenant d"
    c = (await _call(db_session, admin_user, "list_contacts"))["contacts"][0]
    assert c["group"] == cg.name and c["description"] == "NOC lead" and c["tenant"] == tn.name
    assert (await _call(db_session, admin_user, "list_asns"))["asns"][0]["tenant"] == tn.name
    assert (await _call(db_session, admin_user, "list_ssids"))["ssids"][0]["tenant"] == tn.name
    w = (await _call(db_session, admin_user, "list_wireless_links"))["wireless_links"][0]
    assert w["description"] == "roof link" and (w["a_device"], w["b_device"]) == ("ap-a", "ap-b")


# ─────────────────── 纜線與電力 ───────────────────

async def _patch_path(db):  # type: ignore[no-untyped-def]
    from app.models.device import Device
    from app.models.physical import Cable, CableTermination, DevicePort
    sw, panel, srv = Device(name="sw-1", type="switch"), Device(name="pp-1", type="patch_panel"), \
        Device(name="srv-1", type="server")
    db.add_all([sw, panel, srv])
    await db.flush()
    p_sw = DevicePort(device_id=sw.id, name="Gi1/0/1")
    rear = DevicePort(device_id=panel.id, name="R1", type="rear")
    db.add_all([p_sw, rear])
    await db.flush()
    front = DevicePort(device_id=panel.id, name="F1", type="front", peer_port_id=rear.id)
    p_srv = DevicePort(device_id=srv.id, name="eth0")
    db.add_all([front, p_srv])
    await db.flush()
    rear.peer_port_id = front.id
    c1 = Cable(label="C-1", type="cat6", color="blue", length_m=3, description="rack A")
    c2 = Cable(label="C-2", type="cat6", color="red", length_m=15)
    db.add_all([c1, c2])
    await db.flush()
    db.add_all([
        CableTermination(cable_id=c1.id, side="A", object_type="device_port", object_id=p_sw.id),
        CableTermination(cable_id=c1.id, side="B", object_type="device_port", object_id=front.id),
        CableTermination(cable_id=c2.id, side="A", object_type="device_port", object_id=rear.id),
        CableTermination(cable_id=c2.id, side="B", object_type="device_port", object_id=p_srv.id),
    ])
    await db.flush()
    return c1


async def test_cables_resolve_device_ports_and_trace_follows_patch_panel(db_session, admin_user) -> None:
    c1 = await _patch_path(db_session)
    out = await _call(db_session, admin_user, "list_cables")
    row = next(c for c in out["cables"] if c["label"] == "C-1")
    ends = {t["side"]: t for t in row["terminations"]}
    assert (ends["A"]["device"], ends["A"]["port"]) == ("sw-1", "Gi1/0/1")
    assert (ends["B"]["device"], ends["B"]["port"]) == ("pp-1", "F1")
    assert row["description"] == "rack A" and out["total"] == 2
    tr = await _call(db_session, admin_user, "cable_trace", cable_id=str(c1.id))
    assert tr["cable"]["color"] == "blue" and tr["cable"]["length_m"] == 3
    assert tr["cable"]["description"] == "rack A"
    assert {t["device"] for t in tr["terminations"]} == {"sw-1", "pp-1"}
    assert [n.get("device") for n in tr["path"]][-1] == "srv-1", "多跳要穿過跳接面板走到伺服器"
    desc = TOOLS["cable_trace"]["description"]
    assert "patch panel" in desc.lower()


async def test_list_power_panels_feeds_outlets_and_device_power_ports(db_session, admin_user) -> None:
    from app.models.device import Device
    from app.models.location import Location, Rack
    from app.models.physical import DevicePowerPort, PowerFeed, PowerOutlet, PowerPanel
    loc = Location(name=f"dc-{_u()}")
    db_session.add(loc)
    await db_session.flush()
    rack = Rack(name="R01", location_id=loc.id, u_height=42)
    db_session.add(rack)
    await db_session.flush()
    panel = PowerPanel(name="PP-A", location_id=loc.id, description="UPS A")
    db_session.add(panel)
    await db_session.flush()
    feed = PowerFeed(panel_id=panel.id, name="A1", rack_id=rack.id, description="feed d")
    db_session.add(feed)
    await db_session.flush()
    outlet = PowerOutlet(feed_id=feed.id, rack_id=rack.id, label="PDU-A-01")
    dev = Device(name="srv-9", type="server", rack_id=rack.id)
    db_session.add_all([outlet, dev])
    await db_session.flush()
    db_session.add(DevicePowerPort(device_id=dev.id, name="PSU1", outlet_id=outlet.id, max_watts=450))
    await db_session.flush()
    out = await _call(db_session, admin_user, "list_power", rack_id=str(rack.id))
    assert out["panels"][0]["name"] == "PP-A" and out["panels"][0]["location"] == loc.name
    f = out["feeds"][0]
    assert f["panel"] == "PP-A" and f["rack"] == "R01" and f["description"] == "feed d"
    o = out["outlets"][0]
    assert o["feed"] == "A1" and o["rack"] == "R01"
    assert o["power_ports"] == [{"device": "srv-9", "name": "PSU1", "max_watts": 450}]
    pp = out["device_power_ports"][0]
    assert (pp["device"], pp["name"], pp["outlet"], pp["feed"]) == ("srv-9", "PSU1", "PDU-A-01", "A1")
    assert out["truncated"] is False


# ─────────────────── 端點代理類整合 ───────────────────

async def test_wazuh_agents_sca_and_no_cve_claim(db_session, admin_user) -> None:
    from app.models.wazuh import WazuhAgent, WazuhInstance
    inst = WazuhInstance(name=f"wz-{_u()}", api_url="https://192.0.2.55", api_user="u", api_password_enc=b"x",
                         api_password_nonce=b"x")
    db_session.add(inst)
    await db_session.flush()
    db_session.add(WazuhAgent(instance_id=inst.id, agent_id="001", name="web", status="active",
                              sca_policy="CIS Ubuntu 24.04", sca_score=61, sca_pass=120, sca_fail=77,
                              node_name="node01"))
    await db_session.flush()
    out = await _call(db_session, admin_user, "list_wazuh_agents")
    a = out["agents"][0]
    assert (a["sca_policy"], a["sca_score"], a["sca_pass"], a["sca_fail"]) == ("CIS Ubuntu 24.04", 61, 120, 77)
    assert a["server"] == inst.name and a["node_name"] == "node01"
    assert "CVE" not in TOOLS["list_wazuh_agents"]["description"]
    assert "SCA" in TOOLS["list_wazuh_agents"]["description"]


async def test_ocs_computers_are_grouped_per_computer(db_session, admin_user) -> None:
    from app.models.address import IPAddress
    sub = await _subnet(db_session)
    now = datetime.now(UTC)
    hw = {"system": {"vendor": "Dell", "model": "OptiPlex 7010"}, "memory": {"total_mb": 16384}}
    db_session.add_all([
        IPAddress(subnet_id=sub.id, ip="198.51.100.11", state="used", ocs_id=42, os_ocs="Windows 11",
                  ocs_tag="A-42", last_seen_ocs=now, ocs_hw=hw, hostname="pc-42"),
        IPAddress(subnet_id=sub.id, ip="198.51.100.12", state="used", ocs_id=42, last_seen_ocs=now,
                  hostname="pc-42"),
        IPAddress(subnet_id=sub.id, ip="198.51.100.13", state="used", ocs_id=7, last_seen_ocs=now),
    ])
    await db_session.flush()
    out = await _call(db_session, admin_user, "list_ocs_computers")
    assert out["count"] == 2 and out["total"] == 2, "數的是電腦，不是 IP"
    pc = next(c for c in out["computers"] if c["ocs_id"] == 42)
    assert sorted(i["ip"] for i in pc["ips"]) == ["198.51.100.11", "198.51.100.12"]
    assert pc["hardware"] == hw and pc["os"] == "Windows 11" and pc["tag"] == "A-42"


async def test_rustdesk_peers_report_client_hostname_os_user(db_session, admin_user) -> None:
    from app.models.rustdesk import RustDeskPeer, RustDeskServer
    srv = RustDeskServer(name=f"rd-{_u()}")
    db_session.add(srv)
    await db_session.flush()
    db_session.add(RustDeskPeer(server_id=srv.id, rustdesk_id="123456789", hostname="LAPTOP-7",
                                os_name="Windows 11 Pro", username="alice", client_version="1.4.2",
                                report_ip="198.51.100.30", match_evidence=["report_ip"],
                                last_heartbeat_at=datetime.now(UTC), key_fail_count=3))
    await db_session.flush()
    out = await _call(db_session, admin_user, "list_rustdesk_peers", q="laptop")
    p = out["peers"][0]
    assert (p["reported_hostname"], p["os_name"], p["username"], p["client_version"]) == (
        "LAPTOP-7", "Windows 11 Pro", "alice", "1.4.2")
    assert p["report_ip"] == "198.51.100.30" and p["match_evidence"] == ["report_ip"]
    assert p["key_fail_count"] == 3 and p["last_heartbeat_at"]
    desc = TOOLS["list_rustdesk_peers"]["description"]
    assert "does not store hostnames" not in desc


# ─────────────────── IP 變更評估 ───────────────────

async def _impact_run(db):  # type: ignore[no-untyped-def]
    from app.models.change_impact import ChangePlan, ImpactEvidence, ImpactFinding, ImpactGap, ImpactRun
    from app.services.change_impact.config import set_config
    await set_config(db, {"enabled": True}, updated_by=None)
    start = datetime(2026, 11, 1, 1, 0, tzinfo=UTC)
    plan = ChangePlan(title="move web", scenario_type="ip_renumber", target_type="ip_address",
                      target_id=uuid.uuid4(), target_label="198.51.100.10", parameters={"new_ip": "198.51.100.80"},
                      planned_start=start, planned_end=start + timedelta(hours=2), revision=3)
    db.add(plan)
    await db.flush()
    now = datetime.now(UTC)
    run = ImpactRun(plan_id=plan.id, plan_revision=3, job_status="partial", decision_status="needs_review",
                    completeness="partial", truncated=True, truncation={"what": "relations", "limit": 5000,
                                                                        "stage": "expand"},
                    counts={"findings": 1, "blockers": 0, "review": 1, "evidence": 1, "gaps": 1},
                    started_at=now, completed_at=now, expires_at=now + timedelta(hours=24), error_code=None)
    db.add(run)
    await db.flush()
    plan.latest_run_id = run.id
    db.add(ImpactFinding(run_id=run.id, rule_id="dns.a_record", rule_version="1", subject_type="dns_record",
                         subject_label="www", category="dns", impact="change_required", severity="high",
                         disposition="review", evidence_strength="explicit", reason_code="dns_points_to_ip",
                         params={}, evidence_keys=["ev1"], path_refs=[], fingerprint="f1", sort_key="1",
                         visibility_type="global"))
    db.add(ImpactGap(run_id=run.id, category="dns", reason_code="source_stale", params={"source": "pdns-1"},
                     affected_analysis="dns_records", remediation_hint="sync_now", sort_key="1"))
    ev = ImpactEvidence(run_id=run.id, key="ev1", source_type="dns", source_object_type="dns_record", label="www",
                        observed_at=now, collected_at=now, sanitized_payload={"value": "198.51.100.10"},
                        payload_hash="h", freshness="fresh", visibility_type="global")
    db.add(ev)
    await db.flush()
    return plan, run, ev


async def test_impact_get_run_reports_truncation_counts_and_gap_params(db_session, admin_user) -> None:
    plan, run, ev = await _impact_run(db_session)
    out = await _call(db_session, admin_user, "impact_get_run", run_id=str(run.id))
    assert out["truncated"] is True and out["truncation"]["what"] == "relations"
    assert out["counts"]["findings"] == 1
    assert out["completed_at"] and out["expires_at"] and "error_code" in out
    g = out["gaps"][0]
    assert g["params"] == {"source": "pdns-1"} and g["affected_analysis"] == "dns_records"
    assert out["top_findings"][0]["category"] == "dns"
    desc = TOOLS["impact_get_run"]["description"].lower()
    assert "truncated" in desc and "incomplete" in desc
    plans = await _call(db_session, admin_user, "impact_list_plans")
    p = plans["plans"][0]
    assert p["parameters"] == {"new_ip": "198.51.100.80"} and p["revision"] == 3
    assert p["planned_start"].startswith("2026-11-01") and p["created_at"]
    assert plans["total"] == 1 and plans["truncated"] is False
    fs = await _call(db_session, admin_user, "impact_list_findings", run_id=str(run.id))
    assert fs["findings"][0]["category"] == "dns" and fs["total"] == 1
    e = await _call(db_session, admin_user, "impact_get_evidence", run_id=str(run.id), evidence_id=str(ev.id))
    assert e["collected_at"] and e["freshness"] == "fresh"
    _ = plan
