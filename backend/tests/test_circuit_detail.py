"""電路詳細資料（客戶許願清單 2026-10-28）：聯絡人、WAN 位址、租用設備。

- 聯絡人角色有四個內建（業務、機房現場、NOC、帳務），內建的不能刪；角色、聯絡人指派可以改、可以刪
- 電路的 WAN 位址關聯到 IPAM：子網路（ISP 給的一段）或個別 IP（不連續）；都要是已登記的紀錄
- 詳細資料逐一列出 WAN IP：IPAM 狀態、使用的裝置、NAT 數、最近使用時間與跡象數
- 舊的文字欄（固定 IP / CIDR、閘道）可以一鍵轉成關聯
- 租用設備：裝置記錄向哪個供應商租用、掛在哪條電路、要不要歸還、期限
- 看不到的子網路不透露位址；寫入只限管理員，都留稽核
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from app.models.address import IPAddress
from app.models.advanced import Circuit, Provider
from app.models.audit import AuditLog
from app.models.device import Device
from app.models.nat import NATTranslation
from app.models.permission import Permission
from app.models.section import Section
from app.models.subnet import Subnet
from app.models.user import User
from sqlalchemy import select


async def _user(db, *, admin: bool = False) -> User:  # type: ignore[no-untyped-def]
    from app.core.security import hash_password
    u = User(username=f"u-{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@t.local", display_name="U",
             password_hash=hash_password("TestPassword2026!"), auth_provider="local", is_active=True, is_admin=admin)
    db.add(u)
    await db.commit()
    return u


def _hdr(user: User) -> dict[str, str]:
    from app.services.auth import issue_access_token
    return {"Authorization": f"Bearer {issue_access_token(user)}"}


async def _setup(db) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    sec = Section(name=f"wan-{uuid.uuid4().hex[:6]}")
    db.add(sec)
    await db.flush()
    wan = Subnet(section_id=sec.id, cidr="203.0.113.0/29", description="ISP block")
    lan = Subnet(section_id=sec.id, cidr="198.51.100.0/24")
    db.add_all([wan, lan])
    await db.flush()
    fw = Device(name=f"fw-{uuid.uuid4().hex[:6]}")
    db.add(fw)
    await db.flush()
    gw = IPAddress(subnet_id=wan.id, ip="203.0.113.1", state="active", hostname="isp-gw")
    used = IPAddress(subnet_id=wan.id, ip="203.0.113.2", state="active", device_id=fw.id,
                     last_seen_arp=datetime.now(UTC) - timedelta(minutes=5))
    vip = IPAddress(subnet_id=wan.id, ip="203.0.113.3", state="active")
    web = IPAddress(subnet_id=lan.id, ip="198.51.100.20", state="active")
    db.add_all([gw, used, vip, web])
    await db.flush()
    db.add(NATTranslation(name="https-in", type="port_forward", protocol="tcp",
                          src_ip_id=vip.id, src_port=443, dst_ip_id=web.id, dst_port=443))
    prov = Provider(name=f"isp-{uuid.uuid4().hex[:6]}")
    db.add(prov)
    await db.flush()
    c = Circuit(cid=f"CID-{uuid.uuid4().hex[:6]}", provider_id=prov.id, status="active",
                ip_address="203.0.113.2/29", gateway="203.0.113.1", device_id=fw.id)
    db.add(c)
    await db.commit()
    return {"sec": sec, "wan": wan, "lan": lan, "fw": fw, "gw": gw, "used": used, "vip": vip,
            "web": web, "prov": prov, "circuit": c}


def _code(r) -> str:  # type: ignore[no-untyped-def]
    d = r.json().get("detail")
    return d.get("code") if isinstance(d, dict) else str(d)


# ── 聯絡人角色 ──

@pytest.mark.anyio
async def test_builtin_contact_roles_exist_and_cannot_be_deleted(client, auth_headers) -> None:
    r = await client.get("/api/v1/contact-roles", headers=auth_headers)
    assert r.status_code == 200, r.text
    builtin = {x["builtin_key"]: x for x in r.json()["items"] if x["builtin_key"]}
    assert set(builtin) >= {"sales", "onsite", "noc", "billing"}
    r = await client.delete(f"/api/v1/contact-roles/{builtin['sales']['id']}", headers=auth_headers)
    assert r.status_code == 409
    assert _code(r) == "contact_role_builtin"
    r = await client.post("/api/v1/contact-roles", json={"name": f"法務-{uuid.uuid4().hex[:4]}"}, headers=auth_headers)
    assert r.status_code == 201, r.text
    r = await client.delete(f"/api/v1/contact-roles/{r.json()['id']}", headers=auth_headers)
    assert r.status_code == 204


# ── 聯絡人指派 ──

@pytest.mark.anyio
async def test_contacts_on_circuit_and_provider_show_in_detail(client, auth_headers, db_session) -> None:
    s = await _setup(db_session)
    roles = {x["builtin_key"]: x["id"] for x in
             (await client.get("/api/v1/contact-roles", headers=auth_headers)).json()["items"] if x["builtin_key"]}
    a = (await client.post("/api/v1/contacts", json={"name": "王業務", "phone": "02-0000-0001",
                                                      "email": "sales@example.com"}, headers=auth_headers)).json()
    b = (await client.post("/api/v1/contacts", json={"name": "李機房", "phone": "02-0000-0002"},
                           headers=auth_headers)).json()
    r = await client.post("/api/v1/contact-assignments", headers=auth_headers, json={
        "contact_id": a["id"], "role_id": roles["sales"], "object_type": "provider", "object_id": str(s["prov"].id)})
    assert r.status_code == 201, r.text
    r = await client.post("/api/v1/contact-assignments", headers=auth_headers, json={
        "contact_id": b["id"], "role_id": roles["onsite"], "object_type": "circuit",
        "object_id": str(s["circuit"].id)})
    assert r.status_code == 201, r.text
    asg = r.json()["id"]

    d = (await client.get(f"/api/v1/circuits/{s['circuit'].id}/detail", headers=auth_headers)).json()
    assert [(x["contact"]["name"], x["role"]["builtin_key"]) for x in d["contacts"]["circuit"]] == [("李機房", "onsite")]
    prov = d["contacts"]["provider"]
    assert prov[0]["contact"]["name"] == "王業務" and prov[0]["contact"]["email"] == "sales@example.com"

    r = await client.patch(f"/api/v1/contact-assignments/{asg}", json={"role_id": roles["noc"], "priority": 5},
                           headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["priority"] == 5
    r = await client.delete(f"/api/v1/contact-assignments/{asg}", headers=auth_headers)
    assert r.status_code == 204
    d = (await client.get(f"/api/v1/circuits/{s['circuit'].id}/detail", headers=auth_headers)).json()
    assert d["contacts"]["circuit"] == []


@pytest.mark.anyio
async def test_contact_assignment_checks_the_target(client, auth_headers, db_session) -> None:
    c = (await client.post("/api/v1/contacts", json={"name": "x"}, headers=auth_headers)).json()
    r = await client.post("/api/v1/contact-assignments", headers=auth_headers, json={
        "contact_id": c["id"], "object_type": "nonsense", "object_id": str(uuid.uuid4())})
    assert r.status_code == 422
    r = await client.post("/api/v1/contact-assignments", headers=auth_headers, json={
        "contact_id": c["id"], "object_type": "circuit", "object_id": str(uuid.uuid4())})
    assert r.status_code == 404
    assert _code(r) == "contact_target_not_found"


# ── WAN 位址 ──

@pytest.mark.anyio
async def test_link_subnet_and_single_ip_then_list_each_wan_ip(client, auth_headers, db_session) -> None:
    s = await _setup(db_session)
    cid = s["circuit"].id
    r = await client.post(f"/api/v1/circuits/{cid}/addresses", json={"target": "203.0.113.0/29"}, headers=auth_headers)
    assert r.status_code == 201, r.text
    assert r.json()["subnet_id"] == str(s["wan"].id)
    r = await client.post(f"/api/v1/circuits/{cid}/addresses", json={"target": "203.0.113.1", "role": "gateway"},
                          headers=auth_headers)
    assert r.status_code == 201, r.text
    assert r.json()["address_id"] == str(s["gw"].id)
    # 同一個再加一次
    r = await client.post(f"/api/v1/circuits/{cid}/addresses", json={"target": "203.0.113.1"}, headers=auth_headers)
    assert r.status_code == 409
    assert _code(r) == "circuit_address_exists"
    # 沒登記的 IP、不在任何子網路的 CIDR
    r = await client.post(f"/api/v1/circuits/{cid}/addresses", json={"target": "203.0.113.5"}, headers=auth_headers)
    assert r.status_code == 404
    assert _code(r) == "circuit_address_not_in_ipam"
    r = await client.post(f"/api/v1/circuits/{cid}/addresses", json={"target": "not-an-ip"}, headers=auth_headers)
    assert r.status_code == 422

    d = (await client.get(f"/api/v1/circuits/{cid}/detail", headers=auth_headers)).json()
    links = {x["role"]: x for x in d["addresses"]}
    block = next(x for x in d["addresses"] if x["subnet"])
    assert block["subnet"]["cidr"] == "203.0.113.0/29"
    assert block["usable"] == 6 and block["registered"] == 3
    ips = {x["ip"]: x for x in block["ips"]}
    assert set(ips) == {"203.0.113.1", "203.0.113.2", "203.0.113.3"}
    used = ips["203.0.113.2"]
    assert used["device"]["name"] == s["fw"].name
    assert used["last_seen_source"] == "arp" and used["last_seen"]
    assert used["signals"] >= 2                      # 有裝置＋最近看到
    assert ips["203.0.113.3"]["nat_count"] == 1
    assert links["gateway"]["ips"][0]["ip"] == "203.0.113.1"

    r = await client.delete(f"/api/v1/circuits/{cid}/addresses/{links['gateway']['id']}", headers=auth_headers)
    assert r.status_code == 204
    d = (await client.get(f"/api/v1/circuits/{cid}/detail", headers=auth_headers)).json()
    assert len(d["addresses"]) == 1


@pytest.mark.anyio
async def test_convert_the_old_text_fields_to_links(client, auth_headers, db_session) -> None:
    s = await _setup(db_session)
    cid = s["circuit"].id
    r = await client.post(f"/api/v1/circuits/{cid}/addresses/from-legacy", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    made = {(x["role"], x["subnet_id"] or x["address_id"]) for x in body["created"]}
    # 「203.0.113.2/29」→ 那一段子網路；「203.0.113.1」閘道 → 那筆 IP
    assert ("wan", str(s["wan"].id)) in made
    assert ("gateway", str(s["gw"].id)) in made
    # 再轉一次不會重複
    again = (await client.post(f"/api/v1/circuits/{cid}/addresses/from-legacy", headers=auth_headers)).json()
    assert again["created"] == []
    assert {x["reason"] for x in again["skipped"]} == {"exists"}


@pytest.mark.anyio
async def test_hidden_subnets_do_not_leak_addresses(client, db_session) -> None:
    s = await _setup(db_session)
    from app.models.advanced import CircuitAddress
    db_session.add(CircuitAddress(circuit_id=s["circuit"].id, subnet_id=s["wan"].id))
    viewer = await _user(db_session)
    # 萬用讀取裝置（過得了全域讀取門檻），但看不到那段子網路
    db_session.add(Permission(object_type="device", object_id=None, principal_type="user",
                              principal_id=viewer.id, level="read"))
    other = Subnet(section_id=s["sec"].id, cidr="192.0.2.0/28")
    db_session.add(other)
    await db_session.flush()
    db_session.add(Permission(object_type="subnet", object_id=other.id, principal_type="user",
                              principal_id=viewer.id, level="read"))
    await db_session.commit()
    r = await client.get(f"/api/v1/circuits/{s['circuit'].id}/detail", headers=_hdr(viewer))
    assert r.status_code == 200, r.text
    link = r.json()["addresses"][0]
    assert link["hidden"] is True
    assert link["subnet"] is None and link["ips"] == []
    # 電路自己的舊文字欄（固定 IP / CIDR）是電路資料，不算；WAN 位址那一段不可以有任何位址
    assert "203.0.113" not in json.dumps(r.json()["addresses"])


@pytest.mark.anyio
async def test_only_admins_write_and_writes_are_audited(client, auth_headers, db_session) -> None:
    s = await _setup(db_session)
    viewer = await _user(db_session)
    db_session.add(Permission(object_type="subnet", object_id=None, principal_type="user",
                              principal_id=viewer.id, level="read"))
    await db_session.commit()
    cid = s["circuit"].id
    r = await client.post(f"/api/v1/circuits/{cid}/addresses", json={"target": "203.0.113.1"}, headers=_hdr(viewer))
    assert r.status_code == 403
    r = await client.post(f"/api/v1/circuits/{cid}/addresses", json={"target": "203.0.113.1"}, headers=auth_headers)
    assert r.status_code == 201
    rows = (await db_session.execute(select(AuditLog).where(AuditLog.object_type == "circuit_address"))).scalars().all()
    assert any(x.action == "create" for x in rows)


# ── 租用設備 ──

@pytest.mark.anyio
async def test_leased_device_shows_on_the_circuit(client, auth_headers, db_session) -> None:
    s = await _setup(db_session)
    cpe = Device(name=f"isp-cpe-{uuid.uuid4().hex[:4]}")
    db_session.add(cpe)
    await db_session.commit()
    due = (date.today() + timedelta(days=400)).isoformat()
    r = await client.put(f"/api/v1/devices/{cpe.id}/lease", headers=auth_headers, json={
        "provider_id": str(s["prov"].id), "circuit_id": str(s["circuit"].id),
        "return_required": True, "return_due": due, "note": "合約結束要寄回"})
    assert r.status_code == 200, r.text
    assert (await client.get(f"/api/v1/devices/{cpe.id}/lease", headers=auth_headers)).json()["return_due"] == due
    d = (await client.get(f"/api/v1/circuits/{s['circuit'].id}/detail", headers=auth_headers)).json()
    assert [(x["device"]["name"], x["return_due"]) for x in d["leases"]] == [(cpe.name, due)]
    r = await client.delete(f"/api/v1/devices/{cpe.id}/lease", headers=auth_headers)
    assert r.status_code == 204
    assert (await client.get(f"/api/v1/devices/{cpe.id}/lease", headers=auth_headers)).json() is None
    rows = (await db_session.execute(select(AuditLog).where(AuditLog.object_type == "device_lease"))).scalars().all()
    assert {x.action for x in rows} >= {"update", "delete"}


# ── IP 變更評估 ──

async def _analyze(db, user, *, ip=None, new_ip=None, device=None):  # type: ignore[no-untyped-def]
    from app.services.change_impact.config import DEFAULTS
    from app.services.change_impact.engine import analyze
    cfg = {**DEFAULTS, "enabled": True}
    await db.commit()
    if ip is not None:
        return await analyze(db, user=user, scenario_type="ip_renumber", target_type="ip_address", target_id=ip.id,
                             parameters={"new_ip": new_ip}, cfg=cfg)
    return await analyze(db, user=user, scenario_type="device_decommission", target_type="device",
                         target_id=device.id, parameters={}, cfg=cfg)


@pytest.mark.anyio
async def test_change_assessment_reads_wan_links_and_leases(db_session, admin_user) -> None:
    from app.models.advanced import CircuitAddress, DeviceLease
    s = await _setup(db_session)
    db_session.add_all([CircuitAddress(circuit_id=s["circuit"].id, address_id=s["used"].id),
                        CircuitAddress(circuit_id=s["circuit"].id, subnet_id=s["wan"].id)])
    await db_session.commit()
    # 改電路上的 WAN IP：關聯指到它＝要一起改；同一條電路的舊文字欄不再重複列
    res = await _analyze(db_session, admin_user, ip=s["used"], new_ip="203.0.113.6")
    rules = [f.rule_id for f in res.findings if f.subject_type == "circuit"]
    assert "config.circuit_wan_address" in rules
    assert "config.circuit_address" not in rules
    # 只是落在電路的位址段裡：參考
    res = await _analyze(db_session, admin_user, ip=s["vip"], new_ip="203.0.113.6")
    assert "config.circuit_wan_block" in [f.rule_id for f in res.findings]
    # 租用設備除役：要歸還
    db_session.add(DeviceLease(device_id=s["fw"].id, provider_id=s["prov"].id, return_required=True,
                               return_due=date.today() + timedelta(days=30)))
    res = await _analyze(db_session, admin_user, device=s["fw"])
    lease = [f for f in res.findings if f.rule_id == "device.lease_return"]
    assert lease and lease[0].params["provider"] == s["prov"].name


# ── 站對站 VPN 併進電路（客戶許願清單第 3 項）──

@pytest.mark.anyio
async def test_vpn_detail_shows_contacts_and_the_circuit_it_runs_on(client, auth_headers, db_session) -> None:
    from app.models.advanced import CircuitAddress
    from app.models.physical import VPNTunnel
    s = await _setup(db_session)
    db_session.add(CircuitAddress(circuit_id=s["circuit"].id, subnet_id=s["wan"].id))
    peer = Device(name=f"branch-fw-{uuid.uuid4().hex[:4]}")
    db_session.add(peer)
    await db_session.flush()
    vpn = VPNTunnel(name=f"hq-to-branch-{uuid.uuid4().hex[:4]}", type="ipsec_ikev2", status="active",
                    a_device_id=s["fw"].id, b_device_id=peer.id, a_endpoint="203.0.113.2", b_endpoint="198.51.100.77")
    db_session.add(vpn)
    await db_session.commit()
    roles = {x["builtin_key"]: x["id"] for x in
             (await client.get("/api/v1/contact-roles", headers=auth_headers)).json()["items"] if x["builtin_key"]}
    who = (await client.post("/api/v1/contacts", json={"name": "分公司 IT"}, headers=auth_headers)).json()
    r = await client.post("/api/v1/contact-assignments", headers=auth_headers, json={
        "contact_id": who["id"], "role_id": roles["noc"], "object_type": "vpn_tunnel", "object_id": str(vpn.id)})
    assert r.status_code == 201, r.text

    d = (await client.get(f"/api/v1/vpn-tunnels/{vpn.id}/detail", headers=auth_headers)).json()
    assert d["tunnel"]["name"] == vpn.name
    assert d["a_device"]["name"] == s["fw"].name and d["b_device"]["name"] == peer.name
    assert [x["contact"]["name"] for x in d["contacts"]] == ["分公司 IT"]
    # A 端端點 203.0.113.2 落在電路的 WAN 位址段 → 這條 VPN 跑在那條電路上；B 端不是我們的電路
    carriers = {(x["side"], x["circuit"]["cid"]) for x in d["carriers"]}
    assert carriers == {("a", s["circuit"].cid)}
