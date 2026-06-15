#!/usr/bin/env bash
# jt-ipam certificate distribution agent one-shot installer (systemd timer).
#
# Supported: Debian 11/12/13, Ubuntu 22.04/24.04/26.04, RHEL/Rocky/AlmaLinux/CentOS 8/9,
#            Fedora 38+, openSUSE Leap 15+/SLES 15+ (apt / dnf / yum / zypper auto-detected; all use systemd).
# The agent itself depends only on curl + coreutils (no Python / jq / YAML).
# Target-site profiles: nginx / apache(httpd) / caddy / traefik / lighttpd / haproxy /
#                       zoraxy / jetty / postfix / dovecot / exim4 / mosquitto /
#                       cockpit / webmin / wazuh-dashboard / Proxmox VE(pve) /
#                       Proxmox Mail Gateway(pmg) / Proxmox Backup Server(pbs) /
#                       Proxmox Datacenter Manager(pdm) / Zimbra / generic (custom paths + reload).
#
# Usage:
#   sudo JT_IPAM_URL=https://ipam.example.com JT_IPAM_AGENT_KEY=<key> ./jt-ipam-cert-agent-installer.sh
# Options:
#   JT_IPAM_INSECURE=1      set to 1 when the server cert is self-signed (writes VERIFY_TLS=false)
#   JT_IPAM_ONCALENDAR=...  custom schedule (default: daily)
#   JT_IPAM_UNINSTALL=1     remove the agent (timer, service, agent files, config and state) and exit
#
# Re-running re-downloads the latest agent and overwrites it; your config (with your deployments) is kept.
set -euo pipefail

DEST=/usr/local/lib/jt-ipam-cert-agent
CONFDIR=/etc/jt-ipam-cert-agent
CONF="$CONFDIR/config"
AGENT="$DEST/jt_ipam_cert_agent.sh"
STATEDIR=/var/lib/jt-ipam-cert-agent
SVC=jt-ipam-cert-agent

[[ $EUID -eq 0 ]] || { echo "Run as root / sudo" >&2; exit 1; }

# ── Uninstall ──
if [[ -n "${JT_IPAM_UNINSTALL:-}" ]]; then
    systemctl stop "${SVC}.timer" "${SVC}.service" 2>/dev/null || true
    systemctl disable "${SVC}.timer" 2>/dev/null || true
    rm -f "/etc/systemd/system/${SVC}.timer" "/etc/systemd/system/${SVC}.service"
    systemctl daemon-reload 2>/dev/null || true
    rm -rf "$DEST" "$CONFDIR" "$STATEDIR"
    echo "Uninstalled: removed timer/service, ${DEST}, ${CONFDIR} and ${STATEDIR}."
    echo "Note: certificate files already deployed to your services are left untouched."
    exit 0
fi

: "${JT_IPAM_URL:?JT_IPAM_URL is required, e.g. https://ipam.example.com}"
: "${JT_IPAM_AGENT_KEY:?JT_IPAM_AGENT_KEY is required (obtained when creating a cert-agent)}"
JT_IPAM_INSECURE="${JT_IPAM_INSECURE:-}"
JT_IPAM_ONCALENDAR="${JT_IPAM_ONCALENDAR:-daily}"

# ── Detect package manager (only needed to install curl if missing) ──
if   command -v apt-get >/dev/null; then PM=apt
elif command -v dnf     >/dev/null; then PM=dnf
elif command -v yum     >/dev/null; then PM=yum
elif command -v zypper  >/dev/null; then PM=zypper
else PM=""; fi

pkg_install() {
    case "$PM" in
        apt)    DEBIAN_FRONTEND=noninteractive apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$@" ;;
        dnf)    dnf install -y -q "$@" ;;
        yum)    yum install -y -q "$@" ;;
        zypper) zypper --non-interactive --quiet install "$@" ;;
        *)      return 1 ;;
    esac
}

# ── Only dependency: curl (coreutils ships on every distro) ──
command -v curl >/dev/null || pkg_install curl || { echo "curl is required, please install it manually" >&2; exit 1; }

# ── Download agent ──
install -d "$DEST" "$CONFDIR"
# Always set connect/overall timeouts so a dead IPv6 path or a firewall blackhole
# (common in LXC / containers) fails fast instead of hanging silently with no output.
CURL_OPTS=(-fsSL --connect-timeout 10 --max-time 60 --retry 2)
[[ -n "$JT_IPAM_INSECURE" ]] && CURL_OPTS+=(-k)
echo "Downloading agent from ${JT_IPAM_URL%/} …"
if ! curl "${CURL_OPTS[@]}" "${JT_IPAM_URL%/}/api/v1/cert-agents/agent.sh" -o "$AGENT"; then
    echo "ERROR: could not download the agent from ${JT_IPAM_URL%/} (timeout / unreachable)." >&2
    echo "  Check this host can reach the server: curl -fsSLk -m 15 ${JT_IPAM_URL%/}/api/v1/cert-agents/agent.sh -o /dev/null" >&2
    echo "  If it hangs on IPv6, try forcing IPv4 (add -4) or fix the container's IPv6 routing." >&2
    exit 1
