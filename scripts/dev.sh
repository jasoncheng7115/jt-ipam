#!/usr/bin/env bash
# =============================================================================
# jt-ipam — development mode launcher (no containers)
#
# Prerequisites: Python 3.12 / pnpm installed locally, plus local or remote
# Postgres + Redis reachable.
#
# Usage:
#   1. Create backend/.env (copy from .env.example and fill in values)
#   2. ./scripts/dev.sh setup    # create venv + install deps + alembic upgrade
#   3. ./scripts/dev.sh up       # start backend (8000) + frontend (5173)
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$REPO_ROOT/backend"
FRONTEND_DIR="$REPO_ROOT/frontend"
ENV_FILE="$BACKEND_DIR/.env"

cmd="${1:-up}"

case "$cmd" in
    setup)
        echo "[setup] backend venv + deps"
        cd "$BACKEND_DIR"
        python3.12 -m venv .venv
        .venv/bin/pip install --upgrade pip wheel
        .venv/bin/pip install -e ".[dev]"

        if [[ -f "$ENV_FILE" ]]; then
            echo "[setup] alembic upgrade head"
            set -a; source "$ENV_FILE"; set +a
            .venv/bin/alembic upgrade head
        else
            echo "[warn] $ENV_FILE not found; run cp .env.example .env and fill in values first"
        fi

        echo "[setup] frontend deps"
        cd "$FRONTEND_DIR"
        pnpm install
        echo "[done]"
        ;;
    up)
        if [[ ! -f "$ENV_FILE" ]]; then
            echo "[error] $ENV_FILE missing — copy from .env.example first" >&2
            exit 1
        fi
        cd "$BACKEND_DIR"
        set -a; source "$ENV_FILE"; set +a
        # dev defaults to direct TLS + self-signed (avoids prod guard blocking https://localhost)
        export UVICORN_EXTRA_OPTS="${UVICORN_EXTRA_OPTS:-} --reload"
        echo "[up] backend (TLS=${BACKEND_TLS_MODE:-nginx}) on ${BACKEND_BIND_HOST:-127.0.0.1}:${BACKEND_BIND_PORT:-8000}"
        echo "     frontend on :5173 — Ctrl+C to stop both"
        "$REPO_ROOT/scripts/run-backend.sh" &
        BACKEND_PID=$!
        cd "$FRONTEND_DIR"
        pnpm dev &
        FRONTEND_PID=$!
        trap 'kill $BACKEND_PID $FRONTEND_PID 2>/dev/null || true' INT TERM EXIT
        wait
        ;;
    migrate)
        cd "$BACKEND_DIR"
        set -a; source "$ENV_FILE"; set +a
        .venv/bin/alembic "${@:2}"
        ;;
    test)
        cd "$BACKEND_DIR"
        set -a; source "$ENV_FILE"; set +a
        .venv/bin/pytest "${@:2}"
        ;;
    *)
        cat <<USAGE
Usage: ./scripts/dev.sh <command>

  setup            create venv, install deps, run alembic upgrade head, install frontend deps
  up               start backend (uvicorn --reload) and frontend (vite) together
  migrate ARGS...  run alembic (e.g. ./scripts/dev.sh migrate revision --autogenerate -m "x")
  test ARGS...     run pytest

Default command: up
USAGE
        exit 1
        ;;
esac
