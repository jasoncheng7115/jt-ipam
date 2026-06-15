#!/usr/bin/env bash
# jt-ipam certificate distribution agent (pure bash; depends only on curl + coreutils, no Python / jq / YAML).
#
# Periodically (or once) asks jt-ipam "is there a newer version of the certificates I'm responsible for".
# If so, downloads each raw PEM part via curl, writes them to the site atomically, reloads only after
# config-test passes, rolls back automatically on failure, then reports the result back to jt-ipam.
#
# Config file (default /etc/jt-ipam-cert-agent/config, KEY=VALUE, one setting per line, sourced here):
#
#   SERVER=https://ipam.example.com
#   AGENT_KEY=<enrollment key>
#   VERIFY_TLS=true            # set false if the server cert is self-signed, or use CA_CERT
#   # CA_CERT=/etc/ssl/certs/ipam-ca.pem
#   AUTO_UPDATE=true           # auto-update self when the server has a newer agent
#   TLS_BASE=/etc/ssl/jt-ipam  # default write directory for built-in profiles
#
#   # Each deployment is a group of DEPLOY_<N>_* lines (one setting per line). N = 1, 2, 3, ...
#   #
#   # QUICK MODE (preferred): name the cert + service; the agent writes fixed paths and reloads.
#   #   DEPLOY_1_CERT=wildcard-example-com
#   #   DEPLOY_1_PROFILE=nginx
#   #   Where each profile writes (base = TLS_BASE, <cert> = DEPLOY_<N>_CERT):
#   #     nginx    cert+chain <base>/<cert>.fullchain.pem  key <base>/<cert>.key   reload: systemctl reload nginx
#   #     apache   cert <base>/<cert>.crt  chain .chain.pem  key .key              reload: systemctl reload apache2||httpd
#   #     haproxy  combined <base>/<cert>.pem (cert+chain+key)                     reload: systemctl reload haproxy
#   #     postfix  cert+chain <base>/<cert>.fullchain.pem  key .key                reload: systemctl reload postfix
#   #     dovecot  cert+chain <base>/<cert>.fullchain.pem  key .key                reload: systemctl reload dovecot
#   #     pve      /etc/pve/local/pveproxy-ssl.pem + .key (root:www-data 640)  reload: systemctl restart pveproxy
#   #     pmg      /etc/pmg/pmg-api.pem (root:www-data 640) + pmg-tls.pem (root:root 600)  reload: systemctl restart pmgproxy + pmgdaemon restart
#   #     pbs      /etc/proxmox-backup/proxy.pem + .key (root:backup 640)  reload: systemctl reload|restart proxmox-backup-proxy
#   #     pdm      /etc/proxmox-datacenter-manager/auth/api.pem + api.key (root:www-data 640)  reload: systemctl restart proxmox-datacenter-api.service
#   #     wazuh-dashboard  /etc/wazuh-dashboard/certs/dashboard.pem + dashboard-key.pem  reload: systemctl restart wazuh-dashboard
#   #     zimbra   commercial cert via zmcertmgr deploycrt comm + zmcontrol restart (needs intermediate/root in chain)
#   #   Then point your service config at the path(s) above.
#   #
#   # MANUAL MODE: choose exactly where each file goes (set RELOAD yourself or keep PROFILE for its reload):
#   #   DEPLOY_1_CERT=wildcard-example-com
#   #   DEPLOY_1_FULLCHAIN=/etc/nginx/ssl/site.pem  DEPLOY_1_KEY=/etc/nginx/ssl/site.key  DEPLOY_1_RELOAD=systemctl reload nginx
#   #   Other path fields: DEPLOY_1_CRT= (leaf)  DEPLOY_1_CHAIN=  DEPLOY_1_COMBINED=  DEPLOY_1_TEST=
#
# Usage: jt_ipam_cert_agent.sh [--config PATH] [--dry-run] [--force] [--debug] [--upgrade] [--version]
AGENT_VERSION=0.4.169

set -u
SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
ARGS=("$@")
CONFIG=/etc/jt-ipam-cert-agent/config
STATE_DIR=/var/lib/jt-ipam-cert-agent
STATE_FILE="$STATE_DIR/state"
DRY_RUN=0
FORCE=0
UPGRADE_ONLY=0
DEBUG=0

