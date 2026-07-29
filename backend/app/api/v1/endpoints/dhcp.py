"""DHCP 發放範圍（來源中立）。

各 DHCP 來源（OPNsense / pfSense / Windows DHCP）各自同步、各自寫入 dhcp_pool_ranges；
這裡只負責「不分來源」把範圍讀出來，給 IP 詳情／清單標示「在 DHCP 範圍內」用。

分類上屬「全域基礎設施資料」→ require_global_read（見 CLAUDE.md 權限模型第 2 類）。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import require_global_read
from app.core.db import get_session

router = APIRouter(tags=["dhcp"], dependencies=[Depends(require_global_read)])


@router.get("/dhcp-ranges")
async def list_dhcp_ranges(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[dict[str, Any]]:
    """所有 DHCP 來源同步回來的發放範圍。"""
    from app.models.dhcp import DHCPPoolRange

    rows = (await session.execute(
        select(DHCPPoolRange).order_by(DHCPPoolRange.source_type, DHCPPoolRange.start_ip)
    )).scalars().all()
    return [{
        "id": str(r.id),
        "source_type": r.source_type,          # opnsense / pfsense / windows_dhcp
        "source_id": str(r.source_id),
        "source_name": r.source_name,          # 顯示用（例：某台防火牆／某台 Windows DHCP）
        "subnet_cidr": r.subnet_cidr,
        "start_ip": r.start_ip,
        "end_ip": r.end_ip,
        "family": r.family,
        "source": r.source,                    # DHCP 引擎：kea / isc / windows
    } for r in rows]
