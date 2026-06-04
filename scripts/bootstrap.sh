#!/usr/bin/env bash
#
# jt-ipam 一鍵安裝引導：clone 到 /opt/jt-ipam 後直接跑 install。
# 用法（不必先手動 git）：
#   curl -fsSL https://raw.githubusercontent.com/jasoncheng7115/jt-ipam/main/scripts/bootstrap.sh | sudo bash
#   # 可帶參數：... | sudo bash -s -- --tls-mode direct
#
set -euo pipefail

REPO="${JT_IPAM_REPO:-https://github.com/jasoncheng7115/jt-ipam.git}"
DIR="${JT_IPAM_DIR:-/opt/jt-ipam}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "[error] 請以 root 執行（sudo）。" >&2
  exit 1
fi

# git 不在就先裝（Debian/Ubuntu）
if ! command -v git >/dev/null 2>&1; then
  echo "[*] 安裝 git…"
  apt-get update -qq && apt-get install -y -qq git
fi

if [[ -d "$DIR/.git" ]]; then
  echo "[*] $DIR 已是 git repo，改用既有程式碼（如需更新請跑 upgrade）。"
else
  echo "[*] git clone $REPO -> $DIR"
  git clone "$REPO" "$DIR"
fi

cd "$DIR"
echo "[*] 執行 scripts/jt-ipam.sh install $*"
exec bash scripts/jt-ipam.sh install "$@"
