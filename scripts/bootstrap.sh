#!/usr/bin/env bash
#
# jt-ipam one-shot install bootstrap: clone to /opt/jt-ipam then run install directly.
# Usage (no manual git needed):
#   curl -fsSL https://raw.githubusercontent.com/jasoncheng7115/jt-ipam/main/scripts/bootstrap.sh | sudo bash
#   # arguments can be passed: ... | sudo bash -s -- --tls-mode direct
#
set -euo pipefail

REPO="${JT_IPAM_REPO:-https://github.com/jasoncheng7115/jt-ipam.git}"
DIR="${JT_IPAM_DIR:-/opt/jt-ipam}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "[error] Please run as root (sudo)." >&2
  exit 1
fi

# install git first if missing (Debian/Ubuntu)
if ! command -v git >/dev/null 2>&1; then
  echo "[*] Installing git..."
  apt-get update -qq && apt-get install -y -qq git
fi

if [[ -d "$DIR/.git" ]]; then
  echo "[*] $DIR is already a git repo, using existing code (run upgrade to update)."
else
  echo "[*] git clone $REPO -> $DIR"
  git clone "$REPO" "$DIR"
fi

cd "$DIR"
echo "[*] Running scripts/jt-ipam.sh install $*"
exec bash scripts/jt-ipam.sh install "$@"
