#!/usr/bin/env bash
# Build all images on an internet-connected host and save them into ONE archive,
# so they can be carried to an air-gapped (no-internet) host and loaded there.
#
#   online host :  ./offline-export.sh            -> jt-ipam-images-<sha>.tar.gz
#   air-gapped  :  ./offline-import.sh <archive>
#
# Why: on the internal host `docker compose build` / pull can't reach Docker Hub,
# so the app images AND the base images (postgres/redis) must travel pre-built.
set -euo pipefail
cd "$(dirname "$0")"
REPO_ROOT="$(git -C ../.. rev-parse --show-toplevel 2>/dev/null || (cd ../.. && pwd))"

dc() { if docker compose version >/dev/null 2>&1; then docker compose "$@"; else docker-compose "$@"; fi; }

# Base images the compose pulls — they can't be pulled on the air-gapped host, so ship them too.
BASE_IMAGES="pgvector/pgvector:pg16 redis:7-alpine"
APP_IMAGES="jt-ipam-backend:local jt-ipam-web:local"

sha="$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo manual)"
OUT="${1:-jt-ipam-images-${sha}.tar.gz}"

echo "[export] building app images (jt-ipam-backend / jt-ipam-web)..."
dc build
echo "[export] pulling base images: $BASE_IMAGES"
for img in $BASE_IMAGES; do docker pull "$img"; done
echo "[export] saving all images -> $OUT (this file is large)..."
docker save $APP_IMAGES $BASE_IMAGES | gzip > "$OUT"

echo
echo "[export] done: $OUT  ($(du -h "$OUT" | cut -f1))"
echo "[export] carry to the air-gapped host:"
echo "         1) $OUT"
echo "         2) the whole jt-ipam repo folder (compose needs the build-context path to exist,"
echo "            even though --no-build means it is never rebuilt there)"
echo "[export] then on the air-gapped host, in deploy/docker/:"
echo "         first install :  ./gen-env.sh && ./offline-import.sh $OUT"
echo "         upgrade       :  ./offline-import.sh $OUT     (.env stays; just load the newer archive)"
