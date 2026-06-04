#!/usr/bin/env python3
"""GeoIP mmdb 排程更新：由 jt-ipam-geoip-refresh.timer 每日觸發。

依系統設定的 auto_update + frequency 判斷是否到期，到期才向 MaxMind 下載。
（與 scripts/oui_refresh.py 同套路：自帶 async session，讀 backend.env。）
"""
from __future__ import annotations

import asyncio
import sys


async def _main() -> int:
    from app.core.db import SessionLocal
    from app.services.geoip import maybe_scheduled_update

    async with SessionLocal() as session:
        result = await maybe_scheduled_update(session)
    print(f"[geoip_refresh] {result}")
    # 有錯誤回非零，讓 systemd 標記失敗
    if isinstance(result, dict):
        if result.get("error"):
            return 1
        for r in (result.get("results") or {}).values():
            if isinstance(r, dict) and not r.get("ok"):
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
