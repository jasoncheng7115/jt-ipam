#!/usr/bin/env bash
# =============================================================================
# jt-ipam — single entry-point deployment tool
#
# Usage:
#   jt-ipam.sh install [--tls-mode {nginx|direct|self-signed}]
#                      [--public-fqdn ipam.example.com] [--bind-port 8443]
#   jt-ipam.sh upgrade [--no-pull]
#   jt-ipam.sh uninstall [--purge] [--yes]
#   jt-ipam.sh help | -h | --help
#
# Subcommands:
#   install    — fresh install (Debian/Ubuntu; Proxmox LXC or bare metal)
#   upgrade    — upgrade existing install (git pull -> backup -> migrate -> build -> restart)
#   uninstall  — stop and remove systemd units/timers + nginx site;
#                by default keeps DB / config / uploads / jtipam user / source.
#                --purge also runs dropdb + removes /etc/jt-ipam /var/lib/jt-ipam + removes user
#                (requires interactive yes or --yes). Never removes /opt/jt-ipam source.
# =============================================================================
set -euo pipefail

# -- colored log helpers (shared by all subcommands) --
log()  { echo -e "\033[1;32m[jt-ipam]\033[0m $*"; }
warn() { echo -e "\033[1;33m[warn]\033[0m $*" >&2; }
die()  { echo -e "\033[1;31mFATAL:\033[0m $*" >&2; exit 1; }

# -- root guard (used by install/upgrade/uninstall; not by help/usage) --
require_root() {
    if [[ $EUID -ne 0 ]]; then
        echo "[error] must run as root (please use sudo)" >&2
        exit 1
    fi
}

# Repo root (parent of scripts/)
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
    cat <<'USAGE'
jt-ipam — deployment tool (single entry point)

Usage:
  jt-ipam.sh <command> [options]

Commands:
  install      fresh install (apt / postgres / redis / venv / alembic / pnpm / systemd / nginx / tls)
                 --tls-mode {nginx|direct|self-signed}   (default nginx)
                 --public-fqdn <fqdn>                     (default ipam.example.com)
                 --bind-port <port>                       (for direct/self-signed, default 8443)
  upgrade      upgrade existing install (git pull -> backup -> pip -> alembic -> build -> restart)
                 --no-pull                                skip git pull
  uninstall    stop and remove systemd units/timers + nginx site (keeps data by default)
                 --purge                                  also dropdb + remove config/uploads/system user
                 --yes                                    skip interactive confirmation when using --purge
  help | -h | --help   show this help

Examples:
  sudo jt-ipam.sh install --tls-mode self-signed --public-fqdn ipam.lan
  sudo jt-ipam.sh upgrade --no-pull
  sudo jt-ipam.sh uninstall            # only stop services, keep DB / config / source
  sudo jt-ipam.sh uninstall --purge    # also remove DB / config / user (will ask for confirmation)

Note: uninstall never removes the /opt/jt-ipam source.
USAGE
}

