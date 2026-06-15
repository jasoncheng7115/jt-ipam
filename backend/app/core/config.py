"""應用設定。

OWASP A02 / A04：
- 不允許在 production 啟用 debug、留預設密鑰、放寬 CORS
- SECRET_KEY / ENCRYPTION_KEY / AUDIT_CHAIN_GENESIS 必填且不可為範例值
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    Field,
    HttpUrl,
    PostgresDsn,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["development", "staging", "production"]
Theme = Literal["light", "dark", "auto"]
Locale = Literal["zh-TW", "en-US"]
SameSite = Literal["lax", "strict", "none"]
TlsMode = Literal["nginx", "direct"]

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
    cors_origins: Annotated[list[str], NoDecode, Field(default_factory=lambda: ["http://localhost:5173"])]

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

    # ── TLS（A02：強制 SSL；雙模式）──
    # nginx：後端綁 127.0.0.1:8000（純 HTTP loopback），nginx 終結 HTTPS
    # direct：uvicorn 直接吃 cert/key，綁 0.0.0.0:443
    backend_tls_mode: TlsMode = "nginx"
    backend_bind_host: str = "127.0.0.1"
    backend_bind_port: int = 8000
    backend_tls_cert_file: str | None = None
    backend_tls_key_file: str | None = None

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
    rate_limit_enabled: bool = True
    rate_limit_default: str = "100/minute"
    rate_limit_auth: str = "10/minute"
    rate_limit_api_token: str = "600/minute"  # noqa: S105 — 限流字串，非密碼/令牌
    rate_limit_ai: str = "20/minute"   # LLM 推論昂貴，專屬較嚴格限流（防 DoS / 拖垮）

    # ── SSRF Allowlist (A10) ──
    # 預設允許連到 RFC1918 私網：IPAM 的整合對象（OPNsense/LibreNMS/Wazuh/Proxmox/
    # AdGuard/DNS/Ollama…）本來就在內網，關掉會讓多數部署開箱不能用。loopback /
    # link-local / cloud-metadata(169.254.169.254) 仍由 safe_http 硬擋、不受此旗標影響。
    # ⚠️ 取捨：被攻陷的 admin 帳號可藉整合 URL 對內網其他服務發請求（橫向移動面）。
    # 若部署不需打私網，設 false 並用 outbound_allow_cidrs 白名單各整合目標網段收斂。
    outbound_allow_cidrs: Annotated[list[str], NoDecode, Field(default_factory=list)]
    outbound_allow_hosts: Annotated[list[str], NoDecode, Field(default_factory=list)]
    outbound_allow_private: bool = True

    # ── ARP 紀錄保留 ──
    # arp_entries 只新增/更新、不會自動回收；定時 sync 會刪掉 last_seen_at 超過此天數的
    # 舊 ARP（含來源 device 被刪的孤兒 row）。設 0 或負數＝停用清除（永久保留）。
    arp_retention_days: int = 30

    # ── Graylog ──
    graylog_host: str | None = None
    graylog_port: int = 12201
    graylog_facility: str = "jt-ipam"

    # ── SMTP（Email 通知）──
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from: str = "jt-ipam@localhost"
    smtp_tls_mode: Literal["none", "starttls", "tls"] = "starttls"
    smtp_timeout: float = 10.0

    # ── LDAP / AD ──
    ldap_enabled: bool = False
    ldap_server: str | None = None         # 不含 scheme，例 "ldap.example.com"
    ldap_port: int = 389
    ldap_use_ssl: bool = False             # LDAPS（直接 TLS，通常 port 636）
    ldap_use_starttls: bool = True
    ldap_bind_dn: str | None = None
    ldap_bind_password: SecretStr | None = None
    ldap_search_base: str | None = None
    ldap_user_filter: str = "(uid={username})"   # AD 通常用 (sAMAccountName={username})
    ldap_attr_email: str = "mail"
    ldap_attr_display_name: str = "displayName"
    ldap_attr_member_of: str = "memberOf"
    ldap_timeout: float = 8.0
    ldap_admin_groups: Annotated[list[str], NoDecode, Field(default_factory=list)]

    # ── Radius ──
    radius_enabled: bool = False
    radius_server: str | None = None
    radius_port: int = 1812
    radius_secret: SecretStr | None = None
    radius_timeout: float = 5.0
    radius_nas_identifier: str = "jt-ipam"

    # ── OIDC SSO（Phase 3）──
    oidc_enabled: bool = False
    oidc_issuer: str | None = None              # 例 https://accounts.google.com
    oidc_client_id: str | None = None
    oidc_client_secret: SecretStr | None = None
    oidc_redirect_uri: str | None = None
    oidc_scope: str = "openid email profile"
    oidc_admin_groups: Annotated[list[str], NoDecode, Field(default_factory=list)]
    oidc_groups_claim: str = "groups"
    oidc_username_claim: str = "preferred_username"

    # ── SAML SSO（Phase 3）──
    saml_enabled: bool = False
    # IdP（IdP-side metadata：URL 二擇一；遠端 URL 會被快取）
    saml_idp_metadata_url: str | None = None
    saml_idp_metadata_xml: str | None = None    # 直接貼 XML（離線環境用）
    # SP（Service Provider）— 我方
    saml_sp_entity_id: str | None = None        # 預設用 API_PUBLIC_URL/saml/metadata
    saml_sp_acs_url: str | None = None          # 預設用 API_PUBLIC_URL/auth/saml/acs
    saml_sp_sls_url: str | None = None          # Single Logout（選填）
    saml_sp_x509_cert: str | None = None        # PEM；簽名 / 加密用（選填，但建議生產配）
    saml_sp_private_key: SecretStr | None = None
    # 安全選項
    saml_want_assertions_signed: bool = True
    saml_want_assertions_encrypted: bool = False
    saml_want_name_id_encrypted: bool = False
    saml_authn_requests_signed: bool = False    # 開了要 SP cert/key
    # Attribute mapping（IdP 送 attribute 的名字；常見預設）
    saml_attr_username: str = "uid"
    saml_attr_email: str = "mail"
    saml_attr_displayname: str = "cn"
    saml_attr_groups: str = "memberOf"
    saml_admin_groups: Annotated[list[str], NoDecode, Field(default_factory=list)]
    # 舊鍵保留兼容
    saml_metadata_url: str | None = None        # alias of saml_idp_metadata_url
    saml_entity_id: str | None = None           # alias of saml_sp_entity_id
    saml_acs_url: str | None = None             # alias of saml_sp_acs_url

    # ── AI / Ollama（語意搜尋；本地推論不外送，符合規格 §11.1）──
    ollama_enabled: bool = False
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_embedding_model: str = "qwen3-embedding:8b"
    ollama_chat_model: str = "gpt-oss:120b"
    ollama_timeout: float = 90.0  # 大型模型 + 工具結果上下文時 30s 太短會 ReadTimeout
    embedding_dim: int = 768

    # ── Frontend defaults ──
    default_locale: Locale = "zh-TW"
    default_theme: Theme = "auto"

    # ── 上傳檔（機房平面圖等）存放目錄；放在 repo 外，git pull / rebuild 不會被清掉 ──
    upload_dir: str = "/var/lib/jt-ipam/uploads"

    # =====================================================================
    # 驗證器
    # =====================================================================

    @field_validator(
        "cors_origins",
        "outbound_allow_cidrs",
        "outbound_allow_hosts",
        "ldap_admin_groups",
        "oidc_admin_groups",
        "saml_admin_groups",
        mode="before",
    )
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
    def _tls_guards(self) -> Settings:
        """A02 — SSL 強制檢查（任何環境）+ A05 production 安全檢查。

        SSL 為硬性需求：
        - APP_PUBLIC_URL / API_PUBLIC_URL 必須是 https://
        - direct 模式：cert/key 必填且必須是絕對路徑
        - nginx 模式：後端必須綁 loopback（127.0.0.1 / ::1），不對外曝露
        """
        errors: list[str] = []

        # ── SSL 強制（任何環境）──
        if not str(self.app_public_url).startswith("https://"):
            errors.append("APP_PUBLIC_URL must use https:// (SSL is required)")
        if not str(self.api_public_url).startswith("https://"):
            errors.append("API_PUBLIC_URL must use https:// (SSL is required)")

        if self.backend_tls_mode == "direct":
            if not self.backend_tls_cert_file or not self.backend_tls_key_file:
                errors.append(
                    "BACKEND_TLS_MODE=direct requires BACKEND_TLS_CERT_FILE and BACKEND_TLS_KEY_FILE"
                )
            else:
                cert = Path(self.backend_tls_cert_file)
                key = Path(self.backend_tls_key_file)
                if not cert.is_absolute() or not key.is_absolute():
                    errors.append("BACKEND_TLS_CERT_FILE and BACKEND_TLS_KEY_FILE must be absolute paths")
                # 啟動時讀檔由 wrapper / uvicorn 處理；此處不在 settings 載入時做 I/O
        else:
            # nginx 模式：後端不能對外曝露（loopback only）
            if self.backend_bind_host not in ("127.0.0.1", "::1", "localhost"):
                errors.append(
                    f"BACKEND_TLS_MODE=nginx requires BACKEND_BIND_HOST to be loopback "
                    f"(got {self.backend_bind_host!r}); reverse proxy must terminate TLS"
                )

        # ── A05 production 額外檢查 ──
        if self.app_env == "production":
            if self.app_debug:
                errors.append("APP_DEBUG must be false in production")
            if "*" in self.cors_origins or "" in self.cors_origins:
                errors.append("CORS_ORIGINS must not be wildcard in production")
            if not self.session_cookie_secure:
                errors.append("SESSION_COOKIE_SECURE must be true in production")
            if any(str(origin).startswith("http://") for origin in self.cors_origins):
                errors.append("CORS_ORIGINS must all use https:// in production")

        if errors:
            raise ValueError("Configuration security violations: " + "; ".join(errors))
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
    return Settings()


def generate_secret(nbytes: int = 64) -> str:
    """產生十六進制 secret（給 setup script 用）。"""
    return secrets.token_hex(nbytes)
