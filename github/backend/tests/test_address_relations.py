"""位址關係鏈端點：section → subnet → ip → device（→ rack/location）；
缺的環節省略；無權限的子網路看不到。"""

from __future__ import annotations

import uuid

from app.models.address import IPAddress
from app.models.device import Device
from app.models.section import Section
from app.models.subnet import Subnet


async def _mk(session, *, with_device: bool):
    sec = Section(name="ar-sec")
    session.add(sec)
    await session.flush()
    sub = Subnet(section_id=sec.id, cidr="10.7.0.0/24", description="ar-subnet")
    session.add(sub)
    await session.flush()
    dev_id = None
    if with_device:
        dev = Device(name="ar-dev", type="server")
        session.add(dev)
        await session.flush()
        dev_id = dev.id
    addr = IPAddress(subnet_id=sub.id, ip="10.7.0.30", device_id=dev_id, hostname="host-a")
    session.add(addr)
    await session.flush()
    return sec, sub, addr


async def test_chain_up_to_device(client, db_session, auth_headers):
    sec, sub, addr = await _mk(db_session, with_device=True)
    await db_session.commit()

    r = await client.get(f"/api/v1/addresses/{addr.id}/relations", headers=auth_headers)
    assert r.status_code == 200, r.text
    chain = r.json()["chain"]
    assert [c["type"] for c in chain] == ["section", "subnet", "ip", "device"]
    by = {c["type"]: c for c in chain}
    assert by["section"]["id"] == str(sec.id)
    assert by["subnet"]["label"] == "10.7.0.0/24"
    assert by["ip"]["label"] == "10.7.0.30"
    assert by["ip"]["sub"] == "host-a"


async def test_chain_without_device_stops_at_ip(client, db_session, auth_headers):
    sec, sub, addr = await _mk(db_session, with_device=False)
    await db_session.commit()

    r = await client.get(f"/api/v1/addresses/{addr.id}/relations", headers=auth_headers)
    chain = r.json()["chain"]
    assert [c["type"] for c in chain] == ["section", "subnet", "ip"]


async def test_relations_404_for_unknown_address(client, db_session, auth_headers):
    r = await client.get(f"/api/v1/addresses/{uuid.uuid4()}/relations", headers=auth_headers)
    assert r.status_code == 404
