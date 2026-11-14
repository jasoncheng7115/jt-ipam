"""刪除整合實例時收回它寫進共用表的資料；VPN 通道以來源欄位認定歸屬（2026-09-26 稽核）。

以前刪掉一台 DNS／防火牆／Wazuh…，它回報的主機名稱、租約旗標、固定分配、NAT、VPN 通道都還在，
而且再也不會有同步去清。VPN 通道以「防火牆名稱/」前綴認定歸屬 → 防火牆一改名，舊的通道就成了孤兒。
"""
from __future__ import annotations

import ast
import inspect
import uuid
from pathlib import Path

import pytest
from app.models.address import IPAddress
from app.models.dhcp import DHCPLeaseSighting, DHCPPoolRange, DHCPReservation
from app.models.ip_hostname import IPHostnameObservation
from app.models.nat import NATTranslation
from app.models.physical import VPNTunnel
from app.models.section import Section
from app.models.subnet import Subnet
from app.services import dhcp_leases, integration_cleanup
from app.services.hostname_reports import HostnameRun
from sqlalchemy import func, select

A = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
B = uuid.UUID("00000000-0000-0000-0000-0000000000b2")


async def _ip(db, addr="198.51.100.10") -> IPAddress:
    sec = Section(name=f"sec-{uuid.uuid4().hex[:6]}")
    db.add(sec)
    await db.flush()
    sub = Subnet(section_id=sec.id, cidr="198.51.100.0/24")
    db.add(sub)
    await db.flush()
    ip = IPAddress(subnet_id=sub.id, ip=addr)
    db.add(ip)
    await db.flush()
    return ip


async def _seed(db, ip, source: str, sid: uuid.UUID, tag: str) -> None:
    run = HostnameRun(db, source=source, origin=f"{source}:{sid}", peers=2)
    run.report(ip, f"{tag}.example.com")
    await run.finish(complete=True)
    lr = dhcp_leases.LeaseRun(db, source_type=source, source_id=sid)
    lr.saw(ip)
    await lr.finish(complete=True)
    db.add(DHCPReservation(source_type=source, source_id=sid, ip=str(ip.ip), ip_address_id=ip.id,
                           source="kea"))
    db.add(DHCPPoolRange(source_type=source, source_id=sid, start_ip="198.51.100.100",
                         end_ip="198.51.100.200"))
    db.add(NATTranslation(name=f"{tag}-nat", type="port_forward", source_origin=f"{source}:{sid}",
                          external_id=tag))
    db.add(VPNTunnel(name=f"{tag}/ipsec/t1", type="ipsec_ikev2", source_origin=f"{source}:{sid}"))
    await db.flush()


async def _count(db, model, *where) -> int:
    return (await db.execute(select(func.count()).select_from(model).where(*where))).scalar_one()


async def test_forget_instance_removes_only_that_instances_rows(db_session) -> None:
    ip = await _ip(db_session)
    await _seed(db_session, ip, "fortigate", A, "fgt-a")
    await _seed(db_session, ip, "fortigate", B, "fgt-b")

    await integration_cleanup.forget_instance(db_session, source="fortigate", source_id=A)
    await db_session.flush()

    for model, col in ((DHCPReservation, DHCPReservation.source_id),
                       (DHCPPoolRange, DHCPPoolRange.source_id),
                       (DHCPLeaseSighting, DHCPLeaseSighting.source_id)):
        assert await _count(db_session, model, col == A) == 0, model.__name__
        assert await _count(db_session, model, col == B) == 1, model.__name__
    assert await _count(db_session, NATTranslation, NATTranslation.source_origin == f"fortigate:{A}") == 0
    assert await _count(db_session, NATTranslation, NATTranslation.source_origin == f"fortigate:{B}") == 1
    assert await _count(db_session, VPNTunnel, VPNTunnel.source_origin == f"fortigate:{A}") == 0
    assert await _count(db_session, VPNTunnel, VPNTunnel.source_origin == f"fortigate:{B}") == 1
    obs = (await db_session.execute(select(IPHostnameObservation.hostname).where(
        IPHostnameObservation.ip_id == ip.id, IPHostnameObservation.source == "fortigate"))).scalar_one()
    assert obs == "fgt-b.example.com", "A 回報的名稱收回，改用 B 還在回報的"
    await db_session.refresh(ip)
    assert ip.in_dhcp_lease, "B 還發著租約"

    await integration_cleanup.forget_instance(db_session, source="fortigate", source_id=B)
    await db_session.refresh(ip)
    assert not ip.in_dhcp_lease
    assert not ip.dhcp_reserved
    assert ip.hostname is None


