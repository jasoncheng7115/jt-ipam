"""topology builder 測試：VPN 對接去重 + 遠端站點 fallback。

對應 app/services/topology.py 的 VPN 區塊：
- 對接的兩端各有一條 tunnel（A→B、B→A），同一對 device 只該畫一條邊。
- 對端是外部 endpoint（b_device 為 null）時，要產生一個 vpn_site 節點 + 一條邊。
"""

from __future__ import annotations

from app.models.device import Device
from app.models.physical import VPNTunnel
from app.services.topology import build_topology


async def _device(session, name: str) -> Device:
    d = Device(name=name, type="firewall")
    session.add(d)
    await session.flush()
    return d


async def test_vpn_confirmed_pair_dedups_to_one_edge(db_session):
    a = await _device(db_session, "fw-a")
    b = await _device(db_session, "fw-b")
    # 對接：兩端各一條 tunnel，互指對方 device
    db_session.add(VPNTunnel(name="fw-a/wg/x", type="wireguard", status="active",
                             a_device_id=a.id, b_device_id=b.id))
    db_session.add(VPNTunnel(name="fw-b/wg/y", type="wireguard", status="active",
                             a_device_id=b.id, b_device_id=a.id))
    await db_session.flush()

    g = await build_topology(db_session, include_wireless=False, include_l3=False)
    vpn_edges = [e for e in g["edges"] if e["data"]["kind"] == "vpn"]
    assert len(vpn_edges) == 1, "對接兩端應去重成一條邊"
    src, tgt = vpn_edges[0]["data"]["source"], vpn_edges[0]["data"]["target"]
    assert {src, tgt} == {str(a.id), str(b.id)}


async def test_paired_vpn_draws_edge_even_when_peer_filtered_out(db_session):
    """子網路過濾下，對端防火牆不在該網段時，對接的 VPN 仍要連成 device↔device 線。"""
    from app.models.address import IPAddress
    from app.models.section import Section
    from app.models.subnet import Subnet

    a = await _device(db_session, "fw-a")   # 在過濾的網段內
    b = await _device(db_session, "fw-b")   # 不在過濾的網段內
    sec = Section(name="topo-sec"); db_session.add(sec); await db_session.flush()
    sub = Subnet(section_id=sec.id, cidr="10.55.0.0/24"); db_session.add(sub); await db_session.flush()
    # 只有 fw-a 在此網段有 IP（fw-b 沒有 → 預設會被過濾掉）
    db_session.add(IPAddress(subnet_id=sub.id, ip="10.55.0.1", device_id=a.id))
    db_session.add(VPNTunnel(name="fw-a/wg/x", type="wireguard", status="active",
                             a_device_id=a.id, b_device_id=b.id))
    db_session.add(VPNTunnel(name="fw-b/wg/y", type="wireguard", status="active",
                             a_device_id=b.id, b_device_id=a.id))
    await db_session.flush()

    g = await build_topology(db_session, subnet_ids=[sub.id], include_wireless=False, include_l3=False)
    vpn_edges = [e for e in g["edges"] if e["data"]["kind"] == "vpn"]
    site_nodes = [n for n in g["nodes"] if n["data"].get("type") == "vpn_site"]
    assert len(vpn_edges) == 1, "對接 VPN 應連成一條 device↔device 線"
    assert {vpn_edges[0]["data"]["source"], vpn_edges[0]["data"]["target"]} == {str(a.id), str(b.id)}
    assert site_nodes == [], "對接成功時不應退回遠端站點節點"


async def test_vpn_external_site_makes_site_node(db_session):
    a = await _device(db_session, "fw-a")
    db_session.add(VPNTunnel(name="fw-a/wg/ext", type="wireguard", status="active",
                             a_device_id=a.id, b_device_id=None, b_endpoint="203.0.113.9"))
    await db_session.flush()

    g = await build_topology(db_session, include_wireless=False, include_l3=False)
    site_nodes = [n for n in g["nodes"] if n["data"].get("type") == "vpn_site"]
    vpn_edges = [e for e in g["edges"] if e["data"]["kind"] == "vpn"]
    assert len(site_nodes) == 1
    assert site_nodes[0]["data"]["label"] == "203.0.113.9"
    assert len(vpn_edges) == 1
    assert vpn_edges[0]["data"]["target"] == site_nodes[0]["data"]["id"]
