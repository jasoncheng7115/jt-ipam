"""Device schemas。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field, field_validator

from app.schemas.base import StrictModel

_VALID_TYPES = {"server", "switch", "router", "firewall", "ap", "storage", "ipmi", "other"}


class DeviceBase(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    fqdn: Annotated[str | None, Field(max_length=255)] = None
    type: str = "other"
    vendor: Annotated[str | None, Field(max_length=64)] = None
    model: Annotated[str | None, Field(max_length=64)] = None
    serial: Annotated[str | None, Field(max_length=128)] = None
    location_id: uuid.UUID | None = None
    rack_id: uuid.UUID | None = None
    u_position: Annotated[int | None, Field(ge=1, le=99)] = None
    u_size: Annotated[int | None, Field(ge=1, le=99)] = None
    rack_face: Literal["front", "rear"] | None = None
    rack_side: Literal["full", "left", "right"] = "full"
    description: Annotated[str | None, Field(max_length=1024)] = None
    customer_id: uuid.UUID | None = None
    custom_fields: dict[str, Any] | None = None

    @field_validator("type")
    @classmethod
    def _type_valid(cls, v: str) -> str:
        if v not in _VALID_TYPES:
            raise ValueError(f"type must be one of {sorted(_VALID_TYPES)}")
        return v


class DeviceCreate(DeviceBase):
    primary_ip_id: uuid.UUID | None = None


class DeviceUpdate(StrictModel):
    name: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    fqdn: Annotated[str | None, Field(max_length=255)] = None
    type: str | None = None
    vendor: Annotated[str | None, Field(max_length=64)] = None
    model: Annotated[str | None, Field(max_length=64)] = None
    serial: Annotated[str | None, Field(max_length=128)] = None
    location_id: uuid.UUID | None = None
    rack_id: uuid.UUID | None = None
    u_position: Annotated[int | None, Field(ge=1, le=99)] = None
    u_size: Annotated[int | None, Field(ge=1, le=99)] = None
    rack_face: Literal["front", "rear"] | None = None
    rack_side: Literal["full", "left", "right"] | None = None
    description: Annotated[str | None, Field(max_length=1024)] = None
    primary_ip_id: uuid.UUID | None = None
    customer_id: uuid.UUID | None = None
    custom_fields: dict[str, Any] | None = None

    @field_validator("type")
    @classmethod
    def _type_valid(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if v not in _VALID_TYPES:
            raise ValueError(f"type must be one of {sorted(_VALID_TYPES)}")
        return v


class DeviceRead(DeviceBase):
    id: uuid.UUID
    primary_ip_id: uuid.UUID | None
    ip: str | None = None   # 由 endpoint 解析 primary_ip_id 後填入（清單顯示用）
    ip_address_id: str | None = None   # 有對應的 IPAddress → IP 欄可點進該位址
    ip_match_id: str | None = None   # 有相符但未連結的 IPAddress → 可一鍵關聯
    created_at: datetime
    updated_at: datetime
