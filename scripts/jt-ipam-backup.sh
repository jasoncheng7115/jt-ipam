#!/bin/bash
# =============================================================================
# jt-ipam backup script
#
# Backup contents:
#   1. PostgreSQL: pg_dump -Fc (all data + alembic_version)
#   2. /etc/jt-ipam/backend.env       — SECRET_KEY/ENCRYPTION_KEY
#   3. /etc/jt-ipam/tls/              — self-signed certs (if TLS_MODE=direct)
#
# Run daily by jt-ipam-backup.timer. Retained for RETENTION_DAYS days; older ones auto-deleted.
#
# Security:
#   - Output files 0600; directories 0700
#   - The whole /var/backups/jt-ipam/ should be pushed off-site encrypted via ssh/s3
# =============================================================================

set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/var/backups/jt-ipam}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
ENV_FILE="${ENV_FILE:-/etc/jt-ipam/backend.env}"
TLS_DIR="${TLS_DIR:-/etc/jt-ipam/tls}"

if [[ ! -r "$ENV_FILE" ]]; then
    echo "FATAL: cannot read $ENV_FILE" >&2
    exit 1
fi

# shellcheck disable=SC1090
set -a; source <(grep -E '^(POSTGRES_|PG)' "$ENV_FILE"); set +a

PG_DB="${POSTGRES_DB:-jt_ipam}"
PG_USER="${POSTGRES_USER:-jt_ipam}"
PG_HOST="${POSTGRES_HOST:-127.0.0.1}"
PG_PORT="${POSTGRES_PORT:-5432}"

DATE_STAMP="$(date +%F)"
TARGET_DIR="$BACKUP_DIR/$DATE_STAMP"

install -d -m 0700 -o jtipam -g jtipam "$BACKUP_DIR" 2>/dev/null || \
  install -d -m 0700 "$BACKUP_DIR"
install -d -m 0700 "$TARGET_DIR"

echo "[$(date -Iseconds)] backup starting → $TARGET_DIR"

# ── 1. pg_dump ──
DUMP_FILE="$TARGET_DIR/jt-ipam-${DATE_STAMP}.dump"
PGPASSWORD="${POSTGRES_PASSWORD:-}" pg_dump \
    -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" \
    -Fc --no-owner --no-acl \
    -f "$DUMP_FILE" "$PG_DB"
chmod 0600 "$DUMP_FILE"
echo "  pg_dump: $(du -h "$DUMP_FILE" | awk '{print $1}')"

# ── 2. env ──
cp -p "$ENV_FILE" "$TARGET_DIR/backend.env"
chmod 0600 "$TARGET_DIR/backend.env"

# ── 3. TLS certs (if present) ──
if [[ -d "$TLS_DIR" ]]; then
    tar -czf "$TARGET_DIR/tls.tar.gz" -C "$(dirname "$TLS_DIR")" "$(basename "$TLS_DIR")" 2>/dev/null
    chmod 0600 "$TARGET_DIR/tls.tar.gz"
fi

# ── 3b. Uploaded files (data-center floor plans, etc.); stored on filesystem, must be backed up together for a full restore ──
UPLOAD_DIR="${UPLOAD_DIR:-/var/lib/jt-ipam/uploads}"
if [[ -d "$UPLOAD_DIR" ]]; then
    tar -czf "$TARGET_DIR/uploads.tar.gz" -C "$(dirname "$UPLOAD_DIR")" "$(basename "$UPLOAD_DIR")" 2>/dev/null
    chmod 0600 "$TARGET_DIR/uploads.tar.gz"
    echo "  uploads: $(du -h "$TARGET_DIR/uploads.tar.gz" 2>/dev/null | awk '{print $1}')"
fi

# ── 4. Prune expired backups ──
find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -mtime "+$RETENTION_DAYS" -exec rm -rf {} +

echo "[$(date -Iseconds)] backup OK"