log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
dbg() { [ "$DEBUG" = 1 ] && log "[debug] $*"; return 0; }
# Run a command string (may contain shell operators); show its output only in --debug, else discard.
run_q() {
  if [ "$DEBUG" = 1 ]; then log "[debug] \$ $1"; eval "$1"; else eval "$1" >/dev/null 2>&1; fi
}
# Run a command (argv form); show its output only in --debug, else discard.
run_qv() {
  if [ "$DEBUG" = 1 ]; then log "[debug] \$ $*"; "$@"; else "$@" >/dev/null 2>&1; fi
}

usage() {
  cat <<EOF
jt-ipam certificate distribution agent v$AGENT_VERSION

Usage: jt_ipam_cert_agent.sh [options]

Options:
  --config PATH   Config file (default: /etc/jt-ipam-cert-agent/config)
  --dry-run       Show what would be written / reloaded; make no changes
  --force         Re-deploy even if the certificate is already up to date
  --debug         Verbose: print each command and show its full output
                  (config-test, reload, zmcertmgr, downloads). Default off.
  --upgrade       Update this agent to the server's latest version, then exit
                  (works even if AUTO_UPDATE=false in the config)
  --version       Print the agent version and exit
  -h, --help      Show this help and exit

For each DEPLOY_<N>_* in the config the agent fetches that certificate's
current version and writes it to the target paths, reloading the service only
after a successful config-test (auto-rollback on failure).
Scheduled-run logs: journalctl -u jt-ipam-cert-agent.service
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --config) CONFIG="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --force) FORCE=1; shift ;;
    --debug) DEBUG=1; shift ;;
    --upgrade) UPGRADE_ONLY=1; shift ;;
    --version) echo "$AGENT_VERSION"; exit 0 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown arg: $1" >&2; usage >&2; exit 2 ;;
  esac
done

command -v curl >/dev/null 2>&1 || { echo "curl is required" >&2; exit 2; }
[ -r "$CONFIG" ] || { echo "cannot read config file $CONFIG" >&2; exit 2; }
# shellcheck disable=SC1090
. "$CONFIG"
: "${SERVER:?config missing SERVER}"
: "${AGENT_KEY:?config missing AGENT_KEY}"
VERIFY_TLS="${VERIFY_TLS:-true}"
AUTO_UPDATE="${AUTO_UPDATE:-true}"
TLS_BASE="${TLS_BASE:-/etc/ssl/jt-ipam}"
SERVER="${SERVER%/}"
API="$SERVER/api/v1/cert-agents"

CURL=(curl -fsS --max-time 30 -H "X-Agent-Key: $AGENT_KEY" -H "X-Agent-Version: $AGENT_VERSION")
[ "$VERIFY_TLS" = "false" ] && CURL+=(-k)
[ -n "${CA_CERT:-}" ] && CURL+=(--cacert "$CA_CERT")

# ─────────────────── Per-profile file list + test + reload ───────────────────
# Outputs to globals: PFILES (each line "kind|path|mode"), PTEST, PRELOAD, PSPECIAL
profile_spec() {
  local profile="$1" cert="$2" base="$3"
  PFILES=""; PTEST=""; PRELOAD=""; PSPECIAL=""
  case "$profile" in
    nginx)
      PFILES="fullchain|$base/$cert.fullchain.pem|644
key|$base/$cert.key|600"
      PTEST="nginx -t"; PRELOAD="systemctl reload nginx" ;;
    apache)
      PFILES="cert|$base/$cert.crt|644
chain|$base/$cert.chain.pem|644
key|$base/$cert.key|600"
      PTEST="apachectl configtest 2>/dev/null || httpd -t"
      PRELOAD="systemctl reload apache2 2>/dev/null || systemctl reload httpd" ;;
    haproxy)
      PFILES="combined|$base/$cert.pem|600"
      PTEST="haproxy -c -f /etc/haproxy/haproxy.cfg"; PRELOAD="systemctl reload haproxy" ;;
    pve)
      # /etc/pve is pmxcfs (FUSE): chmod/chown are not permitted but the fs already
      # serves these files as root:www-data 0640, so the requested owner/mode are a
      # best-effort no-op there (and correct on any non-pmxcfs path).
      PFILES="fullchain|/etc/pve/local/pveproxy-ssl.pem|640|root:www-data
