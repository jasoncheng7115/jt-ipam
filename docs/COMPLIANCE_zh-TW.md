# 合規對照：ISO/IEC 27001:2022 與 ISO/IEC 42001:2023

> 本檔由 scripts/gen-compliance-docs.py 產生，請改那支腳本再重新產生。

jt-ipam 本身不是一張認證，用了它也不代表通過 ISO/IEC 27001:2022 或 ISO/IEC 42001:2023。這份文件分兩部分：jt-ipam 已經做到、可以拿來當佐證的控制（每一項都附上可以自行驗證的測試或設定位置），以及導入組織使用 jt-ipam 之後要自己負責的事。

這裡只列出目前程式確實具備的功能，依 1.0.6 撰寫，之後的變動以 CHANGELOG 為準。正式盤點時請固定版本或 commit，並在自己的環境實際驗收。

## 資料在哪裡

IPAM 資料、稽核記錄與整合系統的帳號密碼都存在你自己的伺服器上，不需要任何雲端服務也能完整運作；啟用下圖標成「選用」的項目時，相關資料才會送到管理員設定的服務。

- **你的內網（自己架設的伺服器）**：使用者的瀏覽器 ↔ jt-ipam 伺服器 ↔ 網路設備與整合系統、掃描代理（選用）、LLM 伺服器（選用）、公司目錄與單一登入（選用）、稽核記錄轉送（選用）
- **外部**：雲端 AI 服務（OpenAI 相容的 API）：不需要；把 LLM 設成雲端服務才會送
- **外部**：通知管道（Email、Telegram、Slack、Teams、Webhook…）：你開啟才送
- **外部**：公開 DNS 解析器（對外開放服務的外部解析檢查）：只送你指定的公開網域底下的名稱
- **外部**：公開資料（OUI 廠商清單、Recog 指紋、GeoIP（選用））：只下載，不上傳你的資料

版本檢查與 RDAP 查詢只在按下按鈕時才連到外部（GitHub、RIPE、APNIC）；安裝與升級時會從作業系統套件庫、PyPI、npm 與 GitHub 下載程式。

| 資料 | 存在哪裡、留多久 |
|---|---|
| IPAM 資料（區段、子網路、IP、裝置、機櫃、整合同步來的資料） | PostgreSQL（預設資料庫 `jt_ipam`），保留到刪除為止 |
| 稽核記錄 | PostgreSQL；資料庫層只能新增，不能修改、刪除或清空，以雜湊鏈串接，每 5 分鐘驗證並錨定到 `/var/lib/jt-ipam/audit-anchors.jsonl` 與 journald。不會自動清除；長期留存建議另外轉送 SIEM |
| IP 異動記錄、上線歷程 | PostgreSQL，保留到刪除為止（「淡化天數」只影響畫面上的顯示） |
| ARP 表、交換器 MAC 表 | ARP 預設 30 天、MAC 表預設 365 天（`ARP_RETENTION_DAYS`、`FDB_RETENTION_DAYS`，0 表示永久保留） |
| 未納管位址的觀測、外部 DNS 解析變化、掃描代理負載 | 分別保留 30 天、90 天、7 天 |
| AI 對話歷程 | PostgreSQL，預設 90 天（可調整，0 表示永久保留），也可以手動清除 |
| IP 變更評估 | 結案、取消或封存的計畫保留 180 天；失敗或取消的執行保留 30 天，清除時寫入稽核記錄 |
| 整合系統的帳號密碼與金鑰 | PostgreSQL，以 AES-256-GCM 加密，金鑰在 `/etc/jt-ipam/backend.env`；API 權杖只存雜湊 |
| 登入工作階段 | PostgreSQL（伺服器端），存取權杖 15 分鐘、工作階段 14 天；Redis 只放限流計數與主控台的單次票證（30 至 60 秒） |
| 主控台連線（SSH、SFTP、RDP、VNC 等） | 不錄影、不側錄按鍵；連線的開啟與結束、SFTP 每個檔案操作（路徑、大小）寫入稽核記錄；上傳的檔案直接寫到目標主機，不留在 jt-ipam |
| 平面圖等上傳的檔案 | `/var/lib/jt-ipam/uploads`，保留到刪除為止 |
| 系統匯出檔 | 以密碼加密（scrypt、AES-256-GCM），暫存在 `/var/lib/jt-ipam/transfer`，超過 48 小時的在下次匯出或匯入時刪除 |
| 每日備份 | `/var/backups/jt-ipam`，每天 03:30，保留 14 天；內容是資料庫、`backend.env`、TLS 憑證與上傳的檔案。在系統設定填了備份密碼才會加密（`.jtbak`），沒填是明文，要另外保護 |

## ISO/IEC 27001:2022：jt-ipam 提供的資訊安全控制

