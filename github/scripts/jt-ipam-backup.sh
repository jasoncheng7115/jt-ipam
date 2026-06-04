#!/bin/bash
# =============================================================================
# jt-ipam 備份腳本
#
# 備份內容：
#   1. PostgreSQL：pg_dump -Fc（含所有資料 + alembic_version）
#   2. /etc/jt-ipam/backend.env       — SECRET_KEY/ENCRYPTION_KEY
#   3. /etc/jt-ipam/tls/              — 自簽憑證（若 TLS_MODE=direct）
#
# 由 jt-ipam-backup.timer 每天執行。保留 RETENTION_DAYS 天，舊的自動刪。
#
# 安全：
#   - 輸出檔案 0600；目錄 0700
#   - 整個 /var/backups/jt-ipam/ 應該再透過 ssh/s3 加密推到異地
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

# ── 3. TLS 憑證（如果有）──
if [[ -d "$TLS_DIR" ]]; then
    tar -czf "$TARGET_DIR/tls.tar.gz" -C "$(dirname "$TLS_DIR")" "$(basename "$TLS_DIR")" 2>/dev/null
    chmod 0600 "$TARGET_DIR/tls.tar.gz"
fi

# ── 3b. 上傳檔（機房平面圖等）；filesystem 儲存，需一起備份才能完整還原 ──
UPLOAD_DIR="${UPLOAD_DIR:-/var/lib/jt-ipam/uploads}"
if [[ -d "$UPLOAD_DIR" ]]; then
    tar -czf "$TARGET_DIR/uploads.tar.gz" -C "$(dirname "$UPLOAD_DIR")" "$(basename "$UPLOAD_DIR")" 2>/dev/null
    chmod 0600 "$TARGET_DIR/uploads.tar.gz"
    echo "  uploads: $(du -h "$TARGET_DIR/uploads.tar.gz" 2>/dev/null | awk '{print $1}')"
fi

# ── 4. 過期清理 ──
find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -mtime "+$RETENTION_DAYS" -exec rm -rf {} +

echo "[$(date -Iseconds)] backup OK"
