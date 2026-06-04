"""IPsec site-to-site 對接（best-effort）：用端點位址跨 tunnel 比對，
對接成立時標 pairing_method=ipsec_endpoint。涵蓋本次「以對端 tunnel 本地端點」配對的擴充。"""

from __future__ import annotations

from app.models.device import Device
from app.models.physical import VPNTunnel
from app.services.opnsense_firewall import link_ipsec_peers


async def test_ipsec_pairs_by_peer_local_endpoint(db_session):
    dev_a = Device(name="fw-a", type="firewall")
    dev_b = Device(name="fw-b", type="firewall")
    db_session.add_all([dev_a, dev_b])
    await db_session.flush()

    # B 的 tunnel 宣告自己的本地端點（WAN）= 203.0.113.9
    t_b = VPNTunnel(name="b-to-a", type="ipsec_ikev2", a_device_id=dev_b.id,
                    a_endpoint="203.0.113.9", b_endpoint="198.51.100.7")
    # A 的 tunnel 對端 = 203.0.113.9（= B 的本地端點）→ 應配對到 dev_b
    t_a = VPNTunnel(name="a-to-b", type="ipsec_ikev2", a_device_id=dev_a.id,
                    a_endpoint="198.51.100.7", b_endpoint="203.0.113.9")
    db_session.add_all([t_b, t_a])
    await db_session.flush()

    linked = await link_ipsec_peers(db_session)
    assert linked >= 1
    assert t_a.b_device_id == dev_b.id
    assert t_a.pairing_method == "ipsec_endpoint"   # best-effort 標記


async def test_ipsec_unlinks_when_endpoint_gone(db_session):
    dev_a = Device(name="fw-a2", type="firewall")
    db_session.add(dev_a)
    await db_session.flush()
    # 對端位址對不到任何裝置，但 b_device_id 先前被設（用真實裝置）→ 應被還原
    t = VPNTunnel(name="a-orphan", type="ipsec_ikev2", a_device_id=dev_a.id,
                  b_endpoint="192.0.2.250", b_device_id=dev_a.id,
                  pairing_method="ipsec_endpoint")
    db_session.add(t)
    await db_session.flush()

    await link_ipsec_peers(db_session)
    assert t.b_device_id is None
    assert t.pairing_method is None