fi
chmod 0755 "$AGENT"

# ── Ask the server which certificates this agent is allowed to deploy (best-effort) ──
# These are the valid values for DEPLOY_<N>_CERT. The example lines use a generic
# placeholder (example.com); replace it with one of the real names listed here.
CERTS="$(curl "${CURL_OPTS[@]}" --max-time 15 -H "X-Agent-Key: ${JT_IPAM_AGENT_KEY}" \
        "${JT_IPAM_URL%/}/api/v1/cert-agents/check?format=text" 2>/dev/null \
        | awk -F'\t' 'NF>=2{print $1}')"
EXAMPLE_CERT="example.com"
if [[ -n "$CERTS" ]]; then
    CERT_HINT="$(printf '%s\n' "$CERTS" | sed 's/^/#     /')"
else
    CERT_HINT="#     (none yet - add certificates to this agent's scope in jt-ipam, then re-run the installer)"
fi

# ── Config (create a template only if absent; never overwrite your deployments) ──
if [[ ! -f "$CONF" ]]; then
    cat > "$CONF" <<EOF
SERVER=${JT_IPAM_URL%/}
AGENT_KEY=${JT_IPAM_AGENT_KEY}
VERIFY_TLS=$([[ -n "$JT_IPAM_INSECURE" ]] && echo false || echo true)
AUTO_UPDATE=true
TLS_BASE=/etc/ssl/jt-ipam

# Each deployment is a group of DEPLOY_<N>_* lines (one setting per line). N = 1, 2, 3, ...
#
# DEPLOY_<N>_CERT must be a certificate NAME from jt-ipam (the "Name" column on the
# Certificates page). This agent is allowed to deploy these certificate(s):
${CERT_HINT}
#
# TIP: in jt-ipam, open Certificates -> Distribution agents, and click the
#      "Generate config" tool in this agent's action column to build these lines for you.
#
# ══════════════════════════════════════════════════════════════════════════════
#  QUICK MODE (preferred) - just name the certificate and the service.
#  The agent writes the cert files to the fixed paths below and reloads the
#  service. Then edit your service config (nginx/apache/...) to point at those
#  exact paths (shown per profile). Remove the leading '#' to enable.
# ══════════════════════════════════════════════════════════════════════════════
#DEPLOY_1_CERT=${EXAMPLE_CERT}
#DEPLOY_1_PROFILE=nginx
#
# Where each PROFILE writes the files (base = TLS_BASE above = /etc/ssl/jt-ipam, <cert> = DEPLOY_<N>_CERT):
#   nginx
#     cert+chain : /etc/ssl/jt-ipam/<cert>.fullchain.pem
#     key        : /etc/ssl/jt-ipam/<cert>.key
#     reload     : systemctl reload nginx
#     point nginx -> ssl_certificate     /etc/ssl/jt-ipam/<cert>.fullchain.pem;
#                    ssl_certificate_key /etc/ssl/jt-ipam/<cert>.key;
#   apache
#     cert       : /etc/ssl/jt-ipam/<cert>.crt
#     chain      : /etc/ssl/jt-ipam/<cert>.chain.pem
#     key        : /etc/ssl/jt-ipam/<cert>.key
#     reload     : systemctl reload apache2 (or httpd)
#     point apache-> SSLCertificateFile      /etc/ssl/jt-ipam/<cert>.crt
#                    SSLCertificateKeyFile   /etc/ssl/jt-ipam/<cert>.key
#                    SSLCertificateChainFile /etc/ssl/jt-ipam/<cert>.chain.pem
#   haproxy
#     combined   : /etc/ssl/jt-ipam/<cert>.pem   (cert + chain + key in one file)
#     reload     : systemctl reload haproxy      (point: bind ... ssl crt /etc/ssl/jt-ipam/<cert>.pem)
#   postfix
#     cert+chain : /etc/ssl/jt-ipam/<cert>.fullchain.pem    key: /etc/ssl/jt-ipam/<cert>.key
#     reload     : systemctl reload postfix
#   dovecot
#     cert+chain : /etc/ssl/jt-ipam/<cert>.fullchain.pem    key: /etc/ssl/jt-ipam/<cert>.key
#     reload     : systemctl reload dovecot
#   caddy -> <cert>.fullchain.pem + <cert>.key   reload: systemctl reload caddy   (point: tls .../<cert>.fullchain.pem .../<cert>.key)
#   traefik-> <cert>.fullchain.pem + <cert>.key  no reload (file provider watches; certFile/keyFile in dynamic config)
#   lighttpd-> <cert>.pem (cert+chain+key)        reload: systemctl reload lighttpd  (point: ssl.pemfile = ".../<cert>.pem")
#   zoraxy-> <cert>.crt + <cert>.key             reload: systemctl restart zoraxy   (managed via zoraxy UI)
#   jetty -> <cert>.p12 (PKCS#12 keystore)        reload: systemctl reload jetty     (point SslContextFactory KeyStorePath at it)
#   exim4 -> <cert>.fullchain.pem + <cert>.key    reload: systemctl reload exim4     (tls_certificate / tls_privatekey)
#   mosquitto-> <cert>.crt + <cert>.chain.pem + <cert>.key  reload: systemctl restart mosquitto (certfile/cafile/keyfile)
#   cockpit-> /etc/cockpit/ws-certs.d/<cert>.cert (cert+chain+key)  reload: systemctl try-restart cockpit (no config change)
#   webmin-> /etc/webmin/miniserv.pem (cert+chain+key)             reload: systemctl restart webmin (no config change)
#   pve   -> /etc/pve/local/pveproxy-ssl.pem + .key (root:www-data 640)  reload: systemctl restart pveproxy  (no config change; /etc/pve is pmxcfs so perms are fs-managed)
#   pmg   -> /etc/pmg/pmg-api.pem (root:www-data 640) + /etc/pmg/pmg-tls.pem (root:root 600)  reload: systemctl restart pmgproxy + pmgdaemon restart (no config change)
#   pbs   -> /etc/proxmox-backup/proxy.pem + .key (root:backup 640)  reload: systemctl reload/restart proxmox-backup-proxy (no config change)
#   pdm   -> /etc/proxmox-datacenter-manager/auth/api.pem + api.key (root:www-data 640)  reload: systemctl restart proxmox-datacenter-api.service (no config change; key must not be password-protected)
#   wazuh-dashboard -> /etc/wazuh-dashboard/certs/dashboard.pem + dashboard-key.pem  reload: systemctl restart wazuh-dashboard (point server.ssl.* in opensearch_dashboards.yml at these)
#   jitsi -> /root/.jitsi-meet-cfg/web/keys/cert.crt + cert.key  reload: docker restart <jitsi web container> (docker-jitsi-meet; non-root/custom CONFIG -> manual mode)
#   coturn-> /etc/coturn/certs/turn.crt + turn.key (root:65534)  reload: docker restart coturn (or systemctl coturn)
#   zimbra-> commercial cert via zmcertmgr deploycrt comm  reload: su - zimbra -c 'zmcontrol restart' (chain must include intermediate/root)
#
# ══════════════════════════════════════════════════════════════════════════════
#  MANUAL MODE - you choose exactly where each file goes (set RELOAD yourself,
#  or keep PROFILE for its reload command).
# ══════════════════════════════════════════════════════════════════════════════
#DEPLOY_1_CERT=${EXAMPLE_CERT}
#DEPLOY_1_FULLCHAIN=/etc/nginx/ssl/site.pem   # cert + chain
#DEPLOY_1_KEY=/etc/nginx/ssl/site.key         # private key
#DEPLOY_1_RELOAD=systemctl reload nginx       # reload command
# Other path fields: DEPLOY_1_CRT= (leaf cert only)  DEPLOY_1_CHAIN= (intermediate)  DEPLOY_1_COMBINED= (cert+chain+key)
# DEPLOY_1_TEST= config-test command run before reload
EOF
    chmod 0600 "$CONF"
    CONF_STATUS="created (template - edit DEPLOY_1_* to enable)"
