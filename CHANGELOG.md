# Changelog

All notable changes to this project are documented here. The format is loosely
based on [Keep a Changelog](https://keepachangelog.com/); versions track
`frontend/package.json` / `backend/app/version.py`.

## [Unreleased]

### Added
- DNS comparison groups: put DNS servers whose content should match (primary/secondary, AD-integrated and so on, any number) in one group on the DNS page, or pick the group in the server form. jt-ipam does not sync records between the servers; after every member's pull it compares the records already pulled: a zone with records present on only some members counts as one difference, otherwise A, AAAA and PTR records are compared by normalized name, type and value (case, trailing dot, relative names and IPv6 spelling no longer matter; TTL is not compared). Different products can be mixed (for example Windows DNS and UCS); AD service locator data (`_msdcs`, `TrustAnchors`, `DomainDnsZones`, `ForestDnsZones`), which Windows and Samba register differently, is not compared. Unbound (OPNsense) holds host overrides rather than full zones, so it can only be grouped with other Unbound servers; saving a mixed group is refused with the reason. When only some zones are replicated, or one server also holds zones of its own, list them under "Zones not compared" in the group settings or click "Stop comparing this zone" in the difference list; changing members or excluded zones compares the group again right away. A difference is confirmed only when each server missing it has been pulled again at least the grace period (default 30 minutes, for replication delay) after it appeared and still lacks it (members are pulled one after another, so this uses the data time and never judges a server by data it has not refreshed yet); it is listed with which servers have it and which do not, and notifies administrators through the existing notification settings (events "DNS comparison group mismatch" and "DNS comparison group consistent again"; one batch notifies once, new differences notify again). While a member's last pull failed or it was never pulled, the group shows "Incomplete data" and does not judge. "Check now" compares immediately; the group table and the difference list can be filtered, sorted, exported and have selectable columns; groups and their changes are audited, and only administrators can manage them.
- Identical records within a comparison group now show once: the DNS records page (with a "Merge identical records in comparison groups" switch, on by default) shows the servers that hold each record and the group; anomaly detection "DNS points to unregistered address" and IP change assessment also show one row or finding per record with every server listed (evidence still lists each server's record). Servers not in the same group are never merged.
- Anomaly detection, "DNS comparison mismatch": confirmed differences from all groups, with the zone, name, type, value, the servers that have it and the ones missing it. The AI tool `list_anomalies` returns it too.
- Verified against real servers: PowerDNS as primary with BIND9 and Technitium as secondaries (zone transfer) in one group: consistent, then after replication broke only the server actually missing the record was reported, then a resolved notice once it recovered.
- Upgrade: migrations 0203 and 0204 add the tables and columns; existing records get their normalized columns on the next pull, and until then groups show "Incomplete data". Nothing to configure on the host.

### Changed
- Background jobs now report back where you started them: Pull / Sync now on every integration, subnet CSV import and device import open a "Background jobs" panel at the bottom right with a spinner, elapsed time and progress, then the result summary or the full error (with a copy button), and refresh the page's list when done. You no longer need to open the jobs page and then the logs to find out what happened. The panel follows you across pages, resumes after a reload, keeps failures until you close them and collapses successes after 20 seconds.
- Wide tables keep the "Actions" column pinned to the right at desktop width, so the buttons stay in reach while you scroll sideways with a mouse (applied to every table at once; phones are unchanged).

### Fixed
- Jobs page: the result of a phpIPAM migration with changes failed to display (a variable name clash broke the summary).
- Univention UCS: PTR records in reverse zones were never read (only A and AAAA from forward zones), so reverse records from UCS were missing everywhere. Reverse zone names now come from the zone's DN, which also makes IPv6 reverse zones correct.

## [1.0.6] - 2026-10-10

### Security
- Scan agents: an agent with no assigned subnets can no longer update any IP. The report endpoint used to filter by subnet only when the agent had some, so an agent without any skipped the filter entirely and could change the MAC, OS, hostname and last-seen time of any IP. The response's `skipped_no_subnet` says how many results were skipped. The endpoints of all three agent types (scan, certificate, RustDesk) are also rate limited per agent (1,200 requests a minute by default; allowed through when Redis is unreachable).
- AI prompt injection: the main AI chat, the IP investigation narrative and the AI review now tell the model that data and tool results are not instructions, and large data blocks are fenced in `<data>` (fence strings inside the data are broken up first). The rule lives in one place and the triage card, rule-change review and IP change assessment use the same text; a guard test fails if a new caller of the model does not use it.
- Every encrypted secret is bound to its purpose (AES-GCM additional data). The GeoIP license key and the SSH private key for phpIPAM migration were not, so their ciphertext could be moved to another field and still decrypt. Migration 0198 re-encrypts the two existing values, and a guard test checks every encrypt and decrypt call. The system export now carries the GeoIP license key (it used to carry the source host's ciphertext, which could not be decrypted elsewhere).
- Audit: subnet CSV export, device list export (the import template with all devices), report PDF, system export downloads and viewing the certificate agent key or the external MCP key are now audited. Exports generated in the browser (all table exports, reports, topology and rack diagrams, cable trace, raw IP identify results, system logs and the diagnostics report) are reported by the browser when saved (screen, format, rows; `POST /api/v1/export-events`). Two guard tests: endpoints that download files or reveal secrets must audit, and the frontend saves files through a single helper.
- API tokens: the `object_filters` field is gone. It could be set and was stored but was never enforced, so a token looked restricted when it was not; sending it now returns 422. The per-token rate limit (600 requests a minute) was defined but never applied; REST and MCP now share one counter; when Redis is unreachable requests are let through with a warning (as for agents), so integrations using tokens do not all fail at once.
- Sign-in now uses server-side sessions. The refresh token lives only in an HttpOnly cookie (unreadable by JavaScript, limited to `/api/v1/auth`, SameSite=Strict, and refresh and sign-out also require an `X-Requested-With` header), and the database stores only its hash. Every refresh issues a new one; using a replaced token again (after the 60-second grace for several tabs refreshing at once) is treated as theft, so the whole session is revoked and administrators and the user are notified. Access tokens carry the session ID, so signing out, being signed out by an administrator, deactivation and an administrator password reset take effect immediately (signing out used to do nothing, and refresh tokens stayed valid for 14 days). Changing your password signs out your other devices. Deactivating an account also revokes its API tokens (reactivating it used to bring them all back). Access tokens moved from localStorage to the tab's sessionStorage, and SSO sign-in no longer puts tokens in the URL. Settings → Security lists your signed-in devices and can sign out one or all others; the users page can sign someone out everywhere. Everyone signs in again once after upgrading (migration 0200).
- Two-factor authentication (TOTP): administrators can require it for administrators or for everyone under Sign-in security on the users page. People who are required but have not set it up do so at their next sign-in (they are not locked out) and cannot turn it off. SSO trusts the identity provider's own multi-factor authentication by default, with an option to apply it as well. Turning it on creates 10 recovery codes (each works once, stored hashed, can be regenerated) for signing in when the authenticator is lost, and administrators can reset someone's two-factor authentication; when the only administrator loses both, run `python -m app.cli.bootstrap reset-mfa --username <name>` on the host (it also revokes every sign-in and is audited). The same code cannot be used twice (it used to work again within 90 seconds), and wrong codes now count toward account lockout (they did not, so someone with the password could keep guessing).
- Consoles: when an account is deactivated, signed out by an administrator, the sign-in that opened the console ends, or the person loses console rights on that IP, open SSH, SFTP, RDP, VNC, noVNC, BMC and RustDesk web connections close within 30 seconds, the screen says which of these happened, and an audit record is written (they used to be checked only when opened and could stay open until closed).
- Audit log: the database now blocks TRUNCATE as well (migration 0201; a single statement used to wipe the whole chain). Install and upgrade give the audit table to a database role that cannot sign in (`jt_ipam_audit_owner`), so the app role can only read and append and cannot disable the protecting triggers, rewrite, delete or truncate (the app role used to own the table and could disable the triggers). Upgrades hand it back for the migration run and take it away right after. After restoring a backup or with an external database, run `sudo bash /opt/jt-ipam/scripts/jt-ipam.sh harden-audit`; the diagnostics page and `doctor` check it. With audit forwarding configured, every record carries its own hash and the previous one, and the scheduled anchors are forwarded too, so the receiver holds a chain it can check independently (anchors used to be written only to a local file and the system journal).
- Rack embed and Graylog DSV tokens are now encrypted at rest (they were stored in plain text), expire (choose 30, 90, 180 or 365 days when regenerating; existing ones get a year, and administrators are notified before expiry), and are no longer returned with the settings page: Show or copying a URL fetches them and is audited (migration 0202). The Graylog DSV plain-HTTP port 8088 answers only after "Allow plain HTTP" is turned on (off for new installs; sites already using DSV before the upgrade keep it on), allowed source addresses can be restricted, and the token can also be sent in an `X-Auth-Token` header. The nginx access log no longer records tokens in URLs (upgrades patch existing site configs).
- Daily backup encryption: after setting a passphrase under System settings → Daily backup encryption, each day's backup (pg_dump, backend.env, TLS, uploads) is packed and encrypted into one `.jtbak` file (scrypt-derived key, chunked AES-256-GCM, so reordered, edited or truncated files fail to decrypt) and the plain copy is removed; the database and the key that decrypts it used to sit in the same directory in the clear. The decrypt tool needs only Python and `cryptography`, so it works even when the host is gone: `python3 backend/app/services/backup_crypt.py decrypt <file> --out <dir>`. The diagnostics page and `doctor` warn while no passphrase is set.
- Outbound address checks now cover more than HTTP. Console targets (SSH, SFTP, RDP, VNC, BMC) cannot be loopback, link-local (including cloud metadata) or reserved addresses (IP records can be created by anyone with write access, so a record for 127.0.0.1 used to turn jt-ipam into a pivot); the ticket request returns 403 with the reason. LDAP, SMTP, RADIUS, audit forwarding, jump hosts, phpIPAM migration and noVNC to PVE resolve and check the target before connecting (cloud metadata, link-local and multicast are blocked; loopback such as a local mail relay is allowed). The existing checks for BIND 9, Windows DNS, Windows DHCP and certificate SFTP sources moved to the same place with unchanged rules, and WinRM now re-checks on every connection. The administrator allowlist (`OUTBOUND_ALLOW_CIDRS`) still takes precedence. A guard test checks that every non-HTTP connection goes through the check.
- Topology: accounts granted only some objects used to get nothing (403); they now see the devices and subnets in their scope and the links whose both ends are in scope. VPN tunnels and virtual machines are global data and are not shown, and the page says so. The AI tool `get_topology` follows the same rule.
- AI chat confirm: the action runs through the same permission gate as when the model proposed it (it used to rely on each tool checking for administrators itself).
- CI runs an OWASP ZAP baseline scan: on every push it spiders and passively scans the production frontend build behind the shipped nginx config, and any alert not listed in `deploy/zap-baseline.conf` fails CI (it used to be run by hand before releases).
- The external MCP key expires: choose 30, 90, 180 or 365 days (default 90) when generating or replacing it, and expired keys are rejected. Existing keys get 90 days after the upgrade (migration 0199). API tokens and the MCP key are notified 14, 7 and 1 days before expiry and on the day (API tokens notify their owner, the rest notify administrators; the notification settings page can turn it off or add email).

### Added
- Compliance mapping for ISO/IEC 27001:2022 and ISO/IEC 42001:2023 (`docs/COMPLIANCE.md` in three languages and a page on the website, linked from the home page and the READMEs): the controls jt-ipam already implements, each with a test or setting to verify it and the related ISO/IEC 27001:2022 and ISO/IEC 42001:2023 Annex A controls, an index of clauses and controls, what the adopting organisation has to do itself (also with its clauses and controls), and suggested acceptance cases. Only implemented capabilities are listed.
- Anomaly detection, "One host on several NICs (ARP flux)": when every trustworthy MAC on a conflicting IP belongs to the same device, the row is no longer an IP conflict (before, it silently disappeared). It shows the host, which NIC each MAC belongs to (registered IP or port name) and the fix (`net.ipv4.conf.all.arp_ignore=1`, `net.ipv4.conf.all.arp_announce=2`).
- Anomaly detection, "Subnets sharing one layer 2": an IP in one subnet answered by a MAC in use in another subnet, or one VLAN on a switch learning MACs from two subnets. MACs registered in several subnets (router subinterfaces) or registered but not in use are not used as evidence.

### Changed
- IP conflicts and IP changing MAC often no longer count MACs that are likely misread or stale. Likely misread: reported by one source, no vendor, and spliced from two other MACs of the same IP (or one byte away from a MAC several sources agree on), typical of an SNMP walk catching a router's ARP table mid-change. Likely stale cache: a locally administered MAC reported by one device while several sources see another MAC. Both stay listed with a marker. Each MAC now shows how many sources reported it and, on hover, which device's ARP table, interface and when; IP conflicts get a confidence (low when the other machine is reported by a single source). The MAC history page shows the same per IP.
- AI tools: `list_anomalies` documents its result shape and returns `items` as an array (with `kind`) when a single kind is requested; `get_ip_history` lists one ARP entry per MAC with its reporters, reporter count and suspect marker.

## [1.0.5] - 2026-10-09

### Changed
- Windows DHCP and Windows DNS (WinRM): new connections default to HTTP 5985 instead of HTTPS 5986, because the Windows Server firewall blocks 5986 by default; the settings forms say so, pre-select "HTTP (5985, default)" and show "Verify TLS certificate" only for HTTPS. Over HTTP the content and credentials are always NTLM-encrypted (Windows DHCP now requires it explicitly too; if encryption is not possible the connection fails rather than falling back to clear text). Existing connections keep their transport: migration 0197 records HTTPS on Windows DNS servers that had no transport saved, and importing an older export file does the same.
- Tasks page: background task types are shown by name (for example "Check Point gateway sync (DHCP, ARP, leases)" instead of `checkpoint_gaia.sync`), with the internal type underneath; the type filter uses the names too.
- Check Point management server settings now point to the Gateways tab for the gateways' DHCP settings, ARP table and leases, with a button that opens it.

### Fixed
- Table exports showed internal IDs instead of names for linked fields (issue #50: a circuit's provider and type came out as UUIDs such as `adbedc6c-…` instead of "Hinet" or "DSL / VDSL"). When a column's raw value is an ID and the screen shows a name, the export now uses that name (blank when there is none); this covers every table with linked fields (devices' location / rack / unit, NAT addresses, subnets' unit and more). Circuit status and bandwidth also export as shown on screen instead of `active` and raw kbps.

## [1.0.4] - 2026-10-08

### Changed
- Check Point gateways (Gaia API): the ARP table and DHCP leases are now read by default. When the account may not run commands (a read-only Gaia role), the Gaia API has no `run-script`, the gateway has no lease file, or the gateway's DHCP server is off, those parts are marked as skipped with the reason (an info icon next to the gateway's tag) instead of failing the sync or raising an alert; the DHCP settings still sync. A gateway whose DHCP server is off no longer has its leftover leases counted as live. Upgrading turns this on for connections made with 1.0.3 (migration 0196); untick "Read the ARP table and DHCP leases" in the Gaia settings to stop it.
- Export buttons now grey out and show a spinner from picking a format until the file is saved, and ignore a second pick meanwhile: IP change assessment (report and relation graph), every table export button, rack diagrams, the rack room toolbar, IP topology and cable trace.
- Dashboard racks card: clicking the rack drawing itself (not only its title) opens that rack; devices inside still open the device page.
- AI chat window: the expand/collapse button now sits before the close button, which stays rightmost; the close button's tooltip says "Close".

### Fixed
- IP change assessment relation graph and IP topology: the graph could come out taller than the window (about 1,700 px on a 720 px screen) when its height was measured mid tab switch, pushing the assessed IP off screen. A measurement taken before the layout settles is now discarded, and the height never exceeds the visible area.
- AI chat that took a while (several tool rounds, a slow model) ended in a network error with no answer: the stream sent nothing while the model was working and the reverse proxy's read timeout (30 seconds in the bundled nginx configuration) cut the connection although the backend was still running. The AI chat, IP investigation narrative and traceroute streams now send a keepalive comment every 10 seconds while waiting; no proxy setting needs to change. Closing the chat while it is still working stops the model calls.

## [1.0.3] - 2026-10-08

### Added
- **Check Point integration (Beta, phase 1)** (new page under Admin → External integrations, R81.20):
  - Reads the management server (Security Management Server or Multi-Domain) through the Management API, read-only: the login asks for a read-only session and always logs out. One management server covers every gateway and policy package it manages; for Multi-Domain list the domains (one login each), and the policy packages can be narrowed.
  - Syncs the gateway list, network objects (host, network, address range, group, group with exclusion), access rules (sections flattened, inline layers shown as "parent › inline layer", negated source/destination and hit counts kept) and destination NAT (into the shared NAT table, filterable as Check Point on the NAT page). Large policies are paged (500 per request) with upper limits; a section that fails keeps its previous data.
  - Feeds the IP detail firewall lookup (rules and objects link to the matching entry), rule change detection, IP change assessment (firewall rules and NAT), the rogue-DHCP allowlist (gateway addresses), the AI chat (`list_firewalls`, `list_checkpoint_rules`, `list_checkpoint_objects`), system export/import (the API key or password stays encrypted) and the scheduled sync.
  - Authenticate with an API key (recommended; a SmartConsole administrator with the Read Only All profile) or a user name and password. The key is never returned or shown in errors. Migration 0194.
  - Built against the official API reference and a mock server; not yet verified on a real system.
  - **Phase 2: the gateways' Gaia API** (Gateways tab → "Set up Gaia connection", or "Add gateway manually"; migration 0195). Each gateway gets its own Gaia account. Read only (on by default): the gateway's DHCP server settings; pools minus exclusions become DHCP pools, and the default gateway and DNS handed to clients feed IP change assessment ("DHCP subnets" dialog). Optional, off by default: "Allow fixed read commands" uses `run-script` to run two fixed commands, the ARP table (`ip -s neigh show`; the time each neighbour was last confirmed becomes `arp:checkpoint` liveness evidence, PERMANENT and failed entries are ignored) and the lease file `/var/lib/dhcpd/dhcpd.leases` (lease flag, MAC and host name; skipped above 32 MB). The Gaia API has no read command for either, so this needs an account that may run commands, and the form says so. Test connection reports the Gaia API version, DHCP subnets and whether commands may run. Deleting a Gaia connection or the management server takes back its pools, lease flags and host names. VPN status is not synced yet.
- **Technitium DNS Server (DNS and DHCP)**:
  - DNS: "DNS servers" has a Technitium type that pulls A/AAAA/PTR into the same forward/reverse consistency and host name sources as the other DNS servers. Read-only, nothing is written back. The test connection reports the version, the account and how many zones are readable. Technitium grants access per zone and does not list zones the account cannot read; the screen says so.
  - DHCP: a new "Technitium DHCP" integration (Admin → External integrations). Scopes minus their exclusions become DHCP pools, reservations go to DHCP reservations, and leases only tag existing IPs with the lease flag, MAC and host name, without creating IPs. Disabled scopes are not written. The Scopes tab lists the gateway, DNS, NTP, WINS, domain and lease time each scope hands to clients.
  - It connects with a Technitium API token that only needs view permission. The token is sent in the POST body, never in the URL, and never appears in error text. An HTTP-to-HTTPS redirect is not followed; the error names the URL to use. A token with more rights than needed gets a warning.
  - IP change assessment: an address handed out as the default gateway of an enabled scope is critical; as DNS/NTP/WINS or as the DHCP interface address it is high. Reservations, pools, leases and the console URL are assessed like other integrations. Rules version 4.
  - Rogue DHCP detection allows the Technitium console host and each scope's DHCP interface address; deleting the integration takes back what it wrote to the shared tables.
  - Verified against the official container (15.6): read-only account, real DHCP leases, zone permissions. Migration 0192.

### Changed
- **Docs and website**: an IP change assessment card on the home page, IP change assessment and ISOinsight in all three READMEs; the feature list covers graph layouts and exports and report exports; troubleshooting gains "exporting a PDF drops other requests" and "Windows DNS certificate error or 5986 unreachable", and the RDP entry now reflects the guacd default; the remaining full-width slashes on the site are half-width. New guard `tests/test_docs_site_coverage.py` (trilingual paragraph counts, every integration documented, major features, full-width slash and em dash) and release checklist item 5k.
- **IP change assessment and IP topology, another round**: the relation graph opens in "By impact" (rings); the relation graph and the IP topology fill the window (measured from the real layout instead of a guessed offset; pages with a full-height graph shrink the 88px bottom padding to 16px and the thumbnail moves to the bottom left, clear of the AI chat button); the graph exports SVG (vector, no extra package, rings and titles included); data gaps are a table with a header and row lines (category, description, affected analysis); tasks are grouped into pre-checks / change / verify / rollback sections, each with a colour bar, count and one-line description; old "OCS #6" labels drop the duplicated prefix when read.
- **IP change assessment: "old address still in use" also looks at firewall ARP tables and VPN sessions**: it used to read only the scan agent, LibreNMS, Wazuh, Zabbix and OCS times, so a machine only a firewall could see (no agent, not monitored) gave no warning when renumbered. The same sources as the liveness decision count: those that expire (each firewall's ARP table, current VPN sessions), not DHCP leases. The reason text names the sources as "ARP table (OPNsense)" and so on.
- **Report PDFs are real files, and the layout is redesigned**: "Export → PDF" used to open the browser's print dialog; the backend now lays the report out and the browser downloads a .pdf with an embedded Chinese / Japanese font subset (it displays on computers without CJK fonts, and the text can be selected and searched). PDF, DOCX and ODT reports share one layout: A4 portrait, a title block with an accent rule, the overview in two label/value pairs per row, an accent bar before each heading, a dark header row repeated on every page, zebra rows and page numbers in the footer. The impact list goes from 8 columns to 4 (disposition / severity, object, reason, impact / evidence) and tables are exactly the text width, so they no longer run past the right margin; the first cell of blocker and review rows is red and orange. New `POST /api/v1/reports/pdf` (layout only, reads no data; capped input, per-user rate limit and at most two renders at once). Install and upgrade add `fonts-noto-cjk` (about 60 MB download); if apt cannot reach a mirror during an upgrade it only warns, PDF export then says the font is missing and DOCX / ODT are unaffected. New Python dependency `fpdf2` (LGPL-3.0).
- **Dashboard number cards are links**: Sections, Subnets, Allocated IPs and IPv4 capacity open their lists; "24h audit events" opens the audit log narrowed to the last 24 hours (the tag can be removed). Only clickable cards lift on hover; for non-admins the audit card is not a link.
- **Windows DNS (WinRM) can use HTTP 5985 or a self-signed HTTPS certificate**: the Windows Server firewall opens only WinRM HTTP 5985 by default, and an HTTPS 5986 listener usually has a self-signed certificate (a customer saw `CERTIFICATE_VERIFY_FAILED`). The DNS server settings now have "WinRM transport" (HTTPS 5986 or HTTP 5985, with an optional port) and "Verify TLS certificate" for HTTPS. Over HTTP the content and credentials are always NTLM-encrypted; if that is not available the connection fails instead of falling back to clear text. An untrusted certificate now says how to fix it.
- **IP change assessment graph (second round)**: nodes carry type icons (DNS record, firewall rule, NAT, virtual machine, monitoring…) with a legend above; groups are now one per type (no longer split by impact); four layouts can be switched: radial, tree (left-to-right cards), by impact and force, remembered per browser; a thumbnail at the bottom right moves the view on click or drag; node details open in a panel on the right (they used to appear below the graph, out of sight); "References only (no outage propagation)" is now plain language: a thick line means the item is cut off when the other end goes down, a thin line means the address is written in its settings and must be changed with it; PNG export; the clipped right border is fixed.
- IP change assessment result page: the impact list's "Object and reason" shows a type badge and name, then "Why: …"; expanded details are indented with a coloured left bar and tint, and the open row shares the tint; data gaps and History are aligned tables.
- **IP change assessment AI**: while running it shows the step and elapsed seconds (queued, gathering data, waiting for the model, checking, asking the model to fix it); a fallback to the template summary says why; references show the object name as well as the number, with the reason on hover, folding long lists. Migration 0193.
- **IP change assessment export**: reports as PDF, Word (.docx) or OpenDocument (.odt) with the overview, AI summary, impact list, data gaps, tasks by phase and reviews; tables as Excel (.xlsx) or OpenDocument (.ods) with one sheet per tab; Markdown and JSON as before. All generated in the browser with no extra packages.
- **The IP change assessment graph is readable**: an IP is often referenced by twenty or thirty DNS records or reverse proxies, and each used to be its own node with text on every line, so forty-odd names and "References only (no outage propagation)" piled up. Three or more objects of the same category and impact linked to the same object now collapse into one group (for example "DNS (26)"); clicking it lists every member below the graph, and "Expand on the graph" or "Expand all groups" draws them when needed. Objects on a path (which host a VM runs on, which VM a service needs) are still drawn one by one. With many lines the relation text shows only on hover or for the selected node, text on lines to the target sits at the outer end, the layout accounts for label width, and there is "Fit to view". The kind shown for a clicked node used to be a raw translation key for types like DNS records; it is now the category name.
- Cancelled plans are dimmed in the IP change assessment list.
- **ISOinsight schedules are on by default**: the schedule switch used to stay disabled until a preview succeeded. New sources now have the schedule on and the switch can be changed at any time; until a preview with the current settings succeeds, scheduled turns do not sync (no connection, no sync record), the source list shows "needs preview" and the last result says the schedule is waiting for a preview. On upgrade, existing sources that were never previewed and have the schedule off are switched on (it could not be turned on before, so it was not the admin's choice); previewed sources with the schedule off are left alone. Migration 0191.
- IP details "Last seen by source" lists only configured integrations: without AdGuard there is no empty "AdGuard settings" row, and the same goes for LibreNMS and ARP (rows with data are still listed). This also works for subnet-only readers (the IP details now say which integrations exist, booleans only).

### Fixed
- **Exporting a PDF killed a backend worker** (prod, found on release day): fpdf2 (PDF reports) imports numpy, whose OpenBLAS calls `mbind` as soon as it loads; the backend unit's system-call filter blocks that group and its default action kills the process, taking other requests on that worker with it. Install and upgrade now write a systemd drop-in (`20-numa-syscalls.conf`) that allows only the three calls that set the NUMA placement of the process's own memory. Dev and test environments have no such sandbox, so green tests could not catch it; a guard test now does.
- **The version info page showed the unused FreeRDP engine packages as red "Not installed"** (customer on Ubuntu 26.04 desktop): the default engine is guacd, and the FreeRDP packages (xfreerdp, Xvfb, ffmpeg, xclip, about 150 MB) are installed only when the FreeRDP engine is selected, upgrades included, yet the page listed them as missing and said "re-running upgrade installs them". When the engine is not selected they now show "Engine not selected" and stay out of the missing warning; once selected, missing means missing.
- Upgrades no longer print two red pip ERROR lines when the optional aardwolf fallback engine cannot be installed (always the case on Python 3.14): one explanatory line instead, with pip's output kept in a temporary file named on that line. RDP / VNC consoles use guacd and are unaffected.
- IP change assessment: integration endpoints used to be labelled "<type code> <name>", so integrations without a name field such as Proxmox read "proxmox proxmox"; they are now "<product> <name>" (the cluster name for Proxmox), and older results are tidied when read. OCS uses the host name, and an existing record on the new address shows its host name.

## [1.0.2] - 2026-10-08

### Added
- The version page's optional dependencies now list the MAC vendor database (OUI) and GeoIP. When the OUI table is empty, the vendor column in IP lists, MAC history and anomaly detection is blank everywhere, and nothing used to say so; it is now listed like Recog, with its status and last update date, and the name links to its page. GeoIP needs your own MaxMind account: when it is not set up it shows "not configured" and is not counted as missing.
- **IP probes can be cancelled**: a stuck probe (for example when an agent's report was lost during a deploy restart) used to wait 9 minutes for its timeout. The probe page now has "Cancel probe" while running; the task is recorded as cancelled without a notification, results the agent sends later are not recorded, and a new probe can start right away. Audited as `identify_cancel`.
- **"Unmanaged" cells in the IP grid**: with the scan agent's auto-create off, live addresses missing from IPAM used to be dropped, and their cells looked exactly like free ones. No IP record is created now; the sighting is remembered instead (address, subnet, which agent, first and last seen, plus MAC and host name when known), and the cell is drawn as a dashed orange "Unmanaged" box (faded when not seen for over a day), with a count in the legend and who saw it, how long ago, MAC and vendor on hover; clicking the cell still registers it. Unregistered addresses in LibreNMS's ARP table count too. Unauthorised-IP detection now reads these sightings as well (it used to look at LibreNMS ARP only). Only addresses in subnets assigned to that agent with scanning on are kept, and sightings not seen for 30 days are removed. Migration 0186.
- **New device type "Workstation"** (desktops, laptops and other user computers), set automatically: a device's type used to be decided once at creation, and "Create device" on the IP page always wrote "other", so a Windows 11 laptop was "other". It is now derived from the operating system reported for the device's IPs (Wazuh, RustDesk, OCS, then the scan agent's nmap guess, then the OCS chassis type): Windows client editions and macOS are workstations, Windows Server is a server, Linux is left alone because its role cannot be told, and conflicting sources are not guessed. Only devices that are "other" and were never set, or were set automatically last time, are changed; types given by a person, an import or an integration are never touched (the new `devices.type_source` column records who set the type). Laptops and desktops are not told apart: without OCS no source can, and the chassis type is on the OCS card. LibreNMS and phpIPAM workstations used to become servers and now map to Workstation; device import recognises workstation, PC, laptop, desktop and their Chinese and Japanese names. Migration 0185.
- The dashboard racks card has a size setting (a slider in its settings, 50% to 300%, stored with the account): since the whole row is drawn to real proportions, a short rack next to a tall shelf looks small.
- Diagnostics has a new "Data statistics" section: how many records of each kind there are (sections, subnets, IPv4/IPv6, devices, racks, DNS, DHCP, ARP, firewall rules, audit log and more, in five groups). Counted on this server only and never sent anywhere; on very large sites a big table that times out shows the database's estimate, marked "approx.".
- The operating system reported by RustDesk clients is now an OS source (only for devices matched to that IP), last in the OS source precedence by default; existing sites get it appended at the end on upgrade.
- The Tasks page can be searched (type, target, error) and filtered by type, status and trigger; the conditions go to the server, so paging and totals stay right.
- The Tasks page now shows reports from the RustDesk and ISC DHCP agents, and the OUI, Recog and GeoIP database refreshes (scheduled and "update now"). Only integrations the server pulls on a schedule used to leave a record. Agent reports keep one row per source like scheduled syncs, so they do not flood the list, and deleting an integration removes its row (for every integration).
- The IP detail header has a status light left of the address, showing at a glance whether it is online, recently seen or offline, with when each source last saw it on hover (the same light and rule as the IP lists).
- **The IP probe page shows data that was already collected but never displayed** (no extra packets, no wider scan): the Windows computer name, domain or workgroup (from RDP and SMB); for each TLS port the certificate subject, issuer, expiry (flagged within 30 days or when expired), fingerprint and key; SSH host key fingerprints; open, closed and filtered port counts; duration, hop count and estimated uptime; where each name came from (reverse DNS, NetBIOS, mDNS, nmap, RDP, SMB, certificate); and the reasons behind the result (for example an OS fingerprint below the accuracy threshold, no OS fingerprinting because the agent is not root, services named only from the port number). "Compared with the previous probe" now also covers the MAC, OS, device type, names, SSH host keys and certificates, and a changed key or certificate comes with a warning that the host may have been reinstalled, replaced or impersonated. Scan agent 1.17.4 (auto-update) also reports filtered port counts, hop count, uptime, certificate validity and SHA-256 fingerprint (computed from the certificate, which is not sent), SSH host key SHA256 fingerprints, and which script outputs were cut.
- **IP change assessment** (first stage: IP renumber and device decommission; off by default, turned on in System settings): before changing an IP or removing a device, find what references it. It only assesses: no device or setting is changed. Start one with "Assess renumbering" on an IP page, "Assess decommissioning" on a device page, or from the new IP change assessment page: pick the subnet first (narrowed by customer or section; only subnets you can edit are listed), then the IP. An address you have no permission for says so ("no permission on the address or its subnet"), and unregistered addresses, unmanaged addresses and a new IP in a subnet you cannot see are each explained. It checks whether the new address is usable (already registered or reserved, DHCP reservation or lease, recently seen in use, cooldown, what to check when moving subnets) and what references the old one: DNS A/AAAA/PTR, DHCP reservations and pools, rules and aliases on five firewall vendors (groups resolved recursively with cycle detection, scoped per VDOM/vsys, disabled and negated rules marked separately), NAT, Zabbix/LibreNMS/Wazuh/OCS/RustDesk, VMs, certificate IP SANs, subnet gateways and DNS servers, jump hosts and integration endpoints, and text that mentions the address; decommissioning also covers hosted VMs, cabling, power, rack, circuits and VPNs. Every finding carries evidence (observed and collected times apart), a rule version and an evidence strength; missing data (integration not set up, stale or never synced, outside your permissions, CNAMEs not stored, and so on) is listed as gaps, and zero findings never reads as "safe". Plans have revisions, review (blocking items cannot be approved, incomplete data allows only accept-risk with a reason, no self-review by default), a re-analysis that must match before maintenance starts, and verification; tasks are generated from the result (pre-checks, change, verify, rollback) and are manual work. Export to Markdown or JSON. AI can summarise, answer questions and draft tasks from the data you can see only, with citations validated and a template fallback; everything works with AI off. New impact_* MCP tools (creating a plan needs a server-signed draft token). Every read is filtered by current permissions, including counts, the source list and template tasks (hidden items never show up as numbers). **Reviewers** are set in System settings → IP change assessment (users or groups): with a list, only people on it who can see the target may review and they are notified on submission; without one, anyone with edit permission on the IP or device may review and admins are notified; the creator is notified of the result (two new notification events). The plan page shows who it was sent to, only eligible people see Review, and the list has an "Awaiting my review" filter. Migration 0187.
- Unmanaged addresses now get their own row in a subnet's IP list (they used to disappear into the free ranges): a dashed orange marker, an "Unmanaged" tag, host name, MAC and vendor, and who saw it and when in the description column. Clicking an unmanaged cell in the grid or its row opens a page for that address (there is no record, so it lists only what the sources that saw it report) with Probe, Add and Back at the top right; after adding, it switches to the new record's IP page.
- **ISOinsight integration (Beta)** (new page under Admin → External integrations): logs in to the ISOinsight HTTP(S) interface (`/api/logon`, GET or POST form/JSON chosen explicitly, never switched automatically; cookie session or a configured response token) and reads its DHCP leases (`/isosvc?act=DhcpLease`). Read-only: nothing on the device is changed. Each source has a required set of allowed subnets that must all belong to one customer; leases are matched by longest prefix inside them, and overlaps are left unmatched instead of guessed. A lease touches the IP record only while it is within its lease period, has no MAC conflict and matches one subnet: MAC and host name go through the existing source precedence (values maintained by people are never overwritten, an empty name or invalid MAC clears nothing), the DHCP lease flag is set, and the last-seen time is not touched; an IP record can be created in that case (can be turned off). Leases are kept as source observations with their own times and quality tags, and are never released because they are missing from a response. Step-by-step connection test, a preview that writes nothing, sync now, a schedule that can only be turned on after a successful preview and pauses itself after a login failure, sync records with exclusive outcome counts, and a read-only AI tool (`list_isoinsight_leases`) that follows subnet visibility. Passwords, cookies, tokens and the GET login query never reach logs, task records or test results. Pending verification on a real device: the login response, which method works, whether the list is complete, and the time zone.

### Changed
- **IP change assessment, stage two: switch maintenance, node downtime and service dependencies** (migration 0189).
  - **Services**: register business services under IP change assessment → Services with the devices, VMs and IPs they depend on, in dependency groups (every group is required; a group is satisfied when k members are available). Two cables or two NICs never count as redundancy automatically. Viewing needs global read; editing is admin-only.
  - **Switch maintenance** follows physical cabling (including patch-panel pass-through). Devices whose every link goes through the switch are a modelled disruption with the path; devices with other links are unverified redundancy (bond/LAG/VLAN state unknown); a second path that only returns to the same switch is not independent. The switch MAC table only yields a possible disruption.
  - **Node downtime**: running VMs on the node stop with it; "migrate first" and "unexpected failure handled by HA" are available, and HA only means it is configured: without capacity or quorum data it is unverified redundancy, never a guaranteed recovery.
  - **Services are judged with three-valued logic** (available, unavailable, unknown); dependency cycles are found and reported as unknown; several devices can go down together in one combined analysis (up to 20).
  - The device page entry is now an "Assess change" menu (decommission, maintenance, downtime), and results gain a Relations tab (a list on phones).
  - The AI chat can prepare switch maintenance and node downtime drafts too (`impact_prepare_scenario` with `mode` and `also_down`).
  - ISOinsight, AdGuard (DNS rewrites and clients) and RustDesk are now assessment sources; every new integration must feed the assessment or state why not (guarded by a test and a release checklist item).
- **"IP Request Approval" is renamed "Approval settings" with two tabs, "IP request approval" and "IP change assessment review"**, each with its own review policy (migration 0190).
  - IP change assessments can use: people who can edit the target (default, the behaviour before upgrading), admins only, admins plus designated people, parallel sign-off (every group approves) or sequential stages. Reviewers at every stage must still see the target; admins can approve any stage.
  - Sequential review notifies stage 1 on submit and the next stage after each approval; after a plan is sent back or withdrawn and submitted again the stages start over. The plan page shows stage progress and the submit confirmation says how it will be reviewed.
  - The assessment reviewer list and "allow self-review" moved here from System settings and carry over on upgrade (a list becomes designated people, none becomes target editors). The old `/ip-request-policy` URL redirects.
  - New API `GET`/`PUT /api/v1/change-impact/review-policy`; plans also return `review_mode` and `review_steps`; the AI plan listing tool includes review progress.
- IP change assessment: a cancelled plan can be analysed again (it returns to draft first); "Submit" asks for confirmation and lists the reviewers who will be notified (admins when no list is set; a warning when nobody on the list can see the target).
- ISOinsight is marked Beta (page, feature list, API manual); its advanced settings put the login path and the username/password parameter names on one row and the lease path on its own.
- **IP change assessment now reads every stored IP reference**: after going through every column that stores an address, host or URL, the assessment reads the ones it missed.
  - Renumbering: a circuit's IP, gateway and DNS servers; the connection addresses of newer integrations (AdGuard Home, ISOinsight, the other PVE/ESXi cluster nodes, the OCS database, RustDesk servers, legacy pull-mode scan agents, webhooks); jt-ipam's own settings (LDAP, SMTP, AI model, audit forwarding, OIDC/SAML identity providers); and a scan agent seeing the address hand out DHCP or DHCP replies giving it to clients as the default gateway.
  - New address: an IP request not fulfilled yet (pending or approved), or the address falling inside an IP range (with its purpose).
  - **Services**: only switch maintenance and node downtime used to look at the service registry. Decommissioning a device now treats the device, its IPs and the VMs running on it as permanently down and applies the same k-of-n logic to find disrupted services; renumbering lists services that depend on the address and services whose endpoint uses it (clients must change). Sites that model no services get no extra data gap.
  - A Wazuh agent registered with a fixed address (registerIP other than any) is listed separately: after renumbering the manager refuses it until it is re-registered.
  - Integration, setting, field and purpose codes are translated in the sentences instead of showing `scan_agent` or `dns_servers`.
  - New guard test: every column that stores an address, host or URL must be read by the assessment or carry a reason why not (`tests/test_change_impact_column_coverage.py`); adding such a column without deciding fails the test.
  - Fixed: a second "permission limited" gap in the same category was dropped (accounts without global read saw only the VPN endpoint one, not integration endpoints); gaps now say which part of the analysis they affect.
- **External integrations now sit in one collapsible group** under Admin ("External integrations", expanded by default). Items show just the product name (DNS, LibreNMS, OPNsense, ..., Graylog) and configured integrations get a green check; English and Japanese item names were unified the same way.
- Zabbix "monitoring coverage gap" is now "addresses not monitored".
- The RustDesk web connection's performance line (latency, bitrate, frames per second, codec) is hidden by default: the quality menu has a new "Show performance" item that shows it, remembered in this browser.
- The RustDesk icon now matches the RDP and VNC ones: a screen frame with two offset half circles (inspired by, not copied from, the RustDesk logo) instead of a generic computer icon.
- Development: `change-impact.spec.ts` and `change-impact-m2.spec.ts` are now in the release e2e's serial list (the first turns IP change assessment off and on again, and the wizard in the other failed when it ran in between); the guard test now also detects each feature's own global settings (`/…/settings`, approval stages). Eight specs no longer hard-code the admin password and read `E2E_ADMIN_PASS` instead.

### Fixed
- IP change assessment, new address: DHCP reservations and pools were matched against the integration's scope using the old address's subnet, so in overlapping networks another customer's reservation for the same address became a "new address reserved" blocker. The target subnet decides now: out-of-scope ones do not count, and an unscoped integration in an overlap gives an inferred finding plus a data gap.
- DHCP pool usage (the "pool nearly full" alert) matched the range against every IP record on the site: with two customers on the same addresses, the other customer's IPs counted too, giving false alerts such as "12/10 used", and every round loaded all IPs into memory. Only IPs in the pool's own subnet count now (synced pools are placed by CIDR, the integration's scope and the most specific subnet; when that is still ambiguous nothing is counted rather than raising a false alert), and the count runs in the database. The IP list's "in DHCP range" flag works the same way: another customer's pool on the same addresses (manual or synced) no longer marks IPs here, and "DHCP server (auto)" (the firewall's API address) is set only in the firewall's own subnet; the firewall section of IP details and investigation reports finds NAT by this IP record (it took the first record with the address, which could be another customer's); the AI triage card and firewall rule review list every candidate subnet and customer when an address has several records, instead of presenting one as fact.
- **Daily backups failed without anyone knowing**: a leftover table not owned by the jt-ipam role made `pg_dump` fail with "permission denied" every night, so a real site had no backup for at least six weeks, only an empty directory a day; System diagnostics had no such check and `jt-ipam.sh doctor` said OK because the newest dated directory existed.
  - System diagnostics has a "Daily backup" check: a failure, no success for 48 hours or a disabled timer is flagged, and a failure sends a system alert to admins; a permission failure shows the command to run.
  - The backup script writes `/var/backups/jt-ipam/last-run` on every run (result, time, last success, reason); it dumps to a temporary name and renames on success, so a failure no longer truncates an earlier good dump from the same day or leaves an empty directory.
  - `jt-ipam.sh doctor` looks at the status file and real dump files.
  - Upgrades refresh `/usr/local/bin/jt-ipam-backup.sh` (only fresh installs copied it, so fixes to the script never reached existing sites).
  - INSTALL docs corrected: the installer has always enabled the daily backup (the docs said it did not).
- Occasional 500s on scan agent reports (database deadlock): an agent sends per-subnet reports and background probe results at the same time (and resends on timeout); two transactions covering the same IPs locked each other, 28 times in two weeks on prod, losing that report. Reports from one agent are now processed one at a time (different agents still run in parallel), and hostname writes are sorted by IP.
- Occasional "sync failed" (database deadlock): firewall ARP and DHCP lease syncs locked each other with scan agent reports writing the same IPs; OPNsense sync failed 53 times in two weeks on prod, turning the integration page red several times a day. OPNsense, pfSense, FortiGate, Palo Alto, MikroTik, Windows DHCP and Kea syncs now redo the whole transaction once on a deadlock and only record a failure if it happens again.
- IP change assessment, switch maintenance: switch MAC table (FDB) inference used a year of history, so a machine that moved away a month ago still counted as connected; only current entries are used now (seen in the last 24 hours by default, as in the topology map). With two switches down together, ports with the same name are no longer added up when spotting uplinks.
- **Site-wide permission audit (security)**: every endpoint and AI tool was checked for object scope, fixing the following.
  - The semantic search API did no permission filtering: any signed-in account (including zero-permission accounts and API tokens) got site-wide subnets, IPs, hostnames, devices and descriptions. It now follows the caller's visibility and leaves out archived data.
  - Editing a subnet or IP could assign scan agents, console egress and jump hosts (agents and jump hosts scan and open consoles to other networks based on this). Only admins can change them now, and the same goes for turning off anomaly detection or AI audit.
  - Changing a subnet's section or customer, a section's customer or an IP's customer needed only write access: moving under a customer where you are admin granted admin, and data could be pushed into or out of another customer's scope. It now needs admin on the object and write on the destination.
  - An IP could be linked to a device you cannot see, which also set that device's primary IP; device suggestions could name devices you cannot see.
  - Deleting an IP through the phpIPAM-compatible API needed only write access (REST needs admin) and skipped the change log and cooldown.
  - Read-only accounts could send the stale-IP reminder to every admin and to external channels, with the count taken from the request.
  - The IP and device relation charts, the device name in the IP list and the matching IP in the device list could reveal objects you cannot see.
  - The AI device tool returned all of a device's IPs (including invisible subnets); the IP request list used a different rule from REST; IP history mixed in another customer's ARP in overlapping networks; subnet lookups answered differently for hidden and missing subnets.
- The Connections page hint is up to date: it used to mention SSH only and a removed "open in new window" dropdown; it now lists SSH, RDP, VNC, the PVE console, BMC and RustDesk, and says a button opens a new tab.
- In the Japanese UI, operating system families (network device, printer, unknown and so on) were shown in English; they are now translated.
- Wazuh's operating system used to show only the platform and version, so Windows 11 appeared as "windows 10.0.26200.9457" (Windows 11 still reports kernel version 10.0). jt-ipam now also fetches the product name (such as "Microsoft Windows 11 Pro") and shows it first (device page, Wazuh page, OS sources, investigate); without a product name, build 22000 and later reads as Windows 11. Migration 0184 adds `wazuh_agents.os_name`, filled in by the next Wazuh sync.
- **Certificate SFTP sources pin the host key** (security): every connection used to skip host key verification while logging in with a password, so anyone in the middle impersonating the SFTP host could capture it. The host key is now remembered on the first successful connection (audited as `cert_source_pin_host_key`) and must match every time after; a different key is refused, with both the remembered and the current fingerprint shown; changing the host or port starts over; when the host really changed its key, "Trust the host key again" in the source settings clears it (audited). Fingerprints sent by the form are never used.
- **Tools page HTTP check** (security): the jt-ipam server's own LAN addresses are now refused (only 127.0.0.1 was blocked, so services bound to the LAN interface were reachable), and accounts with no view permission at all cannot use it (the same gate as AI chat).
- The RustDesk button on the IP page now follows the other console buttons (Chinese reads "RustDesk 連線" like "SSH 連線").
- The RustDesk page now warns not to open TCP 21114 (where client reports are received) to the internet: reports need no login, so anyone could send fake reports and alarms. Open it to internal networks only, with outside clients on a VPN or restricted by source IP.
- Linux as reported by RustDesk clients used to show verbatim as "Linux 24.04 Ubuntu" with "ubuntu" as the platform; it now reads "Ubuntu 24.04" with "linux" as the platform (RustDesk page, IP and device pages, OS sources).
- The IP probe summary used to show only the NIC vendor, not the MAC itself; a phone's random (private) MAC has no vendor, so the cell read "—" and looked as if no MAC was found. It now shows the MAC address, with the vendor after it when known, and a "Random (private) address" tag explaining why there is no vendor.
- Pressing "Disconnect" in the RustDesk web connection while it was still getting ready (decoder check, ticket) did nothing and the connection went ahead; it now cancels.
- **Column widths in every table**: as soon as one column truncates with "…", the table switches to fixed layout, and all spare width used to go evenly to the columns without a set width, so name and IP columns were mostly empty while the date column wrapped; widening one column also took the space from the others, changing columns you had just resized. Tables that scroll horizontally now share spare width in proportion to each column, and after the first drag every column keeps its current width, so dragging changes only the column you drag and any leftover space stays empty on the right. Applied once in the shared table setup, so every table gets it.
- Anomaly detection "IP conflicts" MAC column layout: MAC, vendor, seen-by and time were crammed together, with MACs and vendor names broken over two or three lines and rows out of line (the styles were scoped and never reached the table cells). Each row now uses the same column widths and MACs and vendors do not wrap.
- Three fixes from the CodeQL review: splitting nmap's candidate names now uses a linear regular expression (the old one backtracked quadratically on long runs of spaces and was only held off by a length cap); the scan agent now explicitly requires TLS 1.2 or later for its connection to the server (**scan agent 1.17.3**); traceroute errors on the Tools page are sent as codes and translated, with the underlying exception text shown to administrators only (any account used to see it, and English and Japanese users saw Chinese).
- Beta labels in the docs and READMEs now match the app: the Proxmox VE console, the RustDesk integration and the RustDesk web connection are no longer marked Beta (the app dropped the label long ago); only the BMC serial console is still Beta.
- The diagnostics "Background jobs" time was printed in UTC (eight hours behind in Taiwan); it now uses the viewer's time zone.
- On the Tasks page, the result of IP probes, agent reports and database refreshes always read "added 0, updated 0, failed 0, total 0" (these tasks do not add or update anything); it now shows the outcome, such as "Windows host · Windows · 5 open ports" or "devices 3 · online 2 · matched to IPs 1", and a failure without an error message says so.
- The diagnostics "Background jobs" check looked at any task record, so agent reports and probes could hide a stalled sync timer; it now looks only at records written by scheduled syncs. The GeoIP refresh script now releases its connection pool on exit (it could make systemd record every run as failed).
- Audit log action column: long action names (such as `rustdesk.web_session_close`) ran over the diff column on the right. The column is wider, and anything longer than the column ends in "…" with the full name on hover.
- Dashboard racks card proportions: each rack used to be shrunk to fit on its own, so tall racks were scaled down while short ones kept their size, and side by side you could not tell which was taller. The whole row now uses one scale. Wide shelving (KALLAX, 90 cm shelves) is no longer squeezed narrower in the thumbnail either.
- The scan agent's auto-create hint was wrong: it said unregistered addresses seen while the option is off still show up in anomaly detection, but the scan agent keeps nothing about them (they appear under unauthorised IPs only if LibreNMS also sees them in an ARP table); it also said "orange marker" where the marker is purple. The IP grid now also marks addresses auto-created from LibreNMS ARP as auto-added (only the list did before).
- Change log sources "system" and "user" were not translated (in the IP detail change log and in the IP changes page's source filter and tags); the IP changes page showed the raw code for every source and now uses the same names as the IP detail. A guard test catches new sources without a translation.
- IP probe misreadings: when nmap itself failed (non-zero exit, host missing from the output, timeout) the page said the host did not respond; it now shows why it failed. Probes of IPv6 addresses always failed (the agent did not pass `-6`; periodic OS detection had the same gap). With no fingerprint verdict the OS was blank; the Windows version SMB reports is now used (Samba claiming to be Windows is ignored), and RDP alone gives "Windows (build N)". The MAC shown is the one the NIC vendor came from, with the MAC seen this time flagged when it differs. Services named only from the port number are marked in the port table. A silent or failed previous probe no longer makes every port "new". Cut script output is marked as truncated.
- Unmanaged addresses showed no MAC: a scan agent outside the subnet's layer 2 cannot see MACs, and unregistered addresses in LibreNMS's ARP table were never merged in (only ARP rows with a subnet were matched, and LibreNMS rows have none). LibreNMS ARP is now merged when this subnet is the single most specific one containing the address (overlaps are not guessed); the newest MAC wins and truncated MACs such as 26:00:00:00:00:00 are not shown. The grid now also shows unmanaged cells it used to miss.
- IP probe page: when space is short, "guess, may be wrong" wraps as a whole to the next line, and the type tag no longer breaks in two.

## [1.0.1] - 2026-10-06

### Added
- **RustDesk web connection: Windows portable peers explain elevated windows and UAC, and elevation can be requested**: a RustDesk running without installation has ordinary user rights, and Windows does not let it send keyboard or mouse input to windows running as administrator, nor see the UAC prompt. The web client used to ignore the notices the peer sends about this, so the screen kept updating while input did nothing and the session looked frozen. The status bar now shows "Portable", a notice above the screen explains when the foreground window is elevated or a UAC prompt is showing, and the toolbar's "Request elevation" offers two ways: confirmed on the peer (someone there clicks the UAC prompt) or with an administrator account of the peer (nobody needed there; the credentials go encrypted straight to the peer, the jt-ipam server cannot see them, and they are never remembered). Every elevation is audited as `rustdesk.elevation_request` (method and result, reported by the browser, no credentials). Installing RustDesk on the peer (as a service) removes the limitation.
- **Every console shows the connected time**: SSH, SFTP, RDP, VNC, the PVE console, BMC and RustDesk (remote desktop and file transfer) show a timer (hours:minutes:seconds) in the status bar, with the start time on hover; after disconnecting it stays at how long the connection lasted, a new connection starts from zero, and RustDesk keeps counting during auto-reconnect.
- Connection length is audited everywhere: SFTP and BMC now record `duration_seconds` when the connection ends (SSH, RDP, VNC, the PVE console and RustDesk already did), with a guard test so new consoles must record it too. Investigate shows how long each recent remote session lasted.

### Changed
- Dependencies: Vue 3.5.43 (`@vue/server-renderer` attribute-name XSS, GHSA-g2v6-rqmx-r4w6), `source-map-js` 1.2.2 (GHSA-68fv-2mgg-jv7q); the development-only `eslint-plugin-vue` moves to 10, bringing `postcss-selector-parser` 7.1.6 (GHSA-rj75-hqrm-r3gf).

### Fixed
- With a dark operating system and jt-ipam in the light theme (or the other way round), notes inside dropdown menus were invisible (for example the blank area at the bottom of the RustDesk web connection's "Quality" and "Request elevation" menus): the browser's own color scheme followed the operating system, so text without an explicit color came out white on white. It now follows the jt-ipam theme, and scrollbars and native controls match too.
- **RustDesk devices that were online sometimes showed as offline**: the open-source hbbs only counts devices with a UDP registration in the last 30 seconds, so UDP loss or clients reaching hbbs over TCP/WebSocket look offline; the five-minute full report trusted hbbs alone and flipped online devices to offline until the next client heartbeat flipped them back. While offline, devices sharing an address with another ID could lose their mapping, and the "Enable RustDesk connection" switch disappeared from the IP form. A device is now online when hbbs says so or a client heartbeat arrived within 45 seconds.

## [1.0.0] - 2026-10-05

### Added
- **Adoption roadmap on the documentation site** (`docs/adoption.html`, Traditional Chinese / English / Japanese): how
  an organisation rolls jt-ipam out in six phases (install and secure; the IP plan as the source of truth; discovery
  and liveness with scan agents; integrations that confirm reality; devices and the physical layer; operations and
  automation). It has a phase diagram, a phase table (goal, features to turn on, prerequisites, "done when"), a
  one-week quick start next to a one-quarter full rollout, the recommended order of integrations with the reason for
  each, and per-phase settings to make, checks and common pitfalls, all using the names on the screens. Linked from
  the nav of every documentation page, the home page footer and its install section. The text links in the nav now
  fold away before they would wrap onto a second line (the home page already wrapped in English at 821 to 860 px).
- **RustDesk web connection: “Send text” can type the text.** Besides **Put on remote clipboard**, the dialog has
  **Type it**, which presses the keys one by one in map mode as on a US keyboard layout (printable ASCII, Enter for line
  breaks, Tab; Shift around capitals and shifted symbols), about 8 ms apart, up to 2000 characters, with a Stop button.
  Text with other characters (Chinese, full-width, emoji) is not typed and the page suggests the clipboard action. Both
  actions are unavailable in view-only mode or while the device has turned off control. It works where pasting does
  not, such as terminals and login screens; a note says that another keyboard layout or Caps Lock can change letter
  case and symbols.
- **RustDesk web connection: multiple displays and image quality.** A **Displays** toolbar menu (hidden only when the
  device has one display and no resolution to choose; with one display it holds just the resolution submenu) lists
  every display with its size and marks the current and the primary one; picking one switches to it (one display at a time) and clicks land on that display: coordinates include the display's position in
  the device's virtual desktop, also for displays left of or above the primary one and for macOS Retina screens.
  Pictures from other displays are dropped, an unplugged display shows a notice and returns to the primary display, a
  resolution change of the shown display is not treated as a switch, and an automatic reconnect returns to the display
  you picked. Without view only, a **Resolution** submenu offers the resolutions the device reports and its original
  resolution. A **Quality** menu sets image quality (Low, Balanced, Best, or Custom 10 to 100), a frame rate limit
  (15, 30 or 60 fps) and a codec preference (Automatic, VP9, H.264, AV1; only codecs this browser can decode and the
  device can encode are listed). Changes apply at once, reconnects keep them, and the browser remembers only these three
  choices. When several people view one device, the most recent quality setting applies to everyone (the menu says
  so). The toolbar shows the delay, the bitrate, the frames the browser decodes per second and the codec in use.
- **RustDesk-compatible web connection: file transfer.** A separate connection to the device for files, opened from
  the ▾ menu of the RustDesk split button on the IP page (**File transfer**, a new tab at `/rustdesk/:id/files`). The entry
  shows only when the RustDesk server setting **Allow web file transfer** is on (off by default; migration 0183 adds it
  with the upload limits, 2048 MB per file and 10240 MB per upload by default) and the user has the RustDesk rights. It
  uses the same ticket (`kind: "file"`, refused with `409 rd_file_disabled` while the setting is off) and the same
  ciphertext-only relay, and logs in with the `file_transfer` union. The page lists folders (Windows drives at the top
  level, hidden files on request), uploads by picking or dropping files (asks to overwrite or skip when the device
  already has a file with that name, optionally for the rest), downloads single files (small ones assembled in memory,
  large ones streamed to disk where the browser supports it, otherwise written in segments), creates folders, renames
  and deletes (a folder with its contents after a second confirmation). One transfer runs at a time, the rest wait in a
  queue with progress and cancel; there is no resume. File actions are audited as `rustdesk.file_download`,
  `rustdesk.file_upload`, `rustdesk.file_delete`, `rustdesk.file_rename` and `rustdesk.file_mkdir`, marked as reported
  by the browser (the backend never sees file contents): it accepts only those operations, caps the path at 512
  characters, needs an integer size and rate-limits each connection. A device without the file transfer permission, or
  set to one-way transfer, gets a translated explanation.
- **Opening a host in the local RustDesk client software is audited too**: that connection runs between the user's own client and the device, not through jt-ipam, so nothing used to be recorded. Clicking it now writes `rustdesk.local_client_open` (who, when, which RustDesk ID), and Investigate lists it under recent remote sessions as "RustDesk client software (local)". New endpoint `POST /api/v1/addresses/{id}/rustdesk/local-open` (same rights as the connect URL in the IP details).
- **Investigate gathers what every integration knows about the address.** Besides hostnames, OS, Wazuh, ARP and
  changes, the dossier now has: identity (device type, model and OS, how the type was decided such as `wazuh:windows`,
  `librenms:…` or `hostname:…`, the NIC vendor and whether the MAC is randomized), LibreNMS found by primary IP as well
  as by device link, Zabbix hosts, OCS inventory (OS, asset tag, hardware, notes), RustDesk devices (online, OS, reported
  hostname and user, match status, key problem), the matching Proxmox/ESXi VM or container (the MAC has to agree), DHCP
  reservations, leases from every DHCP integration and the pool flag, the switch ports the MAC was learned on (LibreNMS
  and MikroTik MAC tables), firewall ARP/VPN/lease evidence, and last seen by source with the liveness verdict. Global
  readers also get the aliases, address objects and rules covering the address on FortiGate, Palo Alto, pfSense and
  MikroTik besides OPNsense, and the DHCP offers observed for it; admins also get the latest identify probe, open
  anomalies and AI findings, and the last 10 console sessions from the audit log (who, when, from where). Empty sections
  are not shown, a broken or unconfigured integration leaves only its own section empty, every section is capped at 20
  rows, and all four export formats include the new sections. The contradictions are now computed by the server
  (`conflicts`), so the screen, the exports and the AI reading use the same list: hostnames that differ only in letter
  case, a trailing dot or short name vs FQDN (`win11-desk-01`, `win11-desk-01.`, `WIN11-DESK-01`) are no longer reported as
  different, and a DHCP reservation bound to another MAC than the one using the address now is. The AI reading gets a
  compact copy (capped lists, no internal IDs) so the bigger dossier does not overflow the model context, and is told
  that a DHCP lease with a randomized MAC makes address and MAC changes normal. On the IP details page, FortiGate and
  Palo Alto policies that reference an address object (or several in one field) are now found and the objects listed.
  The MCP tool `check_ip_exposure` now reads the dossier's firewall rules and hostname (it always returned none).
- **RustDesk: delete old registrations from the RustDesk server.** hbbs keeps every ID that ever registered (reinstalls,
  replaced PCs, test machines), and the open source edition has no admin API to remove them, so the dedicated RustDesk
  agent (1.2.0, updates itself) does it on the RustDesk host. Both sides must agree: the server setting **Allow deleting
  old registrations** (off by default) and the host admin re-running the installer with `--allow-delete` (or
  `JT_RD_ALLOW_DELETE=1`; the existing config is kept, so the key is not needed again). Only then does the systemd unit
  make the hbbs directory writable for the agent (SQLite needs to create its journal there), with the private key file
  `id_ed25519` made inaccessible and all other hardening kept; without the flag the unit is unchanged and read-only, and
  re-running without it makes it read-only again. The agent reports `capabilities.delete` (and the reason when it
  cannot) with each poll, and **Test** gains a write access check ("read-only" is the default, not an error). On the
  Devices tab, offline devices can be ticked (also "tick all matching", up to 500, and a new **Never seen online**
  filter); the confirm dialog says how many will be deleted, that online devices are skipped, and that a client registers
  again by itself when it next comes online. The agent takes up to 200 requests per poll, asks hbbs again right before
  deleting and skips devices that are online, deletes only rows of the `peer` table by ID in one transaction with a 10
  second busy timeout, and reports deleted / skipped (online) / not found / failed. jt-ipam removes deleted devices from
  its list at once, asks the agent for a fresh report, and keeps a deletion log. Requests not taken within a day become
  failed, and turning the setting off cancels waiting ones. Audited as `rustdesk.peer_delete_requested` (who asked,
  which IDs) and `rustdesk.peer_deleted`. Devices that hbbs has seen since its last start stay in its memory and can
  still be reached; they reappear in the list after hbbs restarts and they come online again. Migration 0182.
- **Device type now weighs what IPAM already knows** (checked against an Identify sweep of a production /24). The type
  written to the IP record is decided in this order: the device record's type (firewall, router, switch, AP, storage,
  IPMI/PDU/UPS; generic server/other do not count), LibreNMS's own classification (firewall, wireless, printer, storage,
  power; network split into router or switch by its OS; Proxmox/ESXi hosts as hypervisors), the agents on the machine
  (Wazuh, RustDesk, OCS: a machine with an agent is a general-purpose computer, and its OS comes from the agent), the
  virtualization platform (a VM or container is never a switch, AP, printer, camera or physical hypervisor), and only
  then the port and fingerprint guesses. Facts must be fresh: agents silent for over 7 days are ignored, and a VM whose
  NIC MAC differs from the IP's current MAC is not that IP (a DHCP address had moved to an iPhone, which was shown as a
  Windows VM). The Identify page shows the type the IP record uses and why. New type **Phone/tablet** (`mobile`).
- Identify rules: OPNsense, pfSense, FortiGate and other firewall products, and MikroTik, OpenWrt, DrayTek and other
  routers are recognised from their web interface; xrdp, Samba and a Linux distribution in a version string mean a Linux
  host, not Windows (and once the OS is known to be Linux/BSD/macOS, neither is a Samba AD DC's "Microsoft Windows RPC" nor an IIS
  header passed through a reverse proxy); iOS devices (port 62078) and phone fingerprints are phones; and the NIC vendor (the MAC's OUI) is
  shown separately from the device vendor, so a Mac on a CalDigit dock is no longer labelled "CalDigit".
- **Device type rules cover any network, not just the one they were first tuned on.** Identify and the periodic OS probe
  now map their evidence through generic knowledge tables (`backend/app/services/device_kind_knowledge.py`) built from
  nmap (every OS-fingerprint device type and every service-probe `d/` type), Recog (all 117 device values), the
  Wireshark OUI list (327 single-purpose vendors, 64 mixed vendors that are never used alone, virtual NIC prefixes),
  108 product, banner, page-title and Server-header patterns, 104 port signatures and 23 host-name conventions, each
  entry with jt-ipam's own decision and the reason for it. Evidence is weighed in this order: what the services say
  (product text matched per field, and Recog), nmap's device type for a matched service, open ports, the TCP/IP
  fingerprint class, and last the NIC vendor. Software that looks like a device no longer makes a host one (CUPS,
  node_exporter, Plex, UniFi Network or Omada controllers, video management software such as Blue Iris or Frigate,
  PowerChute), and a guard in one field does not hide a product named in another. Newly recognised: cameras and NVRs
  (Hikvision, Dahua, Axis, Hanwha, Uniview...), IP phones and intercoms (Yealink, Polycom, Grandstream, snom...),
  UPS and PDU cards, NAS (Synology, QNAP, TrueNAS...), firewalls (FortiGate, Sophos, Palo Alto, Check Point...), APs
  and controllers (Aruba, Ruckus, UniFi, Omada), printers of every major brand, ESXi and XCP-ng hosts, BMCs (iDRAC,
  iLO, XCC), PLCs and building automation (Siemens S7 on port 102, EtherNet/IP, BACnet), streaming players and
  speakers (Roku on 8060, Sonos on 1400). A vendor protocol port counts only when the NIC vendor does not name another
  kind (an ATOM Cam camera also listens on the Kasa plug port 9999). A Windows fingerprint now gives `windows`,
  embedded RTOS fingerprints (lwIP, VxWorks, NetBSD) no longer give "server", and a Recog "Security Appliance" is no
  longer a firewall. Host names: only the first DNS label is read (`*.cam.ac.uk` is not a camera), server-role words
  veto device kinds (`nvr-server` and `camera-archive-01` are servers), Windows default names (`DESKTOP-XXXXXXX`,
  `LAPTOP-`, `WIN-`) mean Windows, and a host already known to run Windows or macOS is never turned into a printer or
  camera by its name.
- **Generic rules from the second adversarial review of device types (105 IPs on two subnets).**
  - A "general purpose" TCP/IP fingerprint only says Linux/BSD kernel, and routers, APs and IoT gateways look the same:
    "server" now needs positive evidence of a general-purpose host (OpenSSH, a distribution string, Samba, xrdp/remote
    desktop, a database or similar server service), and embedded software on the same host (BusyBox, Dropbear, GoAhead)
    rules it out. Without evidence the result is unknown instead of a guessed server.
  - An embedded host with 3517/tcp open (802.11 IAPP, roaming between APs) is a wireless AP.
  - A multi-line vendor seen only through its NIC or without a model decides nothing: DrayTek routers, VigorAP and
    VigorSwitch share OUIs and LibreNMS calls them all `draytek`, so the model decides (Vigor2927 is a router, VigorAP
    an AP, VigorSwitch a switch).
  - When 8006 is only a port number, a host that also takes mail (25/26, Proxmox Mail Gateway) or has 8443 open
    (Proxmox Datacenter Manager) is not a hypervisor.
  - The `SHIP 2.0` Server header of newer TP-Link Tapo/Kasa firmware means a smart-home device; a special-purpose device
    that also streams RTSP (554) is a camera.
  - 62078 together with AirPlay (7000) is an Apple TV/HomePod (media); 62078 alone is a phone.
  - "The OS is not Windows" now looks at the TCP/IP stack first: a Windows desktop publishing an Ubuntu container
    through Docker Desktop stays Windows even though its SSH says Ubuntu.
  - When the fingerprint only gives a kernel range (Linux 2.6.32), the OS shows the distribution named in a service
    version string (`Debian 5+deb11u3` becomes Debian Linux 11).
  - Host names: model codes (P105, HS300) need a matching NIC vendor; a name that says two kinds (voip-router-2) is
    decided by the more confident hint and otherwise not at all; `smart-gw` is gone (gw usually means the default
    gateway). A phone's evidence now names the iOS candidate that was actually used.
  - VMs and containers ignore LibreNMS hardware (an LXC reports its host's); a PVE node's reason is `virt:pve-node`.
- **Scan agent 1.17.2** reports how nmap identified each service (`method`, `conf` and `devicetype` per port; older
  servers ignore them). A service name nmap only took from its port table (`method="table"`, such as `jetdirect` on
  9100 or `microsoft-ds` on 445) now counts as "port open", not as a confirmed service, and nmap's device type for a
  matched service is used as evidence.
- **RustDesk: find clients with a wrong Key.** A RustDesk client whose Key differs from the server's still registers
  (it shows "Ready"), still reports, and connects directly on the same LAN; only connections through the relay are refused
  by hbbr, and the web connection always uses the relay, so it looked like the web connection was broken (seen on
  2026-10-05: two letters in the wrong case). The RustDesk agent (1.1.0, updates itself) now reads the hbbr/hbbs logs
  incrementally (official deb layout `/var/log/rustdesk-server/`, `JT_RD_LOG_DIR` to override; copytruncate rotation
  handled; only the last 256 KB on first start) and sends, with its 10-second poll, which IPs were refused for the Key
  and which passed the relay, summed per IP and without log text. jt-ipam records it on the one device at that IP (not
  when several devices share the IP behind NAT) and clears it after the device next passes the relay. The server row
  shows "Wrong Key N" (click to list those devices), the device list marks them and can filter on them, the IP page and
  the device page explain when it was refused and how to fix it, and a web connection that times out because of it says
  so directly (`rd_peer_key_mismatch`) instead of retrying. **Test** gains a check that the logs can be read. Migration
  0181.
- **RustDesk web connection shows the remote cursor.** The local pointer over the picture takes the device's cursor
  shape (text cursor, resize arrows and so on), scaled with the picture and capped at 128x128, and disappears when the
  device hides its cursor; when the device's picture already contains the cursor the pointer stays normal, so there are
  never two. When someone at the device moves the cursor, a marker shows where it is (hidden as soon as you move the
  mouse, or after 3 seconds without a new position). Cursor images arrive zstd-compressed and are decoded in the browser
  with `fzstd` (MIT, new frontend dependency): a size outside 1 to 256 pixels, a length that does not match, or data
  that would expand beyond 256x256x4 bytes is dropped without affecting the session, and decoding never allocates more
  than the cursor needs, whatever the compressed data declares. Up to 64 shapes are cached; the cache is cleared on
  disconnect, reconnect and display switch.
- **RustDesk-compatible web connection: two-way clipboard (text).** A "Clipboard" switch on the toolbar (on by default,
  always off in view only) sets `disable_clipboard` at login and can be flipped during the session. What the device
  copies lands in the browser clipboard as plain text (HTML-only content is converted to text and also written as HTML
  where the browser allows; if the browser refuses because the tab has no focus, a prompt copies it with one click).
  Pressing Ctrl+V on the picture first sends the local clipboard and only then the key, so the device pastes the new
  text; on a Mac, Cmd+V works too and reaches the device as Ctrl+V (unchanged when the device is a Mac). Identical
  content is not sent twice. **Send text** puts typed or pasted text on the device clipboard without pressing anything,
  for browsers that do not let the page read the clipboard. Text is limited to 1 MB in both directions; zstd content
  from the device is decompressed with a 1 MB cap that is checked against the frame headers before anything is
  allocated (new frontend dependency `fzstd`, MIT, pure JavaScript because the CSP blocks WebAssembly). Text sent to the
  device is not compressed (no suitable pure-JavaScript zstd compressor; the device accepts uncompressed content).
  Switching it off stops both directions, and the browser stops sending by itself because the device still writes
  whatever it receives.
- **RustDesk web connection reconnects by itself after an unexpected drop.** When a session that had logged in ends
  without a reason from the device (for example a Linux device at the GDM login screen switches to the new desktop
  session after you log in through the web session), the browser loses its WebSocket to jt-ipam, or the relay or network
  fails, the page no longer stops at "Disconnected": it shows "Connection lost" with a countdown ("reconnecting in N s,
  attempt k of 8"), **Reconnect now** and **Cancel**, and tries again after 1, 2, 3, 5, 5, 10, 10 and 15 seconds. Every
  attempt is a full new connection (new ticket, new relay pairing, new login challenge and hash); while the device is
  still offline, the relay fails, jt-ipam is restarting or the device's login answers with an error from a program that
  is still starting (such as "connection refused"), the next attempt follows, and only after the eighth does the page
  show the error with the last reason. During a reconnect only login answers that need the user or cannot improve stop
  the retries (Wrong Password, 2FA, No Password Access, rate limits, "Desktop..." replies, unsupported display servers,
  anything "not allowed"); the first, manual connection still shows every login error directly. The two refusals a
  device can send before the login challenge, "Your ip is blocked by the peer" (the jt-ipam server is not on the
  device's IP allowlist) and "The main window is not open", also stop the retries at once and show the reason instead
  of trying all 8 times. There is no reconnect when you disconnect or leave the page, when the device
  states a reason, when the connection never logged in, or for errors a retry cannot fix (permission, key mismatch,
  unsupported codec); an attempt that gets Wrong Password, 2FA Required or a rate limit stops and asks as usual. A typed
  password is kept only in the page's memory for reconnecting and is cleared on disconnect, cancel, when the attempts run
  out or when the page is left; it is never written to browser storage. A saved password asks the backend for a fresh
  login hash on every attempt, and a successful reconnect does not save the password again. View only and the clipboard
  switch keep their current values. This covers every kind of session switch on the device (Windows logoff, user switch
  or RDP taking the console; any Linux display manager, X11 or Wayland; the macOS login window), because the device
  always just drops the connection then. Every connection from the page uses the same `session_id` and `my_name`, so a
  device whose program did not restart keeps the earlier login; a planned reconnect never sends `close_reason`; a Wrong
  Password during a reconnect says the device's one-time password may have changed; "No Password Access" while waiting
  for approval says the device is at its login screen and asks for the password on the same connection; "Wayland login
  screen is not supported" stops and explains what to do on the device (the RustDesk documentation link is shown as
  text); a message box from the device (such as Wayland asking someone there to choose the screen to share) is shown
  and the page keeps waiting.
- **RustDesk web connection: Windows session picker.** When an installed Windows device has more than one session
  (for example the console and an RDP session), it sends the session list at login and shows no picture until the
  controller picks one (`Misc.selected_sid`). The page sends it straight away when there is only one session or the
  current one was chosen before; otherwise it lists the sessions with the current one marked and preselected. Picking
  another session makes the device switch and drop the connection; the page reconnects by itself and, when the device's
  current session is then the one picked, sends it without asking again. The choice is kept only in the page's memory.
- **RustDesk web connection: log in to a Linux device that has no desktop.** A RustDesk 1.4.x Linux device with
  "allow headless" and nobody logged in answers "Desktop session not ready" (and related messages); the page now asks
  for an OS username and password (and the RustDesk password too when the device says it is empty or wrong) and logs
  in again on the same connection with `os_login`, reusing the login hash it already sent when the RustDesk password
  is not retyped. "Desktop xsession failed" asks again; "another user login", "xorg not found" and "Desktop none" end
  with an explanation. The OS username and password go only inside the encrypted login message: they are not stored,
  not logged, and never sent again on a reconnect. A wrong RustDesk password on this path ("password wrong") counts
  toward jt-ipam's login failure limit exactly like "Wrong Password"; the first prompt ("password empty") does not.
- **Device type column on more pages**: Connections (shown by default), the Wazuh and OCS "IPs without an agent"
  lists (shown by default, server-side sortable with `sort=device_kind`; the API rows also carry `device_kind` and
  `device_model`), Anomalies (shown by default for "type or OS changed", selectable for the other IP-based
  categories), Exposed services and the RustDesk devices tab (type of the mapped IP, server-side sortable). All use
  one shared column definition, and the column can be picked, sorted
  and exported like the others.
- **RustDesk Server (open source) integration (Beta), with its own dedicated agent.** The open source server has no
  management API, so a small dedicated **RustDesk agent** (`agent/jt_ipam_rustdesk_agent.py`, standard library only; not
  the scan agent) runs on the RustDesk host. Adding a RustDesk server in jt-ipam ("Integrate RustDesk") generates that
  server's own agent key and a one-line install command (`/api/v1/rustdesk/agent/installer.sh`): systemd service
  `jt-ipam-rustdesk-agent`, program in `/opt/jt-ipam-rustdesk-agent/`, config `/etc/jt-ipam-rustdesk-agent.env` (root
  only). The service runs as the owner of the hbbs directory with that directory mounted read-only, no capabilities
  and a read-only system; the agent updates itself. It polls every 10 seconds, so the page's **Test** (the agent
  checks the database, public key, hbbs version, online query and client report receiver on the host and reports each
  result) and **Sync now** answer within seconds. The key is stored hashed for authentication and encrypted for
  showing the install command again; viewing and replacing it are audited, and a key only works for its own server.
  The agent reads the hbbs database read-only (device ID, first registration time, registered IP; never the
  public-key or UUID columns, never the private key file) and asks hbbs which IDs are online (`OnlineRequest`, over
  the host's own address, because hbbs treats loopback as its text admin console). The page follows the Wazuh page:
  tabs for **RustDesk servers** (agent state, host and version, receiver state, edit / test / sync now / install
  command in an action column pinned to the right), **Devices** (search, online filter, mapping status, export) and
  **Connection audit**. An ID is mapped to an IP record only when the evidence is clear (below); the IP page shows the
  RustDesk ID and online state, and users with remote console rights get a "RustDesk" button that launches the local
  client with `rustdesk://connect/<id>@<server>?key=<public key>` (no password in the link). An unreadable database
  never clears the list, a failed online query keeps the last state, devices removed from hbbs are removed here, and
  a silent agent raises a health alert. AI tool `list_rustdesk_peers` (admin, with `subnet_cidr`); `get_ip_detail`
  includes the RustDesk ID. API: `/api/v1/rustdesk/servers` (+ `/peers`, `/audit`, `/test`, `/sync-now`,
  `/agent-key`, `/rotate-agent-key`), agent protocol `/api/v1/rustdesk/agent/{poll,report,events,test-result}`.
- **RustDesk connection audit, client host name / OS and brute-force alarms.** The open source server keeps no record
  of who connected to what, but the open source clients report it themselves to an "API server", and when that
  setting is empty they send it to port 21114 of their ID server. With "Receive client reports" on (default), the
  RustDesk agent listens there, so no client needs reconfiguring: heartbeats, host name / OS / user, connection audit
  (connected, authenticated with peer ID / name / IP and type, closed), file transfers, alarms (6 wrong passwords in a
  minute, over 30 in total, allowlist violations) and session notes. Each report's uuid is compared with the hbbs
  database on the host itself (forged reports are dropped and counted; the uuid never leaves the host), responses
  never push settings or disconnect anyone, and the receiver has body, time, connection and rate limits. If it cannot
  listen (port taken) the reason shows on the page. Alarms raise the `rustdesk.alarm` notification (once per device
  and type per 10 minutes). Mapping an ID to an IP combines signals: the heartbeat's source address (a LAN client
  reaches the agent directly, so this is its real current address), the registered IP and the host name. A host name
  that differs from the IP record only blocks the mapping when the registered IP is the sole evidence (an address
  handed to another machine); a heartbeat that just came from that address wins, since the record simply names the
  machine differently. A name-only match is shown as a suggestion. The client-reported host name is also a new
  **host name source** for IP records (`rustdesk`, last in the default order, so it only fills IPs that no other
  source names; generic names such as localhost / ubuntu are ignored, and the names are withdrawn when a device is no
  longer mapped or the server is deleted). The receiver was written from a spec only (`docs/SPEC_RUSTDESK_API_zh-TW.md`), without RustDesk or
  third-party API server source. Audit is kept 400 days. AI tool `list_rustdesk_audit` (admin).
- **RustDesk connect button needs a per-IP switch**, like SSH / RDP / VNC: "Enable RustDesk connection" in the IP
  edit form (shown only when the IP is mapped to a RustDesk device; migration 0179, off by default, so after
  upgrading the button appears only where you turn it on). The button carries a "Local" badge because it opens the
  RustDesk client on the operator's computer; the RustDesk row on the IP page now shows only the ID (with a copy
  button), server, user, OS and client version, while the last heartbeat moves to "Last seen by source" and the
  host name to the host name sources. The RustDesk page tables no longer combine values in one cell (IP and host
  name, agent host / IP / version, peer ID / name / IP each have their own column), every column can be sorted
  (device and audit lists sort on the server) and shown or hidden, and copy buttons react on hover.
- Console connect forms (SSH, SFTP, RDP, VNC) show the **connection path**: direct, through which jump host or scan
  agent, and whether that is set on the IP or the subnet; if the path cannot be used (jump host disabled, host key
  not pinned, agent not allowed to relay…) the reason shows before you click connect. Users who can edit get
  "Change", which opens the exit setting for this IP in place (the same picker as the IP edit dialog), with a link
  to the subnet's edit dialog to change it for the whole subnet; when nothing but direct is possible it says so. API: `GET /addresses/{id}/console-route`. There is deliberately no per-connection
  choice between direct and relay: picking direct on an overlapping network reaches the wrong host.
- **RustDesk-compatible web connection (Beta, phase 1)**: operate a RustDesk device mapped to an IP record right in the
  browser (screen, keyboard, mouse) without installing RustDesk. RustDesk server settings gain a "Web connection" switch
  (off by default, migration 0180), the hbbs address (empty = the source address the agent reports), the relay address
  (empty = the hbbs host plus the default relay port) and the transport (TCP 21116/21117 or WebSocket 21118/21119). When
  it is on, the RustDesk button on the IP page opens the web connection in a new tab (no badge) and a secondary button
  with the "Local" badge still opens the client installed on your computer; the Connections page gets a RustDesk button
  and filter. The jt-ipam server only does rendezvous (hbbs) and relay (hbbr) and then forwards ciphertext: the secure
  handshake (key exchange v0, secretbox), password hashing, login (two-factor and "waiting for approval" included),
  VP9/H.264/VP8/AV1 decoding (WebCodecs) and map-mode keyboard input (key codes from the spec's appendix A, CapsLock and
  NumLock kept in sync) all run in the browser, so **typed passwords, hashes and keys never reach the server** (unless
  you choose to remember the password, see the "remember the password" entry). No downgrade: if hbbs did not sign
  the device identity, the signature does not verify or the identity does not match, the connection stops (hbbs started
  with `-k <public key string>` does not sign, so web connections are refused; run hbbs with its key file, `KEY=_` in
  the `.env` of the official deb, as the troubleshooting page explains. The backend handles the hbbr race when both
  sides arrive at once: it waits a moment after rendezvous, reconnects with the same uuid when dropped at once, and
  restarts from rendezvous once if nothing pairs within 10 seconds). Security: permission follows the remote
  console rules (the IP must have RustDesk connections enabled); tickets last 30 seconds, work once and are bound to
  user, IP, server and RustDesk ID; the backend only connects to the hbbs/hbbr addresses configured for that server
  (outbound rules applied at connect time); every session start and end is audited (RustDesk ID, transport, the relay
  name hbbs returned and the address actually used, end reason, login results reported by the browser). The device
  counts wrong passwords per source IP, which is the jt-ipam server for everyone, so jt-ipam limits first: 3 failures
  per minute and 10 per day per user and device (two-factor codes counted separately; tune with
  `RUSTDESK_WEB_FAIL_PER_MINUTE`/`_PER_DAY`), and a session that keeps sending logins without reporting results is cut
  off. At most 3 sessions per user and 20 per server (`RUSTDESK_WEB_MAX_SESSIONS`/`_PER_USER`). The nginx console
  WebSocket location now includes `rustdesk` (fresh installs use the template, `jt-ipam.sh upgrade` patches existing
  sites); one message can be up to 16 MB (keyframes can exceed 1 MB, so loosen any WAF in front). A disposable test
  target is included in `scripts/rustdesk-test-target/` (official hbbs/hbbr and Linux client, bound to 127.0.0.1 only).
  The protocol is a clean-room implementation of our own written specification; no RustDesk or third-party web client
  source code was consulted.
- **Drag to reorder columns.** Every table with a "Columns" button (74 pickers on 50 pages) now lets you drag a column
  in that list by its handle to change the table's column order; the handle also moves with the up and down arrow keys,
  and dragging works with touch. The order is saved per user and per table next to the visible columns (in the same
  `table_columns` preference, under `<table>:order`, as the order of every column in the list including hidden ones, so
  a column you hide and show again returns to where you put it). Columns added in later versions, the selection column,
  columns fixed to the left or right edge and columns that are not in the list (such as an actions column) stay where
  the page puts them; exports follow the displayed order; "Reset to default" restores the order too. Tables you never
  reordered look exactly as before. The lists on Subnets, Locations, NAT and Connections now show the columns in the
  same order as the table.
- **RustDesk web connection: remember the password.** Turn on "Remember password" and, once the login succeeds, the
  password goes into the per-user credential vault (protocol `rustdesk`, envelope encrypted, bound to that IP; saving
  again for the same IP replaces the old one; a failed login stores nothing). Next time the form uses the saved
  password without asking: after the device sends its challenge, the browser asks the backend over the connection's
  WebSocket (`login_assist`), and the backend decrypts the password and returns only the hash for this one connection's
  challenge. Neither the password nor the reusable first-stage hash reaches the browser, which keeps only the
  credential id (nothing in localStorage or sessionStorage). The backend checks, in order: the relay is paired and the
  session is not logged in yet, at most 3 requests per connection, salt and challenge are 1 to 64 printable ASCII
  characters, the credential is the user's own `rustdesk` one for this IP, and it decrypts; a failed check is answered
  without closing the connection and the browser asks for the password instead. Each use is audited as
  `rustdesk.saved_password_used` (RustDesk ID and credential id, no secrets) and updates the credential's last-used
  time. If the device rejects the saved password (it was changed), the page says so, never retries it automatically,
  and offers to delete it or remember the new one; a rejected saved password counts toward the same failure limits as
  a typed one. The connect form looks and behaves like the VNC one: a first "Saved password" row with a drop-down
  (the saved one is picked by default; "Use a different password (enter it below)" or clearing it goes back to typing)
  and a delete button next to it, then the password, "View only" and a "Remember password" switch row (with an
  optional name), and the same hint box below. The ticket response gains `has_saved_password`; the vault accepts `protocol=rustdesk` (password only, username may be empty, `target_ip_id`
  required, otherwise `cred_target_required`). New error codes `rd_saved_password_unavailable`,
  `rd_saved_password_decrypt`, `rd_saved_password_rejected` and `rd_login_assist_limit`. No database migration.

### Changed
- Inclusive wording everywhere: the UI, backend messages, code comments and docs say allowlist/denylist (Traditional Chinese 允許清單/封鎖清單, Japanese 許可リスト/拒否リスト), e.g. the PVE firewall posture "Allow-list" and the RustDesk alarm "Source IP not in allowlist". Text quoted from other systems stays verbatim (TigerVNC's `blacklisted` log line). A guard test keeps it that way.
- IP details, "Last seen by source": the "Ago" column is now "How long ago" (Traditional Chinese 「多久以前」 instead of 「距今」).
- **Jump hosts moved to a tab on the Scan agents page**, "SSH jump hosts (sites without an agent)", and left the sidebar;
  the old `/jump-hosts` URL redirects there.
- **Install / upgrade: building the frontend needs Node.js 22 LTS** (Node 20 reached end of life on 2026-04-30).
  A fresh install gets NodeSource 22, and `upgrade` moves an existing Node 20 (or anything older than 22) to 22 by
  itself, before the database migration. If 22 cannot be installed during an upgrade (no access to
  deb.nodesource.com, a proxy, an apt conflict), the upgrade builds with the existing Node 20 or newer and finishes
  with a warning banner, and `doctor` keeps reporting the old Node; with nothing that new it stops before touching
  the database. A fresh install that cannot get 22 stops and says how to install it by hand. An nvm Node 22 of the
  user running `sudo` is used as is; an older nvm Node is not (that lookup had never matched anything before). CI,
  `.nvmrc` and `engines` are on 22.
- Development: CI runners are pinned to Ubuntu 24.04 (`ubuntu-latest` moves to 26.04 from October 19), with a
  non-blocking 26.04 preview on every run; GitHub Actions are on their Node 24 releases. The dependency-audit
  allowlist has a guard test (every entry needs an expiry, and package.json ignores must be registered).
- Development: the release e2e run is `frontend/e2e/run-release.sh`: ordinary specs run in parallel, specs that
  change system-wide settings (listed in `e2e/global-state-specs.txt`) run afterwards one at a time, and the
  language specs use temporary accounts instead of switching the shared admin to Japanese.

### Fixed
- **An IP record's MAC no longer stays stuck on the previous device**: a MAC with an unknown source (old data, imports) used to be treated as "maybe typed in by hand" and was never updated, so after a DHCP address moved to another computer the scan agent, firewall ARP and the lease all saw the new MAC while the IP page kept showing the old vendor, with no change logged. An unknown-source MAC now has the lowest priority: any source that sees a different MAC updates it and logs a "MAC changed" entry; a MAC that was already right just gets its source recorded. MACs edited on the IP form are still marked manual and win; MACs written through the phpIPAM-compatible API are now marked manual too.
- **When an IP moves to another device, the previous device's names no longer linger**: when the MAC changes to a different device, the hostnames the previous device reported about itself (NetBIOS, mDNS, Wazuh, OCS, RustDesk) are cleared and the hostname is recomputed in precedence order, instead of waiting for them to expire (an old NetBIOS name used to stay under "Hostname sources" indefinitely). Sources that follow the address (DNS, firewall, DHCP) are unaffected. Filling in a MAC for the first time does not count as a device change.
- IP detail, "Hostname sources": the "Manual" entry can be removed with its x (after a confirmation; the hostname is then recomputed in precedence order), so an old manual name no longer has to be blanked out in the edit form after the device changes; hovering any source shows when it last reported. Other sources stay read-only (removing them would only last until the next sync).
- **An IP reported with several MACs no longer flips every round**: several LibreNMS devices each reporting a different MAC (one device's ARP cache still holding the previous machine), two Proxmox guests configured with the same IP, or two MACs for one IP in the same firewall batch used to be applied in report order, so the MAC flipped back and forth within and across rounds and kept adding change entries. Each IP now gets one decision per round: the most reported MAC; the current MAC stays when it is among them; nothing changes when it cannot be told apart. Switching back to a MAC this IP used within the last 24 hours (ARP flux on dual-NIC hosts, two VMs sharing an IP) counts as the same set of devices taking turns and does not clear the device-reported names.
- IP edit form, device field: one device no longer shows both "Link the matching device" and "Link to existing device". With the hostname unchanged the system suggestion wins (it also checks MACs and ports and handles IPs with the same hostname); with an edited, unsaved hostname the new name is matched instead.
- RustDesk web connection: latency, bitrate, frames per second and codec moved to their own line under the toolbar, so a narrow window no longer pushes the whole row of buttons onto a second line.
- Consoles (SSH, SFTP, RDP, VNC, noVNC, BMC, RustDesk web connection): with nothing saved yet, the connect form no
  longer shows the "Saved credentials"/"Saved password" row, whose drop-down only offered manual entry.
- Connections: the MAC and MAC vendor columns offered in the "Columns" list could not be turned on (ticking them did
  nothing).
- Virtualization tables: a column meant to be hidden by default (the VMID column of the VM list) was shown at first and,
  once any column choice was saved, could never be shown again (the list of all columns and the default columns were
  swapped).
- RustDesk devices tab with two or more RustDesk servers: choosing a value in the online filter changed the mapping
  status filter instead (the server picker appears after the list loads, and the toolbar's unkeyed components were
  reused by position, keeping the neighbour's event handler). The toolbar components now have keys.
- The last-resort NIC vendor hint matched brand words anywhere in the OUI vendor name: "sonos" matched SonoSite
  (ultrasound), "arlo" matched Carlo Gavazzi, "bose" matched Boser, "brother" matched McKay Brothers, and "dahua" only
  matched a weighing-scale maker, while Dahua's own OUIs (`ZhejiangDahu`), APC (`AmericanPowe`) and PlayStation
  (`SonyInteract`) never matched. The vendor name now has to equal a known manuf short name or registry name, and the
  hint is never taken from a randomized (locally administered) MAC, a virtual NIC prefix or an all-zero MAC.
- An address whose host name suggests a device (for example `printer-2f`) kept that device type after a Windows PC took
  the address, so "Device type or OS changed" never reported it; a host name no longer overrides a Windows result.
- Intel AMT in a vPro PC (port 623 together with 16992 to 16995, or a Windows or macOS fingerprint) is no longer reported
  as a server BMC.
- Identify misclassifications found in the production sweep: an OPNsense firewall was a **printer** (its Prometheus
  node_exporter on port 9100, which nmap only names by port number, counted as JetDirect; port 9100 without a recognised
  product no longer means a printer on a general-purpose OS); a Proxmox Mail Gateway container was a **hypervisor** (its
  web interface is also on 8006; that port alone no longer counts for VMs, containers or Proxmox Mail Gateway/Backup
  Server); a Dyson appliance was an **HP switch** (the top fingerprint candidates disagreed about the kind of device;
  such fingerprints no longer pick a kind or an OS); a camera was a **wireless AP** (a fingerprint's device class is no
  longer used when its vendor contradicts the NIC vendor); nmap's "phone" class meant VoIP. An explicit Identify that
  cannot tell what a host is now clears the previous guess instead of keeping it, and a device model that was really the
  NIC vendor is cleared.
- RustDesk: devices whose old registrations share an IP were all marked "shared"; only IDs seen online in the last 7
  days count now, so old registrations show "not seen online". The agent column showed "offline" whenever the page had
  been open for a minute (the page compared old data with the browser's clock); the server now decides and the servers
  table refreshes every 15 seconds while visible.
- Identify: LPD (515) without a recognised product no longer means a printer on general-purpose or network systems
  (routers and NAS share USB printers with it); nmap's "broadband router" class is a router; LibreNMS hardware models
  outrank coarse types (a VigorAP is an AP even when its device record says router, a Synology RT/MR/WRX is a router,
  not storage); host names give a last-resort hint for phones, cameras, smart plugs, printers, APs, laptops and VoIP.
- RustDesk on the IP page: one split button instead of two; the main button connects in the browser and its arrow
  offers "Open in the RustDesk app on this computer" (like the Proxmox console button). Without the web connection the
  single local-app button with its "Local" badge stays. The devices tab shows the registered and report IP in one
  two-line column, the OS name without build numbers above its platform, and the mapping status inside the status
  column.
- Lists: going back from an IP to the subnet's IP list or the IP address list returns to the page you were on (the page
  and page size are kept in the URL).
- **A Mac was identified as a camera.** macOS's AirPlay receiver serves RTSP on ports 5000 and 7000, and any RTSP
  used to mean "camera", even when the OS fingerprint said macOS; since Identify results are now written back to the IP
  record (and periodic detection also looks at port 5000), the wrong type showed up on the IP page. RTSP alone no longer
  means a camera on a host whose OS is macOS, Windows or iOS (a named camera product still does), and periodic detection
  may now replace an earlier specific type when the fingerprint says one of those desktop systems (embedded devices do not
  run them); a plain Linux fingerprint still cannot turn a camera back into a server.
- RustDesk web connection: the password field no longer lets the browser or a password manager fill in a saved
  password (`autocomplete=off` did not stop Chrome from filling the jt-ipam login password, and every wrong attempt counts
  towards the device blocking jt-ipam).
- RustDesk servers table: the public key preview shows only its first 5 characters (the tooltip and the copy button still
  give the whole key).
- **"Enable RustDesk connection" seemed to vanish on a computer with two network cards.** RustDesk maps only to the
  address the client connects from, so the other IP had no switch at all. When another IP of the same device has RustDesk,
  editing this IP now says so and links to that IP (RustDesk ID, enabled or not). Only IPs linked to the same device count
  (a shared hostname is not enough), and IPs in subnets the user cannot see are not shown. The address API carries this as
  `rustdesk_elsewhere`.
- RustDesk web connection: when the relay does not pair in time, the message now names the most common cause, a device
  whose RustDesk Key differs from the server's. The relay refuses such a device, while the RustDesk app on the same LAN
  connects directly without the relay and so still works, which made the web connection look broken.
- The AI assistant's floating button covered the last row of a list: with the action column pinned to the right,
  the last row's Delete button sat right under it and stayed there even when scrolled to the bottom, so clicking it
  opened the assistant instead. The content area now keeps room at the bottom for the button (desktop and mobile).
- Upgrade: when `node_modules` had been laid out by another pnpm version, `pnpm install` stopped at an
  interactive "reinstall from scratch?" prompt, took the missing answer as no and installed nothing, so the build
  failed on any new frontend dependency. The install now runs non-interactively (`CI=true`).
- **The IP page kept a wrong device type even after Identify got it right** (a Foscam camera showed as
  "Server / computer"). Identify results were only shown on the Identify page; they are now written back to the IP
  record (type, OS, vendor/model). Periodic detection without the "ports" probe only looked at six ports, so it never
  saw the camera's RTSP 554 and fell back to the Linux TCP/IP fingerprint; it now also checks a few
  device-identifying ports (RTSP 554/8554, printer 9100/631/515, SIP 5060, NAS 5000/5001, 8080/8443, NVR
  37777/34567; scan agent 1.17.1). A periodic result backed only by the OS fingerprint no longer turns a specific
  type (camera, printer…) back into a generic server, which also removes false "type changed" anomalies; service,
  Recog, banner OS and virtualization evidence, or a changed OS family, still change it.
- **Saving the IP edit form without touching the host name could clear it**: the form always sends the "pinned
  host name source" field, and the server recomputed the host name whenever that field was present. An IP whose
  name had no source observation behind it (older records, imports) came out empty, and the change log showed a
  manual edit to blank. The name is now only recomputed when the pinned source actually changes.
- IP detail header: the reservation and DHCP tags now have borders like the state tag.
- IP lists: the role icons after an address (auto-added, gateway, DHCP, reservation lock) could spill over the
  host name column in the subnet page (the IP column was too narrow). The column is wider and icons that do not fit
  wrap below the address instead of overlapping.
- Host name source chips on the IP page showed raw keys for OCS, RustDesk, Zabbix, Palo Alto, MikroTik and pfSense;
  they now show the product names. The "other IPs with the same host name" list in the IP form ran IP, MAC and
  vendor together; they are now separate, aligned columns.
- Dashboard rack card: rack names sat at different heights (each name followed its rack's height); they now line up
  while the racks stay bottom-aligned. The rack legend (dashboard and rack pages) showed type keys such as
  `patch_panel`; it now shows the translated device type names.
- Dark mode: tables whose action column is pinned to the right (certificates, scan agents, users, devices,
  RustDesk) showed the scrolled columns through it, so the buttons sat on top of other text. Pinned cells now get an
  opaque background that matches the rest of the row.
- **A host with two NICs was reported as an IP conflict**: when its networks share a broadcast domain, both NICs answer
  ARP for each of its addresses (Linux's default `arp_ignore=0`), which looked like two machines using one IP. Two MACs
  that both belong to the same device (registered on its other IPs or on its ports) no longer count; a third machine
  still does. To stop the double answers on the host itself, set `net.ipv4.conf.all.arp_ignore=1` and
  `net.ipv4.conf.all.arp_announce=2`.
- Device detail and the Investigate report showed Wazuh's raw agent state (`disconnected`); they now show it translated,
  as the Wazuh page does. The Investigate report does the same for the LibreNMS state.

## [0.6.61] - 2026-10-04

### Added
- The subnet page's IP list can show the Device type column too (only the Addresses page had it); both share one renderer.
- IP details "Last seen by source" gains **Counts as up** (current, expired, ARP only or not counted, per the liveness
  sources and time limit in system settings) and **What it means**, adds Zabbix, and sorts by any header; on phones it
  folds to three columns without horizontal scrolling.
- SFTP uploads and downloads show **rate and time left** (5-second average); when data stops the rate falls toward 0 and
  says "stalled" instead of showing the last number forever.
- The jump host page has a Requirements guide (also reachable from the create dialog): what system a jump host can
  be, network needs, local forwarding allowed on the SSH server, no root or shell needed, private key formats
  (passphrase-protected keys are not supported), host key pinning, and an example forwarding-only account on Linux
  (tested: forwarding works, commands and interactive logins are refused). The page intro now says to use relay
  through the scan agent when the site already has one.

### Changed
- **The GraphQL endpoint `/graphql` is removed**: nothing in the UI used it, the API manual never documented it, and nginx
  never forwarded it to the backend (unreachable on standard installs); keeping it meant a second permission check to
  keep in sync with REST by hand. Use REST or MCP for integrations. New installs no longer install strawberry-graphql
  (on upgraded sites it stays in the venv unused).
- More spacing between the sections of IP details.
- **Backend worker count adapts to small machines**: without `UVICORN_WORKERS`, machines with 2 cores or <= 4.5 GB of
  memory (container limits included) run 2 workers, others keep 4. Each worker is about 250 MB; with four on a 4 GB
  machine there was little left for the frontend build an upgrade runs (peak about 1.6 GB).
- **Upgrades (and installs) check memory before building the frontend**: below 2 GB the backend is paused for the build
  (about a minute longer; the upgrade restarts it anyway), and if that is still not enough it suggests adding swap; a
  failed build brings the paused backend back instead of leaving the site down.
- Hardware requirements corrected: 50 GB recommended disk (README and INSTALL disagreed), CPU notes list what actually
  uses CPU (embeddings run on the LLM server, not here), and the 4 GB behaviour and RDP console usage are noted.

### Fixed
- **SFTP uploads overwrote files with the same name, and an interrupted upload deleted the original**. Opening the target
  truncated an existing file to 0 bytes without asking, and a stalled upload then removed the path, so the original was
  gone. A name clash now asks Overwrite / Keep both / Skip (with "do the same for the rest" for batches), and every
  upload is written to a temporary file in the same directory and renamed into place only when complete (keeping the
  old file's permissions when overwriting); a failed or cancelled upload removes only the temporary file.
- **The periodic OS probe took the vendor from whichever NIC answered the probe**: on a host with two NICs whose
  networks share a broadcast domain, Linux answers ARP for every local address on every NIC, so a SuperMicro machine's
  address was sometimes answered by its HP add-in NIC and read "Server · Hewlett Packard". Like the Probe page, it now
  looks up the MAC on the IP record in jt-ipam's own OUI table.
- **Ambiguous nmap guesses are no longer taken as devices**: recent Linux kernels are often guessed as "HP P2000 G3 NAS"
  within 0-1 points of Linux, so two identical machines came out as storage and server. When the top guess is a device
  class but a general-purpose OS is within 2 points, the general-purpose one is used. A host with port 8006 (Proxmox VE
  web UI) open is a hypervisor.
- **Scan agent 1.15.1: an OS probe could come back empty for a whole host**. With a service like PVE's 8006 open, nmap's
  version detection (default intensity tries dozens of probes) ran past the per-host limit and nmap dropped every result
  for that host, so it never got a device type. Version detection now uses the light mode, and if the host still times
  out the agent retries with OS fingerprinting only.
- Scan agents left zombie processes after a self-update: probes (nmap) running at the time were never reaped once they
  finished. The new program records those leftover children at start-up and reaps them each round.

## [0.6.60] - 2026-10-02

### Added
- **Console relay through scan agents** (issue #24 phase 2, Beta). When the server cannot reach into a customer
  network, SSH, SFTP, RDP and VNC consoles can be relayed by the scan agent on site, which dials out over one
  WebSocket per session; nothing has to be reachable inbound. Pick it per subnet (or per IP) as the console exit;
  it must be that subnet's scan agent, so overlapping networks each go through their own agent. Everything is set
  in the web UI with two switches, both off by default: system settings and "Allow console relay" on the agent page
  (which also sets the allowed ports and session limit). Nothing to do on the agent host: once the agent updates
  itself after the upgrade, it works; a host owner who wants to refuse can set `JT_IPAM_RELAY=0` there. The agent
  relays only within the scope allowed in the web UI (assigned subnets, allowed ports), never to loopback or
  link-local; tickets are single use and bound to the agent, and every relay is audited with bytes each way. Any
  failure is refused with a clear message and never falls back to a direct connection. Scan agent 1.15.0; the
  upgrade adds the nginx location.
- IP details show **last seen by source** as a section of its own: source, time and how long ago in aligned
  columns, with the most recent row marked, so you can compare at a glance which sources still see the address
  (these used to be scattered among the basic fields). LibreNMS and Wazuh times are clickable and open the device
  page scrolled to that system's card (OCS already was).
- **Virtual/physical shows the guest kind**: Proxmox VE tells KVM virtual machines from LXC containers, VMware is
  labelled VMware (IP details and device page). The API's `virt_vm` gains `kind` (`vm` / `ct`).
- The sidebar has "MAC address" under Advanced, opening the MAC history page.

### Fixed
- The IP edit dialog never saved the per-IP jump host override (the update did not send it).
- Agents could lose on-demand jobs: when an agent reconnected, the server kept the old long poll open and
  claimed jobs for a client that was gone (the first console relay after an agent restart waited 15 seconds).
- **A disabled jump host no longer falls back to a direct connection** (issue #24): on overlapping networks
  that reached the same address on the server's own network, i.e. the wrong host, with nothing on screen to
  say so. The console now refuses with "jump host is disabled"; remove the assignment to connect directly.
- Scheduled LibreNMS syncs left an empty summary on the Tasks page (only manual syncs had one), so the
  "IPs created from ARP" count and skip reasons were missing for scheduled rounds.
- **A Proxmox LXC container (Linux inside) showed as "Storage · HP"**: the nmap OS fingerprint took the Linux network
  stack for an HP storage device and the device type and vendor were copied from it. When Recog names a different OS
  with high confidence, the fingerprint's type and vendor are now dropped too (the OS decides, e.g. server), and
  guests matched by a virtualization integration never take type or vendor from the fingerprint (with no
  role-specific service they are general hosts), the same in the periodic OS probe, the manual probe and its
  completion notice; when the kind changes the old model is no longer kept. Already misjudged IPs correct
  themselves on the next OS probe.
- The dashboard racks card header holds only the title and a count; which room is shown and the Settings button
  moved to the top of the card body (like the AI audit card).
- The subnet page read "Address ranges (pools) (1)" with two sets of brackets; the count is now a tag next to the
  title, and the IP list header matches.
- The three cards at the bottom of the MAC history page had no gap between them.

## [0.6.59] - 2026-10-01

Security fixes from a CodeQL review: outbound requests could reach the server itself through IPv4-mapped
IPv6 addresses, the address check now happens at connect time (no DNS rebinding window), redirects no longer carry
integration tokens to another host, and ordinary accounts get error codes instead of raw AI errors. LibreNMS can now
create IP records from its ARP table (off by default, guarded against long-lived ARP caches by requiring a recent
switch MAC table sighting); Unauthorized IPs no longer samples large ARP tables; liveness changes no longer claim to
come from LibreNMS on sites without it (#49).

### Added
- **Create IPs from the LibreNMS ARP table** (GitHub #48; LibreNMS integration settings, **off by default**).
  Addresses IPAM does not have but LibreNMS's ARP table shows get a record (source "LibreNMS ARP", flagged as
  auto-collected in the IP list, with a "created" entry in the change history). Because LibreNMS ARP has no
  timestamps and some devices keep ARP entries for hours or days, by default the MAC must also have been seen
  in a switch MAC table (LibreNMS or MikroTik FDB) within 24 hours, so devices that left long ago are not brought
  back. Only inside a unique existing subnet (overlapping networks need a subnet scope); proxy ARP, broadcast and
  multicast MACs, network and broadcast addresses, one IP with several MACs, addresses in cooldown and (by
  default) DHCP pools are skipped; at most 500 per round. The background task summary says how many were
  created and the top reasons for the rest. Turning it on means those devices no longer appear under
  "Unauthorized IPs"; the settings page says so.

### Fixed
- **Online/offline entries in the IP change history said "librenms" on sites without LibreNMS** (GitHub #49).
  The liveness recompute has run for every site since September but still wrote a fixed source. Offline is now
  recorded as `system` (the evidence expired) and online as the source that brought it back (for example
  `scanner` or `opnsense`). The source filter on that page now lists every integration, not just seven. On sites with no LibreNMS integration, existing entries are relabelled `system` (with LibreNMS present they are left alone, since they cannot be told apart).
- **Unauthorized IPs missed most addresses on large sites**: detection looked at an arbitrary 2,000 ARP
  addresses and then cut the list to 200, sorted as text, without saying so. The difference with IPAM is now
  computed in the database, the list shows up to 1,000 (most recently seen first) and the tab says how many
  there are in total.
- The dashboard racks card sometimes forgot its setting after a reload: saving sent two preference updates at
  once and the older one could arrive last. Preference writes are now sent one at a time with the latest state.
- On phones the device list squeezed names into several lines (the name column had no width).
- Changing a LibreNMS integration's settings is now audited with the fields that changed, not only whether the
  token was replaced.
- Backend tests no longer write uploads to `/var/lib/jt-ipam` (they failed on GitHub CI, which cannot create it).

### Security
- **Outbound requests could reach the server itself through IPv4-mapped IPv6 addresses** (CodeQL review).
  `http://[::ffff:127.0.0.1]/` and `[::ffff:169.254.169.254]` were neither loopback nor link-local to the
  guard, yet Linux connects them to 127.0.0.1; any signed-in account could use the Tools page HTTP check to
  reach local services and cloud metadata. Mapped addresses are now judged as the IPv4 they stand for, and the
  AWS IPv6 metadata address `fd00:ec2::254` is blocked.
- **The guard is now applied at connect time.** The name used to be resolved for the check and resolved again
  by the HTTP client, so a DNS answer that changed in between (DNS rebinding) got through. Every outbound
  client (integrations, notifications, the AI, the Tools page) now resolves, checks and connects to the checked
  address in one step; the URL, Host header, SNI and certificate check still use the name.
- **Redirects no longer carry credentials to another host**: integration tokens (LibreNMS, Proxmox, LLM keys)
  were resent to wherever a 302 pointed. Same-host redirects and http to https upgrades keep them; a 303 (or a
  301/302 after POST) becomes a GET without the body; query parameters are no longer appended on every hop.
- The Tools page TCP, UDP and TLS checks now refuse loopback, link-local and multicast targets like the HTTP
  check (they could be used to scan the server's own local ports); private networks remain diagnosable.
- AI chat, semantic search and IP investigation no longer show raw error text to ordinary accounts (internal LLM
  host names, outbound-guard rules, upstream 401 bodies): they get a translated error code, admins still see
  the reason. When an AI tool fails, ordinary accounts see only the exception type; admins see its first line, without the SQL and parameters that database errors append.
- Two regular expressions that parse agent-reported nmap output were quadratic (10+ seconds on crafted input,
  blocking the worker); both are linear now with identical results.
- RIPE/TWNIC import (`/import/ripe/commit`) no longer returns database error text with SQL and parameters, and
  one failed row no longer turns the whole import into a 500.
- Scan agent 1.14.1: a truncated DHCP option in any reply used to end the rogue-DHCP listening window, so one
  malformed packet could hide a rogue server.

## [0.6.58] - 2026-10-01

MAC history: everything about one MAC, the IPs it used, the switch ports it appeared on and its timeline; every
anomaly tab shows a status light and keeps the last result; site-to-site VPNs from FortiGate, Palo Alto and
MikroTik on the topology map; MikroTik phase 2 (interfaces, neighbors, FDB); the periodic OS probe judges the
device type with Recog; a racks card on the dashboard; the system import streams (1.36 GB of memory down to 211
MB); and a redrawn integration map on the website.

### Added
- **Racks card on the dashboard** (at the bottom): rack drawings right on the dashboard, showing either all racks of
  one room or a chosen few; the setting follows the account across browsers. Up to 12 are drawn, the rest link to
  the Racks page.
- **MAC history** (type a full MAC into global search, or click a MAC in IP details, the change history or anomaly
  detection; also under Tools → MAC) shows everything about one MAC: the IPs it used (first and last seen, whether still in
  use, evidence, status light), when it took or left each IP and which MAC replaced it, the switch ports and VLANs it
  appeared on, DHCP reservations, and the device or VM it belongs to. Random (private) MACs are flagged with their
  limits explained: when the same IP keeps its host name but switches to another MAC and one of them is random, it is
  listed as "probably the same device" (a hint only, nothing is merged). Scaled to the user's visibility; the AI chat
  and MCP get a matching `mac_history` tool. API: `GET /api/v1/macs/{mac}/history`, any MAC spelling. Lookups use new
  expression indexes (migration 0172): about 0.2 ms for one MAC among 50,000 change records.
- **Device type from the periodic OS probe, using Recog.** Scan agent 1.14.0 sends the structured nmap result of
  its periodic OS probe (services, banners, page titles, server headers, certificates) instead of one line of text.
  The server judges it with the IP probe's logic, Recog fingerprints included, and stores the OS, a **device type**
  (camera, printer, storage, router, switch, firewall, access point, VoIP…) and the make and model on the IP
  (migration 0169). A real run against an nginx container: the agent's own guess was "Linux 4.15 - 5.19, OpenWrt
  21.02, MikroTik RouterOS 7.2 - 7.5"; the judged result is Linux, server, nginx 1.30.5. Older agents keep working.
- **Anomaly detection: device type or OS changed.** When the judged device type or OS family changes from one
  known value to another (a printer that became a Windows host), it is written to the IP change history and listed
  as an anomaly: the address may have been taken by another machine or the device replaced. Unknown to known does
  not count, a judgement that flips back is not listed, and an IP can be ignored. The AI chat and MCP can list it,
  and three categories the AI could not see before (ARP-only liveness, stale device links, MAC flapping) are added.
- The IP list has a **Device type** column (icon and type, model on hover), the IP details show type and model,
  and topology uses the primary IP's device type for devices whose type is unknown.
- **MikroTik: interfaces, neighbors and FDB (phase 2).** A router is now mapped to a jt-ipam device: chosen in the
  settings, or else the device that owns the IP of the API address (migration 0170). Its physical interfaces become
  that device's ports (MAC and comment included; ports that disappear are removed, cabled and hand-made ports are
  kept); LLDP/CDP/MNDP neighbors from `/ip/neighbor` get their own tab and become topology links between the two
  devices; the bridge host table (FDB, off by default because it can be tens of thousands of rows) feeds the topology and
  fills each IP's switch port with the same rule as LibreNMS, so a site with MikroTik and no LibreNMS gets switch
  ports too. MAC tracing, "which port is this IP on", VLAN members and the LibreNMS FDB list name the MikroTik switch.
  With no device to map to, the three sections are skipped and the router list shows "Device needed", not a failed
  sync. Deleting the router takes its FDB and neighbors with it and removes the ports it created, except cabled ones.
- **Every IP in anomaly detection has a status light.** Unauthorized IPs, IP conflicts, lost IPs, external
  exposure, rogue DHCP servers, dangling DNS, MAC flapping… every row with an IP gets a Status column: the same
  light and rules as the IP list (sortable, exported); in MAC drifts, where a row has several IPs, each IP gets
  its own light. The status is computed when the page is opened, not when detection ran. Addresses IPAM has no
  record of (unauthorized IPs) are judged from each source's ARP observations. Unauthorized IPs also show the MACs
  ARP saw (vendor, random MAC, who saw it, when) and the last-seen time; before, there was only an address.
- **Anomaly detection keeps the last result.** Manual and scheduled results are both kept and shown on entry
  (marked when scheduled), so there is no need to run it again; the tab and filter are in the URL, so going back
  from Identify returns to the same tab (the result used to be cleared and the first tab shown).
- **Site-to-site VPN on the topology map: FortiGate, Palo Alto, MikroTik.** Only OPNsense tunnels used to record
  their own device and get paired with the far end, so no other vendor's tunnel ever appeared on the map. FortiGate
  IPsec tunnels now know their device; Palo Alto gets IPsec tunnel sync (`show vpn flow`, "Site-to-site VPN" in the
  integration settings, on by default, migration 0171); MikroTik WireGuard records its device and local public key.
  Pairing rules are shared by all vendors (public keys for WireGuard, endpoint addresses for IPsec) and work across
  vendors, FortiGate to Palo Alto for example. The default "Subnets only" view now draws VPNs too (it used not to
  fetch them, so two paired WireGuard sites had no line between them).

- **Thinking check** (Admin → LLM / AI, next to the chat model): asks the chat model (and the audit and
  interpretation models when set separately) one very short question with the same thinking-off controls AI
  audit and interpretation use, and says per model whether it still thinks first, which controls the server
  rejected and how long it took. An empty answer counts as thinking, because some gateways (LiteLLM with an
  `ollama/` model) do not pass the reasoning through. When a server ignores the controls the only symptom used
  to be slowness or a cut-off answer. Checked against Ollama 0.33 directly and through LiteLLM 1.103
  (`ollama_chat/` and `ollama/`): thinking off answers in 0.3–0.5 s; the same question without the controls
  thinks 225 characters and runs out of tokens, or (with `ollama/`) returns an empty answer with the reasoning
  hidden. AI chat through LiteLLM with thinking switched off: 2.2 s instead of 21.5 s.

### Changed
- **Recog has its own page** (Admin → Recog fingerprints, like the OUI database page): release, fingerprint
  count, install and last-check times, per-file counts, check for updates now. Version info only lists the Recog
  release and links there.
- The system export's "Data to include" is now one item per row: name and count on one line (count right-aligned),
  what it contains on the next. In the old three-column grid a long name pushed the count onto its own line.
- The security section of System settings is now one setting per row (name and explanation on the left, the
  control on the right); the old three-column grid had uneven rows.
- **System import no longer loads the whole export file into memory.** It reads the file twice in chunks: first to
  verify the passphrase and integrity (and read the per-table counts at the end), then to parse and write row batch
  by row batch. On a 27.5 MB export with 427k rows the peak memory dropped from 1.36 GB to 211 MB at the same speed,
  with identical per-table results. The upload is written straight to the spool directory instead of being read into
  memory. Decrypted content never touches the disk, replace mode wipes nothing before the file is verified, and the
  file format is unchanged.

### Fixed
- Requests rejected by nginx's own rate limit now get `Retry-After: 60` and a JSON body, like the backend's
  `429` (they had neither). Fresh installs ship it; upgrades add it to an existing site config (only when
  missing, backed up, restored if `nginx -t` fails).
- The Investigate dialog no longer calls random MACs a conflict: three MACs on one IP, two of them private
  (random) Wi‑Fi addresses from a phone or laptop, used to read "unusual for a single host"; it now says it is most
  likely the same device, and only more than two vendor-burned MACs still raise the warning.
- KALLAX shelves on the racks page no longer get a horizontal scrollbar: the width was measured rounded (428.28 px
  as 428 px), so the container was a fraction of a pixel too narrow and Macs set to always show scrollbars showed one.
- In the dark theme, embedded rack drawings (the merged room view, the dashboard) no longer get a card border: the
  site-wide card border rule overrode the "no frame" style.

### Documentation
- The integration map on the landing page is redrawn: the old one only had room for 8 products and squeezed the other
  twenty-odd into a tag cloud. It now reads "sources (by category, every product named) → jt-ipam → serves other
  systems" and stacks on phones; the caption now says sources are always read-only and only OPNsense alias push and
  certificate delivery write back, when you enable them.
- "Which sources create IP records on their own?" is re-laid out: a three-column table for the sources that may create
  records, and tags for the fifteen match-only products.
- Section headings on the site are links themselves (clicking copies the URL with the anchor) instead of a trailing
  "#"; headings inside cards are not links.
- Em dashes are gone from all documentation, replaced with colons, commas, semicolons and parentheses.

## [0.6.57] - 2026-10-01

"IPs without an agent" is paged on the server, the AI chat can be told not to think, and error responses keep
their headers. Also fixes the dependency advisories that turned the v0.6.56 CI audit red.

### Changed
- **"IPs without an agent" (Wazuh and OCS) is paged, filtered and sorted on the server.** A large site with
  53,000 gaps used to download the whole list (27–42 MB) every time the tab was opened: 8–10 s, with the
  browser frozen for about 2 s while filtering. Now one page comes back (about 180 KB); the first load takes
  1–3 s and paging is well under a second. Section, subnet, unit and status filters, a new text filter and
  every column sort cover all gaps rather than the page on screen, and the filter menus come from the server.
  Export still downloads everything that matches. Sorting by subnet, section or unit did not work before.
  The API returns the full list as before when `page` is not given.
- **AI chat can turn thinking off** (Admin → LLM / AI, "Let the model think before answering in AI chat";
  on by default, as before). Off sends the thinking-off controls on every chat turn (Ollama's `think:false`,
  and `reasoning_effort` and friends for OpenAI-compatible servers and gateways such as LiteLLM), so a thinking
  model answers much sooner. A control the server rejects is dropped and remembered, as for AI triage.

### Fixed
- Error responses dropped the headers of the exception: a `401` now carries `WWW-Authenticate: Bearer` and a
  backend `429` carries `Retry-After` (60 s for the rate limiter, 900 s for the failed-login lockout).
- The zh-TW changelog was missing the 0.6.56 entry; a test now keeps both changelogs on the same version.

### Security
- Frontend dependencies: axios 1.20.0 (advisories published 2026-09-30), brace-expansion and js-yaml in the
  build tooling (2026-09-29). These turned the v0.6.56 CI audit job red.

### Documentation
- API manual: paging, filter and sort parameters of `/wazuh/missing-agents` and `/ocs/missing-agents`, and
  the `401` / `429` headers. Release test checklist covers these changes.
- **Feature map rewritten**: one line per item in all three languages, every integration named (each DNS
  server, Kea, ISC DHCP, OCS Inventory NG, AdGuard Home, Palo Alto, MikroTik), plus features that were missing
  (IP probe, device import, scan agent load, rack embed, system export / import, AI settings and more). The
  home page integration badges and the "which sources create IP records" table gain the same products, and the
  READMEs gain Wazuh and OCS.
- **Every section heading on the docs site has a `#` link** with a fixed English anchor, so a section that is not
  in the top bar can be shared and opens scrolled to it; clicking the `#` also copies the link. In the API manual
  a link to a subsection opens its section and scrolls to it (it used to fall back to section 1).
- The docs use a half-width slash throughout.

## [0.6.56] - 2026-09-30

Large-scale environments, second round: every other integration, non-admin accounts, background jobs,
export/import, plus fixes found by auditing the API manual against the code.

### Security
- **Audit entries of user management, OPNsense and Wazuh never recorded who did it.** Twenty endpoints took
  the actor from a request attribute nothing set, so creating/deleting accounts, changing group membership
  (privileges) and editing those integrations were logged without an actor. The authenticated user is now
  set for both JWT and API tokens.
- **AI tools could read what the REST API refuses.** Scan agents, certificates and distribution, Wazuh
  agents and gaps, and OCS computers are admin-only over REST but were available to wildcard-read accounts
  in the AI chat and MCP. They are admin-only now, and a test binds each tool to the REST endpoint it mirrors.
- **Writes through MCP `tools/call` are audited** (`mcp_tool_exec`: tool, summary, channel, source IP). Unlike
  the AI chat they run without a confirmation step; the manual now says so.
- phpIPAM Basic-Auth logins had no rate limit: the nginx location `/api/phpipam/user/` never matched the real
  path `/api/phpipam/<app_id>/user/`. Fixed in the templates; upgrades patch existing sites.
- Dependencies: PyJWT ≥ 2.14 (11 CVEs in 2.13), urllib3 ≥ 2.8 (3 CVEs in 2.7).

### Fixed
- **The site-wide liveness recompute failed on sites with more than about 6,500 IPs.** It wrote one row per IP
  into a single INSERT and exceeded the database driver's parameter limit, so online/offline statuses stopped
  updating (every 5 minutes, silently). It now runs in about 10 s on 145k IPs.
- **Accounts that can see more than 32,767 objects got a 500 from every list page and AI tool** (the visible
  set was passed as individual parameters). All of the backend now uses one array parameter; the guard test
  covers every module.
- The dashboard returned 500 for accounts scoped to a customer.
- Wazuh and Zabbix: the same agent/host twice in one response failed the whole sync.
- DNS sync attached a name to an arbitrary one of several overlapping addresses; now unique matches only.
- `GET /api/v1/librenms/devices` returned 500 when a device had a primary IP.
- **MCP at `POST /api/mcp` (no trailing slash) returned 405**, yet that is the URL the manual and the client-config
  generator give. Both forms work now.
- **Rack embed SVG could not be embedded from another site behind nginx**: the site-wide
  `Cross-Origin-Resource-Policy: same-origin` blocked the `<img>`, and the images rule cached it for 30 days.
- `/readyz` behind nginx answered with the web page (always 200); it now reaches the backend (DB + Redis).
- MAC drift was a false alarm in any multi-switch network (a host is also seen on every upstream uplink).
  It is now a port change on the same switch, shown as from/to port and when; VM migrations, randomised MACs
  and moves between shared ports are listed separately as reference and do not notify.
- Topology never drew device-to-subnet links from ARP (it compared LibreNMS device ids with jt-ipam ids);
  the AI `trace_mac` tool had the same mix-up and never showed a switch port to non-admins.
- Behind an LLM gateway such as LiteLLM: when the server rejected one "no thinking" control, all
  three were dropped (including `reasoning_effort`, which LiteLLM turns into Ollama's `think:false`), so
  thinking was never switched off. Only the rejected one is dropped now, and the rejection is remembered per
  server and model.

### Changed
- **Integrations no longer query per row.** Wazuh agent sync 334 s → 16 s on 30k agents; SCA refreshes at most
  200 agents per round, oldest first, and stops on HTTP 429 (migration 0168). The five firewalls share one
  batched writer for ARP / DHCP leases / VPN sessions (Windows DHCP, Kea and ISC DHCP use it too); OCS, Zabbix,
  ESXi, DNS, AdGuard and Proxmox load what they need up front.
- **System export streams** (memory on 145k IPs: 1.7 GB → 150 MB default scope, 5.4 GB → 350 MB full scope;
  same file format). Import writes 1,000 rows per statement. The audit chain is verified in batches.
- Wazuh / OCS pages fetch "IPs without an agent" (and Wazuh's full agent list) only when the tab is opened:
  page load 17 s → 1.5 s on a large site.
- **AI interpretation can use its own model** (Admin → LLM/AI): unauthorised-IP triage, the investigation
  narrative and the firewall-change reading. Empty = the chat model, as before.

### Documentation
- **API manual rewritten against the code** (21 sections in zh/en/ja): OCS, ESXi, Kea, ISC DHCP, IP probe,
  event rules, jump hosts, AI audit, investigate, system administration and rack embed were missing; many
  statements were wrong (`/api/v1/me`, subnet deletion, rate limits, permissions, page sizes, MCP). A test
  keeps every API group in the manual and every path in the manual real.
- Release test checklist covers every recent change, and the zh-TW checklist is complete again.

## [0.6.55] - 2026-09-30

Large-scale environments. GitHub issue #47 (a device with 30,000+ ports broke LibreNMS sync) showed that
the code assumed "one query fits" in many places. A synthetic large site (2,000 /24s plus a full /16,
145k IPs, 20k devices, 200k ports with 40k on one device, 290k FDB, 100k leases, 500k IP changes,
1M daily liveness rows, seeded by `backend/tests/seed_scale.py`) was then run through every GET endpoint, the
sync paths, anomaly detection and every page in a real browser.

### Fixed
- **LibreNMS sync failed with "the number of query arguments cannot exceed 32767" (#47).** Lists that
  grow with the network (ports of a device, IPs of a subnet, leases, agents…) are now passed as one array
  parameter (`app.core.sqlin.in_values()`), in port pruning/reconcile, the ports tab, hostname sources,
  DHCP, uptime, anomaly detection, topology, Wazuh/OCS "missing agents" (also a 500 on large sites),
  OCS, Proxmox and LibreNMS. A guard test keeps these modules from reintroducing Python-list `IN`.
- **56 foreign-key columns had no index** (migration 0167): deleting a referenced row scanned the child
  table once per row. Pruning 33k ports took 106 s (now 8 s), and deleting a /16 scanned several tables per
  IP. A guard test fails when a new foreign key has no index.
- **Opening the topology froze the whole backend.** Backbone inference compared every pair of switches
  against every port (5,000 switches: 10+ minutes at 100% CPU, every request stuck). It now only looks
  at pairs that actually see each other (verified identical to the old algorithm on 200 random
  topologies) and cable ends are loaded in one query (53 s → 20 s on 20k devices). Over 2,000 devices
  the graph is not built; the page (and the AI tool) ask for a subnet filter instead.
- **LibreNMS sync on 5,000 devices went from ~14.5 minutes to ~1.5.** ARP 563 s → 22 s (291k queries →
  6), ports 202 s → 1 s, devices 31 s → 15 s; unchanged rows are no longer rewritten every cycle. A device
  whose IP field is not an IP (a host name) no longer crashes the whole device sync.
- Hostname sources and DHCP lease sightings write in batches (a round of 100k names was 100k queries).
- **Subnet page of a /16:** only the first 1,000 addresses were loaded and all 1,000 rows were rendered
  at once (the browser froze ~10 s). The table is paginated, the page says when only the first 1,000 are
  shown (with a link to the paged, searchable IP list), and the IP indicator uses a server-side count per
  /24 instead of the loaded subset (a full /16 showed 4 blocks).
- OUI search without criteria returned 500 instead of 400.

## [0.6.54] - 2026-09-29

### Added
- **Recog fingerprint database for the IP probe (optional).** The probe now matches what hosts say about
  themselves (SSH banners, HTTP `Server` headers, page titles, TLS certificate subjects/issuers, SMB OS strings,
  FTP/SMTP/POP3/IMAP/telnet banners) against [Recog](https://github.com/rapid7/recog) (Rapid7, BSD-2-Clause).
  It recognises what nmap cannot: a vendor's default certificate (a FortiGate, a Synology), a management page
  title, the distribution in an OpenSSH comment ("Ubuntu Linux 20.04" instead of "Linux 4.15 - 5.8"). Results
  add a device type, OS, hardware vendor, **model** and software for ports nmap left unnamed, each listed in the
  evidence as `recog:…`; Recog's own "assert nothing" entries (e.g. Let's Encrypt, Samba's spoofed "Windows 6.1")
  are respected, and a vendor's default-certificate name is no longer shown as the host's name.
  The Ruby patterns are translated to Python and every fingerprint must pass its own examples, otherwise it is
  dropped (4,671 of 4,676 kept in 3.2.0); patterns that can backtrack catastrophically on a hostile banner are
  refused and inputs are capped. The download is checked against GitHub's SHA-256, only `xml/*.xml` is read,
  with size limits, through defusedxml. Stored in the database (migration 0166, one row per fingerprint file).
  `jt-ipam.sh install` / `upgrade` download it (a failure only warns: the probe works without it; offline hosts
  use `--recog-zip <file>`), and **jt-ipam-recog-refresh.timer checks GitHub once a week** (Recog releases every
  one to six weeks). Version info shows the installed release, fingerprint count, last check and a "Check for
  updates now" button; the system diagnostics warn when it is missing or has not updated for three weeks.
  CLI: `python -m app.cli.recog update [--force] [--file …]` / `status`.
- Scan agent 1.13.0: reports the full certificate subject/issuer fields (the text output has no OU / L, which
  device default certificates are recognised by) and host-level script results; `smb-os-discovery` was
  requested but its output was dropped until now.

### Fixed
- **The OUI and GeoIP refresh timers were never installed** by `jt-ipam.sh`: customer installs never refreshed
  MAC vendors or GeoIP on their own, and the OUI unit on hosts that had one pointed at a script that was never
  committed (it failed every month). Install and upgrade now install all three refresh timers (GeoIP, OUI,
  Recog), a new host fetches the OUI list right away, `doctor` checks them, and `uninstall` removes them.
- Outbound requests with a response size limit decoded gzip twice (every GitHub API call failed with a
  decoding error) and did not follow redirects. Both fixed; the limit still counts the decompressed size.
- Security (CodeQL): Tools → HTTP check could make the server fetch loopback / link-local addresses (cloud
  metadata); every hop, redirects included, is now checked. The login `?next=` accepted `//host` and `/\host`
  and could send the user to another site; only same-origin paths are followed now. Plus hardening: linear-time
  checks instead of backtracking regexes, path containment for transfer and floor-plan files, less detail in
  some error messages.

### Changed
- IP probe: a host that answered nothing is summarised as "no response" (agent 1.12.1 counts closed ports);
  type / OS / vendor are labelled as a guess; classification looks at the whole host (a NAS with media streaming
  is no longer a camera, Samba is no longer Windows); the by-address page shows when ARP last saw the address;
  the ports table sizes columns by content.

## [0.6.53] - 2026-09-29

### Added
- **Device import (#46).** The device list only had export. Import takes CSV or Excel (.xlsx); columns are matched
  by header, including the list's own exported headers in any interface language, and types may be codes or the
  displayed words. Location, rack and unit are given by name (a rack alone implies its location; a rack name
  used in two locations must come with the location); an IP only matches an address already in IPAM and is
  never taken from another device. Existing devices (same name) are skipped or updated; blank cells leave the
  value unchanged. Every row is previewed first (create / update / skip / error with the reason) using the same
  validation as the API, including rack-position overlaps within the file; rows with errors are not written.
  The import runs as a task and audits every device. A template can be downloaded blank or with every current
  device, to edit and import back in update mode.
- **Standalone Kea DHCP Server (#45).** Pools, reservations and leases are read over Kea's JSON control API,
  through the Control Agent (Kea 2.x) or straight from the DHCP server's own HTTP control socket (Kea 3.0); the
  two are told apart automatically. Reservations kept in a host database are read when host_cmds is loaded;
  without lease_cmds pools and reservations still sync and the page says leases need lease_cmds. Optional HTTP
  basic auth (password encrypted). Tested against real Kea 2.4.1 and 3.0.4. Migration 0165.
- **Standalone ISC DHCP Server (isc-dhcp-server, #45).** It has no API that lists leases, so a scan agent
  installed on the DHCP host (agent 1.12.0, self-updates) reads dhcpd.conf (including `include` files) and
  dhcpd.leases locally and reports only the parsed pools, fixed addresses and active leases, never the file
  contents (dhcpd.conf often holds DDNS/OMAPI keys). The paths can only be set on the DHCP host
  (`JT_IPAM_DHCPD_CONF` / `JT_IPAM_DHCPD_LEASES`); the server cannot change them. An unreadable file keeps what
  was there and names the file and the reason; an agent that stops reporting marks the source as failing.
  Tested against a real isc-dhcpd 4.4.3-P1. Both hosts count as known DHCP servers in rogue-DHCP detection
  (Windows DHCP hosts now too).
- **Probe from the anomaly lists (admin only).** Categories whose rows are a single host (IP conflicts,
  lost IPs, unauthorized IPs, rogue DHCP servers, exposure, duplicate records, ARP-only, stale device
  links, MAC flapping) have a Probe button in the Actions column that opens the same probe page as the IP
  detail. An address IPAM has no record of (an unauthorized IP) is probed by address: it must fall inside
  a managed subnet and is run by that subnet's scan agent; addresses outside every managed subnet,
  network/broadcast addresses and overlapping subnets handled by different agents are refused. The page
  marks such an address as not in IPAM.
- **Every table's columns can be resized by dragging the header edge.** Applied once at the table
  component, so every existing and future list gets it; the few hand-written tables with column headers
  (VLAN members, firewall rules on an IP, agent dependencies, notification matrix) use `v-col-resize`.
  Layout before dragging is unchanged.
- **Tab bars that do not fit get left/right scroll buttons** (double-click jumps to the end), so the
  first and last tabs are reachable without a scroll wheel.

### Changed
- **Anomaly tables size short columns by their content** (kind, source, interface, port…); the last column
  takes the remaining width and wraps instead of being cut off. The actions (Probe, Ignore, AI triage) are
  one column; when the window is narrow and the buttons collapse to icons, hovering shows what each is.
- **Firewall rule rot** has a Firewall column (the same rule name can come from several firewalls) and the
  kind is shown in words instead of a code.

### Fixed
- **Device export wrote internal IDs** for location, rack and unit; it now writes their names (and the type and
  physical/virtual as words).
- **LibreNMS FDB sync hit `fdb_entry_unique` and the task stayed "running" (#43).** The same MAC/port/VLAN can
  appear twice in one response; rows are now merged in memory. A task whose database session broke now always
  ends as "failed" with the error instead of staying "running".
- **An integration that cannot connect reported "succeeded, 0 records" (#44).** Proxmox with every node
  failing, and LibreNMS / AdGuard when unreachable or unauthorised, now fail the task with the last error.
- **"Test connection" for Windows DHCP and Kea showed the browser's own 15-second timeout** instead of the real
  reason when the host did not answer.
- **Firewall rule rot false positives.** OPNsense's automatically generated Anti-Lockout rules were
  reported as port forwards whose target is not in IPAM; port forwards to an alias were reported the
  same way; and a WAN rule allowing only ICMP (ping) was reported as "any → any, this interface has no
  firewall". any → any now means every protocol and every port.

## [0.6.52] - 2026-09-28

### Added
- **IP probe (admin only).** A "Probe" button next to Investigate on an IP opens a page where the scan
  agent assigned to the subnet identifies the address: open services and versions, OS fingerprint, TLS
  certificate and page titles, and name lookups (reverse DNS, NetBIOS, mDNS). It only reads information
  and never logs in; the script list is fixed inside the agent. The page shows which stage the probe is
  in, keeps every past result, compares each result with the previous one (opened / closed / version
  changed), downloads the raw result as JSON, and lists applications (including ones recognisable only
  from a self-signed certificate). Leaving the page does not stop it. A probe also appears on the Tasks
  page and notifies the person who started it when it finishes or fails (`identify.done` in the
  notification matrix). One probe per IP at a time; every start is audited. Migration 0162.
- **Scan agent load.** Every cycle is recorded (kept 7 days). The Scan agents page has a Load column
  (liveness cycle time / interval, background queue) and a panel with the trend, per-subnet timing and
  actionable suggestions (which subnets to move, which subnet is unusually slow, which one is too large);
  a subnet can be moved to another agent from there. Three overloaded cycles in a row notify admins once,
  and again on recovery (`agent.overloaded`). Subnets are never re-assigned automatically: the right agent
  must share the subnet's network segment. Migrations 0163-0164.
- **Every screen checked at phone width**: `e2e/mobile-all-routes.spec.ts` opens every route at 390px and
  fails on page-level horizontal scroll, clipped or off-screen content and text squeezed to a few
  characters per line.

### Changed
- **Scan agents report liveness right away** (agent 1.11.0, self-updates). The ping/TCP/ARP/DHCP pass
  reports each subnet as soon as it is done; reverse DNS, NetBIOS, mDNS and OS fingerprinting run in the
  background with bounded parallelism, names not waiting for nmap. Before, a cycle with OS fingerprinting
  could run for over an hour without reporting any subnet's online state. Background results fill in data
  but never count as being seen online and never create IPs, and are retried if the server is restarting.
- **Large subnets are scanned in rotating chunks.** The per-cycle limit rose from 1024 to 4096 addresses and
  larger subnets are covered one chunk per cycle instead of scanning only the first chunk forever. If a full
  pass takes longer than the online threshold, it counts as overload with a suggestion to split the subnet.

### Fixed
- **guacd consoles on high-DPI screens.** The remote screen size is now in device pixels, like the official
  client; the SSH console no longer shows text twice as large on a Retina display.
- **SSH console font size controls work with the guacd engine**: A-/A+ change the size during the session
  (only the font size is let through to guacd, validated) and the size is remembered.
- **Phone layout**: the sidebar scrolls under a finger instead of scrolling the page behind; console status
  bars wrap; the notification popover stays on screen; rack diagrams default to a zoom that fits the phone;
  pagination wraps; tables that squeezed columns one character wide scroll horizontally; the phpIPAM
  migration steps, system transfer cards, Graylog guide and notification matrix fit the screen.
- **AI audit wording**: zh-TW term fixes no longer swap across word boundaries, "production environment"
  uses Taiwanese usage, and raw field names in findings are annotated with the on-screen name in the reader's
  language (e.g. 狀態（state）為使用中（active）).
- A subnet's IP list combines "only stale", "only DHCP" and the text filter; the match count is the number of
  rows listed.
- Bulk delete of IPs starts the reuse cooldown and records the deletion in the IP history, like single delete.
- OCS card: memory shows installed and usable amounts honestly; Ceph RBD, ZFS zvols and DRBD devices are not
  listed as physical disks.
- The probe no longer classifies a Linux host running CUPS as a printer, and a certificate name counts as a
  host name only when it looks like one.
- zh-TW texts no longer use 實例 for an integration.

## [0.6.51] - 2026-09-27

### Added
- **The OCS card on device details shows OCS's own hardware.** Vendor / model / serial come from OCS
  instead of the device fields, which another source may have filled (a Windows machine created by
  LibreNMS showed "windows / Intel x64", its OS and CPU architecture). New rows: chassis type,
  motherboard and BIOS, and the main components: CPU (cores / threads), memory (total and modules),
  physical disks (no zram / loop) and GPUs (one card reported by both lspci and the driver is merged;
  lspci BAR sizes are not shown as video memory). A factory placeholder system serial ("0123456789")
  is replaced by the motherboard serial, marked as such. Stored in `ip_addresses.ocs_hw` (migration
  0160) and filled by the next OCS sync; until then the card says so.
- **"IPs without an agent" can be filtered by online status** (Wazuh and OCS pages): a status column
  with the IP list's dot, and a status filter using the same rule.
- **Ports / cabling: the MAC column shows the vendor** under the MAC, like the IP list.

### Fixed
- **Device ports follow LibreNMS.** A removed NIC or USB NIC stayed in the device's ports although
  LibreNMS had marked it deleted: the sync only ever added ports. Imported ports now record their
  source (migration 0161); after a complete read, ports LibreNMS no longer reports are removed.
  Ports you created, cabled ports and pass-through-mapped ports stay; a failed read or an empty port
  list removes nothing. "Import from source" follows the same rule and says how many it removed.
- Docker / Podman `veth…` interfaces are no longer imported as device ports (one host had 41). Sites
  that never customised the pseudo-interface patterns get the new pattern on upgrade.
- Device fields holding something that is not hardware information (an OS name as vendor, a CPU
  architecture as model) are replaced when OCS has a real value; values you entered stay. A factory
  placeholder serial on the device is replaced by the motherboard serial.
- Exporting "IPs without an agent" left subnet, section and unit blank (the column keys differ from
  the data fields). Table exports can now take a per-column export value.
- The AI chat's IP-detail tool includes the OCS hardware summary.

## [0.6.50] - 2026-09-27

### Fixed
- **Version info: the Required components card no longer squeezes its name column.** A long guacd
  version string (for example `… (build 3) for Ubuntu 24.04 LTS (amd64)`) sat unwrappable on the
  right and crushed the name to one character per line. The right side now shows only the status;
  the version, without the OS suffix, goes on its own line under the name, and the name column keeps
  a minimum width (checked at desktop and phone widths).
- **Confirmation pop-ups, dropdowns and dialogs are no longer covered by the AI assistant button.** The
  floating button sat above every overlay (z-index 9000 against Naive UI's 2000 and up): a delete
  confirmation that opened in the bottom-right corner had its OK button half under it, and clicking
  there opened the assistant instead of deleting (found by the address-range e2e before this release).
  The button now stays above page content but below overlays and the phone sidebar.

### Documentation
- **Supported distributions are listed exactly**: Debian 12 / 13 and Ubuntu 22.04 / 24.04 / 26.04 on
  x86_64. guacd became a required component in 0.6.49 and is prebuilt only for those, so the old
  "Debian 12+ / Ubuntu 22.04+" was no longer true. INSTALL (en / zh-TW / ja) gains a "Supported
  distributions" section: why these versions, what happens on derivatives, other versions and ARM,
  how new OS releases get added, and the `--guacd-tarball` route for other Debian / Ubuntu versions.
  README and the Pages site list the same versions.

### Tests
- The SFTP path-probe ticket test uses a fake Redis like the other ticket tests, so it no longer
  needs a real Redis (it failed on CI, which has none).

## [0.6.49] - 2026-09-27

### Changed
- **guacd is now the default engine for the RDP and VNC consoles, and a required component** (GitHub
  issue #42). The old engine, aardwolf, becomes an optional fallback: it ships wheels only up to
  Python 3.13 and crashes on 3.14. Upgrading switches existing installs to guacd too (migration
  0158; SSH is left as it is). `install` always installs guacd and stops with instructions
  (`--guacd-tarball`) if it cannot; `upgrade` installs it when missing and, if that fails, warns
  loudly but still finishes.
- While guacd is down, RDP / VNC fall back to an available built-in engine instead of failing;
  `doctor` and System check report it as a problem with the fix. The engine is decided when the
  ticket is issued and travels in it, so guacd restarting mid-way cannot make the browser and the
  server speak different protocols.
- Version info gains **Required components**, listing guacd (version, running); aardwolf moves to
  the optional list and no longer raises a warning when absent.
- System settings: RDP / VNC show "guacd (default)", SSH "Built-in (default)"; the guacd status no
  longer flashes a red "cannot reach" before it has loaded.

### Added
- **Detected DHCP ranges appear in the subnet's address ranges (pools) automatically**, marked
  "Auto" with their source (e.g. firewall-a · KEA). They follow upstream (replaced when the range
  changes, removed when it is no longer reported or the integration is deleted); manual ranges
  are never touched; nothing is created when the subnet is ambiguous (overlapping subnets) or the
  range would overlap an existing one (migration 0159). Auto ranges cannot be edited or deleted by
  hand (change the DHCP server) and are not counted twice in DHCP usage.
- **SFTP per-file limit is configurable in System settings** (default still 100 MB, up to 100 GB).
  Changing it makes the browser **test the actual transfer path** (browser → front reverse proxy →
  IPAM nginx → backend, over the same WebSocket path as SFTP) and report upload / download speed
  and how long a file of that size would take; a layer that cannot pass it is named (WebSocket
  blocked, 1009 message too big, cut off mid-way, data not getting through). With a raised limit
  the test re-runs whenever the settings page opens.
- **Large SFTP downloads go straight to disk**: above 64 MB, Chrome / Edge ask where to save and
  write while receiving instead of holding everything in memory; other browsers are told to switch
  or use scp above 2 GB; downloads show progress; files over the limit say so up front.
- **Mobile sidebar**: on phones the sidebar collapses completely and the content uses the full
  width; a button at the top left opens it over the content; picking a page, tapping the dimmed
  area or Esc closes it.
- **Deleting an integration takes back what it wrote**: removing a DNS server, firewall, DHCP
  server, LibreNMS, Zabbix, Wazuh, OCS, Proxmox, ESXi or scan agent also removes its hostnames,
  lease / reservation flags, pool ranges, NAT rows, VPN tunnels and VM mirror (they used to stay,
  with nothing ever syncing them away).
- **Scan agent 1.8.1**: a reverse lookup clears the agent's old name only when DNS answers that
  there is no PTR record (timeouts and unreachable DNS do not); it used to report nothing, so the
  old name stayed forever. Agents update themselves.

### Fixed
- **Data the upstream stopped reporting is finally removed** (reported: an IP got a new machine,
  its DNS record was deleted and synced, yet it kept the old host's name). After auditing every
  integration:
  - Hostnames: none of the 16 sources (DNS, AdGuard, Windows DHCP, five firewalls, LibreNMS,
    Proxmox, Zabbix, Wazuh, OCS, the scan agent's rDNS / NetBIOS / mDNS) ever removed a name. Each
    instance of each source now records what it reports (migration 0155) and removes what it no
    longer reports **only after a complete read**; failed or partial reads remove nothing; a breaker
    stops removing more than half at once or anything after an empty complete read; instances of
    the same kind do not remove each other's names. Pre-upgrade data is claimed by real syncs,
    at once for single-instance sources and after 24 h otherwise. Renames to a later-sorting name
    that never took effect (Proxmox, Zabbix, Wazuh, OCS, scan agent) now do.
  - DNS: records deleted on the server are removed after a sync; zones that fail or read empty
    are left alone; partially failed zones stay in the error message (it used to end as success).
  - "Has a DHCP lease": one boolean shared by six DHCP sources, each clearing it its own way (never
    without a subnet scope, never on Palo Alto, and clearing other sources' leases when scoped).
    Now recorded per source and derived from all of them (migration 0156); leases not refreshed
    for 7 days stop counting. Expired / declined Windows DHCP entries and unused reservations are
    no longer leases.
  - Proxmox: VMs / CTs deleted in PVE are removed; the VM primary IP is recomputed every run (it
    was set once, so after an IP change or VMID reuse the console opened from the old IP reached
    the wrong guest). Two standalone hosts both named "pve" no longer share one cluster; an ESXi
    instance named like a PVE cluster no longer takes and deletes its VMs.
  - Wazuh: agents deleted in Wazuh are removed from the mirror; hostnames and OS come only from an
    agent that still represents the IP (with an old and a new agent on one IP it used to pick one
    at random).
  - LibreNMS: deleted devices are removed (with their VLAN mappings, ARP and FDB); VLANs removed
    from a device are unmapped; the scheduled port sync applies the pseudo-interface filter too
    (Windows ethernet_N ports came back every run); a replaced machine's switch port is cleared,
    while a machine that is merely off keeps its last location.
  - OCS: the BMC (IPMI) interface reported by the Linux agent is no longer used for matching (the
    host's OS and name landed on the BMC address); a MAC on two IPs (an old DHCP address kept
    after moving to a static one) is settled by the IP the NIC reports; after a complete full sync,
    and for stale inventories, OCS OS and inventory id are cleared.
  - VPN tunnels record their owner (migration 0157): renaming a firewall no longer orphans its
    tunnels; MikroTik removes the last peer too; idle MikroTik WireGuard peers were written with a
    status the table rejects, failing the whole VPN section.
- **An unreachable device no longer wipes data while reporting success**: OPNsense (NAT, VPN
  tunnels, pool ranges, reservations), FortiGate (policies, NAT, address objects, pool ranges,
  reservations, IPsec; an unreadable VDOM list is not treated as complete), pfSense (reservations,
  extra pools), Palo Alto (NAT when the vsys list is unreadable), Windows DHCP (lease flags and
  names when a scope fails). Failed reads keep existing data and are reported; OPNsense shows
  partial failures. Rule-change detection no longer snapshots an empty rule set (a failed read) or
  sends a false "all rules removed" alert.
- **Overlapping subnets are no longer guessed**: with the same IP in several units' subnets and no
  scope set on the integration, hostnames, MACs, liveness evidence and lease flags landed on an
  arbitrary record, often another unit's; ambiguous matches now write and create nothing.
- **Liveness is recomputed every sync run**: it only happened during a LibreNMS sync, so sites
  without LibreNMS never saw an IP go back to offline. AdGuard's time no longer counts as liveness
  (it only means the IP is in AdGuard's configuration, so powered-off hosts stayed green); the
  field reads "Last seen (AdGuard config)". The red "rogue DHCP" tag uses the detector's 7-day
  window. Firewall ARP times keep the newer value (two firewalls of one vendor moved it backwards).
- **MikroTik lease hostnames were stored as "manual"**, overriding what users typed and never
  cleared: they use their own source; unknown sources are refused. The few IPs already affected can
  have that manual entry removed under Hostname sources.
- **Untouched hostname / MAC fields in the IP edit form counted as manual input**: saving only a
  description froze the displayed name as manual and marked the MAC as manual. Unchanged values are
  no longer edits; a typed name that another source would override is pinned to manual (it used to
  be overwritten right after saving, without a word).
- **Rack diagrams on phones**: pan left / right when the rack is wider than the screen; the
  Front / Rear toggle no longer sticks out of the card.
- Address range table column widths (the range no longer overlaps the purpose tag; the name column
  no longer collapses to one character per line); liveness evidence rows line up.
- CI backend tests finally run and pass (CI key format, missing rdp-freerdp, test DB fsync, pytest
  plugin order); an invalid ENCRYPTION_KEY says what is expected and how to generate one.

## [0.6.48] - 2026-09-26

### Added
- **guacd console engine (RDP / VNC / SSH, optional)**: each protocol can be switched to guacd (the
  server side of Apache Guacamole) under Admin → System settings; the built-in engines stay the
  default. Credentials go from the server to guacd and never through the browser. SSH host keys
  are still confirmed once and pinned, and guacd has to match the pinned key (a mismatch says
  "host key does not match", not guacd's "Aborted. See logs."). Over guacd, SSH accepts an input
  method (Chinese IME) and copies/pastes with Ctrl+Shift+C / V. jt-ipam prebuilds guacd for every
  supported OS version (Debian 12 / 13, Ubuntu 22.04 / 24.04 / 26.04); install it with
  `jt-ipam.sh install|upgrade --with-guacd`. It runs as the `jt-ipam-guacd` service bound to
  127.0.0.1 only, and `doctor` and System check cover it. Online install works once the prebuilt
  GitHub release is published; until then `--with-guacd` says the build is not listed in
  SHA256SUMS and does not install it (offline: `--guacd-tarball`).
- **VNC with a username**: for VNC servers that ask for one (macOS Screen Sharing, UltraVNC
  MS-Logon, VeNCrypt Plain), fill in the new Username field on the connect form. This needs the
  guacd engine; the built-in engine supports passwords only and says to switch to guacd if a
  username is given. Leaving the field empty on such a server says to fill in the username.
  Saved VNC credentials may have no username; migration 0154 clears the old "vnc" placeholder so
  it is never sent as a username.
- **The console status bar names the engine** of the current connection (aardwolf / FreeRDP /
  guacd / built-in).
- **OCS can be limited to subnets** (like Wazuh and the other integrations): sync only matches MACs
  of addresses in scope, so a record in an overlapping range that happens to have the same MAC
  (a cloned VM, say) is not touched. Empty means global; existing setups behave as before.
- **Firewall rules, aliases and NAT on the IP detail page click through**: a row opens that
  vendor's rules or alias page (OPNsense, pfSense, FortiGate, Palo Alto, MikroTik) or the NAT page
  with the right device selected and only that entry shown; a banner offers "show all" and, when
  the entry is gone, says why that may be.
- **Anomaly detection has a filter** (IP / hostname / MAC / details): one keyword applies to every
  category and the tab counts read "matching / total", so you can see at a glance which kinds of
  anomaly an IP shows up in.
- Page-load diagnostics: each page load records how it started (navigate / reload / back-forward,
  whether the browser had discarded the tab, prerender), logged by the backend only, to track down
  "the page reloads when I switch back from another tab".

### Fixed
- **RDP and VNC are no longer marked Beta** (status bar, connect form, IP edit form, the connection
  type filter, docs).
- **A wrong VNC password is no longer reported as "host unreachable"** (guacd engine): guacd gives
  the same status code for both. After such a failure jt-ipam now checks TCP itself and says
  whether it was the username or password or a host it cannot reach. The check runs only after a
  failure: TigerVNC and others count a bare connect/close as an authentication failure and block
  the source after a few.
- When the built-in VNC engine is unavailable (no aardwolf wheel for this Python), the message
  points to the guacd engine.
- **"IPs without an agent" follows the integration's subnet scope** (Wazuh and OCS): machines
  outside a limited scope are not that Wazuh / OCS's business, yet they were listed as gaps. Only
  the union of the enabled integrations' scopes is listed now; if any integration has no scope,
  the list stays global. The page says it is limited, and the AI chat tool wazuh_missing_agents
  follows the same rule when no subnet is given.
- **Firewall rules / aliases / related NAT on the IP detail page are tables**: a rule used to be
  one line of text with source, destination and description of different lengths, never lined up.
  Columns are now aligned with headers, pass / block are colour-coded, rows highlight on hover, all
  three tables share the column order (vendor and device first, lined up across tables), section
  titles carry a colour bar, and narrow screens scroll sideways.
- The OPNsense rules page loaded only the first 500 rules; arriving with a firewall in the URL
  (`?fw=`, e.g. from an alias on the NAT page) showed an empty table.

## [0.6.47] - 2026-09-25

### Added
- **Exporting a PFX asks for a protection password**: a PFX holds the private key, and the
  certificates page used to export it with no password at all (the backend supported one, the UI
  never offered a field), so anyone who got the file could open it. Choosing PFX now opens a
  dialog for a password (entered twice; empty is still allowed, with a warning).

### Fixed
- **The noVNC "saved PVE credentials" list showed a UUID**: after "remember" the new credential
  was selected but the list was not reloaded, so it showed as soon as a connection failed and
  the form came back. The BMC console had the same gap; a guard test now covers all six consoles.
- **PVE console login failures say why**: a mistyped realm lists the realms PVE has; a rejected
  login names the PVE host and the account and points out that PVE credentials are needed, not
  the VM's own; a rejected saved credential is named; an unreachable PVE gives the underlying
  cause (refused, timed out, certificate). The browser used to give up waiting before the
  backend on an unreachable PVE, so only "failed to get a ticket" ever showed.
- **The PFX password is no longer sent in the URL**, where it ended up in access logs and
  browser history (found by the 0.6.43 authenticated ZAP scan): export is now POST with the
  password in the body, and a GET that carries a password is refused.
- Failed downloads show the reason the server gave instead of "server error".
- **Shelves are drawn true to life**: the 160 px cap on a shelf level is gone (widths were not
  scaled, so gear on shelves looked squashed), and shelves now use the same scale for width
  and height, so KALLAX cells are square and its frame is as thick on top as on the sides; a
  518 mm angle-steel level is no longer drawn at half height. Racks are drawn as before.
- **OCS: containers with an old agent (2.4.2 or earlier) never matched their IP**: those agents
  mark a container's NIC as virtual, and jt-ipam dropped every virtual NIC. When all of a
  computer's NICs are virtual, the ones with a MAC and an IP are used.
- Icon-only buttons on the certificates table have an accessible name.
- The floor plan card on the racks page has the same title bar as the other cards (the title
  used to sit in the card body).
- The certificate download GET no longer advertises a `password` parameter in the API docs
  (it is still refused if sent; the 0.6.47 authenticated ZAP scan sent it because the docs
  listed it).
- The install and upgrade gate containers can use another Debian or PyPI mirror
  (`APT_MIRROR` / `PIP_MIRROR`).

## [0.6.46] - 2026-09-24

### Added
- **Address ranges (pools) inside a subnet** (GitHub issue #40): define a DHCP pool or a reserved
  range by start and end address, which need not be a CIDR (for example .181–.250); the subnet
  stays a CIDR. The subnet page lists each range with its size, how many addresses are used and
  the next free one (click it to create that IP), and the address map marks each range. Ranges
  used as a DHCP pool count as DHCP ranges everywhere: "in a DHCP range", DHCP pool usage, the
  DHCP range list and the AI tools. Ranges must sit inside the subnet and may not overlap; every
  change is audited, and system transfer carries them.
- **Slotted angle steel shelving**, the boltless kind most common in Taiwan: 40mm L posts with a
  full column of keyholes, a steel beam plus 9mm plywood per level, five common sizes (90×45×180cm
  four levels and others) as one-click presets, in black, white or galvanised.
- **IKEA KALLAX**, drawn cell by cell, with a frame thicker than the dividers and no feet; pick
  the grid and IKEA's real dimensions are filled in.
- **LackRack**: an IKEA LACK side table as a 19-inch rack, 8U per table, stacked as high as you
  like, with room for a device on the tabletop. Legs and tabletop are both 50mm and every edge is
  outlined.
- Racks have a "finish" (colour) field for those three kinds; the screen, the SVG/draw.io exports
  and the embed image share one palette.
- **Rack drawings show the cable space on both sides** (from the outer width, measured from the
  465.1mm hole spacing) and **a top panel and base with real thickness** (50mm and 75mm; they were
  a single line).
- **One toolbar for a whole room row**: separate and merged cards both get front/rear, a size
  slider and export; the slider scales every rack at once and keeps them on the same floor line.
  Side by side there used to be no size control, and separate cards had no front/rear or export.
- The FreeRDP RDP engine supports **FreeRDP 3** (Ubuntu 25.10/26.04 only ship freerdp3-x11); the
  installer picks the package the release offers.

### Fixed
- **IP conflicts were never detected without LibreNMS** (GitHub issue #41): the detector only read
  the ARP table that the LibreNMS sync writes. Scan agents and firewall ARP tables (dynamic entries;
  not DHCP leases, VPN sessions or static ARP) now record observations too, regardless of the MAC
  source priority, tied to their subnet so overlapping networks never conflict with each other
  (migration 0152). A MAC flipping between the same two addresses 3+ times in 24 hours is flagged
  as well, since a scan agent sees only one MAC per sweep. The table shows the evidence, the flip
  count and who saw each MAC, and the vendor and locally-administered tags that had stopped
  showing are back. The AI tool states the detection window and says a conflict "cannot be
  determined" rather than "none" when there is no evidence.
- **The console said nothing when the remote host ended the session** (GitHub issue #42): RDP and
  VNC now say so. If an RDP session ends before any screen arrives, the console lists the usual
  server-side causes (no remote logon right, RD licensing or a Connection Broker refusing it, the
  session limit) and suggests the FreeRDP engine, which shows the server's own reason.
- **Reasoning models on OpenAI-compatible servers spent the whole output limit thinking** (GitHub
  issue #36): self-hosted endpoints now get `reasoning_effort: "none"` (llama.cpp b10434+) plus
  `chat_template_kwargs.enable_thinking=false` and `thinking_budget_tokens: 0` (older builds), and a
  reply cut off while thinking says so instead of "(empty response)".
- **When aardwolf can't be installed, say why and what to do** (GitHub issue #39): the installer,
  the RDP/VNC errors and system settings name the host's Python version (aardwolf only ships wheels
  for 3.9–3.13) and point to the FreeRDP engine.
- **The FreeRDP engine could leave an xfreerdp and an Xvfb process behind on every disconnect**: the
  screen-capture ffmpeg blocked on a full pipe and shutdown waited for it forever. Pipes are now
  closed first and every wait has a time limit.
- **The relation chart on the IP page ran the other way round**; it now matches the device page
  and the dashboard (physical on the left, logical on the right).
- **LackRack legs were much thinner than the tabletop and had no outline**: only the 34mm outside
  the rack ears was drawn, and the space under the bottom table looked like the legs were poking
  out.
- An 800mm rack drew devices 1.66 times too wide; the bottom U's number sat in the base; LackRack
  legs overlapped the legend; the single-rack view needed a reload after an edit; device names on
  KALLAX/LackRack weren't centred; industrial rack posts were never drawn; switching to IVAR didn't
  update the level count.
- "Adjust levels" moved into the rack settings dialog and reloads the form afterwards, so saving
  no longer writes the old level count back.
- zh-TW wording: "pool" is always 「集區」.

## [0.6.45] - 2026-09-24

### Added
- **The OCS page has the same tabs as the Wazuh page**: the servers, the agents, and the
  named IPs that OCS has never inventoried. The agents list has one row per computer with its
  IPs listed together; a real sync matched 65 IPs for 16 computers, so counting IPs would have
  overstated the agent count several times over.
- **"IPs without an agent" can be filtered by section, subnet or unit**, on both the Wazuh and
  the OCS page. Picking a section narrows the subnet list, the alert shows "filtered / total",
  and export follows the filter. A row's unit follows the permission hierarchy: the IP's own,
  else its subnet's, else its section's.

### Fixed
- **MikroTik address lists never showed up on the IP detail page**, and rules that refer to them
  (`list:<name>`) could not be traced back to the IP: the single address was compared character
  by character against a member list.
- The "member of aliases" line on the IP detail page is laid out like the firewall rules above
  it: its own grey title, one row per alias with the vendor first, the firewall name and the
  alias description. Aliases on two routers with the same list name are no longer collapsed
  into one.
- The OCS agent version reads "Unix 2.10.0" instead of a long user-agent string whose version
  number was cut off; the full string is in the tooltip.
- The docs site's scan agent, certificate and browser console feature cards are trimmed to the
  length of the others.

## [0.6.44] - 2026-09-23

### Fixed
- **AI audit run by hand failed on OpenAI-compatible services** (GitHub issue #36): "6/6
  batches failed: (empty reply)", while scheduled runs worked. A manual run streams, and the
  stream parser handed every line to the JSON decoder. Server-sent events arrive as
  `data: {...}`, so every line failed to parse and was silently skipped: the model had
  answered; we threw it away. Thanks to the reporter for the precise analysis.
- **The AI chat never used its tools on OpenAI-compatible services.** Same parser: no
  content and no tool calls came through, so the chat fell back to "answer without tools";
  it replied, but without looking anything up. Tool calls split across stream chunks are now
  joined, tool results carry the `tool_call_id` strict services require, and the
  Ollama-only `options` field is no longer sent to OpenAI endpoints.
- **The chat bubble always said "Local Ollama"** (GitHub issue #37). It now shows the
  configured service, and the model-info lookup no longer calls Ollama's `/api/show` on an
  OpenAI-compatible service.

### Changed
- **The rack type comes first in the rack dialog** (GitHub issue #35): level count, numbering
  direction, board thickness and per-level heights all depend on it.
- **The IP lists show the MAC vendor** (GitHub issue #38) under the MAC address, as the IP
  detail page already did.

## [0.6.43] - 2026-09-23

### Security
- **Deleting a user broke the audit hash chain.** `audit_logs.actor_user_id` had a foreign
  key to `users` with `ON DELETE SET NULL`, so deleting an account made the database rewrite
  that person's audit records (actor set to NULL); the stored hashes no longer recomputed and
  verification reported a break: a routine admin action looked exactly like tampering. The
  foreign key is gone, and a database trigger now rejects any UPDATE or DELETE on
  `audit_logs`, so no application path, cascade or mistake can change an audit record. The
  system import never imports audit logs either (the export file keeps the source copy):
  merge used to overwrite rows with the same id and replace used to wipe them.
  The 1,955 long-unverifiable records behind the verification baseline turned out to be the
  same thing: every one has a NULL actor, left behind by test accounts deleted without an
  audit trail; so they are not tampering, but the original ids cannot be recovered.

### Added
- **The docs site has a section for shelving.** Chrome wire shelving and IKEA IVAR-style
  wooden shelving are how a lot of small offices, labs and branch sites actually hold their
  gear, so they get their own section: what the shelf types do, screenshots of two real
  shelves and the shelf settings dialog, in all three languages. The feature card, the rack
  screen and the feature map mention shelving too.
- The demo dataset gains a lab with the same two shelves (fictional names), and the docs
  screenshot script can crop to an element and set a viewport per shot, and refuses a row
  wider than the screen instead of silently cutting its right side off.

### Fixed
- **Switching back to the IP page after opening a console in a new tab left the page black**
  (reported on macOS Chrome). Console tabs kept `window.opener`, so Chrome ran both tabs in
  one renderer process. Every console tab (SSH, SFTP, RDP, VNC, noVNC, BMC) now opens with
  `noopener`, and a check fails on any new-tab `window.open` without it. Pop-out windows keep
  the opener so a named window can still be reused.
- **Racks not yet placed on the floor plan were easy to miss.** The tray of unplaced racks
  sat under the plan as grey dashed buttons, out of view once the plan was tall. It now sits
  above the plan in amber with a count, with a darker label in the light theme so the text
  stays readable.
- A shelf's card title said "Rack:" and used ASCII punctuation in every language. Shelves
  now say "Shelf", and punctuation follows the language.

## [0.6.42] - 2026-09-22

### Added
- **Insert or delete a level, and everything above it moves with it.** Getting a shelf's
  level count wrong used to mean moving every device by hand, one at a time. The operation
  now previews exactly what it will do, refuses to run if anything sits on (or spans) the
  level being removed, and hands back an undo you can send straight back.
- **The level picker shows what is already on each level and where.** A small map per row
  draws the occupied part in its real place, with a dashed outline for where the device you
  are placing would land. A list of names cannot show that half a level is still free, which
  is the whole difference between a shelf and a rack.
- **An optional separate base URL for the embedding model** (GitHub issue #33). Chat and
  embedding models are often deployed separately; leave it empty and it falls back to the
  main URL.

### Fixed
- **Exports were still drawing the old rack model.** The picture came out tens of thousands
  of pixels tall and almost entirely blank, because the bottom-alignment baseline had moved
  from "number of U" to pixels and the exporter was still multiplying it by a fixed row
  height. The exporter also carried its own copy of the geometry, so nothing from the shelving
  work had reached it: no boards, every level the same height, side-by-side devices drawn on
  top of each other (it still read a field replaced months ago), stacking ignored, and a
  device standing on the top board missing entirely. There is now one geometry, shared by the
  single-rack and whole-room exports, and it reads the same pixel values the screen does.
  Side frames stop at the topmost board, as they do on screen and in real life.
- **The rack thumbnail on a device page had the wrong proportions.** Compact mode squeezed
  only the row heights and left the width, board thickness and hole pitch at full size. It is
  now scaled as a whole: measured aspect ratio went from 1.45 to 3.94, the same as the full
  view.
- **The AI could answer with addresses that were never in the data** (GitHub issue #34). When
  a tool had nothing to return, a small model filled the gap with plausible-looking examples;
  when it did return data, a model could mistype an address while repeating it. Three
  deterministic guards: `list_anomalies` now says "nothing found" explicitly instead of a wall
  of zeros, tool errors carry the underlying message (a detector that throws used to look
  identical to "no data"), and every address in an answer is checked against that turn's tool
  results, and anything that appears in neither is flagged in a line appended to the answer.
- **AI and MCP now see the shelving model.** `list_racks` reports the rack kind, whether rows
  are levels or U, per-level heights, the extra place on top, and which rows still have space;
  it used to call a half-occupied level full. `list_devices` and `get_device` expose where a
  device sits within its row, so two devices sharing a level are no longer the same position.
- The per-level height editor listed levels bottom-first while the diagram drew them
  top-first, so changing "the top one" meant looking at the bottom of the list. The numbering
  direction now sits above the level settings, and its two options say where level 1 is
  instead of describing a reading direction.
- Shelving no longer talks about "U positions": the empty-slot tooltip, the position picker
  and the place-device dialog all say levels, and the picker no longer omits the place on top
  of the highest board; it was visible in the diagram but impossible to select.
- Racks standing side by side now share one floor line. The baseline was computed from the
  data rather than measured, and the difference is exactly the frame's border and padding,
  which varies by kind.
- The cross brace on a wooden shelf was hidden behind the right side frame but drawn over the
  left one.
- The embed URL could be copied before the setting was saved, so it returned 404 and looked
  broken.

## [0.6.41] - 2026-09-21

### Added
- **Shelving now behaves like real shelving, not a rack with the labels changed.**
  - **Anything can sit on top of the topmost board.** A shelf unit has no lid, so the top
    surface is a real place to put a device; it shows as a "Top" row above the numbered levels.
  - **Devices can be stacked inside one level, and a level need not be full.** A device now
    records how much of a level's height it uses and where it sits in it (the same
    start-plus-span model already used across the width), so two boxes can share a level and
    still leave headroom above them.
  - **Board thickness and floor clearance are settable.** Level height is the clear space
    between boards, so the boards themselves and the gap under the bottom board are counted
    separately when the elevation is drawn.
  - **An IKEA IVAR preset** fills in the 179 cm side frame, 18 mm boards and level heights in
    one click, and the pine side frames are drawn with their real 32 mm hole pitch and 7 mm
    holes.

### Fixed
- **Side frames no longer continue above the top board.** The top of a shelf unit is open:
  that is the whole point of being able to put something on it.
- A device placed on the top surface was reported as "out of the rack's range" and **was not
  drawn at all**.
- Two devices stacked in one level were reported as overlapping. Overlap now requires the
  vertical ranges to intersect as well.
- **With bottom-up numbering, the open top row was drawn at the very bottom**; that is the
  floor, not the top.
- The "different height per level" toggle rendered as a line of plain text: the component it
  used was never imported, which Vue does not treat as an error. A check now scans every
  template for this.

## [0.6.40] - 2026-09-21

### Added
- **Shelf levels can each have their own height.** Shelf boards are adjustable one level at a
  time on real shelving (IKEA IVAR, chrome wire shelving), and the usual build puts the tall
  bay at the bottom. A rack can now carry a height per level; leave it off and the whole unit
  keeps one height, exactly as before. The drawing, the device blocks that span several
  levels, and the cross-brace span are all computed from the actual heights.

### Fixed
- **Clicking a rack in the dashboard's usage chart now opens that rack**, instead of just
  landing on the rack page showing whichever rack was the default.
- **The face switch and the export button in the rack toolbar are aligned.** They sat about
  2px apart because the two controls had different line heights and were positioned on the
  text baseline.
- The rack elevation's **top board** is drawn on the web diagram too, not only in the SVG
  export; without it a shelf unit looked like it was missing its top level.

## [0.6.39] - 2026-09-20

### Added
- **Wooden shelving is now a rack type** (pine side frames, e.g. IKEA IVAR). The drawing shows
  what makes one recognisable: solid pine side frames with their full column of adjustment
  holes, pine shelf boards, and the steel cross-brace at the back, braced every other bay from
  the bottom, the way the instructions call for, and drawn behind the shelves so you see it
  through the empty ones. Width and depth shortcuts offer the real IVAR board sizes
  (42/83 cm wide, 30/50 cm deep).

## [0.6.38] - 2026-09-20

### Added
- **Racks now have a type** (Racks → Add/Edit → Type): standard server rack, industrial
  enclosure, plain shelving, or chrome wire shelving. The type sets sensible defaults for width
  and level height, decides whether rows are counted in U or in levels, and changes how the
  diagram is drawn: the wire-shelf drawing has round chrome posts with collars at each shelf,
  which is how you recognise one at a glance.

## [0.6.37] - 2026-09-20

### Added
- **Non-standard racks and shelving are supported** ([#30](https://github.com/jasoncheng7115/jt-ipam/issues/30)).
  A rack can carry its real width and its real level height in millimetres; the elevation is
  drawn to those proportions instead of assuming 19" and 1U. Empty values keep the standard
  numbers, so existing racks are unchanged.

## [0.6.36] - 2026-09-20

### Added
- **A row can hold up to six devices side by side**
  ([#31](https://github.com/jasoncheng7115/jt-ipam/issues/31)). Width was previously full /
  left / right only; a device now records a starting cell and how many cells it spans, so
  halves, thirds, quarters, fifths and sixths are all expressible. Existing left/right
  placements are converted automatically and keep their position.

## [0.6.35] - 2026-09-19

### Added
- **OCS inventory data is now available to AI chat and MCP.** A new `list_ocs_computers` tool
  returns OS, asset tag, agent version, last-inventory time and the latest notes, and
  `get_ip_detail` now carries the OCS fields too. Pass `subnet_cidr` to scope a question to one
  subnet (the reply states the scope and total), or `stale_days=N` to find assets that have not
  been inventoried for N days. Read-only, and subject to the same visibility rules as the other
  infrastructure tools. Previously every other integration was queryable this way except OCS.

### Changed
- **The OCS inventory timestamp on an IP is now a link** that opens the device page and scrolls
  straight to its OCS card, briefly highlighting it.

## [0.6.34] - 2026-09-19

### Added
- **Every external-integration card on the device page has a "View in ..." button** that opens
  that device's own page in the source system (LibreNMS, Wazuh, Proxmox VE, OCS). The links are
  built on the server from each integration's configured URL and the device's id there.
- **The OCS card now shows the asset tag, the agent version and the most recent notes**, plus a
  line explaining that OCS matches machines to existing IPs by network-card MAC.

### Fixed
- **Placeholder DMI strings no longer masquerade as hardware data.** Boards that ship without
  DMI values report things like "To Be Filled By O.E.M.", sometimes glued to the real value
  ("To Be Filled By O.E.M. X570D4I-2T"). The prefix is now stripped, and a placeholder already
  stored on a device is replaced with the real value, or cleared when the source has none.
  Values entered by hand are never touched.

## [0.6.33] - 2026-09-19

### Added
- **The device-port import filter is now configurable** (System settings → Device ports): a
  master switch plus an editable list of name patterns, pre-filled with the defaults.

### Fixed
- **Text from OCS is now recovered whatever language it is in.** Agents always report UTF-8, so
  when an OCS database is not UTF-8 the text arrives double-encoded and unreadable. That is a
  single, unambiguous corruption, and it is now reversed for Traditional and Simplified Chinese,
  Japanese and Korean alike. The JSON decoder also no longer trusts the declared charset, so a
  response that is not valid UTF-8 is preserved instead of losing characters.

## [0.6.32] - 2026-09-19

### Added
- **The device page shows an OCS Inventory card** (OS, last inventory, serial/model/vendor).

### Fixed
- **Windows pseudo network interfaces no longer clutter the device port list.** Importing ports
  from the monitoring integration pulled in the NDIS filter, WAN Miniport and tunnel interfaces
  that Windows exposes over SNMP (`ethernet_3`, `wireless_0`, `ppp_32769` ...), most of them
  copying the real adapter's MAC. They are now skipped, and ones imported by an earlier poll are
  removed, but only if they are not cabled and carry no pass-through mapping, so manually
  created ports are never touched. Physical switch and Linux port names do not match these
  patterns and are unaffected.

## [0.6.31] - 2026-09-18

### Changed
- **The OCS integration page now matches the other integration pages**: the sidebar entry reads
  "OCS integration" (like the DNS/Wazuh/... entries), and the list action column uses icon
  buttons with tooltips, consistent with the Proxmox VE and firewall pages.

## [0.6.30] - 2026-09-18

### Fixed
- **OS reported by the OCS agent now outranks the scanner's nmap fingerprint guess.** The first
  release wrote OCS's OS into the scanner's `os_guess` field and only when empty, so a Win11
  machine that nmap had mis-fingerprinted as "Windows XP" kept the wrong value. OCS's OS now
  lives in its own `os_ocs` field and is placed above the scanner in the OS-source precedence
  (an agent's read of the real OS is far more reliable than a fingerprint guess), no longer
  polluting the scanner field.

## [0.6.29] - 2026-09-18

### Added
- **OCS Inventory NG integration (phase 1: asset identity).** Pulls endpoint and server
  inventory from OCS (hostname, OS, NIC MACs, serial/model/vendor, last-inventory time),
  filling the one gap this project had no automatic source for (PC and physical-server assets).
  **Read-only; never changes OCS. Matches existing IPs by MAC only, never creates IPs, and
  treats a MAC seen on several machines as ambiguous rather than guessing.** Admin → OCS
  inventory manages several servers (per customer/site), tests the connection, and syncs
  on demand or on a schedule.
  - Credentials are **optional** (OCS REST has no auth by default); the connection test
    actively checks whether it answers with no credentials and warns when it does.
  - Incremental vs full is chosen by version capability: 2.11 uses `lastupdate`, 2.10 pages
    the full set.
  - The software-list toggle is **off by default** (it inflates each host from ~2 KB to ~80 KB).
  - OCS inventory time is display-only and never feeds the online/offline decision; stale
    inventory never overwrites a fresher source or a device serial.

### Fixed
- **Rack diagram: a device spanning the full width had its label ellipsized at the half-U
  point** (GitHub #32). The label now uses the device's actual width instead of a fixed
  half-U cap.

### Notes
- Database changes: new `ocs_servers` table and `ip_addresses.last_seen_ocs` column (applied
  automatically on upgrade). **No other install/upgrade changes**: no new packages, services
  or outbound settings (OCS calls use the existing safe-outbound mechanism).

## [0.6.28] - 2026-09-18

### Fixed
- **Two people connecting at the same moment could see each other's console.** The FreeRDP engine
  gives every connection its own virtual screen, but decided "is this screen mine?" by checking
  whether a socket file existed. When three connections start at the same instant, all three see
  no lock file, all three try the same number, and only one wins; the other two saw the winner's
  socket and carried on using **someone else's screen**. Ownership is now proved against the PID
  in X's own lock file, and the scan starts at a different number per connection.
- **A correct password could be reported as "username or password incorrect".** When two
  handshakes begin at the same instant the target fails one of them, sometimes as a security
  negotiation failure and sometimes as a logon failure. Handshakes are now queued (across
  processes, so multiple workers are covered too). Retrying was deliberately **not** used:
  a retry re-sends the credentials, which on a target with a lockout policy spends one of the
  account's attempts.
- **Three entirely different connection failures gave the same wrong explanation.** Nothing
  listening on the port, a port that is not RDP, and an unreachable address were all reported as
  "the target rejected these display settings (resolution or colour depth)", sending the reader
  to check a resolution that was never the problem. The test used a line that *every* failure
  prints. The four failures now each say their own thing, and the underlying text is no longer
  cut from the middle of a timestamp.
- **Non-ASCII characters vanished silently on the FreeRDP engine.** FreeRDP translates key events
  through a fixed key table and has no Unicode keyboard channel, so Chinese and Japanese could
  not be sent, and nothing said so, which reads as a broken keyboard. The console now says so and
  points at Paste, which handles any text. This also fixes a worse case: characters on the AltGr
  level were typed as Shift, producing a character the user never asked for.

### Changed
- Two more entries in the troubleshooting page's Remote console section: what to do when the
  FreeRDP engine cannot type your language, and why GNOME Remote Login degrades to a black screen
  after dozens of connections (the target accumulates login-screen sessions it never reaps),
  with the commands to check and clear it.

- **The "last seen" labels on the IP detail page now read alike.** The Wazuh row said
  "Wazuh agent keep-alive" next to "Last seen (scanner)" and "Last seen (LibreNMS)", which
  made one row of the same group look like a different kind of value. All three languages
  adjusted, with a test to keep them in step.

### Notes
- No database changes. **Install and upgrade need no adjustment**: no new packages, units or settings.

## [0.6.27] - 2026-09-17

### Fixed
- **Two cursors on the RDP console.** The FreeRDP engine baked a cursor into every captured
  frame while the browser drew its own on top of the canvas: two cursors at the same point,
  which reads as one cursor being offset. The baked one was not even the remote's pointer: it
  was whatever cursor the *local* X server had, which on a target that never sends a pointer
  update is X11's default shape and has nothing to do with the remote. It is no longer drawn:
  the two engines now behave the same, and moving the pointer over empty space no longer
  produces any frame updates at all.
- **The rightmost column was always black, and aiming drifted towards the right edge.** An RDP
  desktop must have an even width, so an odd request is rounded down, but the canvas was still
  sized to what was asked for, leaving a column that never gets painted and a scale factor off
  by that column. The engine now reports the size it actually got, and the canvas follows it.
- **The FreeRDP engine was killed outright by the syscall filter in production.** Xvfb needs
  four calls the backend itself never makes, and the filter's default action is to *kill* rather
  than return an error, so it vanished without a word and the console said only that no virtual
  display could be started. The install and upgrade scripts now add the matching systemd
  drop-in whenever the FreeRDP engine is selected; sites on the default engine keep the tighter
  filter.

### Changed
- **A "Remote console" section in the troubleshooting page** (all three languages): which engine
  to choose for xrdp or GNOME Remote Login, and what GNOME's "Session Already Running" dialog
  means. Force Stop doing nothing is not a lost click; it is GDM being unable to end a session
  the user already holds on the local console. To reach a desktop that is already logged in,
  use Desktop Sharing rather than Remote Login.
- **Four documentation screenshots re-shot from the demo dataset.** The originals came from a
  real network and showed internal ranges, real hostnames and MACs, and a customer's name.
  Secret scanning cannot read image content and sanitisation cannot change pixels.
  ⚠️ The old images remain in the published git history.

### Notes
- No database changes. **Install and upgrade**: sites using the FreeRDP engine gain one systemd
  drop-in (`/etc/systemd/system/jt-ipam-backend.service.d/freerdp.conf`), installed by the
  scripts; sites on the default engine are unaffected.

## [0.6.26] - 2026-09-17

### Fixed
- **The FreeRDP engine could not start its virtual display on a real install.** The systemd unit
  runs with `PrivateTmp=yes`, so the service gets a fresh, empty `/tmp`, and Xvfb, running as a
  non-root user, will not create `/tmp/.X11-unix` itself. It says so and then fails. The engine
  now creates that directory before starting Xvfb. This could not reproduce in development,
  where the directory already exists and nothing is sandboxed.
- **That failure was reported as a credentials problem.** The message read "connection or
  authentication failed (username, password, domain or NLA)", because FreeRDP's error was being
  run through a classifier written for the other engine's failure modes. It sent the reader to
  check a password that was never wrong. FreeRDP errors now keep their own explanation, and
  Xvfb's own words are included instead of being discarded.
- **FreeRDP needs a writable home directory** and, when it does not have one, reports a security
  negotiation failure, again pointing somewhere unrelated. Each connection now gets its own
  temporary home, so the engine no longer depends on how the service's HOME is configured.
  Verified against a sandbox with no writable paths at all.

### Changed
- **The version page lists what the FreeRDP engine needs** (`xfreerdp`, `Xvfb`, `ffmpeg`,
  `xclip`) alongside the existing optional tools, each with its package name and what it is for.
  That page is where an administrator checks what a host has; an engine that cannot connect for
  want of a package should be visible there rather than in the logs. A test keeps that list and
  the engine's own requirements from drifting apart.

### Notes
- No database changes; installation and upgrade are unaffected.

## [0.6.25] - 2026-09-17

### Added
- **The RDP console can use FreeRDP instead of the built-in client**, selected under
  Admin -> System settings. The default stays aardwolf.

  A customer could not reach an Ubuntu 24 host running GNOME Remote Login. The decisive test:
  same host, same credentials, same moment -- FreeRDP authenticates, our client is rejected with
  STATUS_LOGON_FAILURE. The cause is in the NTLM library we depend on, which does not send the
  message integrity code; MS-NLMP requires one when the server's challenge carries a timestamp,
  and FreeRDP's server side enforces it. GNOME Remote Login and xrdp both use that server side.
  Windows targets were never affected, which is why the default does not move.

### Notes
- FreeRDP needs `freerdp2-x11 xvfb xclip ffmpeg`, about 150 MB of X libraries. They are **not**
  installed by default: `install --with-freerdp` adds them, an upgrade adds them automatically if
  the site has already selected that engine, `doctor` reports whether they are present, and the
  settings page names what is missing and the command to install it.
- ffmpeg is there for screen capture, not video. Reading the framebuffer through the X protocol
  in Python costs 334 ms per 1280x800 frame, capping the console at 2.8 fps; ffmpeg's x11grab
  uses shared memory and measured 47 fps. The console now streams at its 15 fps ceiling, sends
  nothing at all while the screen is idle, and uses about 17 KB/s when the cursor is moving.
  It also draws the remote cursor, which the previous capture path could not see.
- Clipboard redirection is **off** unless the administrator enabled paste. FreeRDP enables it by
  default, so switching engines would otherwise quietly reopen a channel that had been closed.
- The password reaches FreeRDP on stdin, never as a command-line argument, where any local user
  could read it out of the process list. A test pins this.
- Input errors are no longer swallowed. The console used to suppress every exception in its input
  loop, so a failure left the picture running while the mouse and keyboard did nothing, with no
  server-side trace. The same session now logs each step it waits on.

## [0.6.24] - 2026-09-16

### Changed
- **A hostname that contains what you typed now ranks above a match found only in the
  description or owner.** The hostname is the object's identity: someone typing a name wants
  the machine with that name. Trigram similarity alone reversed that, because a short owner
  string ("kappa5 team") scores higher than a longer hostname ("kappa5-web"), so "the machine
  that team owns" came out above the machine itself. The hostname hit is given a floor rather
  than a ceiling, so a better match (an exact IP, a MAC) still wins.

### Notes
- No database changes; installation and upgrade are unaffected.

## [0.6.23] - 2026-09-16

### Added
- **Global search covers the description, owner and note fields.** Searching for a machine by
  what someone wrote about it (a ticket number, a rack position, the team that owns it) found
  nothing, so those fields could only be read, never looked up.

### Fixed
- **A description-only match was silently discarded.** `description` was already in the query,
  so the field looked searchable; but the matching text never reached the result's label or
  sublabel, and the "substring hits win" filter downstream drops every row whose visible text
  does not contain the query. As soon as anything else matched, the description hit vanished.
  A row found by description, owner or note now shows the matching text, which both explains
  why it is in the list and lets it survive that filter.

### Notes
- Notes rank last, deliberately: it is free text, and "spare for 203.0.113.9" written on one
  machine does not mean the user is looking for that machine. Note matches are substring-only
  (no fuzzy matching, which would push noise to the top) and scored in a band below everything
  else. Hostname, description and owner keep competing on similarity as before.
- The row ordering also puts stronger matches first inside the SQL, so note matches can never
  push a hostname match out of the result limit.
- The per-address list search already covered all of these; only the global box was missing them.
- No database changes; installation and upgrade are unaffected.

## [0.6.22] - 2026-09-16

### Fixed
- **A manual anomaly scan no longer reports old findings as new.** The scheduled run and the
  "Run scan" button shared one sentence, `IP conflicts: {count} new`, so pressing the button
  announced findings that had been sitting there for months as if they had just appeared. The
  recipient decides whether to act tonight on that word. The two are now separate sentences,
  and the total, which was never passed, no longer leaves "(of N)" blank.
- **One anomaly notification printed its own translation key.** `notif.anom_mac_flapping` was
  referenced by the code but existed in none of the three language files, so "IPs changing MAC
  frequently" arrived in the bell as the literal text `notif.anom_mac_flapping`. Nothing errors
  when a key is missing; it is simply ugly in front of the user.

### Changed
- **The anomaly tables are translated.** Forty-six column headings lived in a dictionary in the
  page and never went through i18n. The tables are only drawn after a scan has run, so switching
  the interface to Japanese left the headings in Chinese and no existing check ever looked.
- **The firewall rule-rot findings are translated**, including the one that names the alias
  members it found. The Chinese original is still sent alongside: exports and the AI reading
  have no browser to ask for a language.
- **Server-produced display text now translates nested keys.** A notification can name another
  key as a parameter (`label_key`), letting eleven categories share two sentences instead of
  carrying eleven near-identical ones.
- **Seventy-seven hardcoded strings across thirteen screens** now go through i18n: certificate
  deployment previews, export formats, the login realm, event-rule conditions, and the AI tool
  names. Translating the tool names is not word substitution (Chinese joins with nothing,
  English needs spaces, Japanese puts the verb last), so the assembly itself is now translated.

### Notes
- No database changes; installation and upgrade are unaffected.
- The scan-agent install hint existed in two copies that had already drifted apart: only one
  of them mentioned that installing avahi-utils also starts a daemon listening on UDP 5353.
  They are now one shared module.
- The legacy `notif.anom_*` keys are kept and pinned by a test. Notifications already sent live
  in the database and still point at them; deleting them would turn old entries into raw keys.
- Verified in a real browser in all three languages, with a new end-to-end test for the table.

## [0.6.21] - 2026-09-16

### Changed
- **The system-check page speaks the reader's language.** It was the largest screen still
  written entirely in Chinese: 33 checks, each with a title, a finding and a "how to fix",
  all assembled on the server where there is no such thing as a current language. Every check
  now carries a code and its parameters, and the sentence is built in the browser, following the same
  contract the error messages and notifications already use. The stored Chinese remains as the
  fallback, so a check without a code degrades to what it printed before.

- **The downloadable report is built in the browser too.** It used to come from
  `/doctor/report`, which assembles the text server-side and therefore always in Chinese; a
  report you paste into a ticket should be in the language of whoever reads the ticket. The
  server endpoint stays for `curl` and scripts.

### Notes
- No database changes.
- `scripts/jt-ipam.sh doctor` is unaffected: it is the other half of this diagnosis, it runs on
  the server for an operator, and it has always been in English.
- Verified in a real browser in all three languages. Two things only the browser showed: the
  backend process still running the previous build (the page said so itself, in its own
  frontend/backend version check), and a leak detector of mine that flagged correct Japanese
  because it listed kanji Japanese shares with Chinese.

## [0.6.20] - 2026-09-16

### Fixed
- **The last two notifications that were Chinese for everyone.** The audit-chain failure and
  the fallback text of a user-defined event rule were the only `push_notification()` calls
  still without translation keys; all ten call sites now carry them. The event rule keeps the
  admin's own wording when they wrote one; only *our* fallback sentence follows the reader's
  language.

- **The audit-chain alert de-duplicated on its own display text.** It suppressed repeats by
  matching `Notification.title == "稽核鏈驗證失敗"`, so rewording that sentence (a translation,
  a copy edit) would have silently stopped the suppression and started alerting on every run.
  It matches the stable key now, and accepts the old title too so upgrading does not produce a
  duplicate.

- **The dashboard's object-hierarchy row broke when it wrapped.** The arrow and the box after
  it were siblings, so on wrap the arrow stayed at the end of the previous row pointing at
  nothing, and `flex: 1 1 0` stretched the leftover box across the whole row (974px at 1280px
  wide). Each arrow now belongs to its box and wraps with it, and a box cannot grow past 260px.
  Measured in three languages at five widths.

### Fixed (tests)
- **`seed_e2e` now resets each account's stored language.** Nearly every spec finds elements by
  their Chinese text, and the language is a preference stored on the account, so any tool that
  looked at the Japanese or English UI without restoring it (a spec, a one-off measuring script)
  left the next full run with a batch of "element not found". That reads as a product
  regression; it is last run's leftover state. Same reasoning as the ignore-list reset that was
  already there.

### Notes
- No database changes.

## [0.6.19] - 2026-09-14

### Fixed
- **Japanese labels painted outside their boxes in the object-hierarchy diagram** on the site's
  front page. That diagram is hand-written inline SVG with fixed 146px boxes, and an SVG
  `<text>` neither wraps nor clips: a label that does not fit simply paints over the border.
  The Japanese for "Room / Site" needed about 166px; "Virtualization" was grazing the edge too.
  The Chinese label fits, which is why this survived until someone looked at the Japanese page.

  The label now uses the same short form the other two languages already use in that diagram,
  matching the app's own Japanese for rooms, so every label fits at full size. A measuring pass
  also runs on load and on every language change and shrinks anything that still does not fit:
  hand-tuning one font size would break again the next time a string is edited or a language
  is added.

- **Notifications were written in Chinese for everyone.** The notification model has carried
  `title_key` / `body_key` / `params` for a while: the browser renders those in the reader's
  language and falls back to the stored text when they are absent. Certificate and IP-request
  notifications used it; the twelve in the capacity and health checks (DHCP pool, jump-host key,
  certificate source, integration sync, agent silence, system check) never did. The root cause
  was one level down: the shared `_notify()` helper did not forward the keys at all, so a
  producer could not have supplied them even if it tried. Reported from a Japanese session
  showing a Chinese notification panel.

  Existing rows keep the Chinese they were written with: a notification is a record of
  something that happened, not a template to rewrite after the fact.

- **"3 分鐘前" in every language.** `fmtRelative` built the string by hand, so the relative
  timestamps in the notification panel, API tokens, chat history and backup list were Chinese
  regardless of the interface language. It uses `Intl.RelativeTimeFormat` now: plurals
  (1 minute / 2 minutes) and word order are each language's own rules, and hand-built strings
  can only ever be right for one of them.

- **A DHCP pool notification ran the subnet and the range together**
  (`192.0.2.0/24192.0.2.150–192.0.2.200`): the two values were concatenated with no separator,
  leaving the reader to find the boundary.

### Notes
- No database changes.

## [0.6.18] - 2026-09-14

### Fixed
- **The documentation site's page title stayed Chinese in every language.** 0.6.16 made the
  body text and the screenshots follow the reader's language and left the document's own
  metadata behind: no script touched `<title>`, `<meta name="description">` or `<html lang>`,
  so the browser tab, the search result and the link preview were Chinese whichever language
  you picked, and the served HTML said `lang="zh-Hant"` even though the site's default is
  English. Reported from `view-source:` on `?lang=ja`.

  All six multilingual pages now carry the title and description in three languages and update
  them together with everything else. The static values are the **default** language (English),
  so a crawler or a reader without JavaScript gets a consistent page rather than a Chinese
  title above English content. `troubleshooting.html` had its own switcher and did not emit the
  shared `jtipam:langchange` event; it does now, so one contract covers the whole site.

### Notes
- No database changes.

## [0.6.17] - 2026-09-14

### Fixed
- **`upgrade` said "complete" while the site was still returning 502.** After restarting the
  backend the script slept four seconds and asked systemd whether the unit was active, and
  systemd calls a unit active the moment the process is exec'd, which is well before uvicorn
  has imported the application and started listening. Four seconds is enough on an idle box
  and not enough right after a frontend build, which is exactly when `upgrade` runs it. The
  admin saw "Upgrade complete" and a broken site.

  It now polls a route that **has to be proxied to the backend** until it answers (up to 90s)
  and fails loudly with the journal command if it never does. `/healthz` is deliberately not
  used: nginx answers that one itself with a static 200, so it stays green with the backend
  stopped.

- **`install`'s own self-check silently skipped the listening-port test on minimal systems.**
  It used `ss`, guarded by `command -v ss`, and container and minimal-cloud images routinely
  ship without iproute2, so the check vanished precisely on the machines where install
  problems actually happen. It now connects instead of looking for a bound port.

### Added
- **`scripts/test-upgrade.sh`: the upgrade path is now a release gate of its own.** Fresh
  install and upgrade share almost no code: `install` starts from nothing, while `upgrade`
  starts from a machine already running an older version and has to pull, run migrations
  against real data, rebuild and restart without losing the instance. A release could pass
  the fresh-install gate and still break every existing site, and this release did: the 502
  above was found by the first run of this script.

  It installs the previous tag in a throwaway systemd container, **writes a row before
  upgrading**, upgrades, then checks that the version moved in both the frontend and
  `version.py`, that the service answers over HTTPS, that the row survived, and that `doctor`
  is happy. It can be pointed at a local repository so it tests the release you are about to
  publish rather than the one already published.

### Notes
- No database changes.
- TEST_CHECKLIST 5b now lists the upgrade gate.

## [0.6.16] - 2026-09-14

### Fixed
- **Server-generated messages now follow the reader's language.** Error text produced by the
  backend ("跳板「X」尚未信任主機金鑰", "403 拒絕存取：帳號群組權限或 address 限制不足",
  the SFTP "找不到「/etc/nope」" line, the firewall match reason on an IP) was written as
  finished Chinese sentences, so the English and Japanese interfaces showed Chinese. This is
  not a Japanese problem: **English users have been seeing Chinese error messages since the
  English translation shipped**, and nothing errors, so nobody reported it as a bug.

  The backend now sends `{code, params, message}` and the sentence is assembled in the browser
  from `errors.<code>`. 287 codes across ~60 files: every service exception that reaches a
  screen (`RouterOSError`, `CertError`, `ESXiError`, `SftpError`, `SSHTunnelError`,
  `RackPlacementError`, `PveConsoleError`, `TransferCryptoError`, the DNS adapters, FortiGate,
  Palo Alto, Zabbix…), the console WebSocket error frames for SSH / SFTP / RDP / VNC / BMC / PVE,
  and the HTTP `detail` of every endpoint that forwards one. `message` stays as the fallback,
  so an untranslated code degrades to exactly what it printed before.

  Two things this sweep uncovered on the way:

  - **The Proxmox two-factor prompt was already broken.** The browser decided whether to show
    the 6-digit code field by reading `detail.code`, but the response interceptor flattens
    `detail` to a string before any caller sees it, so that branch could never be true and
    the code field never appeared. The code now travels alongside, in `detail_code`.
  - **A translated frame must not swallow the evidence.** `detail_of()` always attaches the
    original text as `reason`, and every fallback sentence interpolates it ("pfSense returned
    an error: {reason}"). Without that, translating would have *removed* the one part that
    says whether it was DNS, a refused connection, or a bad certificate.

  A test walks the source for every `code=` / `ui_detail()` / `detail_of()` and fails if any of
  the three locales is missing that key: the failure mode is silent, so it needs a gate.

### Changed
- **Documentation screenshots are per-language, and taken from a fictional dataset.** The
  English and Japanese pages of the site showed Chinese screenshots, which reads as "this
  product is really only Chinese". There are now `docs/shots/{zh,en,ja}/` and the page swaps
  them when the language changes.

  The shots come from `scripts/demo_dataset.py` + `scripts/docs-shots.mjs`, so they can be
  retaken: the data is invented (4 sites, 6 racks, 5 subnets, 39 addresses, 23 devices, a
  patch-panel cable path), addresses are all in the RFC 5737 / RFC 3849 documentation ranges,
  and nothing in the picture belongs to a real network. The previous screenshots were taken
  against a live system.

### Fixed (tests)
- **A browser test that had quietly rotted.** The "an IP that keeps changing MAC" spec seeds
  six ARP rows and the rule looks at *the last 7 days*, but the seeder only created rows that
  did not already exist, so once seeded, the timestamps never moved and the fixture fell out
  of the window a week later. The failure read "no table row appeared", which looks exactly
  like the feature being broken. The seeder now re-anchors the timestamps every run, and the
  spec asks the backend whether the sample exists *before* waiting, so an environment without
  it skips instead of spending 60 seconds timing out.
- The scan-agent probe spec read the dropdown with a single `allInnerTexts()` call, which races
  the panel mounting; with enough accumulated agents it started losing. It now uses retrying
  expectations.

### Notes
- No database changes.
- TEST_CHECKLIST gained section 5g (messages the server writes on the screen).
- The AI-chat screenshots and the browser-console screenshot are still the old Chinese ones:
  the first four need a reachable language model and the last needs a live SSH/RDP target.
- Not converted, deliberately: the startup secret check in `config.py` (it only ever reaches
  the system journal), and `BackgroundTask.error` (a diagnostic field carrying arbitrary
  exception text, never translated). The system-check page and the anomaly finding descriptions
  are a separate Chinese surface, not error messages; they remain as they were.

## [0.6.15] - 2026-09-14

### Fixed
- **The AI now answers in the language the person is actually using.** Only two of the five
  places that send text to a language model were following the UI language. The investigation
  window decided with `lang.startswith("zh")`, so a Japanese user got English; the AI triage
  card for unauthorised IPs and the firewall rule-change reading had the instruction written
  into a Chinese prompt, so **English users got Chinese too**; that one predates Japanese
  entirely and nobody noticed, because nothing errors: the answer simply comes back in the
  wrong language.

  There is now a single `answer_language()` that every entry point uses, so a new feature
  cannot quietly pick its own rule and a new language only has to be added once. A test
  asserts all five call it, because the failure mode is silent.

### Changed
- **The documentation site switches language with a dropdown instead of three buttons in a
  row.** Side-by-side buttons crowd the header on a narrow window, and every additional
  language makes it worse; a dropdown keeps the same width no matter how many there are.

### Added
- Japanese for the data model and the phpIPAM API mapping references.

### Notes
- No database changes.
- Two facts in `DATA_MODEL` were out of date after 0.6.14 and are corrected: `user_preferences.locale`
  now lists `ja-JP` (with the reminder that adding a language means widening a CHECK constraint
  in a migration, or saving fails with nothing on screen but "save failed"), and the two Wazuh
  CVE count columns are recorded as removed.

## [0.6.14] - 2026-09-14

### Added
- **Japanese, as a first-class third language.** The app UI (all 3,491 strings), the whole
  documentation site, and the main Markdown guides (README, INSTALL, SECURITY, PLUGINS,
  UPGRADE_FROM_0.4) are now available in Japanese alongside Traditional Chinese and English.
  Picking it is a normal preference, saved per account and carried across devices; naive-ui's
  own strings (date picker, pagination, upload) switch with it, so the page is not half-and-half.

  Adding a language is more than a file of strings, and each of the pieces below fails
  *silently* if forgotten: a key missing from one locale makes vue-i18n fall back to English,
  so the screen renders half-translated with no error; and `user_preferences.locale` carries a
  CHECK constraint, so without migration `0139` choosing Japanese would fail on save with
  nothing on screen but "save failed". Both are now held down by tests: `check-i18n` requires
  the three locales to have exactly the same key set, a vitest asserts the Japanese file
  contains no untranslated Chinese and keeps every interpolation placeholder, and an e2e spec
  switches the language in a browser and walks the main pages looking for raw i18n keys.

- **An install / upgrade troubleshooting page** at
  [troubleshooting.html](https://jasoncheng7115.github.io/jt-ipam/troubleshooting.html):
  searchable Q&A with a clickable table of contents, in all three languages. When `install.sh`
  or `jt-ipam.sh` fails it now prints that URL, **in the language of the operator's terminal**
  (`LANG=ja_JP` gets the Japanese page, `zh_*` the Chinese one, anything else English). A
  failed install previously ended at a `FATAL:` line with nowhere to go next.

### Security
- **vitest and @vitest/coverage-v8 upgraded to 4.1.11** (GHSA-82fw-gwwq-j7x9, path traversal,
  moderate). Dependabot found these, not us: our own audit gate was set to `high`, so the CI was
  green the whole time. The gate is now `moderate`: a threshold set above the problems you
  actually have is the same as not scanning.
- Sample addresses in the Tools page now use RFC 5737 documentation ranges instead of RFC 1918
  private ones, so a ZAP scan no longer reports private-IP disclosure on them. A report
  with unavoidable findings in it is a report where the real one gets missed.

### Removed
- **The two Wazuh CVE count columns**, which nothing has ever written (migration `0138`). Since
  Wazuh 4.8 the manager API has no vulnerability endpoint; the only source is the Wazuh Indexer,
  which needs a credential that can read the whole SIEM. So the API and the MCP tool were
  returning a `cve_critical` that is always null, which reads as "checked, nothing found"
  rather than "never checked". That is worse than not answering at all. Old export files still
  import: unknown columns are ignored.

### Fixed
- A rack diagram and a long API path no longer push the documentation pages sideways on a phone
  (a grid column defaults to a minimum width of `auto`, so one long line stretches the page).

### Notes
- Migrations `0138` and `0139`. `0139` drops and recreates a CHECK constraint by looking its name
  up in the catalogue rather than hard-coding it: the name differs between installs, so a literal
  would break one of them.

## [0.6.13] - 2026-09-13

### Fixed
- **An upgrade that can no longer fast-forward now recovers by itself.** The upgrade pulls with
  `--ff-only` and the script runs under `set -e`, so a failed pull stopped everything right
  there: no backup, no migration, no build, no restart -- and all the operator saw was git's own
  message. It was not a one-off either; every later upgrade failed the same way, because nothing
  about the repository had changed. The fix is `git reset --hard origin/<branch>`, which nobody
  would guess.

  The ways to get here are mundane: somebody committed an edit on the box, a shallow or partial
  clone, or rewritten upstream history. In all of them the checked-out source is meant to be a
  copy of upstream -- customer configuration lives in `/etc/jt-ipam`, not in the repository -- so
  resetting to the remote is the correct move. Nothing is discarded silently: commits that exist
  only on that machine are kept on a timestamped branch and the message says where they went.

### Changed
- **The installer's own tests now run in CI.** The guards under `scripts/tests/` were not run by
  any CI -- a guard nobody runs is not a guard. Both `scripts/ci.sh` and GitHub Actions run them
  now, and a new one covers the recovery above by simulating rewritten upstream history with two
  throwaway repositories.

### Notes
- No database changes; this release touches only the installer/upgrade script and tests.

## [0.6.12] - 2026-09-08

### Added
- **Anomaly detection can run on a schedule** (Admin -> Anomaly detection -> Schedule). The
  detection has always been there, but it only ever ran when somebody pressed the button --
  and IP conflicts, rogue DHCP servers and externally exposed services do not wait for office
  hours.

  Four frequencies: **every N minutes** (minimum 5), daily, on chosen weekdays, or on a chosen
  day of the month. Minute-level intervals matter for operational checks like these, which is
  why the interval mode exists here and not for the AI audit -- that one costs an LLM call and
  keeps fixed wall-clock times, because interval scheduling drifts with each run. The 5-minute
  floor is not arbitrary: the scheduler is driven by `jt-ipam-sync.timer`, which wakes about
  every 5 minutes, so anything shorter is not more responsive -- it just looks like the setting
  did not take.

- **A scheduled run only notifies about findings that are new since the last run.** This is what
  makes the schedule usable at all: detection reports the *current state*, not a stream of
  events, so an unresolved IP conflict reappears every single run. Notifying every time would
  mute the whole category within days -- and genuinely new events with it. The comparison uses a
  fingerprint built from stable fields (IP, MAC, CIDR, device name...), not a hash of the whole
  record: include something like "last seen" and every run looks new, which is the same as not
  deduplicating. Pressing the button by hand behaves as before.

### Changed
- The schedule arithmetic (daily / chosen weekdays / day of month, including clamping the 31st
  to the last day of a shorter month) moved out of `services/ai_audit.py` into a shared
  `services/schedule.py`. Two copies of this logic would mean only one of them gets fixed, and
  the difference is invisible on screen.

### Added (continued)
- **Anomaly notifications are now per category** (Admin -> Notification settings). There used to be
  a single `anomaly.detected` row: all ten kinds of finding, or none. They differ enormously in
  weight -- a rogue DHCP server needs attention now, unreachable IPs are more of a weekly tidy-up --
  and lumping them together means people mute the lot to stop the noise, losing the urgent ones too.
  Upgrading changes nothing: a site that had turned email on for anomalies keeps it on for all
  eleven (the old row remains the default source until the settings are saved once).

- **New rule: an IP that keeps changing MAC.** The existing "MAC drift" asks whether one MAC appears
  on two switch ports (miswiring, spoofing); this asks the opposite -- one IP cycling through MACs,
  which means a recycled DHCP pool, somebody taking a static address by hand, or a device using MAC
  randomisation. Each MAC is listed with its times, and the locally administered ones (the signature
  of privacy randomisation) are marked.

- **Per-IP anomaly exemptions** (migration `0137`). Windows 11 / macOS / iOS / Android pick a new MAC
  on every connection with privacy features on -- without an exemption the only option is turning the
  whole rule off, which also hides genuine address takeovers. Exemptions are per category, and not
  every category can be exempted: a rogue DHCP server or an IP conflict should never be silenced with
  "that one is always like that". Randomised addresses are deliberately not skipped automatically --
  that can also be someone spoofing a MAC, so the finding states the facts and leaves the judgement.

- **MAC changes now appear in an IP's change log.** Hostname changes have always been recorded; MAC
  changes were not, so the timeline on the IP page showed "hostname changed" but never "this machine
  changed NIC", even though the ARP table knew.

### Fixed (continued)
- **FDB history and "currently valid" were being used interchangeably** (raised in an external
  review; confirmed). Deriving an IP's switch port read the *entire* FDB history, filtered only on
  "has a port name" -- a port seen once six months ago carried the same weight as today's, so
  `switch_port` could point at a location the machine had long since left, with nothing on screen to
  suggest it. Only entries seen recently now take part (`FDB_CURRENT_MAX_AGE_HOURS`, default 24);
  the history is still kept.
- **LibreNMS's own timestamps are no longer discarded.** The sync stamped `first_seen_at` and
  `last_seen_at` with jt-ipam's sync time, while LibreNMS records `created_at` / `updated_at` itself
  (one row on a real installation was first seen in 2021 and updated that morning). Overwriting them
  turns "this MAC has been on this port for years" into "first seen today".
- **FDB entries are now pruned** (`FDB_RETENTION_DAYS`, default 365, 0 = keep forever). ARP has had
  pruning for a while; FDB only ever grew. The long default is deliberate: the value of this table is
  knowing which port a machine used to be on.

### Added (notifications)
- **Eight new alert sources**, each switchable in Admin -> Notification settings:
  `integration.sync_failed` (an expired token stops a sync for days while the UI looks fine),
  `agent.offline` (scan and certificate agents), `system.health` (the **bad** items from the
  system check), `dhcp.pool_exhausted` (a full pool means new machines silently get no address,
  and the symptom on site is "the network is broken"), `jump_host.key_changed` (a changed
  fingerprint is what interception looks like, and previously only a live console attempt would
  catch it), `cert.fetch_failed` (failing to fetch a renewed certificate has no symptom until
  expiry day), plus `audit.chain_broken` and `ip.stale`, which were already being sent but had no
  row in the settings page -- received but impossible to turn off.

  **State-based alerts fire only on the transition into and out of trouble**
  (`services/state_alert`). That is what keeps them from becoming noise: integrations sync every
  five minutes, so an expired token fails every round -- notifying each time is 288 messages a
  day, the category gets muted by day two, and the genuinely new problems go with it.
  Integrations additionally need two consecutive failures before counting as broken.

  Agent staleness thresholds differ **per kind**: a scan agent long-polls (300 s by default), so
  half an hour of silence is already wrong; a certificate agent runs from a systemd timer whose
  installer default is **daily**, so it gets two days -- one missed run can be a reboot, two in a
  row cannot. The numbers came from a real installation: with a single 30-minute threshold, all
  seventeen certificate agents were reported offline in one round.

  Deliberate judgements: an agent that has **never** reported is not offline (it was just
  created); a jump host that is **unreachable** is not a changed key (crying interception at a
  reboot teaches people to ignore the alert); system-check **warnings** are not notified (half
  the page is legitimately warnings); and a check that **disappears** is never announced as
  recovered -- we simply stopped knowing.

  Subnet utilisation is deliberately **not** a notification: it is a planning number that creeps
  towards its threshold by design, and would produce a daily "still fairly full".

### Notes
- Off by default: the schedule notifies every administrator, and an upgrade should not start
  sending mail on its own.
- No new migration; the settings live in the existing `system_settings`.
- Upgrading must also refresh `scripts/jt-ipam-sync.py`, which is where the schedule is
  triggered. Sites using `jt-ipam.sh upgrade` get this automatically.

## [0.6.11] - 2026-09-07

### Fixed
- **The sections list dropped half the sections** (GitHub issue #27). The reporter has 95
  sections and saw 50; raising the page size to 500 changed nothing, and the footer said
  "50 total" -- the count was wrong too.

  The page asked the backend for the first page of 50 and then paginated and searched in the
  browser. With the data incomplete, the frontend cannot tell "this is everything" from "this
  is page one", and the footer counts loaded rows, so it states 50 with complete confidence.
  It now pages through to the end before handing the rows to the table. **The subnets list had
  the same shape** (a single page of 500) and is fixed with it: a site with more than 500
  subnets was quietly losing everything past that.

- **A device's primary IP could not be selected, and typing a keyword did not help** (same
  issue). The dropdown loaded 500 addresses and then filtered *within those 500* in the
  browser. Past 500 addresses the one you just created is simply not there, and the keyword
  searches memory rather than the database.

  Search now goes to the backend. The logic moved into a shared `useIpOptions` used by all
  three places that pick an address -- the form on the device list, the edit dialog on the
  device page, and the "link an IP" dialog -- which previously each had their own copy, so
  fixing one left the other two broken. Editing an existing device also fetches the currently
  selected address, which would otherwise show blank when it falls outside the first batch.

### Notes
- No database or API change; `/addresses` already accepted `q`, the frontend just never used it.
- The test checklist gains two entries: seed the fixtures before running the browser suite, and
  a list page is not proved by opening it -- reconcile the footer count against the server, on
  more records than fit one page.

## [0.6.10] - 2026-09-05

### Added
- **The version page now states the licence.** This is an AGPL project - the obligations
  that come with distributing or modifying it follow the licence, and nobody should have
  to read the source to find out which one applies. The string comes from the backend
  (package metadata, generated from `pyproject.toml`), with a link to the full text.

  The licence changed once already (Apache-2.0 to AGPL-3.0-or-later on 2026-08-15), and it
  is declared in four places: `backend/pyproject.toml`, `frontend/package.json`, the root
  `LICENSE`, and now this line on screen. Miss one and the page states the wrong licence
  with complete confidence - worse than not stating it at all. `tests/test_license_declaration.py`
  ties the four together.

### Fixed
- **The LLM settings page opened with four 500s.** `GET /system/llm/models` handled
  "cannot connect" (`httpx.HTTPError`) but not "blocked by our own outbound guard" - and the
  default Ollama address is `http://127.0.0.1:11434`, while loopback is unconditionally
  blocked in `safe_http` (allowing it takes an explicit `OUTBOUND_ALLOW_CIDRS`). All the
  page said was "server error": not which address was blocked, and certainly not how to
  allow it.

  Being stopped by our own guard is an expected outcome, not a server fault: it now returns
  200 with a message that names the address and the setting to change. The same error is
  already handled on the AI chat path; this was a single missed spot.

### Notes
- No new migration; the schema is unchanged (still `0136`). Install and upgrade need no
  changes: no new Python or apt packages, no new systemd unit, no new listening port.

## [0.6.9] - 2026-09-05

### Added
- **System check page (Admin → System check).** Press a button, read the result on screen, download
  a plain-text report to paste into a ticket. It reports the database schema version, PostgreSQL and
  its extensions, whether the built frontend matches the backend, background work, integration
  errors, disk space, ICMP capability, and a **data health check**. Every check carries what to do
  about it, not just that something is wrong.

  This exists because of a customer report: the dashboard counted 55 devices while the device list
  returned Internal Server Error and showed nothing. Every question we had to ask them ("run this on
  the server", "paste this SQL") was work we should have been doing ourselves. System-level checks
  (systemd, nginx, backups, scan agent) are still only visible to the CLI `jt-ipam.sh doctor`, and
  the page says so rather than implying everything is fine.

- **Schema drift is now detected at startup**, logged as an error, and shown to administrators as a
  banner. A database left behind by an interrupted upgrade makes pages that read full records fail
  with a 500 while pages that only show counts look normal; the system knew this at boot and said
  nothing.

### Fixed
- **Dismissing an AI audit finding now sticks.** Findings recurred on every run and had to be
  dismissed again and again. The fingerprint was "category + the set of cited addresses", but the
  model cites a *different subset* each run: on live data one "IPMI on a service subnet" issue had
  become five findings citing `{.60}`, `{.46}`, `{.60,.46}`, `{.74,.60,.54}` and one mistyped
  address. Dismissal is now recorded against the **subjects**: a finding whose addresses have all
  been dismissed in that category is not reopened, while a finding involving a **new** address still
  surfaces, because that is new information, not a repeat.

- **A single row no longer takes down a whole list page.** `DeviceRead` inherited the write-side
  limits (vendor/model ≤ 64 chars, u_position 1–99), but those columns are `text` and an unconstrained
  `integer` in the database, and integrations legitimately write longer values. One such row made the
  entire device list return 500 with no indication of which record was at fault. Read schemas now
  accept what the database can hold; the write-side limits stay where they belong, on input.

## [0.6.8] - 2026-09-05

### Added
- **Consoles can route through a jump host (GitHub issue #24, phase 1, Beta).** For sites the
  backend cannot reach directly, an SSH jump host can be put in front: backend → jump host → target.
  The exit is configured on the subnet and can be overridden on an individual address, resolved in
  the order **address > subnet > direct**.

  The ambiguity this feature exists for is already solved by structure: a console always starts from
  **one IP record**, and every IP record belongs to exactly one subnet. So several customers sharing
  the same private range cannot be confused with each other; no extra disambiguation was needed.

  **The failure mode being defended against is not "cannot connect", it is "connected to someone
  else".** The sites that need a jump host are the sites with overlapping private ranges, so a
  console that quietly falls back to a direct connection reaches a *different customer's* machine,
  with nothing on screen to say so. Consequently:
  - **The host key must be pinned before any connection is allowed.** Test connection fetches the
    fingerprint *without sending credentials*; only after it is checked and trusted does the jump
    host actually get used. A changed fingerprint aborts with a man-in-the-middle warning.
  - **BMC refuses instead of falling back.** IPMI/SOL is UDP 623 and an SSH tunnel forwards TCP
    only, so a BMC console on an address with a jump host returns an explicit error naming the
    reason. noVNC is unaffected because it connects to the virtualization host configured in the
    Proxmox integration, not to the address itself.
  - Deleting a jump host reports how many subnets and addresses will fall back to direct.
  - Every session records which jump host it went through, in the audit log and on screen.

  Sessions to the same jump host **share one SSH connection**, with a configurable per-jump-host
  session limit; the forward lives and dies with the WebSocket session.

  SSH, SFTP, RDP and VNC are supported. Verified against a real SSH jump host, in a real browser:
  an SSH shell and an SFTP directory listing both over the two-hop path, plus connection reuse,
  the session limit, and reference release after a failed forward.

  A detail worth recording: aardwolf's `create_connection_newtarget()` replaces the RDP/VNC target's
  ip/hostname but **keeps the port from the parsed URL**. Without the port written into the URL, a
  tunnelled RDP session would have connected to `127.0.0.1:3389`, the backend host itself.

### Notes
- Migration `0136` adds `jump_hosts` plus a nullable `jump_host_id` on `subnets` and `ip_addresses`.
- Install/upgrade unchanged: no new Python or apt package, no new systemd unit, no new listening
  port. The backend must be able to reach the jump host's SSH port.
- Phase 2 (relay through the scan agent, for sites that can only dial outwards) is **not** included;
  that is the scenario in the original issue and remains open.

## [0.6.7] - 2026-09-04

### Fixed
- **Page actions are back in the toolbar, not the card header.** 0.6.5 moved Export / Refresh (and
  the IP change log's description line) into the card header to stop them occupying a row of their
  own; the header is the wrong home for them. They now flow at the end of the filter row, and the
  two buttons are bound into a single flex item so they wrap together instead of leaving Refresh on
  one line and Export stranded on the next. The description line sits on its own row above the
  filters. Checked at 1440, 1100 and 900 px on both pages, with a horizontal-overflow assertion.

## [0.6.6] - 2026-09-04

### Fixed
- **The backend package version no longer goes stale.** `backend/pyproject.toml` had
  `version = "0.3.0"` hard-coded and had stayed there while the product moved to 0.6.x. The release
  routine touches `app/version.py`, `package.json` and the two READMEs, and a fifth place that has
  to be remembered separately is a place that eventually stops being true. It is now derived from
  `app/version.py` (`[tool.hatch.version]`), verified by building a wheel and by running the exact
  `pip install -e .` the installer uses.
- Two places still described the product as "新世代 IPAM"; that wording was retired long ago
  everywhere else. The FastAPI app description (visible in the API docs) now matches the rest.

## [0.6.5] - 2026-09-04

### Changed
- **The IP change log can be narrowed to a section, a subnet or a unit.** The page holds every
  address change on the site, but the question people actually arrive with is "what changed in
  *this unit* this week" or "what changed in *this network*". The unit filter follows the same
  inheritance rule as the rest of the app: a subnet with no unit of its own uses its section's.
  Matching only `subnets.customer_id` would silently drop every site that sets the unit at the
  section level: a few rows short, with nothing on screen to say so.
- **RIPE / TWNIC import moved from Admin to Advanced.** It is a lookup-and-import tool, not a
  system setting. The `/import` route still works so existing links do not break, and the two tabs
  now live in one shared component so the page and the menu entry can never drift apart.
- **Page actions no longer sit on a row of their own.** On the topology page Export and Refresh
  occupied a dedicated right-aligned row, which wasted a band of space and collided with the filter
  row on a narrow window; both now sit in the card header next to the title. The IP change log got
  the same treatment: with three more filters added, its Export button would otherwise have been
  pushed onto a second line by itself.

## [0.6.4] - 2026-09-04

### Fixed
- **FortiGate: stopped guessing a VDOM name when the device does not have VDOMs** (GitHub issue #26,
  "does the tool only support a FortiGate with VDOMs split?"). Reading the code, VDOMs were never
  required, but when the VDOM list could not be read (permissions, or a firmware without that
  endpoint) the integration fell back to the literal name `root` and put it on **every** request.
  On a device without VDOMs `root` is usually correct, but that is a coincidence, not a guarantee:
  if a firmware objects to a `vdom` parameter while VDOMs are disabled, *every* endpoint fails at
  once and all the operator sees is a wall of identical errors with no hint that the shared cause is
  a parameter we added ourselves.

  The integration now asks the device directly (`system/global` → `vdom-mode`) and, for `no-vdom` or
  when the answer cannot be read, sends **no VDOM parameter at all**; FortiOS then uses the
  management VDOM, which is what we want. Names written into data (VPN tunnel names, NAT external
  ids, the VDOM column in the read-only view) still show `root` so nothing renders blank.

- **The connection diagnostic can now tell "wrong VDOM scope" apart from "endpoint not available".**
  When an endpoint fails with a VDOM set, it is retried once without one; if that succeeds the row
  says so explicitly. The two cases produce identical error text from FortiOS, so previously there
  was no way to tell them apart from the screen. The diagnostic also reports the device's
  `vdom-mode` and whether the queries were VDOM-scoped at all.

### Security
- `postcss-selector-parser` pinned to >= 6.1.3 (Dependabot alert, low / CVSS 2.1: uncontrolled AST
  recursion). It is a transitive **development** dependency of `eslint-plugin-vue` and
  `@vue/eslint-config-typescript`; it never reaches the built frontend, so this hardens the build
  environment rather than the shipped product.

## [0.6.3] - 2026-09-04

### Added
- **MikroTik RouterOS integration (Beta, phase 1).** Read-only pull over the RouterOS v7 REST API
  (`www-ssl` must be enabled; the account needs `api` + `read` only). It syncs DHCP leases,
  DHCP ranges, firewall filter/mangle/NAT rules, address lists, VPN (PPP sessions and WireGuard
  peers) and (off by default) the ARP table. New admin page **Integrations → MikroTik** and a
  read-only **Router (MikroTik)** view; the rules, address lists, NAT source filter, IP-detail
  firewall lookup, rule-change detection, AI chat tools, audit target names, system export/import
  and scheduled sync all cover it, enforced by `tests/test_integration_coverage.py`.

  **This integration is designed around not slowing the router down**, because at the site that
  asked for it the MikroTik boxes (CCR2004 / CCR1072) are the *main* routers:
  - one TLS connection is reused for a whole round instead of one handshake per endpoint;
  - sections run strictly **sequentially** with a configurable pause between them, so endpoints are
    never fetched in parallel;
  - CPU load is re-read after every section and **the rest of the round is skipped** once it passes
    a threshold, with the reason recorded and shown in the list;
  - every request carries `.proplist` and, where possible, a server-side filter, so the router
    serialises as little as possible;
  - responses have a size cap (RouterOS REST has **no pagination**), and `/ip/route` and
    connection tracking are refused in code, not merely in a comment;
  - the expensive sections default to **off**, and "Test connection" reports **rows and seconds per
    endpoint** so the administrator decides with numbers in front of them.

  Two limits are stated rather than hidden: **RouterOS 6.x has no REST API** and is named as such
  instead of failing with a vague connection error, and **no claim of zero impact is made**. Any
  query costs the router some CPU; what is guaranteed is low frequency, little data, and getting
  out of the way when it is busy.

- **`arp:mikrotik` counts as liveness evidence, on a different basis from the other vendors.**
  RouterOS's `/ip/arp` has no age, TTL or expiry field, so the trick used for OPNsense (`expires`),
  FortiOS (`age`) and PAN-OS (`ttl`), which derives when the entry was really refreshed, does not
  apply. Instead only `status=reachable` entries are recorded: that state *means* "confirmed within
  the reachability timeout", so stamping the sync time is defensible. `stale`, `delay`, `probe`,
  `permanent` and the rest are not recorded at all. DHCP leases remain `lease:mikrotik`
  (non-ageing, not trusted for liveness by default).

- **Outbound HTTP gained a shared-connection helper and a response size cap** (`safe_client()`,
  `max_bytes=`). Both were prerequisites for the above and are available to every integration.

### Fixed
- **Firewall rule-change detection was reading a stale view of the database and, for three vendors,
  was not working at all.** Production sessions use `autoflush=False`, and most integrations sync
  rules by deleting the instance's rows and inserting the new set. The DELETE reaches the database
  immediately; the INSERTs sit in the session. `run_sentinel()` then queried the rules table and got
  back **nothing**. For the mirror-replace vendors (FortiGate, Palo Alto, and the new MikroTik) that
  meant an empty snapshot every round: rule-change detection silently did nothing, with no error
  anywhere. OPNsense mostly updates existing rows so it appeared to work, but a rule added during a
  round was only noticed a round later. `run_sentinel()` now flushes before it reads.

  This is the same trap as the audit-chain break in 0.5.204, and it hid for the same reason: the
  test fixtures use `autoflush=True`, so the whole scenario is green in tests. The new regression
  test sets `autoflush = False` explicitly and fails without the fix.

### Notes
- Migration `0135` adds `mikrotik_routers` / `mikrotik_rules` / `mikrotik_address_lists`.
- Install/upgrade unchanged: no new Python or apt package, no new systemd unit (the existing
  `jt-ipam-sync.timer` runs it). The backend must be able to reach the RouterOS management
  interface, usually a private address, so `OUTBOUND_ALLOW_PRIVATE` applies.
- FDB (`/interface/bridge/host`) and neighbour discovery (`/ip/neighbor`) are **deliberately not in
  this phase**: `fdb_entries` is keyed on a LibreNMS device and `librenms_links` requires a
  LibreNMS instance, so a MikroTik source needs schema work first. Half-wiring them would have
  produced switches that show up in the topology as nothing at all.

## [0.6.2] - 2026-09-03

### Added
- **Switch port descriptions now come across from LibreNMS.** A port's `ifAlias`, the description
  configured on the switch and typically the most useful line on the whole page ("HR-J.Chen-10.0.0.5"),
  was never fetched, so our port list showed an empty description column next to LibreNMS's
  populated one. It is now synced into `device_ports.description`.
  **Aliases that merely repeat the interface name are ignored**: on Linux hosts LibreNMS reports
  `ifAlias` identical to `ifName`/`ifDescr`, and a dry run over the live instance found 88 such
  ports and no real descriptions; copying them verbatim would have filled the column with
  `eno1np0` and made it worse than empty. An existing description is never cleared when LibreNMS
  has nothing to offer, the same rule the port MAC already follows.

### Fixed (rack diagram and settings layout)
- **Evidence-source options spilled outside their card on a narrow window.** The option grid is a
  flex child, and a flex child cannot shrink below its content unless told to, so at 820px it ran
  195px past the card. Fixed, and the gap in the tests it slipped through is fixed too: the
  layout spec now walks five widths, and the route sweep runs at 900px asserting that no page
  scrolls horizontally. A defect that only exists below some width proves nothing when every
  test runs wide.
- **The Proxmox VE integration page sometimes opened empty** and needed a click on its tab to
  appear: the active tab was chosen in `onMounted`, so the first render pointed at a tab that does
  not exist in admin mode. It is now decided during setup, and a watcher follows the route because
  the two menu entries share one component and switching between them does not remount it.
- **The PVE firewall tab had no cluster column**, and, worse, its rules were matched by VMID
  alone. A VMID is unique only within a cluster, so with two clusters a guest picked up the other
  cluster's rules: both the rule count and the expanded list were wrong. Rules are now matched by
  (cluster, VMID) and the cluster is shown.
- **Rack device names ignored the alignment setting.** With "centre" selected they still hugged the
  left. The name box is absolutely positioned across the device's full height, and it carried the
  `max-width: 110px` meant for the text: with `left` and `right` both pinned, the browser keeps
  `left` and drops `right`, so a 126px box sat against the left edge and the text was centred
  inside *that*. Measured, not eyeballed: box 126px against a 250px row.
- **A name spanning several U was hidden by the units below it.** The label overflows its own cell
  by design, but the cells beneath are painted afterwards: a 2U name was cut in half and a 4U name
  vanished entirely. Both are now covered by a geometric test.
- **Fields side by side were vertically offset.** A "space out adjacent fields" rule was pushing the
  right-hand column down 14px wherever two fields shared a row (visible in Display & maps and in
  GeoIP). Spacing now comes from the containers, not from sibling margins.
- **Hovering a device made its name disappear.** The highlight used `filter: brightness()`, and a
  filtered element becomes its own stacking context, so the name, which is positioned in the
  device's top unit and stretches down over the others, could no longer paint above them. The
  highlight (and the dim state, which used `opacity`) now overlay a translucent layer instead, and
  the test asserts the highlighted units create no stacking context.
- **A card holding a single field wasted half its width**, so its help text wrapped early with the
  right half empty.
- The "never expires" caution in the liveness picker is now its own highlighted line instead of a
  sentence buried in grey help text.

## [0.6.1] - 2026-09-03

Version numbering moves to 0.6.x from here; 0.5.247 was the last 0.5 release.

### Fixed
- **A powered-off host could look online for longer than the configured threshold.** An ARP entry
  survives in the firewall's table until it times out (20 minutes on FreeBSD), and every sync round
  in that window was recording it as "just seen", so the threshold was effectively stacked on top
  of the ARP timeout. Each round now derives the same observation time from the entry's own
  countdown, so a host that stopped answering at 12:00 is reported offline one threshold later,
  not one threshold plus twenty minutes. This is now pinned by a test that walks the rounds after
  a shutdown, and by an end-to-end check that a host last seen 25 minutes ago is online at a
  30-minute threshold and offline at a 20-minute one.

- **Zabbix was registered as a liveness source but never actually fed one.** Its sync linked hosts
  to addresses and mirrored their availability into its own table, yet nothing was written back to
  the IP, so the liveness rules could not see it and the settings page could not offer it, which
  reads as "Zabbix isn't supported here". Availability is now recorded on the address
  (`last_seen_zabbix`, migration 0134) **only when Zabbix reports the host up**: "down" is evidence
  of the opposite and "unknown" is no evidence, and writing either would turn them into "seen".
  A guard test now requires every source the contract marks as expiring to be wired through.

### Changed
- The liveness evidence picker separates its groups with a rule and more spacing, so probes, ARP
  tables, VPN sessions and DHCP leases no longer read as one undifferentiated block; its label is
  now "evidence sources counted".

## [0.5.247] - 2026-09-02

### Fixed (everything a new integration has to reach)
Adding Palo Alto covered the sync, the rule-change sentinel and the settings page, and then a
sweep found a string of places still stopping at the previous vendor. None of them broke: they
were simply one vendor short, which is exactly why nobody noticed.
- **AI chat** could not see it: `list_firewalls` returned three vendors, so the model would answer
  "which firewalls do we have" from an incomplete list, and there were no tools for Palo Alto
  policies or address objects. Both added, scoped to global-read like the other firewall tools.
- **An IP's detail page** did not show which Palo Alto rules touch that address. The App-ID is
  shown next to the service, because that is what a PAN-OS rule actually matches on.
- **Audit entries** showed a truncated UUID instead of the instance name, and clicking one went
  nowhere.
- **Unauthorised-DHCP detection** did not know the firewall's own management address, so a Palo
  Alto could report itself as a rogue server.
- **OS fingerprinting** did not classify PAN-OS as a network device.
- **The rule-change page** still said it compares "three firewalls", and the change kind was
  crammed into the diff column; it is now its own column, so rows line up.
- **The precedence page** printed raw lowercase keys (`paloalto`, `zabbix`) for sources with no
  display name, and its intro enumerated a stale subset of sources.
- **The liveness evidence picker** wrapped into ragged rows; sources are now grouped by kind
  (probes / ARP tables / VPN sessions / DHCP leases) in an aligned grid.
- Docs: the feature map, the home page and the API endpoint table now list Palo Alto.

### Fixed (what makes a firewall's ARP table usable evidence)
- **A firewall's ARP entries were stamped with the sync time, not the time they were refreshed.**
  Asked why a firewall's ARP table may claim a host is online when LibreNMS's may not, the honest
  answer turned out to expose a flaw in our own code. Live data from two OPNsense boxes: every
  entry carries `expires`, counting down from FreeBSD's 1200-second `max_age`; of 84 entries, 22
  had last been refreshed more than five minutes earlier and six were 15–20 minutes old, all of
  which we were recording as "just seen". That is the same defect we criticise LibreNMS ARP for.
  The entry's own clock is now used: `expires` (OPNsense / pfSense), `age` (FortiOS), `ttl`
  (PAN-OS) are converted back to when the entry was actually refreshed. Permanent entries and ones
  already flagged expired are skipped, and `max_age` is taken from the batch so a site that tuned
  it still lines up. Where a vendor gives no such field we fall back to the sync time, which
  claims only "still within the ARP timeout", a weaker statement, deliberately.
  **The rule is now written down: a source may claim liveness if it can say *when*, not because
  it happens to be called ARP.**

### Testing
- **A guard for "did the new integration reach everything?"** (`test_integration_coverage.py`).
  It checks each vendor against every place that enumerates them: AI tools, rule-change
  detection and its on-screen copy, the IP-detail lookup, NAT, audit naming, scheduled sync,
  export/import, the evidence contract, and display names in both locales. It also records the
  two features that are **deliberately** vendor-limited (exposed-services and rule-rot detection)
  with the reason, so they are not "fixed" by accident: a FortiGate policy or a PAN-OS App-ID
  rule is not the same claim as "this port is reachable from the internet".

## [0.5.246] - 2026-09-02

### Fixed
- **A VNC server with no password could never be reached.** From RFB 3.7 onwards the client must
  reply with one byte naming the security type it picked; the library we use only sends it on the
  password path, leaving the no-password branch empty, so both sides waited for each other until
  the timeout. All the operator saw was "連線逾時", which points at the network rather than at the
  handshake. Patched alongside the mouse fix already applied to that library.
- **Console errors no longer hide the reason.** "連線/認證失敗（密碼錯誤或 VNC 設定）" was sent for
  every failure, including the most common one: the target closing the TCP connection before the
  RFB handshake, where the password is never even sent. That message walks the operator into
  checking a password that was never the problem. Failures are now classified (refused / timed out /
  closed before the handshake / authentication) and carry the underlying reason, the same rule the
  integrations already follow. Applied to the RDP console too.

### Testing
- **A minimal RFB server as a test target** (`frontend/e2e/fixtures/vnc-target.py`, standard library
  only) plus a backend test that completes a real handshake against it and an e2e that renders the
  framebuffer in the browser and measures the pixels. Nothing had ever completed a VNC handshake in
  a test, so when a real target failed to connect we could not tell our half from theirs, which is
  exactly how the no-password defect had stayed invisible.

## [0.5.245] - 2026-09-02

### Added
- **A BMC / SOL setup page on the documentation site** (`docs/bmc-sol.html`, linked from the feature
  map and from the BMC console's own setup guide). Until now the only thing said about the BIOS was
  one optional line ("point console redirection at the same COM port, 115200 8N1"), which leaves out
  the fields that actually decide whether you see anything. The page reproduces the AMI
  `Console Redirection Settings` screen with a recommended value for every field and what breaks when
  it is wrong, most importantly **Redirection After BIOS POST**: anything other than `Always Enable`
  stops the relay when POST ends, so you see the BIOS and then nothing after boot, which is easy to
  misread as a missing setting on the operating-system side. It also maps each symptom (blank screen,
  garbage, freezing after a few lines, replacement boxes, cut-off edges) to the one field to check
  first, and names the equivalent setting on HPE iLO, Dell iDRAC and Supermicro.

## [0.5.244] - 2026-09-02

### Added
- **Palo Alto (PAN-OS) integration (Beta).** Its own settings page, independent of the other
  firewalls, read-only over the PAN-OS API: ARP table, DHCP leases, security policies (with the
  App-ID, which is where a PAN-OS rule's meaning actually lives), NAT and address objects, across
  every vsys. Rule-change detection covers it like the others, so a changed policy raises the same
  notification with the same diff. There is **no appliance to test against**, so the parsing is
  deliberately tolerant and "Test connection" reports **per endpoint** whether it could be read,
  including the REST version segment it detected, because PAN-OS binds `/restapi/v11.1/…` to the
  firmware version and a wrong guess 404s everything.
- **Wazuh agents now count towards liveness.** An agent's keep-alive is maintained by the manager
  and expires, which is exactly what a liveness source has to be. The **agent's own keep-alive
  time** is stored, not the time we synced; otherwise an agent that went silent three months ago
  would mark its address online at every sync.

### Changed (this one changes what "online" means, so read it)
- **Firewall evidence is now recorded per source.** Everything the OPNsense / pfSense / FortiGate /
  Palo Alto sync learned (ARP tables, DHCP leases, VPN sessions) used to be written into
  `last_seen_scanner`. Two consequences: sites with no scan agent at all were shown "online
  (scanner)", and there was no way to trust one kind of evidence without trusting all of them.
  Each now lands under its own name (`arp:opnsense`, `vpn:pfsense`, `lease:fortigate`…) and the
  liveness settings list them individually, showing only the integrations that site actually has.
  - A firewall's own ARP table **can** claim a host is up: entries age out in minutes and we
    re-read them each round. It stays trusted by default, so a firewall-only site does not go
    dark on upgrade. **Static/permanent entries are skipped**: they never age out.
  - A **DHCP lease cannot**: a lease often outlives the machine's uptime by days. Off by default.
  - LibreNMS's ARP still cannot, unchanged: its API returns no timestamp at all.
- Ghost-IP and "ARP only" detection follow the same rule: an address a firewall can still see is
  neither a ghost nor ARP-only.

### Fixed
- **The dashboard's virtualisation node had no right answer when both platforms were in use.**
  The number is the sum of Proxmox and VMware, so either destination showed half of it and looked
  like data had gone missing. With both configured the node no longer navigates (and no longer
  looks clickable); with one, it goes where it always did.
- **The IP change log printed a raw translation key** for any event type without a translation
  (`ipChanges.event.update`). It now falls back to the raw value, matching what the IP edit dialog
  already did.

### Testing
- **Every route is now opened by a test.** The sweep visited 22 of 78 routes; forty-odd pages had
  never been opened by anything. The new spec parses the route list out of the router itself, so a
  new page is covered the day it is added, and it fails on blank screens, JS exceptions, failed
  API calls and untranslated keys. It immediately caught a 500 on **creating** a Palo Alto firewall
  (the API key was encrypted into the wrong shape), a defect every backend test had missed,
  because none of them called that function.

## [0.5.243] - 2026-09-01

### Fixed (a sweep of accounts and permissions)
- **The login path could demote the last administrator.** With a group mapping configured, the last
  remaining admin only had to fall out of that group (a renamed group, a typo, a directory change)
  and the next login revoked their admin, leaving nobody able to reach the admin area short of
  running the CLI on the server. `PATCH` and `DELETE` already guarded this; login did not. All
  three external login paths (LDAP, OIDC, SAML) now agree: **the count of effective admins is
  never allowed to reach zero**.
- **Deleting a user or a group left orphaned grants.** `permissions.principal_id` can point at
  either a user or a group, so it cannot have a foreign key: nothing cleans it up. The rows that
  remain show on the permissions page but match nobody, leaving an audit with a grant it cannot
  explain (**one already existed in production**; it has been removed). Deletion now clears them
  and records how many in the audit entry: that is a permission change, not a side effect.
- **Deleting a subnet, section, customer or device also clears grants pointing at it**
  (`object_id` has no foreign key either).

### Checked and found sound
Every user and group endpoint is guarded by `require_admin` at the router level; a deactivated
account is rejected on each request and on token refresh; changing one's own password requires the
current one; permission grants and group membership changes are audited; the last-admin guard on
`PATCH`/`DELETE` remains.

## [0.5.242] - 2026-09-01

### Fixed
- **Admin granted to an LDAP/SSO account switched itself back off** (reported by a customer). All
  three external login paths (LDAP, OIDC, SAML) unconditionally ran
  `is_admin = user is in an admin group`, and the admin-group mapping is **empty by default**: an
  empty list always evaluates to false, so every login revoked admin. No external account could
  ever be an administrator, while the switch in the UI looked perfectly usable.
  That was **inferring "not an admin" from "nothing configured"**: with no mapping, the system
  knows nothing about who should be an administrator, and the right move is to leave the flag
  alone and let local administration decide. **With a mapping configured the directory remains the
  source of truth** (that is the point of configuring it), and the switch now states the rule,
  because letting someone flip it and silently reverting on next login is the worst of both.

## [0.5.241] - 2026-08-31

### Added
- **Expiry warning lead time is configurable per certificate** (migration 0131). Renewal takes
  different amounts of time for different certificates: a commercial one bought by hand needs a
  month of lead time, an auto-renewed one needs a week; one threshold for all of them is either
  too noisy or too late. Each row on the certificates page gets an "expiry notice" button, and
  system settings holds the global default. **Unset means "use the default", not "never warn"**,
  and it can be set back to the default (a `null` in PATCH means "don't change", so there is an
  explicit flag for it; otherwise setting it once would be irreversible).

### Fixed
- **Exposed-services list: searching one IP showed a different one.** The table's row-key used the
  **array index** (`key-via-index`), so after filtering the same index referred to a different row
  and the table reused the old one. Rows now get a stable identity when the data is built. Verified
  by replaying the same filter over real production data: 7 matches, all of them the searched IP,
  agreeing with the "7 rows" the page reported.
- **The virtualization card on the device page was untranslated**: field labels and statuses like
  `running`/`stopped` now have translations; an unrecognised status is shown as-is, since translating
  a word we do not know would just invent information.

## [0.5.240] - 2026-08-31

### Fixed
- **A console could only use a stored credential, with no way to switch to another.** Clearing the
  selection meant using the ✕ that **only appears on hover**; opening the dropdown showed just the
  saved entry, so there appeared to be no other option. Being possible is not the same as being
  discoverable. All six consoles (SSH / SFTP / RDP / VNC / noVNC / BMC) now offer "use different
  credentials" in the dropdown itself, which returns to the manual fields. A test watches all six:
  fixing one instance of a shared interaction leaves five inconsistent ones.
- **"Reachable, just slow the first time" was reported as unreachable.** Fetching the host key is
  the **first** outbound step of the whole path, yet it allowed only 8 seconds, less than the 15
  the actual connection gets. Measured against one OpenSSH 8.9 host: over 8s the first time, 1.08s
  the second, 0.05s the third (a common cause of the first being slow is the server doing a reverse
  DNS lookup on the source). The timeout now matches the connection's, and the message names the
  likely cause and suggests retrying.

## [0.5.239] - 2026-08-30

### Added
- **A new anomaly category: "device link may be stale."** Asked directly: if the IP is later used
  by a different host, does it stay linked to this device? It does. A link is never re-evaluated
  once written, so when the address is handed to another machine (common with DHCP) the link
  **quietly becomes wrong**: the screen looks fine, it just points at the wrong device. IPs whose
  **MAC changed after the link was made** are now surfaced; that is a recorded fact, not an
  inference. Nothing is unlinked automatically, because that would be guessing too; both timestamps are
  shown so the order can be checked.
- The switch and port fields sit on one line via an input group. Previously the second field was
  pushed onto its own line when space was tight, leaving the "@" stranded.

### Fixed
- **A notification has to take you where you need to look.** The "firewall rules changed" one
  carried **no link at all**, so clicking it did nothing; a sweep then found two more pointing at
  **routes that do not exist** (`/admin/audit`, `/admin/event-rules`; those pages live at
  `/audit` and `/event-rules`). Both failures look identical on screen. A guard test now checks
  every notification against the front-end route table: it must carry a link, and that link must
  resolve.
- **The device suggestion now lists candidates instead of offering a blanket switch.** It used to
  have a "also link the other IPs with this hostname" checkbox, but **the same hostname does not
  mean the same machine**: a reused DHCP address keeps its old hostname. In the field one laptop's
  name was spread over nine IPs, among them a Proxmox VM and an ESP32. Candidates are now listed
  individually with their **evidence (MAC, vendor)**; those sharing the MAC are marked and ticked
  by default, the rest are not. On apply the server **does not trust the ids from the client** and
  accepts only those in the set it computes itself.
- **The device field on the IP detail page showed a fragment of a UUID.** The name was resolved
  only against the one page `listDevices()` returns (200 rows), so a freshly created device was not
  in it. It now **fetches that device by id**.
- **Rack diagram: a 2U device's name sat half a row too high.** The name was drawn in "the middle
  row", and an even number of Us has no middle row. It now spans the whole device and is centred
  within it (compensating the outline width, without which everything shifts 2px down). A
  geometric end-to-end test guards it, because this kind of drift is invisible in a screenshot.
- **AI review prose contained addresses with no source.** One run produced both `192.16CA.1.59`
  (the model mangling `192.168.1.59`) and `196.168.1.39` (valid but nonexistent). Such errors look
  precise and read as confident, and people go and look them up. Addresses in the prose that do not
  match the cited evidence are now removed (CIDRs are kept, since those describe a range): better one
  sentence short than one plausible-looking falsehood.

### Changed
- **Only the legend keeps the topology "virtual machines" toggle.** There were two controls for it
  at different layers: the legend merely hid nodes, while VMs had not been fetched at all, so
  clicking it did nothing. The legend entry now controls whether VMs are loaded, and reflects that.

## [0.5.238] - 2026-08-30

### Added
- **Editing an IP now suggests creating or linking a device.** A laptop on DHCP shows up under a
  dozen IPs with the same hostname, and creating and linking a device by hand for each one is pure
  drudgery. The device field now offers "Create device X and link it" (or "Link to the existing
  device X"), with a checkbox to **also link the other IPs that share the hostname and have no
  device yet**. It is **only a suggestion: nothing happens until it is clicked** (a test guards
  exactly that: looking at the suggestion must have no side effects). It follows the existing
  refuse-to-guess rules: no suggestion when several devices match the name, an existing link is
  never overwritten, the batch only touches empty ones, every change is written to the IP history
  and the audit log, and creating a device stays admin-only. The "how many sibling IPs" count is
  scoped in SQL: filtering after the fetch would count rows the user cannot see.

### Fixed
- Matching an existing device by address used a string **prefix**, so `10.0.0.1` matched
  `10.0.0.10` and `10.0.0.100`. It is an exact match now; a wrong device link is harder to notice
  than no link at all.

### Note: the SFTP upload stall
The upload failures chased over several days came down to **a faulty wired network adapter on the
client machine** (switching to Wi-Fi worked): its TCP had accepted 85442 bytes to send, put only
66820 on the wire, and then neither sent nor retransmitted the rest before resetting the connection
some twenty seconds later. The server acknowledged everything it received with a steadily growing
window, and another machine on the same LAN pushed 5 MB to it in 0.1 s.

The changes from 0.5.229 to 0.5.237 were therefore **not** the fix for that, but they stay, because
each stands on its own: console keepalive, a pong timeout that no longer cuts off slow links, upload
flow control that discovers what a path can carry, and above all the **step-by-step diagnostics**;
they are what turned "connection lost" into "nothing arrived after byte 49152", which is what made
the network adapter findable at all.

## [0.5.237] - 2026-08-30

### Fixed
- **A rack can be chosen without picking a location first** (both the device list and the device
  detail editor). A rack already belongs to a location (that is a lookup, not a question for the
  user), and choosing a rack now **fills the location in**. A guard test watches both entry points,
  since editing the same object from two places is where a fix usually gets applied to only one.

### Added
- **The in-flight upload window discovers what the path can carry**: it starts at 32 KiB, doubles
  while acknowledgements keep arriving (up to 4 MiB), and halves and retries the file when it
  stalls. Hard-coding a small value would make everyone pay for one bad path: over a link with
  100 ms of round trip, a 32 KiB window is only about 320 KB/s. A healthy path reaches the ceiling
  within a few round trips.
- Upload frames are a fixed 16 KiB. A 256 KiB frame was measured taking the connection down the
  moment it was sent; what actually needs controlling is **how much is in flight** (a 256 KiB frame
  puts 256 KiB on the wire at once), and frame size itself does not affect throughput.

# Changelog

All notable changes to this project are documented here. The format is loosely
based on [Keep a Changelog](https://keepachangelog.com/); versions track
`frontend/package.json` / `backend/app/version.py`.

## [0.5.236] - 2026-08-30

### Added
- **Uploads are paced against acknowledgement**: the server confirms each block it takes and the
  client keeps only a small amount in flight. A path was measured where continuously streamed data
  was swallowed after roughly 48 KB; only this pattern gets through it.

## [0.5.235] - 2026-08-30

### Fixed
- **Upload frames are now 16 KiB: large frames were killing the connection.** Measured in the
  field: on one and the same connection a 16 KiB data frame reached the server in **7 ms**, and the
  256 KiB frame sent immediately after **took the connection down** (the server had received
  exactly those 16384 bytes; close code 1006), while text commands worked throughout. This is the
  direct cause behind every earlier "the upload does nothing and then the connection drops": it
  stopped at the first large frame every time.
- Smaller frames do not slow the transfer: data is streamed rather than acknowledged per frame, so
  throughput is set by the link and by how fast the remote writes, not by frame size.

## [0.5.234] - 2026-08-30

### Added
- **Uploads now start small and grow only after the server confirms receipt.** The server reports
  how many bytes it has taken; the client sends **16 KiB** first and only widens to 256 KiB once
  that is acknowledged. A path was seen in the field where text commands (small packets) worked
  throughout while the first 256 KiB data frame **never reached the server at all**, and the
  connection died on its own twenty-odd seconds later. If small blocks get through, the whole
  upload gets through; if they do not, the user is told within 15 seconds instead of waiting.
- **The "not getting through" message points somewhere.** The server having replied "ready to
  receive" means the server is fine, so the trouble is between this computer and it: commonly a
  VPN or proxy discarding larger packets, or a browser extension interfering; try an incognito
  window or a different network. A bare "connection lost" sends people the wrong way.
- On-screen upload progress now counts **bytes the server confirmed**, not bytes handed to the
  local socket. The two agree when things work and diverge when they do not, which is exactly
  when it matters.

## [0.5.233] - 2026-08-30

### Fixed
- **Console replies now carry a request id: "the screen says it finished while the server received
  nothing" is no longer possible.** The client had a **single unlabelled waiting slot**: any `ok`
  from the server resolved whatever happened to be waiting. One protocol slip (a late reply, a
  duplicate, a reordering) swapped success for failure: the field log showed `written=0` while the
  screen reported "1.5 MB of 1.5 MB sent" and moved on to the next file. With ids, a reply that
  matches nothing is ignored and the pending request keeps waiting, so "not received" shows up
  honestly as "still waiting".
- **WebSocket compression is off** (`--ws-per-message-deflate false`). The console carries file
  bytes (usually already-compressed archives and executables), so deflate buys nothing on the wire
  while adding a **stateful** layer between "the browser called send()" and "the server received
  bytes" that fails without an error on either end, and burning CPU per frame.

### Added
- Console logging now records **how long the remote open took** and **when the first data frame
  arrived and what kind it was**. Without those, "the remote is slow to open a file" and "no data
  ever arrived" look identical in the log.

### Testing
- The end-to-end tests clean up the files they create. Accumulated files pushed the listing past one
  page, so "is the row I just uploaded visible?" failed for entirely unrelated reasons, and that
  misled three separate investigations today.

## [0.5.232] - 2026-08-30

### Added
- **Whole folders can now be dropped onto the SFTP console** (nested subdirectories included).
  Dropping a folder previously produced only a "skipped" notice. The remote directory structure is
  created first and files follow; the other order fails every file for a missing parent.
  A single drop takes at most 500 files and 16 levels, and **says how many items were left out**
  rather than truncating silently.
- The server's `mkdir` gained a `parents` option (create missing levels, treat an existing
  directory as success). Without it the client collects a string of bogus failures for
  directories that already exist, and those errors abort the upload in progress.

### Verified
- **Before/after for the ping timeout** (v0.5.231): the same 5.8 MB file over a genuinely
  rate-limited link: with the default 20s pong timeout the transfer **died at 47.9s having
  written 1.8 MB** (close code 1006); with 600s it **completed all 5,831,130 bytes in 104s**.
  One setting apart.
- ⚠️ This only reproduces with **incompressible** data: WebSocket permessage-deflate shrinks
  compressible test data to almost nothing, so a slow link never backs up and the test passes
  for the wrong reason. The file that failed in the field was an `.exe`.
- The end-to-end test now checks that a folder and its nested contents really arrive, plus new
  `dropWalk` unit tests (batched `readEntries` must not lose files, over-limit must be reported,
  `..` and path separators are refused).

## [0.5.231] - 2026-08-30

### Fixed
- **Large console uploads were being cut off by the server itself. This is the real cause behind "connection lost".** A 5.8 MB file dropped into SFTP died after 26 seconds; the server recorded "the peer closed the connection", the upload timeout was never reached, and there is no reverse proxy in the path. The culprit was a uvicorn default: **a WebSocket ping every 20s, and the connection is dropped if no pong arrives within 20s**. The browser answers the ping immediately, but that pong is queued **behind the megabytes of upload data already sitting in the same TCP stream**, so on a slow uplink it simply cannot get back in time. The bigger the file and the slower the link, the more certain the failure.
- The ping interval stays at 20s (a genuinely dead peer must still be reaped), but the **patience for the reply is now 600s**, which covers the 100 MB cap down to roughly 1.4 Mbps of uplink. A guard test (`tests/test_ws_ping_timeout.py`) keeps someone from quietly restoring the default later.

### Added
- **Byte-level upload progress**: "{sent} / {total} ({pct}%)" while uploading, and the disconnect card now states **where it stopped**. Zero bytes sent (the file could not be read on your own machine) and 95% sent (something went wrong in transit) looked identical on screen, and they call for opposite investigations.
- Server-side logging of upload progress and the interruption point (a line every 4 MB; bytes written and the close code when the peer goes away).

### Ruled out (recorded so nobody re-investigates)
A slow uplink on its own, a TLS reverse proxy and its default timeouts, dropping a folder and a file together, the file size, and client-side send backpressure. Locally, the same file over a 2 Mbps-throttled link through an nginx reverse proxy always succeeded, **because browser-level throttling does not put the pong behind the upload; only a genuinely slow connection does**.

## [0.5.230] - 2026-08-30

### Fixed
- **A failed read during upload never told the server, and the whole session hung.** `file.slice(...).arrayBuffer()` in the send loop **can throw**: the file was moved after being dropped, an external disk went away, an iCloud file was not downloaded locally yet. Without a guard the exception escaped the upload function and the "giving up" notice was **never sent**: the server kept waiting for bytes that would never arrive, and the user saw "the upload does nothing, then the connection drops" while the actual cause was on their own machine. A failed read now notifies the server, and reports **"cannot read this item" rather than "connection lost"**, since the latter sends people to investigate the network, which is entirely the wrong direction.

### Added
- **The disconnect card now shows the WebSocket close code.** `1000` is a normal close; `1006` means the connection was cut mid-way (usually an idle timeout in an intervening reverse proxy). Those two call for completely different investigations, and until now the screen said only "connection lost".
- **Logging for what happens inside an SFTP session**: every command, how many bytes each upload wrote against how many it declared and whether it was interrupted, plus the reason a session ended and the original exception behind a failed operation. Three rounds of diagnosing one upload problem came down to guesswork because the log was blank between "session start" and "session end". **Paths are not logged** (those are the user's file names), only the operation, size and outcome.

### Verified
New browser end-to-end test "folder and file dropped together" reproduces the field scenario exactly (macOS reports a folder as 256 bytes) and checks that the folder is skipped, the file arrives **byte for byte**, and the session stays usable. All 15 SFTP end-to-end tests pass.

## [0.5.229] - 2026-08-30

### Fixed
- **An idle SFTP console was being disconnected. This is the main cause behind "connection lost".** The log makes it plain: the session was established, went **60 seconds with no traffic at all**, and was cut, without a single upload in between. Sixty seconds is the most common idle timeout default in reverse proxies. Our own nginx sets 3600s, but **a user may have their own reverse proxy in front** (Mode C explicitly supports that deployment), and that one is not ours to configure.
- **The cause was that SFTP lacked a heartbeat while every other console has one**: SSH, RDP and VNC exchange ping/pong and noVNC sends its own keepalive packet; only SFTP had none. The server now sends a tiny keepalive every 20 seconds. Server-side is more reliable than client-side (a backgrounded browser tab gets throttled), and it uses **application data rather than a WebSocket ping frame**: control frames get swallowed by some proxies, while data frames are always forwarded; forwarding data is what makes something a proxy.
- ⚠️ The BMC console still has **no** heartbeat (it is a pure relay and injected data would corrupt the SOL stream); it is recorded as a known gap in the test checklist.

### Verified
After connecting and sitting **idle for 90 seconds** (past the common 60-second timeout): four keepalives received, and the directory still lists normally afterwards.

## [0.5.228] - 2026-08-30

### Fixed
- **Dropping a folder into SFTP broke the whole batch**; this was the actual root cause behind the reports. Folders were detected with `File.size > 0`, but **macOS reports a folder's `size` as 256**, not 0, so the folder passed the filter, was uploaded as a file, and only failed when its contents were read. That is the "0/256 bytes written" in the error. The screen said "1 folder skipped" while nothing was actually skipped.
- Type is now decided per item by `webkitGetAsEntry().isFile`: **size must never be used to decide what something is**. The logic moved to `utils/dropFilter.ts` with unit tests, one of which is exactly the folder that reports 256 bytes.
- **Readability is checked before anything is sent**: one byte is read first, and an unreadable item is reported as "this item cannot be read (compress a folder first)" and skipped without touching the connection. Previously the server opened the file and waited out a 30-second timeout before cleaning up: a full minute of apparently frozen UI for two items.

### Verified
- All three protocol scenarios pass (two files in sequence / a command sent mid-transfer / a declared-but-unsent upload)
- **The customer's case reproduced in a real browser**: a folder reporting 256 bytes dropped alongside an 800 KB file. The folder is skipped, the file uploads with **byte-for-byte correct contents**, the connection stays up, and the backend raises no exception

## [0.5.227] - 2026-08-29

### Fixed
- **0.5.225 broke SFTP uploads: a regression of our own making, and we are sorry for it.** Rewriting the upload block in that release dropped the line that tells the client it may begin sending (`put_ready`): the server opened the file and went straight into the receive loop while the client waited for a permission that never came, so not a byte was sent and it timed out after 30 seconds. **The symptom was identical to the bug being fixed** ("connection lost"), which makes it easy to read as "still not fixed" rather than "newly broken". Restored, with a test of its own: something that breaks everything when one line goes missing, while looking like the old problem, deserves to be pinned down.

### Verified (actually exercised, not just read)
A protocol-level harness drove the WebSocket directly to reproduce the timing from the crash log, alongside a real browser uploading two files at once:
- Two files in sequence: both succeed at the right size, **md5 identical to the source**
- A command sent mid-transfer (the logged crash): connection survives, the interrupted command still runs, the partial file is removed
- A declared size with nothing sent: the session remains usable after the timeout
- **Zero** ASGI exceptions on the backend throughout

## [0.5.226] - 2026-08-29

### Fixed
- **SFTP uploads still dropped the whole connection; this time the server log gave the exact cause** (reported by a customer, following 0.5.224 and 0.5.225):

  ```
  File ".../sftp_console.py", line 332, in sftp_ws
  File ".../starlette/websockets.py", line 128, in receive_bytes
  KeyError: 'bytes'
  ```

  `receive_bytes()` raises `KeyError` when a **text** frame arrives, taking down the handler and closing the connection. A client sending its next command before finishing the data is entirely possible (it gives up on one file and moves to the next), and the server had no defence against it: the user sees "connection lost" when the real cause is simply **no guard against a frame of an unexpected type**. The timeouts added in the previous two releases could not help, because the next command arrives *immediately*, long before any timeout.
- The upload loop now inspects the frame type itself: data gets written, a command ends the upload (removing the partial file) and **is kept and executed as usual**, since an action the user asked for should not vanish silently.
- When the client gives up mid-file it now sends an explicit `put_abort` rather than moving on as if nothing happened.
- Two new tests pin the exact line from the log: the upload loop must not call `receive_bytes()` directly, and an interrupted command must not be swallowed.

## [0.5.225] - 2026-08-29

### Fixed
- **Dragging several files into SFTP let the first failure kill the whole connection** (reported by a customer, following 0.5.224). The ordering was wrong: the server announced "ready to receive" **before** opening the remote file. When the open failed (the remote answered "no such file or directory" on real hardware), the client had already begun sending binary frames, while the server reported the error and returned to reading a *text* message, and read those frames instead. The protocol desynchronised and the connection died, taking the remaining files with it as "connection lost". The file is now opened first and only then is the client invited to send; the main loop quietly discards stray frames so any leftover resynchronises by itself; and the client stops sending as soon as an error arrives.
- **Selecting a rack still demanded a location** (reported by a customer). A rack already belongs to a location; the rack dropdown even reads "Server room / R1". What can be derived should not be asked: an empty location now takes the rack's, and only a genuine contradiction between the two is blocked, because then one of them is wrong and choosing for the user would be a guess. The logic existed separately in the device list and the device page's edit dialog; it is now one shared function with unit tests, because when the same logic exists twice, usually only one copy gets fixed.

### Added
- **The version page's dependency lists were completed and are now guarded by a test.** Both lists (backend Python, frontend npm) are hand-written and go stale silently, and that page is exactly what an upgrade or audit checks "what is actually installed here" against, where a missing line raises no error and simply is not there. A test now compares them against what `pyproject.toml` / `package.json` actually declare: anything missing fails, and anything listed but undeclared needs a stated reason (only `pillow` today, a transitive dependency of the optional RDP package aardwolf). The frontend list went from 11 entries to 20.
- **A "logic between related fields" section in the test plan**, listing the places where one field determines another (rack→location, subnet→section, IP→subnet, VM→host, U position→rack height…) along with the rule: **before adding any "if A is set then B is required" check, ask whether B can be looked up from A**, then derive it if it can, block only if it cannot.

## [0.5.224] - 2026-08-29

### Fixed
- **An interrupted SFTP upload wedged the whole connection and dragged the service down with it** (reported by a customer). The receive loop kept waiting until the declared byte count arrived, with **no time limit at all**, so if the client stopped sending after `put_ready`, the server waited indefinitely. The reported symptoms line up exactly: a **zero-byte** file on the remote (opened, never written), "connection lost" on screen, then reconnect requests timing out and the whole interface unresponsive for a long stretch. Each frame now has a 30-second limit; on timeout **only that upload fails**, the empty file is removed, and the session stays usable. One interrupted upload should not force a reconnect.
- **The client sent files without backpressure**, pushing an entire file into the browser's WebSocket send buffer; when the server could not keep up, Chrome simply closed the connection (dragging two files reproduced it). It now waits whenever the buffer exceeds 4 MiB and checks the socket is still open before each send.
- **The same class of defect was swept for across the project.** Console WebSockets have two kinds of wait and only one should be bounded: **waiting for the next command while idle** (nobody typing at a terminal) is normal for hours and must not be timed out, while **waiting mid-protocol** must be. Besides the upload, that meant the first config message on all five consoles (SSH/SFTP/RDP/VNC/BMC) and the SSH host-key confirmation. All are now bounded (30s for config, 180s for the key prompt). Without it, opening N connections and staying silent holds N sets of resources, and from outside it just looks like "the system is slow".
- A guard test now catches a new console that forgets a handshake limit, and the opposite mistake of adding one to an idle loop.

## [0.5.223] - 2026-08-29

### Added
- **Every line on the topology map now says where it came from.** The map carries two different things: links someone recorded (cabling, wireless links, IP-to-device links) and links we derived (FDB saw a MAC on a port, ARP saw a subnet, a device name happens to be an IP). Drawing both the same way claims we are equally sure of them, which is not true. Each edge now carries `evidence` mapped to the evidence contract's tiers (**recorded by a person / reported by monitoring / learned passively / guessed from the name**), visible when you click a link.
- **`inferred` is deliberately not part of the evidence contract**: the contract is about *sources*, and "the name looks like an IP" is not a source but a guess, so it has to be distinguishable from the other three. Those lines are drawn dotted and faded.
- **A "recorded only" toggle** drops every derived link so you can see how much is actually known. The result is telling: in the access-layer view of this environment, **every** link is either learned or reported by monitoring; not one was recorded by a person.
- Evidence is not expressed through colour or dash pattern: those two dimensions already carry meaning (link type, and whether attachment is direct), and stacking a third on top would make none of them readable.

## [0.5.222] - 2026-08-29

### Added
- **The audit action filter now accepts several values, from a dropdown or typed by hand.** There are dozens of actions and the set grows with each feature (the previous release alone added `group_member_add` and `cert_agent_key_rotate`), so a dropdown alone cannot offer new ones and free text alone means memorising names. The options come from **the actions actually present in the log**, with counts; a hard-coded list would go stale in a way nobody notices: it just looks like the action is missing from the filter.
- **A settings screen for rack diagram embedding** (Admin → System settings): the master switch, viewing and copying the token, and regenerating it. Regeneration says plainly that every existing embed URL stops working immediately; otherwise someone's dashboard breaks with no obvious cause.

## [0.5.221] - 2026-08-29

### Added
- **Rack diagrams can be embedded in other systems.** One URL returns an SVG, so another dashboard (a LibreNMS widget, say) can show it with a plain `<img>`. That system will not run our frontend, so the drawing moved to the backend (same geometry and palette, see `services/rack_svg.py`).
- **SVG rather than PNG**: a PNG would need a rendering library (cairo or similar), which is a poor trade for a picture made of rectangles and text; an SVG displays in an `<img>` just as well and stays sharp when scaled.
- **An image rather than an iframe**, deliberately: this service sends `frame-ancestors 'none'` and `X-Frame-Options: DENY`, so iframes are blocked by design, and allowing specific origins in order to embed would open a clickjacking surface.

### Security design
- **Two switches must both be on**: embedding enabled with a token at system level, plus the **per-rack** toggle (off by default everywhere). A rack diagram reveals device names and positions, so one rack being worth sharing must not expose the rest.
- **A rack that does not exist and a rack that is not shared return exactly the same response**; otherwise the token would double as a way to enumerate racks.
- Token comparison is constant-time; the response carries `Content-Security-Policy: default-src 'none'` and `X-Content-Type-Options: nosniff` (SVG can carry scripts, and this image gets pasted onto someone else's page); device names are user input and are always escaped.
- The audit entry records whether embedding is enabled and whether the token was rotated, **never the token itself**: an audit log must not become a second copy of a key.

## [0.5.220] - 2026-08-29

### Added
- **A guard test for audit coverage.** It walks the whole API and reports endpoints whose HTTP method changes data, whose body really does write, and which record no audit entry. An exemption has to be written down with a reason, so "not recorded" becomes a decision someone made rather than something forgotten. What remains exempt is deliberate: high-frequency agent reports, per-user notification read state and UI preferences.

### Fixed
- **Group membership changes were not audited at all**, and groups carry permissions: adding someone to a group is a privilege change. The two endpoints did not even take `request`, so not only was the change unrecorded, **there was no way to tell who made it**. They now record `group_member_add` / `group_member_remove` with the group and the affected user.
- **Certificate agent key rotation and deletion were not audited** (`cert_agent_key_rotate` / `cert_agent_delete` / `cert_agent_update`); those change who can obtain a private key. Deletion records **before** it deletes: afterwards the name is gone and an audit entry holding only a UUID says nothing.
- **Certificate settings changes were not audited**, though uploads and deletions were (the entry contains no key material).
- **Manual ESXi and pfSense syncs were not audited**, while every other integration already recorded `sync`.

### Verified complete (this review)
- Console sessions: SSH / RDP / VNC / noVNC / BMC all record `session_open` and `session_close` including duration, and SSH additionally records host-key pinning. SFTP records the session plus **every upload, download, delete, rename and mkdir**, with path and byte count.
- Login success and failure, TOTP enable/disable, password change and OIDC/SAML logins are all recorded (in `services/auth.py`).

## [0.5.219] - 2026-08-29

### Fixed
- **A group of network devices was wrongly reported as "switch port unknown".** The old rule treated any port carrying more than four MACs as an uplink, but the port of an access point, a hypervisor or a downstream dumb switch legitimately carries dozens of MACs, and that is exactly where the device is plugged in. Six network devices on real hardware landed in the unknown area because of it, including an AP whose port carries 35 MACs (its wireless clients). The rule now uses **containment**: the port nearest a device holds a MAC set that is a proper subset of the outer port's (verified on real data: that AP's 35 MACs are a subset of the uplink's 144). Where no single innermost port exists, or a device was seen just once on one busy port, it still refuses to guess: one router on real hardware appears on four ports at once, and that genuinely cannot be resolved. The inference also now reads **every** sighting rather than only the switches currently drawn: the inner/outer comparison depends on the uplink's "sees everything" list, and that uplink switch is often excluded by the subnet filter because its management IP lives elsewhere. Visibility is applied when drawing, not when reasoning. Together the two changes took one subnet from 6 access-layer links to 18, and from 7 located devices to 19.

### Changed
- **The topology map now opens on "subnets only"**, the view that makes sense without configuration; switch to a physical view when you want one.
- The "virtual machines" legend entry moved next to "servers / other".

## [0.5.218] - 2026-08-28

### Fixed
- **A row of devices in the mixed view looked disconnected.** They are in fact "in this subnet, but we cannot tell which switch port they are on"; drawing them inside the box was not enough to say that, so they read as devices with no links at all. They now go into a clearly labelled sub-area, "same subnet · switch port unknown". A box with no access-layer information at all is not split, since there would be nothing to contrast against.
- **The subnet picker grew taller with every subnet selected, pushing the layout around.** Tags now collapse onto a single line with a +N overflow, so the toolbar stays 34px tall no matter how many are selected.

## [0.5.217] - 2026-08-28

### Added
- **The topology map can show which physical host each virtual machine runs on (off by default, tick to enable).** Real hardware has 149 VMs that all know their node, yet not one of them appeared on the map; in a virtualisation-heavy room that is most of the estate missing. With "virtual machines" ticked, each VM sits directly beneath its host and shares the host's subnet box. It stays **off by default** because it adds hundreds of nodes at once and drowns the picture.
- Matching uses `virtual_machines.node` (the PVE node or ESXi host name) against device names, case-insensitively; all five node names match on real hardware. Note that `virtual_machines.device_id` is the device the **VM itself** maps to, not its physical host; the two are easy to confuse. A VM already mapped to a device reuses that node instead of drawing the same machine twice.
- A VM whose host cannot be identified, or whose node name matches several devices, is **not drawn**: this feature answers "what does it run on", and a dot connected to nothing cannot answer that; it is only noise (same reasoning as the access-layer-only view).

## [0.5.216] - 2026-08-28

### Fixed
- **Exporting to another machine and importing lost a large share of the devices** (reported by a customer). The import writes table by table in foreign-key dependency order, but six columns point *forward*: `devices.primary_ip_id` references `ip_addresses`, which is imported later, while `sections.parent_id`, `subnets.master_subnet_id`, `device_ports.peer_port_id`, `contact_groups.parent_id` and `tenant_groups.parent_id` reference another row of the same table. When such a row is written its target does not exist yet, the foreign key fails and **the whole row is dropped**. It looks like random data loss but is perfectly regular: every device with a primary IP fails and every device without one survives; nested sections and subnets lose whichever rows happen to be exported before their parent. Those columns are now left empty on the first pass and filled in once every table is written, so no link is lost.
- **The import screen reported a green "import complete" even when whole rows had failed.** The failure count was only a column in a table, which is why the customer discovered the missing devices afterwards rather than at import time. A run with failures now shows a warning that says those rows are missing entirely, and lists the first few reasons, the part that can actually be reported back to us.
- **Clicking another machine in a rack left "ports / cabling" showing the first one** (reported against 0.5.208). Clicking a device in the rack diagram navigates to `/devices/:id`; when only the route parameter changes Vue reuses the component and simply passes a new prop, while that panel only loaded its data on mount. The power-port panel and the uptime bar beside it already watched the prop; this one did not.

## [0.5.215] - 2026-08-28

### Changed
- **The mixed view now really merges the subnet with its switches instead of drawing two sets of links.** The previous version placed the subnet below the switch, but every host still got two lines (one to the switch, one to the subnet), saying the same thing twice and tangling the picture. The subnet is now a **box**, its members (switches included) are drawn inside it, and "belongs to this network" is expressed by containment rather than by an edge.
- **A subnet with several switches is not pinned to one of them**: a subnet is a broadcast domain and spans the core and access switches by nature, so the box holds them all with the backbone drawn inside it. Members whose port is unknown sit centred beneath the whole box rather than under the first switch; nothing says which switch they belong to, and picking one would be invention.
- **A device that spans several subnets goes in no box at all** (routers, firewalls): putting it in one would claim it belongs only there. Those keep their L3 edges, which makes the cross-subnet devices easy to spot.
- **A switch with FDB data but no IP record of its own** is folded into the box its hosts all belong to; otherwise the switch sits outside while its hosts sit inside and a bundle of edges crosses the boundary, which reads as broken. A switch whose hosts span several boxes stays outside, because that is a genuinely cross-subnet switch.

### Fixed
- **The map opened far too spread out, shrinking everything past legibility.** Three measured causes: arranging hosts in a semicircle widened the graph as the host count grew (8 hosts × 3 switches measured 2274px wide at 0.53 zoom), and a grid above each switch brought it to 1477px at 0.73; a fixed six-per-row member grid turned a large subnet into a tall strip, so it now scales by square root; and nodes outside the box were scattered far away, where a single distant node is enough to shrink the whole picture.
- **The backbone line ran straight through a third switch, with its port label landing on that switch's name.** Switches are now ordered so that backbone-connected ones sit side by side, and the port label is lifted clear of the line.

## [0.5.214] - 2026-08-28

### Added
- **The topology map now offers a choice of view.** The same data supports two quite different readings, and forcing them into one picture serves neither: the subnet view answers "who is on which network", the access-layer view answers "which switch port is this host plugged into". Pick one from the toolbar: **Automatic (mixed) / Centred on switches / Access layer only (FDB) / Subnets only**.
- **The switch-centred layout computes its own coordinates rather than swapping in another force layout.** A force layout has no notion of up and down, so it cannot express "switches in the middle, hosts above, the subnet hanging underneath its switch". Spacing scales with the actual node count: fixed wide spacing makes a small graph shrink until the labels are unreadable, and a layout being structurally right is not the same as being legible.
- **What "automatic" decides**: with access-layer data in range, centre on the switches and hang the subnet below; without it, fall back to the subnet-centred layout. Choosing "centred on switches" with no FDB data falls back the same way, since there is no point forcing a layout around a centre that does not exist.

### Changed
- **The access layer (FDB) checkbox now starts unticked.** It pulls every endpoint into the picture, which gets dense immediately. Tick it when you want it, or pick the "access layer only" view, which turns it on by itself.
- **"Access layer only" no longer draws devices whose position is unknown.** In that view a device with no FDB data has nothing to say, and drawing it as a dot floating off to one side is just noise: on real hardware only about 10 of 105 devices have FDB data, and the other 95 orphan dots wreck the zoom and read as "not connected".

## [0.5.213] - 2026-08-27

### Added
- **The topology map can finally draw the access layer: which host hangs off which switch port.** The data was always there (the FDB table LibreNMS collects), but the map never read it; the file header even claimed the graph was built from "device + cabling + FDB" while not a single line of it was used. Two kinds of link are now derived: **access** (host to switch port) and **switch backbone** (how the switches connect to each other). With no data source for LLDP/CDP, FDB is the only thing that can draw a backbone automatically.
- **Inference must not draw what merely looks right**, so three guards apply: (1) a port carrying more MACs than the threshold is an uplink, and the hosts on it are not drawn as plugged into it; (2) a MAC that maps to more than one device (overlapping subnets) is not guessed; (3) two switches count as directly connected only when each sees the other on one of its ports **and** the MAC sets behind those two ports are disjoint; without that third condition an A-B-C chain gets drawn as A-C too.
- **"Directly attached" and "behind this port" are drawn differently**: a solid line only when the port carries exactly one MAC, dashed when several hosts sit behind it (a dumb switch downstream, or a hypervisor carrying its guests' MACs). The difference is visible on real data: the same hosts appear on two switches at once, and drawing both as direct would be a lie.

### Fixed
- **An unreachable integration only ever said `transport: ConnectError`, which says nothing.** A name that does not resolve, a refused connection, an unroutable host and a certificate that fails verification all looked identical on screen, leaving whoever handles it to guess. The reason was right there underneath (`socket.gaierror` / `ssl.SSLCertVerificationError`) and was being thrown away. It now reads like `transport: ConnectError: [SSL: CERTIFICATE_VERIFY_FAILED] ...`. All **21** sites shared the same shape (LibreNMS, Zabbix, Wazuh, pfSense, FortiGate, Proxmox, DNS, SSO, AI) and were fixed together. Certificate problems are the ones most often mistaken for "the network is down", because httpx wraps handshake-time SSL errors in `ConnectError` too and the outer message can be empty; one test pins exactly that case.
- **vCenter sync aborted on long network names** (issue #25, reported by eric700928). An NSX-T generated portgroup name embeds a UUID (78 chars in the report) and overflowed the 64-char limit on `vm_interfaces.bridge`, ending the whole run with `StringDataRightTruncationError`. The length of a name from a third-party platform is not ours to bound, so `bridge` and `node` (an ESXi host FQDN, up to 253 chars) are now unbounded (migration 0129).

### Changed
- **Pre-release security checks now cover the scan agent and the maintenance scripts.** The bandit rules (ruff's `S` set) only ever looked at `backend/app/`, while the code under `agent/` and `scripts/`, which runs with real privileges on customer machines, sat outside every check. The one finding worth fixing was fixed: the agent validates the scheme of `JT_IPAM_URL` at startup, so a mistyped value can no longer turn every poll into a local file read with the agent key attached.

## [0.5.212] - 2026-08-27

### Fixed
- **The SFTP permission column now uses a monospace font.** Every position in `drwxr-xr-x` carries a fixed meaning, and in a proportional font the rows do not line up, so checking one bit means counting characters. This also fixes the root cause: the cell already carried `class="mono"`, but that rule only targeted `input`, and nodes produced by a render function live inside the table and never receive this component's scoped-style attribute, so it looked set and did nothing. It is an inline style now.
- **The "all subnets" dropdown on the devices page was shorter than the search box and buttons beside it.** It carried `size="small"` while its neighbours used the default size. A toolbar measurement sweep across 18 list pages in a real browser confirmed this was the only remaining row with mixed heights.

## [0.5.211] - 2026-08-27

### Fixed
- **The SFTP sort-mode dropdown was shorter than the controls beside it and had no icon.** Leaving the size unset fell back to the component default, so one toolbar row had two heights; every other control there is "icon + label", and this one was bare text. It now matches the neighbouring filter box and carries a sort icon ahead of the selected value (the chevron stays, since it is what says "this opens"). Verified by measuring all seven toolbar controls in a real browser: one height, one top edge.

## [0.5.210] - 2026-08-27

### Added
- **The SFTP file list can sort folders first or mix them with files.** Both conventions are defensible (a file manager groups directories, `ls` does not), so it is a preference rather than a decision made for you, stored per user so it follows you to another device. Folders-first holds in **both** sort directions: putting the grouping inside the comparison would send directories to the bottom the moment you switch to descending, which is what nobody wants. That required taking sorting off the table component, since its sorter never sees the direction.

### Changed
- Sorting by size or modified time now also honours the chosen mode; previously only the name column grouped directories, so switching columns silently changed the grouping rule.

## [0.5.209] - 2026-08-27

### Added
- **Evidence sources now carry a contract.** Every source declares two things in one place: which tier it belongs to (asserted / probed / monitored / learned) and (the part that matters) whether its evidence expires. `learned` sources such as ARP, FDB, DNS and virtualization config never expire, because they answer "this mapping was learned at some point", not "the machine is alive now". A guard test refuses any source used by a precedence list that has not declared this, so adding an integration means answering the question rather than discovering the answer in production. This is the structural version of the fix that a powered-off VM showing 52 days of green forced: the knowledge used to live in string comparisons scattered across modules, where a new source silently fell into whichever branch matched first.
- **Cooldown after an address is released.** DNS records and caches, firewall rules, ACLs, certificate SANs and monitoring configuration all keep pointing at an address after it is freed; handing it to another machine immediately produces the hardest kind of fault to diagnose. Releasing an address now starts a 30-day cooldown (configurable, 0 disables): the allocator will not offer it, creating it by hand is refused with the previous hostname and the end date, and an admin can clear an individual address early, which is recorded rather than erased. The record deliberately lives in its own table, because releasing an address in practice means deleting it, and a note attached to the deleted row would vanish with it.
- **Event rules: event → conditions → actions.** Webhooks could only subscribe to event names; what people actually want is conditional ("notify when a new subnet's description contains Production", "alert on an unauthorised IP only inside server ranges"). Conditions are structured fields, evaluated by code that executes nothing: rules are user input, so an expression language would be an injection path carrying a database session. Regular expressions are deliberately absent for the same class of reason (a single rule could stall every dispatch). A malformed rule is flagged and skipped rather than silently doing nothing, and a dry-run reports what would match without sending anything.
- IP lifecycle states `deprecated` and `quarantine`, alongside the existing vocabulary.

### Changed
- The five source-precedence modules (hostname, MAC, OS, device name, model; 661 lines) shared one shape: settings key, source list, default order, disabled list, 60-second cache, sanitising. That half is now one module; each of the five keeps only what is genuinely its own. Copies are how a new source ends up registered in four places and forgotten in the fifth.

### Fixed
- The test fixture that clears precedence caches between tests had been silently doing nothing since the caches moved, because it looked them up with a tolerant `getattr(..., "_cache", {})`. Tests then leaked settings into each other, which shows up as "one test occasionally fails". It now imports the shared cache directly, so a future move breaks loudly instead.
- Structured API error details are rendered as their message instead of `[object Object]`.

## [0.5.208] - 2026-08-26

### Fixed
- **The system settings groups stopped looking like cards.** Splitting them out of the single large card left them as bordered panels with no card surface of their own, sitting directly on the page background, so the background read as wrong and the cards read as missing. Each group is a real card component again, which is also what keeps its surface identical to every other page in both light and dark themes; hand-rolled card styling was what drifted in the first place.

## [0.5.207] - 2026-08-26

### Added
- **Which evidence counts as "online" is now a setting**, under Admin → System settings → Liveness. Scan-agent probes and LibreNMS device status are on; **ARP is off by default**, because it proves a MAC-to-IP binding was learned rather than that the machine is alive, and the LibreNMS ARP API returns no timestamp at all, so a reporting device's cache can keep a powered-off machine looking online indefinitely. The recompute also honours the configured threshold, which it previously ignored: the backend had 30 minutes hard-coded while the settings page invited you to change it.
- **The AI chat can be stopped mid-answer.** Aborting the request closes the connection, which is also what makes the LLM server stop generating rather than finishing an answer nobody will read.

- **The chat says what it is doing.** A spinner with nothing next to it is indistinguishable from a hang, and these answers can take tens of seconds. The status line now names the phase (connecting, the model thinking and how much it has produced, which tool is running, working through the results, writing the answer), along with the query round and the elapsed seconds. Tool names are turned into readable text rather than shown as identifiers. The thinking phase is reported by the backend, which previously produced no events at all while the model was thinking, so the screen sat blank for the longest part of the wait.

### Fixed
- **A stale transition no longer paints green once any evidence source reappears.** Demoting ARP was not enough on its own: the moment the machine in question was powered on and the scan agent saw it, "this address has a source" became true again and the inference happily refilled the fifty days it had been off. Carrying a state forward now requires the source that state *claims* to still exist: a transition recorded as "online (librenms)" is not carried on an address that has no LibreNMS evidence at all.
- **The AI chat could answer with a blank message.** When the model returned neither text nor a tool call (usually because it emitted only thinking, or hit the output limit), the empty string was passed straight through and the UI showed "(no answer)", which says nothing about what went wrong. It now asks the model once more for a direct answer, and if that also comes back empty, says which of the two it was and what to adjust.

### Changed
- The system settings page is no longer one long card inside another card: each group is its own card, the content uses the full width, and field grids go to three columns on wide screens.

## [0.5.206] - 2026-08-25

### Fixed
- **A machine that had been powered off for weeks could show 52 days of unbroken availability.** Two things combined to produce that. First, ARP was treated as timestamped evidence of life: LibreNMS's ARP API returns no timestamp at all, so an address appearing in the dump was stamped with the sync clock, meaning a lingering entry in some device's ARP cache (in the case that surfaced this, a wireless AP's) reads as "seen just now" forever, whether or not the host is running. On the affected system every such address carried a byte-identical timestamp, which is the sync run, not an observation. Second, the availability bar carries the last known state forward, so one transition recorded in July painted every day since green without a single observation behind it.
- ARP evidence is now stored separately from LibreNMS device status and forms its own status tier, `online (arp)`. It still counts as online (a learned MAC-to-IP binding is real information), but it is labelled as such in the UI, with a note explaining why it can outlive the machine, and **the availability bar no longer paints days backed only by ARP**. Carrying a state forward now requires an evidence source that actually expires (scan agent probes, LibreNMS device status): without one, days with no observation of their own are grey rather than green.
- The upgrade separates existing data by fingerprint: where `last_seen_librenms` exactly equals the address's ARP timestamp, the value came from the ARP path and is moved accordingly, so the distinction applies to history rather than only to new syncs.

- **Availability is now recorded daily rather than inferred.** Even with ARP demoted, one stale transition could still paint weeks of green the moment any evidence source reappeared, because "does this address have a source that expires" is a per-address flag, not a per-day fact. The exact case that surfaced this: the VM was started, the scan agent saw it within minutes, and the inference would happily fill in the fifty days it had been off. Every sync round now writes down what was actually observed for each address that day, and days with a record use it instead of inference. Days whose only observation was ARP are grey. The bar states where the recorded era begins, so inference and observation are not silently mixed.

### Added
- New anomaly category, **ARP-only liveness**: addresses that look online where ARP is the only source saying so, and neither the scan agent nor LibreNMS device status has ever seen them. That is precisely the class of record that misleads, and it is worth reviewing rather than trusting.

## [0.5.205] - 2026-08-25

### Added
- **URLs in the browser terminals are now clickable**, including the ones a TUI has broken across several lines. This is the case that matters: an app like Claude Code measures the width itself and writes the URL out one row at a time, so those rows are separate logical lines in the buffer. The standard link addon only follows the terminal's own wrapping and would leave such a URL unlinked, and selecting it by hand produces text with line breaks inside that pastes as a broken address. Both kinds of wrapping are now rejoined: the terminal's own (via the wrap flag) and the app's (a row filled to the last column followed by a row starting at column 0 with URL characters). The rejoin is a heuristic, so it is deliberately narrow: it stops at whitespace, refuses when what follows the run on the final row is more text rather than padding, and **hovering shows the full assembled target in a bar at the bottom of the terminal**, so what a click will open is visible before clicking. Only http and https are opened, in a new tab with no opener, since the text comes from the remote host. Applies to the SSH, BMC serial and Proxmox console screens.
- Selecting such a broken URL and copying it now puts the rejoined address on the clipboard. This only happens when removing the line breaks yields exactly one URL with no other whitespace; in every other case what you copied is what you get, untouched.

### Fixed
- The terminals now use the Unicode 11 width tables. Getting the width of box-drawing characters and emoji wrong shifts a TUI's layout, and a shifted layout means what you see no longer lines up with the buffer underneath, which is what makes selections come out misaligned in the first place.

## [0.5.204] - 2026-08-24

### Added
- **The audit chain is now verified on a schedule and anchored outside the database.** A hash chain proves that no record was altered or removed *in the middle*, but it cannot detect the one thing an intruder would actually do, which is cut off the tail: delete the last N entries and what remains still verifies perfectly. Every sync round now verifies the chain and appends the newest entry's hash, id and total count to `/var/lib/jt-ipam/audit-anchors.jsonl` and to the system journal. If that anchored entry later goes missing, or its hash changed, or the total shrank, every admin gets an alert naming which of the three it was. Verification is incremental: it resumes from the last anchor instead of rewalking the whole chain each round. A test in the suite deliberately truncates the tail and asserts that chain verification alone still reports "intact", so the reason this module exists cannot be quietly forgotten.
- **Zabbix integration.** Zabbix is the most widely deployed open-source NMS in Taiwan, and it is positioned here as a *complement* to LibreNMS rather than a replacement: it contributes host-to-IP mapping, availability as a third evidence source for effective status, maintenance windows (so a host under maintenance is not reported as missing), and a monitoring coverage gap (addresses IPAM knows the hostname of that Zabbix is not watching). What it deliberately does not claim is ARP/FDB, which is not in Zabbix's built-in data and would need per-site custom SNMP items. Authentication accepts an API token (5.4+) or username/password; both are encrypted at rest. As with every other integration it only stamps addresses that already exist, honours the subnet scope, and takes `limit(1)` so overlapping ranges cannot abort a whole sync round.

### Security
- **Dependency vulnerabilities: 85 down to 1.** A `pip-audit` sweep found advisories across 17 packages, several of them in the request path: starlette, aiohttp, python-multipart, pyjwt, cryptography, pillow. All are upgraded and the minimum versions are pinned in `pyproject.toml` so a fresh install cannot land back on a vulnerable release. The one remaining finding is `diskcache` 5.6.3, pulled in transitively, for which no fixed version exists upstream yet.

### Fixed
- **Audit records written in the same transaction all chained off the same predecessor.** `append_audit` added its row without flushing, and production sessions run with `autoflush=False`, so a bulk operation writing several audit rows at once had every one of them point at the same previous hash: a real break in the chain, caused by the writer rather than by anyone tampering. Production had accumulated 28 such breaks, 26 of them from NAT bulk-delete. Each entry now chains off the previous one within the same transaction. The reason no test caught this is worth stating: the test fixture used SQLAlchemy's default `autoflush=True`, which hid the bug; the regression test now disables it to match production.
- `JT_IPAM_AUDIT_CHAIN_BASELINE_ID` sets the id verification starts from. Existing deployments carry records that can no longer be made verifiable (breaks left by the writer bug above, and on our own production a batch of 1,953 rows from one day of end-to-end test traffic written by a different build), and rewriting historical hashes to "repair" them would itself be tampering. The baseline draws an explicit line instead, and every round logs a warning naming what is not covered, so the compromise stays visible rather than silent.

## [0.5.203] - 2026-08-24

### Added
- **The PVE firewall tab now shows the rules themselves.** It stated a verdict for each guest without showing the evidence, which left the obvious question (which rules does this VM actually have?) unanswerable. Every row expands to its rules, with the guest's own rules first and the datacenter and node rules that also apply to it below, each tagged with the level it comes from. Disabled rules are marked, and a rule referencing a security group, IPSet or alias can be expanded to its contents, since a bare name says nothing. A rule-count column makes it visible at a glance which guests carry rules at all.

### Changed
- zh-TW wording: 姿態 → 防護狀態 (posture). The former was a literal translation and is not how this is said in Taiwan.

## [0.5.202] - 2026-08-24

### Changed
- The PVE firewall tab now carries the same table furniture as every other tab: a filter box, a posture dropdown (each option showing its own count), sortable column headers, the column picker and export. The posture summary is four equal-width cards ordered by risk instead of a row of differently sized tags, and the selected one is outlined.

## [0.5.201] - 2026-08-24

### Added
- **Proxmox VE firewall sync.** The east-west layer was invisible: rules written at the datacenter, node and guest levels never reached IPAM, so a guest could look unmanaged while carrying a dozen rules, or look protected while none of them applied. Three things decide whether a rule does anything, and all three are now read and combined into a single posture: the cluster switch, the guest switch, and **the per-NIC `firewall=1` flag that lives in the VM config rather than the firewall API**. The default policy matters more than the rules themselves (`policy_in=ACCEPT` with no rules is wide open while the rule list looks clean), so `policy_in`/`policy_out` are stored alongside a flag recording whether the value was set explicitly or inherited, because the API omits keys that were never configured. Security groups, IPSets and aliases are expanded (a guest-level name shadows a datacenter one), and unresolvable members are marked rather than dropped, since dropping them makes a rule look narrower than it is. Deliberately **not** merged into the exposed-services list: a PVE rule is not a statement about reachability from outside.
- **Network probes can be run from a scan agent instead of the server.** The server only sees its own segment; verifying reachability inside a customer site has to happen from that segment. Because agents only ever dial out, the request travels as a queued job: created by the backend, long-polled by the agent, executed locally, reported back. Admins only, since it means sending packets inside someone's network: the probe kind is restricted to ping/tcp/traceroute/rdns, targets must parse as an address or hostname, arguments are passed as a list and never through a shell, jobs expire if no agent takes them, and **the agent re-validates everything itself** rather than trusting what the backend handed it.

## [0.5.200] - 2026-08-23

### Fixed
- **A single IP could accumulate thousands of change-log entries that said nothing.** Two Wazuh agents registered against the same address overwrote each other's hostname on every sync round, writing a pair of entries each time: one address had flipped 620 times in ten days, and the worst had 1,838 entries, every one of them the same event, burying the edits a person actually made. The Wazuh sync now converges on one name the way the Proxmox sync already did, and `log_change` drops an entry whose immediate predecessor is its exact inverse within ten minutes: the net effect is zero, so recording both is noise.
- **The switch-port field could not be edited correctly.** The stored format is `switch / port` while the read view renders it as `switch@port`, so anyone copying what they saw typed a value that displayed as one unbroken string. Editing now has separate switch and port inputs with the `@` shown between them, and the canonical format is composed on save. The field also states plainly that the LibreNMS sync maintains it and will overwrite a value entered by hand.

### Changed
- The change-log section on the IP detail page shows the total in its header, filters by event type and source (each option carrying its own count), and pages through 50 at a time instead of an endless "load more". The endpoint returns the total and the available filter values rather than a bare array: with 1,838 entries behind it, a single page of results tells the reader nothing about how much they are not seeing.

## [0.5.199] - 2026-08-22

### Changed
- **FortiGate is no longer marked Beta.** The label existed because the integration was written against the documentation with no hardware to test on; it has since been validated against a customer's live device, which surfaced and fixed the two defects that mattered: one unreadable endpoint aborting the whole instance sync (0.5.195) and FortiOS concatenating several JSON documents in one response (0.5.196). The tolerant parsing and per-section isolation stay; only the label is gone.

## [0.5.198] - 2026-08-20

### Changed
- The two tabs on the exposed-services page now read as a pair ("By IP" and "By FQDN") instead of one being a phrase and the other a bare acronym.

## [0.5.197] - 2026-08-20

### Added
- **The exposed-services list can now be read by name**: a second tab shows the same data grouped by FQDN, resolved through the DNS records IPAM already syncs (A/AAAA directly, CNAME aliases followed up to three hops). No live resolution is performed: an audit list has to be reproducible, not dependent on what external DNS happens to answer today. Exposures with no DNS name are counted and named in the FQDN view so one page is never mistaken for the whole picture, and the IP view stays the default.
- `list_attack_surface` for AI chat / MCP: ask by name (`fqdn=`) or by address (`ip=`), with `scope`, a true `count`, and unregistered targets flagged.

### Fixed
- **Clicking a notification did nothing.** The bell only marked it read (it never navigated), and the link the backend wrote pointed at `/anomalies` while the route is `/anomaly`, so even the notification page led nowhere. Notifications now carry the category (`/anomaly?tab=fw_rule_rot`) and the page opens on that tab. Only same-site paths are followed.
- **AI could not be asked about firewall rule decay**: `fw_rule_rot` was missing from the anomaly tool's detector list, so that category was invisible to the assistant.
- IP values in the anomaly tables link to the IP detail page (by id when known, otherwise a search), instead of leaving the reader to copy the address elsewhere.

### Changed
- The anomaly summary numbers are now bordered cards with a background: a non-zero count turns amber, and clicking a card switches to that category.

## [0.5.196] - 2026-08-20

### Fixed
- **FortiGate DHCP lease sync failed against a real device even though the response was valid JSON.** FortiOS returns several JSON documents concatenated (one per VDOM/scope, with no array wrapping them), so the standard parser stopped at the second document with "Extra data" and the whole DHCP section was reported as "response is not JSON", while the body plainly started with `{"http_method":"GET","results":[...]}`. Responses are now parsed document by document and their `results` merged. Genuinely non-JSON bodies (a login page, for instance) still raise, and the error now includes the parser's own message, which is what distinguishes "several documents" from "not JSON at all".

## [0.5.195] - 2026-08-19

### Fixed
- **One unreadable FortiGate endpoint aborted the whole instance sync.** A real device reported "9 of 10 endpoints readable" (its firmware answers the DHCP-lease monitor path with the web UI instead of JSON). Because `sync_instance` ran the sections in one unguarded sequence, that single failure stopped ARP, policies, NAT and address objects from syncing at all, while the UI showed one error line; it looked like the whole firewall was broken. Each section is now isolated; partial failures are recorded in `last_error` (never silently reported as success) and the remaining sections still sync.

### Changed
- TEST_CHECKLIST gained section 7c (integration sync resilience): section isolation, partial failure recorded in `last_error`, no chain abort across instances, errors that carry evidence, and a connection test that reflects what the sync actually gets.
- The FortiGate connection test no longer reports a bare "response is not JSON". It now carries the evidence (`content-type` and the first 120 characters) and, when the body is HTML, says plainly that the firewall answered with a web page rather than the API, which means either that firmware has no such endpoint or the API administrator cannot read that resource.

## [0.5.194] - 2026-08-19

### Fixed
- **LDAP login returned HTTP 500 when the LDAP user shared an email with an existing local account** (user report: the same person legitimately has both a local and an LDAP account). The LDAP bind actually succeeded; the request then died committing the auto-provisioned user because `users.email` was a unique key. Email is contact information, not identity (identity is the username), so migration 0120 drops the unique index (a plain index remains). Both login lookups are now realm-scoped (`email` matches only LDAP accounts in the LDAP realm and only non-LDAP accounts in the local realm) and no longer use `scalar_one_or_none()`, so duplicate emails cannot turn into a `MultipleResultsFound` 500 either.
- **AI answers about one subnet were computed from whole-system data**: `wazuh_missing_agents` had no subnet parameter at all, so "which hosts in 198.51.100.0/24 have no Wazuh agent" returned every IP in the system (the reply mixed in 203.0.113.x and 192.0.2.x). The same gap existed in `list_wazuh_agents`, `list_fdb`, `list_dhcp_ranges`, `list_vms` and `list_nat`; `list_power`/`list_racks`/`list_devices` could not be limited to a rack or location. All of them now take a scope parameter (resolved through the existing visibility check) and return `scope`, and their tool descriptions require the model to pass it and to state the coverage.
- **Silent truncation in AI list tools**: several tools returned only a `limit`-clipped array with no total, so the model presented one page as the complete answer. They now return `count` (total in scope) alongside `returned`.
- `list_devices` and `list_racks` applied the visibility filter *after* the SQL `LIMIT`, so a restricted account received fewer rows than requested and the count included rows it could not see. Visibility is now part of the query.

### Changed
- `list_ip_requests` now derives its scope from the shared permission tier (global read) instead of its own ad-hoc admin check, so tools and REST endpoints cannot drift apart.
- The user list no longer repeats the realm suffix in the account column (`jason@ldap` shows as `jason`); the authentication-method column already carries it. The stored username is unchanged and the full value stays in the tooltip.

## [0.5.193] - 2026-08-18

### Added
- **AI chat panel can now be expanded**: a maximize toggle sits to the right of the close (X) button; it grows the panel leftward and upward to roughly two-thirds of the screen (anchored bottom-right), with the message area filling the extra height and the input pinned to the bottom. Click again to restore the original size.

## [0.5.192] - 2026-08-17

### Fixed
- **The hostname-sources row and FDB tag on the IP detail page appeared only sometimes** (user report: "am I doing it wrong or is it the system?"; it was the system): the watch that loads them lacked `immediate`, so opening the modal from the list (show toggling) triggered it while a direct URL / refresh (inline mode, where the condition holds from mount and never changes) never did; the whole row vanished. `immediate: true` makes both entry paths identical.

### Changed
- zh-TW wording: 腐化 → 劣化 (firewall rule/alias decay).

## [0.5.191] - 2026-08-17

### Changed
- **The unauthorized-IP AI triage now matches the firewall rule-change AI analysis** (the same feedback batch resurfaced on this page): the column header reads "Actions" instead of duplicating the button label; the result modal renders markdown via the site-wide escape-then-tag renderer instead of leaking literal asterisks; the analysis runs in the background with a View-result button growing on completion (results stay on the page, multiple rows concurrently); the button gained an icon; the modal names the model that produced the reading; and the report can be downloaded as .md/.txt with an IP/model/disclaimer header.

## [0.5.190] - 2026-08-17

### Fixed
- **In direct TLS mode (uvicorn terminating TLS) nothing ever served the UI** (customer report: doctor all green, `/healthz` fine, but `https://host:8443/` answered `{"detail":"Not Found"}`): direct mode skips nginx (correctly), but the backend never mounted the frontend either. The backend now serves the SPA from `frontend/dist` when present, mounted last so API routes always win: `/` and client-side routes (refresh / direct URL) return index.html, API 404s stay JSON, index.html and version.json carry no-cache (the update detector depends on it) while hashed assets remain cacheable. nginx mode is unaffected: nginx serves dist itself and this mount is never reached.
- **doctor gained an end-to-end UI check for direct mode**: `/healthz` alone stayed green through the failure above; the API was alive while the page users load was a 404. It now requires `https://127.0.0.1:<port>/` to answer HTML, and points at the upgrade when it does not.

## [0.5.189] - 2026-08-16

### Fixed (exposed-services list, a round of hands-on feedback)
- **Clicking column headers did nothing**: column keys pointed at nested fields (`identity.ip` etc.) so the sorters compared undefineds. Rows are now flattened before entering the table; sort/filter/search all read flat fields, and ports sort numerically (sources mix ints and strings).
- **The table overflowed the card's right edge**: added `scroll-x` sized to the visible columns, so it scrolls inside the card.
- **Mixed-case protocols** (tcp vs TCP): normalized to uppercase in cells and the filter dropdown.
- **"? unregistered" noise**: NAT entries with neither a target IP nor a port (OPNsense's Anti-Lockout auto-rule and kin) are undeterminable and no longer listed; dangling forwards that do carry a port stay, because those are red flags.
- **Chinese text leaking into the English UI**: the scope note was hardcoded Chinese from the backend; it now comes from frontend i18n.

### Added
- **The "paired" tag is now interactive**: hovering pops a card listing the counterpart entries (type + name + firewall); previously the tag never said what it paired with.

### Changed (version info page)
- The backend package list now covers the **complete runtime dependency set** (34 packages; pyjwt/pyotp/ldap3/dnspython/pywinrm/python3-saml/pgvector/geoip2/celery/… were missing).
- Optional dependencies list **traceroute (preferred) and tracepath (fallback) separately**, and the "installed" badge no longer wraps.
- "Go to Releases" now links to the GitHub project: releases aren't published for this repo, so the old link landed on an empty page.

## [0.5.188] - 2026-08-16

### Changed
- Rule-change table width rebalanced: the actions column shrinks to just fit its buttons (185px, the View-result button wraps when it appears) so spare width goes to the diff column; the meaningless sort arrow on the actions column is gone too.

## [0.5.187] - 2026-08-15

### Added
- **AI analysis results can be downloaded as a report**: the modal footer gained "Download .md" / "Download .txt", where .md keeps the original markdown (with a header carrying the firewall, time, model and disclaimer), .txt strips the markup (BOM-prefixed so Chinese text opens cleanly); zero-dependency, generated entirely in the browser.
- **The analysis shows which model produced it**: the backend returns the configured chat model with the result, shown in the modal footer and embedded in downloaded reports; different models carry different credibility, so it is part of the finding.

### Changed
- The acknowledge and AI columns merged into a single "Actions" column: column headers identical to the button labels inside read like an accidental duplicate (user feedback); once acknowledged, the button gives way to the status text in place.

## [0.5.186] - 2026-08-15

### Changed
- **Firewall rule-change AI analysis now runs in the background**: the LLM takes tens of seconds and the UI used to block on it; the button now returns immediately (multiple rows can analyze concurrently) and a "View result" button grows next to it when done, and the result stays on the page for re-reading.
- **The AI result modal renders markdown** via the site-wide zero-dependency renderer (escape-then-tag, no injection surface); model output like `**bold**` previously showed its literal asterisks.
- zh-TW: 認領 → 認可 (more formal); the acknowledge and AI-analysis buttons gained icons, matching the site convention.

## [0.5.185] - 2026-08-15

### License change
- **Relicensed from Apache-2.0 to AGPL-3.0-or-later as of this release.** The AGPL's network copyleft means anyone offering a modified jt-ipam as a service must publish their source: closed-source derivatives are no longer possible. Releases up to and including v0.5.184 remain available under their original Apache-2.0 terms.

### Added
- **Path trace, three hands-on fixes**: (1) it now prefers `traceroute -I` (ICMP), because tracepath probes with high UDP ports that are commonly filtered late in the path, so the same route a terminal reached in 9 hops went silent after hop 7 for us and never "arrived"; ICMP almost always gets through (10 hops to destination in testing). The install/upgrade script now installs the traceroute package. (2) Each hop shows its **reverse-DNS name** alongside the IP (matching the terminal traceroute experience; a short timeout keeps unresolvable hops from slowing the trace). (3) The result **states whether the destination was reached**: an unreached trace gets a labelled tag with an explanation instead of silently stopping mid-path and looking finished.
- **The trace button becomes "Cancel" while running**: a 30–60-second job cannot offer only a spinner; cancelling aborts the stream and the backend kills the probe process immediately. A user's own cancel shows as info, not a red error.
- **Four more dropdowns on the exposed-services list**: type (NAT/rule), protocol, status and customer, with options derived from the data, stacking with the firewall filter and the search box.
- **NAT ↔ rule pairing**: when the same target IP and port has both a NAT forward and a permit rule, both rows carry a "paired" tag (port forwards usually travel with an associated rule); this is pure data matching, nothing guessed.

### Changed
- The live-status column no longer prints raw strings like `online (librenms)`; it shows a green/red dot with a localized label and a small source note instead.
- The AI chat's three header icons are now properly centred (the icon slot kept its text gap when labels were hidden, nudging icons low-left).
- zh-TW wording: 查看 → 檢視.

## [0.5.184] - 2026-08-15

### Changed
- **Final form of the grid's auto-recorded marker: diagonal two-colour cells.** Solid purple (0.5.183) was unmissable but hid liveness; prominence and status should not be a trade-off. The upper-left half is purple (auto-recorded) and the lower-right half keeps the normal liveness colour; both facts are visible at a glance, legend updated.

## [0.5.183] - 2026-08-15

### Changed
- **Third take on the subnet grid's auto-recorded marker: solid purple cells.** An orange outline and then an orange corner badge were both reported "still too small" among hundreds of tiny cells: a few pixels can never stand out. Purple is the one colour the palette does not use (green/red/amber/blue/grey are taken), so the whole cell changes colour and is unmissable; liveness moves to the tooltip and the legend says so. The IP list's auto-recorded marker turned purple to match.

## [0.5.182] - 2026-08-15

### Changed
- **Site-wide layout rule: card headers hold no controls.** Buttons, dropdowns, inputs and column pickers moved from card header rows into a toolbar at the top of the card body across 23 views/components (racks, section/subnet/device/customer detail, IP requests, topology, tasks, notifications, API tokens, AI review, chat history, rule changes, dashboard widgets, IP detail and more). Behaviour and permission conditions untouched; only the position changed. Verified by a browser sweep: 13 main pages with zero header controls, no blank pages, no JS errors.
- Firewall rule-change diffs: the red "+" no longer sits on its own line; small coloured tags (added/removed/changed) now share the line with the rule text.

### Added
- **IP and device cards show virtual/physical.** Correlated with the virtualisation integrations (Proxmox/VMware): when an IP or MAC matches a VM interface, a "Virtual machine" tag appears with the VM name and cluster; devices are matched three ways (name, primary IP, and port MACs; a renamed VM still matches by IP/MAC). **No match shows nothing**: the integration may simply not cover that host; "unknown" is not "physical", and asserting otherwise would mislead (pinned by a test).

## [0.5.181] - 2026-08-15

### Changed (a round of hands-on feedback on the exposed-services page)
- The toolbar (search / firewall filter / columns / refresh) **moved into the card body**; card headers no longer hold controls, and all four controls share one height.
- Added a **type-to-filter search box** (IP / hostname / name / description / port).
- **Registered IPs link straight to their IP card** (the site-wide entity links).
- The subnet grid's auto-recorded marker became an **orange corner badge**, because the outline was too easy to miss among hundreds of small cells; legend updated.
- zh-TW wording: 紅旗→警訊, 盤點→清單, 逐家→逐一.

## [0.5.180] - 2026-08-15

### Changed
- Page renamed: "Attack surface" → "**Exposed services**"; it says what the page actually lists, and avoids colliding with the existing external-exposure anomaly category.
- **Every column split apart** (from real-world use): "IP:port (hostname)" crammed into one cell and "NAT | name" into another. IP, port, hostname, type (NAT/rule), name and firewall (vendor + instance) are now separate, sortable columns; NAT rows gained the firewall instance name (previously vendor only).
- Added the **site-wide column picker** (same preference store, synced across devices) and a **firewall source dropdown** (options derived from the data, so manual entries or future vendors need no code change).

## [0.5.179] - 2026-08-15

### Added
- **An "Attack surface" inventory page** (next to firewall rule changes; for admins and read-all accounts with global read, since auditors are exactly its audience). It aggregates what is reachable from outside: enabled NAT port forwards plus WAN permits whose destination is a single IP, each entry with its IPAM identity (hostname, customer/subnet, Wazuh agent presence, live status). **"Unregistered" is flagged in red**: an external opening pointing at a host IPAM does not know is a red flag in itself. Anomaly detection's external-exposure check finds problems; this page is the inventory an audit asks for first. Rules whose destination is an alias / any / a network are **not expanded by guesswork** (a list an auditor signs must contain nothing guessed); the page states its scope plainly.

## [0.5.178] - 2026-08-15

### Added (three applications of the synced firewall rules)
- **A "Firewall" block on the IP detail view**, answering the reverse question: which rules explicitly cover this IP (exact address / covering network / alias membership, each with its match reason), which NAT entries point at it, which aliases contain it. `any` rules are deliberately not listed (every any-rule matches every IP; listing them is pure noise); a footnote says they also apply. Dual-gated: the IP must be readable, and firewall rules are global-infrastructure data requiring global read.
- **Alias-rot detection** (part of the firewall-rule-rot anomaly category): alias members that fall inside subnets this IPAM manages but have no IP record; the rule looks unchanged while the alias now points at an unknown address. External members are normal and never flagged.
- **Acknowledgements for rule changes** (the compliance trail): an admin can mark each change as known, with a note (e.g. a ticket number). Unacknowledged changes accumulate into exactly what an audit asks for: "N firewall changes this month, M unexplained."

### Fixed
- Three new endpoints logged audits without `request_id` (two were latent in earlier versions; the tests forced them out).

## [0.5.177] - 2026-08-15

### Added
- **AI analysis for firewall rule changes** (a button on each change in the rule-changes page; admin-only, on demand). Detection and alerting stay fully deterministic; the AI is an interpretation layer, and it brings **system-wide evidence about the target address** to the model: IPAM registration and change timeline, ARP/MAC, whether a Wazuh agent is present (an unmonitored host is one more reason for suspicion), reverse DNS, what other NAT exposures the host already has, whether it is a VM, and which subnet/customer owns it; this is information the IPAM has and the firewall does not. Output is three fixed sections: what the change does / risk assessment / what to do next.
- That system-wide evidence layer (`full_ip_context`) now also feeds the AI triage card for unauthorised IPs; both features share it. A failing evidence source just loses one line; it cannot blank the card.

### Fixed
- Target lookups only accept genuine single addresses: **networks (e.g. 10.0.0.0/24) were being treated as hosts** (caught by an adversarial test); aliases and "any" are also skipped, and lookups are capped.

## [0.5.176] - 2026-08-15

### Changed
- Wording (zh-TW): watcher-type features are no longer called 哨兵 ("sentinel", uncommon in Taiwan); they are 異動偵測 ("change detection"), matching the existing 異常偵測 (anomaly detection). Notification matrix, the rule-changes page and docs updated; sentinel *values* in code comments are now 保留值.

## [0.5.175] - 2026-08-15

### Added
- **A new anomaly category: firewall rule rot.** The rule-change sentinel watches *changes*; this watches what is *already wrong* in the active ruleset: (1) **dangling port forwards**, meaning synced, enabled forwards whose target address is not in IPAM (usually reclaimed, so traffic goes to an unknown host); (2) **any-to-any permits** (that interface effectively has no firewall); (3) **management ports** (SSH/Telnet/RDP/VNC/IPMI) **open to any source on a WAN interface**. All deterministic, and deliberately conservative. Manual NAT entries do not count as dangling (an unlinked IP is normal there), disabled rules are skipped, and SSH-to-any on a LAN is everyday practice: this page's enemy is the false positive. The any-any and management-port checks start with pfSense (the most stable data shape); OPNsense/FortiGate follow once verified per vendor.
- The docs feature page gained the v0.5.172–174 security-AI entries (rule sentinel / IP forensics / triage cards).

## [0.5.174] - 2026-08-15

### Fixed
- **Baseline snapshots now store their diff as SQL NULL.** SQLAlchemy's JSON columns serialise Python None as **JSON null** by default, so "`diff IS NULL` means baseline" never held at the SQL level; the API happened to be fine (it reads back as None), but any direct SQL (reports, future features) would misjudge it. Found on the first real snapshots in production; existing rows were normalised as well.

## [0.5.173] - 2026-08-15

### Added
- **A "Firewall rule changes" view** (next to anomaly detection, admin-only). The sentinel notification says "details are in the snapshot", but 0.5.172 had no screen that showed snapshots, so the notification pointed at a place that did not exist. This page lists every change event with its full diff (added / removed / changed, with before-and-after values per field); the first snapshot is labelled as the comparison baseline. Rule descriptions render as plain text (never v-html), so injection phrases stay literal.

### Fixed
- **Snapshot timestamps now come from the application.** They relied on PostgreSQL's `now()`, which is the **transaction** timestamp: two snapshots in one transaction got identical times, making "the latest snapshot" unstable, so the sentinel could diff against the wrong baseline. Caught by a test taking two snapshots in a single transaction.

## [0.5.172] - 2026-08-15

### Added (security x AI, three pieces)
- **A firewall rule-change sentinel.** We sync rules from three firewall families (OPNsense / pfSense / FortiGate), but nothing ever watched them; a permit rule appearing overnight is the classic sign of a compromised firewall or an insider backdoor, and each sync silently overwrote the previous state. Every sync now normalises the rules and hashes them; **only when the hash differs is a snapshot row stored (with a per-rule diff)** and admins notified (switchable in the notification matrix). Reordering rules in the UI does **not** count as a change (cry wolf twice and nobody reads the alert again), the first snapshot is a baseline and does not alert, and rule descriptions are untrusted text, so the notification body is assembled from plain data, never through an LLM. A sentinel failure cannot break the sync itself.
- **IP forensics (`get_ip_history` MCP tool).** The first question in any incident is "who was this IP at the time". Ask in AI chat and get the evidence timeline: field-level change log (with source), ARP IP-MAC bindings (a MAC change is immediately visible), per-source hostname observations, and DHCP-server sightings. All deterministic retrieval; interpretation is left to the human or the model. RBAC matches the IP detail rules: a restricted account asking about an IP it cannot see gets **no ARP/MAC data** (otherwise history queries become a side door around permissions).
- **An AI triage card for unauthorised IPs.** Each row of the anomaly page's unauthorised-IP list gains an "AI triage" button: OUI vendor, per-source hostnames, MACs and switch ports are assembled for the local LLM, which produces a card covering what this device most likely is, the risk, and where to look next (admin-only; clearly labelled as inference, with the raw evidence returned alongside). **Injection resistance is the core of the design**: hostnames are attacker-controlled text (a hostile device can set its mDNS name to an injection phrase), so every untrusted field is fenced in `<data>` markers, truncated, fence-breaking sequences are neutralised, and the model is told data is not instructions; this is pinned by adversarial tests.

## [0.5.171] - 2026-08-14

### Fixed
- **After `git pull`, the rest of an upgrade still ran from the old copy of the script.** The new code was pulled correctly, but the backup, the migration, the frontend build, the systemd units and the nginx configuration all ran the old logic, meaning **fixes to installation and upgrade only took effect on the customer's *second* upgrade**, while the first one looked completely normal and exited 0. The script now hands over to the new version once the pull has updated it (with `--no-pull`, since the pull already happened; a flag prevents handing over in a loop; nothing re-runs when the commit is unchanged).

  WARNING: **this fix lives in the new script**, so the upgrade that pulls it is still driven by the old one. **Sites on 0.5.170 or earlier should run `upgrade` twice** (or run `jt-ipam.sh doctor` afterwards and follow whatever it prints). After that, once is enough.

## [0.5.170] - 2026-08-13

### Fixed
- **The SFTP file browser was only partly masked after a disconnect** (reported by a user). The dimming was applied **piece by piece**: the path bar and the table were covered, while the pagination row and alerts stayed bright and looked usable. Applying it piece by piece always misses one, and "looks clickable but nothing happens" is harder to understand than "obviously disabled". The whole panel is now masked at once: the content stays visible underneath (so you can still see what was there), nothing in it is interactive, and the centre of the panel says the connection dropped, notes that the listing may no longer match the remote host, and offers a reconnect button.
- **Pressing "Disconnect" dropped back to the connection form with "the connection was closed before it was established (code 1005)".** `disconnect()` set the state to "closed" first, and the WebSocket's onclose then read that state to decide whether it had ever connected; finding something other than "connected", it concluded the connection had failed before it opened. The user's own click looked like a connection error. A dedicated flag now records whether the session ever came up, rather than inferring it from the state.

## [0.5.169] - 2026-08-13

### Fixed
- **A partly failed AI review deleted findings it had never looked at.** The review is sent to the model in batches; when one batch fails (a timeout, a reply that will not parse as JSON), that batch's data **was not examined at all this round**, so its problems naturally do not appear in the results. The reconcile step only asked "did this come back again?", concluded those findings were resolved, and removed them; on screen the problems appeared to have fixed themselves while they were still there.

  A run with any failed batch now only adds and updates, **never deletes**, and says so in the result: "to avoid treating unexamined problems as resolved, existing findings were not removed this time" (otherwise the only visible effect is a list that mysteriously did not shrink). Better to keep one finding that may already be fixed than to let a real one disappear quietly.

## [0.5.168] - 2026-08-13

### Fixed
- **Deleting a folder over SFTP failed with nothing but "Failure"** (reported by a user). **SFTP v3 has no "directory not empty" status code**: a server asked to remove a folder with contents can only answer with the generic failure, which asyncssh surfaces verbatim as `SFTPFailure("Failure")`. The screen said neither why nor what to do next. (The message table did have `SFTPDirNotEmpty`, but that exception almost never appears in practice.)

  A failed delete now **works out the reason itself**: it lists the directory, and if there is anything in it says so plainly ("the folder X is not empty (N items left)"), then asks whether to **delete it along with its contents**. If the directory really is empty (so the failure has another cause, usually permissions) the original error is kept rather than claiming it is not empty. Batch deletes list non-empty folders separately instead of mixing them in with real failures.

  The recursive delete **does not follow symbolic links**; it removes the link itself. Following one would delete things outside the tree being removed, which is data loss rather than an inconvenience. Verified against a real SFTP server: after removing a directory containing a subdirectory and a symlink, the file the link pointed at was untouched.

## [0.5.167] - 2026-08-13

### Added
- **The AI review can now be scheduled weekly or monthly**, not just "at these times every day". The schedule is now two dimensions: **which days** (daily / chosen weekdays / a chosen day of the month) x **what times** (the existing list). Picking the 31st runs on the **last day** of months that are shorter, rather than skipping those months entirely. That failure mode (a condition that is simply never true) produces no error and no log entry; it just looks like the feature is not working, so it is pinned down by tests.
- **Scan agents have a "Record unregistered IPs automatically" toggle, off by default.** A scan agent used to create a record for every live address IPAM did not know about, **unconditionally**; it was the last of the three source families still doing so (0115 covered OPNsense/pfSense, 0116 Proxmox/VMware). WARNING: **this changes behaviour**. After upgrading, scan agents no longer record new addresses until you turn this on under Scan agents. The reason: once an address is recorded it **no longer appears in unauthorised-IP detection** (whose whole test is "we can see it, IPAM does not have it"), so a machine somebody plugged in without asking would quietly become a normal-looking record.
- **The subnet grid now marks auto-recorded addresses**: cells created automatically by an integration or a scan agent, which nobody registered by hand, are drawn **green with an orange outline** (same state as before, but visibly unregistered), and the legend gained an "Auto-recorded (n)" entry. The orange marker in the IP list now also covers the scan agent; it previously recognised only OPNsense/pfSense/Proxmox/VMware, so scanner-created records carried no marker at all.

### Changed
- **Dismissing an AI review finding now asks first.** Dismissing is not "hide it this time": every later review skips that finding automatically, and undoing it means finding it again under the Dismissed tab. It uses the same popconfirm as "Clear all" on that page rather than a second pattern.
- The subnet detail's "Import CSV" and "Export CSV" buttons got icons (upload / download arrows), matching the rest of that row.

### Fixed
- **Addresses dropped during a scan-agent report are now counted.** There were two silent paths (auto-recording switched off, and "no assigned, scanning-enabled subnet contains this address"), and both simply `continue`d, so all a user saw was "it scanned and nothing happened", with nothing on screen pointing at the real cause. The response now carries `created`, `skipped_not_in_ipam` and `skipped_no_subnet`.
- The "auto-recorded" tooltip claimed the address came from a DHCP sync, but the sources now include virtualisation and scan agents. It is worded generally and states the cost (unauthorised-IP detection stops listing it).
- The schedule hint said runs happen "at these times every day", which stopped being true once the frequency became configurable.

## [0.5.166] - 2026-08-12

### Added
- **`jt-ipam.sh doctor`, a one-command health check.** It checks the configuration file, whether the backend actually answers, the database and its `pgvector` extension, whether the schema is at the latest revision, whether the built frontend matches the backend version, the timers and the backup directory, the last sync result, and the local scan agent. **Anything it can't confirm comes with a command you can copy and run**, so nobody has to go log-hunting first; attaching its output is enough to open a useful bug report. (It deliberately decides "is the service up?" by connecting rather than by looking for a bound port: minimal images often lack `iproute2`, so an `ss`-based check reports a failure while the service is fine, and a diagnostic that lies is worse than no diagnostic.)
- **`scripts/test-fresh-install.sh` runs a real first-time install in a clean-OS container** and verifies the parts that only ever break at a customer site: the backend answers, the backup and sync timers actually reach `Result=success`, the backup unit self-heals when its directory is deleted, and `doctor` comes back clean. It is now a required release step (TEST_CHECKLIST 5b). **Install bugs cannot reproduce on a machine that is already installed**: every install failure customers reported had that in common.

### Fixed
- **A fresh install in nginx mode was unreachable from a browser** (found by the container test above, before any customer hit it). The installer only ever ran `systemctl reload nginx`, and reload against a service that was never started just prints `nginx.service is not active, cannot reload` and moves on, with the exit status swallowed. The config was written, the certificate was in place and the backend was healthy, but nginx had never been started and was not enabled at boot, so **nothing served the UI**. A single function now handles it: test the config, enable it at boot, start or reload as appropriate, and **verify it is actually running**, saying plainly that jt-ipam is unreachable until it is.
- **Neither the health check nor the install test treats `/healthz` as end-to-end evidence any more**: the nginx site answers that path itself with a static `return 200 "ok"`, so it stays green with the backend completely stopped; it only ever proved nginx was alive. Both now request a route that has to be proxied (an unauthenticated 401 counts as "the backend answered"; a dead backend gives 502).
- **A fresh install logged a red `duplicate key ... uq_groups_name` exception on first start.** Seeding the built-in roles and circuit types is idempotent, but **uvicorn starts several workers at once**: four processes see an empty table simultaneously, all INSERT, and the losers hit the unique constraint. Nothing was actually broken (the winner had already seeded), but the first thing a customer saw in the log looked like a failed install. The seed functions now take a PostgreSQL advisory lock so callers queue up (the lock lives with the invariant it protects, so every call path is covered). **Idempotent is not the same as concurrency-safe**; there is now a regression test that really does run them concurrently.
- **The backup service failed the first time it ran after a fresh install** (reported by a customer on Debian 12). `jt-ipam-backup.service` listed `/var/backups/jt-ipam` in `ReadWritePaths` before anything created it, and systemd refuses to start with `226/NAMESPACE`, an error that names nothing about the actual cause, leaving the customer to create the directory by hand. Install and upgrade now create the directories the units need, and the unit itself no longer fails when one is missing.
- **`pgvector` was installed for the wrong PostgreSQL major** when the host already had a cluster on a different version, so the database connected but the extension was absent (which shows up as semantic search and AI features quietly returning nothing). The installer now detects the **running** cluster's major and installs the matching `postgresql-N-pgvector`.
- **A failing frontend dependency install was swallowed**, so the installer finished with no usable frontend. It now shows the full output, verifies the build artifacts afterwards, and stops with a clear message if either step failed.

### Changed
- **SFTP's new folder, rename and move now use in-app dialogs** rather than the browser's `window.prompt`. A native prompt looks like a system warning, ignores the theme, and leaves nowhere for guidance or validation.
- **Move offers both a path field and a clickable directory browser**: type an absolute path at the top, or walk the directory list below (directories only, since a file cannot be a destination). The button states the destination outright: "Move here: /some/path". An empty level says so rather than looking broken.
- **Large directories are paginated** instead of being truncated with "showing part of it". The listing cap went from 2000 to 20000 entries (paginating removed the reason it was kept low); beyond that it still truncates, and still says so.

### Fixed
- **In the device list the type tag overlapped the physical/virtual column, and the delete button sat past the table's right edge.** The type column had no width (its longest label is "Wireless AP") and the actions column had 136px for four buttons and was not pinned. Same family as the scan-agent page, and the same fix: enough width plus `fixed: right`.

### Tests
- The SFTP e2e specs drive the real dialogs now, and assert that **no native browser dialog appears**: reintroducing `window.prompt` turns them red.
- Fixed two races in the tests themselves: with a fixed filename, a leftover file from the previous run made "the row is in the table" true before the drop even happened, so the test read the file mid-write and got an empty string. Filenames are now unique per run.

## [0.5.165] - 2026-08-12

### Fixed (**major: several integrations had not run at all since v0.5.150**)
- **The scheduled sync stopped after its first two integrations.** In `jt-ipam-sync.py` the ESXi block lost four spaces of indentation, which did two things: it ran against an **already-closed session**, and **every block from Wazuh onwards ended up nested inside the ESXi `for` loop**. On any host without an enabled ESXi instance that loop body never executes, so **Wazuh, LibreNMS, ARP pruning, AdGuard, FortiGate, Windows DHCP, Proxmox, DNS, certificate fetch, certificate alerts, IP-to-device autolink and the AI review all silently stopped**, with nothing on screen to show it.

  The leaked connection then raised `RuntimeError: greenlet is being finalized` during interpreter shutdown, so systemd recorded every run as failed (259 times in 24 hours on our own prod). The `MissingGreenlet` a customer reported has the same root cause.

  One visible symptom was "**the AI review schedule is set but never runs**": that code sits at the end of the script and was never reached. After the fix, on prod: every integration runs again, the AI review produced 19 findings, and the greenlet errors are gone.

- **Added `await engine.dispose()` before the script exits.** Without it the pooled asyncpg connections survive until interpreter shutdown, where the event loop and greenlet are gone and `terminate()` blows up.

- **A single unreachable integration no longer marks the whole run as failed.** An offline firewall is a *reported* condition (written to that instance's last_error and visible in the UI), yet the script exited non-zero, so `systemctl status jt-ipam-sync` stayed red forever, which made a genuine failure indistinguishable from a device being down (it misled both a customer and us). Non-zero is now reserved for a run that could not complete, and the log states "sync completed with N integration error(s)".

### Tests
- Added `tests/test_sync_script_structure.py`: an AST check that **every integration block is a direct child of the session block**, failing if any becomes nested inside another loop. Restoring the broken indentation turns 9 of its 12 cases red. Python does not report this kind of indentation as an error (it simply means something else), so only a structural check can hold it.

## [0.5.164] - 2026-08-12

### Changed (**behaviour change: please read**)
- **Proxmox no longer creates IP records unconditionally.** It was the one integration that created any address IPAM did not have, with **no toggle at all**. That is now governed by "trust addresses from virtualization", **off by default** (migration 0116).

  ⚠️ **Upgrading changes behaviour**: VM and node IPs that used to appear on their own no longer do. To restore it, switch the toggle on under the Proxmox VE integration. This is deliberate: auto-recording removes those addresses from the "unauthorised IPs" anomaly check (whose test is "seen in ARP, absent from IPAM"), and that trade should be an explicit choice.

- **Fixed an overlapping-subnet hazard in Proxmox while there.** It picked a subnet with `ORDER BY masklen(cidr) DESC LIMIT 1`, which **silently chooses one** when two tenants each hold `198.51.100.0/24`, potentially filing a VM under someone else's subnet. It now uses the same decision as every other integration: **ambiguous means don't create**.

### Added
- **VMware / ESXi gains the same "trust addresses from virtualization" toggle** (off by default). It previously never created anything, only matched existing records.
- **A dedicated "Which sources create IP records on their own?" table in the README and on the site**, listing scan agent / LibreNMS / Proxmox / VMware / OPNsense / pfSense / the remaining integrations / imports with their toggles and defaults, information that was previously only discoverable by reading the source.
- The auto-recorded marker now covers the `proxmox` and `vmware` sources too.

## [0.5.163] - 2026-08-12

### Added
- **An "create addresses IPAM does not have" toggle for OPNsense and pfSense** (migration 0115, **off by default**). The firewall DHCP/ARP sync only ever stamped addresses that already existed; anything else was dropped, silently; a customer had to read the source to find out. With the toggle on, an address present in a DHCP lease but absent from IPAM is created.

  Placement reuses the LibreNMS rule (now extracted to `services/ip_autocreate.py` and shared by all three integrations): longest-prefix match, **created only when exactly one subnet matches**. Where overlapping subnets make it ambiguous (two tenants each holding 198.51.100.0/24), **nothing is created**: filing a record under the wrong tenant is worse than not filing it. Setting the integration's subnet scope removes the ambiguity.

  ⚠️ **The risk is stated next to the toggle**: a machine that obtained a DHCP address is not necessarily one that belongs in IPAM. An unauthorised device that got a lease would be recorded as a legitimate entry, **and once in IPAM it stops appearing under "unauthorised IPs" in anomaly detection**, whose entire test is "seen in ARP, absent from IPAM". Hence off by default, with the warning shown when it is switched on.

- **Auto-recorded addresses are flagged in the IP list** (amber icon plus explanation): "Auto-recorded (unregistered): created automatically by PFSENSE's DHCP sync; nobody registered it by hand. Confirm this device is expected."

### Changed
- **Sync summaries report how many entries were skipped because IPAM had no such address** (`skipped_no_ipam_record`), and how many were created. This was previously silent.
- `discovery_source` now permits `pfsense` (only `opnsense` was allowed).

## [0.5.162] - 2026-08-12

### Fixed
- **The scan agent delete button was pushed off screen** (reported as "there is no delete"). It had always been there, but with enough columns the table overflowed horizontally and the fourth action button was simply out of view. The actions column is now **pinned right** and wide enough for four buttons (matching the users and certificates pages). The confirmation also states **how many subnets are assigned to that agent**, since scanning always needs one, so deleting it leaves those subnets unscanned.
- **The edit dialog has a delete button too**, bottom-left and away from Save: an irreversible action should not sit next to the primary one.
- **SFTP uploads accept multiple files.** The picker was single-select, so ten files meant opening it ten times. Multiple files are sent one at a time with an "Uploading 3/10" line; failures are named individually rather than stopping the batch.
- **A saved SFTP credential is now selected by default** (as in the SSH console): the most recent one is picked, the manual fields collapse, and Connect just works. Clear the dropdown to go back to entering credentials by hand.
- **"Clear selection" gained its icon**, so all four batch buttons match.

### Added
- **Drag-and-drop upload in SFTP**: drop files onto the file area to upload them into the current directory. While dragging, the whole panel becomes a drop zone labelled with the destination path. Dropped folders are reported as skipped (files only for now).

## [0.5.161] - 2026-08-10

### Fixed (install; customer reports)
- **Installing on a host that already runs PostgreSQL failed with `extension "vector" is not available`.** The installer picked the version apt could install (16), but jt-ipam connects to `127.0.0.1:5432`, the cluster that was **already there** (18, in this case). pgvector went to 16, so 18 never had it. The installer now asks the running cluster for its version and installs pgvector for that, and **no longer pulls a second server package** (which would create a second cluster on another port). A cluster below 16, or one with no matching pgvector available, now stops the install with a message that says so.
- **A failed extension no longer passes silently.** The `psql` heredoc lacked `ON_ERROR_STOP`, so `CREATE EXTENSION vector` printed its error and still exited 0, surfacing a hundred lines later as an alembic traceback. It now stops there and names the package to install.
- **`/usr/local/bin/pnpm: No such file or directory`.** The pnpm install line sent npm's error to `/dev/null` with `|| true` and then fell back to a path that did not exist. It now keeps the output, tries three ways of installing pnpm, and verifies `pnpm --version` runs before continuing; on failure it prints what npm actually said plus the manual command.
- **The installer no longer says "Done" when nothing is running.** Before finishing it checks the env file, the frontend dist, the service state, the listening port, and nginx in nginx mode, and lists whatever is missing.
- **Direct TLS on port 443 now gets `CAP_NET_BIND_SERVICE` automatically** (whenever `--bind-port` is below 1024). Without it the unit starts and dies immediately with `Permission denied`, which reads like a certificate problem but is a port problem.

### Documentation
- **Does `--tls-mode self-signed` need nginx or apache? No.** The most frequently asked install question is now answered in INSTALL and the FAQ: that mode is a complete HTTPS service on its own and **listens on 8443 by default, not 443**, with commands to confirm it.
- Added "how to use 443 instead" (including the privileged-port capability) and full steps for adding nginx later; plus a warning that a hand-written nginx config **must copy the WebSocket upgrade block**, or the consoles cannot connect and the browser shows only a bare 404.
- FAQ entries for all three install failures, including how to recover on older versions.

## [0.5.160] - 2026-08-10

### Fixed
- **A subnet with scanning enabled was never actually scanned** (customer report). Leaving the subnet's scan agent blank displayed "Local scan (jt-ipam host)", but nothing in the backend schedules a local scan: the only entry point is a manual API call, and the frontend never calls it. The setting looked complete while liveness never updated.

  Scanning now **always runs through an agent**:
  - **Install and upgrade set up a scan agent on the jt-ipam host itself** (with the probe tools nmap / samba-common-bin / avahi-utils) and flag it as the local one. Idempotent: an existing agent is left alone, and its key is never re-issued (that would kick the running agent off); a failure here warns rather than failing the install.
  - **Migration 0114** points existing subnets that had scanning enabled but no agent at the local agent, so an upgrade starts scanning them for real.
  - The dropdown's blank option changed from "Local scan (jt-ipam host)" to **"(unassigned, will not be scanned)"**, and selecting it states plainly that the subnet will not be scanned and where to add an agent. The host's own agent is listed as "name (agent on the jt-ipam host)".

  Worth stating outright: the probe checkboxes (ARP, reverse PTR, NetBIOS, mDNS, DHCP server detection, OS detection) have only ever been executed by an agent.

### Added
- `python -m app.cli.scan_agent ensure-local`: creates the local scan agent and prints its one-time key (used by the installer; leaves an existing one untouched).

## [0.5.159] - 2026-08-09

### Fixed
- **The sidebar logo panel and the top bar did not end at the same line**, leaving a visible notch in the top-left corner. Each was sized by its own content (`14+32+14` against `8+content+8`); a few pixels apart is enough to see. Both are now bound to one `--app-header-h` with `box-sizing: border-box`, so they cannot drift apart again.
- **The SFTP filter field was shorter than the path field** (it was the small size). Both measure 34px now.

### Changed
- **The SFTP file area has a frame**: path bar, batch bar and listing sit in one panel, with the **connection status row deliberately outside it**, since that row is about the connection, not the files, matching the SSH console.
- **"Up one level" gained an icon**; every button in that row now has one.

### Tests
- New `e2e/layout.spec.ts` measures the two bottom edges and fails if they differ by more than a pixel.
- A layout test for SFTP: the frame exists, the status row is outside it, path and filter fields match in height, and all four buttons carry icons.
- Fixed self-contamination in the batch test: without emptying the destination first, a second run failed to move (name already taken) while the assertions still passed; the test was green for no reason.

## [0.5.158] - 2026-08-09

### Added
- **Batch operations in SFTP.** Rows are selectable; selecting any reveals a batch bar with **download, move and delete**. Move asks for an absolute destination path; delete asks for confirmation.
  - Directories cannot be downloaded as files, so the result **says how many were skipped** rather than quietly sending fewer.
  - If some entries fail, the message **names them**; one failure neither stops the rest nor lets the others pass for a success.
  - Changing directory or refreshing clears the selection, because carrying a selection across directories deletes the wrong things.
- **A filter field** narrows the current directory as you type, with a line below stating "showing N of M entries in this directory" so a filtered view is never mistaken for the whole directory.

### Changed
- **Icons on the buttons**: upload, new folder, download, rename, delete, and the three batch actions.
- **Directory and file names line up**: files have no icon but reserve exactly the icon's width. (Measured, not eyeballed: an emoji was 17px off, and switching to an icon component was still 16px off because **scoped CSS does not reach elements built in a render function**; the fix is inline styles.)
- **Remote errors are written for people.** The screen used to show the exception class ("SFTPNoSuchFile: No such file"); it now says which path could not be found, the one piece of information that matters when a path is mistyped. Permission denied, not-a-directory, already-exists, directory-not-empty, disk full and read-only filesystem each have their own wording, and anything unmapped **keeps its original message rather than being given an invented one**.
- **Connection failures too**: "Cannot reach 192.0.2.10:2222: the host refused the connection; check that its SSH service is listening on that port" replaces `ConnectionRefusedError: [Errno 111]`.

## [0.5.157] - 2026-08-09

### Fixed
- **SFTP could not connect at all in a real deployment** (both 0.5.155 and 0.5.156). Picking a credential and clicking connect made the form flicker and come back, with no message.

  The cause was not SFTP itself: nginx forwards the WebSocket upgrade headers only for `(ssh|rdp|vnc|novnc|bmc)/ws`, and **`sftp` was not on that list**. Without those headers nginx passes a plain GET to the backend, which has only a WebSocket route at that path and no HTTP one, so it answers 404. The browser sees nothing but a closed connection, with no hint that a proxy is involved. Local development hides this: vite's dev proxy forwards WebSockets for all of `/api`.

  Fixed in both nginx templates and the installer; `jt-ipam.sh upgrade` now **widens the location on existing sites automatically** (back up, then `nginx -t`, reload only on success, restore on failure). The four hand-written substitutions that each matched one historical protocol list are replaced by a single whole-line rewrite, so the next protocol cannot be half-added.

- **A failed connection no longer stays silent.** When the WebSocket closes before the session is established, the screen now says so, with the close code and a pointer to the most common cause (a proxy not forwarding the upgrade headers), instead of dropping back to the form without a word.

- **The connect card no longer jumps.** It was centered only in the "form" phase, so pressing connect threw it to the top-left corner and a failure threw it back. It now stays put through connecting and failure.

### Tests
- Added a cross-check: every `/<protocol>/ws` endpoint the backend registers must appear in both nginx templates and the installer's protocol list. Removing `sftp`, which is exactly what shipped, turns it red.

## [0.5.156] - 2026-08-09

### Changed
- **The SFTP connect screen now matches the SSH console.** The previous release gave SFTP its own form (labels above, fields stacked down the page), which looked nothing like SSH, RDP or VNC. The same task shaped differently per protocol asks the user to learn it twice. It is now the same card form (left-aligned labels, auth-method radio, hint block, connect button bottom-right), and once connected, the same status bar (green pill, hostname, protocol tag, disconnect).

  Also filled in what should have been there from the start: **"remember these credentials"** (into the same per-user encrypted vault SSH uses), **deleting a stored credential**, and **reconnecting after a disconnect** without retyping.

- **SFTP is now its own toggle** (migration 0113). It previously rode on `ssh_enabled`, so enabling SSH also enabled file transfer. In practice those are not always the same decision: a host may be meant for dropping in a config or pulling a log, without handing out a shell. The **authorization model deliberately stays identical to SSH**: someone who can read and write remote files holds effectively the same power as someone with a shell, and "it's only file transfer" is not a reason to loosen it.

  **On upgrade, existing rows inherit `ssh_enabled`**: under 0.5.155 an SSH-enabled address could already use SFTP, so defaulting everything to off would make the feature silently disappear. Anyone who wants it withdrawn can simply switch it off.

- **The entry button now reads "SFTP Files"**; it previously said just "Files", which did not say which kind of connection it opened.

### Security
- Raised the floor on three transitive build-time dependencies (none ship to the browser): `nanoid` ≥ 3.3.17 (custom generators loop indefinitely when size is zero) and `brace-expansion` ≥ 1.1.18 / 2.1.4 / 5.0.9 (DoS via unbounded intermediate arrays). `pnpm audit` goes from four high findings to none.

## [0.5.155] - 2026-08-09

### Added
- **SFTP file browser.** You could already open an SSH terminal on an address, but getting a config file onto that host (or a log excerpt off it) meant reaching for another tool and entering the credentials again. IP detail now offers an SFTP entry point: list, navigate, download, upload, make a directory, rename, delete, all in the browser.

  **It is the same gate as SSH**: the same `can_use_ssh` permission, the same single-use ticket (60 seconds, one redemption, bound to that address), the same stored credentials. Open, close, download, upload, mkdir, rename and delete are all audited; directory listings are not, since they would drown the audit trail.

  A single file is capped at 100 MB and a directory listing at 2000 entries; both limits are stated on screen rather than silently applied. Uploads are truncated to the declared size, so a client cannot declare one size and send more. When the remote does not report a file's size the browser shows a dash rather than 0 B, because those are different facts.

### Fixed
- **Consoles could not connect against older Redis.** Single-use tickets called Redis `GETDEL`, which only exists in **6.2 and later**. Older deployments answered `unknown command GETDEL`, so SSH, RDP, VNC, noVNC and BMC (five consoles) all failed to connect, showing nothing more specific than a connection failure. The same operation now runs as a Lua script (`EVAL` has existed since Redis 2.6), preserving single-use semantics.

  This surfaced while verifying SFTP in a real browser: the fake Redis used by the unit tests implements `getdel`, so no amount of unit testing would have caught it.

## [0.5.154] - 2026-08-07

### Changed
- **The AI review knows whether a subnet is actually being scanned.** A finding read "a large number of unmonitored IP addresses… this may indicate a monitoring blind spot" and advised checking whether monitoring covers the subnet. The subnet *was* being scanned, by an assigned agent, and 130 of its 233 addresses had been seen. The advice sent the reader to the wrong place: what those 103 addresses need is stale records cleaned up, or a check on hosts that answer no probe.

  The model was not wrong so much as under-informed: each subnet in the snapshot carried only a CIDR and a description, with nothing that could distinguish "not monitored" from "monitored, and these never answered". Subnets now carry `scan_enabled` and how many of their addresses a scanner has ever seen, with the instruction that a scanned subnet where many addresses have been seen is covered, and that recommending a coverage check when scanning already works there sends someone to the wrong place.

- **Each finding records which model wrote it** (migration 0112). A review is inference, and models differ; after switching models there was no way to tell which conclusions came from which, and therefore no way to judge whether the new one is actually better.

- **"AI inference, not verified fact" now reads "An AI reading of your IPAM data, worth checking yourself."** The original phrasing denied the thing's value; it is inference drawn from facts, which is worth reading as long as you confirm it.

- **Severity is shown as the background of its own cell** instead of a coloured bar down the left edge of each row. The bar said the same thing twice, and left rows visibly misaligned, which the layout then had to compensate for.

## [0.5.153] - 2026-08-07

### Changed
- **An address on the virtualisation pages links to its IPAM record.** The data was already in the system, but reading a VM's address and checking how it is registered meant copying the digits, switching page and pasting them into a search.

  **A link only appears when exactly one record matches.** With overlapping subnets (different units sharing `198.51.100.0/24`, which this project exists to support), the same address string legitimately has several records, and there is no way to tell which one a VM's address refers to. In that case the text stays plain: a wrong link is worse than no link, because people trust it. Verified against production: of 79 addresses on VM interfaces, 78 resolve to exactly one record and become links.

## [0.5.152] - 2026-08-07

### Added

- **The investigate view can export a report** (`.md` / `.txt` / `.html` / `.csv`): the facts and the AI reading together, for a handover note, a ticket attachment or an audit trail. Produced entirely in the browser, so **no new dependency and nothing to change in install or upgrade**. Text and CSV carry a UTF-8 BOM because Excel otherwise opens Chinese as mojibake; Markdown deliberately does not, since a BOM breaks the first heading.
- **AI chat can answer whether an address is reachable from the internet** and which ports are open. The investigate view already put NAT forwards and firewall rules side by side, but only if you knew to open it; the question people actually ask is one sentence long. The tool reports facts only and does not pronounce on whether that exposure is appropriate, since that depends on what the host is meant to do, which only a person knows. It sits at the same permission level as the NAT and firewall listings.
- **The AI reading streams**, with elapsed seconds and character counts, instead of leaving a button that looks dead for a minute. Thinking and output are counted separately, because a reasoning model emits nothing else for the first stretch.
- **The AI reading is told which patterns are normal for the host it is looking at.** A reverse proxy with twenty names resolving to it was reported as "a striking contradiction between the DNS records and the hostname sources"; the model did what it was told, since the prompt asks it to call out contradictions and nothing said that shape is ordinary for a proxy. Role signals are now computed from the facts and passed in, with the instruction that a false contradiction is worse than none because it buries the real ones.
- **A VMware NIC now records its port group.** That column was blank because nothing ever read it, and a blank cell cannot be told apart from a failed fetch.
- **Devices can be filtered by subnet**, and the list and detail pages say whether a device is virtual or physical. The kind is derived from the virtual-machine inventory rather than stored, so there is no second copy of the truth to maintain or to go stale.
- **Addresses can be attached to their device automatically, by NIC MAC.** A multi-homed machine's second address usually has no device: the existing LibreNMS sync links only the primary one. The device page's address list is then incomplete, and the AI review reported such a pair as a duplicate record, while the MAC was sitting on that device's `eth1` port all along. The system already knew; it just never used it.

  **Off by default, and the switch is deliberate.** An upgrade that quietly starts a job which rewrites data every five minutes is not something anyone asked for. It can be limited to chosen subnets, following the `scope_subnet_ids` convention every other integration here uses, and a **Preview** reports what it would attach before it is turned on, using the same evidence that made the first run trustworthy (39 of 41 candidates were independently corroborated by hostname).

  Ten rules decide when *not* to act, and none of them tries to guess better: an existing link is never overwritten and never removed; a MAC found on more than one device is left alone; an address whose device field a person has edited (including cleared) is never touched again, because otherwise clearing a wrong link would simply see it restored on the next round; protocol-reserved MACs (VRRP, HSRP) are shared across machines by definition; malformed and non-unicast MACs are rejected, since the port MAC column is free text and hand-editable, where `"N/A"` normalises to a non-empty `"a"` and would key a lookup; a hostname naming a different device is a contradiction between two independent signals; a customer conflict is respected, resolved through the subnet when the address itself has none; archived subnets are left alone.

  Every attachment is written to the IP change log with what it matched on, so it can be traced and reversed. Each round logs both what was attached and what was skipped, per reason, because "everything was blocked" must not look like "nothing to do".

  One residual risk is stated rather than papered over: a link is never re-evaluated, so a NIC moved to another machine will leave a link that is quietly wrong. That belongs to after-the-fact detection, not to more guessing at write time.

- **A login failure now says which kind of failure it was.** With the backend down, every request returned 502 and the login page still said "check your username and password", blaming the operator's credentials for a service outage, so the natural response is to retype the password and doubt the account while the real problem is elsewhere. Only a 401 means the server actually checked and rejected the credentials; an unreachable server, a 5xx, rate limiting and a locked account now each say what they are, and the server-side ones point at `systemctl status jt-ipam-backend`.

## [0.5.151] - 2026-08-07

### Changed
- **An upgrade cannot repair semantic search on its own, so the settings page now says so instead of staying silent.** 0.5.148 fixed the shipped default and made the failure reportable, but for an existing installation none of that takes effect by itself: a saved embedding model in the database wins over the new default, the replacement model still has to be pulled on your own LLM server, and existing records only get vectors once a reindex runs. An upgraded site would have kept returning nothing from semantic search, with nothing on screen explaining why: the same silence as before.

  The settings page now **probes the dimension when it loads** and states plainly when the model's output does not match the database column. It also has a **Rebuild index** button: the endpoint has existed all along, but there was no way to reach it from the interface, so there was no way to make semantic search actually start working. Both install guides gained an "if you are upgrading" section listing the three steps that genuinely need a hand.

  Fully automatic was neither possible nor right: this project cannot pull a model onto someone else's LLM server, and silently overwriting a model an operator chose is not a thing an upgrade should do. What it can do is fail loudly and offer the fix in one click.

## [0.5.150] - 2026-08-06

### Fixed
- **A VMware host could not be added at all.** `POST /api/v1/esxi` returned 422 `extra_forbidden` on every attempt, so the integration was unusable from the moment it shipped, and it went out twice that way.

  The failover-address field was added to the model, the migration and the form, but to none of the schemas. Request schemas here forbid unknown fields, and the form always sends that key (as `null` when blank), so every submission was rejected. The 33 existing ESXi tests were all green because they exercise the SOAP parsing and the sync; **none of them goes through a schema**.

  The field is now accepted on create and update, clearing it stores null rather than an empty string, and eight endpoint tests cover the contract, one of them posting the form's exact payload including the blank fields the customer had. A further test asserts that **every non-internal model column is reachable through the Create and Update schemas**, so this class of defect fails loudly next time; a column deliberately kept internal must be listed as such. A sweep of all 50 request schemas found no second instance.

  The frontend client took `Record<string, unknown>`, which is why type-checking never noticed. It is typed now, though that only catches typos, not front/back drift; the request-level tests are what actually catch this.

## [0.5.149] - 2026-08-06

### Changed
- **The VMware ESXi / vCenter integration is now in the README and on the project pages.** It shipped in 0.5.148 as that release's headline feature and was mentioned in neither: Proxmox VE appeared 5 times in the README and VMware not once. A capability nobody can find out about may as well not exist.
- **"Scan cadence" reads as "scan frequency" in Traditional Chinese.** 節奏 is not how this is said in Taiwan.
- **The per-probe intervals are laid out as an aligned three-column grid** (name, value, human-readable equivalent) instead of six full-width stacked fields. Six probes turned the dialog into a long scroll, and comparing intervals meant scrolling between them.

### Fixed
- **A traceroute now streams one hop at a time instead of showing nothing for a minute.** A hop that does not answer can only be confirmed once its timeout expires, so 15 hops take 30–60 seconds, during which the button simply looked unresponsive, with no way to tell running from hung from broken.

  The part that would have failed silently: `tracepath` block-buffers its stdout when it is a pipe, so every line arrives at once when the process exits (measured: all of it at 6.02s). The command is now run under `stdbuf -oL`, after which lines genuinely arrive at +0.02s, +3.02s and +6.03s. The response also sets `X-Accel-Buffering: no`, because nginx otherwise holds the whole stream until it completes. Without either of those, the streaming code would have looked correct and changed nothing on screen.

- **Ping now spaces its packets, and both code paths agree.** Asking for 10 pings returned instantly: it really did send and receive 10, but the whole burst finished in 53 ms. A 50 ms burst cannot show jitter or intermittent loss, and devices that rate-limit ICMP report loss that isn't real. Worse, the two paths measured different things entirely: a host that can open an ICMP socket took 0.05s, one falling back to `ping -c 10` took about 9 seconds, and which you got depended on the host. Both now space packets by 0.25s (10 pings ≈ 2.3s), verified against production.
- **The address hover card showed two unlabelled English values side by side** (`active` and `unknown`), which reads as one contradictory status. They are two different fields: what the address is recorded as, and what monitoring has actually observed. They are now separate labelled rows using the same translations as the rest of the app, so `online (scanner)` reads as 上線（scanner）. A component test asserts what is rendered, since this was purely a display defect that type-checking cannot catch.

## [0.5.148] - 2026-08-06

### Added

- **OpenAI-compatible LLM endpoints.** The provider setting adds an OpenAI-compatible mode alongside Ollama, which covers ChatGPT, vLLM, LM Studio, OpenRouter and anything else speaking that protocol, including Ollama's own `/v1` layer.

  **The default stays Ollama, and switching is deliberate.** This project's premise is that a self-hosted model keeps your data on your own network; sending subnets, hostnames and topology to an outside service is a decision for the operator to make, not a behaviour that changes on upgrade. The settings page says so explicitly when the external option is selected, rather than leaving it implied.

  The differences between the two are real and each was handled rather than papered over: different chat and embedding paths, different reply shapes, `options` (`num_ctx`) being Ollama-only and rejected elsewhere, and a model list at `/v1/models` instead of `/api/tags`; that last one would have left the model dropdown quietly empty with nothing on screen to explain it. A base URL already ending in `/v1` is not doubled. No key is sent when none is configured, because local endpoints usually want none and an empty `Bearer` reads as a failed authentication.

  The key is **encrypted at rest** (AES-GCM, its own AAD), like every other secret in this project: a paid credential should not sit in clear text in `system_settings` where a database backup or an open `psql` would show it. It is never returned to the browser; the page only reports whether one is set.

- **VMware ESXi / vCenter integration (Beta).** One implementation covers both a standalone ESXi host and vCenter: they are the same VIM API on `/sdk`, and a ContainerView absorbs the difference in inventory depth. Virtual machines land in the **same tables as Proxmox**, so topology, AI chat and the MCP `list_vms` tool needed no changes at all.

  **The SOAP is hand-written rather than using pyvmomi.** The SDK would bypass `safe_request` (the layer that performs the SSRF check, re-validates the URL after every redirect, and applies the configured TLS verification), and every other outbound integration in this project goes through it. Read-only inventory needs only five calls, so the trade of a security-architecture exception for a little convenience was not worth making. It also means no new dependency.

  Read-only throughout: nothing is ever written back to ESXi. Parsing tolerates missing fields by design, because they are genuinely absent in normal operation: a powered-off VM has no `guest.*`, a VM without VMware Tools reports no address, a template has no `runtime.host`. Continuation tokens are followed, since dropping one loses the rest of a large inventory **silently**.

  The settings page reports connection diagnostics step by step rather than a single pass/fail: which call failed is what you actually need. A wrong password surfaces VMware's own message, because VMware returns authentication failures as a SOAP Fault over HTTP 500, which otherwise reads as a bare server error.

- **The virtualisation view is split into "Virtualization (Proxmox VE)" and "Virtualization (VMware)"**, each listing only its own platform.

- **Semantic search never worked, on any installation.** The shipped default embedding model returns 4096-dimensional vectors while the database column is `vector(768)`, so every single index write raised, and the error was swallowed by a `return False`. On the production box all three tables held zero embeddings. Nothing on screen ever said so: a full-table reindex reported `{subnets: 0, ip_addresses: 0, devices: 0}`, which is indistinguishable from "there was nothing to index".

  Three changes, because the silence was the real defect: reindex now reports **how many failed and why** (the same run on production then said `failed: 97` with the mismatch spelled out); the settings page has a **Check dimension** button that asks the model for a vector and states what it returned versus what the column holds; and the default is now `granite-embedding:278m`, which is 768-dimensional.

  The replacement was chosen by testing, not by dimension count. `nomic-embed-text` is also 768 but returned **byte-identical vectors for different Chinese descriptions**: it is an English-only model, and the distinct strings collapsed to the same unknown tokens. That would have looked fixed while ranking results at random. The model that shipped was verified to produce distinct vectors for the actual descriptions in use, down to two that differ by one word.

### Changed
- **Every probe now has a configurable interval on the scan agent page**, not only the heavy ones. The backend already accepted all seven and clamped each to its own minimum; the light probes simply had no field, so they were stuck on defaults. The page also states the resulting cadence ("one round every 5 minutes"), because the fast loop is the shortest light-probe interval, a coupling that previously existed only in the code.
- **The i18n check now scans single-quoted keys too.** It only matched `t("…")`, so `t('addresses.os')` was skipped entirely, yet it was a key that did not exist and rendered as the raw key on screen. Strengthening the check found that one immediately, and it was the only one.
- **Switch-port values in the investigate view use `device@port`**, matching the address detail page. The formatter is now shared rather than duplicated: one copy would eventually drift from the other.

### Fixed
- **A full reindex could deadlock against the integration sync** and abort the whole run: it held one transaction across every row of `ip_addresses`, which `jt-ipam-sync` updates every five minutes. It now commits in batches, so the conflict window is 25 rows rather than the whole table. Found by running a reindex on production for the first time it was ever capable of succeeding.

## [0.5.147] - 2026-08-05

### Fixed
- **The Ping tool now works on hosts where `net.ipv4.ping_group_range` cannot be widened**, which is every LXC container, since the kernel belongs to the host. Install and upgrade already detected that case and verified the sysctl actually took effect rather than assuming it; they then printed instructions and left it to the operator. They now apply the alternative themselves: a systemd drop-in granting the backend `CAP_NET_RAW`, with `CapabilityBoundingSet` pinned to that one capability, which is narrower than the service's default.

  Verified on an unprivileged LXC container: the container's capability bounding set is full, so no change on the Proxmox host is needed. `AmbientCapabilities` is applied by systemd itself, so it survives `NoNewPrivileges=yes`, the setting that makes the `setcap` route silently useless.

  Both paths then **read back what the running service actually holds** and say plainly whether ping is available. Writing a unit file is not the same as it taking effect: that is precisely the trap the sysctl route fell into, where a file was written, the value never applied, and ping stayed broken while looking configured. Set `JT_IPAM_NO_NET_RAW=1` to decline the grant; every other connectivity check (TCP / UDP / TLS / HTTP) works without it.

### Changed
- **The API manual shows one section at a time** instead of being a single 16-section page that the contents list only jumped around within. The contents list marks where you are, and each section ends with links to the previous and next one, because a manual is meant to be read through, not only jumped into. Sections are grouped at runtime, so the page still reads completely with JavaScript disabled, and `#anchor` deep links, browser back/forward and the language toggle all keep working.

## [0.5.146] - 2026-08-05

### Added
- **Investigate mode.** One button on an address gathers everything known about it into a single view: the record, other records for the same address in overlapping subnets, what each source reports as its hostname and OS, monitoring coverage, ARP history, recent changes, and (for global readers) DNS, NAT and firewall rules. Contradictions are computed and shown at the top, because that is the point: sources disagreeing on the hostname, a disconnected agent still claiming the address, several MACs seen on one address.

  Tracking down the two problems fixed earlier this week meant paging through six screens each time. On the address that had macOS attributed to a Linux VM, the dossier shows the whole story at once: four sources reporting four different names, and a disconnected macOS agent still attached.

  Facts and inference stay separate: the dossier is what can be looked up, and a model's reading is only produced when asked for, labelled as inference. If the model is unavailable the feature still works: collecting the clues in one place is what saves the time, not the prose. Also available to AI chat and MCP as `investigate_ip`.

## [0.5.145] - 2026-08-05

### Added
- **Three more rules in Anomaly detection**, all computed facts rather than inference:
  - **Dangling DNS**: an A/AAAA record pointing at an address that does not exist in IPAM at all. On an external zone that is the precondition for subdomain takeover: the name still resolves, the address is unmanaged, and whoever obtains it inherits the name. Only A/AAAA are examined, since a CNAME's value is a name and would "never be found" by definition. A production zone had 8, including one pointing at a Docker bridge address.
  - **Duplicate records in overlapping subnets**: the same address recorded in two subnets where one contains the other. Two departments registering the identical CIDR is deliberate multi-tenant use and is *not* reported; containment almost always means a mistake. It matters because integrations stamp only one of the records, so the other's liveness freezes; on a production site this showed a running machine as offline with 0% availability.
  - **Suspicious changes**: bulk deletion by one account, repeated login failures from one source, and any permission/account/token change. Deletions with no actor are excluded: those are integrations replacing their own rows during a sync (one such sync deleted 967 rows in 19 minutes), and including them would put routine work at the top of the list and bury real mistakes. "Out of hours" is deliberately not a rule: it needs a reliable timezone and working-hours policy, and a rule that cries wolf trains people to ignore the whole list.

### Changed
- **Hovering a hostname in AI review evidence now shows a summary card**, as hovering an IP already did. Both are clues for checking a finding, so both should be equally cheap to check.

## [0.5.144] - 2026-08-05

### Added
- **Security configuration assessment (SCA) on the device page.** Wazuh scores each host against benchmarks (CIS, vendor-specific) and reports how many checks pass and fail; jt-ipam now stores that per agent and shows it on the Wazuh card. A host running several benchmarks shows the **lowest-scoring** one; showing the flattering number would be self-congratulation. On a production site 35 agents have data, the worst at 23/100 (112 passed, 361 failed).

  This is deliberately **not** CVE counts. Wazuh removed all vulnerability endpoints from the manager API in 4.8 (verified by listing the 150 routes this server actually exposes, none of which concern vulnerabilities), and the only remaining source is the Wazuh Indexer. That would require a second, long-lived credential able to read **every alert in the SIEM**, in exchange for two numbers, plus a dependency on an internal index name that a future release can rename. The trade is not worth it, so the integration is not offered; a brief implementation of it was removed before release rather than shipped half-considered.

### Changed
- **Integrations no longer guess when an address is ambiguous.** With overlapping subnets (two departments both using 198.51.100.0/24), the same IP string is two different machines. Wazuh built its lookup table with a dict (later rows silently overwriting earlier ones) and LibreNMS took the first row, so which record received the data depended on database row order. Both now decline to match when an address resolves to more than one record, and report how many were skipped, because attaching data to the wrong department is worse than attaching none: with no data you go and look, with wrong data you never find out, and across departments it is a data leak. Setting "limit to subnets" on the integration narrows the candidates back to one and restores matching.

## [0.5.143] - 2026-08-05

### Fixed
- **The Wazuh card claimed "0 / 0" vulnerabilities for machines that had never been checked.** The CVE fetch called `/vulnerability/*` on the Wazuh manager API, endpoints **removed in Wazuh 4.8** (the production server is 4.14.5 and returns 404). The error was swallowed, the columns stayed NULL, and the UI rendered NULL as 0. Reporting "no vulnerabilities" for something never examined is worse than reporting nothing.

## [0.5.142] - 2026-08-05

### Fixed
- **AI review findings accumulated across runs instead of replacing them.** Four scheduled runs had left 62 open findings, most of them the same handful of issues restated. The fingerprint is category plus the set of cited IPs, and the model regroups those IPs differently each time: `{.97,.46,.129} + {.54}` became `{.54,.129,.46} + {.97}` the next day, which reads as a new fingerprint. A review is a snapshot of what is wrong now, not an append-only log, so each run now reconciles the open list: findings that are still present keep their original discovery time, findings that are gone are removed, and dismissed ones are left alone as the suppression record rather than being re-inserted on every run.
- **Search results now say which subnet a record belongs to.** With overlapping subnets the same address legitimately exists more than once, and the two rows were indistinguishable, while one said online with 100% availability and the other said offline with 0%, because one subnet has scanning enabled and the other does not.
- **Hostname source tags no longer offer a delete affordance.** They are observations of what each source reported; which one is used is decided by the hostname precedence setting. Offering an X implied the choice was made there, and anything deleted came back on the next sync.

### Added
- **DHCP reservations are now synced and shown**: whether an address is bound to a specific NIC rather than handed out dynamically. Supported on every DHCP source: OPNsense (Kea reservations *and* ISC static mappings from config.xml; one production firewall uses each, so both paths are needed), pfSense static mappings, Windows DHCP reservations, and FortiGate `reserved-address`. Shown as a "Reserved" tag with the bound MAC and originating DHCP server on the address detail page, and as an icon in the address list. Entries with no IP are skipped: a static mapping without an address only identifies a NIC, it does not reserve anything.

  This matters because of the mix-up fixed in 0.5.141, where a laptop's OS was attributed to a VM: the address involved was **dynamic**, so it got recycled to another machine. A reserved address is not recycled, so "is this address pinned?" is exactly what you want to know when data appears to belong to the wrong host.

## [0.5.141] - 2026-08-04

### Added
- **External exposure detection**, as a new category in Anomaly detection: which internal hosts are reachable from outside, and whether their state justifies it. Four rules: exposed with no monitoring coverage at all, exposed while offline, exposed from an archived subnet, and DNS still pointing at an offline host. It reads only what is already synced into jt-ipam (NAT rules, firewall rules, DNS records) and never contacts a firewall or device during detection. This sits in Anomaly detection rather than AI review on purpose: these are computed facts, so they can be stated plainly rather than hedged. Also queryable through AI chat and MCP via `list_anomalies`.

### Fixed
- **A macOS host's identity was being pasted onto a Linux VM.** An IP showed OS "macOS (source: Wazuh)" while its MAC said Proxmox and no macOS VM existed. The Wazuh agent `laptop-a1.local` (a laptop, status *disconnected*) still had that DHCP address recorded from months earlier, and the address had since been recycled to a VM. Agents were matched to addresses by IP alone, so the stale claim won. A disconnected agent is now ignored when the address has been seen alive *after* the agent stopped checking in; a machine that is merely powered off (no newer liveness evidence) still keeps its data. The same rule now decides monitoring coverage in exposure detection: a disconnected agent is not watching anything.
- **Underscores inside identifiers were rendered as italics in AI chat.** `recent_ip_changes` came out as recent*ip*changes. CommonMark deliberately forbids intra-word emphasis with underscores, precisely for snake_case names; our minimal renderer did not have that condition. It also mangled identifiers inside inline code.

### Fixed
- **Every OPNsense NAT rule was recorded as disabled.** The config.xml parser tested whether the `<disabled>` element was *present*, but this firewall writes the value explicitly: `<disabled>0</disabled>` means enabled, and presence-testing read that as disabled. On a live site all 44 NAT rules showed as disabled; after the fix, 28 are enabled and 16 genuinely are not. The parser now accepts both conventions (presence-only in older configs, explicit 0/1 in newer ones), and the same class of bug on the JSON API path (`bool("0")` is `True`) is fixed with it. This is also why exposure detection initially found nothing: the data it reads was wrong, not the rule.
- **Three components used in templates were never imported**, so they silently vanished at runtime, rendering their slot content as bare text in the wrong place: the customer dropdown when editing a location, the IP filter box on a subnet's detail page, and the member-subnet tags on the VLAN page. A CI check now scans every `.vue` for `<n-…>` tags that are not imported in that file, because this class of bug passes typecheck, lint and build.

## [0.5.140] - 2026-08-04

### Added
- **AI review findings can be cleared in one go**, so the next review starts from a blank slate. This is a *delete*, deliberately not a "dismiss all": dismissing records a fingerprint so the same finding is skipped on every future run, which would have permanently buried exactly what you wanted re-examined. Dismissed findings go too: those records are what suppresses them, so keeping them would mean nothing was really cleared. The confirmation says so, and the operation is audited.
- **AI review is now listed on the feature map page** (docs/features.html). It was described on the front page but missing from the map.

### Changed
- **AI review counters use the same size as every other statistic in the product** (24px). They were 20px on the review page and 22px on the dashboard, which read as a size smaller than the KPI cards right above them.

## [0.5.139] - 2026-08-04

### Added
- **AI review findings can be filtered by category** (exposure / stale / conflict / naming / coverage / policy / other), next to the existing severity filter. Every finding already carried the tag; being able to see it but not filter on it meant picking out "all the exposed management interfaces" from a page of high-severity findings had to be done by eye.

## [0.5.138] - 2026-08-04

### Fixed
- **The device list only ever showed the first 200 devices, and the search box could not reach the rest.** A site with 272 devices saw 272 on the dashboard and "200 rows" on the Devices page. The list requested a single page, and the on-page search filtered only the rows already loaded, so a newly added device whose name sorted past the first 200 was invisible *and* unsearchable, while opening it from its rack worked fine (that view queries by rack and returns a small result set). Reported by a customer as "new devices do not show up, and searching by name does not find them either". The list now pages through the full set, and search is done by the server (name / model / serial / description, case-insensitive) so it reaches devices beyond what is loaded. Above 5,000 devices the list says how many of the total it is showing instead of silently truncating.

### Added
- **AI chat and MCP can now be asked about anomaly detection** (`list_anomalies`): IP conflicts, MAC drifts, ghost IPs, unauthorised IPs and rogue DHCP servers. AI review findings were already reachable (`list_ai_findings`). The two are deliberately reported differently: anomaly results are measured facts and can be stated plainly; AI review findings are the model's inference and come back tagged as such with their evidence. The query is read-only and explicitly does not send the notifications a scheduled scan would.

### Security
- **AI review findings were reachable through AI chat by non-admins.** In 0.5.137 every AI review REST endpoint was tightened to admin, but the MCP tool was left one tier lower (global read), so a read-only viewer with wildcard read permission could not see the page yet could ask the chat for its conclusions: the same data behind two doors with two different locks. Both the findings and the new anomaly tool are now admin-only, and the tool classification test that would have caught this now recognises the admin tier, so a future tool cannot be added without picking a tier.

### Changed
- **The sort controls in the uptime tracking dialog have icons**, matching every other button in the product; they were the only plain-text buttons left in that dialog.
- **Devices can be deleted from the device detail page.** Previously deletion existed only in the list, which is exactly where a device you cannot find is not deletable either.

## [0.5.137] - 2026-08-03

### Changed
- **Every AI review endpoint now requires admin.** Once the feature moved into the Admin area, the permissions had to match the placement; reading findings previously only needed global read, which produced the worst combination: hidden from the menu but reachable by URL. That looks like access control without being any. The route guard and the dashboard block were tightened to match (a non-admin would only have seen a block that 403s). The reason is not only placement: a review is effectively a cross-department weakness list (which segments have no monitoring, which management interfaces sit in general subnets) and should not be visible to accounts scoped to a few objects.
- **The findings list has sortable column headers** (severity / finding / date / action). Findings are long-form text and read badly as a table, so only the sortable parts became a header row. Default is severity high-to-low, with time as the tie-break so the order does not jump around between refreshes.

## [0.5.136] - 2026-08-03

### Fixed
- **The ping tool returned 500 on any machine where the unprivileged ICMP socket could be opened.** uvloop does not implement `loop.sock_sendto` / `sock_recv`, so that path raised `NotImplementedError` immediately. Machines where the socket could *not* be opened were fine, because they fall back to the external `ping`, meaning this only surfaced after following our own instructions to widen `net.ipv4.ping_group_range`. Fallbacks added; verified against localhost and the gateway.

### Changed
- **Anomaly detection can be limited to chosen subnets** (the "Scope" button on the Anomalies page, or "Include in anomaly detection" on the subnet edit page; both write the same field). Guest, lab and contractor segments are noisy by nature; excluding them stops the findings that matter from being buried.
- **Unauthorised IPs are no longer flooded with 169.254.x.x.** Those are link-local addresses a machine assigns itself when DHCP fails, a symptom of "no address", not of someone plugging in a rogue device. On a production site all 53 entries were this. Multicast and reserved addresses, subnet network/broadcast addresses (which map to no machine), and anything outside every subnet are excluded too.
- **AI review moved into the Admin area, right after Anomaly detection.** Both look for problems, but they are **deliberately not merged**: anomaly detection reports measured facts (ARP really did see two MACs), while AI review is a model's inference and can be wrong. One combined list would make it impossible to tell which conclusions can simply be trusted.
- **When ping cannot send, there is now a "How to fix" link next to the error**, opening two options you can actually follow (widen `ping_group_range`, or grant the service `CAP_NET_RAW`) with the difference in privilege spelled out. Before it only said what was broken.
- **Connectivity diagnostics moved to its own tab.** These tools really do send packets from the server, are rate-limited and audited, quite unlike the pure calculators above them on the same page.
- **AI review: high and medium findings get a coloured bar down the left**, so the ones worth reading first are obvious. Low findings get none: a bar on every row is no emphasis at all. The counters are now bordered cards, matching the dashboard KPIs.
- **AI review body text no longer wraps early.** It had a 78ch line cap, but a Chinese character is about two `ch`, so that worked out to 39 characters; a wide screen showed a narrow column with a large empty margin.
- **Uptime tracking can be sorted** (in Edit tracking list): by IP, hostname or uptime, ascending or descending. Rows with no data always sort last, since that is "unknown", not "0%".

## [0.5.135] - 2026-08-03

### Added
- **Rogue DHCP server detection** (scan agent). The agent broadcasts one standard DHCPDISCOVER on the segment and records everything that answers; **any host handing out addresses that is not marked as a DHCP server** is listed in a red banner at the top of the Anomalies page, with its address, subnet, MAC, vendor, the address it offered and the gateway it pointed at. This is one of the few findings that almost always means something real: usually a consumer router someone plugged in, or a VM with DHCP left on, handing wrong addresses and gateways to the whole segment.
  - **Off by default**: it broadcasts on the segment, so whether to do it is decided per subnet in that subnet's scan settings.
  - Sends only DISCOVER, never REQUEST; it does not actually take an address.
  - Whether a server is legitimate is decided **at query time**, not baked into the sighting: mark one as legitimate later and the old records follow, rather than leaving a permanently wrong "rogue" label behind.
  - The comparison is per subnet: with overlapping ranges (several units sharing 198.51.100.0/24), one segment's marking is never mistaken for another's authorisation.
  - Relayed offers are not flagged: that server was never on this segment to begin with.
- **Each subnet can be included in or excluded from the AI review**: a tick box on the subnet edit page, or a multi-select under Admin → LLM / AI. Both write **the same field**, so either place works. Sensitive segments can be excluded entirely and are never sent to the model.
- **AI review**: have the language model look over the IPAM data this system manages and flag what is suspicious, inconsistent or a security concern (addresses recorded as in use but never seen alive, hosts whose name and role disagree, duplicate or contradictory records, subnets with no monitoring coverage at all…). Three entry points: a new **AI review** page in the sidebar, a summary block on the dashboard, and an on/off switch plus schedule under Admin → LLM / AI (off by default). The schedule rides on the existing sync timer rather than adding a job; the page also has a **Run now** button so you do not have to wait for it.
  - **Sampling goes through permissions first**: a review only sees what the account it runs as can see, rather than handing the whole database to the model.
  - **Every finding carries its evidence**, with the IPs clickable so you can check the claim. Without evidence a finding is just an assertion you cannot verify.
  - **Model output is treated as untrusted input**: invented severities and categories are downgraded, fields are truncated, and anything that will not parse is discarded whole.
  - "Nothing found this time" and "the model is broken" are kept apart: conflate them and you either report a failure as all-clear, or all-clear as a failure.
  - Findings are written in **the language of the account that ran them** (Traditional Chinese / English).
  - **The model used for the review can be set separately** (Admin → LLM / AI). Leave it empty to use the AI chat model. A review sends a lot of data in one batch, which is a different trade-off from interactive chat: point it at a larger model for better judgement, or a smaller one to save compute.
  - **The schedule is a list of times of day, not an interval**; add as many as you want. An interval drifts with each run, and after a few days nobody can say whether it hits the LLM at 3am or mid-morning. Times follow the server's timezone, which the settings page states outright.
  - **The inventory is split into batches sized to the model's context**, and the run reports which batch it is on, how many addresses are in scope, which model is being used and how many findings so far, with a progress bar and elapsed time.
  - **Runs as a background job**: "Run now" returns immediately and the review continues on the server. Close the tab, switch pages, reload: the progress and the result are still there when you come back. It used to be tied to the connection, so leaving the page cancelled the whole thing and ten minutes of work was simply gone.
  - Pressing it again while one is running is refused (409) rather than queued: two at once is not faster, because they starve the same LLM.
  - **Caps the output length of each batch**: a model was observed stuck in a repetition loop, writing 54,000 characters in one batch and burning the entire timeout before failing.
  - **Dismissing is permanent but reversible.** Once a finding is dismissed as a false positive, later reviews file the same finding straight into Dismissed instead of surfacing it again every day. Matching uses a fingerprint of category plus the evidence IP list, not the title: the model rewords titles every run, so title matching would almost never hit. Pressed it by mistake? Switch to "Dismissed" and press Restore.
  - **Turns off the model's thinking mode**: thinking counts against the output budget; gemma4 was observed writing 10,401 characters of thinking in one batch, which truncated the actual answer and made all three batches unparseable, storing nothing. With it off, the same data produced 5 findings. Older Ollama versions that reject the parameter are automatically retried without it.
  - **The review's context length (num_ctx) can be set separately**, empty meaning inherit from the chat model. It decides how many records fit in one batch: larger means fewer batches and a faster run, at the cost of memory/VRAM.
  - **Truncated JSON keeps the findings that were completed**, instead of discarding the batch. Only a response with no complete finding at all counts as a failure.
  - The schedule's "last run" is recorded on its own rather than inferred from the newest finding; otherwise a clean review writes nothing, the scheduler reads that as "never ran", and hits the LLM again every sync cycle (~5 minutes).
- **AI chat and MCP catch up with the recent features**: `list_firewalls` now covers OPNsense, pfSense and FortiGate together (returning only one vendor lets the model present half a list as the whole thing); new `list_dhcp_ranges` (DHCP pool ranges synced from the integrations), `list_fortigate_policies`, `list_fortigate_addresses` and `list_ai_findings`. The system prompt now also mentions certificate custody and distribution, DHCP ranges and review findings.

### Fixed

- **The BIND 9 integration could not work at all** (customer report, since v0.5.129): it connects, shows as enabled, syncs without error, and returns zero DNS records, always. Two things were saved but never took effect:
  - **There was no field for the zone list.** DNS has no way to enumerate zones, so the sync only reads zones that are listed explicitly, and the form had nowhere to list them, so no AXFR ever happened. The settings page now has a "Zones" field (reverse zones included), and a sync with none configured **fails with a clear message** instead of quietly returning nothing.
  - **The TSIG key was never split.** The field asks for `algorithm:keyname:base64key`, but the backend treated the whole string as the secret and read the key name from somewhere that was never written, so the key name was always empty, which is the same as having no TSIG, and BIND refuses the transfer. It is now parsed as the placeholder describes.
  - Also fixed: BIND9 settings were only written to extra_config when a username or TLS-verify option was present: BIND9 has neither, so even the zone list was discarded.

- **Sending the whole inventory in one request overflowed the context, got truncated, and the model answered with prose, while the screen looked like "finished, nothing found".** In production, 360 addresses came to ~75,000 characters, far past `num_ctx=16384`; Ollama quietly drops the front of the prompt, so the model received half an instruction set and wrote a "network overview" essay instead. Fixed in three places: the data is **split into batches** sized to the context, Ollama is asked for `format=json` to force structured output, and the token estimate counts **CJK characters as one token each** (estimating Chinese at "4 characters per token" undercounts badly).
- **A failed run was invisible on screen.** The error only appeared as a toast that disappears, while the "last run" timestamp updated anyway; together those read as success. Failures now leave a persistent error on the page, including what the model actually replied.
- **500s from mismatched timestamp types**: the model declared a naive `DateTime` while the column is `timestamptz`; reads were fine, but writing a timezone-aware value blew up. This broke dismissing a review finding entirely, and circuits' install / contract-end dates (a 500 when set through the API with a timezone). Added a test that sweeps every timestamp column across all models so it does not happen again.

## [0.5.133] - 2026-08-02

### Added
- **Three more diagnostics in Tools → IP addresses**, all requiring no privileges:
  - **TLS certificate check**, showing what the host actually serves: subject, issuer, validity with days remaining colour-coded, SAN, negotiated version and cipher. It fetches the certificate *without* verifying first, on purpose: a self-signed, expired or wrong-name certificate is exactly what you need to look at, and refusing to show it would defeat the point. Whether it validates against the system trust store, whether the name matches and whether it is self-signed are reported as separate columns rather than collapsing into "failed".
  - **HTTP check**: status, the full redirect chain and the headers worth seeing (Server, Content-Type, HSTS).
  - **Bulk reverse DNS**: which addresses in a range have a PTR and which do not, with a count. Establishing that one lookup at a time is tedious enough that people skip it.
- **A device's detail page now shows when it is a DHCP server**, with a tooltip naming which of its addresses carries the role. The flag already existed and the IP list already displayed it; looking at the device gave no hint, so the same host told you different things depending on which page you opened.
- The same role tags (gateway, DHCP server, in DHCP range) now appear on the IP detail page, which was showing less than the list it was reached from.

### Fixed
- **The connectivity diagnostics are no longer half in two columns and half in one.** Every one of them produces a result table, and half-width was too narrow: the TCP card was breaking "Connection refused" mid-word into "Connectio n refused". They are now uniformly full-width, and table cells no longer break inside a word. The calculators above stay in two columns: they are compact key-value widgets, so being consistently different reads better than forcing them to match.

## [0.5.132] - 2026-08-02

### Added
- **Certificate distribution for WinRM, Remote Desktop and LDAPS.** WinRM matters here because jt-ipam is itself a WinRM client (the Windows DNS and DHCP integrations talk over 5986), so a proper certificate on those hosts is what lets TLS verification be turned on at the jt-ipam end instead of left off. Both were verified on a real Windows host, each confirmed from outside with `openssl s_client` rather than by trusting the agent's own log.
  - Remote Desktop keeps its thumbprint in WMI rather than http.sys, so that profile writes there and then confirms over TLS. A failed probe does not by itself trigger a rollback: the setting is read back first, because Remote Desktop simply being switched off is not the same as the change having failed.
  - LDAPS reads from the service store `NTDS\My`, not `LocalMachine\My`; a certificate placed in the usual store does nothing for it. The `store` profile now takes a target store, and after writing to an NTDS store it asks the domain controller to reload via the rootDSE `renewServerCertificate` operation, since otherwise it keeps serving the old certificate until it rotates on its own. **This path is not verified on real hardware** (it needs a domain controller); IIS, WinRM and Remote Desktop are.
  - When WinRM refuses a certificate the agent now says why: the certificate's CN/SAN must include the host's own name and carry the Server Authentication EKU. The raw `WSManFault` gives no hint of that.

- **UDP port probing**, reported in three states rather than two. UDP has no handshake, so silence proves nothing: the port may be open but not replying, filtered, or the packet may have been lost. Calling that "open" would be quietly wrong, so it is its own state and the operator judges. "Closed" means an ICMP port unreachable came back, which a connected UDP socket surfaces without needing raw sockets. Ports 53 and 123 get a real DNS query and NTP client packet, so a protocol reply (decoded to `DNS NOERROR`, `NTP stratum 3` and so on) is what makes them definitively open.

### Fixed
- **`upgrade` never installed OS packages, so existing deployments got new features without the binaries they need.** The ping and traceroute tools added in 0.5.131 were only pulled in on a fresh install. Both paths now run the same check, which installs only what is missing and never fails the run.
- Version information (admin) now lists optional dependencies and whether they are present, so a missing package is visible there rather than surfacing as a tool that silently does nothing.
- Output from external commands is trimmed before it reaches a report field: a localized `WSManFault` plus a PowerShell error record ran to several hundred characters and buried the actual message.

## [0.5.131] - 2026-08-02

### Added
- **TWNIC import now works**; it had been a "planned" placeholder. Both registries are now queried live over RDAP: RIPE directly, TWNIC via APNIC, which redirects Taiwanese networks to TWNIC's own database (APNIC is authoritative for Taiwan; TWNIC is its national registry). The preview shows the registration (netname, country, address range, allocation type, contacts, remarks and the URL the data came from) before anything is written.
  - RDAP cannot find a network from a handle: APNIC returns 404 for entity lookups and RIPE's response carries no networks. The Handle field promised something the protocol cannot deliver, so it is gone; searching by handle or organisation is now done by pasting whois output, which the existing parser handles.
  - **The RIPE tab was broken too**: the page posted JSON while the endpoint expected a file upload, so it answered 422. Neither field had ever been connected to anything.
- **Connectivity diagnostics in Tools → IP addresses**: ping (many targets at once, with a concurrency setting), traceroute and a TCP port check. Targets accept IPs, hostnames or a CIDR that expands to its hosts.
  - Nothing goes through a shell: commands are executed with an argument list, and targets are validated as addresses or hostnames as a second line of defence. Target count, concurrency, per-target timeout and an overall deadline are all capped, and each run is rate-limited per user and written to the audit log, so the server cannot be turned into a scanner.
  - Traceroute prefers `tracepath`: it needs no privileges and reports path MTU, which `traceroute` does not. Hops that do not answer are listed rather than omitted: hiding them makes a path look like it goes 1→3→5 with nothing in between. If it runs out of time the hops found so far are returned rather than discarded.
  - The TCP check is often more useful than ping: a host that drops ICMP still answers on the port you actually care about.

### Fixed
- **The IP conflict list showed no MAC addresses at all.** The renderer decided an array was location data if its entries had a `last_seen_at` field (which the MAC entries also have), so it drew "device / port" columns for objects that have neither, leaving a table of dashes and omitting the one thing the report exists to show.
- **Conflicts are now readable.** Each MAC carries its OUI vendor, and addresses with the locally-administered bit set are labelled as such. On a real deployment 64 of 133 conflicting MACs are locally administered (virtual machines, containers and phone MAC randomisation), so an IP showing a real MAC alongside a randomised one is usually one device that changed address, not two fighting over an IP. Every anomaly category now also explains what it means and why entries appear. (The vendor lookup was itself wrong at first: `vendor_map()` is keyed by the normalised 6-digit prefix, not the full MAC, so every vendor came back empty, silently, with no error.)
- `detect_ip_conflicts` returned the raw `IPv4Address` and MAC objects asyncpg produces for INET/MACADDR columns instead of strings (known pitfall #10).

## [0.5.130] - 2026-08-01

### Changed
- **Windows certificate distribution is now documented as Windows Server 2019 and later.** Server 2016 is dropped as a supported target. The PKCS#12 handed to the agent stays PBESv1-SHA1-3DES, but for a different reason than before: it is the form every version of the Windows CryptoAPI accepts, and the encryption here guards nothing an attacker can reach. The blob is generated per request, encrypted with a random password that only ever lives in memory, and imported and discarded without ever touching the disk. Trading a known-working path for a stronger algorithm that protects nothing was not worth it. (Verified on a real host: both PBESv1 and PBESv2 import fine on current builds, so this is a compatibility floor, not a limitation.)

### Fixed
- **Every dashboard card now has an icon in its header, and the icon, title and count tag line up.** Only the availability card had an icon, which made it look bolted on rather than part of the page. The alignment was off because the header was laid out with a spacing component that wraps each child separately, so an 18px icon, a line of text and a 22px tag each sat on their own baseline. Card headers now go through one small shared component with a single flex rule, so alignment is decided in one place rather than per card (measured at 0.01px across all ten).

## [0.5.129] - 2026-08-01

### Fixed
- **The Windows scheduled task was missing the properties that make the Linux timer reliable.** The bash agent runs as a `Type=oneshot` unit driven by a systemd timer with `RandomizedDelaySec=600` and `Persistent=true`; the Windows task had neither, so every host would poll on the same second and a run missed because the machine was off was simply lost. It now sets `-RandomDelay 10m` and `-StartWhenAvailable` to match.
  - Two more come from Task Scheduler defaults that have no systemd equivalent and are wrong here: it **refuses to start a task on battery power and stops one that switches to battery**, which would silently skip renewals on a laptop or on a VM that reports a battery. Both are now disabled. `ExecutionTimeLimit` is also capped at an hour; the default is three days, long enough for one hung run to block every later one.
  - For the record, since it comes up: the agent is a scheduled task rather than a Windows service **because that is what the Linux one is**, a one-shot process run on a timer, not a resident daemon. A service would mean writing a sleep loop for no benefit.

## [0.5.128] - 2026-08-01

### Fixed
Found by running the new Windows agent against a real Windows 11 + IIS host, not by review. Two of them reported success while doing nothing at all.

- **A second deployment of the same certificate was silently skipped and reported as done.** Deployment state was keyed on certificate + profile only, so a host serving one certificate on two bindings (two SNI sites on 443, say) only ever updated the first. The second was treated as "already up to date" forever: never renewed, while reporting ok. State is now also keyed on what makes the deployment distinct (the binding for `iis`, the output paths for `files`). **The same flaw was in the shipped bash agent** for manual-mode deployments writing one certificate to several paths, and is fixed there too (agent 0.4.174). State written by an older agent is still honoured, but only for deployments that have no distinct target.
- **On a host with several IIS sites, the SNI bindings were never given a certificate, and it reported success.** Deciding "is the right certificate already bound?" was done by opening a TLS connection, but an SNI binding with nothing registered is still answered by the catch-all non-SNI binding on the same port. When that fallback happened to serve the certificate being deployed, the agent concluded it was already bound, reported ok and recorded the deployment as done, while `netsh http show sslcert` showed no registration at all for that hostname. The question is now put to http.sys about that specific binding instead, matching the 40-hex-digit thumbprint value rather than netsh's localized labels.
- **The Windows installer could never register its scheduled task.** `schtasks /TR` takes the whole command as one argument and its quoting mangles any path containing a space, which the default install path under `C:\Program Files` always does. Switched to `Register-ScheduledTask`, which passes the argument string through verbatim. The task principal is set by SID rather than the name "SYSTEM", which is localized.
- A failed IIS deployment with no previously bound certificate left behind an http.sys registration on a port no site answers on; it is now removed, so a failure leaves nothing behind.
- A missing or incomplete config printed a PowerShell stack trace that buried the actual message. It now prints one readable line and exits 2.

## [0.5.127] - 2026-08-01

### Added
- **Certificate distribution to Windows / IIS**, via a new PowerShell agent (`agent/jt_ipam_cert_agent.ps1`) alongside the existing bash one. Windows PowerShell 5.1 (built into Windows Server 2016 and later) is all it needs: no modules, no Python, no OpenSSL.
  - IIS does not read certificates from files; it binds one held in the Windows certificate store, by thumbprint. So rather than "write files, test config, reload", the agent **imports the certificate, repoints the HTTPS binding, then opens a real TLS connection to check which certificate is actually being served**, and puts the previous one back if it is not the expected one. Verifying by observation rather than by a command's exit code also means it does not depend on parsing `netsh` output, which is localized.
  - The PKCS#12 handed to Windows is deliberately encrypted with **PBESv1-SHA1-3DES**. The library default (PBESv2/AES-256-CBC) cannot be imported by the CryptoAPI on Server 2016/2012R2, and it fails with a misleading "the password is incorrect". The agent generates a random password per run and keeps it in memory, so the private key is never written to disk on the way in.
  - Three deployment profiles: `iis` (import + rebind), `store` (import only, for Exchange / RD Gateway / your own software that takes a thumbprint) and `files` (write PEM/PFX to paths you choose, then run a command). Private-key files get an ACL of SYSTEM + Administrators only, set by well-known SID rather than by group name, because the name is localized on non-English Windows.
  - `jt-ipam-cert-agent-installer.ps1` registers a daily Task Scheduler job running as SYSTEM, and supports `-Uninstall`. The agent self-updates against the server the same way the bash one does.
  - The certificate agent page now has a Linux / Windows switch that changes the install commands, the supported-OS list, the deployment profiles and the config generator. The "latest agent version" indicator shows both agents, since they version independently.

### Changed
- `GET /cert-agents/bundle/raw?part=pkcs12` accepts an `X-Pfx-Password` header, which also selects the Windows-compatible PKCS#12 encryption. Without the header the behaviour is unchanged (unencrypted), so the existing jetty profile is unaffected.

## [0.5.126] - 2026-08-01

### Fixed
- **Dashboard availability watchlist showed a raw UUID instead of the IP once you typed in the picker.** Searching replaced the whole option list with the matches, so the option backing an already-selected IP disappeared, and with no option to resolve, the select fell back to rendering its raw value. Selected entries now keep their label via a local cache and are always merged into the option list. An IP that has become inaccessible shows an explicit note rather than a UUID.

## [0.5.125] - 2026-07-31

### Added
- **Availability watchlist on the dashboard**: a full-width block where you pick the IPs you care about (up to 30) and see all of their 90-day bars stacked and aligned, each with its uptime percentage. Rows link through to the IP. The selection is stored per account in the existing generic `user_preferences.pinned` map, so it follows you across browsers and needs no schema change.
  - Backed by a new `POST /api/v1/addresses/uptime/batch`, which does two queries regardless of how many IPs are requested; calling the per-IP endpoint thirty times would have been sixty round trips. It returns one series *per IP* (unlike the device endpoint, which merges an entire device into one), preserves the order you arranged them in, and **silently drops IPs you cannot see** rather than erroring, so the block does not break when permissions change.
  - The same honesty rules as the detail-page bar: an IP with no liveness source is entirely grey and its percentage shows a dash rather than 0% or 100%.

## [0.5.124] - 2026-07-31

### Fixed
- **PVE console failed for accounts with two-factor authentication enabled** (GitHub issue #23, reported by @kelp45705753-bit). Proxmox answers `/access/ticket` for a TFA-enabled account with **HTTP 200** and a *challenge* ticket (`{"ticket": "PVE:!tfa!…", "NeedTFA": 1}`), not an error. That was taken as a normal ticket, so the failure only surfaced later when opening the websocket, with a message that gave no hint of the real cause. Login now detects the challenge and exchanges it for a real ticket using `tfa-challenge` plus `password=totp:<code>`; if no code was supplied it returns a distinct `tfa_required` so the console asks for the 6-digit code instead of dropping into an opaque error. A wrong or expired code is reported as such rather than being passed on to the websocket. Accounts without TFA still make a single request.

### Changed
- Terminology: replaced "詳情" with "詳細資料" and "膠囊" with plainer wording across comments and the Chinese changelog (Taiwan usage).

### Notes
- The TFA exchange follows the documented Proxmox flow but **could not be tested against a live TFA-enabled PVE account**; the unit tests cover the challenge, the successful exchange, a wrong code and the untouched non-TFA path.

## [0.5.123] - 2026-07-31

### Fixed
- **A never-interrupted IP was drawn as "not monitored".** The bar was reconstructed purely from `effective_status` transitions, but an IP that has been up ever since it was added produces *no transitions at all*, so it came out entirely grey even while the page above it showed "online, last seen 30 seconds ago". "No transitions" is not "no monitoring". The reconstruction now also reads the IP's current status and `last_seen_*`: with a liveness source and no transitions in the window, the current state is backfilled from when the IP was added (earlier than that stays unknown). Two real production IPs went from 90 grey days to 67 green days at 100%.
- **A month of continuous downtime looked like a month of separate blips.** Every day with any downtime was amber, so an IP offline since early July rendered as 29 identical amber marks. Days are now split: amber means the day had both up and down time (a real outage window), red means it was down all day. The same production IP now reads as 29 red days and 2 amber, which is what actually happened.

### Changed
- Bars are square rather than pill-shaped, and the cursor is a pointer over them since each one has a tooltip.

## [0.5.122] - 2026-07-31

### Added
- **Availability bar on the IP detail and device detail pages**: a 90-day status-page style strip, green for up, amber for a day with an outage, grey for no data. Device bars merge every IP on that device: a day is marked as an outage if *any* of its IPs went down, so a single failed interface still surfaces.
  - There is no per-IP time series in the schema, so daily state is *reconstructed* from the `effective_status` transitions already recorded in `ip_change_log`: a state holds until the next transition, and anything before the first transition is unknown.
  - **Days without data are grey, never green.** An IP with no liveness source (scan agent or LibreNMS) shows an entirely grey bar, which is the honest signal: it means "not monitored", not "was fine". A tooltip says so.
  - **The uptime percentage counts only days that have data.** An IP monitored for three days, all up, reads 100% rather than being diluted by 87 grey days or scored as if grey were downtime. The denominator is shown next to the figure so the number cannot be read out of context.
  - Grey uses the Naive UI theme variable rather than a fixed colour, so it stays subtle in dark mode; green and amber are fixed because they are status semantics that read correctly in both themes.

## [0.5.121] - 2026-07-31

### Fixed
- **FortiGate VPN sync could not distinguish "nothing connected" from "endpoint unreadable".** Both produced `ssl_sessions: 0`, because a failing endpoint was swallowed with `except FortiGateError: continue`. A customer's real sync reported exactly that, and there was no way to tell from the audit summary whether the SSL-VPN parsing worked at all. The summary now carries `ssl_unavailable` / `ipsec_unavailable` when every VDOM's endpoint failed, so a genuine zero and a silent failure look different, which matters most for an integration developed without a live device.

### Notes
- **FortiGate is now validated against a real appliance** for VDOM discovery, ARP (454), DHCP leases (339), DHCP ranges (3), address objects (632), firewall policies (211), NAT (14) and IPsec tunnels (4), thanks to a customer enabling every sync toggle. SSL-VPN sessions reported 0; with the change above, a future run will say whether that means "nobody connected" or "endpoint unreadable".

## [0.5.120] - 2026-07-31

### Fixed
- **The audit log's Target column showed a truncated UUID for every integration.** A customer testing FortiGate spotted rows reading `a1b2c3d4…` instead of the instance name; with several instances of the same type, the log could not tell you which one had synced. `_LABEL_REGISTRY` only covered 14 object types; every integration instance, agent, certificate and API token fell through to the raw id. Added 26 more (all verified to resolve against their model and column), and integration rows now link to their settings page instead of rendering as plain text. A test pins that every registry entry resolves and that no integration type is missing, since adding an integration without registering it silently regresses to UUIDs.

## [0.5.119] - 2026-07-30

### Added
- **LibreNMS LLDP / CDP neighbour sync.** LibreNMS discovers link-layer neighbours via its `xdp` module; jt-ipam now mirrors them into `librenms_links`. Unlike the existing FDB/ARP inference (which learns adjacency from observed traffic and uses "the port with the fewest MACs is the access port" as a heuristic), LLDP/CDP is *declared by the far end*, so switch-to-switch trunks come out correctly. That is precisely where the FDB heuristic is weakest, because a trunk port carries many MACs. Neighbours whose far end is not itself monitored are kept too: they only carry the LLDP-advertised hostname/platform strings, which is exactly the signal for "this port goes to an unmanaged device". Per-instance toggle (`sync_links`, on by default), read endpoint `GET /api/v1/librenms/links` at global read, and migration 0101.
  - **An empty source is not an error.** Verified against a live LibreNMS: when nothing has been discovered, `GET /api/v0/resources/links` returns `404` with `{"message": "Links do not exist"}`. That is a valid state, not a failure, and is treated as zero rows; otherwise one environment without LLDP enabled would break the whole LibreNMS sync round.

### Notes
- **Endpoint paths were confirmed against a live instance; the field parsing was not.** The production LibreNMS (82 devices) has an empty `links` table, so there was no real payload to validate against; every field is therefore read tolerantly and a rename between LibreNMS versions degrades to a blank column rather than a failed sync. Field names follow the LibreNMS `links` schema. `/api/v0/resources/links/all` does not exist; it is parsed as `links/{id}` and returns `400`.
- No install/upgrade changes.

## [0.5.118] - 2026-07-30

### Security
- **Disabling TOTP now requires re-authentication (A07).** `POST /auth/totp/disable` previously accepted any valid session, so anyone holding an access token (via XSS, a stolen token, an unlocked screen, or an unrestricted API token) could turn off an account's 2FA in one request and leave it password-only. It was audited, but auditing detects rather than prevents. Local accounts must now supply their current password; externally-authenticated accounts (LDAP/OIDC/SAML, which have no local password hash) must supply a current 6-digit code. Change-password in the same file already required the current password, which is what made this look like an oversight rather than a decision.
- **The last active admin can no longer be demoted or deactivated.** `DELETE /users/{id}` already refused to remove the last admin, but `PATCH` could achieve the same outcome with `is_admin: false` or `is_active: false`, permanently locking everyone out of the admin area (audit, users, integrations, system settings) with recovery only via shell access to the server. `PATCH` now returns `409` to match.
- **Webhook notifications now pass through the SSRF guard.** `notify_channels._post` deliberately bypassed `safe_request`, reasoning that targets are admin-configured and equivalent to SMTP. But on a non-2xx response it puts the first 200 bytes of the body into an error message that surfaces in the settings page and `last_error`, which made it an admin-only primitive for reading a slice of any internal URL, such as cloud metadata. It now calls `assert_url_safe()` (the same check the other twenty services use) while keeping `follow_redirects=False`.

### Fixed
- **`GET /addresses` leaked a global count in `total`.** The same defect fixed in 0.5.116 for sections and subnets was still present on the largest table: the count query carried the subnet/section/archived filters but never the visibility condition, which was applied only to rows after pagination. A restricted account saw a total far larger than what it could see, and pagination was broken.
- **Two MCP tools mishandled overlapping subnets.** `switch_port_for_ip` queried `IPAddress.ip == ip` with no scope and no `limit(1)` before `scalar_one_or_none()`, so in an overlapping-subnet deployment (several customers sharing `198.51.100.0/24`, the product's core scenario), it raised `MultipleResultsFound` and the tool failed outright. Both it and `get_ip_detail` also checked visibility *after* picking an arbitrary row, so picking a row in an invisible subnet reported "IP not found" even when the caller could see the same IP in another subnet. Both now scope the query first and then take one row.
- **The Permissions page could not grant rack or location permissions.** It requested `/api/v1/locations/racks` and `/api/v1/locations/locations`; the real paths are `/api/v1/racks` and `/api/v1/locations`, so both were swallowed by `/locations/{location_id}`, failed UUID parsing and returned `400`, leaving those two object lists permanently empty.
- **Nine i18n keys were never translated** and rendered as raw keys: the task trigger column and its two values, four BMC serial-console troubleshooting entries, and the two MAC columns on the connections page. Also removed two orphan keys that existed only in en-US.

### Changed
- **The Advanced menu now hides integration views that have nothing behind them.** Firewall (OPNsense / pfSense / FortiGate), Virtualization, DNS records and Certificate distribution only appear once that integration has at least one instance configured; otherwise the page could only ever say "not configured yet". Backed by a new `GET /system/integration-presence` that returns booleans only and is gated at global read, so non-admins with global read still get a correct menu.

### Notes
- No install/upgrade changes and no migration.
- Two of these were found by rendering every route in both locales in a real browser, and two more by scanning for the specific defect patterns that earlier releases had already been bitten by. Neither `vue-tsc`, ESLint nor the production build catches this class.

## [0.5.117] - 2026-07-30

### Fixed
- **"Test connection" on FortiGate could appear frozen for ~100 seconds.** The diagnostics ran its 10 endpoint probes sequentially, so against an unreachable appliance (a mistyped IP or a firewall dropping packets, which is exactly what you hit the first time you configure one), each probe waited out its own 10-second timeout and they accumulated. The probes are independent GETs, so they now run concurrently: measured 11.9s instead of ~100s, with every endpoint still reporting its own result. Found by actually clicking the button in a browser rather than by reading the code.
- **A permission error reported itself as a connection failure.** Opening a global-infrastructure page (e.g. VLAN) as an account without global read produced the toast "連線失敗，請稍後再試"; the backend had correctly returned `403` and leaked no data, but the message told the user the system was broken rather than that they lacked permission. `403` is now localized centrally in the API client, and the 48 `catch` blocks that unconditionally reported a connection failure now prefer the backend's message (via a new `apiErrMsg()` helper), so permission and validation errors stop being mislabelled.
- **VLAN page did not grey out its write buttons** for read-only accounts, unlike the equivalent VRF / NAT / Physical pages: a user with no write permission could open the create form and only fail on submit. Wired `can_edit` into both create buttons, both edit buttons and both delete confirmations.

### Notes
- The `can_edit` gating rejected in 0.5.116 was rejected for *admin-only* pages, where it is genuinely dead code (anyone who can open them is an admin, and `can_edit` is unconditionally true for admins). VLAN is reachable with only global read, so there the gating does matter; this corrects that earlier judgement.
- No install/upgrade changes and no migration.

## [0.5.116] - 2026-07-30

### Security
- **API token `scopes` are now actually enforced.** The column existed and could be set, but **nothing in the codebase ever read it**: a token created with `scopes: ["read"]` could still delete subnets, because tokens simply inherited their owner's full RBAC permissions. A read-only token now gets `403` on `POST`/`PATCH`/`PUT`/`DELETE`, enforced at all three places that accept a `jt_` token: the REST API, the phpIPAM compatibility layer, and MCP (which reuses its existing read-only mode, since JSON-RPC is always `POST`).
  - `scopes: []` still means unrestricted, so **existing tokens keep working**.
  - Creating a token with any other scope value (`write`, `subnets:read`, …) is now rejected with `422` rather than silently accepted and ignored.
  - Exception: `DELETE /api/phpipam/<app>/user/` (revoking your own token) stays allowed for read-only tokens, because that reduces privilege rather than changing data, and blocking it would break the classic login → query → logout flow.
  - `object_filters` is still **not** enforced. To restrict a token to specific objects, create a low-privilege user, grant it those objects via RBAC, and create the token as that user. The field is now documented as reserved in both the API schema and the UI.

### Security
- **RBAC audit across recently-added features: six real gaps closed.** Every claim below was verified against the code before changing anything.
  - **GraphQL was a parallel API surface that RBAC had never caught up with.** It is not a FastAPI route, so it does not appear in dependency-tree scans and was nearly missed entirely. Three resolvers had no authorization at all: `devices` (any authenticated account could enumerate every device), `vlans` (bypassed the `require_global_read` that guards `GET /api/v1/vlans`), and `trace_ip` (ARP/FDB lookup, which can resolve any IP to its switch and port). All three now apply the same checks as their REST counterparts, via a `_assert_global_read()` helper that mirrors `require_global_read` exactly.
  - **IDOR on locations**: `GET /locations/{id}` and `GET /locations/{id}/floorplan` only required *authentication*, so any signed-in account could read any location and download any machine-room floor plan by id. Both now require `require_object_perm("location", "read")`. A systematic scan of every per-object detail endpoint confirmed these two were the only gaps.
  - **`total` leaked global counts** on `GET /sections` and `GET /subnets`: rows were filtered *after* pagination while the count query had no visibility condition, so a restricted account learned how many sections/subnets exist system-wide, and pagination was broken (pages returned fewer than `page_size` rows, sometimes none). Both now apply the visible-id filter to the query *before* paginating, so `total` is the visible count.
  - **Firewall read-only views were inconsistent**: pfSense's rules/aliases were admin-only while the frontend「防火牆 (pfSense)」view page sits under Advanced (not Admin), so a non-admin with global read saw the menu entry and hit 403. FortiGate had the same defect from a different angle: its view page needs `GET /fortigate` to enumerate firewalls, and that endpoint was on the admin router. Both are now split consistently: stored read data and the instance list are `require_global_read`; writes and the live device fetch (`GET /pfsense/{id}/nat`) stay `require_admin`. Verified first that neither read schema exposes a token (`has_key` is only a boolean flag), with a test pinning that.
- Ten regression tests added, each reverse-verified: removing the corresponding fix turns its test red. That step caught a flaw in the tests themselves: the `total` tests originally used a zero-permission account, which short-circuits before the count query runs and so could not detect the leak at all; they now use *partial* visibility (three objects, one granted).

### Notes
- Three audit findings were investigated and **rejected** as incorrect: `/customers/{id}/summary` does not leak (a read grant on a customer legitimately inherits down to its sections/subnets/IPs/devices, pinned by a test on the inheritance table); `/vlans` and `/vrfs` are `require_global_read`, not `require_admin`; and the sidebar already hides VLAN/VRF/NAT for accounts without global read.
- Adding `can_edit` gating to the integration admin pages was also rejected: those routes are `meta: { admin: true }` and `can_edit` is unconditionally true for admins, so it would be dead code.

### Added
- **API token management UI** (user menu → API tokens). Previously tokens could only be created by calling the API with a JWT; there was no page for it at all, which made handing an API token to a customer awkward. Lists your own tokens with status, scope, expiry and last use; creates them with a read-only or unrestricted choice; shows the plaintext exactly once with a copy button and a ready-to-paste `curl` example; revokes with confirmation.
- **API manual on GitHub Pages** (`docs/api.html`, bilingual, linked from the site nav): token auth and scopes, conventions and pagination, error and status-code reference, how the permission model shapes results, the core resources (sections / subnets / addresses / devices) with parameter tables and `curl` examples, an index of all ~500 routes by area, the phpIPAM-compatible API, Graylog DSV lookups, MCP, agent protocols, rate limits, CORS, and how to obtain the OpenAPI spec.

### Fixed
- **`DHCP_SOURCE_TYPES` had gone stale**: FortiGate already wrote `source_type="fortigate"` into `dhcp_pool_ranges`, but the constant still listed only opnsense / pfsense / windows_dhcp. Nothing read the constant, so nothing was broken at runtime, but it was the only written record of which sources that table carries, and it had silently drifted. Added `fortigate`, plus a test that scans the service layer for `source_type=` literals and fails if any is undeclared (or declared but unused), so it cannot drift again.
- The FortiGate delete test **reimplemented** the endpoint's cleanup SQL instead of exercising it, so it would have passed even if the endpoint had forgotten to clean the shared `dhcp_pool_ranges` / `nat_translations` rows. Extracted that cleanup into `cleanup_shared_rows()` and pointed the test at the real function.
- Terminology: replaced the remaining "前綴" with "首碼" (Taiwan usage) across the Chinese changelog and code comments, keeping the entries that describe the terminology change itself.

## [0.5.115] - 2026-07-29

### Added
- **FortiGate integration (Beta)**: a standalone integration alongside OPNsense and pfSense, each keeping its own settings and sync. Reads over the FortiOS REST API (**GET only: nothing on the firewall is ever modified**) and supports **multiple VDOMs** (listed explicitly or auto-discovered; a non-VDOM appliance falls back to `root`):
  - **DHCP leases** and **ARP** mark existing addresses (`in_dhcp_lease`, MAC, hostname) and never create addresses, matching the other firewall integrations
  - **DHCP address ranges** land in the shared multi-source range table as `fortigate`
  - **IPsec site-to-site tunnels** go to the existing VPN tunnel table; **SSL-VPN sessions** stamp the assigned tunnel IP
  - **NAT** (VIP → DNAT / port forward, IP pool → SNAT) joins the existing NAT page with a FortiGate source filter
  - **Policies** and **address objects / groups** are mirrored into their own tables with a read-only per-VDOM viewer
  - **Test connection** runs a per-endpoint diagnosis (which endpoints are readable and how many rows), so field differences between FortiOS versions are easy to spot
- Registered `fortigate` as a hostname and MAC precedence source so it participates in the existing precedence settings.

### Notes
- Authentication uses the `Authorization: Bearer` header. The `?access_token=` URL form is deliberately not used: it is covered by PSIRT FG-IR-24-268 and is disabled by default from FortiOS 7.4.5 / 7.6.1. API tokens are also unavailable in FIPS-CC mode, which the error message now calls out.
- Built without access to a live appliance: endpoint paths and field names follow the official documentation and every field is parsed tolerantly, so a differing FortiOS version degrades to "that item returns nothing" instead of breaking the rest of the sync. Hence **Beta**; use the connection diagnosis against a real appliance to confirm.
- No install or upgrade changes are needed (no new dependency, service or package). The backend must be able to reach the FortiGate management interface; appliances on private networks require `OUTBOUND_ALLOW_PRIVATE`.


## [0.5.114] - 2026-07-29

### Fixed
- zh-TW menu: the Windows DHCP entry now carries the same "整合 " (integration) prefix as every other integration in that group.


## [0.5.113] - 2026-07-29

### Added
- **pfSense now syncs DHCP address ranges, not just leases**, with a separate per-firewall toggle (pfSense keeps its own DHCP settings). Reads the per-interface DHCP config plus any extra address pools over the pfSense REST API. Until now only OPNsense produced ranges, so pfSense-only sites never saw the "in DHCP range" hint on an address.
- **Windows DHCP Server integration (Beta)**: a standalone integration with its own settings page, syncing scopes (address ranges) and leases read-only over WinRM + PowerShell (`Get-DhcpServerv4Scope` / `Get-DhcpServerv4Lease`; only `Get-*` cmdlets run, nothing on the DHCP server is modified). Leases mark existing addresses (`in_dhcp_lease`, MAC and client-registered hostname) and never create addresses, matching the OPNsense/pfSense behaviour. `windows_dhcp` is registered as a hostname and MAC source so it takes part in the existing precedence settings. Runs on the regular sync timer; no new service or system package is needed (`pywinrm` was already a dependency).

### Changed
- DHCP address ranges from all three sources now live in one derived table keyed by source, instead of a table hard-wired to OPNsense. **Each integration keeps its own settings and sync and only ever clears its own rows**; this is shared storage, not a unified "DHCP server" abstraction. New source-neutral endpoint `GET /api/v1/dhcp-ranges` (global-read); the old OPNsense-specific path still works and returns OPNsense rows.

### Notes
- The pfSense endpoints were confirmed against a live device (the list endpoint is the plural `/api/v2/services/dhcp_servers`; the singular form requires an id). Field names follow the official package documentation and are parsed tolerantly, so a differing pfSense version degrades to "no ranges" instead of breaking the rest of the sync.
- Windows DHCP needs the backend to reach WinRM (5986/HTTPS by default). Servers on private networks additionally require `OUTBOUND_ALLOW_PRIVATE`, same as the existing Windows DNS integration.


## [0.5.112] - 2026-07-29

### Security
- **Frontend dependency advisories cleared (13 of 15 Dependabot alerts)**: `axios` 1.16.0 → **1.18.1** (fixes nine advisories: proxy inheritance after interceptor config cloning, several prototype-pollution gadgets, `maxBodyLength` bypasses, `formDataToJSON` recursion DoS, `NO_PROXY` bypass); `postcss` → **8.5.24** (source-map path traversal); `js-yaml` → **5.2.2** (merge-key quadratic CPU); `brace-expansion` pinned to a patched release per major line (1.1.17 / 2.1.3 / 5.0.8). `axios` is the only one of these that ships in the browser bundle.
- Two `brace-expansion` alerts remain and are **accepted**: the advisory lists 5.0.8 as the sole fixed version, so the 1.x / 2.x lines can never satisfy it, and forcing 5.x breaks `minimatch@3` (`expand is not a function`, which takes ESLint down). Both paths are dev-only (`eslint`, `@vue/test-utils`) and the package is not present in the production bundle.


## [0.5.111] - 2026-07-26

### Fixed
- **Proxmox VMs without the guest agent never got a hostname**: when PVE cannot report a VM's IP (no qemu-guest-agent, not an LXC, no cloud-init `ipconfig`), the sync skipped the whole IPAM linking step, so the PVE VM name was never recorded as a hostname observation and `primary_ip_id` stayed empty (which also broke IP→VM resolution for the PVE console). The sync now falls back to matching the VM's NIC MAC against IPs jt-ipam already knows (learned from the scan agent / ARP). It only matches existing addresses (it never creates one), and an ambiguous MAC (the same MAC on several IPs, e.g. overlapping subnets) is skipped rather than guessed.
- **A statically-configured IP inside a DHCP pool was tagged "DHCP"**: the tag was shown both for a real lease and for merely falling inside a pool range. Those are now distinct: a real lease still shows an orange **DHCP** tag, while an address that is only inside the range shows a neutral **In DHCP range** tag, with a tooltip suggesting an exclusion or reservation to avoid future conflicts.


## [0.5.110] - 2026-07-24

### Changed
- **Virtualization → Clusters: a cluster can now be deleted even when it still has synced VMs or a linked Proxmox connection** (revising the 0.5.109 behavior that blocked this), for when you stop using Proxmox. Deleting a cluster cascades away its synced VMs, VM interfaces and Proxmox connections, and also cleans up the connection's encrypted token and scheduled-sync heartbeat. Your IP addresses and devices are not affected (VMs only reference them). The confirmation dialog spells out what will be removed. Covered by unit + browser (Playwright) tests.


## [0.5.109] - 2026-07-24

### Fixed
- **Virtualization → Clusters: manually-added clusters could not be deleted**: there was no delete endpoint or button. Added `DELETE /virt/clusters/{id}` (admin) and a delete action in the UI. To avoid wiping synced data (the VM / Proxmox foreign keys cascade), deletion is blocked with a clear message if the cluster still has VMs or a linked Proxmox connection; only empty clusters can be removed.


## [0.5.108] - 2026-07-22

### Fixed
- zh-TW: use full-width punctuation in the scan-agent install-help strings (commas / semicolon / parentheses), per the project's Chinese punctuation convention.


## [0.5.107] - 2026-07-22

### Fixed
- **Two-factor (TOTP) status now shown on the Security page**. After enabling TOTP the page never reflected it as enabled: `/me` did not expose the state and both buttons were always shown. `/me` now returns `totp_enabled`, and the Security tab shows the current status (Enabled / Not enabled) with only the relevant Enable/Disable button, refreshed via `/me` after enrolling or disabling. Adds a browser e2e test for the full enable → reload → disable cycle.


## [0.5.106] - 2026-07-20

### Fixed
- **Dashboard IPv6 / IPv4-capacity KPI tiles rendered raw i18n keys**: the IPv6 subnet tile (and the renamed IPv4-capacity tile) referenced keys missing from the locale files, so they showed the key path instead of text. Added the missing labels in both locales.


## [0.5.105] - 2026-07-17

### Added
- **Device types: Patch Panel, PDU and UPS** (issue #21). LibreNMS sync now captures the native device type and maps `power` → UPS/PDU (with a vendor-keyword split) and `wireless` → AP; patch panels are passive and stay manual-only. Device-type labels are localized across the UI (list, edit dialog, rack legend, dashboard). Migration 0097.


## [0.5.104] - 2026-07-16

### Added
- **System Export / Import (cross-instance migration)**: a new admin page and CLI (`app.cli.system_transfer`) to move a whole jt-ipam to another instance via a passphrase-protected (scrypt + AES-256-GCM), versioned bundle. UUIDs are preserved so foreign keys and per-record secret AAD stay valid; secrets are decrypted on export and re-encrypted under the target instance's key. Supports merge and replace with a dry-run preview, and is backward compatible with older export files.


## [0.5.103] - 2026-07-11

### Changed
- Internal lint/test cleanup: ruff import ordering, removed dead code / unused imports (eslint), and updated a unit test for the added ssh-rsa client signature. No functional change. Full local suite green: 441 backend tests, vue-tsc, ruff, eslint, migrations up to 0096.


## [0.5.102] - 2026-07-10

### Changed
- **Dashboard capacity: split IPv4 / IPv6**. Summing IPv6 address counts produced an astronomically large, unhelpful "total capacity" number. The KPI now shows **IPv4 usable** (a real, plannable number, comma-formatted) and, when any IPv6 subnet exists, a separate **IPv6** tile showing the subnet count (address space is vast, not summed). The utilization gauge is now IPv4-only (IPv6 never "runs out").


## [0.5.101] - 2026-07-10

### Changed
- Dashboard: renamed the "Total capacity" KPI to **"Total IP capacity"** to make clear it's the total IP address capacity.


## [0.5.100] - 2026-07-09

### Fixed
- **Timestamps showed UTC instead of local time**: the Tasks table (queued / finished), the last-seen columns in Subnet detail and Device detail, and the Anomaly detail rendered timestamps by stripping the ISO `T` without converting timezone, so they showed UTC. They now use the shared local-time formatter (the viewer's browser timezone), consistent with the rest of the app.


## [0.5.99] - 2026-07-09

### Fixed
- **Some help texts rendered blank in the production build**: vue-i18n treats `@` (linked messages), `{`/`}` (interpolation) and `|` (plural) as special syntax, and several messages contained a literal `@` (`root@phpipam-host`, `account@IP`, `@BotFather`), `{...}` (JSON examples) or `|` (a shell pipe). In dev these only logged a warning, but the production build threw a compile error that blanked the surrounding render: most visibly the phpIPAM migration "Steps" guide, plus the SSH/RDP/VNC credential-name placeholder and the Telegram / generic-webhook notification hints. Those literals are now escaped so they render correctly.


## [0.5.98] - 2026-07-09

### Fixed
- **phpIPAM migration / SSH tunnel: support old hosts + clearer auth errors**. The tunnel now also offers the `ssh-rsa` (SHA-1) client signature, so a valid RSA key on a very old phpIPAM sshd is accepted (asyncssh otherwise sends only rsa-sha2). The permission-denied message now lists exactly what to check (authorized_keys, PermitRootLogin, key perms, key/pubkey pairing).
- **`device_ports.name` widened 64 → 255**: long real interface names (e.g. a Windows NDIS filter adapter description, 71 chars) overflowed VARCHAR(64) and aborted LibreNMS/Proxmox port sync with StringDataRightTruncation. Names are also truncated defensively at the sync sites (migration 0096).

### Changed
- Renamed a local variable in the migration view that shadowed the i18n `t`.


## [0.5.97] - 2026-07-07

### Fixed
- **Completed the Tasks-table count audit across all sync types**: Wazuh syncs now count correctly (`new` → added, `fetched` → total; previously only `updated` was picked up), and the detail popover now renders readable summaries for DNS, pfSense, Wazuh and Proxmox syncs instead of a generic line. All task kinds (LibreNMS / OPNsense / pfSense / DNS / Wazuh / Proxmox / AdGuard / phpIPAM) now show real counts.
- Minor: a space before the count in the Tasks "Active (0)" tab.


## [0.5.96] - 2026-07-07

### Fixed
- **Tasks table showed "0" totals for DNS / pfSense / OPNsense syncs**: following the LibreNMS fix, the DNS sync summary (`pulled_zones/pulled_records/hostname_obs`), the pfSense heartbeat (`arp/rules/aliases/nat`) and the OPNsense heartbeat (`mappings`) used keys the count aggregation didn't recognise, so the Tasks table rendered 0 even though the syncs pulled data. These shapes are now mapped, plus a fallback (total = added + updated) so any sync with data shows a meaningful count. The syncs themselves were verified pulling data (DNS 7 zones / 119 records, pfSense 6 ARP / 8 rules, OPNsense 9 alias mappings).


## [0.5.95] - 2026-07-07

### Added
- **`jt-ipam.sh upgrade --force`**: when the working tree has local changes to a tracked file (e.g. a hand-edited or partially-updated `scripts/jt-ipam.sh`), the upgrade previously aborted with "Your local changes would be overwritten by merge". It now detects this and either prompts (interactive) or, with `--force`, discards the local changes to tracked files and continues. Untracked files and config outside the repo are never touched.

### Fixed
- **Scheduled Proxmox sync showed a raw cluster UUID** in the Tasks table target column; it now shows the cluster name (falling back to the node URL).
- **Cryptic UCS DNS error on empty credentials**: a UCS DNS server saved with an empty username/password produced UCS's confusing "basic auth credentials are malformed" 400; jt-ipam now returns an actionable message telling you to re-enter the UCS credentials.


## [0.5.94] - 2026-07-07

### Fixed
- **Tasks table showed "added 0 / updated 0 / total 0" for LibreNMS syncs**: the LibreNMS sync summary is nested (`{devices:{...}, arp:{...}, fdb:{...}, vlans:{...}}`), but the Tasks table's count aggregation (and detail popover) only read flat top-level numbers, so it displayed all zeros even when devices / ARP / FDB were actually synced. It now recurses into the nested groups so the counts reflect the real work, making it clear the integration is connected and working.


## [0.5.93] - 2026-07-06

### Fixed
- **LibreNMS ARP sync hit a dead per-device route**: jt-ipam called `/api/v0/devices/{id}/ip/arp/all` for every device, which no longer exists in current LibreNMS, returning 404 for every device on every 5-minute sync. ARP-based liveness therefore synced nothing, and the burst of 404s tripped web-scan/recon IDS rules (e.g. Wazuh) on the LibreNMS host, flagging jt-ipam's IP as a scanner. Switched to the single global `/api/v0/resources/ip/arp/all` endpoint (one request instead of N) with in-batch de-duplication of (ip, mac, device) rows. ARP liveness now syncs correctly and the false IDS alerts stop.


## [0.5.92] - 2026-07-03

### Fixed
- **Remote consoles no longer drop on idle / when the tab is backgrounded**: SSH/RDP/VNC consoles closed after ~60s of inactivity because liveness relied on a JS-timer heartbeat, which browsers throttle in background tabs. They now stay connected as long as the WebSocket is alive (kept alive by the transport-layer ping/pong, which works even in background tabs); the session ends only on a real disconnect or when you disconnect.
- **Reconnect reuses saved credentials**: after connecting with "remember credentials" then disconnecting, Reconnect in the same tab re-prompted for username/password (only a full page reload picked up the saved credential). The console now records the just-saved credential locally so Reconnect reuses it. Applies to SSH/RDP/VNC/PVE consoles.


## [0.5.91] - 2026-07-03

### Security
- **Constant-time comparison for the public Graylog DSV access token**: the token-gated lookup endpoints (`/api/v1/lookup/...`, also reachable over plaintext :8088) compared the access token with a plain `!=`, a timing side-channel. They now use `hmac.compare_digest` and encode with `surrogatepass` so a crafted (non-UTF-8) token is rejected safely instead of raising a 500. Found and fixed via an internal security review.


## [0.5.90] - 2026-07-03

### Fixed
- **Connections table status dot for overlapping subnets**: when one physical host is split across multiple overlapping-subnet records for the same IP, the connection-enabled record could show offline because the scanner / LibreNMS only stamps one record per IP. The Connections view now borrows the freshest last-seen from the same IP's other records within the user's visible scope, so the dot reflects the host's real liveness (RBAC-safe: only records the user can see).


## [0.5.89] - 2026-07-03

### Added
- **Connections table: MAC and MAC vendor columns**, both off by default, available in the column picker; the vendor is resolved from the IEEE OUI table.

### Fixed
- **Liveness tooltip timestamps now show local time**: the IP status-dot tooltip rendered scanner / LibreNMS / DNS last-seen times in UTC; they now follow the browser's local timezone, like the rest of the app.


## [0.5.88] - 2026-07-03

### Added
- **Tasks table: Trigger column (Scheduled / Manual)**. The periodic sync timer now records a rolling heartbeat row per integration (one per integration, upserted each run, so no flooding), tagged **Scheduled**, so scheduled syncs are visible in the Tasks table and distinguishable from **Manual** runs. Previously the timer wrote directly to the integration tables without any task record, so the Tasks table looked frozen even while syncs ran fine.

### Fixed
- **DNS pull now reports failure instead of "succeeded 0"**: a hard adapter error (e.g. UCS UDM returning HTTP 400) during a DNS pull is now surfaced as a failed task rather than a misleading success with zero counts.


## [0.5.87] - 2026-07-03

### Added
- **SSH console: legacy-device compatibility**. The in-browser SSH terminal (and the host-key preview) now also negotiate older algorithms (aes-cbc, 3des-cbc, diffie-hellman-group14/group1-sha1, ssh-rsa host keys, hmac-sha1) so it can reach old network gear (e.g. D-Link DGS-1510 switches, legacy firewalls) that offers nothing newer. Modern devices still negotiate strong algorithms first; the truly broken ciphers (arcfour / blowfish / cast / single-DES) are deliberately excluded.


## [0.5.86] - 2026-07-02

### Changed
- **BMC setup guide: field-tested serial-console lessons**. The in-app guide + README troubleshooting now cover: use **only the SOL port** in `console=` (multiple `ttyS` can make the kernel pick the wrong one → login shows but no boot messages; check `/proc/consoles`), find the SOL port via `/proc/tty/driver/serial` `rx`, disable systemd boot-message emoji with `systemd.setenv=SYSTEMD_EMOJI=0`, and set BIOS Terminal Type to VT100+ (not VT-UTF8) to avoid BIOS-screen emoji.


## [0.5.85] - 2026-07-02

### Changed
- Notification-settings intro reworded to clarify that this page configures **external** channels (opt-in), while in-app notifications (top-right icon) work without any setup (the old “通知不需設定即可使用” was ambiguous after dropping the 站內 prefix).

### Tests
- pfSense parse regression tests (`_as_text` list-flatten for alias descr; `_valid_ip` rejects alias names like `Web_Test`), covering the two DataErrors fixed in v0.5.48.


## [0.5.84] - 2026-07-02

### Changed
- **Dashboard live-status source line reflects the actual setup**: the “Source: …” caption under the IP live-status card is now built from the sources actually configured (enabled scan agents / LibreNMS / OPNsense / pfSense), instead of a fixed “scan agent + LibreNMS + OPNsense ARP”. Shows a hint when none is set up.


## [0.5.83] - 2026-07-02

### Changed
- Notification settings: the **notification matrix** card now sits above all the per-channel settings (right under the intro), so the “which event → which channel” overview comes first instead of being sandwiched between Email and the other channels.


## [0.5.82] - 2026-07-02

### Added
- **Generic Webhook notification channel**: POSTs `{app, subject, text}` JSON to a custom URL (optional Bearer token via Authorization header); config form + Test button like the other channels. For n8n / custom endpoints / anything not covered by the built-in channels.


## [0.5.81] - 2026-07-02

### Fixed
- **Notification channels: send concurrently**. Enabled webhook channels now fire in parallel (asyncio.gather) instead of sequentially, so worst-case latency is one channel's timeout, not the sum (avoids stalling IP-request/sync flows when several channels are slow).
- **Teams webhook: support the new Workflows webhooks**. It falls back to an Adaptive Card payload when the legacy `{"text"}` (O365 connector) form is rejected, so both legacy connectors and current Workflows incoming webhooks work.


## [0.5.80] - 2026-07-02

### Added
- **LibreNMS integration: Verify TLS toggle** (migration 0094), like Wazuh. Turn it off to connect when LibreNMS uses a self-signed cert or the hostname doesn't match (e.g. connecting by IP); the API client then uses `verify=False`. Fixes `transport: ConnectError` on self-signed LibreNMS without hacking the venv's certifi bundle (which upgrades would wipe). Default on.


## [0.5.79] - 2026-07-02

### Changed
- Notification wording: 站內通知 → 通知 (drop the 站內 prefix per Taiwan usage).


## [0.5.78] - 2026-07-02

### Changed
- **Notification wording: 鈴鐺 → 站內通知** (Taiwan usage) in the notification-settings copy and matrix column; the intro now lists all supported channels (Email + Telegram/Slack/Teams/Nextcloud/Zulip) instead of “in development”.
- docs: TEST_CHECKLIST spot-checks for the recent features; graylog DSV docstring uses RFC 5737 example IPs.


## [0.5.77] - 2026-07-01

### Added
- **Notification channels: Telegram, Slack, Microsoft Teams, Nextcloud Talk, Zulip**, all implemented (previously grayed “coming soon”). Each has a config form (encrypted tokens/webhooks) + a Test button on the notification-settings page; enabled channels receive every alert the matrix fires (IP requests, anomalies, certificate expiry/drift/deploy, stale-IP reminders) alongside Email/in-app. Admin-configured outbound (same trust model as SMTP).


## [0.5.76] - 2026-07-01

### Changed
- **In-app notifications now follow the UI language**: notifications store an i18n key + params (migration 0093); the bell and the Notifications page render them in the current language (falls back to the stored text for older notifications). Covers IP-request approve/reject/pending, anomaly alerts, certificate expiry/drift/deploy, and stale-IP reminders. Emails keep the default-language text.


## [0.5.75] - 2026-07-01

### Changed
- **Connections list OS column matches the IP detail page**, using the shared `OsCell`: OS icon + localized family name + （source） annotation, with the raw detected string on hover; the OS shown is the source-precedence-resolved value (same as IP detail), not just the raw scanner guess.


## [0.5.74] - 2026-07-01

### Changed
- **Disconnected overlay now covers only the display area**: it no longer dims the toolbar, so the Reconnect button stays fully visible and clickable.
- **Export button now has a border**, matching the neighbouring Columns / Refresh buttons (was borderless `quaternary`); applies to every table page via the shared `ExportButton`.


## [0.5.73] - 2026-07-01

### Fixed
- **BMC blank-screen hint text no longer hides behind the info icon**: the previous row-tightening also shrank the alert's left padding (which reserves space for the icon); now only the vertical padding is reduced.


## [0.5.72] - 2026-07-01

### Changed
- **Scan agent: much more accurate OS detection (agent 1.7.0)**. The OS probe now adds `nmap -sV` service/banner detection + `smb-os-discovery` and derives the OS from **banners** (SSH `OpenSSH … Debian/Ubuntu`, `Service Info: OS:`, SMB) instead of trusting raw TCP/IP-stack fingerprinting, which confidently mis-guessed appliances/BMCs. The aggressive `-O` guess is now the last resort and is dropped when it's a device model (NAS/router/OpenWrt/…) rather than a general-purpose OS, since it is better to show unknown than a wrong model. Verified: Proxmox Datacenter Manager `HP P2000 NAS`→`Debian`, Windows `XP SP3`→`Windows`, BMC `OpenWrt Kamikaze`→unknown.


## [0.5.71] - 2026-07-01

### Added
- **Remote console: clear "Disconnected" overlay**. When an SSH / RDP / VNC / noVNC / xterm / BMC session drops, a large centered overlay with a broken-link icon and "Disconnected" appears over the display so it's obvious at a glance; it fades out automatically on reconnect. Shared `ConsoleDisconnectedOverlay` across all console types.


## [0.5.70] - 2026-07-01

### Changed
- **Connection buttons are now single buttons**: dropped the split-button dropdown chevron (the "open in popout window" menu) on SSH/RDP/VNC/noVNC/BMC in both the Connections list and the IP detail card; the button just opens the console (new tab). Tighter connection-list row height. BMC blank-screen hint trimmed to one line (details behind the Setup-guide link).


## [0.5.69] - 2026-07-01

### Changed
- **Connection buttons: clearer RDP/VNC/noVNC icons**. The three shared a monitor glyph with a tiny 10px letter that was hard to tell apart; the letter is now large (13.5px) and bold, filling the screen, so R / V / N read at a glance. The split-button dropdown chevron is narrower (Connections list + IP detail).


## [0.5.68] - 2026-07-01

### Added
- **BMC console: "Fit to window" button**. Serial consoles carry no window-size negotiation, so full-screen apps default to 80×24 with black margins. The button sends an `stty rows/cols` command (using xterm.js's real dimensions) into the session to match the browser window. Hovering shows an immediate tooltip that it **sends a command** and must be pressed at a shell prompt. No per-host script needed.
- **BMC setup guide: Troubleshooting section**. Topics: SPCR can point to the wrong ttyS (echo-test each port), baud must match SOL's bit rate, `TERM=xterm-256color` for clean curses rendering (glances), and the fit-to-window note. README (EN/zh) mirrors it.


## [0.5.67] - 2026-07-01

### Fixed
- **BMC "remember credentials" never saved**: the credential-vault create/list endpoint rejected `protocol='bmc'` (400, swallowed by the UI), so BMC passwords were never stored and every session re-prompted. `bmc` is now accepted in create/list/permission dispatch (password-only, `can_use_bmc`).

### Added
- **BMC console: built-in serial-console setup guide**. A **Setup guide** button (form + toolbar + blank-screen hint) opens a step-by-step modal: find the ttyS SOL maps to (ACPI SPCR / dmesg), add `console=tty0 console=ttySx,115200n8` (GRUB or PVE `/etc/kernel/cmdline`), enable `serial-getty`, optional BIOS Console Redirection, reboot. README (EN/zh) + docs landing page document the same.


## [0.5.66] - 2026-07-01

### Changed
- **BMC console blank-screen hint now explains the two-layer serial-console requirement**: BIOS Console Redirection (POST/BIOS/boot menu) **and** an OS serial console (kernel `console=ttySx,115200n8` + `serial-getty`; ttyS from ACPI SPCR; PVE uses `/etc/kernel/cmdline` + `proxmox-boot-tool refresh`). Without the OS layer, SOL goes blank once the kernel loads.
- test: `test_map_provider` accepts the `builtin` default map provider.


## [0.5.65] - 2026-07-01

### Fixed
- BMC console terminal: prominent drop-shadow to match the RDP/VNC console screen.
- **DNS (Univention UCS): username is now required on save.** An empty username produced a UCS `400 "basic auth malformed"` and the sync silently pulled 0 records.


## [0.5.64] - 2026-07-01

### Fixed
- **BMC console connect button now appears on the IP detail card** (next to SSH/RDP/VNC); the editor modal wasn't rendering it / emitting the event.
- **BMC console screen restyled to match SSH/RDP/VNC** (card height, left-label form, `switch` for "remember", aligned title icon, status-pill toolbar, full-height terminal) + a "blank screen is normal; press Enter" hint for an idle SOL console.


## [0.5.63] - 2026-07-01

### Fixed
- **Connections page 500**: `list_connection_targets` had a leftover 4-tuple unpack after BMC added a 5th
  element; the page errored with no rows. Fixed.
- BMC console: added the connect button to the IP detail page (it was only on the Connections page).

### Changed
- Terminology: dropped "帶外" (not Taiwan usage) from the BMC console UI; comments use OOB.


## [0.5.62] - 2026-07-01

### Changed
- BMC console: generic username placeholder (`ADMIN / root`).


## [0.5.61] - 2026-07-01

### Added
- **BMC out-of-band console (Beta)**: a browser IPMI **SOL** console (keyboard + text screen) for BMC
  management IPs, integrated into the Connections page and the IP editor (per-IP toggle). Standard, vendor-agnostic
  transport (`ipmitool` SOL over RMCP+) with **cipher auto-fallback (17→3)**, connection self-check (SOL enabled /
  privilege), single-session handling, credential vault (`protocol=bmc`), **same RBAC as SSH**, and audit on
  open/close. Non-destructive: keyboard + screen only, no mouse, no power/sensor/boot control. Migration 0092
  (`bmc_enabled`). Install/upgrade auto-install `ipmitool` + `freeipmi-tools`; the nginx WebSocket location now
  covers `bmc`. (Graphic screenshot adapters are a future, isolated phase.)


## [0.5.60] - 2026-06-30

### Fixed
- **Subnets list: the CIDR column was squished.** `scroll-x` was set far below the columns' real total, so the
  table compressed the flexible CIDR/description columns below their `minWidth`. Fixed `scroll-x` to the real
  total and widened the CIDR minimum, so the CIDR (the key column) stays fully readable; the table scrolls
  horizontally when the window is narrow.


## [0.5.59] - 2026-06-30

### Changed
- Terminology: replaced the remaining "前綴" with "首碼" (Taiwan usage), notably the OUI search placeholder.


## [0.5.58] - 2026-06-30

### Fixed
- **IP request list now actually shows the subnet CIDR.** 0.5.56 made the frontend use `subnet_cidr`, but the
  list endpoint never populated it (only the detail endpoint did), so the column still fell back to the UUID.
  The list response now fills `subnet_cidr`.


## [0.5.57] - 2026-06-30

### Added
- **IP heatmap legend now has hover tooltips** explaining each state (online / recently-seen / offline /
  reserved / unknown / idle), including the actual liveness thresholds. "Recently seen" = last detected between
  the online threshold (default 30 min) and 4× that (default 2 h), which likely means a missed scan or flapping.


## [0.5.56] - 2026-06-30

### Fixed
- **IP request list: the "subnet" column now shows the subnet CIDR** instead of the raw subnet UUID (the read
  already returned `subnet_cidr`; the list just wasn't using it).

### Changed
- New-IP-request dialog: added an icon to the title and to both buttons (cancel / submit).


## [0.5.55] - 2026-06-30

### Fixed
- **IP request approval now writes the request's hostname and purpose onto the allocated IP.** The hostname is
  recorded as a **manual** hostname observation (top precedence, so a later scan/sync won't overwrite it) and the
  purpose is saved to the IP's **note**. (The description was already copied.) Applies to both direct and
  multi-stage approval (both fulfil through the same path).


## [0.5.54] - 2026-06-30

### Changed
- Change-password dialog: added an icon to the title and to both footer buttons (cancel / change), matching the
  other dialogs.


## [0.5.53] - 2026-06-30

### Changed
- **IP list: gateway / DHCP-server markers are now compact icons (with tooltips)** instead of wide text tags,
  so they no longer squeeze the IP into a one-character-per-line vertical strip. In-DHCP-range shows as a small dot.
- **IP list: widened the OS column** (110→150 px) so the OS family label is no longer truncated.


## [0.5.52] - 2026-06-30

### Changed
- **Scan-agent installer now installs base tools (`curl git sudo`) and, by default, `avahi-utils` for mDNS**, so
  mDNS name resolution works out of the box (previously opt-in via `JT_IPAM_ENABLE_MDNS`). `avahi-utils` brings
  up `avahi-daemon` (UDP 5353); set `JT_IPAM_NO_MDNS=1` to skip it, `JT_IPAM_SKIP_PROBE_TOOLS=1` to skip all probe tools.
- **Docs: install instructions now install `curl` first** (a minimal system may not ship it, and the one-liner needs it).


## [0.5.51] - 2026-06-30

### Changed
- **LibreNMS "auto-add devices" now defaults ON** (and existing instances are flipped on by migration), so every
  sync / pull also match-or-creates the jt-ipam devices: no more clicking "Link devices" by hand each time.

### Added
- **DNS integration: a "Sync now" button** on the DNS servers list. DNS was only synced silently by the periodic
  timer (never showing in Tasks); the manual pull now enqueues a `dns.sync` task that appears in Tasks like the
  other integrations.


## [0.5.50] - 2026-06-30

### Changed
- **Subnet scan: enabling scan now requires an explicit choice**, either "Local scan (jt-ipam host)" or a specific
  scan agent; saving with nothing selected is blocked with a warning. The old ambiguous "blank = scan from the
  host" became an explicit **Local scan** option, so a scan no longer silently does nothing in setups (e.g.
  Docker) where the host can't reach the LAN. Existing locally-scanned subnets show as "Local scan".


## [0.5.49] - 2026-06-30

### Added
- **Self-service password change for local accounts**: a "Change password" item in the top-right account menu
  opens a dialog that verifies the current password and sets a new one (≥ 12 chars). Hidden for externally
  authenticated accounts (LDAP / SSO). New endpoint `POST /api/v1/auth/change-password` (audited).


## [0.5.48] - 2026-06-30

### Fixed
- **pfSense sync no longer crashes** on (a) aliases whose `detail` is returned as a **list** (now coerced to
  text) and (b) NAT port-forward **targets that are alias names** rather than IPs (now skipped instead of being
  cast to INET). Both previously raised an asyncpg `DataError` and aborted the whole fetch.

### Changed
- **Scan-agent OS detection now uses `nmap --osscan-guess`**: hosts with no exact fingerprint match still get a
  best-guess OS (the top guess, shown with a confidence %), instead of nothing. Agent v1.6.0 (auto-updates).


## [0.5.47] - 2026-06-30

### Fixed
- **IP relationship chain: a device placed in a rack now inherits the rack's location (machine room)** even when
  the device row has no location of its own. Previously the chain stopped at the rack for such devices (e.g. a
  PVE node whose rack has a location but the device's own `location_id` was empty), so two hosts in the same
  rack could show inconsistently: one with the machine room, one without.


## [0.5.46] - 2026-06-29

### Added
- **IP list: special-role markers on each IP**, for **Gateway** (the subnet's gateway), **DHCP server**
  (auto-detected when the IP matches an integrated OPNsense/pfSense firewall, plus a manual per-IP toggle in
  the IP editor), and **in DHCP range / lease**. Shown as small colour-coded tags with tooltips next to the IP.


## [0.5.45] - 2026-06-29

### Changed
- **Sections: the "strict mode" toggle (and column) are hidden from the UI.** It was a phpIPAM-compatibility
  field that jt-ipam never enforced, so the switch did nothing. The field is still stored and round-tripped via
  the phpIPAM-compatible API / migration (existing values are preserved), just no longer shown as a control.


## [0.5.44] - 2026-06-29

### Fixed
- **AI chat widget no longer shows until LLM/AI is enabled** (管理 → LLM/AI). On a fresh install you could
  type and click Send before configuring an LLM; `/me` now exposes `ai_enabled` and the widget is gated on it.
- **LLM/AI settings: the model list is no longer fetched while "啟用 Ollama 伺服器連接" is off**, so it no
  longer shows a spurious "無法連 Ollama：Internal Server Error". Toggling off clears the list and the error.
- **LLM/AI: a half-width space before "(未在 Ollama 找到)"** on model names.


## [0.5.43] - 2026-06-29

### Added
- **Docker Compose air-gapped (offline) workflow**: `offline-export.sh` builds + saves all four images
  (app + postgres/redis) into one archive on an internet-connected host; `offline-import.sh` loads them and
  starts the stack on a host with no internet (`--no-build --pull never`). Same flow for install and upgrade.
  Documented in `deploy/docker/README*`.

### Changed
- Terminology: anomaly detection "MAC 漂移" → "MAC 變動" (proper Taiwan usage).


## [0.5.42] - 2026-06-29

### Fixed
- **IP list "switch port" column widened** so it shows the full `switch@port` (e.g. `switch-003@eth1/0/24`)
  instead of truncating to `switch-003@eth1/…`.


## [0.5.41] - 2026-06-29

### Fixed
- **Locations map (built-in) now zooms in to fit all markers** instead of always showing a wide ~24°×16°
  view, so nearby sites no longer collapse into what looks like a single point. A small minimum view is kept
  only to avoid over-zooming a single/very-close point (the built-in low-res basemap would blur).


## [0.5.40] - 2026-06-29

### Changed
- **pfSense integration table now shows the same columns as OPNsense** (name / API URL / TLS / last sync /
  last error / actions); removed the extra 啟用 / 同步項目 / 別名數 / 規則 columns.

### Added
- **The left sidebar auto-expands the group that contains the current page** (管理 / 進階 / a subnet group),
  whether you navigate there or land on it directly, so your location is visible.


## [0.5.39] - 2026-06-29

### Fixed
- **OPNsense firewall column picker no longer lists phantom columns.** It used to offer 狀態/DHCP/ARP/OpenVPN/
  Rules/NAT entries that the table doesn't actually render (all shown checked but never appearing). The picker
  now matches the real columns: name, API URL, TLS, last sync, last error, actions.


## [0.5.38] - 2026-06-29

### Changed
- **pfSense integration page now matches the OPNsense page**: adds a TLS column, a "TLS verification disabled"
  warning banner when any instance has Verify TLS off, the same in-form TLS warning, and the same action-button
  order (edit / test / sync / delete).
- **PVE LXC (xterm) console hint moved into the toolbar** (single line next to the status tags, ellipsis if too
  long, dismissible) instead of a full-width banner, with shorter wording.


## [0.5.37] - 2026-06-29

### Added
- **Change-log entries older than a configurable number of days are shown dimmed**, so recent changes stand
  out. Threshold set in 管理 → 系統設定 → 顯示 (default 30 days; 0 = never dim). Applies to the IP-detail
  change-log timeline and the IP 異動記錄 page.


## [0.5.36] - 2026-06-29

### Added
- **PVE LXC (xterm) console: a dismissible hint banner** reminds you to click the screen and press Enter once
  if only a cursor shows and no prompt appears (a known PVE LXC console quirk).


## [0.5.35] - 2026-06-28

### Fixed
- **RDP: modifier shortcuts (Ctrl+V / Ctrl+C / Ctrl+A …) now work, which makes the clipboard paste actually
  paste.** Letter/number keys were sent as Unicode characters, and RDP does not combine a Unicode key event
  with the scancode Ctrl/Alt modifier, so Ctrl+V did nothing (it just typed "v"). When a modifier is held the
  key is now sent as a scancode. Verified end-to-end against a real Windows host (server issues
  `CB_FORMAT_DATA_REQUEST` on Ctrl+V and we answer with the clipboard text).
- The RDP Paste button now reports the number of characters actually sent to the remote clipboard.


## [0.5.34] - 2026-06-28

### Fixed
- **RDP clipboard paste: fixed RDP dropping ~10–20s after connecting when the feature was enabled.** When the
  remote requested our clipboard before any text had been set, aardwolf's cliprdr channel crashed
  (`'NoneType' object has no attribute 'datatype'`) and tore down the session. We now seed an empty clipboard
  on connect so `clipboard.data` is never null.

### Changed
- **All consoles (SSH / RDP / VNC / noVNC / xterm): the display area is greyed out** (grayscale + dimmed,
  non-interactive) once the session disconnects, so it is obvious the connection is closed.


## [0.5.33] - 2026-06-28

### Fixed
- **Users admin table: the Actions column is now pinned to the right** so it stays visible when the table
  scrolls horizontally on narrow screens (previously it scrolled off-screen).


## [0.5.32] - 2026-06-28

### Added
- **RDP console: optional one-way clipboard paste (controller → controlled host).** A new "貼上" button in the
  RDP toolbar pushes your local clipboard text into the remote's clipboard (text only; then press Ctrl+V on the
  remote). The remote clipboard is **never** sent back to the browser/server. Gated by a new admin security
  toggle **管理 → 系統設定 → 資安 → 允許 RDP 控制端貼上文字到被控端**, **off by default (deny by default)**.
  Backend only attaches the RDP clipboard (cliprdr) channel when the toggle is on; pastes are length-capped.
  Verified end-to-end against a real Windows RDP host.


## [0.5.31] - 2026-06-28

### Fixed
- **Connections page: the PVE console buttons now match the IP detail page**. The label is just noVNC / xterm
  with a small "PVE" badge in the top-right corner (instead of an inline "·PVE").


## [0.5.30] - 2026-06-28

### Fixed
- **PVE console (noVNC/xterm) disconnect now behaves like RDP**: clicking 中斷連線 (or a dropped connection)
  leaves the last frame frozen in a "已關閉" state with a 重新連線 button, instead of jumping back to the
  connection form.


## [0.5.29] - 2026-06-27

### Fixed
- **noVNC / xterm console screen now has the same framed look as the RDP console**: border, rounded corners
  and drop shadow (previously it was flush with no frame).


## [0.5.28] - 2026-06-27

### Fixed
- **PVE console connect form now matches the SSH form.** It auto-selects the most recent saved PVE credential
  (compact form, ready to connect), the hint switches to the saved-credential wording when one is selected,
  and the card title / connect button icon reflects the protocol (xterm → terminal, noVNC → screen).


## [0.5.27] - 2026-06-27

### Fixed
- **PVE xterm (CT) console now has padding around the terminal** (like the SSH console) instead of sitting
  flush against the edges.


## [0.5.26] - 2026-06-27

### Fixed
- **Version page now lists the noVNC dependencies** that were missing: backend `websockets` (the PVE
  console relay) and frontend `@novnc/novnc`.
- **Connections page: the PVE console button now matches the IP detail page**. It shows xterm (CT) / noVNC
  (VM), is highlighted (orange / PVE), and its tooltip reads "xterm 連線" / "noVNC 連線" instead of a generic
  "連線".
- **Global search: a matching Proxmox VMID now surfaces the VM/CT itself**, by name, under a "Virtualization"
  group. Previously the result used a type the dropdown didn't recognise, so it was dropped entirely (only
  unrelated IP matches showed).


## [0.5.25] - 2026-06-27

### Fixed
- **noVNC button now uses a distinct icon** (a screen with "N") instead of reusing the RDP icon, so noVNC and
  RDP are no longer visually identical.
- **PVE console connect form is now centred on the page in the error state too** (previously only the initial
  form was centred; an error left the card stuck top-left).
- **Console connection buttons (SSH / RDP / VNC / noVNC) now use the in-app tooltip** instead of the
  browser-native `title` popup, on both the Connections page and the IP detail header.
- **Audit log** now resolves PVE-credential targets to their label instead of showing a raw UUID.
- **Fixed a 500 when connecting with a *saved* PVE credential**: the stored password was decoded twice
  (`str` has no `.decode()`); now decrypts once like the RDP/VNC paths.


## [0.5.24] - 2026-06-27

### Fixed
- **Device detail page: Edit now opens the dialog in-place** (it used to jump to the device list). The device
  edit dialog is now a shared `DeviceEditModal` component.
- **Virtualization VM table filter:** a numeric query (e.g. `102`) no longer matches internal fields such as
  `memory_mb` (1024); the quick filter now only matches the **displayed columns** (name / VMID / node / IP /
  MAC / status), and matches inside IP/MAC lists.


## [0.5.23] - 2026-06-27

### Fixed / Changed
- **PVE console (noVNC/xterm) UI now matches SSH/RDP/VNC.** Same card connect form (帳號 → 密碼 → realm order,
  short "記住此帳密"), and the connected toolbar gains **send-keys + scale (fit / native) + "中斷連線"** for
  graphical VM consoles. The connect button uses the right icon/tooltip (noVNC vs xterm), and the
  connection-type filter no longer truncates "noVNC/xterm".
- The PVE console toggle now appears on **all of a VM's IPs**: a multi-IP VM resolves via its interface MAC,
  not only its primary IP.
- **Global search:** a numeric query (e.g. `227`) is now also treated as a possible Proxmox **VMID** and finds
  the matching VM/CT; the right-side hint shows "VLAN / VMID" instead of only "vlan_number".
- **Rack:** the device dialog's "U 位 (起始)" field is wider (the number shows), and the U-position picker now
  reflects **half-U** occupancy (left/right), so you can place into the free half.


## [0.5.22] - 2026-06-27

### Added
- **In-browser PVE console (noVNC / xterm) for Proxmox VE VMs/CTs.** For an IP that maps to a Proxmox VM/CT,
  a per-IP toggle adds an in-browser console button (with a **PVE** badge): QEMU VMs open a graphical **noVNC**
  console, LXC containers open an **xterm** terminal. The connection uses the **PVE credentials you enter at
  connect time** (optionally saved to the encrypted vault, like SSH/RDP/VNC) and is gated by PVE's own
  permissions: without `VM.Console` you can't connect. The browser talks only to jt-ipam's **same-origin**
  WebSocket, which byte-relays to PVE's `vncwebsocket` (vncproxy for VMs, termproxy for CTs); credentials are
  never stored on the server beyond the optional vault, the WebSocket relay is single-use-ticketed, and every
  session is audited (`novnc.session_open` / `novnc.session_close`).
- The Proxmox sync now back-links each VM/CT's primary IP (`VirtualMachine.primary_ip_id`) so an IP can resolve
  to its PVE console target (also backfills existing VMs).


## [0.5.21] - 2026-06-27

### Fixed
- Traditional-Chinese wording: use 內建 / 本機 phrasing instead of the mainland terms 自帶 / 同源 in the map-provider UI text and comments.


## [0.5.20] - 2026-06-27

### Added / Changed
- **Map provider now defaults to "Built-in (offline)"**: the self-contained world map (no external calls).
  Admins can still switch the Locations preview to **OpenStreetMap** or **Google Maps** under
  Settings → System.
- **OpenStreetMap tiles load through a same-origin backend proxy** (`/api/v1/system/map-tile/{z}/{x}/{y}`):
  the browser never contacts OSM directly, so the CSP stays `img-src 'self'` + COEP `require-corp` (ZAP clean)
  even when an admin selects OSM. The proxy is bounded read-only (server-built OSM-only URL, validated tile
  coordinates, small in-memory LRU cache, nginx-rate-limited).
- Google Maps: the in-page preview uses the built-in map (Google tiles cannot be proxied per their Terms);
  the "open externally" link opens Google Maps.


## [0.5.19] - 2026-06-27

### Security
- Hardening + documentation around the one remaining accepted finding (CSP `style-src 'unsafe-inline'`,
  inherent to Vue + Naive UI: `v-show` / `:style` / floating-element positioning emit inline style
  *attributes*, which CSP cannot nonce/hash). Enabled Naive UI's **`inline-theme-disabled`** to move theme
  styling out of inline attributes into `<style>` blocks (smaller inline surface + SSR/perf), and documented it
  as an **accepted risk with compensating controls** in `SECURITY.md` (EN/zh): strict `script-src 'self'` (no JS
  exec) + `img-src`/`connect-src 'self'` (no exfiltration) + Vue auto-escaping. No real exploitability remains.


## [0.5.18] - 2026-06-27

### Security / Changed
- **The Locations map is now fully self-contained: no embedded OpenStreetMap.** The OSM tile renderer is
  replaced by a bundled Natural Earth world outline (public domain) projected locally. The map now works on
  isolated/offline networks, sends **no requests to OSM** (it no longer leaks which sites an admin is viewing),
  and lets the headers tighten: the OSM exception is dropped from CSP `img-src`, and
  `Cross-Origin-Embedder-Policy` is upgraded to **`require-corp`** (the strongest value, now that there are
  zero cross-origin subresources). nginx proxy snippets `proxy_hide_header` COEP too (single source).
- **Column-picker labels across all admin tables re-translate on a live language switch**: 19 pickers wrapped
  in `computed` (they were frozen at the language active when the page first loaded).
- pfSense NAT sync was **verified against a live port-forward** and refined (external `destination_port` for the
  NAT port; `target` linked to the internal IP).

### Added
- `deploy/zap-baseline.conf`: a documented ZAP baseline-triage of accepted, justified low/informational
  exceptions (Naive-UI `style-src 'unsafe-inline'`, IPAM example IPs, asset caching, SPA detection). The release
  gate is now: a ZAP scan with **no findings beyond this baseline** (0 FAIL / 0 WARN).


## [0.5.17] - 2026-06-27

### Changed
- **More pfSense/OPNsense parity.** The "pfSense firewall" admin page no longer has a view-rules button;
  rule/alias viewing lives in **Advanced → Firewall (pfSense)** (read-only), matching OPNsense. Menu entries
  renamed: **Firewall (OPN) → Firewall (OPNsense)**, **Firewall (pf) → Firewall (pfSense)**, with the in-page
  titles made consistent; the pfSense rules tab is now labelled **"Firewall rules"**.
- The NAT-rules **Source** filter now offers **pfSense**, and pfSense NAT port-forwards are synced into the NAT
  table (`source_origin = pfsense:<id>`) so they list alongside OPNsense NAT.

### Fixed
- Column-picker labels now re-translate immediately on a live language switch (no page refresh needed) on the
  pfSense pages and the NAT source filter; they were frozen at the language active when the page first loaded.


## [0.5.16] - 2026-06-27

### Changed
- **pfSense UI aligned with the OPNsense pages.** The "pfSense firewall" admin table now has a column picker
  + export and a fitting default column set (the actions column is no longer cut off on narrow widths); the
  add/edit dialog spacing is fixed (sync toggles / Expose-DSV grouped into form rows); and the page title is
  now **"pfSense firewall"** (was "Integrate pfSense").
- The Advanced → "Firewall rules / aliases" entry (OPNsense) was renamed to **"Firewall (OPN)"**.

### Added
- **Advanced → "Firewall (pf)"**: a read-only pfSense rules & aliases viewer (instance selector + tabs +
  quick filter + column picker + export), mirroring the OPNsense "Firewall (OPN)" page.
- `pfsense` is registered in the **hostname/ARP source precedence**, defaulting just below `opnsense`.


## [0.5.15] - 2026-06-27

### Security / Docs
- **The security headers are now documented as a required deployment setting and surfaced in install/upgrade
  output.** When jt-ipam is fronted by your *own* edge reverse proxy / load balancer (Mode C), that proxy
  **must** set the security headers itself: they don't survive an extra proxy hop, so otherwise the public
  site ships with no CSP/HSTS. The external-proxy snippet (`jt-ipam-external-proxy-snippet.conf`) now also
  `proxy_hide_header`s the upstream's security headers (dedup, matching the internal snippet in v0.5.14);
  INSTALL (EN/zh), README (EN/zh) and the landing page now call this out as **required** with a
  verify-through-the-public-URL step; and `jt-ipam.sh install`/`upgrade` print a required-headers notice.


## [0.5.14] - 2026-06-27

### Security
- **Fixed duplicate security headers + a stale CSP on `/api/*` responses** (found by an authenticated ZAP
  scan). The backend middleware still emitted the pre-v0.5.8 permissive CSP (`frame-src` allowing
  google/openstreetmap), and behind nginx every proxied `/api` response carried **two** copies of each
  security header (HSTS / CSP / X-Frame-Options / Referrer-Policy / Permissions-Policy / COOP / CORP); ZAP
  flagged "Strict-Transport-Security multiple header entries". Backend CSP tightened to `frame-src 'self'`
  (so the `direct`/`self-signed` TLS mode is also correct), and the nginx proxy snippet now
  `proxy_hide_header`s the upstream's security headers so the server block's hardened values are the single
  canonical source. Verified live: one of each header, tightened CSP.


## [0.5.13] - 2026-06-27

### Fixed
- **Full test suite & lint green.** Ran the complete pytest suite (412 tests) + migrations 0001→0088 on a
  fresh DB and fixed 4 test assertions that had drifted behind earlier feature work: the new
  `list_connection_targets` MCP tool (missing from the tool-args guard), the Proxmox guest-agent `timeout`
  arg (test mock signature), and the external-MCP toggle now returning **403** when disabled (was asserted
  as 401). Also removed two dead-code lint errors and sorted imports. No product behaviour change.


## [0.5.12] - 2026-06-27

### Added
- **pfSense integration Phase 2**: firewall **rules sync** + a read-only **Rules / NAT viewer** (eye action
  on the pfSense page), and **Graylog DSV** endpoints for pfSense: `…/lookup/pfsense/{id}/aliases`
  (alias → members) and `…/lookup/pfsense/{id}/rules` (filterlog `tracker` → rule description), token-gated
  and per-instance `expose_dsv`. New per-instance toggles: sync rules, expose DSV. Verified against pfSense
  CE 2.8.1. (migration 0088)
- TEST_CHECKLIST: added a pfSense integration section + spot-checks for recent features.


## [0.5.11] - 2026-06-27

### Added
- **pfSense integration (Phase 1)**: a separate integration with its own settings page (Admin →
  pfSense), independent of OPNsense. pfSense CE has no built-in REST API, so this connects via the
  third-party **pfSense-pkg-RESTAPI** package (pfrest.org): base path `/api/v2`, `X-API-Key` auth. It pulls
  the **ARP table** and **DHCP leases** to stamp IP liveness / MAC / hostname within scoped subnets
  (overlap-safe), and reads **firewall aliases**. Per-instance sync toggles (DHCP off by default to avoid
  clashing with another DHCP server), subnet scoping, verify-TLS, test-connection and sync-now; runs in the
  periodic sync loop. `pfsense` is registered as a hostname/ARP source. Verified end-to-end against pfSense
  CE 2.8.1. (migration 0087; firewall rules / NAT / Graylog-DSV are planned for Phase 2.)


## [0.5.10] - 2026-06-27

### Fixed
- **"Add address" inside a subnet's IP list had no IP input field**, so submitting failed with HTTP 422
  (missing IP) (issue #14). The create form now shows a required **IP** field (prefilled from context when
  one is provided), and submitting with an empty IP is blocked client-side with a clear message.


## [0.5.9] - 2026-06-27

### Added
- **Notification matrix** (Admin → Notification settings): a per-event × per-channel grid (in-app bell /
  email) to choose which events send notifications. Events: IP request submitted / approved / rejected,
  certificate expiring or expired, **agent deployed a new certificate** (new), certificate drift, anomaly
  detected. Every notification site now respects the matrix; certificate and anomaly events can now also be
  emailed (previously in-app only).
- **New event `cert.deployed`**: when a distribution agent successfully swaps a cert for a new version, admins
  are notified (the agent report endpoint diffs the previous vs new fingerprint per cert/service).
- **Certificate distribution: a `files` service profile** that only writes the cert files (fullchain + key to
  `/etc/ssl/jt-ipam`) and does **not** test, reload or restart any service, for operators who reload
  themselves.


## [0.5.8] - 2026-06-26

### Security
- **Removed the embedded third-party map iframe** on the Locations page (Google Maps / OpenStreetMap); the
  map now opens in a new tab. The embed pulled a third-party page (and its scripts) into ours: a privacy
  leak and the source of the ZAP findings **Cross-Domain JavaScript Source File Inclusion** and **Sub
  Resource Integrity Attribute Missing** (they came from Google's/OSM's embed page, not jt-ipam). Google/OSM
  are now contacted only when the user clicks.
- **Tightened CSP `frame-src` to `'self'`** (dropped the google/openstreetmap allowances now that nothing is framed).
- **nginx reference config hardened**: hide the upstream (uvicorn) `Server` / `X-Powered-By` headers (no
  framework fingerprint), and add `Cross-Origin-Resource-Policy: same-origin`.

### Docs
- INSTALL (EN/zh) and the landing page now document the **hardened nginx reverse proxy as the production
  standard** (TLS 1.2/1.3, HSTS preload, strict CSP, full security-header set, hidden upstream banner,
  backend bound to loopback).


## [0.5.7] - 2026-06-26

### Added
- **MCP client-config generator.** On Admin → LLM/AI, the "expose MCP" card has a "Generate client config"
  button that produces ready-to-paste MCP server snippets for Claude Desktop (via `mcp-remote`), opencode,
  mcpo, and generic clients (Cursor / Cline / VS Code), with the endpoint URL and API key filled in, each
  with its own copy button.


## [0.5.6] - 2026-06-26

### Changed
- **Anomaly detection page reorganized into tabs.** The four detectors (IP conflicts / MAC drift / ghost
  IPs / unauthorized IPs) are now tabs instead of one long stacked page.
- **Each anomaly table now has a column picker**, and the internal `ip_address_id` UUID column is hidden by
  default (still selectable).

### Added
- **MAC drift now also shows the matching IP / hostname** for each drifting MAC (resolved from IPAM, with
  ARP fallback), so you can tell which host a roaming MAC belongs to.


## [0.5.5] - 2026-06-26

### Added
- **Scan agents: a "Dependencies" column.** Each agent now reports its probe-tool inventory; the column
  shows how many are installed (e.g. `4/7`) and clicking opens a detail dialog listing every tool: whether
  it is installed and at which version, which probes it enables (nmap → OS/ports, nmblookup → NetBIOS,
  avahi-resolve → mDNS …), and the install command for the missing ones. Helps diagnose "no machine name"
  (NetBIOS needs `nmblookup`) at a glance. Agent self-updates to v1.5.0 to report this (migration 0086).


## [0.5.4] - 2026-06-24

### Fixed
- **Background tasks could stay "in progress" forever after a restart (issue #9).** Tasks run via
  `asyncio.create_task` inside the worker process, so a backend restart (deploy / upgrade / crash) orphaned
  any in-flight task with no terminal status, leaving it stuck "running" in Operations. On startup, lingering
  pending/running tasks are now reconciled to `failed` ("interrupted: backend restarted").
- **LibreNMS sync aborted midway with a duplicate device-port error (issue #12).** Port sync now upserts
  (`ON CONFLICT (device_id, name)`) instead of a plain insert, so an existing port (e.g. two LibreNMS
  devices mapped to one jt-ipam device, or a re-processed interface) no longer breaks the whole sync with
  `UniqueViolationError` on `device_port_unique_name`.


## [0.5.3] - 2026-06-24

### Fixed
- **Contact groups could not be created / edited / deleted: "Method Not Allowed" (issue #11).** The
  backend only had `GET /contact-groups`; added `POST` / `PATCH` / `DELETE`.
- Added the missing `DELETE` endpoints for **providers, circuits, wireless SSIDs and wireless links**;
  their delete buttons previously returned 405 (same class of bug).


## [0.5.2] - 2026-06-24

### Fixed
- **Proxmox VM list capped at 500 (issue #9).** The list now fetches every page, so all VMs show
  (e.g. 592, not 500). The same paginate-all fix covers other advanced-resource lists.
- **Proxmox sync slow / stuck "in progress" (issue #9).** The best-effort per-VM guest-agent IP query
  now uses a short 6 s timeout, so unresponsive guest agents on running VMs no longer stall the whole
  sync (previously each could hold the shared 20 s timeout).
- **Wazuh agent list showed only 200 (issue #10).** All agents were stored; the admin page now fetches
  every page instead of just the first 200.
- **Other integrations audited for the same cap.** LibreNMS `/devices` and AdGuard already return
  everything; OPNsense alias / rule / IPsec searches no longer cap at 1000 / 500 (`rowCount = -1` = all).

### Changed
- Table footers now show the total row count on the left (e.g. "Total: 592").
- The floating AI-chat button is semi-transparent at rest and turns solid on hover.


## [0.5.1] - 2026-06-24

### Added
- **RDP / VNC "send keys".** Send special key combos the browser/OS would otherwise intercept (Esc, Tab,
  F1–F12, Ctrl + Alt + Del, ⊞ Win, Alt + Tab; VNC adds macOS ⌘ combos) from a keycap-styled menu with
  per-platform icons.
- **RDP "refit".** One click reconnects at the current window size for a crisp native picture (aardwolf
  cannot hot-resize a live session, so it rebuilds the session to match).
- **Richer version page.** Adds asyncssh / aardwolf / Pillow package versions, a host-environment section
  (OS / kernel / nginx / Node.js / PostgreSQL) and frontend-framework versions (Vue / Naive UI / Vite…),
  with a reorganized layout.
- **Expose MCP to external systems (read-only).** New toggle under Admin → LLM / AI; only when on does
  jt-ipam accept external HTTP MCP calls (`/api/mcp`, Streamable HTTP / JSON-RPC). Generate/regenerate a
  **read-only** API key (stored encrypted); the page shows the endpoint URL and auth header (name → value).
  The read-only key always blocks the 6 data-changing tools (and hides them from the tool list). Off by
  default (deny-by-default); existing per-user API-token auth still works and is also gated by the toggle.
- New MCP tool `list_connection_targets` (read-only): lists IPs/devices with a browser remote console
  enabled (SSH / RDP / VNC) that the caller may reach; it never returns credentials.

### Changed
- Console toolbar: a protocol label (SSH / RDP / VNC) sits next to the hostname; buttons are more compact
  and clearly clickable, with a red-outline disconnect. In Advanced → Connections and on IP detail, the
  console action buttons collapse to icon-only only when too narrow (threshold scales with the protocols
  per row).
- The relationship graph now shows the PVE node a VM runs on (and that node's rack/room) when a host is a
  Proxmox VM guest, on both the IP and device detail pages.

### Fixed
- **Proxmox VMs with the same name in one cluster could not be imported (issue #8).** The VM uniqueness
  key changed from `(cluster, name)` to `(cluster, VMID)` (migration 0085): Proxmox allows same-named VMs
  with different VMIDs, which previously collided with `vm_cluster_name_uq`.
- **AI chat: recover tool calls emitted as text.** A (tool-capable) model occasionally returns a tool call
  as inline text instead of structured `tool_calls`; these are now parsed and executed instead of leaking
  into the answer, with a neutral retry notice when unrecoverable.
- The external MCP sub-app no longer serves FastAPI's auto-generated `/openapi.json` and `/docs` (MCP is
  discovered via JSON-RPC `tools/list`, not OpenAPI; that schema was meaningless to MCP clients and
  unauthenticated).
- Audit detail shows `switch_port` as `device@port` (consistent with other pages) and resolves credential
  targets to a label instead of a raw UUID.


## [0.5.0] - 2026-06-22

### Added
- **In-browser RDP connection management (Beta).** Open a Windows RDP desktop straight from an IP's
  detail page (verified against NLA-enforced Windows 11).
  - Per-IP `rdp_enabled` toggle (migration 0083); permission `can_use_rdp` (deny-by-default, reuses the
    `can_ssh` capability); detail-page split button + an "RDP" filter/action in Advanced → Connections.
  - Backend `endpoints/rdp_console.py`: single-use ticket → WebSocket bridge to the remote desktop
    (NLA / CredSSP+NTLM); framebuffer streamed as PNG tiles to a `<canvas>`, keyboard/mouse/wheel sent
    back; target host locked to the catalogued IP (anti-SSRF); session open/close audited (never the
    password); a concurrency cap (`rdp_max_sessions`).
  - Native `<canvas>` rendering, **no new frontend dependency**. Resolution picker incl. "auto-fit".
- **In-browser VNC connection management (Beta).** Same pattern for VNC (RFB) targets (verified against
  a real VNC server).
  - Per-IP `vnc_enabled` toggle (migration 0084); permission `can_use_vnc`; detail-page split button +
    "VNC" in Advanced → Connections.
  - Desktop size is server-decided; the screen has a **Fit / 1:1 scale toggle** (with correct
    mouse-coordinate mapping when scaled).
  - **VNC auth support: RFB security types None and VNC Authentication (password) only.** Account-based
    schemes (UltraVNC MS-Logon, VeNCrypt, RealVNC RA2/RA2ne) are not supported; the connect screen
    states this.
- **Optional dependency, zero impact on the base install.** RDP/VNC use `aardwolf` (pinned to a version
  with prebuilt manylinux wheels → no Rust toolchain needed). Install/upgrade attempt it **best-effort**
  (`pip install --only-binary=:all: -e ".[rdp]"`); if no wheel exists it fails fast and the feature is
  simply disabled. The backend detects availability and the UI hides the entry points when absent.
- The shared **per-user encrypted credential vault** now stores SSH / RDP / VNC credentials
  (`protocol` + optional `domain`); credential audit records carry the protocol (e.g. `rdp_credential`).

### Changed
- Advanced → Connections lists SSH/RDP/VNC targets together; the OS column resolves through the same
  source-precedence as the detail page.
- nginx WebSocket-upgrade location widened to cover the SSH/RDP/VNC console paths; the upgrade path
  patches existing sites in place.

### Fixed
- Audit detail shows `switch_port` as `device@port` (consistent with other pages) and resolves credential
  targets to a label instead of a raw UUID.

## [0.4.210] - 2026-06-21

### Added
- **"Remember" SSH credentials (per-user, individually owned).** Each user can store their own
  password / private key and reuse it next time without retyping:
  - Backend `ssh_credentials` (migration 0082): password / private key / passphrase are each
    **envelope-encrypted** (per-field random DEK wrapped by the master KEK = ENCRYPTION_KEY, AAD bound to
    owner+field); plaintext never hits the DB, logs, or the frontend.
  - `GET/POST/DELETE /api/v1/ssh-credentials`: owner-only, masked reads (never plaintext).
  - Connecting now uses a **reference (credential_id)**: the frontend sends only the id; the backend
    decrypts in-memory at connect time and discards it. `can_use_ssh(target)` is still enforced; scope
    supports both target-bound and personal-default (any IP the user may reach).
  - Audit logs the `credential_id` (never plaintext) and flows to the existing SIEM forwarder; disabling a
    user makes their credentials unusable immediately.
  - Connect form gains a "Saved credential" dropdown (pick to connect) and a "Remember" toggle.

### Out of scope (roadmap)
- PTY session recording, MFA re-auth for sensitive targets, external Vault/KMS-backed KEK, SSH CA short-lived certs.

## [0.4.209] - 2026-06-21

### Added
- **Advanced → Connections page**: a table of all SSH-enabled targets you're allowed to connect to (backend `GET /addresses/ssh/targets`, same deny-by-default filtering as `can_use_ssh`), with sort / live filter / column picker / export, and per-row "SSH" (new tab) or dropdown "open in new window".

### Changed
- The IP detail "SSH" button now **opens a new tab** (main click) and **a new window** (dropdown); the in-page embedded terminal was removed.
- SSH connect form reordered: auth method first, password directly under username.
- Connection status is now a colored-dot pill badge (connected pulses green); disconnect / reconnect / open-in-new-window all have icons.

### Fixed
- After enabling "SSH management" and saving, the SSH button required a refresh to appear: the PATCH `/addresses/{id}` response didn't compute `ssh_available`; now it does (matching GET).

## [0.4.208] - 2026-06-21

### Added
- **SSH connection management for IP addresses (embedded / pop-out terminal).** A new "Enable SSH management"
  toggle in the IP edit dialog; once enabled, authorized users see an "SSH" split button at the top-right of the
  detail page (left of Edit): the main button opens an xterm.js terminal inline, and the dropdown arrow offers
  "Open in new window" for a standalone full-page terminal.
- **Connection security:** the client first exchanges its JWT for a single-use 60-second ticket, then opens a
  WebSocket with `?ticket=` (bridged to SSH via asyncssh on the backend). Credentials (password / private key)
  are **sent only at connect time, never stored, never logged**; the target host is fixed to the IP record's
  address (so it can't be abused as a generic SSH proxy); host keys use trust-on-first-use pinning (mismatch warns);
  session open/close are audited.
- **Permission:** a new standalone "SSH access" capability (`users.can_ssh`). Usage is allowed for admins, users
  with write on the IP, or users with the SSH-access capability who can at least view the IP (deny-by-default).
  Toggle per user in the Users admin page.

### Changed
- nginx site config (incl. the external reverse-proxy template) now sets WebSocket upgrade headers and a long
  read timeout for the SSH terminal (`deploy/nginx/*.conf`). ⚠️ Apply this to the production nginx as well.
- New frontend deps `@xterm/xterm` / `@xterm/addon-fit` (pure frontend, bundled at build time; picked up
  automatically by the install/upgrade pnpm install).

## [0.4.207] - 2026-06-19

### Changed
- **Docker Compose now auto-generates the admin password.** `gen-env.sh` also generates a random `admin`
  password (printed in its output, stored as `JT_IPAM_ADMIN_PASSWORD` in `.env`, mode 0600); the backend
  creates the admin on first boot using it, so you can log in straight away, matching the systemd installer's
  "auto-create admin" experience.
- **The site's Deployment section is now split into two zones:** "Primary: systemd + apt" and "Optional:
  Docker Compose", each boxed/badged with its own install / first-password / upgrade commands. The Docker
  zone spells out that upgrading is `./update.sh` (**not** `jt-ipam.sh upgrade`).
- docs/INSTALL §2.7 and the deploy/docker README (EN + zh) "first admin" notes updated to match.

## [0.4.206] - 2026-06-19

### Changed
- **Graylog DSV settings: "Format" and "Token" are now two side-by-side cards** (each bordered / tinted /
  rounded) for a clear, tidy separation, wrapping on narrow screens, replacing the stacked layout.

## [0.4.205] - 2026-06-19

### Fixed
- **Two Docker Compose startup issues** (caught by actually running `docker compose` end-to-end):
  1. **`.env.example` had `BACKEND_BIND_HOST=0.0.0.0`, which the security check rejects** in nginx mode (it
     requires a loopback bind) → changed to `127.0.0.1`; the container's uvicorn still binds `0.0.0.0` (via the
     image CMD, only on the compose network, not published to the host).
  2. **`sync` / `web` started before DB migrations finished** (`depends_on: service_started` only waits for the
     container to start) → `backend` now has a healthcheck (healthy once uvicorn is listening = after
     migrations), and `sync` / `web` use `depends_on: service_healthy`, eliminating the first-boot
     `relation "opnsense_firewalls" does not exist` error.
- Verified by a full `docker compose up`: all 5 services healthy, HTTP→HTTPS redirect, frontend and `/api`
  proxy both return 200, admin auto-created, admin login returns an access token, and the `sync` loop runs
  with zero errors.

## [0.4.204] - 2026-06-19

### Added
- **Optional Docker Compose deployment** (`deploy/docker/`). A secondary / optional path (systemd + apt
  remains the primary one): one compose file brings up `postgres` (pgvector) / `redis` / `backend` / `sync`
  (a background sync loop replacing the systemd timer) / `web` (nginx serving the frontend + reverse-proxying
  `/api` + self-signed HTTPS). Ships `gen-env.sh` (random secrets) and `update.sh` (`git pull` → rebuild →
  restart). **Upgrading is just `./update.sh`**: the backend container runs `alembic upgrade head` on start,
  so there's no manual migration step. Verified end-to-end: images build, a fresh pgvector runs all
  migrations 0001→0080, the admin is auto-created, and uvicorn boots.

## [0.4.203] - 2026-06-18

### Changed
- **Proxmox VE VM DSV is now per-cluster (supports multiple PVE clusters / standalone nodes).** Since vmids
  repeat across clusters, a single global DSV would conflate them. Added a per-cluster endpoint
  `GET /api/v1/lookup/proxmox/{cluster_id}/vms`; the Graylog DSV settings page lists **one row per cluster**
  (mirroring OPNsense's multiple firewalls), each with its own URL / lookup table. The global
  `…/proxmox/vms` (all clusters, de-duplicated) is kept for single-cluster setups.

## [0.4.202] - 2026-06-18

### Added
- **New Graylog DSV source for Proxmox VE VMs (vmid → VM name).** Endpoint
  `GET /api/v1/lookup/proxmox/vms` (reusing the Graylog DSV token) maps key = Proxmox VMID to value = the
  synced VM name, so Graylog can enrich a log's vmid with a readable VM name. If vmids collide across
  clusters, only the first per vmid is emitted. The Graylog DSV settings page lists it automatically
  (global, alongside "IP → hostname").

### Fixed
- **Firewall DSV hint text column indices** also corrected to key = 0, value = 1 (0-based; the previous
  release only fixed the main guide table and missed this hint string).

## [0.4.201] - 2026-06-18

### Changed
- **Added a "Delete" button to the subnet detail page toolbar** (with a confirm prompt). Previously you had to
  go back to the "All subnets" list and use the row trash icon or batch delete, and the actions column is
  often pushed off the right edge. Now you can delete a subnet straight from its detail page; it refreshes the
  sidebar subnet tree and returns to the list.

## [0.4.200] - 2026-06-18

### Fixed
- **Version check flagged an older version as newer.** "Check GitHub latest" compared version strings with
  `!=`, so `0.4.79` looked newer than `0.4.199` (string-wise `'7' > '1'`); and since releases are pushed to
  main without a release/tag, it fell back to a stale tag. It now reads `version.py` from the **main branch**
  (reflecting what's actually published) and compares **numerically** (the tags fallback also picks the
  numerically-highest).

### Changed
- **Version Info page layout:** "Check GitHub latest" now sits in the third cell of the top row (next to
  Current version / Python) instead of spanning its own full-width row.
- **Hardened LibreNMS auto-create subnet selection to avoid wrong placement.** The target subnet is now the
  *single most-specific* (longest-prefix) match: nested ranges pick the most specific; under **overlapping
  subnets where two+ share the longest prefix, it skips rather than guessing** (better to not create than
  create in the wrong unit); no creation if no existing subnet contains the IP. Set the instance's subnet
  scope to disambiguate.

## [0.4.199] - 2026-06-18

### Fixed
- **Graylog DSV guide had the wrong Key/Value column indices.** Graylog's "DSV File from HTTP" adapter uses
  **0-based** column indices, so the correct values are **Key column = 0, Value column = 1**; the guide page
  and README previously said 1/2.

## [0.4.198] - 2026-06-18

### Fixed
- **Firewall rule DSV (`rid → alias`) dropped UUID-format rules.** A filterlog `rid` (the pf rule label) comes
  in two formats: a 32-char md5 (pure hex) and a UUID (with hyphens). The old `_RL_LABEL` regex `[0-9A-Za-z]+`
  excluded hyphens, so rules with a UUID label failed to match entirely and were skipped; only the md5-labeled
  ones survived (one firewall captured 10 rules when it should have been 59, covering 44 aliases). The pattern
  now captures the full quoted label content (which *is* the `rid`), covering md5 / UUID / custom labels.
  > Note: `rid → alias` only ever covers aliases referenced by a labeled rule; aliases not used in any rule have
  > no `rid` (and never appear in filterlog), which is expected.

## [0.4.197] - 2026-06-18

### Added
- **Cert-distribution agents can link to a device.** The agent edit dialog gains a "Linked device" picker
  (`cert_agents.device_id`, migration 0080, SET NULL on device delete). Once linked: ① the agent **name**
  in the distribution-agents list and the **Advanced → Cert distribution status** page becomes a clickable
  link to that device's detail; ② the **source-IP column** becomes clickable; the backend resolves the
  agent's reported source IP to its IPAM address (preferring the one attached to the linked device under
  overlapping ranges) and links to it. Falls back to plain text when there is no linked device or the
  source IP has no matching address.

### Changed
- **Graylog DSV guide tweaks.** "Format" (output setting) and "Regenerate token" (the key) are unrelated and
  no longer share a row. The Extractor and Pipeline are **alternatives** (pick one), not sequential steps;
  they are now "Method A / Method B" under Step 2 sharing one "log field" input, instead of being numbered
  Steps 2 and 3. The click-to-copy toast now says "Copied to clipboard".

## [0.4.196] - 2026-06-18

### Added
- **LibreNMS sync can auto-create discovered IPs.** Each LibreNMS instance gains an "Auto-create
  discovered IPs" toggle (default on): on sync, each monitored device's **primary IP** is auto-created
  as an IPAddress inside the matching existing subnet (tagged `discovery_source=librenms`). Device
  primary IPs only, not ARP neighbours; if the instance has a subnet scope, only within that scope; and
  skipped if the subnet does not exist in IPAM yet. Fixes the confusing "0 used / live status all zero"
  state when only LibreNMS is connected (no scan agent): LibreNMS imports devices and previously only
  stamped liveness onto pre-existing IPs, never creating them.

### Fixed
- **Dashboard "live status" miscounted scanner/LibreNMS-confirmed online IPs as "unknown".** The counter
  matched against case-mismatched literals (`Online (scanner)` etc.), but the values actually written are
  lowercase with a source suffix (`online (scanner)` / `online (librenms)`) → now uses
  `startswith("online")` (matching `recompute_effective_status`).

### Changed
- **Default chat model is now `gemma4:26b`** (was `gpt-oss:120b`), aligning the compiled default with
  the README's existing recommendation; applies to anything that hasn't overridden it in LLM settings
  (including fresh installs). Existing overrides are unaffected.
- **Docs:** the Local AI section now notes that no LLM Server is bundled; set one up on a GPU-capable
  host and point jt-ipam at it.

## [0.4.195] - 2026-06-18

### Changed
- **Graylog DSV page cleanup.** The DSV sources table loses the redundant "Copy" button in the actions
  column (value copying already lives in the guide below: click any value to copy); the "Details" button
  is renamed to "URLs / settings" to better describe the lookup URLs and settings it shows.
- **"Log field to query" input moved into Step 2 (Extractor).** It used to sit orphaned between Step 1 and
  Step 2 with no step number; it now lives where it is first used (above the Extractor's Source field), and
  the Step 3 (Pipeline) text now points at "the log field configured in Step 2".

## [0.4.194] - 2026-06-18

### Changed
- **Graylog DSV guide polish.** The setup steps now use prominent numbered circles (matching the cert
  install help), and every source (including the firewall rule/alias DSVs) shows **both** the Extractor
  and the Pipeline method (each with the concrete field / Lookup Table / output for that source). The
  config tables now tint the left (field-name) column to separate it from the values, and every value you
  paste into Graylog is **click-to-copy** (click any highlighted value).

## [0.4.193] - 2026-06-18

### Changed
- **Graylog DSV page: the endpoint list is now a real data table and drives the guide.** The DSV sources
  table gains sorting, a column picker, a quick-filter box and a refresh button; clicking a row selects
  that source and the Graylog setup guide below re-renders for it (correct lookup URL, Lookup Table
  names, key/value columns and a matching pipeline rule: IP→hostname keeps the LAN cidr_match guard and
  firewall rule/alias sources use a plain rid/alias lookup), with a fade/slide transition when switching.
  The page also drops its fixed max-width and uses the full width. Term: "詳細資料" → "詳細資料".

## [0.4.192] - 2026-06-18

### Changed
- **Graylog DSV page reworked into one extensible endpoint table + detail drawer.** Instead of stacking a
  separate card with two URL boxes per DSV source (which got cluttered as firewalls were added), all DSV
  endpoints (IP→hostname plus each firewall's rule and alias lookups) now appear in a single table
  (name / mapping / status / actions); clicking "Details" opens a drawer with the HTTPS + intranet-HTTP
  URLs, copy buttons, and per-source settings (the IP→hostname enable/path live there). The shared format
  and token sit above the table. New DSV types only need a row in the source list, so the layout scales.

## [0.4.191] - 2026-06-18

### Added
- **OPNsense firewall Graylog DSV (rule label → alias, and alias → members).** In addition to the existing
  IP→hostname DSV, each OPNsense firewall can now expose two token-protected lookup tables for Graylog to
  enrich firewall logs: `/api/v1/lookup/firewall/{id}/rule-aliases` (key = filterlog `rid` / pf rule
  label, value = the alias names that rule references) and `/api/v1/lookup/firewall/{id}/aliases`
  (key = alias name, value = member list). The rule-label map is parsed each sync cycle from
  `/api/diagnostics/firewall/pf_statistics/rules` (covers user + plugin + auto rules); the alias DSV uses
  the already-synced alias content. Enable per firewall with the new "Expose firewall DSV" toggle
  (Integrations → OPNsense); the lookup URLs (per firewall, distinct paths) appear on the Graylog DSV
  settings page. Migration 0078 (opnsense_rule_labels + opnsense_firewalls.expose_dsv).

## [0.4.190] - 2026-06-17

### Changed
- **Circuits table now shows bandwidth, static IP and gateway columns.** These fields already existed on
  the circuit (and in the edit form) but weren't surfaced in the list; added a human-readable bandwidth
  column (↓down / ↑up, formatted as Gbps/Mbps/kbps) plus the static IP/CIDR and gateway columns (all
  toggleable in the column picker).

## [0.4.189] - 2026-06-17

### Security
- **Cleared the open Dependabot alerts** (frontend build toolchain) by pinning patched versions via
  `pnpm.overrides`: `form-data` ≥4.0.6 (CRLF injection, GHSA-hmw2-7cc7-3qxx; reached via axios/jsdom),
  `vite` ≥6.4.3 (`server.fs.deny` bypass on Windows, GHSA-fx2h-pf6j-xcff; also fixes the bundled
  launch-editor NTLMv2 advisory), and `js-yaml` ≥4.2.0 (quadratic-complexity DoS in merge keys). `pnpm
  audit` is now clean and the build is unchanged (vite stays in 6.x). These are build/dev dependencies and
  are not part of the shipped browser bundle.

## [0.4.188] - 2026-06-17

### Changed
- **The scan-agent installer no longer installs avahi (mDNS) by default.** `avahi-utils` depends on
  `avahi-daemon`, so installing it brings up a resident service that listens on UDP 5353 and announces
  the host over mDNS, an unwanted side effect on most servers. The installer now installs only `nmap`
  (OS) and `samba-common-bin` (NetBIOS), neither of which starts a daemon; mDNS is opt-in via
  `JT_IPAM_ENABLE_MDNS=1`. (The main server install/upgrade never touched these.) The agent
  install-help note now flags that avahi-utils brings up avahi-daemon.

## [0.4.187] - 2026-06-17

### Changed
- **NetBIOS / mDNS hostname sources now show localized labels** in the IP detail panel (the source tags
  and the "pin hostname source" dropdown), matching the source-precedence page. Added a regression test
  asserting NetBIOS / mDNS names from a scan-agent report are recorded as distinct `netbios` / `mdns`
  observation sources.

## [0.4.186] - 2026-06-17

### Fixed
- **Save button in the IP address edit modal did nothing / lost edits (issue #6, thanks @lin-junyou).**
  The conditionally-rendered action buttons (Save / Edit / Create / Cancel / Back) and the delete
  popconfirm shared a slot via `v-if`/`v-else` with no unique `:key`, so Vue reused the vnode across the
  view↔edit switch and kept the *previous* branch's `@click`: clicking Save fired Back/Edit and the edit
  was silently dropped. Gave each conditional button/popconfirm a stable `key` (both the inline
  `#header-extra` and the modal `#footer`).
- **Install on Ubuntu 26 failed with "requires a different Python: 3.14 not in '<3.14,>=3.11'" (issue #5,
  thanks @Ghucos).** Ubuntu 26.04 ships Python 3.14; the backend's `requires-python` capped it below 3.14,
  so pip refused to install. Widened to `>=3.11,<3.15` to allow 3.14.

## [0.4.185] - 2026-06-16

### Added
- **NetBIOS and mDNS name probes are now actually implemented** in the scan agent (previously they were
  advertised as selectable probes but were no-op Phase-B stubs that produced no name). The agent now runs
  `nmblookup -A <ip>` (or `nbtscan`) for NetBIOS and `avahi-resolve -a <ip>` for mDNS against alive hosts
  that have those probes enabled, and reports the resolved names. They are recorded as **distinct hostname
  sources** (`netbios` / `mdns`) so you can order or disable them independently in **Name / ARP source
  precedence**. Agent bumped to v1.4.0 (self-updates). SNMP remains intentionally unimplemented
  (credential-based). No migration (the observation `source` column is unconstrained).

## [0.4.184] - 2026-06-16

### Changed
- **Login language switcher is now a click-to-open dropdown** listing both languages, instead of a button
  that toggled immediately.
- **"Save order" buttons on the source-precedence page now have a save icon** (all five sections).

## [0.4.183] - 2026-06-16

### Changed
- **Login page now has a language switcher** (zh-TW ⇄ en-US) in the card header, so you can switch
  language before signing in.
- **Notification bell tidy-ups:** an icon before the "Notifications" title and on the "mark all read"
  button, and the list now scrolls inside the popover (capped height) instead of growing past the screen
  when there are many notifications.
- **IP-request notifications are now Chinese** ("IP 申請已核准" / "IP 申請已拒絕") instead of the
  hardcoded English "IP request approved/rejected" (matching the other in-app notifications).
- **Scan-agents table column widths:** the source-IP column no longer wraps, and the spare width is
  shared between the name and last-error columns instead of leaving the name column overly wide.

## [0.4.182] - 2026-06-16

### Changed
- **Login: SSO buttons only show for configured providers.** `/auth/realms` now also reports which SSO
  providers (OIDC / SAML) are enabled, and the login page renders a provider's button only when it is
  actually configured, so clicking e.g. "Sign in with SAML" no longer dumps a raw `{"detail":"SAML is
  disabled"}` page. The whole "or SSO" section is hidden when neither is enabled.
- **Login: the jt-ipam logo now appears before the title** on the login card.
- **Webhooks: events are now a checkbox list with descriptions** instead of a free-text tag input. The
  catalogue lists exactly the events the backend emits (`subnet.created`, `ip_request.created` /
  `.fulfilled` / `.rejected`, `anomaly.detected`) plus `*` (all), each with a one-line explanation.
- **Integration scope: tidier layout.** On the six integration settings forms the scope-subnet dropdown
  and the overlap warning now stack in a full-width block instead of being squeezed side-by-side.
- **RIPE / TWNIC import: less cramped fields**. There is now comfortable spacing between the Handle / CIDR /
  target-section rows so the hints no longer touch the next label.

### Added
- **LLM settings: optional chat context length (`num_ctx`).** Lets an admin raise the chat model's
  context window so tool-heavy MCP chats with large injected data don't overflow Ollama's default (~4096)
  and get silently truncated. Blank / 0 = use the model/Ollama default; flows into Ollama `options.num_ctx`
  for chat only (not embeddings).

## [0.4.181] - 2026-06-16

### Changed
- **Tidier certificate detail panel.** The per-version detail in the certificate Files modal (domains /
  subject / issuer / serial / validity / fingerprint / uploaded-at) is now a two-column aligned grid
  (definition list) so every value lines up in a single column, with serial and fingerprint in a
  monospace font. Previously it was a ragged list of `label：value` lines.

## [0.4.180] - 2026-06-16

### Fixed
- **nginx config test failing on Debian 13 with `"server_tokens" directive is duplicate`.** Our nginx
  site set `server_tokens off;` at http context (top of the included file). Debian 13's stock
  `nginx.conf` now ships `server_tokens off;` in its own `http{}` block, so a second one in the same
  context is a fatal `[emerg]` (older Debian/Ubuntu had it commented out, so it never clashed). Moved
  `server_tokens off;` into each `server{}` block in both `jt-ipam.conf` and the external-proxy template, since
  server context coexists with / overrides any http-level value on every distro. Verified with
  `nginx -t` under a parent `http{}` that already sets it. Config template only.

## [0.4.179] - 2026-06-15

### Fixed
- **Install silently aborting right after `Building frontend…` on hosts without `~/.nvm`** (same
  `set -e` + `pipefail` class as v0.4.178). In `ensure_node`, `nb=$(find ~/.nvm/... | sort | head -1)`
  fails the whole assignment when `find` hits a missing directory (or `head` SIGPIPEs `sort`), and under
  `set -e` that exits the script with **no error message**, leaving Node uninstalled and the frontend
  unbuilt while the run "looked" like it just stopped. Guarded that and the other pipe-in-`$()` spots
  (nvm lookup, admin-password generation, backup-file lookup) with `|| true` so a failed/SIGPIPE'd
  pipeline can no longer abort the install. The success path is unchanged (the guard is a no-op when the
  pipeline succeeds), so working installs are unaffected. Install-script only.

## [0.4.178] - 2026-06-15

### Fixed
- **Real root cause of the Debian 13 install failure: a `set -o pipefail` + `grep -q` SIGPIPE bug in the
  package-availability check.** `apt-cache madison <pkg> | grep -q .` reports a package as *unavailable*
  whenever madison emits multiple version lines (e.g. trixie lists `postgresql-17` twice, 17.10 from
  -security and 17.9 from main): `grep -q` exits on the first line and closes the pipe, `apt-cache` gets
  SIGPIPE (rc 141) writing the next line, and `pipefail` propagates that as a failed pipeline. So the
  installer "couldn't see" native PG 17 + pgvector even though both exist, and fell through to PGDG and a
  FATAL. Replaced the piped check with a pipe-free `_pkg_installable()` (command substitution + `[ -n ]`),
  applied to both the PostgreSQL and Python detection loops. Single-version distros (Ubuntu 24.04) emit
  one line and never hit it, which is why it surfaced only on Debian 13. Install-script only.

## [0.4.177] - 2026-06-15

### Changed
- **Installer refreshes the apt index and retries before falling back to PGDG.** If no PostgreSQL
  (>= 16) with a matching `postgresql-N-pgvector` is found in the default repos on the first look, the
  script now runs `apt-get update` once and re-checks before adding the PGDG repo, so a transient/stale
  apt index at install time (the likely reason a Debian 13 box with native PG 17 + pgvector wasn't picked
  up) uses the native packages cleanly instead of needlessly pulling in PGDG. Install-script only.

## [0.4.176] - 2026-06-15

### Fixed
- **Install on Debian 13 (trixie) no longer dies on `postgresql-16-pgvector` not installable** (customer
  report). The installer used to pick a PostgreSQL server package by itself and, on fallback, hardcode
  PG 16, but PGDG for trixie currently ships pgvector only for its newer versions (17/18), so
  `postgresql-16-pgvector` was missing and the install aborted. It now selects a PostgreSQL version where
  **both** the server **and** the matching `postgresql-N-pgvector` are installable (tries 16 → 17 → 18 in
  the default repos first, then adds PGDG and retries), instead of forcing 16. Install-script only.

## [0.4.175] - 2026-06-15

### Changed
- **Config-generator service grid no longer wraps long labels**: the service multi-select now uses
  auto-fill columns wide enough (min 135px) for the longest profile name (`wazuh-dashboard`) and keeps
  each label on a single line, so only that one option no longer breaks onto two rows.
- Docs: the certificate-distribution caption now reads "certificate files can be uploaded manually or
  pulled from a URL / SFTP source on a periodic sync".

## [0.4.174] - 2026-06-15

### Changed
- **Hid the `jitsi` and `coturn` cert-distribution service types** from the deploy-profile picker for now:
  docker-jitsi-meet is not officially supported yet, so those options are no longer offered in the UI or
  listed in the docs (the dormant agent profile code is kept for easy re-enable later). Also refreshed the
  docs gallery (added a certificate-distribution screenshot) and the feature map's certificate-vault branch.

## [0.4.173] - 2026-06-15

### Added
- **Auto-fetched certificates (SFTP / URL sources) now auto-complete their chain.** When a sync pulls a
  new cert that only has leaf+intermediate, jt-ipam builds the full intermediate+root chain before storing
  (using the fetched files or the server's system trust store, e.g. ISRG Root X1), so strict services
  (Zimbra / PDM) keep verifying on every renewal without anyone clicking "Build full chain" again.
- New distribution profiles **`jitsi`** (docker-jitsi-meet web: `/root/.jitsi-meet-cfg/web/keys/cert.{crt,key}`,
  restarts the jitsi web container) and **`coturn`** (`/etc/coturn/certs/turn.{crt,key}`, root:65534 so the
  container user can read the key; restarts the coturn container or native systemd coturn).

## [0.4.172] - 2026-06-15

### Fixed
- **The cert-agent installer no longer hangs silently** in LXC/containers with a dead IPv6 path or a
  firewall blackhole. Its curl calls now use `--connect-timeout 10 --max-time 60 --retry 2` (so a stuck
  IPv6 attempt falls back to IPv4 in ~10s instead of hanging forever), print a "Downloading agent…" line,
  and emit a clear error with a connectivity-test hint if the download fails.

## [0.4.171] - 2026-06-15

### Changed
- The cert agent now prints progress lines for the slow Zimbra steps even without `--debug`
  ("verifying… / deploying… / restarting Zimbra (zmcontrol restart: can take a few minutes)…"),
  so a normal run no longer looks hung during the multi-minute `zmcontrol restart`.
- The installer-generated nginx site config (`deploy/nginx/*.conf` → `/etc/nginx/sites-enabled/jt-ipam`)
  now has **English-only comments** (customer-facing deployed files should not contain Chinese).

## [0.4.170] - 2026-06-15

### Fixed
- **Zimbra deployment ran `zmcertmgr` as root and failed** (`zmcertmgr: ERROR: no longer runs as root!`).
  It now runs via `su - zimbra` and stages the cert/chain/key in a zimbra-readable dir
  (`/etc/.../jt-ipam` → `/opt/zimbra/ssl/jt-ipam`), matching Proxmox/Zimbra's documented flow.
- The cert-status page no longer shows "up to date" for a deployment that actually failed: status now
  requires both a fingerprint match **and** an `ok` report.

### Added
- **Certificate chain check + one-click fix.** The Files/info dialog now analyses each version's chain:
  "Full chain" (reaches the root CA), "Chain fixable" (root present but not in the chain; a **Build full
  chain** button rebuilds it in place, fingerprint unchanged), or "Missing root CA" (with a hint on how to
  obtain and re-upload the root). Strict-validating services (Zimbra / PDM) need the full chain.
- The **Files dialog is now a detailed certificate-info view**: SAN domains, subject, issuer, serial,
  validity window, full SHA-256 fingerprint (copyable), upload time, plus per-format download.
- **Export buttons** on the Certificates, Distribution-agents and cert-status pages (the last two were missing).
- The cert-status page now shows **one row per agent** with its services aggregated (e.g. `pbs, pve`)
  instead of one row per deployment; the status tooltip lists each cert/service.

## [0.4.169] - 2026-06-15

### Fixed
- **Corrected the `pdm` (Proxmox Datacenter Manager) profile** to the official paths and service:
  cert+chain → `/etc/proxmox-datacenter-manager/auth/api.pem`, key → `…/auth/api.key` (root:www-data 640),
  reload `systemctl restart proxmox-datacenter-api.service`. (Previous paths/service were wrong guesses.)
- **Every generated shell command that used `sudo` is now root-aware.** A shared `SUDO` helper
  (`$([ "$(id -u)" -ne 0 ] && echo sudo)`) is applied to: the cert-agent dry-run / run commands and the
  install/uninstall one-liners, the scan-agent install one-liner, and the probe-tool `apt install` hints.
  On hosts that are already root with no `sudo` binary they now run directly.

### Added
- The cert agent gains a **`--debug`** flag (default off) that prints each command and shows the full
  output of config-test / reload / `zmcertmgr` / downloads, useful for diagnosing e.g. a Zimbra
  `verifycrt` failure (whose root cause is usually a chain missing the root CA).

### Changed
- Install-help step 3 now leads with the **Generate config** tool (quick path) and demotes manual
  config editing to a secondary note.

## [0.4.168] - 2026-06-15

### Fixed
- **Critical: the conditional-sudo install one-liner from 0.4.167 failed as root.** With `$(…)` expanding
  to empty, the `VAR=value` env assignments after it were parsed as a command, not an assignment
  (`JT_IPAM_URL=…: No such file or directory`). Fixed by running through `env`
  (`… | $([ "$(id -u)" -ne 0 ] && echo sudo) env JT_IPAM_URL=… bash`), which works as both root and non-root.
- The AI chat header action buttons now align hard-right (they could drift left when the header wrapped).

### Changed
- The cert-agent **install-help dialog no longer duplicates the full install command**: each agent's
  dialog already shows its ready-to-paste one-liner (key filled in, sudo auto-detected), so the help now
  just points there and keeps the supported-OS overview.
- Relabeled the one-liner from "(root)" to "(auto root / sudo)".

## [0.4.167] - 2026-06-15

### Fixed
- The cert-agent install / uninstall one-liners now add `sudo` **only when not already root**
  (`$([ "$(id -u)" -ne 0 ] && echo sudo)`). On hosts that are already root and have no `sudo` binary
  (common on Proxmox VE / PBS / PDM and minimal appliances) the previous `| sudo … bash` failed with
  `sudo: command not found`; it now runs directly as root.

## [0.4.166] - 2026-06-15

### Fixed
- **Deleting a certificate that a distribution agent still references is now blocked** (409 with the
  agent names) instead of leaving an orphan UUID in the agent's scope. The edit-agent dialog also now
  shows any already-orphaned scope entries as "<id>… (certificate deleted)" so they can be removed,
  rather than a bare UUID.

### Added
- New distribution profiles: **`pdm`** (Proxmox Datacenter Manager) and **`wazuh-dashboard`**
  (OpenSearch Dashboards). Univention UCS was evaluated and intentionally left to manual mode (its
  cert path is FQDN-specific and managed by the UCS internal CA).
- **Filter the distribution-agent list by certificate** (which cert an agent is scoped to), alongside
  the existing name/IP filter.

### Changed
- The distribution-agent **"deployed / reported" count** now shows the actual deployments on hover
  (each cert / profile and its status).
- **Tidied the cert-agent installer's post-install output**: one compact summary (timer, config status,
  deployable certs, test/apply commands, logs) instead of a long multi-line dump.

## [0.4.165] - 2026-06-15

### Changed: consistent table pagination + filter alignment
- Applied the shared `useTablePagination` (page-size bound to the user preference, cross-device) to all
  client-side list tables that were still missing it: the certificate + distribution-agent tables, the
  read-only cert-status page, and a sweep across Advanced resources, Physical (cabling/power/VPN),
  Virtualization, VLANs/VRFs, NAT, Devices, Scan agents, Groups, Permissions, Wazuh, Anomaly, firewall
  alias mappings, customer sub-tables and device ports. Server-paginated tables (addresses, audit, users,
  tasks, IP changes) and small fixed config/instance panels are intentionally left unpaginated.
- Fixed the certificate/agent/cert-status **filter inputs** rendering shorter than the toolbar buttons
  (toolbar buttons are forced to 34px; the inputs now use the default size to match).

## [0.4.164] - 2026-06-15

### Added: certificate tools for AI chat / MCP
- Two read-only MCP tools so the AI chat (and external MCP clients) can answer about certificates:
  - `list_certificates` returns managed cert metadata: name, domains, current fingerprint, expiry, days
    remaining, version count, self-signed flag, auto-fetch source; `expiring_within_days` filters to
    soon-to-expire certs.
  - `list_cert_distribution` returns distribution agents and their per-site deployment status (cert/profile,
    up-to-date vs drift, expiry, agent version, and whether one key is shared by multiple hosts).
- Both are **read-only and never expose private keys / PEM bodies**, and are gated as global-read
  infrastructure data (admin or a universal-read viewer), consistent with the cert-status page.

## [0.4.163] - 2026-06-15

### Added
- **Manual renew for self-signed certificates**: self-signed certs get a **Renew** action that
  re-issues a new version reusing the current CN/SANs (adjustable validity days), so agents pick it
  up on the next fingerprint change.
- **Same-key-on-multiple-hosts detection**: the agent records recent reporting source IPs
  (migration 0077, `recent_sources`); if a key is used from more than one IP within 7 days the
  distribution-agent list flags a warning next to the source IP, and the create-agent dialog +
  install help now recommend **one key per host**.
- **Agent CLI flags**: `--help` usage, `--upgrade` (self-update to the server's latest agent then
  exit, even when `AUTO_UPDATE=false`), and `--force` (re-deploy even when already up to date).
- Name/IP **filter box** on the certificate + distribution-agent tables; the read-only cert-status
  page (Advanced) gains a column picker, sortable columns, a filter row, and **source-IP + agent-version**
  columns. Tab headers got icons.

### Changed / Fixed
- **Agent now reports even when already up to date**: previously a re-keyed agent showed `0/0`
  because the up-to-date path sent no report; it now reports the current state every run.
- **Proxmox/Zimbra hardening (cont. from 0.4.162):** carried into this release with the version-column
  "update available" indicator changed from a text tag to a single icon that no longer wraps.

## [0.4.162] - 2026-06-15

### Added: more web-server / service profiles for the distribution agent
- The cert distribution agent (and the **Generate config** tool + installer) now ship 9 more profiles:
  **caddy / traefik / lighttpd / zoraxy / jetty / exim4 / mosquitto / cockpit / webmin** (on top of
  nginx / apache / haproxy / postfix / dovecot / pve / pmg / pbs / zimbra). Each provides its fixed
  write paths + reload command; **jetty** receives a **PKCS#12 keystore** (`<cert>.p12`), served via a
  new `part=pkcs12` on `GET /cert-agents/bundle/raw`.

### Changed: install-help UX
- Supported OS / distributions are shown as prominent tags (Debian / Ubuntu / RHEL family / Fedora / SUSE).
- Fixed the leading-space indent on the first line of the curl one-liner (inline `<code>` now `display: block`).
- The standalone **Config help** toolbar button is hidden: config generation lives in the per-agent
  **Generate config** action; step 3 of the install help points to it (with its tool icon).

## [0.4.161] - 2026-06-15

### Added: certificate file viewer & multi-format download
- A **Files** button on each certificate row lists every version (fingerprint / expiry / domains / current)
  and lets you **download** each one in a chosen format: full chain / cert (.crt) / chain / private key
  (.key) / combined / **DER** / **PKCS#12 (.pfx)** (built server-side via cryptography). Formats containing
  the private key (key / combined / pfx) are audited (`GET /certificates/{id}/versions/{vid}/file?fmt=`).

## [0.4.160] - 2026-06-15

### Changed
- Added right padding to the action column (delete button) so it no longer hugs the edge.
- When a certificate already has a source or a version, the **"Self-signed" button is disabled** (avoids
  overwriting the existing cert), with a hover explanation.
- The installer config comments now note you can use the "Generate config" tool in jt-ipam.

### Security
- Fixed Dependabot alert (GHSA-gv7w-rqvm-qjhr, High): bumped **esbuild to 0.28.1** via a pnpm override
  (0.25.12 came in through vite; <0.28.1 has a "Deno module binary integrity" issue). It's a build-time
  dev dependency and this project builds via Node/vite (not esbuild's Deno install path), so it isn't
  actually reachable; the frontend build passes after the bump.

## [0.4.159] - 2026-06-15

### Changed: richer config generator
- Each "certificate / service" block now **generates the service's SSL config snippet** (e.g. nginx
  ssl_certificate / ssl_certificate_key, apache SSLCertificate*), with a **copy button on every write path
  and on each snippet**. Services that read fixed paths (pve/pmg/pbs) show "no service config change".
- Added the **full dry-run / real-run commands** (with the complete sudo bash path) plus copy buttons.
- The service checkboxes are now laid out in a tidy grid.

### Added: edit agent / enable toggle
- The distribution-agent action column gains an **Edit** button: rename, **adjust the deployable-certificate
  scope** (add more later for more sites), and toggle enabled.
- The "Enabled" column is now a **switch** for one-click enable/disable.
- The "Deployable certs" column shows **which certificates** (names) on hover.
- "Rotate key" / "View key" tooltips clarify it's the **agent connection key (not the SSL cert)**.
- Install help step 3 points to the "Generate config" tool (with its icon); the toolbar "Config help"
  button was removed (reachable from inside the install help).

## [0.4.158] - 2026-06-15

### Added: distribution-agents page improvements
- **Config generator** (a tool button in the action column): pick certificates (within the agent's scope)
  and check services (nginx/apache/pve… multiple) to auto-generate the quick-mode config; an "Advanced /
  manual mode" section lets you fill custom paths. Live preview + one-click copy to paste into the host.
  It also **lists the full on-host paths/filenames each quick-mode profile writes** (cert / key / chain),
  so you know where to point your service config.
- The "Deployed / reported" column gained a tooltip (successful deployments / total reported).
- **Slimmer install help**: the config-format explanation is split into a separate **"Config help"** button;
  the install help keeps only the install/uninstall steps.
- **Latest server agent version** shown in the distribution-agents toolbar (`GET /cert-agents/server-version`).
- The "Close" button in the agent-info dialog now has an icon.

## [0.4.157] - 2026-06-15

### Changed
- The installer's `DEPLOY_1_CERT` example now uses the generic placeholder `example.com` (RFC 2606
  reserved domain) instead of a real certificate name; the real deployable names are still listed in the
  "This agent is allowed to deploy" comment above for you to substitute.

## [0.4.156] - 2026-06-15

### Changed: installer pre-fills the certificate names this agent can deploy
- At install time the installer asks the server (with the agent key) which certificates this agent may
  deploy, and **lists the real names in the config comments and pre-fills the `DEPLOY_1_CERT` example**, so
  you no longer have to guess what `DEPLOY_<N>_CERT` should be (it's the certificate name from jt-ipam).
- The installer also prints the deployable certificate list at the end (it won't overwrite an existing
  config, but still prints the list for reference).

## [0.4.155] - 2026-06-15

### Fixed
- Distribution-agent table: the version column's "update available" tag now wraps (and the column is
  wider) instead of overflowing into the source-IP column.
- Name and last-report columns are both flexible so they share the leftover width; the name column no
  longer over-stretches on its own.

## [0.4.154] - 2026-06-15

### Changed
- The agent config template is now split into **QUICK MODE (preferred)** and **MANUAL MODE** sections.
  The quick-mode comments spell out exactly which cert / key / chain paths and filenames each profile
  writes, with the matching nginx / apache directives, so you know what to point your service config at.

## [0.4.153] - 2026-06-14

### Changed
- The agent config template comments now **list every built-in profile** (nginx / apache / haproxy /
  postfix / dovecot / pve / pmg / pbs / zimbra / generic) with each one's default file paths and reload
  command, so opening the config file shows exactly what's available.

## [0.4.152] - 2026-06-14

### Changed
- Distribution-agent config now centers on `DEPLOY_<N>_PROFILE` (the service), which **provides the reload
  command**, so `DEPLOY_<N>_RELOAD` is no longer needed in the common case. Set just "cert + service", or
  add custom paths (`FULLCHAIN`/`KEY`…) to override where files go while still using the profile's reload;
  `DEPLOY_<N>_RELOAD` is demoted to an advanced override for custom services. Template and help updated.

## [0.4.151] - 2026-06-14

### Changed: distribution-agent config is now one setting per line
- The agent config moved from a single packed line (`DEPLOY_1="cert=..; profile=..; fullchain_path=.."`)
  to readable, one-setting-per-line `DEPLOY_<N>_*` groups:
  - `DEPLOY_1_CERT=` (certificate), `DEPLOY_1_FULLCHAIN=` (cert file path), `DEPLOY_1_KEY=` (key path),
    `DEPLOY_1_RELOAD=` (reload command); optional `DEPLOY_1_CHAIN/CRT/COMBINED/TEST`.
  - Or just `DEPLOY_1_CERT=` + `DEPLOY_1_PROFILE=nginx` to use a built-in profile (fixed paths).
- Installer template and the install-help modal example updated. Validated end-to-end against a live
  server (dry-run + real apply).

## [0.4.150] - 2026-06-14

### Changed
- The distribution-agent scripts (`jt_ipam_cert_agent.sh` and the installer) are now fully English
  (comments, terminal output, config template), matching the `scripts/*.sh` convention: scripts that run
  on customer terminals don't contain Chinese.
- The installer gains an **uninstall** mode: `JT_IPAM_UNINSTALL=1` stops and removes the timer / service,
  agent program, config and state (certificate files already deployed to services are kept). The install
  help modal now includes the uninstall one-liner.

## [0.4.149] - 2026-06-14

### Added: re-viewable agent key & install command
- A distribution agent's enroll key is now also stored AES-GCM encrypted (alongside the hash), so it can
  be **retrieved again from the "View" action** in the list (admin only, `GET /cert-agents/{id}/key`). The
  action column gains a "View" button that shows the key + the one-line install command (with the key) +
  copy buttons.
- The create / rotate-key dialog now also shows the one-line install command; "cannot be retrieved later"
  is replaced with "retrievable later via View".
- Deleting an agent also removes its encrypted key.
- Agents created on older versions (no stored plaintext) return a hint to rotate the key instead.

## [0.4.148] - 2026-06-14

### Changed
- After "Generate & install key", the login-private-key field becomes disabled and shows "Generated and
  stored by jt-ipam", so users don't think they still need to paste a key.

## [0.4.147] - 2026-06-14

### Fixed
- Certificate table layout: the action column is now `fixed: "right"` (pinned, never pushed off-screen on
  narrow widths) and widened to fit all icons; name and domains are flexible and share the leftover width.
- Traditional-Chinese copy now uses full-width punctuation and Taiwan-localized terms (rollback, one-time,
  atomic-write wording) across the agent install help, source config, and agent script comments.

## [0.4.146] - 2026-06-14

### Changed: distribution agent is now pure bash (no Python / PyYAML)
- The distribution agent was rewritten as **pure bash** (`jt_ipam_cert_agent.sh`), depending only on
  **curl + coreutils** (no Python, jq or YAML). Config is now `KEY=VALUE`
  (`/etc/jt-ipam-cert-agent/config`, `DEPLOY_N="cert=..; profile=.."`); profiles, atomic write,
  config-test, reload, rollback, `--dry-run` and self-update are all preserved.
- Backend support for the bash agent: `GET /cert-agents/check?format=text` (line-based, no JSON to parse),
  a new `GET /cert-agents/bundle/raw?cert=&part=cert|key|chain|fullchain|combined` (raw PEM straight to
  `curl -o`, with an `X-Cert-Fingerprint` header), and `POST /report` also accepts TSV. The download route
  is now `agent.sh` and version/self-update compare against the `.sh`. The installer no longer installs
  python3-yaml.
- The install-instructions modal was reorganized (numbered steps + spacing); requirements now read
  "pure bash, only needs curl + coreutils".

## [0.4.145] - 2026-06-14

### Fixed / Changed
- Certificate / distribution-agent tables now set `:scroll-x` (matching the rest of the app): the name
  column no longer over-stretches and the action column is no longer pushed off-screen; narrow viewports
  scroll horizontally instead of clipping.
- Source-type selector: the **selected type is now a solid green filled button** (previously only a thin
  border, making the active choice hard to tell); "Off (manual upload)" shortened to **"Manual upload"**.

## [0.4.144] - 2026-06-14

### Changed
- Certificate / distribution-agent action-column buttons are now **left-aligned** (centering removed),
  matching every other list page in the app.

## [0.4.143] - 2026-06-14

### Fixed: a class of post-commit serialization 500s (found via flow review)
- `updated_at` has a SQL-side `onupdate=func.now()`, so it's expired after an UPDATE flush; several cert
  endpoints serialized the ORM object right after commit, triggering a sync lazy load → `MissingGreenlet`
  500. Added `session.refresh` after commit (matching other endpoints): `PATCH /certificates/{id}`,
  `PATCH /cert-agents/{id}`, `POST /cert-agents/{id}/rotate-key` (v0.4.142 already fixed
  `PUT /certificates/{id}/source`).

### Changed: generating a key now installs the public key on the host
- Since jt-ipam already has the SFTP login password, "Generate key" now **logs in with the password and
  appends the public key to `~/.ssh/authorized_keys`** (idempotent), so you don't have to paste it. On
  success it shows "installed"; with no password or on failure the key is still generated and the public
  key is shown for manual install with the reason (`POST /certificates/{id}/source/ssh-keypair` now takes
  the source config and returns installed/message).

## [0.4.142] - 2026-06-14

### Fixed
- **500 when saving an SFTP/URL source** (MissingGreenlet): `PUT /certificates/{id}/source` serialized
  the ORM object after commit, triggering a lazy load in a sync context. Now refreshes the object
  (`session.refresh`) after commit before serializing.

### Added: Source connection test + auto-generated SSH key
- Source config gains a **"Test connection"** button: it actually probes the URL / SFTP source using the
  current form values (blank password/key = reuse stored), returning a success message or a readable
  failure reason, without saving (`POST /certificates/{id}/source/test`).
- The SFTP login private key gains a **"Generate key"** button: jt-ipam generates an ed25519 keypair,
  stores the private key AES-GCM encrypted (never returned), and returns the **public key** to add to the
  SFTP host's `authorized_keys` (`POST /certificates/{id}/source/ssh-keypair`).

### Changed
- Certificate / distribution-agent action buttons are now **icon-only with hover tooltips** (matching the
  rest of the app), with tighter, centered columns, fixing the over-wide left gap, right overflow, and
  left-aligned icons.

## [0.4.141] - 2026-06-14

### Fixed / Changed
- The "update available" reload banner had its icon and text misaligned vertically; the icon is now
  centered in a 16×16 box, with `line-height:1` on the container and text.
- The certificate table's "Expiry" column is split into two independent columns: **"Expiry date"** and
  **"Days left"** (each sortable and pickable).
- Certificate / distribution-agent action-column icons are now centered (column `align:center` +
  NSpace `justify:center`).

## [0.4.140] - 2026-06-14

### Changed: Certificate auto-fetch source UX
- SFTP source config clarity: **"Login password" / "Login private key (SSH key, PEM)"** are now a
  distinct "SFTP login auth" section placed right under the username, with a hint: "Used to log in to
  the SFTP host. Provide a password OR an SSH private key (key takes precedence). The certificate's own
  private key is the remote key_path file below, unrelated to this." Remote file paths
  (cert_path/key_path/chain_path) are grouped separately. (The backend already supported SSH-key login;
  only the field placement/naming was easy to mistake for the certificate's private key.)
- The "Off" source type now reads **"Off (manual upload)"** so it's clear upload / paste / self-signed
  are still available.

### Changed: Certificate / distribution-agent tables match the rest of the app
- Both tables now have **sortable columns** (autoSort) and a **column picker** (preferences persisted to
  the backend and synced across devices).
- Action-column buttons now show **icon + text** and collapse to **icon-only** when the column is too
  narrow (col-actions container query; the label still shows on hover).

## [0.4.139] - 2026-06-14

### Added: Distribution-agent version display & self-update
- The admin "Distribution agents" tab now shows the agent **version** (flagged "update available"
  with a hint when it lags the server) and **source IP**, mirroring the scan agent.
- The distribution agent now **self-updates**: `/check` returns the sha256 of the server's agent.py;
  if the running copy differs the agent downloads the new version, atomically replaces itself and
  re-execs (the download is sha-verified before replacing; a failure is logged and never aborts
  deployment). Set `auto_update: false` in the config to disable.
- The read-only "Certificate distribution status" page (`GET /cert-agents/status`) now also returns
  `last_source_ip` / `server_agent_version`.

## [0.4.138] - 2026-06-13

### Added: Certificate auto-fetch source
- A certificate can now have an **auto-fetch source** (in addition to upload / paste / self-signed):
  the system periodically (and on demand via "Fetch now") pulls the renewed bundle from the source,
  and **only stores a new version if the content actually changed**; if the fingerprint matches the
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

## [0.4.137] - 2026-06-13

### Fixed
- **Certificate pages returned 405 / "server error" (regression in the cert API client)**: the
  `certificates.ts` API calls (and the subnet-overlap check in `integrations.ts`) were missing the
  `/api/v1` prefix that the shared axios client requires (its baseURL is `/`), so requests hit the
  SPA paths (`/certificates`, `/cert-agents`) and nginx returned 405 for POST / index.html for GET.
  All cert API paths are now correctly prefixed. The certificate admin page, agents, self-signed,
  and the Advanced status view work.
- Added the missing icon on the certificate/agent "Save" buttons.

## [0.4.136] - 2026-06-13

### Certificate distribution: UX
- The certificate version upload now supports **pasting PEM text** (certificate / key / chain) as
  an alternative to uploading files, via a toggle in the upload dialog.
- Renamed the Advanced-menu read-only certificate view label to match the admin one.

## [0.4.135] - 2026-06-13

### Certificate distribution: follow-ups
- **Cross-distro agent installer**: the cert-agent installer now auto-detects the package
  manager (apt / dnf / yum / zypper), so it works on Debian 11/12/13, Ubuntu 22.04/24.04/26.04,
  RHEL / Rocky / AlmaLinux / CentOS, Fedora and openSUSE/SLES (all systemd). PyYAML is installed
  via the right package name per distro.
- **More profiles**: added `pbs` (Proxmox Backup Server: `proxy.pem`/`proxy.key`, reloads
  `proxmox-backup-proxy`). The `apache` profile now reloads `apache2` or `httpd` (whichever exists),
  so it works on Debian/Ubuntu and RHEL/SUSE.
- **Install-instructions button** on the Distribution Agents tab (like Scan Agents): one-liner
  install command, config example, supported distros, and the `--dry-run` hint.
- **Read-only certificate status under Advanced**: a non-admin viewer with global read can now
  see each agent's deployment status (last update, valid-from, expiry, days remaining, up-to-date
  vs drift) via a new Advanced menu entry. New `GET /cert-agents/status` (gated `require_global_read`).

## [0.4.134] - 2026-06-13

### Fixed
- **PGDG repo setup failed on Debian 12 when the keyring file already existed (customer report)**:
  the installer ran `gpg --dearmor` onto `/usr/share/postgresql-common/pgdg/apt.postgresql.org.gpg`
  (a file owned by the `postgresql-common` package). When that file already existed, gpg prompted
  "File exists. Overwrite?" / failed non-interactively, so the key was never written, the PGDG repo
  signature was invalid, and `postgresql-16-pgvector` was "not installable". Now the key is written
  to its own `/etc/apt/keyrings/jt-ipam-pgdg.gpg` with `gpg --dearmor --yes` (no collision, idempotent).
  Verified end-to-end in a Debian 12 container.

### Added: Certificate distribution (commercial certs → push to all sites)
- Central store for commercial certificates with a pull-based distribution agent. You upload a
  renewed bundle (crt/key/chain) once; agents on each host pick up the new version, write it to
  the right paths, run a config-test, reload the service, and roll back on failure.
- **Backend**: migration `0075` (`certificates` / `cert_versions` / `cert_agents`); the private
  key is stored AES-GCM encrypted and is never returned by any management API. `/certificates`
  admin CRUD + `POST /{id}/versions` (validates key↔cert match, SAN/expiry, rejects mismatched/
  expired/duplicate) + **`POST /{id}/self-signed`** (generate a self-signed cert with a custom
  CN/SAN/validity, handy while waiting for the commercial cert). `/cert-agents` admin CRUD +
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

## [0.4.133] - 2026-06-13

### Fixed
- **Install on minimal Debian 12 / 13 containers (customer report)**. Two gaps surfaced on
  clean container images:
  - The PGDG-repo step runs `curl | gpg` and the later PostgreSQL setup uses `sudo -u postgres`,
    but `ca-certificates` / `curl` / `gnupg` / `sudo` were not guaranteed present (minimal Debian
    container images often omit them). The PGDG step (which Debian 12 always takes, since its
    default repo ships PG 15, not 16) failed at `curl`, and the PostgreSQL config step failed with
    `sudo: command not found`. These four are now installed up-front.
  - Combined with the v0.4.131 `apt-cache madison` version detection, the matrix is now: Debian 12
    → PGDG PostgreSQL 16; Debian 13 → native PostgreSQL 17 (+ `postgresql-17-pgvector`, no PGDG);
    Ubuntu 24.04 → native 16; Ubuntu 26.04 → native 17/18. The app supports PG 16/17/18.

## [0.4.132] - 2026-06-12

### Fixed
- **CSV import 500 on real import (customer report / issue #4)**: the import endpoint passed
  `subnet.cidr` (an asyncpg `IPv4Network` object, not a str) as the background task's VARCHAR
  `target_label` → asyncpg `DataError`. Dry-run was unaffected (no task spawned), which is why
  preview worked but the actual import 500'd. Now coerced with `str()`.
- **IP request list 500 when a request has a manually-specified IP (issue #4)**: asyncpg returns
  `IPv4Address` from the `INET` column, but `IPRequestRead.requested_ip` is typed `str`, so Pydantic
  validation failed and the whole list page 500'd. Added a `mode="before"` coercion (the same
  pattern already used for `IPAddressRead.ip` / `SubnetRead.cidr`).
- **Scan agent could not return hostnames (customer report)**: reports carrying rdns/NetBIOS/mDNS/OS
  hostnames 500'd for newly-discovered IPs. With `autoflush=False` and a DB-generated UUID, a freshly
  added `IPAddress` had `id=None` when `apply_observation` built the hostname-observation FK row →
  `NOT NULL` violation. Now flushes right after creating the IP so its id is populated. (icmp+arp-only
  reports were unaffected because they never call `apply_observation`.)
- **Hardened the same asyncpg INET/CIDR-as-str class of bug** across other read schemas that build via
  `model_validate(ORM)` and were missing coercion: `APITokenRead.last_used_ip`, `VMInterfaceRead`
  (`primary_ip`/`mac`), and `ARPEntryRead`/`FDBEntryRead` (`ip`/`mac`).

## [0.4.131] - 2026-06-12

### Fixed
- **Install on Ubuntu 26.04 (customer report)**: the installer hardcoded `postgresql-16`,
  which isn't in Ubuntu 26.04's default repos (it ships PG 17/18). The old fallback added the
  PGDG repo for the new release codename, which PGDG often doesn't carry until months after
  release → `apt-get update` 404'd and the install aborted. The installer now detects the
  PostgreSQL version already available in the enabled repos (prefers 16, otherwise the distro's
  native 17/18/…) and installs that plus the matching `postgresql-N-pgvector`; PGDG is only
  used as a last resort when no `postgresql-N` (>=16) exists at all. The app is compatible with
  PG 16/17/18. Python detection also now includes `python3.14` (Ubuntu 26.04's default).

### Fixed
- **ARP table retention**: `arp_entries` was insert/update only and never pruned, so it
  grew unbounded over time (MAC↔IP churn and orphaned rows from deleted devices each left a
  row). The sync timer now deletes ARP entries older than `ARP_RETENTION_DAYS` (default 30;
  set 0 to disable) once per run, including orphan rows.

### Added
- **Overlapping-subnet warning on integration settings**: when overlapping subnets exist
  (the same IP can appear in more than one subnet) and an integration (LibreNMS / OPNsense /
  Wazuh / Proxmox / AdGuard / DNS) has no subnet scope set, the settings form now shows a
  warning that a sync may stamp liveness / DHCP / MAC onto the wrong tenant's copy of an IP,
  pointing the admin to set the subnet scope. New `GET /subnets/overlaps/exists` (admin).

### Notes
- No new duplicate-IP / duplicate-ARP risk: `ip_addresses` is unique on `(subnet_id, ip)`;
  `arp_entries` is upserted on `(ip, mac, device_id)`; only LibreNMS writes ARP (scanner and
  OPNsense only stamp existing IPs). Same-IP-string across overlapping subnets remains by design.

## [0.4.129] - 2026-06-11

### Security
- **RBAC IDOR fixes**. Several detail/aggregate endpoints accepted an object id without an
  object-level visibility check, letting any signed-in account read objects outside its scope:
  `GET /devices/{id}` and its sub-resources (`/integrations` exposed Wazuh CVE counts + Proxmox
  VMs, plus `/librenms`, `/vlans`, `/relations`), `GET /customers/{id}` and `/{id}/summary`
  (full per-customer asset dump), and `GET /racks/{id}/diagram`. All now require object `read`
  permission (404 on no access). The MCP `get_topology` tool no longer leaks the full topology
  to scoped accounts (was missing the `user` filter) and is gated as global-read; the REST
  `GET /topology` is gated with `require_global_read` to match.
- **OIDC ID Token verification**: the callback previously base64-decoded the ID Token and
  trusted its claims (including `groups`, which drives admin promotion) without verifying the
  signature. It now verifies the ID Token against the provider's JWKS (signature + `aud`/`iss`/
  `nonce`) before trusting any claim; on failure it falls back to userinfo only instead of
  trusting unverified groups.
- **CSV export formula injection**: IP address CSV export now escapes cells beginning with
  `= + - @` / tab / CR so spreadsheets don't execute them as formulas.

### Fixed
- **Integration sync resilience**: `jt-ipam-sync.py` now rolls back the session before writing
  `last_error` in every integration's exception handler; a single failing instance (e.g. an
  AdGuard `MultipleResultsFound` on overlapping subnets) no longer aborts the whole sync run.
- **Overlapping subnets**: AdGuard sync (`sync_clients` / `sync_rewrites`) and the MCP ARP
  lookup matched `IPAddress.ip` with `scalar_one_or_none()`; with overlapping subnets the same
  IP yields multiple rows → `MultipleResultsFound`. Changed to `limit(1)` + `first()`.
- **Non-UCS DNS server connection tests**: BIND 9 (dnspython `OSError`/connection-refused),
  Windows DNS (WinRM/`requests` exceptions), PowerDNS and OPNsense Unbound (non-JSON responses
  on auth failure) leaked raw exceptions that the `/dns/servers/{id}/test` endpoint didn't catch,
  producing a 500 with no message. Adapters now wrap these as `DNSAdapterError`, and the test
  endpoint has a safety net that turns any unexpected error into a readable 502.

### UI / Docs
- Fixed a missing i18n key on the section detail page ("display order" showed the raw key).
- Added error feedback to the notifications "mark all read" and group-members actions.
- Terminology: use 「外掛」 (not 「插件」) for "plugin" in zh-TW docs.

## [0.4.128] - 2026-06-10

### Fixed / Improved
- **External reverse proxy + OIDC / Microsoft 365 (Entra ID) login**: the frontend now parses
  the token the backend returns in the URL fragment after the OIDC/SAML callback (previously
  ignored → stuck on the login page); the backend merges **ID Token** claims into userinfo, since
  Entra ID returns `groups` only in the ID Token (not the Graph userinfo endpoint), so admin-
  group mapping now matches. Added `deploy/nginx/jt-ipam-external-proxy.{conf,snippet}`
  templates (HTTP-only, no HSTS, `X-Forwarded-Proto` passthrough) and a README "Mode C:
  external reverse proxy" section (set `APP_PUBLIC_URL`/CORS to your domain, forward the proto).
- **Install (Ubuntu 24.04)**: `ensure_node` no longer pipes the NodeSource output to `/dev/null`
  and now **verifies Node ≥ 18** after install, otherwise it stops with a clear remedy; this fixes a
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

## [0.4.114] - 2026-06-09

### Added / Improved
- **DNS records page**: filter by server / type (type dropdown shows per-type counts), a source
  column showing the originating DNS server, IP matching resolved against the **actual IP value**
  in `ip_addresses` (fixes "the IP is in IPAM but shows no match"), and a column picker. DNS sync
  now keeps only **A / AAAA / PTR** (IP↔name mapping); CNAME/MX/TXT etc. are no longer stored.
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

## [0.4.113] - 2026-06-09

### Added: IP request approval gate + notifications
- **Configurable approval policy** (Admin → IP Request Approval) with four modes so
  each site can pick: `admin only`; `administrators + designated users/groups`
  (single gate, any one approves); **parallel sign-off** (multiple gates, any order,
  all must approve); and **sequential multi-stage** (ordered gates, each with its own
  approvers; a request must pass gate 1→2→3…). Plus a separation-of-duties self-approval
  toggle. Per-step approvals are tracked in a new `ip_request_stage_approvals` table
  (migration 0073). Approve/reject authorize via the policy, not a blanket admin check.
- The request detail page shows **gate progress** (which gates passed / which is
  awaiting); each sequential gate's approvers are notified only when it's their turn.
- **Inline approve / reject** on the IP Requests list for approvers (pending rows),
  in addition to the request detail page.
- Request detail: fully localized; shows the subnet CIDR (linked) and, for pending
  requests, the **IP that will be allocated** (including the auto-picked first-free
  IP), which the **approver can change** before approving.
- **Approver notifications**: when a request is submitted, every approver gets an
  in-app bell notification and (if the Email channel is enabled) an email.
- **Notification channels settings** (Admin → Notification Channels): an SMTP/email
  channel (host/port/TLS/credentials/from, encrypted password, test-send button).
  Telegram / Slack / Teams / Nextcloud / Zulip are shown as "in development".

### Added: DHCP
- Subnet detail shows a **DHCP ranges** row when OPNsense DHCP pool ranges exist for
  that subnet (hidden when none), and a **DHCP-only** filter on its IP list.

### Added: DNS records (Advanced → DNS Records)
- New page listing DNS records pulled from integrated DNS servers, with search, an
  **IP lookup** (find records matching an IP: forward A/AAAA or the IP's PTR), and a
  **"no matching IP"** filter (A/AAAA records whose target isn't in IPAM).

## [0.4.112] - 2026-06-09

### Fixed
- **Manually-edited MAC was not protected from sync overwrite.** Unlike hostname
  (which records a `manual` observation), editing an IP's MAC in the UI only set
  `ip.mac` without marking `mac_source="manual"`, so the next scan/ARP sync could
  clobber it. The IP-edit endpoint now stamps `mac_source="manual"` on manual MAC
  edits (highest ARP precedence) and clears the source when the MAC is cleared.
  (Hostname's manual-vs-precedence path was verified correct end-to-end; if a
  manually-set hostname seems to vanish, hard-refresh: it is usually a stale SPA
  bundle, not the backend.)
- IP Requests toolbar: the status filter select was `small` while the buttons next
  to it were default size, so it sat shorter; it is now aligned to the same height.

## [0.4.111] - 2026-06-08

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

## [0.4.110] - 2026-06-08

### Fixed (create-admin CLI)
- `create-admin` crashed with `MultipleResultsFound` when the given username matched
  one account and the email matched a different one (or an email was shared by more
  than one account). The lookup now queries username and email separately and
  reports a clear conflict instead of crashing.
- `--force-update` now also writes the supplied username/email onto the matched
  account; previously it reset only the password, so a new `--email` was silently
  ignored and the old address kept showing.

## [0.4.109] - 2026-06-08

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

## [0.4.108] - 2026-06-07

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

## [0.4.107] - 2026-06-07

### Added
- **Subnet scope for Wazuh / Proxmox VE / AdGuard / DNS integrations** (migration
  0072), mirroring LibreNMS: each integration can be limited to specific subnets so
  syncs only match IPs within those subnets; overlapping subnets from unrelated
  systems no longer mis-attach hostname / OS / etc. Empty scope = global matching.

### Changed
- Subnet edit: scan-probe checkboxes are disabled when the selected scan agent
  can't run that probe (consistent with the scan-agent page).
- NAT table: clicking a rule row opens its detail (ignores the IP / device links).
- Subnet list: the tree expand arrow now sits on the CIDR column, not the pin column.

### Fixed
- switch_port tooltip shows the `device@port` form (not `device / port`).

## [0.4.106] - 2026-06-07

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

## [0.4.105] - 2026-06-07

### Fixed
- Subnet save returned "Invalid request": the edit form sends `master_subnet_id`
  but `SubnetUpdate` (a strict, extra-forbid schema) didn't declare it. Added the
  field so editing a subnet works again.
- Subnet list: clicking a row's tree expand arrow navigated into the subnet instead
  of expanding its children; the row-click now ignores the expand trigger.

### Changed
- **Unified subnet edit**: the subnet list and the subnet-detail page now share one
  `SubnetEditModal` component, so both edit the same fields (section / VLAN / VRF /
  parent subnet / per-probe scan options / scan agent …); they previously diverged.
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

## [0.4.104] - 2026-06-07

### Fixed
- Graylog DSV lookup endpoint now emits each IP only once. The same IP can exist
  in multiple (overlapping) subnets, which produced duplicate rows and made
  Graylog's "DSV File from HTTP" data adapter fail with "Multiple entries with
  same key". Keys are now de-duplicated (first by IP order).

## [0.4.103] - 2026-06-07

### Changed
- Top bar is now responsive: on narrow screens the language / theme / account
  controls collapse to icon-only (icon-triggered dropdowns) and the search box
  shrinks, instead of wrapping onto multiple rows.
- New subnets inherit the **customer (unit)** of their containing parent subnet
  when none is specified, so a child subnet lands under the same sidebar group;
  the sidebar subnet tree refreshes immediately after create / edit / delete.
- IP edit dialog: the OS probe now shows the same "intrusive" tag + tooltip as the
  subnet / scan-agent settings.

## [0.4.102] - 2026-06-07

### Changed
- Anomaly detection (MAC roaming): the "seen at" location now resolves the switch
  to its friendly name (LibreNMS sysname / hostname) instead of a raw device UUID,
  and the device / port / last-seen fields are rendered as an aligned grid.

## [0.4.101] - 2026-06-06

### Changed
- Dashboard **Section heat** card redesigned: the bar now reflects *average subnet
  utilization* (no longer diluted to ~0% by a single large sparse subnet), plus a
  per-section distribution of subnets by utilization band (full / high / medium /
  low) and a subnet/used summary; the card is far more informative.

### Fixed
- Racks: the leftmost rack's frame left border was clipped by the horizontal-scroll
  container; added small side padding so it renders fully.
- OS source precedence section title wording.

## [0.4.100] - 2026-06-06

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

## [0.4.99] - 2026-06-06

### Changed
- MCP tools surface the new scan/OS fields: `get_ip_detail` returns OS guess /
  family / source and excluded probes; `list_scan_agents` returns enabled /
  available probes and last source IP.

## [0.4.98] - 2026-06-06

### Changed
- OS detection result now shown as an **"Operating system"** field in the IP detail
  table (top), and as an optional **OS** column in the subnet IP list.

## [0.4.97] - 2026-06-06

### Added
- Scan agents: a **"Scan now"** action that triggers the agent to run all enabled
  probes immediately on its next poll (migration 0070); OS detection (`nmap -O`)
  is now exercised end-to-end on agent hosts that have nmap (runs as root).
- Probe-interval inputs show a unit (seconds) + human-readable equivalent.

## [0.4.96] - 2026-06-06

### Changed
- Scan agent probe-interval inputs now show a unit suffix (seconds) and a
  human-readable equivalent (e.g. 86400 -> "1 day").

## [0.4.95] - 2026-06-06

### Changed
- Scan probes: removed the port-probing options (TCP port liveness, port/service
  scan) and SNMP; jt-ipam does not expose credential-based or port-scan probes.
  Remaining: ICMP / ARP / reverse DNS / NetBIOS / mDNS / OS detection.
- Scan Agents: show an **"update available"** tag when an agent's reported version
  is behind the server's bundled agent (it self-updates from the jt-ipam server,
  not GitHub; the tag surfaces agents that failed to self-update).
- Topology: VPN-paired firewalls are kept near each other instead of being pushed
  to opposite far ends when subnet centers are spread apart.

## [0.4.94] - 2026-06-06

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

## [0.4.92] - 2026-06-06

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
- Contact-groups table reused tenant-group columns (hit the wrong API); this is now fixed.
- ruff: corrected noqa rule code in `audit.py`, import ordering in `sso.py`.

### Security / Chore
- **vitest** dev dependency bumped 3.2.6 → 4.1.8 (resolves the critical
  "Vitest UI server arbitrary file read/exec" advisory; dev-only, not in the
  production bundle).
- Bilingual docs added: `CHANGELOG_zh-TW.md`, `SECURITY_zh-TW.md`, and
  `TEST_CHECKLIST.md` (English) alongside `TEST_CHECKLIST_zh-TW.md`.
- All `scripts/*.sh` are now English-only (comments and messages); behavior
  unchanged.

## [0.4.79] - 2026-06-06

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

## [0.4.61] - 2026-06-05

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

## [0.4.43] - 2026-06-04

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

## [0.4.32] - 2026-06-02

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
- Subnet-pin persistence survived a refresh inconsistently: pins now persist
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

## [0.4.31] - 2026-06-01

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

## [0.4.30] - 2026-06-01

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

## [0.4.x] - 2026-05/06

### Added
- **Object-level RBAC** across 7 object types (customer / section / subnet / IP /
 device / rack / location) with hierarchical cascade, per-type "All" wildcard,
 and 5 built-in roles (System Administrator, Read-only Viewer, Network
 Operator, Auditor, Department Administrator). Visibility is enforced on list
 endpoints, global search, the topology graph, and every selector.
- **Permission management UI**: principal (user/group) picker, grant table, and
 add-grant flow with "All"/specific multi-select and read/write/admin levels.
- **MCP server**: expanded toolset with both stdio and Streamable HTTP
 transports; mounted under `/api/mcp` so it is reachable through the nginx
 reverse proxy. Write tools self-gate on admin.
- **Customers** (managing units) attached to sections/subnets/devices/IPs, and an
 IEEE **OUI vendor** table with a monthly refresh timer.
- **AI chat** improvements: persistent history, per-message timestamps, model &
 elapsed-time display, and a model-parameters tooltip (family / parameter size /
 quantization / context length via Ollama `/api/show`).
- **Global search** now covers VPN, customers, racks, locations, NAT, DNS
 records, firewalls, and IP requests, all RBAC-filtered.
- Floating sticky horizontal scrollbar on wide tables; premium light/dark theme;
 Cabling / Power / VPN split into three independent pages.

### Changed
- prod database migrated from `SQL_ASCII` to `UTF8`.
- Terminology fixes for Taiwanese usage (e.g. 首碼 instead of 前綴).

### Fixed
- Numerous QA-driven UI fixes (column widths, dashboard widget styling, text
 selection contrast in light mode, topology node detail popovers, tooltip
 clipping).

## [0.3] - Phase 1–3 baseline

- phpIPAM parity (Sections/Subnets/IPs/VLANs/VRFs/NAT/Devices/Racks/Locations/
 IP-Requests), TOTP + API tokens, forced TLS.
- Multi-vendor DNS, deep LibreNMS integration, anomaly detection, SHA-256 audit
 chain, pgvector semantic search.
- Tenancy/Cabling/Power/VPN/Virtualization, Proxmox sync, Cytoscape topology,
 OIDC/SAML SSO, OPNsense firewall sync, Wazuh agent inventory.
