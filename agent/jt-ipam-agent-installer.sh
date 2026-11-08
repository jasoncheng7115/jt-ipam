#!/usr/bin/env bash
# jt-ipam scan agent one-line installer (systemd, no Docker).
#
# Usage:
#   sudo JT_IPAM_URL=https://ipam.example.com JT_IPAM_AGENT_KEY=<key> ./jt-ipam-agent-installer.sh
# Optional:
#   JT_IPAM_PIN=sha256//<base64>   the jt-ipam server's public key (the install command shown in jt-ipam sets
#                          it): the agent talks only to that server, also with a self-signed certificate
#   JT_IPAM_INTERVAL=300   JT_IPAM_INSECURE=1   (skip TLS verification entirely; not recommended, use JT_IPAM_PIN)
#   JT_IPAM_DHCPD_CONF=... JT_IPAM_DHCPD_LEASES=...  (only on an isc-dhcp-server host whose files are
#   not in the usual places; used when an "ISC DHCP" source in jt-ipam points at this agent)
#   Console relay is switched on and configured in the jt-ipam web UI; nothing to pass here. Optional
#   local limits for the host owner: JT_IPAM_RELAY=0 (refuse all relaying), JT_IPAM_RELAY_PORTS=22,...,
#   JT_IPAM_RELAY_MAX=2, JT_IPAM_RELAY_CIDRS=<cidr,...>. Re-running keeps the ones already in the env file.
#
# Re-running this installer re-downloads the latest agent and overwrites the old one; settings not given are
# kept from /etc/jt-ipam-agent.env (so the key is not needed again on an installed host).
# The agent also auto-updates itself when the server has a newer version.
set -euo pipefail

DEST=/opt/jt-ipam-agent
SVC=jt-ipam-scan-agent
ENVFILE=/etc/jt-ipam-agent.env

# Values not given on this run are kept from the existing env file, so on an installed host re-running the
# installer only to upgrade or to change the server check needs neither the key nor the other settings again
# (a re-install must not silently drop a limit the host owner set, either). Read line by line, never sourced.
# The server check is one choice: a run that passes JT_IPAM_PIN or JT_IPAM_INSECURE (even empty, as the
# "verify with system CAs" command does) replaces both, so an old pin cannot outlive a certificate change.
TRUST_GIVEN=""
[[ -n "${JT_IPAM_PIN+x}${JT_IPAM_INSECURE+x}" ]] && TRUST_GIVEN=1
_KEEP=(JT_IPAM_URL JT_IPAM_AGENT_KEY JT_IPAM_INTERVAL JT_IPAM_DHCPD_CONF JT_IPAM_DHCPD_LEASES
       JT_IPAM_RELAY JT_IPAM_RELAY_PORTS JT_IPAM_RELAY_MAX JT_IPAM_RELAY_CIDRS)
[[ -z "$TRUST_GIVEN" ]] && _KEEP+=(JT_IPAM_INSECURE JT_IPAM_PIN)
for _v in "${_KEEP[@]}"; do
  if [[ -z "${!_v:-}" && -f "$ENVFILE" ]]; then
    _old="$(grep -E "^${_v}=" "$ENVFILE" | tail -1 | cut -d= -f2- || true)"
    [[ -n "$_old" ]] && printf -v "$_v" '%s' "$_old"
  fi
done

: "${JT_IPAM_URL:?JT_IPAM_URL is required, e.g. https://ipam.example.com}"
: "${JT_IPAM_AGENT_KEY:?JT_IPAM_AGENT_KEY is required (get it when creating an agent in jt-ipam)}"
JT_IPAM_URL="${JT_IPAM_URL%/}"
JT_IPAM_INTERVAL="${JT_IPAM_INTERVAL:-300}"
JT_IPAM_PIN="${JT_IPAM_PIN:-}"
JT_IPAM_INSECURE="${JT_IPAM_INSECURE:-}"
[[ -n "$JT_IPAM_PIN" ]] && JT_IPAM_INSECURE=""
JT_IPAM_DHCPD_CONF="${JT_IPAM_DHCPD_CONF:-}"
JT_IPAM_DHCPD_LEASES="${JT_IPAM_DHCPD_LEASES:-}"

if [[ $EUID -ne 0 ]]; then echo "Please run as root / sudo" >&2; exit 1; fi
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
command -v ping >/dev/null || { echo "ping is required (iputils-ping)" >&2; exit 1; }

# Base tools this installer and the agent rely on: curl (downloads the agent below),
# git and sudo (commonly needed on minimal images). Best-effort.
# Probe tooling — unlocks extra probes the agent can run (capability auto-detected):
#   nmap → OS detection | samba-common-bin → NetBIOS (nmblookup) | avahi-utils → mDNS (avahi-resolve)
#   snmp (net-snmp client tools: snmpget / snmpbulkwalk) → SNMP collection from the network devices the
#   jt-ipam server assigns to this agent (read-only; credentials come from the server, never stored on disk)
# NOTE: avahi-utils pulls in avahi-daemon, a resident service that listens on UDP 5353 and
# announces this host over mDNS. It is installed by default here so mDNS name resolution works;
# set JT_IPAM_NO_MDNS=1 to leave it out. JT_IPAM_SKIP_PROBE_TOOLS=1 skips all probe tools (not base).
# Best-effort: apt-get (Debian/Ubuntu); dnf / apk get the probe tools only; skipped offline (install manually).
if command -v apt-get >/dev/null; then
  DEBIAN_FRONTEND=noninteractive apt-get update -qq || true
  echo "==> Installing base tools (curl git sudo)…"
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl git sudo || \
    echo "    (some base tools failed to install)"
  if [[ -z "${JT_IPAM_SKIP_PROBE_TOOLS:-}" ]]; then
    PROBE_PKGS=(nmap samba-common-bin snmp)
    [[ -z "${JT_IPAM_NO_MDNS:-}" ]] && PROBE_PKGS+=(avahi-utils)   # avahi-utils → avahi-daemon (UDP 5353)
    echo "==> Installing probe tools (${PROBE_PKGS[*]})…"
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${PROBE_PKGS[@]}" || \
      echo "    (some probe tools failed to install; NetBIOS/OS/mDNS/SNMP may stay unavailable)"
  fi
