# jt-ipam v1.0.22

[![License](https://img.shields.io/github/license/jasoncheng7115/jt-ipam?color=blue)](LICENSE)
[![Last commit](https://img.shields.io/github/last-commit/jasoncheng7115/jt-ipam)](https://github.com/jasoncheng7115/jt-ipam/commits/main)
[![Stars](https://img.shields.io/github/stars/jasoncheng7115/jt-ipam?style=flat)](https://github.com/jasoncheng7115/jt-ipam/stargazers)
![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-async-009688?logo=fastapi&logoColor=white)
![Vue](https://img.shields.io/badge/Vue-3-42b883?logo=vuedotjs&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)
![OWASP](https://img.shields.io/badge/OWASP-Top%2010%3A2025-000000)

**🌐 [Project site / 專案介紹網站 →](https://jasoncheng7115.github.io/jt-ipam/?lang=en)**

> A self-hosted, integration-focused IPAM, independently developed with an operation flow familiar to phpIPAM users, deeply integrated with multiple DNS servers, LibreNMS, OPNsense, pfSense, FortiGate, Palo Alto, Check Point, MikroTik RouterOS, Windows DHCP Server, standalone Kea and ISC DHCP, Technitium DNS/DHCP, Proxmox VE, VMware ESXi / vCenter, Hyper-V / SCVMM, CubeCOS / OpenStack, Wazuh, Zabbix, and a local LLM.
>
> By Jason Tools Co., Ltd. · License: AGPL-3.0 · 繁體中文: [README_zh-TW.md](README_zh-TW.md) · 日本語: [README_ja.md](README_ja.md)

---

## Why jt-ipam?

Familiar to phpIPAM users so they are productive from day one, but built from scratch on a modern stack (not based on phpIPAM's codebase). Deep integrations:

- **DNS**: PowerDNS, BIND 9, OPNsense Unbound, Univention UCS, Microsoft Windows DNS, Technitium DNS Server (read-only: reads forward/reverse records and their consistency, never writes to the DNS server; Windows DNS over WinRM HTTP 5985 with forced NTLM encryption by default, or HTTPS 5986)
- **LibreNMS**: device sync, ARP / FDB harvesting, online-status reconciliation, auto-onboarding to monitoring
- **SNMP collection (scan agents)**: for networks without LibreNMS, the scan agent responsible for a subnet reads the network devices you assign to it over read-only SNMP v2c/v3 (credentials managed centrally, never returned by the API): router ARP tables fill MACs the agent cannot see across a router, switch MAC tables (Q-BRIDGE / BRIDGE-MIB, Cisco per VLAN) locate hosts on switch ports, LLDP / CDP neighbors draw switch links in the topology, and interfaces become device ports. The agent installer adds the net-snmp client tools (`snmp` / `net-snmp-utils` / `net-snmp-tools`)
- **Zabbix**: read-only complement on the monitoring side, providing host-to-IP mapping, availability as an extra evidence source for effective status, maintenance windows, and a **monitoring coverage gap** (addresses IPAM has a hostname for that Zabbix is not watching). ARP/FDB stay with LibreNMS; they are not part of Zabbix's built-in data
- **Infrastructure**: Proxmox VE, **VMware ESXi / vCenter (Beta)**, one setup covering both a standalone ESXi host and vCenter, read-only over the vSphere API for virtual machines, NICs and addresses, landing in the same virtualisation tables as Proxmox; **Microsoft Hyper-V (Beta)**, read-only over WinRM (PowerShell, NTLM or Kerberos), either directly from the hosts (every node of a failover cluster, since NTLM cannot hop between nodes) or through System Center VMM, for virtual machines, NICs, VLANs, addresses and guest OS (KVP), with the host NICs and virtual switches added as ports of the host's device; **Bigstack CubeCOS / OpenStack**, read-only over Keystone v3 (password or application credential), Nova and Neutron (only GET requests; the Keystone token request is the only POST) for VMs, Neutron ports, subnets and floating IPs, landing in the same virtualisation tables; VM addresses link to IPAM only inside Neutron subnets mapped to a jt-ipam subnet (mapped automatically only when the CIDR is unique in the cloud and exactly one jt-ipam subnet in scope has it, so overlapping project networks wait for a manual mapping), floating IPs are shown with their port but never taken as guest addresses, and the CubeCOS extension adds the node list; Wazuh, OPNsense / pfSense (alias / rule / NAT sync), **FortiGate**, read-only over the FortiOS REST API (DHCP leases and ranges, ARP, IPsec tunnels and SSL-VPN sessions, policies, NAT, address objects; multi-VDOM), **Palo Alto (Beta)**, read-only over the PAN-OS API (ARP, DHCP leases, security policies with App-ID, NAT, address objects; multi-vsys), **Check Point (Beta)**, read-only over the R81.20 Management API of the management server (gateway list, network objects, access rules with inline layers and hit counts, destination NAT; Multi-Domain aware, one login per domain, read-only session; per gateway, the Gaia API adds the DHCP server settings read-only, plus the ARP table and DHCP leases through fixed read commands, skipped automatically when the account may not run commands), and **MikroTik RouterOS (Beta)**, read-only over the RouterOS v7 REST API (firewall filter/mangle/NAT rules, address lists, DHCP leases and ranges, VPN, ARP). A MikroTik is usually the site's main router, so sections run sequentially with pauses between them, the round stops once CPU load passes a threshold, and responses are size-capped (the RouterOS REST API has no pagination); the expensive sections are off by default and the connection diagnostic reports rows and seconds per endpoint
- **DHCP**: each server is configured on its own. OPNsense (Kea/dnsmasq/ISC) and pfSense sync leases, address ranges and reservations over their REST APIs; **Windows DHCP Server (Beta)** is read-only over WinRM + PowerShell (`Get-*` only, needs WinRM reachable, HTTP 5985 with NTLM encryption by default since the Windows Server firewall blocks 5986 by default); a **standalone Kea** server is read over its JSON control API (Control Agent, or Kea 3.0's own HTTP control socket; leases need the lease_cmds hook); **Technitium DHCP** is read over the Technitium HTTP API with a view-only token (scopes minus exclusions, reservations, leases, and the gateway/DNS each scope hands out, which IP change assessment checks); a **standalone ISC DHCP** server (isc-dhcp-server, which has no lease-listing API) is read by a scan agent installed on the DHCP host, which parses dhcpd.conf / dhcpd.leases locally and reports only the parsed pools, fixed addresses and active leases. Addresses inside a pool are flagged in the IP list and detail view; exclusions configured on the DHCP server (Windows DHCP, FortiGate, Technitium, Check Point) are taken out of the pool first and the excluded addresses are flagged "DHCP excluded"; **ISOinsight (Beta)** reads DHCP leases read-only by logging in to its HTTP(S) interface.
- **Wazuh**: agent inventory (status, OS, CVE counts, SCA) matched to IPs, plus the IPs with a hostname that have no active agent
- **OCS Inventory NG**: computers matched to existing IPs by NIC MAC (never creates records), hardware on the device page, plus the IPs OCS has never inventoried
- **RustDesk Server (open source)**: a dedicated RustDesk agent on the RustDesk host (one-line install) reads the registered device IDs and their online state (read-only; the private key is never opened; when both jt-ipam and the host admin allow it, it can also delete old registrations); IDs are mapped to IPs only when the registered IP is unambiguous and recently online, the IP page connects right in the browser (RustDesk-compatible web connection, encrypted end to end in the browser) or launches the local RustDesk client with the server and key filled in (no password in the link), and the clients' own reports give connection audit, host name / OS / user and brute-force alarms without reconfiguring any client
- **Graylog**: exposes an IP→hostname/FQDN DSV lookup endpoint for Graylog's "DSV File from HTTP" data adapter
- **Local AI**: natural-language queries and semantic search over LLM Server (self-hosted by default, so data never leaves the host; an OpenAI-compatible endpoint can be selected explicitly instead), plus an MCP server (stdio and Streamable HTTP transports) so external LLM clients can drive the IPAM; `gemma4:26b` works well in our testing Security-side AI: a **firewall rule-change sentinel** (per-sync snapshot diffs of every firewall integration: OPNsense, pfSense, FortiGate, Palo Alto, Check Point, MikroTik; a permit rule appearing overnight notifies admins), **IP forensics in chat** (ask "who was this IP last week" and get the field-level change log, ARP/MAC bindings and per-source hostnames as an evidence timeline), and an **AI triage card for unauthorised IPs** (OUI vendor, hostnames and switch port assembled into "what this likely is and where to look next", with evidence fencing against prompt injection).

**IP change assessment**: before renumbering, retiring, switch maintenance or node downtime, list every place across the integrations that references the address or device (DNS, DHCP, firewall rules and NAT, monitoring, virtualization, integration endpoints, certificates…) and the services affected, sorted into blockers, review, change required and data gaps; with a by-impact relation graph, review workflow, tasks and rollback steps, and reports as PDF, Word or OpenDocument (tables as Excel). Read-only; it never changes a device.

**Scan agents**: lightweight agents on each network segment report online status, MACs, names (reverse DNS, NetBIOS, mDNS) and OS; the server hands out the work, agents update themselves, and a load panel with overload alerts shows how each one keeps up. Large subnets can be registered with their real mask and still be scanned (a /8, for example): registered IPs and addresses recently seen in ARP are scanned every cycle and the rest are explored in the background for new devices, so there is no need to split the network just for scanning. Each IP's hostname shows which agent or integration reported it and when it was last seen.

Also built in: a **browser-based remote console** offering an SSH terminal, an **SFTP file browser** (upload/download without a separate client), plus RDP and VNC desktops, a **BMC out-of-band serial console** (IPMI SOL) and a RustDesk-compatible **web connection** (encrypted in the browser, with file transfer, no RustDesk client needed) (BMC is **Beta**) in the browser, with the connected time in the status bar, where credentials are not stored by default, with an optional per-user **encrypted credential vault**, **jump hosts** (for sites the backend cannot reach directly, a console can go backend → jump host → target; the exit is set per subnet and can be overridden per address, and the jump host's key must be pinned before any connection is allowed), object-level RBAC, single-use ticket→WebSocket sessions and full audit (every session records its length; RDP and VNC run on **guacd**, a required component prebuilt by jt-ipam for each supported OS version and installed automatically; the old pure-Python engine aardwolf is an optional fallback; SSH can use guacd too), an **approval workflow** for IP requests and IP change assessments (each with its own configurable multi-stage / parallel sign-off, in-app + email notifications), **DNS record review** (find records with no matching IPAM address), a **scan agent** (ICMP/ARP/rDNS/NetBIOS/mDNS/OS probes; one is installed on the host automatically; scanning always runs through an agent), an admin-only **IP probe** that works out what a host is from its services, OS fingerprint, banners and certificates (matched against the optional [Recog](https://github.com/rapid7/recog) fingerprint database, downloaded at install and checked weekly), with every conclusion shown with its evidence, **central certificate storage & distribution** (upload a commercial / self-signed cert once; a pure-bash agent pulls it on a schedule and deploys it to nginx / apache / caddy / haproxy / Proxmox VE·PMG·PBS / Zimbra and more, reloading the service; plus a **PowerShell agent for Windows / IIS** that imports into the Windows certificate store, repoints the HTTPS binding and verifies the switch over a real TLS connection, rolling back if it fails; **push to firewalls** (Beta) with no agent, through the FortiGate, OPNsense, Palo Alto and pfSense management APIs, verified and rolled back the same way; all with encrypted private keys, expiry alerts and manual renew), **floor plans + rack elevations** (standard racks, industrial enclosures and shelving including chrome wire, slotted angle steel, wooden (IKEA IVAR-style) and IKEA KALLAX shelving, plus LackRack (an IKEA LACK side table as a 19" rack), with non-standard widths, a height you can set per level, an open top you can put things on, and devices stacked within one level; up to six devices side by side in one row, front/rear, SVG/PNG/draw.io export), **cable tracing** (multi-hop), an IP change log with stale-IP reclaim, and a universal table column-picker + multi-format export.

## Graylog log enrichment (DSV lookup)

jt-ipam generates a **live** IP → hostname / FQDN lookup table that Graylog's "DSV File from HTTP" data adapter can poll, so log events that only carry an IP get a human-readable name automatically.

- Enable under **Admin → External integrations → Graylog**: pick a path slug, output format (CSV / TSV), and generate an access token
- Endpoint `GET /api/v1/lookup/<path>?token=<token>` is generated on each request straight from the database
- **Fields provided**: two columns per row, with column 1 = IP (key) and column 2 = hostname or FQDN (value); only IPs that have a hostname are emitted
- **Data format**: UTF-8 plain text. CSV is comma-separated with **every field wrapped in double quotes** (RFC 4180 escaping); TSV is tab-separated (unquoted). For example:

  ```csv
  "10.1.1.141","log1.example.com"
  "10.1.1.145","mg-host"
  ```

- In Graylog's "DSV File from HTTP" adapter: set the URL above, separator to comma or tab per format, and **Key column = 0, Value column = 1** (Graylog's column indices are 0-based)
- The token is validated per request and can be regenerated anytime; the settings page shows a ready-to-copy full lookup URL
- The token expires (30 to 365 days, notified before expiry) and is shown only when an administrator reveals it, which is audited
- Use the HTTPS URL: the token is in the URL, so plain-HTTP port 8088 answers 404 unless **Allow plain HTTP** is turned on (sites that used it before upgrading keep it on); **Allowed sources** can restrict which addresses may poll

## BMC out-of-band console (IPMI SOL, Beta)

Open a keyboard + text console to a server's **BMC** (IPMI 2.0 Serial-over-LAN) straight from its IP, with no vendor Java/HTML5 KVM. Enable it per IP (same RBAC level as SSH), keep the BMC credentials in the same encrypted vault, and every session is audited. It is **non-destructive**: keyboard + text screen only, no power control or mouse.

SOL only relays the host's **serial port**, so the host needs a serial console configured or the screen stays blank. One-time host setup:

1. **Find the port SOL maps to**: `dmesg | grep -iE 'ttyS|SPCR'` (e.g. `SPCR: console: uart,io,0x3f8,115200` → `0x3f8` = ttyS0, `0x2f8` = ttyS1). The wrong port stays blank.
2. **Add the kernel console** (keep `tty0` so the physical monitor keeps its output):
   - Generic Linux (GRUB): add `console=tty0 console=ttyS0,115200n8` to `GRUB_CMDLINE_LINUX` in `/etc/default/grub`, then `update-grub`.
   - Proxmox VE (systemd-boot / ZFS): append the same to `/etc/kernel/cmdline`, then `proxmox-boot-tool refresh`.
3. **Enable serial login** (immediate, no reboot): `systemctl enable --now serial-getty@ttyS0`.
4. **(Optional) BIOS Console Redirection**: point it at the same COM port (115200 8N1) to also see POST / BIOS over SOL. Per-field values (Terminal Type, Flow Control, **Redirection After BIOS POST**, …) are in the [BMC / SOL setup guide](https://jasoncheng7115.github.io/jt-ipam/bmc-sol.html?lang=en).
5. **Reboot** so `console=` takes effect; then SOL shows the whole boot and kernel panics. The physical monitor is unaffected.

Just want a login now? Step 3 alone is enough. The same guide is built into the app, from the BMC console's **Setup guide** button.

**Troubleshooting (gotchas seen in the field):**

- **Connected but blank / Enter does nothing**: SOL may not map to the port SPCR declares. With SOL connected, `echo test > /dev/ttyS0` (and `/dev/ttyS1`) and see which appears; or check `/proc/tty/driver/serial`, where the ttyS with a non-zero `rx` is the SOL port.
- **Login prompt shows but no boot messages**: the kernel console landed on the wrong ttyS (a non-SOL port) while `serial-getty` is on the right one. Put **only** the SOL port in `console=` (e.g. `console=tty0 console=ttyS1,115200n8`), not multiple `ttyS`; the kernel may pick the wrong one. Verify with `cat /proc/consoles`.
- **Output appears but is garbled**: the serial baud doesn't match SOL. Check `ipmitool -I open sol info 1 | grep 'Bit Rate'` and set `serial-getty` to the same baud.
- **Boxes / colors look broken (e.g. glances)**: set the serial login's `TERM` to `xterm-256color` (serial-getty often defaults to `vt220`).
- **Emoji (⚠️ etc.) in the OS boot messages**: those are systemd's own glyphs; add `systemd.setenv=SYSTEMD_EMOJI=0` to the kernel cmdline. For emoji on the **BIOS** screen, set the BIOS Console Redirection **Terminal Type = VT100+** (not VT-UTF8).
- **Console area is tiny with black margins**: serial can't auto-negotiate window size; use the console's **Fit to window** button (it sends an `stty rows/cols` command, so press it at a shell prompt), or run `stty rows N cols N` yourself.

## Which sources create IP records on their own?

When an integration sees an address IPAM does not have yet, the behaviour differs by
source, and it matters, because the "unauthorised IPs" anomaly check is defined as
**"seen in ARP, absent from IPAM"**. Hence this table:

| Source | When IPAM has no such address | Toggle | Default | Subnet placement |
|---|---|---|---|---|
| **Scan agent** | Can create it | "Record unregistered IPs automatically" | **Off** | only inside subnets assigned to that agent with scanning enabled |
| **LibreNMS** | May create (device primary IP only, never ARP neighbours) | "Auto-create discovered IPs" | **on by default** | puts it in the smallest subnet containing it; creates nothing if that is unclear |
| **Proxmox VE** | May create | "Trust addresses from virtualization" | **off by default** | puts it in the smallest subnet containing it; creates nothing if that is unclear |
| **VMware / ESXi** | May create | "Trust addresses from virtualization" | **off by default** | puts it in the smallest subnet containing it; creates nothing if that is unclear |
| **Hyper-V / SCVMM** | May create | "Trust addresses from virtualization" | **off by default** | puts it in the smallest subnet containing it; creates nothing if that is unclear |
| **CubeCOS / OpenStack** | May create (only inside a mapped Neutron subnet) | "Trust addresses from virtualization" | **off by default** | only the jt-ipam subnet that Neutron subnet is mapped to; creates nothing while the subnet is pending mapping |
| **OPNsense / pfSense** | May create (DHCP leases) | "Create addresses IPAM does not have" | **off by default** | puts it in the smallest subnet containing it; creates nothing if that is unclear |
| AdGuard / Wazuh / Zabbix / OCS / RustDesk / DNS / Windows DHCP / Kea / ISC DHCP / Technitium DHCP / ISOinsight / FortiGate / Palo Alto / Check Point / MikroTik | **Match only, never create** | None | None | None |
| CSV import / phpIPAM migration | Created from the imported data (an explicit user action) | None | None | as imported |

**Shared rule**: every auto-creation path uses the same decision
(`services/ip_autocreate.py`): **put the address in the smallest subnet that contains it;
if which one is unclear, create nothing**.

For example:
- `10.1.1.5` falls inside both `10.0.0.0/8` and `10.1.1.0/24` → it goes into the **smaller**
  `10.1.1.0/24`.
- Tenants A and B **each have their own `192.168.1.0/24`** → there is no way to tell whose
  machine this is, so **nothing is created**. Filing a record under the wrong tenant is worse
  than having no record.
- The address is inside no existing subnet → nothing is created (subnets are never invented).

The second case disappears once you set that integration's subnet scope to your own
subnets: only yours remain as candidates, the choice is clear, and records are created
normally.

> ⚠️ **Enabling auto-creation trades away part of your detection.** A machine that obtained
> an address is not necessarily one that belongs in IPAM: once an unauthorised device is
> recorded, it **no longer appears under "unauthorised IPs"**. Auto-created records are
> flagged "Auto-recorded (unregistered)" in the IP list (orange icon) and outlined in orange on the
> subnet grid, where the legend carries an "Auto-recorded" count; review them regularly.

## Core entities

`Section → Subnet → IPAddress`, plus `Device` / `Rack` / `Location`, `Customer` (managing unit), `VLAN` / `VRF`, `NAT`, OPNsense / pfSense / FortiGate / Palo Alto / Check Point / MikroTik firewalls, and an IEEE OUI vendor table (monthly refresh).

## Access control (RBAC)

Object-level permissions across **7 object types** (customer / section / subnet / IP / device / rack / location) with:

- **Hierarchical cascade**: granting an upper level (e.g. a customer or section) automatically covers everything beneath it (subnets → IPs; locations → racks → devices)
- **"All" wildcard** per object type
- **5 built-in roles**: System Administrator, Read-only Viewer, Network Operator, Auditor, Department Administrator
- Visibility is enforced everywhere: list endpoints, global search, the topology graph, and every dropdown only ever show objects the principal may see. Deny-by-default.

## Security (OWASP Top 10:2025)

Security is a day-one requirement; every module and PR is checked against **OWASP Top 10:2025**. See [`SECURITY.md`](SECURITY.md); **please report security issues privately as described there** (Security tab "Report a vulnerability" or service@jason.tools), not in a public issue. Published advisories: [Security advisories](https://github.com/jasoncheng7115/jt-ipam/security/advisories). For the controls that can serve as ISO/IEC 27001:2022 and ISO/IEC 42001:2023 evidence, and what the adopting organisation has to do itself, see [`docs/COMPLIANCE.md`](docs/COMPLIANCE.md).

- **TLS enforced**: pick one of nginx reverse-proxy termination (`BACKEND_TLS_MODE=nginx`) or uvicorn serving a self-signed cert directly (`BACKEND_TLS_MODE=direct`)
- A01: deny-by-default RBAC with object-level checks (above)
- A02: argon2id password hashing; application-layer encryption for stored secrets (DNS credentials / SNMP / API tokens), each bound to its purpose; daily backups can be encrypted with a passphrase
- A03: parameterized SQLAlchemy, strict Pydantic v2 validation, CSP + output escaping
- A05: HSTS, CSP, X-Frame-Options, Referrer-Policy
- A07: TOTP two-factor authentication that administrators can require (with recovery codes and replay protection), account lockout that also counts wrong codes, server-side sessions with the refresh token only in an HttpOnly+Secure+SameSite=Strict cookie (revocable per device, deactivation takes effect immediately, open consoles close within 30 seconds), API-token TTL
- A08: SHA-256 audit chain, verified every sync round and anchored outside the database
  (`/var/lib/jt-ipam/audit-anchors.jsonl` + journald), because the chain alone cannot detect
  the tail being cut off. `JT_IPAM_AUDIT_CHAIN_BASELINE_ID` sets the id verification starts
  from, for deployments carrying older records that can no longer be made verifiable. The table is
  append-only in the database and the application account cannot disable that (`scripts/jt-ipam.sh harden-audit`)
- A09: structured audit logging, including exports and reveals of keys and tokens
- A10: outbound connections checked for every protocol (HTTP at connect time; LDAP, SMTP, RADIUS, WinRM, DNS, syslog and consoles before connecting); metadata / link-local blocked, and consoles also refuse loopback

## Stack

| Layer | Choice |
|------|--------|
| Backend | Python 3.12 · FastAPI · SQLAlchemy 2.0 (async) · asyncpg · Alembic · Pydantic v2 |
| Database | PostgreSQL 16 (native `inet`/`cidr`/`macaddr`) + pgvector |
| Frontend | Vue 3 · TypeScript · Vite · Naive UI · Pinia · vue-i18n |
| Auth | argon2id · TOTP · short-lived JWT + refresh |
| AI | LLM Server (local) · pgvector · MCP server |
| Deploy | systemd + nginx + apt packages; **no Docker image needed** (VM / container friendly) |

## Install (single host / VM / container)

> **Supported:** Debian 12 / 13 and Ubuntu 22.04 / 24.04 / 26.04, x86_64 (amd64); Ubuntu 24.04 LTS or Debian 12 / 13
> recommended. TLS is mandatory. These are exactly the versions jt-ipam prebuilds guacd for (the required RDP / VNC
> console engine); on other versions, derivatives (e.g. Linux Mint) or ARM the installer stops. New OS releases are added
> as they ship; see [docs/INSTALL.md](docs/INSTALL.md#supported-distributions).
>
> **Minimum:** 2 vCPU · 4 GB RAM · 20 GB disk. **Recommended:** 4 vCPU · 8 GB RAM · 50 GB disk (room for the database, audit log, IP history and backups to grow). On a 4 GB machine the backend runs 2 workers, and an upgrade pauses it while the frontend builds if memory is short; 2 GB of swap avoids that. Each RDP console session takes a few hundred MB and some CPU. An LLM server on the same machine needs its own memory on top.
>
> The optional local LLM (Ollama) is **not** included in these figures. Run it on a separate host; it needs its own RAM/VRAM sized to the chosen model.

```bash
# Prerequisite: a minimal system may not ship curl (the one-liner needs it):
sudo apt-get update && sudo apt-get install -y curl
# One line: auto-clones to /opt/jt-ipam and installs (no manual git needed):
curl -fsSL https://raw.githubusercontent.com/jasoncheng7115/jt-ipam/main/scripts/bootstrap.sh | sudo bash
```

The script installs `postgresql-16` / `python3.12` / `nginx` / `redis` (plus `fonts-noto-cjk`, the Chinese / Japanese font embedded into PDF reports), creates the `jtipam` system account and PG role, generates keys into `/etc/jt-ipam/backend.env`, runs `alembic upgrade head`, builds the frontend, and enables `jt-ipam-backend.service`.

Upgrade an existing install with `sudo bash /opt/jt-ipam/scripts/jt-ipam.sh upgrade`: **the script runs `git pull` itself**, then backup → deps → alembic → build → restart. See [`docs/INSTALL.md`](docs/INSTALL.md).

> **Coming from 0.5.170 or earlier, run `upgrade` twice.** The upgrade script is itself updated by the `git pull`, but that run is still driven by the old copy (from 0.5.171 it hands over to the new one after pulling). Afterwards, `sudo bash /opt/jt-ipam/scripts/jt-ipam.sh doctor` will confirm the result and print a fix for anything it cannot verify.

> **Optional: Docker Compose.** A secondary deploy path lives in [`deploy/docker/`](deploy/docker/) (`./gen-env.sh` then `docker compose up -d --build`; update later with `./update.sh`). systemd + apt remains the primary, fully-supported method.

### Installed, but something looks off? Run the health check

```bash
sudo bash /opt/jt-ipam/scripts/jt-ipam.sh doctor
```

It checks the configuration file, whether the backend actually answers, the database and its `pgvector` extension, whether the schema is at the latest revision, whether the built frontend matches the backend version, the timers and the backup directory, the last sync result, and the local scan agent. **Anything it can't confirm comes with a command you can copy and run** (the `→` line), so no log archaeology is required. Including this output makes a bug report much faster to act on.

### First login & resetting the admin password

On a fresh install the script **creates an `admin` account with a random password and prints it once** at the end (also saved to `/etc/jt-ipam/.admin-initial-password`, root-only; it lives under `/etc`, outside the web root, so it is never reachable over HTTP). Log in and change it immediately, then you can safely delete the file: `sudo rm /etc/jt-ipam/.admin-initial-password`.

To reset the admin password (or create the first admin if none exists), run on the server:

```bash
sudo -u jtipam bash -c 'cd /opt/jt-ipam/backend; set -a; source /etc/jt-ipam/backend.env; set +a; \
  .venv/bin/python -m app.cli.bootstrap create-admin \
    --username admin --email admin@example.com --password-stdin --force-update'
# then type the new password on stdin (≥ 12 chars)
```

Omit `--force-update` to create a brand-new admin instead of resetting an existing one.

If the only administrator has lost both the authenticator and the recovery codes, clear their two-factor
authentication from the server (it also signs them out everywhere and writes an audit record):

```bash
sudo -u jtipam bash -c 'cd /opt/jt-ipam/backend; set -a; source /etc/jt-ipam/backend.env; set +a; \
  .venv/bin/python -m app.cli.bootstrap reset-mfa --username admin'
```

## TLS / HTTPS

HTTPS is mandatory; pick one mode via `BACKEND_TLS_MODE` in `/etc/jt-ipam/backend.env`.

**Mode A: nginx reverse proxy (default, recommended)** `BACKEND_TLS_MODE=nginx`
nginx terminates TLS and proxies to uvicorn on 127.0.0.1:8000. To install a real cert:

```bash
# overwrite the fixed cert/key paths, then reload (paths are hard-coded in the nginx site)
cp fullchain.pem /etc/jt-ipam/tls/server.crt
cp privkey.pem   /etc/jt-ipam/tls/server.key
chmod 600 /etc/jt-ipam/tls/server.key
nginx -t && systemctl reload nginx
```

Let's Encrypt: point `ssl_certificate` at `/etc/letsencrypt/live/<FQDN>/fullchain.pem` and `ssl_certificate_key` at `…/privkey.pem`, then `systemctl reload nginx` after renewal. Minimal self-hosted reverse-proxy block:

```nginx
server {
    listen 443 ssl;
    server_name ipam.example.com;
    ssl_certificate     /etc/jt-ipam/tls/server.crt;
    ssl_certificate_key /etc/jt-ipam/tls/server.key;
    root /opt/jt-ipam/frontend/dist;
    index index.html;
    location /api/ { proxy_pass http://127.0.0.1:8000; proxy_set_header Host $host; proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for; proxy_set_header X-Forwarded-Proto $scheme; }
    location / { try_files $uri $uri/ /index.html; }
}
```

**Mode B: uvicorn direct, self-signed** `BACKEND_TLS_MODE=direct`
uvicorn serves TLS itself; `scripts/generate-self-signed-cert.sh` creates a self-signed cert at install. To replace it, overwrite the same paths and restart the service:

```bash
cp fullchain.pem /etc/jt-ipam/tls/server.crt
cp privkey.pem   /etc/jt-ipam/tls/server.key
chmod 600 /etc/jt-ipam/tls/server.key
systemctl restart jt-ipam-backend
```

> Both modes use the same cert paths (`/etc/jt-ipam/tls/server.{crt,key}`); the only difference is who terminates TLS: Mode A reloads nginx, Mode B restarts the backend.

**Mode C: behind your own external reverse proxy** (a separate nginx / LB terminates TLS)
The local nginx serves plain HTTP; apply the external-proxy templates:

```bash
sudo cp deploy/nginx/jt-ipam-external-proxy.conf         /etc/nginx/sites-available/jt-ipam
sudo cp deploy/nginx/jt-ipam-external-proxy-snippet.conf /etc/nginx/snippets/jt-ipam-proxy.conf
sudo nginx -t && sudo systemctl reload nginx
```

> ⚠️ **Required: security headers at the public edge.** The proxy that **terminates TLS for users** must
> emit the security headers (HSTS, CSP `frame-src 'self'`, X-Frame-Options, nosniff, Referrer-Policy,
> Permissions-Policy, COOP, CORP) and `server_tokens off`. They do **not** survive an extra proxy hop, so if
> your edge box is a *different* machine than the one above, **set the headers on that edge box too**
> (the bundled templates already do; replicate them on a non-nginx LB). Verify through the real public URL:
> `curl -skI https://your-domain/ | grep -iE 'strict-transport|content-security|x-frame|cross-origin|^server'`
> and check that each header appears **exactly once**, with `Server: nginx` (no version).

An external proxy does **not** break OIDC / M365 (Entra ID) login, but three things must be right or you'll be redirected to `ipam.example.com` or stuck on the login page:
1. Set `APP_PUBLIC_URL` / `API_PUBLIC_URL` / `CORS_ORIGINS` in `/etc/jt-ipam/backend.env` to your public domain (not the default `ipam.example.com`), then `systemctl restart jt-ipam-backend`.
2. The external proxy must forward `X-Forwarded-Proto $scheme` (=https) and `Host $host`; the template passes them through so the backend sees https (Secure cookies work).
3. Set the OIDC Redirect URI to `https://your-domain/api/v1/auth/oidc/callback` in both the IdP and the jt-ipam UI; note that the **UI/DB value overrides .env**, so re-save it in the UI after editing .env.

## Project layout

```
jt-ipam/
├── docs/              # spec, security, data model, API reference
├── backend/           # FastAPI app
│   └── app/
│       ├── core/      # config / db / audit / safe_http / encrypted_secret
│       ├── models/    # SQLAlchemy 2.0
│       ├── schemas/   # Pydantic v2
│       ├── api/v1/    # REST API
│       ├── services/  # business logic (ai / oui / opnsense / topology / search / permission)
│       ├── mcp/       # MCP server + tools (for LLM clients)
│       └── plugins/   # plugin system
├── frontend/          # Vue 3 + TS
│   └── src/{views,components,composables,api,stores,i18n,router}
└── scripts/           # jt-ipam.sh (install/upgrade/uninstall), ci.sh, oui_refresh.py
```

## Roadmap status

- **Phase 1 (done)**: phpIPAM-equivalent features + improvements (Section/Subnet/IP/VLAN/VRF/NAT/Devices/Racks/Locations/IP-Requests, TOTP/API-Token/RBAC, phpIPAM import, CSV/RIPE/TWNIC, visual subnet grid, forced TLS)
- **Phase 2 (done)**: multi-vendor DNS + deep LibreNMS integration (device/ARP/FDB/effective-status) + anomaly detection + SHA-256 audit chain + pgvector AI semantic search
- **Phase 3 (done)**: Tenancy/Contacts/Cabling/Power/VPN/Virtualization + Proxmox VE sync + Cytoscape topology + OIDC/SAML SSO + OPNsense / pfSense / FortiGate / Palo Alto / Check Point / MikroTik firewall sync + VMware ESXi / vCenter inventory + Wazuh agent inventory + Zabbix monitoring coverage
- **Phase 4 (done, scoped)**: MCP server + local-LLM natural language (LLM Server) + plugin mechanism

### Embedding a rack diagram in another system

A rack diagram can be served as an SVG at a single URL, so another dashboard (a LibreNMS
widget, for example) can show it with a plain `<img>`:

```html
<img src="https://your-ipam.example.com/api/v1/racks/<rack-id>/embed.svg?token=<token>">
```

Two switches must both be on: enable embedding and generate a token under **Admin → System Settings**, and turn on **that rack's** own "external embedding" toggle (per rack, off by
default). A rack diagram shows device names and positions, and the token is a key; do not
post it publicly. Regenerating the token invalidates every existing URL immediately. The token
expires (30 to 365 days, notified before expiry) and is shown only when an administrator reveals
it, which is audited.

Serving an image rather than an iframe is deliberate: this service sends
`frame-ancestors 'none'`, so iframe embedding is blocked by design, and allowing specific
origins would open a clickjacking surface.

## License

AGPL-3.0-or-later. Commercial support: contact Jason Tools.
