"""AI 工具要回傳畫面上看得到的欄位（使用者 2026-10-15）。

起因：問「電路的申請日與到期日要去哪裡查」，AI 回答「電路沒有這兩個欄位」—— 編輯畫面明明有「開通日」
「合約到期」，是 `list_circuits` 沒回傳 `install_date`／`contract_end_date`，模型只看得到工具給的欄位，
就斷言系統沒有。工具少給一個欄位，AI 就會很有把握地說錯。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.mcp.tools import TOOLS

pytestmark = pytest.mark.anyio


async def test_circuits_return_dates_dns_and_tenant(db_session, admin_user) -> None:
    from app.models.advanced import Circuit, Provider, Tenant
    p = Provider(name=f"isp-{uuid.uuid4().hex[:6]}")
    tn = Tenant(name=f"t-{uuid.uuid4().hex[:6]}", slug=f"t-{uuid.uuid4().hex[:6]}")
    db_session.add_all([p, tn])
    await db_session.flush()
    end = (datetime.now(UTC) + timedelta(days=30)).replace(hour=0, minute=0, second=0, microsecond=0)
    db_session.add(Circuit(cid="HN-1", provider_id=p.id, tenant_id=tn.id, dns_servers="192.0.2.53",
                           install_date=datetime(2024, 3, 1, tzinfo=UTC), contract_end_date=end))
    await db_session.flush()
    out = await TOOLS["list_circuits"]["fn"](db_session, user=admin_user)
    row = next(c for c in out["circuits"] if c["cid"] == "HN-1")
    assert row["install_date"] == "2024-03-01"
    assert row["contract_end_date"] == end.date().isoformat()
    assert row["contract_days_left"] in (29, 30)
    assert row["dns_servers"] == "192.0.2.53"
    assert row["tenant"] == tn.name
    desc = TOOLS["list_circuits"]["description"]
    assert "install_date" in desc and "contract_end_date" in desc, "說明要讓模型知道有日期欄位"


async def test_circuits_return_wan_addresses_contacts_and_leased_devices(db_session, admin_user) -> None:
    """電路詳細資料的三樣也要給 AI（客戶許願清單 2026-10-28）：問「這條線的 WAN IP / 業務是誰 / 租的設備」不能說沒有。"""
    from app.models.address import IPAddress
    from app.models.advanced import (
        Circuit,
        CircuitAddress,
        Contact,
        ContactAssignment,
        ContactRole,
        DeviceLease,
        Provider,
    )
    from app.models.device import Device
    from app.models.section import Section
    from app.models.subnet import Subnet
    p = Provider(name=f"isp-{uuid.uuid4().hex[:6]}")
    sec = Section(name=f"s-{uuid.uuid4().hex[:6]}")
    db_session.add_all([p, sec])
    await db_session.flush()
    sub = Subnet(section_id=sec.id, cidr="203.0.113.0/29")
    db_session.add(sub)
    await db_session.flush()
    gw = IPAddress(subnet_id=sub.id, ip="203.0.113.1", state="active")
    cpe = Device(name=f"cpe-{uuid.uuid4().hex[:4]}")
    role = ContactRole(name=f"r-{uuid.uuid4().hex[:4]}", builtin_key=None)
    who = Contact(name="王業務", phone="02-0000-0001")
    c = Circuit(cid="WAN-7", provider_id=p.id)
    db_session.add_all([gw, cpe, role, who, c])
    await db_session.flush()
    db_session.add_all([
        CircuitAddress(circuit_id=c.id, subnet_id=sub.id),
        CircuitAddress(circuit_id=c.id, address_id=gw.id, role="gateway"),
        ContactAssignment(contact_id=who.id, role_id=role.id, object_type="provider", object_id=p.id),
        DeviceLease(device_id=cpe.id, provider_id=p.id, circuit_id=c.id),
    ])
    await db_session.flush()
    out = await TOOLS["list_circuits"]["fn"](db_session, user=admin_user)
    row = next(x for x in out["circuits"] if x["cid"] == "WAN-7")
    assert {(a["role"], a["address"]) for a in row["wan_addresses"]} == {("wan", "203.0.113.0/29"),
                                                                         ("gateway", "203.0.113.1")}
    assert row["contacts"][0]["name"] == "王業務" and row["contacts"][0]["of"] == "provider"
    assert row["leased_devices"] == [cpe.name]
    desc = TOOLS["list_circuits"]["description"]
    assert "wan_addresses" in desc and "contacts" in desc and "leased_devices" in desc


async def test_vpn_tunnels_return_contacts_and_carrying_circuits(db_session, admin_user) -> None:
    from app.models.advanced import Circuit, CircuitAddress, Contact, ContactAssignment, Provider
    from app.models.physical import VPNTunnel
    from app.models.section import Section
    from app.models.subnet import Subnet
    p = Provider(name=f"isp-{uuid.uuid4().hex[:6]}")
    sec = Section(name=f"s-{uuid.uuid4().hex[:6]}")
    db_session.add_all([p, sec])
    await db_session.flush()
    sub = Subnet(section_id=sec.id, cidr="203.0.113.8/29")
    c = Circuit(cid="WAN-VPN", provider_id=p.id)
    t = VPNTunnel(name="hq-branch", type="ipsec_ikev2", a_endpoint="203.0.113.10", b_endpoint="198.51.100.9")
    who = Contact(name="分公司 IT")
    db_session.add_all([sub, c, t, who])
    await db_session.flush()
    db_session.add_all([CircuitAddress(circuit_id=c.id, subnet_id=sub.id),
                        ContactAssignment(contact_id=who.id, object_type="vpn_tunnel", object_id=t.id)])
    await db_session.flush()
    out = await TOOLS["list_vpn_tunnels"]["fn"](db_session, user=admin_user)
    row = next(x for x in out["vpn_tunnels"] if x["name"] == "hq-branch")
    assert row["contacts"][0]["name"] == "分公司 IT"
    assert row["carried_by_circuits"] == [{"side": "a", "circuit": "WAN-VPN"}]
