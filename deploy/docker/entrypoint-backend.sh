#!/usr/bin/env bash
# jt-ipam backend container entrypoint:
#   1. wait for PostgreSQL
#   2. run DB migrations (alembic upgrade head)  <- this is what makes version upgrades automatic
#   3. optionally create the first admin (only if JT_IPAM_ADMIN_* env are set)
#   4. exec the given command (uvicorn by default)
set -euo pipefail

cd /app/backend

PGHOST="${POSTGRES_HOST:-postgres}"
PGPORT="${POSTGRES_PORT:-5432}"
PGUSER="${POSTGRES_USER:-jt_ipam}"

echo "[entrypoint] waiting for postgres at ${PGHOST}:${PGPORT} ..."
until pg_isready -h "${PGHOST}" -p "${PGPORT}" -U "${PGUSER}" >/dev/null 2>&1; do
    sleep 1
done
echo "[entrypoint] postgres is ready"

echo "[entrypoint] applying database migrations (alembic upgrade head)"
alembic upgrade head

# Optional first-admin creation. create-admin errors if the admin already exists
# (unless --force-update), so we tolerate that and keep going.
if [[ -n "${JT_IPAM_ADMIN_USERNAME:-}" && -n "${JT_IPAM_ADMIN_PASSWORD:-}" ]]; then
    force_flag=()
    [[ "${JT_IPAM_ADMIN_FORCE:-}" == "1" ]] && force_flag=(--force-update)
    echo "[entrypoint] ensuring admin user '${JT_IPAM_ADMIN_USERNAME}'"
    if printf '%s' "${JT_IPAM_ADMIN_PASSWORD}" | python -m app.cli.bootstrap create-admin \
        --username "${JT_IPAM_ADMIN_USERNAME}" \
        --email "${JT_IPAM_ADMIN_EMAIL:-admin@example.com}" \
        --password-stdin "${force_flag[@]}"; then
        echo "[entrypoint] admin ensured"
    else
        echo "[entrypoint] admin already exists or was skipped (this is fine)"
    fi
fi

echo "[entrypoint] starting: $*"
exec "$@"
