"""Windows DHCP Server schemas（Beta）。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import Field

from app.schemas.base import StrictModel


class WindowsDhcpBase(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    host: Annotated[str, Field(min_length=1, max_length=255)]
    username: Annotated[str, Field(min_length=1, max_length=255)]
    port: Annotated[int, Field(ge=1, le=65535)] = 5986
    use_ssl: bool = True
    verify_tls: bool = True
    enabled: bool = True
    sync_scopes: bool = True
    sync_leases: bool = True
    sync_interval_seconds: Annotated[int, Field(ge=30, le=86400)] = 300
    description: Annotated[str | None, Field(max_length=2048)] = None
    scope_subnet_ids: list[uuid.UUID] | None = None


class WindowsDhcpCreate(WindowsDhcpBase):
    password: Annotated[str, Field(min_length=1, max_length=512)]


class WindowsDhcpUpdate(StrictModel):
    name: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    host: Annotated[str | None, Field(min_length=1, max_length=255)] = None
    username: Annotated[str | None, Field(min_length=1, max_length=255)] = None
    password: Annotated[str | None, Field(min_length=1, max_length=512)] = None
    port: Annotated[int | None, Field(ge=1, le=65535)] = None
    use_ssl: bool | None = None
    verify_tls: bool | None = None
    enabled: bool | None = None
    sync_scopes: bool | None = None
    sync_leases: bool | None = None
    sync_interval_seconds: Annotated[int | None, Field(ge=30, le=86400)] = None
    description: Annotated[str | None, Field(max_length=2048)] = None
    scope_subnet_ids: list[uuid.UUID] | None = None


class WindowsDhcpRead(StrictModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    name: str
    host: str
    username: str
    port: int
    use_ssl: bool
    verify_tls: bool
    enabled: bool
    sync_scopes: bool
    sync_leases: bool
    sync_interval_seconds: int
    description: str | None = None
    scope_subnet_ids: list[uuid.UUID] | None = None
    last_sync_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime
