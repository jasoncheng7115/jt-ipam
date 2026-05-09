"""Aggregator for /api/v1/."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import (
    addresses,
    api_tokens,
    auth,
    custom_fields,
    devices,
    locations,
    nat,
    notifications,
    scan,
    sections,
    subnets,
    tools,
    vlans,
    vrfs,
)

api_v1_router = APIRouter()
api_v1_router.include_router(auth.router)
api_v1_router.include_router(api_tokens.router)
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

# Phase 1 待補：users(管理), groups, scan_agents, search
