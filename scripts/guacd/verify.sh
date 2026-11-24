#!/bin/bash
# 驗 build.sh 編出來的每一份：乾淨容器只裝執行期套件 → guacd 跑得起來、rdp／vnc／ssh 外掛載得到 →
# 預設再真的連三種協定：RDP 連 xrdp 測試靶（scripts/rdp-test-target）、VNC 連最小 RFB 靶
# （e2e/fixtures/vnc-target.py）、SSH 連容器裡臨時起的 sshd；RDP／SSH 還會打一行字。截圖存在同目錄。
#
# 用法：scripts/guacd/verify.sh [映像 …]        不給就驗 targets.txt 全部
#       NO_RDP=1 只驗外掛（沒有 docker 映像建置權限、或只想快速看一下時）
# 為什麼一定要真的連：1.6.0 在 Ubuntu 26.04 上外掛載得到，一畫第一個畫面就 segfault。
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
. "$HERE/source.env"
DIST="$ROOT/dist/guacd/${GUACD_VERSION}-${GUACD_BUILD_REV}"
[ -d "$DIST" ] || { echo "先跑 scripts/guacd/build.sh（找不到 $DIST）"; exit 1; }
if [ $# -gt 0 ]; then TARGETS=("$@"); else mapfile -t TARGETS < <(grep -v '^\s*#' "$HERE/targets.txt" | grep -v '^\s*$'); fi

RDP_TARGET=""
if [ -z "${NO_RDP:-}" ]; then
  docker image inspect jtipam-rdp-target >/dev/null 2>&1 || docker build -q -t jtipam-rdp-target "$ROOT/scripts/rdp-test-target" >/dev/null
  docker rm -f -v jtipam-guacd-rdp-target >/dev/null 2>&1 || true
  # 只綁 127.0.0.1：這台 RDP 伺服器的密碼寫在原始碼裡
  docker run -d --name jtipam-guacd-rdp-target -p 127.0.0.1:13390:3389 jtipam-rdp-target >/dev/null
  trap 'docker rm -f -v jtipam-guacd-rdp-target >/dev/null 2>&1 || true' EXIT
  sleep 3
  RDP_TARGET="127.0.0.1:13390:rdpuser:TestPw-123"
fi

fail=0
n=0
for img in "${TARGETS[@]}"; do
  n=$((n + 1)); port=$((14820 + n))
  (
    . <(docker run --rm "$img" cat /etc/os-release)
    pkg=$(cd "$DIST" && ls "jt-ipam-guacd-${GUACD_VERSION}-${GUACD_BUILD_REV}-${ID}${VERSION_ID}-"*.tar.gz 2>/dev/null | head -1)
    [ -n "$pkg" ] || { echo "FAILED $img：沒有對應的預編檔"; exit 1; }
    pkg="${pkg%.tar.gz}"
    docker run --rm --network host -e APT_MIRROR="${APT_MIRROR:-}" -e UBUNTU_MIRROR="${UBUNTU_MIRROR:-}" \
      -e RDP_TARGET="$RDP_TARGET" -e GUACD_PORT="$port" -v "$HERE":/src:ro -v "$DIST":/dist \
      -v "$ROOT/frontend/e2e/fixtures/vnc-target.py":/vnc-target.py:ro "$img" \
      bash /src/in-container-verify.sh "$pkg" > "$DIST/.verify-${pkg}.log" 2>&1 \
      && echo "VERIFIED $pkg（$(grep -hE '^(rdp|vnc|ssh):' "$DIST/.verify-${pkg}.log" | cut -c1-40 | tr '\n' ' ')）" \
      || { echo "FAILED $img（見 $DIST/.verify-${pkg}.log）"; exit 1; }
  ) &
done
for job in $(jobs -p); do wait "$job" || fail=1; done
exit $fail
