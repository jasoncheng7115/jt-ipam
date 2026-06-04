"""裝置清單的有效 IP 解析與「一鍵關聯」旗標：
- 裝置名稱本身是 IP，且有同 IP 的位址物件但尚未連結 → ip_match_id（可一鍵關聯）
- 位址物件已連到本裝置 → ip_address_id（可點進），但不再給 ip_match_id
- 名稱是 IP 但無對應位址物件 → 只有 ip，無 ip_address_id / ip_match_id
"""

from __future__ import annotations

from app.models.address import IPAddress
from app.models.device import Device
from app.models.section import Section
from app.models.subnet import Subnet


async def _mk_ip(session, cidr: str, ip: str, device_id=None) -> IPAddress:
    sec = Section(name=f"dim-{ip}")
    session.add(sec)
    await session.flush()
    sub = Subnet(section_id=sec.id, cidr=cidr)
    session.add(sub)
    await session.flush()
    obj = IPAddress(subnet_id=sub.id, ip=ip, device_id=device_id)
    session.add(obj)
    await session.flush()
    return obj


def _find(items, name):
    return next(d for d in items if d["name"] == name)


async def test_matching_unlinked_ip_offers_link(client, db_session, auth_headers):
    dev = Device(name="10.20.0.5", type="switch")
    db_session.add(dev)
    await db_session.flush()
    addr = await _mk_ip(db_session, "10.20.0.0/24", "10.20.0.5", device_id=None)
    await db_session.commit()

    r = await client.get("/api/v1/devices", headers=auth_headers)
    d = _find(r.json()["items"], "10.20.0.5")
    assert d["ip"] == "10.20.0.5"
    assert d["ip_address_id"] == str(addr.id)
    assert d["ip_match_id"] == str(addr.id)   # 未連結 → 可一鍵關聯


async def test_already_linked_ip_no_match_flag(client, db_session, auth_headers):
    dev = Device(name="sw-linked", type="switch")
    db_session.add(dev)
    await db_session.flush()
    addr = await _mk_ip(db_session, "10.21.0.0/24", "10.21.0.9", device_id=dev.id)
    dev.primary_ip_id = addr.id
    await db_session.commit()

    r = await client.get("/api/v1/devices", headers=auth_headers)
    d = _find(r.json()["items"], "sw-linked")
    assert d["ip"] == "10.21.0.9"
    assert d["ip_address_id"] == str(addr.id)
    assert d["ip_match_id"] is None   # 已連到本裝置 → 不需再關聯


async def test_name_is_ip_without_address_object(client, db_session, auth_headers):
    dev = Device(name="10.22.0.250", type="server")
    db_session.add(dev)
    await db_session.commit()

    r = await client.get("/api/v1/devices", headers=auth_headers)
    d = _find(r.json()["items"], "10.22.0.250")
    assert d["ip"] == "10.22.0.250"   # 名稱是 IP → 仍顯示
    assert d["ip_address_id"] is None
    assert d["ip_match_id"] is None
