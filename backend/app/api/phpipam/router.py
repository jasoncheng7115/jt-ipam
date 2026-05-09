"""phpIPAM v1.7 API 相容層（骨架）。

詳見 docs/PHPIPAM_API_MAPPING.md。Phase 1 將完成 user / sections / subnets /
addresses / vlans / vrfs / devices / tools。

回應格式統一包裝為 phpIPAM 風格：
    {"code": int, "success": bool, "data": ..., "message": str, "time": float}
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Request

phpipam_router = APIRouter()


def phpipam_response(
    *,
    data: Any = None,  # noqa: ANN401
    success: bool = True,
    code: int = 200,
    message: str = "",
    started: float | None = None,
) -> dict[str, Any]:
    elapsed = round(time.perf_counter() - started, 4) if started else 0.0
    return {
        "code": code,
        "success": success,
        "data": data,
        "message": message,
        "time": elapsed,
    }


@phpipam_router.get("/{app_id}/", include_in_schema=False)
async def app_root(app_id: str, request: Request) -> dict[str, Any]:
    """phpIPAM 風格 root endpoint — 回 placeholder。Phase 1 補完整功能。"""
    started = time.perf_counter()
    return phpipam_response(
        success=True,
        message=f"jt-ipam phpIPAM compatibility layer; app_id={app_id}",
        data={
            "app_id": app_id,
            "version": "0.3.0",
            "endpoints": ["user", "sections", "subnets", "addresses", "vlans", "vrf", "devices", "tools"],
        },
        started=started,
    )
