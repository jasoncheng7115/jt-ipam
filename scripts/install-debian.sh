#!/usr/bin/env bash
# =============================================================================
# jt-ipam — Debian/Ubuntu 安裝腳本（適用於 Proxmox LXC 與裸機）
#
# 用法：
#   sudo ./scripts/install-debian.sh [--tls-mode {nginx|direct|self-signed}]
#                                    [--public-fqdn ipam.example.com]
#                                    [--bind-port 8443]
#
# TLS 模式（強制 SSL，A02）：
#   nginx        — 後端綁 127.0.0.1:8000；nginx 終結 HTTPS（你需自己準備憑證或跑 certbot）
#   direct       — 後端 uvicorn 直接吃自簽憑證（綁 8443）；不裝 nginx site
#   self-signed  — = direct + 自動產生自簽憑證（最快上線；瀏覽器會警示）
#
# 行為：
#   1. apt 安裝：postgresql-16、redis-server、python ≥ 3.11（venv）、nginx、build-essential
#   2. 建立系統使用者 jtipam（無 shell）
#   3. 設定 PostgreSQL 帳號 jt_ipam + DB jt_ipam
#   4. 設定 Redis（requirepass）
#   5. 建立 Python venv + pip install backend
#   6. 跑 alembic upgrade head
#   7. pnpm build frontend，把 dist 放到 /opt/jt-ipam/frontend/dist
#   8. 安裝 systemd unit + nginx site（依 tls-mode）
#   9. 自簽 / direct 模式自動產生 ECDSA P-384 憑證
#
# 安全考量（OWASP A02 / A05 / A07）：
#   * 自動產生 SECRET_KEY / ENCRYPTION_KEY / AUDIT_CHAIN_GENESIS / DB password
#   * /etc/jt-ipam/backend.env 權限 0640，owner root:jtipam
#   * Postgres 密碼使用 SCRAM-SHA-256
#   * SSL 強制；config.py production guard 會擋 http:// 的 APP_PUBLIC_URL
# =============================================================================
set -euo pipefail

# ── 預設參數 ──
TLS_MODE="nginx"
PUBLIC_FQDN="ipam.example.com"
BIND_PORT_DIRECT=8443

while [[ $# -gt 0 ]]; do
    case "$1" in
        --tls-mode) TLS_MODE="$2"; shift 2 ;;
        --public-fqdn) PUBLIC_FQDN="$2"; shift 2 ;;
        --bind-port) BIND_PORT_DIRECT="$2"; shift 2 ;;
        -h|--help) sed -n '2,28p' "$0"; exit 0 ;;
        *) echo "Unknown arg: $1" >&2; exit 2 ;;
    esac
done

case "$TLS_MODE" in
    nginx|direct|self-signed) ;;
    *) echo "[error] --tls-mode must be one of: nginx | direct | self-signed (got: $TLS_MODE)" >&2; exit 2 ;;
esac

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
TLS_DIR="$ETC_DIR/tls"
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

# 偵測可用 Python（由新到舊取，需 ≥ 3.11）。
# 用 apt-cache madison：實際可裝才算數（apt-cache show 會匹配 Provides，不可靠）。
PYTHON_BIN=""
PYTHON_PKGS=()
for ver in python3.13 python3.12 python3.11; do
    if apt-cache madison "${ver}-venv" 2>/dev/null | grep -q .; then
        PYTHON_BIN="$ver"
        PYTHON_PKGS=("$ver" "${ver}-venv" "${ver}-dev")
        break
    fi
done
if [[ -z "$PYTHON_BIN" ]] && command -v python3 >/dev/null && \
        python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
    PYTHON_BIN="python3"
    PYTHON_PKGS=(python3 python3-venv python3-dev)
fi
if [[ -z "$PYTHON_BIN" ]]; then
    echo "[error] need Python ≥ 3.11；Ubuntu 22.04 請改 24.04，或啟用 deadsnakes PPA：" >&2
    echo "        sudo add-apt-repository -y ppa:deadsnakes/ppa && sudo apt-get update" >&2
    exit 1
fi
log "Using $PYTHON_BIN for backend venv"

