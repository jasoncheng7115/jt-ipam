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


async def test_fallback_prefers_the_os_address_over_the_bmc(client, db_session, auth_headers):
    """客戶 10-23：DELL R750 有作業系統 198.51.100.13 與 iDRAC 198.51.100.113，沒設主要 IP；
    關係圖抓到 iDRAC 那一個。沒設主要 IP 時，主機名稱像 BMC（idrac／ilo／ipmi…）的排後面，
    其他的照位址排；另外帶 more＝這台還有幾個 IP。"""
    dev = Device(name="DELL R750", type="server")
    db_session.add(dev)
    await db_session.flush()
    sec, sub, _ = await _mk_chain_ip(db_session, cidr="198.51.100.0/24", ip="198.51.100.113", device_id=dev.id)
    bmc = (await db_session.execute(__import__("sqlalchemy").select(IPAddress).where(IPAddress.ip == "198.51.100.113"))).scalar_one()
    bmc.hostname = "idrac-ABC1234.example.local"
    db_session.add(IPAddress(subnet_id=sub.id, ip="198.51.100.13", hostname="r750-1.example.local", device_id=dev.id))
    await db_session.commit()

    r = await client.get(f"/api/v1/devices/{dev.id}/relations", headers=auth_headers)
    ip = next(c for c in r.json()["chain"] if c["type"] == "ip")
    assert ip["label"] == "198.51.100.13"
    assert ip["more"] == 1


async def test_ip_node_lists_every_address_of_the_device_across_subnets(client, db_session, auth_headers):
    """客戶 11-02：關係圖的 IP 卡片「另 1 個 IP」點下去，是拿第一個 IP 去清單模糊搜尋
    （198.51.100.21 連 .214、.217 都出來），也看不到另一個網段的管理介面。
    IP 節點要帶這台裝置所有的 IP（含網段），畫面列出來、點了直接進那個 IP。"""
    dev = Device(name="srv-rel", type="server")
    db_session.add(dev)
    await db_session.flush()
    sec, sub, os_ip = await _mk_chain_ip(db_session, cidr="198.51.100.0/24", ip="198.51.100.21", device_id=dev.id)
    os_ip.hostname = "srv-rel.example.local"
    mgmt = Subnet(section_id=sec.id, cidr="203.0.113.0/24", description="management")
    db_session.add(mgmt)
    await db_session.flush()
    bmc = IPAddress(subnet_id=mgmt.id, ip="203.0.113.21", hostname="idrac-srv-rel.example.local", device_id=dev.id)
    db_session.add(bmc)
    db_session.add(IPAddress(subnet_id=sub.id, ip="198.51.100.214"))   # 不是這台的，不可以出現
    await db_session.commit()

    r = await client.get(f"/api/v1/devices/{dev.id}/relations", headers=auth_headers)
    ip = next(c for c in r.json()["chain"] if c["type"] == "ip")
    assert ip["label"] == "198.51.100.21" and ip["more"] == 1
    assert [(x["ip"], x["subnet"]) for x in ip["ips"]] == [
        ("198.51.100.21", "198.51.100.0/24"), ("203.0.113.21", "203.0.113.0/24")]
    assert ip["ips"][1]["id"] == str(bmc.id) and ip["ips"][1]["hostname"] == "idrac-srv-rel.example.local"


async def test_ip_node_list_only_shows_addresses_the_user_can_see(client, db_session):
    from app.core.security import hash_password
    from app.models.permission import Permission
    from app.models.user import User
    from app.services.auth import issue_access_token
    dev = Device(name="srv-rel2", type="server")
    db_session.add(dev)
    await db_session.flush()
    sec, sub, os_ip = await _mk_chain_ip(db_session, cidr="198.51.100.0/24", ip="198.51.100.31", device_id=dev.id)
    hidden = Subnet(section_id=sec.id, cidr="203.0.113.0/24")
    db_session.add(hidden)
    await db_session.flush()
    db_session.add(IPAddress(subnet_id=hidden.id, ip="203.0.113.31", device_id=dev.id))
    u = User(username="rel-viewer", email="rel-viewer@t.local", display_name="V",
             password_hash=hash_password("TestPassword2026!"), auth_provider="local", is_active=True)
    db_session.add(u)
    await db_session.flush()
    for ot, oid in (("device", dev.id), ("subnet", sub.id)):
        db_session.add(Permission(object_type=ot, object_id=oid, principal_type="user", principal_id=u.id, level="read"))
    await db_session.commit()
    r = await client.get(f"/api/v1/devices/{dev.id}/relations",
                         headers={"Authorization": f"Bearer {issue_access_token(u)}"})
    assert r.status_code == 200, r.text
    ip = next(c for c in r.json()["chain"] if c["type"] == "ip")
    assert [x["ip"] for x in ip.get("ips", [])] == ["198.51.100.31"], "看不到的網段裡的 IP 不列、也不算進「另 N 個」"
    assert "more" not in ip
