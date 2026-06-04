"""IP Request schemas。"""

from __future__ import annotations

import ipaddress
import uuid
from datetime import datetime
from typing import Annotated

from pydantic import Field, field_validator

from app.schemas.base import StrictModel


class IPRequestCreate(StrictModel):
    subnet_id: uuid.UUID
    purpose: Annotated[str, Field(min_length=3, max_length=512)]
    hostname: Annotated[str | None, Field(max_length=253)] = None
    description: Annotated[str | None, Field(max_length=1024)] = None
    requested_ip: str | None = None
    expires_at: datetime | None = None

    @field_validator("requested_ip")
    @classmethod
    def _ip_valid(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        try:
            ipaddress.ip_address(v)
        except ValueError as exc:
            raise ValueError(f"Invalid IP: {v}") from exc
        return v


class IPRequestReject(StrictModel):
    reason: Annotated[str, Field(min_length=3, max_length=1024)]


class IPRequestRead(StrictModel):
    id: uuid.UUID
    status: str
    requester_user_id: uuid.UUID
    approver_user_id: uuid.UUID | None
    subnet_id: uuid.UUID
    requested_ip: str | None
    hostname: str | None
    description: str | None
    purpose: str
    expires_at: datetime | None
    allocated_ip_id: uuid.UUID | None
    approved_at: datetime | None
    rejected_at: datetime | None
    rejected_reason: str | None
    fulfilled_at: datetime | None
    cancelled_at: datetime | None
    created_at: datetime
    updated_at: datetime


class IPRequestEventRead(StrictModel):
    id: uuid.UUID
    actor_user_id: uuid.UUID | None
    event_type: str
    message: str | None
    created_at: datetime


class IPRequestDetail(StrictModel):
    request: IPRequestRead
    events: list[IPRequestEventRead]
