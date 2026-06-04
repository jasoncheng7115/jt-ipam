"""IP 異動記錄讀取 schema（feature B）。"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import ConfigDict

from app.schemas.base import StrictModel


class IPChangeLogRead(StrictModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ip_id: uuid.UUID | None
    subnet_id: uuid.UUID | None
    ip_text: str
    event_type: str
    field: str | None
    old_value: str | None
    new_value: str | None
    source: str
    actor_user_id: uuid.UUID | None
    note: str | None
    created_at: datetime
    # 顯示用：actor 帳號名（join 後填）
    actor_username: str | None = None