elif [[ -z "${JT_IPAM_SKIP_PROBE_TOOLS:-}" ]] && command -v dnf >/dev/null; then
  # RHEL / Rocky / Alma / Fedora: same tools under their names (best-effort)
  PROBE_PKGS=(nmap samba-common-tools net-snmp-utils)
  echo "==> Installing probe tools (${PROBE_PKGS[*]})…"
  dnf install -y -q "${PROBE_PKGS[@]}" || \
    echo "    (some probe tools failed to install; NetBIOS/OS/SNMP may stay unavailable)"
elif [[ -z "${JT_IPAM_SKIP_PROBE_TOOLS:-}" ]] && command -v apk >/dev/null; then
  # Alpine
  PROBE_PKGS=(nmap samba-common-tools net-snmp-tools)
  echo "==> Installing probe tools (${PROBE_PKGS[*]})…"
  apk add --no-cache "${PROBE_PKGS[@]}" || \
    echo "    (some probe tools failed to install; NetBIOS/OS/SNMP may stay unavailable)"
fi

echo "==> Installing agent program to ${DEST}"
mkdir -p "$DEST"
CURL_TLS=()
# 釘選：curl 不看 CA 與主機名稱、只認這把公鑰（-k 之下 --pinnedpubkey 照樣檢查）
if [[ -n "$JT_IPAM_PIN" ]]; then CURL_TLS=(-k --pinnedpubkey "$JT_IPAM_PIN")
elif [[ -n "$JT_IPAM_INSECURE" ]]; then CURL_TLS=(-k); fi
if [[ -f "$(dirname "$0")/jt_ipam_agent.py" ]]; then
  # installer shipped alongside the agent (offline install)
  install -m 0755 "$(dirname "$0")/jt_ipam_agent.py" "$DEST/jt_ipam_agent.py"
else
  # curl | bash mode: download the latest agent from the server (overwrites existing)
  echo "    downloading agent from ${JT_IPAM_URL}"
  curl -fsSL ${CURL_TLS[@]+"${CURL_TLS[@]}"} "${JT_IPAM_URL}/api/v1/scan-agents/agent.py" -o "$DEST/jt_ipam_agent.py"
  chmod 0755 "$DEST/jt_ipam_agent.py"
fi

echo "==> Writing config ${ENVFILE}"
# The server check chosen on this run wins over what the agent learned by itself (jt-ipam-trust next to it)
[[ -n "$TRUST_GIVEN" ]] && rm -f "${DEST}/jt-ipam-trust"
umask 077
cat > "$ENVFILE" <<EOF
JT_IPAM_URL=${JT_IPAM_URL}
JT_IPAM_AGENT_KEY=${JT_IPAM_AGENT_KEY}
JT_IPAM_INTERVAL=${JT_IPAM_INTERVAL}
JT_IPAM_INSECURE=${JT_IPAM_INSECURE}
JT_IPAM_PIN=${JT_IPAM_PIN}
EOF
# isc-dhcp-server's files in non-default places only (the agent finds /etc/dhcp/dhcpd.conf etc. itself)
if [[ -n "$JT_IPAM_DHCPD_CONF" ]]; then echo "JT_IPAM_DHCPD_CONF=${JT_IPAM_DHCPD_CONF}" >> "$ENVFILE"; fi
if [[ -n "$JT_IPAM_DHCPD_LEASES" ]]; then echo "JT_IPAM_DHCPD_LEASES=${JT_IPAM_DHCPD_LEASES}" >> "$ENVFILE"; fi
for _v in JT_IPAM_RELAY JT_IPAM_RELAY_PORTS JT_IPAM_RELAY_MAX JT_IPAM_RELAY_CIDRS; do
  if [[ -n "${!_v:-}" ]]; then echo "${_v}=${!_v}" >> "$ENVFILE"; fi
done

echo "==> Creating systemd service ${SVC}"
cat > "/etc/systemd/system/${SVC}.service" <<EOF
[Unit]
Description=jt-ipam scan agent
After=network-online.target
Wants=network-online.target

[Service]
EnvironmentFile=${ENVFILE}
ExecStart=/usr/bin/python3 ${DEST}/jt_ipam_agent.py
Restart=always
RestartSec=10
AmbientCapabilities=CAP_NET_RAW

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SVC}.service" >/dev/null 2>&1 || true
# restart (not just start) so a re-install always reloads the new key/config —
# `enable --now` would leave an already-running agent on the OLD key and 401 after a key reset.
systemctl restart "${SVC}.service"
echo
echo "Done. Status: systemctl status ${SVC}"
echo "Logs:        journalctl -u ${SVC} -f"
echo
echo "NOTE: a newly installed agent scans nothing until you assign subnets to it"
echo "      (jt-ipam: edit a subnet -> pick this scan agent, or edit the agent -> pick subnets)."
