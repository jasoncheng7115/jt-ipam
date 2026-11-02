"""IP 變更評估規則第 5 版（2026-10-21 一份正式站台的改址評估逐筆稽核後修正）。

1. CNAME 有存卻寫「沒有保存 CNAME」的缺口，而且漏掉指向舊位址的別名 → 改成實際追 CNAME 鏈
2. LXC 容器的位址寫在 Proxmox 設定裡（改址＝改設定），以前一律「僅參考」→ 新規則 virt.config_static_ip
3. OCS、虛擬機的證據時效寫死「未知」→ 用來源的時效
4. Wazuh 代理沒綁固定位址（register_ip 為 any）只是參考，不該算進「需覆核」
5. 同一個比對群組的 DNS 伺服器「不回報真實 TTL」合併成一筆缺口
6. 沒有任何租約相關發現時，不列「DHCP 租約到期時間沒有保存」
7. NAT 與防火牆規則的名稱帶上防火牆、介面與埠，同名的幾條分得出來
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.models.dns import DNSRecord, DNSServer, DNSZone
from app.models.nat import NATTranslation
from app.services.change_impact.model import RULES, RULES_VERSION
from tests.test_change_impact_engine import _dns, _gap_codes, _ip, _net, _opnsense, _rules, _run


def test_rules_version_bumped() -> None:
    assert int(RULES_VERSION) >= 5   # 之後的版本（6＝電路 WAN 位址與租用設備）沿用這些規則


async def test_cname_chain_to_the_old_address_is_found(db_session, admin_user) -> None:
    sub = await _net(db_session)
    root = await _ip(db_session, sub, "198.51.100.10")
    await _dns(db_session, [("kvm", "A", "198.51.100.10"), ("amviz", "CNAME", "kvm.example.net."),
                            ("deep", "CNAME", "amviz.example.net."), ("other", "CNAME", "elsewhere.example.org.")])
    res = await _run(db_session, admin_user, root, "198.51.100.80")
    cn = [f for f in res.findings if f.rule_id == "dns.cname_chain"]
    assert sorted(f.params["name"] for f in cn) == ["amviz.example.net", "deep.example.net"]
    deep = next(f for f in cn if f.params["name"] == "deep.example.net")
    assert deep.params["target"] == "kvm.example.net"                 # 最後指到哪個 A 記錄
    assert "dns_cname_not_collected" not in _gap_codes(res)          # 有存 CNAME 就不說沒存
    assert RULES["dns.cname_chain"].impact == "reference_only"        # 別名本身不用改，但要列入驗證


async def test_cname_loop_stops(db_session, admin_user) -> None:
    sub = await _net(db_session)
    root = await _ip(db_session, sub, "198.51.100.10")
    await _dns(db_session, [("www", "A", "198.51.100.10"), ("a", "CNAME", "b.example.net."),
                            ("b", "CNAME", "a.example.net."), ("c", "CNAME", "www.example.net.")])
    res = await _run(db_session, admin_user, root, "198.51.100.80")
    assert [f.params["name"] for f in res.findings if f.rule_id == "dns.cname_chain"] == ["c.example.net"]


async def _pve(db, kind: str, ip: str) -> None:  # type: ignore[no-untyped-def]
    from app.models.virt import ProxmoxInstance, VirtCluster, VirtualMachine, VMInterface
    c = VirtCluster(name=f"cluster-{uuid.uuid4().hex[:4]}", type="proxmox")
    db.add(c)
    await db.flush()
    db.add(ProxmoxInstance(cluster_id=c.id, api_url="https://192.0.2.9:8006",
                           auth_username="root@pam", auth_token_id="t", enabled=True, last_sync_at=datetime.now(UTC)))
    vm = VirtualMachine(cluster_id=c.id, name="web-proxy", kind=kind, status="running", node="pve1")
    db.add(vm)
    await db.flush()
    db.add(VMInterface(vm_id=vm.id, name="net0", primary_ip=ip))
    await db.flush()


async def test_container_static_address_needs_a_config_change(db_session, admin_user) -> None:
    sub = await _net(db_session)
    root = await _ip(db_session, sub, "198.51.100.10")
    await _pve(db_session, "ct", "198.51.100.10")
    res = await _run(db_session, admin_user, root, "198.51.100.80")
    f = next(f for f in res.findings if f.rule_id.startswith("virt."))
    assert f.rule_id == "virt.config_static_ip"
    assert RULES["virt.config_static_ip"].impact == "change_required"
    ev = next(e for e in res.evidence if e.key == f.evidence_keys[0])
    assert ev.freshness == "fresh"                                    # 用 Proxmox 來源的時效，不是未知
    assert ev.integration_ref and ev.integration_ref.startswith("proxmox:")


async def test_vm_address_from_the_guest_agent_stays_reference_only(db_session, admin_user) -> None:
    sub = await _net(db_session)
    root = await _ip(db_session, sub, "198.51.100.10")
    await _pve(db_session, "vm", "198.51.100.10")
    res = await _run(db_session, admin_user, root, "198.51.100.80")
    assert "virt.vm_interface" in _rules(res) and "virt.config_static_ip" not in _rules(res)


def test_wazuh_agent_without_a_fixed_address_is_informational() -> None:
    assert RULES["monitoring.wazuh_agent"].disposition == "informational"
    assert RULES["monitoring.wazuh_register_ip"].disposition == "review"     # 綁固定位址的另一條仍要覆核


async def test_ttl_gaps_of_one_compare_group_are_merged(db_session, admin_user) -> None:
    from app.models.dns_compare_group import DNSCompareGroup
    sub = await _net(db_session)
    root = await _ip(db_session, sub, "198.51.100.10")
    g = DNSCompareGroup(name=f"ucs-{uuid.uuid4().hex[:4]}")
    db_session.add(g)
    await db_session.flush()
    for n in ("dc1", "dc2"):
        srv = DNSServer(name=f"{n}-{uuid.uuid4().hex[:4]}", type="univention_ucs", enabled=True,
                        last_sync_at=datetime.now(UTC), compare_group_id=g.id)
        db_session.add(srv)
        await db_session.flush()
        z = DNSZone(server_id=srv.id, name="example.net", type="forward")
        db_session.add(z)
        await db_session.flush()
        db_session.add(DNSRecord(zone_id=z.id, name="www", type="A", value="198.51.100.10", ttl=300))
    res = await _run(db_session, admin_user, root, "198.51.100.80")
    ttl = [g for g in res.gaps if g.reason_code == "ttl_unknown"]
    assert len(ttl) == 1
    assert "dc1" in ttl[0].params["server"] and "dc2" in ttl[0].params["server"]


async def test_lease_expiry_gap_only_when_leases_matter(db_session, admin_user) -> None:
    from app.models.dhcp_standalone import IscDhcpServer
    sub = await _net(db_session)
    root = await _ip(db_session, sub, "198.51.100.10")
    db_session.add(IscDhcpServer(name=f"isc-{uuid.uuid4().hex[:4]}", enabled=True, last_sync_at=datetime.now(UTC)))
    res = await _run(db_session, admin_user, root, "198.51.100.80")
    assert not [f for f in res.findings if f.rule_id.startswith("dhcp.")]
    assert "dhcp_lease_expiry_unknown" not in _gap_codes(res)          # 沒有租約相關的發現：這個限制與本次無關


async def test_nat_and_rule_labels_say_which_firewall_interface_and_port(db_session, admin_user) -> None:
    from app.models.firewall_rule import OPNsenseRule
    sub = await _net(db_session)
    root = await _ip(db_session, sub, "198.51.100.10")
    fw = await _opnsense(db_session, sync_nat=True)
    db_session.add(OPNsenseRule(firewall_id=fw.id, legacy_uuid=uuid.uuid4().hex, enabled=True, sequence=5,
                                action="pass", interface="wan2", protocol="TCP", destination_net="198.51.100.10",
                                destination_port="443", description="Reverse Proxy", raw={},
                                last_synced_at=datetime.now(UTC)))
    db_session.add(NATTranslation(name="Reverse Proxy", type="port_forward", dst_ip_id=root.id, protocol="tcp",
                                  dst_port=443, source_origin=f"opnsense:{fw.id}"))
    res = await _run(db_session, admin_user, root, "198.51.100.80")
    nat = next(f for f in res.findings if f.rule_id == "nat.translation")
    assert fw.name in nat.subject_label and "443" in nat.subject_label
    assert nat.params["firewall"] == fw.name and nat.params["dst_port"] == 443
    rule = next(f for f in res.findings if f.rule_id == "fw.rule_exact")
    assert "wan2" in rule.subject_label and "443" in rule.subject_label
