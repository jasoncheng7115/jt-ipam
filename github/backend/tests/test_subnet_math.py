"""Subnet 數學工具：host_count、CIDR 驗證。"""

from __future__ import annotations

import ipaddress

import pytest

from app.services.subnet import host_count


@pytest.mark.parametrize(
    "cidr,expected",
    [
        ("192.0.2.0/24", 254),       # /24 扣 network + broadcast
        ("10.0.0.0/30", 2),          # /30 = 4 - 2
        ("198.51.100.0/31", 2),      # /31 RFC 3021，不扣
        ("203.0.113.5/32", 1),       # /32 host route
        ("192.0.2.0/16", 65534),     # /16
    ],
)
def test_host_count_ipv4(cidr: str, expected: int):
    net = ipaddress.ip_network(cidr, strict=False)
    assert host_count(net) == expected


def test_host_count_ipv6_cap():
    """IPv6 大網段 host_count 應在合理上限內，不爆掉。"""
    net = ipaddress.ip_network("2001:db8::/48", strict=False)
    n = host_count(net)
    assert isinstance(n, int)
    assert n > 0
    assert n <= (1 << 48)


def test_host_count_ipv6_small():
    net = ipaddress.ip_network("2001:db8::/126", strict=False)
    assert host_count(net) == 4