async def test_deleting_a_dns_server_takes_its_names_with_it(client, auth_headers, db_session) -> None:
    """端到端：透過 API 刪掉 DNS 伺服器 → 它回報的主機名稱從 IP 上消失。"""
    from app.models.dns import DNSServer

    ip = await _ip(db_session, "198.51.100.77")
    srv = DNSServer(name=f"dns-{uuid.uuid4().hex[:6]}", type="bind9")
    db_session.add(srv)
    await db_session.flush()
    run = HostnameRun(db_session, source="dns", origin=f"dns:{srv.id}")
    run.report(ip, "old-host.example.com")
    await run.finish(complete=True)
    await db_session.commit()
    assert ip.hostname == "old-host.example.com"

    r = await client.delete(f"/api/v1/dns/servers/{srv.id}", headers=auth_headers)
    assert r.status_code == 204, r.text
    await db_session.refresh(ip)
    assert ip.hostname is None


# 會寫進共用表的整合：刪除處理函式必須收回資料。新增整合時加進來（漏了這個測試會提醒）。
_DELETE_HANDLERS = {
    "firewall.py": "delete_firewall",          # OPNsense
    "pfsense.py": "delete_firewall",
    "fortigate.py": "cleanup_shared_rows",
    "paloalto.py": "cleanup_shared_rows",
    "checkpoint.py": "delete_server",          # Check Point 管理伺服器（NAT）
    "mikrotik.py": "cleanup_shared_rows",
    "windows_dhcp.py": "delete_server",
    "dns.py": "delete_server",
    "adguard.py": "delete_instance",
    "librenms.py": "delete_instance",
    "zabbix.py": "delete_instance",
    "checkmk.py": "delete_instance",
    "wazuh.py": "delete_instance",
    "ocs.py": "delete_server",
    "rustdesk.py": "delete_server",
    "isoinsight.py": "delete_source",          # ISOinsight 整合
    "technitium.py": "delete_server",          # Technitium DHCP
    "virt.py": "delete_proxmox",
    "hyperv.py": "delete_instance",            # Hyper-V（直連主機或 SCVMM）
    "openstack.py": "delete_instance",         # Bigstack CubeCOS／OpenStack
    "scan_agents.py": "delete_agent",
}
# 同一個檔案裡有好幾個整合的（字典一個檔案只能對一個函式）
_MORE_DELETE_HANDLERS = [
    ("dhcp_standalone.py", "delete_kea"),       # 獨立 Kea DHCP（issue #45）
    ("dhcp_standalone.py", "delete_isc"),       # 獨立 ISC DHCP（issue #45）
    ("checkpoint_gaia.py", "delete_target"),    # Check Point 閘道的 Gaia API（第二階段）
]
_ENDPOINTS = Path(__file__).resolve().parent.parent / "app" / "api" / "v1" / "endpoints"


@pytest.mark.parametrize(("fname", "func"), sorted([*_DELETE_HANDLERS.items(), *_MORE_DELETE_HANDLERS]))
def test_every_integration_delete_forgets_its_shared_rows(fname: str, func: str) -> None:
    tree = ast.parse((_ENDPOINTS / fname).read_text(encoding="utf-8"))
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef) and n.name == func), None)
    assert fn is not None, f"{fname} 找不到 {func}"
    called = {n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
              for n in ast.walk(fn) if isinstance(n, ast.Call)}
    assert called & {"forget_instance", "forget_proxmox_instance", "forget_hyperv_instance", "forget_openstack_instance",
                     "cleanup_shared_rows"}, (
        f"{fname}:{func} 刪除實例時沒有收回它寫進共用表的資料（integration_cleanup.forget_instance）")


