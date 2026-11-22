# jt-ipam Install & Operations SOP

> 繁體中文版：[INSTALL_zh-TW.md](INSTALL_zh-TW.md) · 日本語：[INSTALL_ja.md](INSTALL_ja.md)

For **Proxmox LXC, bare metal, and VMs** on Debian 12 / 13 or Ubuntu 22.04 / 24.04 / 26.04, x86_64 (see
[Supported distributions](#supported-distributions)). The **primary, recommended** install
uses **systemd + apt** directly (no Docker). A Docker Compose path exists but is **optional / secondary, not
the preferred mode**; see [§2.7](#27-optional-docker-compose-not-the-preferred-mode).

> Security is a day-one requirement: HTTPS is enforced in all environments; the cert can be
> served via an nginx reverse proxy or a self-signed cert served directly by uvicorn. If SSL
> isn't set up, the backend **will not start** (A02).

---

## 1. System requirements

| Item | Minimum | Recommended | Notes |
|---|---|---|---|
| OS | Debian 12 / 13, Ubuntu 22.04 / 24.04 / 26.04 (x86_64) | **Ubuntu 24.04 LTS** | only these (see below); 24.04 ships Python 3.12 + PG 16, saving effort |
| CPU | 2 vCPU | 4 vCPU | upgrades build the frontend (about a minute); RDP consoles (guacd) and the local scan agent (nmap) use CPU. Embeddings run on the LLM server, not here |
| RAM | 4 GB | 8 GB | about 1.8 GB in use with 4 workers; the frontend build during an upgrade peaks at about 1.6 GB. With 4 GB or 2 cores the backend runs 2 workers and is paused during the build if memory is short (2 GB of swap avoids that). Each RDP console takes a few hundred MB. An LLM server on the same machine needs 8 GB+ on top |
| Disk | 20 GB | 50 GB | the install takes about 2 GB (Python packages, node_modules, caches) plus the OS; the database, audit log, IP history and backups grow with the network; journald caps its logs |
| Python | 3.11 | 3.12 | 24.04 defaults to 3.12 |
| PostgreSQL | 16 + pgvector | None | 22.04 needs the PGDG repo (the script adds it automatically) |
| Redis | 7 | None | 24.04 defaults to 7.0.15 |
| Node | 22 LTS | None | only for building the frontend; `jt-ipam.sh` installs NodeSource 22 on install (only Ubuntu 26.04 ships 22 itself) and moves an older Node to 22 on upgrade |
| guacd | jt-ipam build for this OS | None | **Required**: the RDP / VNC console engine. `jt-ipam.sh` installs it (see [guacd](#guacd-console-engine-default-for-rdp--vnc)); aardwolf, the old engine, is optional |
| Recog | latest release | None | **Optional**: fingerprint database the IP probe uses to recognise devices and OS versions; downloaded by `jt-ipam.sh`, checked weekly (see [Recog](#recog-fingerprint-database-optional)) |
| CJK font | `fonts-noto-cjk` | None | **Needed for PDF reports**: the Chinese / Japanese font the backend embeds into PDF exports (about 60 MB download). `jt-ipam.sh` installs it on install and upgrade; if apt cannot reach a mirror the upgrade only warns, PDF export then says the font is missing (DOCX / ODT still work). Fix later with `sudo apt install fonts-noto-cjk` |
| SNMP client tools | `snmp` (net-snmp) | None | **Optional**: `snmpget` / `snmpbulkwalk` for SNMP collection by the local scan agent (collector "server"). `jt-ipam.sh` installs it on install and upgrade (the upgrade only warns if it cannot); remote scan agents get it from their installer (`snmp` on apt, `net-snmp-utils` on dnf, `net-snmp-tools` on apk). Without it that agent simply has no SNMP capability |

### Supported distributions

| Distribution | Versions | Architecture |
|---|---|---|
| Debian | 12 (bookworm), 13 (trixie) | x86_64 (amd64) |
| Ubuntu | 22.04 LTS, 24.04 LTS, 26.04 LTS | x86_64 (amd64) |

Why exactly these: guacd, the engine behind the RDP and VNC consoles, is a **required component**, and no
distribution ships a usable one (Debian dropped it; Ubuntu only has 1.3.0 with known remote-code-execution
bugs). jt-ipam builds guacd for each version above; `install` stops on anything else, and `upgrade` warns.
The installer picks the build from `/etc/os-release` (`ID` + `VERSION_ID`), so:

- **Proxmox LXC templates, VMs and bare metal** of the versions above all work, including a Proxmox VE host
  itself (it is Debian).
- **Derivatives** (Linux Mint, Pop!_OS, Zorin, …), **other versions** (e.g. Debian 11, Ubuntu 20.04 or a
  non-LTS release without a build) and **ARM** (Raspberry Pi, Ampere) are not supported.
- **New OS releases** are checked every release (`scripts/guacd/check-new-os.sh`) and added once built and
  verified. Do not upgrade a jt-ipam host to a new OS release until it appears in this table.
- Advanced: a build for another Debian / Ubuntu version can be made with `scripts/guacd/build.sh` (Docker)
  and installed with `jt-ipam.sh install --guacd-tarball <file>`; it is not tested by us.

**Virtualization note**: on Proxmox VM / LXC, load avg may spike for 1-2 minutes right after boot/reboot (other VMs on the hypervisor contending for CPU; see `%steal` in `mpstat`); this isn't the VM itself being busy, you can just run the install.

---

## 2. One-shot install

### 2.1 Prep: apt update + reboot (strongly recommended)

Fully update the OS on a new machine before installing, to avoid kernel + libc mismatches:

```bash
sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get -y -qq upgrade
sudo systemctl reboot
```

### 2.2 One-shot install

Fastest: one-shot bootstrap (auto-clones to /opt/jt-ipam, then runs the unified deploy script; install flags can be passed through):

```bash
# prerequisite: a minimal system may not ship curl (the one-liner needs it)
sudo apt-get update && sudo apt-get install -y curl
curl -fsSL https://raw.githubusercontent.com/jasoncheng7115/jt-ipam/main/scripts/bootstrap.sh \
  | sudo bash -s -- --tls-mode nginx --public-fqdn ipam.example.com
```

Or clone manually and run the unified deploy script `scripts/jt-ipam.sh`:

```bash
git clone https://github.com/jasoncheng7115/jt-ipam.git /opt/jt-ipam
cd /opt/jt-ipam

# Pick one of three TLS modes:
#
#   nginx         : nginx terminates HTTPS, backend on loopback; auto self-signed bootstrap if cert missing
#   self-signed   : uvicorn direct with its own self-signed cert (no nginx; fastest to go live)
#   direct        : uvicorn direct, you provide the cert (falls back to self-signed if missing)

# (A) nginx + temporary self-signed (cp a real cert in later), recommended for production
sudo ./scripts/jt-ipam.sh install --tls-mode nginx --public-fqdn ipam.example.com

# (B) uvicorn direct self-signed (fastest for internal/dev)
sudo ./scripts/jt-ipam.sh install --tls-mode self-signed --public-fqdn ipam.local
```

> `scripts/install-debian.sh` is kept as a compatibility shim (it forwards to `jt-ipam.sh install`), so older commands still work.

The script will:

1. Install PostgreSQL 16 + pgvector + Redis 7 (Ubuntu 22.04 auto-adds the PGDG repo; Ubuntu 24.04 uses the official source directly)
2. Create the `jtipam` system user, `/opt/jt-ipam/backend/.venv`, install Python deps
3. Create the `jt_ipam` DB role/database, apply alembic migrations
4. Generate `SECRET_KEY` / `ENCRYPTION_KEY` / `AUDIT_CHAIN_GENESIS` into `/etc/jt-ipam/backend.env` (0640)
5. Generate a self-signed cert in `/etc/jt-ipam/tls/` ECDSA P-384 / 5 years; SAN auto-includes `localhost / FQDN / short hostname / 127.0.0.1 / ::1 / host IP`
6. pnpm install + frontend vite build
7. Install `jt-ipam-backend.service` + `jt-ipam-sync.timer` (every 5 min) + `jt-ipam-ai-audit.timer` (checks every 5 min whether the scheduled AI review is due; nothing runs until it is turned on) + `jt-ipam-backup.timer` (daily 03:30)
8. Only in nginx mode: install the nginx site and reload

### 2.3 Bootstrap the first admin

```bash
# generate a random strong password, read from stdin (leaves no shell history)
ADMIN_PW=$(openssl rand -base64 24)
sudo -u jtipam env $(grep -v '^#' /etc/jt-ipam/backend.env | xargs) \
    /opt/jt-ipam/backend/.venv/bin/python -m app.cli.bootstrap create-admin \
    --username admin --email admin@your.domain --password-stdin <<<"$ADMIN_PW"
echo "ADMIN_PASSWORD=$ADMIN_PW"   # keep it safe
```

Open `https://<your-fqdn>/` in a browser to log in. The browser warns about the self-signed cert; click Advanced → Continue.

### 2.4 End-to-end sanity check

```bash
# healthz
curl -kfsS https://127.0.0.1/healthz                       # should return ok

# login + chain verify (verifies A08)
TOKEN=$(curl -kfsS -X POST https://127.0.0.1/api/v1/auth/login \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"admin\",\"password\":\"$ADMIN_PW\"}" | jq -r .access_token)
curl -kfsS -X POST https://127.0.0.1/api/v1/audit/verify \
    -H "Authorization: Bearer $TOKEN" | jq .
# should see {"ok": true, "broken_at_id": null, "checked": N}

# systemd security score
systemd-analyze security jt-ipam-backend | tail -3
# target: ≤ 3.5; currently measured 1.3
```

### 2.5 Switch to a real TLS cert (nginx mode)

Bootstrapped with self-signed; once you have the real cert, **just cp it over and reload nginx**:

```bash
# cp the vendor cert + key to the fixed paths (preserve permissions root:jtipam)
sudo install -m 0644 -o root -g jtipam /path/to/your-cert.pem  /etc/jt-ipam/tls/server.crt
sudo install -m 0640 -o root -g jtipam /path/to/your-key.pem   /etc/jt-ipam/tls/server.key

# if you got a fullchain (with intermediates) + key, prefer the fullchain
sudo install -m 0644 -o root -g jtipam /path/to/fullchain.pem  /etc/jt-ipam/tls/server.crt

# verify + reload
sudo nginx -t && sudo systemctl reload nginx

# confirm the new cert is live
openssl s_client -connect ipam.example.com:443 -servername ipam.example.com </dev/null 2>/dev/null \
    | openssl x509 -noout -issuer -subject -dates
```

To replace again later, just repeat the three steps above; there is no need to re-run install.

Let's Encrypt route:

```bash
sudo apt install -y certbot python3-certbot-nginx
# certbot auto-edits ssl_certificate to point at /etc/letsencrypt/live/...
sudo certbot --nginx -d ipam.example.com
```

### 2.6 Switch to a real TLS cert (uvicorn direct / self-signed mode)

With `BACKEND_TLS_MODE=direct` (or `self-signed`), **uvicorn serves TLS itself**, no nginx. The cert paths are the same as nginx mode; the only difference is you **restart the backend** (not reload nginx) after swapping:

```bash
# cp the real (or self-managed) cert + key to the fixed paths
sudo install -m 0644 -o root -g jtipam /path/to/fullchain.pem /etc/jt-ipam/tls/server.crt
sudo install -m 0640 -o root -g jtipam /path/to/your-key.pem  /etc/jt-ipam/tls/server.key

# restart the service to apply (uvicorn reads --ssl-certfile/--ssl-keyfile at startup)
sudo systemctl restart jt-ipam-backend

# confirm the new cert is live (for the port, see "Which port" below)
openssl s_client -connect ipam.example.com:8443 -servername ipam.example.com </dev/null 2>/dev/null \
    | openssl x509 -noout -issuer -subject -dates
```

> To regenerate a self-signed cert yourself: `sudo bash /opt/jt-ipam/scripts/generate-self-signed-cert.sh` then `systemctl restart jt-ipam-backend`.

#### Which port does it listen on? (**8443 by default, not 443**)

In `self-signed` / `direct` mode uvicorn terminates TLS itself and **listens on 8443 by default**:
the URL is `https://<your-fqdn>:8443/`. "Port 443 never came up" after an install is almost always
this, not a failed install. The actual value lives in `BACKEND_BIND_PORT` in `/etc/jt-ipam/backend.env`.

To use 443 without nginx:

```bash
# at install time (preferred: the installer also grants the capability needed for a privileged port)
sudo ./scripts/jt-ipam.sh install --tls-mode self-signed --public-fqdn ipam.example.com --bind-port 443

# on an existing install:
sudo sed -i 's/^BACKEND_BIND_PORT=.*/BACKEND_BIND_PORT=443/' /etc/jt-ipam/backend.env
sudo sed -i 's#^APP_PUBLIC_URL=.*#APP_PUBLIC_URL=https://ipam.example.com#' /etc/jt-ipam/backend.env
sudo install -d /etc/systemd/system/jt-ipam-backend.service.d
sudo tee /etc/systemd/system/jt-ipam-backend.service.d/20-bind-privileged-port.conf >/dev/null <<'EOF'
[Service]
AmbientCapabilities=CAP_NET_RAW CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_RAW CAP_NET_BIND_SERVICE
EOF
sudo systemctl daemon-reload && sudo systemctl restart jt-ipam-backend
```

> The service does not run as root, so binding a port below 1024 **requires**
> `CAP_NET_BIND_SERVICE`. Without it the unit starts and dies immediately with
> `Permission denied`, which reads like a certificate problem but is a port problem.

#### Do I need nginx in self-signed mode?

**No.** `self-signed` / `direct` is a complete HTTPS service on its own; no web server required.
Reasons to add nginx anyway: you want 443 without granting the service a capability, you share 443
with other sites on the same host, or you want the reverse proxy's hardened headers (see 2.7).
If you do, **switch to nginx mode** rather than putting TLS in front of TLS:

```bash
sudo apt install -y nginx

# 1) backend back to loopback + plain HTTP (nginx terminates TLS)
sudo sed -i 's/^BACKEND_TLS_MODE=.*/BACKEND_TLS_MODE=nginx/' /etc/jt-ipam/backend.env
sudo sed -i 's/^BACKEND_BIND_PORT=.*/BACKEND_BIND_PORT=8000/' /etc/jt-ipam/backend.env
sudo sed -i 's#^APP_PUBLIC_URL=.*#APP_PUBLIC_URL=https://ipam.example.com#' /etc/jt-ipam/backend.env

# 2) install the nginx site (replace the template server_name with your FQDN)
sudo install -d -m 0755 /etc/nginx/snippets
sudo install -m 0644 /opt/jt-ipam/deploy/nginx/jt-ipam-proxy.conf /etc/nginx/snippets/jt-ipam-proxy.conf
sudo sed 's/ipam\.example\.com/ipam.example.com/g' /opt/jt-ipam/deploy/nginx/jt-ipam.conf \
    | sudo tee /etc/nginx/sites-available/jt-ipam >/dev/null
sudo ln -sf /etc/nginx/sites-available/jt-ipam /etc/nginx/sites-enabled/jt-ipam
sudo rm -f /etc/nginx/sites-enabled/default

# 3) certificates where nginx can read them: the template reads
#    /etc/jt-ipam/tls/server.crt and server.key (the existing self-signed pair works)

sudo nginx -t && sudo systemctl restart jt-ipam-backend && sudo systemctl reload nginx
```

> The bundled nginx template already contains the WebSocket upgrade block the consoles need
> (SSH / SFTP / RDP / VNC / noVNC / BMC). **If you write your own config, copy that block**:
> without the upgrade headers the consoles cannot connect and the browser shows only a bare 404.

### 2.7 Production standard: hardened nginx reverse proxy

For any internet-facing or production deployment, the **standard is to run jt-ipam behind the bundled
hardened nginx reverse proxy** (`--tls-mode nginx`, reference config `deploy/nginx/jt-ipam.conf`). nginx
terminates TLS, the backend stays bound to loopback, and the proxy enforces a strict security baseline so the
app server is never exposed directly:

- **TLS**: TLS 1.2/1.3 only, modern cipher suite, OCSP stapling, session tickets off.
- **HSTS**: `max-age` 2y + `includeSubDomains` + `preload`.
- **CSP**: `default-src 'self'`; `script-src 'self'`; `connect-src 'self'`; `frame-src 'self'`;
  `frame-ancestors 'none'`; `base-uri 'self'`; `form-action 'self'`; no third-party script/frame origins.
- **Headers**: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`,
  `Permissions-Policy` (geolocation/mic/camera/payment/usb off), `Cross-Origin-Opener-Policy` and
  `Cross-Origin-Resource-Policy: same-origin`.
- **No banner leak**: `server_tokens off` and the upstream (uvicorn) `Server`/`X-Powered-By` headers are
  hidden, so there is no version or framework fingerprint.
- Backend listens on `127.0.0.1` only; nginx is the sole public listener.

> Do **not** expose uvicorn directly to the internet. `--tls-mode self-signed`/`direct` is for internal/dev.

> ### ⚠️ Required when you put your OWN reverse proxy in front (Mode C)
> The security headers above are applied by whatever nginx **terminates TLS at the public edge**. If you front
> jt-ipam with a separate reverse proxy (e.g. a company edge nginx / load balancer), **that proxy MUST set the
> security headers itself**; they will NOT automatically survive an extra hop, so the public site would ship
> with *no* CSP / HSTS / Permissions-Policy. This is a **required** part of the deployment, not optional.
>
> Install the bundled hardened external-proxy config on that edge box:
> [`deploy/nginx/jt-ipam-external-proxy.conf`](https://github.com/jasoncheng7115/jt-ipam/blob/main/deploy/nginx/jt-ipam-external-proxy.conf)
> + [`jt-ipam-external-proxy-snippet.conf`](https://github.com/jasoncheng7115/jt-ipam/blob/main/deploy/nginx/jt-ipam-external-proxy-snippet.conf)
> (sets HSTS preload, the tightened CSP, X-Frame-Options DENY, nosniff, Referrer-Policy, Permissions-Policy,
> COOP + CORP, `server_tokens off`, and `proxy_hide_header`s the upstream copies so each header appears once).

**Verify the headers are present *through the public URL the users actually hit*** (i.e. through your edge
proxy, not just the local box):

```bash
curl -skI https://ipam.example.com/ \
  | grep -iE 'strict-transport|content-security|x-frame|x-content|referrer|permissions|cross-origin|^server'
# Must show: HSTS, Content-Security-Policy (frame-src 'self'), X-Frame-Options, X-Content-Type-Options,
# Referrer-Policy, Permissions-Policy, COOP, CORP: each exactly ONCE, and Server: nginx (no version).
```

**Consoles and SFTP through your edge proxy.** Every console, SFTP included, is one long-lived
WebSocket on `/api/v1/addresses/<id>/(ssh|sftp|rdp|vnc|novnc|bmc|rustdesk)/ws`. SFTP moves files over that
WebSocket in 256 KB messages, so an HTTP body limit such as nginx `client_max_body_size` does **not**
cap the file size. What can break large transfers is a layer that:

- does not pass the WebSocket upgrade on that path (consoles fail outright);
- limits the size of a single WebSocket message (some WAFs do; allow at least 1 MB, and 16 MB for the RustDesk-compatible web connection, which sends whole video keyframes);
- caps the volume or lifetime of one WebSocket connection, or drops idle ones in under 30 s
  (jt-ipam sends a keep-alive every 20 s).

You do not have to work this out by reading configs:
**Admin → System Settings → SFTP per-file transfer limit** runs a real transfer test from your browser through every layer (edge proxy, the
IPAM nginx, the backend) whenever the limit is raised above the default, and says which kind of limit
got in the way. It also estimates how long a file of the configured size would take at the measured
speed.

### 2.8 Optional: Docker Compose (NOT the preferred mode)

> ⚠️ **Docker Compose is a secondary / optional path; it is NOT the project's preferred or primary
> deployment mode.** The supported, recommended install is **systemd + apt** (sections above). Use Compose
> only for a quick evaluation or a container-first environment; the systemd path gets the most testing.

The files live in [`deploy/docker/`](https://github.com/jasoncheng7115/jt-ipam/tree/main/deploy/docker). One
compose file brings up `postgres` (pgvector), `redis`, `backend` (FastAPI/uvicorn), `sync` (a background sync
loop that replaces the systemd timer), and `web` (nginx serving the frontend + reverse-proxying `/api`, with a
self-signed HTTPS cert on first run).

Prerequisites: **git** and **Docker Engine with the `docker compose` v2 plugin**. The official
`get.docker.com` script installs both; **do not** use `apt install docker.io`, because it lacks the `docker compose`
subcommand. On non-Debian distros install git with your own package manager.

```bash
# prerequisites: curl/git first, then Docker Engine (incl. the compose plugin)
sudo apt-get update && sudo apt-get install -y curl git
curl -fsSL https://get.docker.com | sudo sh
docker compose version         # should print v2.x

# clone the repo first: gen-env.sh / docker-compose.yml live inside it under deploy/docker/
git clone https://github.com/jasoncheng7115/jt-ipam.git
cd jt-ipam/deploy/docker
./gen-env.sh                   # create .env with random secrets (once)
docker compose up -d --build   # build images and start the stack
# then open https://localhost  (self-signed cert on first run; trust the warning)
```

- **First admin:** `gen-env.sh` generates a random `admin` password (printed in its output, stored as
  `JT_IPAM_ADMIN_PASSWORD` in `.env`, mode 0600) and the backend creates the admin on first boot; change it
  after the first login. Prefer your own? Set `JT_IPAM_ADMIN_PASSWORD` in `.env` before the first `up`; or
  leave it empty and create one later with
  `docker compose exec backend python -m app.cli.bootstrap create-admin --username admin --email admin@example.com --password-stdin`.
- **Real TLS cert:** drop `server.crt` / `server.key` into `deploy/docker/certs/` to override the self-signed one.
- **Ports / domain:** edit `HTTP_PORT` / `HTTPS_PORT` / `JT_IPAM_SERVER_NAME` and the matching `APP_PUBLIC_URL` / `CORS_ORIGINS` in `.env`.

**Updating to a newer version** is one command:

```bash
./update.sh    # git pull  ->  docker compose build  ->  docker compose up -d
```

Database migrations run **automatically** when the backend container starts (its entrypoint runs
`alembic upgrade head`), so there is no separate migration step.

**Air-gapped / no-internet host** (build outside, run inside): build the images on an internet-connected
host, carry them over, and load them; the flow is the same for install and upgrade.

```bash
# on the internet-connected host: get the source, then build + pack
git clone https://github.com/jasoncheng7115/jt-ipam.git
cd jt-ipam/deploy/docker
./offline-export.sh                    # -> jt-ipam-images-<sha>.tar.gz (app + postgres/redis images)
                                       # (to upgrade later: git pull first, then re-run offline-export.sh)

# copy that archive + the whole jt-ipam repo folder to the air-gapped host, then in deploy/docker/:
./gen-env.sh                           # first install only (needs openssl, no internet)
./offline-import.sh jt-ipam-images-<sha>.tar.gz   # docker load + up -d --no-build --pull never
```

To upgrade an air-gapped host, re-export on the online host after `git pull`, copy the newer archive over,
and run `./offline-import.sh <newer-archive>` again (`.env` stays put).

> Not bundled in the Compose setup: the plaintext Graylog DSV port 8088, and the GeoIP / OUI scheduled
> refreshes. See [`deploy/docker/README.md`](https://github.com/jasoncheng7115/jt-ipam/blob/main/deploy/docker/README.md)
> for full details.

---

## 3. Environment variables

Main config file: `/etc/jt-ipam/backend.env` (root:jtipam 0640)

| Variable | Required | Notes |
|---|---|---|
| `SECRET_KEY` | ✓ | JWT signing; the installer generates a 64-byte hex |
| `ENCRYPTION_KEY` | ✓ | AES-256-GCM key (encrypts DNS/SNMP/API credentials) |
| `AUDIT_CHAIN_GENESIS` | ✓ | SHA-256 chain head; **must never change** (A08) |
| `POSTGRES_*` | ✓ | DB connection |
| `REDIS_PASSWORD` | ✓ | rate limiter / cache |
| `BACKEND_TLS_MODE` | ✓ | `nginx` or `direct` |
| `APP_PUBLIC_URL` | ✓ | frontend base URL |
| `API_PUBLIC_URL` | ✓ | used for OIDC/SAML callbacks |
| `CORS_ORIGINS` | ✓ | comma-separated |
| `OUTBOUND_ALLOW_CIDRS` | None | safe_http SSRF allowlist; blank = public internet only |
| `FDB_CURRENT_MAX_AGE_HOURS` | 24 | How long an FDB entry counts as current; older ones stay as history but no longer decide the switch port |
| `FDB_RETENTION_DAYS` | 365 | FDB history retention in days; 0 = keep forever. Much longer than ARP on purpose -- the value of this table is knowing which port a machine used to be on |
| `ARP_RETENTION_DAYS` | 30 | ARP entry retention in days; 0 = no pruning |
| `OIDC_*` | None | enable OIDC SSO |
| `SAML_*` | None | enable SAML SSO |
| `LDAP_*` | None | LDAP/AD auth |
| `OLLAMA_ENABLED` | None | enable AI semantic search + chat |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | LLM server address |
| `OLLAMA_CHAT_MODEL` | `gemma4:26b` | chat model |
| `OLLAMA_EMBEDDING_MODEL` | `granite-embedding:278m` | embedding model; **its dimension must equal `EMBEDDING_DIM`** |
| `EMBEDDING_DIM` | `768` | size of the database `vector(N)` column; changing it means changing the migration too |

See the Settings class in `app/core/config.py` for the full list.

---

## 4. Integration setup (after install)

All integrations are added in the admin UI under Admin → External integrations (for example `/firewall`, `/wazuh`, `/librenms`, `/dns`); configured ones are marked in the menu.
Once added, `jt-ipam-sync.timer` syncs them automatically every 5 minutes by default.

> Note: as of v0.4.76+, OIDC and SAML SSO are also configurable in the web UI under Admin → System Settings (no need to edit env). The env vars below still work as defaults.

### OPNsense firewall

1. OPNsense → System → Access → Users → add a service user → get API key/secret
2. jt-ipam → Firewall → Add → fill in `https://opnsense:443`, key, secret
3. Add alias mappings (selector JSON example: `{"type":"section","section_id":"<uuid>"}`)

### pfSense firewall

pfSense has no built-in REST API, so install the third-party **pfSense-pkg-RESTAPI** package (pfrest.org):

1. pfSense → System → Package Manager → install **pfSense-pkg-RESTAPI**
2. System → REST API → Settings → add **API Key** to the auth methods (the default is BasicAuth only)
3. System → REST API → Keys → create a key
4. jt-ipam → Integrate pfSense → Add → fill in `https://pfsense`, the API key; turn off Verify TLS for a self-signed cert
5. Syncs DHCP / ARP / aliases / rules / NAT (base path `/api/v2`, `X-API-Key` auth). pfSense has its own settings page (not shared with OPNsense)

### Wazuh

1. Wazuh manager → API user (default `wazuh-wui` or your own)
2. jt-ipam → Wazuh → Add → fill in `https://wazuh:55000`, user, password
3. Click sync; afterwards the missing-agent page lists IPs without an agent

### LibreNMS

1. LibreNMS → API → generate a token
2. jt-ipam → LibreNMS → Add → fill in URL, token

### OIDC (Keycloak/Azure AD/Google)

Either configure it in the web UI (Admin → System Settings → Single sign-on (OIDC)), or add to `/etc/jt-ipam/backend.env`:

```
OIDC_ENABLED=true
OIDC_ISSUER=https://accounts.google.com
OIDC_CLIENT_ID=xxx
OIDC_CLIENT_SECRET=yyy
OIDC_REDIRECT_URI=https://ipam.example.com/api/v1/auth/oidc/callback
OIDC_ADMIN_GROUPS=jt-ipam-admins
```

`systemctl restart jt-ipam-backend`. The login page shows a "Sign in with OIDC" button.

### SAML (AD FS / Shibboleth)

```
SAML_ENABLED=true
SAML_IDP_METADATA_URL=https://idp.example.com/FederationMetadata.xml
SAML_ADMIN_GROUPS=jt-ipam-admins
```

Or for offline environments use `SAML_IDP_METADATA_XML="<EntityDescriptor>...</EntityDescriptor>"`.
After restart, register the SP metadata with the IdP: `curl https://ipam.example.com/api/v1/auth/saml/metadata`.

---

## 5. Backup & restore

### Recog fingerprint database (optional)

The IP probe matches what a host says about itself (SSH banners, HTTP `Server` headers, page titles, TLS
certificates, SMB OS strings) against [Recog](https://github.com/rapid7/recog) (Rapid7, BSD-2-Clause) to
recognise devices (a vendor's default certificate, a management page) and exact OS versions. Without it the probe
still works; it just identifies less.

- `jt-ipam.sh install` and `upgrade` download the latest release (outbound HTTPS to `api.github.com`,
  `github.com` and `release-assets.githubusercontent.com`). A failure only prints a warning.
- `jt-ipam-recog-refresh.timer` checks for a new release every Monday (Recog releases every one to six weeks)
  and downloads only when there is one. **Version info** shows the installed release, the last check and a
  **Check for updates now** button; the system diagnostics warn when it is missing or has not updated for
  three weeks.
- Offline hosts: download `recog-content-<version>.zip` from the
  [releases page](https://github.com/rapid7/recog/releases), then
  `sudo /opt/jt-ipam/scripts/jt-ipam.sh upgrade --recog-zip <file>` (or `install --recog-zip <file>`).

### External DNS resolution (optional)

The FQDN tab of **Exposed services** can show what each name resolves to on the internet. Set it up under
Admin → System Settings, in the **DNS resolvers** card: one or more resolvers (for example 1.1.1.1), the public
domains to check, and optionally your public ranges. Only names under those domains are sent out. The server needs
outbound UDP and TCP port 53 to the resolvers, and to the domains' authoritative servers when that option is on. No
extra package is needed.

### LLM / AI (optional)

AI chat and semantic search run against a **self-hosted Ollama** by default, so data stays on your own network. The installer does not install the LLM server; it usually lives on a separate machine with a GPU.

```bash
# on the LLM server (example)
ollama pull gemma4:26b                 # chat model
ollama pull granite-embedding:278m     # embedding model (768-dim, multilingual)
```

Then fill in the URL and the two models under **Admin → LLM / AI**, press **Check dimension** to confirm they match, and press **Rebuild index** to populate vectors for existing records.

> **Read this if you are upgrading.** An upgrade does **not** repair semantic search on its own; three things need a hand:
> 1. The shipped default is now `granite-embedding:278m`, but that only applies **if you never saved an embedding model on the settings page**; a value in the database wins, and yours is probably the old one.
> 2. The new model has to be pulled on your LLM server yourself.
> 3. Existing records only get vectors once you press **Rebuild index**.
>
> The settings page probes the dimension when it loads and says so if they do not match, which is precisely what the old behaviour lacked: a mismatch produced no error at all, only "semantic search never returns anything".

> ⚠️ **The embedding model's output dimension must equal `EMBEDDING_DIM` (768 by default)**, which is the size of the database's `vector(N)` column. A mismatch produces **no error message at all**; the only symptom is that semantic search never returns anything, because every index write failed. After changing the embedding model, press **Check dimension** on the settings page: it reports what the model returned versus what the column holds.
>
> Also note that **English-only embedding models (such as `nomic-embed-text`) collapse different non-Latin descriptions into the same vector**. The dimension looks right and search returns results, but the ranking is meaningless. Prefer a multilingual model, and compare a few of your own descriptions to confirm they produce different results.

You can point it at an external OpenAI-compatible endpoint instead (ChatGPT, vLLM, LM Studio, OpenRouter…) by choosing "OpenAI-compatible" and supplying an API key (stored AES-GCM encrypted). **Your subnets, hostnames and topology are then sent to that provider**; keep the self-hosted Ollama if data must not leave your network.

### Automatic backup

The installer installs and enables the daily backup (`jt-ipam-backup.timer`), and upgrades refresh the backup script. If you did not use the installer, add it by hand:

```bash
sudo cp /opt/jt-ipam/scripts/jt-ipam-backup.sh /usr/local/bin/
sudo install -m 0644 /opt/jt-ipam/deploy/systemd/jt-ipam-backup.{service,timer} \
    /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now jt-ipam-backup.timer
```

Runs daily at 03:30 by default, packaging `pg_dump -Fc` + `/etc/jt-ipam/backend.env` + TLS certs
into `/var/backups/jt-ipam/`, retained for 14 days.

Every run, successful or not, writes `/var/backups/jt-ipam/last-run`: the result, the time, the last successful time and the reason for a failure. "Daily backup" on the System diagnostics page and `sudo /opt/jt-ipam/scripts/jt-ipam.sh doctor` both read it; a failed backup, or no success for 48 hours, turns the check red and sends a system alert to admins. The most common failure is a table in the database not owned by the jt-ipam role (for example a copy someone left behind): pg_dump cannot read it and stops; the diagnostics page shows the command to run.

### Backup encryption

The backup directory holds the database dump **and** `backend.env`, which contains the key that decrypts the secrets in that dump. Set a backup passphrase under **Admin → System Settings → Daily backup encryption** (at least 12 characters): from then on each day's backup is packed and encrypted into one file, `/var/backups/jt-ipam/jt-ipam-<date>.jtbak` (scrypt-derived key, AES-256-GCM in 1 MiB chunks; a truncated, reordered or modified file fails to decrypt), and the plain directory is removed. `last-run` records `encrypted=1`; while no passphrase is set, the diagnostics page and `doctor` warn.

Keep the passphrase somewhere other than this host (a password manager): without it the backups cannot be decrypted. After changing it, older backups still need the passphrase they were made with. The decrypt tool needs only Python 3 and `cryptography` (`apt install python3-cryptography`), so it also works on a fresh machine before jt-ipam is reinstalled:

```bash
python3 /opt/jt-ipam/backend/app/services/backup_crypt.py decrypt \
    /var/backups/jt-ipam/jt-ipam-2026-10-09.jtbak --out /root/jt-ipam-restore
# → /root/jt-ipam-restore/2026-10-09/ (dump, backend.env, tls.tar.gz, uploads.tar.gz)
```

### Offsite backup

rsync `/var/backups/jt-ipam/` to a NAS / S3 / another machine:

```bash
# e.g. push to NAS daily at 04:00
0 4 * * * rsync -a /var/backups/jt-ipam/ jtipam@nas.local:/backups/jt-ipam/
```

### Restore

```bash
# 0. stop services
sudo systemctl stop jt-ipam-backend jt-ipam-sync.timer jt-ipam-ai-audit.timer

# 0b. encrypted backup (.jtbak): decrypt it first (asks for the backup passphrase)
python3 /opt/jt-ipam/backend/app/services/backup_crypt.py decrypt \
    /var/backups/jt-ipam/jt-ipam-2026-05-10.jtbak --out /root/jt-ipam-restore
# then use /root/jt-ipam-restore/2026-05-10/ in place of /var/backups/jt-ipam/2026-05-10/ below

# 1. recreate an empty DB
sudo -u postgres dropdb jt_ipam
sudo -u postgres createdb -O jt_ipam jt_ipam
sudo -u postgres psql -d jt_ipam -c '
    CREATE EXTENSION IF NOT EXISTS pgcrypto;
    CREATE EXTENSION IF NOT EXISTS citext;
    CREATE EXTENSION IF NOT EXISTS pg_trgm;
    CREATE EXTENSION IF NOT EXISTS btree_gist;
    CREATE EXTENSION IF NOT EXISTS vector;
'

# 2. restore the dump (note: you MUST use the same ENCRYPTION_KEY to decrypt sensitive fields like DNS/API credentials)
sudo -u postgres pg_restore -d jt_ipam \
    /var/backups/jt-ipam/2026-05-10/jt-ipam-2026-05-10.dump

# 3. restore the config file (if still present)
sudo cp /var/backups/jt-ipam/2026-05-10/backend.env /etc/jt-ipam/

# 4. give the audit table back to its NOLOGIN owner (the app role may only read and append),
#    then start
sudo bash /opt/jt-ipam/scripts/jt-ipam.sh harden-audit
sudo systemctl start jt-ipam-backend jt-ipam-sync.timer jt-ipam-ai-audit.timer

# 5. verify the chain (any tampered row shows up immediately)
curl -X POST https://ipam.example.com/api/v1/audit/verify \
    -H "Authorization: Bearer <admin token>"
```

> Backup files contain sensitive data (the DB holds encrypted API credentials; the env holds SECRET_KEY/ENCRYPTION_KEY).
> Set a backup encryption passphrase (above), store them with `0600` permissions and transfer encrypted (rsync over ssh / S3 server-side encryption).

---

## 6. Upgrade

One-shot upgrade via the unified script (git pull → backup → pip → stop writes → baseline inventory → alembic → per-record check → build → restart):

```bash
sudo bash /opt/jt-ipam/scripts/jt-ipam.sh upgrade
# already pulled, skip git pull:  sudo bash /opt/jt-ipam/scripts/jt-ipam.sh upgrade --no-pull
```

The equivalent manual steps (run individually if needed):

```bash
cd /opt/jt-ipam
git pull
sudo systemctl stop jt-ipam-backend
sudo -u jtipam /opt/jt-ipam/backend/.venv/bin/pip install -e backend
sudo -u jtipam bash -c 'cd /opt/jt-ipam/backend; set -a; . /etc/jt-ipam/backend.env; set +a; .venv/bin/python -m app.cli.upgrade_verify snapshot --out /var/lib/jt-ipam/upgrade-checks/manual/before.json'
sudo -u jtipam /opt/jt-ipam/backend/.venv/bin/alembic -c backend/alembic.ini upgrade head
sudo -u jtipam bash -c 'cd /opt/jt-ipam/backend; set -a; . /etc/jt-ipam/backend.env; set +a; .venv/bin/python -m app.cli.upgrade_verify compare --before /var/lib/jt-ipam/upgrade-checks/manual/before.json --report /var/lib/jt-ipam/upgrade-checks/manual/report.json'
cd frontend && sudo -u jtipam pnpm install --frozen-lockfile && sudo -u jtipam pnpm run build
sudo systemctl start jt-ipam-backend
```

**Data check.** An in-place upgrade must not lose existing data. Before the migration the script stops writes (the backend and the jt-ipam timers) and records every location, rack, device placement, power and cabling record, the device–IP links and the permissions; after the migration it compares them **record by record and field by field** (not just counts; floor-plan files by checksum). Data problems that already existed are reported separately; only new ones fail. If anything is missing or changed the script stops, prints how to restore the backup it took, and **leaves the backend and timers stopped** so nothing writes on top of a damaged database. The inventory and the report (`/var/lib/jt-ipam/upgrade-checks/<time>/`) contain site data and are readable by the service account only (0600).

---

## 7. Monitoring & alerting

### Journal

```bash
journalctl -u jt-ipam-backend -f          # backend
journalctl -u jt-ipam-sync -n 200          # periodic sync
journalctl -u jt-ipam-ai-audit -n 200      # scheduled AI review
journalctl -u jt-ipam-backup -n 50         # backup
```

### Healthcheck

`https://<your-fqdn>/api/v1/healthz` returning 200 = OK.

### Push to SIEM/Slack

The backend supports webhook subscriptions: admin → Settings → notifications, add a
webhook URL. You can also add a Graylog GELF endpoint in the `BACKEND_*` env to forward
all audit logs.

---

## 8. Uninstall

Unified-script uninstall (by default only stops services, removes systemd units/timers + the nginx site, and **keeps DB / config / source**):

```bash
sudo bash /opt/jt-ipam/scripts/jt-ipam.sh uninstall
# also drop DB / config / uploads / system user (asks to confirm; --yes skips the prompt):
sudo bash /opt/jt-ipam/scripts/jt-ipam.sh uninstall --purge
```

> `uninstall` never deletes the `/opt/jt-ipam` source.

The equivalent manual steps:

```bash
sudo systemctl disable --now jt-ipam-backend jt-ipam-sync.timer jt-ipam-ai-audit.timer jt-ipam-backup.timer
sudo rm /etc/systemd/system/jt-ipam-{backend,sync,backup}.{service,timer}
sudo rm -rf /etc/jt-ipam /var/log/jt-ipam /var/lib/jt-ipam
sudo -u postgres dropdb jt_ipam
sudo -u postgres dropuser jt_ipam
sudo userdel -r jtipam
```

Decide for yourself whether to keep the backups (`/var/backups/jt-ipam/`).

---

## 9. FAQ

**Q: I installed with `--tls-mode self-signed`. Do I still need nginx or apache?**
A: **No.** In this mode uvicorn terminates TLS itself; the install is already a complete HTTPS
service with no web server involved. The URL is `https://<your-fqdn>:8443/` (**8443 by default,
not 443**). To confirm:

```bash
sudo systemctl status jt-ipam-backend        # expect active (running)
sudo ss -ltnp | grep 8443                    # expect uvicorn listening
curl -kI https://127.0.0.1:8443/             # expect HTTP/1.1 200
```

To move to 443, or to add nginx later, see 2.6 "Which port" and "Do I need nginx".

**Q: The install stopped at `extension "vector" is not available`, but postgresql-16-pgvector is installed?**
A: This host most likely **already runs a PostgreSQL cluster** (from SonarQube, GitLab, …).
jt-ipam connects to `127.0.0.1:5432`, i.e. that existing cluster, so pgvector has to be installed
for **its** major version; installing it for another version has no effect:

```bash
sudo -u postgres psql -tAc 'SHOW server_version_num'   # e.g. 180004 → major 18
sudo apt install -y postgresql-18-pgvector
sudo -u postgres psql -d jt_ipam -c 'CREATE EXTENSION IF NOT EXISTS vector;'
```

> Since v0.5.161 the installer detects the running cluster's version, installs the matching
> pgvector, no longer pulls a second server package (which would create a second cluster), and
> stops with a readable message if extension creation fails instead of surfacing it later as an
> alembic traceback.

**Q: The install stopped at `/usr/local/bin/pnpm: No such file or directory`?**
A: pnpm failed to install and the older script discarded npm's error. Install it and re-run
(the installer is safe to re-run; completed steps are skipped):

```bash
sudo npm install -g pnpm@9      # or: curl -fsSL https://get.pnpm.io/install.sh | sh -
sudo ./scripts/jt-ipam.sh install --tls-mode self-signed --public-fqdn ipam.example.com
```

> Since v0.5.161 the script keeps npm's output, tries three ways of getting pnpm, and verifies
> `pnpm --version` runs before continuing.

**Q: The installer said "Done" but nothing is running?**
A: Since v0.5.161 the installer self-checks before saying so (env file, frontend dist, service
state, listening port, and nginx in nginx mode) and lists whatever is missing. On older versions,
check by hand:

```bash
ls -l /etc/jt-ipam/backend.env                 # configuration present?
ls -l /opt/jt-ipam/frontend/dist/index.html    # frontend built?
sudo systemctl status jt-ipam-backend
sudo journalctl -u jt-ipam-backend -n 50 --no-pager
```

**Q: backend won't start, journal shows "ENCRYPTION_KEY: invalid format"?**
A: ENCRYPTION_KEY must be 32-byte base64 (44 chars ending in `=`). The installer generates it;
if setting manually, use `python -c 'import base64,os; print(base64.b64encode(os.urandom(32)).decode())'`.

**Q: backend OOM?**
A: argon2id defaults to 64 MiB / 4 parallelism; if RAM is tight (< 2GB), lower `ARGON2_MEMORY_COST_KIB=32768`.

**Q: nginx 502?**
A: the backend binds `127.0.0.1:8000` by default; confirm `systemctl status jt-ipam-backend`
is active and that the nginx site's upstream also points at `127.0.0.1:8000`.

**Q: pgvector not found?**
A: Ubuntu 22.04 doesn't bundle it; the installer auto-adds the PGDG repo + `postgresql-16-pgvector`.
Manual fix: `sudo apt install postgresql-16-pgvector` then `CREATE EXTENSION vector;`.

**Q: `/api/v1/audit/verify` returns `{"ok": false}` chain broken?**
A: Known historical bug: before v0.3.0 nginx passed its `$request_id` (32-hex without hyphens)
to the backend, which wrote it into the audit canonical, but PG read it back as a hyphenated UUID,
so verify mismatched. 0.3.1+ middleware normalizes it (see `app/core/middleware.py`
`RequestIDMiddleware`). If your old chain is already broken and the data isn't in production yet:
```bash
# reset the chain (wipes all audit_logs; only do this on a non-live environment)
sudo -u postgres psql -d jt_ipam -c "TRUNCATE audit_logs RESTART IDENTITY;"
sudo systemctl restart jt-ipam-backend
```

**Q: the installer warns `nginx: ssl_stapling ignored, issuer certificate not found`?**
A: self-signed certs have no issuer chain so OCSP stapling can't be used, which is harmless. It disappears once you switch to a real cert or Let's Encrypt.

**Q: integration tests need `JTIPAM_TEST_DATABASE_URL`; does production too?**
A: No. Production only needs `POSTGRES_*` in `backend.env`. `JTIPAM_TEST_DATABASE_URL` is just a separate DB for pytest (to avoid polluting prod data).

**Q: after install, IP-only access shows "Welcome to nginx" instead of jt-ipam?**
A: Known issue (before 0.3.1): apt enables nginx's `default` site, which grabs IP-only access. 0.3.1+ `install-debian.sh` will: (1) auto `rm /etc/nginx/sites-enabled/default`, (2) add `listen ... default_server` to the jt-ipam site, so IP / FQDN / any hostname goes to jt-ipam. On an old machine, do it manually:
```bash
sudo rm /etc/nginx/sites-enabled/default
sudo sed -i 's|listen 80;|listen 80 default_server;|; s|listen 443 ssl http2;|listen 443 ssl http2 default_server;|' /etc/nginx/sites-available/jt-ipam
sudo nginx -t && sudo systemctl reload nginx
```

**Q: what are the default admin credentials? What if I forget?**
A: **There are no default credentials.** After install you must bootstrap manually (see §2.3); the password is randomly generated by `openssl rand` and **printed only once at bootstrap**, so save it. If forgotten or to change it:
```bash
# Option A: create another admin (if the original admin still works, have another admin change the password from UI /users)
ADMIN_PW=$(openssl rand -base64 24)
sudo -u jtipam env $(grep -v '^#' /etc/jt-ipam/backend.env | xargs) \
    /opt/jt-ipam/backend/.venv/bin/python -m app.cli.bootstrap create-admin \
    --username admin2 --email admin2@your.domain --password-stdin <<<"$ADMIN_PW"
echo "$ADMIN_PW"

# Option B: original admin locked out / lost; edit the DB directly to unlock and reset the password
sudo -u jtipam env $(grep -v '^#' /etc/jt-ipam/backend.env | xargs) \
    /opt/jt-ipam/backend/.venv/bin/python -c '
import asyncio, sys
from app.core.db import SessionLocal
from app.core.security import hash_password
from app.models.user import User
from sqlalchemy import select

async def main(username, new_pwd):
    async with SessionLocal() as s:
        u = (await s.execute(select(User).where(User.username == username))).scalar_one()
        u.password_hash = hash_password(new_pwd)
        u.locked_until = None
        u.failed_login_count = 0
        u.is_active = True
        u.is_admin = True
        await s.commit()
        print(f"reset {u.username}")

asyncio.run(main(sys.argv[1], sys.argv[2]))
' admin "MyNewPassword2026!"
```

**Q: `Node.js install failed or too old (need >= 22)`, or the upgrade prints a "Node.js 22 could not be installed" banner?**
A: Node.js 22 LTS is only used to build the frontend (Node 20 reached end of life on 2026-04-30). `jt-ipam.sh`
installs it from NodeSource (`setup_22.x`) on install and on upgrade, so normally there is nothing to do. When the
host cannot install it (no access to deb.nodesource.com, a proxy, an apt conflict), `install` stops, while `upgrade`
keeps an existing Node 20 or newer, builds with it and finishes with that banner; pnpm then also prints
`Unsupported engine: wanted: {"node":">=22"}`, and `doctor` keeps reporting the old Node. Install it by hand and
re-run the same command:
```bash
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo bash -
sudo apt install -y nodejs
node -v      # v22.x
sudo bash /opt/jt-ipam/scripts/jt-ipam.sh upgrade      # or install
```
If apt reports a conflict with the distro's own packages (`trying to overwrite ... libnode-dev`), remove them first:
`sudo apt-get purge -y nodejs libnode-dev`. With nvm, an nvm Node 22 of the user who runs `sudo` is used as is;
an older nvm Node is ignored.

## RDP console engines

The browser RDP console can use one of three engines, selected under **Admin → System Settings**:

- **guacd** (default, required) -- see the next section. The most compatible, and no virtual display.
- **aardwolf** (optional) -- a pure-Python client, now only a fallback used while guacd is down. It
  ships prebuilt wheels only up to Python 3.13 and crashes on 3.14 (GitHub issue #42), so it is
  installed where it can be and nothing depends on it.
- **FreeRDP** -- broader compatibility than aardwolf. Linux RDP servers (xrdp, and the GNOME "Remote Login"
  that Ubuntu 24 ships) reject aardwolf's NTLM authentication, because the library does not send
  the message integrity code that those servers require. FreeRDP authenticates against them.

FreeRDP needs packages that are **not installed by default**, because most sites never switch:

```
sudo apt-get install -y freerdp2-x11 xvfb xclip ffmpeg
```

or pass `--with-freerdp` to `jt-ipam.sh install`. An upgrade installs them automatically if the
site has already selected the FreeRDP engine. `jt-ipam.sh doctor` reports which engine is in use
and whether its packages are present, and the settings page shows the same thing with the exact
command to run.

ffmpeg is there for screen capture, not video: reading the framebuffer any other way we measured
costs 334 ms per frame, which caps the console at under 3 fps.

## guacd console engine (default for RDP / VNC)

guacd is the server side of [Apache Guacamole](https://guacamole.apache.org/). It is the
**default engine for the RDP and VNC consoles** and a **required component** (since 0.6.49;
upgrading switches existing installs to it too), and SSH can be switched to it under
**Admin → System Settings**. `install` puts it in place and stops if it cannot; `upgrade`
installs it when missing. While guacd is down, RDP and VNC fall back to the optional built-in
engine (aardwolf) where that is installed, and `doctor`, **Admin → System check** and
**Version info -> Required components** report the problem.

Why a separate install: Debian no longer ships guacd, and Ubuntu only has 1.3.0, which has known
remote-code-execution bugs. So jt-ipam provides its own build for each supported OS version
(Debian 12/13, Ubuntu 22.04/24.04/26.04). Each build links against the OS's own libraries, so
FreeRDP, libvncclient and the rest keep getting security fixes from apt.

```
sudo /opt/jt-ipam/scripts/jt-ipam.sh install                     # guacd is included
sudo /opt/jt-ipam/scripts/jt-ipam.sh upgrade                     # installs it if missing, keeps it current
sudo /opt/jt-ipam/scripts/jt-ipam.sh upgrade --guacd-tarball ./jt-ipam-guacd-...-ubuntu24.04-amd64.tar.gz
                                                                 # offline: the .deps file must sit next to it
```

- It runs as the `jt-ipam-guacd` systemd service, **bound to 127.0.0.1:4822 only**. guacd itself
  has no authentication, so it must never listen on another interface. The service uses a dynamic
  user with no privileges.
- Every `upgrade` keeps it on the build that matches that jt-ipam version. Downloads are checked
  against `scripts/guacd/SHA256SUMS`.
- Credentials are passed to guacd by the server; they never reach the browser. SSH host keys are
  still confirmed the first time and pinned, and guacd must match the pinned key.
- SSH through guacd: the terminal is drawn on the server. Copy and paste with Ctrl+Shift+C /
  Ctrl+Shift+V (⌘C / ⌘V on a Mac). Chinese/Japanese input methods work.
- `jt-ipam.sh doctor` and **Admin → System check** report whether guacd is running and whether each
  protocol set to it is supported.

Licensing: guacd is Apache-2.0, and each package includes its LICENSE, NOTICE and a SOURCE file
naming the exact source and build scripts. These builds are made by jt-ipam; they are not
official Apache releases.
