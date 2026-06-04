"""裝置關係鏈端點：device → 主要 IP → 子網路 → 區段；無主要 IP 時退回任一連到本裝置的 IP。"""

from __future__ import annotations

from app.models.address import IPAddress
from app.models.device import Device
from app.models.section import Section
from app.models.subnet import Subnet


async def _mk_chain_ip(session, cidr="10.5.0.0/24", ip="10.5.0.10", device_id=None):
    sec = Section(name="rel-sec")
    session.add(sec)
    await session.flush()
    sub = Subnet(section_id=sec.id, cidr=cidr, description="rel-subnet")
    session.add(sub)
    await session.flush()
    addr = IPAddress(subnet_id=sub.id, ip=ip, device_id=device_id)
    session.add(addr)
    await session.flush()
    return sec, sub, addr


async def test_full_chain_via_primary_ip(client, db_session, auth_headers):
    dev = Device(name="rel-dev", type="server")
    db_session.add(dev)
    await db_session.flush()
    sec, sub, addr = await _mk_chain_ip(db_session, device_id=dev.id)
    dev.primary_ip_id = addr.id
    await db_session.commit()

    r = await client.get(f"/api/v1/devices/{dev.id}/relations", headers=auth_headers)
    assert r.status_code == 200, r.text
    chain = r.json()["chain"]
    assert [c["type"] for c in chain] == ["device", "ip", "subnet", "section"]
    by_type = {c["type"]: c for c in chain}
    assert by_type["ip"]["label"] == "10.5.0.10"
    assert by_type["subnet"]["label"] == "10.5.0.0/24"
    assert by_type["section"]["id"] == str(sec.id)


async def test_chain_falls_back_to_linked_ip(client, db_session, auth_headers):
    dev = Device(name="rel-dev-2", type="switch")
    db_session.add(dev)
    await db_session.flush()
    # 沒設 primary_ip，但有 IP 連到本裝置
    await _mk_chain_ip(db_session, cidr="10.6.0.0/24", ip="10.6.0.20", device_id=dev.id)
    await db_session.commit()

    r = await client.get(f"/api/v1/devices/{dev.id}/relations", headers=auth_headers)
    chain = r.json()["chain"]
    assert [c["type"] for c in chain] == ["device", "ip", "subnet", "section"]
    assert next(c for c in chain if c["type"] == "ip")["label"] == "10.6.0.20"


async def test_bare_device_chain_is_device_only(client, db_session, auth_headers):
    dev = Device(name="rel-dev-3", type="other")
    db_session.add(dev)
    await db_session.commit()

    r = await client.get(f"/api/v1/devices/{dev.id}/relations", headers=auth_headers)
    chain = r.json()["chain"]
    assert [c["type"] for c in chain] == ["device"]


async def test_relations_404_for_unknown_device(client, db_session, auth_headers):
    import uuid
    r = await client.get(f"/api/v1/devices/{uuid.uuid4()}/relations", headers=auth_headers)
    assert r.status_code == 404
