# jt-ipam Install & Operations SOP

> 繁體中文版：[INSTALL_zh-TW.md](INSTALL_zh-TW.md)

For **Proxmox LXC, bare metal, and VMs** (Ubuntu 22.04+/Debian 12+). The **primary, recommended** install
uses **systemd + apt** directly (no Docker). A Docker Compose path exists but is **optional / secondary, not
the preferred mode** — see [§2.7](#27-optional-docker-compose-not-the-preferred-mode).

> Security is a day-one requirement: HTTPS is enforced in all environments; the cert can be
> served via an nginx reverse proxy or a self-signed cert served directly by uvicorn. If SSL
> isn't set up, the backend **will not start** (A02).

---

## 1. System requirements

| Item | Minimum | Recommended | Notes |
|---|---|---|---|
| OS | Ubuntu 22.04 / Debian 12 | **Ubuntu 24.04 LTS** | 24.04 ships Python 3.12 + PG 16 + Node 18, saving effort |
| CPU | 2 vCPU | 4 vCPU | argon2id + pgvector embeddings are CPU-heavy |
| RAM | 4 GB | 8 GB | add another 8 GB if running LLM Server |
| Disk | 20 GB | 50 GB | audit log grows |
| Python | 3.11 | 3.12 | 24.04 defaults to 3.12 |
| PostgreSQL | 16 + pgvector | — | 22.04 needs the PGDG repo (the script adds it automatically) |
| Redis | 7 | — | 24.04 defaults to 7.0.15 |
| Node | 20 LTS | 22 LTS | 24.04 defaults to 18.19; vite 6 runs but warns |

**Virtualization note**: on Proxmox VM / LXC, load avg may spike for 1-2 minutes right after boot/reboot (other VMs on the hypervisor contending for CPU — see `%steal` in `mpstat`); this isn't the VM itself being busy, you can just run the install.

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
curl -fsSL https://raw.githubusercontent.com/jasoncheng7115/jt-ipam/main/scripts/bootstrap.sh \
  | sudo bash -s -- --tls-mode nginx --public-fqdn ipam.example.com
```

Or clone manually and run the unified deploy script `scripts/jt-ipam.sh`:

```bash
git clone https://github.com/jasoncheng7115/jt-ipam.git /opt/jt-ipam
cd /opt/jt-ipam

# Pick one of three TLS modes:
#
#   nginx         — nginx terminates HTTPS, backend on loopback; auto self-signed bootstrap if cert missing
#   self-signed   — uvicorn direct with its own self-signed cert (no nginx; fastest to go live)
#   direct        — uvicorn direct, you provide the cert (falls back to self-signed if missing)

# (A) nginx + temporary self-signed (cp a real cert in later) — recommended for production
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
7. Install `jt-ipam-backend.service` + `jt-ipam-sync.timer` (every 5 min) + `jt-ipam-backup.timer` (daily 03:30)
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

To replace again later, just repeat the three steps above — no need to re-run install.

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

# confirm the new cert is live (in direct mode the backend listens on 443 directly)
openssl s_client -connect ipam.example.com:443 -servername ipam.example.com </dev/null 2>/dev/null \
    | openssl x509 -noout -issuer -subject -dates
```

> To regenerate a self-signed cert yourself: `sudo bash /opt/jt-ipam/scripts/generate-self-signed-cert.sh` then `systemctl restart jt-ipam-backend`.
> To move from direct to an nginx reverse proxy: set `BACKEND_TLS_MODE=nginx` in `/etc/jt-ipam/backend.env`, install the nginx site, then restart the backend + reload nginx.

### 2.7 Production standard: hardened nginx reverse proxy

For any internet-facing or production deployment, the **standard is to run jt-ipam behind the bundled
hardened nginx reverse proxy** (`--tls-mode nginx`, reference config `deploy/nginx/jt-ipam.conf`). nginx
terminates TLS, the backend stays bound to loopback, and the proxy enforces a strict security baseline so the
app server is never exposed directly:

- **TLS**: TLS 1.2/1.3 only, modern cipher suite, OCSP stapling, session tickets off.
- **HSTS**: `max-age` 2y + `includeSubDomains` + `preload`.
- **CSP**: `default-src 'self'`; `script-src 'self'`; `connect-src 'self'`; `frame-src 'self'`;
  `frame-ancestors 'none'`; `base-uri 'self'`; `form-action 'self'` — no third-party script/frame origins.
- **Headers**: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`,
  `Permissions-Policy` (geolocation/mic/camera/payment/usb off), `Cross-Origin-Opener-Policy` and
  `Cross-Origin-Resource-Policy: same-origin`.
- **No banner leak**: `server_tokens off` and the upstream (uvicorn) `Server`/`X-Powered-By` headers are
  hidden — no version or framework fingerprint.
- Backend listens on `127.0.0.1` only; nginx is the sole public listener.

> Do **not** expose uvicorn directly to the internet. `--tls-mode self-signed`/`direct` is for internal/dev.

> ### ⚠️ Required when you put your OWN reverse proxy in front (Mode C)
> The security headers above are applied by whatever nginx **terminates TLS at the public edge**. If you front
> jt-ipam with a separate reverse proxy (e.g. a company edge nginx / load balancer), **that proxy MUST set the
> security headers itself** — they will NOT automatically survive an extra hop, so the public site would ship
> with *no* CSP / HSTS / Permissions-Policy. This is a **required** part of the deployment, not optional.
>
> Install the bundled hardened external-proxy config on that edge box —
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
# Referrer-Policy, Permissions-Policy, COOP, CORP — each exactly ONCE, and Server: nginx (no version).
```

### 2.8 Optional: Docker Compose (NOT the preferred mode)

> ⚠️ **Docker Compose is a secondary / optional path — it is NOT the project's preferred or primary
> deployment mode.** The supported, recommended install is **systemd + apt** (sections above). Use Compose
> only for a quick evaluation or a container-first environment; the systemd path gets the most testing.

The files live in [`deploy/docker/`](https://github.com/jasoncheng7115/jt-ipam/tree/main/deploy/docker). One
compose file brings up `postgres` (pgvector), `redis`, `backend` (FastAPI/uvicorn), `sync` (a background sync
loop that replaces the systemd timer), and `web` (nginx serving the frontend + reverse-proxying `/api`, with a
self-signed HTTPS cert on first run).

```bash
# clone the repo first — gen-env.sh / docker-compose.yml live inside it under deploy/docker/
git clone https://github.com/jasoncheng7115/jt-ipam.git
cd jt-ipam/deploy/docker
./gen-env.sh                   # create .env with random secrets (once)
docker compose up -d --build   # build images and start the stack
# then open https://localhost  (self-signed cert on first run — trust the warning)
```

- **First admin:** `gen-env.sh` generates a random `admin` password (printed in its output, stored as
  `JT_IPAM_ADMIN_PASSWORD` in `.env`, mode 0600) and the backend creates the admin on first boot — change it
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
| `OUTBOUND_ALLOW_CIDRS` | — | safe_http SSRF allowlist; blank = public internet only |
| `OIDC_*` | — | enable OIDC SSO |
| `SAML_*` | — | enable SAML SSO |
| `LDAP_*` | — | LDAP/AD auth |
| `OLLAMA_ENABLED` | — | enable AI semantic search + chat |

See the Settings class in `app/core/config.py` for the full list.

---

## 4. Integration setup (after install)

All integrations are added in the admin UI (`/firewall`, `/wazuh`, `/librenms`, `/dns`).
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

Either configure it in the web UI (Admin → System Settings → Single sign-on — OIDC), or add to `/etc/jt-ipam/backend.env`:

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

### Automatic backup

The installer doesn't enable backups; add a cron or systemd timer manually. Simplest:

```bash
sudo cp /opt/jt-ipam/scripts/jt-ipam-backup.sh /usr/local/bin/
sudo install -m 0644 /opt/jt-ipam/deploy/systemd/jt-ipam-backup.{service,timer} \
    /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now jt-ipam-backup.timer
```

Runs daily at 03:30 by default, packaging `pg_dump -Fc` + `/etc/jt-ipam/backend.env` + TLS certs
into `/var/backups/jt-ipam/`, retained for 14 days.

### Offsite backup

rsync `/var/backups/jt-ipam/` to a NAS / S3 / another machine:

```bash
# e.g. push to NAS daily at 04:00
0 4 * * * rsync -a /var/backups/jt-ipam/ jtipam@nas.local:/backups/jt-ipam/
```

### Restore

```bash
# 0. stop services
sudo systemctl stop jt-ipam-backend jt-ipam-sync.timer

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
    /var/backups/jt-ipam/jt-ipam-2026-05-10.dump

# 3. restore the config file (if still present)
sudo cp /var/backups/jt-ipam/2026-05-10/backend.env /etc/jt-ipam/

# 4. start
sudo systemctl start jt-ipam-backend jt-ipam-sync.timer

# 5. verify the chain (any tampered row shows up immediately)
curl -X POST https://ipam.example.com/api/v1/audit/verify \
    -H "Authorization: Bearer <admin token>"
```

> Backup files contain sensitive data (the DB holds encrypted API credentials; the env holds SECRET_KEY/ENCRYPTION_KEY).
> Store them with `0600` permissions and transfer encrypted (rsync over ssh / S3 server-side encryption).

---

## 6. Upgrade

One-shot upgrade via the unified script (git pull → backup → pip → alembic → build → restart):

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
sudo -u jtipam /opt/jt-ipam/backend/.venv/bin/alembic -c backend/alembic.ini upgrade head
cd frontend && sudo -u jtipam pnpm install --frozen-lockfile && sudo -u jtipam pnpm run build
sudo systemctl start jt-ipam-backend
```

---

## 7. Monitoring & alerting

### Journal

```bash
journalctl -u jt-ipam-backend -f          # backend
journalctl -u jt-ipam-sync -n 200          # periodic sync
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
sudo systemctl disable --now jt-ipam-backend jt-ipam-sync.timer jt-ipam-backup.timer
sudo rm /etc/systemd/system/jt-ipam-{backend,sync,backup}.{service,timer}
sudo rm -rf /etc/jt-ipam /var/log/jt-ipam /var/lib/jt-ipam
sudo -u postgres dropdb jt_ipam
sudo -u postgres dropuser jt_ipam
sudo userdel -r jtipam
```

Decide for yourself whether to keep the backups (`/var/backups/jt-ipam/`).

---

## 9. FAQ

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
A: self-signed certs have no issuer chain so OCSP stapling can't be used — harmless. It disappears once you switch to a real cert or Let's Encrypt.

**Q: integration tests need `JTIPAM_TEST_DATABASE_URL` — does production too?**
A: No. Production only needs `POSTGRES_*` in `backend.env`. `JTIPAM_TEST_DATABASE_URL` is just a separate DB for pytest (to avoid polluting prod data).

**Q: after install, IP-only access shows "Welcome to nginx" instead of jt-ipam?**
A: Known issue (before 0.3.1): apt enables nginx's `default` site, which grabs IP-only access. 0.3.1+ `install-debian.sh` will: (1) auto `rm /etc/nginx/sites-enabled/default`, (2) add `listen ... default_server` to the jt-ipam site, so IP / FQDN / any hostname goes to jt-ipam. On an old machine, do it manually:
```bash
sudo rm /etc/nginx/sites-enabled/default
sudo sed -i 's|listen 80;|listen 80 default_server;|; s|listen 443 ssl http2;|listen 443 ssl http2 default_server;|' /etc/nginx/sites-available/jt-ipam
sudo nginx -t && sudo systemctl reload nginx
```

**Q: what are the default admin credentials? What if I forget?**
A: **There are no default credentials.** After install you must bootstrap manually (see §2.3); the password is randomly generated by `openssl rand` and **printed only once at bootstrap** — save it. If forgotten or to change it:
```bash
# Option A: create another admin (if the original admin still works, have another admin change the password from UI /users)
ADMIN_PW=$(openssl rand -base64 24)
sudo -u jtipam env $(grep -v '^#' /etc/jt-ipam/backend.env | xargs) \
    /opt/jt-ipam/backend/.venv/bin/python -m app.cli.bootstrap create-admin \
    --username admin2 --email admin2@your.domain --password-stdin <<<"$ADMIN_PW"
echo "$ADMIN_PW"

# Option B: original admin locked out / lost — edit the DB directly to unlock and reset the password
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

**Q: Ubuntu 24.04 frontend build shows `Unsupported engine: wanted Node >= 20`?**
A: 24.04 bundles nodejs 18; vite 6 / vue-tsc run but warn. To silence it: install Node 20+ via nvm / nodesource:
```bash
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo bash -
sudo apt install -y nodejs
```
then re-run `pnpm install && pnpm build` in `/opt/jt-ipam/frontend`.