key|/etc/pve/local/pveproxy-ssl.key|640|root:www-data"
      PRELOAD="systemctl restart pveproxy" ;;
    pmg)
      # pmgproxy runs as www-data and reads pmg-api.pem; pmg-tls.pem (postfix) stays root:root 0600.
      PFILES="combined|/etc/pmg/pmg-api.pem|640|root:www-data
combined|/etc/pmg/pmg-tls.pem|600|root:root"
      PRELOAD="systemctl restart pmgproxy; command -v pmgdaemon >/dev/null 2>&1 && pmgdaemon restart || true" ;;
    pbs)
      # proxmox-backup-proxy runs as the 'backup' user and must be able to read proxy.key.
      PFILES="fullchain|/etc/proxmox-backup/proxy.pem|640|root:backup
key|/etc/proxmox-backup/proxy.key|640|root:backup"
      PRELOAD="systemctl reload proxmox-backup-proxy 2>/dev/null || systemctl restart proxmox-backup-proxy" ;;
    pdm)
      # Proxmox Datacenter Manager (official: cert+chain -> auth/api.pem, key -> auth/api.key,
      # root:www-data; the API daemon runs as www-data on :8443). Key must NOT be password-protected.
      PFILES="fullchain|/etc/proxmox-datacenter-manager/auth/api.pem|640|root:www-data
key|/etc/proxmox-datacenter-manager/auth/api.key|640|root:www-data"
      PRELOAD="systemctl restart proxmox-datacenter-api.service" ;;
    wazuh-dashboard)
      # Wazuh dashboard (OpenSearch Dashboards). opensearch_dashboards.yml must point
      # server.ssl.certificate / server.ssl.key at these files (the Wazuh default paths).
      PFILES="fullchain|/etc/wazuh-dashboard/certs/dashboard.pem|640|wazuh-dashboard:wazuh-dashboard
key|/etc/wazuh-dashboard/certs/dashboard-key.pem|640|wazuh-dashboard:wazuh-dashboard"
      PRELOAD="systemctl restart wazuh-dashboard" ;;
    postfix)
      PFILES="fullchain|$base/$cert.fullchain.pem|644
key|$base/$cert.key|600"
      PRELOAD="systemctl reload postfix" ;;
    dovecot)
      PFILES="fullchain|$base/$cert.fullchain.pem|644
key|$base/$cert.key|600"
      PRELOAD="systemctl reload dovecot" ;;
    caddy)
      PFILES="fullchain|$base/$cert.fullchain.pem|644
key|$base/$cert.key|600"
      PRELOAD="systemctl reload caddy" ;;
    traefik)
      # Traefik file provider watches the files; no reload needed.
      PFILES="fullchain|$base/$cert.fullchain.pem|644
key|$base/$cert.key|600" ;;
    lighttpd)
      PFILES="combined|$base/$cert.pem|600"
      PTEST="lighttpd -tt -f /etc/lighttpd/lighttpd.conf"; PRELOAD="systemctl reload lighttpd" ;;
    exim4)
      PFILES="fullchain|$base/$cert.fullchain.pem|644
key|$base/$cert.key|640"
      PRELOAD="systemctl reload exim4 2>/dev/null || systemctl reload exim" ;;
    mosquitto)
      PFILES="cert|$base/$cert.crt|644
chain|$base/$cert.chain.pem|644
key|$base/$cert.key|600"
      PRELOAD="systemctl restart mosquitto" ;;
    cockpit)
      PFILES="combined|/etc/cockpit/ws-certs.d/$cert.cert|640"
      PRELOAD="systemctl try-restart cockpit.service 2>/dev/null || true" ;;
    webmin)
      PFILES="combined|/etc/webmin/miniserv.pem|600"
      PRELOAD="systemctl restart webmin" ;;
    zoraxy)
      # Zoraxy hot-reloads certs from disk; restart only if needed.
      PFILES="fullchain|$base/$cert.crt|644
