#!/bin/bash
# 啟動已建立的 OpenStack 測試靶（例如 開發機 重開機之後）。
# 主機重開機後要重新載入 OVS 核心模組、補回 DOCKER-USER 的兩條轉送規則；DevStack 的服務是 systemd 單元，會自己起來。
#
#   scripts/openstack-test-target/start.sh          啟動兩個容器並等 API 回應
#   scripts/openstack-test-target/start.sh stop     停止兩個容器（資料保留）
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
. "$HERE/common.sh"

if [ "${1:-}" = stop ]; then
    for c in "$CMP" "$CTL"; do lxc stop "$c" 2>/dev/null || true; done
    log "已停止"
    exit 0
fi

for m in openvswitch vport_geneve; do lsmod | grep -q "^${m} " || modprobe "$m"; done
if iptables -n -L DOCKER-USER >/dev/null 2>&1; then
    iptables -C DOCKER-USER -i "$BRIDGE" -j ACCEPT 2>/dev/null || iptables -I DOCKER-USER -i "$BRIDGE" -j ACCEPT
    iptables -C DOCKER-USER -o "$BRIDGE" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null \
        || iptables -I DOCKER-USER -o "$BRIDGE" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
fi
for c in "$CTL" "$CMP"; do
    lxc info "$c" >/dev/null 2>&1 || die "找不到容器 $c，先跑 $HERE/create.sh"
    [ "$(lxc info "$c" | awk '/^Status:/{print $2}')" = RUNNING ] || lxc start "$c"
done
log "等 Keystone 與 Nova 回應"
for _ in $(seq 1 90); do
    if curl -sf -o /dev/null "http://${CTL_IP}/identity/v3" && curl -sf -o /dev/null "http://${CTL_IP}/compute/"; then
        lxc exec "$CTL" -- sudo -u stack -H bash -c \
            'export OS_CLOUD=devstack-admin; /opt/stack/data/venv/bin/openstack compute service list -f value -c Binary -c Host -c State' || true
        log "已啟動：http://${CTL_IP}/identity/v3"
        exit 0
    fi
    sleep 2
done
die "API 沒有回應；看容器內 journalctl -u 'devstack@*'"
