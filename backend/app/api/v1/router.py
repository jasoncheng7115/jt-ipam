"""Aggregator for /api/v1/."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import sections

api_v1_router = APIRouter()
api_v1_router.include_router(sections.router)

# Phase 1 待補：subnets, addresses, vlans, vrfs, devices, racks, locations, nat,
# users, groups, api_tokens, scan_agents, custom_fields, search, calculator
