"""API Token schemas。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import Field

from app.schemas.base import StrictModel


class APITokenCreate(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    # 預設 90 天，最長 1 年（A07）
    expires_in_days: Annotated[int, Field(ge=1, le=365)] = 90
    scopes: list[str] = Field(default_factory=list)
    object_filters: dict[str, Any] | None = None


class APITokenCreateResponse(StrictModel):
    """建立成功一次性回傳明文 token；之後再也無法取得。"""

    id: uuid.UUID
    name: str
    token: str               # 完整 token（jt_<env>_<random>）— 僅此一次
    token_prefix: str
    expires_at: datetime
    scopes: list[str]


class APITokenRead(StrictModel):
    id: uuid.UUID
    name: str
    token_prefix: str
    scopes: list[str]
    object_filters: dict[str, Any] | None
    expires_at: datetime
    last_used_at: datetime | None
    last_used_ip: str | None
    revoked_at: datetime | None
    created_at: datetime
