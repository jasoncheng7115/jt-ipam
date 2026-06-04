"""rate_limit_enabled=false 時 check_rate_limit 直接放行，不碰 Redis。

迴歸：測試共用 prod Redis、所有請求都來自 127.0.0.1，限流 bucket 會在測試間
累積，超過 auth 10/min 後造成後續 e2e 測試連鎖 429/401 失敗。conftest 預設關閉
限流即可避免；本測試確保 kill-switch 確實短路（且不需要 Redis 連線）。
"""

from __future__ import annotations

from app.core.config import get_settings
from app.core.rate_limit import check_rate_limit


async def test_rate_limit_disabled_short_circuits_without_redis():
    # conftest 已設定 RATE_LIMIT_ENABLED=false
    assert get_settings().rate_limit_enabled is False
    # 遠超過 auth 10/min；若有碰 Redis 或計數會 raise 429，這裡應全部放行
    for _ in range(50):
        await check_rate_limit(bucket="rl:auth:ip:127.0.0.1", rate="10/minute")
