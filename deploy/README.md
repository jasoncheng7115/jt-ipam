# jt-ipam deployment

> 繁體中文：[README_zh-TW.md](README_zh-TW.md)

> The primary deployment does not use Docker (an optional docker-compose path is in [`docker/`](docker/README.md)). Default target: **Proxmox LXC** (Debian 12 / Ubuntu 24.04) or bare metal.

---

## 1. Quick install (single host / LXC)

### 1.1 System requirements

- Debian 12 / Ubuntu 22.04+ (a Proxmox LXC template is fine)
- 2 vCPU / 4 GB RAM / 20 GB disk (minimum)
- Python 3.12, PostgreSQL 16, Redis 7, Node 20+
- **TLS enforced** (pick one of two modes, see §1.3)

### 1.2 One-click install

The script supports `--tls-mode {nginx|direct|self-signed}`:

```bash
# Mode A: nginx reverse-proxy HTTPS (recommended; public service / existing FQDN)
sudo ./scripts/install-debian.sh \
    --tls-mode nginx \
    --public-fqdn ipam.your-domain.tld

# Mode B: backend uvicorn directly serves a self-signed certificate (minimal / intranet / no FQDN)
sudo ./scripts/install-debian.sh \
    --tls-mode self-signed \
    --public-fqdn ipam.local \
    --bind-port 8443
```

The script automatically:

1. `apt install postgresql-16 redis-server python3.12 nginx*¹ pnpm` and other dependencies
2. Creates the system user `jtipam` (no shell)
3. Creates the PostgreSQL role + DB (SCRAM-SHA-256; password auto-generated)
4. Configures Redis `requirepass`
5. Builds the Python venv + installs the backend
6. Auto-generates `SECRET_KEY` / `ENCRYPTION_KEY` / `AUDIT_CHAIN_GENESIS`
7. Runs `alembic upgrade head`
8. `pnpm build` the frontend into `frontend/dist`
9. **Generates `/etc/jt-ipam/backend.env` according to the TLS mode**
10. Installs the systemd unit: `jt-ipam-backend.service`
11. Mode A: installs the nginx site; Mode B: generates a self-signed certificate

> *¹ nginx is only installed in Mode A; not installed in B.

When finished:

```bash
systemctl status jt-ipam-backend
# Mode A
curl -fsS http://127.0.0.1:8000/healthz
# Mode B
curl -fsSk https://127.0.0.1:8443/healthz
```

### 1.3 The two TLS modes in detail

#### Mode A: nginx reverse-proxy HTTPS (recommended, default)

Architecture:

```
browser ── HTTPS ──► nginx :443 ── HTTP ──► uvicorn 127.0.0.1:8000
            ▲
            │ certificate goes here (Let's Encrypt / intranet CA / self-signed)
            └─ /etc/letsencrypt/... or /etc/ssl/...
```

`/etc/jt-ipam/backend.env`:

```ini
BACKEND_TLS_MODE=nginx
BACKEND_BIND_HOST=127.0.0.1
BACKEND_BIND_PORT=8000
APP_PUBLIC_URL=https://ipam.your-domain.tld
API_PUBLIC_URL=https://ipam.your-domain.tld
CORS_ORIGINS=https://ipam.your-domain.tld
```

Obtain a certificate (public internet):

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d ipam.your-domain.tld
sudo systemctl reload nginx
```

Or an intranet self-signed certificate (for nginx):

```bash
sudo ./scripts/generate-self-signed-cert.sh \
    --out-dir /etc/jt-ipam/tls \
    --cn ipam.local \
    --san "DNS:ipam.local,IP:192.168.1.10"