key|$base/$cert.key|600"
      PRELOAD="systemctl restart zoraxy 2>/dev/null || true" ;;
    jetty)
      # Jetty uses a PKCS#12 keystore (no password); point jetty's SslContextFactory at it.
      PFILES="pkcs12|$base/$cert.p12|640"
      PRELOAD="systemctl reload jetty 2>/dev/null || systemctl restart jetty" ;;
    zimbra)
      PSPECIAL="zimbra" ;;
    generic)
      : ;;  # overridden entirely via config
    *)
      return 1 ;;
  esac
  return 0
}

# Download one raw PEM part to a file; return 0 on success
fetch_part() {
  local cert="$1" part="$2" dest="$3"
  dbg "fetch $part of '$cert' -> $dest"
  if [ "$DEBUG" = 1 ]; then
    "${CURL[@]}" -G --data-urlencode "cert=$cert" --data-urlencode "part=$part" -o "$dest" "$API/bundle/raw"
    local rc=$?; dbg "fetch $part rc=$rc bytes=$(wc -c <"$dest" 2>/dev/null || echo 0)"; return $rc
  fi
  "${CURL[@]}" -G --data-urlencode "cert=$cert" --data-urlencode "part=$part" \
    -o "$dest" "$API/bundle/raw" 2>/dev/null
}

# ─────────────────── Apply a single deployment ───────────────────
# Global REPORT_LINES accumulates TSV: cert\tprofile\tstatus\tfingerprint\tnot_after\tdry_run\tmessage
REPORT_LINES=()
add_report() {  # cert profile status fp not_after message
  local msg="${6:-}"
  msg="${msg//$'\t'/ }"; msg="${msg//$'\n'/ }"
  REPORT_LINES+=("$(printf '%s\t%s\t%s\t%s\t%s\t%s\t%s' "$1" "$2" "$3" "$4" "$5" "$DRY_RUN" "$msg")")
}

# Zimbra deploys commercial certs through zmcertmgr (not a plain file copy):
#   stage the key, verifycrt comm, deploycrt comm, then 'zmcontrol restart' as the zimbra user.
# The certificate's chain must contain the intermediate (and root) CA for zmcertmgr to verify.
deploy_zimbra() {  # cert fp not_after
  local cert="$1" fp="$2" na="$3"
  local zmcert=/opt/zimbra/bin/zmcertmgr
  local zssl=/opt/zimbra/ssl/zimbra/commercial
  if [ ! -x "$zmcert" ]; then
    log "[$cert/zimbra] $zmcert not found — is this a Zimbra host?"
    add_report "$cert" zimbra failed "$fp" "$na" "zmcertmgr not found"; return 1
  fi
  if [ "$DRY_RUN" = 1 ]; then
    log "[$cert/zimbra] (dry-run) would deploy the commercial cert via zmcertmgr:"
    log "    key  -> $zssl/commercial.key (zimbra:zimbra 600)"
    log "    verify+deploy: $zmcert verifycrt comm <key> <crt> <ca_chain> && $zmcert deploycrt comm <crt> <ca_chain>"
    log "    restart: su - zimbra -c 'zmcontrol restart'"
    add_report "$cert" zimbra dry-run "$fp" "$na" "planned"; return 0
  fi
  local tmpd; tmpd="$(mktemp -d)"
  if ! fetch_part "$cert" cert "$tmpd/commercial.crt" \
     || ! fetch_part "$cert" chain "$tmpd/commercial_ca.crt" \
     || ! fetch_part "$cert" key "$tmpd/commercial.key"; then
    log "[$cert/zimbra] download failed"; rm -rf "$tmpd"
    add_report "$cert" zimbra failed "$fp" "$na" "download failed"; return 1
  fi
  # Zimbra wants the private key staged at the commercial path before verify/deploy.
  mkdir -p "$zssl"
  cp -f "$tmpd/commercial.key" "$zssl/commercial.key"
  chown zimbra:zimbra "$zssl/commercial.key" 2>/dev/null || true
  chmod 600 "$zssl/commercial.key" 2>/dev/null || true
  if ! run_qv "$zmcert" verifycrt comm "$zssl/commercial.key" "$tmpd/commercial.crt" "$tmpd/commercial_ca.crt"; then
    log "[$cert/zimbra] zmcertmgr verifycrt failed (key/cert mismatch, or the chain is missing the intermediate/root CA) — re-run with --debug to see zmcertmgr's message"
    rm -rf "$tmpd"; add_report "$cert" zimbra failed "$fp" "$na" "verifycrt failed"; return 1
  fi
  if ! run_qv "$zmcert" deploycrt comm "$tmpd/commercial.crt" "$tmpd/commercial_ca.crt"; then
    log "[$cert/zimbra] zmcertmgr deploycrt failed"
    rm -rf "$tmpd"; add_report "$cert" zimbra failed "$fp" "$na" "deploycrt failed"; return 1
  fi
  rm -rf "$tmpd"
  # zmcontrol restart is heavy but is the documented way to load a new cert; the
  # fingerprint guard means this only runs when the certificate actually changed.
  if ! run_q "su - zimbra -c 'zmcontrol restart'"; then
    log "[$cert/zimbra] deployed, but 'zmcontrol restart' failed — restart Zimbra manually to load the new cert"
    add_report "$cert" zimbra ok "$fp" "$na" "deployed (manual restart needed)"; return 0
  fi
  log "[$cert/zimbra] applied (${fp:0:12}…)"
  add_report "$cert" zimbra ok "$fp" "$na" "applied"; return 0
}

