"""DNS 比對群組的兩種排除（使用者 2026-11-05）：

1. 「排除 DHCP 範圍」：預設不勾；勾了之後，對應 IP 落在 DHCP 範圍內的紀錄不比對（DHCP 動態登記的主機名稱
   本來就會一台有、一台還沒有，或租約到期後只剩一台）。DHCP 範圍＝子網路「位址範圍」裡用途為 DHCP 的
   （手動建的，以及整合偵測到後自動建立的）。
2. 排除清單：管理員自己加要忽略比對的 FQDN（可用 `*.example.com`）、IP、CIDR 或 `起-迄` 範圍。

A / AAAA 看紀錄的 IP；PTR 從反解名稱算回 IP，並用它指向的主機名稱比對名稱排除。
"""
from __future__ import annotations

import uuid

from app.models.ip_range import IPRange
from app.models.section import Section
from app.models.subnet import Subnet
from tests.test_dns_compare_group_api import API, _srv, _zone


async def _group(client, auth_headers, servers, **kw) -> dict:
    r = await client.post(f"{API}/compare-groups", headers=auth_headers, json={
        "name": f"g-{uuid.uuid4().hex[:6]}", "server_ids": [str(s.id) for s in servers], "grace_minutes": 0, **kw})
    assert r.status_code == 201, r.text
    return r.json()


async def _diffs(client, auth_headers, gid) -> set[tuple[str, str]]:
    await client.post(f"{API}/compare-groups/{gid}/check", headers=auth_headers)
    r = await client.get(f"{API}/compare-groups/{gid}/diffs", headers=auth_headers)
    assert r.status_code == 200, r.text
    items = r.json()["items"] if isinstance(r.json(), dict) else r.json()
    return {(d["name"], d["type"]) for d in items}


async def _two_servers(db_session):
    a, b = await _srv(db_session, "ns1"), await _srv(db_session, "ns2")
    await _zone(db_session, a, "corp.example", [
        ("pc-dyn-1", "A", "198.51.100.150"), ("srv1", "A", "198.51.100.10"), ("test-box", "A", "198.51.100.20"),
        ("printer", "A", "198.51.100.30"), ("lab-a", "A", "198.51.100.41"), ("shared", "A", "198.51.100.10")])
    await _zone(db_session, b, "corp.example", [("shared", "A", "198.51.100.10")])
    await _zone(db_session, a, "100.51.198.in-addr.arpa", [("150", "PTR", "pc-dyn-1.corp.example."),
                                                           ("20", "PTR", "test-box.corp.example.")])
    await _zone(db_session, b, "100.51.198.in-addr.arpa", [])
    await _zone(db_session, b, "corp.example2", [])
    await db_session.commit()
    return a, b


async def _dhcp_range(db_session) -> None:
    sec = Section(name=f"s-{uuid.uuid4().hex[:6]}")
    db_session.add(sec)
    await db_session.flush()
    sn = Subnet(section_id=sec.id, cidr="198.51.100.0/24")
    db_session.add(sn)
    await db_session.flush()
    db_session.add(IPRange(subnet_id=sn.id, start_ip="198.51.100.100", end_ip="198.51.100.199", purpose="dhcp"))
    db_session.add(IPRange(subnet_id=sn.id, start_ip="198.51.100.20", end_ip="198.51.100.20", purpose="reserved"))
    await db_session.commit()


