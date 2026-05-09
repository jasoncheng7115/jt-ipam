"""應用設定。

OWASP A05 / A02：
- 不允許在 production 啟用 debug、留預設密鑰、放寬 CORS
- SECRET_KEY / ENCRYPTION_KEY / AUDIT_CHAIN_GENESIS 必填且不可為範例值
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import (
    AnyUrl,
    Field,
    HttpUrl,
    PostgresDsn,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "staging", "production"]
Theme = Literal["light", "dark", "auto"]
Locale = Literal["zh-TW", "en-US"]
SameSite = Literal["lax", "strict", "none"]

_PLACEHOLDER_PREFIX = "__CHANGE_ME__"
_MIN_SECRET_BYTES = 32


def _is_placeholder(value: str) -> bool:
    return value.startswith(_PLACEHOLDER_PREFIX) or len(value) < _MIN_SECRET_BYTES


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ──
    app_env: Environment = "development"
    app_debug: bool = True
    app_log_level: str = "INFO"
    app_timezone: str = "Asia/Taipei"
    app_public_url: HttpUrl = Field(default=HttpUrl("http://localhost:5173"))
    api_public_url: HttpUrl = Field(default=HttpUrl("http://localhost:8000"))

    # ── CORS ──
    cors_origins: Annotated[list[str], Field(default_factory=lambda: ["http://localhost:5173"])]

    # ── Secrets (A02 / A07) ──
    secret_key: SecretStr
    encryption_key: SecretStr
    audit_chain_genesis: SecretStr

    argon2_time_cost: int = 3
    argon2_memory_cost_kib: int = 65536
    argon2_parallelism: int = 4

    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 14
    session_cookie_secure: bool = True
    session_cookie_samesite: SameSite = "lax"

    # ── Database ──
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "jt_ipam"
    postgres_user: str = "jt_ipam"
    postgres_password: SecretStr
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # ── Redis ──
    redis_host: str = "redis"
    redis_port: int = 6379
    redis_password: SecretStr | None = None
    redis_db: int = 0

    # ── Rate Limiting ──
    rate_limit_default: str = "100/minute"
    rate_limit_auth: str = "10/minute"
    rate_limit_api_token: str = "600/minute"

    # ── SSRF Allowlist (A10) ──
    outbound_allow_cidrs: Annotated[list[str], Field(default_factory=list)]
    outbound_allow_hosts: Annotated[list[str], Field(default_factory=list)]
    outbound_allow_private: bool = True

    # ── Graylog ──
    graylog_host: str | None = None
    graylog_port: int = 12201
    graylog_facility: str = "jt-ipam"

    # ── Frontend defaults ──
    default_locale: Locale = "zh-TW"
    default_theme: Theme = "auto"

    # =====================================================================
    # 驗證器
    # =====================================================================

    @field_validator("cors_origins", "outbound_allow_cidrs", "outbound_allow_hosts", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> list[str]:
        if v is None or v == "":
            return []
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        return []

    @field_validator("secret_key", "encryption_key", "audit_chain_genesis")
    @classmethod
    def _no_placeholder_secret(cls, v: SecretStr) -> SecretStr:
        if _is_placeholder(v.get_secret_value()):
            raise ValueError(
                "Secret 看起來是範例值或長度不足；請以 `openssl rand -hex 64` 產生並寫入 .env"
            )
        return v

    @model_validator(mode="after")
    def _production_guards(self) -> "Settings":
        """A05 — production 安全檢查。"""
        if self.app_env != "production":
            return self

        errors: list[str] = []
        if self.app_debug:
            errors.append("APP_DEBUG must be false in production")
        if "*" in self.cors_origins or "" in self.cors_origins:
            errors.append("CORS_ORIGINS must not be wildcard in production")
        if not self.session_cookie_secure:
            errors.append("SESSION_COOKIE_SECURE must be true in production")
        if str(self.app_public_url).startswith("http://"):
            errors.append("APP_PUBLIC_URL must use HTTPS in production")
        if str(self.api_public_url).startswith("http://"):
            errors.append("API_PUBLIC_URL must use HTTPS in production")
        if errors:
            raise ValueError("Production security violations: " + "; ".join(errors))
        return self

    # =====================================================================
    # Derived properties
    # =====================================================================

    @property
    def database_url(self) -> str:
        """SQLAlchemy async URL（asyncpg driver）。"""
        return str(
            PostgresDsn.build(
                scheme="postgresql+asyncpg",
                username=self.postgres_user,
                password=self.postgres_password.get_secret_value(),
                host=self.postgres_host,
                port=self.postgres_port,
                path=self.postgres_db,
            )
        )

    @property
    def redis_url(self) -> str:
        auth = ""
        if self.redis_password is not None:
            auth = f":{self.redis_password.get_secret_value()}@"
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


def generate_secret(nbytes: int = 64) -> str:
    """產生十六進制 secret（給 setup script 用）。"""
    return secrets.token_hex(nbytes)
