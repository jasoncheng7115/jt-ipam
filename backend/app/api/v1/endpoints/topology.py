"""拓樸圖 endpoint：回傳 Cytoscape.js 可直接吃的 nodes/edges。"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_global_read
from app.core.db import get_session
from app.services.topology import build_topology

router = APIRouter(prefix="/topology", tags=["topology"])


@router.get("", dependencies=[Depends(require_global_read)])
async def topology(
    _user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    location_id: uuid.UUID | None = Query(None),
    subnet_id: list[uuid.UUID] | None = Query(None, description="只顯示這些子網路（可複選）"),
    include_wireless: bool = Query(True),
    include_vpn: bool = Query(True),
    include_l3: bool = Query(True),
    online_only: bool = Query(False, description="只畫上線的裝置"),
) -> dict[str, Any]:
    return await build_topology(
        session,
        user=_user,
        location_id=location_id,
        subnet_ids=subnet_id or None,
        include_wireless=include_wireless,
        include_vpn=include_vpn,
        include_l3=include_l3,
        online_only=online_only,
    )
