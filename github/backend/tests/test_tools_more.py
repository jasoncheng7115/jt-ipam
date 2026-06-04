"""新增的 IP/CIDR/FQDN/DNS 工具端點。"""

from __future__ import annotations


async def test_ip_in_cidr(client, auth_headers):
    r = await client.get("/api/v1/tools/ip-in-cidr",
                         params={"ip": "192.168.1.50", "cidr": "192.168.1.0/24"},
                         headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["contains"] is True
    r2 = await client.get("/api/v1/tools/ip-in-cidr",
                          params={"ip": "10.0.0.1", "cidr": "192.168.1.0/24"},
                          headers=auth_headers)
    assert r2.json()["contains"] is False


async def test_cidr_relation(client, auth_headers):
    r = await client.get("/api/v1/tools/cidr-relation",
                         params={"a": "10.0.0.0/8", "b": "10.1.0.0/16"}, headers=auth_headers)
    assert r.json()["relation"] == "a_contains_b"


async def test_range_to_cidr(client, auth_headers):
    r = await client.get("/api/v1/tools/range-to-cidr",
                         params={"start": "192.168.1.0", "end": "192.168.1.255"},
                         headers=auth_headers)
    assert r.json()["cidrs"] == ["192.168.1.0/24"]


async def test_aggregate(client, auth_headers):
    r = await client.get("/api/v1/tools/aggregate",
                         params={"cidrs": "192.168.0.0/24, 192.168.1.0/24"}, headers=auth_headers)
    assert r.json()["aggregated"] == ["192.168.0.0/23"]


async def test_netmask_convert(client, auth_headers):
    r = await client.get("/api/v1/tools/netmask", params={"value": "255.255.255.0"}, headers=auth_headers)
    assert r.json()["prefixlen"] == 24
    r2 = await client.get("/api/v1/tools/netmask", params={"value": "/24"}, headers=auth_headers)
    assert r2.json()["netmask"] == "255.255.255.0"


async def test_mac_format(client, auth_headers):
    r = await client.get("/api/v1/tools/mac-format", params={"mac": "00:0e:0c:11:22:33"}, headers=auth_headers)
    body = r.json()
    assert body["bare"] == "000e0c112233"
    assert body["cisco_dot"] == "000e.0c11.2233"
    assert body["oui"] == "000e0c"


async def test_fqdn_parse(client, auth_headers):
    r = await client.get("/api/v1/tools/fqdn", params={"name": "sw1.dc.example.com"}, headers=auth_headers)
    body = r.json()
    assert body["valid"] is True and body["is_fqdn"] is True
    assert body["host"] == "sw1"
    assert body["tld"] == "com"
    bad = await client.get("/api/v1/tools/fqdn", params={"name": "-bad-.x"}, headers=auth_headers)
    assert bad.json()["valid"] is False
