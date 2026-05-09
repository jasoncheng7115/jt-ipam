"""Aggregator for /api/v1/."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import (
    addresses,
    api_tokens,
    auth,
    devices,
    locations,
    nat,
    scan,
    sections,
    subnets,
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

# Phase 1 待補：users(管理), groups, scan_agents, custom_fields, search, calculator
