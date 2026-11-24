#!/bin/bash
# 可丟棄的 RustDesk 測試靶：官方 hbbs／hbbr（1.1.16）＋官方 Linux 受控端（1.4.1，Xvfb＋大字級 xterm）。
# 給「相容 RustDesk 的網頁連線」做端到端驗證（登入、畫面、滑鼠、鍵盤）。說明見同目錄 README.md。
#
#   scripts/rustdesk-test-target/run.sh up      建置並啟動，印出 ID、密碼、公鑰與 jt-ipam 要填的位址
#   scripts/rustdesk-test-target/run.sh info    再印一次
#   scripts/rustdesk-test-target/run.sh shot X  把受控端的螢幕存成 X（png）
#   scripts/rustdesk-test-target/run.sh down    全部移除
#
# ⚠️ 只綁 127.0.0.1；受控端只接在 docker 的 internal 網路上（連不到網際網路），
#    不會向公用或正式的 RustDesk 伺服器註冊。密碼寫在這支腳本裡，不可以對外開。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NET=rdtest-net            # 一般 bridge：發布到 127.0.0.1 的埠在這裡
INT=rdtest-int            # internal：受控端只在這裡（沒有對外路由）
SRV=rdtest-server
CLI=rdtest-client
PASSWORD="${PASSWORD:-Rd-Test-2026}"
PORT_BASE="${PORT_BASE:-31116}"   # 127.0.0.1:31116..31119 → 21116..21119（不佔用預設埠）
DEB=rustdesk-1.4.1-x86_64.deb
DEB_URL="https://github.com/rustdesk/rustdesk/releases/download/1.4.1/${DEB}"
DEB_SHA=a5cea9e1d36d07f6f20eb7adb45b97c43a59c055d6f6bcc7ea2f60c3960cd96d

log() { printf '[rdtest] %s\n' "$*"; }

info() {
    local key id ip
    key="$(docker exec "$SRV" cat /data/id_ed25519.pub 2>/dev/null || true)"
    id="$(docker logs "$CLI" 2>/dev/null | sed -n 's/^rustdesk id: //p' | tail -1)"
    ip="$(docker inspect "$SRV" --format "{{(index .NetworkSettings.Networks \"$NET\").IPAddress}}" 2>/dev/null || true)"
    cat <<INFO
RustDesk ID      : ${id:-（還沒拿到，等幾秒再跑 info）}
永久密碼          : ${PASSWORD}
伺服器公鑰        : ${key}
hbbs（TCP／WS）  : ${ip}:21116 / ${ip}:21118   （也發布在 127.0.0.1:${PORT_BASE} / 127.0.0.1:$((PORT_BASE + 2))）
hbbr（TCP／WS）  : ${ip}:21117 / ${ip}:21119   （也發布在 127.0.0.1:$((PORT_BASE + 1)) / 127.0.0.1:$((PORT_BASE + 3))）
jt-ipam 設定建議  : hbbs 位址填 ${ip}、中繼位址留空（容器位址是私網，出站規則預設允許；127.0.0.1 會被出站規則擋）
INFO
}

up() {
    if [[ ! -f "$HERE/$DEB" ]]; then
        log "下載官方受控端 ${DEB}…"
        curl -fL --retry 3 -C - -o "$HERE/$DEB.part" "$DEB_URL"
        mv "$HERE/$DEB.part" "$HERE/$DEB"
    fi
    echo "${DEB_SHA}  $HERE/$DEB" | sha256sum -c - >/dev/null
    log "建置映像…"
    docker build -q -t jtipam-rdtest-server -f "$HERE/Dockerfile.server" "$HERE" >/dev/null
    docker build -q -t jtipam-rdtest-client -f "$HERE/Dockerfile.client" "$HERE" >/dev/null
    docker rm -f -v "$SRV" "$CLI" >/dev/null 2>&1 || true
    docker network inspect "$NET" >/dev/null 2>&1 || docker network create "$NET" >/dev/null
    docker network inspect "$INT" >/dev/null 2>&1 || docker network create --internal "$INT" >/dev/null
    log "啟動 hbbs／hbbr…"
    docker run -d --name "$SRV" --network "$NET" \
        -p "127.0.0.1:${PORT_BASE}:21116" -p "127.0.0.1:${PORT_BASE}:21116/udp" \
        -p "127.0.0.1:$((PORT_BASE + 1)):21117" -p "127.0.0.1:$((PORT_BASE + 2)):21118" \
        -p "127.0.0.1:$((PORT_BASE + 3)):21119" \
        -e RELAY="${SRV}:21117" jtipam-rdtest-server >/dev/null
    docker network connect "$INT" "$SRV"
    local key="" i
    for i in $(seq 50); do
        key="$(docker exec "$SRV" cat /data/id_ed25519.pub 2>/dev/null || true)"
        [[ -n "$key" ]] && break
        sleep 0.2
    done
    [[ -n "$key" ]] || { log "hbbs 沒有產生公鑰"; exit 1; }
    log "啟動受控端（只接 internal 網路）…"
    docker run -d --name "$CLI" --network "$INT" -e KEY="$key" -e ID_SERVER="$SRV" -e PASSWORD="$PASSWORD" \
        jtipam-rdtest-client >/dev/null
    for i in $(seq 60); do
        docker logs "$CLI" 2>/dev/null | grep -q '^rustdesk id:' && break
        sleep 1
    done
    info
}

down() {
    docker rm -f -v "$SRV" "$CLI" >/dev/null 2>&1 || true
    docker network rm "$INT" "$NET" >/dev/null 2>&1 || true
    log "已移除"
}

shot() {
    local out="${1:?用法：run.sh shot 輸出.png}"
    docker exec "$CLI" sh -c 'import -display :0 -window root /tmp/shot.png'
    docker cp "$CLI:/tmp/shot.png" "$out" >/dev/null
    log "已存 $out"
}

case "${1:-up}" in
    up) up ;;
    info) info ;;
    shot) shift; shot "$@" ;;
    down) down ;;
    *) echo "用法：$0 up|info|shot <檔名>|down" >&2; exit 2 ;;
esac
