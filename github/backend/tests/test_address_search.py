"""位址搜尋：exact=true 時 IP 須完全相等（避免 192.168.1.1 撈到 192.168.1.1xx）。"""

from __future__ import annotations


async def _setup(client, auth_headers) -> str:
    sec = await client.post("/api/v1/sections", headers=auth_headers, json={"name": "search-sec"})
    sid = sec.json()["id"]
    sub = await client.post("/api/v1/subnets", headers=auth_headers,
                            json={"section_id": sid, "cidr": "192.168.77.0/24"})
    subnet_id = sub.json()["id"]
    for ip in ("192.168.77.1", "192.168.77.11", "192.168.77.111"):
        r = await client.post("/api/v1/addresses", headers=auth_headers,
                              json={"subnet_id": subnet_id, "ip": ip})
        assert r.status_code in (200, 201), r.text
    return subnet_id


async def test_fuzzy_matches_prefix(client, auth_headers):
    await _setup(client, auth_headers)
    r = await client.get("/api/v1/addresses", headers=auth_headers,
                         params={"q": "192.168.77.1", "exact": "false"})
    ips = {row["ip"].split("/")[0] for row in r.json()["items"]}
    # 模糊 → .1 / .11 / .111 都中
    assert {"192.168.77.1", "192.168.77.11", "192.168.77.111"} <= ips


async def test_exact_matches_only_one(client, auth_headers):
    await _setup(client, auth_headers)
    r = await client.get("/api/v1/addresses", headers=auth_headers,
                         params={"q": "192.168.77.1", "exact": "true"})
    ips = {row["ip"].split("/")[0] for row in r.json()["items"]}
    assert ips == {"192.168.77.1"}
