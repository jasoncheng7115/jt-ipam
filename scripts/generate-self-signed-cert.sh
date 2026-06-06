#!/usr/bin/env bash
# =============================================================================
# jt-ipam — Generate a self-signed TLS certificate (ECDSA P-384, 5 years)
#
# Usage:
#   sudo ./scripts/generate-self-signed-cert.sh \
#       [--out-dir /etc/jt-ipam/tls] \
#       [--cn ipam.local] \
#       [--san "DNS:ipam.local,DNS:ipam.example.com,IP:192.168.1.10"] \
#       [--days 1825] \
#       [--owner root:jtipam]
#
# Default behavior:
#   * Auto-detect the hostname and all non-loopback local IPs, add them to the SAN
#   * Always add DNS:localhost, IP:127.0.0.1, IP:::1
#   * Private key perms 0640, certificate 0644, owner root:jtipam
#
# OWASP A02:
#   * ECDSA P-384 (can also switch to RSA-4096) — smaller than RSA, faster signing
#   * SHA-384 message digest (no longer using SHA-1 / MD5)
#   * Defaults to 5-year validity; self-signed certs can be long-lived, for proper CA-signed certs use the certbot flow
#
# Note: browsers warn on self-signed certs; for public services use Let's Encrypt or an internal CA instead.
# =============================================================================
set -euo pipefail

OUT_DIR="/etc/jt-ipam/tls"
CN=""
EXTRA_SAN=""
DAYS=1825
OWNER="root:jtipam"
FORCE=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --out-dir) OUT_DIR="$2"; shift 2 ;;
        --cn) CN="$2"; shift 2 ;;
        --san) EXTRA_SAN="$2"; shift 2 ;;
        --days) DAYS="$2"; shift 2 ;;
        --owner) OWNER="$2"; shift 2 ;;
        --force) FORCE=1; shift ;;
        -h|--help)
            sed -n '2,30p' "$0"
            exit 0
            ;;
        *)
            echo "Unknown arg: $1" >&2
            exit 2
            ;;
    esac
done

if [[ $EUID -ne 0 ]]; then
    echo "[error] must run as root (needs to write $OUT_DIR and set owner)" >&2
    exit 1
fi

# ── SAN auto-detection ──
HOSTNAME_FQDN="$(hostname -f 2>/dev/null || hostname)"
HOSTNAME_SHORT="$(hostname -s 2>/dev/null || hostname)"
[[ -z "$CN" ]] && CN="$HOSTNAME_FQDN"

san_lines=(
    "DNS:localhost"
    "DNS:$HOSTNAME_FQDN"
    "DNS:$HOSTNAME_SHORT"
    "IP:127.0.0.1"
    "IP:::1"
)

# Add primary IPs (all IPv4 returned by hostname -I)
for ip in $(hostname -I 2>/dev/null || true); do
    [[ -n "$ip" ]] && san_lines+=("IP:$ip")
done

# Extra SAN (caller-specified)
if [[ -n "$EXTRA_SAN" ]]; then
    IFS=',' read -ra extra <<< "$EXTRA_SAN"
    for s in "${extra[@]}"; do
        s="${s// /}"
        [[ -n "$s" ]] && san_lines+=("$s")
    done
fi

# Deduplicate
declare -A seen
unique_san=()
for s in "${san_lines[@]}"; do
    if [[ -z "${seen[$s]:-}" ]]; then
        unique_san+=("$s")
        seen[$s]=1
    fi
done
SAN_VALUE="$(IFS=,; echo "${unique_san[*]}")"

CERT="$OUT_DIR/server.crt"
KEY="$OUT_DIR/server.key"

if [[ -f "$CERT" || -f "$KEY" ]]; then
    if [[ $FORCE -ne 1 ]]; then
        echo "[error] $CERT or $KEY already exists; pass --force to overwrite" >&2
        exit 1
    fi
fi

install -d -m 0750 -o root -g "${OWNER#*:}" "$OUT_DIR"

# ── OpenSSL config (avoids CLI quote-escaping hell) ──
TMPCONF="$(mktemp)"
trap 'rm -f "$TMPCONF"' EXIT
cat > "$TMPCONF" <<EOF
[req]
distinguished_name = req_dn
req_extensions     = v3_req
prompt             = no

[req_dn]
CN = $CN
O  = jt-ipam
OU = self-signed

[v3_req]
basicConstraints     = critical, CA:FALSE
keyUsage             = critical, digitalSignature, keyEncipherment
extendedKeyUsage     = serverAuth
subjectAltName       = $SAN_VALUE
EOF

echo "[gen] CN=$CN"
echo "[gen] SAN=$SAN_VALUE"
echo "[gen] days=$DAYS curve=prime384v1"

# Generate ECDSA P-384 private key
openssl ecparam -name secp384r1 -genkey -noout -out "$KEY"

# CSR + self-sign
openssl req -new -x509 \
    -key "$KEY" \
    -out "$CERT" \
    -days "$DAYS" \
    -sha384 \
    -config "$TMPCONF" \
    -extensions v3_req

# Permissions
chown "$OWNER" "$KEY" "$CERT"
chmod 0640 "$KEY"
chmod 0644 "$CERT"

echo "[done]"
echo "  cert: $CERT  ($(stat -c '%U:%G %a' "$CERT" 2>/dev/null || stat -f '%Su:%Sg %Lp' "$CERT"))"
echo "  key:  $KEY  ($(stat -c '%U:%G %a' "$KEY"  2>/dev/null || stat -f '%Su:%Sg %Lp' "$KEY"))"
echo
echo "Verify:"
echo "  openssl x509 -in $CERT -noout -text | grep -E 'Subject:|DNS:|IP Address:|Not After'"
echo
echo "Set in /etc/jt-ipam/backend.env:"
echo "  BACKEND_TLS_MODE=direct"
echo "  BACKEND_BIND_HOST=0.0.0.0"
echo "  BACKEND_BIND_PORT=8443"
echo "  BACKEND_TLS_CERT_FILE=$CERT"
echo "  BACKEND_TLS_KEY_FILE=$KEY"
