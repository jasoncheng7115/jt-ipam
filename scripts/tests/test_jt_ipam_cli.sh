#!/usr/bin/env bash
# =============================================================================
# CLI/guard 安全測試（不做任何真實安裝；不需 root；不碰系統）
#
# 只驗證 jt-ipam.sh 的「分派 / usage / 不明指令 / root guard」行為。
# 任何會真的動到 apt/systemctl/postgres 的路徑都不會被觸發，因為：
#   - help / 無參數 / 不明指令 在 root guard 之前就回傳
#   - install/upgrade/uninstall 在做任何系統動作之前就 EUID guard
# =============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$HERE/../jt-ipam.sh"

PASS=0
FAIL=0
ok()   { echo "  ok: $*"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $*" >&2; FAIL=$((FAIL+1)); }

# 跑 SCRIPT，回傳 exit code 進 $RC、輸出進 $OUT（合併 stdout+stderr）
run() {
    set +e
    OUT="$("$SCRIPT" "$@" 2>&1)"
    RC=$?
    set -e
}

echo "== jt-ipam.sh CLI guard tests =="

# 0. 檔案存在且可執行
[[ -f "$SCRIPT" ]] || { echo "FAIL: $SCRIPT 不存在（紅燈：腳本還沒建立）" >&2; exit 1; }

# 1. bash -n 語法檢查
if bash -n "$SCRIPT"; then ok "bash -n 語法通過"; else bad "bash -n 語法錯誤"; fi

# 2. 無參數 → 印 usage 且 exit 2
run
if [[ $RC -eq 2 ]]; then ok "無參數 exit 2"; else bad "無參數應 exit 2，實得 $RC"; fi
if grep -q "install" <<<"$OUT" && grep -q "upgrade" <<<"$OUT" && grep -q "uninstall" <<<"$OUT"; then
    ok "無參數 usage 含 install/upgrade/uninstall"
else
    bad "無參數 usage 缺 install/upgrade/uninstall"
fi

# 3. help → exit 0 + usage
run help
if [[ $RC -eq 0 ]]; then ok "help exit 0"; else bad "help 應 exit 0，實得 $RC"; fi
grep -q "install" <<<"$OUT" && grep -q "upgrade" <<<"$OUT" && grep -q "uninstall" <<<"$OUT" \
    && ok "help usage 含三個子指令" || bad "help usage 缺子指令"

# 3b. -h / --help 也 exit 0
run -h
[[ $RC -eq 0 ]] && ok "-h exit 0" || bad "-h 應 exit 0，實得 $RC"
run --help
[[ $RC -eq 0 ]] && ok "--help exit 0" || bad "--help 應 exit 0，實得 $RC"

# 4. 不明指令 → exit 2 + error
run bogus
if [[ $RC -eq 2 ]]; then ok "bogus exit 2"; else bad "bogus 應 exit 2，實得 $RC"; fi
grep -qiE "unknown|不明|未知|error|錯誤" <<<"$OUT" && ok "bogus 印出錯誤訊息" || bad "bogus 沒印錯誤訊息"

# 5. root guard — 以目前 non-root 身分跑各子指令，要被擋且訊息含 root/sudo
#    （這些 guard 在任何 apt/systemctl 之前，故安全）
if [[ $EUID -ne 0 ]]; then
    for sub in install upgrade uninstall; do
        run "$sub"
        if [[ $RC -ne 0 ]]; then
            ok "$sub 非 root 被擋（exit $RC）"
        else
            bad "$sub 非 root 竟成功（exit 0）— guard 失效！"
        fi
        grep -qiE "root|sudo" <<<"$OUT" && ok "$sub guard 訊息含 root/sudo" \
            || bad "$sub guard 訊息不含 root/sudo：$OUT"
    done
else
    echo "  (以 root 執行測試，略過 non-root guard 斷言)"
fi

echo
echo "通過：$PASS　失敗：$FAIL"
if [[ $FAIL -eq 0 ]]; then
    echo "ALL OK"
    exit 0
else
    exit 1
fi