# =============================================================================
# cmd_install — fresh install (original scripts/install-debian.sh logic, preserved verbatim)
# =============================================================================
cmd_install() {
    # -- default parameters --
    local TLS_MODE="nginx"
    local PUBLIC_FQDN="ipam.example.com"
    local BIND_PORT_DIRECT=8443

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --tls-mode) TLS_MODE="$2"; shift 2 ;;
            --public-fqdn) PUBLIC_FQDN="$2"; shift 2 ;;
            --bind-port) BIND_PORT_DIRECT="$2"; shift 2 ;;
            -h|--help) usage; exit 0 ;;
            *) echo "Unknown arg: $1" >&2; exit 2 ;;
        esac
    done

    case "$TLS_MODE" in
        nginx|direct|self-signed) ;;
        *) echo "[error] --tls-mode must be one of: nginx | direct | self-signed (got: $TLS_MODE)" >&2; exit 2 ;;
    esac

    # -- required checks --
    require_root

    if ! command -v lsb_release >/dev/null 2>&1; then
        apt-get update -qq
        apt-get install -y -qq lsb-release
    fi

    local DISTRO
    DISTRO=$(lsb_release -si)
    if [[ "$DISTRO" != "Debian" && "$DISTRO" != "Ubuntu" ]]; then
        echo "[warn] this script targets Debian/Ubuntu; install manually on other distros" >&2
    fi

    local ETC_DIR="/etc/jt-ipam"
    local TLS_DIR="$ETC_DIR/tls"
    local BACKEND_DIR="${REPO_ROOT}/backend"
    local FRONTEND_DIR="${REPO_ROOT}/frontend"
    local JTIPAM_USER="jtipam"
    local JTIPAM_GROUP="jtipam"

    # -- 1. apt packages --
    log "Installing apt packages…"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq

    # Detect available Python (newest to oldest, needs >= 3.11).
    # Use apt-cache madison: only counts if actually installable (apt-cache show matches Provides, unreliable).
    local PYTHON_BIN=""
    local PYTHON_PKGS=()
    local ver
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
        echo "[error] need Python >= 3.11; on Ubuntu 22.04 switch to 24.04, or enable the deadsnakes PPA:" >&2
        echo "        sudo add-apt-repository -y ppa:deadsnakes/ppa && sudo apt-get update" >&2
        exit 1
    fi
    log "Using $PYTHON_BIN for backend venv"

    local PKGS=(
        postgresql-16 postgresql-contrib-16
        postgresql-16-pgvector
        redis-server
        "${PYTHON_PKGS[@]}"
        build-essential libpq-dev pkg-config
        curl ca-certificates gnupg openssl
    )

    # Node: if nodejs is already installed (e.g. nodesource v20), leave it alone; otherwise install distro nodejs+npm
    if ! command -v node >/dev/null 2>&1; then
        PKGS+=(nodejs npm)
    fi
    # only install nginx in nginx mode
    if [[ "$TLS_MODE" == "nginx" ]]; then
        PKGS+=(nginx)
    fi

    # Ubuntu < 24.04 / Debian < 13 lack postgresql-16; check first and add the PGDG repo if needed
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

    # corepack enables pnpm (for the frontend build)
    if ! command -v pnpm >/dev/null 2>&1; then
        log "Enabling corepack + pnpm…"
        corepack enable || npm install -g pnpm@9
        corepack prepare pnpm@9 --activate || true
    fi

    # -- 2. system user --
    if ! id -u "$JTIPAM_USER" >/dev/null 2>&1; then
        log "Creating system user $JTIPAM_USER…"
        useradd --system --home-dir /var/lib/jt-ipam --shell /usr/sbin/nologin "$JTIPAM_USER"
    fi

    install -d -o "$JTIPAM_USER" -g "$JTIPAM_GROUP" -m 0750 \
        /var/lib/jt-ipam /var/log/jt-ipam \
        /var/lib/jt-ipam/uploads /var/lib/jt-ipam/uploads/floorplans
    install -d -m 0755 "$ETC_DIR"

    # Make jtipam own the whole project directory (including .git): so venv / node_modules / dist are writable,
    # and so later upgrades running git pull as jtipam don't fail because .git is owned by root (especially when bootstrap clones as root).
    chown -R "$JTIPAM_USER:$JTIPAM_GROUP" "$REPO_ROOT"

    # -- 3. PostgreSQL --
    log "Configuring PostgreSQL…"
    systemctl enable --now postgresql

    # Enable SCRAM-SHA-256
    local PG_HBA PG_CONF
    PG_HBA="$(sudo -u postgres psql -tAc 'SHOW hba_file;')"
    PG_CONF="$(sudo -u postgres psql -tAc 'SHOW config_file;')"
    if ! grep -q "^password_encryption" "$PG_CONF"; then
        echo "password_encryption = scram-sha-256" >> "$PG_CONF"
    fi

    # Create role + DB (if they don't exist)
    local DB_PASSWORD=""
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

    # Enable required extensions
    sudo -u postgres psql -d jt_ipam <<'SQL'
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS btree_gist;
-- pgvector: alembic migration 0009 also does IF NOT EXISTS once, but it needs superuser,
-- so create it here as postgres first; alembic's run later is then a no-op
CREATE EXTENSION IF NOT EXISTS vector;
SQL

    systemctl reload postgresql || systemctl restart postgresql

    # -- 4. Redis --
    log "Configuring Redis…"
    local REDIS_PASSWORD=""
    if [[ -f "$ETC_DIR/.redis-password" ]]; then
        REDIS_PASSWORD="$(cat "$ETC_DIR/.redis-password")"
    else
        REDIS_PASSWORD="$(openssl rand -base64 32 | tr -d '=+/')"
        install -m 0600 -o root -g root /dev/null "$ETC_DIR/.redis-password"
        echo -n "$REDIS_PASSWORD" > "$ETC_DIR/.redis-password"
    fi

    # Set requirepass + bind 127.0.0.1
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

    # -- 5. backend venv --
    log "Setting up backend venv…"
    cd "$BACKEND_DIR"
    sudo -u "$JTIPAM_USER" "$PYTHON_BIN" -m venv .venv
    sudo -u "$JTIPAM_USER" .venv/bin/pip install --upgrade pip wheel
    # prod installs runtime deps only (matching upgrade); for dev/test tools run pip install -e ".[dev]" separately
    sudo -u "$JTIPAM_USER" .venv/bin/pip install -e .

    # -- 6. backend.env --
    log "Generating /etc/jt-ipam/backend.env…"
    local ENV_FILE="$ETC_DIR/backend.env"
    if [[ ! -f "$ENV_FILE" ]]; then
        local SECRET_KEY ENCRYPTION_KEY AUDIT_CHAIN_GENESIS BACKEND_TLS_BLOCK PUBLIC_URL
        SECRET_KEY="$(openssl rand -hex 64)"
        ENCRYPTION_KEY="$(openssl rand -base64 32)"
        AUDIT_CHAIN_GENESIS="$(openssl rand -hex 64)"

        # -- TLS configuration block --
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

        # Derive the public URL
        if [[ "$TLS_MODE" == "nginx" ]]; then
            PUBLIC_URL="https://${PUBLIC_FQDN}"
        else
            # direct / self-signed: public = backend host:port
            PUBLIC_URL="https://${PUBLIC_FQDN}:${BIND_PORT_DIRECT}"
        fi

        cat > "$ENV_FILE" <<EOF
