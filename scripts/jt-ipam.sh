#!/usr/bin/env bash
# =============================================================================
# jt-ipam — single entry-point deployment tool
#
# Usage:
#   jt-ipam.sh install [--tls-mode {nginx|direct|self-signed}]
#                      [--public-fqdn ipam.example.com] [--bind-port 8443]
#   jt-ipam.sh upgrade [--no-pull]
#   jt-ipam.sh uninstall [--purge] [--yes]
#   jt-ipam.sh help | -h | --help
#
# Subcommands:
#   install    — fresh install (Debian/Ubuntu; Proxmox LXC or bare metal)
#   upgrade    — upgrade existing install (git pull -> backup -> migrate -> build -> restart)
#   uninstall  — stop and remove systemd units/timers + nginx site;
#                by default keeps DB / config / uploads / jtipam user / source.
#                --purge also runs dropdb + removes /etc/jt-ipam /var/lib/jt-ipam + removes user
#                (requires interactive yes or --yes). Never removes /opt/jt-ipam source.
# =============================================================================
set -euo pipefail

# -- colored log helpers (shared by all subcommands) --
log()  { echo -e "\033[1;32m[jt-ipam]\033[0m $*"; }
warn() { echo -e "\033[1;33m[warn]\033[0m $*" >&2; }
die()  { echo -e "\033[1;31mFATAL:\033[0m $*" >&2; exit 1; }

# -- where to send an operator when something fails --
#
# Every failure path ends in exit != 0, so the hint is printed from a single EXIT
# trap instead of being repeated at 20 call sites (and forgotten at the 21st --
# including the failures nobody wrote a message for, where `set -e` just stops).
#
# The page is trilingual (Chinese / English / Japanese) and the language is chosen
# here, from the OS locale, rather than left to the reader: someone whose terminal
# is already Japanese should not land on an English page and then have to find the
# switch. Anything else gets English, which is the page default.
# 「服務起來了」不等於「在服務」。systemd 的 is-active 在行程 exec 的那一刻就是 active，
# 而 uvicorn 還要載入整個應用程式才開始接受連線；固定 `sleep N` 在忙碌的機器上不夠 ——
# 升級剛跑完前端建置，那正是機器最忙的時候。實測：升級回報成功、站台仍回 502。
#
# 所以用「連得到」當判準，而且要連**必須經過後端**的路徑。連不到就等，等不到就明講。
wait_until_serving() {
    local url="$1" timeout="${2:-90}" waited=0 code
    while (( waited < timeout )); do
        code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 3 "$url" 2>/dev/null || echo 000)"
        # 1xx–4xx 都代表後端答了話（401 未登入是完全正常的答覆）；000 是連不上，5xx 多半是代理拿不到後端
        [[ "$code" =~ ^[1-4] ]] && return 0
        sleep 2
        waited=$((waited + 2))
    done
    return 1
}

DOCS_BASE="${JT_IPAM_DOCS_BASE:-https://jasoncheng7115.github.io/jt-ipam}"
troubleshooting_url() {
    case "${LC_ALL:-${LC_MESSAGES:-${LANG:-}}}" in
        zh_*|zh|zh-*|zh.*) echo "${DOCS_BASE}/troubleshooting.html?lang=zh-TW" ;;
        ja_*|ja|ja-*|ja.*) echo "${DOCS_BASE}/troubleshooting.html?lang=ja" ;;
        *)                 echo "${DOCS_BASE}/troubleshooting.html?lang=en" ;;
    esac
}
HELP_HINT=1          # set to 0 around an exit that is a user choice, not a failure
_HINT_SHOWN=0
print_help_hint() {
    [[ "${HELP_HINT}" == "1" ]] || return 0
    [[ "${_HINT_SHOWN}" == "1" ]] && return 0
    _HINT_SHOWN=1
    echo >&2
    echo -e "\033[1;36mInstall / upgrade troubleshooting:\033[0m $(troubleshooting_url)" >&2
    echo    "  Health check on this host:  sudo bash $0 doctor" >&2
}
on_exit_hint() {
    local rc=$?
    # 2 = usage / unknown command; it has already printed the usage text.
    [[ $rc -eq 0 || $rc -eq 2 ]] || print_help_hint
}
trap on_exit_hint EXIT

# Best-effort install of aardwolf, the OPTIONAL fallback engine for the RDP / VNC consoles (guacd
# is the required, default one since 2026-09-27). Pinned to a wheel-having version;
# --only-binary=:all: means: if there is no prebuilt wheel for this platform/Python, fail FAST
# instead of pulling an sdist and triggering a Rust toolchain build. Failure is harmless.
install_rdp_optional() {
    local bd="${REPO_ROOT}/backend"
    local u; u="$(stat -c '%U' "$bd/.venv" 2>/dev/null || echo jtipam)"
    [ -x "$bd/.venv/bin/pip" ] || return 0
    log "Installing optional fallback console engine (aardwolf, prebuilt wheel only)…"
    # pip 找不到合適的 wheel 時會印兩行紅色 ERROR（Python 3.14 必然如此）。這是選用的備援引擎，
    # 紅字只會讓人以為升級失敗（客戶 2026-10-08，Ubuntu 26.04）→ pip 的輸出收進暫存檔，下面一行講清楚。
    local pip_log; pip_log="$(mktemp /tmp/jt-ipam-aardwolf.XXXXXX.log)"
    chmod 0644 "$pip_log"
    if ( cd "$bd" && sudo -u "$u" "$bd/.venv/bin/pip" install --quiet --only-binary=:all: -e ".[rdp]" ) >"$pip_log" 2>&1; then
        log "aardwolf installed (optional fallback engine)."
        rm -f "$pip_log"
    else
        # Usual cause: a Python newer than the wheels aardwolf publishes (GitHub issues #39, #42).
        # Nothing depends on it any more -- guacd serves RDP and VNC.
        local pyver
        pyver="$("$bd/.venv/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "?")"
        log "aardwolf (optional fallback engine) not installed: its prebuilt wheels cover CPython 3.9-3.13 and this host runs Python ${pyver} (or is offline). RDP / VNC use guacd; nothing else is affected. (pip output: ${pip_log})"
    fi
    # Python side of the FreeRDP engine (screen capture + input injection). These are small,
    # pure-Python packages, so they go on every host -- the heavy part is the apt side
    # (freerdp2-x11, xvfb), which is opt-in. Without these the FreeRDP engine reports
    # itself unavailable in Admin -> System settings rather than failing at connect time.
    if ( cd "$bd" && sudo -u "$u" "$bd/.venv/bin/pip" install --quiet -e ".[rdp-freerdp]" ); then
        log "FreeRDP engine (Python side) installed."
    else
        warn "FreeRDP engine Python dependencies not installed; that engine stays unavailable."
    fi
}

# Kerberos for WinRM (Hyper-V integration; some customers' domains only allow Kerberos, 2026-10-20).
# krb5-user gives kinit (the backend trades the account's password for a ticket in a private cache);
# libkrb5-dev lets pip compile pykerberos, which has no Linux wheels. Best-effort on install AND upgrade:
# without it the Kerberos option shows as unavailable in the Hyper-V form, NTLM keeps working.
ensure_kerberos_support() {
    local bd="${REPO_ROOT}/backend"
    local u; u="$(stat -c '%U' "$bd/.venv" 2>/dev/null || echo jtipam)"
    [ -x "$bd/.venv/bin/pip" ] || return 0
    if ! command -v kinit >/dev/null 2>&1 || ! command -v krb5-config >/dev/null 2>&1; then
        log "Installing Kerberos client tools for WinRM (krb5-user, libkrb5-dev)…"
        if ! DEBIAN_FRONTEND=noninteractive apt-get install -y -qq krb5-user libkrb5-dev >/dev/null 2>&1; then
            warn "could not install krb5-user / libkrb5-dev: Hyper-V can still use NTLM; Kerberos stays unavailable"
            return 0
        fi
    fi
    if "$bd/.venv/bin/python" -c 'import kerberos' >/dev/null 2>&1; then
        log "Kerberos for WinRM present (kinit, pykerberos)"
        return 0
    fi
    local pip_log; pip_log="$(mktemp /tmp/jt-ipam-kerberos.XXXXXX.log)"
    chmod 0644 "$pip_log"
    if ( cd "$bd" && sudo -u "$u" "$bd/.venv/bin/pip" install --quiet -e ".[kerberos]" ) >"$pip_log" 2>&1; then
        log "Kerberos for WinRM installed (pykerberos)."
        rm -f "$pip_log"
    else
        warn "pykerberos could not be built (pip output: ${pip_log}): Hyper-V can still use NTLM; Kerberos stays unavailable"
    fi
}

# apt packages the FreeRDP engine needs. ffmpeg is in the list for screen capture, not for video:
# reading the framebuffer through python-xlib costs 334 ms per 1280x800 frame (it parses 4 MB of
# pixels in pure Python), which caps the console at 2.8 fps. ffmpeg's x11grab uses MIT-SHM and
# measured 47 fps on the same host. ~150 MB of X libraries in total, so this is NOT installed by
# default: the default engine is guacd and most sites never switch. Two ways in:
#   - install --with-freerdp
#   - upgrade, when the site has already selected the FreeRDP engine (see ensure_freerdp_if_selected)
# FreeRDP 2 (xfreerdp) where the distro still ships it, otherwise FreeRDP 3 (xfreerdp3):
# Ubuntu 25.10 / 26.04 only have freerdp3-x11 (GitHub issue #39). The engine accepts either and
# passes the same command-line options (checked against an xrdp target).
freerdp_client_pkg() {
    if apt-cache policy freerdp2-x11 2>/dev/null | grep -q 'Candidate: [0-9]'; then
        echo freerdp2-x11
    else
        echo freerdp3-x11
    fi
}
FREERDP_APT_PACKAGES=("$(freerdp_client_pkg)" xvfb xclip ffmpeg)

# NUMA memory-policy calls (mbind / set_mempolicy / get_mempolicy) for the backend.
# The unit's SystemCallFilter drops @resources, and the filter's default action is to KILL. fpdf2 (PDF
# reports) imports numpy, and numpy's OpenBLAS calls mbind for its thread buffers the moment it loads --
# so the first PDF export killed the uvicorn worker with SIGSYS, silently, taking any other request on
# that worker with it (prod 2026-10-08). These calls only set the NUMA placement of the process's own
# memory: no privilege, no effect on other processes.
# A drop-in rather than an edit to the unit: upgrades do not reinstall jt-ipam-backend.service, so this
# is the only way existing sites get it. Called from BOTH install and upgrade.
BACKEND_NUMA_DROPIN=/etc/systemd/system/jt-ipam-backend.service.d/20-numa-syscalls.conf

ensure_backend_syscall_dropin() {
    mkdir -p "$(dirname "$BACKEND_NUMA_DROPIN")"
    local want
    want="$(cat <<'NUMA'
# Installed by jt-ipam.sh. numpy's OpenBLAS (pulled in by fpdf2 for PDF reports) calls mbind when it loads;
# without this the unit's SystemCallFilter kills the worker with SIGSYS. Own-memory NUMA policy only.
[Service]
SystemCallFilter=mbind set_mempolicy get_mempolicy
NUMA
)"
    if [ -f "$BACKEND_NUMA_DROPIN" ] && [ "$(cat "$BACKEND_NUMA_DROPIN")" = "$want" ]; then
        return 0
    fi
    printf '%s\n' "$want" > "$BACKEND_NUMA_DROPIN"
    systemctl daemon-reload 2>/dev/null || true
    log "Installed systemd drop-in for NUMA memory-policy calls ($BACKEND_NUMA_DROPIN)."
}

# The backend's systemd unit carries a SystemCallFilter allowlist. Xvfb needs four calls the
# backend itself never makes, and the filter's default action is to KILL -- so Xvfb dies with
# SIGSYS, instantly and silently, and the console reports only "could not start a display".
#
# The relaxation is small in practice: the service runs as an unprivileged user with
# NoNewPrivileges=yes, so setresuid/setuid can only move between identities it already has
# (its own), and a non-root fchown cannot give a file away. It is still opt-in, installed only
# with the engine that needs it, so sites on the default engine keep the tighter filter.
FREERDP_DROPIN=/etc/systemd/system/jt-ipam-backend.service.d/freerdp.conf

install_freerdp_dropin() {
    mkdir -p "$(dirname "$FREERDP_DROPIN")"
    cat > "$FREERDP_DROPIN" <<'DROPIN'
# Installed by jt-ipam.sh for the FreeRDP console engine. Safe to delete if you switch back to
# the built-in engine (Admin -> System settings -> RDP connection engine), then:
#   sudo systemctl daemon-reload && sudo systemctl restart jt-ipam-backend
#
# Xvfb needs these four; the backend does not. SystemCallFilter= entries are merged across
# drop-ins, so this adds to the unit's allowlist rather than replacing it.
[Service]
SystemCallFilter=mincore setresuid setuid fchown
DROPIN
    systemctl daemon-reload 2>/dev/null || true
    log "Installed systemd drop-in for the FreeRDP engine ($FREERDP_DROPIN)."
}

install_freerdp_apt() {
    log "Installing FreeRDP engine packages (${FREERDP_APT_PACKAGES[*]})…"
    if ! DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${FREERDP_APT_PACKAGES[@]}"; then
        warn "Could not install FreeRDP engine packages; that engine stays unavailable."
        return 1
    fi
    install_freerdp_dropin
    log "FreeRDP engine packages installed."
    return 0
}

# Does this host already have every FreeRDP package?
freerdp_apt_present() {
    { command -v xfreerdp >/dev/null 2>&1 || command -v xfreerdp3 >/dev/null 2>&1; } \
        && command -v Xvfb >/dev/null 2>&1 \
        && command -v ffmpeg >/dev/null 2>&1
}

# Packages alone are not enough: without the drop-in, Xvfb is killed by the syscall filter.
freerdp_ready() {
    freerdp_apt_present && [ -f "$FREERDP_DROPIN" ]
}

# An upgrade must not silently take away an engine the site is actually using. If the admin has
# selected the FreeRDP engine in Admin -> System settings, make sure its apt packages are present
# -- otherwise the first RDP connection after the upgrade fails with "engine not installed", and
# nothing in the upgrade output said why.
ensure_freerdp_if_selected() {
    command -v apt-get >/dev/null 2>&1 || return 0
    freerdp_ready && return 0
    local selected
    selected="$(sudo -u postgres psql -tAd jt_ipam -c \
        "SELECT value->>'rdp_engine' FROM system_settings WHERE key='console_security'" \
        2>/dev/null | tr -d '[:space:]')"
    [[ "$selected" == "freerdp" ]] || return 0
    if freerdp_apt_present; then
        # 套件都在，只缺 systemd 的放寬設定（升級到有這段程式碼的版本時會走這裡）
        log "This site uses the FreeRDP console engine; installing its systemd drop-in…"
        install_freerdp_dropin
        return 0
    fi
    log "This site uses the FreeRDP console engine but its packages are missing — installing…"
    install_freerdp_apt || warn "RDP consoles will fail until these are installed."
}

# ───────────────────────── guacd（必要元件：RDP／VNC 主控台的預設引擎，SSH 可選）─────────────────────────
# Distributions no longer ship a usable guacd (Debian removed it; Ubuntu only has 1.3.0 with
# known remote-code-execution bugs), so jt-ipam provides its own build per OS version:
# scripts/guacd/ builds them, GitHub releases carry them, scripts/guacd/SHA256SUMS pins them.
# The builds link against the distribution's own libraries (FreeRDP, libvncclient, cairo...),
# so security fixes for those still come from apt.
#
# guacd is a REQUIRED component (since 2026-09-27, GitHub issue #42): the default RDP / VNC console
# engine. The old engine, aardwolf, is optional (a fallback) -- it has no wheels for new Pythons
# and crashes on 3.14. install stops if guacd cannot be installed; upgrade installs it when
# missing and, if that fails, warns loudly but still finishes (stopping after the code update
# and before the migrations would leave the site broken) -- doctor then reports it as a problem.
#
#   install / upgrade                        install it, or keep it current
#   --guacd-tarball <file>                   offline hosts / builds that are not published yet:
#                                            use this file (its .deps must sit next to it)
GUACD_PREFIX=/opt/jt-ipam-guacd
GUACD_UNIT=jt-ipam-guacd.service
GUACD_RELEASE_BASE="https://github.com/jasoncheng7115/jt-ipam/releases/download"

# 要裝哪個 build：以 scripts/guacd/SHA256SUMS 實際列出的為準（這個 OS 最新的 rev），沒有才退回 source.env。
# source.env 的 GUACD_BUILD_REV 是「下一次要編」的版本：先升上去推到 GitHub 才能觸發 workflow 編出新 release，
# release 與新的 SHA256SUMS 進來之前不可以去抓還不存在的檔案（那段空窗期客戶安裝會失敗；2026-10-23 為了 Ubuntu 26.10）
guacd_published_rev() {
    local ver os_tag arch rev
    ver="$(grep -oP '^GUACD_VERSION=\K\S+' "$REPO_ROOT/scripts/guacd/source.env")"
    # shellcheck disable=SC1091
    os_tag="$(. /etc/os-release && echo "${ID}${VERSION_ID}")"
    arch="$(dpkg --print-architecture)"
    rev="$(grep -oP "jt-ipam-guacd-\Q${ver}\E-\K[0-9]+(?=-\Q${os_tag}-${arch}\E\.tar\.gz$)" \
             "$REPO_ROOT/scripts/guacd/SHA256SUMS" 2>/dev/null | sort -n | tail -1 || true)"
    [[ -n "$rev" ]] || rev="$(grep -oP '^GUACD_BUILD_REV=\K\S+' "$REPO_ROOT/scripts/guacd/source.env")"
    echo "$rev"
}

# jt-ipam-guacd-<version>-<rev>-<os><ver>-<arch>, e.g. ...-ubuntu24.04-amd64
guacd_package_name() {
    local ver rev
    ver="$(grep -oP '^GUACD_VERSION=\K\S+' "$REPO_ROOT/scripts/guacd/source.env")"
    rev="$(guacd_published_rev)"
    # shellcheck disable=SC1091
    local os_tag; os_tag="$(. /etc/os-release && echo "${ID}${VERSION_ID}")"
    echo "jt-ipam-guacd-${ver}-${rev}-${os_tag}-$(dpkg --print-architecture)"
}

guacd_installed_version() {
    grep -oP '^version:\s*\K.*' "$GUACD_PREFIX/share/doc/jt-ipam-guacd/SOURCE" 2>/dev/null | head -1
}

