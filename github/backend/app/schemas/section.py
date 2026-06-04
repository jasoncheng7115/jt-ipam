"""Section schemas。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import Field

from app.schemas.base import StrictModel


class SectionBase(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    description: Annotated[str | None, Field(max_length=1024)] = None
    parent_id: uuid.UUID | None = None
    strict_mode: bool = False
    display_order: Annotated[int, Field(ge=0, le=10_000)] = 0
    customer_id: uuid.UUID | None = None


class SectionCreate(SectionBase):
    pass


class SectionUpdate(StrictModel):
    name: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    description: Annotated[str | None, Field(max_length=1024)] = None
    parent_id: uuid.UUID | None = None
    strict_mode: bool | None = None
    display_order: Annotated[int | None, Field(ge=0, le=10_000)] = None
    customer_id: uuid.UUID | None = None


class SectionRead(SectionBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    # 由 list/get endpoint 在回傳時計算填入；單獨 fetch 也會帶
    subnet_count: int = 0
