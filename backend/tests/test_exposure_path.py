"""對外路徑（客戶許願清單 2026-10-28）：網際網路 → WAN IP → 防火牆 → [手動插入] → 交換器埠 → 內部主機。

- 每一段由資料推出：NAT 的外部位址與埠、承載的電路、哪台防火牆、交換器 MAC 表上的接入埠、內部主機與埠
- 從內部主機或外部位址的頁面看都是同一條；停用的 NAT 不畫
- 手動插入的設備在防火牆與交換器之間、照順序；要選裝置或填名稱
- 看要全域讀取＋看得到子網路；加、改、刪要子網路修改權，留稽核
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest
from app.models.address import IPAddress
from app.models.advanced import Circuit, CircuitAddress, Provider
from app.models.audit import AuditLog
from app.models.device import Device
from app.models.firewall import OPNsenseFirewall
from app.models.librenms import FDBEntry
from app.models.nat import NATTranslation
from app.models.permission import Permission
from app.models.section import Section
from app.models.subnet import Subnet
from app.models.user import User
from sqlalchemy import select

MAC = "00:00:5e:00:53:20"


async def _setup(db) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    sec = Section(name=f"p-{uuid.uuid4().hex[:6]}")
    db.add(sec)
    await db.flush()
    wan = Subnet(section_id=sec.id, cidr="203.0.113.0/29")
    lan = Subnet(section_id=sec.id, cidr="198.51.100.0/24")
    db.add_all([wan, lan])
    await db.flush()
    web_dev = Device(name="web-01")
    sw = Device(name="core-sw-01")
    waf = Device(name="waf-01")
    db.add_all([web_dev, sw, waf])
    await db.flush()
    ext = IPAddress(subnet_id=wan.id, ip="203.0.113.3", state="active")
    web = IPAddress(subnet_id=lan.id, ip="198.51.100.20", state="active", mac=MAC, device_id=web_dev.id,
                    hostname="web-01.example.com")
    db.add_all([ext, web])
    fw = OPNsenseFirewall(name="fw-01", api_url="https://192.0.2.1", api_key_enc=b"x", api_key_nonce=b"y",
                          api_secret_enc=b"x", api_secret_nonce=b"y")
    prov = Provider(name=f"isp-{uuid.uuid4().hex[:4]}")
    db.add_all([fw, prov])
    await db.flush()
    c = Circuit(cid="WAN-1", provider_id=prov.id)
    db.add(c)
    await db.flush()
    db.add_all([
        CircuitAddress(circuit_id=c.id, subnet_id=wan.id),
        NATTranslation(name="https-to-web", type="port_forward", protocol="tcp", src_ip_id=ext.id, src_port=443,
                       dst_ip_id=web.id, dst_port=8443, source_origin=f"opnsense:{fw.id}"),
        NATTranslation(name="old-rule", type="port_forward", protocol="tcp", src_ip_id=ext.id, src_port=8080,
                       dst_ip_id=web.id, dst_port=80, disabled=True),
        FDBEntry(mac=MAC, port_name="Gi1/0/12", vlan_id_num=20, switch_device_id=sw.id, source="snmp"),
    ])
    await db.commit()
    return {"sec": sec, "lan": lan, "ext": ext, "web": web, "waf": waf}


async def _user(db) -> User:  # type: ignore[no-untyped-def]
    from app.core.security import hash_password
    u = User(username=f"u-{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@t.local", display_name="U",
             password_hash=hash_password("TestPassword2026!"), auth_provider="local", is_active=True)
    db.add(u)
    await db.commit()
    return u


def _hdr(user: User) -> dict[str, str]:
    from app.services.auth import issue_access_token
    return {"Authorization": f"Bearer {issue_access_token(user)}"}


@pytest.mark.anyio
async def test_path_from_internet_to_the_host_with_a_manual_hop(client, auth_headers, db_session) -> None:
    s = await _setup(db_session)
    r = await client.post(f"/api/v1/addresses/{s['web'].id}/path-hops", headers=auth_headers,
                          json={"kind": "waf", "device_id": str(s["waf"].id)})
    assert r.status_code == 201, r.text
    r = await client.post(f"/api/v1/addresses/{s['web'].id}/path-hops", headers=auth_headers,
                          json={"kind": "load_balancer", "label": "LB 叢集", "note": "VIP 10.0.0.5"})
    assert r.status_code == 201, r.text

    for ip in (s["web"], s["ext"]):          # 從內部主機或外部位址看是同一條
        d = (await client.get(f"/api/v1/addresses/{ip.id}/exposure-path", headers=auth_headers)).json()
        assert len(d["paths"]) == 1, "停用的 NAT 不畫"
        hops = d["paths"][0]["hops"]
        assert [h["kind"] for h in hops] == ["internet", "wan", "firewall", "waf", "load_balancer", "switch", "target"]
        wan, fw, waf, lb, sw, target = hops[1:]
        assert (wan["label"], wan["port"]) == ("203.0.113.3", "443")
        assert [c["cid"] for c in wan["circuits"]] == ["WAN-1"]
        assert fw["label"] == "fw-01" and fw["nat"]["name"] == "https-to-web"
        assert waf["device"]["name"] == "waf-01" and lb["label"] == "LB 叢集"
        assert (sw["label"], sw["port"], sw["vlan"]) == ("core-sw-01", "Gi1/0/12", 20)
        assert (target["label"], target["port"], target["device"]["name"]) == ("198.51.100.20", "8443", "web-01")
    rows = (await db_session.execute(select(AuditLog).where(AuditLog.object_type == "ip_path_hop"))).scalars().all()
    assert len(rows) == 2


@pytest.mark.anyio
async def test_hops_can_be_reordered_and_removed(client, auth_headers, db_session) -> None:
    s = await _setup(db_session)
    a = (await client.post(f"/api/v1/addresses/{s['web'].id}/path-hops", headers=auth_headers,
                           json={"kind": "waf", "label": "WAF"})).json()
    b = (await client.post(f"/api/v1/addresses/{s['web'].id}/path-hops", headers=auth_headers,
                           json={"kind": "proxy", "label": "Proxy"})).json()
    assert (a["position"], b["position"]) == (0, 1)
    r = await client.patch(f"/api/v1/addresses/{s['web'].id}/path-hops/{b['id']}", headers=auth_headers,
                           json={"position": 0})
    assert r.status_code == 200
    await client.patch(f"/api/v1/addresses/{s['web'].id}/path-hops/{a['id']}", headers=auth_headers,
                       json={"position": 1})
    d = (await client.get(f"/api/v1/addresses/{s['web'].id}/exposure-path", headers=auth_headers)).json()
    assert [h["label"] for h in d["manual_hops"]] == ["Proxy", "WAF"]
    r = await client.delete(f"/api/v1/addresses/{s['web'].id}/path-hops/{a['id']}", headers=auth_headers)
    assert r.status_code == 204
    r = await client.post(f"/api/v1/addresses/{s['web'].id}/path-hops", headers=auth_headers, json={"kind": "other"})
    assert r.status_code == 422


@pytest.mark.anyio
async def test_permissions(client, db_session) -> None:
    s = await _setup(db_session)
    viewer = await _user(db_session)
    db_session.add(Permission(object_type="subnet", object_id=None, principal_type="user",
                              principal_id=viewer.id, level="read"))
    await db_session.commit()
    r = await client.get(f"/api/v1/addresses/{s['web'].id}/exposure-path", headers=_hdr(viewer))
    assert r.status_code == 200 and r.json()["can_edit"] is False
    r = await client.post(f"/api/v1/addresses/{s['web'].id}/path-hops", headers=_hdr(viewer),
                          json={"kind": "waf", "label": "x"})
    assert r.status_code == 404
    scoped = await _user(db_session)          # 只被指派一個子網路：沒有全域讀取
    db_session.add(Permission(object_type="subnet", object_id=s["lan"].id, principal_type="user",
                              principal_id=scoped.id, level="write"))
    await db_session.commit()
    r = await client.get(f"/api/v1/addresses/{s['web'].id}/exposure-path", headers=_hdr(scoped))
    assert r.status_code == 403


@pytest.mark.anyio
async def test_decommissioning_a_hop_device_asks_to_update_the_path(db_session, admin_user) -> None:
    from app.models.ip_path import IPPathHop
    from app.services.change_impact.config import DEFAULTS
    from app.services.change_impact.engine import analyze
    s = await _setup(db_session)
    db_session.add(IPPathHop(address_id=s["web"].id, kind="waf", device_id=s["waf"].id))
    await db_session.commit()
    res = await analyze(db_session, user=admin_user, scenario_type="device_decommission", target_type="device",
                        target_id=s["waf"].id, parameters={}, cfg={**DEFAULTS, "enabled": True})
    hop = [f for f in res.findings if f.rule_id == "device.path_hop"]
    assert hop and hop[0].params == {"address": "198.51.100.20", "hop_kind": "waf"}