async def test_dhcp_ranges_are_compared_unless_the_box_is_ticked(client, auth_headers, db_session) -> None:
    a, b = await _two_servers(db_session)
    await _dhcp_range(db_session)
    g = await _group(client, auth_headers, [a, b])
    assert g["exclude_dhcp_ranges"] is False, "預設不勾"
    diffs = await _diffs(client, auth_headers, g["id"])
    assert ("pc-dyn-1.corp.example", "A") in diffs

    r = await client.patch(f"{API}/compare-groups/{g['id']}", headers=auth_headers, json={"exclude_dhcp_ranges": True})
    assert r.status_code == 200 and r.json()["exclude_dhcp_ranges"] is True
    diffs = await _diffs(client, auth_headers, g["id"])
    assert ("pc-dyn-1.corp.example", "A") not in diffs, "IP 在 DHCP 範圍內 → 不比"
    assert not any(t == "PTR" and n.startswith("150.") for n, t in diffs), "反解也看 IP"
    assert ("srv1.corp.example", "A") in diffs, "範圍外照比"
    assert ("test-box.corp.example", "A") in diffs, "用途不是 DHCP 的範圍不算"


async def test_exclusion_list_names_ips_cidrs_and_ranges(client, auth_headers, db_session) -> None:
    a, b = await _two_servers(db_session)
    g = await _group(client, auth_headers, [a, b], excluded_names=["Test-Box.corp.example.", "*.lab.corp.example"],
                     excluded_ips=["198.51.100.10", "198.51.100.40/30", "198.51.100.150-198.51.100.160"])
    assert g["excluded_names"] == ["test-box.corp.example", "*.lab.corp.example"], "正規化：小寫、去結尾點"
    diffs = await _diffs(client, auth_headers, g["id"])
    assert ("test-box.corp.example", "A") not in diffs
    assert not any(t == "PTR" and n.startswith("20.") for n, t in diffs), "PTR 指向排除的名稱 → 不比"
    assert ("srv1.corp.example", "A") not in diffs, "單一 IP"
    assert ("lab-a.corp.example", "A") not in diffs, "CIDR"
    assert ("pc-dyn-1.corp.example", "A") not in diffs, "起-迄 範圍"
    assert ("printer.corp.example", "A") in diffs, "沒排除的照比"


async def test_wildcard_matches_only_below_the_name(client, auth_headers, db_session) -> None:
    a, b = await _srv(db_session, "ns1"), await _srv(db_session, "ns2")
    await _zone(db_session, a, "corp.example", [("x.lab", "A", "198.51.100.5"), ("lab", "A", "198.51.100.6")])
    await _zone(db_session, b, "corp.example", [])
    await db_session.commit()
    g = await _group(client, auth_headers, [a, b], excluded_names=["*.lab.corp.example"])
    diffs = await _diffs(client, auth_headers, g["id"])
    assert ("x.lab.corp.example", "A") not in diffs
    assert ("lab.corp.example", "A") in diffs, "*.lab 不含 lab 本身"


async def test_bad_entries_are_rejected_and_changes_are_audited(client, auth_headers, db_session) -> None:
    a, b = await _srv(db_session, "ns1"), await _srv(db_session, "ns2")
    await db_session.commit()
    g = await _group(client, auth_headers, [a, b])
    for bad in (["not-an-ip"], ["198.51.100.20-198.51.100.10"], ["198.51.100.1-2001:db8::1"]):
        r = await client.patch(f"{API}/compare-groups/{g['id']}", headers=auth_headers, json={"excluded_ips": bad})
        assert r.status_code == 422, bad
        assert r.json()["detail"]["code"] == "dns_compare_bad_exclusion", r.text
    r = await client.patch(f"{API}/compare-groups/{g['id']}", headers=auth_headers, json={"excluded_names": ["bad name!"]})
    assert r.status_code == 422
    r = await client.patch(f"{API}/compare-groups/{g['id']}", headers=auth_headers,
                           json={"excluded_ips": ["2001:db8::/64"], "excluded_names": ["a.example"]})
    assert r.status_code == 200 and r.json()["excluded_ips"] == ["2001:db8::/64"]
    from sqlalchemy import select

    from app.models.audit import AuditLog
    diff = (await db_session.execute(select(AuditLog.diff).where(
        AuditLog.object_type == "dns_compare_group", AuditLog.action == "update")
        .order_by(AuditLog.ts.desc()).limit(1))).scalar_one()
    assert "excluded_ips" in diff and "excluded_names" in diff
