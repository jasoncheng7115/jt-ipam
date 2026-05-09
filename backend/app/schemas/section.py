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


class SectionCreate(SectionBase):
    pass


class SectionUpdate(StrictModel):
    name: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    description: Annotated[str | None, Field(max_length=1024)] = None
    parent_id: uuid.UUID | None = None
    strict_mode: bool | None = None
    display_order: Annotated[int | None, Field(ge=0, le=10_000)] = None


class SectionRead(SectionBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
