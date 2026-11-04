#!/usr/bin/env bash
# 替會自我更新的代理簽章：憑證派送代理（.sh / .ps1）、掃描代理、RustDesk 代理（.py）。每次改了都要重簽，守門測試會擋。
#
# 代理自我更新時只接受用這把金鑰簽過的版本（Linux 用 openssl、Windows 用 .NET 驗），所以假冒或被入侵的
# jt-ipam 都推不了會以 root / SYSTEM 執行的腳本。私鑰不放在程式庫裡：預設讀
# ~/.config/jt-ipam/agent-signing-key.pem（權限 600，另外備份；遺失就只能讓代理重新安裝才能換新金鑰）。
set -euo pipefail
KEY="${JT_IPAM_SIGNING_KEY:-$HOME/.config/jt-ipam/agent-signing-key.pem}"
cd "$(dirname "$0")/.."
[ -r "$KEY" ] || { echo "signing key not found: $KEY" >&2; exit 1; }
for f in agent/jt_ipam_cert_agent.sh agent/jt_ipam_cert_agent.ps1 agent/jt_ipam_agent.py agent/jt_ipam_rustdesk_agent.py; do
  openssl dgst -sha256 -sign "$KEY" "$f" | base64 -w0 > "$f.sig"
  echo >> "$f.sig"
  echo "signed $f"
done
