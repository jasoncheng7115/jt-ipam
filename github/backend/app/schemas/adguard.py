"""AdGuard Home schemas。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import Field, HttpUrl

from app.schemas.base import StrictModel


class AdGuardBase(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    api_url: HttpUrl
    api_user: Annotated[str, Field(min_length=1, max_length=128)]
    enabled: bool = True
    verify_tls: bool = True
    sync_clients: bool = True
    sync_rewrites: bool = True
    sync_interval_seconds: Annotated[int, Field(ge=30, le=86400)] = 300
    description: Annotated[str | None, Field(max_length=2048)] = None


class AdGuardCreate(AdGuardBase):
    api_password: Annotated[str, Field(min_length=1, max_length=512)]


class AdGuardUpdate(StrictModel):
    name: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    api_url: HttpUrl | None = None
    api_user: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    api_password: Annotated[str | None, Field(min_length=1, max_length=512)] = None
    enabled: bool | None = None
    verify_tls: bool | None = None
    sync_clients: bool | None = None
    sync_rewrites: bool | None = None
    sync_interval_seconds: Annotated[int | None, Field(ge=30, le=86400)] = None
    description: Annotated[str | None, Field(max_length=2048)] = None


class AdGuardRead(AdGuardBase):
    id: uuid.UUID
    last_sync_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime
