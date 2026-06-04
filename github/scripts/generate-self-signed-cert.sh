#!/usr/bin/env bash
# =============================================================================
# jt-ipam — 產生自簽 TLS 憑證（ECDSA P-384，5 年）
#
# 用法：
#   sudo ./scripts/generate-self-signed-cert.sh \
#       [--out-dir /etc/jt-ipam/tls] \
#       [--cn ipam.local] \
#       [--san "DNS:ipam.local,DNS:ipam.example.com,IP:192.168.1.10"] \
#       [--days 1825] \
#       [--owner root:jtipam]
#
# 預設行為：
#   * 自動偵測 hostname、本機所有非 loopback IP，加入 SAN
#   * 一律加入 DNS:localhost、IP:127.0.0.1、IP:::1
#   * 私鑰權限 0640，憑證 0644，所有權 root:jtipam
#
# OWASP A02：
#   * ECDSA P-384（也可改 RSA-4096）— 比 RSA 小、簽章快
#   * SHA-384 訊息摘要（不再用 SHA-1 / MD5）
#   * 預設 5 年有效；自簽憑證可長一點，正式 CA 簽的請走 certbot 流程
#
# 注意：自簽憑證瀏覽器會出現警示；公開服務請改用 Let's Encrypt 或內網 CA。
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
    echo "[error] 必須以 root 執行（需寫入 $OUT_DIR 並設定 owner）" >&2
    exit 1
fi

# ── SAN 自動偵測 ──
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

# 加入主要 IP（hostname -I 給的所有 IPv4）
for ip in $(hostname -I 2>/dev/null || true); do
    [[ -n "$ip" ]] && san_lines+=("IP:$ip")
done

# 額外 SAN（呼叫者指定）
if [[ -n "$EXTRA_SAN" ]]; then
    IFS=',' read -ra extra <<< "$EXTRA_SAN"
    for s in "${extra[@]}"; do
        s="${s// /}"
        [[ -n "$s" ]] && san_lines+=("$s")
    done
fi

# 去重
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

# ── OpenSSL config（避免 CLI 引號跳脫地獄）──
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

# 產生 ECDSA P-384 私鑰
openssl ecparam -name secp384r1 -genkey -noout -out "$KEY"

# CSR + 自簽
openssl req -new -x509 \
    -key "$KEY" \
    -out "$CERT" \
    -days "$DAYS" \
    -sha384 \
    -config "$TMPCONF" \
    -extensions v3_req

# 權限
chown "$OWNER" "$KEY" "$CERT"
chmod 0640 "$KEY"
chmod 0644 "$CERT"

echo "[done]"
echo "  cert: $CERT  ($(stat -c '%U:%G %a' "$CERT" 2>/dev/null || stat -f '%Su:%Sg %Lp' "$CERT"))"
echo "  key:  $KEY  ($(stat -c '%U:%G %a' "$KEY"  2>/dev/null || stat -f '%Su:%Sg %Lp' "$KEY"))"
echo
echo "驗證："
echo "  openssl x509 -in $CERT -noout -text | grep -E 'Subject:|DNS:|IP Address:|Not After'"
echo
echo "在 /etc/jt-ipam/backend.env 設定："
echo "  BACKEND_TLS_MODE=direct"
echo "  BACKEND_BIND_HOST=0.0.0.0"
echo "  BACKEND_BIND_PORT=8443"
echo "  BACKEND_TLS_CERT_FILE=$CERT"
echo "  BACKEND_TLS_KEY_FILE=$KEY"