apply_deployment() {  # cert profile fp not_after  (+ D_* override vars)
  local cert="$1" profile="$2" fp="$3" na="$4"
  local base="$TLS_BASE"
  if ! profile_spec "$profile" "$cert" "$base"; then
    log "[$cert/$profile] unknown profile"; add_report "$cert" "$profile" failed "$fp" "$na" "unknown profile"; return 1
  fi
  # Special deployment handlers that don't fit the file-copy model.
  if [ "${PSPECIAL:-}" = zimbra ]; then deploy_zimbra "$cert" "$fp" "$na"; return $?; fi

  # config overrides: reload / test / <kind>_path
  [ -n "${D_reload:-}" ] && PRELOAD="$D_reload"
  [ -n "${D_test:-}" ] && PTEST="$D_test"
  local files="$PFILES" line kind path mode owner
  # override each kind's path (generic relies on these too); preserve the owner column
  local newfiles="" ov
  if [ -n "$files" ]; then
    while IFS='|' read -r kind path mode owner; do
      [ -z "$kind" ] && continue
      ov="D_${kind}_path"
      [ -n "${!ov:-}" ] && path="${!ov}"
      newfiles+="$kind|$path|$mode|$owner"$'\n'
    done <<< "$files"
  fi
  # generic / overrides: add kinds not in the profile defaults (no owner override)
  for kind in cert key chain fullchain combined pkcs12; do
    ov="D_${kind}_path"
    if [ -n "${!ov:-}" ] && ! printf '%s' "$newfiles" | grep -q "^$kind|"; then
      local m=644; { [ "$kind" = key ] || [ "$kind" = combined ] || [ "$kind" = pkcs12 ]; } && m=600
      newfiles+="$kind|${!ov}|$m|"$'\n'
    fi
  done
  files="$(printf '%s' "$newfiles")"

  if [ "$profile" = generic ] && [ -z "$files" ]; then
    log "[$cert/$profile] generic specifies no *_path"; add_report "$cert" "$profile" failed "$fp" "$na" "generic: no path specified"; return 1
  fi

  # dry-run: just print the plan
  if [ "$DRY_RUN" = 1 ]; then
    log "[$cert/$profile] (dry-run) would write:"
    while IFS='|' read -r kind path mode owner; do [ -n "$kind" ] && log "    $kind -> $path ($mode${owner:+, $owner})"; done <<< "$files"
    [ -n "$PRELOAD" ] && log "    reload: $PRELOAD"
    add_report "$cert" "$profile" dry-run "$fp" "$na" "planned"
    return 0
  fi

  # Download + install (atomic swap + backup, restore on failure)
  local tmpd; tmpd="$(mktemp -d)"; local -a written=() backed=()
  rollback() {
    local p
    for p in "${written[@]}"; do rm -f "$p"; done
    for p in "${backed[@]}"; do mv -f "$p.jtbak" "$p" 2>/dev/null; done
  }
  while IFS='|' read -r kind path mode owner; do
    [ -z "$kind" ] && continue
    if ! fetch_part "$cert" "$kind" "$tmpd/f"; then
      log "[$cert/$profile] download $kind failed"; rollback; rm -rf "$tmpd"
      add_report "$cert" "$profile" failed "$fp" "$na" "download $kind failed"; return 1
    fi
    mkdir -p "$(dirname "$path")"
    if [ -e "$path" ]; then cp -p "$path" "$path.jtbak"; backed+=("$path"); else written+=("$path"); fi
    # Write content into place, then set mode/owner best-effort. Proxmox pmxcfs (/etc/pve)
    # forbids chmod/chown ("Operation not permitted") — the content still lands and the
    # filesystem manages the permissions itself, so we don't treat that as a failure.
    dbg "write $kind -> $path (mode $mode${owner:+ owner $owner})"
    cp -f "$tmpd/f" "$path.jtnew" && mv -f "$path.jtnew" "$path"
    chmod "$mode" "$path" 2>/dev/null \
      || log "[$cert/$profile] note: kept filesystem-managed permissions for $path (chmod not permitted — normal on Proxmox /etc/pve)"
    [ -n "$owner" ] && { chown "$owner" "$path" 2>/dev/null \
      || log "[$cert/$profile] note: kept filesystem-managed owner for $path (chown not permitted — normal on Proxmox /etc/pve)"; }
  done <<< "$files"
  rm -rf "$tmpd"

  # config-test
  if [ -n "$PTEST" ] && ! run_q "$PTEST"; then
    log "[$cert/$profile] config-test failed -> rollback"; rollback
    add_report "$cert" "$profile" failed "$fp" "$na" "config-test failed"; return 1
  fi
  # reload
  if [ -n "$PRELOAD" ] && ! run_q "$PRELOAD"; then
    log "[$cert/$profile] reload failed -> rollback"; rollback
    add_report "$cert" "$profile" failed "$fp" "$na" "reload failed"; return 1
  fi
  # success -> drop backups
  local p; for p in "${backed[@]}"; do rm -f "$p.jtbak"; done
  log "[$cert/$profile] applied (${fp:0:12}…)"
  add_report "$cert" "$profile" ok "$fp" "$na" "applied"
  return 0
}

