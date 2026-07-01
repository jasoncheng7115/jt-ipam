#!/usr/bin/env bash
# Create deploy/docker/.env from .env.example with freshly generated random secrets.
# Re-running will NOT overwrite an existing .env (so your secrets stay stable).
set -euo pipefail
cd "$(dirname "$0")"

if [[ -f .env ]]; then
    echo "[gen-env] .env already exists — leaving it untouched."
    echo "[gen-env] delete it first if you really want fresh secrets (this invalidates encrypted data)."
    exit 0
fi

if ! command -v openssl >/dev/null 2>&1; then
    echo "[gen-env] openssl is required to generate secrets." >&2
    exit 1
fi

secret_key="$(openssl rand -hex 64)"
encryption_key="$(openssl rand -base64 32)"
audit_genesis="$(openssl rand -hex 32)"
pg_password="$(openssl rand -hex 24)"
# admin password: hex (no sed-special chars), 32 chars — the backend creates the
# admin on first boot using this, so you can log in straight away.
admin_password="$(openssl rand -hex 16)"

sed \
    -e "s|^SECRET_KEY=.*|SECRET_KEY=${secret_key}|" \
    -e "s|^ENCRYPTION_KEY=.*|ENCRYPTION_KEY=${encryption_key}|" \
    -e "s|^AUDIT_CHAIN_GENESIS=.*|AUDIT_CHAIN_GENESIS=${audit_genesis}|" \
    -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${pg_password}|" \
    -e "s|^JT_IPAM_ADMIN_PASSWORD=.*|JT_IPAM_ADMIN_PASSWORD=${admin_password}|" \
    .env.example > .env

chmod 600 .env
echo "[gen-env] wrote .env with random secrets (chmod 600)."
echo "[gen-env] the admin account will be created on first boot:"
echo "[gen-env]     username: admin"
echo "[gen-env]     password: ${admin_password}"
echo "[gen-env] (also stored as JT_IPAM_ADMIN_PASSWORD in .env — change it after first login)"
echo "[gen-env] review APP_PUBLIC_URL / JT_IPAM_SERVER_NAME / ports, then: docker compose up -d --build"
