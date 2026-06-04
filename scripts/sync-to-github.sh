#!/usr/bin/env bash
# =============================================================================
# jt-ipam → GitHub 發佈根同步（Mode B）。
#
# 把「可公開」的子集同步進 github/（= 公開 repo 根），並做機密淨化：
#   - 私有資產不進去：CLAUDE.md、本腳本、記憶、build 產物、node_modules、.venv、uploads
#   - 淨化「會識別到內網/本人」的值：prod IP、掃描網段、真實網域 → 文件用保留範例
# 之後在 github/ 跑 §10 掃描，乾淨才 commit/push。
#
# 用法：bash scripts/sync-to-github.sh   （不 push，只建 github/）
# =============================================================================
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PUB="$ROOT/github"

echo "==> rebuild $PUB"
rm -rf "$PUB"
mkdir -p "$PUB"

# ── 1. 同步可公開內容（rsync，排除私有/產物）──
rsync -a \
  --exclude='.git/' \
  --exclude='github/' \
  --exclude='images/' \
  --exclude='.claude/' \
  --exclude='.claude.json' \
  --exclude='CLAUDE.md' \
  --exclude='scripts/sync-to-github.sh' \
  --exclude='**/__pycache__/' --exclude='*.pyc' --exclude='*.pyo' \
  --exclude='backend/.venv/' \
  --exclude='frontend/node_modules/' --exclude='frontend/dist/' \
  --exclude='**/.pytest_cache/' \
  --exclude='frontend/test-results/' --exclude='frontend/playwright-report/' \
  --exclude='*.log' --exclude='*.sqlite' \
  --exclude='.env' --exclude='*.key' --exclude='*.pem' \
  --exclude='.DS_Store' \
  "$ROOT"/ "$PUB"/

# ── 2. 機密淨化（只在 github/ 內，動不到開發目錄）──
#  prod IP / 掃描網段 → RFC5737 文件保留網段；真實網域 → RFC2606 example.*
mapfile -t FILES < <(grep -rlIE '192\.168\.1\.144|172\.16\.100|jason\.tools|jsjss\.com\.tw|shifeng-sg\.com' "$PUB" 2>/dev/null || true)
for f in "${FILES[@]}"; do
  sed -i \
    -e 's/192\.168\.1\.144/192.0.2.10/g' \
    -e 's/172\.16\.100/198.51.100/g' \
    -e 's/jsjss\.com\.tw/example.net/g' \
    -e 's/shifeng-sg\.com/example.org/g' \
    -e 's/jason\.tools/example.com/g' \
    "$f"
done
echo "==> sanitized ${#FILES[@]} files"

# ── 3. 提醒：跑 §10 掃描 ──
echo "==> next: scan $PUB for residual secrets/internal refs, then commit/push from $PUB"
