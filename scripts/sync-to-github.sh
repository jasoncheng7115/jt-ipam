#!/usr/bin/env bash
# =============================================================================
# jt-ipam → GitHub publish-root sync (Mode B).
#
# Sync the "publishable" subset into github/ (= public repo root) and sanitize secrets:
#   - Private assets stay out: CLAUDE.md, this script, memory, build artifacts, node_modules, .venv, uploads
#   - Sanitize values that identify the internal network / the author: prod IP, scan subnets, real domains -> documentation reserved examples
# Then run the Section 10 scan in github/; only commit/push once clean.
#
# Usage: bash scripts/sync-to-github.sh   (does not push, only builds github/)
# =============================================================================
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PUB="$ROOT/github"

echo "==> rebuild $PUB"
rm -rf "$PUB"
mkdir -p "$PUB"

# ── 1. Sync publishable content (rsync, excluding private/artifacts) ──
rsync -a \
  --exclude='.git/' \
  --exclude='github/' \
  --exclude='images/' \
  --exclude='.claude/' \
  --exclude='.claude.json' \
  --exclude='CLAUDE.md' \
  --exclude='scripts/sync-to-github.sh' \
  --exclude='ai_qa_table.md' \
  --exclude='machine-room-6ping.png' \
  --exclude='**/__pycache__/' --exclude='*.pyc' --exclude='*.pyo' \
  --exclude='backend/.venv/' \
  --exclude='frontend/node_modules/' --exclude='frontend/dist/' \
  --exclude='**/.pytest_cache/' \
  --exclude='frontend/test-results/' --exclude='frontend/playwright-report/' \
  --exclude='*.log' --exclude='*.sqlite' \
  --exclude='.env' --exclude='*.key' --exclude='*.pem' \
  --exclude='.DS_Store' \
  "$ROOT"/ "$PUB"/

# ── 2. Secret sanitization (only inside github/, never touches the dev tree) ──
#  prod IP / scan subnets -> RFC5737 documentation reserved ranges; real domains -> RFC2606 example.*
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

# ── 3. Reminder: run the Section 10 scan ──
echo "==> next: scan $PUB for residual secrets/internal refs, then commit/push from $PUB"
