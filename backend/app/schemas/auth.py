"""認證 schemas。"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from app.schemas.base import StrictModel


class LoginRequest(StrictModel):
    username: Annotated[str, Field(min_length=1, max_length=128)]
    password: Annotated[str, Field(min_length=1, max_length=256)]


class TokenResponse(StrictModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class RefreshRequest(StrictModel):
    refresh_token: Annotated[str, Field(min_length=1, max_length=4096)]
