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
# 上傳檔（系統匯入的暫存、機房平面圖）寫到這次測試自己的暫存目錄：預設的 /var/lib/jt-ipam 在
# GitHub CI 上不存在、也沒有權限建（v0.6.58 的 CI 因此紅）；開發機以 root 跑才剛好過，而且會寫進系統目錄。
if "UPLOAD_DIR" not in os.environ:
    import tempfile
    os.environ["UPLOAD_DIR"] = os.path.join(tempfile.mkdtemp(prefix="jtipam-test-"), "uploads")


# 這一套測試會在**每個測試前 TRUNCATE 所有資料表**。也就是說 JTIPAM_TEST_DATABASE_URL
# 指到哪裡，哪裡就會被清空 —— 貼錯一次連線字串（prod、或本機那份 prod-like 的 jt_ipam）
# 就是不可逆的資料損失，而且不會有任何確認步驟。
#
# 所以這裡擋在最前面：只接受名字看得出是拋棄式的資料庫。這條規則刻意用「名字」而不是
# 主機位址 —— CI 與本機的測試庫都在 127.0.0.1，用主機分不出來；真正要防的是「同一台機器上
# 的另一個資料庫」。
_DISPOSABLE_SUFFIXES = ("_test", "_e2e")


def _assert_disposable(url: str) -> None:
    from urllib.parse import urlparse

    name = urlparse(url.replace("postgresql+asyncpg://", "postgresql://")).path.lstrip("/")
    if not name.endswith(_DISPOSABLE_SUFFIXES):
        raise RuntimeError(
            f"JTIPAM_TEST_DATABASE_URL 指向 {name!r}，這不是拋棄式測試庫。\n"
            f"測試會在每個測試前清空整個資料庫，所以只接受名稱以 "
            f"{' / '.join(_DISPOSABLE_SUFFIXES)} 結尾的資料庫（例如 jt_ipam_test）。\n"
            f"要對其他資料庫跑，請先把它複製成一個 *_test 的拋棄式副本。"
        )


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

    _assert_disposable(url)
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
            # 稽核表擋 TRUNCATE（0201）：測試帳號是表的擁有者，清表時暫停觸發器、清完立刻恢復。
            # 正式環境的應用程式帳號不是擁有者（scripts/jt-ipam.sh harden-audit），做不到這件事
            has_guard = (await conn.execute(text(
                "SELECT 1 FROM pg_trigger WHERE tgname = 'audit_logs_no_truncate'"))).first() is not None
            if has_guard:
                await conn.execute(text("ALTER TABLE audit_logs DISABLE TRIGGER audit_logs_no_truncate"))
            await conn.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"))
            if has_guard:
                await conn.execute(text("ALTER TABLE audit_logs ENABLE TRIGGER audit_logs_no_truncate"))
    yield


@pytest.fixture
async def db_session(_engine):  # type: ignore[no-untyped-def]
    """獨立 AsyncSession（自己的 connection）；endpoint 用各自 session。"""
    from sqlalchemy.ext.asyncio import AsyncSession

    async with AsyncSession(_engine, expire_on_commit=False) as session:
        yield session


@pytest.fixture
def session_factory(_engine):  # type: ignore[no-untyped-def]
    """可開「多個各自獨立連線」的 session 工廠。

    給需要模擬並行的測試用（例如多個 uvicorn worker 同時啟動時的 seed 競態）——
    共用同一個 session 模擬不出競態，那只是同一條連線上的循序操作。
    """
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    return async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)


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
    """清掉來源優先序的 in-process 60s 快取，避免測試間互相污染。

    DB 交易每個測試都 rollback，但模組級快取不會 —— 前一個測試設過的順序／停用會在
    TTL 內被後面的測試讀到（CI 機器跑得快，更容易踩到）。

    ⚠️ 這裡刻意**直接 import**、不做 `getattr(..., "_cache", {})` 那種容錯：
    快取搬家時（v0.5.209 收斂到 services/precedence）容錯寫法會安靜地什麼都不清，
    測試就開始互相污染，而且看起來只是「某支測試偶爾失敗」。寧可 import 失敗炸掉。
    """
    from app.services.precedence import bust_all
    bust_all()
    # system_config 的設定快取（LLM／MCP、稽核轉送、通知管道）同樣是模組級 60 秒：一支測試打開對外 MCP，
    # 緊接著的測試在 60 秒內還讀到「已開啟」（v1.0.13 推上 GitHub 後 CI 才抓到，本機那次跑得慢剛好過）
    from app.services import system_config as _sc
    _sc._cache.clear()
    _sc._af_cache.clear()
    _sc._ncfg_cache.clear()
    yield
