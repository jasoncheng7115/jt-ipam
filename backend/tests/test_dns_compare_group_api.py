"""DNS 比對群組的 API：管理員才能改、每個異動留稽核、紀錄清單可以合併同一組的相同紀錄。"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models.audit import AuditLog
from app.models.dns import DNSRecord, DNSServer, DNSZone
from app.models.dns_compare_group import DNSCompareGroup
from app.services import dns_compare

API = "/api/v1/dns"


async def _srv(session, name: str, *, synced: bool = True) -> DNSServer:
    s = DNSServer(name=f"{name}-{uuid.uuid4().hex[:4]}", type="bind9",
                  last_sync_at=datetime.now(UTC) - timedelta(minutes=1) if synced else None)
    session.add(s)
    await session.flush()
    return s


async def _zone(session, server, zone, recs) -> None:
    z = DNSZone(server_id=server.id, name=zone, type="forward")
    session.add(z)
    await session.flush()
    for name, rtype, value in recs:
        session.add(DNSRecord(zone_id=z.id, name=name, type=rtype, value=value, source="from_dns_pulled",
                              name_norm=dns_compare.normalize_name(name, zone),
                              value_norm=dns_compare.normalize_value(rtype, value)))
    await session.flush()


async def _viewer_headers(session) -> dict[str, str]:
    """有全域讀取（萬用讀取權限）的非管理員：讀得到 DNS 頁，但不能改群組。
    用它測「管理員限定」才有意義 —— 沒有全域讀取的帳號在 router 那層就被擋，測不到端點本身。"""
    from app.models.permission import Permission
    from app.models.user import User
    from app.services.auth import issue_access_token
    u = User(username=f"v-{uuid.uuid4().hex[:6]}", email=f"{uuid.uuid4().hex[:6]}@example.invalid",
             password_hash="x", is_active=True, is_admin=False)
    session.add(u)
    await session.flush()
    session.add(Permission(object_type="subnet", object_id=None, principal_type="user", principal_id=u.id, level="read"))
    await session.commit()
    return {"Authorization": f"Bearer {issue_access_token(u)}"}


async def _actions(session) -> list[str]:
    return list((await session.execute(select(AuditLog.action).where(
        AuditLog.object_type == "dns_compare_group"))).scalars().all())


async def test_group_crud_is_admin_only_and_audited(client, auth_headers, db_session) -> None:
    a, b, c = await _srv(db_session, "ns1"), await _srv(db_session, "ns2"), await _srv(db_session, "ns3")
    await db_session.commit()
    nonadmin_headers = await _viewer_headers(db_session)
    assert (await client.get(f"{API}/records", headers=nonadmin_headers)).status_code == 200, "前提：讀得到 DNS"
    body = {"name": "corp-dns", "server_ids": [str(a.id), str(b.id)], "grace_minutes": 15}
    assert (await client.post(f"{API}/compare-groups", headers=nonadmin_headers, json=body)).status_code == 403
    r = await client.post(f"{API}/compare-groups", headers=auth_headers, json=body)
    assert r.status_code == 201, r.text
    gid = r.json()["id"]
    assert {m["id"] for m in r.json()["members"]} == {str(a.id), str(b.id)}
    assert r.json()["grace_minutes"] == 15 and r.json()["notify_enabled"] is True

    # 名稱重複
    assert (await client.post(f"{API}/compare-groups", headers=auth_headers, json={"name": "corp-dns"})).status_code == 409

    # 改成員：換掉 b、加入 c
    r = await client.patch(f"{API}/compare-groups/{gid}", headers=auth_headers,
                           json={"server_ids": [str(a.id), str(c.id)], "notify_enabled": False})
    assert r.status_code == 200
    assert {m["id"] for m in r.json()["members"]} == {str(a.id), str(c.id)} and r.json()["notify_enabled"] is False
    await db_session.refresh(b)
    assert b.compare_group_id is None

    lst = (await client.get(f"{API}/compare-groups", headers=auth_headers)).json()
    assert [g["name"] for g in lst] == ["corp-dns"]

    assert (await client.delete(f"{API}/compare-groups/{gid}", headers=auth_headers)).status_code == 204
    await db_session.refresh(a)
    assert a.compare_group_id is None, "刪掉群組，成員回到沒分組"
    assert await _actions(db_session) == ["create", "update", "delete"]


async def test_check_now_and_diffs_list_server_names(client, auth_headers, db_session) -> None:
    a, b = await _srv(db_session, "ns1"), await _srv(db_session, "ns2")
    g = DNSCompareGroup(name=f"g-{uuid.uuid4().hex[:6]}", grace_minutes=0, notify_enabled=False)
    db_session.add(g)
    await db_session.flush()
    a.compare_group_id = b.compare_group_id = g.id
    await _zone(db_session, a, "example.com", [("www.example.com", "A", "192.0.2.10"), ("x.example.com", "A", "192.0.2.9")])
    await _zone(db_session, b, "example.com", [("www.example.com", "A", "192.0.2.10")])
    await db_session.commit()
    r = await client.post(f"{API}/compare-groups/{g.id}/check", headers=auth_headers)
    assert r.status_code == 200 and r.json()["status"] == "mismatch"
    diffs = (await client.get(f"{API}/compare-groups/{g.id}/diffs", headers=auth_headers)).json()
    assert len(diffs) == 1
    d = diffs[0]
    assert (d["name"], d["type"], d["value"], d["confirmed"]) == ("x.example.com", "A", "192.0.2.9", True)
    assert [s["name"] for s in d["present_on"]] == [a.name] and [s["name"] for s in d["missing_on"]] == [b.name]
    assert "check" in await _actions(db_session)


async def test_server_form_sets_and_clears_the_group(client, auth_headers, db_session) -> None:
    g = DNSCompareGroup(name=f"g-{uuid.uuid4().hex[:6]}")
    db_session.add(g)
    await db_session.commit()
    r = await client.post(f"{API}/servers", headers=auth_headers,
                          json={"name": f"ns-{uuid.uuid4().hex[:4]}", "type": "bind9", "server_address": "192.0.2.53",
                                "compare_group_id": str(g.id)})
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    assert r.json()["compare_group_id"] == str(g.id)
    r = await client.patch(f"{API}/servers/{sid}", headers=auth_headers, json={"enabled": False})
    assert r.json()["compare_group_id"] == str(g.id), "沒帶這個欄位就不動它"
    r = await client.patch(f"{API}/servers/{sid}", headers=auth_headers, json={"compare_group_id": None})
    assert r.json()["compare_group_id"] is None, "明確給 null 才清掉"


async def test_records_merge_identical_records_within_a_group(client, auth_headers, db_session) -> None:
    a, b = await _srv(db_session, "ns1"), await _srv(db_session, "ns2")
    lone = await _srv(db_session, "other")
    g = DNSCompareGroup(name=f"g-{uuid.uuid4().hex[:6]}")
    db_session.add(g)
    await db_session.flush()
    a.compare_group_id = b.compare_group_id = g.id
    await _zone(db_session, a, "example.com", [("WWW.example.com", "A", "192.0.2.10"), ("only-a.example.com", "A", "192.0.2.11")])
    await _zone(db_session, b, "example.com", [("www.example.com", "A", "192.0.2.10")])
    # 不在同一組的伺服器有一樣的紀錄：不合併（不同組不保證是同一份資料）
    await _zone(db_session, lone, "example.com", [("www.example.com", "A", "192.0.2.10")])
    await db_session.commit()

    plain = (await client.get(f"{API}/records", headers=auth_headers, params={"q": "example.com"})).json()
    assert plain["total"] == 4

    merged = (await client.get(f"{API}/records", headers=auth_headers,
                               params={"q": "example.com", "merge": "true"})).json()
    assert merged["total"] == 3
    www = [r for r in merged["items"] if r["name"].lower() == "www.example.com"]
    assert len(www) == 2
    grouped = next(r for r in www if r["compare_group_name"] == g.name)
    assert sorted(grouped["servers"]) == sorted([a.name, b.name])
    alone = next(r for r in www if r["compare_group_name"] is None)
    assert alone["servers"] == [lone.name]

    counts = (await client.get(f"{API}/records/type-counts", headers=auth_headers,
                               params={"q": "example.com", "merge": "true"})).json()
    assert counts == [{"type": "A", "count": 3}]


async def test_unbound_cannot_share_a_group_with_zone_servers(client, auth_headers, db_session) -> None:
    """不能混搭的組合在存檔時就擋下，並講出原因（使用者 2026-10-10：不能混搭就要求一樣的）。"""
    a = await _srv(db_session, "ns1")
    u1 = DNSServer(name=f"fw1-{uuid.uuid4().hex[:4]}", type="unbound_opnsense")
    u2 = DNSServer(name=f"fw2-{uuid.uuid4().hex[:4]}", type="unbound_opnsense")
    db_session.add_all([u1, u2])
    await db_session.commit()
    r = await client.post(f"{API}/compare-groups", headers=auth_headers,
                          json={"name": "mixed", "server_ids": [str(a.id), str(u1.id)]})
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "dns_compare_group_mixed_kinds"
    r = await client.post(f"{API}/compare-groups", headers=auth_headers,
                          json={"name": "fw-ha", "server_ids": [str(u1.id), str(u2.id)]})
    assert r.status_code == 201, "兩台 Unbound（例如 OPNsense HA）可以同組"
    gid = r.json()["id"]
    # 從伺服器那邊加入也一樣擋
    r = await client.patch(f"{API}/servers/{a.id}", headers=auth_headers, json={"compare_group_id": gid})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "dns_compare_group_mixed_kinds"
    # 已在組裡的伺服器改成別的類型也擋
    r = await client.patch(f"{API}/servers/{u1.id}", headers=auth_headers, json={"type": "bind9"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "dns_compare_group_mixed_kinds"
    # 編輯群組成員時也擋
    r = await client.patch(f"{API}/compare-groups/{gid}", headers=auth_headers,
                           json={"server_ids": [str(u1.id), str(a.id)]})
    assert r.status_code == 422


async def test_excluded_zones_round_trip_audited_and_offered(client, auth_headers, db_session) -> None:
    """不比對的 zone：建立／修改時正規化、留稽核；讀取時附上成員有的 zone 給選單用。"""
    a, b = await _srv(db_session, "ns1"), await _srv(db_session, "ns2")
    await _zone(db_session, a, "repl.example", [("www.repl.example", "A", "198.51.100.10")])
    await _zone(db_session, b, "Own.Example", [("x.own.example", "A", "198.51.100.99")])
    await db_session.commit()
    r = await client.post(f"{API}/compare-groups", headers=auth_headers,
                          json={"name": f"g-{uuid.uuid4().hex[:6]}", "server_ids": [str(a.id), str(b.id)],
                                "excluded_zones": ["OWN.example."]})
    assert r.status_code == 201, r.text
    g = r.json()
    assert g["excluded_zones"] == ["own.example"]
    assert g["zones"] == ["own.example", "repl.example"], "成員有的 zone（正規化、排序）給選單用"
    r = await client.patch(f"{API}/compare-groups/{g['id']}", headers=auth_headers, json={"excluded_zones": []})
    assert r.status_code == 200 and r.json()["excluded_zones"] == []
    rows = (await db_session.execute(select(AuditLog.diff).where(
        AuditLog.object_type == "dns_compare_group", AuditLog.action == "update"))).scalars().all()
    assert any("excluded_zones" in (d or {}) for d in rows), "改不比對的 zone 要留稽核"
    # 太長的清單擋下
    r = await client.patch(f"{API}/compare-groups/{g['id']}", headers=auth_headers,
                           json={"excluded_zones": [f"z{i}.example" for i in range(600)]})
    assert r.status_code == 422


async def test_saving_members_or_excluded_zones_recompares_right_away(client, auth_headers, db_session) -> None:
    """改了成員或不比對的 zone，差異清單要馬上跟著變（不要等到下一次拉取才對得起來）。"""
    a, b = await _srv(db_session, "ns1"), await _srv(db_session, "ns2")
    a.last_sync_at = b.last_sync_at   # 同一時間拉取（沒有的那台資料不比較舊，寬限 0 就能確認）
    await _zone(db_session, a, "repl.example", [("www.repl.example", "A", "198.51.100.10")])
    await _zone(db_session, b, "repl.example", [("www.repl.example", "A", "198.51.100.10")])
    await _zone(db_session, b, "own.example", [("x.own.example", "A", "198.51.100.99")])
    await db_session.commit()
    r = await client.post(f"{API}/compare-groups", headers=auth_headers,
                          json={"name": f"g-{uuid.uuid4().hex[:6]}", "server_ids": [str(a.id), str(b.id)],
                                "grace_minutes": 0, "notify_enabled": False})
    g = r.json()
    assert g["last_status"] == "mismatch" and g["diff_count"] == 1, "建立時有兩台以上成員就先比一次"
    r = await client.patch(f"{API}/compare-groups/{g['id']}", headers=auth_headers, json={"excluded_zones": ["own.example"]})
    assert r.json()["last_status"] == "ok" and r.json()["diff_count"] == 0
    r = await client.patch(f"{API}/compare-groups/{g['id']}", headers=auth_headers, json={"description": "x"})
    assert r.json()["last_status"] == "ok", "只改說明不需要重新比對"