# install_guacd [tarball]
install_guacd() {
    command -v apt-get >/dev/null 2>&1 || { warn "guacd needs apt (Debian / Ubuntu); skipped."; return 1; }
    local name tmp tarball deps sums want got
    name="$(guacd_package_name)"
    tmp="$(mktemp -d)"
    if [[ -n "${1:-}" ]]; then
        tarball="$1"
        deps="${tarball%.tar.gz}.deps"
        [[ -f "$tarball" && -f "$deps" ]] || { warn "guacd: $tarball or its .deps file is missing."; rm -rf "$tmp"; return 1; }
    else
        local tag="guacd-${name#jt-ipam-guacd-}"; tag="${tag%-*-*}"   # guacd-<version>-<rev>
        log "Downloading guacd for this OS ($name)…"
        if ! curl -fsSL --retry 2 -o "$tmp/$name.tar.gz" "$GUACD_RELEASE_BASE/$tag/$name.tar.gz" \
           || ! curl -fsSL --retry 2 -o "$tmp/$name.deps" "$GUACD_RELEASE_BASE/$tag/$name.deps"; then
            warn "guacd: no prebuilt package for this OS at $GUACD_RELEASE_BASE/$tag/ ($name)."
            warn "  Offline, or a new OS version not built yet: pass --guacd-tarball <file>."
            rm -rf "$tmp"; return 1
        fi
        tarball="$tmp/$name.tar.gz"; deps="$tmp/$name.deps"
    fi
    # Integrity: a published build must match scripts/guacd/SHA256SUMS. A local file that is not
    # listed (a build that has not been published yet) is allowed, but said out loud.
    sums="$REPO_ROOT/scripts/guacd/SHA256SUMS"
    got="$(sha256sum "$tarball" | cut -d' ' -f1)"
    # `|| true`：還沒有 SHA256SUMS（從未發佈過）時 awk 讀不到檔案，set -e 會讓整支腳本安靜地結束
    want="$(awk -v f="$(basename "$tarball")" '$2==f {print $1}' "$sums" 2>/dev/null || true)"
    if [[ -n "$want" && "$want" != "$got" ]]; then
        warn "guacd: checksum mismatch for $(basename "$tarball") (expected $want, got $got) -- NOT installing."
        rm -rf "$tmp"; return 1
    elif [[ -z "$want" ]]; then
        if [[ -z "${1:-}" ]]; then
            warn "guacd: $(basename "$tarball") is not listed in scripts/guacd/SHA256SUMS -- NOT installing."
            rm -rf "$tmp"; return 1
        fi
        warn "guacd: $(basename "$tarball") is not a published build (not in SHA256SUMS); installing the local file as given."
    fi
    log "Installing guacd runtime packages ($(tr '\n' ' ' < "$deps"))…"
    # shellcheck disable=SC2046
    if ! DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends $(cat "$deps"); then
        warn "guacd: could not install its runtime packages."; rm -rf "$tmp"; return 1
    fi
    systemctl stop "$GUACD_UNIT" 2>/dev/null || true
    rm -rf "$GUACD_PREFIX"
    tar -C / -xzf "$tarball" || { warn "guacd: could not unpack $tarball."; rm -rf "$tmp"; return 1; }
    rm -rf "$tmp"
    install -m 0644 "$REPO_ROOT/deploy/systemd/$GUACD_UNIT" "/etc/systemd/system/$GUACD_UNIT"
    systemctl daemon-reload
    systemctl enable --now "$GUACD_UNIT" >/dev/null 2>&1 || true
    local i
    for i in $(seq 1 20); do
        # 用連的：精簡映像常常沒有 iproute2（ss），用 ss 看的話在那種機器上永遠等滿 10 秒
        (exec 3<>/dev/tcp/127.0.0.1/4822) 2>/dev/null && break
        sleep 0.5
    done
    if ! systemctl is-active --quiet "$GUACD_UNIT"; then
        warn "guacd installed but not running -- journalctl -u jt-ipam-guacd -n 50"
        return 1
    fi
    log "guacd $(guacd_installed_version) running on 127.0.0.1:4822 (default RDP / VNC console engine)."
    return 0
}

# On upgrade: install guacd (required) when missing, or keep it on the build for this version.
ensure_guacd_current() {
    local tarball="${1:-}"
    command -v apt-get >/dev/null 2>&1 || return 0
    local installed=0
    [[ -x "$GUACD_PREFIX/sbin/guacd" ]] && installed=1
    [[ "$installed" == "0" ]] && log "Installing guacd (required: the RDP / VNC console engine)…"
    local name want_ver
    name="$(guacd_package_name)"
    want_ver="$(grep -oP '^GUACD_VERSION=\K\S+' "$REPO_ROOT/scripts/guacd/source.env") (build $(guacd_published_rev))"
    if [[ "$installed" == "1" && -z "$tarball" && "$(guacd_installed_version)" == "$want_ver"* ]]; then
        # Same build: just make sure the unit is current and running
        install -m 0644 "$REPO_ROOT/deploy/systemd/$GUACD_UNIT" "/etc/systemd/system/$GUACD_UNIT"
        systemctl daemon-reload
        systemctl enable --now "$GUACD_UNIT" >/dev/null 2>&1 || true
        systemctl restart "$GUACD_UNIT" 2>/dev/null || true
        return 0
    fi
    if ! install_guacd "$tarball"; then
        warn "================================================================================"
        warn "guacd (REQUIRED) was not installed/updated -- see above. The upgrade continues so the"
        warn "site keeps working, but RDP / VNC consoles only work through the optional built-in"
        warn "engine until guacd is in place. Fix, then re-run: sudo $0 upgrade"
        warn "  (offline host / OS without a prebuilt package: sudo $0 upgrade --guacd-tarball <file>)"
        warn "================================================================================"
    fi
}

# Node.js only builds the frontend; nothing runs on it once the site is up.
NODE_REQUIRED_MAJOR=22          # same as frontend/package.json "engines", .nvmrc and CI
NODE_FALLBACK_MAJOR=20          # upgrade only: the oldest node kept when installing 22 fails
# Test hooks (like JT_IPAM_MEMINFO): where links for root go, and where NodeSource puts node.
NODE_LINK_DIR="${JT_IPAM_NODE_LINK_DIR:-/usr/local/bin}"
NODE_SYSTEM_BIN="${JT_IPAM_NODE_SYSTEM_BIN:-/usr/bin/node}"
NODE_READY=0                    # settled for this run (upgrade checks early, then builds)

# Major version of a node binary; 0 when it is missing or does not run.
node_major() {
    local v
    v="$("${1:-node}" -v 2>/dev/null || true)"
    v="${v#v}"; v="${v%%.*}"
    if [[ "$v" =~ ^[0-9]+$ ]]; then echo "$v"; else echo 0; fi
}

