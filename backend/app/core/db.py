"""非同步資料庫連線與 session。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings


def _build_engine() -> AsyncEngine:
    settings = get_settings()
    engine = create_async_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
        pool_recycle=1800,
        echo=False,  # production 永遠不開 echo（A05 / A09 — 避免 SQL 含參數寫入 log）
        future=True,
    )
    # 啟用 statement timeout（A04：避免單一查詢掛死整個 worker）
    @event.listens_for(engine.sync_engine, "connect")
    def _set_pg_statement_timeout(dbapi_conn: Any, _: Any) -> None:  # noqa: ANN401
        cursor = dbapi_conn.cursor()
        try:
            cursor.execute("SET statement_timeout = '30s'")
        finally:
            cursor.close()

    return engine


engine: AsyncEngine = _build_engine()

SessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
    class_=AsyncSession,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency。"""
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