| 面向 | 對應控制項 | jt-ipam 的做法 | 驗證方式（測試、設定） |
|---|---|---|---|
| 登入、雙因素驗證與工作階段 | A.5.16、A.5.17、A.8.5 | • 本機與 LDAP 帳號可啟用 TOTP 雙因素驗證<br>• 管理員可要求管理員或所有人使用（還沒設定的人登入時先完成設定，不會被鎖在外面；被要求的人不能自行停用），SSO 可選擇是否一併套用<br>• 啟用時產生 10 組只能用一次的復原碼（只存雜湊），管理員可以重設某人的雙因素驗證（唯一的管理員可從主機的指令列重設）<br>• 同一組驗證碼不能用第二次<br>• 密碼或驗證碼連續錯 5 次鎖定 15 分鐘並寫進稽核<br>• 登入、驗證碼與換發權杖每個來源 IP 每分鐘最多 10 次<br>• 登入建立伺服器端工作階段：更新權杖只放在 HttpOnly Cookie、每次換發都換新，已經換掉的又被使用就撤銷整個工作階段並通知管理員與本人<br>• 存取權杖 15 分鐘到期並綁定工作階段，登出、強制登出、停用帳號與管理員重設密碼時存取權杖與更新權杖立即失效（已開著的主控台見「遠端主控台」），改密碼會登出其他裝置<br>• 使用者可以檢視並登出自己的裝置<br>• 每個請求都重新讀取帳號狀態與權限 | [`backend/tests/test_mfa_policy.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_mfa_policy.py), [`test_session_revocation.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_session_revocation.py), [`test_security_alerts.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_security_alerts.py), [`test_rate_limit_switch.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_rate_limit_switch.py); [`frontend/src/api/__tests__/tokenStorage.test.ts`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/frontend/src/api/__tests__/tokenStorage.test.ts) |
| 物件權限與跨單位隔離 | A.5.15、A.5.18、A.8.2、A.8.3 | • 依單位、區段、子網路、IP、裝置、機櫃、地點授權，可往下繼承<br>• 搜尋、IP 關聯、拓樸圖、AI 對話的工具、MCP 工具與 REST 用同一套可見範圍<br>• 重疊網段依子網路比對，不會對到其他單位的同一個位址<br>• 看不到的物件回應不存在（404）<br>• 只被授權部分物件的帳號在拓樸圖上只看得到自己範圍內的裝置與子網路，VPN、虛擬機等全域資料只給有全域讀取權限的帳號 | [`test_mcp_tools_match_rest_permissions.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_mcp_tools_match_rest_permissions.py), [`test_rbac_enforcement.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_rbac_enforcement.py), [`test_rbac_detail_idor.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_rbac_detail_idor.py), [`test_search_overlapping.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_search_overlapping.py), [`test_semantic_search_scope.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_semantic_search_scope.py), [`test_topology_scope.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_topology_scope.py) |
| API 權杖與 MCP | A.5.17、A.8.3、A.8.5 | • 個人 API 權杖有效期 1 到 365 天（預設 90 天），可隨時撤銷並留下稽核，資料庫只存雜湊<br>• 唯讀權杖不能呼叫會異動資料的方法<br>• 每把權杖每分鐘最多 600 次（REST 與 MCP 合計）<br>• 帳號停用時權杖一併撤銷<br>• 外部 MCP 預設關閉，使用同一套權杖，或可輪替的唯讀 MCP 金鑰（有效 30 到 365 天）<br>• API 權杖、MCP 金鑰與公開端點權杖在到期前 14、7、1 天與當天通知擁有者或管理員 | [`test_api_token_scope.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_api_token_scope.py), [`test_api_token_mcp_hardening.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_api_token_mcp_hardening.py), [`test_session_revocation.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_session_revocation.py), [`test_mcp_transport.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_mcp_transport.py) |
| 機密資料加密 | A.5.17、A.8.24 | • 整合的密碼、API 金鑰、公開端點的權杖等以 AES-256-GCM 加密存放，每一處加解密都綁定用途（密文搬到別的欄位就解不開，守門測試逐一檢查），SSH 帳密使用每筆獨立的資料金鑰<br>• 加密金鑰在主機的 backend.env，不放在資料庫<br>• 設定頁不直接回傳權杖與金鑰，要檢視時另外取回並留稽核<br>• 系統匯出檔以使用者設定的密碼加密（scrypt 加 AES-256-GCM） | [`test_security.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_security.py), [`test_secret_aad.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_secret_aad.py), [`test_encryption_key_format.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_encryption_key_format.py), [`test_public_endpoint_tokens.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_public_endpoint_tokens.py), [`test_system_transfer.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_system_transfer.py) |
| 傳輸加密 | A.5.14、A.8.24 | • 網頁與 API 強制 HTTPS（nginx 反向代理或後端直接提供 TLS，二擇一；對外網址不是 https 時後端不會啟動），nginx 設定為 TLS 1.2 與 1.3，並送出 HSTS<br>• Graylog DSV 查表的明文 8088 埠要在設定頁明確開啟才服務（新安裝預設關閉），可限制允許的來源位址<br>• 網址上的權杖不寫進 nginx 存取記錄 | [`app/core/config.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/app/core/config.py), [`deploy/nginx/jt-ipam.conf`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/deploy/nginx/jt-ipam.conf), [`test_public_endpoint_tokens.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_public_endpoint_tokens.py) |
| 出站連線防護 | A.8.20、A.8.26 | • HTTP 類整合在建立連線的當下檢查目標位址並連到檢查過的位址，擋下本機、雲端中繼資料位址與 DNS rebinding<br>• 轉址時重新檢查，而且不把認證資訊帶到別的主機<br>• 其他協定（SSH、LDAP、SMTP、RADIUS、WinRM、DNS、syslog 轉送）連線前解析並檢查所有位址，擋雲端中繼資料、link-local 與多播<br>• 遠端主控台的目標另外不能是本機位址（守門測試檢查每一處連線都經過檢查） | [`test_safe_http_guard.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_safe_http_guard.py), [`test_netdiag_http_guard.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_netdiag_http_guard.py), [`test_net_guard.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_net_guard.py) |
| 稽核記錄 | A.5.28、A.5.33、A.8.15、A.8.16 | • 會異動資料的端點都要寫稽核，下載匯出檔與檢視金鑰、權杖也要寫（兩道守門測試逐一檢查，例外要寫明理由）<br>• 瀏覽器產生的表格與報告匯出由瀏覽器回報一筆<br>• 稽核記錄以雜湊鏈串接，資料庫層拒絕修改、刪除與清空<br>• 安裝與升級把稽核表交給不能登入的資料庫角色，應用程式帳號只能讀取與新增，無法停用這些保護<br>• 定期驗證並把鏈尾錨定到資料庫以外（檔案與系統日誌）<br>• 設定轉送到 Graylog 時，每筆記錄帶著自己與前一筆的雜湊，錨定點也一併送出<br>• 這些防修改限制適用於應用程式的資料庫角色：資料庫超級使用者與主機管理者仍可繞過<br>• 錨定的檔案與系統日誌也在同一台主機上，不能單獨當作獨立證據，要搭配另行保管的錨定資料與外部的證據儲存機制 | [`test_audit_coverage.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_audit_coverage.py), [`test_audit_read_coverage.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_audit_read_coverage.py), [`test_audit_immutability.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_audit_immutability.py), [`test_audit_hardening.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_audit_hardening.py), [`test_audit_anchor.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_audit_anchor.py), [`test_audit_chain_order.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_audit_chain_order.py); [`scripts/sql/audit-harden.sql`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/scripts/sql/audit-harden.sql) |
| 遠端主控台 | A.5.15、A.5.18、A.8.2 | • 看得到 IP 不等於能連線：該 IP 要開啟對應的主控台功能，而且使用者是管理員、對子網路有寫入權，或被明確授予主控台權限<br>• 目標不能是本機、link-local 或保留位址<br>• 連線使用 60 秒內有效的單次票證（綁定使用者、IP 與工作階段）<br>• 帳號被停用、被強制登出、登出那個工作階段或被收回權限後，新的連線要求立即拒絕<br>• 已經開著的連線在下一次權限檢查時中斷並寫稽核（每 30 秒檢查一次，最長 30 秒）<br>• SFTP 的開啟、上傳、下載、改名、刪除都寫稽核，每個主控台連線結束時記錄連線時間 | [`test_ticket_take_once.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_ticket_take_once.py), [`test_console_guard.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_console_guard.py), [`test_net_guard.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_net_guard.py), [`test_sftp_permissions.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_sftp_permissions.py), [`test_console_session_duration.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_console_session_duration.py) |
| 憑證與私鑰派送 | A.8.3、A.8.24 | • 每個憑證代理只拿得到指派給它的憑證<br>• 代理每次下載、管理員每次匯出私鑰都寫稽核<br>• 代理金鑰可輪替、停用或刪除 | [`test_cert_agents_api.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_cert_agents_api.py) |
| 掃描代理 | A.5.16、A.8.3 | • 代理只會收到指派給它的子網路去掃描，回報也只在指派的子網路內比對（沒有指派任何子網路的代理不能更新任何 IP）<br>• 每個代理一把金鑰（只存雜湊），可輪替、停用或刪除<br>• 掃描、憑證與 RustDesk 代理的端點逐代理限流<br>• 同一台代理的回報排隊處理，單次回報筆數有上限 | [`test_scan_agent_scope.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_scan_agent_scope.py), [`test_scan_agent_report_concurrency.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_scan_agent_report_concurrency.py), [`test_scan_agent_load.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_scan_agent_load.py) |
| 資料來源與時間 | A.5.9、A.8.9 | • 每個 IP 的主機名稱、MAC、ARP 觀測都記錄來源與觀測時間，欄位異動有異動記錄（誰、哪個來源、舊值與新值）<br>• 手動輸入的值依來源優先序或釘選保留（預設手動最優先），不會被自動同步覆蓋<br>• 異常偵測標出疑似讀壞的資料與可信度 | [`test_hostname_sources.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_hostname_sources.py), [`test_ip_edit_manual_sources.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_ip_edit_manual_sources.py), [`test_evidence_contract.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_evidence_contract.py), [`test_anomaly_arp_quality.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_anomaly_arp_quality.py) |
| 匯出與對外嵌入 | A.5.14、A.8.3 | • 表格匯出只包含使用者看得到的資料，每次匯出都留稽核（伺服器產生的由伺服器記，瀏覽器產生的由瀏覽器回報）<br>• 系統匯出限管理員，並以密碼加密<br>• 機櫃圖對外嵌入需要系統權杖，而且要逐一開啟每個機櫃<br>• 機櫃嵌入與 Graylog DSV 的權杖會到期（30 到 365 天） | [`test_audit_read_coverage.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_audit_read_coverage.py), [`frontend/src/utils/__tests__/saveFileOnly.test.ts`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/frontend/src/utils/__tests__/saveFileOnly.test.ts), [`test_rack_embed.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_rack_embed.py), [`test_public_endpoint_tokens.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_public_endpoint_tokens.py), [`test_system_transfer.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_system_transfer.py) |
| 備份與還原 | A.8.13、A.8.24 | • 每天自動備份資料庫、設定檔（含加密金鑰）、TLS 憑證與上傳的檔案，保留 14 天<br>• 設定備份密碼後，每天的備份整包加密成一個檔案（scrypt 加 AES-256-GCM 分塊加密，截斷、調換或修改都解不開），明文刪除，解密工具只需要 Python 與 cryptography<br>• 系統診斷顯示最近一次備份的結果與是否加密<br>• 還原步驟寫在安裝維運手冊 | [`test_backup_script.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_backup_script.py), [`test_backup_encryption.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_backup_encryption.py), [`test_self_check_backup.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_self_check_backup.py), [`docs/INSTALL.md`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/docs/INSTALL.md) |
| 安全開發與弱點管理 | A.8.8、A.8.25、A.8.28、A.8.29 | • 每次推送由 CI 執行 bandit 安全規則、pip-audit、pnpm audit，以及對著實際 nginx 設定與正式建置前端的 OWASP ZAP 基準掃描（基準檔以外的任何警示都讓 CI 失敗）<br>• GitHub 程式碼掃描（CodeQL）的警示逐項判讀<br>• 軟體物料清單（SBOM，CycloneDX 1.5）：每個站台可在「管理 > 版本資訊」下載（或執行 jt-ipam.sh sbom），內容是實際安裝的 Python 套件與打包進畫面的 npm 套件，每個版本 tag 由 CI 產生一份、通過 schema 驗證後附在 GitHub Release<br>• 發版前另跑登入後的 ZAP 掃描、在乾淨系統實測全新安裝與升級<br>• 每次改動對應的檢查項目與守門測試寫在測試清單 | [`.github/workflows/ci.yml`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/.github/workflows/ci.yml), [`.github/workflows/sbom.yml`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/.github/workflows/sbom.yml), [`test_sbom.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_sbom.py), [`deploy/zap-baseline.conf`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/deploy/zap-baseline.conf), [`TEST_CHECKLIST.md`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/TEST_CHECKLIST.md), [`scripts/test-fresh-install.sh`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/scripts/test-fresh-install.sh), [`scripts/test-upgrade.sh`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/scripts/test-upgrade.sh) |

### 對應的條文與控制項（ISO/IEC 27001:2022）

編號依 ISO/IEC 27001:2022 附錄 A（與 ISO/IEC 27002:2022 相同），不帶 A. 的是本文條文；中文與日文名稱為參考譯名，以標準原文為準。jt-ipam 提供的是技術面的佐證，控制項是否達成由導入組織依風險評鑑與適用性聲明判定。附錄 A 共 93 項，jt-ipam 能提供佐證的有 22 項；下表另列出「導入組織要做的事」對應的條文與控制項，表中沒有的控制項由導入組織以其他方式處理。

| 編號 | 名稱 | jt-ipam 提供佐證的面向 | 導入組織要做的事 |
|---|---|---|---|
| 6.1.2 | 資訊安全風險評鑑 |  | 管理制度 |
| 6.1.3 | 資訊安全風險處理（含適用性聲明） |  | 管理制度 |
| 7.2 | 適任性 |  | 管理制度 |
| 7.3 | 認知 |  | 管理制度 |
| 9.2 | 內部稽核 |  | 管理制度 |
| 9.3 | 管理階層審查 |  | 管理制度 |
| A.5.9 | 資訊及其他相關聯資產之清冊 | 資料來源與時間 |  |
| A.5.14 | 資訊傳送 | 傳輸加密、匯出與對外嵌入 |  |
| A.5.15 | 存取控制 | 物件權限與跨單位隔離、遠端主控台 |  |
| A.5.16 | 身分管理 | 登入、雙因素驗證與工作階段、掃描代理 |  |
| A.5.17 | 鑑別資訊 | 登入、雙因素驗證與工作階段、API 權杖與 MCP、機密資料加密 | 帳號、整合帳號 |
| A.5.18 | 存取權限 | 物件權限與跨單位隔離、遠端主控台 | 離職、調職、存取審查、掃描代理與憑證代理 |
| A.5.28 | 證據之蒐集 | 稽核記錄 | 稽核證據 |
| A.5.30 | 營運持續之 ICT 備妥性 |  | 金鑰與備份 |
| A.5.33 | 紀錄之保護 | 稽核記錄 |  |
| A.6.5 | 聘用終止或變更後之責任 |  | 離職、調職 |
| A.8.2 | 特殊存取權限 | 物件權限與跨單位隔離、遠端主控台 | 存取審查、整合帳號 |
| A.8.3 | 資訊存取限制 | 物件權限與跨單位隔離、API 權杖與 MCP、憑證與私鑰派送、掃描代理、匯出與對外嵌入 |  |
| A.8.5 | 安全鑑別 | 登入、雙因素驗證與工作階段、API 權杖與 MCP | 帳號 |
| A.8.8 | 技術脆弱性之管理 | 安全開發與弱點管理 | 更新 |
| A.8.9 | 組態管理 | 資料來源與時間 |  |
| A.8.13 | 資訊備份 | 備份與還原 | 金鑰與備份 |
| A.8.15 | 存錄（日誌） | 稽核記錄 | 稽核證據 |
| A.8.16 | 監視活動 | 稽核記錄 |  |
| A.8.20 | 網路安全 | 出站連線防護 | 網路 |
| A.8.22 | 網路區隔 |  | 網路 |
| A.8.24 | 密碼技術之使用 | 機密資料加密、傳輸加密、憑證與私鑰派送、備份與還原 |  |
| A.8.25 | 安全開發生命週期 | 安全開發與弱點管理 |  |
| A.8.26 | 應用程式安全要求事項 | 出站連線防護 |  |
| A.8.28 | 安全程式設計 | 安全開發與弱點管理 |  |
| A.8.29 | 開發及驗收中之安全測試 | 安全開發與弱點管理 |  |
| A.8.32 | 變更管理 |  | 更新 |

## ISO/IEC 42001:2023：AI 功能與控制

### AI 功能清冊

| AI 功能 | 用途 | 預設 |
|---|---|---|
| AI 對話 | 用自然語言查詢 IPAM，模型透過工具取得資料 | 啟用 LLM 後可用 |
| 語意搜尋 | 以嵌入模型做相似度搜尋 | 啟用 LLM 後可用 |
| IP 調查判讀、IP 研判卡 | 把已查到的事實整理成說明 | 啟用 LLM 後可用 |
| 防火牆規則異動判讀 | 解釋程式比對出的規則差異 | 啟用 LLM 後可用 |
| IP 變更評估的 AI 建議 | 依評估結果提出待辦建議 | 啟用 LLM 後可用 |
| AI 巡檢 | 排程檢視資料並列出發現 | 關閉 |
| 外部 MCP | 讓外部 AI 用戶端呼叫 jt-ipam 的工具 | 關閉 |

模型端點可以是自架的 Ollama，或 OpenAI 相容的 API（由管理員選擇）。LLM 未啟用時，jt-ipam 不會主動呼叫模型；AI 巡檢另有開關。外部 MCP 不受 LLM 開關影響：外部 AI 用戶端用的是它自己的模型，由「對外提供 MCP」開關與權杖、MCP 金鑰控制。異常偵測、規則異動偵測與 IP 變更評估的結果由程式查詢算出，不是模型產生的。預期用途是查詢與說明 IPAM 資料、整理已經查到的事實；AI 的回答與判讀是輔助，不是決定，會異動資料的動作一律要人確認。

### AI 控制

| 面向 | 對應控制項 | jt-ipam 的做法 | 驗證方式（測試、設定） |
|---|---|---|---|
| AI 權限一致 | A.9.2 | • AI 對話與 MCP 的工具在後端逐一授權，用的是與 REST 相同的可見範圍<br>• 會異動資料的工具限管理員 | [`test_mcp_tools_match_rest_permissions.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_mcp_tools_match_rest_permissions.py), [`test_mcp_admin_tools.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_mcp_admin_tools.py), [`test_mcp_rbac_scope.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_mcp_rbac_scope.py) |
| 異動要人確認 | A.9.2 | • AI 對話裡會改資料的工具不會直接執行，要使用者按下確認才執行<br>• 確認時再過一次權限檢查 | [`test_ai_inline_toolcalls.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_ai_inline_toolcalls.py), [`test_ai_guard.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_ai_guard.py) |
| 查詢範圍與筆數 | A.7.4、A.8.2 | • 工具回傳查詢範圍、總數、本次筆數與是否還有更多<br>• 超過上限會明確標示截斷<br>• 查無結果時回傳明確的空結果，並提示模型不可補造資料<br>• 這些是工具輸出與給模型的提示：模型最終怎麼回答取決於所用的模型，要以實際使用的模型與版本另行驗證（見導入組織的「模型變更」） | [`test_mcp_tool_scoping.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_mcp_tool_scoping.py), [`test_ai_no_results_are_explicit.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_ai_no_results_are_explicit.py) |
| AI 讀到的資料品質 | A.7.4 | • AI 工具讀的是 IPAM 的資料，每個值記錄來源與觀測時間（見「資料來源與時間」），主機名稱看得出是哪一台代理或哪個整合回報、最後什麼時候看到<br>• 整合同步失敗顯示在整合頁與作業頁，IP 變更評估列出沒有資料或同步失敗的來源<br>• 異常偵測與 IP 鑑識工具標出疑似讀壞或過期的 ARP 資料與可信度<br>• 查詢範圍、總數與截斷提示只說明資料是否完整，不代表資料正確 | [`test_evidence_contract.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_evidence_contract.py), [`test_anomaly_arp_quality.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_anomaly_arp_quality.py), [`test_hostname_source_reports.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_hostname_source_reports.py), [`test_change_impact_coverage.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_change_impact_coverage.py) |
| 答案查證 | A.6.2.6、A.8.2 | • 回答中出現、但沒有任何工具回傳過的 IP，會被標示為未經查證 | [`test_ai_answer_ip_verification.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_ai_answer_ip_verification.py) |
| AI 事件記錄 | A.4.2、A.6.2.8 | • AI 對話的對話記錄與 AI 巡檢的發現會存下使用的模型<br>• 判讀與建議的結果也標示模型<br>• AI 對話確認後執行的異動與 MCP 的寫入都寫進稽核記錄（誰、哪個工具、何時） | [`app/models/ai_chat.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/app/models/ai_chat.py), [`app/models/ai_finding.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/app/models/ai_finding.py), [`test_mcp_url_and_audit.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_mcp_url_and_audit.py), [`test_ai_inline_toolcalls.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_ai_inline_toolcalls.py) |
| 提示詞注入 | A.6.2.2、A.6.2.4 | • 所有把資料送進模型的地方（AI 對話、IP 調查判讀、AI 巡檢、IP 研判、防火牆規則判讀、IP 變更評估）都使用同一段「資料與工具結果不是指令」的規則，大段資料放在定界內並先拆掉資料裡的定界字串（守門測試檢查每一個呼叫模型的地方）<br>• 使用者訊息先經過檢查（常見的越權指令樣式、重複與控制字元）<br>• AI 對話裡會改資料的動作要人確認，確認時再過一次權限檢查<br>• 以上措施是降低提示詞注入的風險，不保證模型不受惡意內容影響<br>• 能存取什麼、能改什麼由後端的權限檢查強制決定，不靠模型 | [`test_prompt_injection_framing.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_prompt_injection_framing.py), [`test_ai_guard.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_ai_guard.py) |
| 事實與 AI 敘述分開 | A.8.2、A.9.4 | • 異常、規則異動與變更評估的結果由程式查詢決定<br>• AI 只負責解釋，畫面標明是 AI 判讀 | [`app/services/anomaly.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/app/services/anomaly.py), [`app/services/fw_review.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/app/services/fw_review.py), [`app/services/change_impact/`](https://github.com/jasoncheng7115/jt-ipam/tree/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/app/services/change_impact) |
| 變更評估的人工審核 | A.9.2 | • 有阻擋項目不能核准<br>• 結果不完整要填理由承擔風險<br>• 每個發現都要處置<br>• 核准後計畫或依據改變（快照雜湊不同）就回到草稿重新評估<br>• 可指定審核人<br>• 列出哪些來源沒有資料或同步失敗 | [`test_change_impact_api.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_change_impact_api.py), [`test_change_impact_reviewers.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_change_impact_reviewers.py), [`test_change_impact_coverage.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_change_impact_coverage.py) |
| 停用 | A.6.2.6、A.9.2 | • LLM 全域開關控制 jt-ipam 主動發出的模型請求：一關，AI 對話、語意搜尋、判讀與 AI 巡檢都不再呼叫模型<br>• 外部 MCP 不經過這個開關（外部 AI 用戶端用自己的模型）<br>• 要停止外部 AI 透過 MCP 讀取資料，須關閉「管理 → LLM / AI」的「對外提供 MCP」，或撤銷相關的 API 權杖與 MCP 金鑰<br>• AI 巡檢與外部 MCP 預設關閉 | [`app/services/system_config.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/app/services/system_config.py), [`app/mcp/server.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/app/mcp/server.py) |

