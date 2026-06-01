# Changelog

All notable changes to this project are documented here. The format is loosely
based on [Keep a Changelog](https://keepachangelog.com/); versions track
`frontend/package.json` / `backend/app/version.py`.

## [0.4.31] — 2026-06-01

### Added
- NAT/Circuit field expansion (migration 0053): NAT gains the full OPNsense
  rule set (disabled / no-RDR / IP-version / source·dest invert / port ranges /
  log / category / NAT-reflection / pool / filter-rule / alias references);
  Circuit gains up/down bandwidth. OPNsense sync populates them.
- Device detail: Wazuh agent + Proxmox VM panels (matched by IP); edit button.
- Tools: DNS/mail diagnostics (MX/SPF/DKIM/DMARC) + data-center power calculators.
- Rack diagram → draw.io-editable SVG export; room pinning; quick filter across
  list pages; self AI chat-history in the user menu; permissions overview.
- Topology: zoom/fit buttons, clickable legend toggles, default-to-pinned-subnets.

### Changed
- 機房 = 地點 (nav relabelled「機房 / 地點」); 站對站 VPN; NAT alias references are
  clickable to the Firewall page.
- VPN WireGuard pairing cross-fills each side's real WAN IP (was showing LAN).
- Floor plan: fixed-size handles, 0/90/180/270 rotation snap, toolbar below canvas.
- Global card header band + dark-mode table/card depth.

### Fixed
- Topology subnet filter dropped name-/ARP-derived devices.
- Many i18n/terminology/button-height fixes (協定, 配電盤/饋線/插座, Notifications…).

## [0.4.30] — 2026-06-01

### Added
- Table export (CSV / Markdown / PDF / ODS / ODT) on the admin tables: Users,
 Audit, DNS, LibreNMS, Wazuh (instances/agents/missing), Firewall
 (firewalls/mappings/rules), and Scan Agents.

### Changed
- The global **map provider** selector moved from the Locations page to
 **Settings → System** (admin-only). A non-admin `GET /system/map-provider`
 endpoint now lets the Locations map preview render for all users while the
 `PUT` stays admin-gated.

### Fixed
- Data-table column headers no longer wrap: a global rule keeps short CJK titles
 (e.g. 子網路) on one line regardless of the sort-arrow spacing.

## [0.4.x] — 2026-05/06

### Added
- **Object-level RBAC** across 7 object types (customer / section / subnet / IP /
 device / rack / location) with hierarchical cascade, per-type "All" wildcard,
 and 5 built-in roles (System Administrator, Read-only Viewer, Network
 Operator, Auditor, Department Administrator). Visibility is enforced on list
 endpoints, global search, the topology graph, and every selector.
- **Permission management UI** — principal (user/group) picker, grant table, and
 add-grant flow with "All"/specific multi-select and read/write/admin levels.
- **MCP server** — expanded toolset with both stdio and Streamable HTTP
 transports; mounted under `/api/mcp` so it is reachable through the nginx
 reverse proxy. Write tools self-gate on admin.
- **Customers** (managing units) attached to sections/subnets/devices/IPs, and an
 IEEE **OUI vendor** table with a monthly refresh timer.
- **AI chat** improvements — persistent history, per-message timestamps, model &
 elapsed-time display, and a model-parameters tooltip (family / parameter size /
 quantization / context length via Ollama `/api/show`).
- **Global search** now covers VPN, customers, racks, locations, NAT, DNS
 records, firewalls, and IP requests — all RBAC-filtered.
- Floating sticky horizontal scrollbar on wide tables; premium light/dark theme;
 Cabling / Power / VPN split into three independent pages.

### Changed
- prod database migrated from `SQL_ASCII` to `UTF8`.
- Terminology fixes for Taiwanese usage (e.g. 首碼 instead of 前綴).

### Fixed
- Numerous QA-driven UI fixes (column widths, dashboard widget styling, text
 selection contrast in light mode, topology node detail popovers, tooltip
 clipping).

## [0.3] — Phase 1–3 baseline

- phpIPAM parity (Sections/Subnets/IPs/VLANs/VRFs/NAT/Devices/Racks/Locations/
 IP-Requests), TOTP + API tokens, forced TLS.
- Multi-vendor DNS, deep LibreNMS integration, anomaly detection, SHA-256 audit
 chain, pgvector semantic search.
- Tenancy/Cabling/Power/VPN/Virtualization, Proxmox sync, Cytoscape topology,
 OIDC/SAML SSO, OPNsense firewall sync, Wazuh agent inventory.
