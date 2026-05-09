"""共用 fixture。

DB-需要的測試會自動 skip，除非設定 `JTIPAM_TEST_DATABASE_URL`。

整合測試 fixtures：
- `db_session`：每測試獨立 transaction，結束 rollback（DB 回到乾淨狀態）
- `client`：FastAPI ASGITransport HTTP client，dependency 覆寫使用 db_session
- `admin_user`：在 db_session 內建立 admin
- `auth_headers`：以 admin_user 簽發的 access token
"""

from __future__ import annotations

import os
import uuid

import pytest

# Dummy secrets 讓 import 期能建 Settings
os.environ.setdefault("SECRET_KEY", "0" * 64 + "a" * 64)
os.environ.setdefault(
    "ENCRYPTION_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
)
os.environ.setdefault("AUDIT_CHAIN_GENESIS", "0" * 64 + "b" * 64)
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("REDIS_PASSWORD", "test")
os.environ.setdefault("APP_PUBLIC_URL", "https://localhost:5173")
os.environ.setdefault("API_PUBLIC_URL", "https://localhost:8443")
os.environ.setdefault("CORS_ORIGINS", "https://localhost:5173")
os.environ.setdefault("BACKEND_TLS_MODE", "nginx")
os.environ.setdefault("BACKEND_BIND_HOST", "127.0.0.1")
os.environ.setdefault("BACKEND_BIND_PORT", "8000")
os.environ.setdefault("OUTBOUND_ALLOW_PRIVATE", "true")


@pytest.fixture(scope="session")
def test_database_url() -> str:
    url = os.environ.get("JTIPAM_TEST_DATABASE_URL")
    if not url:
        pytest.skip("JTIPAM_TEST_DATABASE_URL not set; skipping DB-backed tests")
    # 把 settings.database_url override 為測試 DB（讓 alembic / app 共用）
    # asyncpg URL 格式：postgresql+asyncpg://user:pass@host:port/dbname
    return url


@pytest.fixture(scope="session", autouse=True)
def _override_db_settings(request):  # type: ignore[no-untyped-def]
    """在 session 開始就把 POSTGRES_* 改寫到測試 DB（如果有 JTIPAM_TEST_DATABASE_URL）。

    autouse 確保 app.core.config 第一次 import 前就生效。
    """
    url = os.environ.get("JTIPAM_TEST_DATABASE_URL")
    if url:
        from urllib.parse import urlparse
        p = urlparse(url.replace("postgresql+asyncpg://", "postgresql://"))
        if p.hostname:
            os.environ["POSTGRES_HOST"] = p.hostname
        if p.port:
            os.environ["POSTGRES_PORT"] = str(p.port)
        if p.username:
            os.environ["POSTGRES_USER"] = p.username
        if p.password:
            os.environ["POSTGRES_PASSWORD"] = p.password
        if p.path and p.path != "/":
            os.environ["POSTGRES_DB"] = p.path.lstrip("/")
        # 同時清掉 lru_cache 的 get_settings
        try:
            from app.core.config import get_settings
            get_settings.cache_clear()
        except ImportError:
            pass
    yield


@pytest.fixture(scope="session")
def _engine(test_database_url):  # type: ignore[no-untyped-def]
    from sqlalchemy.ext.asyncio import create_async_engine
    return create_async_engine(test_database_url, future=True, pool_pre_ping=True)


@pytest.fixture(autouse=True)
async def _clean_db(_engine, request):  # type: ignore[no-untyped-def]
    """每個 e2e 測試開始前 TRUNCATE 所有資料表（保留 alembic_version）。

    只在標記 e2e 的測試或實際使用 db_session/client 的測試生效；純 schema 測試不會跑到。
    """
    if not any(name in request.fixturenames for name in ("db_session", "client", "admin_user")):
        yield
        return
    from sqlalchemy import text
    async with _engine.begin() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT tablename FROM pg_tables "
                    "WHERE schemaname='public' AND tablename <> 'alembic_version'"
                )
            )
        ).fetchall()
        if rows:
            tables = ", ".join(f'"{r[0]}"' for r in rows)
            await conn.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
async def db_session(_engine):  # type: ignore[no-untyped-def]
    """獨立 AsyncSession（自己的 connection）；endpoint 用各自 session。"""
    from sqlalchemy.ext.asyncio import AsyncSession

    async with AsyncSession(_engine, expire_on_commit=False) as session:
        yield session


@pytest.fixture
async def client():  # type: ignore[no-untyped-def]
    """FastAPI httpx async client；endpoint 用真正的 get_session。"""
    from httpx import ASGITransport, AsyncClient

    from app.main import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", follow_redirects=False,
    ) as c:
        yield c


@pytest.fixture
async def admin_user(db_session):  # type: ignore[no-untyped-def]
    from app.core.security import hash_password
    from app.models.user import User

    user = User(
        username=f"admin-{uuid.uuid4().hex[:8]}",
        email=f"admin-{uuid.uuid4().hex[:8]}@test.local",
        display_name="Admin Test",
        password_hash=hash_password("TestPassword2026!"),
        auth_provider="local",
        is_active=True,
        is_admin=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture
async def auth_headers(admin_user):  # type: ignore[no-untyped-def]
    from app.services.auth import issue_access_token
    return {"Authorization": f"Bearer {issue_access_token(admin_user)}"}