# Auto-generated — $(date -Iseconds) (TLS mode: ${TLS_MODE})
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

# -- TLS (SSL enforced; A02) --
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

    # -- 7. alembic migrate --
    log "Running alembic migrations…"
    cd "$BACKEND_DIR"
    sudo -u "$JTIPAM_USER" --preserve-env=PATH \
        bash -c "set -a; source $ENV_FILE; set +a; .venv/bin/alembic upgrade head"

    # -- 8. frontend build --
    log "Building frontend…"
    cd "$FRONTEND_DIR"
    sudo -u "$JTIPAM_USER" pnpm install --frozen-lockfile || sudo -u "$JTIPAM_USER" pnpm install
    sudo -u "$JTIPAM_USER" pnpm build

    # -- 9. TLS certificate --
    # Unified cert paths: /etc/jt-ipam/tls/server.{crt,key}
    # - self-signed mode: force regeneration
    # - nginx mode: auto-generate a self-signed cert if missing (gets the site up first; cp the real cert later and reload)
    # - direct mode: generate when cert is missing (so the backend can start)
    if [[ "$TLS_MODE" == "self-signed" ]]; then
        log "Generating self-signed TLS certificate…"
        "$REPO_ROOT/scripts/generate-self-signed-cert.sh" \
            --out-dir "$TLS_DIR" \
            --cn "$PUBLIC_FQDN" \
            --san "DNS:${PUBLIC_FQDN}" \
            --owner "root:${JTIPAM_GROUP}" \
            --force
    elif [[ "$TLS_MODE" == "nginx" || "$TLS_MODE" == "direct" ]]; then
        if [[ ! -f "$TLS_DIR/server.crt" || ! -f "$TLS_DIR/server.key" ]]; then
            log "Generating bootstrap self-signed cert (just cp your real cert over it at $TLS_DIR/server.{crt,key})…"
            "$REPO_ROOT/scripts/generate-self-signed-cert.sh" \
                --out-dir "$TLS_DIR" \
                --cn "$PUBLIC_FQDN" \
                --san "DNS:${PUBLIC_FQDN}" \
                --owner "root:${JTIPAM_GROUP}"
        else
            log "Existing TLS cert in $TLS_DIR — keeping it"
        fi
    fi

    # -- 10. systemd --
    log "Installing systemd units…"
    install -m 0644 "$REPO_ROOT/deploy/systemd/jt-ipam-backend.service" \
        /etc/systemd/system/jt-ipam-backend.service
    install -m 0644 "$REPO_ROOT/deploy/systemd/jt-ipam-sync.service" \
        /etc/systemd/system/jt-ipam-sync.service
    install -m 0644 "$REPO_ROOT/deploy/systemd/jt-ipam-sync.timer" \
        /etc/systemd/system/jt-ipam-sync.timer
    install -m 0644 "$REPO_ROOT/deploy/systemd/jt-ipam-backup.service" \
        /etc/systemd/system/jt-ipam-backup.service
    install -m 0644 "$REPO_ROOT/deploy/systemd/jt-ipam-backup.timer" \
        /etc/systemd/system/jt-ipam-backup.timer
    install -m 0755 "$REPO_ROOT/scripts/jt-ipam-backup.sh" \
        /usr/local/bin/jt-ipam-backup.sh
    systemctl daemon-reload
    systemctl enable --now jt-ipam-backend
    # Periodically sync OPNsense / Wazuh / LibreNMS (per each instance's own sync_interval_seconds)
    systemctl enable --now jt-ipam-sync.timer
    # Daily backup at 03:30; keep 14 days under /var/backups/jt-ipam/
    systemctl enable --now jt-ipam-backup.timer

    # -- 11. nginx site (nginx mode only) --
    if [[ "$TLS_MODE" == "nginx" ]]; then
        log "Installing nginx site (mode: nginx terminates TLS)…"
        install -d -m 0755 /etc/nginx/snippets
        install -m 0644 "$REPO_ROOT/deploy/nginx/jt-ipam-proxy.conf" \
            /etc/nginx/snippets/jt-ipam-proxy.conf

        # Replace the template server_name with the actual FQDN
        sed "s/ipam\.example\.com/${PUBLIC_FQDN}/g" \
            "$REPO_ROOT/deploy/nginx/jt-ipam.conf" \
            > /etc/nginx/sites-available/jt-ipam
        chmod 0644 /etc/nginx/sites-available/jt-ipam
        ln -sf /etc/nginx/sites-available/jt-ipam /etc/nginx/sites-enabled/jt-ipam

        # Remove apt's default site (its "Welcome to nginx" page gets picked up on IP access);
        # jt-ipam.conf is already the default_server, so removing default leaves only it
        if [[ -e /etc/nginx/sites-enabled/default ]]; then
            rm -f /etc/nginx/sites-enabled/default
            log "Removed default nginx site (Welcome to nginx page)"
        fi

        # Uses /etc/jt-ipam/tls/server.{crt,key} by default (#9 already generated a self-signed bootstrap cert).
        # To swap in a real cert: cp your cert + key to the paths above, then sudo systemctl reload nginx
        # Let's Encrypt route: edit /etc/nginx/sites-available/jt-ipam to point ssl_certificate at
        #   /etc/letsencrypt/live/${PUBLIC_FQDN}/{fullchain,privkey}.pem, then run certbot
        if nginx -t; then
            systemctl reload nginx
        else
            warn "nginx config test failed; review /etc/nginx/sites-available/jt-ipam"
        fi
    else
        log "Skipping nginx (mode: ${TLS_MODE} — uvicorn terminates TLS directly)"
    fi

    # -- Done --
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
            log "  Note: browsers warn on self-signed certs; in production use an internal CA or Let's Encrypt"
            ;;
    esac
    log "Review /etc/jt-ipam/backend.env (especially APP_PUBLIC_URL / CORS_ORIGINS)"
}

# =============================================================================
# cmd_upgrade — upgrade existing install (original scripts/jt-ipam-upgrade.sh logic, preserved verbatim)
# =============================================================================
cmd_upgrade() {
    local ROOT="$REPO_ROOT"
    local ENV_FILE="${ENV_FILE:-/etc/jt-ipam/backend.env}"
    local SVC="jt-ipam-backend"
    local DO_PULL=1
    [[ "${1:-}" == "--no-pull" ]] && DO_PULL=0

    [[ $EUID -eq 0 ]] || die "please run as root / sudo (needs to restart services and write backups)"
    [[ -r "$ENV_FILE" ]] || die "cannot read $ENV_FILE"
    [[ -d "$ROOT/backend/.venv" ]] || die "cannot find $ROOT/backend/.venv; this host does not look like an installed jt-ipam"

    # Run git / pip / pnpm as the repo owner (avoid root touching jtipam's files and venv)
    local JTIPAM_USER="${JTIPAM_USER:-$(stat -c '%U' "$ROOT")}"
    as_user() { sudo -u "$JTIPAM_USER" "$@"; }

    ver_of() { grep -m1 '"version"' "$ROOT/frontend/package.json" | sed -E 's/.*"version"\s*:\s*"([^"]+)".*/\1/'; }
    alembic_head() {
      # Must source env inside the sudo subshell (sudo strips parent environment variables)
      ( as_user bash -c "cd '$ROOT/backend'; set -a; source '$ENV_FILE'; set +a; .venv/bin/alembic current" 2>/dev/null | head -1 ) || true
    }

    local OLD_VER OLD_REV
    OLD_VER="$(ver_of)"
    OLD_REV="$(as_user git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo '?')"
    log "Before upgrade: version ${OLD_VER}  commit ${OLD_REV}  alembic $(alembic_head)"

    # -- rollback guidance on failure --
    local DUMP_PATH=""
    on_err() {
      warn "Upgrade aborted. How to roll back:"
      warn "  1) Code: sudo -u $JTIPAM_USER git -C $ROOT reset --hard $OLD_REV"
      [[ -n "$DUMP_PATH" ]] && \
      warn "  2) Database: pg_restore --clean --no-owner -d <db> $DUMP_PATH"
      warn "  3) Rebuild frontend and restart: run build in $ROOT/frontend, then systemctl restart $SVC"
    }
    trap on_err ERR

    # -- 2. git pull --
    if [[ $DO_PULL -eq 1 ]]; then
      log "git pull --ff-only"
      as_user git config --global --add safe.directory "$ROOT" 2>/dev/null || true
      as_user git -C "$ROOT" pull --ff-only
    else
      log "Skipping git pull (--no-pull)"
    fi

    local NEW_VER NEW_REV
    NEW_VER="$(ver_of)"
    NEW_REV="$(as_user git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo '?')"
    if [[ "$OLD_REV" == "$NEW_REV" && $DO_PULL -eq 1 ]]; then
      log "Already up to date (commit unchanged); still runs migration / build once to ensure consistency."
    fi

    # -- 3. back up the database (use the existing script if present) --
    if [[ -x "$ROOT/scripts/jt-ipam-backup.sh" ]]; then
      log "Backing up the database…"
      "$ROOT/scripts/jt-ipam-backup.sh"
      DUMP_PATH="$(find /var/backups/jt-ipam -name '*.dump' -newermt '-2 min' 2>/dev/null | sort | tail -1)"
      [[ -n "$DUMP_PATH" ]] && log "Backup file: $DUMP_PATH"
    else
      warn "cannot find jt-ipam-backup.sh, skipping automatic backup (strongly recommend a manual pg_dump first)"
    fi

    # -- 3c. ensure upload directories exist (floorplans etc.; may not exist yet when upgrading from an old version) --
    install -d -o "$JTIPAM_USER" -g "$JTIPAM_USER" -m 0750 \
      /var/lib/jt-ipam/uploads /var/lib/jt-ipam/uploads/floorplans 2>/dev/null || true

    # -- 4. backend dependencies --
    log "Updating backend dependencies (pip install -e .)…"
    ( cd "$ROOT/backend"; as_user .venv/bin/pip install --quiet -e . )

    # -- 5. database migration --
    log "alembic upgrade head…"
    # env must be sourced inside the sudo subshell (sudo does not carry parent environment by default)
    as_user bash -c "cd '$ROOT/backend'; set -a; source '$ENV_FILE'; set +a; .venv/bin/alembic upgrade head"

    # -- 6. frontend build (prefer pnpm, fall back to npm) --
    log "Building frontend…"
    cd "$ROOT/frontend"
    if command -v pnpm >/dev/null 2>&1; then
      as_user pnpm install --frozen-lockfile || as_user pnpm install
      as_user pnpm build
    else
      as_user npm ci || as_user npm install
      as_user npm run build
    fi

    # -- 7. restart backend --
    log "Restarting $SVC…"
    systemctl restart "$SVC"
    sleep 4
    systemctl is-active --quiet "$SVC" || die "$SVC did not come up after restart; check journalctl -u $SVC"

    trap - ERR
    log "Upgrade complete: ${OLD_VER} (${OLD_REV}) -> ${NEW_VER} (${NEW_REV})  alembic $(alembic_head)"
    log "Frontend rebuilt (nginx serves dist directly, no restart needed)."
}

# =============================================================================
# cmd_uninstall — stop and remove systemd units/timers + nginx site
#   default: keep DB / /etc/jt-ipam / /var/lib/jt-ipam / jtipam user / /opt/jt-ipam
#   --purge: also dropdb + remove config/uploads/system user (requires yes or --yes)
#   Never removes the /opt/jt-ipam source.
# =============================================================================
cmd_uninstall() {
    local PURGE=0
    local ASSUME_YES=0
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --purge) PURGE=1; shift ;;
            --yes|-y) ASSUME_YES=1; shift ;;
            -h|--help) usage; exit 0 ;;
            *) echo "Unknown arg: $1" >&2; exit 2 ;;
        esac
    done

    require_root

    local ETC_DIR="/etc/jt-ipam"
    local DATA_DIR="/var/lib/jt-ipam"
    local JTIPAM_USER="jtipam"

    # -- stop + disable systemd units / timers --
    # backend + known timers + a possibly-present scan-agent
    local UNITS=(
        jt-ipam-backend.service
        jt-ipam-sync.timer
        jt-ipam-sync.service
        jt-ipam-oui-refresh.timer
        jt-ipam-oui-refresh.service
        jt-ipam-backup.timer
        jt-ipam-backup.service
        jt-ipam-scan-agent.service
    )
    local unit
    for unit in "${UNITS[@]}"; do
        if systemctl list-unit-files "$unit" >/dev/null 2>&1 \
                && systemctl list-unit-files "$unit" 2>/dev/null | grep -q "$unit"; then
            log "Stopping + disabling $unit…"
            systemctl disable --now "$unit" 2>/dev/null || true
        fi
        # Remove the unit file (if present)
        if [[ -f "/etc/systemd/system/$unit" ]]; then
            rm -f "/etc/systemd/system/$unit"
            log "Removed /etc/systemd/system/$unit"
        fi
    done
    systemctl daemon-reload

    # Remove the backup wrapper (install puts it in /usr/local/bin)
    if [[ -f /usr/local/bin/jt-ipam-backup.sh ]]; then
        rm -f /usr/local/bin/jt-ipam-backup.sh
        log "Removed /usr/local/bin/jt-ipam-backup.sh"
    fi

    # -- nginx site / snippet --
    local NGINX_RELOAD=0
    if [[ -e /etc/nginx/sites-enabled/jt-ipam ]]; then
        rm -f /etc/nginx/sites-enabled/jt-ipam
        log "Removed nginx sites-enabled/jt-ipam"
        NGINX_RELOAD=1
    fi
    if [[ -e /etc/nginx/sites-available/jt-ipam ]]; then
        rm -f /etc/nginx/sites-available/jt-ipam
        log "Removed nginx sites-available/jt-ipam"
        NGINX_RELOAD=1
    fi
    if [[ -e /etc/nginx/snippets/jt-ipam-proxy.conf ]]; then
        rm -f /etc/nginx/snippets/jt-ipam-proxy.conf
        log "Removed nginx snippet jt-ipam-proxy.conf"
        NGINX_RELOAD=1
    fi
    if [[ $NGINX_RELOAD -eq 1 ]] && command -v nginx >/dev/null 2>&1; then
        if nginx -t >/dev/null 2>&1; then
            systemctl reload nginx 2>/dev/null || true
        else
            warn "nginx -t failed, not reloading; please check /etc/nginx manually"
        fi
    fi

    if [[ $PURGE -eq 0 ]]; then
        log "Stopped and removed systemd units/timers + nginx site."
        log "Kept: database jt_ipam / $ETC_DIR / $DATA_DIR / system user $JTIPAM_USER / source $REPO_ROOT"
        log "To also delete the data: sudo jt-ipam.sh uninstall --purge"
        return 0
    fi

    # -- --purge: destructive operation, requires explicit confirmation --
    echo
    echo -e "\033[1;31m###############################################################\033[0m" >&2
    echo -e "\033[1;31m# WARNING: --purge will permanently delete the following, unrecoverable:\033[0m" >&2
    echo -e "\033[1;31m#   * PostgreSQL database jt_ipam (dropdb, all IPAM data)\033[0m" >&2
    echo -e "\033[1;31m#   * $ETC_DIR (config / secrets / TLS certs)\033[0m" >&2
    echo -e "\033[1;31m#   * $DATA_DIR (uploads / floorplans / logs)\033[0m" >&2
    echo -e "\033[1;31m#   * system user $JTIPAM_USER\033[0m" >&2
    echo -e "\033[1;31m# (the source $REPO_ROOT will not be deleted)\033[0m" >&2
    echo -e "\033[1;31m###############################################################\033[0m" >&2
    echo

    if [[ $ASSUME_YES -ne 1 ]]; then
        local ans=""
        read -r -p "Are you sure you want to permanently delete the above? Type yes to confirm: " ans
        if [[ "$ans" != "yes" ]]; then
            die "Did not type yes, purge aborted (nothing was deleted)."
        fi
    else
        warn "--yes given, skipping interactive confirmation, purging directly."
    fi

    # 1) dropdb jt_ipam
    if command -v psql >/dev/null 2>&1; then
        log "Dropping database jt_ipam…"
        sudo -u postgres dropdb --if-exists jt_ipam 2>/dev/null \
            || warn "dropdb jt_ipam failed (DB may not exist or postgres is not running)"
        # Also remove the role (if present)
        if sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='jt_ipam'" 2>/dev/null | grep -q 1; then
            sudo -u postgres psql -c "DROP ROLE IF EXISTS jt_ipam;" 2>/dev/null \
                || warn "DROP ROLE jt_ipam failed (may have dependent objects)"
        fi
    else
        warn "cannot find psql, skipping dropdb (please clean up PostgreSQL manually)"
    fi

    # 2) /etc/jt-ipam
    if [[ -d "$ETC_DIR" ]]; then
        rm -rf "$ETC_DIR"
        log "Removed $ETC_DIR"
    fi

    # 3) /var/lib/jt-ipam (+ log directory)
    if [[ -d "$DATA_DIR" ]]; then
        rm -rf "$DATA_DIR"
        log "Removed $DATA_DIR"
    fi
    if [[ -d /var/log/jt-ipam ]]; then
        rm -rf /var/log/jt-ipam
        log "Removed /var/log/jt-ipam"
    fi

    # 4) system user
    if id -u "$JTIPAM_USER" >/dev/null 2>&1; then
        userdel "$JTIPAM_USER" 2>/dev/null || warn "userdel $JTIPAM_USER failed (may have running processes)"
        log "Removed system user $JTIPAM_USER"
    fi

    log "Purge complete. The source $REPO_ROOT was kept (rm it yourself if you want it gone)."
}

# =============================================================================
# top-level dispatch
# =============================================================================
main() {
    local cmd="${1:-}"
    case "$cmd" in
        ""|help|-h|--help)
            usage
            # no args -> exit 2; explicit help -> exit 0
            [[ -z "$cmd" ]] && exit 2 || exit 0
            ;;
        install)   shift; cmd_install "$@" ;;
        upgrade)   shift; cmd_upgrade "$@" ;;
        uninstall) shift; cmd_uninstall "$@" ;;
        *)
            echo "[error] Unknown command: $cmd" >&2
            echo >&2
            usage >&2
            exit 2
            ;;
    esac
}

main "$@"
