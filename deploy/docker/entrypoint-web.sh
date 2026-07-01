#!/bin/sh
# Generate a self-signed TLS cert on first start if none is mounted.
# Mount your own cert/key at /etc/nginx/certs/server.{crt,key} to override.
set -e

CERT_DIR=/etc/nginx/certs
CN="${JT_IPAM_SERVER_NAME:-localhost}"

if [ ! -f "$CERT_DIR/server.crt" ] || [ ! -f "$CERT_DIR/server.key" ]; then
    mkdir -p "$CERT_DIR"
    echo "[jt-ipam] no TLS cert found; generating a self-signed cert for CN=$CN"
    echo "[jt-ipam] (mount real certs at $CERT_DIR/server.{crt,key} to use your own)"
    openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
        -keyout "$CERT_DIR/server.key" -out "$CERT_DIR/server.crt" \
        -subj "/CN=$CN" \
        -addext "subjectAltName=DNS:$CN,DNS:localhost,IP:127.0.0.1" >/dev/null 2>&1
    chmod 600 "$CERT_DIR/server.key"
fi