def test_every_hostname_source_is_covered_by_a_delete_handler() -> None:
    """每個會回報主機名稱的整合都要在上面的清單裡（手動與掃描代理的子來源除外）。"""
    from app.models.ip_hostname import HOSTNAME_SOURCES
    src = inspect.getsource(integration_cleanup)
    assert "forget_origin" in src
    covered = {"opnsense", "pfsense", "fortigate", "paloalto", "mikrotik", "windows_dhcp", "dns",
               "adguard", "librenms", "zabbix", "checkmk", "wazuh", "ocs", "proxmox", "scanner", "netbios", "mdns",
               "kea_dhcp", "isc_dhcp", "isoinsight", "technitium", "rustdesk", "checkpoint", "hyperv", "openstack"}
    missing = set(HOSTNAME_SOURCES) - covered - {"manual"}
    assert not missing, f"這些主機名稱來源沒有刪除時的收回：{sorted(missing)}"


# ── VPN 通道的歸屬 ──

async def test_a_renamed_firewall_does_not_leave_orphan_tunnels(db_session, monkeypatch) -> None:
    from app.models.fortigate import FortiGateFirewall
    from app.services import fortigate as fg

    fw = FortiGateFirewall(name="fgt-old", api_url="https://192.0.2.1",
                           api_token_enc=b"x", api_token_nonce=b"x", vdoms=["root"])
    db_session.add(fw)
    await db_session.flush()

    async def fake(_fw, path, *, vdom=None, timeout=15.0):
        if path == fg.EP_VPN_IPSEC:
            return [{"name": "to-branch", "rgwy": "198.51.100.7", "proxyid": []}]
        return []
    monkeypatch.setattr(fg, "_api_get", fake)
    await fg.sync_vpn(db_session, fw, ["root"])
    fw.name = "fgt-new"
    await fg.sync_vpn(db_session, fw, ["root"])
    await db_session.flush()
    names = (await db_session.execute(select(VPNTunnel.name).where(
        VPNTunnel.source_origin == f"fortigate:{fw.id}"))).scalars().all()
    assert names == ["fgt-new/ipsec/root/to-branch"]
    assert await _count(db_session, VPNTunnel, VPNTunnel.name.startswith("fgt-old/")) == 0


async def test_mikrotik_tunnel_cleanup_does_not_treat_underscore_as_a_wildcard(db_session, monkeypatch) -> None:
    """以前用 LIKE 'rtr_a/wireguard/%' —— 底線是萬用字元，會清到 rtrXa 的通道。"""
    from app.models.mikrotik import MikroTikRouter
    from app.services import mikrotik as mt

    other = VPNTunnel(name="rtrXa/wireguard/wg0/peer", type="wireguard")
    db_session.add(other)
    router = MikroTikRouter(name="rtr_a", api_url="https://192.0.2.9", api_username="ipam",
                            api_password_enc=b"x", api_password_nonce=b"y",
                            section_delay_ms=0, cpu_load_limit=0)
    db_session.add(router)
    await db_session.flush()

    async def fake_get(_r, path, *, client=None, **_kw):
        if path == mt.EP_WG_PEERS:
            return [{"interface": "wg0", "name": "p1", "public-key": "k1"}]
        return []
    monkeypatch.setattr(mt, "_get", fake_get)
    monkeypatch.setattr(mt, "_breathe", lambda _r: _noop())
    await mt.sync_vpn(db_session, router, client=None)
    await db_session.flush()
    assert await _count(db_session, VPNTunnel, VPNTunnel.name == "rtrXa/wireguard/wg0/peer") == 1


async def _noop() -> None:
    return None


async def test_deleting_a_librenms_instance_takes_its_arp_and_fdb(db_session) -> None:
    """ARP／FDB 對實例的外鍵是 SET NULL：以前刪了實例，觀測變成沒有歸屬、繼續參與推算。"""
    from app.models.librenms import ARPEntry, FDBEntry, LibreNMSInstance

    inst = LibreNMSInstance(name=f"lnms-{uuid.uuid4().hex[:6]}", api_url="https://librenms.example",
                            api_token_enc=b"x", api_token_nonce=b"y")
    db_session.add(inst)
    await db_session.flush()
    db_session.add(ARPEntry(ip="198.51.100.5", mac="00:00:5e:00:53:05", instance_id=inst.id))
    db_session.add(FDBEntry(mac="00:00:5e:00:53:05", instance_id=inst.id, port_name="ge-0"))
    await db_session.flush()
    await integration_cleanup.forget_instance(db_session, source="librenms", source_id=inst.id)
    assert await _count(db_session, ARPEntry, ARPEntry.instance_id == inst.id) == 0
    assert await _count(db_session, FDBEntry, FDBEntry.instance_id == inst.id) == 0
