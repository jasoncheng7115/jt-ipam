#!/usr/bin/env bash
# jt-ipam RustDesk agent installer (systemd service).
#
# Install this on the host that runs RustDesk Server (open source; hbbs/hbbr), not on a scan agent host.
# One agent per RustDesk server: the agent key comes from that server's row in jt-ipam
# ("Integrate RustDesk" -> the server -> install command).
#
# Supported: any systemd Linux with python3 (Debian 11/12/13, Ubuntu 22.04/24.04/26.04, RHEL family ...).
# The agent itself is a single Python file, standard library only (no pip, no extra packages).
#
# Usage:
#   sudo JT_IPAM_URL=https://ipam.example.com JT_IPAM_AGENT_KEY=<key> ./jt-ipam-rustdesk-agent-installer.sh
# Options:
#   JT_IPAM_PIN=sha256//...   the jt-ipam server's public key (the install command shown in jt-ipam sets it):
#                             the agent talks only to that server, also with a self-signed certificate
#   JT_IPAM_INSECURE=1        skip TLS verification entirely (not recommended; use JT_IPAM_PIN)
#   JT_IPAM_RUSTDESK_DIR=...  hbbs working directory when it is not /var/lib/rustdesk-server (official deb)
#   JT_IPAM_UNINSTALL=1       remove the agent (service, program, config) and exit
#   --allow-delete            (or JT_RD_ALLOW_DELETE=1) let jt-ipam delete old registrations from the hbbs
#                             database: the hbbs directory becomes writable for the agent and the private key
#                             file id_ed25519 is hidden from it. Without it the directory stays read-only.
#                             jt-ipam must ALSO allow it for this server ("Allow deleting old registrations").
#
# What it sets up:
#   /opt/jt-ipam-rustdesk-agent/jt_ipam_rustdesk_agent.py   program (self-updates from jt-ipam)
#   /etc/jt-ipam-rustdesk-agent.env                          config (root only, 0600; holds the agent key)
#   jt-ipam-rustdesk-agent.service                           runs as the owner of the hbbs directory, with
#                                                            that directory mounted read-only for the agent
#                                                            (writable, minus the private key, with --allow-delete)
# Re-running upgrades the program. JT_IPAM_URL / JT_IPAM_AGENT_KEY / JT_IPAM_INSECURE / JT_IPAM_PIN / JT_IPAM_RUSTDESK_DIR that are
# not given are kept from the existing config, so on an installed host
#   curl -fsSL <jt-ipam>/api/v1/rustdesk/agent/installer.sh | sudo env JT_RD_ALLOW_DELETE=1 bash
# only switches deleting on. Each run decides it again: re-running without --allow-delete makes it read-only.
set -euo pipefail

ALLOW_DELETE=""
case "${JT_RD_ALLOW_DELETE:-}" in 1|true|yes) ALLOW_DELETE=1 ;; esac
for arg in "$@"; do
    case "$arg" in
        --allow-delete) ALLOW_DELETE=1 ;;
        --read-only) ALLOW_DELETE="" ;;
        -h|--help)
            echo "Usage: [JT_IPAM_URL=... JT_IPAM_AGENT_KEY=...] $0 [--allow-delete | --read-only]"
            echo "  Values not given are kept from ${ENVFILE:-/etc/jt-ipam-rustdesk-agent.env}."
            echo "  --allow-delete  let jt-ipam delete old registrations from the hbbs database"
            echo "  --read-only     the default: the hbbs directory stays read-only for the agent"
            exit 0 ;;
        *) echo "Unknown option: $arg (see --help)" >&2; exit 1 ;;
    esac
done

DEST=/opt/jt-ipam-rustdesk-agent
AGENT="$DEST/jt_ipam_rustdesk_agent.py"
ENVFILE=/etc/jt-ipam-rustdesk-agent.env
SVC=jt-ipam-rustdesk-agent
UNIT="/etc/systemd/system/${SVC}.service"

[[ $EUID -eq 0 ]] || { echo "Please run as root / sudo" >&2; exit 1; }

if [[ -n "${JT_IPAM_UNINSTALL:-}" ]]; then
    systemctl disable --now "${SVC}.service" 2>/dev/null || true
    rm -f "$UNIT"
    systemctl daemon-reload 2>/dev/null || true
    rm -rf "$DEST" "$ENVFILE"
    echo "Uninstalled: removed ${SVC}.service, ${DEST} and ${ENVFILE}."
    echo "RustDesk Server itself is untouched."
    exit 0
fi

