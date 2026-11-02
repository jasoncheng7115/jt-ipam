"""IP 詳細頁的防火牆規則／所屬別名要點得進去（使用者要求，2026-09-25）。

點一列就帶到該廠牌的規則／別名頁、選好那一台、只顯示那一筆 —— 所以每一筆都要帶
「哪一台」（firewall_id）與「哪一筆」（ref）。ref 的規則：
- 有資料表的規則（OPNsense／FortiGate／Palo Alto／MikroTik）→ 那一列的 id；
- pfSense 規則存在 JSONB → 有 tracker 用 tracker，沒有就用 `#<在清單中的位置>`；
- 別名 → 名稱（同一台防火牆上別名名稱唯一）。
NAT 清單也要帶防火牆名稱，表格才能跟規則一樣「廠牌｜防火牆」兩欄分開。
"""
from __future__ import annotations

import uuid

import pytest
from app.services.fw_lookup import rules_touching_ip

IP = "198.51.100.70"


@pytest.mark.anyio
async def test_every_rule_and_alias_says_which_firewall_and_which_entry(db_session) -> None:
    from app.models.firewall import OPNsenseFirewall, OPNsenseSyncedAlias
    from app.models.firewall_rule import OPNsenseRule
    from app.models.fortigate import FortiGateFirewall, FortiGatePolicy
    from app.models.mikrotik import MikroTikRouter, MikroTikRule
    from app.models.paloalto import PaloAltoFirewall, PaloAltoPolicy
    from app.models.pfsense import PfSenseFirewall

    opn = OPNsenseFirewall(name="opn-l", api_url="https://192.0.2.1",
                           api_key_enc=b"x", api_key_nonce=b"y",
                           api_secret_enc=b"x", api_secret_nonce=b"y")
    pf = PfSenseFirewall(name="pf-l", api_url="https://192.0.2.2",
                         api_key_enc=b"x", api_key_nonce=b"y", rules=[
                             {"type": "pass", "source": "203.0.113.9", "destination": "any"},
                             {"type": "pass", "tracker": 1700000001, "source": "any",
                              "destination": IP},
                             {"type": "block", "source": IP, "destination": "any"},
                         ])
    fg = FortiGateFirewall(name="fg-l", api_url="https://192.0.2.3",
                           api_token_enc=b"x", api_token_nonce=b"y")
    pa = PaloAltoFirewall(name="pa-l", api_url="https://192.0.2.4",
                          api_key_enc=b"x", api_key_nonce=b"y")
    mt = MikroTikRouter(name="mt-l", api_url="https://192.0.2.5", api_username="u",
                        api_password_enc=b"x", api_password_nonce=b"y")
    db_session.add_all([opn, pf, fg, pa, mt])
    await db_session.flush()
    orule = OPNsenseRule(firewall_id=opn.id, legacy_uuid="l-1", enabled=True, action="pass",
                         source_net="any", destination_net=IP)
    fpol = FortiGatePolicy(firewall_id=fg.id, policyid="7", action="accept",
                           srcaddr="all", dstaddr=IP)
    ppol = PaloAltoPolicy(firewall_id=pa.id, name="to-web", action="allow",
                          source="any", destination=IP)
    mrule = MikroTikRule(router_id=mt.id, table_name="filter", action="accept", dst_address=IP)
    db_session.add_all([
        orule, fpol, ppol, mrule,
        OPNsenseSyncedAlias(firewall_id=opn.id, name="web_l", alias_type="host",
                            enabled=True, content=[IP]),
    ])
    await db_session.flush()

    out = await rules_touching_ip(db_session, IP)
    by = {(r["source_type"], r["ref"]): r for r in out["rules"]}
    assert by[("opnsense", str(orule.id))]["firewall_id"] == str(opn.id)
    assert by[("fortigate", str(fpol.id))]["firewall_id"] == str(fg.id)
    assert by[("paloalto", str(ppol.id))]["firewall_id"] == str(pa.id)
    assert by[("mikrotik", str(mrule.id))]["firewall_id"] == str(mt.id)
    # pfSense：有 tracker 用 tracker、沒有用位置（位置是在完整清單中的，不是比對後的）
    assert by[("pfsense", "1700000001")]["firewall_id"] == str(pf.id)
    assert ("pfsense", "#2") in by
    alias = next(a for a in out["aliases"] if a["name"] == "web_l")
    assert alias["firewall_id"] == str(opn.id) and alias["ref"] == "web_l"


@pytest.mark.anyio
async def test_nat_list_carries_the_firewall_name(db_session, client, auth_headers) -> None:
    from app.models.firewall import OPNsenseFirewall
    from app.models.nat import NATTranslation

    opn = OPNsenseFirewall(name=f"opn-{uuid.uuid4().hex[:6]}", api_url="https://192.0.2.1",
                           api_key_enc=b"x", api_key_nonce=b"y",
                           api_secret_enc=b"x", api_secret_nonce=b"y")
    db_session.add(opn)
    await db_session.flush()
    db_session.add(NATTranslation(name="nat-l", type="port_forward",
                                  source_origin=f"opnsense:{opn.id}"))
    await db_session.commit()
    r = await client.get("/api/v1/nat?page_size=200", headers=auth_headers)
    assert r.status_code == 200, r.text
    row = next(x for x in r.json()["items"] if x["name"] == "nat-l")
    assert row["source_kind"] == "opnsense"
    assert row["source_firewall_name"] == opn.name


@pytest.mark.anyio
async def test_nat_list_carries_both_addresses_as_text(db_session, client, auth_headers) -> None:
    """IP 頁的「相關 NAT」要寫出「外部 IP:埠 → 內部 IP:埠」（客戶 2026-10-28 許願清單）。

    以前只回 src_ip_id / dst_ip_id，表格只能列一個埠，看不出外面哪個位址轉到裡面哪一台。
    """
    from app.models.address import IPAddress
    from app.models.nat import NATTranslation
    from app.models.section import Section
    from app.models.subnet import Subnet

    sec = Section(name=f"nat-sec-{uuid.uuid4().hex[:6]}")
    db_session.add(sec)
    await db_session.flush()
    wan = Subnet(section_id=sec.id, cidr="203.0.113.0/29")
    lan = Subnet(section_id=sec.id, cidr="198.51.100.64/26")
    db_session.add_all([wan, lan])
    await db_session.flush()
    ext = IPAddress(subnet_id=wan.id, ip="203.0.113.3", state="active")
    inner = IPAddress(subnet_id=lan.id, ip=IP, state="active")
    db_session.add_all([ext, inner])
    await db_session.flush()
    db_session.add(NATTranslation(name="https-to-web", type="port_forward", protocol="tcp",
                                  src_ip_id=ext.id, src_port=443, dst_ip_id=inner.id, dst_port=8443))
    await db_session.commit()
    r = await client.get(f"/api/v1/nat?ip_id={inner.id}", headers=auth_headers)
    assert r.status_code == 200, r.text
    row = next(x for x in r.json()["items"] if x["name"] == "https-to-web")
    assert row["src_ip"] == "203.0.113.3"
    assert row["dst_ip"] == IP
