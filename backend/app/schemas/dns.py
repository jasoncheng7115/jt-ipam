"""DNS schemas。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, HttpUrl

from app.schemas.base import StrictModel

DNSServerType = Literal["powerdns", "bind9", "unbound_opnsense", "windows_dns", "univention_ucs"]


class DNSServerCreate(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    type: DNSServerType
    api_url: HttpUrl | None = None
    server_address: Annotated[str | None, Field(max_length=255)] = None
    extra_config: Annotated[str | None, Field(max_length=4096)] = None  # JSON string
    enabled: bool = True
    sync_interval_seconds: Annotated[int, Field(ge=60, le=86400)] = 300
    scope_subnet_ids: list[str] | None = None
    # 機密欄位（不寫進 dns_servers，會分流到 encrypted_secrets）
    api_key: Annotated[str | None, Field(min_length=1, max_length=512)] = None
    api_secret: Annotated[str | None, Field(min_length=1, max_length=512)] = None
    tsig_key: Annotated[str | None, Field(min_length=1, max_length=512)] = None
    password: Annotated[str | None, Field(min_length=1, max_length=512)] = None


class DNSServerUpdate(StrictModel):
    name: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    type: DNSServerType | None = None
    api_url: HttpUrl | None = None
    server_address: Annotated[str | None, Field(max_length=255)] = None
    extra_config: Annotated[str | None, Field(max_length=4096)] = None
    enabled: bool | None = None
    sync_interval_seconds: Annotated[int | None, Field(ge=60, le=86400)] = None
    scope_subnet_ids: list[str] | None = None
    api_key: Annotated[str | None, Field(min_length=1, max_length=512)] = None
    api_secret: Annotated[str | None, Field(min_length=1, max_length=512)] = None
    tsig_key: Annotated[str | None, Field(min_length=1, max_length=512)] = None
    password: Annotated[str | None, Field(min_length=1, max_length=512)] = None


class DNSServerRead(StrictModel):
    id: uuid.UUID
    name: str
    type: str
    api_url: str | None
    server_address: str | None
    extra_config: str | None  # JSON：username / verify_tls 等非機密設定（回填編輯用）
    enabled: bool
    sync_interval_seconds: int
    scope_subnet_ids: list[str] | None = None
    last_sync_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class DNSZoneRead(StrictModel):
    id: uuid.UUID
    server_id: uuid.UUID
    name: str
    type: str
    managed: bool
    associated_subnet_ids: list[uuid.UUID]
    last_sync_at: datetime | None


class DNSRecordRead(StrictModel):
    id: uuid.UUID
    zone_id: uuid.UUID
    name: str
    type: str
    value: str
    ttl: int
    source: str
    consistency_state: str
    ipam_address_id: uuid.UUID | None
    last_seen_at: datetime | None
    # 依「IP 值」實查 ip_addresses 是否有對應位址（A/AAAA）；非 sync 的 hostname 比對結果
    matched_ip_id: uuid.UUID | None = None
    # 此記錄來自哪台整合 DNS 伺服器（來源欄顯示用）
    server_id: uuid.UUID | None = None
    server_name: str | None = None


class DNSRecordTypeCount(StrictModel):
    type: str
    count: int


class ConsistencyReportItem(StrictModel):
    state: str
    count: int


class InconsistentRecord(StrictModel):
    zone_id: uuid.UUID
    zone_name: str
    server_name: str
    name: str
    type: str
    value: str
    state: str