else
    CONF_STATUS="kept (existing config left untouched)"
fi

# Compact one-line list of certs this agent can deploy (valid DEPLOY_<N>_CERT values).
if [[ -n "$CERTS" ]]; then
    CERT_LINE="$(printf '%s, ' $CERTS)"; CERT_LINE="${CERT_LINE%, }"
else
    CERT_LINE="(none yet - add certificates to this agent's scope in jt-ipam)"
fi

# ── systemd service + timer (all distros use systemd) ──
cat > "/etc/systemd/system/${SVC}.service" <<EOF
[Unit]
Description=jt-ipam certificate distribution agent
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
SyslogIdentifier=${SVC}
ExecStart=/usr/bin/env bash ${AGENT} --config ${CONF}
EOF

cat > "/etc/systemd/system/${SVC}.timer" <<EOF
[Unit]
Description=Run jt-ipam cert agent on a schedule

[Timer]
OnCalendar=${JT_IPAM_ONCALENDAR}
RandomizedDelaySec=600
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable "${SVC}.timer" >/dev/null 2>&1 || true
systemctl start "${SVC}.timer"

RUN="bash ${AGENT} --config ${CONF}"
cat <<EOF

  jt-ipam cert agent installed - runs on a ${SVC}.timer (${JT_IPAM_ONCALENDAR}).

  Next: edit your deployments in ${CONF}
        config: ${CONF_STATUS}
        certs you can deploy: ${CERT_LINE}

    test:   ${RUN} --dry-run
    apply:  ${RUN}

  Logs: journalctl -u ${SVC}.service -f   |   Help: bash ${AGENT} --help
EOF
