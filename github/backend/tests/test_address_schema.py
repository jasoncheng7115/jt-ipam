"""IPAddress schema 驗證（hostname / MAC / IP）。"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.schemas.address import IPAddressCreate


def _payload(**overrides):
    base = {
        "subnet_id": uuid.uuid4(),
        "ip": "192.168.1.10",
    }
    base.update(overrides)
    return base


def test_valid_ipv4():
    obj = IPAddressCreate(**_payload())
    assert obj.ip == "192.168.1.10"


def test_valid_ipv6():
    obj = IPAddressCreate(**_payload(ip="2001:db8::1"))
    assert "2001:db8" in obj.ip


def test_invalid_ip():
    with pytest.raises(ValidationError):
        IPAddressCreate(**_payload(ip="999.999.999.999"))


@pytest.mark.parametrize(
    "mac",
    [
        "00:11:22:33:44:55",
        "00-11-22-33-44-55",
        "001122334455",
    ],
)
def test_valid_mac(mac: str):
    obj = IPAddressCreate(**_payload(mac=mac))
    assert obj.mac == mac


@pytest.mark.parametrize(
    "mac",
    [
        "ZZ:11:22:33:44:55",
        "00:11:22:33:44",        # 太短
        "00:11:22:33:44:55:66",  # 太長
    ],
)
def test_invalid_mac(mac: str):
    with pytest.raises(ValidationError):
        IPAddressCreate(**_payload(mac=mac))


@pytest.mark.parametrize(
    "hostname",
    [
        "host01",
        "host-01.example.com",
        "internal.example.com",
    ],
)
def test_valid_hostname(hostname: str):
    obj = IPAddressCreate(**_payload(hostname=hostname))
    assert obj.hostname == hostname


@pytest.mark.parametrize(
    "hostname",
    [
        "-leading-hyphen",
        "trailing-hyphen-",
        "with space",
        "double..dot",
        "a" * 256,  # 太長
    ],
)
def test_invalid_hostname(hostname: str):
    with pytest.raises(ValidationError):
        IPAddressCreate(**_payload(hostname=hostname))


def test_extra_field_forbidden():
    """A03 — 不允許未知欄位。"""
    with pytest.raises(ValidationError):
        IPAddressCreate(**_payload(unknown_field="boom"))
