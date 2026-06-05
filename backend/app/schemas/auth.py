"""認證 schemas。"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from app.schemas.base import StrictModel


class LoginRequest(StrictModel):
    username: Annotated[str, Field(min_length=1, max_length=128)]
    password: Annotated[str, Field(min_length=1, max_length=256)]
    realm: Annotated[str, Field(max_length=32)] = "local"


class TokenResponse(StrictModel):
    """登入 / refresh 成功的回應；若 user 有開 TOTP 則回傳 mfa_required + mfa_token。"""

    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str = "bearer"  # noqa: S105 — OAuth token_type 字面值，非密碼
    expires_in: int | None = None  # seconds

    # MFA 挑戰（僅 login 第一步成功且 user 有 TOTP 時設定）
    mfa_required: bool = False
    mfa_token: str | None = None


class RefreshRequest(StrictModel):
    refresh_token: Annotated[str, Field(min_length=1, max_length=4096)]
