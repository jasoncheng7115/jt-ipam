"""Checkmk 整合 schemas。

認證用自動化帳號（automation user）＋它的 automation secret；secret 只進不出，Read 只回有沒有設。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import Field, HttpUrl

from app.schemas.base import StrictModel


class CheckmkBase(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    #: 網站網址（https://主機/網站名稱），也接受從瀏覽器複製的整串網址或完整的 API 網址
    api_url: HttpUrl
    api_user: Annotated[str, Field(min_length=1, max_length=128)]
    enabled: bool = True
    verify_tls: bool = True
    scope_subnet_ids: list[uuid.UUID] | None = None
    sync_interval_seconds: Annotated[int, Field(ge=30, le=86400)] = 300
    description: Annotated[str | None, Field(max_length=2048)] = None


class CheckmkCreate(CheckmkBase):
    api_secret: Annotated[str, Field(min_length=1, max_length=512)]


class CheckmkUpdate(StrictModel):
    name: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    api_url: HttpUrl | None = None
    api_user: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    #: 留空＝不變更
    api_secret: Annotated[str | None, Field(min_length=1, max_length=512)] = None
    enabled: bool | None = None
    verify_tls: bool | None = None
    scope_subnet_ids: list[uuid.UUID] | None = None
    sync_interval_seconds: Annotated[int | None, Field(ge=30, le=86400)] = None
    description: Annotated[str | None, Field(max_length=2048)] = None


class CheckmkRead(StrictModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    name: str
    api_url: str
    api_user: str
    enabled: bool
    verify_tls: bool
    has_api_secret: bool = False
    scope_subnet_ids: list[uuid.UUID] | None = None
    sync_interval_seconds: int
    description: str | None = None
    last_sync_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime
