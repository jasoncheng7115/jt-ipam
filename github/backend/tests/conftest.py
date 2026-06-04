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
# 測試一律關閉限流：全部請求來自 127.0.0.1，共用 Redis bucket 會在測試間累積、
# 觸發 429/401 連鎖失敗，且會污染 prod 的 rl:* bucket。
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")


def _apply_test_db_env() -> None:
    """把 POSTGRES_* 改寫到 JTIPAM_TEST_DATABASE_URL 指向的測試庫。

    **必須在 collection（import 測試模組）之前就生效**：某些 test module（如
    test_mcp_vpn_tool）在 module top-level 就會 transitively import app.core.db，
    而 app.core.db 在 import 時就 `engine = _build_engine()`。若這發生在 session-autouse
    fixture 跑之前（fixture 在 collection 之後才跑），engine 會 bind 到 prod DB，
    導致 app 讀寫 prod 而看不到測試庫裡 commit 的 admin_user → login 401。
    因此這裡用「module-level 直接覆寫」而非僅靠 fixture。
    """
    url = os.environ.get("JTIPAM_TEST_DATABASE_URL")
    if not url:
        return
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


# 在 conftest import 當下（早於任何測試模組 collection）就生效
_apply_test_db_env()


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
    _apply_test_db_env()  # module-level 已套過，這裡再保險一次
    if os.environ.get("JTIPAM_TEST_DATABASE_URL"):
        # 清掉 lru_cache 的 get_settings（萬一 collection 期間已 cache 過舊值）
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


@pytest.fixture(autouse=True)
def _reset_precedence_caches():
    """清掉各 precedence 服務的 in-process 60s 快取，避免測試間互相污染：
    DB 交易每測試 rollback，但模組級 `_cache` 不會，導致前一個測試設過的
    順序/停用在 TTL 內被後面的測試讀到（CI 機器快、更容易踩到）。"""
    import importlib
    for _mod in (
        "app.services.hostname",
        "app.services.device_name_precedence",
        "app.services.arp_precedence",
        "app.services.model_precedence",
    ):
        try:
            _m = importlib.import_module(_mod)
            getattr(_m, "_cache", {}).clear()
        except Exception:
            pass
    yield
