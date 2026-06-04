#!/usr/bin/env bash
# =============================================================================
# jt-ipam 本地 CI — 升版前把關（對應 TEST_CHECKLIST.md 的靜態檢查 + 選用整合測試）
#
# 預設（快、免 DB）：
#   - 後端可 import
#   - 後端 pytest 收集（DB 測試會 skip）
#   - 前端 vue-tsc
#   - 前端 build
#
# 加 --db：另跑後端整合測試（需設 JTIPAM_TEST_DATABASE_URL 指向拋棄式 test DB，
#          且該 DB 已 alembic upgrade head）。
#
# 用法：
#   scripts/ci.sh                # 靜態檢查
#   JTIPAM_TEST_DATABASE_URL=... scripts/ci.sh --db    # 連同整合測試
# 任一步失敗即以非 0 結束。
# =============================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAIL=0
step() { echo -e "\n\033[1;36m== $* ==\033[0m"; }
ok()   { echo -e "\033[1;32mPASS\033[0m $*"; }
bad()  { echo -e "\033[1;31mFAIL\033[0m $*"; FAIL=1; }

# ── 後端 ──
if [[ -x "$ROOT/backend/.venv/bin/python" ]]; then
  cd "$ROOT/backend"
  step "backend import"
  if .venv/bin/python -c "import app.main" 2>/dev/null; then ok "import app.main"; else bad "import app.main（檢查是否缺 env：set -a; source <env>; set +a）"; fi

  step "backend pytest collect"
  if .venv/bin/pytest -q --collect-only >/dev/null 2>&1; then ok "pytest collect"; else bad "pytest collect"; fi

  if [[ "${1:-}" == "--db" ]]; then
    step "backend pytest (integration, needs JTIPAM_TEST_DATABASE_URL)"
    if [[ -n "${JTIPAM_TEST_DATABASE_URL:-}" ]]; then
      if .venv/bin/pytest -q; then ok "pytest"; else bad "pytest"; fi
    else
      bad "JTIPAM_TEST_DATABASE_URL 未設，跳過整合測試"
    fi
  fi
else
  bad "找不到 backend/.venv —— 略過後端檢查"
fi

# ── 前端 ──
if [[ -d "$ROOT/frontend/node_modules" ]]; then
  cd "$ROOT/frontend"
  step "frontend vue-tsc"
  if npx vue-tsc --noEmit; then ok "vue-tsc"; else bad "vue-tsc"; fi
  step "frontend build"
  if npm run build >/dev/null 2>&1; then ok "build"; else bad "build"; fi
else
  bad "找不到 frontend/node_modules —— 略過前端檢查"
fi

echo
if [[ $FAIL -eq 0 ]]; then echo -e "\033[1;32mCI OK — 可以升版\033[0m"; else echo -e "\033[1;31mCI FAILED — 先修紅的再升版\033[0m"; fi
exit $FAIL
