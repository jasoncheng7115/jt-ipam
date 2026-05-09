#!/usr/bin/env bash
# =============================================================================
# jt-ipam — backend uvicorn 啟動 wrapper
#
# 由 systemd（jt-ipam-backend.service）或 dev.sh 呼叫，依 BACKEND_TLS_MODE
# 決定是否啟用 uvicorn 內建的 TLS。
#
# 環境變數（從 /etc/jt-ipam/backend.env 或 backend/.env 讀入）：
#   BACKEND_TLS_MODE         nginx | direct（預設 nginx）
#   BACKEND_BIND_HOST        綁定 host（nginx 模式必須是 loopback）
#   BACKEND_BIND_PORT        綁定 port
#   BACKEND_TLS_CERT_FILE    direct 模式下的 PEM 憑證
#   BACKEND_TLS_KEY_FILE     direct 模式下的 PEM 私鑰
#   UVICORN_WORKERS          worker 數量（預設 4）
#   UVICORN_EXTRA_OPTS       額外旗標（例如 --reload）
#
# OWASP 對應：
#   * A02：強制 TLS — direct 模式啟動前驗證 cert/key 存在 + 權限合理
#   * A05：以 exec 取代當前行程，systemd watchdog 才能正確管理
# =============================================================================
set -euo pipefail

VENV_BIN="/opt/jt-ipam/backend/.venv/bin"
if [[ ! -x "$VENV_BIN/uvicorn" ]]; then
    echo "[run-backend] uvicorn not found in $VENV_BIN; run install or dev.sh setup first" >&2
    exit 1
fi

mode="${BACKEND_TLS_MODE:-nginx}"
host="${BACKEND_BIND_HOST:-127.0.0.1}"
port="${BACKEND_BIND_PORT:-8000}"
workers="${UVICORN_WORKERS:-4}"
extra_opts="${UVICORN_EXTRA_OPTS:-}"

args=(
    "app.main:app"
    --host "$host"
    --port "$port"
    --workers "$workers"
    --proxy-headers
    --forwarded-allow-ips "127.0.0.1"
    --no-server-header
)

case "$mode" in
    nginx)
        # 後端只跑 HTTP；nginx 終結 HTTPS。
        # config.py 的 _tls_guards 會擋掉非 loopback 的 host。
        if [[ "$host" != "127.0.0.1" && "$host" != "::1" && "$host" != "localhost" ]]; then
            echo "[run-backend] BACKEND_TLS_MODE=nginx requires loopback BACKEND_BIND_HOST" >&2
            echo "             got: $host" >&2
            exit 1
        fi
        ;;
    direct)
        cert="${BACKEND_TLS_CERT_FILE:-}"
        key="${BACKEND_TLS_KEY_FILE:-}"
        if [[ -z "$cert" || -z "$key" ]]; then
            echo "[run-backend] BACKEND_TLS_MODE=direct requires BACKEND_TLS_CERT_FILE and BACKEND_TLS_KEY_FILE" >&2
            exit 1
        fi
        if [[ ! -r "$cert" ]]; then
            echo "[run-backend] cannot read TLS cert: $cert" >&2
            exit 1
        fi
        if [[ ! -r "$key" ]]; then
            echo "[run-backend] cannot read TLS key: $key" >&2
            exit 1
        fi
        # 私鑰權限檢查（OWASP A02 / A05）：拒絕 world-readable / writable
        # POSIX stat 旗標差異：先試 GNU，再退到 BSD
        key_perm="$(stat -c '%a' "$key" 2>/dev/null || stat -f '%Lp' "$key" 2>/dev/null || echo '?')"
        if [[ "$key_perm" =~ ^[0-9]+$ ]]; then
            # 取最後一位（others 的權限位）
            others_octal="${key_perm: -1}"
            if (( others_octal != 0 )); then
                echo "[run-backend] TLS key $key has world-accessible bits ($key_perm); chmod 0640 (or 0600) and retry" >&2
                exit 1
            fi
        fi
        args+=(--ssl-keyfile "$key" --ssl-certfile "$cert")
        ;;
    *)
        echo "[run-backend] unknown BACKEND_TLS_MODE: $mode (expected: nginx | direct)" >&2
        exit 1
        ;;
esac

if [[ -n "$extra_opts" ]]; then
    # shellcheck disable=SC2206
    extra=( $extra_opts )
    args+=("${extra[@]}")
fi

cd /opt/jt-ipam/backend
exec "$VENV_BIN/uvicorn" "${args[@]}"
