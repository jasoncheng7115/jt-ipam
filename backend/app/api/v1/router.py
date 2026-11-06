"""Aggregator for /api/v1/."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import (
    addresses,
    adguard,
    advanced,
    ai,
    ai_audit,
    anomaly,
    api_tokens,
    audit,
    auth,
    bmc_console,
    cert_agents,
    cert_push,
    certificates,
    checkpoint,
    checkpoint_gaia,
    circuit_detail,
    client_diag,
    custom_fields,
    customers,
    dashboard,
    device_groups,
    device_import,
    devices,
    dhcp,
    dhcp_standalone,
    dns,
    domains,
    esxi,
    event_rules,
    export_events,
    firewall,
    fortigate,
    hyperv,
    import_external,
    investigate,
    ip_changes,
    ip_identify,
    ip_path,
    ip_ranges,
    ip_requests,
    isoinsight,
    jump_hosts,
    librenms,
    locations,
    macs,
    maintenance,
    migration,
    mikrotik,
    nat,
    notifications,
    novnc_console,
    ocs,
    openstack,
    oui,
    paloalto,
    pfsense,
    physical,
    plugins,
    preferences,
    rack_diagram,
    rdp_console,
    reports,
    rustdesk,
    rustdesk_agent,
    rustdesk_console,
    scan,
    scan_agent_relay,
    scan_agents,
    search,
    sections,
    sftp_console,
    snmp,
    ssh_console,
    ssh_credentials,
    sso,
    subnets,
    system_logs,
    technitium,
    tools,
    topology,
    users,
    virt,
    vlans,
    vnc_console,
    vrfs,
    warroom,
    wazuh,
    windows_dhcp,
    zabbix,
)
from app.api.v1.endpoints import (
    audit_admin as audit_admin_ep,
)
from app.api.v1.endpoints import (
    background_tasks as bg_tasks_endpoint,
)
from app.api.v1.endpoints import (
    change_impact as change_impact_ep,
)
from app.api.v1.endpoints import (
    change_impact_services as change_impact_services_ep,
)
from app.api.v1.endpoints import (
    external_dns as external_dns_ep,
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
from app.api.v1.endpoints import (
    system_transfer as system_transfer_ep,
)

api_v1_router = APIRouter()
api_v1_router.include_router(auth.router)
api_v1_router.include_router(sso.router)
api_v1_router.include_router(api_tokens.router)
api_v1_router.include_router(preferences.router)
api_v1_router.include_router(dashboard.router)
api_v1_router.include_router(sections.router)
api_v1_router.include_router(subnets.router)
api_v1_router.include_router(ip_ranges.router)
api_v1_router.include_router(system_logs.router)
api_v1_router.include_router(addresses.router)
api_v1_router.include_router(ip_identify.router)
api_v1_router.include_router(ip_identify.ip_router)
api_v1_router.include_router(ssh_console.router)
api_v1_router.include_router(sftp_console.router)
api_v1_router.include_router(ssh_credentials.router)
api_v1_router.include_router(jump_hosts.router)
api_v1_router.include_router(rdp_console.router)
api_v1_router.include_router(vnc_console.router)
api_v1_router.include_router(novnc_console.router)
api_v1_router.include_router(bmc_console.router)
api_v1_router.include_router(vlans.router)
api_v1_router.include_router(vrfs.router)
# device_import 要在 devices 之前：`/devices/import-template` 不能被 `/devices/{device_id}` 吃掉
api_v1_router.include_router(device_import.router)
api_v1_router.include_router(devices.router)
api_v1_router.include_router(device_groups.router)
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
api_v1_router.include_router(rack_diagram.admin_router)
api_v1_router.include_router(migration.router)
api_v1_router.include_router(ai_audit.router)
api_v1_router.include_router(investigate.router)
api_v1_router.include_router(esxi.router)
api_v1_router.include_router(hyperv.router)
api_v1_router.include_router(openstack.router)
api_v1_router.include_router(openstack.read_router)
api_v1_router.include_router(warroom.router)
api_v1_router.include_router(warroom.admin_router)
api_v1_router.include_router(import_external.router)
api_v1_router.include_router(scan_agents.router)
api_v1_router.include_router(scan_agent_relay.router)
api_v1_router.include_router(certificates.router)
api_v1_router.include_router(cert_agents.router)
api_v1_router.include_router(cert_push.router)
api_v1_router.include_router(dns.router)
api_v1_router.include_router(librenms.router)
# 外部 DNS 解析（/system/dns-resolvers 與 /anomalies/attack-surface/external-dns）
api_v1_router.include_router(external_dns_ep.router)
api_v1_router.include_router(external_dns_ep.admin_router)
api_v1_router.include_router(anomaly.router)
api_v1_router.include_router(ai.router)
api_v1_router.include_router(advanced.router)
api_v1_router.include_router(circuit_detail.router)
api_v1_router.include_router(circuit_detail.device_router)
api_v1_router.include_router(domains.router)
api_v1_router.include_router(ip_path.router)
api_v1_router.include_router(maintenance.router)
api_v1_router.include_router(virt.router)
api_v1_router.include_router(physical.router)
api_v1_router.include_router(topology.router)
api_v1_router.include_router(plugins.router)
api_v1_router.include_router(firewall.router)
api_v1_router.include_router(dhcp.router)
api_v1_router.include_router(pfsense.router)
api_v1_router.include_router(pfsense.view_router)
api_v1_router.include_router(event_rules.router)
api_v1_router.include_router(fortigate.router)
api_v1_router.include_router(paloalto.router)
api_v1_router.include_router(macs.router)
api_v1_router.include_router(mikrotik.router)
api_v1_router.include_router(fortigate.view_router)
api_v1_router.include_router(paloalto.view_router)
api_v1_router.include_router(mikrotik.view_router)
api_v1_router.include_router(ocs.router)
api_v1_router.include_router(ocs.view_router)
api_v1_router.include_router(wazuh.router)
api_v1_router.include_router(zabbix.router)
api_v1_router.include_router(zabbix.view_router)
api_v1_router.include_router(windows_dhcp.router)
api_v1_router.include_router(dhcp_standalone.kea_router)
api_v1_router.include_router(dhcp_standalone.isc_router)
api_v1_router.include_router(technitium.router)
api_v1_router.include_router(checkpoint.router)
api_v1_router.include_router(reports.router)
api_v1_router.include_router(export_events.router)
api_v1_router.include_router(checkpoint_gaia.router)
api_v1_router.include_router(isoinsight.router)
api_v1_router.include_router(snmp.router)
api_v1_router.include_router(rustdesk.router)
api_v1_router.include_router(rustdesk_agent.router)
api_v1_router.include_router(rustdesk_console.router)
api_v1_router.include_router(audit.router)
api_v1_router.include_router(users.router)
api_v1_router.include_router(bg_tasks_endpoint.router)
api_v1_router.include_router(adguard.router)
api_v1_router.include_router(system_settings_ep.router)
api_v1_router.include_router(system_settings_ep.public_router)
api_v1_router.include_router(system_settings_ep.view_router)
api_v1_router.include_router(system_transfer_ep.router)
api_v1_router.include_router(client_diag.router)
api_v1_router.include_router(graylog_dsv_ep.admin_router)
api_v1_router.include_router(graylog_dsv_ep.public_router)
api_v1_router.include_router(ldap_admin_ep.admin_router)
api_v1_router.include_router(audit_admin_ep.admin_router)
api_v1_router.include_router(change_impact_ep.router)
api_v1_router.include_router(change_impact_services_ep.router)

# Phase 3 [DONE] Tenancy/Contacts/ASN/Circuits/Wireless、Virtualization/Proxmox、
#           Cabling/Power/VPN、Topology、OIDC SSO（SAML stub）
# Phase 4 [DONE] MCP Server、本地 LLM 自然語言查詢、Plugin 機制
# Phase 4 範圍縮減（不做）：Zimbra/Odoo/Ansible/Terraform/HA
