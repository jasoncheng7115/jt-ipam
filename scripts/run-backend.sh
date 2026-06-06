#!/usr/bin/env bash
# =============================================================================
# jt-ipam — backend uvicorn launch wrapper
#
# Invoked by systemd (jt-ipam-backend.service) or dev.sh; decides whether to
# enable uvicorn's built-in TLS based on BACKEND_TLS_MODE.
#
# Environment variables (read from /etc/jt-ipam/backend.env or backend/.env):
#   BACKEND_TLS_MODE         nginx | direct (default nginx)
#   BACKEND_BIND_HOST        bind host (must be loopback in nginx mode)
#   BACKEND_BIND_PORT        bind port
#   BACKEND_TLS_CERT_FILE    PEM certificate for direct mode
#   BACKEND_TLS_KEY_FILE     PEM private key for direct mode
#   UVICORN_WORKERS          number of workers (default 4)
#   UVICORN_EXTRA_OPTS       extra flags (e.g. --reload)
#
# OWASP mapping:
#   * A02: enforce TLS — in direct mode, verify cert/key exist + sane permissions before start
#   * A05: replace the current process via exec so the systemd watchdog can manage it correctly
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
        # Backend serves plain HTTP; nginx terminates HTTPS.
        # _tls_guards in config.py rejects any non-loopback host.
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
        # Private key permission check (OWASP A02 / A05): reject world-readable / writable
        # POSIX stat flag differences: try GNU first, then fall back to BSD
        key_perm="$(stat -c '%a' "$key" 2>/dev/null || stat -f '%Lp' "$key" 2>/dev/null || echo '?')"
        if [[ "$key_perm" =~ ^[0-9]+$ ]]; then
            # take the last digit (the others permission bits)
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
