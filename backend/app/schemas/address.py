"""IPAddress schemas。"""

from __future__ import annotations

import ipaddress
import re
import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import Field, field_validator

from app.schemas.base import StrictModel

_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}([:\-]|$)){6}$|^[0-9A-Fa-f]{12}$")
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*$"
)


class IPAddressBase(StrictModel):
    subnet_id: uuid.UUID
    ip: Annotated[str, Field(min_length=2, max_length=64)]
    hostname: str | None = None
    description: Annotated[str | None, Field(max_length=1024)] = None
    state: str = "active"
    mac: str | None = None
    owner: Annotated[str | None, Field(max_length=128)] = None
    device_id: uuid.UUID | None = None
    switch_port: Annotated[str | None, Field(max_length=64)] = None
    exclude_from_ping: bool = False
    # 此 IP 略過的探測項目（扣除）；icmp 與 exclude_from_ping 在端點層雙向同步
    excluded_probes: list[str] = Field(default_factory=list)
    ptr_ignore: bool = False
    note: Annotated[str | None, Field(max_length=2048)] = None
    customer_id: uuid.UUID | None = None
    custom_fields: dict[str, Any] | None = None

    @field_validator("ip", mode="before")
    @classmethod
    def _ip_valid(cls, v: object) -> str:
        if v is None:
            raise ValueError("ip is required")
        # asyncpg 把 inet 反序列化為 ipaddress.IPv4Address/IPv6Address；轉成字串
        s = str(v).split("/")[0] if hasattr(v, "compressed") else str(v)
        try:
            ipaddress.ip_address(s)
        except ValueError as exc:
            raise ValueError(f"Invalid IP address: {s}") from exc
        return s

    @field_validator("hostname")
    @classmethod
    def _hostname_normalize(cls, v: str | None) -> str | None:
        # Base 只負責正規化（空字串視為 None）；嚴格 DNS 驗證僅在 Create 時做，
        # 因為 Read schema 也吃這支：DB 既有資料可能來自 phpIPAM 匯入 / 早期版本，
        # 不該因為一筆中文 hostname 就讓整個 list endpoint 500。
        if v is None or v == "":
            return None
        return v

    @field_validator("mac", mode="before")
    @classmethod
    def _mac_valid(cls, v: object) -> str | None:
        if v is None or v == "":
            return None
        v = str(v)   # asyncpg macaddr → str
        if not _MAC_RE.match(v):
            raise ValueError("Invalid MAC address")
        return v

    @field_validator("state")
    @classmethod
    def _state_valid(cls, v: str) -> str:
        allowed = {"active", "reserved", "offline", "dhcp", "used"}
        if v not in allowed:
            raise ValueError(f"state must be one of {sorted(allowed)}")
        return v


class IPAddressCreate(IPAddressBase):
    @field_validator("hostname")
    @classmethod
    def _hostname_strict(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if not _HOSTNAME_RE.match(v):
            raise ValueError("Invalid hostname")
        return v


class IPAddressAllocate(StrictModel):
    """配發第一個空閒 IP（不需指定 IP）。"""

    subnet_id: uuid.UUID
    hostname: str | None = None
    description: str | None = None
    mac: str | None = None
    state: str = "active"


class IPAddressUpdate(StrictModel):
    hostname: str | None = None
    description: Annotated[str | None, Field(max_length=1024)] = None
    state: str | None = None
    mac: str | None = None
    owner: Annotated[str | None, Field(max_length=128)] = None
    device_id: uuid.UUID | None = None
    switch_port: Annotated[str | None, Field(max_length=64)] = None
    exclude_from_ping: bool | None = None
    excluded_probes: list[str] | None = None
    ptr_ignore: bool | None = None
    note: Annotated[str | None, Field(max_length=2048)] = None
    customer_id: uuid.UUID | None = None
    custom_fields: dict[str, Any] | None = None
    # feature A：固定以某來源 hostname 為準（"" / null = 跟全域優先序）
    hostname_source_pin: Annotated[str | None, Field(max_length=16)] = None
    # ip / subnet_id 不允許更新；如要搬移走專用 endpoint


class IPAddressRead(IPAddressBase):
    id: uuid.UUID
    discovery_source: str
    in_dhcp_lease: bool = False   # 自動判定：目前有 DHCP 租約（由 OPNsense lease 同步維護）
    hostname_source_pin: str | None = None
    switch_port_confident: bool | None = None
    os_guess: str | None = None
    os_family: str | None = None
    os_source: str | None = None   # 有效 OS 來自哪個來源（scanner/librenms/wazuh）
    probe_last_run: dict[str, Any] | None = None
    # 此 IP 實際會被執行的探測（subnet.scan_method − excluded − ∩ agent 能力），後端算好
    effective_probes: list[str] | None = None
    last_seen_scanner: datetime | None
    last_seen_librenms: datetime | None
    last_seen_dns: datetime | None
    effective_status: str | None
    # 所屬 subnet 是否啟用掃描；前端用來判定「沒掃描的網段不該標離線紅燈」
    subnet_scan_enabled: bool | None = None
    # 後端從 oui_vendors 表 lookup 帶上來；前端不用自己查
    mac_vendor: str | None = None
    # 關聯裝置名稱（清單顯示用，前端不用再查）
    device_name: str | None = None
    created_at: datetime
    updated_at: datetime
