# Changelog

All notable changes to this project are documented here. The format is loosely
based on [Keep a Changelog](https://keepachangelog.com/); versions track
`frontend/package.json` / `backend/app/version.py`.

## [0.4.142] — 2026-06-14

### Fixed
- **500 when saving an SFTP/URL source** (MissingGreenlet): `PUT /certificates/{id}/source` serialized
  the ORM object after commit, triggering a lazy load in a sync context. Now refreshes the object
  (`session.refresh`) after commit before serializing.

### Added — Source connection test + auto-generated SSH key
- Source config gains a **"Test connection"** button: it actually probes the URL / SFTP source using the
  current form values (blank password/key = reuse stored), returning a success message or a readable
  failure reason, without saving (`POST /certificates/{id}/source/test`).
- The SFTP login private key gains a **"Generate key"** button: jt-ipam generates an ed25519 keypair,
  stores the private key AES-GCM encrypted (never returned), and returns the **public key** to add to the
  SFTP host's `authorized_keys` (`POST /certificates/{id}/source/ssh-keypair`).

### Changed
- Certificate / distribution-agent action buttons are now **icon-only with hover tooltips** (matching the
  rest of the app), with tighter, centered columns — fixing the over-wide left gap, right overflow, and
  left-aligned icons.

## [0.4.141] — 2026-06-14

### Fixed / Changed
- The "update available" reload banner had its icon and text misaligned vertically — the icon is now
  centered in a 16×16 box, with `line-height:1` on the container and text.
- The certificate table's "Expiry" column is split into two independent columns: **"Expiry date"** and
  **"Days left"** (each sortable and pickable).
- Certificate / distribution-agent action-column icons are now centered (column `align:center` +
  NSpace `justify:center`).

## [0.4.140] — 2026-06-14

### Changed — Certificate auto-fetch source UX
- SFTP source config clarity: **"Login password" / "Login private key (SSH key, PEM)"** are now a
  distinct "SFTP login auth" section placed right under the username, with a hint: "Used to log in to
  the SFTP host. Provide a password OR an SSH private key (key takes precedence). The certificate's own
  private key is the remote key_path file below — unrelated to this." Remote file paths
  (cert_path/key_path/chain_path) are grouped separately. (The backend already supported SSH-key login;
  only the field placement/naming was easy to mistake for the certificate's private key.)
- The "Off" source type now reads **"Off (manual upload)"** so it's clear upload / paste / self-signed
  are still available.

### Changed — Certificate / distribution-agent tables match the rest of the app
- Both tables now have **sortable columns** (autoSort) and a **column picker** (preferences persisted to
  the backend and synced across devices).
- Action-column buttons now show **icon + text** and collapse to **icon-only** when the column is too
  narrow (col-actions container query; the label still shows on hover).

## [0.4.139] — 2026-06-14

### Added — Distribution-agent version display & self-update
- The admin "Distribution agents" tab now shows the agent **version** (flagged "update available"
  with a hint when it lags the server) and **source IP**, mirroring the scan agent.
- The distribution agent now **self-updates**: `/check` returns the sha256 of the server's agent.py;
  if the running copy differs the agent downloads the new version, atomically replaces itself and
  re-execs (the download is sha-verified before replacing; a failure is logged and never aborts
  deployment). Set `auto_update: false` in the config to disable.
- The read-only "Certificate distribution status" page (`GET /cert-agents/status`) now also returns
  `last_source_ip` / `server_agent_version`.

## [0.4.138] — 2026-06-13

### Added — Certificate auto-fetch source
- A certificate can now have an **auto-fetch source** (in addition to upload / paste / self-signed):
  the system periodically (and on demand via "Fetch now") pulls the renewed bundle from the source,
  and **only stores a new version if the content actually changed** — if the fingerprint matches the
  current version it is skipped (no-op). If the source provides no key, the current version's key is
  reused (common for renewals that keep the same key).
- Sources: **URL** (fetched via the SSRF-guarded safe_http client) and **SFTP** (asyncssh; the host
  is checked against the SSRF block-list). Credentials (SFTP password / private key) are AES-GCM
  encrypted (`encrypted_secret`) and never returned. New migration `0076`.
- Endpoints: `PUT /certificates/{id}/source`, `POST /certificates/{id}/fetch-now`; the sync timer
  auto-fetches each source-backed certificate on its own interval. Frontend: per-certificate source
  config (URL/SFTP) + "Fetch now", with last-fetch error surfaced.
- CIFS / NFS are out of scope for now (the backend runs non-root and can't mount); use a pre-mounted
  path or fetch via URL/SFTP.

## [0.4.137] — 2026-06-13

### Fixed
- **Certificate pages returned 405 / "server error" (regression in the cert API client)** — the
  `certificates.ts` API calls (and the subnet-overlap check in `integrations.ts`) were missing the
  `/api/v1` prefix that the shared axios client requires (its baseURL is `/`), so requests hit the
  SPA paths (`/certificates`, `/cert-agents`) and nginx returned 405 for POST / index.html for GET.
  All cert API paths are now correctly prefixed. The certificate admin page, agents, self-signed,
  and the Advanced status view work.
- Added the missing icon on the certificate/agent "Save" buttons.

## [0.4.136] — 2026-06-13

### Certificate distribution — UX
- The certificate version upload now supports **pasting PEM text** (certificate / key / chain) as
  an alternative to uploading files — a toggle in the upload dialog.
- Renamed the Advanced-menu read-only certificate view label to match the admin one.

## [0.4.135] — 2026-06-13

### Certificate distribution — follow-ups
- **Cross-distro agent installer** — the cert-agent installer now auto-detects the package
  manager (apt / dnf / yum / zypper), so it works on Debian 11/12/13, Ubuntu 22.04/24.04/26.04,
  RHEL / Rocky / AlmaLinux / CentOS, Fedora and openSUSE/SLES (all systemd). PyYAML is installed
  via the right package name per distro.
- **More profiles** — added `pbs` (Proxmox Backup Server: `proxy.pem`/`proxy.key`, reloads
  `proxmox-backup-proxy`). The `apache` profile now reloads `apache2` or `httpd` (whichever exists),
  so it works on Debian/Ubuntu and RHEL/SUSE.
- **Install-instructions button** on the Distribution Agents tab (like Scan Agents): one-liner
  install command, config example, supported distros, and the `--dry-run` hint.
- **Read-only certificate status under Advanced** — a non-admin viewer with global read can now
  see each agent's deployment status (last update, valid-from, expiry, days remaining, up-to-date
  vs drift) via a new Advanced menu entry. New `GET /cert-agents/status` (gated `require_global_read`).

## [0.4.134] — 2026-06-13

### Fixed
- **PGDG repo setup failed on Debian 12 when the keyring file already existed (customer report)** —
  the installer ran `gpg --dearmor` onto `/usr/share/postgresql-common/pgdg/apt.postgresql.org.gpg`
  (a file owned by the `postgresql-common` package). When that file already existed, gpg prompted
  "File exists. Overwrite?" / failed non-interactively, so the key was never written, the PGDG repo
  signature was invalid, and `postgresql-16-pgvector` was "not installable". Now the key is written
  to its own `/etc/apt/keyrings/jt-ipam-pgdg.gpg` with `gpg --dearmor --yes` (no collision, idempotent).
  Verified end-to-end in a Debian 12 container.

### Added — Certificate distribution (commercial certs → push to all sites)
- Central store for commercial certificates with a pull-based distribution agent. You upload a
  renewed bundle (crt/key/chain) once; agents on each host pick up the new version, write it to
  the right paths, run a config-test, reload the service, and roll back on failure.
- **Backend**: migration `0075` (`certificates` / `cert_versions` / `cert_agents`); the private
  key is stored AES-GCM encrypted and is never returned by any management API. `/certificates`
  admin CRUD + `POST /{id}/versions` (validates key↔cert match, SAN/expiry, rejects mismatched/
  expired/duplicate) + **`POST /{id}/self-signed`** (generate a self-signed cert with a custom
  CN/SAN/validity — handy while waiting for the commercial cert). `/cert-agents` admin CRUD +
  key rotate, plus the agent protocol (`X-Agent-Key`): `check` / `bundle` (decrypts the key,
  scope-limited, audited every time) / `report`.
- **Agent** (`agent/jt_ipam_cert_agent.py` + installer): pull model, built-in service profiles
  (nginx / apache / haproxy / pve / pmg / postfix / dovecot / zimbra / generic), atomic write +
  timestamped backup + config-test gate + rollback, idempotent, and **`--dry-run`**. Config is a
  small per-host YAML listing which certs deploy via which profile.
- **Monitoring**: daily expiry alerts and **drift detection** (an agent reporting a fingerprint
  other than the current version → that site didn't update) via the existing notification/bell.
- **Frontend**: a Certificates admin page (upload, self-signed, version/expiry status, agents +
  one-time key, scope).

## [0.4.133] — 2026-06-13

### Fixed
- **Install on minimal Debian 12 / 13 containers (customer report)** — two gaps surfaced on
  clean container images:
  - The PGDG-repo step runs `curl | gpg` and the later PostgreSQL setup uses `sudo -u postgres`,
    but `ca-certificates` / `curl` / `gnupg` / `sudo` were not guaranteed present (minimal Debian
    container images often omit them). The PGDG step (which Debian 12 always takes, since its
    default repo ships PG 15, not 16) failed at `curl`, and the PostgreSQL config step failed with
    `sudo: command not found`. These four are now installed up-front.
  - Combined with the v0.4.131 `apt-cache madison` version detection, the matrix is now: Debian 12
    → PGDG PostgreSQL 16; Debian 13 → native PostgreSQL 17 (+ `postgresql-17-pgvector`, no PGDG);
    Ubuntu 24.04 → native 16; Ubuntu 26.04 → native 17/18. The app supports PG 16/17/18.

## [0.4.132] — 2026-06-12

### Fixed
- **CSV import 500 on real import (customer report / issue #4)** — the import endpoint passed
  `subnet.cidr` (an asyncpg `IPv4Network` object, not a str) as the background task's VARCHAR
  `target_label` → asyncpg `DataError`. Dry-run was unaffected (no task spawned), which is why
  preview worked but the actual import 500'd. Now coerced with `str()`.
- **IP request list 500 when a request has a manually-specified IP (issue #4)** — asyncpg returns
  `IPv4Address` from the `INET` column, but `IPRequestRead.requested_ip` is typed `str`, so Pydantic
  validation failed and the whole list page 500'd. Added a `mode="before"` coercion (the same
  pattern already used for `IPAddressRead.ip` / `SubnetRead.cidr`).
- **Scan agent could not return hostnames (customer report)** — reports carrying rdns/NetBIOS/mDNS/OS
  hostnames 500'd for newly-discovered IPs. With `autoflush=False` and a DB-generated UUID, a freshly
  added `IPAddress` had `id=None` when `apply_observation` built the hostname-observation FK row →
  `NOT NULL` violation. Now flushes right after creating the IP so its id is populated. (icmp+arp-only
  reports were unaffected because they never call `apply_observation`.)
- **Hardened the same asyncpg INET/CIDR-as-str class of bug** across other read schemas that build via
  `model_validate(ORM)` and were missing coercion: `APITokenRead.last_used_ip`, `VMInterfaceRead`
  (`primary_ip`/`mac`), and `ARPEntryRead`/`FDBEntryRead` (`ip`/`mac`).

## [0.4.131] — 2026-06-12

### Fixed
- **Install on Ubuntu 26.04 (customer report)** — the installer hardcoded `postgresql-16`,
  which isn't in Ubuntu 26.04's default repos (it ships PG 17/18). The old fallback added the
  PGDG repo for the new release codename, which PGDG often doesn't carry until months after
  release → `apt-get update` 404'd and the install aborted. The installer now detects the
  PostgreSQL version already available in the enabled repos (prefers 16, otherwise the distro's
  native 17/18/…) and installs that plus the matching `postgresql-N-pgvector`; PGDG is only
  used as a last resort when no `postgresql-N` (>=16) exists at all. The app is compatible with
  PG 16/17/18. Python detection also now includes `python3.14` (Ubuntu 26.04's default).

### Fixed
- **ARP table retention** — `arp_entries` was insert/update only and never pruned, so it
  grew unbounded over time (MAC↔IP churn and orphaned rows from deleted devices each left a
  row). The sync timer now deletes ARP entries older than `ARP_RETENTION_DAYS` (default 30;
  set 0 to disable) once per run, including orphan rows.

### Added
- **Overlapping-subnet warning on integration settings** — when overlapping subnets exist
  (the same IP can appear in more than one subnet) and an integration (LibreNMS / OPNsense /
  Wazuh / Proxmox / AdGuard / DNS) has no subnet scope set, the settings form now shows a
  warning that a sync may stamp liveness / DHCP / MAC onto the wrong tenant's copy of an IP,
  pointing the admin to set the subnet scope. New `GET /subnets/overlaps/exists` (admin).

### Notes
- No new duplicate-IP / duplicate-ARP risk: `ip_addresses` is unique on `(subnet_id, ip)`;
  `arp_entries` is upserted on `(ip, mac, device_id)`; only LibreNMS writes ARP (scanner and
  OPNsense only stamp existing IPs). Same-IP-string across overlapping subnets remains by design.

## [0.4.129] — 2026-06-11

### Security
- **RBAC IDOR fixes** — several detail/aggregate endpoints accepted an object id without an
  object-level visibility check, letting any signed-in account read objects outside its scope:
  `GET /devices/{id}` and its sub-resources (`/integrations` exposed Wazuh CVE counts + Proxmox
  VMs, plus `/librenms`, `/vlans`, `/relations`), `GET /customers/{id}` and `/{id}/summary`
  (full per-customer asset dump), and `GET /racks/{id}/diagram`. All now require object `read`
  permission (404 on no access). The MCP `get_topology` tool no longer leaks the full topology
  to scoped accounts (was missing the `user` filter) and is gated as global-read; the REST
  `GET /topology` is gated with `require_global_read` to match.
- **OIDC ID Token verification** — the callback previously base64-decoded the ID Token and
  trusted its claims (including `groups`, which drives admin promotion) without verifying the
  signature. It now verifies the ID Token against the provider's JWKS (signature + `aud`/`iss`/
  `nonce`) before trusting any claim; on failure it falls back to userinfo only instead of
  trusting unverified groups.
- **CSV export formula injection** — IP address CSV export now escapes cells beginning with
  `= + - @` / tab / CR so spreadsheets don't execute them as formulas.

### Fixed
- **Integration sync resilience** — `jt-ipam-sync.py` now rolls back the session before writing
  `last_error` in every integration's exception handler; a single failing instance (e.g. an
  AdGuard `MultipleResultsFound` on overlapping subnets) no longer aborts the whole sync run.
- **Overlapping subnets** — AdGuard sync (`sync_clients` / `sync_rewrites`) and the MCP ARP
  lookup matched `IPAddress.ip` with `scalar_one_or_none()`; with overlapping subnets the same
  IP yields multiple rows → `MultipleResultsFound`. Changed to `limit(1)` + `first()`.
- **Non-UCS DNS server connection tests** — BIND 9 (dnspython `OSError`/connection-refused),
  Windows DNS (WinRM/`requests` exceptions), PowerDNS and OPNsense Unbound (non-JSON responses
  on auth failure) leaked raw exceptions that the `/dns/servers/{id}/test` endpoint didn't catch,
  producing a 500 with no message. Adapters now wrap these as `DNSAdapterError`, and the test
  endpoint has a safety net that turns any unexpected error into a readable 502.

### UI / Docs
- Fixed a missing i18n key on the section detail page ("display order" showed the raw key).
- Added error feedback to the notifications "mark all read" and group-members actions.
- Terminology: use 「外掛」 (not 「插件」) for "plugin" in zh-TW docs.

## [0.4.128] — 2026-06-10

### Fixed / Improved
- **External reverse proxy + OIDC / Microsoft 365 (Entra ID) login**: the frontend now parses
  the token the backend returns in the URL fragment after the OIDC/SAML callback (previously
  ignored → stuck on the login page); the backend merges **ID Token** claims into userinfo —
  Entra ID returns `groups` only in the ID Token (not the Graph userinfo endpoint), so admin-
  group mapping now matches. Added `deploy/nginx/jt-ipam-external-proxy.{conf,snippet}`
  templates (HTTP-only, no HSTS, `X-Forwarded-Proto` passthrough) and a README "Mode C —
  external reverse proxy" section (set `APP_PUBLIC_URL`/CORS to your domain, forward the proto).
- **Install (Ubuntu 24.04)**: `ensure_node` no longer pipes the NodeSource output to `/dev/null`
  and now **verifies Node ≥ 18** after install, otherwise it stops with a clear remedy — fixes a
  silent Node-install failure that left the frontend unbuilt while the run "looked" successful.
- **AI chat**: when Ollama is disabled / unreachable / misconfigured, a **friendly, actionable**
  error is shown (pointing to Admin → LLM / AI) instead of a cryptic string.
- **Circuits**: fixed the empty "associated device" dropdown when editing (device query exceeded
  the backend `page_size` cap); circuit table gains Device / Description columns and a localized
  Status column.
- **Tables (scan agents / device detail)**: tightened column widths so the actions column no
  longer overflows, empty columns no longer hog width, and MAC / timestamps no longer wrap.
- **NAT rules**: moved under the Advanced menu; clicking a row opens a read-only view (fields
  disabled), editing is via the pencil action.
- **Update banner**: a bordered + shadowed clickable box with an SVG icon (not an emoji) and
  clearer wording.
- **Per-table page size** now remembers the user preference (`user_preferences.page_size`).

## [0.4.114] — 2026-06-09

### Added / Improved
- **DNS records page**: filter by server / type (type dropdown shows per-type counts), a source
  column showing the originating DNS server, IP matching resolved against the **actual IP value**
  in `ip_addresses` (fixes "the IP is in IPAM but shows no match"), and a column picker. DNS sync
  now keeps only **A / AAAA / PTR** (IP↔name mapping) — CNAME/MX/TXT etc. are no longer stored.
- **IP addresses**: new `in_dhcp_lease` (migration 0074) auto-managed by the OPNsense DHCP-lease
  sync; phpIPAM import now labels `discovery_source='phpipam'` (was mislabeled "manual"); OPNsense
  DHCP/ARP sync scopes the IP lookup to the firewall's subnets + `limit(1)`, fixing
  `MultipleResultsFound` on overlapping subnets sharing an IP.
- **Global search**: matches **partial MAC prefixes** (e.g. `bc:24`); DNS-record hits open
  Advanced → DNS records with the name pre-filled.
- **Racks**: the merged single-card view can export **SVG / PNG / draw.io** (all racks side-by-
  side); draw.io device boxes are now square to match the on-screen diagram.
- **AI chat**: the zero-dependency Markdown renderer now supports **GFM tables**.
- **MCP**: new `list_dns_records` tool; AI answers about subnet usage call real data instead of
  generic CIDR arithmetic.
- **IP request approval emails** include a **clickable link** (routes through login then back to
  the approval page if not signed in).
- The IP change log renders `switch_port` as **device@port**.

## [0.4.113] — 2026-06-09

### Added — IP request approval gate + notifications
- **Configurable approval policy** (Admin → IP Request Approval) with four modes so
  each site can pick: `admin only`; `administrators + designated users/groups`
  (single gate, any one approves); **parallel sign-off** (multiple gates, any order,
  all must approve); and **sequential multi-stage** (ordered gates, each with its own
  approvers — must pass gate 1→2→3…). Plus a separation-of-duties self-approval
  toggle. Per-step approvals are tracked in a new `ip_request_stage_approvals` table
  (migration 0073). Approve/reject authorize via the policy, not a blanket admin check.
- The request detail page shows **gate progress** (which gates passed / which is
  awaiting); each sequential gate's approvers are notified only when it's their turn.
- **Inline approve / reject** on the IP Requests list for approvers (pending rows),
  in addition to the request detail page.
- Request detail: fully localized; shows the subnet CIDR (linked) and, for pending
  requests, the **IP that will be allocated** — including the auto-picked first-free
  IP — which the **approver can change** before approving.
- **Approver notifications**: when a request is submitted, every approver gets an
  in-app bell notification and (if the Email channel is enabled) an email.
- **Notification channels settings** (Admin → Notification Channels): an SMTP/email
  channel (host/port/TLS/credentials/from, encrypted password, test-send button).
  Telegram / Slack / Teams / Nextcloud / Zulip are shown as "in development".

### Added — DHCP
- Subnet detail shows a **DHCP ranges** row when OPNsense DHCP pool ranges exist for
  that subnet (hidden when none), and a **DHCP-only** filter on its IP list.

### Added — DNS records (Advanced → DNS Records)
- New page listing DNS records pulled from integrated DNS servers, with search, an
  **IP lookup** (find records matching an IP — forward A/AAAA or the IP's PTR), and a
  **"no matching IP"** filter (A/AAAA records whose target isn't in IPAM).

## [0.4.112] — 2026-06-09

### Fixed
- **Manually-edited MAC was not protected from sync overwrite.** Unlike hostname
  (which records a `manual` observation), editing an IP's MAC in the UI only set
  `ip.mac` without marking `mac_source="manual"`, so the next scan/ARP sync could
  clobber it. The IP-edit endpoint now stamps `mac_source="manual"` on manual MAC
  edits (highest ARP precedence) and clears the source when the MAC is cleared.
  (Hostname's manual-vs-precedence path was verified correct end-to-end; if a
  manually-set hostname seems to vanish, hard-refresh — it is usually a stale SPA
  bundle, not the backend.)
- IP Requests toolbar: the status filter select was `small` while the buttons next
  to it were default size, so it sat shorter — aligned to the same height.

## [0.4.111] — 2026-06-08

### Security (MCP per-object RBAC scoping)
- Several MCP/AI list tools returned data outside the caller's visible scope.
  `list_racks` / `list_locations` / `list_sections` / `list_customers` now filter
  rows by per-object visibility; `recent_ip_changes` is scoped to visible subnets;
  `get_customer_summary` denies non-visible customers; `stats_overview` scales its
  per-object counts to the caller's scope and omits global-infrastructure counts
  for users without global read. `dns_lookup` is now treated as global-infra.
- Added a regression test suite (`test_mcp_rbac_scope.py`) covering zero-visibility
  denial, partial-visibility blocking global-infra tools, row scoping, and scoped
  stats counts.

## [0.4.110] — 2026-06-08

### Fixed (create-admin CLI)
- `create-admin` crashed with `MultipleResultsFound` when the given username matched
  one account and the email matched a different one (or an email was shared by more
  than one account). The lookup now queries username and email separately and
  reports a clear conflict instead of crashing.
- `--force-update` now also writes the supplied username/email onto the matched
  account — previously it reset only the password, so a new `--email` was silently
  ignored and the old address kept showing.

## [0.4.109] — 2026-06-08

### Added (MCP / AI tools)
- **10 new MCP tools** for entities that had no AI coverage: `list_circuits`,
  `list_providers`, `list_asns`, `list_tenants`, `list_contacts`, `list_ssids`,
  `list_cables`, `cable_trace`, `list_power`, `list_wazuh_agents`.

### Changed (MCP field coverage caught up with recent feature growth)
- `list_subnet_ips` now returns `effective_status` (online/offline) + `os_family`.
- `list_nat` now resolves real source/destination IPs and adds interface, aliases,
  disabled/no_rdr, ip_version (was name/proto/ports only).
- `get_subnet_detail` adds scan_method, scan agent, VRF, parent subnet, archived.
- `get_device` adds customer, fqdn, location, description, power ports;
  `list_devices` adds customer + fqdn.
- `list_vms` adds tenant/primary_ip/device + network interfaces.
- `get_ip_detail` adds `effective_probes`; `list_customers` adds title/address;
  `stats_overview` now also counts VMs / circuits / providers / ASNs / tenants /
  contacts / cables.

### Security (MCP RBAC hardening)
- The MCP HTTP/stdio dispatch (`tools/call`) previously applied **no** visibility
  gate. Both the MCP protocol and the NL-chat path now share one `authorize_tool`
  gate: zero-visibility users are denied all data tools, global-infrastructure
  tools (VLAN/VRF/NAT/firewall/DNS/VM/VPN/circuits/cables/power/Wazuh…) require
  admin-or-wildcard read, and mutating tools require admin. `tools/list` and the
  LLM tool list are filtered to what the caller may actually call.

## [0.4.108] — 2026-06-07

### Fixed
- **Effective status stuck "offline" after a scan-agent reported the host alive.**
  The agent `/report` endpoint stamped `last_seen_scanner` but never recomputed
  `effective_status`, so 實際狀態 reflected the last LibreNMS recompute (which could
  be stale by days). It now flips the IP to `online (scanner)` / `online` immediately
  on a fresh agent sighting and logs the offline→online transition.

### Added
- **Installer auto-creates the first `admin` account with a random password** and
  prints it once at the end (also saved to `/etc/jt-ipam/.admin-initial-password`,
  root-only). README documents the `create-admin --force-update` password-reset CLI.
- **Scan-agent installer now installs optional probe tools** (`nmap`,
  `samba-common-bin`, `avahi-utils`) so OS / NetBIOS / mDNS probes work out of the
  box; skip with `JT_IPAM_SKIP_PROBE_TOOLS=1`.
- **"Install help" popover next to unavailable probes** (scan-agent page and the
  subnet edit dialog) showing the exact package/command to unlock the probe.

## [0.4.107] — 2026-06-07

### Added
- **Subnet scope for Wazuh / Proxmox VE / AdGuard / DNS integrations** (migration
  0072), mirroring LibreNMS: each integration can be limited to specific subnets so
  syncs only match IPs within those subnets — overlapping subnets from unrelated
  systems no longer mis-attach hostname / OS / etc. Empty scope = global matching.

### Changed
- Subnet edit: scan-probe checkboxes are disabled when the selected scan agent
  can't run that probe (consistent with the scan-agent page).
- NAT table: clicking a rule row opens its detail (ignores the IP / device links).
- Subnet list: the tree expand arrow now sits on the CIDR column, not the pin column.

### Fixed
- switch_port tooltip shows the `device@port` form (not `device / port`).

## [0.4.106] — 2026-06-07

### Added
- **OPNsense firewall association scope** (migration 0071): each firewall can be
  scoped by location / customer / explicit subnets / interface→subnet map. Synced
  NAT rules then resolve their IPs only within the firewall's scope, so multiple
  firewalls reusing the same RFC1918 subnet no longer cross-attach to the wrong
  jt-ipam IP. Unscoped firewalls keep the previous global IP-string matching.
- NAT page: hovering an IP that's linked to a jt-ipam IP shows its details
  (hostname / status / MAC / vendor / subnet / customer / device / switch port …),
  lazily loaded; clicking opens that IP's detail page.

### Changed
- New child subnets inherit the parent subnet's customer (unit); this is now
  self-healing in `rebuild_subnet_hierarchy` (cascades through levels).
- Sidebar subnet tree: child subnets render as real nested, expandable nodes with
  connector lines (instead of a "↳" prefix); the parent label still opens its detail.
- Sidebar version label enlarged.

### Fixed
- Light-theme tooltips containing links (e.g. table ellipsis tooltips) used the
  green link colour on a dark tooltip; links now inherit the tooltip's light text.
- Firewall scope form: the customer / unit select showed "no data" (options weren't
  loaded).

## [0.4.105] — 2026-06-07

### Fixed
- Subnet save returned "Invalid request": the edit form sends `master_subnet_id`
  but `SubnetUpdate` (a strict, extra-forbid schema) didn't declare it. Added the
  field so editing a subnet works again.
- Subnet list: clicking a row's tree expand arrow navigated into the subnet instead
  of expanding its children; the row-click now ignores the expand trigger.

### Changed
- **Unified subnet edit**: the subnet list and the subnet-detail page now share one
  `SubnetEditModal` component, so both edit the same fields (section / VLAN / VRF /
  parent subnet / per-probe scan options / scan agent …) — they previously diverged.
- Left sidebar: subnets are nested under their parent (by `master_subnet_id`) within
  each unit group, indented with a "↳" marker (still clickable to open).
- Responsive top bar restyle: language / theme / account are pill buttons with hover,
  dividers around the bell, vertically centered with the search box; dropdown carets
  removed to save width.
- Graylog guide: suggested Title / Description / Name (`jt_ipam_adapter` /
  `jt_ipam_cache` / `jt_ipam_table`); both HTTPS and plain-HTTP (8088) lookup URLs;
  Line Separator `\n`, Ignore characters `#`, Refresh interval 300s, Expire-after-
  access 300s, Default single/multi value empty; an IP-field-name box that rewrites
  the pipeline rule live with Graylog field-name validation; rule named
  `jt-ipam enrich <field> -> <field>_hostname`; pipeline `lookup_value()` uses the
  table name; examples use `src_ip_hostname`.

## [0.4.104] — 2026-06-07

### Fixed
- Graylog DSV lookup endpoint now emits each IP only once. The same IP can exist
  in multiple (overlapping) subnets, which produced duplicate rows and made
  Graylog's "DSV File from HTTP" data adapter fail with "Multiple entries with
  same key". Keys are now de-duplicated (first by IP order).

## [0.4.103] — 2026-06-07

### Changed
- Top bar is now responsive: on narrow screens the language / theme / account
  controls collapse to icon-only (icon-triggered dropdowns) and the search box
  shrinks, instead of wrapping onto multiple rows.
- New subnets inherit the **customer (unit)** of their containing parent subnet
  when none is specified, so a child subnet lands under the same sidebar group;
  the sidebar subnet tree refreshes immediately after create / edit / delete.
- IP edit dialog: the OS probe now shows the same "intrusive" tag + tooltip as the
  subnet / scan-agent settings.

## [0.4.102] — 2026-06-07

### Changed
- Anomaly detection (MAC roaming): the "seen at" location now resolves the switch
  to its friendly name (LibreNMS sysname / hostname) instead of a raw device UUID,
  and the device / port / last-seen fields are rendered as an aligned grid.

## [0.4.101] — 2026-06-06

### Changed
- Dashboard **Section heat** card redesigned: the bar now reflects *average subnet
  utilization* (no longer diluted to ~0% by a single large sparse subnet), plus a
  per-section distribution of subnets by utilization band (full / high / medium /
  low) and a subnet/used summary — the card is far more informative.

### Fixed
- Racks: the leftmost rack's frame left border was clipped by the horizontal-scroll
  container; added small side padding so it renders fully.
- OS source precedence section title wording.

## [0.4.100] — 2026-06-06

### Added
- **OS source precedence** (scan agent / LibreNMS / Wazuh): a drag-to-reorder list
  (under Name / ARP source precedence) that decides which source wins when several
  report an OS; the IP detail OS row shows the resolved source.
- Racks: the "merged single card" toggle is now a clear two-option switch (separate
  cards / merged card); the merged card gains a shared front/rear toggle and an
  export action (combined device list).

### Changed
- Audit forwarding settings relabelled from "Graylog" to the generic "log server"
  (GELF / syslog work with any collector).
- IP list OS column shows on one line (icon never shrinks; label truncates when
  space is tight); hostname column narrowed to give other columns room.
- Scan-agent table column widths tuned so "last seen" no longer wraps.
- switch_port tooltip always shows the full `device@port` text (plus the
  low-confidence note when applicable).

## [0.4.99] — 2026-06-06

### Changed
- MCP tools surface the new scan/OS fields: `get_ip_detail` returns OS guess /
  family / source and excluded probes; `list_scan_agents` returns enabled /
  available probes and last source IP.

## [0.4.98] — 2026-06-06

### Changed
- OS detection result now shown as an **"Operating system"** field in the IP detail
  table (top), and as an optional **OS** column in the subnet IP list.

## [0.4.97] — 2026-06-06

### Added
- Scan agents: a **"Scan now"** action that triggers the agent to run all enabled
  probes immediately on its next poll (migration 0070); OS detection (`nmap -O`)
  is now exercised end-to-end on agent hosts that have nmap (runs as root).
- Probe-interval inputs show a unit (seconds) + human-readable equivalent.

## [0.4.96] — 2026-06-06

### Changed
- Scan agent probe-interval inputs now show a unit suffix (seconds) and a
  human-readable equivalent (e.g. 86400 -> "1 day").

## [0.4.95] — 2026-06-06

### Changed
- Scan probes: removed the port-probing options (TCP port liveness, port/service
  scan) and SNMP — jt-ipam does not expose credential-based or port-scan probes.
  Remaining: ICMP / ARP / reverse DNS / NetBIOS / mDNS / OS detection.
- Scan Agents: show an **"update available"** tag when an agent's reported version
  is behind the server's bundled agent (it self-updates from the jt-ipam server,
  not GitHub; the tag surfaces agents that failed to self-update).
- Topology: VPN-paired firewalls are kept near each other instead of being pushed
  to opposite far ends when subnet centers are spread apart.

## [0.4.94] — 2026-06-06

### Added
- **Configurable scan probes** with a three-layer model (migration 0069):
  - **Probe catalog** (icmp / tcp / arp / rdns / netbios / mdns / os / ports)
    with per-probe class (light/heavy), default interval, and intrusiveness; default
    is **ICMP only**. Heavy probes (OS / port scan) run on their **own long interval**,
    never at the ICMP cadence.
  - **Scan agent**: pick which probes it may run + per-heavy-probe interval; the agent
    self-reports which probes it can actually perform (others greyed out).
  - **Subnet**: choose the probes to run (`scan_method`).
  - **IP address**: skip specific probes (the old "exclude from ping" generalised; icmp
    stays in sync). The IP detail page shows the **effective** probe set
    (subnet probes − IP skips ∩ agent capability).
- **OS detection display**: scan results are normalised into an OS family
  (Windows / Linux / macOS / BSD / network / printer / storage / hypervisor / …) and
  shown with a per-family SVG icon (IP list column + IP detail), tooltip = raw string.
- Agent poll/report protocol carries per-subnet probes, per-probe intervals, per-IP
  skip overrides, and richer results (rdns / os_guess / open_ports / probes_run); the
  bundled agent gains tcp/arp/rdns probes and fast/slow scheduling.

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
