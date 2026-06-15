"""回歸測試：read-schema 對 asyncpg 回傳的 IPv4Address / IPv4Network（INET/CIDR 欄位）
要能強制轉成 str，否則開列表時 Pydantic str 驗證失敗 → 整頁 500。

對應 GitHub issue #4（IP 申請列表手填 IP 後 500）＋客戶回報的同類隱性 bug。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from ipaddress import IPv4Address, IPv4Network


def _now() -> datetime:
    return datetime.now(UTC)


def test_ip_request_read_coerces_ipv4address():
    from app.schemas.ip_request import IPRequestRead
    m = IPRequestRead.model_validate({
        "id": uuid.uuid4(), "status": "pending",
        "requester_user_id": uuid.uuid4(), "approver_user_id": None,
        "subnet_id": uuid.uuid4(),
        "requested_ip": IPv4Address("192.168.80.4"),  # asyncpg INET → 物件
        "hostname": None, "description": None, "purpose": "test purpose",
        "expires_at": None, "allocated_ip_id": None,
        "approved_at": None, "rejected_at": None, "rejected_reason": None,
        "fulfilled_at": None, "cancelled_at": None,
        "created_at": _now(), "updated_at": _now(),
    })
    assert m.requested_ip == "192.168.80.4"
    assert isinstance(m.requested_ip, str)


def test_ip_request_read_none_requested_ip_ok():
    from app.schemas.ip_request import IPRequestRead
    m = IPRequestRead.model_validate({
        "id": uuid.uuid4(), "status": "pending",
        "requester_user_id": uuid.uuid4(), "approver_user_id": None,
        "subnet_id": uuid.uuid4(), "requested_ip": None,
        "hostname": None, "description": None, "purpose": "p",
        "expires_at": None, "allocated_ip_id": None,
        "approved_at": None, "rejected_at": None, "rejected_reason": None,
        "fulfilled_at": None, "cancelled_at": None,
        "created_at": _now(), "updated_at": _now(),
    })
    assert m.requested_ip is None


def test_api_token_read_coerces_last_used_ip():
    from app.schemas.api_token import APITokenRead
    m = APITokenRead.model_validate({
        "id": uuid.uuid4(), "name": "t", "token_prefix": "jt_x", "scopes": [],
        "object_filters": None, "expires_at": _now(), "last_used_at": _now(),
        "last_used_ip": IPv4Address("10.0.0.9"), "revoked_at": None, "created_at": _now(),
    })
    assert m.last_used_ip == "10.0.0.9"


def test_vm_interface_read_coerces_primary_ip():
    from app.api.v1.endpoints.virt import VMInterfaceRead
    m = VMInterfaceRead.model_validate({
        "id": uuid.uuid4(), "vm_id": uuid.uuid4(), "name": "net0",
        "mac": "aa:bb:cc:dd:ee:ff", "primary_ip": IPv4Address("172.16.5.5"), "bridge": "vmbr0",
    })
    assert m.primary_ip == "172.16.5.5"


def test_arp_fdb_read_coerce():
    from app.api.v1.endpoints.librenms import ARPEntryRead, FDBEntryRead
    a = ARPEntryRead.model_validate({
        "id": uuid.uuid4(), "ip": IPv4Address("192.0.2.7"), "mac": "aa:bb:cc:00:11:22",
        "interface": "eth0", "vrf": None, "device_id": None,
        "first_seen_at": _now(), "last_seen_at": _now(),
    })
    assert a.ip == "192.0.2.7"
    f = FDBEntryRead.model_validate({
        "id": uuid.uuid4(), "mac": "aa:bb:cc:00:11:33", "vlan_id_num": 10,
        "port_name": "Gi0/1", "device_id": None,
        "first_seen_at": _now(), "last_seen_at": _now(),
    })
    assert f.mac == "aa:bb:cc:00:11:33"


def test_subnet_label_uses_str_of_network():
    # 模擬 CSV 匯入路徑：subnet.cidr 是 IPv4Network（asyncpg CIDR），str() 後才能進 VARCHAR
    cidr_obj = IPv4Network("192.72.214.0/24")
    assert str(cidr_obj) == "192.72.214.0/24"
