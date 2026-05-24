#!/usr/bin/env bash
# =============================================================================
# jt-ipam smoke test — 部署後端對端驗證
#
# 用法：
#   ./scripts/smoke-test.sh                          # 對 https://localhost
#   ./scripts/smoke-test.sh https://ipam.example.com
#   ADMIN_USER=admin ADMIN_PASS=xxx ./scripts/smoke-test.sh https://localhost
#
# 不會寫任何持久資料（建測試 section 完用 cleanup 刪掉）。
#
# 退出碼：
#   0 — 全綠
#   非 0 — 至少一項失敗（會印失敗清單）
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

# ─── 1. 基礎連線 ───
echo "[1] 連線與健康檢查"
check "TCP 443 / TLS handshake" "${CURL[@]}" -o /dev/null "$BASE/"
check "/healthz 回 ok"    bash -c "[[ \"\$(${CURL[*]} $BASE/healthz)\" == \"ok\" ]]"
check "frontend index 200" "${CURL[@]}" -o /dev/null "$BASE/"

# 沒密碼就不做需要登入的測試
if [[ -z "$ADMIN_PASS" ]]; then
    echo
    printf '%s跳過登入相關測試（沒提供 ADMIN_PASS）%s\n' "$YELLOW" "$RST"
    echo
    printf '通過 %d / 失敗 %d\n' "$PASS" "$FAIL"
    exit $FAIL
fi

# ─── 2. 認證 ───
echo
echo "[2] 認證 (A07)"
LOGIN_BODY=$(python3 -c "import json,sys; print(json.dumps({'username':sys.argv[1],'password':sys.argv[2]}))" "$ADMIN_USER" "$ADMIN_PASS")
LOGIN_RESP=$("${CURL[@]}" -X POST "$BASE/api/v1/auth/login" \
    -H "Content-Type: application/json" -d "$LOGIN_BODY" 2>/dev/null || true)
TOKEN=$(echo "$LOGIN_RESP" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('access_token', '') or '')" 2>/dev/null || echo "")
if [[ -z "$TOKEN" || "$TOKEN" == "null" ]]; then
    printf '  %s✗%s 登入失敗 (response: %s)\n' "$RED" "$RST" "${LOGIN_RESP:0:80}"
    FAILS+=("login")
    FAIL=$((FAIL+1))
    echo
    printf '通過 %d / 失敗 %d\n' "$PASS" "$FAIL"
    exit 1
fi
printf '  %s✓%s 登入並拿到 access_token\n' "$GREEN" "$RST"
PASS=$((PASS+1))
AUTH=("-H" "Authorization: Bearer $TOKEN")

# Anti-enumeration（A07）
BAD_USER_CODE=$("${CURL[@]}" -o /dev/null -w '%{http_code}' -X POST "$BASE/api/v1/auth/login" \
    -H "Content-Type: application/json" \
    -d '{"username":"ghost-zzz-no-such-user","password":"anything-12345"}' 2>/dev/null || true)
check_eq "anti-enumeration unknown user 也回 401" "401" "$BAD_USER_CODE"

# /me
ME=$("${CURL[@]}" "${AUTH[@]}" "$BASE/api/v1/auth/me")
ME_USER=$(echo "$ME" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('username', '') or '')" 2>/dev/null || echo "")
check_eq "/me 回應正確使用者" "$ADMIN_USER" "$ME_USER"
IS_ADMIN=$(echo "$ME" | python3 -c "import json,sys; d=json.load(sys.stdin); print(str(d.get('is_admin', False)).lower())" 2>/dev/null || echo "")
check_eq "/me is_admin=true"          "true"   "$IS_ADMIN"

# 不帶 token 401
NO_AUTH=$("${CURL[@]}" -o /dev/null -w '%{http_code}' "$BASE/api/v1/auth/me" 2>/dev/null || true)
check_eq "/me 無 token 401"            "401"    "$NO_AUTH"

# ─── 3. CRUD happy path ───
echo
echo "[3] CRUD 主路徑"
SUFFIX=$(date +%s)
SEC_NAME="smoke-${SUFFIX}"
SEC_RESP=$("${CURL[@]}" "${AUTH[@]}" -X POST "$BASE/api/v1/sections" \
    -H "Content-Type: application/json" \
    -d "{\"name\":\"$SEC_NAME\",\"description\":\"smoke test $(date -Iseconds)\",\"strict_mode\":false}")
SEC_ID=$(echo "$SEC_RESP" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('id', '') or '')" 2>/dev/null || echo "")
if [[ -z "$SEC_ID" || "$SEC_ID" == "null" ]]; then
    printf '  %s✗%s 建 section 失敗\n' "$RED" "$RST"
    FAILS+=("create-section"); FAIL=$((FAIL+1))