# Edit /etc/nginx/sites-available/jt-ipam and change the ssl_certificate paths to:
#   ssl_certificate     /etc/jt-ipam/tls/server.crt;
#   ssl_certificate_key /etc/jt-ipam/tls/server.key;
sudo nginx -t && sudo systemctl reload nginx
```

Pros: mature, good performance, can attach ACME auto-renewal, can add HTTP/2 / HTTP/3, can host multiple sites at once.
Cons: one more component to operate.

---

#### Mode B: backend direct TLS + self-signed certificate

Architecture:

```
browser ── HTTPS ──► uvicorn 0.0.0.0:8443
                       │
                       └─ serves cert/key directly
                          /etc/jt-ipam/tls/server.{crt,key}
```

`/etc/jt-ipam/backend.env`:

```ini
BACKEND_TLS_MODE=direct
BACKEND_BIND_HOST=0.0.0.0
BACKEND_BIND_PORT=8443
BACKEND_TLS_CERT_FILE=/etc/jt-ipam/tls/server.crt
BACKEND_TLS_KEY_FILE=/etc/jt-ipam/tls/server.key
APP_PUBLIC_URL=https://ipam.local:8443
API_PUBLIC_URL=https://ipam.local:8443
CORS_ORIGINS=https://ipam.local:8443
```

Generate / reissue a self-signed certificate (ECDSA P-384, 5 years; auto-detects hostname / IP and adds them to the SAN):

```bash
sudo ./scripts/generate-self-signed-cert.sh \
    --out-dir /etc/jt-ipam/tls \
    --cn ipam.local \
    --san "DNS:ipam.local,DNS:ipam,IP:192.168.1.10" \
    --days 1825 \
    --owner root:jtipam
```

Verify:

```bash
openssl x509 -in /etc/jt-ipam/tls/server.crt -noout -text \
    | grep -E 'Subject:|DNS:|IP Address:|Not After'
```

Want to use 443 instead of 8443? The default systemd unit has `CapabilityBoundingSet=` fully dropped (A05 hardening), so if the browser should reach `https://host/` instead of `:8443`, the backend needs `CAP_NET_BIND_SERVICE`:

```bash
sudo systemctl edit jt-ipam-backend
# Paste the contents of deploy/systemd/override-direct-tls-port443.conf.example here
sudo systemctl daemon-reload
sudo systemctl restart jt-ipam-backend
```

> **Note**: a self-signed certificate triggers a browser warning. Options: (1) import the CA / trust the certificate on the client side; (2) stand up a small intranet CA (step-ca, smallstep); (3) switch back to Mode A with Let's Encrypt.

Pros: one fewer component, simpler configuration, suitable for small LXC deployments.
Cons: HTTP/2 / HTTP/3 / multiple sites are inconvenient, self-signed trust issues, performance not as good as nginx.

---

### 1.4 Switching modes

To change mode later, **no reinstall is needed**:

1. Edit `/etc/jt-ipam/backend.env`, update `BACKEND_TLS_MODE` / related fields
2. Install / remove the nginx site as appropriate
3. `sudo systemctl restart jt-ipam-backend`

---

## 2. Directory and file layout

```
/opt/jt-ipam/                      # source code (git checkout)
├── backend/
│   ├── .venv/                     # Python venv (owned by jtipam)
│   └── ...
└── frontend/
    └── dist/                      # static files after vite build (nginx points here)

/etc/jt-ipam/
├── backend.env                    # application config (includes secrets, 0640 root:jtipam)
├── .db-password                   # generated by the script (0600 root:root)
└── .redis-password                # generated by the script (0600 root:root)

/etc/systemd/system/
└── jt-ipam-backend.service

/etc/nginx/
├── sites-available/jt-ipam
├── sites-enabled/jt-ipam → ../sites-available/jt-ipam
└── snippets/jt-ipam-proxy.conf

/var/lib/jt-ipam/                  # state (StateDirectory; jtipam:jtipam)
/var/log/jt-ipam/                  # log (LogsDirectory; jtipam:jtipam)
```

---

## 3. systemd hardening (OWASP A05)

`jt-ipam-backend.service` already applies by default:

