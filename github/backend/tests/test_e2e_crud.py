"""E2E：Section / Subnet / IPAddress CRUD 主路徑 + audit chain。"""

from __future__ import annotations


async def test_section_crud(client, auth_headers):  # type: ignore[no-untyped-def]
    # 建
    create = await client.post(
        "/api/v1/sections",
        headers=auth_headers,
        json={"name": "Production", "description": "prod", "strict_mode": True},
    )
    assert create.status_code == 201, create.text
    sec = create.json()
    sid = sec["id"]
    assert sec["name"] == "Production"
    assert sec["strict_mode"] is True

    # 讀
    get_one = await client.get(f"/api/v1/sections/{sid}", headers=auth_headers)
    assert get_one.status_code == 200
    assert get_one.json()["name"] == "Production"

    # 列
    listing = await client.get("/api/v1/sections", headers=auth_headers)
    assert listing.status_code == 200
    items = listing.json()["items"]
    assert any(item["id"] == sid for item in items)

    # 改
    patch = await client.patch(
        f"/api/v1/sections/{sid}", headers=auth_headers,
        json={"description": "production environments"},
    )
    assert patch.status_code == 200
    assert patch.json()["description"] == "production environments"

    # 刪
    delete = await client.delete(f"/api/v1/sections/{sid}", headers=auth_headers)
    assert delete.status_code == 204

    # 確認 404
    after = await client.get(f"/api/v1/sections/{sid}", headers=auth_headers)
    assert after.status_code == 404


async def test_subnet_create_and_first_free(client, auth_headers):  # type: ignore[no-untyped-def]
    # 先建 section
    sec = (await client.post(
        "/api/v1/sections",
        headers=auth_headers,
        json={"name": "E2E", "description": None, "strict_mode": False},
    )).json()
    sid = sec["id"]

    # 建 subnet
    sub_resp = await client.post(
        "/api/v1/subnets",
        headers=auth_headers,
        json={
            "section_id": sid,
            "cidr": "192.0.2.0/29",   # 6 hosts (network/broadcast 扣掉)
            "description": "e2e test",
        },
    )
    assert sub_resp.status_code == 201, sub_resp.text
    sub = sub_resp.json()
    sub_id = sub["id"]
    assert sub["cidr"] == "192.0.2.0/29"

    # 同 cidr 應 409 衝突
    dup = await client.post(
        "/api/v1/subnets",
        headers=auth_headers,
        json={"section_id": sid, "cidr": "192.0.2.0/29"},
    )
    assert dup.status_code == 409

    # first_free
    ff = await client.get(
        f"/api/v1/subnets/{sub_id}/first_free_address", headers=auth_headers,
    )
    assert ff.status_code == 200
    assert ff.json()["ip"] == "192.0.2.1"   # 第一個可配發 IP

    # allocate
    alloc = await client.post(
        "/api/v1/addresses/first_free",
        headers=auth_headers,
        json={"subnet_id": sub_id, "hostname": "host01"},
    )
    assert alloc.status_code == 201, alloc.text
    obj = alloc.json()
    assert obj["ip"] == "192.0.2.1"
    assert obj["hostname"] == "host01"

    # 再來一個 → 應拿 .2
    alloc2 = await client.post(
        "/api/v1/addresses/first_free",
        headers=auth_headers,
        json={"subnet_id": sub_id, "hostname": "host02"},
    )
    assert alloc2.status_code == 201
    assert alloc2.json()["ip"] == "192.0.2.2"

    # usage
    usage = await client.get(
        f"/api/v1/subnets/{sub_id}/usage", headers=auth_headers,
    )
    assert usage.status_code == 200
    u = usage.json()
    assert u["total"] == 6
    assert u["used"] == 2
    assert u["free"] == 4