else
    printf '  %s✓%s 建 section ok (id=%s...)\n' "$GREEN" "$RST" "${SEC_ID:0:8}"
    PASS=$((PASS+1))

    # 建 subnet
    SUB_RESP=$("${CURL[@]}" "${AUTH[@]}" -X POST "$BASE/api/v1/subnets" \
        -H "Content-Type: application/json" \
        -d "{\"section_id\":\"$SEC_ID\",\"cidr\":\"203.0.113.0/29\",\"description\":\"smoke\"}")
    SUB_ID=$(echo "$SUB_RESP" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('id', '') or '')" 2>/dev/null || echo "")
    if [[ -n "$SUB_ID" && "$SUB_ID" != "null" ]]; then
        printf '  %s✓%s 建 subnet ok\n' "$GREEN" "$RST"
        PASS=$((PASS+1))

        # first_free 應該是 .1
        FF_IP=$("${CURL[@]}" "${AUTH[@]}" "$BASE/api/v1/subnets/$SUB_ID/first_free_address" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('ip', '') or '')" 2>/dev/null)
        check_eq "first_free_address 是 .1"     "203.0.113.1" "$FF_IP"

        # allocate
        ALLOC_RESP=$("${CURL[@]}" "${AUTH[@]}" -X POST "$BASE/api/v1/addresses/first_free" \
            -H "Content-Type: application/json" \
            -d "{\"subnet_id\":\"$SUB_ID\",\"hostname\":\"smoke-host\"}")
        ALLOC_IP=$(echo "$ALLOC_RESP" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('ip', '') or '')" 2>/dev/null)
        check_eq "allocate first_free 拿到 .1"  "203.0.113.1" "$ALLOC_IP"
    else
        printf '  %s✗%s 建 subnet 失敗\n' "$RED" "$RST"
        FAILS+=("create-subnet"); FAIL=$((FAIL+1))
    fi

    # cleanup：先刪 section（CASCADE 會帶走 subnet + IP）
    DEL_CODE=$("${CURL[@]}" "${AUTH[@]}" -o /dev/null -w '%{http_code}' \
        -X DELETE "$BASE/api/v1/sections/$SEC_ID" 2>/dev/null || true)
    check_eq "cleanup: section delete 204"   "204" "$DEL_CODE"
fi

# ─── 4. A08 chain verify（核心健康指標）───
echo
echo "[4] A08 SHA-256 異動鏈"
CHAIN_RESP=$("${CURL[@]}" "${AUTH[@]}" -X POST "$BASE/api/v1/audit/verify")
CHAIN_OK=$(echo "$CHAIN_RESP" | python3 -c "import json,sys; d=json.load(sys.stdin); print(str(d.get('ok', False)).lower())" 2>/dev/null || echo "")
CHAIN_CHECKED=$(echo "$CHAIN_RESP" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('checked', '') or '')" 2>/dev/null || echo "0")
CHAIN_BROKEN=$(echo "$CHAIN_RESP" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('broken_at_id', '') or '')" 2>/dev/null || echo "null")
if [[ "$CHAIN_OK" == "true" ]]; then
    printf '  %s✓%s audit chain ok (checked=%s)\n' "$GREEN" "$RST" "$CHAIN_CHECKED"
    PASS=$((PASS+1))
else
    printf '  %s✗%s audit chain 斷裂 at id=%s\n' "$RED" "$RST" "$CHAIN_BROKEN"
    FAILS+=("chain-verify"); FAIL=$((FAIL+1))
fi

# ─── 5. Admin endpoints（A01）───
echo
echo "[5] Admin endpoints (A01)"
USERS_CODE=$("${CURL[@]}" "${AUTH[@]}" -o /dev/null -w '%{http_code}' "$BASE/api/v1/users" 2>/dev/null)
check_eq "/users 列表 admin 可讀 (200)"  "200" "$USERS_CODE"

AUDIT_CODE=$("${CURL[@]}" "${AUTH[@]}" -o /dev/null -w '%{http_code}' "$BASE/api/v1/audit?limit=5" 2>/dev/null)
check_eq "/audit 列表 admin 可讀 (200)"  "200" "$AUDIT_CODE"

# ─── 6. 安全 headers（A02）───
echo
echo "[6] 安全 headers (A02)"
HEADERS=$("${CURL[@]}" -I "$BASE/" 2>/dev/null)
echo "$HEADERS" | grep -qi '^strict-transport-security:' \
    && { printf '  %s✓%s HSTS\n' "$GREEN" "$RST"; PASS=$((PASS+1)); } \
    || { printf '  %s✗%s 缺 HSTS\n' "$RED" "$RST"; FAILS+=("hsts"); FAIL=$((FAIL+1)); }
echo "$HEADERS" | grep -qi '^content-security-policy:' \
    && { printf '  %s✓%s CSP\n' "$GREEN" "$RST"; PASS=$((PASS+1)); } \
    || { printf '  %s✗%s 缺 CSP\n' "$RED" "$RST"; FAILS+=("csp"); FAIL=$((FAIL+1)); }
echo "$HEADERS" | grep -qi '^x-frame-options: *deny' \
    && { printf '  %s✓%s X-Frame-Options: DENY\n' "$GREEN" "$RST"; PASS=$((PASS+1)); } \
    || { printf '  %s✗%s 缺 X-Frame-Options\n' "$RED" "$RST"; FAILS+=("xfo"); FAIL=$((FAIL+1)); }
echo "$HEADERS" | grep -qi '^x-content-type-options: *nosniff' \
    && { printf '  %s✓%s X-Content-Type-Options: nosniff\n' "$GREEN" "$RST"; PASS=$((PASS+1)); } \
    || { printf '  %s✗%s 缺 X-Content-Type-Options\n' "$RED" "$RST"; FAILS+=("xcto"); FAIL=$((FAIL+1)); }
echo "$HEADERS" | grep -qi '^server: nginx$' \
    && { printf '  %s✓%s server header 已去版本號\n' "$GREEN" "$RST"; PASS=$((PASS+1)); } \
    || { printf '  %s!%s server header 可能有版本資訊 (%sserver_tokens off%s)\n' "$YELLOW" "$RST" "$DIM" "$RST"; }

# ─── 7. systemd 安全分數（只有 root 在本機跑時可測）───
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

# ─── 結論 ───
echo
echo "===================="
if [[ "$FAIL" -eq 0 ]]; then
    printf '%s全部通過%s — %d 項\n' "$GREEN" "$RST" "$PASS"
    exit 0
else
    printf '%s失敗 %d 項%s（通過 %d）\n' "$RED" "$FAIL" "$RST" "$PASS"
    printf '失敗清單：\n'
    for f in "${FAILS[@]}"; do echo "  - $f"; done
    exit 1
fi
