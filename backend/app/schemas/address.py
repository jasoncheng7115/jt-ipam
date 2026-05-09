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
    ptr_ignore: bool = False
    note: Annotated[str | None, Field(max_length=2048)] = None
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
    def _hostname_valid(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if not _HOSTNAME_RE.match(v):
            raise ValueError("Invalid hostname")
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
    pass


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
    ptr_ignore: bool | None = None
    note: Annotated[str | None, Field(max_length=2048)] = None
    custom_fields: dict[str, Any] | None = None
    # ip / subnet_id 不允許更新；如要搬移走專用 endpoint


class IPAddressRead(IPAddressBase):
    id: uuid.UUID
    discovery_source: str
    last_seen_scanner: datetime | None
    last_seen_librenms: datetime | None
    last_seen_dns: datetime | None
    effective_status: str | None
    created_at: datetime
    updated_at: datetime
