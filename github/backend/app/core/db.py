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


def _json_dumps_utf8(obj: Any) -> str:
    """JSONB 序列化 — ensure_ascii=False 讓中文以原生 UTF-8 bytes 流向 PG，
    避免被轉成 \\uXXXX escape 後在 SQL_ASCII encoding 的 DB 解碼失敗。
    default=str：UUID / datetime 等非原生 JSON 型別一律轉字串（稽核 diff 常含
    UUID FK，否則整批 update 端點都會 500）。
    """
    import json
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)


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
        json_serializer=_json_dumps_utf8,
    )
    # 啟用 statement timeout（A04：避免單一查詢掛死整個 worker）
    @event.listens_for(engine.sync_engine, "connect")
    def _set_pg_statement_timeout(dbapi_conn: Any, _: Any) -> None:
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
