"""Subnet schema 驗證。"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.schemas.subnet import SubnetCreate


def _payload(**overrides):
    base = {
        "section_id": uuid.uuid4(),
        "cidr": "192.168.1.0/24",
    }
    base.update(overrides)
    return base


def test_valid_cidr_v4():
    obj = SubnetCreate(**_payload(cidr="10.0.0.0/8"))
    assert obj.cidr == "10.0.0.0/8"


def test_valid_cidr_v6():
    obj = SubnetCreate(**_payload(cidr="2001:db8::/48"))
    assert obj.cidr.startswith("2001:db8")


def test_cidr_normalised_to_network():
    """非網段位的 CIDR 應自動 truncate 為 network。"""
    obj = SubnetCreate(**_payload(cidr="192.168.1.42/24"))
    assert obj.cidr == "192.168.1.0/24"


def test_invalid_cidr():
    with pytest.raises(ValidationError):
        SubnetCreate(**_payload(cidr="not-a-cidr"))


@pytest.mark.parametrize("methods", [["icmp"], ["icmp", "snmp"], ["nmap"]])
def test_valid_scan_methods(methods: list[str]):
    obj = SubnetCreate(**_payload(scan_method=methods))
    assert obj.scan_method == methods


def test_invalid_scan_method():
    with pytest.raises(ValidationError):
        SubnetCreate(**_payload(scan_method=["telepathy"]))


def test_threshold_pct_bounds():
    SubnetCreate(**_payload(threshold_pct=0))
    SubnetCreate(**_payload(threshold_pct=100))
    with pytest.raises(ValidationError):
        SubnetCreate(**_payload(threshold_pct=-1))
    with pytest.raises(ValidationError):
        SubnetCreate(**_payload(threshold_pct=101))


def test_extra_field_forbidden():
    with pytest.raises(ValidationError):
        SubnetCreate(**_payload(secret_field=123))
