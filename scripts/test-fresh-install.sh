#!/usr/bin/env bash
# test-fresh-install.sh — run a real first-time install in a throwaway container.
#
# Why this exists: every install problem customers have reported was invisible on
# an already-working box. A pre-existing PostgreSQL cluster on a different major,
# a silent pnpm failure, a systemd unit whose ReadWritePaths directory did not
# exist yet (fails as 226/NAMESPACE) -- none of them can reproduce on dev or prod,
# because there the thing is already there. The only way to see what a customer
# sees is to start from a clean OS.
#
# This is a release gate, not an optional extra. See TEST_CHECKLIST.md section 5b.
#
# Usage:  scripts/test-fresh-install.sh [debian:12|ubuntu:24.04|...]
# Mirror: APT_MIRROR=ftp.tw.debian.org 讓容器裡的 apt 改走指定的 Debian 鏡像站。deb.debian.org
#         慢的時候（2026-09-24 實測 59 KB/s），PID 1 裝 systemd 就會超過等待時間而回報
#         「systemd never came up」—— 看起來像 systemd 壞了，其實是 apt 還在下載。
#         PIP_MIRROR=https://<index>/simple 同理換 PyPI（files.pythonhosted.org 同一天也只有 50 KB/s）。
#         兩者只影響這個拋棄式容器，不影響發佈內容與客戶安裝。
# guacd: 必要元件（RDP／VNC 的預設引擎，2026-09-27 起），安裝一定會裝 —— 不設 GUACD_TARBALL 就是走客戶
#         實際的路：從 GitHub release 下載、以 scripts/guacd/SHA256SUMS 核對後安裝，並驗它在跑、只綁 127.0.0.1。
#         GUACD_TARBALL=dist/guacd/<ver>/jt-ipam-guacd-…-debian12-amd64.tar.gz 改用本機的檔案
#         （.deps 要在同目錄；要選跟 IMAGE 同一個 OS 版本的那份）。
# Needs:  docker, and a source tree at the repo root. Nothing else.
#
# The container runs systemd (privileged + host cgroups) because the whole point
# is to exercise the real units, timers and sandboxing -- not just the Python.

set -euo pipefail

IMAGE="${1:-debian:12}"
NAME="jt-install-test-$(echo "$IMAGE" | tr -c 'a-z0-9' '-')"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILED=0

