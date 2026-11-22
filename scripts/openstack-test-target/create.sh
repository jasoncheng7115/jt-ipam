#!/bin/bash
# 可丟棄的 OpenStack 測試靶：LXD 容器裡跑 DevStack（stable/2026.1），Nova 用 fake 虛擬化驅動（不需要 KVM）。
# 給 jt-ipam 的 OpenStack 整合（Keystone v3／Nova／Neutron，唯讀）做端到端驗證。說明見同目錄 README.md。
#
#   scripts/openstack-test-target/create.sh          建立兩個容器並各跑一次 stack.sh（第一次約 40～60 分鐘）
#   scripts/openstack-test-target/create.sh --ctl    只建控制節點
#   scripts/openstack-test-target/create.sh --cmp    只建第二台運算節點（控制節點要先好）
#
# 可以重複執行：已存在的儲存集區／網路／設定檔／容器會沿用，已經跑完 stack.sh 的節點會跳過。
#
# ⚠️ 只接在 LXD 自己的私網橋接（jtosbr0，10.241.73.0/24）上，開發機 的區網介面沒有發布任何埠；
#    區網上的機器就算加了路由，轉送規則也只放行「已建立的連線」回程，連不進來。
#    容器是特權容器（OVS 要用核心 datapath），只能當測試用，不可以放任何真實資料。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
. "$HERE/common.sh"

DO_CTL=1
DO_CMP=1
case "${1:-}" in
    --ctl) DO_CMP=0 ;;
    --cmp) DO_CTL=0 ;;
    "") ;;
    *) echo "用法：$0 [--ctl|--cmp]" >&2; exit 2 ;;
esac

# ---------- 主機端前置（只做 modprobe 與兩條轉送規則，不在主機裝任何 OpenStack 套件） ----------
host_prereqs() {
    if ! lxc info >/dev/null 2>&1; then
        die "LXD 沒有在跑（snap 版 lxd）"
    fi
    # OVS 核心 datapath 與 Geneve 通道；容器裡不能 modprobe，只能在主機先載入
    for m in openvswitch vport_geneve; do
        lsmod | grep -q "^${m} " || modprobe "$m"
    done

    # LXD 之前從沒初始化過也沒關係：只建我們自己的儲存集區、網路與設定檔，不動 default 設定檔
    if ! lxc storage show "$POOL" >/dev/null 2>&1; then
        log "建立儲存集區 $POOL（dir）"
        lxc storage create "$POOL" dir
    fi
    if ! lxc network show "$BRIDGE" >/dev/null 2>&1; then
        log "建立網路 $BRIDGE（${BRIDGE_CIDR}，NAT，無 IPv6）"
        lxc network create "$BRIDGE" ipv4.address="$BRIDGE_CIDR" ipv4.nat=true ipv6.address=none \
            ipv4.dhcp.ranges="${NET_PREFIX}.100-${NET_PREFIX}.199"
    fi
    if ! lxc profile show "$PROFILE" >/dev/null 2>&1; then
        log "建立設定檔 $PROFILE"
        lxc profile create "$PROFILE"
    fi
    # 特權＋巢狀＋不限 AppArmor：OVS／ovn-controller／MySQL 在非特權容器裡會卡在 netlink 與 AppArmor
    lxc profile edit "$PROFILE" <<EOF
name: $PROFILE
description: jt-ipam OpenStack 測試靶（DevStack + fake 虛擬化驅動）
config:
  security.privileged: "true"
  security.nesting: "true"
  linux.kernel_modules: openvswitch,vport_geneve
  raw.lxc: |
    lxc.apparmor.profile=unconfined
devices:
  root:
    type: disk
    path: /
    pool: $POOL
  eth0:
    type: nic
    name: eth0
    network: $BRIDGE
EOF

    # 開發機 有 docker：它把 FORWARD 預設改成 DROP，容器會上不了網（clone／pip／apt 都會失敗）。
    # 只在 DOCKER-USER 放行「從橋接出去」與「回到橋接的已建立連線」，所以區網連不進容器。
    if iptables -n -L DOCKER-USER >/dev/null 2>&1; then
        iptables -C DOCKER-USER -i "$BRIDGE" -j ACCEPT 2>/dev/null \
            || iptables -I DOCKER-USER -i "$BRIDGE" -j ACCEPT
        iptables -C DOCKER-USER -o "$BRIDGE" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null \
            || iptables -I DOCKER-USER -o "$BRIDGE" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
    fi
}

