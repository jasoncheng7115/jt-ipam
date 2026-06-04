#!/usr/bin/env bash
# jt-ipam scan agent one-line installer (systemd, no Docker).
#
# Usage:
#   sudo JT_IPAM_URL=https://192.0.2.10 JT_IPAM_AGENT_KEY=<key> ./jt-ipam-agent-installer.sh
# Optional:
#   JT_IPAM_INTERVAL=300   JT_IPAM_INSECURE=1   (set 1 for self-signed server cert)
#
# Re-running this installer re-downloads the latest agent and overwrites the old one.
# The agent also auto-updates itself when the server has a newer version.
set -euo pipefail

DEST=/opt/jt-ipam-agent
SVC=jt-ipam-scan-agent
ENVFILE=/etc/jt-ipam-agent.env

: "${JT_IPAM_URL:?JT_IPAM_URL is required, e.g. https://192.0.2.10}"
: "${JT_IPAM_AGENT_KEY:?JT_IPAM_AGENT_KEY is required (get it when creating an agent in jt-ipam)}"
JT_IPAM_INTERVAL="${JT_IPAM_INTERVAL:-300}"
JT_IPAM_INSECURE="${JT_IPAM_INSECURE:-}"

if [[ $EUID -ne 0 ]]; then echo "Please run as root / sudo" >&2; exit 1; fi
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
command -v ping >/dev/null || { echo "ping is required (iputils-ping)" >&2; exit 1; }

echo "==> Installing agent program to ${DEST}"
mkdir -p "$DEST"
INSECURE_FLAG=""
[[ -n "$JT_IPAM_INSECURE" ]] && INSECURE_FLAG="-k"
if [[ -f "$(dirname "$0")/jt_ipam_agent.py" ]]; then
  # installer shipped alongside the agent (offline install)
  install -m 0755 "$(dirname "$0")/jt_ipam_agent.py" "$DEST/jt_ipam_agent.py"
else
  # curl | bash mode: download the latest agent from the server (overwrites existing)
  echo "    downloading agent from ${JT_IPAM_URL}"
  curl -fsSL $INSECURE_FLAG "${JT_IPAM_URL}/api/v1/scan-agents/agent.py" -o "$DEST/jt_ipam_agent.py"
  chmod 0755 "$DEST/jt_ipam_agent.py"
fi

echo "==> Writing config ${ENVFILE}"
umask 077
cat > "$ENVFILE" <<EOF
JT_IPAM_URL=${JT_IPAM_URL}
JT_IPAM_AGENT_KEY=${JT_IPAM_AGENT_KEY}
JT_IPAM_INTERVAL=${JT_IPAM_INTERVAL}
JT_IPAM_INSECURE=${JT_IPAM_INSECURE}
EOF

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
