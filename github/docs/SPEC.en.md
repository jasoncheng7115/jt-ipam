# jt-ipam IPAM System Specification

> 繁體中文版：[SPEC.md](SPEC.md)

> Version: v0.3 (full integrated edition)
> Author: Jason Tools Co., Ltd.
> Positioning: a self-hostable, integration-focused modern IPAM, deeply integrating multiple DNS servers, LibreNMS, OPNsense and local AI; offering a phpIPAM-compatible API and a painless migration path (not built on top of phpIPAM)

---

## 0. Design thesis

**Thesis: a modern IPAM in its own right**; the operating experience "references" phpIPAM's familiar terminology and habits to lower the migration barrier, and absorbs NetBox's strengths, but this system is not built on top of phpIPAM nor constrained by its architecture.

| Dimension | Adopted from | Notes |
|------|--------|------|
| Primary data structure | **phpIPAM** | Section → Subnet → IP Address |
| Primary terminology | **phpIPAM** | uses Section, Subnet, Address, not Aggregate/Prefix |
| UI flow | **phpIPAM** | Section tree, Subnet visual block map, IP table |
| Subnet scanning | **phpIPAM** | keeps active scanning (NetBox lacks it) |
| NAT module | **phpIPAM** | 1:1, N:1, Port Forwarding |
| Devices inventory | **phpIPAM** | a simple device list, not NetBox's complex DCIM |
| Locations | **phpIPAM** | Locations module + map |
| RACK | **phpIPAM** | simple U-position management |
| Advanced features (optional) | **NetBox** | Tenancy, Contacts, Cabling, Circuits, L2VPN… opt-in |

**In plain terms**: what a user opens in jt-ipam is the familiar phpIPAM interface (Section tree, Subnet blocks, IP table), just with a more modern UI, better performance, more complete Traditional Chinese, a stronger API, an AI assistant, and deep DNS (read + optional push) / LibreNMS (two-way sync) interop.

---

## 1. Product positioning