# ---------- 容器 ----------
ensure_container() {
    local name=$1 ip=$2 mem=$3
    if ! lxc info "$name" >/dev/null 2>&1; then
        log "建立容器 $name（$IMAGE，$ip，記憶體上限 $mem）"
        lxc init "$IMAGE" "$name" -p "$PROFILE"
        lxc config device override "$name" eth0 ipv4.address="$ip"
        lxc config set "$name" limits.memory="$mem" limits.cpu="$CPUS"
    fi
    if [ "$(lxc info "$name" | awk '/^Status:/{print $2}')" != "RUNNING" ]; then
        lxc start "$name"
    fi
    log "等待 $name 開機完成（cloud-init）"
    for _ in $(seq 1 120); do
        lxc exec "$name" -- cloud-init status 2>/dev/null | grep -qE 'done|error|disabled' && break
        sleep 2
    done
    lxc exec "$name" -- bash -c 'for i in $(seq 1 60); do getent hosts opendev.org >/dev/null && exit 0; sleep 2; done; exit 1' \
        || die "$name 解析不到 opendev.org（檢查 $BRIDGE 的 NAT 與 DOCKER-USER 規則）"
}

prepare_stack_user() {
    local name=$1
    lxc exec "$name" -- bash -eu -c "
        export DEBIAN_FRONTEND=noninteractive
        command -v git >/dev/null || { apt-get update -q; apt-get install -yq git; }
        id stack >/dev/null 2>&1 || {
            useradd -s /bin/bash -d /opt/stack -m stack
            chmod +x /opt/stack
            echo 'stack ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/stack
            chmod 0440 /etc/sudoers.d/stack
        }
        if [ ! -d /opt/stack/devstack ]; then
            sudo -u stack git clone -q --depth 1 -b $DEVSTACK_BRANCH https://opendev.org/openstack/devstack /opt/stack/devstack
        fi
        cd /opt/stack/devstack
        # fake 驅動的 nova-compute 主機名稱固定是「容器主機名稱＋1」，跟 ovn-controller 註冊的 chassis 名稱對不上，
        # Neutron 會拒絕繫結埠（Refusing to bind port ... no OVN chassis for host）。改成可用 JT_FAKE_HOST 指定。
        grep -q JT_FAKE_HOST lib/nova || sed -i 's|iniset \$fake_conf DEFAULT host \"\${HOSTNAME}\${i}\"|iniset \$fake_conf DEFAULT host \"\${JT_FAKE_HOST:-\${HOSTNAME}\${i}}\"|' lib/nova
        grep -q JT_FAKE_HOST functions || sed -i 's|--host \`hostname\`1 |--host \${JT_FAKE_HOST:-\`hostname\`1} |' functions
        grep -q JT_FAKE_HOST lib/nova && grep -q JT_FAKE_HOST functions
        # 容器裡 /sys/kernel/mm 是唯讀的（也不該去動主機的 KSM），寫不進去就跳過，不要讓 stack.sh 失敗
        sed -i 's|if \[\[ -f /sys/kernel/mm/ksm/run \]\]|if [[ -w /sys/kernel/mm/ksm/run ]]|' lib/host
    "
}

push_secrets() {
    # 密碼不寫進 repo：第一次建立時隨機產生，存在控制節點的 /root/jt-target.secrets
    local sec
    if ! lxc exec "$CTL" -- test -s /root/jt-target.secrets 2>/dev/null; then
        sec="ADMIN_PASSWORD=Adm-$(openssl rand -hex 8)
READER_PASSWORD=Rdr-$(openssl rand -hex 8)
MEMBER_PASSWORD=Mbr-$(openssl rand -hex 8)"
        printf '%s\n' "$sec" | lxc exec "$CTL" -- bash -c 'umask 077; cat > /root/jt-target.secrets'
    fi
    lxc exec "$CTL" -- cat /root/jt-target.secrets
}