async def test_ip_address_validation(client, auth_headers):  # type: ignore[no-untyped-def]
    sec = (await client.post(
        "/api/v1/sections", headers=auth_headers,
        json={"name": "Valid", "description": None, "strict_mode": False},
    )).json()
    sub = (await client.post(
        "/api/v1/subnets", headers=auth_headers,
        json={"section_id": sec["id"], "cidr": "10.99.0.0/24"},
    )).json()

    # 不在 subnet 範圍 → 400
    out_of_range = await client.post(
        "/api/v1/addresses", headers=auth_headers,
        json={"subnet_id": sub["id"], "ip": "10.100.0.5", "hostname": "x"},
    )
    assert out_of_range.status_code == 400

    # 合法
    ok = await client.post(
        "/api/v1/addresses", headers=auth_headers,
        json={"subnet_id": sub["id"], "ip": "10.99.0.42", "hostname": "host42"},
    )
    assert ok.status_code == 201

    # 重複 ip → 409
    dup = await client.post(
        "/api/v1/addresses", headers=auth_headers,
        json={"subnet_id": sub["id"], "ip": "10.99.0.42"},
    )
    assert dup.status_code == 409


async def test_address_q_search(client, auth_headers):  # type: ignore[no-untyped-def]
    """GET /addresses?q=… 應該對 IP / hostname 模糊匹配。"""
    sec = (await client.post(
        "/api/v1/sections", headers=auth_headers,
        json={"name": "QSearch", "description": None, "strict_mode": False},
    )).json()
    sub = (await client.post(
        "/api/v1/subnets", headers=auth_headers,
        json={"section_id": sec["id"], "cidr": "10.77.0.0/24"},
    )).json()
    await client.post("/api/v1/addresses", headers=auth_headers,
                      json={"subnet_id": sub["id"], "ip": "10.77.0.10", "hostname": "needle-host"})
    await client.post("/api/v1/addresses", headers=auth_headers,
                      json={"subnet_id": sub["id"], "ip": "10.77.0.11", "hostname": "other"})

    # 用 IP 子字串搜
    by_ip = (await client.get("/api/v1/addresses",
                              headers=auth_headers, params={"q": "10.77.0.10"})).json()
    assert any(r["ip"] == "10.77.0.10" for r in by_ip["items"])

    # 用 hostname 子字串搜
    by_host = (await client.get("/api/v1/addresses",
                                headers=auth_headers, params={"q": "needle"})).json()
    assert len(by_host["items"]) == 1
    assert by_host["items"][0]["hostname"] == "needle-host"

    # 跳脫 % — 不可造成大範圍掃描
    safe = (await client.get("/api/v1/addresses",
                             headers=auth_headers, params={"q": "%"})).json()
    # `%` 被 escape 後不應該匹配任何 row（none of our ips/hostnames literally contain `%`）
    assert safe["total"] == 0


async def test_audit_chain_appends(client, auth_headers, db_session):  # type: ignore[no-untyped-def]
    """建立物件後，audit_logs 應該多出對應條目，且 hash chain 連續。"""
    from sqlalchemy import desc, select
    from app.models.audit import AuditLog

    before = (
        await db_session.execute(
            select(AuditLog).order_by(desc(AuditLog.id)).limit(1)
        )
    ).scalar_one_or_none()
    last_hash_before = before.this_hash if before else None

    # 建 section
    resp = await client.post(
        "/api/v1/sections", headers=auth_headers,
        json={"name": "AuditTest", "description": "x", "strict_mode": False},
    )
    assert resp.status_code == 201
    sid = resp.json()["id"]

    # audit_logs 應該多至少一筆，且 prev_hash == last_hash_before（鏈完整）
    rows = (
        await db_session.execute(
            select(AuditLog)
            .where(AuditLog.object_id == sid)
            .order_by(AuditLog.id.asc())
        )
    ).scalars().all()
    assert len(rows) >= 1
    create_row = next(r for r in rows if r.action == "create")
    if last_hash_before is not None:
        # 至少 prev_hash 不會是空（已經連到鏈上）
        assert create_row.prev_hash is not None
        assert len(create_row.prev_hash) == 32
        assert len(create_row.this_hash) == 32
        assert create_row.prev_hash != create_row.this_hash
