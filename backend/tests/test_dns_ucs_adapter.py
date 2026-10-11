"""UCS（UDM REST）adapter：反解 zone 的 PTR 以前從來沒讀進來（2026-10-10 比對群組實測發現）。

UDM 的物件形狀（正式站台 UCS 實測）：
- reverse_zone：dn 是 `zoneName=2.0.192.in-addr.arpa,cn=dns,...`，properties.subnet 是 `192.0.2`
- ptr_record：properties.address 是 zone 內的相對名稱（`145`），properties.ptr_record 是 FQDN 清單（帶結尾點）
"""
from __future__ import annotations

from typing import Any

from app.services.dns.ucs import UniventionUCSAdapter

FWD_DN = "zoneName=example.com,cn=dns,dc=example,dc=com"
REV_DN = "zoneName=2.0.192.in-addr.arpa,cn=dns,dc=example,dc=com"
REV6_DN = "zoneName=8.b.d.0.1.0.0.2.ip6.arpa,cn=dns,dc=example,dc=com"


def _obj(dn: str, **props: Any) -> dict[str, Any]:
    return {"dn": dn, "properties": props}


def _adapter(monkeypatch) -> UniventionUCSAdapter:
    ad = UniventionUCSAdapter(api_url="https://ucs.example.com", username="u", password="p")
    data: dict[tuple[str, str | None], list[dict[str, Any]]] = {
        ("/dns/forward_zone/", None): [_obj(FWD_DN, zone="example.com")],
        ("/dns/reverse_zone/", None): [_obj(REV_DN, subnet="192.0.2"), _obj(REV6_DN, subnet="2001:0db8")],
        ("/dns/host_record/", FWD_DN): [_obj("relativeDomainName=dc01," + FWD_DN, name="dc01", a=["192.0.2.5"])],
        ("/dns/alias/", FWD_DN): [],
        ("/dns/ptr_record/", REV_DN): [
            _obj("relativeDomainName=5," + REV_DN, address="5", ptr_record=["dc01.example.com."]),
            _obj("relativeDomainName=6," + REV_DN, address="6", ptr_record=["a.example.com.", "b.example.com."]),
        ],
        ("/dns/ptr_record/", REV6_DN): [],
    }

    async def fake_get(path: str, params: dict | None = None) -> dict:  # type: ignore[type-arg]
        key = (path, (params or {}).get("superordinate"))
        return {"_embedded": {"udm:object": data.get(key, [])}}
    monkeypatch.setattr(ad, "_get", fake_get)
    return ad


async def test_reverse_zone_names_come_from_the_dn(monkeypatch) -> None:
    zones = {(z.name, z.kind) for z in await _adapter(monkeypatch).list_zones()}
    assert ("2.0.192.in-addr.arpa", "reverse") in zones
    assert ("8.b.d.0.1.0.0.2.ip6.arpa", "reverse") in zones, "IPv6 反解 zone 不可以被當成 IPv4 倒過來"
    assert ("example.com", "forward") in zones


async def test_ptr_records_are_read_from_reverse_zones(monkeypatch) -> None:
    recs = await _adapter(monkeypatch).list_records("2.0.192.in-addr.arpa")
    assert sorted((r.name, r.type, r.value) for r in recs) == [
        ("5.2.0.192.in-addr.arpa", "PTR", "dc01.example.com"),
        ("6.2.0.192.in-addr.arpa", "PTR", "a.example.com"),
        ("6.2.0.192.in-addr.arpa", "PTR", "b.example.com"),
    ]


async def test_forward_zone_still_reads_hosts(monkeypatch) -> None:
    recs = await _adapter(monkeypatch).list_records("example.com")
    assert [(r.name, r.type, r.value) for r in recs] == [("dc01.example.com", "A", "192.0.2.5")]