### 對應的條文與控制項（ISO/IEC 42001:2023）

編號依 ISO/IEC 42001:2023 附錄 A，不帶 A. 的是本文條文；中文與日文名稱為參考譯名，以標準原文為準。附錄 A 共 38 項，jt-ipam 能提供佐證的有 9 項；下表另列出「導入組織要做的事」對應的條文與控制項。

| 編號 | 名稱 | jt-ipam 提供佐證的面向 | 導入組織要做的事 |
|---|---|---|---|
| 5.2 | AI 政策 |  | AI 政策與清冊 |
| 6.1.3 | AI 風險處理（含適用性聲明） |  | 管理制度 |
| 6.1.4 | AI 系統衝擊評鑑 |  | 影響評估 |
| 7.2 | 適任性 |  | 管理制度 |
| 7.3 | 認知 |  | 管理制度 |
| 8.4 | AI 系統衝擊評鑑（運作） |  | 影響評估 |
| 9.2 | 內部稽核 |  | 管理制度 |
| 9.3 | 管理階層審查 |  | 管理制度 |
| A.2.2 | AI 政策 |  | AI 政策與清冊 |
| A.3.2 | AI 角色與責任 |  | AI 政策與清冊 |
| A.3.3 | 關切事項之通報 |  | 事件處理 |
| A.4.2 | 資源之文件化 | AI 事件記錄、AI 功能清冊 | AI 政策與清冊 |
| A.5.2 | AI 系統衝擊評鑑過程 |  | 影響評估 |
| A.5.4 | 評鑑 AI 系統對個人或群體之衝擊 |  | 影響評估 |
| A.6.2.2 | AI 系統要求事項與規格 | 提示詞注入 |  |
| A.6.2.4 | AI 系統之驗證與確認 | 提示詞注入 | 模型變更 |
| A.6.2.5 | AI 系統部署 |  | 模型變更 |
| A.6.2.6 | AI 系統運作與監視 | 答案查證、停用 |  |
| A.6.2.8 | AI 系統事件日誌之紀錄 | AI 事件記錄 |  |
| A.7.4 | AI 系統資料之品質 | 查詢範圍與筆數、AI 讀到的資料品質 |  |
| A.8.2 | 系統文件及提供使用者之資訊 | 查詢範圍與筆數、答案查證、事實與 AI 敘述分開、AI 功能清冊 |  |
| A.8.4 | 事故之溝通 |  | 事件處理 |
| A.9.2 | 負責任使用 AI 系統之過程 | AI 權限一致、異動要人確認、變更評估的人工審核、停用 | 人工覆核 |
| A.9.4 | AI 系統之預期用途 | 事實與 AI 敘述分開 | 預期用途 |
| A.10.3 | 供應者 |  | 資料流 |

