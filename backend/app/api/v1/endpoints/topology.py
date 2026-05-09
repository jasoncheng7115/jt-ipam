"""拓樸圖 endpoint：回傳 Cytoscape.js 可直接吃的 nodes/edges。"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser
from app.core.db import get_session
from app.services.topology import build_topology

router = APIRouter(prefix="/topology", tags=["topology"])


@router.get("")
async def topology(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    location_id: uuid.UUID | None = Query(None),
    include_wireless: bool = Query(True),
    include_vpn: bool = Query(True),
) -> dict[str, Any]:
    return await build_topology(
        session,
        location_id=location_id,
        include_wireless=include_wireless,
        include_vpn=include_vpn,
    )