| Directive | Purpose |
|---|---|
| `User=jtipam` / `Group=jtipam` | Runs as non-root |
| `NoNewPrivileges=true` | Blocks setuid / setcap privilege escalation |
| `ProtectSystem=strict` | Entire `/` is read-only, only ReadWritePaths are writable |
| `ProtectHome=true` | Cannot see user homes |
| `PrivateTmp=true` | Isolates `/tmp` |
| `PrivateDevices=true` | Hides devices except `/dev/null` and similar |
| `ProtectKernelTunables/Modules/Logs` | Cannot change sysctl / load modules |
| `RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6` | Only IP sockets allowed |
| `RestrictNamespaces=true` | Blocks container/namespace operations |
| `MemoryDenyWriteExecute=true` | Blocks W^X violations |
| `SystemCallFilter=@system-service` | seccomp allowlist |
| `CapabilityBoundingSet=` | All capabilities dropped |
| `LimitNOFILE=65536` / `TasksMax=1024` | Resource limits |

Verify:

```bash
sudo systemd-analyze security jt-ipam-backend
# Expected score ≤ 3.5 (OK); a score > 5.0 means hardening is not in effect
```

---

## 4. Upgrade procedure

```bash
cd /opt/jt-ipam
sudo -u jtipam git pull --ff-only

# Backend dependencies
sudo -u jtipam backend/.venv/bin/pip install -e backend

# DB migration (back up first)
sudo -u postgres pg_dump jt_ipam | gzip > /var/backups/jt-ipam-$(date +%F).sql.gz
sudo -u jtipam --preserve-env=PATH bash -c \
  'set -a; source /etc/jt-ipam/backend.env; set +a; cd backend && .venv/bin/alembic upgrade head'

# Frontend
cd /opt/jt-ipam/frontend
sudo -u jtipam pnpm install --frozen-lockfile
sudo -u jtipam pnpm build

# Restart
sudo systemctl restart jt-ipam-backend
sudo systemctl reload nginx
```

---

## 5. Backup and restore

### 5.1 Backup

```bash
# Daily (recommended to place in /etc/cron.daily/jt-ipam-backup)
pg_dump -U jt_ipam jt_ipam | zstd -19 > /var/backups/jt-ipam/db-$(date +%F).sql.zst

# /etc/jt-ipam (includes keys) — safe / Vault
tar czf - /etc/jt-ipam | gpg --encrypt --recipient backup@example.com \
    > /var/backups/jt-ipam/etc-$(date +%F).tar.gz.gpg
```

### 5.2 Restore

```bash
systemctl stop jt-ipam-backend
sudo -u postgres dropdb jt_ipam
sudo -u postgres createdb -O jt_ipam jt_ipam
zstdcat db-2026-05-09.sql.zst | sudo -u postgres psql jt_ipam
systemctl start jt-ipam-backend
```

> **Note (A02)**: the `ENCRYPTION_KEY` and `AUDIT_CHAIN_GENESIS` in `/etc/jt-ipam/backend.env` **must not be lost**, otherwise existing sensitive fields cannot be decrypted and the audit chain cannot be verified. Recommended to encrypt with GPG and store off-site (Proxmox Backup Server dual-site, Vault).

---

## 6. HA architecture (Phase 3+ plan)

> The first release only supports a single host. HA documentation will be completed in Phase 3; the framework is listed in advance:

- PostgreSQL **streaming replication** (pg-primary + pg-standby) + pgBouncer + patroni
- Redis **Sentinel** (3 nodes)
- Application layer with multiple replicas: `jt-ipam-backend.service` running on multiple LXCs, with a unified nginx upstream for the frontend
- Shared storage: the static frontend can be synced via rsync, or served uniformly from a CDN/object storage

---

## 7. Proxmox LXC template

(Planned) jt-ipam will provide a Proxmox LXC `.tar.zst` template:

- Debian 12 base
- PG 16, Redis 7, Python 3.12, nginx already installed
- On first boot, cloud-init runs `install-debian.sh`
- Default account `jtipam`, systemd already enabled

Download and deployment documentation will be released in late Phase 1.
