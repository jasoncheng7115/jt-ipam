"""EUI-64 計算邏輯純單元測試（不啟用 FastAPI 應用）。"""

from __future__ import annotations

import ipaddress

import pytest


def _eui64(mac: str, prefix: str) -> str:
    cleaned = mac.replace(":", "").replace("-", "").lower()
    assert len(cleaned) == 12
    first = int(cleaned[0:2], 16) ^ 0x02
    iid_hex = f"{first:02x}{cleaned[2:6]}fffe{cleaned[6:12]}"
    iid_int = int(iid_hex, 16)
    net = ipaddress.IPv6Network(prefix, strict=False)
    addr = ipaddress.IPv6Address(int(net.network_address) + iid_int)
    return str(addr)


@pytest.mark.parametrize(
    "mac,prefix,expected",
    [
        # RFC 4291 範例：MAC 00:0E:0C:11:22:33 → 020E:0CFF:FE11:2233
        ("00:0E:0C:11:22:33", "2001:db8::/64", "2001:db8::20e:cff:fe11:2233"),
        ("00:11:22:33:44:55", "2001:db8::/64", "2001:db8::211:22ff:fe33:4455"),
    ],
)
def test_eui64_known_vectors(mac: str, prefix: str, expected: str):
    got = _eui64(mac, prefix)
    assert ipaddress.IPv6Address(got) == ipaddress.IPv6Address(expected)
