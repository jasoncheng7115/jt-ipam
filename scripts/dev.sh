#!/usr/bin/env bash
# =============================================================================
# jt-ipam — 開發模式啟動（無容器）
#
# 前置：本機已裝好 Python 3.12 / pnpm / 本機可連 Postgres + Redis（或遠端）
#
# 用法：
#   1. 建立 backend/.env（複製自 .env.example 並填值）
#   2. ./scripts/dev.sh setup    # 建立 venv + 安裝依賴 + alembic upgrade
#   3. ./scripts/dev.sh up       # 啟動 backend (8000) + frontend (5173)
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
            echo "[warn] $ENV_FILE 不存在；請先 cp .env.example .env 並填值"
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
        # dev 預設 direct TLS + 自簽（避免 prod guard 擋 https://localhost）
        export UVICORN_EXTRA_OPTS="${UVICORN_EXTRA_OPTS:-} --reload"
        echo "[up] backend (TLS=${BACKEND_TLS_MODE:-nginx}) on ${BACKEND_BIND_HOST:-127.0.0.1}:${BACKEND_BIND_PORT:-8000}"
        echo "     frontend on :5173 — Ctrl+C 結束兩者"
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
用法：./scripts/dev.sh <command>

  setup            建立 venv、安裝依賴、跑 alembic upgrade head、安裝前端依賴
  up               同時啟動 backend (uvicorn --reload) 與 frontend (vite)
  migrate ARGS...  執行 alembic（如 ./scripts/dev.sh migrate revision --autogenerate -m "x"）
  test ARGS...     執行 pytest

預設指令：up
USAGE
        exit 1
        ;;
esac
