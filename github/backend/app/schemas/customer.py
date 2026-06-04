"""Customer / 管理單位 schemas。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import Field

from app.schemas.base import StrictModel


class CustomerBase(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    title: Annotated[str | None, Field(max_length=256)] = None
    description: Annotated[str | None, Field(max_length=2048)] = None
    contact: Annotated[str | None, Field(max_length=128)] = None
    email: Annotated[str | None, Field(max_length=256)] = None
    phone: Annotated[str | None, Field(max_length=64)] = None
    address: Annotated[str | None, Field(max_length=512)] = None


class CustomerCreate(CustomerBase):
    pass


class CustomerUpdate(StrictModel):
    name: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    title: Annotated[str | None, Field(max_length=256)] = None
    description: Annotated[str | None, Field(max_length=2048)] = None
    contact: Annotated[str | None, Field(max_length=128)] = None
    email: Annotated[str | None, Field(max_length=256)] = None
    phone: Annotated[str | None, Field(max_length=64)] = None
    address: Annotated[str | None, Field(max_length=512)] = None


class CustomerRead(CustomerBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    subnet_count: int = 0