## 導入組織要負責的事

### 資訊安全（ISO/IEC 27001:2022）

- 帳號：在使用者管理頁的「登入安全」把雙因素驗證設成管理員必須或所有人必須，並定期確認例外。（A.5.17、A.8.5）
- 離職：停用帳號，不要只用「強制登出」（強制登出只結束工作階段，帳號仍能再登入，個人 API 權杖也還有效）。停用會撤銷所有工作階段與這個人的個人 API 權杖、拒絕之後的每個請求，開著的主控台在下一次權限檢查時中斷（最長 30 秒）；確認這些都已失效。用 LDAP、OIDC 或 SAML 登入的帳號同時在身分提供者停用；這個人保管過的共用憑證（唯讀 MCP 金鑰、整合帳號的密碼、代理金鑰）要輪替。（A.5.18、A.6.5）
- 調職：調整權限指派與群組成員；權限每個請求都重新讀取，下一個請求就以新權限判斷，個人 API 權杖跟著帳號權限改變，開著的主控台最長 30 秒內依新權限中斷。必要時用「強制登出」讓本人重新登入，並確認新權限正確。（A.5.18、A.6.5）
- 存取審查：定期檢視權限指派、群組成員、API 權杖、主控台授權與登入中的工作階段，必要時對照稽核記錄。（A.5.18、A.8.2）
- 整合帳號：每個整合使用專用的最小權限帳號，多數整合只需要唯讀；定期輪替密碼與金鑰；到期通知出現時及時更換權杖。（A.5.17、A.8.2）
- 金鑰與備份：設定備份加密密碼並把密碼儲存在 jt-ipam 主機以外；把備份放到異地儲存，定期做還原演練（含解密），並訂出 RPO 與 RTO。（A.5.30、A.8.13）
- 稽核證據：把稽核記錄與錨定點轉送到另一台主機的 Graylog 或 SIEM，並在那一端另行設定不可改寫的儲存機制（轉送本身不提供這個能力）；限制誰能取得資料庫超級使用者與主機管理權限；定期查看稽核鏈的驗證結果；還原備份或使用外部資料庫時執行 harden-audit。（A.5.28、A.8.15）
- 網路：只開放必要的連入埠；把 jt-ipam 放在管理網段；Graylog DSV 盡量走 HTTPS，必須用明文 8088 時限制允許的來源位址。（A.8.20、A.8.22）
- 掃描代理與憑證代理：只指派需要的子網路與憑證；不再使用的代理停用或刪除，定期輪替代理金鑰。（A.5.18）
- 更新：追蹤新版本並依 CHANGELOG 升級（升級腳本會先備份）。（A.8.8、A.8.32）
- 管理制度：風險評鑑與處理、適用性聲明、教育訓練、內部稽核與管理審查由導入組織負責。（6.1.2、6.1.3、7.2、7.3、9.2、9.3）

