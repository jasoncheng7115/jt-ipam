"""User schemas。"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import EmailStr

from app.schemas.base import StrictModel


class UserMe(StrictModel):
    """`/me` 回傳的精簡資料；不洩漏 password_hash / totp_secret 等。"""

    id: uuid.UUID
    username: str
    email: EmailStr
    display_name: str | None
    auth_provider: str
    is_active: bool
    is_admin: bool
    last_login_at: datetime | None
    created_at: datetime