# ─────────────────── Self-update ───────────────────
# self_update <server_sha> [force]  — force=1 ignores AUTO_UPDATE=false (used by --upgrade).
self_update() {
  local server_sha="$1" force="${2:-0}"
  [ "$force" != 1 ] && [ "$AUTO_UPDATE" = "false" ] && return 0
  [ -z "$server_sha" ] && return 0
  command -v sha256sum >/dev/null 2>&1 || return 0
  local self_sha; self_sha="$(sha256sum "$SELF" | cut -d' ' -f1)"
  [ "$server_sha" = "$self_sha" ] && return 0
  log "[update] server has a newer agent, downloading update…"
  local tmp="$SELF.new"
  if "${CURL[@]}" -o "$tmp" "$API/agent.sh" 2>/dev/null; then
    local new_sha; new_sha="$(sha256sum "$tmp" | cut -d' ' -f1)"
    if [ "$new_sha" = "$server_sha" ]; then
      chmod 0755 "$tmp"; mv -f "$tmp" "$SELF"
      log "[update] updated, re-executing new version"
      exec "$SELF" "${ARGS[@]}"
    fi
    log "[update] downloaded sha mismatch, skipping this round"; rm -f "$tmp"
  fi
}

# ─────────────────── Main flow ───────────────────
mkdir -p "$STATE_DIR"; touch "$STATE_FILE"
CHECK="$(mktemp)"
if ! "${CURL[@]}" -o "$CHECK" "$API/check?format=text" 2>/dev/null; then
  log "check failed (cannot reach $SERVER)"; rm -f "$CHECK"; exit 1
