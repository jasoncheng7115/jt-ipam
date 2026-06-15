"""Firewall schemas（OPNsense）。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import Field, HttpUrl

from app.schemas.base import StrictModel


class OPNsenseFirewallBase(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    api_url: HttpUrl
    enabled: bool = True
    verify_tls: bool = True
    sync_interval_seconds: Annotated[int, Field(ge=30, le=86400)] = 300
    sync_dhcp: bool = False
    sync_arp: bool = False
    sync_openvpn: bool = False
    sync_rules: bool = False
    sync_nat: bool = False
    sync_aliases: bool = True
    description: Annotated[str | None, Field(max_length=2048)] = None
    # 關聯範圍（NAT 對應）；留空 = 沿用全域 IP 字串比對
    scope_location_id: uuid.UUID | None = None
    scope_customer_id: uuid.UUID | None = None
    scope_subnet_ids: list[uuid.UUID] | None = None
    iface_subnet_map: dict[str, uuid.UUID] | None = None


class OPNsenseFirewallCreate(OPNsenseFirewallBase):
    api_key: Annotated[str, Field(min_length=4, max_length=512)]
    api_secret: Annotated[str, Field(min_length=4, max_length=512)]


class OPNsenseFirewallUpdate(StrictModel):
    name: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    api_url: HttpUrl | None = None
    api_key: Annotated[str | None, Field(min_length=4, max_length=512)] = None
    api_secret: Annotated[str | None, Field(min_length=4, max_length=512)] = None
    enabled: bool | None = None
    verify_tls: bool | None = None
    sync_interval_seconds: Annotated[int | None, Field(ge=30, le=86400)] = None
    sync_dhcp: bool | None = None
    sync_arp: bool | None = None
    sync_openvpn: bool | None = None
    sync_rules: bool | None = None
    sync_nat: bool | None = None
    sync_aliases: bool | None = None
    description: Annotated[str | None, Field(max_length=2048)] = None
    scope_location_id: uuid.UUID | None = None
    scope_customer_id: uuid.UUID | None = None
    scope_subnet_ids: list[uuid.UUID] | None = None
    iface_subnet_map: dict[str, uuid.UUID] | None = None


class OPNsenseFirewallRead(OPNsenseFirewallBase):
    id: uuid.UUID
    last_sync_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class OPNsenseAliasMappingBase(StrictModel):
    firewall_id: uuid.UUID
    alias_name: Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")]
    alias_type: Annotated[str, Field(min_length=1, max_length=32)] = "host"
    selector: dict[str, Any]
    direction: Annotated[str, Field(pattern=r"^(push|pull|both)$")] = "push"


class OPNsenseAliasMappingCreate(OPNsenseAliasMappingBase):
    pass


class OPNsenseAliasMappingUpdate(StrictModel):
    alias_name: Annotated[str | None, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")] = None
    alias_type: Annotated[str | None, Field(min_length=1, max_length=32)] = None
    selector: dict[str, Any] | None = None
    direction: Annotated[str | None, Field(pattern=r"^(push|pull|both)$")] = None


class OPNsenseAliasMappingRead(OPNsenseAliasMappingBase):
    id: uuid.UUID
    last_alias_uuid: str | None
    last_synced_count: int | None
    last_sync_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime
