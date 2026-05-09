"""Aggregator for /api/v1/."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import addresses, auth, sections, subnets

api_v1_router = APIRouter()
api_v1_router.include_router(auth.router)
api_v1_router.include_router(sections.router)
api_v1_router.include_router(subnets.router)
api_v1_router.include_router(addresses.router)

# Phase 1 待補：vlans, vrfs, devices, racks, locations, nat,
# users(管理), groups, api_tokens, scan_agents, custom_fields, search, calculator