# Values not given on this run are kept from the existing config (re-running only to add --allow-delete,
# or to upgrade, does not need the key again). Parsed line by line, never sourced.
# The server check is one choice: a run that passes JT_IPAM_PIN or JT_IPAM_INSECURE (even empty, as the
# "verify with system CAs" command does) replaces it, so an old pin cannot outlive a certificate change.
TRUST_GIVEN=""
[[ -n "${JT_IPAM_PIN+x}${JT_IPAM_INSECURE+x}" ]] && TRUST_GIVEN=1
if [[ -f "$ENVFILE" ]]; then
    while IFS='=' read -r k v || [[ -n "$k" ]]; do
        case "$k" in
            JT_IPAM_URL)          [[ -n "${JT_IPAM_URL:-}" ]] || JT_IPAM_URL="$v" ;;
            JT_IPAM_AGENT_KEY)    [[ -n "${JT_IPAM_AGENT_KEY:-}" ]] || JT_IPAM_AGENT_KEY="$v" ;;
            JT_IPAM_INSECURE)     [[ -n "$TRUST_GIVEN" ]] || JT_IPAM_INSECURE="$v" ;;
            JT_IPAM_PIN)          [[ -n "$TRUST_GIVEN" ]] || JT_IPAM_PIN="$v" ;;
            JT_IPAM_RUSTDESK_DIR) [[ -n "${JT_IPAM_RUSTDESK_DIR:-}" ]] || JT_IPAM_RUSTDESK_DIR="$v" ;;
        esac
    done < "$ENVFILE"
fi

: "${JT_IPAM_URL:?JT_IPAM_URL is required, e.g. https://ipam.example.com}"
: "${JT_IPAM_AGENT_KEY:?JT_IPAM_AGENT_KEY is required (shown for the RustDesk server in jt-ipam)}"
JT_IPAM_URL="${JT_IPAM_URL%/}"
JT_IPAM_PIN="${JT_IPAM_PIN:-}"
JT_IPAM_INSECURE="${JT_IPAM_INSECURE:-}"
[[ -n "$JT_IPAM_PIN" ]] && JT_IPAM_INSECURE=""
DATA_DIR="${JT_IPAM_RUSTDESK_DIR:-/var/lib/rustdesk-server}"
case "$JT_IPAM_URL" in https://*|http://*) ;; *) echo "JT_IPAM_URL must start with https://" >&2; exit 1 ;; esac

command -v systemctl >/dev/null || { echo "systemd is required" >&2; exit 1; }
if ! command -v python3 >/dev/null || ! command -v curl >/dev/null; then
    if   command -v apt-get >/dev/null; then DEBIAN_FRONTEND=noninteractive apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 curl
    elif command -v dnf     >/dev/null; then dnf install -y -q python3 curl
    elif command -v yum     >/dev/null; then yum install -y -q python3 curl
    elif command -v zypper  >/dev/null; then zypper --non-interactive --quiet install python3 curl
    fi
fi
command -v python3 >/dev/null || { echo "python3 is required, please install it" >&2; exit 1; }
command -v curl >/dev/null || { echo "curl is required, please install it" >&2; exit 1; }

# The agent runs as whoever owns the hbbs directory (the official deb: root; a hardened install: rustdesk),
# so it can read the database without loosening any permission there. systemd mounts that directory
# read-only for the agent, so it cannot change anything of RustDesk's even as the same user.
if [[ -d "$DATA_DIR" ]]; then
    RUN_USER="$(stat -c %U "$DATA_DIR")"
    RUN_GROUP="$(stat -c %G "$DATA_DIR")"
    [[ -f "$DATA_DIR/db_v2.sqlite3" ]] || echo "WARNING: ${DATA_DIR}/db_v2.sqlite3 not found yet (is hbbs running here?)" >&2
else
    echo "WARNING: ${DATA_DIR} does not exist. Is RustDesk Server installed on this host?" >&2
    echo "         Set JT_IPAM_RUSTDESK_DIR if hbbs uses another working directory. Continuing as root." >&2
    RUN_USER=root RUN_GROUP=root
fi

CURL_OPTS=(-fsSL --connect-timeout 10 --max-time 60 --retry 2)
# 釘選：curl 不看 CA 與主機名稱、只認這把公鑰（-k 之下 --pinnedpubkey 照樣檢查）
if [[ -n "$JT_IPAM_PIN" ]]; then CURL_OPTS+=(-k --pinnedpubkey "$JT_IPAM_PIN")
elif [[ -n "$JT_IPAM_INSECURE" ]]; then CURL_OPTS+=(-k); fi
echo "==> Downloading the agent from ${JT_IPAM_URL}"
install -d -m 0755 "$DEST"
TMP="$(mktemp "${DEST}/.agent.XXXXXX")"
if ! curl "${CURL_OPTS[@]}" "${JT_IPAM_URL}/api/v1/rustdesk/agent/agent.py" -o "$TMP"; then
    rm -f "$TMP"
    echo "ERROR: could not download the agent (timeout / unreachable / TLS)." >&2
    echo "  Check: curl -fsSL -m 15 ${JT_IPAM_URL}/api/v1/rustdesk/agent/agent.py -o /dev/null" >&2
    exit 1
fi
python3 -c "import ast,sys; ast.parse(open(sys.argv[1]).read())" "$TMP" || { rm -f "$TMP"; echo "ERROR: downloaded file is not the agent" >&2; exit 1; }
chmod 0755 "$TMP"
mv -f "$TMP" "$AGENT"
# self-update replaces the program in place, so the service user owns the program directory
chown -R "$RUN_USER:$RUN_GROUP" "$DEST"

