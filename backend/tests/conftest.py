"""共用 fixture。

DB-需要的測試會自動 skip，除非設定 `JTIPAM_TEST_DATABASE_URL`。
"""

from __future__ import annotations

import os

import pytest

# 確保 import 期能拿到必要 secrets — 若 .env 沒有，灌入 dummy 值
os.environ.setdefault(
    "SECRET_KEY", "0" * 64 + "a" * 64
)
os.environ.setdefault(
    "ENCRYPTION_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
)
os.environ.setdefault(
    "AUDIT_CHAIN_GENESIS", "0" * 64 + "b" * 64
)
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("REDIS_PASSWORD", "test")
os.environ.setdefault("APP_PUBLIC_URL", "https://localhost:5173")
os.environ.setdefault("API_PUBLIC_URL", "https://localhost:8443")
os.environ.setdefault("CORS_ORIGINS", "https://localhost:5173")
os.environ.setdefault("BACKEND_TLS_MODE", "nginx")
os.environ.setdefault("BACKEND_BIND_HOST", "127.0.0.1")
os.environ.setdefault("BACKEND_BIND_PORT", "8000")


@pytest.fixture(scope="session")
def test_database_url() -> str:
    url = os.environ.get("JTIPAM_TEST_DATABASE_URL")
    if not url:
        pytest.skip("JTIPAM_TEST_DATABASE_URL not set; skipping DB-backed tests")
    return url
