"""Subnet schemas。"""

from __future__ import annotations

import ipaddress
import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import Field, field_validator

from app.schemas.base import StrictModel


def _validate_cidr(value: str) -> str:
    """確保是合法 CIDR；且網段位（host bits = 0）。"""
    try:
        net = ipaddress.ip_network(value, strict=False)
    except ValueError as exc:
        raise ValueError(f"Invalid CIDR: {value}") from exc
    return str(net)


class SubnetBase(StrictModel):
    section_id: uuid.UUID
    cidr: Annotated[str, Field(min_length=3, max_length=64)]
    description: Annotated[str | None, Field(max_length=1024)] = None
    vlan_id: uuid.UUID | None = None
    vrf_id: uuid.UUID | None = None
    is_pool: bool = False
    is_full: bool = False
    scan_enabled: bool = False
    scan_method: list[str] = Field(default_factory=lambda: ["icmp"])
    threshold_pct: Annotated[int | None, Field(ge=0, le=100)] = None
    auto_dns: bool = False
    scan_agent_id: uuid.UUID | None = None
    customer_id: uuid.UUID | None = None
    gateway: Annotated[str | None, Field(max_length=64)] = None
    dns_servers: Annotated[str | None, Field(max_length=512)] = None
    location_id: uuid.UUID | None = None
    custom_fields: dict[str, Any] | None = None

    @field_validator("gateway", mode="before")
    @classmethod
    def _gateway_norm(cls, v: object) -> object:
        if v is None or v == "":
            return None
        s = str(v).strip()
        try:
            ipaddress.ip_address(s)
        except ValueError as exc:
            raise ValueError("gateway must be a valid IP") from exc
        return s

    @field_validator("cidr", mode="before")
    @classmethod
    def _cidr_normalised(cls, v: object) -> str:
        # 接受 str 或 ipaddress.IPv4Network/IPv6Network（asyncpg 反序列化結果）
        if v is None:
            raise ValueError("cidr is required")
        return _validate_cidr(str(v))

    @field_validator("scan_method")
    @classmethod
    def _scan_methods_valid(cls, v: list[str]) -> list[str]:
        allowed = {"icmp", "snmp", "arp", "nmap", "mdns", "netbios"}
        bad = [m for m in v if m not in allowed]
        if bad:
            raise ValueError(f"invalid scan_method: {bad}; allowed={sorted(allowed)}")
        return v


class SubnetCreate(SubnetBase):
    # 明確允許與現有網段重疊（例如同 CIDR 但單位/地點不同）；僅建立時用，不存欄位
    allow_overlap: bool = False


class SubnetUpdate(StrictModel):
    section_id: uuid.UUID | None = None
    description: Annotated[str | None, Field(max_length=1024)] = None
    vlan_id: uuid.UUID | None = None
    vrf_id: uuid.UUID | None = None
    is_pool: bool | None = None
    is_full: bool | None = None
    scan_enabled: bool | None = None
    scan_method: list[str] | None = None
    threshold_pct: Annotated[int | None, Field(ge=0, le=100)] = None
    auto_dns: bool | None = None
    scan_agent_id: uuid.UUID | None = None
    customer_id: uuid.UUID | None = None
    gateway: Annotated[str | None, Field(max_length=64)] = None
    dns_servers: Annotated[str | None, Field(max_length=512)] = None
    location_id: uuid.UUID | None = None
    custom_fields: dict[str, Any] | None = None
    # 注意：cidr 不允許更新（會破壞已配發 IP）；如要 resize 走專用 endpoint


class SubnetRead(SubnetBase):
    id: uuid.UUID
    master_subnet_id: uuid.UUID | None
    customer_name: str | None = None  # 方便前端不必另查 customers（非管理員也能顯示單位名）
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class SubnetUsage(StrictModel):
    subnet_id: uuid.UUID
    cidr: str
    total: int           # 可用主機數（不含 network/broadcast，IPv6 除外）
    used: int            # 已建檔的 IP 數
    free: int
    used_pct: float


class FirstFreeAddress(StrictModel):
    subnet_id: uuid.UUID
    ip: str | None       # None = 已滿
    cidr: str
