"""User schemas。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import StringConstraints

from app.schemas.base import StrictModel


class UserMe(StrictModel):
    """`/me` 回傳的精簡資料；不洩漏 password_hash / totp_secret 等。"""

    id: uuid.UUID
    username: str
    # 不用 EmailStr — 內部部署常用 .local / .lan TLD（reserved domains）
    email: Annotated[str, StringConstraints(min_length=3, max_length=255)]
    display_name: str | None
    auth_provider: str
    is_active: bool
    is_admin: bool
    last_login_at: datetime | None
    created_at: datetime
