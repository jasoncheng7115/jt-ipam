"""AI chat 工具 list_vpn_tunnels：能回報 site-to-site 對接狀態。"""

from __future__ import annotations

from app.mcp.tools import list_vpn_tunnels
from app.models.device import Device
from app.models.physical import VPNTunnel


async def test_list_vpn_tunnels_reports_site_to_site(db_session, admin_user):
    a = Device(name="fw-a", type="firewall")
    b = Device(name="fw-b", type="firewall")
    db_session.add_all([a, b])
    await db_session.flush()
    # 對接（兩端都是已知 device）
    db_session.add(VPNTunnel(name="fw-a/wg/x", type="wireguard", status="active",
                             a_device_id=a.id, b_device_id=b.id))
    # 對外站點（對端非管理 device）
    db_session.add(VPNTunnel(name="fw-a/wg/ext", type="wireguard", status="active",
                             a_device_id=a.id, b_endpoint="203.0.113.9"))
    await db_session.flush()

    res = await list_vpn_tunnels(db_session, user=admin_user)
    tuns = {t["name"]: t for t in res["vpn_tunnels"]}
    assert tuns["fw-a/wg/x"]["site_to_site"] is True
    assert tuns["fw-a/wg/x"]["a_device"] == "fw-a"
    assert tuns["fw-a/wg/x"]["b_device"] == "fw-b"
    assert tuns["fw-a/wg/ext"]["site_to_site"] is False
    assert tuns["fw-a/wg/ext"]["b_endpoint"] == "203.0.113.9"
