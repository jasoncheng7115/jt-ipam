"""BackgroundTask schemas。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from app.schemas.base import StrictModel


class BackgroundTaskRead(StrictModel):
    id: uuid.UUID
    kind: str
    status: str
    trigger: str
    target_type: str | None
    target_id: uuid.UUID | None
    target_label: str | None
    actor_user_id: uuid.UUID | None
    progress: int
    summary: dict[str, Any] | None
    error: str | None
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