echo "==> Writing ${ENVFILE}"
# The server check chosen on this run wins over what the agent learned by itself (jt-ipam-trust next to it)
[[ -n "$TRUST_GIVEN" ]] && rm -f "${DEST}/jt-ipam-trust"
( umask 077
  {
    echo "JT_IPAM_URL=${JT_IPAM_URL}"
    echo "JT_IPAM_AGENT_KEY=${JT_IPAM_AGENT_KEY}"
    echo "JT_IPAM_INSECURE=${JT_IPAM_INSECURE}"
    echo "JT_IPAM_PIN=${JT_IPAM_PIN}"
    [[ -n "${JT_IPAM_RUSTDESK_DIR:-}" ]] && echo "JT_IPAM_RUSTDESK_DIR=${JT_IPAM_RUSTDESK_DIR}"
    # the agent reports "can delete" only with this AND a writable directory (both come from this run)
    [[ -n "$ALLOW_DELETE" ]] && echo "JT_RD_ALLOW_DELETE=1"
    true
  } > "$ENVFILE" )
chown root:root "$ENVFILE"
chmod 0600 "$ENVFILE"

# The hbbs directory: read-only for the agent (default), or with --allow-delete writable (SQLite needs to create
# its journal next to the database) with the private key file made inaccessible ("-": fine if it does not exist).
RW_PATHS="${DEST}"
DATA_LINES=""
ACCESS="read-only"
if [[ -d "$DATA_DIR" ]]; then
    if [[ -n "$ALLOW_DELETE" ]]; then
        RW_PATHS="${DEST} ${DATA_DIR}"
        DATA_LINES="InaccessiblePaths=-${DATA_DIR}/id_ed25519"
        ACCESS="writable for deleting old registrations; id_ed25519 hidden"
    else
        DATA_LINES="ReadOnlyPaths=${DATA_DIR}"
    fi
elif [[ -n "$ALLOW_DELETE" ]]; then
    echo "WARNING: --allow-delete ignored: ${DATA_DIR} does not exist" >&2
    ACCESS="not found"
fi
HARDEN_NOTE="# hardening: no capabilities, read-only system, the hbbs directory read-only, only the program directory
# writable (self-update)"
if [[ "$RW_PATHS" != "$DEST" ]]; then
    HARDEN_NOTE="# hardening: no capabilities, read-only system; writable: the program directory (self-update) and,
# installed with --allow-delete, the hbbs directory (deleting old registrations), its private key inaccessible"
fi

echo "==> Creating ${SVC}.service (runs as ${RUN_USER})"
cat > "$UNIT" <<EOF
[Unit]
Description=jt-ipam RustDesk agent
Documentation=https://github.com/jasoncheng7115/jt-ipam
After=network-online.target rustdesk-hbbs.service
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
Group=${RUN_GROUP}
EnvironmentFile=${ENVFILE}
ExecStart=/usr/bin/env python3 ${AGENT}
Restart=always
RestartSec=10
${HARDEN_NOTE}
NoNewPrivileges=yes
CapabilityBoundingSet=
AmbientCapabilities=
ProtectSystem=strict
ReadWritePaths=${RW_PATHS}
${DATA_LINES}
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
ProtectClock=yes
ProtectHostname=yes
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
RestrictNamespaces=yes
RestrictRealtime=yes
RestrictSUIDSGID=yes
LockPersonality=yes
SystemCallArchitectures=native
UMask=0077

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SVC}.service" >/dev/null 2>&1 || true
systemctl restart "${SVC}.service"
sleep 2
if systemctl is-active --quiet "${SVC}.service"; then STATE="running"; else STATE="NOT running (see the log below)"; fi
if [[ "$RW_PATHS" != "$DEST" ]]; then
    DELETE_NOTE="Deleting old registrations: allowed on this host (jt-ipam must also allow it for this server).
  Re-run without --allow-delete to make the hbbs directory read-only again."
else
    DELETE_NOTE="Deleting old registrations: not allowed on this host (the hbbs directory is read-only).
  To allow it, re-run with --allow-delete (the existing config is kept)."
fi

cat <<EOF

  jt-ipam RustDesk agent installed: ${STATE}
    program : ${AGENT}
    config  : ${ENVFILE}
    reads   : ${DATA_DIR} (${ACCESS})

  Back in jt-ipam the server's agent column turns "connected" within a few seconds;
  press "Test" there to check the database, the online query and the client report receiver.

  ${DELETE_NOTE}

  RustDesk clients send their reports (host name, OS, connection audit) to TCP 21114 on this host
  when receiving is switched on in jt-ipam: allow it in this host's firewall if there is one.

  Logs:   journalctl -u ${SVC} -f
  Remove: curl -fsSL ${JT_IPAM_URL}/api/v1/rustdesk/agent/installer.sh | sudo env JT_IPAM_UNINSTALL=1 bash
EOF