common_localrc() {
    local admin_pw=$1
    cat <<EOF
ADMIN_PASSWORD=$admin_pw
DATABASE_PASSWORD=$admin_pw
RABBIT_PASSWORD=$admin_pw
SERVICE_PASSWORD=$admin_pw
SERVICE_HOST=$CTL_IP
MYSQL_HOST=$CTL_IP
RABBIT_HOST=$CTL_IP
REGION_NAME=$REGION
VIRT_DRIVER=fake
NUMBER_FAKE_NOVA_COMPUTE=1
API_WORKERS=1
GIT_DEPTH=1
LOGFILE=/opt/stack/logs/stack.sh.log
LOG_COLOR=False
INSTALL_TEMPEST=False
NOVA_VNC_ENABLED=False
DOWNLOAD_DEFAULT_IMAGES=False
IMAGE_URLS=
# 單一 conductor、不分 cell1（運算節點也要一致，否則連 RabbitMQ 的 vhost nova_cell1 會找不到）
CELLSV2_SETUP=singleconductor
ENABLE_KSM=False
ENABLE_KSMTUNED=False
Q_AGENT=ovn
Q_ML2_PLUGIN_MECHANISM_DRIVERS=ovn
Q_ML2_TENANT_NETWORK_TYPE=geneve
EOF
}

run_stack() {
    local name=$1
    if lxc exec "$name" -- test -f /opt/stack/.jt-stacked; then
        log "$name 已經跑過 stack.sh，跳過"
        return 0
    fi
    log "$name 開始跑 stack.sh（記錄：容器內 /opt/stack/logs/stack.sh.log）"
    if lxc exec "$name" -- sudo -u stack -H bash -c 'cd /opt/stack/devstack && ./stack.sh' >"$LOGDIR/stack-$name.out" 2>&1; then
        lxc exec "$name" -- touch /opt/stack/.jt-stacked
        log "$name stack.sh 完成"
    else
        tail -40 "$LOGDIR/stack-$name.out" >&2
        die "$name stack.sh 失敗；完整輸出在 $LOGDIR/stack-$name.out"
    fi
}

build_ctl() {
    ensure_container "$CTL" "$CTL_IP" "$CTL_MEM"
    prepare_stack_user "$CTL"
    local admin_pw
    admin_pw="$(push_secrets | sed -n 's/^ADMIN_PASSWORD=//p')"
    {
        echo '[[local|localrc]]'
        common_localrc "$admin_pw"
        cat <<EOF
HOST_IP=$CTL_IP
JT_FAKE_HOST=$CTL
# 只開 Keystone／Placement／Glance／Nova／Neutron(OVN)；Cinder、Horizon、Swift、Tempest、metadata 都不開
ENABLED_SERVICES=key,mysql,rabbit,g-api,placement-api,placement-client,n-api,n-cond,n-sch,n-cpu,ovn-controller,ovn-northd,ovs-vswitchd,ovsdb-server,q-svc
ENABLE_CHASSIS_AS_GW=True
NEUTRON_CREATE_INITIAL_NETWORKS=False
EOF
    } | lxc exec "$CTL" -- sudo -u stack tee /opt/stack/devstack/local.conf >/dev/null
    run_stack "$CTL"
}

build_cmp() {
    lxc exec "$CTL" -- test -f /opt/stack/.jt-stacked 2>/dev/null || die "控制節點 $CTL 還沒好，先跑 $0 --ctl"
    ensure_container "$CMP" "$CMP_IP" "$CMP_MEM"
    prepare_stack_user "$CMP"
    local admin_pw
    admin_pw="$(push_secrets | sed -n 's/^ADMIN_PASSWORD=//p')"
    {
        echo '[[local|localrc]]'
        common_localrc "$admin_pw"
        cat <<EOF
HOST_IP=$CMP_IP
JT_FAKE_HOST=$CMP
# 第二台運算節點：nova-compute（fake）、ovn-controller；q-ovn-agent 是為了讓 DevStack 裝 OVS／OVN 套件（沒開任何 q-* 就不會裝）
ENABLED_SERVICES=n-cpu,placement-client,ovn-controller,ovs-vswitchd,ovsdb-server,q-ovn-agent
ENABLE_CHASSIS_AS_GW=False
EOF
    } | lxc exec "$CMP" -- sudo -u stack tee /opt/stack/devstack/local.conf >/dev/null
    run_stack "$CMP"
    log "在控制節點登記新的運算主機（cell_v2 discover_hosts）"
    lxc exec "$CTL" -- sudo -u stack -H bash -c '/opt/stack/data/venv/bin/nova-manage cell_v2 discover_hosts --verbose' || true
}

host_prereqs
[ "$DO_CTL" = 1 ] && build_ctl
[ "$DO_CMP" = 1 ] && build_cmp
log "完成。下一步：$HERE/seed.sh"
