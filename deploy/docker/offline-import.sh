#!/usr/bin/env bash
# Load images exported by offline-export.sh and (re)start the stack on an
# air-gapped host. Same script for FIRST INSTALL and UPGRADE: carry over the
# newer archive and run it again. Database migrations run automatically when the
# backend container starts (its entrypoint runs `alembic upgrade head`).
set -euo pipefail
cd "$(dirname "$0")"

ARCHIVE="${1:-}"
if [[ -z "$ARCHIVE" || ! -f "$ARCHIVE" ]]; then
  echo "usage: ./offline-import.sh <jt-ipam-images-*.tar.gz>" >&2
  exit 1
fi
if [[ ! -f .env ]]; then
  echo "[import] .env not found — run ./gen-env.sh first (first install only)." >&2
  exit 1
fi

dc() { if docker compose version >/dev/null 2>&1; then docker compose "$@"; else docker-compose "$@"; fi; }

echo "[import] loading images from $ARCHIVE ..."
docker load -i "$ARCHIVE"
echo "[import] starting stack (--no-build --pull never: images come only from the archive)..."
dc up -d --no-build --pull never
echo "[import] done. follow boot/migration logs with: docker compose logs -f backend"