### AI 管理（ISO/IEC 42001:2023）

- AI 政策與清冊：決定要開哪些 AI 功能、由誰負責，記錄模型端點與版本。（5.2、A.2.2、A.3.2、A.4.2）
- 資料流：選擇自架模型或外部 API；若開放外部 MCP，外部 AI 用戶端拿到的資料可能再交給它自己的雲端模型處理，這條資料流要另外評估與核准。（A.10.3）
- 模型變更：換模型或調整參數前，用一組固定問題做回歸測試（工具使用、查詢範圍、查無結果時的回答）。（A.6.2.4、A.6.2.5）
- 人工覆核：AI 的回答與巡檢發現是建議，不是結論；網路變更走 IP 變更評估與審核流程。（A.9.2）
- 預期用途：訂出 AI 功能允許的用途（例如查詢、說明、整理事實）、使用限制（例如不得當作資安判定或人員責任歸屬的唯一依據），以及哪些情況要人工覆核，並告知使用者。（A.9.4）
- 影響評估：評估錯誤判讀對人與組織的影響，例如誤把設備歸給某位使用者、錯誤的維護建議造成服務中斷。（6.1.4、8.4、A.5.2、A.5.4）
- 事件處理：訂出什麼情況算 AI 事故（判定條件）、要通知誰（使用者、管理者、受影響的單位）、由誰負責與處理時限，以及回報 AI 錯誤與暫停 AI 功能的程序（LLM 全域開關、「對外提供 MCP」開關、撤銷權杖）。jt-ipam 的通知事件與通道可以用來發送，對象、條件與時限由導入組織訂定。（A.3.3、A.8.4）
- 管理制度：適用性聲明、教育訓練、內部稽核與管理審查由導入組織負責。（6.1.3、7.2、7.3、9.2、9.3）