say()  { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
pass() { printf '  \033[1;32mPASS\033[0m %s\n' "$*"; }
fail() { printf '  \033[1;31mFAIL\033[0m %s\n' "$*"; FAILED=$((FAILED + 1)); }
dex()  { docker exec "$NAME" "$@"; }

command -v docker >/dev/null || { echo "docker is required"; exit 1; }

say "Starting a clean $IMAGE with systemd"
docker rm -f -v "$NAME" >/dev/null 2>&1 || true
# The base images ship no init, so PID 1 installs systemd and then becomes it.
docker run -d --name "$NAME" --privileged --cgroupns=host \
    -v /sys/fs/cgroup:/sys/fs/cgroup:rw --tmpfs /run --tmpfs /run/lock \
    -e DEBIAN_FRONTEND=noninteractive -e APT_MIRROR="${APT_MIRROR:-}" \
    -e PIP_MIRROR="${PIP_MIRROR:-}" "$IMAGE" \
    sh -c 'if [ -n "$PIP_MIRROR" ]; then
             # 寫全域設定：安裝腳本用 sudo -u jtipam 跑 pip，環境變數會被 sudo 清掉
             printf "[global]\nindex-url = %s\n" "$PIP_MIRROR" > /etc/pip.conf; fi
           if [ -n "$APT_MIRROR" ]; then
             # 只換主套件庫；debian-security 多數鏡像站沒有，維持官方來源
             sed -i -e "s#//deb.debian.org/debian\$#//$APT_MIRROR/debian#" \
               -e "s#//deb.debian.org/debian #//$APT_MIRROR/debian #" \
               /etc/apt/sources.list.d/*.sources /etc/apt/sources.list 2>/dev/null; fi
           apt-get update -qq && apt-get install -y -qq systemd systemd-sysv \
           ca-certificates curl >/dev/null && exec /sbin/init' >/dev/null
# PID 1 先裝 systemd 再變成 systemd，所以這裡等的其實是**容器裡的 apt**。
# 原本給 90 次（180 秒）—— 在網路慢的建置機上 apt 光下載就要好幾分鐘，關卡會回
# 「systemd never came up」，看起來像 systemd 壞了，其實只是還沒裝完。
# 可用 SYSTEMD_WAIT_TRIES 覆寫。
for _ in $(seq "${SYSTEMD_WAIT_TRIES:-450}"); do
    state=$(dex systemctl is-system-running 2>/dev/null || true)
    [[ "$state" == running || "$state" == degraded ]] && break
    sleep 2
done
[[ "$state" == running || "$state" == degraded ]] || { fail "systemd never came up in $IMAGE"; exit 1; }
pass "systemd is up ($state)"

say "Copying the working tree in (as a customer's git clone would land)"
dex mkdir -p /opt/jt-ipam
tar -C "$ROOT" --exclude=.git --exclude=node_modules --exclude=.venv \
    --exclude=dist --exclude=__pycache__ --exclude=zap-reports -cf - . \
    | docker cp - "$NAME:/opt/jt-ipam"

GUACD_ARGS=()
if [[ -n "${GUACD_TARBALL:-}" && "${GUACD_TARBALL}" != online ]]; then
    docker cp "$GUACD_TARBALL" "$NAME:/tmp/"
    docker cp "${GUACD_TARBALL%.tar.gz}.deps" "$NAME:/tmp/"
    GUACD_ARGS=(--guacd-tarball "/tmp/$(basename "$GUACD_TARBALL")")
fi

say "Running scripts/jt-ipam.sh install ${GUACD_ARGS[*]} (this is the part customers do)"
if dex env DEBIAN_FRONTEND=noninteractive bash /opt/jt-ipam/scripts/jt-ipam.sh install "${GUACD_ARGS[@]}" \
        >/tmp/$NAME.install.log 2>&1; then
    pass "install exited 0"
else
    fail "install failed -- last 40 lines:"; tail -40 "/tmp/$NAME.install.log"
fi

say "Checking the result the way a customer would"

# 1. The service actually answers -- on the URL a user would open. "Done" printed
#    by the installer is not evidence, and neither is a listening socket: in nginx
#    mode the backend can be perfectly healthy on 8000 while nginx is stopped and
#    nobody can reach the product (that is a real bug this test caught).
#    Note: /healthz is answered by nginx itself (static 200), so it cannot tell us
#    whether the backend is reachable. Ask for a route that has to be proxied --
#    401 means the backend answered; 502 means it did not.
mode=$(dex sh -c 'grep -oP "^BACKEND_TLS_MODE=\K\S+" /etc/jt-ipam/backend.env 2>/dev/null' || true)
if [[ "$mode" == "nginx" ]]; then
    url="https://127.0.0.1/api/v1/system/version"
else
    port=$(dex sh -c 'grep -oP "^BACKEND_BIND_PORT=\K\S+" /etc/jt-ipam/backend.env 2>/dev/null' || true)
    url="https://127.0.0.1:${port:-8443}/api/v1/system/version"
fi
code=$(dex curl -sk -o /dev/null -w '%{http_code}' --max-time 15 "$url" 2>/dev/null || echo 000)
if [[ "$code" =~ ^[1-4] ]]; then
    pass "answers on $url (mode: ${mode:-unknown}, HTTP $code)"
else
    fail "no answer on $url (mode: ${mode:-unknown}, HTTP $code)"
    dex journalctl -u jt-ipam-backend -n 30 --no-pager || true
    dex systemctl status nginx --no-pager -l 2>/dev/null | head -15 || true
fi

UI_URL="${url%/api/v1/system/version}"
# 畫面本身也要送得出來：API 有回應只證明後端活著；nginx 送的 dist 可能根本沒建、或 index.html 指到不存在的檔案
page="$(dex curl -sk --max-time 15 "${UI_URL}/" 2>/dev/null || true)"
asset="$(grep -oP 'src="\K/assets/[^"]+\.js' <<<"$page" | head -1 || true)"
acode="000"
[[ -n "$asset" ]] && acode="$(dex curl -sk -o /dev/null -w '%{http_code}' --max-time 15 "${UI_URL}${asset}" 2>/dev/null || echo 000)"
if [[ "${page,,}" == *"<!doctype html"* && "$acode" == 200 ]]; then
    pass "UI page and its script bundle are served (${UI_URL}${asset})"
else
    fail "UI is not served end to end (page: ${#page} bytes, bundle '${asset:-none}' HTTP $acode)"
fi

# 2. Timer-driven units. These only ever fail in the field, hours after install,
#    which is exactly why they have to be triggered here.
for unit in jt-ipam-backup jt-ipam-sync; do
    dex systemctl start "$unit.service" >/dev/null 2>&1 || true
    for _ in $(seq 60); do
        dex systemctl is-active --quiet "$unit.service" || break
        sleep 2
    done
    result=$(dex systemctl show -p Result --value "$unit.service" 2>/dev/null || echo unknown)
    if [[ "$result" == success ]]; then
        pass "$unit ran successfully"
    else
        fail "$unit Result=$result"
        dex journalctl -u "$unit" -n 25 --no-pager || true
    fi
done

# 3. The sandbox self-heal: a missing ReadWritePaths directory is 226/NAMESPACE,
#    an error message that says nothing about the actual cause. One customer hit
#    exactly this and had to create /var/backups/jt-ipam by hand.
dex rm -rf /var/backups/jt-ipam
dex systemctl start jt-ipam-backup.service >/dev/null 2>&1 || true
sleep 5
if dex journalctl -u jt-ipam-backup -n 40 --no-pager 2>/dev/null | grep -q '226/NAMESPACE'; then
    fail "backup unit fails with 226/NAMESPACE when its directory is missing"
else
    pass "backup unit survives a missing /var/backups/jt-ipam"
fi

# 3b. guacd (required, always installed): running, and reachable on loopback only -- its port
#     has no authentication at all, anyone who can reach it can make it connect anywhere.
if true; then
    if dex systemctl is-active --quiet jt-ipam-guacd \
       && dex bash -c 'exec 3<>/dev/tcp/127.0.0.1/4822' 2>/dev/null; then
        pass "guacd is running on 127.0.0.1:4822"
    else
        fail "guacd is not running"; dex journalctl -u jt-ipam-guacd -n 25 --no-pager || true
    fi
    ext=$(dex sh -c "hostname -I | awk '{print \$1}'" 2>/dev/null || true)
    if [[ -n "$ext" ]] && dex bash -c "exec 3<>/dev/tcp/$ext/4822" 2>/dev/null; then
        fail "guacd answers on $ext:4822 -- it must listen on 127.0.0.1 only"
    else
        pass "guacd does not answer on the container address ($ext)"
    fi
fi

# 3c. Reference-data refresh (GeoIP / OUI / Recog): the timers are enabled and each unit
#     really runs to Result=success. Until 2026-09-29 install never installed these at all,
#     and the OUI unit on hosts that had one pointed at a script that was never committed.
for unit in jt-ipam-geoip-refresh jt-ipam-oui-refresh jt-ipam-recog-refresh; do
    if ! dex systemctl is-enabled --quiet "$unit.timer" 2>/dev/null; then
        fail "$unit.timer is not enabled"
        continue
    fi
    dex systemctl start "$unit.service" >/dev/null 2>&1 || true
    for _ in $(seq 150); do
        dex systemctl is-active --quiet "$unit.service" || break
        sleep 2
    done
    result=$(dex systemctl show -p Result --value "$unit.service" 2>/dev/null || echo unknown)
    if [[ "$result" == success ]]; then
        pass "$unit ran successfully"
    else
        fail "$unit Result=$result"
        dex journalctl -u "$unit" -n 25 --no-pager || true
    fi
done
# Recog is optional, but a host that can reach GitHub must end up with it installed
if dex bash -c 'u=$(stat -c %U /opt/jt-ipam); sudo -u "$u" bash -c "cd /opt/jt-ipam/backend; set -a; . /etc/jt-ipam/backend.env; set +a; .venv/bin/python -m app.cli.recog status"' \
        >"/tmp/$NAME.recog.log" 2>&1; then
    pass "Recog fingerprint database: $(tr '\t' ' ' <"/tmp/$NAME.recog.log" | head -1)"
else
    fail "Recog fingerprint database is not installed: $(head -3 "/tmp/$NAME.recog.log")"
fi
# ...and it must be the install itself that installed it: a later timer run covering for a failed
# download hid a real race here once (2026-09-29, duplicate key on recog_databases)
if grep -q 'Recog fingerprint database was not installed' "/tmp/$NAME.install.log" 2>/dev/null; then
    fail "install failed to install Recog by itself:"; grep -A1 'Recog fingerprint database was not installed' "/tmp/$NAME.install.log" | head -4
else
    pass "install installed Recog without warnings"
fi

# 4. doctor must agree with reality -- a diagnostic that lies is worse than none.
if dex bash /opt/jt-ipam/scripts/jt-ipam.sh doctor >/tmp/$NAME.doctor.log 2>&1; then
    pass "doctor reports a healthy install"
else
    fail "doctor reports problems:"; grep -E '✗|→' "/tmp/$NAME.doctor.log" || true
fi

# 5. 建置前端用的 Node.js 要是 frontend/package.json "engines" 要求的版本。裝了舊的，建置有時照樣成功，
#    要到下一次升級或下一個相依套件才會出事。
want_node="$(grep -oP '"node":\s*">=\K[0-9]+' "$ROOT/frontend/package.json" || echo 22)"
nv="$(dex node -v 2>/dev/null || echo none)"
nmaj="${nv#v}"; nmaj="${nmaj%%.*}"
if [[ "$nmaj" =~ ^[0-9]+$ ]] && (( nmaj >= want_node )); then
    pass "Node.js $nv for the frontend build (needs >= $want_node)"
else
    fail "Node.js is $nv; the frontend build needs >= $want_node"
fi

say "Result"
if [[ "$FAILED" -eq 0 ]]; then
    printf '\033[1;32mFresh install on %s is clean.\033[0m\n' "$IMAGE"
else
    printf '\033[1;31m%d check(s) failed on %s.\033[0m\n' "$FAILED" "$IMAGE"
fi
echo "Container kept as '$NAME' for inspection: docker exec -it $NAME bash"
echo "Remove it with: docker rm -f -v $NAME"
exit "$FAILED"
