"""Aggregator for /api/v1/."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import (
    addresses,
    adguard,
    advanced,
    ai,
    anomaly,
    api_tokens,
    audit,
    auth,
    cert_agents,
    certificates,
    custom_fields,
    customers,
    dashboard,
    devices,
    dns,
    firewall,
    import_external,
    ip_changes,
    ip_requests,
    librenms,
    locations,
    migration,
    nat,
    notifications,
    oui,
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
    system_logs,
    tools,
    topology,
    users,
    virt,
    vlans,
    vrfs,
    wazuh,
)
from app.api.v1.endpoints import (
    audit_admin as audit_admin_ep,
)
from app.api.v1.endpoints import (
    background_tasks as bg_tasks_endpoint,
)
from app.api.v1.endpoints import (
    graylog_dsv as graylog_dsv_ep,
)
from app.api.v1.endpoints import (
    ldap_admin as ldap_admin_ep,
)
from app.api.v1.endpoints import (
    system_settings as system_settings_ep,
)

api_v1_router = APIRouter()
api_v1_router.include_router(auth.router)
api_v1_router.include_router(sso.router)
api_v1_router.include_router(api_tokens.router)
api_v1_router.include_router(preferences.router)
api_v1_router.include_router(dashboard.router)
api_v1_router.include_router(sections.router)
api_v1_router.include_router(subnets.router)
api_v1_router.include_router(system_logs.router)
api_v1_router.include_router(addresses.router)
api_v1_router.include_router(vlans.router)
api_v1_router.include_router(vrfs.router)
api_v1_router.include_router(devices.router)
api_v1_router.include_router(locations.router)
api_v1_router.include_router(nat.router)
api_v1_router.include_router(scan.router)
api_v1_router.include_router(tools.router)
api_v1_router.include_router(custom_fields.router)
api_v1_router.include_router(customers.router)
api_v1_router.include_router(notifications.router)
api_v1_router.include_router(oui.router)
api_v1_router.include_router(search.router)
api_v1_router.include_router(ip_requests.router)
api_v1_router.include_router(ip_changes.router)
api_v1_router.include_router(rack_diagram.router)
api_v1_router.include_router(migration.router)
api_v1_router.include_router(import_external.router)
api_v1_router.include_router(scan_agents.router)
api_v1_router.include_router(certificates.router)
api_v1_router.include_router(cert_agents.router)
api_v1_router.include_router(dns.router)
api_v1_router.include_router(librenms.router)
api_v1_router.include_router(anomaly.router)
api_v1_router.include_router(ai.router)
api_v1_router.include_router(advanced.router)
api_v1_router.include_router(virt.router)
api_v1_router.include_router(physical.router)
api_v1_router.include_router(topology.router)
api_v1_router.include_router(plugins.router)
api_v1_router.include_router(firewall.router)
api_v1_router.include_router(wazuh.router)
api_v1_router.include_router(audit.router)
api_v1_router.include_router(users.router)
api_v1_router.include_router(bg_tasks_endpoint.router)
api_v1_router.include_router(adguard.router)
api_v1_router.include_router(system_settings_ep.router)
api_v1_router.include_router(system_settings_ep.public_router)
api_v1_router.include_router(graylog_dsv_ep.admin_router)
api_v1_router.include_router(graylog_dsv_ep.public_router)
api_v1_router.include_router(ldap_admin_ep.admin_router)
api_v1_router.include_router(audit_admin_ep.admin_router)

# Phase 3 [DONE] Tenancy/Contacts/ASN/Circuits/Wireless、Virtualization/Proxmox、
#           Cabling/Power/VPN、Topology、OIDC SSO（SAML stub）
# Phase 4 [DONE] MCP Server、本地 LLM 自然語言查詢、Plugin 機制
# Phase 4 範圍縮減（不做）：Zimbra/Odoo/Ansible/Terraform/HA
