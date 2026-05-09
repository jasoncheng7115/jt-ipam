"""Health checks（liveness / readiness）。

健康檢查不洩漏內部資訊（A05 / A09）：
- /healthz：只回 ok（200）
- /readyz：DB 可連 + Redis 可 ping，否則 503；不回傳細節
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status
from redis.asyncio import Redis
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import engine

router = APIRouter()


@router.get("/healthz", include_in_schema=False)
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", include_in_schema=False)
async def readiness(response: Response) -> dict[str, str]:
    settings = get_settings()
    # DB
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "db_unavailable"}
    # Redis
    redis = Redis.from_url(settings.redis_url)
    try:
        pong = await redis.ping()
        if not pong:
            raise RuntimeError("ping returned false")
    except Exception:  # noqa: BLE001
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "redis_unavailable"}
    finally:
        await redis.aclose()
    return {"status": "ready"}
