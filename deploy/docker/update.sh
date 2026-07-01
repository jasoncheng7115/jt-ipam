#!/usr/bin/env bash
# Update a Docker Compose deployment to the latest version.
#   git pull  ->  rebuild images  ->  recreate containers
# Database migrations run automatically when the backend container starts
# (the entrypoint runs `alembic upgrade head`), so there is no separate step.
set -euo pipefail
cd "$(dirname "$0")"
REPO_ROOT="$(git -C ../.. rev-parse --show-toplevel)"

dc() { if docker compose version >/dev/null 2>&1; then docker compose "$@"; else docker-compose "$@"; fi; }

echo "[update] current: $(git -C "$REPO_ROOT" rev-parse --short HEAD)"
echo "[update] pulling latest source..."
git -C "$REPO_ROOT" pull --ff-only

echo "[update] rebuilding images..."
dc build

echo "[update] recreating containers (alembic upgrade runs on backend start)..."
dc up -d

echo "[update] now at: $(git -C "$REPO_ROOT" rev-parse --short HEAD)"
echo "[update] follow migration/boot logs with: docker compose logs -f backend"