PKGS=(
    postgresql-16 postgresql-contrib-16
    postgresql-16-pgvector
    redis-server
    "${PYTHON_PKGS[@]}"
    build-essential libpq-dev pkg-config
    curl ca-certificates gnupg openssl
)

# Node：若系統已裝 nodejs（例如 nodesource v20），不要動；否則裝 distro nodejs+npm
if ! command -v node >/dev/null 2>&1; then
    PKGS+=(nodejs npm)
fi
# nginx 模式才裝 nginx
if [[ "$TLS_MODE" == "nginx" ]]; then
    PKGS+=(nginx)
fi

# Ubuntu < 24.04 / Debian < 13 沒有 postgresql-16；先檢查並在需要時加 PGDG repo
if ! apt-cache show postgresql-16 >/dev/null 2>&1; then
    warn "postgresql-16 not in default repos; adding PGDG repo…"
    install -d /usr/share/postgresql-common/pgdg
    curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        | gpg --dearmor -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.gpg
    echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.gpg] \
          https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list
    apt-get update -qq
fi

apt-get install -y "${PKGS[@]}"

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

# 讓 jtipam 能寫 /opt/jt-ipam/backend/.venv 與 /opt/jt-ipam/frontend/{node_modules,dist}
chown -R "$JTIPAM_USER:$JTIPAM_GROUP" "$BACKEND_DIR" "$FRONTEND_DIR"

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
-- pgvector：alembic migration 0009 也會 IF NOT EXISTS 一次，但需要 superuser，
-- 所以先在這以 postgres 身分建好；之後 alembic 跑時是 no-op
CREATE EXTENSION IF NOT EXISTS vector;
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
sudo -u "$JTIPAM_USER" "$PYTHON_BIN" -m venv .venv
sudo -u "$JTIPAM_USER" .venv/bin/pip install --upgrade pip wheel
sudo -u "$JTIPAM_USER" .venv/bin/pip install -e ".[dev]"

# ── 6. backend.env ──
log "Generating /etc/jt-ipam/backend.env…"
ENV_FILE="$ETC_DIR/backend.env"
if [[ ! -f "$ENV_FILE" ]]; then
    SECRET_KEY="$(openssl rand -hex 64)"
    ENCRYPTION_KEY="$(openssl rand -base64 32)"
    AUDIT_CHAIN_GENESIS="$(openssl rand -hex 64)"

    # ── TLS 設定段 ──
    case "$TLS_MODE" in
        nginx)
            BACKEND_TLS_BLOCK="BACKEND_TLS_MODE=nginx
BACKEND_BIND_HOST=127.0.0.1
BACKEND_BIND_PORT=8000"
            ;;
        direct|self-signed)
            BACKEND_TLS_BLOCK="BACKEND_TLS_MODE=direct
BACKEND_BIND_HOST=0.0.0.0
BACKEND_BIND_PORT=${BIND_PORT_DIRECT}
BACKEND_TLS_CERT_FILE=${TLS_DIR}/server.crt
BACKEND_TLS_KEY_FILE=${TLS_DIR}/server.key"
            ;;
    esac

    # 推導對外 URL
    if [[ "$TLS_MODE" == "nginx" ]]; then
        PUBLIC_URL="https://${PUBLIC_FQDN}"
    else
        # direct / self-signed：對外 = 後端 host:port
        PUBLIC_URL="https://${PUBLIC_FQDN}:${BIND_PORT_DIRECT}"
    fi

    cat > "$ENV_FILE" <<EOF
# 自動產生 — $(date -Iseconds)（TLS 模式：${TLS_MODE}）
APP_ENV=production
APP_DEBUG=false
APP_LOG_LEVEL=INFO
APP_TIMEZONE=Asia/Taipei

APP_PUBLIC_URL=${PUBLIC_URL}
API_PUBLIC_URL=${PUBLIC_URL}
CORS_ORIGINS=${PUBLIC_URL}

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

# ── TLS（強制 SSL；A02）──
${BACKEND_TLS_BLOCK}

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

