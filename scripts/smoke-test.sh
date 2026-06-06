#!/usr/bin/env bash
# =============================================================================
# jt-ipam smoke test — end-to-end verification after deployment
#
# Usage:
#   ./scripts/smoke-test.sh                          # against https://localhost
#   ./scripts/smoke-test.sh https://ipam.example.com
#   ADMIN_USER=admin ADMIN_PASS=xxx ./scripts/smoke-test.sh https://localhost
#
# Does not write any persistent data (the test section is removed via cleanup).
#
# Exit codes:
#   0 — all green
#   non-zero — at least one failure (prints the failure list)
# =============================================================================

set -uo pipefail

BASE="${1:-https://localhost}"
ADMIN_USER="${ADMIN_USER:-admin}"
ADMIN_PASS="${ADMIN_PASS:-}"
CURL=(curl -kfsS --max-time 10)

GREEN=$'\e[32m'; RED=$'\e[31m'; YELLOW=$'\e[33m'; DIM=$'\e[2m'; RST=$'\e[0m'

PASS=0
FAIL=0
FAILS=()

check() {
    local name="$1"; shift
    if "$@" >/dev/null 2>&1; then
        printf '  %s✓%s %s\n' "$GREEN" "$RST" "$name"
        PASS=$((PASS+1))
    else
        printf '  %s✗%s %s\n' "$RED" "$RST" "$name"
        FAIL=$((FAIL+1))
        FAILS+=("$name")
    fi
}

check_eq() {
    local name="$1"; local expected="$2"; local got="$3"
    if [[ "$got" == "$expected" ]]; then
        printf '  %s✓%s %s\n' "$GREEN" "$RST" "$name"
        PASS=$((PASS+1))
    else
        printf '  %s✗%s %s (expected %q got %q)\n' "$RED" "$RST" "$name" "$expected" "$got"
        FAIL=$((FAIL+1))
        FAILS+=("$name")
    fi
}

printf '%sjt-ipam smoke test%s — %s\n' "$YELLOW" "$RST" "$BASE"
echo

# ─── 1. Basic connectivity ───
echo "[1] Connectivity and health check"
check "TCP 443 / TLS handshake" "${CURL[@]}" -o /dev/null "$BASE/"
check "/healthz returns ok"    bash -c "[[ \"\$(${CURL[*]} $BASE/healthz)\" == \"ok\" ]]"
check "frontend index 200" "${CURL[@]}" -o /dev/null "$BASE/"

# Skip login-dependent tests when no password is provided
if [[ -z "$ADMIN_PASS" ]]; then
    echo
    printf '%sSkipping login-related tests (ADMIN_PASS not provided)%s\n' "$YELLOW" "$RST"
    echo
    printf 'Passed %d / Failed %d\n' "$PASS" "$FAIL"
    exit $FAIL
fi

# ─── 2. Authentication ───
echo
echo "[2] Authentication (A07)"
LOGIN_BODY=$(python3 -c "import json,sys; print(json.dumps({'username':sys.argv[1],'password':sys.argv[2]}))" "$ADMIN_USER" "$ADMIN_PASS")
LOGIN_RESP=$("${CURL[@]}" -X POST "$BASE/api/v1/auth/login" \
    -H "Content-Type: application/json" -d "$LOGIN_BODY" 2>/dev/null || true)
TOKEN=$(echo "$LOGIN_RESP" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('access_token', '') or '')" 2>/dev/null || echo "")
if [[ -z "$TOKEN" || "$TOKEN" == "null" ]]; then
    printf '  %s✗%s login failed (response: %s)\n' "$RED" "$RST" "${LOGIN_RESP:0:80}"
    FAILS+=("login")
    FAIL=$((FAIL+1))
    echo
    printf 'Passed %d / Failed %d\n' "$PASS" "$FAIL"
    exit 1
fi
printf '  %s✓%s logged in and obtained access_token\n' "$GREEN" "$RST"
PASS=$((PASS+1))
AUTH=("-H" "Authorization: Bearer $TOKEN")

# Anti-enumeration（A07）
BAD_USER_CODE=$("${CURL[@]}" -o /dev/null -w '%{http_code}' -X POST "$BASE/api/v1/auth/login" \
    -H "Content-Type: application/json" \
    -d '{"username":"ghost-zzz-no-such-user","password":"anything-12345"}' 2>/dev/null || true)
check_eq "anti-enumeration unknown user also returns 401" "401" "$BAD_USER_CODE"

# /me
ME=$("${CURL[@]}" "${AUTH[@]}" "$BASE/api/v1/auth/me")
ME_USER=$(echo "$ME" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('username', '') or '')" 2>/dev/null || echo "")
check_eq "/me returns correct user" "$ADMIN_USER" "$ME_USER"
IS_ADMIN=$(echo "$ME" | python3 -c "import json,sys; d=json.load(sys.stdin); print(str(d.get('is_admin', False)).lower())" 2>/dev/null || echo "")
check_eq "/me is_admin=true"          "true"   "$IS_ADMIN"

# 401 without a token
NO_AUTH=$("${CURL[@]}" -o /dev/null -w '%{http_code}' "$BASE/api/v1/auth/me" 2>/dev/null || true)
check_eq "/me without token returns 401"            "401"    "$NO_AUTH"

# ─── 3. CRUD happy path ───
echo
echo "[3] CRUD main path"
SUFFIX=$(date +%s)
SEC_NAME="smoke-${SUFFIX}"
SEC_RESP=$("${CURL[@]}" "${AUTH[@]}" -X POST "$BASE/api/v1/sections" \
    -H "Content-Type: application/json" \
    -d "{\"name\":\"$SEC_NAME\",\"description\":\"smoke test $(date -Iseconds)\",\"strict_mode\":false}")
SEC_ID=$(echo "$SEC_RESP" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('id', '') or '')" 2>/dev/null || echo "")
if [[ -z "$SEC_ID" || "$SEC_ID" == "null" ]]; then
    printf '  %s✗%s create section failed\n' "$RED" "$RST"
    FAILS+=("create-section"); FAIL=$((FAIL+1))
else
    printf '  %s✓%s create section ok (id=%s...)\n' "$GREEN" "$RST" "${SEC_ID:0:8}"
    PASS=$((PASS+1))

    # create subnet
    SUB_RESP=$("${CURL[@]}" "${AUTH[@]}" -X POST "$BASE/api/v1/subnets" \
        -H "Content-Type: application/json" \
        -d "{\"section_id\":\"$SEC_ID\",\"cidr\":\"203.0.113.0/29\",\"description\":\"smoke\"}")
    SUB_ID=$(echo "$SUB_RESP" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('id', '') or '')" 2>/dev/null || echo "")
    if [[ -n "$SUB_ID" && "$SUB_ID" != "null" ]]; then
        printf '  %s✓%s create subnet ok\n' "$GREEN" "$RST"
        PASS=$((PASS+1))

        # first_free should be .1
        FF_IP=$("${CURL[@]}" "${AUTH[@]}" "$BASE/api/v1/subnets/$SUB_ID/first_free_address" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('ip', '') or '')" 2>/dev/null)
        check_eq "first_free_address is .1"     "203.0.113.1" "$FF_IP"

        # allocate
        ALLOC_RESP=$("${CURL[@]}" "${AUTH[@]}" -X POST "$BASE/api/v1/addresses/first_free" \
            -H "Content-Type: application/json" \
            -d "{\"subnet_id\":\"$SUB_ID\",\"hostname\":\"smoke-host\"}")
        ALLOC_IP=$(echo "$ALLOC_RESP" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('ip', '') or '')" 2>/dev/null)
        check_eq "allocate first_free returns .1"  "203.0.113.1" "$ALLOC_IP"
    else
        printf '  %s✗%s create subnet failed\n' "$RED" "$RST"
        FAILS+=("create-subnet"); FAIL=$((FAIL+1))
    fi

    # cleanup: delete the section first (CASCADE removes subnet + IP)
    DEL_CODE=$("${CURL[@]}" "${AUTH[@]}" -o /dev/null -w '%{http_code}' \
        -X DELETE "$BASE/api/v1/sections/$SEC_ID" 2>/dev/null || true)
    check_eq "cleanup: section delete 204"   "204" "$DEL_CODE"
fi

# ─── 4. A08 chain verify (core health metric) ───
echo
echo "[4] A08 SHA-256 audit chain"
CHAIN_RESP=$("${CURL[@]}" "${AUTH[@]}" -X POST "$BASE/api/v1/audit/verify")
CHAIN_OK=$(echo "$CHAIN_RESP" | python3 -c "import json,sys; d=json.load(sys.stdin); print(str(d.get('ok', False)).lower())" 2>/dev/null || echo "")
CHAIN_CHECKED=$(echo "$CHAIN_RESP" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('checked', '') or '')" 2>/dev/null || echo "0")
CHAIN_BROKEN=$(echo "$CHAIN_RESP" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('broken_at_id', '') or '')" 2>/dev/null || echo "null")
if [[ "$CHAIN_OK" == "true" ]]; then
    printf '  %s✓%s audit chain ok (checked=%s)\n' "$GREEN" "$RST" "$CHAIN_CHECKED"
    PASS=$((PASS+1))