## 建議的驗收案例

| 驗收案例 | 合格判定 | 驗證方式（測試、設定） |
|---|---|---|
| A、B 單位使用相同的私有網段 | A 透過搜尋、IP 關聯、AI 對話與 MCP 都拿不到 B 的資料 | [`test_search_overlapping.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_search_overlapping.py), [`test_mcp_rbac_scope.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_mcp_rbac_scope.py) |
| 唯讀權杖或一般帳號要求修改 IP | 後端拒絕，資料沒有變更 | [`test_api_token_scope.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_api_token_scope.py), [`test_mcp_admin_tools.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_mcp_admin_tools.py) |
| 查詢結果超過單次上限 | 回傳總數與本次筆數，不把部分清單當成全部 | [`test_mcp_tool_scoping.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_mcp_tool_scoping.py) |
| 查無資料 | 工具回傳明確的空結果與不可補造資料的提示（模型最終的回答另以實際使用的模型與版本驗證） | [`test_ai_no_results_are_explicit.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_ai_no_results_are_explicit.py) |
| 核准後依據改變 | 舊核准不能直接套用，要重新評估 | [`test_change_impact_api.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_change_impact_api.py) |
| 撤銷 API 權杖或停用帳號 | 下一個 REST、API 權杖或 MCP 請求就被拒絕 | [`test_api_token_scope.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_api_token_scope.py), [`app/api/v1/dependencies.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/app/api/v1/dependencies.py) |
| 用應用程式的資料庫帳號修改或刪除稽核記錄 | 資料庫拒絕；鏈尾錨定能發現尾端被截掉 | [`test_audit_immutability.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_audit_immutability.py), [`test_audit_anchor.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_audit_anchor.py) |
| 重複使用主控台票證 | 第二次使用被拒絕 | [`test_ticket_take_once.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_ticket_take_once.py) |
| 管理員要求所有人使用雙因素驗證 | 沒設定的人登入時先設定並拿到復原碼，不能自行停用；復原碼只能用一次 | [`test_mfa_policy.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_mfa_policy.py) |
| 登出、強制登出或停用帳號 | 存取權杖與更新權杖立即失效、新的主控台連線立即拒絕；已開著的主控台在下一次權限檢查時中斷，最長 30 秒 | [`test_session_revocation.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_session_revocation.py), [`test_console_guard.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_console_guard.py) |
| 已經換掉的更新權杖又被使用 | 整個工作階段撤銷，通知管理員與本人 | [`test_session_revocation.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_session_revocation.py) |
| 用應用程式的資料庫帳號停用稽核表的保護或清空稽核表 | 資料庫拒絕 | [`test_audit_hardening.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_audit_hardening.py), [`scripts/sql/audit-harden.sql`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/scripts/sql/audit-harden.sql) |
| 加密備份被截斷、調換或修改 | 解密失敗，不會還原出被動過的資料 | [`test_backup_encryption.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_backup_encryption.py) |
| 對 127.0.0.1 或 169.254.169.254 的 IP 開主控台 | 被拒絕並說明原因 | [`test_net_guard.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_net_guard.py) |
| 主機名稱裡夾帶給模型的指令 | 提示詞把它標成資料並放在定界內，資料拆不掉定界 | [`test_prompt_injection_framing.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_prompt_injection_framing.py) |
| 憑證代理要求別的憑證 | 回應不存在，沒有內容外流 | [`test_cert_agents_api.py`](https://github.com/jasoncheng7115/jt-ipam/blob/e4f339800ffa63b8242e806b16c63a5b5bafc0fe/backend/tests/test_cert_agents_api.py) |

右欄的測試在每次發版前執行；導入組織可以在自己的環境用同樣的情境驗收。

驗證紀錄：上面的測試與設定檔連結固定在 commit e4f33980（2026-11-19 公開）。這個 commit 在 GitHub Actions 的 CI（後端與前端測試、安全規則、相依套件弱點、OWASP ZAP 基準掃描）全部通過；同一份程式在發版前的完整測試：後端 5,252 項通過、46 項略過，前端 1,157 項通過（2026-11-19）。導入組織應以固定的版本，在自己的環境重新執行。 [CI 執行結果](https://github.com/jasoncheng7115/jt-ipam/actions/runs/44474788408)

## 盤點表欄位

盤點時可以沿用以下欄位，一列一個控制項目：

`項目編號 | 標準與管理面向 | 適用功能 | 狀態 | 程式碼或設定位置 | 驗收方式 | 測試證據 | 缺口 | 負責人 | 完成期限`
