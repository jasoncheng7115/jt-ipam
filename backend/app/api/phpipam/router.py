"""phpIPAM v1.7 API 相容層。

詳見 docs/PHPIPAM_API_MAPPING.md。Phase 1 完成 user / sections / subnets /
addresses 的核心唯讀路徑與 token 換取流程。寫入路徑（POST/PATCH/DELETE）
建議走現代 REST API（/api/v1/）；老腳本若需要寫入 phpIPAM compat，後續
版本再補。
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Request

from app.api.phpipam.endpoints import addresses, sections, subnets, user

phpipam_router = APIRouter()
phpipam_router.include_router(user.router)
phpipam_router.include_router(sections.router)
phpipam_router.include_router(subnets.router)
phpipam_router.include_router(addresses.router)


@phpipam_router.get("/{app_id}/", include_in_schema=False)
async def app_root(app_id: str, request: Request) -> dict:
    started = time.perf_counter()
    elapsed = round(time.perf_counter() - started, 4)
    return {
        "code": 200,
        "success": True,
        "data": {
            "app_id": app_id,
            "version": "0.3.0",
            "endpoints": [
                "user",
                "sections",
                "subnets",
                "addresses",
            ],
        },
        "message": "jt-ipam phpIPAM compatibility layer",
        "time": elapsed,
    }
