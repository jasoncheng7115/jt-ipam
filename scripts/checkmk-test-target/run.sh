#!/bin/bash
# 可丟棄的 Checkmk 測試靶：2.4（Raw）與 2.5（Community）各一個容器，只在 docker 內部網路，不綁主機的連接埠。
#
#   bash scripts/checkmk-test-target/run.sh       # 起容器、建 5 台測試主機與唯讀自動化帳號 jt-ipam-ro
#   docker rm -f jtipam-cmk24 jtipam-cmk25          # 用完收掉
#
# 起好之後照最後印出的那行跑 tests/test_checkmk_real.py。
set -euo pipefail
cd "$(dirname "$0")"
urls=()
for spec in "cmk24 checkmk/check-mk-raw:2.4.0-latest" "cmk25 checkmk/check-mk-community:2.5.0-latest"; do
  set -- $spec
  docker rm -f "jtipam-$1" >/dev/null 2>&1 || true
  docker run -d --name "jtipam-$1" --tmpfs /opt/omd/sites/cmk/tmp:uid=1000,gid=1000 \
    -e CMK_PASSWORD='Cmk-Admin-2026!' -e CMK_SITE_ID=cmk "$2" >/dev/null
done
for c in jtipam-cmk24 jtipam-cmk25; do
  for _ in $(seq 1 60); do
    [ "$(docker inspect -f '{{.State.Health.Status}}' $c)" = healthy ] && break
    sleep 5
  done
  ip=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' $c)
  python3 seed.py "http://$ip:5000/cmk" >/dev/null
  urls+=("http://$ip:5000/cmk")
done
sleep 60   # 讓監控核心把每台主機至少檢查一次
ips=$(printf '%s\n' "${urls[@]}" | sed -E 's#http://([^:]+):.*#\1/32#' | paste -sd, -)
echo "JTIPAM_TEST_CHECKMK=$(IFS=,; echo "${urls[*]}") OUTBOUND_ALLOW_CIDRS=$ips pytest tests/test_checkmk_real.py"
