"""Custom Field schemas。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import Field, field_validator

from app.schemas.base import StrictModel

_OBJECT_TYPES = {"subnet", "ip", "device"}
_FIELD_TYPES = {"text", "int", "float", "bool", "date", "select", "multi_select", "regex"}


class CustomFieldBase(StrictModel):
    object_type: str
    name: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
    label_zh_tw: Annotated[str | None, Field(max_length=128)] = None
    label_en_us: Annotated[str | None, Field(max_length=128)] = None
    field_type: str
    options: dict[str, Any] | None = None
    validation_regex: Annotated[str | None, Field(max_length=512)] = None
    required: bool = False
    display_order: Annotated[int, Field(ge=0, le=10_000)] = 0

    @field_validator("object_type")
    @classmethod
    def _ot(cls, v: str) -> str:
        if v not in _OBJECT_TYPES:
            raise ValueError(f"object_type must be one of {sorted(_OBJECT_TYPES)}")
        return v

    @field_validator("field_type")
    @classmethod
    def _ft(cls, v: str) -> str:
        if v not in _FIELD_TYPES:
            raise ValueError(f"field_type must be one of {sorted(_FIELD_TYPES)}")
        return v


class CustomFieldCreate(CustomFieldBase):
    pass


class CustomFieldUpdate(StrictModel):
    label_zh_tw: Annotated[str | None, Field(max_length=128)] = None
    label_en_us: Annotated[str | None, Field(max_length=128)] = None
    options: dict[str, Any] | None = None
    validation_regex: Annotated[str | None, Field(max_length=512)] = None
    required: bool | None = None
    display_order: Annotated[int | None, Field(ge=0, le=10_000)] = None


class CustomFieldRead(CustomFieldBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
