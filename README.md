# jt-ipam v0.4.167

[![License](https://img.shields.io/github/license/jasoncheng7115/jt-ipam?color=blue)](LICENSE)
[![Last commit](https://img.shields.io/github/last-commit/jasoncheng7115/jt-ipam)](https://github.com/jasoncheng7115/jt-ipam/commits/main)
[![Stars](https://img.shields.io/github/stars/jasoncheng7115/jt-ipam?style=flat)](https://github.com/jasoncheng7115/jt-ipam/stargazers)
![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-async-009688?logo=fastapi&logoColor=white)
![Vue](https://img.shields.io/badge/Vue-3-42b883?logo=vuedotjs&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)
![OWASP](https://img.shields.io/badge/OWASP-Top%2010%3A2025-000000)

**🌐 [Project site / 專案介紹網站 →](https://jasoncheng7115.github.io/jt-ipam/)**

> A self-hosted, integration-focused IPAM, independently developed with an operation flow familiar to phpIPAM users, deeply integrated with multiple DNS servers, LibreNMS, OPNsense, Proxmox VE, Wazuh, and a local LLM.
>
> By Jason Tools Co., Ltd. · License: Apache-2.0 · 繁體中文: [README_zh-TW.md](README_zh-TW.md)

---

## Why jt-ipam?

Familiar to phpIPAM users so they are productive from day one, but built from scratch on a modern stack (not based on phpIPAM's codebase). Deep integrations:

- **DNS** — PowerDNS, BIND 9, OPNsense Unbound, Univention UCS, Microsoft Windows DNS (reads forward/reverse status, optional record push)
- **LibreNMS** — device sync, ARP / FDB harvesting, online-status reconciliation, auto-onboarding to monitoring
- **Infrastructure** — Proxmox VE, Wazuh, OPNsense (alias / rule / NAT sync)
- **Graylog** — exposes an IP→hostname/FQDN DSV lookup endpoint for Graylog's "DSV File from HTTP" data adapter
- **Local AI** — natural-language queries and semantic search over LLM Server (data never leaves the host), plus an MCP server (stdio and Streamable HTTP transports) so external LLM clients can drive the IPAM; `gemma4:26b` works well in our testing

Also built in: an **IP request approval workflow** (configurable multi-stage / parallel sign-off, with in-app + email notifications), **DNS record review** (find records with no matching IPAM address), a **scan agent** (ICMP/ARP/rDNS/NetBIOS/mDNS/OS probes), **central certificate storage & distribution** (upload a commercial / self-signed cert once; a pure-bash agent pulls it on a schedule and deploys it to nginx / apache / caddy / haproxy / Proxmox VE·PMG·PBS / Zimbra and more, reloading the service — with encrypted private keys, expiry alerts and manual renew), **floor plans + rack U-diagrams** (half-U, front/rear, SVG/PNG/draw.io export), **cable tracing** (multi-hop), an IP change log with stale-IP reclaim, and a universal table column-picker + multi-format export.

## Graylog log enrichment (DSV lookup)

jt-ipam generates a **live** IP → hostname / FQDN lookup table that Graylog's "DSV File from HTTP" data adapter can poll, so log events that only carry an IP get a human-readable name automatically.

- Enable under **Admin → System Settings → Graylog DSV**: pick a path slug, output format (CSV / TSV), and generate an access token
- Endpoint `GET /api/v1/lookup/<path>?token=<token>` is generated on each request straight from the database
- **Fields provided**: two columns per row — column 1 = IP (key), column 2 = hostname or FQDN (value); only IPs that have a hostname are emitted
- **Data format**: UTF-8 plain text. CSV is comma-separated with **every field wrapped in double quotes** (RFC 4180 escaping); TSV is tab-separated (unquoted). For example:

  ```csv
  "10.1.1.141","log1.example.com"
  "10.1.1.145","mg-host"
  ```

- In Graylog's "DSV File from HTTP" adapter: set the URL above, separator to comma or tab per format, and **Key column = 1, Value column = 2**
- The token is validated per request and can be regenerated anytime; the settings page shows a ready-to-copy full lookup URL

## Core entities

`Section → Subnet → IPAddress`, plus `Device` / `Rack` / `Location`, `Customer` (managing unit), `VLAN` / `VRF`, `NAT`, OPNsense firewalls, and an IEEE OUI vendor table (monthly refresh).

## Access control (RBAC)

Object-level permissions across **7 object types** (customer / section / subnet / IP / device / rack / location) with:

- **Hierarchical cascade** — granting an upper level (e.g. a customer or section) automatically covers everything beneath it (subnets → IPs; locations → racks → devices)
- **"All" wildcard** per object type
- **5 built-in roles** — System Administrator, Read-only Viewer, Network Operator, Auditor, Department Administrator
- Visibility is enforced everywhere: list endpoints, global search, the topology graph, and every dropdown only ever show objects the principal may see. Deny-by-default.

## Security (OWASP Top 10:2025)

Security is a day-one requirement; every module and PR is checked against **OWASP Top 10:2025**. See [`SECURITY.md`](SECURITY.md).

- **TLS enforced** — pick one: nginx reverse-proxy termination (`BACKEND_TLS_MODE=nginx`) or uvicorn serving a self-signed cert directly (`BACKEND_TLS_MODE=direct`)
- A01 — deny-by-default RBAC with object-level checks (above)
- A02 — argon2id password hashing; application-layer encryption for stored secrets (DNS credentials / SNMP / API tokens)
- A03 — parameterized SQLAlchemy, strict Pydantic v2 validation, CSP + output escaping
- A05 — HSTS, CSP, X-Frame-Options, Referrer-Policy
- A07 — TOTP MFA, account lockout, HttpOnly+Secure+SameSite cookies, API-token TTL
- A08 — SHA-256 audit chain
- A09 — structured audit logging
- A10 — SSRF allow-listing for all outbound integrations; metadata / link-local blocked

## Stack

| Layer | Choice |
|------|--------|
| Backend | Python 3.12 · FastAPI · SQLAlchemy 2.0 (async) · asyncpg · Alembic · Pydantic v2 |
| Database | PostgreSQL 16 (native `inet`/`cidr`/`macaddr`) + pgvector |
| Frontend | Vue 3 · TypeScript · Vite · Naive UI · Pinia · vue-i18n |
| Auth | argon2id · TOTP · short-lived JWT + refresh |
| AI | LLM Server (local) · pgvector · MCP server |
| Deploy | systemd + nginx + apt packages — **no Docker image needed** (VM / container friendly) |

## Install (single host / VM / container)

> Debian 12 / Ubuntu 22.04+ (64-bit). TLS is mandatory.
>
> **Minimum:** 2 vCPU · 4 GB RAM · 20 GB disk. **Recommended:** 4 vCPU · 8 GB RAM · 40 GB+ disk (room for the PostgreSQL database, GeoIP/OUI data, and backups to grow).
>
> The optional local LLM (Ollama) is **not** included in these figures — run it on a separate host; it needs its own RAM/VRAM sized to the chosen model.

```bash
# One line — auto-clones to /opt/jt-ipam and installs (no manual git needed):
curl -fsSL https://raw.githubusercontent.com/jasoncheng7115/jt-ipam/main/scripts/bootstrap.sh | sudo bash
```

The script installs `postgresql-16` / `python3.12` / `nginx` / `redis`, creates the `jtipam` system account and PG role, generates keys into `/etc/jt-ipam/backend.env`, runs `alembic upgrade head`, builds the frontend, and enables `jt-ipam-backend.service`.

Upgrade an existing install with `sudo bash /opt/jt-ipam/scripts/jt-ipam.sh upgrade` — **the script runs `git pull` itself**, then backup → deps → alembic → build → restart. See [`docs/INSTALL.md`](docs/INSTALL.md).

### First login & resetting the admin password

On a fresh install the script **creates an `admin` account with a random password and prints it once** at the end (also saved to `/etc/jt-ipam/.admin-initial-password`, root-only — it lives under `/etc`, outside the web root, so it is never reachable over HTTP). Log in and change it immediately, then you can safely delete the file: `sudo rm /etc/jt-ipam/.admin-initial-password`.

To reset the admin password (or create the first admin if none exists), run on the server:

```bash
sudo -u jtipam bash -c 'cd /opt/jt-ipam/backend; set -a; source /etc/jt-ipam/backend.env; set +a; \
  .venv/bin/python -m app.cli.bootstrap create-admin \
    --username admin --email admin@example.com --password-stdin --force-update'
# then type the new password on stdin (≥ 12 chars)
```

Omit `--force-update` to create a brand-new admin instead of resetting an existing one.

## TLS / HTTPS

HTTPS is mandatory; pick one mode via `BACKEND_TLS_MODE` in `/etc/jt-ipam/backend.env`.

**Mode A — nginx reverse proxy (default, recommended)** `BACKEND_TLS_MODE=nginx`
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

**Mode B — uvicorn direct, self-signed** `BACKEND_TLS_MODE=direct`
uvicorn serves TLS itself; `scripts/generate-self-signed-cert.sh` creates a self-signed cert at install. To replace it, overwrite the same paths and restart the service:

```bash
cp fullchain.pem /etc/jt-ipam/tls/server.crt
cp privkey.pem   /etc/jt-ipam/tls/server.key
chmod 600 /etc/jt-ipam/tls/server.key
systemctl restart jt-ipam-backend
```

> Both modes use the same cert paths (`/etc/jt-ipam/tls/server.{crt,key}`); the only difference is who terminates TLS — Mode A reloads nginx, Mode B restarts the backend.

**Mode C — behind your own external reverse proxy** (a separate nginx / LB terminates TLS)
The local nginx serves plain HTTP; apply the external-proxy templates:

```bash
sudo cp deploy/nginx/jt-ipam-external-proxy.conf         /etc/nginx/sites-available/jt-ipam
sudo cp deploy/nginx/jt-ipam-external-proxy-snippet.conf /etc/nginx/snippets/jt-ipam-proxy.conf
sudo nginx -t && sudo systemctl reload nginx
```

An external proxy does **not** break OIDC / M365 (Entra ID) login, but three things must be right or you'll be redirected to `ipam.example.com` or stuck on the login page:
1. Set `APP_PUBLIC_URL` / `API_PUBLIC_URL` / `CORS_ORIGINS` in `/etc/jt-ipam/backend.env` to your public domain (not the default `ipam.example.com`), then `systemctl restart jt-ipam-backend`.
2. The external proxy must forward `X-Forwarded-Proto $scheme` (=https) and `Host $host`; the template passes them through so the backend sees https (Secure cookies work).
3. Set the OIDC Redirect URI to `https://your-domain/api/v1/auth/oidc/callback` in both the IdP and the jt-ipam UI — note the **UI/DB value overrides .env**, so re-save it in the UI after editing .env.

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

- **Phase 1 (done)** — phpIPAM-equivalent features + improvements (Section/Subnet/IP/VLAN/VRF/NAT/Devices/Racks/Locations/IP-Requests, TOTP/API-Token/RBAC, phpIPAM import, CSV/RIPE/TWNIC, visual subnet grid, forced TLS)
- **Phase 2 (done)** — multi-vendor DNS + deep LibreNMS integration (device/ARP/FDB/effective-status) + anomaly detection + SHA-256 audit chain + pgvector AI semantic search
- **Phase 3 (done)** — Tenancy/Contacts/Cabling/Power/VPN/Virtualization + Proxmox VE sync + Cytoscape topology + OIDC/SAML SSO + OPNsense firewall sync + Wazuh agent inventory
- **Phase 4 (done, scoped)** — MCP server + local-LLM natural language (LLM Server) + plugin mechanism

## License

Apache-2.0. Commercial support: contact Jason Tools.
