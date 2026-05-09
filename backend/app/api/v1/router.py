"""Aggregator for /api/v1/."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import (
    addresses,
    api_tokens,
    auth,
    custom_fields,
    dashboard,
    devices,
    import_external,
    ip_requests,
    locations,
    migration,
    nat,
    notifications,
    preferences,
    rack_diagram,
    scan,
    scan_agents,
    search,
    sections,
    subnets,
    tools,
    vlans,
    vrfs,
)

api_v1_router = APIRouter()
api_v1_router.include_router(auth.router)
api_v1_router.include_router(api_tokens.router)
api_v1_router.include_router(preferences.router)
api_v1_router.include_router(dashboard.router)
api_v1_router.include_router(sections.router)
api_v1_router.include_router(subnets.router)
api_v1_router.include_router(addresses.router)
api_v1_router.include_router(vlans.router)
api_v1_router.include_router(vrfs.router)
api_v1_router.include_router(devices.router)
api_v1_router.include_router(locations.router)
api_v1_router.include_router(nat.router)
api_v1_router.include_router(scan.router)
api_v1_router.include_router(tools.router)
api_v1_router.include_router(custom_fields.router)
api_v1_router.include_router(notifications.router)
api_v1_router.include_router(search.router)
api_v1_router.include_router(ip_requests.router)
api_v1_router.include_router(rack_diagram.router)
api_v1_router.include_router(migration.router)
api_v1_router.include_router(import_external.router)
api_v1_router.include_router(scan_agents.router)

# Phase 1 餘項：users(管理) / groups（前端管理頁；Phase 2 進行）
