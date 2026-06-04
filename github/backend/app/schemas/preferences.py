"""User preferences schemas。"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from pydantic import Field

from app.schemas.base import StrictModel


class UserPreferenceRead(StrictModel):
    locale: Literal["zh-TW", "en-US"]
    theme: Literal["light", "dark", "auto"]
    timezone: str
    calendar: Literal["gregorian", "minguo"]
    page_size: int
    table_columns: dict[str, Any] | None = None
    pinned_subnet_ids: list[uuid.UUID] | None = None


class UserPreferenceUpdate(StrictModel):
    locale: Literal["zh-TW", "en-US"] | None = None
    theme: Literal["light", "dark", "auto"] | None = None
    timezone: Annotated[str | None, Field(max_length=64)] = None
    calendar: Literal["gregorian", "minguo"] | None = None
    page_size: Annotated[int | None, Field(ge=10, le=500)] = None
    table_columns: dict[str, Any] | None = None
    pinned_subnet_ids: list[uuid.UUID] | None = None
