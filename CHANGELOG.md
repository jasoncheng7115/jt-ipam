# Changelog

All notable changes to this project are documented here. The format is loosely
based on [Keep a Changelog](https://keepachangelog.com/); versions track
`frontend/package.json` / `backend/app/version.py`.

## [0.4.92] — 2026-06-06

### Added
- Advanced (tenancy / circuits / contacts) and Power pages: multi-table pages are
  split into **inner tabs** (matching the firewall rules/aliases style). Every
  Advanced / Power / Virtualization table now has a **unified toolbar**:
  filter + refresh + create + column picker + export.
- Racks: a **merged single-card** view mode (all racks of a room in one card).
- Topology: **persistent edge-selection highlight** with a two-end card (IP /
  device / port, shown even when only one end is known); multi-subnet centers
  spread apart so dual-homed devices no longer overlap.
- Circuits: **fixed-IP fields** (IP / gateway / netmask / DNS) + device link
  (migration 0067); built-in **circuit types** with add/delete management.
- Scan agents: show **last source IP** (migration 0068).
- Device detail: one-click **link-IP-mapping** button in the IP list.
- Graylog DSV settings promoted to a standalone **Graylog integration** page
  under Wazuh, with a wiring guide.

### Changed
- nginx API rate-limit raised (100 → 1200 r/m, burst 20 → 80) to stop spurious
  "connection failed" on API-heavy pages.
- IP-address editor: green save buttons; save/cancel returns to the IP detail page.

### Fixed
- **Stale-bundle navigation hang**: the router auto-reloads once when a code-split
  chunk fails to load, and the build now **retains hashed assets across deploys**
  (pruning ones older than 7 days), so tabs opened before a deploy no longer 404
  their chunks and hang on navigation.
- Contact-groups table reused tenant-group columns (hit the wrong API) — fixed.
- ruff: corrected noqa rule code in `audit.py`, import ordering in `sso.py`.

### Security / Chore
- **vitest** dev dependency bumped 3.2.6 → 4.1.8 (resolves the critical
  "Vitest UI server arbitrary file read/exec" advisory; dev-only, not in the
  production bundle).
- Bilingual docs added: `CHANGELOG_zh-TW.md`, `SECURITY_zh-TW.md`, and
  `TEST_CHECKLIST.md` (English) alongside `TEST_CHECKLIST_zh-TW.md`.
- All `scripts/*.sh` are now English-only (comments and messages); behavior
  unchanged.

## [0.4.79] — 2026-06-06

### Added
- **SSO web UI configuration**: OIDC and SAML are now DB-backed with an admin web
  UI (env defaults + DB override, AES-GCM encrypted secrets); LDAP management page.
- **Device power ports ↔ PDU outlets** modeling (NetBox PowerPort style,
  migration 0066).
- **Version auto-reload**: `dist/version.json` polling prompts a reload when a new
  build is deployed (the root cause behind "my save didn't take" stale-bundle
  reports).
- **Full bilingual documentation**: README and all `docs/*.md` in English and
  zh-TW; GitHub Pages feature-map tree.

### Changed
- Universal table **column picker + multi-format export** everywhere (incl.
  zero-dependency `.ods` / `.odt`, `.xlsx`, PDF).
- Generic pinning moved to backend preferences (migration 0065); rack front/rear
  face support.

## [0.4.61] — 2026-06-05

### Added
- **RBAC convergence for global infrastructure data**: `require_global_read` /
  `has_global_read` / `can_edit`. Lists, details, search, dashboard aggregates,
  counts and trends all scale to the user's visibility; action buttons grey out
  by capability.
- **Cable Trace** (NetBox-style multi-hop, migration 0063): a `device_ports`
  table with bridge → NIC → external-device traversal.
- Rack **half-U** support and front/rear visualization; device-detail rack
  diagram highlighting the current device.

### Changed
- AI / MCP: 100-question test-and-fix pass; tool list filtered by permission;
  a **change-confirm gate** before any write; cursor pagination + "next batch"
  continuation for large results.
- Archived subnets also hide their IPs (lists + search).

### Fixed
- AI chat / topology **RBAC leaks** closed (zero-permission accounts could
  previously query IPs/devices and view the topology).

## [0.4.43] — 2026-06-04

### Added
- **Device-to-device cabling / port** connection management; cabling and power
  resources gain full CRUD editing.
- LDAP management page; AI change-confirmation gate; Graylog DSV lookup
  (plain HTTP on port 8088).
- Dashboard charts; device detail shows its rack diagram.

### Changed
- Proxmox connection settings moved to the admin area; node network interfaces
  (bridge / bond / NIC) are pulled and made traceable.

### Fixed
- Hostname sync thrash (repeated re-sync); left half-U device save 500;
  audit `object_id` must be a UUID (`append_audit` needs `request_id`).

## [0.4.32] — 2026-06-02

### Added
- Device install direction (migration 0057): a device can be marked as mounted
  on the **rack front or rear**; the rack presentation-face field was removed.
- Version-info admin page: current version + Python and key backend package
  versions, with a button to check the latest version on GitHub.
- Locations list shows **rack count / device count** columns; subnets list shows
  a **pinned** column with one-click toggle.
- IP-address editor offers a one-click **link to a matching device** (mirror of
  the device→IP link button).
- Common rack width/depth **preset chips** in the rack form; the rack page
  auto-selects a pinned location on entry.
- Table export can fetch the **full dataset** (not just the visible page) on
  remote-paginated lists (Addresses / Audit / Users / Devices).
- GitHub Actions CI now actually runs and gates: frontend (eslint flat config /
  vue-tsc / vitest / build) and backend (alembic / pytest / bandit / gitleaks).
- Device ↔ IP linking: the device list resolves an effective management IP
  (primary_ip → LibreNMS mgmt IP → name-is-IP), renders it as a clickable link
  when a matching address object exists, and offers a one-click "link" button
  when a same-IP address object exists but isn't yet attached to the device.
  The IP-address editor can pick its device, and `/devices/{id}/relations`
  exposes the relation chain.
- Scan-agent auto-discovery: an agent push for an unknown IP inside one of its
  assigned, scan-enabled subnets auto-creates the address object (with its own
  descriptive note, not copied from phpIPAM); overlapping ranges are matched by
  longest-prefix within the agent's own subnets only.
- Per-source precedence is now split into independent cards: hostname,
  device-name, ARP/MAC, and a new **device model** precedence (manual is highest
  and cannot be disabled in each).
- Address search gains an **exact-match** toggle (IP / hostname must equal the
  query, so `192.168.1.1` no longer also matches `192.168.1.1xx`).
- Subnets may explicitly **allow overlap** (e.g. same CIDR under a different
  tenant / location) via `allow_overlap`.
- OPNsense alias sync: aliases are pulled into `opnsense_synced_aliases`.
- Dashboard: pinned locations / pinned racks cards; rack page can pick a device
  into an empty U-slot via a mini-rack picker.
- GitHub Pages site: project logo + favicon, inline SVG icons (no emoji),
  corrected positioning (not "built on phpIPAM").

### Changed
- Device naming from LibreNMS now prefers **sysName** over hostname (which is
  often just an IP); device-name precedence default reordered accordingly, and
  model is backfilled from LibreNMS hardware.
- DNS sync applies one deterministic hostname per IP (sorted), fixing name
  flapping when an IP has multiple A records.
- Hostname precedence now includes the Wazuh and AdGuard sources in the order
  list (they were observed but missing from the precedence UI).
- Floor-plan racks rotate to **any angle** (soft-snap to orthogonal), not just
  0/90/180/270; footprint scales by real width/depth.
- VPN tunnel pairing is labelled by method (migration 0058): WireGuard pubkey
  (reliable) vs IPsec endpoint-match (best-effort); IPsec matching also maps each
  firewall's own tunnel local endpoints to raise hit rate.
- Reworded taglines from "next-generation" to "self-hosted, integration-focused"
  across Pages / README / SPEC / app; Pages emphasizes OSS integration + phpIPAM
  import, adds accent colors, and splits Install / Upgrade / Uninstall.

### Security
- OPNsense config.xml parsed via **defusedxml** (XXE); subnet overlap/master SQL
  fully parameterized; bandit clean at medium+ severity.

### Fixed
- Subnet-pin persistence survived a refresh inconsistently — pins now persist
  synchronously on toggle instead of via a component-scoped watcher.
- GeoIP database download switched to the legacy `geoip_download` endpoint
  (the new permalink 302'd to S3 and rejected the forwarded auth header).
- Floor-plan upload 500 (uploads dir ownership); audit_logs doc table row
  broken by unescaped SQL `||` operators.

### Tests
- Added regression coverage: model precedence, subnet overlap, exact IP search,
  scan auto-discovery, hostname-source clearing, hostname-order completeness,
  device IP-matching flags, device/address relation chains, OPNsense alias
  parse+sync, LibreNMS device link, DNS pull naming, Wazuh/Proxmox sync,
  IPsec pairing, version endpoint, rack-face/location-counts, and a frontend
  usePinned unit test.

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
