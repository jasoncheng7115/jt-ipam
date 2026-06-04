"""Redis-backed rate limit（OWASP A06 / A07）。

簡單 sliding window；給認證、寫入、API token 三個典型 case 用。
未來可換 slowapi / fastapi-limiter；先寫成最小實作避免引入額外依賴。
"""

from __future__ import annotations

import time
from typing import Final

from fastapi import HTTPException, Request, status
from redis.asyncio import Redis

from app.core.config import get_settings

_redis: Redis[bytes] | None = None

_LUA_SLIDING_WINDOW: Final[str] = """
-- KEYS[1] = bucket key
-- ARGV[1] = now (ms)
-- ARGV[2] = window (ms)
-- ARGV[3] = limit
local now    = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit  = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, now - window)
local count = redis.call('ZCARD', KEYS[1])
if count >= limit then
    return {0, redis.call('PTTL', KEYS[1])}
end
redis.call('ZADD', KEYS[1], now, now .. ':' .. math.random())
redis.call('PEXPIRE', KEYS[1], window)
return {1, count + 1}
"""


def _redis_client() -> Redis[bytes]:
    global _redis
    if _redis is None:
        _redis = Redis.from_url(get_settings().redis_url, decode_responses=False)
    return _redis


def _parse_rate(rate: str) -> tuple[int, int]:
    """e.g. "100/minute" -> (100, 60_000 ms)."""
    n, period = rate.split("/", 1)
    period_ms = {
        "second": 1000,
        "sec": 1000,
        "minute": 60_000,
        "min": 60_000,
        "hour": 3_600_000,
        "day": 86_400_000,
    }[period.strip().lower()]
    return int(n), period_ms


async def check_rate_limit(
    *,
    bucket: str,
    rate: str,
) -> None:
    """超出限制時 raise 429。"""
    # 測試環境（或 ops kill-switch）關閉限流：避免共用 Redis bucket 在測試間累積、
    # 也避免測試污染 prod 的 rl:* bucket。預設仍為開啟。
    if not get_settings().rate_limit_enabled:
        return
    limit, window = _parse_rate(rate)
    now_ms = int(time.time() * 1000)
    client = _redis_client()
    result = await client.eval(  # type: ignore[no-untyped-call]
        _LUA_SLIDING_WINDOW,
        1,
        bucket,
        now_ms,
        window,
        limit,
    )
    allowed, _ = result
    if int(allowed) == 0:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests",
            headers={"Retry-After": str(int(window / 1000))},
        )


async def limit_per_ip(request: Request, *, name: str = "default") -> None:
    settings = get_settings()
    rate_map = {
        "default": settings.rate_limit_default,
        "auth": settings.rate_limit_auth,
        "api_token": settings.rate_limit_api_token,
        "ai": settings.rate_limit_ai,
    }
    rate = rate_map.get(name, settings.rate_limit_default)
    ip = request.client.host if request.client else "unknown"
    await check_rate_limit(bucket=f"rl:{name}:ip:{ip}", rate=rate)
