#!/usr/bin/env bash
# =============================================================================
# jt-ipam local CI — pre-release gate (mirrors TEST_CHECKLIST.md static checks + optional integration tests)
#
# Default (fast, no DB):
#   - backend imports
#   - backend ruff (lint + security "S" bandit rules)
#   - backend pytest collection (DB tests are skipped)
#   - frontend vue-tsc
#   - frontend eslint
#   - frontend i18n compile scan (catches literal @ { } | that blank the prod render)
#   - frontend build
#
# With --db: also run backend integration tests (requires JTIPAM_TEST_DATABASE_URL
#            pointing at a disposable test DB that has been alembic upgrade head'd).
#
# Usage:
#   scripts/ci.sh                # static checks
#   JTIPAM_TEST_DATABASE_URL=... scripts/ci.sh --db    # including integration tests
# Exits non-zero if any step fails.
# =============================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAIL=0
step() { echo -e "\n\033[1;36m== $* ==\033[0m"; }
ok()   { echo -e "\033[1;32mPASS\033[0m $*"; }
bad()  { echo -e "\033[1;31mFAIL\033[0m $*"; FAIL=1; }

# ── backend ──
if [[ -x "$ROOT/backend/.venv/bin/python" ]]; then
  cd "$ROOT/backend"
  step "backend import"
  if .venv/bin/python -c "import app.main" 2>/dev/null; then ok "import app.main"; else bad "import app.main (check for missing env: set -a; source <env>; set +a)"; fi

  step "backend ruff (lint + security S-rules)"
  if [[ -x .venv/bin/ruff ]]; then
    if .venv/bin/ruff check app/ >/dev/null 2>&1; then ok "ruff"; else .venv/bin/ruff check app/ 2>&1 | tail -40; bad "ruff"; fi
  else
    echo "ruff not installed in .venv — skipping"
  fi

  # The scan agent and the sync/maintenance scripts run with real privileges on
  # customer machines, yet used to sit outside every security check we run.
  # Same bandit ("S") rules, narrower scope -- see per-file-ignores in pyproject.
  step "scripts + agent security rules (bandit S)"
  if [[ -x .venv/bin/ruff ]]; then
    if .venv/bin/ruff check --select S "$ROOT/scripts" "$ROOT/agent" >/dev/null 2>&1; then
      ok "scripts/agent S-rules"
    else
      .venv/bin/ruff check --select S "$ROOT/scripts" "$ROOT/agent" 2>&1 | tail -40
      bad "scripts/agent S-rules"
    fi
  fi

  # 這兩支守的是安裝／升級腳本本身（CLI 分派、歷史分歧時的自動復原）。
  # 原本沒有被任何 CI 跑到 —— 沒有人跑的守門等於沒有。
  step "installer/upgrade shell tests"
  for t in "$ROOT"/scripts/tests/*.sh; do
    if bash "$t" >/dev/null 2>&1; then ok "$(basename "$t")"; else bash "$t" 2>&1 | tail -20; bad "$(basename "$t")"; fi
  done

  step "backend pytest collect"
  if .venv/bin/pytest -q --collect-only >/dev/null 2>&1; then ok "pytest collect"; else bad "pytest collect"; fi

  if [[ "${1:-}" == "--db" ]]; then
    step "backend pytest (integration, needs JTIPAM_TEST_DATABASE_URL)"
    if [[ -n "${JTIPAM_TEST_DATABASE_URL:-}" ]]; then
      if .venv/bin/pytest -q; then ok "pytest"; else bad "pytest"; fi
    else
      bad "JTIPAM_TEST_DATABASE_URL not set, skipping integration tests"
    fi
  fi
else
  bad "backend/.venv not found -- skipping backend checks"
fi

# ── frontend ──
# The build needs Node >= 22 (the version in .nvmrc). A machine shared with other projects may
# well have an older Node as the system one -- and it must stay that way, or the
# other projects break. So: if the Node in PATH is too old, switch to the version
# in .nvmrc through nvm, for this script only. Failing loudly beats a build that
# dies inside esbuild with an error that names neither Node nor the version.
if [[ -d "$ROOT/frontend/node_modules" ]]; then
  node_major="$(node -v 2>/dev/null | sed 's/^v//; s/\..*//')"
  if [[ -z "$node_major" || "$node_major" -lt 22 ]]; then
    if [[ -s "${NVM_DIR:-$HOME/.nvm}/nvm.sh" ]]; then
      # shellcheck disable=SC1091
      . "${NVM_DIR:-$HOME/.nvm}/nvm.sh" && nvm use >/dev/null 2>&1 || nvm use 22 >/dev/null 2>&1
      echo "node: switched to $(node -v 2>/dev/null) via nvm (system node was v${node_major:-none})"
    fi
    node_major="$(node -v 2>/dev/null | sed 's/^v//; s/\..*//')"
    if [[ -z "$node_major" || "$node_major" -lt 22 ]]; then
      bad "node >= 22 required (found $(node -v 2>/dev/null || echo none)); install it or run: nvm install"
    fi
  fi
  cd "$ROOT/frontend"
  step "frontend vue-tsc"
  if npx vue-tsc --noEmit; then ok "vue-tsc"; else bad "vue-tsc"; fi
  step "frontend eslint"
  if npx eslint src --ext .ts,.vue >/dev/null 2>&1; then ok "eslint"; else npx eslint src --ext .ts,.vue 2>&1 | tail -40; bad "eslint"; fi
  step "frontend i18n compile scan"
  if node scripts/check-i18n.mjs; then ok "i18n"; else bad "i18n — escape literal @ { } | in the messages above"; fi
  step "frontend naive-ui imports"
  if node scripts/check-naive-imports.mjs; then ok "naive imports"; else bad "naive imports -- a component used in a template is not imported; it silently disappears at runtime"; fi
  step "frontend build"
  # 型別檢查另外跑：正式的 build 不含 vue-tsc（2026-11-06：8 GB 主機上 vue-tsc 超過 Node 預設 2 GB 上限，升級建置失敗）
  if npm run typecheck >/dev/null 2>&1; then ok "typecheck"; else bad "typecheck"; fi
  if npm run build >/dev/null 2>&1; then ok "build"; else bad "build"; fi
else
  bad "frontend/node_modules not found -- skipping frontend checks"
fi

# Bold that will not render: `**「text」**` is printed literally, because a `**` run
# cannot open when it follows a Han character and precedes a full-width bracket.
# NB: the frontend block above leaves us in $ROOT/frontend — come back first.
cd "$ROOT"
step "docs bold syntax (CJK)"
if python3 scripts/check-md-bold.py README.md README_zh-TW.md CHANGELOG.md CHANGELOG_zh-TW.md \
       docs/INSTALL.md docs/INSTALL_zh-TW.md TEST_CHECKLIST.md >/dev/null 2>&1; then
  ok "docs bold"
else
  python3 scripts/check-md-bold.py README.md README_zh-TW.md CHANGELOG.md CHANGELOG_zh-TW.md \
       docs/INSTALL.md docs/INSTALL_zh-TW.md TEST_CHECKLIST.md 2>&1 | tail -12
  bad "docs bold -- these ** will be printed as-is; write 「**text**」 instead"
fi

echo
if [[ $FAIL -eq 0 ]]; then echo -e "\033[1;32mCI OK — safe to release\033[0m"; else echo -e "\033[1;31mCI FAILED — fix the red items before releasing\033[0m"; fi
exit $FAIL