# ── 9. TLS 憑證（self-signed 模式自動產生）──
if [[ "$TLS_MODE" == "self-signed" ]]; then
    log "Generating self-signed TLS certificate…"
    "$REPO_ROOT/scripts/generate-self-signed-cert.sh" \
        --out-dir "$TLS_DIR" \
        --cn "$PUBLIC_FQDN" \
        --san "DNS:${PUBLIC_FQDN}" \
        --owner "root:${JTIPAM_GROUP}" \
        --force
elif [[ "$TLS_MODE" == "direct" ]]; then
    if [[ ! -f "$TLS_DIR/server.crt" || ! -f "$TLS_DIR/server.key" ]]; then
        warn "$TLS_DIR/server.{crt,key} 不存在；direct 模式請手動放入或改用 --tls-mode self-signed"
    fi
fi

# ── 10. systemd ──
log "Installing systemd units…"
install -m 0644 "$REPO_ROOT/deploy/systemd/jt-ipam-backend.service" \
    /etc/systemd/system/jt-ipam-backend.service
install -m 0644 "$REPO_ROOT/deploy/systemd/jt-ipam-sync.service" \
    /etc/systemd/system/jt-ipam-sync.service
install -m 0644 "$REPO_ROOT/deploy/systemd/jt-ipam-sync.timer" \
    /etc/systemd/system/jt-ipam-sync.timer
systemctl daemon-reload
systemctl enable --now jt-ipam-backend
# 定期同步 OPNsense / Wazuh / LibreNMS（依各 instance 自己的 sync_interval_seconds）
systemctl enable --now jt-ipam-sync.timer

# ── 11. nginx site（僅 nginx 模式）──
if [[ "$TLS_MODE" == "nginx" ]]; then
    log "Installing nginx site (mode: nginx terminates TLS)…"
    install -d -m 0755 /etc/nginx/snippets
    install -m 0644 "$REPO_ROOT/deploy/nginx/jt-ipam-proxy.conf" \
        /etc/nginx/snippets/jt-ipam-proxy.conf

    # 把模板 server_name 換成實際 FQDN
    sed "s/ipam\.example\.com/${PUBLIC_FQDN}/g" \
        "$REPO_ROOT/deploy/nginx/jt-ipam.conf" \
        > /etc/nginx/sites-available/jt-ipam
    chmod 0644 /etc/nginx/sites-available/jt-ipam
    ln -sf /etc/nginx/sites-available/jt-ipam /etc/nginx/sites-enabled/jt-ipam

    if [[ ! -f "/etc/letsencrypt/live/${PUBLIC_FQDN}/fullchain.pem" ]]; then
        warn "尚未取得 ${PUBLIC_FQDN} 的 Let's Encrypt 憑證"
        warn "請先 sudo apt install certbot python3-certbot-nginx"
        warn "並執行：sudo certbot --nginx -d ${PUBLIC_FQDN}"
        warn "若為內網 / 自簽，請參考 deploy/README.md「nginx 模式：使用自簽憑證」"
        warn "目前 nginx 不會 reload（缺憑證會失敗）；憑證就緒後 sudo systemctl reload nginx"
    elif nginx -t; then
        systemctl reload nginx
    else
        warn "nginx config test failed; review /etc/nginx/sites-available/jt-ipam"
    fi
else
    log "Skipping nginx (mode: ${TLS_MODE} — uvicorn terminates TLS directly)"
fi

# ── Done ──
log "Done."
case "$TLS_MODE" in
    nginx)
        log "  Backend on http://127.0.0.1:8000 (loopback only)"
        log "  Frontend served by nginx via https://${PUBLIC_FQDN}/"
        log "  Health: curl -fsS http://127.0.0.1:8000/healthz"
        ;;
    direct|self-signed)
        log "  Backend (TLS) on https://${PUBLIC_FQDN}:${BIND_PORT_DIRECT}/"
        log "  Health: curl -fsSk https://127.0.0.1:${BIND_PORT_DIRECT}/healthz"
        log "  Cert: ${TLS_DIR}/server.crt  Key: ${TLS_DIR}/server.key"
        log "  注意：自簽憑證瀏覽器會警示；正式環境請改用內網 CA 或 Let's Encrypt"
        ;;
esac
log "Review /etc/jt-ipam/backend.env (尤其是 APP_PUBLIC_URL / CORS_ORIGINS)"
