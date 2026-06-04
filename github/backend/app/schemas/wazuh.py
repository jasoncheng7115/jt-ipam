"""Wazuh schemas。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import Field, HttpUrl, field_validator

from app.schemas.base import StrictModel


class WazuhInstanceBase(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    api_url: HttpUrl
    api_user: Annotated[str, Field(min_length=1, max_length=128)]
    enabled: bool = True
    verify_tls: bool = True
    sync_interval_seconds: Annotated[int, Field(ge=30, le=86400)] = 300
    description: Annotated[str | None, Field(max_length=2048)] = None


class WazuhInstanceCreate(WazuhInstanceBase):
    api_password: Annotated[str, Field(min_length=4, max_length=512)]


class WazuhInstanceUpdate(StrictModel):
    name: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    api_url: HttpUrl | None = None
    api_user: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    api_password: Annotated[str | None, Field(min_length=4, max_length=512)] = None
    enabled: bool | None = None
    verify_tls: bool | None = None
    sync_interval_seconds: Annotated[int | None, Field(ge=30, le=86400)] = None
    description: Annotated[str | None, Field(max_length=2048)] = None


class WazuhInstanceRead(WazuhInstanceBase):
    id: uuid.UUID
    last_sync_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class WazuhAgentRead(StrictModel):
    id: uuid.UUID
    instance_id: uuid.UUID
    agent_id: str
    name: str | None
    ip: str | None
    register_ip: str | None
    status: str | None
    os_platform: str | None
    os_version: str | None
    agent_version: str | None
    group: str | None
    node_name: str | None
    last_keep_alive: datetime | None
    last_seen_at: datetime | None
    jt_ipam_address_id: uuid.UUID | None
    cve_critical_count: int | None
    cve_high_count: int | None
    cve_summary_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @field_validator("ip", "register_ip", mode="before")
    @classmethod
    def _coerce_ip(cls, v: object) -> str | None:
        if v is None:
            return None
        return str(v).split("/", 1)[0]


class MissingAgentRow(StrictModel):
    ip_address_id: uuid.UUID
    ip: str | None
    hostname: str | None