else
    printf '  %s✗%s audit chain broken at id=%s\n' "$RED" "$RST" "$CHAIN_BROKEN"
    FAILS+=("chain-verify"); FAIL=$((FAIL+1))
fi

# ─── 5. Admin endpoints (A01) ───
echo
echo "[5] Admin endpoints (A01)"
USERS_CODE=$("${CURL[@]}" "${AUTH[@]}" -o /dev/null -w '%{http_code}' "$BASE/api/v1/users" 2>/dev/null)
check_eq "/users list readable by admin (200)"  "200" "$USERS_CODE"

AUDIT_CODE=$("${CURL[@]}" "${AUTH[@]}" -o /dev/null -w '%{http_code}' "$BASE/api/v1/audit?limit=5" 2>/dev/null)
check_eq "/audit list readable by admin (200)"  "200" "$AUDIT_CODE"

# ─── 6. Security headers (A02) ───
echo
echo "[6] Security headers (A02)"
HEADERS=$("${CURL[@]}" -I "$BASE/" 2>/dev/null)
echo "$HEADERS" | grep -qi '^strict-transport-security:' \
    && { printf '  %s✓%s HSTS\n' "$GREEN" "$RST"; PASS=$((PASS+1)); } \
    || { printf '  %s✗%s missing HSTS\n' "$RED" "$RST"; FAILS+=("hsts"); FAIL=$((FAIL+1)); }
echo "$HEADERS" | grep -qi '^content-security-policy:' \
    && { printf '  %s✓%s CSP\n' "$GREEN" "$RST"; PASS=$((PASS+1)); } \
    || { printf '  %s✗%s missing CSP\n' "$RED" "$RST"; FAILS+=("csp"); FAIL=$((FAIL+1)); }
echo "$HEADERS" | grep -qi '^x-frame-options: *deny' \
    && { printf '  %s✓%s X-Frame-Options: DENY\n' "$GREEN" "$RST"; PASS=$((PASS+1)); } \
    || { printf '  %s✗%s missing X-Frame-Options\n' "$RED" "$RST"; FAILS+=("xfo"); FAIL=$((FAIL+1)); }
echo "$HEADERS" | grep -qi '^x-content-type-options: *nosniff' \
    && { printf '  %s✓%s X-Content-Type-Options: nosniff\n' "$GREEN" "$RST"; PASS=$((PASS+1)); } \
    || { printf '  %s✗%s missing X-Content-Type-Options\n' "$RED" "$RST"; FAILS+=("xcto"); FAIL=$((FAIL+1)); }
echo "$HEADERS" | grep -qi '^server: nginx$' \
    && { printf '  %s✓%s server header version stripped\n' "$GREEN" "$RST"; PASS=$((PASS+1)); } \
    || { printf '  %s!%s server header may expose version info (%sserver_tokens off%s)\n' "$YELLOW" "$RST" "$DIM" "$RST"; }

# ─── 7. systemd security score (only testable when run as root locally) ───
if [[ "$EUID" -eq 0 ]] && command -v systemd-analyze >/dev/null && systemctl is-active --quiet jt-ipam-backend; then
    echo
    echo "[7] systemd hardening"
    SCORE=$(systemd-analyze security jt-ipam-backend 2>&1 | grep -E 'Overall exposure level' | grep -oE '[0-9]+\.[0-9]+' | head -1)
    if [[ -n "$SCORE" ]]; then
        if awk "BEGIN{exit !($SCORE <= 3.5)}"; then
            printf '  %s✓%s systemd-analyze security score=%s (≤ 3.5)\n' "$GREEN" "$RST" "$SCORE"
            PASS=$((PASS+1))
        else
            printf '  %s✗%s systemd-analyze security score=%s (> 3.5)\n' "$RED" "$RST" "$SCORE"
            FAILS+=("systemd-score"); FAIL=$((FAIL+1))
        fi
    fi
fi

# ─── Conclusion ───
echo
echo "===================="
if [[ "$FAIL" -eq 0 ]]; then
    printf '%sAll passed%s — %d checks\n' "$GREEN" "$RST" "$PASS"
    exit 0
else
    printf '%s%d failed%s (passed %d)\n' "$RED" "$FAIL" "$RST" "$PASS"
    printf 'Failure list:\n'
    for f in "${FAILS[@]}"; do echo "  - $f"; done
    exit 1
fi
