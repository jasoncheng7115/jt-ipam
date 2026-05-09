#!/usr/bin/env bash
# =============================================================================
# jt-ipam — Debian/Ubuntu 安裝腳本（適用於 Proxmox LXC 與裸機）
#
# 用法：
#   sudo ./scripts/install-debian.sh
#
# 行為：
#   1. apt 安裝：postgresql-16、redis-server、python3.12（venv）、nginx、build-essential
#   2. 建立系統使用者 jtipam（無 shell）
#   3. 設定 PostgreSQL 帳號 jt_ipam + DB jt_ipam
#   4. 設定 Redis（requirepass）
#   5. 建立 Python venv + pip install backend
#   6. 跑 alembic upgrade head
#   7. pnpm build frontend，把 dist 放到 /opt/jt-ipam/frontend/dist
#   8. 安裝 systemd unit + nginx site
#
# 安全考量（OWASP A02 / A05 / A07）：
#   * 自動產生 SECRET_KEY / ENCRYPTION_KEY / AUDIT_CHAIN_GENESIS / DB password
#   * /etc/jt-ipam/backend.env 權限 0640，owner root:jtipam
#   * Postgres 密碼使用 SCRAM-SHA-256
# =============================================================================
set -euo pipefail

# ── 必要檢查 ──
if [[ $EUID -ne 0 ]]; then
    echo "[error] 必須以 root 執行（sudo）" >&2
    exit 1
fi

if ! command -v lsb_release >/dev/null 2>&1; then
    apt-get update -qq
    apt-get install -y -qq lsb-release
fi

DISTRO=$(lsb_release -si)
if [[ "$DISTRO" != "Debian" && "$DISTRO" != "Ubuntu" ]]; then
    echo "[warn] 此腳本針對 Debian/Ubuntu；其他發行版請手動安裝" >&2
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ETC_DIR="/etc/jt-ipam"
BACKEND_DIR="${REPO_ROOT}/backend"
FRONTEND_DIR="${REPO_ROOT}/frontend"
JTIPAM_USER="jtipam"
JTIPAM_GROUP="jtipam"

log() { echo -e "\033[1;32m[install]\033[0m $*"; }
warn() { echo -e "\033[1;33m[warn]\033[0m $*" >&2; }

# ── 1. apt 套件 ──
log "Installing apt packages…"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
    postgresql-16 postgresql-contrib-16 \
    redis-server \
    python3.12 python3.12-venv python3.12-dev \
    build-essential libpq-dev pkg-config \
    nginx \
    curl ca-certificates gnupg \
    nodejs npm \
    || {
        # postgresql-16 在較舊 Ubuntu 需要加 PGDG repo
        warn "Falling back to PGDG repo for PostgreSQL 16…"
        install -d /usr/share/postgresql-common/pgdg
        curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
            | gpg --dearmor -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.gpg
        echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.gpg] \
              https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
            > /etc/apt/sources.list.d/pgdg.list
        apt-get update -qq
        apt-get install -y -qq postgresql-16 postgresql-contrib-16
    }

# corepack 啟用 pnpm（給 frontend build）
if ! command -v pnpm >/dev/null 2>&1; then
    log "Enabling corepack + pnpm…"
    corepack enable || npm install -g pnpm@9
    corepack prepare pnpm@9 --activate || true
fi

# ── 2. 系統使用者 ──
if ! id -u "$JTIPAM_USER" >/dev/null 2>&1; then
    log "Creating system user $JTIPAM_USER…"
    useradd --system --home-dir /var/lib/jt-ipam --shell /usr/sbin/nologin "$JTIPAM_USER"
fi

install -d -o "$JTIPAM_USER" -g "$JTIPAM_GROUP" -m 0750 \
    /var/lib/jt-ipam /var/log/jt-ipam
install -d -m 0755 "$ETC_DIR"

# ── 3. PostgreSQL ──
log "Configuring PostgreSQL…"
systemctl enable --now postgresql

# 啟用 SCRAM-SHA-256
PG_HBA="$(sudo -u postgres psql -tAc 'SHOW hba_file;')"
PG_CONF="$(sudo -u postgres psql -tAc 'SHOW config_file;')"
if ! grep -q "^password_encryption" "$PG_CONF"; then
    echo "password_encryption = scram-sha-256" >> "$PG_CONF"
fi

# 建立 role + DB（如果不存在）
DB_PASSWORD=""
if [[ -f "$ETC_DIR/.db-password" ]]; then
    DB_PASSWORD="$(cat "$ETC_DIR/.db-password")"
else
    DB_PASSWORD="$(openssl rand -base64 32 | tr -d '=+/')"
    install -m 0600 -o root -g root /dev/null "$ETC_DIR/.db-password"
    echo -n "$DB_PASSWORD" > "$ETC_DIR/.db-password"
fi

if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='jt_ipam'" | grep -q 1; then
    sudo -u postgres psql -c "CREATE ROLE jt_ipam LOGIN PASSWORD '${DB_PASSWORD}';"
fi
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='jt_ipam'" | grep -q 1; then
    sudo -u postgres createdb -O jt_ipam jt_ipam
fi

# 啟用必要 extension
sudo -u postgres psql -d jt_ipam <<'SQL'
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS btree_gist;
SQL

systemctl reload postgresql || systemctl restart postgresql

# ── 4. Redis ──
log "Configuring Redis…"
REDIS_PASSWORD=""
if [[ -f "$ETC_DIR/.redis-password" ]]; then
    REDIS_PASSWORD="$(cat "$ETC_DIR/.redis-password")"