fi
SERVER_SHA="$(awk -F= '/^agent_sha=/{print $2}' "$CHECK")"
self_update "$SERVER_SHA" "$UPGRADE_ONLY"   # --upgrade forces even when AUTO_UPDATE=false
if [ "$UPGRADE_ONLY" = 1 ]; then
  # If an update was available, self_update already re-exec'd; reaching here means latest.
  log "[upgrade] agent is already at the latest version (v$AGENT_VERSION)"
  rm -f "$CHECK"; exit 0
fi

state_fp() { awk -F'\t' -v c="$1" -v p="$2" '$1==c && $2==p{print $3}' "$STATE_FILE"; }
set_state() {  # cert profile fp
  local tmp; tmp="$(mktemp)"
  awk -F'\t' -v c="$1" -v p="$2" '!($1==c && $2==p)' "$STATE_FILE" > "$tmp"
  printf '%s\t%s\t%s\n' "$1" "$2" "$3" >> "$tmp"; mv -f "$tmp" "$STATE_FILE"
}

# Read one config field for deployment N, e.g. _f 1 FULLCHAIN -> value of DEPLOY_1_FULLCHAIN
_f() { local v="DEPLOY_${1}_${2}"; printf '%s' "${!v:-}"; }

FAILED=0; n=1
while :; do
  cert="$(_f "$n" CERT)"
  [ -z "$cert" ] && break
  profile="$(_f "$n" PROFILE)"; profile="${profile:-generic}"
  # Map the one-per-line path / reload / test fields to the D_* overrides used by apply_deployment.
  unset "${!D_@}" 2>/dev/null || true
  [ -n "$(_f "$n" FULLCHAIN)" ] && D_fullchain_path="$(_f "$n" FULLCHAIN)"
  [ -n "$(_f "$n" KEY)" ]       && D_key_path="$(_f "$n" KEY)"
  [ -n "$(_f "$n" CHAIN)" ]     && D_chain_path="$(_f "$n" CHAIN)"
  [ -n "$(_f "$n" CRT)" ]       && D_cert_path="$(_f "$n" CRT)"
  [ -n "$(_f "$n" COMBINED)" ]  && D_combined_path="$(_f "$n" COMBINED)"
  [ -n "$(_f "$n" RELOAD)" ]    && D_reload="$(_f "$n" RELOAD)"
  [ -n "$(_f "$n" TEST)" ]      && D_test="$(_f "$n" TEST)"
  n=$((n+1))
  # look up this cert's current fingerprint from the check output
  fp="$(awk -F'\t' -v c="$cert" '$1==c{print $2}' "$CHECK")"
  na="$(awk -F'\t' -v c="$cert" '$1==c{print $3}' "$CHECK")"
  if [ -z "$fp" ]; then
    log "[$cert/$profile] cert not found on server or not in scope, skipping"
    add_report "$cert" "$profile" skipped "" "" "not in scope / no current version"; continue
  fi
  if [ "$DRY_RUN" != 1 ] && [ "$FORCE" != 1 ] && [ "$(state_fp "$cert" "$profile")" = "$fp" ]; then
    # Already applied locally — skip the file write/reload, but STILL report the
    # current state so the server reflects this deployment (e.g. after re-keying an
    # agent, whose local state matches but the new agent row has never reported).
    # --force overrides this and re-writes anyway.
    log "[$cert/$profile] already up to date (${fp:0:12}…), skipping write"
    add_report "$cert" "$profile" ok "$fp" "$na" "already up to date"; continue
  fi
  if apply_deployment "$cert" "$profile" "$fp" "$na"; then
    [ "$DRY_RUN" != 1 ] && set_state "$cert" "$profile" "$fp"
  else
    FAILED=1
  fi
done
rm -f "$CHECK"

# report (TSV; does not affect deployment result)
if [ "${#REPORT_LINES[@]}" -gt 0 ]; then
  printf '%s\n' "${REPORT_LINES[@]}" | \
    "${CURL[@]}" -X POST -H "Content-Type: text/plain" --data-binary @- "$API/report" >/dev/null 2>&1 \
    || log "report failed (does not affect deployment)"
fi
exit "$FAILED"