### 1.1 Core positioning
- **Fully covers all phpIPAM features**, zero learning cost for phpIPAM veterans
- **Uses a modern tech stack**, solving phpIPAM's legacy performance, UI, and API baggage
- **Reinforces what phpIPAM lacks** (taken from NetBox's design, but kept simple)
- **Deeply integrates multiple DNS servers**: OPNsense Unbound, Windows DNS, BIND 9, PowerDNS (reads forward/reverse status, optional record push; not fully automatic two-way sync)
- **Deeply integrates LibreNMS**: two-way sync, ARP/FDB fetching, live-status complementation, auto-add to monitoring
- **Integrates Jason's existing open-source ecosystem**: Proxmox VE, Wazuh, Graylog, OPNsense, Zimbra, Odoo
- **Built-in local AI**: Ollama, natural-language queries, semantic search
- **Meets Taiwan security-audit needs**: Traditional Chinese UI, Minguo calendar, SHA-256 change auditing

### 1.2 Design philosophy
1. **phpIPAM user experience first**
2. **Advanced features can be disabled** (NetBox-grade modules off by default)
3. **API as a first-class citizen** (full phpIPAM API compatibility, zero changes to old scripts)
4. **No proprietary lock-in** (open-source components, AGPL license)
5. **Performance first** (solves phpIPAM's /8-supernet and IPv6 /48-scale bottlenecks)
6. **Integration over reinvention**: data obtainable from LibreNMS or DNS servers is fetched directly, not re-implemented
7. **Security by Design**: aligned with OWASP Top 10:2025, see `docs/SECURITY.md`

---

## 2. Technical architecture

### 2.1 Tech stack

| Layer | Choice | Notes |
|------|------|------|
| Backend framework | Python 3.12 + FastAPI | async, automatic OpenAPI |
| ORM | SQLAlchemy 2.0 + Alembic | |
| Database | PostgreSQL 16 (native inet/cidr/macaddr) | far outperforms phpIPAM's MySQL |
| Cache/queue | Redis 7 + RQ/Celery | background scanning, event queue |
| Full-text search | PostgreSQL pg_trgm + pgvector | paired with AI semantic search |
| Frontend | Vue 3 + TypeScript + Vite + Naive UI | Traditional-Chinese-friendly, full light/dark theme support |
| Charts | ECharts + Cytoscape.js | topology, rack, ARP/FDB relation graphs |
| API | REST (OpenAPI 3.1) + GraphQL + phpIPAM compatibility layer | three tracks in parallel |
| Auth | local / AD / LDAP / Radius / SAML2 / OIDC | argon2id + TOTP MFA |
| Deployment | systemd + nginx + apt (no containerization); Proxmox LXC template, bare metal | consistent with Jason's existing ops ecosystem |
| HA | PG streaming replication + Redis Sentinel + multi-replica backend | Phase 3+ |

### 2.2 Module architecture

```
jt-ipam/
├── core/              # core: users, groups, permissions, custom fields, audit
│
├── ── phpIPAM primary structure ──
├── sections/          # Section
├── subnets/           # Subnet (IPv4/IPv6, VRF, VLAN, nesting)
├── addresses/         # individual IP Address
├── vlans/             # VLAN management
├── vrfs/              # VRF management
├── nat/               # NAT module (phpIPAM signature)
├── devices/           # device inventory (phpIPAM style)
├── racks/             # rack management
├── locations/         # locations + map
├── ip_requests/       # IP request workflow
├── tools/             # calculator, search, import/export
│
├── ── data-source integration (key) ──
├── scanner/           # in-house active scanning (ICMP/SNMP/ARP/Nmap)
├── dns_integration/   # multi-DNS-server integration
│   ├── powerdns/        # PowerDNS API
│   ├── bind9/           # BIND 9 (rndc / zone transfer)
│   ├── unbound_opnsense/# OPNsense Unbound API
│   └── windows_dns/     # Microsoft Windows DNS (PowerShell / WMI / DNSCmd)
├── librenms/          # LibreNMS deep integration (key)
│   ├── sync/            # device-list two-way sync
│   ├── arp/             # ARP table fetch
│   ├── fdb/             # FDB / MAC table fetch
│   ├── status/          # live-status complementation
│   └── auto_add/        # auto-add to monitoring
│
├── ── advanced modules (optional, off by default) ──
├── advanced/
│   ├── tenancy/         # tenants/departments
│   ├── contacts/        # contacts
│   ├── circuits/        # circuits
│   ├── cabling/         # cabling
│   ├── power/           # power tracking
│   ├── wireless/        # wireless
│   ├── vpn/             # VPN/L2VPN
│   ├── virtualization/  # virtualization (Proxmox sync)
│   └── asn/             # ASN management
│
├── ── other external integrations ──
├── integration/
│   ├── proxmox/         # Proxmox VE
│   ├── opnsense/        # OPNsense (firewall, aliases, DHCP)
│   ├── wazuh/           # Wazuh (agent sync, IOC matching)
│   ├── graylog/         # Graylog (event forwarding)
│   ├── zimbra/          # Zimbra (contacts)
│   └── odoo/            # Odoo ERP (customers/Tenant)
│
├── ai/                # AI assistant: MCP server, semantic search, NL queries
├── plugin/            # plugin mechanism
└── ui/                # Web UI
```

---

## 3. phpIPAM primary structure (core modules)

### 3.1 Section
- **Identical to phpIPAM**
- Attributes: name, description, parent Section (nesting), permissions, Strict Mode
- UI: left-side tree navigation

### 3.2 Subnet
- **Identical to phpIPAM**
- Attributes: CIDR, description, owning Section, VLAN, VRF, Master Subnet (auto-computed), permissions, custom fields, Show as Folder, Mark as Used/Full, scan settings, threshold alert
- Core display:
  - **Subnet visual block map** (phpIPAM signature, kept and enhanced)
  - **IP table view**
  - **Used / Free stats bar**
  - **Free space auto-shown** (First free address / First free subnet)
- Nested subnets auto-attributed
- Subnet split tools (Resize / Split / Renumber)

### 3.3 IP Address
- **Identical to phpIPAM**
- Attributes: IP, hostname, description, State, MAC, Owner, Switch+Port, Exclude from ping, custom fields, PTR ignore, Note
- **New attributes (v0.3 integration sources)**:
  - `discovery_source` (manual / scanner / librenms / dns / proxmox / opnsense)
  - `last_seen_scanner` (last seen online by in-house scan)
  - `last_seen_librenms` (last seen online by LibreNMS)
  - `last_seen_dns` (last DNS resolution time)
  - `effective_status` (combined status, see §6.4)
- Change log (who, when, what changed)

### 3.4 Subnet / IP visualization
- **Subnet matrix block map** (phpIPAM signature)
  - one block = one IP, color = status
  - large subnets (/16+) auto-aggregate/zoom
- **IP calculator** (IPv4/IPv6, CIDR, Netmask, Wildcard, EUI-64)
- **First available IP / subnet** shown directly

---

## 4. Existing phpIPAM features (fully retained)

| Feature | Notes |
|------|------|
| VLAN management | VLAN Domain, ID 1–4094, Subnet association, conflict detection |
| VRF management | RD, cross-VRF overlapping addresses, configurable allow/deny IP overlap |
| NAT management | 1:1, N:1 (PAT), Port Forwarding, mapping to OPNsense/pfSense rules |
| Devices | phpIPAM-style concise list: name, IP, Type, Model, Vendor, Section, Rack |
| Racks | name, Location, U count, simple U-position visualization |
| Locations | name, address, coordinates, Leaflet + OpenStreetMap map |
| IP Requests | multi-stage approval, Email/Telegram/Slack notifications |
| Subnet active scanning | ICMP / SNMP / ARP / mDNS / NetBIOS / Nmap |
| Import/Export | CSV/XLS, RIPE, TWNIC, JSON/YAML |
| Email notifications | subnet threshold, IP request, expiry reminder |
| Custom fields | all objects, multi-type, regex validation |
| Change log | full before/after diff + SHA-256 hash chain |
| IP calculator | IPv4/IPv6 |
| Full-text search | + AI semantic search (pgvector) |

---

## 5. Multi-DNS-server integration (v0.3 focus)

### 5.1 Design goal
Make IPAM and various DNS servers interoperate: IPAM is the source of truth for IP planning, DNS is the query service, and the two should reconcile. **A hostname can be pushed from IPAM to DNS, and DNS records can be pulled back into IPAM to show the "actual forward/reverse status"**, making it easy to spot doc-vs-reality discrepancies.

### 5.2 Supported DNS servers

| DNS Server | Method | Capability |
|-----------|---------|---------|
| **PowerDNS** | HTTP API (existing phpIPAM mechanism) | read+write |
| **BIND 9** | rndc + AXFR/IXFR zone transfer + nsupdate (TSIG) | read+write |
| **OPNsense Unbound** | OPNsense REST API (`/api/unbound/*`) | read+write |
| **Microsoft Windows DNS** | WinRM + PowerShell (`Get-DnsServerResourceRecord` / `Add-DnsServerResourceRecord`) | read+write |
| **Knot DNS / NSD** | optional, second-phase | read+write |

### 5.3 Shared data model

```
DNSServer
├── id
├── name                     # "OPNsense-Ankang-Unbound"
├── type                     # powerdns / bind9 / unbound_opnsense / windows_dns
├── connection               # API URL / IP / connection credentials (stored encrypted)
├── credentials              # API key / TSIG / WinRM credentials (encrypted)
├── enabled
├── sync_interval            # default 5 minutes
└── last_sync_at

DNSZone
├── id
├── server_id → DNSServer
├── name                     # "example.com", "168.192.in-addr.arpa"
├── type                     # forward / reverse
├── associated_subnets[]     # which Subnets it associates with (reverse zones auto-pair)
├── managed                  # true: IPAM writes actively; false: read-only
└── last_sync_at

DNSRecord
├── id
├── zone_id → DNSZone
├── name                     # "host01"
├── type                     # A / AAAA / PTR / CNAME / MX / TXT / SRV
├── value                    # "192.168.1.10"
├── ttl
├── source                   # manual / from_ipam / from_dns_pulled
├── ipam_address_id → IPAddress  # matching IP (if any)
└── last_seen_at
```

### 5.4 Sync behavior

#### 5.4.1 IPAM → DNS (push)
- Trigger: create/edit an IP whose owning Subnet has "Auto DNS" enabled
- Action: per the Subnet's DNS server and zone, create/update A/AAAA + PTR
- On write failure: mark `dns_sync_failed`, red warning in UI
- Dry-run: preview the records to be written without actually pushing

#### 5.4.2 DNS → IPAM (pull)
- Scheduled task (default 5 min) fetches all zones' records via AXFR/API
- Comparison falls into four buckets:
  1. **consistent**: IP and hostname identical on both sides (green)
  2. **IPAM has, DNS doesn't**: suggest push (yellow)
  3. **DNS has, IPAM doesn't**: one-click import as an IP (yellow)
  4. **mismatch**: same IP, different hostname / reverse mismatch (red)
- Shows a "DNS actual resolution status" section on the IP detail page

### 5.5 DNS section on the Subnet detail page

Each Subnet can specify:
- Forward zones (multiple, e.g. `example.com`, `internal.example.com`)
- Reverse zone (auto-derived from CIDR, e.g. `168.192.in-addr.arpa`)
- DNS server (optionally multiple, primary/secondary)
- Auto DNS toggle
- Default TTL

### 5.6 Windows DNS special handling
- Connect to Windows Server via WinRM (HTTPS)
- Use `Get-DnsServerZone`, `Get-DnsServerResourceRecord`, `Add/Set/Remove-DnsServerResourceRecord`
- Credentials via Kerberos / NTLM, stored in an encrypted config
- AD-integrated zones supported too
- Replaces the old pain of manual sync

### 5.7 OPNsense Unbound special handling
- Via OPNsense REST API:
  - `GET /api/unbound/settings/get` to read Host Override / Domain Override
  - `POST /api/unbound/settings/addHostOverride` to add a host
  - `POST /api/unbound/service/reconfigure` to apply changes
- Combined with OPNsense DHCP static mappings, can sync IPAM ↔ DHCP ↔ Unbound three-way

### 5.8 BIND 9 special handling
- AXFR / IXFR: use a TSIG key for zone transfer to pull data
- nsupdate: add/delete on a dynamic zone with TSIG
- Read-only mode supported too (pull only, no write)

### 5.9 UI presentation

| Screen | Content |
|------|---------|
| IP detail | matching forward/reverse records; DNS consistency; quick "re-push" button |
| Subnet detail | the subnet's DNS zones, overall consistency rate (e.g. 97% consistent) |
| DNS Servers admin | list all DNS servers, connection status, last sync time, failure log |
| DNS Zones | list all zones, record counts, which subnets they associate with |
| Inconsistency report | system-wide inconsistent-IP list, batch-processable |

---

## 6. LibreNMS deep integration (v0.3 focus)

### 6.1 Design goal
LibreNMS already does SNMP monitoring, ARP-table fetching, and FDB fetching; this data is highly valuable to IPAM, so we **consume it directly from the LibreNMS API** rather than reinventing. Meanwhile, new devices discovered by IPAM can optionally auto-join LibreNMS monitoring, closing the loop.

### 6.2 LibreNMS API integration

| LibreNMS API | Purpose |
|--------------|------|
| `GET /api/v0/devices` | get all monitored devices |
| `GET /api/v0/devices/{id}` | single device detail (status, uptime, SNMP info) |
| `POST /api/v0/devices` | **auto-add to monitoring** (v1 / v2c / v3) |
| `GET /api/v0/devices/{id}/ports` | all ports of a device |
| `GET /api/v0/resources/ip/arp/{ip}` | **ARP lookup by IP** (key) |
| `GET /api/v0/devices/{id}/fdb` | **device FDB / MAC table** (key) |
| `GET /api/v0/resources/fdb/{mac}` | **reverse-lookup which switch/port a MAC is on** (key) |
| `GET /api/v0/devices/{id}/availability` | online status and availability |
| `GET /api/v0/alerts` | get alerts |

### 6.3 Two-way device sync

#### 6.3.1 LibreNMS → IPAM
- Scheduled task (default 5 min) pulls the device list
- Compare against IPAM Devices:
  - in LibreNMS, not in IPAM → show in the "unmatched list", one-click import or ignore
  - in both → sync online status, SNMP info (model, OS, serial, uptime)
  - in IPAM, not in LibreNMS → show an "unmonitored" tag on the IPAM device, one-click add to monitoring (see §6.5)

#### 6.3.2 Synced fields
- hostname, IP, SNMP sysDescr / sysObjectID, vendor, model, OS, version, serial, uptime, location

### 6.4 Live-status complementation (key feature)

#### 6.4.1 Problem scenario
phpIPAM's in-house scanning has limits:
- firewall blocks ICMP → false offline
- device in another subnet → scanner can't reach
- limited scan frequency

But LibreNMS uses SNMP, a different vantage point, covering these blind spots.

#### 6.4.2 Combined status (effective_status)

| In-house Scanner | LibreNMS | effective_status | UI |
|------------|----------|-----------------|---------|
| Online | Online | **Online** | green (double confirm) |
| Online | (no data) | **Online (scanner)** | green |
| Offline | Online | **Online (via LibreNMS)** | green (source noted) |
| Online | Offline | **Online (scanner)** | green (LibreNMS may lag) |
| Offline | Offline | **Offline** | red |
| (no data) | Online | **Online (LibreNMS only)** | green (noted) |
| Offline | (no data) | **Offline** | red |
| (no data) | (no data) | **Unknown** | gray |

- Each IP shows its "status source": scanner / librenms / both
- Hover to see both `last_seen_scanner` and `last_seen_librenms`
- This logic's priority weights are customizable in Settings → status determination

#### 6.4.3 IP detail status section

```
Status: ● Online (combined)
  In-house scan: ● Online    last response: 2026-05-06 09:32:11
  LibreNMS:      ● Online    last poll:     2026-05-06 09:33:45
  Source:        scanner + librenms
```

### 6.5 Auto-add to LibreNMS monitoring (key feature)

#### 6.5.1 Flow
1. In-house scanner discovers a new IP (never seen before)
2. Attempt SNMP probe (v1 / v2c / v3, using configured community / credential database)
3. If the SNMP probe succeeds → show an "SNMP reachable" badge and an "Add to LibreNMS" button next to the IP
4. When the user clicks, or an "auto-add" rule is enabled, call `POST /api/v0/devices` to add to monitoring
5. **Decision is per single IP**, not all-or-nothing for the whole network

#### 6.5.2 Auto-add rules (conditional)
- Combinable conditions:
  - Subnet equals X
  - VLAN equals X
  - hostname matches a regex
  - SNMP sysObjectID matches (auto-identifies Cisco / HPE / Aruba…)
  - device type = Switch / Router / Firewall / AP
- Actions:
  - auto-add (send API directly)
  - suggest add (wait for user confirmation in a "pending list")
  - ignore
- Configurable SNMP profile (v2c community or v3 credential group)

#### 6.5.3 Reverse flow
- Delete a device in IPAM → ask whether to also remove from LibreNMS
- LibreNMS removes a device → IPAM shows "no longer monitored"

### 6.6 ARP table fetching (key)

#### 6.6.1 Source
LibreNMS periodically SNMP-fetches `ipNetToMediaTable` (ARP cache) from all monitored L3 devices (Router, Firewall, L3 Switch); IPAM consumes this directly.

#### 6.6.2 Data model

```
ARPEntry
├── id
├── ip                       # inet
├── mac                      # macaddr
├── device_id → LibreNMSDevice  # fetched from which device
├── interface                # which interface
├── vrf                      # if the device has a VRF
├── first_seen_at
├── last_seen_at
└── source                   # librenms / opnsense / proxmox_host
```

#### 6.6.3 Fetch frequency
- Default sync from the LibreNMS API every 15 minutes
- Single-device fetch can be triggered manually

#### 6.6.4 Uses
- **Auto-fill an IP's MAC**: when IPAM has an IP but no MAC, fill from the ARP table
- **MAC conflict detection**: same MAC on multiple IPs (normal); same IP, different MAC (anomaly, possible IP conflict)
- **Find ghost IPs**: IPAM has an IP record but ARP never saw it → possibly long-offline or a phantom record
- **Find unrecorded IPs**: in ARP but not IPAM → one-click import

### 6.7 FDB / MAC table fetching (key)

#### 6.7.1 Source
LibreNMS periodically fetches `dot1dTpFdbTable` (bridge MAC table) or `qBridgeMib` (VLAN-aware) from L2 devices (switches).

#### 6.7.2 Data model

```
FDBEntry
├── id
├── mac                      # macaddr
├── vlan_id
├── device_id → LibreNMSDevice  # switch
├── port_id → DevicePort     # which port it's on
├── port_name                # "Gi0/24"
├── first_seen_at
├── last_seen_at
└── source                   # librenms
```

#### 6.7.3 Full path derivation
By joining the ARP + FDB tables you can derive:

```
IP (IPAM)
  ↓ ARP (LibreNMS)
MAC
  ↓ FDB (LibreNMS)
Switch + Port
  ↓ Cabling (optional module, if enabled)
physical Patch Panel + cable
```

This is exactly the "IP → Switch + Port" field phpIPAM relied on manual maintenance for — now automated.

#### 6.7.4 IP detail display

```
Network location (auto-derived)
├─ MAC: 00:11:22:33:44:55 (source: LibreNMS ARP @ Core-Router-Ankang, 2 min ago)
├─ Switch: Access-SW-3F-01
├─ Port: Gi0/24 (VLAN 100)
└─ Uplink: Core-SW-Ankang Te1/1 (if the Cabling module is enabled)
```

Click to expand a timeline: "which ports this IP appeared on in the last 7 days" (move tracking).

### 6.8 Topology visualization (reinforcement)
Combining LibreNMS LLDP / CDP data + IPAM's IP/Subnet/VLAN, produce an interactive topology (Cytoscape.js):
- click a switch to see all MACs/IPs on each port
- click an IP to highlight its full path
- color by VLAN
- export PNG / SVG / PDF (for docs, audits)

### 6.9 Conflict and anomaly detection

| Detection | Logic |
|--------|------|
| IP conflict | same IP with different MACs at different times (short window) |
| MAC drift | same MAC bouncing across multiple switch ports |
| Ghost IP | in IPAM but never in ARP/FDB for over N days |
| Unauthorized device | in ARP but not IPAM, and not in the allowlist |
| Cross-VLAN anomaly | MAC appearing in an unexpected VLAN |

Anomaly triggers: in-app notification + Email + Telegram + write to Graylog (jt-glogarch archive, audit-compliant).

### 6.10 LibreNMS settings page
Within IPAM you can:
- configure multiple LibreNMS instances (multi-site)
- set API URL + token
- toggle each integration item (device sync / ARP / FDB / live status / auto-add)
- set sync frequency
- show connection status and last sync time
- show sync-failure error log

---

## 7. Advanced modules (optional, off by default)

> These are NetBox features phpIPAM lacks, added as reinforcement. Users decide whether to enable them in "System Settings → Module Management"; if disabled, the UI doesn't show them at all.

| Module | Content |
|------|------|
| Tenancy | Tenant Group / Tenant, multi-customer/department isolation |
| Contacts | Contact Group / Contact / Role, attachable to Site/Device/Subnet, syncable with Zimbra |
| Circuits | Provider (Chunghwa Telecom, TFN…), Circuit Type, monthly fee, contract expiry |
| Cabling | end-to-end tracing, patch-panel mapping, Cable Trace |
| Power | Power Panel → Feed → PDU → Device, dual-feed redundancy check |
| Wireless | SSID management, wireless backhaul links |
| VPN / L2VPN | IKE/IPsec tunnels, VPLS/VXLAN/EVPN, integrated with OPNsense/Strongswan |
| Virtualization | Cluster / VM / VM Interface, **two-way sync with Proxmox VE** |
| ASN management | 16/32-bit ASN, associated with Site/Tenant |

---

## 8. Core system features

### 8.1 Authentication & permissions
- **Local accounts** (password complexity, TOTP MFA, argon2id)
- **AD / LDAP** (existing in phpIPAM)
- **Radius** (existing in phpIPAM)
- **SAML 2.0** (reinforcement: Keycloak, Azure AD, Google Workspace)
- **OIDC / OAuth2** (reinforcement)
- **API Token** (per-token permission scope and expiry)
- **Permission model**:
  - per-Section / per-Subnet permissions (phpIPAM-consistent)
  - User Group permissions
  - reinforcement: object-level permissions, tag filtering

### 8.2 Change auditing
- Each write records: time, user, IP, before/after diff
- **SHA-256 hash chain** (consistent with the jt-glogarch design)
- Forwardable to Graylog
- Configurable retention

### 8.3 Notifications
- Email (SMTP / Zimbra)
- Telegram Bot
- Webhook
- In-app notification center
- Custom templates

### 8.4 Search
- Full-text search (PostgreSQL FTS + pg_trgm)
- Advanced filtering (per-field operators)
- Saved queries
- **AI semantic search** (pgvector + qwen3-embedding:8b)

### 8.5 Background jobs
- Scheduled (Celery Beat): DNS sync, LibreNMS sync, ARP/FDB fetch, subnet scan
- Real-time (RQ)
- Task status page, failure retry, log query

### 8.6 Reports
- Built-in: IP utilization, Subnet utilization, device warranty expiry, orphan IPs, DNS inconsistency, unmonitored devices, ARP/FDB anomalies
- Custom reports (saved SQL/GraphQL)
- PDF export (Playwright, consistent with jt-glogarch)
- Minguo calendar support, watermark

### 8.7 Import/Export
- CSV / XLS / XLSX (keeps phpIPAM behavior)
- JSON / YAML
- RIPE / TWNIC
- **Full phpIPAM database migration tool** (one-click migration)
- NetBox data import (bring in from an existing NetBox)

---

## 9. UI & appearance

### 9.1 Overall layout (phpIPAM style)
- **Left tree navigation** (Section tree)
- **Top toolbar**: search, language switch, theme switch, user menu, notification center
- **Main content**: Subnet visual blocks, IP table, Dashboard
- **Dashboard**: phpIPAM-style top stats (subnet count, IP utilization, recent changes, DNS inconsistencies, unmonitored devices…)

### 9.2 Multilingual (i18n)
- **First release supports two languages**:
  - **Traditional Chinese (Taiwan) zh-TW**: first-class citizen
  - **English (en-US)**: full coverage
- Framework for adding locales (gettext / ICU MessageFormat); future ja-JP, zh-CN
- Switching: login page, user preference, Accept-Language auto-detect, URL `?lang=` override
- Object names (Site name, Tenant name…) are user input, not translated
- Built-in dictionaries (Status, Role…) have bilingual mappings
- Custom field labels support bilingual input
- Date/time/calendar: zh-TW can switch Gregorian/Minguo, English ISO 8601
- PDF reports generated per locale (zh-TW Source Han Sans, English Inter / Noto Sans)

### 9.3 Theme
- **Three modes**:
  - **Light**: white background, dark text, for daytime offices and printing
  - **Dark**: dark background, light text, for NOC duty and long sessions
  - **Auto**: follows the OS `prefers-color-scheme`
- Switching: one-click top-right button, saved in user preference
- Implementation: CSS Variables (single stylesheet)
- Charts (ECharts, Cytoscape, Subnet block map) must be readable in both themes
- Meets WCAG 2.1 AA contrast (body 4.5:1, large text 3:1)
- Color-blind-friendly palette toggle option
- PDF reports fixed to light (print-friendly)

### 9.4 User preferences (unified storage)
- language, theme, timezone, calendar, page size, default Section, dashboard card settings
- stored in the `user_preferences` table

---

## 10. API & integration

### 10.1 phpIPAM API compatibility layer (important)
- **Full mapping of phpIPAM v1.7 API endpoints**
- old scripts migrate with zero changes
- path prefix `/api/phpipam/`
- the same token mechanism

### 10.2 Modern REST API
- OpenAPI 3.1 auto docs (Swagger UI + Redoc)
- full CRUD, filtering, sorting, pagination, field selection, bulk
- path prefix `/api/v1/`
- API token rate limiting

### 10.3 GraphQL API
- nested queries (especially good for the multi-level "IP → MAC → Switch Port → uplink" join)
- subscriptions (live push: new IP appears, status changes)

### 10.4 MCP Server
- exposes IPAM capabilities to an LLM (Ollama gpt-oss:120b, qwen3.5:122b)
- tools: `search_ip`, `allocate_subnet`, `find_free_ip`, `get_device_by_ip`, `list_vlans`, `trace_mac`, `check_dns_consistency`…
- integrates Jason's existing Telegram Bot + Node-RED + Ollama
- natural language: "find me a free IP in VLAN 100 at the Ankang data center for a new Wazuh agent"

### 10.5 Outbound webhooks
- object change → POST to a configured URL
- HMAC signature
- retry mechanism
- target URL must pass the SSRF allowlist (A10)

### 10.6 Full external-integration matrix

| System | Integration | Phase |
|------|---------|------|
| **phpIPAM** | full data migration tool + API compatibility layer | Phase 1 |
| **PowerDNS** | forward/reverse two-way sync (existing, retained) | Phase 1 |
| **BIND 9** | AXFR/IXFR + nsupdate (TSIG) | Phase 2 |
| **OPNsense Unbound** | REST API bidirectional | Phase 2 |
| **Windows DNS** | WinRM + PowerShell bidirectional | Phase 2 |
| **LibreNMS** | device sync, ARP, FDB, status complementation, auto-add | Phase 2 |
| **OPNsense / pfSense** | DHCP scope, aliases, NAT rules | Phase 3 |
| **Proxmox VE** | VM/CT/Node sync, auto IP assignment, SDN | Phase 3 |
| **Wazuh** | Agent IP/hostname sync, IOC matching | Phase 3 |
| **Graylog** | IP-change event forwarding, query-endpoint correlation | Phase 2 |
| **Zimbra** | contact sync | Phase 4 |
| **Odoo ERP** | customer/Tenant sync | Phase 4 |

---

## 11. AI capabilities

### 11.1 Local AI assistant
- connects to local Ollama (gpt-oss:120b, qwen3-vl)
- **data never leaves the premises** (meets government/enterprise security requirements)
- floating button bottom-right

### 11.2 Use cases
1. **Natural-language query**: "list all out-of-warranty Dell PowerEdge connected to VLAN 100"
2. **Smart allocation**: "I'm deploying 5 new Proxmox nodes, plan the IPs and VLAN for me"
3. **Anomaly detection**: MAC drift, IP conflict, ghost IP, unauthorized device
4. **Doc generation**: auto-produce network topology descriptions, rack layout reports (zh-TW/English)
5. **OCR import**: photograph a data-center whiteboard, auto-recognize handwritten subnet plans (qwen3-vl)
6. **Compliance check**: auto-review whether IP allocation complies with internal policy

### 11.3 Embedded semantic search
- qwen3-embedding:8b vectorizes each object's description
- stored in pgvector
- fuzzy queries, similar-object recommendations

---

## 12. Security & deployment

### 12.1 Security (overview; details in docs/SECURITY.md)
- aligned with OWASP Top 10:2025
- **TLS mandatory**: HTTPS at the user layer always; two modes supported (① nginx reverse-proxy termination, ② uvicorn direct self-signed)
- SHA-256 change chain
- Graylog forwarding
- login-failure lockout, IP allowlist, API token expiry
- PII de-identification option
- encrypted storage of sensitive fields (DNS credentials, SNMP community, API key)

### 12.2 Compliance
- meets ISMS / ISO 27001 audit needs
- Minguo calendar support
- backup strategy: daily PG pg_dump + weekly full + offsite (Proxmox Backup Server Ankang/Taiping dual site)

### 12.3 Deployment forms (no containerization)
1. **Proxmox LXC template** (preferred, familiar to Jason customers)
2. **Bare metal Debian / Ubuntu** (systemd + nginx + apt packages)
3. **Offline install bundle** (closed government environments; includes .deb and a wheel cache)

> One-shot script: `scripts/install-debian.sh`; systemd units: `deploy/systemd/`.

### 12.4 System requirements (minimum)
- 2 vCPU, 4 GB RAM, 20 GB disk
- PostgreSQL 16+, Redis 7+, Python 3.12+
- recommended: Proxmox VE LXC container

### 12.5 High availability
- PostgreSQL streaming replication
- Redis Sentinel
- stateless application layer (multi-replica)
- health endpoints (/healthz, /readyz)

### 12.6 License
- **AGPL-3.0** (recommended)
- commercial support provided by Jason Tools

---

## 13. Roadmap (v0.3 ground-truth status)

### Phase 1: phpIPAM equivalence + upgrade
- ✅ Section / Subnet / IP Address three-layer core
- ✅ VLAN / VRF / NAT
- ✅ in-house subnet scanning (ICMP; SNMP/ARP/Nmap on Phase 2 Celery scheduling)
- ✅ Devices / Racks / Locations / IP Requests (incl. timeline state machine)
- ✅ auth (local + LDAP/AD/Radius) + argon2id + TOTP + lockout + API Token
- ✅ phpIPAM API compatibility layer (read/write)
- ✅ phpIPAM data **sync** tool (repeated imports, conflict policy, parallel use)
- ✅ PowerDNS integration
- ✅ zh-TW/English bilingual, light/dark theme
- ✅ **systemd + nginx + apt deployment** (Docker dropped, switched to Proxmox LXC / bare metal)
- ✅ **OWASP Top 10:2025 baseline fully landed** (incl. A10 Mishandling of Exceptional Conditions)
- ✅ **HTTPS mandatory (nginx reverse-proxy / uvicorn self-signed dual mode)**
- ✅ Subnet visual block map, rack U-position visualization, IP indicator dashboard
- ✅ Tools (IP/CIDR calculator, EUI-64)
- ✅ Custom Fields, CSV import/export (dry-run, idempotent)
- ✅ RIPE / TWNIC whois import
- ✅ notification center (in-app + Webhook + SMTP)
- ✅ full-text search + auto query-type detection

### Phase 2: multi-DNS integration + LibreNMS deep integration + AI semantic search
- ✅ BIND 9 (AXFR + nsupdate TSIG)
- ✅ OPNsense Unbound (REST host override)
- ✅ Windows DNS (WinRM + PowerShell)
- ✅ PowerDNS (v4 HTTP API)
- ✅ DNS two-way sync, inconsistency report
- ✅ LibreNMS device two-way sync
- ✅ LibreNMS ARP table fetch (auto-fill IP MAC)
- ✅ LibreNMS FDB / MAC table fetch
- ✅ live-status complementation (effective_status §6.4.2 truth table)
- ✅ auto-add to LibreNMS monitoring
- ✅ IP → MAC → Switch Port auto-derived trace
- ✅ anomaly detection (IP conflict / MAC drift / ghost IP / unauthorized IP)
- ✅ SHA-256 change chain, Graylog forwarding
- ✅ modern REST API + GraphQL (Strawberry, read-only)
- ✅ AI semantic search (pgvector + Ollama embedding)

### Phase 3: advanced modules + integrations + topology + SSO
- ✅ Tenancy (TenantGroup / Tenant)
- ✅ Contacts (Group / Role / Contact / polymorphic Assignment)
- ✅ Circuits (Provider / Type / Circuit)
- ✅ Cabling (Cable + polymorphic Termination)
- ✅ Power (Panel → Feed → Outlet)
- ✅ Wireless (SSID + Link)
- ✅ VPN / L2VPN (IPsec/WG/L2TP/VxLAN/VPLS/EVPN)
- ✅ Virtualization (Cluster / VM / Interface) + Proxmox VE sync
- ✅ ASN
- ✅ topology visualization (Cytoscape.js + cose-bilkent)
- ✅ OIDC SSO (discovery + state/nonce + auto-provision)
- ✅ SAML 2.0 SSO (python3-saml; metadata/ACS/SLO; assertion signing on by default)
- ✅ OPNsense firewall alias sync (bidirectional; selector by section/subnet/tag/custom_field)
- ✅ Wazuh agent inventory sync + missing-agent detection (the not-installed list for the SOC)

### Phase 4 (reduced): AI / Plugin
- ✅ MCP Server (exposes IPAM tools to a local LLM; JSON-RPC 2.0 subset)
- ✅ local-LLM natural-language query (Ollama chat + tool use; UI floating window)
- ✅ Plugin mechanism (importlib.metadata entry_points + admin list + docs)

### Out of scope (explicitly not done)
- ❌ HA deployment (PG streaming + Redis Sentinel + multi-replica backend)
- ❌ Ansible Collection (jasontools.jt-ipam)
- ❌ Terraform Provider
- ❌ Zimbra contact sync
- ❌ Odoo ERP sync
- ❌ Docker / Helm Chart / Kubernetes containerized deployment

---

## 14. Naming & branding

- Project name: `jt-ipam`
- GitHub: `github.com/jasontools/jt-ipam`
- Official site: `ipam.example.com`
- Demo: `demo-ipam.example.com`
- Docs: `docs-ipam.example.com`
- Logo style: continuing the jt- series (clean, blue-green palette, one version each for light/dark)

---

## Appendix A: phpIPAM feature mapping (one-to-one coverage)

| phpIPAM feature | jt-ipam equivalent | Phase |
|---|---|---|
| Section | Section (same name, same logic) | 1 |
| Subnet | Subnet (same name, same logic) | 1 |
| IP Address | IP Address (same name, same logic) | 1 |
| Subnet visual blocks | Subnet visual blocks (retained) | 1 |
| auto free-space display | First free address / subnet | 1 |
| auto subnet scanning | Scanner (ICMP/SNMP/Nmap) | 1 |
| PowerDNS integration | PowerDNS module | 1 |
| NAT support | NAT module | 1 |
| RACK management | Racks module | 1 |
| AD/LDAP/Radius | Auth | 1 |
| group permissions | per-Section/Subnet permissions | 1 |
| device management | Devices module | 1 |
| RIPE import | Import (+ TWNIC) | 1 |
| XLS/CSV import | Import | 1 |
| IP request module | IP Requests | 1 |
| REST API | phpIPAM API compatibility layer | 1 |
| Locations module | Locations + map | 1 |
| VLAN management | VLAN module | 1 |
| VRF management | VRF module | 1 |
| IPv4 / IPv6 calculator | Tools | 1 |
| IP database search | Search (+ AI semantic) | 1 |
| Email notifications | Notification (+ Telegram/Webhook) | 1 |
| custom fields | Custom Fields | 1 |
| translation | i18n (zh-TW/English) | 1 |
| change log | Change Log (+ SHA-256) | 1 |

## Appendix B: v0.3 new features

| Category | Feature | Phase |
|------|------|------|
| DNS integration | OPNsense Unbound two-way sync | 2 |
| DNS integration | Windows DNS two-way sync (WinRM) | 2 |
| DNS integration | BIND 9 two-way sync (TSIG) | 2 |
| DNS integration | DNS ↔ IPAM inconsistency report | 2 |
| LibreNMS | device two-way sync | 2 |
| LibreNMS | ARP table fetch | 2 |
| LibreNMS | FDB / MAC table fetch | 2 |
| LibreNMS | live-status complementation (effective_status) | 2 |
| LibreNMS | auto-add to monitoring (per-device decision) | 2 |
| LibreNMS | IP→MAC→Switch Port auto-derivation | 2 |
| LibreNMS | MAC drift, ghost-IP anomaly detection | 2 |
| UI | zh-TW/English bilingual | 1 |
| UI | light/dark theme (incl. Auto) | 1 |
| AI | MCP Server | 4 |
| AI | local-LLM natural-language query | 4 |
| AI | semantic search (pgvector) | 2 |
| Security | OWASP Top 10:2025 baseline | 1 |
| Security | argon2id + TOTP MFA | 1 |
| Security | SHA-256 change chain | 2 |
| Security | SSRF allowlist | 2 |

## Appendix C: NetBox reinforcement mapping (optional)

| NetBox feature | jt-ipam equivalent | Default | Phase |
|---|---|---|---|
| Tenancy | advanced module | off | 3 |
| Contacts | advanced module | off | 3 |
| Circuits | advanced module | off | 3 |
| Cabling | advanced module | off | 3 |
| Power Tracking | advanced module | off | 3 |
| Wireless | advanced module | off | 3 |
| VPN / L2VPN | advanced module | off | 3 |
| Virtualization | advanced module | off | 3 |
| ASN management | advanced module | off | 3 |
| GraphQL API | API | on | 2 |
| Plugin mechanism | Plugin | on | 4 |

---

**v0.3 integration highlights**
1. Thesis fixed as "phpIPAM-primary, NetBox-secondary"
2. Added a phpIPAM API compatibility layer (zero changes to old scripts) and a one-click data migration tool
3. zh-TW/English bilingual + light/dark theme explicitly in Phase 1
4. **Added multi-DNS integration**: OPNsense Unbound, Windows DNS, BIND 9, PowerDNS (read + optional push)
5. **Added LibreNMS deep integration**:
   - device two-way sync
   - ARP table fetch (auto-fill IP MAC)
   - FDB table fetch (auto-locate which Switch Port an IP is on)
   - live-status complementation (use LibreNMS results when in-house can't reach)
   - auto-add to monitoring (per-device decision)
6. Anomaly detection: IP conflict, MAC drift, ghost IP, unauthorized device
7. **Security by design**: aligned with OWASP Top 10:2025