else
    REDIS_PASSWORD="$(openssl rand -base64 32 | tr -d '=+/')"
    install -m 0600 -o root -g root /dev/null "$ETC_DIR/.redis-password"
    echo -n "$REDIS_PASSWORD" > "$ETC_DIR/.redis-password"
fi

# 設定 requirepass + bind 127.0.0.1
sed -i \
    -e "s/^# *requirepass .*/requirepass ${REDIS_PASSWORD}/" \
    -e "s/^requirepass .*/requirepass ${REDIS_PASSWORD}/" \
    -e "s/^bind .*/bind 127.0.0.1 ::1/" \
    /etc/redis/redis.conf

if ! grep -q "^requirepass" /etc/redis/redis.conf; then
    echo "requirepass ${REDIS_PASSWORD}" >> /etc/redis/redis.conf
fi

systemctl enable --now redis-server
systemctl restart redis-server

# ── 5. backend venv ──
log "Setting up backend venv…"
cd "$BACKEND_DIR"
sudo -u "$JTIPAM_USER" python3.12 -m venv .venv
sudo -u "$JTIPAM_USER" .venv/bin/pip install --upgrade pip wheel
sudo -u "$JTIPAM_USER" .venv/bin/pip install -e ".[dev]"

# ── 6. backend.env ──
log "Generating /etc/jt-ipam/backend.env…"
ENV_FILE="$ETC_DIR/backend.env"
if [[ ! -f "$ENV_FILE" ]]; then
    SECRET_KEY="$(openssl rand -hex 64)"
    ENCRYPTION_KEY="$(openssl rand -base64 32)"
    AUDIT_CHAIN_GENESIS="$(openssl rand -hex 64)"

    cat > "$ENV_FILE" <<EOF
# 自動產生 — $(date -Iseconds)
APP_ENV=production
APP_DEBUG=false
APP_LOG_LEVEL=INFO
APP_TIMEZONE=Asia/Taipei

APP_PUBLIC_URL=https://ipam.example.com
API_PUBLIC_URL=https://ipam.example.com
CORS_ORIGINS=https://ipam.example.com

SECRET_KEY=${SECRET_KEY}
ENCRYPTION_KEY=${ENCRYPTION_KEY}
AUDIT_CHAIN_GENESIS=${AUDIT_CHAIN_GENESIS}

ARGON2_TIME_COST=3
ARGON2_MEMORY_COST_KIB=65536
ARGON2_PARALLELISM=4

ACCESS_TOKEN_EXPIRE_MINUTES=15
REFRESH_TOKEN_EXPIRE_DAYS=14
SESSION_COOKIE_SECURE=true
SESSION_COOKIE_SAMESITE=lax

POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
POSTGRES_DB=jt_ipam
POSTGRES_USER=jt_ipam
POSTGRES_PASSWORD=${DB_PASSWORD}

REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_PASSWORD=${REDIS_PASSWORD}
REDIS_DB=0

RATE_LIMIT_DEFAULT=100/minute
RATE_LIMIT_AUTH=10/minute
RATE_LIMIT_API_TOKEN=600/minute

OUTBOUND_ALLOW_PRIVATE=true

VITE_DEFAULT_LOCALE=zh-TW
VITE_DEFAULT_THEME=auto
EOF
    chown root:"$JTIPAM_GROUP" "$ENV_FILE"
    chmod 0640 "$ENV_FILE"
    log "Wrote $ENV_FILE (secrets generated; review APP_PUBLIC_URL etc.)"
else
    warn "$ENV_FILE already exists; skipping (review manually)"
fi

# ── 7. alembic migrate ──
log "Running alembic migrations…"
cd "$BACKEND_DIR"
sudo -u "$JTIPAM_USER" --preserve-env=PATH \
    bash -c "set -a; source $ENV_FILE; set +a; .venv/bin/alembic upgrade head"

# ── 8. frontend build ──
log "Building frontend…"
cd "$FRONTEND_DIR"
sudo -u "$JTIPAM_USER" pnpm install --frozen-lockfile || sudo -u "$JTIPAM_USER" pnpm install
sudo -u "$JTIPAM_USER" pnpm build

# ── 9. systemd ──
log "Installing systemd units…"
install -m 0644 "$REPO_ROOT/deploy/systemd/jt-ipam-backend.service" \
    /etc/systemd/system/jt-ipam-backend.service
systemctl daemon-reload
systemctl enable --now jt-ipam-backend

# ── 10. nginx ──
log "Installing nginx site…"
install -m 0644 "$REPO_ROOT/deploy/nginx/jt-ipam-proxy.conf" \
    /etc/nginx/snippets/jt-ipam-proxy.conf
install -m 0644 "$REPO_ROOT/deploy/nginx/jt-ipam.conf" \
    /etc/nginx/sites-available/jt-ipam
ln -sf /etc/nginx/sites-available/jt-ipam /etc/nginx/sites-enabled/jt-ipam

if nginx -t; then
    systemctl reload nginx
else
    warn "nginx config test failed; review /etc/nginx/sites-available/jt-ipam"
fi

# ── Done ──
log "Done. Health: curl -fsS http://127.0.0.1:8000/healthz"
log "Edit /etc/jt-ipam/backend.env to set APP_PUBLIC_URL / TLS cert / etc."
log "Frontend served from /opt/jt-ipam/frontend/dist via nginx."
