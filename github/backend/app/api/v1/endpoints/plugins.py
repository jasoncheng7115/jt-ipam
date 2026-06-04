"""Plugin 管理 endpoint：列出目前載入的 plugin（含失敗）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.v1.dependencies import require_admin
from app.plugins import list_plugins

router = APIRouter(prefix="/plugins", tags=["plugins"])


@router.get("", dependencies=[Depends(require_admin)])
async def list_loaded_plugins() -> dict:  # type: ignore[type-arg]
    plugins = list_plugins()
    return {
        "count": len(plugins),
        "plugins": [
            {
                "name": p.name,
                "version": p.version,
                "description": p.description,
                "error": p.error,
            }
            for p in plugins
        ],
    }
