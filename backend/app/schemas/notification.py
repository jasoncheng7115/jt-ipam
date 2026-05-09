"""Notification + Webhook schemas。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import Field, HttpUrl, field_validator

from app.schemas.base import StrictModel


class NotificationRead(StrictModel):
    id: uuid.UUID
    severity: str
    title: str
    body: str | None
    link: str | None
    object_type: str | None
    object_id: uuid.UUID | None
    read_at: datetime | None
    created_at: datetime


class WebhookCreate(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    target_url: HttpUrl
    events: list[str] = Field(default_factory=lambda: ["*"])
    headers: dict[str, str] | None = None

    @field_validator("events")
    @classmethod
    def _events_valid(cls, v: list[str]) -> list[str]:
        for e in v:
            if not (e == "*" or e.replace(".", "").replace("_", "").isalnum()):
                raise ValueError(f"invalid event name: {e!r}")
        return v


class WebhookCreateResponse(StrictModel):
    id: uuid.UUID
    name: str
    target_url: str
    events: list[str]
    secret: str  # 只回傳一次（A02 — 之後可手動 rotate）
    enabled: bool


class WebhookRead(StrictModel):
    id: uuid.UUID
    name: str
    target_url: str
    events: list[str]
    enabled: bool
    failure_count: int
    last_attempt_at: datetime | None
    last_success_at: datetime | None
    last_error: str | None
    headers: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime
