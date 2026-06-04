#!/usr/bin/env bash
# =============================================================================
# jt-ipam — 單一進入點部署工具
#
# 用法：
#   jt-ipam.sh install [--tls-mode {nginx|direct|self-signed}]
#                      [--public-fqdn ipam.example.com] [--bind-port 8443]
#   jt-ipam.sh upgrade [--no-pull]
#   jt-ipam.sh uninstall [--purge] [--yes]
#   jt-ipam.sh help | -h | --help
#
# 子指令：
#   install    — 全新安裝（Debian/Ubuntu；Proxmox LXC 或裸機）
#   upgrade    — 升級既有安裝（git pull → 備份 → migrate → build → restart）
#   uninstall  — 停用並移除 systemd units/timers + nginx site；
#                預設保留 DB / 設定 / 上傳檔 / jtipam user / 原始碼。
#                --purge 才會 dropdb + 刪 /etc/jt-ipam /var/lib/jt-ipam + 刪 user
#                （需互動 yes 或 --yes）。永不刪 /opt/jt-ipam 原始碼。
# =============================================================================
set -euo pipefail

# ── 顏色 log helper（全子指令共用）──
log()  { echo -e "\033[1;32m[jt-ipam]\033[0m $*"; }
warn() { echo -e "\033[1;33m[warn]\033[0m $*" >&2; }
die()  { echo -e "\033[1;31mFATAL:\033[0m $*" >&2; exit 1; }

# ── root guard（install/upgrade/uninstall 用；help/usage 不用）──
require_root() {
    if [[ $EUID -ne 0 ]]; then
        echo "[error] 必須以 root 執行（請用 sudo）" >&2
        exit 1
    fi
}

# Repo 根目錄（scripts/ 的上一層）
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
    cat <<'USAGE'
jt-ipam — 部署工具（單一進入點）

用法：
  jt-ipam.sh <command> [options]

Commands:
  install      全新安裝（apt / postgres / redis / venv / alembic / pnpm / systemd / nginx / tls）
                 --tls-mode {nginx|direct|self-signed}   （預設 nginx）
                 --public-fqdn <fqdn>                     （預設 ipam.example.com）
                 --bind-port <port>                       （direct/self-signed 用，預設 8443）
  upgrade      升級既有安裝（git pull → 備份 → pip → alembic → build → restart）
                 --no-pull                                跳過 git pull
  uninstall    停用並移除 systemd units/timers + nginx site（預設保留資料）
                 --purge                                  另外 dropdb + 刪設定/上傳檔/系統 user
                 --yes                                    --purge 時跳過互動確認
  help | -h | --help   顯示此說明

範例：
  sudo jt-ipam.sh install --tls-mode self-signed --public-fqdn ipam.lan
  sudo jt-ipam.sh upgrade --no-pull
  sudo jt-ipam.sh uninstall            # 只停服務，保留 DB / 設定 / 原始碼
  sudo jt-ipam.sh uninstall --purge    # 連 DB / 設定 / user 一起刪（會要求確認）

注意：uninstall 永不刪除 /opt/jt-ipam 原始碼。
USAGE
}

# =============================================================================
# cmd_install — 全新安裝（原 scripts/install-debian.sh 邏輯，逐字保留）
# =============================================================================
cmd_install() {
    # ── 預設參數 ──
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

    # ── 必要檢查 ──
    require_root

    if ! command -v lsb_release >/dev/null 2>&1; then
        apt-get update -qq
        apt-get install -y -qq lsb-release
    fi

    local DISTRO
    DISTRO=$(lsb_release -si)
    if [[ "$DISTRO" != "Debian" && "$DISTRO" != "Ubuntu" ]]; then
        echo "[warn] 此腳本針對 Debian/Ubuntu；其他發行版請手動安裝" >&2
    fi

    local ETC_DIR="/etc/jt-ipam"
    local TLS_DIR="$ETC_DIR/tls"
    local BACKEND_DIR="${REPO_ROOT}/backend"
    local FRONTEND_DIR="${REPO_ROOT}/frontend"
    local JTIPAM_USER="jtipam"
    local JTIPAM_GROUP="jtipam"

    # ── 1. apt 套件 ──
    log "Installing apt packages…"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq

    # 偵測可用 Python（由新到舊取，需 ≥ 3.11）。
    # 用 apt-cache madison：實際可裝才算數（apt-cache show 會匹配 Provides，不可靠）。
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
        echo "[error] need Python ≥ 3.11；Ubuntu 22.04 請改 24.04，或啟用 deadsnakes PPA：" >&2
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
        /var/lib/jt-ipam /var/log/jt-ipam \
        /var/lib/jt-ipam/uploads /var/lib/jt-ipam/uploads/floorplans
    install -d -m 0755 "$ETC_DIR"

    # 讓 jtipam 擁有整個專案目錄（含 .git）：venv / node_modules / dist 可寫，
    # 且日後 upgrade 以 jtipam 身分跑 git pull 不會因 .git 為 root 所有而失敗（bootstrap 以 root clone 時尤其重要）。
    chown -R "$JTIPAM_USER:$JTIPAM_GROUP" "$REPO_ROOT"

    # ── 3. PostgreSQL ──
    log "Configuring PostgreSQL…"
    systemctl enable --now postgresql

    # 啟用 SCRAM-SHA-256
    local PG_HBA PG_CONF
    PG_HBA="$(sudo -u postgres psql -tAc 'SHOW hba_file;')"
    PG_CONF="$(sudo -u postgres psql -tAc 'SHOW config_file;')"
    if ! grep -q "^password_encryption" "$PG_CONF"; then
        echo "password_encryption = scram-sha-256" >> "$PG_CONF"
    fi

    # 建立 role + DB（如果不存在）
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
    local REDIS_PASSWORD=""
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
    # prod 只裝執行期相依（與 upgrade 一致）；開發/測試工具請另跑 pip install -e ".[dev]"
    sudo -u "$JTIPAM_USER" .venv/bin/pip install -e .

    # ── 6. backend.env ──
    log "Generating /etc/jt-ipam/backend.env…"
    local ENV_FILE="$ETC_DIR/backend.env"
    if [[ ! -f "$ENV_FILE" ]]; then
        local SECRET_KEY ENCRYPTION_KEY AUDIT_CHAIN_GENESIS BACKEND_TLS_BLOCK PUBLIC_URL
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

    # ── 9. TLS 憑證 ──
    # 統一憑證路徑：/etc/jt-ipam/tls/server.{crt,key}
    # - self-signed 模式：強制 force 重產
    # - nginx 模式：若無憑證自動產一個自簽（先讓站起得來；正式憑證之後 cp 過去 reload）
    # - direct 模式：缺憑證時直接產（避免 backend 起不來）
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
            log "Generating bootstrap self-signed cert (用 cp 換成正式憑證即可，路徑：$TLS_DIR/server.{crt,key})…"
            "$REPO_ROOT/scripts/generate-self-signed-cert.sh" \
                --out-dir "$TLS_DIR" \
                --cn "$PUBLIC_FQDN" \
                --san "DNS:${PUBLIC_FQDN}" \
                --owner "root:${JTIPAM_GROUP}"
        else
            log "Existing TLS cert in $TLS_DIR — keeping it"
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
    install -m 0644 "$REPO_ROOT/deploy/systemd/jt-ipam-backup.service" \
        /etc/systemd/system/jt-ipam-backup.service
    install -m 0644 "$REPO_ROOT/deploy/systemd/jt-ipam-backup.timer" \
        /etc/systemd/system/jt-ipam-backup.timer
    install -m 0755 "$REPO_ROOT/scripts/jt-ipam-backup.sh" \
        /usr/local/bin/jt-ipam-backup.sh
    systemctl daemon-reload
    systemctl enable --now jt-ipam-backend
    # 定期同步 OPNsense / Wazuh / LibreNMS（依各 instance 自己的 sync_interval_seconds）
    systemctl enable --now jt-ipam-sync.timer
    # 每日 03:30 備份；保留 14 天到 /var/backups/jt-ipam/
    systemctl enable --now jt-ipam-backup.timer

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

        # 砍 apt 預設的 default site（「Welcome to nginx」會被 IP 訪問時抓到）；
        # jt-ipam.conf 已是 default_server，砍掉 default 就只剩它
        if [[ -e /etc/nginx/sites-enabled/default ]]; then
            rm -f /etc/nginx/sites-enabled/default
            log "Removed default nginx site (Welcome to nginx page)"
        fi

        # 預設使用 /etc/jt-ipam/tls/server.{crt,key}（#9 已產自簽當 bootstrap）。
        # 換正式憑證：cp 你的 cert + key 到上述路徑後 sudo systemctl reload nginx
        # Let's Encrypt 路線：修 /etc/nginx/sites-available/jt-ipam 把 ssl_certificate 改指到
        #   /etc/letsencrypt/live/${PUBLIC_FQDN}/{fullchain,privkey}.pem 後跑 certbot
        if nginx -t; then
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
}

# =============================================================================
# cmd_upgrade — 升級既有安裝（原 scripts/jt-ipam-upgrade.sh 邏輯，逐字保留）
# =============================================================================
cmd_upgrade() {
    local ROOT="$REPO_ROOT"
    local ENV_FILE="${ENV_FILE:-/etc/jt-ipam/backend.env}"
    local SVC="jt-ipam-backend"
    local DO_PULL=1
    [[ "${1:-}" == "--no-pull" ]] && DO_PULL=0

    [[ $EUID -eq 0 ]] || die "請以 root / sudo 執行（需重啟服務與寫入備份）"
    [[ -r "$ENV_FILE" ]] || die "讀不到 $ENV_FILE"
    [[ -d "$ROOT/backend/.venv" ]] || die "找不到 $ROOT/backend/.venv，這台不像是已安裝的 jt-ipam"

    # 以 repo 擁有者身分跑 git / pip / pnpm（避免用 root 動到 jtipam 的檔案與 venv）
    local JTIPAM_USER="${JTIPAM_USER:-$(stat -c '%U' "$ROOT")}"
    as_user() { sudo -u "$JTIPAM_USER" "$@"; }

    ver_of() { grep -m1 '"version"' "$ROOT/frontend/package.json" | sed -E 's/.*"version"\s*:\s*"([^"]+)".*/\1/'; }
    alembic_head() {
      # 必須在 sudo 的子 shell 內 source env（sudo 會清掉父層環境變數）
      ( as_user bash -c "cd '$ROOT/backend'; set -a; source '$ENV_FILE'; set +a; .venv/bin/alembic current" 2>/dev/null | head -1 ) || true
    }

    local OLD_VER OLD_REV
    OLD_VER="$(ver_of)"
    OLD_REV="$(as_user git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo '?')"
    log "升級前：版本 ${OLD_VER}　commit ${OLD_REV}　alembic $(alembic_head)"

    # ── 失敗時的回滾指引 ──
    local DUMP_PATH=""
    on_err() {
      warn "升級中斷。回滾方式："
      warn "  1) 程式碼：sudo -u $JTIPAM_USER git -C $ROOT reset --hard $OLD_REV"
      [[ -n "$DUMP_PATH" ]] && \
      warn "  2) 資料庫：pg_restore --clean --no-owner -d <db> $DUMP_PATH"
      warn "  3) 重建前端並重啟：在 $ROOT/frontend 跑 build，再 systemctl restart $SVC"
    }
    trap on_err ERR

    # ── 2. git pull ──
    if [[ $DO_PULL -eq 1 ]]; then
      log "git pull --ff-only"
      as_user git config --global --add safe.directory "$ROOT" 2>/dev/null || true
      as_user git -C "$ROOT" pull --ff-only
    else
      log "略過 git pull（--no-pull）"
    fi

    local NEW_VER NEW_REV
    NEW_VER="$(ver_of)"
    NEW_REV="$(as_user git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo '?')"
    if [[ "$OLD_REV" == "$NEW_REV" && $DO_PULL -eq 1 ]]; then
      log "已是最新（commit 未變），仍會跑一次 migration / build 以確保一致。"
    fi

    # ── 3. 備份資料庫（有就用既有腳本）──
    if [[ -x "$ROOT/scripts/jt-ipam-backup.sh" ]]; then
      log "備份資料庫…"
      "$ROOT/scripts/jt-ipam-backup.sh"
      DUMP_PATH="$(find /var/backups/jt-ipam -name '*.dump' -newermt '-2 min' 2>/dev/null | sort | tail -1)"
      [[ -n "$DUMP_PATH" ]] && log "備份檔：$DUMP_PATH"
    else
      warn "找不到 jt-ipam-backup.sh，略過自動備份（強烈建議先手動 pg_dump）"
    fi

    # ── 3c. 確保上傳目錄存在（機房平面圖等；舊版升上來時可能還沒有）──
    install -d -o "$JTIPAM_USER" -g "$JTIPAM_USER" -m 0750 \
      /var/lib/jt-ipam/uploads /var/lib/jt-ipam/uploads/floorplans 2>/dev/null || true

    # ── 4. 後端相依 ──
    log "更新後端相依（pip install -e .）…"
    ( cd "$ROOT/backend"; as_user .venv/bin/pip install --quiet -e . )

    # ── 5. 資料庫 migration ──
    log "alembic upgrade head…"
    # env 要在 sudo 的子 shell 內 source（sudo 預設不帶父層環境變數）
    as_user bash -c "cd '$ROOT/backend'; set -a; source '$ENV_FILE'; set +a; .venv/bin/alembic upgrade head"

    # ── 6. 前端 build（優先 pnpm，無則 npm）──
    log "建置前端…"
    cd "$ROOT/frontend"
    if command -v pnpm >/dev/null 2>&1; then
      as_user pnpm install --frozen-lockfile || as_user pnpm install
      as_user pnpm build
    else
      as_user npm ci || as_user npm install
      as_user npm run build
    fi

    # ── 7. 重啟後端 ──
    log "重啟 $SVC…"
    systemctl restart "$SVC"
    sleep 4
    systemctl is-active --quiet "$SVC" || die "$SVC 重啟後沒有起來，請看 journalctl -u $SVC"

    trap - ERR
    log "升級完成：${OLD_VER} (${OLD_REV}) → ${NEW_VER} (${NEW_REV})　alembic $(alembic_head)"
    log "前端已重建（nginx 直接服務 dist，免重啟）。"
}

# =============================================================================
# cmd_uninstall — 停用並移除 systemd units/timers + nginx site
#   預設：保留 DB / /etc/jt-ipam / /var/lib/jt-ipam / jtipam user / /opt/jt-ipam
#   --purge：另外 dropdb + 刪設定/上傳檔/系統 user（需 yes 或 --yes）
#   永不刪 /opt/jt-ipam 原始碼。
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

    # ── 停用 + disable systemd units / timers ──
    # backend + 已知 timers + 可能存在的 scan-agent
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
        # 移除 unit file（若存在）
        if [[ -f "/etc/systemd/system/$unit" ]]; then
            rm -f "/etc/systemd/system/$unit"
            log "Removed /etc/systemd/system/$unit"
        fi
    done
    systemctl daemon-reload

    # 移除 backup wrapper（install 放到 /usr/local/bin）
    if [[ -f /usr/local/bin/jt-ipam-backup.sh ]]; then
        rm -f /usr/local/bin/jt-ipam-backup.sh
        log "Removed /usr/local/bin/jt-ipam-backup.sh"
    fi

    # ── nginx site / snippet ──
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
            warn "nginx -t 失敗，未 reload；請手動檢查 /etc/nginx"
        fi
    fi

    if [[ $PURGE -eq 0 ]]; then
        log "已停用並移除 systemd units/timers + nginx site。"
        log "保留：資料庫 jt_ipam / $ETC_DIR / $DATA_DIR / 系統使用者 $JTIPAM_USER / 原始碼 $REPO_ROOT"
        log "若要連資料一起刪除：sudo jt-ipam.sh uninstall --purge"
        return 0
    fi

    # ── --purge：摧毀性操作，需明確確認 ──
    echo
    echo -e "\033[1;31m###############################################################\033[0m" >&2
    echo -e "\033[1;31m# 警告：--purge 將永久刪除以下資料，無法復原：\033[0m" >&2
    echo -e "\033[1;31m#   * PostgreSQL 資料庫 jt_ipam（dropdb，全部 IPAM 資料）\033[0m" >&2
    echo -e "\033[1;31m#   * $ETC_DIR（設定 / secrets / TLS 憑證）\033[0m" >&2
    echo -e "\033[1;31m#   * $DATA_DIR（上傳檔 / 機房平面圖 / log）\033[0m" >&2
    echo -e "\033[1;31m#   * 系統使用者 $JTIPAM_USER\033[0m" >&2
    echo -e "\033[1;31m# （原始碼 $REPO_ROOT 不會被刪除）\033[0m" >&2
    echo -e "\033[1;31m###############################################################\033[0m" >&2
    echo

    if [[ $ASSUME_YES -ne 1 ]]; then
        local ans=""
        read -r -p "確定要永久刪除上述資料嗎？請輸入 yes 確認：" ans
        if [[ "$ans" != "yes" ]]; then
            die "未輸入 yes，已中止 purge（沒有刪除任何資料）。"
        fi
    else
        warn "--yes 指定，跳過互動確認，直接 purge。"
    fi

    # 1) dropdb jt_ipam
    if command -v psql >/dev/null 2>&1; then
        log "Dropping database jt_ipam…"
        sudo -u postgres dropdb --if-exists jt_ipam 2>/dev/null \
            || warn "dropdb jt_ipam 失敗（DB 可能不存在或 postgres 未跑）"
        # 順便移除 role（如果存在）
        if sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='jt_ipam'" 2>/dev/null | grep -q 1; then
            sudo -u postgres psql -c "DROP ROLE IF EXISTS jt_ipam;" 2>/dev/null \
                || warn "DROP ROLE jt_ipam 失敗（可能有依賴物件）"
        fi
    else
        warn "找不到 psql，略過 dropdb（請手動清理 PostgreSQL）"
    fi

    # 2) /etc/jt-ipam
    if [[ -d "$ETC_DIR" ]]; then
        rm -rf "$ETC_DIR"
        log "Removed $ETC_DIR"
    fi

    # 3) /var/lib/jt-ipam（+ log 目錄）
    if [[ -d "$DATA_DIR" ]]; then
        rm -rf "$DATA_DIR"
        log "Removed $DATA_DIR"
    fi
    if [[ -d /var/log/jt-ipam ]]; then
        rm -rf /var/log/jt-ipam
        log "Removed /var/log/jt-ipam"
    fi

    # 4) 系統使用者
    if id -u "$JTIPAM_USER" >/dev/null 2>&1; then
        userdel "$JTIPAM_USER" 2>/dev/null || warn "userdel $JTIPAM_USER 失敗（可能有執行中程序）"
        log "Removed system user $JTIPAM_USER"
    fi

    log "Purge 完成。原始碼 $REPO_ROOT 已保留（如需移除請自行 rm）。"
}

# =============================================================================
# 頂層分派
# =============================================================================
main() {
    local cmd="${1:-}"
    case "$cmd" in
        ""|help|-h|--help)
            usage
            # 無參數 → exit 2；明確要 help → exit 0
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
