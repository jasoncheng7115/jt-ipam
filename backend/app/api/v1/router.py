"""Aggregator for /api/v1/."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import (
    addresses,
    advanced,
    ai,
    anomaly,
    api_tokens,
    auth,
    custom_fields,
    dashboard,
    devices,
    dns,
    import_external,
    ip_requests,
    librenms,
    locations,
    migration,
    nat,
    notifications,
    physical,
    plugins,
    preferences,
    rack_diagram,
    scan,
    scan_agents,
    search,
    sections,
    sso,
    subnets,
    tools,
    topology,
    virt,
    vlans,
    vrfs,
)

api_v1_router = APIRouter()
api_v1_router.include_router(auth.router)
api_v1_router.include_router(sso.router)
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
api_v1_router.include_router(dns.router)
api_v1_router.include_router(librenms.router)
api_v1_router.include_router(anomaly.router)
api_v1_router.include_router(ai.router)
api_v1_router.include_router(advanced.router)
api_v1_router.include_router(virt.router)
api_v1_router.include_router(physical.router)
api_v1_router.include_router(topology.router)
api_v1_router.include_router(plugins.router)

# Phase 3 ✅ Tenancy/Contacts/ASN/Circuits/Wireless、Virtualization/Proxmox、
#           Cabling/Power/VPN、Topology、OIDC SSO（SAML stub）
# Phase 4 ✅ MCP Server、本地 LLM 自然語言查詢、Plugin 機制
# Phase 4 範圍縮減（不做）：Zimbra/Odoo/Ansible/Terraform/HA