# Ensure Node.js >= $NODE_REQUIRED_MAJOR is available to root for the frontend build.
#   $1 = install | upgrade
# In this order:
#  - the node on PATH is already new enough (NodeSource 22, a distro 22, an earlier link): use it
#  - invoked via sudo and the caller has an nvm node >= 22: link it for root (nvm's node is not
#    on root's PATH). An nvm node OLDER than 22 is not reused: NodeSource 22 is installed for
#    root instead, so the build does not depend on what a developer left in their home.
#  - otherwise install NodeSource 22. On a site that already has NodeSource 20 (every install
#    up to v0.6.61) this rewrites the apt source and upgrades the same 'nodejs' package in place.
#
# Why upgrade falls back and install does not: node is only the build toolchain, and the build
# still works on Node 20 today. A site that cannot reach deb.nodesource.com during an upgrade
# (proxy, local mirror, outage) is better off upgraded with a loud warning than stuck on the old
# version over a toolchain download; the next upgrade tries 22 again, and doctor keeps saying so.
# A fresh install has no site to keep running and no toolchain known to work, so there a node
# that is not 22 stops the install with a clear message.
ensure_node() {
    local mode="${1:-install}" cur_bin cur h nb="" fallback="" sys_major
    [[ "$NODE_READY" == 1 ]] && return 0
    hash -r 2>/dev/null || true
    cur_bin="$(command -v node 2>/dev/null || true)"
    cur="$(node_major "$cur_bin")"
    if (( cur >= NODE_REQUIRED_MAJOR )); then
        log "Using Node.js $("$cur_bin" -v) ($cur_bin)"
        NODE_READY=1
        return 0
    fi
    if [[ "$mode" == upgrade ]] && (( cur >= NODE_FALLBACK_MAJOR )); then
        fallback="$cur_bin"
    fi

    if [[ -n "${SUDO_USER:-}" ]]; then
        # WARNING: with `set -e` + `pipefail`, `var=$(pipeline)` fails the whole
        # assignment when the pipeline fails, so the script exits SILENTLY with no
        # error at all. Here `find` returns non-zero when ~/.nvm does not exist, and
        # `| head` can SIGPIPE its upstream -- hence the mandatory `|| true`. This one
        # line was why a customer's Debian 13 install printed "Building frontend..."
        # and dropped straight back to the prompt.
        h=$(getent passwd "$SUDO_USER" | cut -d: -f6 || true)
        # nvm keeps node at ~/.nvm/versions/node/vX.Y.Z/bin/node, three levels down. This used
        # to be `-maxdepth 2`, which never matched, so the nvm case had silently never run.
        nb=$(find "$h/.nvm/versions/node" -mindepth 3 -maxdepth 3 -path '*/bin/node' -type f 2>/dev/null | sort -Vr | head -1 || true)
        if [[ -n "$nb" ]] && (( $(node_major "$nb") >= NODE_REQUIRED_MAJOR )); then
            ln -sf "$nb" "$NODE_LINK_DIR/node"
            ln -sf "$(dirname "$nb")/npm" "$NODE_LINK_DIR/npm" 2>/dev/null || true
            hash -r
            log "Using nvm Node.js $("$nb" -v) from \$SUDO_USER ($SUDO_USER)"
        elif [[ -n "$nb" ]]; then
            log "nvm Node.js $("$nb" -v) of \$SUDO_USER ($SUDO_USER) is older than ${NODE_REQUIRED_MAJOR}; not using it."
        fi
    fi

    if (( $(node_major "$(command -v node 2>/dev/null || true)") < NODE_REQUIRED_MAJOR )); then
        log "Installing Node.js ${NODE_REQUIRED_MAJOR} (NodeSource)…"
        # NOTE: errors are NOT silenced here — a failed Node install must be visible, not
        # swallowed (a silent failure leaves the frontend unbuilt yet the install "looks" OK).
        curl -fsSL "https://deb.nodesource.com/setup_${NODE_REQUIRED_MAJOR}.x" | bash - \
            || warn "NodeSource setup script returned non-zero (see output above)"
        if ! apt-get install -y nodejs; then
            if [[ -n "$fallback" ]]; then
                # No purge-and-retry here: it would remove the very node this upgrade falls back
                # on, and when the retry failed too (offline, proxy) the site would have none.
                warn "nodejs ${NODE_REQUIRED_MAJOR} did not install; keeping the existing Node.js $("$fallback" -v) ($fallback)."
            else
                # Likely the distro libnode-dev/headers (e.g. Ubuntu 22.04 v12) conflict with the
                # NodeSource package files → purge the distro node stack and retry once.
                warn "nodejs install hit a conflict; purging distro node packages and retrying…"
                apt-get purge -y nodejs npm libnode-dev 2>/dev/null || true
                apt-get autoremove -y 2>/dev/null || true
                apt-get install -y nodejs || true
            fi
        fi
        hash -r
        # NodeSource puts node in /usr/bin, but an older node earlier on PATH still wins --
        # typically a /usr/local/bin/node link an earlier run made to a sudo user's nvm node --
        # and the build would quietly go on using it.
        cur_bin="$(command -v node 2>/dev/null || true)"
        sys_major="$(node_major "$NODE_SYSTEM_BIN")"
        if [[ -n "$cur_bin" ]] && (( sys_major >= NODE_REQUIRED_MAJOR )) \
                && (( $(node_major "$cur_bin") < NODE_REQUIRED_MAJOR )); then
            if [[ -L "$cur_bin" && "$(readlink "$cur_bin")" == */.nvm/* ]]; then
                warn "Removing $cur_bin -> $(readlink "$cur_bin") (Node.js $("$cur_bin" -v)); it hid Node.js $("$NODE_SYSTEM_BIN" -v) in $NODE_SYSTEM_BIN."
                rm -f "$cur_bin"
                if [[ -L "$(dirname "$cur_bin")/npm" && "$(readlink "$(dirname "$cur_bin")/npm")" == */.nvm/* ]]; then
                    rm -f "$(dirname "$cur_bin")/npm"
                fi
            else
                warn "$cur_bin (Node.js $("$cur_bin" -v)) comes before $NODE_SYSTEM_BIN on PATH; using $(dirname "$NODE_SYSTEM_BIN") first for this run."
                PATH="$(dirname "$NODE_SYSTEM_BIN"):$PATH"
            fi
            hash -r
        fi
    fi

    # Verdict: verify what PATH really resolves to now -- don't let the frontend build fail
    # later (or quietly use an old node) while the install appears successful.
    cur_bin="$(command -v node 2>/dev/null || true)"
    cur="$(node_major "$cur_bin")"
    if (( cur >= NODE_REQUIRED_MAJOR )); then
        log "Using Node.js $("$cur_bin" -v) ($cur_bin)"
    elif [[ -n "$fallback" ]] && (( cur >= NODE_FALLBACK_MAJOR )); then
        warn "================================================================================"
        warn "Node.js ${NODE_REQUIRED_MAJOR} could not be installed -- see above. The upgrade continues and builds the"
        warn "frontend with the existing Node.js $("$cur_bin" -v) ($cur_bin), which still works for now;"
        warn "a later version will need ${NODE_REQUIRED_MAJOR}. Fix it, then re-run the upgrade:"
        warn "  curl -fsSL https://deb.nodesource.com/setup_${NODE_REQUIRED_MAJOR}.x | sudo bash - && sudo apt-get install -y nodejs"
        warn "  sudo $0 upgrade"
        warn "================================================================================"
    else
        die "Node.js install failed or too old (need >= ${NODE_REQUIRED_MAJOR}; got '$([[ -n "$cur_bin" ]] && "$cur_bin" -v 2>/dev/null || echo none)').\n  Install Node ${NODE_REQUIRED_MAJOR} manually, then re-run ${mode}:\n  curl -fsSL https://deb.nodesource.com/setup_${NODE_REQUIRED_MAJOR}.x | sudo bash - && sudo apt-get install -y nodejs"
    fi
    NODE_READY=1
}

# Memory the system can still hand out, in MB: MemAvailable + free swap, capped by the
# container's cgroup limit (without lxcfs /proc/meminfo shows the host's memory).
# JT_IPAM_MEMINFO / JT_IPAM_CGROUP_DIR only exist for the tests.
mem_available_mb() {
    local avail swap cgdir cgmax cgcur
    avail="$(awk '/^MemAvailable:/ {print int($2/1024)}' "${JT_IPAM_MEMINFO:-/proc/meminfo}" 2>/dev/null || echo 0)"
    swap="$(awk '/^SwapFree:/ {print int($2/1024)}' "${JT_IPAM_MEMINFO:-/proc/meminfo}" 2>/dev/null || echo 0)"
    avail=$(( ${avail:-0} + ${swap:-0} ))
    cgdir="${JT_IPAM_CGROUP_DIR:-/sys/fs/cgroup}"
    cgmax="$(cat "$cgdir/memory.max" 2>/dev/null || echo max)"
    cgcur="$(cat "$cgdir/memory.current" 2>/dev/null || echo 0)"
    if [[ "$cgmax" =~ ^[0-9]+$ && "$cgcur" =~ ^[0-9]+$ ]] && (( (cgmax - cgcur) / 1048576 < avail )); then
        avail=$(( (cgmax - cgcur) / 1048576 ))
    fi
    echo "$avail"
}

# The frontend build peaks around 1.6 GB (measured 2026-10-02). On a 4 GB machine with the
# backend running, an upgrade that meets a background sync could be OOM-killed half way.
# When memory is short, pause the backend for the build (about a minute; the upgrade
# restarts it anyway) instead of letting the kernel pick a victim. Sets BUILD_PAUSED_BACKEND.
FRONTEND_BUILD_MIN_MB=2048
BUILD_PAUSED_BACKEND=0
ensure_build_memory() {
    local avail
    avail="$(mem_available_mb)"
    (( avail >= FRONTEND_BUILD_MIN_MB )) && return 0
    if systemctl is-active --quiet jt-ipam-backend.service 2>/dev/null; then
        warn "Only ${avail} MB of memory available; pausing jt-ipam-backend during the frontend build (needs about ${FRONTEND_BUILD_MIN_MB} MB)."
        systemctl stop jt-ipam-backend.service 2>/dev/null || true
        BUILD_PAUSED_BACKEND=1
        sleep 2
        avail="$(mem_available_mb)"
    fi
    if (( avail < FRONTEND_BUILD_MIN_MB )); then
        warn "Only ${avail} MB of memory available for the frontend build (about ${FRONTEND_BUILD_MIN_MB} MB is needed)."
        warn "If the build is killed, add swap and re-run: fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile"
    fi
}

# Build the frontend as root with a clean toolchain, then hand ownership back to $2.
# Why as root: avoids (a) stale corepack pnpm shims pinned to an old /usr/bin/node, and
# (b) sudo -u / PAM failures when the owner is a nologin system account on restrictive hosts.
# $1 = frontend dir, $2 = owner (user:group), $3 = install | upgrade (how ensure_node may fall back)
build_frontend() {
    local fdir="$1" owner="$2" pnpm_bin
    ensure_node "${3:-install}"
    cd "$fdir"
    # drop stale corepack pnpm shims (they may hardcode an old node path → v12 errors)
    rm -f /usr/bin/pnpm /usr/local/bin/pnpm 2>/dev/null || true

    # Install pnpm and VERIFY it runs. This used to be `>/dev/null 2>&1 || true` followed by a
    # fallback to the literal path /usr/local/bin/pnpm — when the install failed (a customer hit
    # this on Debian 12) the build died with "no such file or directory" and the actual npm error
    # had been thrown away. Keep the error, and try the other ways of getting pnpm before giving up.
    local pnpm_log; pnpm_log="$(mktemp)"
    npm install -g --prefix /usr/local pnpm@9 >"$pnpm_log" 2>&1 \
        || npm install -g pnpm@9 >>"$pnpm_log" 2>&1 \
        || { command -v corepack >/dev/null 2>&1 && corepack enable pnpm >>"$pnpm_log" 2>&1; } \
        || true
    hash -r 2>/dev/null || true
    pnpm_bin="$(command -v pnpm || true)"
    if [[ -z "$pnpm_bin" ]] || ! "$pnpm_bin" --version >/dev/null 2>&1; then
        warn "Could not install pnpm; last output was:"
        sed 's/^/    /' "$pnpm_log" >&2 || true
        rm -f "$pnpm_log"
        die "pnpm is required to build the frontend.\n  Install it manually and re-run:\n    sudo npm install -g pnpm@9   # or: curl -fsSL https://get.pnpm.io/install.sh | sh -"
    fi
    rm -f "$pnpm_log"
    log "Using pnpm $("$pnpm_bin" --version) (node $(node -v))"

    # CI=true: when node_modules was laid out by another pnpm version/config, pnpm asks
    # "The modules directory will be removed and reinstalled. Proceed?" and, with no terminal
    # (upgrade over SSH, cron, systemd), takes no answer as "no" and installs nothing; the build
    # then fails on the new dependency (2026-10-05, tweetnacl on the production host).
    HOME=/var/lib/jt-ipam CI=true "$pnpm_bin" install --frozen-lockfile \
        || HOME=/var/lib/jt-ipam CI=true "$pnpm_bin" install
    ensure_build_memory
    if ! HOME=/var/lib/jt-ipam "$pnpm_bin" run build; then
        # a failed upgrade must not leave the site down because we paused the backend for the build
        if (( BUILD_PAUSED_BACKEND == 1 )); then
            systemctl start jt-ipam-backend.service 2>/dev/null || true
        fi
        die "Frontend build failed (see the output above). If it was killed for lack of memory, add swap and re-run."
    fi
    chown -R "$owner" node_modules dist 2>/dev/null || true
}

# Direct-TLS mode on a privileged port: the service does not run as root, so binding
# 443 needs CAP_NET_BIND_SERVICE. Without it the unit starts and immediately dies with
# "Permission denied" — which reads like a TLS problem, not a port problem.
grant_bind_privileged_port() {
    local port="$1"
    [[ "$port" =~ ^[0-9]+$ ]] || return 0
    (( port < 1024 )) || return 0
    local dir=/etc/systemd/system/jt-ipam-backend.service.d
    local conf="$dir/20-bind-privileged-port.conf"
    install -d -m 0755 "$dir"
    cat > "$conf" <<'BINDCAP'
# jt-ipam: added by the installer because the backend binds a port below 1024.
# The service runs as an unprivileged user, so systemd has to grant the capability.
[Service]
AmbientCapabilities=CAP_NET_RAW CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_RAW CAP_NET_BIND_SERVICE
BINDCAP
    systemctl daemon-reload >/dev/null 2>&1 || true
    log "Granted CAP_NET_BIND_SERVICE so the backend can bind port ${port} (${conf})"
}


# Directories that sandboxed units reference must exist before the unit starts.
# With ProtectSystem=strict + ReadWritePaths=<dir>, a missing <dir> makes systemd fail at
# namespace setup with 226/NAMESPACE and the message "Failed at step NAMESPACE spawning
# <ExecStart>: No such file or directory" — which reads as "the script is missing" and sends
# people looking in the wrong place entirely (a customer lost time to exactly this).
ensure_unit_dirs() {
    install -d -m 0700 /var/backups/jt-ipam 2>/dev/null || true
    install -d -m 0755 /etc/jt-ipam 2>/dev/null || true
}

# Every console protocol whose WebSocket needs the nginx upgrade headers.
# Adding a protocol here is the ONLY place to change: both the fresh-install
# template check and the upgrade patch below are derived from it.
#
# Getting this wrong fails in a way that is hard to read: without the upgrade
# headers nginx forwards a plain GET, the backend has no HTTP route at that
# path, and the browser sees a bare 404 with nothing to suggest the proxy.
# That is exactly how SFTP shipped broken in 0.5.155.
WS_PROTOCOLS='ssh|sftp|rdp|vnc|novnc|bmc|rustdesk'
WS_LOCATION_LINE="location ~ ^/api/v1/addresses/[0-9a-fA-F-]+/(${WS_PROTOCOLS})/ws\$ {"

# Idempotently stop writing tokens into nginx's access log on an EXISTING site (2026-10-09):
# Graylog DSV lookups and the rack embed SVG carry their token in the query string, and the
# default "combined" format logs the whole request line. Adds a query-less log format, uses it
# for those locations (and a dedicated HTTPS lookup location). Same safety rules as the other
# patch_* functions: only-if-missing marker, back up, gate on `nginx -t`, restore on failure.
patch_nginx_token_logging() {
    local site=/etc/nginx/sites-available/jt-ipam
    [[ -f "$site" ]] || return 0
    command -v nginx >/dev/null 2>&1 || return 0
    grep -q 'jtipam_noquery' "$site" && return 0
    local bak="${site}.pre-noquery.bak"
    cp -p "$site" "$bak" 2>/dev/null || true
    log "Keeping DSV / rack-embed tokens out of the nginx access log…"
    awk '
      !fmt && /^server[[:space:]]*\{/ {
        print "# Requests whose query string carries a token (Graylog DSV lookups, rack embed SVG) are logged without it.";
        print "log_format jtipam_noquery \047$remote_addr - $remote_user [$time_local] \"$request_method $uri $server_protocol\" \047";
        print "                          \047$status $body_bytes_sent \"$http_referer\" \"$http_user_agent\"\047;";
        print "";
        fmt = 1;
      }
      !lk && /location \/api\/ \{/ {
        print "    # jtipam-lookup-noquery: Graylog DSV lookups carry the token in the query string.";
        print "    location /api/v1/lookup/ {";
        print "        access_log /var/log/nginx/access.log jtipam_noquery;";
        print "        limit_req zone=api burst=80 nodelay;";
        print "        proxy_pass http://127.0.0.1:8000;";
        print "        include /etc/nginx/snippets/jt-ipam-proxy.conf;";
        print "    }";
        print "";
        lk = 1;
      }
      { print }
      /location \/api\/v1\/lookup\/ \{/ || /embed\\\.svg\$ \{/ {
        if (!(getline nxt)) { next }
        if (nxt !~ /jtipam_noquery/) print "        access_log /var/log/nginx/access.log jtipam_noquery;";
        print nxt;
      }
    ' "$site" > "${site}.tmp" && mv "${site}.tmp" "$site"
    if apply_nginx_config; then
        log "nginx access log no longer records DSV / rack-embed tokens."
    else
        warn "nginx -t failed after the access-log patch; restoring previous config."
        cp -p "$bak" "$site" 2>/dev/null || true
        apply_nginx_config || true
    fi
    return 0
}

# Apply an nginx config change: test it, make sure nginx is actually RUNNING and
# enabled at boot, then pick reload or start as appropriate.
#
# Why this is a function and not an inline `systemctl reload nginx`: reload on a
# stopped unit does nothing except print "nginx.service is not active, cannot
# reload" -- and, since the message goes by in a wall of install output and the
# exit status is swallowed, a fresh install could finish with a fully configured
# nginx that had never been started and was not enabled at boot. Everything looked
# installed; the product was simply unreachable. Caught by scripts/test-fresh-install.sh.
# Idempotently fix two locations in an EXISTING nginx site on upgrade (2026-09-30):
#   - /readyz fell through to the SPA (always 200 + index.html) -> proxy it to the backend
#   - the phpIPAM login rate limit used a prefix that never matches /api/phpipam/<app_id>/user/
# Same safety rules as patch_nginx_websocket: only-if-missing, back up, gate on `nginx -t`,
# restore on failure, never abort the upgrade.
patch_nginx_readyz_phpipam() {
    local site=/etc/nginx/sites-available/jt-ipam
    [[ -f "$site" ]] || return 0
    command -v nginx >/dev/null 2>&1 || return 0
    local need_ready=0 need_php=0 need_embed=0 need_429=0
    grep -q 'location = /readyz' "$site" || need_ready=1
    grep -q 'location /api/phpipam/user/ {' "$site" && need_php=1
    grep -q 'embed\\.svg' "$site" || need_embed=1
    grep -q '@rate_limited' "$site" || need_429=1
    (( need_ready || need_php || need_embed || need_429 )) || return 0
    local bak="${site}.pre-readyz.bak"
    cp -p "$site" "$bak" 2>/dev/null || true
    if (( need_php )); then
        log "Fixing nginx phpIPAM login rate-limit location…"
        sed -i 's#location /api/phpipam/user/ {#location ~ ^/api/phpipam/[^/]+/user/ {#' "$site"
    fi
    if (( need_ready )); then
        log "Adding nginx /readyz location (was answered by the SPA)…"
        awk '
          !ins && /location \/api\/ \{/ {
            print "    location = /readyz {";
            print "        access_log off;";
            print "        proxy_pass http://127.0.0.1:8000;";
            print "        include /etc/nginx/snippets/jt-ipam-proxy.conf;";
            print "    }";
            print "";
            ins = 1;
          }
          { print }
        ' "$site" > "${site}.tmp" && mv "${site}.tmp" "$site"
    fi
    if (( need_embed )); then
        # Rack embed SVG: other sites load it with <img>; the server-level CORP same-origin blocks
        # that and the image/* expires rule caches it for 30 days over the backend's no-store.
        log "Adding nginx location for the rack embed SVG (cross-origin, not cached)…"
        awk '
          !done && /location \/api\/ \{/ {
            print "    location ~ ^/api/v1/racks/[0-9a-fA-F-]+/embed\\.svg$ {";
            print "        limit_req zone=api burst=80 nodelay;";
            print "        expires off;";
            print "        add_header Strict-Transport-Security \"max-age=63072000; includeSubDomains; preload\" always;";
            print "        add_header X-Content-Type-Options \"nosniff\" always;";
            print "        add_header Referrer-Policy \"strict-origin-when-cross-origin\" always;";
            print "        add_header Cross-Origin-Resource-Policy \"cross-origin\" always;";
            print "        proxy_pass http://127.0.0.1:8000;";
            print "        include /etc/nginx/snippets/jt-ipam-proxy.conf;";
            print "    }";
            print "";
            done = 1;
          }
          { print }
        ' "$site" > "${site}.tmp" && mv "${site}.tmp" "$site"
    fi
    if (( need_429 )); then
        # nginx's own 429 (limit_req) carried no Retry-After, unlike the backend's (2026-10-01).
        log "Adding Retry-After to nginx rate-limit responses…"
        awk '
          !done && /location \/api\/ \{/ {
            print "    # Requests nginx itself rejects (limit_req) get Retry-After too, like the backend'"'"'s 429.";
            print "    error_page 429 @rate_limited;";
            print "    location @rate_limited {";
            print "        default_type application/json;";
            print "        add_header Retry-After 60 always;";
            print "        add_header Strict-Transport-Security \"max-age=63072000; includeSubDomains; preload\" always;";
            print "        add_header X-Content-Type-Options \"nosniff\" always;";
            print "        return 429 \047{\"detail\":\"Too Many Requests\"}\047;";
            print "    }";
            print "";
            done = 1;
          }
          { print }
        ' "$site" > "${site}.tmp" && mv "${site}.tmp" "$site"
    fi
    if apply_nginx_config; then
        log "nginx /readyz + phpIPAM login limit + rack embed + 429 Retry-After patched + reloaded."
    else
        warn "nginx -t failed after the /readyz / phpIPAM patch; restoring previous config."
        cp -p "$bak" "$site" 2>/dev/null || true
    fi
    return 0
}

apply_nginx_config() {
    if ! nginx -t >/dev/null 2>&1; then
        warn "nginx config test failed; leaving the running config alone. Check: sudo nginx -t"
        return 1
    fi
    systemctl enable nginx >/dev/null 2>&1 || true
    if systemctl is-active --quiet nginx; then
        systemctl reload nginx || systemctl restart nginx || true
    else
        systemctl start nginx || true
    fi
    if systemctl is-active --quiet nginx; then
        return 0
    fi
    warn "nginx did not start. jt-ipam is unreachable until it does -- check: systemctl status nginx"
    return 1
}

# Idempotently add WebSocket upgrade support (consoles) to an EXISTING nginx
# site on upgrade. Fresh installs already ship the correct template; upgrade
# deliberately leaves the (often hand-customized) site config alone, so we patch
# only the two WS bits in-place when missing.
#
# Safe by design: only-if-missing (marker grep), back up first, gate on `nginx -t`,
# restore on failure, and NEVER abort the upgrade (always returns 0).
patch_nginx_websocket() {
    local site=/etc/nginx/sites-available/jt-ipam
    [[ -f "$site" ]] || return 0                       # not nginx mode → nothing to do
    command -v nginx >/dev/null 2>&1 || return 0
    # Already lists every current protocol → nothing to do
    grep -qF "(${WS_PROTOCOLS})/ws" "$site" && return 0

    local bak="${site}.pre-ws.bak"

    # An existing WS location with ANY protocol list → rewrite the whole line to
    # the current one. Matching the line rather than each historical list means a
    # newly added protocol can never be missed (four hand-written substitutions
    # used to be needed, and SFTP was the one that got forgotten).
    if grep -qE 'location ~ \^/api/v1/addresses/\[0-9a-fA-F-\]\+/.*/ws\$' "$site"; then
        log "Widening nginx WebSocket location to cover ${WS_PROTOCOLS}…"
        cp -p "$site" "$bak" 2>/dev/null || true
        awk -v repl="    ${WS_LOCATION_LINE}" \
            '/location ~ \^\/api\/v1\/addresses\/\[0-9a-fA-F-\]\+\/.*\/ws\$/ { print repl; next } { print }' \
            "$site" > "${site}.tmp" && mv "${site}.tmp" "$site"
        if apply_nginx_config; then
            log "nginx WebSocket location widened (${WS_PROTOCOLS}) + reloaded."
        else
            warn "nginx -t failed after widening WS location; restoring previous config."
            cp -p "$bak" "$site" 2>/dev/null || true
        fi
        return 0
    fi

    log "Patching nginx site for console WebSocket (${WS_PROTOCOLS})…"
    cp -p "$site" "$bak" 2>/dev/null || true

    # 1) http-level map (skip if some connection_upgrade map already exists)
    if ! grep -q 'connection_upgrade' "$site"; then
        { printf '%s\n' \
            '# jt-ipam-conn-ws: WebSocket upgrade map (added on upgrade)' \
            'map $http_upgrade $connection_upgrade { default upgrade; '\'''\'' close; }' \
            ''; cat "$site"; } > "${site}.tmp" && mv "${site}.tmp" "$site"
    fi

    # 2) dedicated WS location, inserted before the first "location /api/ {"
    # NB: set headers explicitly (do NOT include jt-ipam-proxy.conf) — that snippet
    # already sets proxy_read_timeout, and re-declaring it here = "duplicate directive".
    awk -v wsloc="    ${WS_LOCATION_LINE}" '
      !ins && /location \/api\/ \{/ {
        print "    # jt-ipam-conn-ws: console WebSocket (long-lived)";
        print wsloc;
        print "        proxy_pass http://127.0.0.1:8000;";
        print "        proxy_http_version 1.1;";
        print "        proxy_set_header Host               $host;";
        print "        proxy_set_header X-Real-IP          $remote_addr;";
        print "        proxy_set_header X-Forwarded-For    $proxy_add_x_forwarded_for;";
        print "        proxy_set_header X-Forwarded-Proto  $scheme;";
        print "        proxy_set_header X-Request-ID       $request_id;";
        print "        proxy_set_header Upgrade            $http_upgrade;";
        print "        proxy_set_header Connection         $connection_upgrade;";
        print "        proxy_read_timeout 3600s;";
        print "        proxy_send_timeout 3600s;";
        print "        proxy_buffering off;";
        print "    }";
        print "";
        ins = 1;
      }
      { print }
    ' "$site" > "${site}.tmp" && mv "${site}.tmp" "$site"

    if apply_nginx_config; then
        log "nginx WebSocket patch applied + reloaded."
    else
        warn "nginx -t failed after WebSocket patch; restoring previous config."
        warn "  SSH terminal needs a manual nginx update — see deploy/nginx/jt-ipam.conf."
        cp -p "$bak" "$site" 2>/dev/null || true
    fi
    return 0
}

# Console relay through scan agents (issue #24 phase 2, 2026-10-02): agents dial OUT to
# /api/v1/scan-agents/relay/<sid>/ws. Without a WebSocket location nginx does not forward the
# Upgrade and closes idle sockets after 60 s. Same safety rules as patch_nginx_websocket:
# only-if-missing, back up, gate on `nginx -t`, restore on failure, never abort the upgrade.
patch_nginx_agent_relay() {
    local site=/etc/nginx/sites-available/jt-ipam
    [[ -f "$site" ]] || return 0
    command -v nginx >/dev/null 2>&1 || return 0
    grep -q 'scan-agents/relay' "$site" && return 0
    grep -q 'connection_upgrade' "$site" || return 0      # patch_nginx_websocket adds the map first
    local bak="${site}.pre-relay.bak"
    log "Adding nginx location for scan agent console relay…"
    cp -p "$site" "$bak" 2>/dev/null || true
    local proto='$scheme'
    grep -q 'jt_fwd_proto' "$site" && proto='$jt_fwd_proto'
    awk -v proto="$proto" '
      !ins && /location \/api\/ \{/ {
        print "    # jt-ipam-agent-relay: console relay WebSocket dialed OUT by scan agents";
        print "    location ~ \"^/api/v1/scan-agents/relay/[0-9a-f]{32}/ws$\" {";
        print "        proxy_pass http://127.0.0.1:8000;";
        print "        proxy_http_version 1.1;";
        print "        proxy_set_header Host               $host;";
        print "        proxy_set_header X-Real-IP          $remote_addr;";
        print "        proxy_set_header X-Forwarded-For    $proxy_add_x_forwarded_for;";
        print "        proxy_set_header X-Forwarded-Proto  " proto ";";
        print "        proxy_set_header Upgrade            $http_upgrade;";
        print "        proxy_set_header Connection         $connection_upgrade;";
        print "        proxy_read_timeout 3600s;";
        print "        proxy_send_timeout 3600s;";
        print "        proxy_buffering off;";
        print "    }";
        print "";
        ins = 1;
      }
      { print }
    ' "$site" > "${site}.tmp" && mv "${site}.tmp" "$site"
    if apply_nginx_config; then
        log "nginx agent relay location added + reloaded."
    else
        warn "nginx -t failed after adding the agent relay location; restoring previous config."
        cp -p "$bak" "$site" 2>/dev/null || true
    fi
    return 0
}

# -- root guard (used by install/upgrade/uninstall; not by help/usage) --
require_root() {
    if [[ $EUID -ne 0 ]]; then
        echo "[error] must run as root (please use sudo)" >&2
        exit 1
    fi
}

# Repo root (parent of scripts/)
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ── Scheduled reference-data refresh (GeoIP, OUI vendors, Recog fingerprints) ──
# Installed by BOTH install and upgrade. Until 2026-09-29 only hand-configured hosts had
# these timers: customer installs never refreshed OUI or GeoIP at all, and the OUI unit
# on hosts that had one pointed at a script that was never committed.
REFRESH_UNITS=(jt-ipam-geoip-refresh jt-ipam-oui-refresh jt-ipam-recog-refresh)
install_refresh_timers() {
    local u
    for u in "${REFRESH_UNITS[@]}"; do
        install -m 0644 "$REPO_ROOT/deploy/systemd/$u.service" "/etc/systemd/system/$u.service"
        install -m 0644 "$REPO_ROOT/deploy/systemd/$u.timer" "/etc/systemd/system/$u.timer"
    done
    systemctl daemon-reload
    for u in "${REFRESH_UNITS[@]}"; do
        systemctl enable --now "$u.timer" >/dev/null 2>&1 || warn "could not enable $u.timer"
    done
    # A monthly timer that has never fired would leave a new host without MAC vendors for
    # up to a month: fetch once now, in the background (a failure only shows in the journal).
    if [[ "$(systemctl show -p LastTriggerUSecMonotonic --value jt-ipam-oui-refresh.timer 2>/dev/null)" == "0" ]]; then
        systemctl start --no-block jt-ipam-oui-refresh.service 2>/dev/null || true
    fi
}

# ── Scheduled AI review (2026-10-12) ──
# Its own timer instead of running inside jt-ipam-sync: that service is capped at 10 minutes
# (TimeoutStartSec=600), so a long review was killed and started over 5 minutes later, and the
# minutes it ran held up every integration's sync. Installed by BOTH install and upgrade
# (upgrade does not reinstall jt-ipam-sync.service, so this cannot ride along with it).
install_ai_audit_timer() {
    install -m 0644 "$REPO_ROOT/deploy/systemd/jt-ipam-ai-audit.service" /etc/systemd/system/jt-ipam-ai-audit.service
    install -m 0644 "$REPO_ROOT/deploy/systemd/jt-ipam-ai-audit.timer" /etc/systemd/system/jt-ipam-ai-audit.timer
    systemctl daemon-reload
    systemctl enable --now jt-ipam-ai-audit.timer >/dev/null 2>&1 \
        || warn "could not enable jt-ipam-ai-audit.timer (scheduled AI review will not run)"
}

# Recog fingerprint database (optional; the IP probe uses it to tell NAS / firewall /
# printer / exact OS apart). A failed download only warns: the probe works without it,
# and jt-ipam-recog-refresh.timer retries every week. Offline hosts: --recog-zip <file>.
install_recog_db() {  # <backend_dir> <env_file> <user> [zip]
    local backend="$1" env_file="$2" user="$3" zip="${4:-}"
    local args="update" tmpd="" out rc=0
    if [[ -n "$zip" ]]; then
        if [[ ! -r "$zip" ]]; then
            warn "Recog: cannot read $zip -- skipped"
            return 0
        fi
        # The file may live where the service user cannot read it (e.g. /root): hand over a
        # copy with the same name (the release version is read from the file name).
        tmpd="$(mktemp -d)"
        chmod 0755 "$tmpd"
        install -m 0644 "$zip" "$tmpd/$(basename "$zip")"
        args="update --file '$tmpd/$(basename "$zip")'"
    fi
    log "Recog fingerprint database (optional, used by the IP probe)…"
    out="$(cd "$backend" && timeout 300 sudo -u "$user" bash -c \
        "set -a; source '$env_file'; set +a; .venv/bin/python -m app.cli.recog $args" 2>&1)" || rc=$?
    [[ -n "$tmpd" ]] && rm -rf "$tmpd"
    if [[ $rc -eq 0 ]]; then
        log "Recog: ${out//$'\t'/ }"
    else
        warn "Recog fingerprint database was not installed/updated (optional -- the probe still works):"
        warn "  ${out//$'\t'/ }"
        warn "  Retry: sudo -u $user bash -c 'cd $backend; set -a; source $env_file; set +a; .venv/bin/python -m app.cli.recog update'"
        warn "  Offline host: get recog-content-<version>.zip from https://github.com/rapid7/recog/releases and re-run with --recog-zip <file>"
    fi
    return 0
}

usage() {
    cat <<'USAGE'
jt-ipam — deployment tool (single entry point)

Usage:
  jt-ipam.sh <command> [options]

Commands:
  install      fresh install (apt / postgres / redis / venv / alembic / pnpm / systemd / nginx / tls)
                 --tls-mode {nginx|direct|self-signed}   (default nginx)
                 --public-fqdn <fqdn>                     (default ipam.example.com)
                 --bind-port <port>                       (for direct/self-signed, default 8443)
                 --with-freerdp                           also install the FreeRDP console engine
                                                          (freerdp2-x11 xvfb xclip, ~150 MB of X libraries).
                                                          Needed only for RDP targets that reject the default
                                                          engine -- xrdp and GNOME Remote Login do.
                 --guacd-tarball <file>                   install guacd (required: the RDP / VNC console engine,
                                                          prebuilt per OS version, bound to 127.0.0.1 only) from this
                                                          file instead of downloading it -- for offline hosts
                 --recog-zip <file>                       install the Recog fingerprint database (optional, used by
                                                          the IP probe) from this recog-content-<ver>.zip instead of
                                                          downloading it from GitHub -- for offline hosts
  doctor       check a running install and print an exact fix for anything wrong
  harden-audit give the audit table to a NOLOGIN role (install and upgrade do this; run it after a restore)
  sbom         print this install's software bill of materials (CycloneDX 1.5 JSON): the Python packages
               actually installed and the npm packages bundled into the web UI. The same file can be downloaded
               from Admin > Version Info. For vulnerability tools such as Dependency-Track.
                 --output <file>                          write to a file instead of stdout
  upgrade      upgrade existing install (git pull -> backup -> pip -> alembic -> build -> restart)
                 --no-pull                                skip git pull
                 --force                                  discard local changes to tracked files (e.g. an edited
                                                          scripts/jt-ipam.sh) so git pull won't abort
                 --guacd-tarball <file>                   use this guacd package instead of downloading it
                                                          (guacd is required and installed/updated on every upgrade)
                 --recog-zip <file>                       update the Recog fingerprint database from this file
                                                          instead of checking GitHub
  uninstall    stop and remove systemd units/timers + nginx site (keeps data by default)
                 --purge                                  also dropdb + remove config/uploads/system user
                 --yes                                    skip interactive confirmation when using --purge
  help | -h | --help   show this help

Examples:
  sudo jt-ipam.sh install --tls-mode self-signed --public-fqdn ipam.lan
  sudo jt-ipam.sh upgrade --no-pull
  sudo jt-ipam.sh uninstall            # only stop services, keep DB / config / source
  sudo jt-ipam.sh uninstall --purge    # also remove DB / config / user (will ask for confirmation)

Note: uninstall never removes the /opt/jt-ipam source.
USAGE
}

# =============================================================================
# cmd_install — fresh install (original scripts/install-debian.sh logic, preserved verbatim)
# =============================================================================
# OWASP A05 — security headers are a required part of the deployment. The bundled nginx config
# applies them; but if the operator fronts this box with their own edge proxy, that proxy must
# set them too (they don't survive an extra hop). Print a clear, required notice.
security_headers_notice() {
    local mode="${1:-nginx}" fqdn="${2:-your-fqdn}"
    echo
    echo "  === SECURITY HEADERS (required) ============================="
    if [[ "$mode" == "nginx" ]]; then
        echo "   This install's nginx applies HSTS / CSP (frame-src 'self') / X-Frame-Options /"
        echo "   nosniff / Referrer-Policy / Permissions-Policy / COOP / CORP and hides the banner."
    fi
    echo "   ⚠  If you put your OWN reverse proxy / load balancer in FRONT of this box, that edge"
    echo "      proxy MUST set the same security headers — they do NOT survive an extra hop, or the"
    echo "      public site ships with no CSP/HSTS. Apply on that edge box:"
    echo "        deploy/nginx/jt-ipam-external-proxy.conf  (+ jt-ipam-external-proxy-snippet.conf)"
    echo "   Verify through the PUBLIC url users actually hit:"
    echo "     curl -skI https://${fqdn}/ | grep -iE 'strict-transport|content-security|x-frame|cross-origin|^server'"
    echo "     (each header exactly once; Server: nginx, no version)"
    echo "  ============================================================"
    echo
}

# Optional OS packages the running app shells out to. Kept in one place and called from
# BOTH install and upgrade: an existing deployment that upgrades gets the new features, and
# without this it would get them without the binaries that make them work.
# Never fatal -- the app detects what is missing at runtime and disables just that tool.
ensure_runtime_deps() {
    local missing=()
    command -v ping >/dev/null 2>&1 || missing+=("iputils-ping")
    command -v tracepath >/dev/null 2>&1 || missing+=("iputils-tracepath")
    # traceroute: preferred over tracepath for path tracing. tracepath probes with
    # high UDP ports which are commonly filtered late in the path (a trace to
    # 8.8.8.8 went silent after hop 7 and never "reached" it); traceroute -I uses
    # ICMP echo, which almost always gets through. It lives in /usr/sbin.
    { command -v traceroute >/dev/null 2>&1 || [ -x /usr/sbin/traceroute ]; } \
        || missing+=("traceroute")
    # net-snmp client tools (snmpget / snmpbulkwalk) for the LOCAL scan agent's SNMP collection
    # (Integrations -> SNMP, collector "server"). Remote agents get them from their own installer.
    { command -v snmpbulkwalk >/dev/null 2>&1 && command -v snmpget >/dev/null 2>&1; } \
        || missing+=("snmp")
    ensure_report_font
    if [ "${#missing[@]}" -eq 0 ]; then
        log "Runtime dependencies present (ping, tracepath, traceroute, snmp)"
        return 0
    fi
    log "Installing runtime dependencies: ${missing[*]}"
    if apt-get install -y -qq "${missing[@]}" >/dev/null 2>&1; then
        log "Installed ${missing[*]}"
    else
        warn "could not install ${missing[*]} — the connectivity diagnostics in Tools will show those tools as unavailable (and without 'snmp' the local scan agent cannot do SNMP collection)"
    fi
}

# CJK font for PDF reports (the backend embeds a subset into each PDF, so Chinese and
# Japanese show up in any viewer). Noto Sans CJK has proper Traditional Chinese and Japanese
# glyph forms plus a bold face, which the smaller WenQuanYi fonts lack (~60 MB download,
# ~90 MB installed). Never fatal: without it the PDF export returns report_pdf_no_font with
# the command to run, and DOCX/ODT exports keep working.
ensure_report_font() {
    if find /usr/share/fonts /usr/local/share/fonts -name 'NotoSansCJK*' -print -quit 2>/dev/null | grep -q .; then
        log "CJK font for PDF reports present (Noto Sans CJK)"
        return 0
    fi
    log "Installing the CJK font for PDF reports (fonts-noto-cjk, ~60 MB download)…"
    if DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends fonts-noto-cjk >/dev/null 2>&1 \
        || { apt-get update -qq >/dev/null 2>&1 \
             && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends fonts-noto-cjk >/dev/null 2>&1; }; then
        log "Installed fonts-noto-cjk"
    else
        warn "could not install fonts-noto-cjk — PDF reports will fall back to another CJK font if one is installed, otherwise PDF export reports that a font is missing (DOCX/ODT still work). Install later with: sudo apt install fonts-noto-cjk"
    fi
}

# Let the backend send ICMP echo.
#
# The backend runs as a systemd service with NoNewPrivileges=true, which makes the
# cap_net_raw file capability on /bin/ping inert: ping works in a shell but sends
# nothing from the service. Without this, the Ping tool reports "no reply" for every
# target -- indistinguishable from "the target is down", which is worse than an error.
#
# We widen net.ipv4.ping_group_range rather than granting the service CAP_NET_RAW:
# it permits ICMP echo datagram sockets only -- no crafted packets, no sniffing --
# which is a far narrower grant than raw-socket capability for the whole backend.
# Many distributions already ship it open for exactly this reason.
#
# Set JT_IPAM_SKIP_PING_SYSCTL=1 to skip (the Tools page then explains how to do it
# by hand). Undo with: rm /etc/sysctl.d/99-jt-ipam-ping.conf && sysctl --system
ensure_icmp_capability() {
    local conf="/etc/sysctl.d/99-jt-ipam-ping.conf"
    if [ "${JT_IPAM_SKIP_PING_SYSCTL:-0}" = "1" ]; then
        log "Skipping ICMP sysctl (JT_IPAM_SKIP_PING_SYSCTL=1) -- the Ping tool will report 'cannot send'"
        return 0
    fi
    local cur
    cur="$(sysctl -n net.ipv4.ping_group_range 2>/dev/null || echo "")"
    # Already open for all groups? Leave the system alone.
    if [ "${cur//[[:space:]]/ }" = "0 2147483647" ]; then
        log "ICMP already permitted for unprivileged sockets"
        return 0
    fi
    if [ ! -w /etc/sysctl.d ] 2>/dev/null; then
        warn "cannot write ${conf} -- the Ping tool will report 'cannot send'; see Tools -> Connectivity for the manual fix"
        return 0
    fi
    printf '# jt-ipam: allow unprivileged ICMP echo so the Ping tool can send packets.\n# Remove this file and run `sysctl --system` to undo.\nnet.ipv4.ping_group_range = 0 2147483647\n' > "$conf"
    sysctl -q -p "$conf" >/dev/null 2>&1 || true

    # Verify by reading the value back. Writing the file is not the same as it taking
    # effect: inside an unprivileged LXC container this sysctl is read-only, and `sysctl -p`
    # fails with "Invalid argument". Leaving the file there would be worse than useless --
    # it looks configured while ping stays broken forever, including after a reboot.
    cur="$(sysctl -n net.ipv4.ping_group_range 2>/dev/null || echo "")"
    if [ "${cur//[[:space:]]/ }" = "0 2147483647" ]; then
        log "Enabled unprivileged ICMP echo (${conf})"
        return 0
    fi

    rm -f "$conf"
    log "net.ipv4.ping_group_range cannot be changed on this host$(         [ "$(systemd-detect-virt 2>/dev/null)" = "lxc" ] && printf ' (normal inside an LXC container: the kernel belongs to the host)')"
    grant_net_raw_capability
}

# Fallback when the sysctl route is unavailable: give the backend service CAP_NET_RAW.
#
# Verified inside an unprivileged LXC container: the container's capability bounding set is
# full, so this works without touching the Proxmox host. The service runs as the non-root
# `jtipam` user, which is why it needs the grant at all.
#
# AmbientCapabilities is applied by systemd itself, so it works despite NoNewPrivileges=yes
# (that setting only disables *file* capabilities, i.e. the setcap route). CapabilityBoundingSet
# is pinned to the same single capability, which is narrower than the service's default.
#
# Set JT_IPAM_NO_NET_RAW=1 to decline: everything except the Ping tool works without it
# (TCP / UDP / TLS / HTTP checks never needed privileges).
grant_net_raw_capability() {
    local dir="/etc/systemd/system/jt-ipam-backend.service.d"
    local conf="${dir}/10-netraw.conf"
    if [ "${JT_IPAM_NO_NET_RAW:-0}" = "1" ]; then
        rm -f "$conf"
        warn "JT_IPAM_NO_NET_RAW=1 -- the Ping tool will report 'cannot send packets'"
        return 0
    fi
    if ! command -v systemctl >/dev/null 2>&1; then
        warn "no systemd here -- the Ping tool will report 'cannot send packets'"
        return 0
    fi
    mkdir -p "$dir"
    cat > "$conf" <<'NETRAW'
# jt-ipam: the Ping tool needs to open an ICMP socket. The service runs as a non-root user,
# and on this host net.ipv4.ping_group_range could not be widened (typical inside LXC, where
# the kernel belongs to the host).
#
# AmbientCapabilities is granted by systemd directly, so it survives NoNewPrivileges=yes.
# The bounding set is pinned to the same single capability.
#
# To undo: delete this file, then `systemctl daemon-reload && systemctl restart jt-ipam-backend`.
[Service]
AmbientCapabilities=CAP_NET_RAW
CapabilityBoundingSet=CAP_NET_RAW
NETRAW
    systemctl daemon-reload >/dev/null 2>&1 || true
    log "Granted CAP_NET_RAW to jt-ipam-backend so the Ping tool can send (${conf})"
}

# Read back what the *running* service actually got. Writing a unit file is not the same as
# it taking effect -- exactly the trap the sysctl route fell into, where a file was written,
# the value never applied, and ping stayed broken while looking configured.
verify_icmp_ready() {
    command -v systemctl >/dev/null 2>&1 || return 0
    local cur
    cur="$(sysctl -n net.ipv4.ping_group_range 2>/dev/null || echo "")"
    if [ "${cur//[[:space:]]/ }" = "0 2147483647" ]; then
        log "Ping tool: ready (unprivileged ICMP allowed by sysctl)"
        return 0
    fi
    local pid amb
    pid="$(systemctl show jt-ipam-backend -p MainPID --value 2>/dev/null || echo 0)"
    amb="$(awk '/^CapAmb:/{print $2}' "/proc/${pid}/status" 2>/dev/null || echo "")"
    # CAP_NET_RAW is bit 13
    if [ -n "$amb" ] && [ "$(( 0x${amb} >> 13 & 1 ))" = "1" ]; then
        log "Ping tool: ready (backend holds CAP_NET_RAW)"
        return 0
    fi
    warn "Ping tool: NOT available on this host -- it will report 'cannot send packets'."
    warn "Everything else (TCP / UDP / TLS / HTTP checks) is unaffected; those never needed privileges."
}

# ── Audit table ownership (2026-10-09) ──────────────────────────────────────────────
# The append-only triggers on audit_logs (migrations 0149/0201) can be disabled by the
# table owner, and the app's database role used to own it. harden-audit gives the table to
# a NOLOGIN role so the app role keeps only SELECT/INSERT; unharden hands it back for the
# duration of a migration run (migrations run as the app role). Local PostgreSQL only:
# with an external database the DBA runs scripts/sql/audit-harden.sql once.
audit_db_setting() {
    local key="$1" def="$2" v=""
    [[ -f "${ENV_FILE:-/etc/jt-ipam/backend.env}" ]] &&         v="$(grep -oP "^${key}=\K\S+" "${ENV_FILE:-/etc/jt-ipam/backend.env}" 2>/dev/null | tail -1)"
    echo "${v:-$def}"
}

audit_db_is_local() {
    local host; host="$(audit_db_setting POSTGRES_HOST 127.0.0.1)"
    [[ "$host" == "127.0.0.1" || "$host" == "localhost" || "$host" == "::1" || "$host" == /* ]] || return 1
    command -v psql >/dev/null 2>&1 && id -u postgres >/dev/null 2>&1
}

audit_table_sql() {
    local file="$1" db app root
    root="${ROOT:-$REPO_ROOT}"
    db="$(audit_db_setting POSTGRES_DB jt_ipam)"
    app="$(audit_db_setting POSTGRES_USER jt_ipam)"
    if ! audit_db_is_local; then
        warn "Database is not local; ask the DBA to run $root/scripts/sql/$file as a superuser (-v app=$app -d $db)."
        return 0
    fi
    if ! sudo -u postgres psql -d "$db" -tAc "SELECT 1 FROM pg_tables WHERE tablename='audit_logs'" 2>/dev/null | grep -q 1; then
        return 0          # fresh install before migrations, or no database yet
    fi
    sudo -u postgres psql -q -v ON_ERROR_STOP=1 -v app="$app" -d "$db" -f "$root/scripts/sql/$file" >/dev/null
}

harden_audit_table() {
    if audit_table_sql audit-harden.sql; then
        log "Audit table owned by jt_ipam_audit_owner (the app role can only read and append)."
    else
        warn "Could not hand the audit table to jt_ipam_audit_owner; the app role can still disable its triggers. Re-run: sudo bash $0 harden-audit"
    fi
}

unharden_audit_table() {
    audit_table_sql audit-unharden.sql || warn "Could not hand the audit table back for migrations; a migration that changes it may fail."
}

# Software bill of materials of what is installed here (backend/app/services/sbom.py). Read-only, no root needed.
# JT_IPAM_PYTHON is a test hook (CI has no .venv).
cmd_sbom() {
    local out=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --output) out="${2:-}"; shift 2 || { echo "[error] --output needs a file" >&2; exit 2; } ;;
            *) echo "[error] Unknown option for sbom: $1" >&2; exit 2 ;;
        esac
    done
    local py="${JT_IPAM_PYTHON:-$REPO_ROOT/backend/.venv/bin/python}"
    if ! command -v "$py" >/dev/null 2>&1 && [[ ! -x "$py" ]]; then
        echo "[error] $py not found: is jt-ipam installed in $REPO_ROOT?" >&2
        exit 1
    fi
    if [[ -n "$out" ]]; then
        ( cd "$REPO_ROOT/backend" && "$py" -m app.services.sbom "$REPO_ROOT/frontend" ) > "$out"
        echo "SBOM written to $out" >&2
    else
        ( cd "$REPO_ROOT/backend" && "$py" -m app.services.sbom "$REPO_ROOT/frontend" )
    fi
}

cmd_harden_audit() {
    require_root
    local ENV_FILE="${ENV_FILE:-/etc/jt-ipam/backend.env}"
    harden_audit_table
}


cmd_install() {
    # -- default parameters --
    local TLS_MODE="nginx"
    local PUBLIC_FQDN="ipam.example.com"
    local BIND_PORT_DIRECT=8443
    local WITH_FREERDP=0
    local GUACD_TARBALL=""      # guacd 是必要元件，一定會裝（GitHub issue #42）
    local RECOG_ZIP=""          # Recog 指紋庫（選用）：離線主機用手上的發佈檔

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --tls-mode) TLS_MODE="$2"; shift 2 ;;
            --recog-zip) RECOG_ZIP="$(readlink -f "$2")"; shift 2 ;;
            --with-freerdp) WITH_FREERDP=1; shift ;;
            --with-guacd) shift ;;                     # 舊參數，保留相容（guacd 一定會裝）
            --guacd-tarball) GUACD_TARBALL="$(readlink -f "$2")"; shift 2 ;;
            --public-fqdn) PUBLIC_FQDN="$2"; shift 2 ;;
            --bind-port) BIND_PORT_DIRECT="$2"; shift 2 ;;
            -h|--help) usage; exit 0 ;;
            *) echo "Unknown arg: $1" >&2; exit 2 ;;
        esac
    done

    case "$TLS_MODE" in
        nginx|direct|self-signed) ;;
        *) echo "[error] --tls-mode must be one of: nginx | direct | self-signed (got: $TLS_MODE)" >&2; exit 2 ;;
    esac

    # -- required checks --
    require_root

    if ! command -v lsb_release >/dev/null 2>&1; then
        apt-get update -qq
        apt-get install -y -qq lsb-release
    fi

    local DISTRO
    DISTRO=$(lsb_release -si)
    if [[ "$DISTRO" != "Debian" && "$DISTRO" != "Ubuntu" ]]; then
        echo "[warn] this script targets Debian/Ubuntu; install manually on other distros" >&2
    fi

    local ETC_DIR="/etc/jt-ipam"
    local TLS_DIR="$ETC_DIR/tls"
    local BACKEND_DIR="${REPO_ROOT}/backend"
    local FRONTEND_DIR="${REPO_ROOT}/frontend"
    local JTIPAM_USER="jtipam"
    local JTIPAM_GROUP="jtipam"

    # -- 1. apt packages --
    log "Installing apt packages…"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq

    # Minimal images (a clean Debian 12 or an Ubuntu LXC) are missing these basics:
    #  - curl / gpg / ca-certificates: the "add the PGDG repo" step (curl | gpg) runs
    #    BEFORE the main package list is installed. Debian 12 has no PG16 by default,
    #    so PGDG is unavoidable and this step fails outright without them.
    #  - sudo: all the PostgreSQL setup below goes through `sudo -u postgres psql ...`,
    #    and minimal Debian containers frequently have no sudo -> `sudo: command not
    #    found`. This is usually the second wall customers hit after installing PG.
    apt-get install -y -qq ca-certificates curl gnupg sudo

    ensure_runtime_deps
    ensure_icmp_capability

    # Is a package installable? Use command substitution -- NOT `apt-cache madison X
    # | grep -q .`. Under `set -o pipefail`, madison prints several lines for packages
    # with multiple candidates (Debian 13's postgresql-17 has both the 17.10 security
    # update and 17.9). `grep -q` closes the pipe on the first match, apt-cache gets
    # SIGPIPE (141) writing the second line, and pipefail then calls the whole pipeline
    # a failure -- so a package that plainly exists is treated as missing. That is the
    # real reason a customer's Debian 13 never picked native PG17, detoured through
    # PGDG and died. Distros with a single candidate print one line and never SIGPIPE,
    # which is why it went unnoticed for so long. Command substitution reads stdout to
    # completion: no pipe, no SIGPIPE.
    _pkg_installable() { [ -n "$(apt-cache madison "$1" 2>/dev/null)" ]; }

    # Detect available Python (newest to oldest, needs >= 3.11).
    # madison only reports what is genuinely installable (apt-cache show also matches
    # Provides, which is not reliable here).
    local PYTHON_BIN=""
    local PYTHON_PKGS=()
    local ver
    for ver in python3.14 python3.13 python3.12 python3.11; do
        if _pkg_installable "${ver}-venv"; then
            PYTHON_BIN="$ver"
            PYTHON_PKGS=("$ver" "${ver}-venv" "${ver}-dev")
            break
        fi
    done
    if [[ -z "$PYTHON_BIN" ]] && command -v python3 >/dev/null && \
            python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
        PYTHON_BIN="python3"
        PYTHON_PKGS=(python3 python3-venv python3-dev)
    fi
    if [[ -z "$PYTHON_BIN" ]]; then
        echo "[error] need Python >= 3.11; on Ubuntu 22.04 switch to 24.04, or enable the deadsnakes PPA:" >&2
        echo "        sudo add-apt-repository -y ppa:deadsnakes/ppa && sudo apt-get update" >&2
        exit 1
    fi
    log "Using $PYTHON_BIN for backend venv"

    # PostgreSQL: never hard-code the version. Prefer a postgresql-NN (>=16) that the
    # distro already carries in its default repos.
    # Why not always 16: Ubuntu 26.04 ships PG 17/18 and has no postgresql-16, so the
    # old code went and added PGDG's 16 -- but PGDG often takes months to publish for a
    # freshly released Ubuntu codename, so `apt-get update` 404s and the install dies.
    # That is the reported "ubuntu26 won't install". The app works on 16/17/18, so use
    # whatever the distro provides and fall back to PGDG 16 only if there is nothing.
    # pgvector is then installed for that same major.
    _add_pgdg_repo() {
        # Put the keyring in /etc/apt/keyrings under our own filename. Do NOT reuse
        # /usr/share/postgresql-common/pgdg/apt.postgresql.org.gpg: that file belongs to
        # postgresql-common, and when it already exists `gpg --dearmor` prompts "File
        # exists. Overwrite?" on /dev/tty. Non-interactively that is an immediate
        # "dearmoring failed" -> no key written -> PGDG signatures invalid -> pgvector
        # unavailable. A customer's Debian 12 stalled exactly here. `--yes` keeps the
        # overwrite unattended and the whole script re-runnable.
        local keyring=/etc/apt/keyrings/jt-ipam-pgdg.gpg
        install -d /etc/apt/keyrings
        curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
            | gpg --dearmor --yes -o "$keyring"
        echo "deb [signed-by=$keyring] https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
            > /etc/apt/sources.list.d/pgdg.list
        apt-get update -qq
    }
    # The rule: only pick a PG major where BOTH the server package AND the matching
    # postgresql-N-pgvector are installable. Checking the server alone and falling back
    # to 16 is what broke a customer's Debian 13 (trixie): native was skipped, PGDG 16
    # was chosen, but PGDG/trixie currently ships pgvector for 17/18 only -- no
    # postgresql-16-pgvector -- so the install went FATAL. Now: look for a
    # server+pgvector pair in the default repos first (16 -> 17 -> 18; the app supports
    # all three), and only add PGDG and look again if none is found (PGDG/trixie
    # provides 17 with pgvector).
    # This host may ALREADY run PostgreSQL for something else (a customer hit this with
    # SonarQube's cluster). We connect to 127.0.0.1:5432, i.e. THAT cluster — so pgvector
    # has to be installed for ITS major version. Installing postgresql-16-pgvector next to
    # a running 18 cluster leaves `CREATE EXTENSION vector` failing with
    # 'extension "vector" is not available', which is impossible to read as a version mismatch.
    _running_pg_major() {
        command -v psql >/dev/null 2>&1 || return 1
        id -u postgres >/dev/null 2>&1 || return 1
        local n
        n="$(sudo -u postgres psql -tAc 'SHOW server_version_num' 2>/dev/null | tr -dc '0-9')"
        [[ -n "$n" ]] || return 1
        echo $(( n / 10000 ))
    }

    _pick_pg() {   # echo the first major where server AND pgvector are both installable
        local v     # (via _pkg_installable, which avoids the grep -q SIGPIPE trap above)
        # A cluster is already running -> we must use ITS major; picking another one
        # installs pgvector where the database isn't.
        local running; running="$(_running_pg_major || true)"
        if [[ -n "$running" ]]; then
            if [[ "$running" -lt 16 ]]; then
                die "This host already runs PostgreSQL $running on 127.0.0.1:5432, but jt-ipam needs >= 16.\n  Upgrade that cluster, or point jt-ipam at another one (POSTGRES_HOST / POSTGRES_PORT in /etc/jt-ipam/backend.env) and re-run install."
            fi
            if ! _pkg_installable "postgresql-$running-pgvector"; then
                die "This host already runs PostgreSQL $running, but 'postgresql-$running-pgvector' is not installable from the configured repos.\n  jt-ipam connects to that cluster, so pgvector must exist for ITS version.\n  Add the PGDG repo (or install the package manually), then re-run install."
            fi
            echo "$running"; return 0
        fi
        for v in 16 17 18; do
            _pkg_installable "postgresql-$v"          || continue
            _pkg_installable "postgresql-$v-pgvector" || continue
            echo "$v"; return 0
        done
        return 1
    }
    local PG_VER
    PG_VER="$(_pick_pg || true)"
    if [[ -z "$PG_VER" ]]; then
        # No pair in the default repos: refresh the index and try once more. This covers
        # a stale or partially-failed apt index at install time -- a customer's Debian 13
        # had postgresql-17 and postgresql-17-pgvector available yet neither was picked,
        # so it detoured through PGDG down to 16. After a refresh, native usually works.
        warn "no PostgreSQL (>=16) with matching pgvector yet; refreshing apt index and retrying…"
        apt-get update -qq || true
        PG_VER="$(_pick_pg || true)"
    fi
    if [[ -z "$PG_VER" ]]; then
        warn "still none in default repos; adding PGDG…"
        _add_pgdg_repo || die "apt-get update failed after adding the PGDG repo for codename '$(lsb_release -cs)'. PGDG may not carry this release yet — install PostgreSQL >= 16 + matching pgvector manually, then re-run install."
        PG_VER="$(_pick_pg || true)"
        [[ -n "$PG_VER" ]] || die "no PostgreSQL 16/17/18 with a matching postgresql-N-pgvector is installable, even after adding PGDG (codename '$(lsb_release -cs)'). Install PostgreSQL + pgvector manually, then re-run install."
    fi
    log "Using PostgreSQL $PG_VER (with pgvector)"

    local PG_PKGS=("postgresql-$PG_VER" "postgresql-contrib-$PG_VER" "postgresql-$PG_VER-pgvector")
    if [[ -n "$(_running_pg_major || true)" ]]; then
        # A cluster is already running: add pgvector only. Pulling the server package
        # again would create a SECOND cluster on another port.
        log "PostgreSQL $PG_VER is already running here; only adding pgvector for it."
        PG_PKGS=("postgresql-$PG_VER-pgvector")
    fi

    local PKGS=(
        "${PG_PKGS[@]}"
        redis-server
        "${PYTHON_PKGS[@]}"
        build-essential libpq-dev pkg-config
        curl ca-certificates gnupg openssl
        ipmitool freeipmi-tools
    )

    # Node.js is handled by ensure_node() right before the frontend build — distro 'nodejs'
    # is too old on most supported releases, so we install NodeSource 22 / reuse a node >= 22 instead.
    # only install nginx in nginx mode
    if [[ "$TLS_MODE" == "nginx" ]]; then
        PKGS+=(nginx)
    fi

    apt-get install -y "${PKGS[@]}"


    # -- 2. system user --
    if ! id -u "$JTIPAM_USER" >/dev/null 2>&1; then
        log "Creating system user $JTIPAM_USER…"
        useradd --system --home-dir /var/lib/jt-ipam --shell /usr/sbin/nologin "$JTIPAM_USER"
    fi

    install -d -o "$JTIPAM_USER" -g "$JTIPAM_GROUP" -m 0750 \
        /var/lib/jt-ipam /var/log/jt-ipam \
        /var/lib/jt-ipam/uploads /var/lib/jt-ipam/uploads/floorplans
    install -d -m 0755 "$ETC_DIR"
    # The backup directory has to exist first: jt-ipam-backup.service uses
    # ProtectSystem=strict + ReadWritePaths, and when a listed path is missing systemd
    # fails while setting up the mount namespace (226/NAMESPACE). The message reads like
    # "can't find /usr/local/bin/jt-ipam-backup.sh", pointing at entirely the wrong
    # thing -- a customer lost time to exactly this.
    ensure_unit_dirs

    # Make jtipam own the whole project directory (including .git): so venv / node_modules / dist are writable,
    # and so later upgrades running git pull as jtipam don't fail because .git is owned by root (especially when bootstrap clones as root).
    chown -R "$JTIPAM_USER:$JTIPAM_GROUP" "$REPO_ROOT"

    # -- 3. PostgreSQL --
    log "Configuring PostgreSQL…"
    systemctl enable --now postgresql

    # Enable SCRAM-SHA-256
    local PG_HBA PG_CONF
    PG_HBA="$(sudo -u postgres psql -tAc 'SHOW hba_file;')"
    PG_CONF="$(sudo -u postgres psql -tAc 'SHOW config_file;')"
    if ! grep -q "^password_encryption" "$PG_CONF"; then
        echo "password_encryption = scram-sha-256" >> "$PG_CONF"
    fi

    # Create role + DB (if they don't exist)
    local DB_PASSWORD=""
    if [[ -f "$ETC_DIR/.db-password" ]]; then
        DB_PASSWORD="$(cat "$ETC_DIR/.db-password")"
    else
        DB_PASSWORD="$(openssl rand -base64 32 | tr -d '=+/')"
        install -m 0600 -o root -g root /dev/null "$ETC_DIR/.db-password"
        echo -n "$DB_PASSWORD" > "$ETC_DIR/.db-password"
    fi

    if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='jt_ipam'" | grep -q 1; then
        sudo -u postgres psql -c "CREATE ROLE jt_ipam LOGIN PASSWORD '${DB_PASSWORD}';"
    fi
    if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='jt_ipam'" | grep -q 1; then
        sudo -u postgres createdb -O jt_ipam jt_ipam
    fi

    # Enable required extensions
    # ON_ERROR_STOP: without it psql prints the error and still exits 0, so a missing
    # pgvector surfaced 100 lines later as an alembic traceback instead of here.
    sudo -u postgres psql -v ON_ERROR_STOP=1 -d jt_ipam <<'SQL' || die "Failed to create the required PostgreSQL extensions (see the error above).\n  Most often 'vector' is missing for the running cluster: install postgresql-<major>-pgvector matching\n  \`sudo -u postgres psql -tAc 'SHOW server_version_num'\`, then re-run install."
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS btree_gist;
-- pgvector: alembic migration 0009 also does IF NOT EXISTS once, but it needs superuser,
-- so create it here as postgres first; alembic's run later is then a no-op
CREATE EXTENSION IF NOT EXISTS vector;
SQL

    systemctl reload postgresql || systemctl restart postgresql

    # -- 4. Redis --
    log "Configuring Redis…"
    local REDIS_PASSWORD=""
    if [[ -f "$ETC_DIR/.redis-password" ]]; then
        REDIS_PASSWORD="$(cat "$ETC_DIR/.redis-password")"
    else
        REDIS_PASSWORD="$(openssl rand -base64 32 | tr -d '=+/')"
        install -m 0600 -o root -g root /dev/null "$ETC_DIR/.redis-password"
        echo -n "$REDIS_PASSWORD" > "$ETC_DIR/.redis-password"
    fi

    # Set requirepass + bind 127.0.0.1
    sed -i \
        -e "s/^# *requirepass .*/requirepass ${REDIS_PASSWORD}/" \
        -e "s/^requirepass .*/requirepass ${REDIS_PASSWORD}/" \
        -e "s/^bind .*/bind 127.0.0.1 ::1/" \
        /etc/redis/redis.conf

    if ! grep -q "^requirepass" /etc/redis/redis.conf; then
        echo "requirepass ${REDIS_PASSWORD}" >> /etc/redis/redis.conf
    fi

    systemctl enable --now redis-server
    systemctl restart redis-server

    # -- 5. backend venv --
    log "Setting up backend venv…"
    cd "$BACKEND_DIR"
    sudo -u "$JTIPAM_USER" "$PYTHON_BIN" -m venv .venv
    # pip 至少 26.2.2：舊版 pip 內嵌的 certifi 仍信任 2026-10 金鑰外洩的 eMudhra/emSign 中間憑證，從 PyPI 下載會被冒充
    sudo -u "$JTIPAM_USER" .venv/bin/pip install --upgrade "pip>=26.2.2" wheel
    # prod installs runtime deps only (matching upgrade); for dev/test tools run pip install -e ".[dev]" separately
    sudo -u "$JTIPAM_USER" .venv/bin/pip install -e .
    install_rdp_optional
    ensure_kerberos_support
    if [[ "$WITH_FREERDP" == "1" ]]; then
        install_freerdp_apt || true
    else
        log "FreeRDP console engine not installed (use --with-freerdp, or install later:"
        log "  sudo apt-get install -y ${FREERDP_APT_PACKAGES[*]} )"
    fi
    # guacd 是必要元件：裝不起來就停在這裡（還沒有任何服務對外），講清楚怎麼補
    if ! install_guacd "$GUACD_TARBALL"; then
        die "guacd is required (the RDP / VNC console engine) and could not be installed -- see above.
  Offline host, or an OS version without a prebuilt package yet: build or download it for this OS
  (scripts/guacd/, GitHub releases) and re-run: sudo $0 install --guacd-tarball <file> [other options]"
    fi

    # -- 6. backend.env --
    log "Generating /etc/jt-ipam/backend.env…"
    local ENV_FILE="$ETC_DIR/backend.env"
    if [[ ! -f "$ENV_FILE" ]]; then
        local SECRET_KEY ENCRYPTION_KEY AUDIT_CHAIN_GENESIS BACKEND_TLS_BLOCK PUBLIC_URL
        SECRET_KEY="$(openssl rand -hex 64)"
        ENCRYPTION_KEY="$(openssl rand -base64 32)"
        AUDIT_CHAIN_GENESIS="$(openssl rand -hex 64)"

        # -- TLS configuration block --
        case "$TLS_MODE" in
            nginx)
                BACKEND_TLS_BLOCK="BACKEND_TLS_MODE=nginx
BACKEND_BIND_HOST=127.0.0.1
BACKEND_BIND_PORT=8000"
                ;;
            direct|self-signed)
                BACKEND_TLS_BLOCK="BACKEND_TLS_MODE=direct
BACKEND_BIND_HOST=0.0.0.0
BACKEND_BIND_PORT=${BIND_PORT_DIRECT}
BACKEND_TLS_CERT_FILE=${TLS_DIR}/server.crt
BACKEND_TLS_KEY_FILE=${TLS_DIR}/server.key"
                ;;
        esac

        # Derive the public URL
        if [[ "$TLS_MODE" == "nginx" ]]; then
            PUBLIC_URL="https://${PUBLIC_FQDN}"
        else
            # direct / self-signed: public = backend host:port
            PUBLIC_URL="https://${PUBLIC_FQDN}:${BIND_PORT_DIRECT}"
        fi

        cat > "$ENV_FILE" <<EOF
# Auto-generated — $(date -Iseconds) (TLS mode: ${TLS_MODE})
APP_ENV=production
APP_DEBUG=false
APP_LOG_LEVEL=INFO
APP_TIMEZONE=Asia/Taipei

APP_PUBLIC_URL=${PUBLIC_URL}
API_PUBLIC_URL=${PUBLIC_URL}
CORS_ORIGINS=${PUBLIC_URL}

SECRET_KEY=${SECRET_KEY}
ENCRYPTION_KEY=${ENCRYPTION_KEY}
AUDIT_CHAIN_GENESIS=${AUDIT_CHAIN_GENESIS}

ARGON2_TIME_COST=3
ARGON2_MEMORY_COST_KIB=65536
ARGON2_PARALLELISM=4

ACCESS_TOKEN_EXPIRE_MINUTES=15
REFRESH_TOKEN_EXPIRE_DAYS=14
SESSION_COOKIE_SECURE=true
SESSION_COOKIE_SAMESITE=lax

# -- TLS (SSL enforced; A02) --
${BACKEND_TLS_BLOCK}

POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
POSTGRES_DB=jt_ipam
POSTGRES_USER=jt_ipam
POSTGRES_PASSWORD=${DB_PASSWORD}

REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_PASSWORD=${REDIS_PASSWORD}
REDIS_DB=0

RATE_LIMIT_DEFAULT=100/minute
RATE_LIMIT_AUTH=10/minute
RATE_LIMIT_API_TOKEN=600/minute

OUTBOUND_ALLOW_PRIVATE=true

VITE_DEFAULT_LOCALE=zh-TW
VITE_DEFAULT_THEME=auto
EOF
        chown root:"$JTIPAM_GROUP" "$ENV_FILE"
        chmod 0640 "$ENV_FILE"
        log "Wrote $ENV_FILE (secrets generated; review APP_PUBLIC_URL etc.)"
    else
        warn "$ENV_FILE already exists; skipping (review manually)"
    fi

    # -- 7. alembic migrate --
    log "Running alembic migrations…"
    cd "$BACKEND_DIR"
    sudo -u "$JTIPAM_USER" --preserve-env=PATH \
        bash -c "set -a; source $ENV_FILE; set +a; .venv/bin/alembic upgrade head"
    harden_audit_table

    # -- 7b. first admin (only if none yet): generate a random password and show it once --
    ADMIN_PW_RECORD="$ETC_DIR/.admin-initial-password"
    INITIAL_ADMIN_PW=""
    if [[ ! -f "$ADMIN_PW_RECORD" ]]; then
        local _gen_pw _tmp_pw
        # `head -c 20` closes the pipe early and SIGPIPEs the upstream tr, which with
        # pipefail + set -e would abort the install. The 20 characters are already
        # captured, so `|| true` swallows only the exit status, not the password.
        _gen_pw="$(openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | head -c 20 || true)"
        _tmp_pw="$(mktemp)"; chmod 600 "$_tmp_pw"; printf '%s' "$_gen_pw" > "$_tmp_pw"; chown "$JTIPAM_USER" "$_tmp_pw"
        # create-admin errors (non-zero) if an admin already exists → then we just skip silently
        if sudo -u "$JTIPAM_USER" --preserve-env=PATH bash -c \
            "set -a; source $ENV_FILE; set +a; .venv/bin/python -m app.cli.bootstrap create-admin --username admin --email admin@localhost --password-stdin < '$_tmp_pw'" >/dev/null 2>&1; then
            install -m 0600 -o root -g root /dev/null "$ADMIN_PW_RECORD"
            printf '%s' "$_gen_pw" > "$ADMIN_PW_RECORD"
            INITIAL_ADMIN_PW="$_gen_pw"
        fi
        rm -f "$_tmp_pw"
    fi

    # -- 8. frontend build (as root with a clean toolchain, then chown back) --
    log "Building frontend…"
    build_frontend "$FRONTEND_DIR" "$JTIPAM_USER:$JTIPAM_GROUP"

    # -- 9. TLS certificate --
    # Unified cert paths: /etc/jt-ipam/tls/server.{crt,key}
    # - self-signed mode: force regeneration
    # - nginx mode: auto-generate a self-signed cert if missing (gets the site up first; cp the real cert later and reload)
    # - direct mode: generate when cert is missing (so the backend can start)
    if [[ "$TLS_MODE" == "self-signed" ]]; then
        log "Generating self-signed TLS certificate…"
        "$REPO_ROOT/scripts/generate-self-signed-cert.sh" \
            --out-dir "$TLS_DIR" \
            --cn "$PUBLIC_FQDN" \
            --san "DNS:${PUBLIC_FQDN}" \
            --owner "root:${JTIPAM_GROUP}" \
            --force
    elif [[ "$TLS_MODE" == "nginx" || "$TLS_MODE" == "direct" ]]; then
        if [[ ! -f "$TLS_DIR/server.crt" || ! -f "$TLS_DIR/server.key" ]]; then
            log "Generating bootstrap self-signed cert (just cp your real cert over it at $TLS_DIR/server.{crt,key})…"
            "$REPO_ROOT/scripts/generate-self-signed-cert.sh" \
                --out-dir "$TLS_DIR" \
                --cn "$PUBLIC_FQDN" \
                --san "DNS:${PUBLIC_FQDN}" \
                --owner "root:${JTIPAM_GROUP}"
        else
            log "Existing TLS cert in $TLS_DIR — keeping it"
        fi
    fi

    # -- 10. systemd --
    log "Installing systemd units…"
    install -m 0644 "$REPO_ROOT/deploy/systemd/jt-ipam-backend.service" \
        /etc/systemd/system/jt-ipam-backend.service
    ensure_backend_syscall_dropin
    install -m 0644 "$REPO_ROOT/deploy/systemd/jt-ipam-sync.service" \
        /etc/systemd/system/jt-ipam-sync.service
    install -m 0644 "$REPO_ROOT/deploy/systemd/jt-ipam-sync.timer" \
        /etc/systemd/system/jt-ipam-sync.timer
    install -m 0644 "$REPO_ROOT/deploy/systemd/jt-ipam-backup.service" \
        /etc/systemd/system/jt-ipam-backup.service
    install -m 0644 "$REPO_ROOT/deploy/systemd/jt-ipam-backup.timer" \
        /etc/systemd/system/jt-ipam-backup.timer
    install -m 0755 "$REPO_ROOT/scripts/jt-ipam-backup.sh" \
        /usr/local/bin/jt-ipam-backup.sh
    systemctl daemon-reload
    systemctl enable --now jt-ipam-backend
    verify_icmp_ready
    # Periodically sync OPNsense / Wazuh / LibreNMS (per each instance's own sync_interval_seconds)
    systemctl enable --now jt-ipam-sync.timer
    # Scheduled AI review (does nothing until it is turned on under Admin → LLM / AI)
    install_ai_audit_timer
    # Daily backup at 03:30; keep 14 days under /var/backups/jt-ipam/
    systemctl enable --now jt-ipam-backup.timer
    # GeoIP / OUI / Recog refresh timers, then the Recog database itself (optional)
    install_refresh_timers
    install_recog_db "$BACKEND_DIR" "$ENV_FILE" "$JTIPAM_USER" "$RECOG_ZIP"

    # -- 11. nginx site (nginx mode only) --
    if [[ "$TLS_MODE" == "nginx" ]]; then
        log "Installing nginx site (mode: nginx terminates TLS)…"
        install -d -m 0755 /etc/nginx/snippets
        install -m 0644 "$REPO_ROOT/deploy/nginx/jt-ipam-proxy.conf" \
            /etc/nginx/snippets/jt-ipam-proxy.conf

        # Replace the template server_name with the actual FQDN
        sed "s/ipam\.example\.com/${PUBLIC_FQDN}/g" \
            "$REPO_ROOT/deploy/nginx/jt-ipam.conf" \
            > /etc/nginx/sites-available/jt-ipam
        chmod 0644 /etc/nginx/sites-available/jt-ipam
        ln -sf /etc/nginx/sites-available/jt-ipam /etc/nginx/sites-enabled/jt-ipam

        # Remove apt's default site (its "Welcome to nginx" page gets picked up on IP access);
        # jt-ipam.conf is already the default_server, so removing default leaves only it
        if [[ -e /etc/nginx/sites-enabled/default ]]; then
            rm -f /etc/nginx/sites-enabled/default
            log "Removed default nginx site (Welcome to nginx page)"
        fi

        # Uses /etc/jt-ipam/tls/server.{crt,key} by default (#9 already generated a self-signed bootstrap cert).
        # To swap in a real cert: cp your cert + key to the paths above, then sudo systemctl reload nginx
        # Let's Encrypt route: edit /etc/nginx/sites-available/jt-ipam to point ssl_certificate at
        #   /etc/letsencrypt/live/${PUBLIC_FQDN}/{fullchain,privkey}.pem, then run certbot
        if apply_nginx_config; then
            log "nginx running and enabled at boot"
        else
            warn "review /etc/nginx/sites-available/jt-ipam"
        fi
    else
        log "Skipping nginx (mode: ${TLS_MODE} — uvicorn terminates TLS directly)"
    fi

    # Direct TLS on a privileged port needs an extra capability (see the function)
    if [[ "$TLS_MODE" != "nginx" ]]; then
        grant_bind_privileged_port "$BIND_PORT_DIRECT"
        systemctl restart jt-ipam-backend.service 2>/dev/null || true
        sleep 2
    fi

    # -- Self-check before claiming success --
    # A customer was told "install complete" while the service was not listening and the env
    # file was missing. Saying "done" without looking is worse than saying nothing: it sends
    # people looking in the wrong place. Check what has to be true, and name what is not.
    local _fail=0
    [[ -s "$ENV_FILE" ]] || { warn "MISSING: $ENV_FILE (backend configuration)"; _fail=1; }
    [[ -s "$FRONTEND_DIR/dist/index.html" ]] \
        || { warn "MISSING: $FRONTEND_DIR/dist/index.html (frontend was not built)"; _fail=1; }
    systemctl is-active --quiet jt-ipam-backend.service \
        || { warn "NOT RUNNING: jt-ipam-backend.service — journalctl -u jt-ipam-backend -n 50"; _fail=1; }
    local _expect_port
    if [[ "$TLS_MODE" == "nginx" ]]; then _expect_port=8000; else _expect_port="$BIND_PORT_DIRECT"; fi
    # 用連的，不要只看有沒有人 bind：精簡映像常常沒有 iproute2，`ss` 不在的時候
    # 這段以前是整個跳過 —— 等於在最需要驗證的那種機器上什麼都沒驗。
    local _probe_url
    if [[ "$TLS_MODE" == "nginx" ]]; then _probe_url="http://127.0.0.1:${_expect_port}/healthz"
    else _probe_url="https://127.0.0.1:${_expect_port}/healthz"; fi
    wait_until_serving "$_probe_url" 60 \
        || { warn "NOT ANSWERING on port ${_expect_port} (expected for --tls-mode ${TLS_MODE}) — journalctl -u jt-ipam-backend -n 50"; _fail=1; }
    if [[ "$TLS_MODE" == "nginx" ]]; then
        systemctl is-active --quiet nginx \
            || { warn "NOT RUNNING: nginx (needed in --tls-mode nginx to serve the UI on 443)"; _fail=1; }
    fi
    if (( _fail )); then
        warn "Install finished with problems — the items above must be fixed before jt-ipam works."
        warn "Re-running this installer is safe: it skips what is already in place."
    fi

    # -- Done --
    log "Done."
    case "$TLS_MODE" in
        nginx)
            log "  Backend on http://127.0.0.1:8000 (loopback only)"
            log "  Frontend served by nginx via https://${PUBLIC_FQDN}/"
            log "  Health: curl -fsS http://127.0.0.1:8000/healthz"
            ;;
        direct|self-signed)
            log "  Backend (TLS) on https://${PUBLIC_FQDN}:${BIND_PORT_DIRECT}/"
            log "  Health: curl -fsSk https://127.0.0.1:${BIND_PORT_DIRECT}/healthz"
            log "  Cert: ${TLS_DIR}/server.crt  Key: ${TLS_DIR}/server.key"
            log "  Note: browsers warn on self-signed certs; in production use an internal CA or Let's Encrypt"
            ;;
    esac
    log "Review /etc/jt-ipam/backend.env (especially APP_PUBLIC_URL / CORS_ORIGINS)"
    security_headers_notice "$TLS_MODE" "$PUBLIC_FQDN"

    # -- scan agent on this host (scanning always goes through an agent) --
    local _agent_url
    case "$TLS_MODE" in
        nginx) _agent_url="https://127.0.0.1" ;;
        *)     _agent_url="https://127.0.0.1:${BIND_PORT_DIRECT}" ;;
    esac
    install_local_scan_agent "$BACKEND_DIR" "$ENV_FILE" "$JTIPAM_USER" "$_agent_url"

    # -- first-admin credentials --
    if [[ -n "$INITIAL_ADMIN_PW" ]]; then
        echo
        echo "  ============================================================"
        echo "   First admin account created — change this password after login:"
        echo "     username: admin"
        echo "     password: ${INITIAL_ADMIN_PW}"
        echo "   (also saved to ${ADMIN_PW_RECORD}, root-only)"
        echo "   Reset later: sudo -u ${JTIPAM_USER} bash -c 'cd ${BACKEND_DIR}; set -a; source ${ENV_FILE}; set +a; .venv/bin/python -m app.cli.bootstrap create-admin --username admin --email admin@localhost --password-stdin --force-update'"
        echo "  ============================================================"
        echo
    else
        log "An admin account already exists; skipped creating one. To reset its password:"
        log "  sudo -u ${JTIPAM_USER} bash -c 'cd ${BACKEND_DIR}; set -a; source ${ENV_FILE}; set +a; .venv/bin/python -m app.cli.bootstrap create-admin --username admin --email admin@localhost --password-stdin --force-update'"
    fi
}

# Install (or repair) the scan agent that runs on the jt-ipam host itself.
#
# Scanning ALWAYS goes through an agent: the backend has no scheduled scan of its
# own, so a subnet left without an agent is never scanned at all. A customer who
# enabled scanning and waited for liveness to update waited forever (real report).
# A fresh install therefore ships one agent here, on this host.
#
# Idempotent and never fatal: the CLI leaves an existing agent alone (re-issuing
# its key would kick the running one off), and any failure here is reported but
# must not fail the install — the app itself is fine without it.
install_local_scan_agent() {
    local BACKEND_DIR="$1" ENV_FILE="$2" JTIPAM_USER="$3" SERVER_URL="$4"
    local installer="$REPO_ROOT/agent/jt-ipam-agent-installer.sh"
    [[ -f "$installer" ]] || { warn "Scan agent installer not found; skipping local agent."; return 0; }

    local out key
    out="$(cd "$BACKEND_DIR" && sudo -u "$JTIPAM_USER" --preserve-env=PATH bash -c \
        "set -a; source $ENV_FILE; set +a; .venv/bin/python -m app.cli.scan_agent ensure-local" 2>/dev/null)" || {
        warn "Could not create the local scan agent; add one from the UI (Admin -> Scan agents)."
        return 0
    }

    if [[ "$out" == adopted* ]]; then
        log "Adopted the scan agent already installed on this host ($(printf '%s' "$out" | cut -f2))."
        return 0
    fi
    if [[ "$out" == exists* ]]; then
        log "Local scan agent already registered; leaving it as is."
        # Still make sure the service is actually running (upgrade from a broken state)
        systemctl is-active --quiet jt-ipam-scan-agent.service \
            && { log "Local scan agent service is running."; return 0; }
        log "Local scan agent registered but its service is not running; re-running the installer."
        return 0
    fi

    key="$(printf '%s' "$out" | cut -f3)"
    [[ -n "$key" ]] || { warn "No enrollment key returned; skipping local agent install."; return 0; }

    log "Installing the local scan agent (probe tools included)…"
    # JT_IPAM_INSECURE=1: a fresh install commonly has a self-signed cert, and this
    # agent talks to its own host over the loopback-ish URL.
    if JT_IPAM_URL="$SERVER_URL" JT_IPAM_AGENT_KEY="$key" JT_IPAM_INSECURE=1 \
            bash "$installer" >/dev/null 2>&1; then
        log "Local scan agent installed and enrolled."
    else
        warn "Local scan agent install failed. Install it manually:"
        warn "  sudo JT_IPAM_URL='$SERVER_URL' JT_IPAM_AGENT_KEY='<key from Admin -> Scan agents>' bash $installer"
    fi
}

# =============================================================================
# cmd_doctor — check a live install and say exactly what to do about anything wrong
#
# Written because the failures customers actually hit were all *legible only if you
# already knew where to look*: a backup unit dying at 226/NAMESPACE that reads like a
# missing script, a sync timer marked failed because one firewall was unreachable,
# pgvector installed for the wrong PostgreSQL major, an integration silently not
# scanning because no agent was assigned. Each check below prints a fix, not a verdict.
# =============================================================================
cmd_doctor() {
    local ENV_FILE="${ENV_FILE:-/etc/jt-ipam/backend.env}"
    local ROOT="$REPO_ROOT"
    local BACKEND_DIR="$ROOT/backend"
    local problems=0
    local warns=0
    _bad()  { echo -e "  \033[1;31m✗\033[0m $1"; [[ -n "${2:-}" ]] && echo -e "      → $2"; problems=$((problems+1)); }
    _warn() { echo -e "  \033[1;33m!\033[0m $1"; [[ -n "${2:-}" ]] && echo -e "      → $2"; warns=$((warns+1)); }
    _ok()   { echo -e "  \033[1;32m✓\033[0m $1"; }

    echo "jt-ipam doctor — $(date '+%Y-%m-%d %H:%M:%S %Z')"
    echo

    # ── configuration ──
    echo "Configuration"
    if [[ -s "$ENV_FILE" ]]; then
        _ok "$ENV_FILE present"
    else
        _bad "$ENV_FILE missing or empty" \
             "re-run: sudo $0 install --tls-mode <nginx|self-signed> --public-fqdn <fqdn>"
    fi
    local TLS_MODE PORT
    TLS_MODE="$(grep -oP '^BACKEND_TLS_MODE=\K\S+' "$ENV_FILE" 2>/dev/null || echo nginx)"
    PORT="$(grep -oP '^BACKEND_BIND_PORT=\K\S+' "$ENV_FILE" 2>/dev/null || echo 8000)"
    _ok "TLS mode: $TLS_MODE (backend port $PORT)"

    # ── services ──
    echo
    echo "Services"
    if systemctl is-active --quiet jt-ipam-backend; then
        _ok "jt-ipam-backend running"
    else
        _bad "jt-ipam-backend is not running" "journalctl -u jt-ipam-backend -n 50 --no-pager"
    fi
    # Decide "is it up?" by connecting, not by looking for a bound port: minimal
    # images often lack iproute2, so an `ss`-based check reports a failure while
    # the service is perfectly fine. A diagnostic that lies is worse than none.
    local health_url
    if [[ "$TLS_MODE" == "nginx" ]]; then health_url="http://127.0.0.1:${PORT}/healthz"
    else health_url="https://127.0.0.1:${PORT}/healthz"; fi
    if curl -fsSk --max-time 10 "$health_url" >/dev/null 2>&1; then
        _ok "backend answers on $health_url"
    else
        _bad "backend does not answer on $health_url" \
             "journalctl -u jt-ipam-backend -n 50 --no-pager"
    fi
    if [[ "$TLS_MODE" == "nginx" ]]; then
        if systemctl is-active --quiet nginx; then
            _ok "nginx running"
        else
            _bad "nginx is not running — the UI is unreachable until it is" \
                 "sudo systemctl enable --now nginx   (then: systemctl status nginx)"
        fi
        # The path users actually take: 443 -> nginx -> backend. The backend can be
        # perfectly healthy on 8000 while nobody can reach the product.
        #
        # Deliberately NOT /healthz: the nginx site answers that one itself with a
        # static `return 200 "ok"`, so it stays green with the backend stopped -- it
        # proves nginx is alive and nothing more. Use a route that must be proxied;
        # 401 (unauthenticated) is a perfectly good "the backend answered", while a
        # dead backend gives 502.
        local code
        code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 10 \
                 "https://127.0.0.1/api/v1/system/version" 2>/dev/null || echo 000)"
        if [[ "$code" =~ ^[1-4] ]]; then
            _ok "reachable end to end over HTTPS (nginx -> backend, HTTP $code)"
        elif [[ "$code" == "000" ]]; then
            _bad "nothing answers HTTPS on port 443 -- this is what users hit" \
                 "sudo nginx -t && sudo systemctl restart nginx"
        else
            _bad "nginx answers but cannot reach the backend (HTTP $code)" \
                 "sudo systemctl restart jt-ipam-backend && journalctl -u jt-ipam-backend -n 50 --no-pager"
        fi
        local site=/etc/nginx/sites-available/jt-ipam
        if [[ -f "$site" ]] && grep -qF "(${WS_PROTOCOLS})/ws" "$site"; then
            _ok "nginx forwards WebSocket for all consoles"
        else
            _bad "nginx WebSocket location is missing or out of date" \
                 "sudo $0 upgrade   (rewrites it; consoles cannot connect without it)"
        fi
    else
        # direct mode: uvicorn must serve the SPA itself (no nginx to do it).
        # /healthz alone is not end-to-end -- a customer had every check green
        # while https://host:8443/ answered {"detail":"Not Found"}: the API was
        # fine and nobody was serving the UI. Ask for the page users actually load.
        local root_body
        root_body="$(curl -fsk --max-time 10 "https://127.0.0.1:${PORT}/" 2>/dev/null || true)"
        if [[ "${root_body,,}" == *"<!doctype html"* ]]; then
            _ok "UI served end to end on https://127.0.0.1:${PORT}/"
        else
            _bad "backend answers but the UI is not served on / (users get a JSON 404)" \
                 "upgrade to >=0.5.190 (backend serves the SPA in direct mode), then: sudo systemctl restart jt-ipam-backend"
        fi
    fi

    # guacd（必要元件：RDP／VNC 的預設引擎）：一定要裝、在跑、答得出來
    if [[ -x "$GUACD_PREFIX/sbin/guacd" ]]; then
        local greply=""
        if systemctl is-active --quiet "$GUACD_UNIT"; then
            greply="$(timeout 5 bash -c 'exec 3<>/dev/tcp/127.0.0.1/4822; printf "6.select,3.rdp;" >&3; head -c 100 <&3' 2>/dev/null || true)"
        fi
        if [[ "$greply" == *args* ]]; then
            _ok "guacd $(guacd_installed_version) running and answering on 127.0.0.1:4822"
        else
            _bad "guacd is installed but not answering on 127.0.0.1:4822" \
                 "sudo systemctl restart $GUACD_UNIT; journalctl -u jt-ipam-guacd -n 50 --no-pager"
        fi
    else
        _bad "guacd is not installed -- it is required (the RDP / VNC console engine); consoles only work through the optional built-in engine" \
             "sudo $0 upgrade   (installs it; offline: sudo $0 upgrade --guacd-tarball <file>)"
    fi

    # ── database ──
    echo
    echo "Database"
    if command -v psql >/dev/null 2>&1 && id -u postgres >/dev/null 2>&1; then
        local pgmaj
        pgmaj="$(sudo -u postgres psql -tAc 'SHOW server_version_num' 2>/dev/null | tr -dc '0-9')"
        if [[ -n "$pgmaj" ]]; then
            pgmaj=$(( pgmaj / 10000 ))
            _ok "PostgreSQL $pgmaj reachable"
            local dbname; dbname="$(grep -oP '^POSTGRES_DB=\K\S+' "$ENV_FILE" 2>/dev/null || echo jt_ipam)"
            if sudo -u postgres psql -d "$dbname" -tAc \
                    "SELECT 1 FROM pg_extension WHERE extname='vector'" 2>/dev/null | grep -q 1; then
                _ok "pgvector extension present"
            else
                _bad "pgvector is not enabled in database '$dbname'" \
                     "sudo apt install -y postgresql-${pgmaj}-pgvector && sudo -u postgres psql -d $dbname -c 'CREATE EXTENSION IF NOT EXISTS vector;'"
            fi
            local aowner
            aowner="$(sudo -u postgres psql -d "$dbname" -tAc "SELECT tableowner FROM pg_tables WHERE tablename='audit_logs'" 2>/dev/null | tr -d ' ')"
            if [[ "$aowner" == "jt_ipam_audit_owner" ]]; then
                _ok "audit table owned by jt_ipam_audit_owner (app role can only read and append)"
            elif [[ -n "$aowner" ]]; then
                _warn "audit table is owned by '$aowner': that role can disable its append-only triggers" \
                      "sudo bash $ROOT/scripts/jt-ipam.sh harden-audit"
            fi
        else
            _bad "cannot reach PostgreSQL as the postgres user" "systemctl status postgresql"
        fi
    else
        _warn "psql not available; skipped database checks"
    fi
    if [[ -x "$BACKEND_DIR/.venv/bin/alembic" ]]; then
        local head cur
        head="$(cd "$BACKEND_DIR" && sudo -u "${JTIPAM_USER:-jtipam}" bash -c \
            "set -a; source $ENV_FILE; set +a; .venv/bin/alembic heads 2>/dev/null" | awk '{print $1}' | head -1)"
        cur="$(cd "$BACKEND_DIR" && sudo -u "${JTIPAM_USER:-jtipam}" bash -c \
            "set -a; source $ENV_FILE; set +a; .venv/bin/alembic current 2>/dev/null" | awk '{print $1}' | tail -1)"
        if [[ -n "$head" && "$cur" == "$head"* ]]; then
            _ok "database schema at head ($head)"
        else
            _bad "database schema is behind (current '${cur:-none}', head '${head:-?}')" \
                 "sudo $0 upgrade"
        fi
    fi

    # ── frontend ──
    echo
    echo "Frontend"
    if [[ -s "$ROOT/frontend/dist/index.html" ]]; then
        local fev bev
        fev="$(grep -oP '"version":"\K[^"]+' "$ROOT/frontend/dist/version.json" 2>/dev/null || echo '?')"
        bev="$(grep -oP '^__version__ = "\K[^"]+' "$BACKEND_DIR/app/version.py" 2>/dev/null || echo '?')"
        if [[ "$fev" == "$bev" ]]; then
            _ok "frontend built and matches backend ($fev)"
        else
            _warn "frontend build is $fev but backend is $bev" "sudo $0 upgrade   (rebuilds the frontend)"
        fi
    else
        _bad "frontend was never built (no dist/index.html)" "sudo $0 upgrade"
    fi
    # The build toolchain is only used by install / upgrade, so an old one is a warning. A site
    # shows it after an upgrade that could not install Node.js 22 and fell back (ensure_node).
    local nbin nmaj
    nbin="$(command -v node 2>/dev/null || true)"
    nmaj="$(node_major "$nbin")"
    if (( nmaj >= NODE_REQUIRED_MAJOR )); then
        _ok "Node.js $("$nbin" -v) for building the frontend"
    elif (( nmaj > 0 )); then
        _warn "Node.js $("$nbin" -v) is older than ${NODE_REQUIRED_MAJOR}, which building the frontend needs" \
              "curl -fsSL https://deb.nodesource.com/setup_${NODE_REQUIRED_MAJOR}.x | sudo bash - && sudo apt-get install -y nodejs   (or: sudo $0 upgrade)"
    else
        _warn "Node.js not found (only needed to rebuild the frontend)" "sudo $0 upgrade   (installs it)"
    fi

    # ── scheduled work ──
    echo
    echo "Scheduled work"
    local t
    for t in jt-ipam-sync.timer jt-ipam-ai-audit.timer jt-ipam-backup.timer; do
        systemctl is-active --quiet "$t" && _ok "$t enabled" \
            || _bad "$t is not running" "sudo systemctl enable --now $t"
    done
    # Reference-data refresh: missing only means stale data, so a warning, not a failure
    local rt
    for rt in "${REFRESH_UNITS[@]}"; do
        if systemctl is-active --quiet "$rt.timer"; then
            _ok "$rt.timer enabled"
        elif [[ -f "/etc/systemd/system/$rt.timer" ]]; then
            _warn "$rt.timer is installed but not running" "sudo systemctl enable --now $rt.timer"
        else
            _warn "$rt.timer is not installed (reference data will not refresh)" "sudo $0 upgrade"
        fi
    done
    # Recog fingerprint database (optional component of the IP probe)
    local recog_out
    if recog_out="$(cd "$BACKEND_DIR" && timeout 30 sudo -u "${JTIPAM_USER:-jtipam}" bash -c \
            "set -a; source '$ENV_FILE'; set +a; .venv/bin/python -m app.cli.recog status" 2>/dev/null)"; then
        _ok "Recog fingerprint database: ${recog_out//$'\t'/ }"
    else
        _warn "Recog fingerprint database not installed (optional; the IP probe identifies less without it)" \
              "sudo -u ${JTIPAM_USER:-jtipam} bash -c 'cd $BACKEND_DIR; set -a; source $ENV_FILE; set +a; .venv/bin/python -m app.cli.recog update'"
    fi
    # Without the directory the backup unit dies at 226/NAMESPACE, and the message
    # looks like "script not found".
    if [[ -d /var/backups/jt-ipam ]]; then
        # Look at the status file the backup script writes and at real (non-empty) dump files. "The
        # newest dated directory exists" is not enough: a failed run used to leave one with a 0-byte dump, and
        # backups failed for weeks on a real site while this check said OK.
        local bst="" berr="" benc="" latest_dump=""
        if [[ -r /var/backups/jt-ipam/last-run ]]; then
            bst="$(sed -n 's/^status=//p' /var/backups/jt-ipam/last-run | head -1)"
            berr="$(sed -n 's/^error=//p' /var/backups/jt-ipam/last-run | head -1)"
            benc="$(sed -n 's/^encrypted=//p' /var/backups/jt-ipam/last-run | head -1)"
        fi
        # Plain backups are <date>/<name>.dump; encrypted ones (2026-10-09) are jt-ipam-<date>.jtbak
        latest_dump="$( { find /var/backups/jt-ipam -mindepth 2 -maxdepth 2 -name '*.dump' -size +0 -printf '%T@ %p\n'
                          find /var/backups/jt-ipam -mindepth 1 -maxdepth 1 -name '*.jtbak' -size +0 -printf '%T@ %p\n'
                        } 2>/dev/null | sort -n | tail -1 | cut -d' ' -f2-)"
        if [[ "$bst" == "fail" ]]; then
            _bad "the last backup failed: ${berr:-see the backup log}" "journalctl -u jt-ipam-backup -n 30"
        elif [[ -z "$latest_dump" ]]; then
            _warn "no backup file yet" "sudo systemctl start jt-ipam-backup.service"
        elif [[ -z "$(find "$latest_dump" -mmin -2880 2>/dev/null)" ]]; then
            _warn "the newest backup is older than 48 hours: $latest_dump" "journalctl -u jt-ipam-backup -n 30"
        else
            _ok "backups present (latest: $latest_dump)"
        fi
        if [[ "$bst" == "ok" && "$benc" != "1" ]]; then
            _warn "the latest backup is not encrypted: it holds the database and backend.env (the key that decrypts its secrets) in the clear" \
                  "set a passphrase under System settings → Backup encryption"
        fi
    else
        _bad "/var/backups/jt-ipam is missing — the backup unit will fail at 226/NAMESPACE" \
             "sudo install -d -m 0700 /var/backups/jt-ipam"
    fi
    local sync_res
    sync_res="$(systemctl show jt-ipam-sync.service -p Result --value 2>/dev/null)"
    if [[ -z "$sync_res" || "$sync_res" == "success" ]]; then
        _ok "last sync run completed"
    else
        _bad "last sync run ended with '$sync_res'" "journalctl -u jt-ipam-sync -n 60 --no-pager"
    fi

    # ── scanning ──
    echo
    echo "Scanning"
    if systemctl is-active --quiet jt-ipam-scan-agent 2>/dev/null; then
        _ok "local scan agent running"
    else
        _warn "no scan agent on this host — subnets assigned to it will not be scanned" \
              "sudo $0 upgrade   (installs one), or add an agent under Admin → Scan agents"
    fi

    # ── remote console ──
    # Only report the engine the site actually selected. Saying "FreeRDP not installed" on a
    # site that uses the default engine is noise, and noise is what makes people stop reading
    # the output.
    local rdp_engine vnc_engine
    rdp_engine="$(sudo -u postgres psql -tAd jt_ipam -c \
        "SELECT COALESCE(value->>'rdp_engine','guacd') FROM system_settings WHERE key='console_security'" \
        2>/dev/null | tr -d '[:space:]')"
    [[ -n "$rdp_engine" ]] || rdp_engine="guacd"
    vnc_engine="$(sudo -u postgres psql -tAd jt_ipam -c \
        "SELECT COALESCE(value->>'vnc_engine','guacd') FROM system_settings WHERE key='console_security'" \
        2>/dev/null | tr -d '[:space:]')"
    [[ -n "$vnc_engine" ]] || vnc_engine="guacd"
    echo
    echo "Remote console"
    if [[ "$rdp_engine" == "freerdp" ]]; then
        if freerdp_ready; then
            _ok "RDP engine: FreeRDP (packages and syscall drop-in present)"
        elif ! freerdp_apt_present; then
            _bad "RDP engine is set to FreeRDP but its packages are missing — RDP consoles will not connect" \
                 "sudo apt-get install -y ${FREERDP_APT_PACKAGES[*]}"
        else
            _bad "RDP engine is set to FreeRDP but the syscall drop-in is missing — the virtual display is killed on start (SIGSYS)" \
                 "sudo $0 upgrade   (installs it), or see docs/INSTALL.md"
        fi
    elif [[ "$rdp_engine" == "guacd" ]]; then
        _ok "RDP engine: guacd (checked above)"
    fi
    # aardwolf is optional now; only a problem when a console is explicitly set to it
    if [[ "$rdp_engine" == "aardwolf" || "$vnc_engine" == "builtin" ]]; then
        if "$BACKEND_DIR/.venv/bin/python" -c 'import aardwolf' 2>/dev/null; then
            _ok "aardwolf present (RDP engine: $rdp_engine, VNC engine: $vnc_engine)"
        else
            _bad "a console is set to the built-in aardwolf engine (RDP: $rdp_engine, VNC: $vnc_engine) but aardwolf is not installed" \
                 "switch RDP / VNC back to guacd in Admin -> System settings (aardwolf is optional)"
        fi
    fi

    echo
    if (( problems )); then
        echo -e "\033[1;31m$problems problem(s), $warns warning(s) — follow the → lines above\033[0m"
        return 1
    fi
    if (( warns )); then
        echo -e "\033[1;33mNo problems, $warns warning(s)\033[0m"
        return 0
    fi
    echo -e "\033[1;32mAll checks passed\033[0m"
    return 0
}

# =============================================================================
# cmd_upgrade — upgrade existing install (original scripts/jt-ipam-upgrade.sh logic, preserved verbatim)
# =============================================================================
# Recover a repository whose history no longer fast-forwards onto the remote.
#
# Why this exists: the upgrade pulls with --ff-only, and the script runs under
# `set -e`. When the pull fails the whole upgrade stops right there -- no backup,
# no migration, no build, no restart -- and all the operator sees is git's own
# message. Worse, it is not a one-off: every subsequent upgrade fails the same
# way, because nothing about the repository has changed. The fix is
# `git reset --hard origin/<branch>`, which nobody would guess.
#
# Causes seen in practice: someone committed a local edit on the box, a partial
# clone, or upstream history that was rewritten. In all of them the checked-out
# source is meant to be a copy of upstream -- customer configuration lives in
# /etc/jt-ipam, not in the repo -- so resetting to the remote is the right move.
#
# Nothing is thrown away silently: any commits that exist only here are kept on a
# timestamped branch, and the message says where they went.
recover_diverged_repo() {
    local root="$1" branch upstream local_only backup
    branch="$(as_user git -C "$root" rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)"
    upstream="origin/${branch}"

    as_user git -C "$root" fetch origin --prune >/dev/null 2>&1 || true
    if ! as_user git -C "$root" rev-parse --verify --quiet "$upstream" >/dev/null; then
        die "Cannot reach ${upstream}. Check network access to the git remote, then re-run upgrade."
    fi

    # A dirty tree was already handled (discarded or aborted) before the pull, so
    # anything left here is committed history -- safe to move aside, not to lose.
    local_only="$(as_user git -C "$root" log --oneline "${upstream}..HEAD" 2>/dev/null || true)"

    warn "The local repository no longer fast-forwards onto ${upstream}."
    if [[ -n "$local_only" ]]; then
        warn "These commits exist only in ${root}:"
        printf '%s\n' "$local_only" | sed 's/^/      /' >&2
        backup="upgrade-recovery/$(date +%Y%m%d-%H%M%S)"
        as_user git -C "$root" branch "$backup" HEAD >/dev/null 2>&1 || true
        warn "Kept on branch '${backup}' -- nothing is lost."
    fi

    log "Resetting the source tree to ${upstream} so the upgrade can continue…"
    as_user git -C "$root" reset --hard "$upstream"
    log "Recovered: $(as_user git -C "$root" rev-parse --short HEAD)"
}

cmd_upgrade() {
    local UPGRADE_ARGS=("$@")
    local ROOT="$REPO_ROOT"
    local ENV_FILE="${ENV_FILE:-/etc/jt-ipam/backend.env}"
    local SVC="jt-ipam-backend"
    local DO_PULL=1
    local FORCE=0
    local GUACD_TARBALL=""      # guacd 是必要元件：每次升級都會裝上／更新
    local RECOG_ZIP=""          # Recog 指紋庫（選用）：離線主機用手上的發佈檔
    local _prev=""
    for arg in "$@"; do
      if [[ "$_prev" == "--guacd-tarball" ]]; then GUACD_TARBALL="$(readlink -f "$arg")"; _prev=""; continue; fi
      if [[ "$_prev" == "--recog-zip" ]]; then RECOG_ZIP="$(readlink -f "$arg")"; _prev=""; continue; fi
      case "$arg" in
        --no-pull) DO_PULL=0 ;;
        --force|-f) FORCE=1 ;;
        --with-guacd) ;;                    # 舊參數，保留相容（guacd 一定會裝）
        --guacd-tarball) _prev="--guacd-tarball" ;;
        --recog-zip) _prev="--recog-zip" ;;
      esac
    done

    [[ $EUID -eq 0 ]] || die "please run as root / sudo (needs to restart services and write backups)"
    [[ -r "$ENV_FILE" ]] || die "cannot read $ENV_FILE"
    [[ -d "$ROOT/backend/.venv" ]] || die "cannot find $ROOT/backend/.venv; this host does not look like an installed jt-ipam"

    # Run git / pip / pnpm as the repo owner (avoid root touching jtipam's files and venv)
    local JTIPAM_USER="${JTIPAM_USER:-$(stat -c '%U' "$ROOT")}"
    as_user() { sudo -u "$JTIPAM_USER" "$@"; }

    ver_of() { grep -m1 '"version"' "$ROOT/frontend/package.json" | sed -E 's/.*"version"\s*:\s*"([^"]+)".*/\1/'; }
    alembic_head() {
      # Must source env inside the sudo subshell (sudo strips parent environment variables)
      ( as_user bash -c "cd '$ROOT/backend'; set -a; source '$ENV_FILE'; set +a; .venv/bin/alembic current" 2>/dev/null | head -1 ) || true
    }

    local OLD_VER OLD_REV
    OLD_VER="$(ver_of)"
    OLD_REV="$(as_user git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo '?')"
    log "Before upgrade: version ${OLD_VER}  commit ${OLD_REV}  alembic $(alembic_head)"

    # Same OS packages as a fresh install — a feature added in a newer version may need a
    # binary the existing host does not have yet.
    ensure_runtime_deps
    ensure_icmp_capability

    # -- rollback guidance on failure --
    local DUMP_PATH=""
    on_err() {
      warn "Upgrade aborted. How to roll back:"
      warn "  1) Code: sudo -u $JTIPAM_USER git -C $ROOT reset --hard $OLD_REV"
      if [[ "$DUMP_PATH" == *.jtbak ]]; then
        warn "  2) Database: decrypt first: python3 $ROOT/backend/app/services/backup_crypt.py decrypt $DUMP_PATH --out /root/jt-ipam-restore"
        warn "     then: pg_restore --clean --no-owner -d <db> /root/jt-ipam-restore/*/*.dump"
      elif [[ -n "$DUMP_PATH" ]]; then
        warn "  2) Database: pg_restore --clean --no-owner -d <db> $DUMP_PATH"
      fi
      warn "  3) Rebuild frontend and restart: run build in $ROOT/frontend, then systemctl restart $SVC"
    }
    trap on_err ERR

    # -- 2. git pull --
    if [[ $DO_PULL -eq 1 ]]; then
      as_user git config --global --add safe.directory "$ROOT" 2>/dev/null || true
      # Handle a dirty working tree (local changes to tracked files — e.g. a hand-edited or
      # partially-updated scripts/jt-ipam.sh) so the upgrade doesn't just abort with
      # "Your local changes to the following files would be overwritten by merge".
      local DIRTY
      DIRTY="$(as_user git -C "$ROOT" status --porcelain --untracked-files=no 2>/dev/null || true)"
      if [[ -n "$DIRTY" ]]; then
        warn "Local changes to tracked files were found in $ROOT:"
        printf '%s\n' "$DIRTY" | sed 's/^/      /' >&2
        local do_discard=0
        if [[ $FORCE -eq 1 ]]; then
          do_discard=1
          log "--force set → discarding these local changes and continuing."
        elif [[ -t 0 ]]; then
          local ans=""
          read -r -p "Discard these local changes and continue upgrading? [y/N] " ans || true
          [[ "$ans" =~ ^[Yy] ]] && do_discard=1
        else
          die "Upgrade would overwrite local changes. Re-run 'jt-ipam.sh upgrade --force' to discard them, or commit/stash them first."
        fi
        if [[ $do_discard -eq 1 ]]; then
          # Only touches tracked files; untracked/ignored files (customer config lives outside
          # the repo, in /etc/jt-ipam) are left alone. reset to HEAD, then the pull fast-forwards.
          log "Discarding local changes (git reset --hard HEAD)…"
          as_user git -C "$ROOT" reset --hard >/dev/null
        else
          die "Aborted: local changes kept. Commit or stash them, then re-run upgrade (or use --force)."
        fi
      fi
      log "git pull --ff-only"
      if ! as_user git -C "$ROOT" pull --ff-only; then
          recover_diverged_repo "$ROOT"
      fi
    else
      log "Skipping git pull (--no-pull)"
    fi

    local NEW_VER NEW_REV
    NEW_VER="$(ver_of)"
    NEW_REV="$(as_user git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo '?')"
    if [[ "$OLD_REV" == "$NEW_REV" && $DO_PULL -eq 1 ]]; then
      log "Already up to date (commit unchanged); still runs migration / build once to ensure consistency."
    fi

    # The pull may have rewritten THIS script, and bash reads a script
    # incrementally by byte offset -- so once the file underneath changes, the
    # rest of this run is undefined. In practice bash simply STOPS at that point
    # and exits 0: the upgrade prints "git pull", reports success, and never runs
    # the backup, the migration, the build or the restart. Nothing looks wrong.
    # (Reproduced directly: a script that rewrites itself mid-run stops dead with
    # exit code 0.) That also meant every fix we ship to this script only took
    # effect one upgrade later, because the old copy was still driving.
    #
    # So: hand over to the new copy exactly once, skipping the pull it already did.
    if [[ "$OLD_REV" != "$NEW_REV" && "${JT_IPAM_UPGRADE_REEXEC:-0}" != "1" ]]; then
        log "The upgrade script itself was updated (${OLD_REV} -> ${NEW_REV}); continuing with the new version…"
        export JT_IPAM_UPGRADE_REEXEC=1
        exec bash "$ROOT/scripts/jt-ipam.sh" upgrade --no-pull "${UPGRADE_ARGS[@]}"
    fi

    # -- 2b. Node.js for the frontend build: settle it before anything touches the database.
    # Found out at build time (as it used to be), a missing node stopped the upgrade with the
    # schema already migrated under a backend still running the old code. Falls back to an
    # existing Node >= 20 when 22 cannot be installed -- see ensure_node. The call in
    # build_frontend is then a no-op.
    ensure_node upgrade

    # -- 3. back up the database (use the existing script if present) --
    if [[ -x "$ROOT/scripts/jt-ipam-backup.sh" ]]; then
      log "Backing up the database…"
      "$ROOT/scripts/jt-ipam-backup.sh"
      DUMP_PATH="$(find /var/backups/jt-ipam \( -name '*.dump' -o -name '*.jtbak' \) -newermt '-2 min' 2>/dev/null | sort | tail -1 || true)"
      [[ -n "$DUMP_PATH" ]] && log "Backup file: $DUMP_PATH"
    else
      warn "cannot find jt-ipam-backup.sh, skipping automatic backup (strongly recommend a manual pg_dump first)"
    fi

    # -- 3c. ensure upload directories exist (floorplans etc.; may not exist yet when upgrading from an old version) --
    install -d -o "$JTIPAM_USER" -g "$JTIPAM_USER" -m 0750 \
      /var/lib/jt-ipam/uploads /var/lib/jt-ipam/uploads/floorplans 2>/dev/null || true

    # -- 4. backend dependencies --
    log "Updating backend dependencies (pip install -e .)…"
    # 先把 pip 升到安全版本（以前升級完全不升 pip）：舊版 pip 內嵌的 certifi 仍信任 2026-10 外洩的 emSign 中間憑證
    ( cd "$ROOT/backend"; as_user .venv/bin/pip install --quiet --upgrade "pip>=26.2.2" ) \
        || warn "Could not upgrade pip to 26.2.2 or later; continuing with the current pip"
    ( cd "$ROOT/backend"; as_user .venv/bin/pip install --quiet -e . )
    install_rdp_optional
    ensure_kerberos_support
    ensure_freerdp_if_selected
    ensure_guacd_current "$GUACD_TARBALL"
    # IPMI tools for the BMC console (install on upgrade of existing setups; best-effort)
    if command -v apt-get >/dev/null 2>&1 && ! command -v ipmitool >/dev/null 2>&1; then
        log "Installing IPMI tools (ipmitool freeipmi-tools) for the BMC console…"
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ipmitool freeipmi-tools || \
            warn "ipmitool install failed; BMC console unavailable until installed."
    fi

    # -- 5. database migration --
    # The audit table belongs to a NOLOGIN role; migrations run as the app role, so hand it
    # back for the migration and take it away again right after (also on failure below).
    unharden_audit_table
    log "alembic upgrade head…"
    # env must be sourced inside the sudo subshell (sudo does not carry parent environment by default)
    if ! as_user bash -c "cd '$ROOT/backend'; set -a; source '$ENV_FILE'; set +a; .venv/bin/alembic upgrade head"; then
        harden_audit_table
        die "alembic upgrade failed (see above)."
    fi
    harden_audit_table

    # -- 5b. Recog fingerprint database (optional): install if missing, update if GitHub has a newer release --
    install_recog_db "$ROOT/backend" "$ENV_FILE" "$JTIPAM_USER" "$RECOG_ZIP"

    # -- 6. frontend build (as root with a clean toolchain, then chown back) --
    log "Building frontend…"
    build_frontend "$ROOT/frontend" "$JTIPAM_USER:$JTIPAM_USER" upgrade

    # -- 6b. ensure nginx forwards WebSocket (SSH terminal); idempotent, safe no-op if already present --
    patch_nginx_websocket
    patch_nginx_readyz_phpipam
    patch_nginx_agent_relay
    patch_nginx_token_logging

    # -- 6c. directories the sandboxed units require --
    # Installs from older versions never created /var/backups/jt-ipam, while
    # jt-ipam-backup.service lists it in ReadWritePaths -- so the daily backup died at
    # 226/NAMESPACE every night, with a message that looked like "backup script not
    # found".
    # PDF 報告的 fpdf2 會載入 numpy／OpenBLAS，它一載入就呼叫 mbind：沒有這份 drop-in 會被 SystemCallFilter 殺掉
    ensure_backend_syscall_dropin
    ensure_unit_dirs

    # -- 6c2. the daily backup runs /usr/local/bin/jt-ipam-backup.sh, a copy made at install
    # time. Without refreshing it here no fix to the backup script ever reaches an existing
    # site (2026-10-08: the status file and the safe temp-then-rename only exist in the repo copy).
    if [[ -f "$ROOT/scripts/jt-ipam-backup.sh" ]]; then
        install -m 0755 "$ROOT/scripts/jt-ipam-backup.sh" /usr/local/bin/jt-ipam-backup.sh
    fi

    # -- 6d. scheduled refresh of GeoIP / OUI / Recog (older installs never had these timers) --
    install_refresh_timers
    # -- 6e. scheduled AI review now has its own timer (it used to run inside jt-ipam-sync) --
    install_ai_audit_timer

    # -- 7. restart backend --
    log "Restarting $SVC…"
    systemctl restart "$SVC"
    systemctl is-active --quiet "$SVC" || die "$SVC did not come up after restart; check journalctl -u $SVC"
    local _rdy_tls _rdy_port _rdy_url
    _rdy_tls="$(grep -oP 'BACKEND_TLS_MODE=\K\S+' "$ENV_FILE" 2>/dev/null || echo nginx)"
    if [[ "$_rdy_tls" == "nginx" ]]; then
        # 走使用者實際走的路徑（443 → nginx → 後端）。**刻意不用 /healthz**：
        # nginx 自己就靜態回它 200，後端停著也一樣綠。
        _rdy_url="https://127.0.0.1/api/v1/system/version"
    else
        _rdy_port="$(grep -oP 'BACKEND_BIND_PORT=\K\S+' "$ENV_FILE" 2>/dev/null || echo 8443)"
        _rdy_url="https://127.0.0.1:${_rdy_port}/api/v1/system/version"
    fi
    if wait_until_serving "$_rdy_url" 90; then
        log "Backend is answering on ${_rdy_url}"
    else
        die "$SVC restarted but nothing answers on ${_rdy_url} after 90s; check: journalctl -u $SVC -n 50"
    fi
    # Existing installs upgraded from a version without the capability drop-in get it here,
    # and either way we read back what the running service actually holds.
    verify_icmp_ready

    # -- 7b. scan agent on this host: installs are expected to have one, and older
    # installs predate that. Without an agent nothing scans at all — the subnet
    # setting that reads "scan on this host" has no scheduler behind it.
    local _up_tls _up_port _up_url
    _up_tls="$(grep -oP 'BACKEND_TLS_MODE=\K\S+' "$ENV_FILE" 2>/dev/null || echo nginx)"
    _up_port="$(grep -oP 'BACKEND_PORT=\K\S+' "$ENV_FILE" 2>/dev/null || echo 8443)"
    if [[ "$_up_tls" == "nginx" ]]; then _up_url="https://127.0.0.1"; else _up_url="https://127.0.0.1:${_up_port}"; fi
    install_local_scan_agent "$ROOT/backend" "$ENV_FILE" "$JTIPAM_USER" "$_up_url"

    trap - ERR
    log "Upgrade complete: ${OLD_VER} (${OLD_REV}) -> ${NEW_VER} (${NEW_REV})  alembic $(alembic_head)"
    log "Frontend rebuilt (nginx serves dist directly, no restart needed)."
    security_headers_notice "$(grep -oP 'BACKEND_TLS_MODE=\K\S+' "$ENV_FILE" 2>/dev/null || echo nginx)" \
        "$(grep -oP 'APP_PUBLIC_URL=https?://\K[^/]+' "$ENV_FILE" 2>/dev/null || echo your-fqdn)"
}

# =============================================================================
# cmd_uninstall — stop and remove systemd units/timers + nginx site
#   default: keep DB / /etc/jt-ipam / /var/lib/jt-ipam / jtipam user / /opt/jt-ipam
#   --purge: also dropdb + remove config/uploads/system user (requires yes or --yes)
#   Never removes the /opt/jt-ipam source.
# =============================================================================
cmd_uninstall() {
    local PURGE=0
    local ASSUME_YES=0
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --purge) PURGE=1; shift ;;
            --yes|-y) ASSUME_YES=1; shift ;;
            -h|--help) usage; exit 0 ;;
            *) echo "Unknown arg: $1" >&2; exit 2 ;;
        esac
    done

    require_root

    local ETC_DIR="/etc/jt-ipam"
    local DATA_DIR="/var/lib/jt-ipam"
    local JTIPAM_USER="jtipam"

    # -- stop + disable systemd units / timers --
    # backend + known timers + a possibly-present scan-agent
    local UNITS=(
        jt-ipam-backend.service
        jt-ipam-sync.timer
        jt-ipam-sync.service
        jt-ipam-ai-audit.timer
        jt-ipam-ai-audit.service
        jt-ipam-oui-refresh.timer
        jt-ipam-oui-refresh.service
        jt-ipam-geoip-refresh.timer
        jt-ipam-geoip-refresh.service
        jt-ipam-recog-refresh.timer
        jt-ipam-recog-refresh.service
        jt-ipam-backup.timer
        jt-ipam-backup.service
        jt-ipam-scan-agent.service
        jt-ipam-guacd.service
    )
    local unit
    for unit in "${UNITS[@]}"; do
        if systemctl list-unit-files "$unit" >/dev/null 2>&1 \
                && systemctl list-unit-files "$unit" 2>/dev/null | grep -q "$unit"; then
            log "Stopping + disabling $unit…"
            systemctl disable --now "$unit" 2>/dev/null || true
        fi
        # Remove the unit file (if present)
        if [[ -f "/etc/systemd/system/$unit" ]]; then
            rm -f "/etc/systemd/system/$unit"
            log "Removed /etc/systemd/system/$unit"
        fi
    done
    systemctl daemon-reload

    # Remove the backup wrapper (install puts it in /usr/local/bin)
    if [[ -f /usr/local/bin/jt-ipam-backup.sh ]]; then
        rm -f /usr/local/bin/jt-ipam-backup.sh
        log "Removed /usr/local/bin/jt-ipam-backup.sh"
    fi

    # -- nginx site / snippet --
    local NGINX_RELOAD=0
    if [[ -e /etc/nginx/sites-enabled/jt-ipam ]]; then
        rm -f /etc/nginx/sites-enabled/jt-ipam
        log "Removed nginx sites-enabled/jt-ipam"
        NGINX_RELOAD=1
    fi
    if [[ -e /etc/nginx/sites-available/jt-ipam ]]; then
        rm -f /etc/nginx/sites-available/jt-ipam
        log "Removed nginx sites-available/jt-ipam"
        NGINX_RELOAD=1
    fi
    if [[ -e /etc/nginx/snippets/jt-ipam-proxy.conf ]]; then
        rm -f /etc/nginx/snippets/jt-ipam-proxy.conf
        log "Removed nginx snippet jt-ipam-proxy.conf"
        NGINX_RELOAD=1
    fi
    if [[ $NGINX_RELOAD -eq 1 ]] && command -v nginx >/dev/null 2>&1; then
        if nginx -t >/dev/null 2>&1; then
            systemctl reload nginx 2>/dev/null || true
        else
            warn "nginx -t failed, not reloading; please check /etc/nginx manually"
        fi
    fi

    if [[ $PURGE -eq 0 ]]; then
        log "Stopped and removed systemd units/timers + nginx site."
        log "Kept: database jt_ipam / $ETC_DIR / $DATA_DIR / system user $JTIPAM_USER / source $REPO_ROOT"
        log "To also delete the data: sudo jt-ipam.sh uninstall --purge"
        return 0
    fi

    # -- --purge: destructive operation, requires explicit confirmation --
    echo
    echo -e "\033[1;31m###############################################################\033[0m" >&2
    echo -e "\033[1;31m# WARNING: --purge will permanently delete the following, unrecoverable:\033[0m" >&2
    echo -e "\033[1;31m#   * PostgreSQL database jt_ipam (dropdb, all IPAM data)\033[0m" >&2
    echo -e "\033[1;31m#   * $ETC_DIR (config / secrets / TLS certs)\033[0m" >&2
    echo -e "\033[1;31m#   * $DATA_DIR (uploads / floorplans / logs)\033[0m" >&2
    echo -e "\033[1;31m#   * system user $JTIPAM_USER\033[0m" >&2
    echo -e "\033[1;31m#   * $GUACD_PREFIX (guacd, if installed)\033[0m" >&2
    echo -e "\033[1;31m# (the source $REPO_ROOT will not be deleted)\033[0m" >&2
    echo -e "\033[1;31m###############################################################\033[0m" >&2
    echo

    if [[ $ASSUME_YES -ne 1 ]]; then
        local ans=""
        read -r -p "Are you sure you want to permanently delete the above? Type yes to confirm: " ans
        if [[ "$ans" != "yes" ]]; then
            HELP_HINT=0; die "Did not type yes, purge aborted (nothing was deleted)."
        fi
    else
        warn "--yes given, skipping interactive confirmation, purging directly."
    fi

    # 1) dropdb jt_ipam
    if command -v psql >/dev/null 2>&1; then
        log "Dropping database jt_ipam…"
        sudo -u postgres dropdb --if-exists jt_ipam 2>/dev/null \
            || warn "dropdb jt_ipam failed (DB may not exist or postgres is not running)"
        # Also remove the role (if present)
        if sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='jt_ipam'" 2>/dev/null | grep -q 1; then
            sudo -u postgres psql -c "DROP ROLE IF EXISTS jt_ipam;" 2>/dev/null \
                || warn "DROP ROLE jt_ipam failed (may have dependent objects)"
            sudo -u postgres psql -c "DROP ROLE IF EXISTS jt_ipam_audit_owner;" 2>/dev/null || true
        fi
    else
        warn "cannot find psql, skipping dropdb (please clean up PostgreSQL manually)"
    fi

    # 2) /etc/jt-ipam
    if [[ -d "$ETC_DIR" ]]; then
        rm -rf "$ETC_DIR"
        log "Removed $ETC_DIR"
    fi

    # 2b) guacd（選用的主控台引擎；服務已在上面停掉）
    if [[ -d "$GUACD_PREFIX" ]]; then
        rm -rf "$GUACD_PREFIX" /var/lib/private/jt-ipam-guacd /var/lib/jt-ipam-guacd
        log "Removed $GUACD_PREFIX"
    fi

    # 3) /var/lib/jt-ipam (+ log directory)
    if [[ -d "$DATA_DIR" ]]; then
        rm -rf "$DATA_DIR"
        log "Removed $DATA_DIR"
    fi
    if [[ -d /var/log/jt-ipam ]]; then
        rm -rf /var/log/jt-ipam
        log "Removed /var/log/jt-ipam"
    fi

    # 4) system user
    if id -u "$JTIPAM_USER" >/dev/null 2>&1; then
        userdel "$JTIPAM_USER" 2>/dev/null || warn "userdel $JTIPAM_USER failed (may have running processes)"
        log "Removed system user $JTIPAM_USER"
    fi

    log "Purge complete. The source $REPO_ROOT was kept (rm it yourself if you want it gone)."
}

# =============================================================================
# top-level dispatch
# =============================================================================
main() {
    local cmd="${1:-}"
    case "$cmd" in
        ""|help|-h|--help)
            usage
            # no args -> exit 2; explicit help -> exit 0
            [[ -z "$cmd" ]] && exit 2 || exit 0
            ;;
        install)   shift; cmd_install "$@" ;;
        upgrade)   shift; cmd_upgrade "$@" ;;
        uninstall) shift; cmd_uninstall "$@" ;;
        doctor)    shift; cmd_doctor "$@" ;;
        harden-audit) shift; cmd_harden_audit "$@" ;;
        sbom)      shift; cmd_sbom "$@" ;;
        *)
            echo "[error] Unknown command: $cmd" >&2
            echo >&2
            usage >&2
            exit 2
            ;;
    esac
}

# Only run when executed, not when sourced. The recovery logic below is worth a
# test, and a test cannot reach it if sourcing the file starts an upgrade.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
