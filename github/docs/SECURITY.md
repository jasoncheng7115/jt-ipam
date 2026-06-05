# jt-ipam 安全設計（OWASP Top 10:2025 對齊）

> English: [SECURITY.en.md](SECURITY.en.md)

> 本文件是 jt-ipam 專案的**強制性**安全規範。每個 PR 與功能設計都必須通過此處的自我檢核清單。
>
> 對齊版本：[OWASP Top 10:2025](https://owasp.org/Top10/)
> 維護者：Jason Tools 安全團隊
> 最近更新：2026-05-23（從 2021 升到 2025）

> **與 2021 的差異**：
> - A02 從 Cryptographic Failures 換成 **Security Misconfiguration**（雲端時代最大宗）
> - A03 從 Injection 換成 **Software Supply Chain Failures**（取代並擴展 Vulnerable Components）
> - A04 Cryptographic Failures（從 A02 降下來）
> - A05 Injection（從 A03 降下來，因 ORM / 框架普及減少了發生率）
> - A07 名稱簡化為 **Authentication Failures**
> - A09 從 Logging and **Monitoring** Failures 改為 Logging and **Alerting** Failures（更強調「會叫」）
> - **A10 全新**：**Mishandling of Exceptional Conditions** 取代 SSRF；SSRF 不再單列，併入 A06 Insecure Design

---

## 〇、總則

1. **Security by Design**：安全是第一次寫的時候就要做對，不是事後修補。
2. **Defense in Depth**：每一層（網路、應用、資料、人員）都要有防線，任何一層被突破不導致整體淪陷。
3. **Least Privilege**：使用者、容器、服務帳號、API Token 一律最小權限；deny-by-default。
4. **Fail Closed**：認證失敗、授權失敗、設定缺失、例外發生時，預設**拒絕**而非放行（A10 新類別的核心）。
5. **Auditable**：所有寫入操作都要可追溯（誰、何時、做了什麼），SHA-256 雜湊鏈保證防竄改。
6. **No Secrets in Repo**：任何密碼、金鑰、Token 都不得進入 git 歷史；使用 `.env` + 秘密管理。

---

## 一、A01:2025 — Broken Access Control

### 1.1 威脅
- 水平/垂直越權存取
- IDOR（Insecure Direct Object Reference）
- 權限繞過、強制瀏覽、強制改 URL

### 1.2 設計
- **RBAC**：User Group + Role，預設 deny；明確授權才放行。
- **物件級權限**：Section / Subnet / Tenant 各自有 ACL；存取每個物件前都要檢查。
- **API endpoint 強制檢查**：每個 endpoint 套 `Depends(require_permission("ipam.subnet.read", subnet_id))` 風格的 dependency；不靠路由邏輯隱含權限。
- **Bulk 操作逐筆檢查**：批次刪除/更新不能因為效能繞過權限。
- **API Token scope**：每個 token 限定 endpoint pattern + 物件範圍。
- **Tenant 隔離**：啟用 Tenancy 模組時，跨 tenant 查詢必須由 query 層自動加 `tenant_id` 條件。
- **不能刪最後一個 active admin**：應用層 + DB 層雙重保護。

### 1.3 測試
- pytest fixture 模擬不同 role 的使用者，每個 endpoint 都要有 403 case。
- 自動化掃描：橫向越權測試（A 使用者拿 B 使用者的 ID）。
- 已有測試：`test_users_groups_audit.py::test_cannot_delete_last_admin`。

---

## 二、A02:2025 — Security Misconfiguration（從 2021 A05 升上來）

> 2025 年雲端設定錯誤已成最大宗風險，從 A05 升到 A02。

### 2.1 威脅
- Debug 資訊洩漏
- 預設帳密
- 不必要的 services / ports
- 缺乏 HTTP 安全 headers
- 雲端 IAM / storage 開放權限

### 2.2 設計
- **Production guard**：`APP_ENV=production` 時：
  - `APP_DEBUG=false`
  - 禁用 `/docs`、`/redoc`（或要求 admin role）
  - 不回傳 stack trace 給 client（與 A10 重疊）
  - 不允許預設密碼或範例密碼
- **HTTP Headers**（middleware 統一注入）：
  - `Strict-Transport-Security: max-age=63072000; includeSubDomains; preload`
  - `Content-Security-Policy: ...`（見 §5）
  - `X-Frame-Options: DENY`
  - `X-Content-Type-Options: nosniff`
  - `Referrer-Policy: strict-origin-when-cross-origin`
  - `Permissions-Policy: geolocation=(), microphone=(), camera=()`
  - `Cross-Origin-Opener-Policy: same-origin`
  - 移除可洩露版本資訊的預設 header（如 `Server`）
- **CORS**：白名單來源；prod 禁止 `*`。
- **systemd hardening**（不採容器化；見 `deploy/systemd/jt-ipam-backend.service`）：
  - `User=jtipam` / `Group=jtipam`（非 root）
  - `NoNewPrivileges=true`、`CapabilityBoundingSet=`（drop 全部）、`AmbientCapabilities=`
  - `ProtectSystem=strict`（整個 `/` 唯讀，僅 `ReadWritePaths` 可寫）
  - `ProtectHome=true`、`PrivateTmp=true`、`PrivateDevices=true`
  - `ProtectKernelTunables/Modules/Logs=true`、`ProtectControlGroups=true`、`ProtectClock=true`、`ProtectHostname=true`、`ProtectProc=invisible`
  - `RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6`、`RestrictNamespaces=true`、`RestrictRealtime=true`、`RestrictSUIDSGID=true`
  - `LockPersonality=true`、`MemoryDenyWriteExecute=true`
  - `SystemCallArchitectures=native`、`SystemCallFilter=@system-service` 並排除 `@privileged @resources @obsolete @debug`
  - `LimitNOFILE=65536` / `TasksMax=1024`
  - 驗證：`systemd-analyze security jt-ipam-backend`（目標分數 ≤ 3.5；目前實測 1.3 ）
- **預設帳密**：首次啟動強制 admin 改密碼；安裝程式產生隨機密碼而非寫死。
- **健康檢查端點**：`/healthz`（liveness）只回 200，不洩漏內部資訊。

---

## 三、A03:2025 — Software Supply Chain Failures（取代 Vulnerable Components）

> 2025 把 Vulnerable Components 改名擴展為**整個供應鏈**：依賴、CI/CD、建置工具、釋出管道。

### 3.1 威脅
- 過時的 dependency 含 CVE
- 未鎖定版本，build 不可重現
- GitHub Actions / npm registry 被植入（typosquatting）
- 第三方 plugin 上鏈不可信
- container/wheel artifact 被中間人替換

### 3.2 設計
- **依賴鎖定**：
  - Python：`uv lock` / `poetry.lock` 進 git
  - Node：`pnpm-lock.yaml` 進 git；`packageManager: "pnpm@9.15.9"` 釘版本（避 corepack 拉新版炸）
- **CI 掃描**：
  - `pip-audit`（Python CVE）
  - `pnpm audit --audit-level=high`
  - `osv-scanner`（補強，Python + Node 雙軌）
  - `bandit`（Python SAST）
  - `apt list --upgradable` + `unattended-upgrades`（OS 層；LXC / 裸機定期 patch）
- **Dependabot / Renovate**：自動 PR 更新；CVSS ≥ 7 的 patch 24 小時內 review。
- **SBOM**：每次 release 產生 CycloneDX / SPDX SBOM。
- **Supply chain hardening**：
  - PyPI 用 `uv`（或 `pip` + `--require-hashes`）啟用 hash check
  - 釋出 `.deb` 與離線安裝包均以 GPG 簽章
  - 鎖定 `apt` 來源（PGDG、official Debian/Ubuntu 倉庫；不啟用第三方 PPA 除非審核過）
  - **GitHub Actions 釘 SHA 而非 tag**（`uses: actions/checkout@<sha>`）
  - **plugin 簽章**：jt-ipam plugin 載入前驗 GPG / sigstore；未簽章只允許 admin 強制載入
  - **release tag GPG 簽章**：`git tag -s`
  - 阻擋直接 push main（必須 PR + review）

---

## 四、A04:2025 — Cryptographic Failures（從 2021 A02 降到 A04）

### 4.1 威脅
- 敏感資料明文儲存（DNS 帳密、SNMP community、API token、WinRM 密碼）
- 弱密碼雜湊（MD5/SHA-1/未加 salt）
- TLS 設定錯誤（過舊版本、自簽憑證未驗）

### 4.2 設計
- **密碼雜湊**：argon2id（`time_cost=3, memory=64MiB, parallelism=4`）；不允許 bcrypt/sha256。
- **應用層欄位加密**：DB 中敏感欄位使用 AES-256-GCM 雙欄（`*_enc` + `*_nonce`）；aad 綁 instance id 防 nonce reuse 跨實例。金鑰來自 `ENCRYPTION_KEY` 環境變數，正式環境改用 KMS（AWS / Vault Transit）。
- **加密欄位列表**：
  - DNS Server credentials（PowerDNS API key、BIND TSIG、Windows WinRM 密碼、OPNsense API secret）
  - OPNsense Firewall API key/secret（雙欄、aad 綁 firewall id）
  - Wazuh API password（aad 綁 instance id）
  - LibreNMS API token、Scan agent token
  - SNMP community（v1/v2c）、SNMPv3 auth/priv password
  - 使用者 TOTP secret
  - Webhook secret（僅建立時顯示一次）
- **TLS（強制 SSL — 任何環境）**：使用者瀏覽器到 jt-ipam 的傳輸層必須是 HTTPS；**不存在 HTTP-only 部署選項**。`config.py._tls_guards` 會在啟動時擋下任何 `http://` 的 `APP_PUBLIC_URL` / `API_PUBLIC_URL`。
  - 兩種支援模式：
    - **`BACKEND_TLS_MODE=nginx`**（預設）：uvicorn 綁 `127.0.0.1:8000` 純 HTTP loopback，nginx 終結 TLS。
    - **`BACKEND_TLS_MODE=direct`**：uvicorn 直接吃 PEM cert/key。
  - 自簽憑證走 ECDSA P-384 / SHA-384 / 5 年。
  - 對外整合呼叫（DNS server / LibreNMS / Webhook）強制 TLS 1.2+，建議 1.3；驗證憑證鏈，**不接受 `verify=False`**（OPNsense/Wazuh 有 `verify_tls` 旗標可關，但 prod 不應關）。
- **Cookie**：`Secure`、`HttpOnly`、`SameSite=Lax`（敏感操作 token 用 `Strict`）。
- **JWT**：HS512 用於 short-lived access token；refresh token 不放 LocalStorage，用 HttpOnly cookie。
- **金鑰輪替**：`SECRET_KEY` / `ENCRYPTION_KEY` 支援多版本（kid），可無縫輪替。

### 4.3 測試
- 單元測試：寫入 SNMP community 後，DB 欄位應為密文，且重啟後可正確解密。
- 已有測試：`test_firewall_opnsense.py::test_credential_encrypt_roundtrip` / `test_wazuh.py::test_password_encrypt_aad` 涵蓋 aad 防止跨實例解密。
- 滲透測試：dump DB 後不能從中還原任何敏感欄位。

---

## 五、A05:2025 — Injection（從 2021 A03 降到 A05）

> 2025 SQL injection 等已不如以往普遍，主因是 ORM / 框架普及；但仍是高影響漏洞。

### 5.1 威脅
- SQL Injection、NoSQL Injection
- LDAP Injection（AD/LDAP 認證）
- Command Injection（Scanner 呼叫 nmap、ping、SNMP CLI）
- XSS（IP 描述 / 自訂欄位 / Webhook payload）
- SSTI（Email 模板、PDF 報表）

### 5.2 設計
- **SQLAlchemy ORM 全程參數化**：禁止 `.execute(text(f"..."))` 等字串內插；如需 raw SQL，必須用 bound parameters。
- **Pydantic v2 嚴格驗證**：所有 API 輸入皆過 schema；StrictModel 設 `extra='forbid'`。
- **Email 模板沙箱**：Jinja2 用 `SandboxedEnvironment`；自訂模板不允許 `__class__`、`__mro__`、import。
- **Scanner 子程序**：呼叫 `nmap` / `ping` 時用 `subprocess.run([...], shell=False)`；目標 IP/CIDR 先過 `ipaddress` 模組驗證。
- **LDAP**：使用 `ldap3` library，DN 與 filter 用 escape 函式（`ldap3.utils.conv.escape_filter_chars`）。
- **XSS**：
  - Vue 3 預設 HTML 跳脫；禁止 `v-html` 對使用者輸入（lint 規則強制）。
  - 後端輸出 HTML（PDF 報表）用 markupsafe / jinja autoescape。
  - 設定 `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self';`
- **WinRM PowerShell**：傳入參數用 PowerShell 的 splatting / parameter binding，不字串拼接。

### 5.3 測試
- pytest 測 `'; DROP TABLE`、`<script>`、`{{7*7}}`、`$(rm -rf /)` 等 payload。
- CI 跑 `bandit` 掃 Python、`semgrep` 規則組。

---

## 六、A06:2025 — Insecure Design

> SSRF 在 2025 不再單列為 A10，**併入此項**。

### 6.1 威脅
- 缺乏威脅模型
- 業務邏輯瑕疵（IP 重複配發、重複申請、TOCTOU）
- 缺乏速率限制造成濫用 / 暴力破解
- **SSRF**：攻擊者透過設定 DNS Server / LibreNMS / Webhook 目標為 metadata IP / 內網 IP / file://，從伺服器內部發起請求；DNS rebinding 繞過驗證

### 6.2 設計（一般）
- **威脅模型**：每個新模組（DNS 整合、LibreNMS、AI）設計階段做 STRIDE 分析，記錄於 `docs/threat-models/`。
- **業務不變式**：在 DB 層做 unique constraint（IP+VRF 唯一、subnet 不重疊）；不只靠應用層檢查。
- **Rate Limiting**：
  - 全域：`100 req/min/IP`
  - 認證端點：`10 req/min/IP`（防暴力）
  - 寫入端點：`30 req/min/user`
  - 對外整合呼叫（DNS push、LibreNMS API）：用 token bucket 控速
- **冪等性**：所有寫入端點支援 `Idempotency-Key` header。
- **Anti-CSRF**：cookie 認證的端點要驗 CSRF token；API token 認證的端點不需要（無 ambient credential）。
- **預設關閉危險功能**：進階模組（Cabling/Power/...）、Webhook 出站、AI MCP Server 預設 disabled。

### 6.3 設計（SSRF 防護，原 2021 A10）
- **目標 URL 白名單**：
  - DNS Server / LibreNMS / Webhook / OPNsense / Wazuh URL 設定時，先做 DNS 解析；解析結果必須通過 CIDR 白名單檢查
  - 預設禁止：`127.0.0.0/8`、`169.254.0.0/16`（含 AWS metadata `169.254.169.254`、GCP/Azure metadata）、`::1`、`fe80::/10`、`fc00::/7`、`0.0.0.0/8`
  - 設定 `OUTBOUND_ALLOW_PRIVATE=true` 才允許 RFC1918（內網 IPAM 通常需要，但需明確開啟）
  - 額外白名單：`OUTBOUND_ALLOW_CIDRS` / `OUTBOUND_ALLOW_HOSTS`
- **DNS rebinding 防護**：建立 HTTP client 時 pin IP（用解析時的 IP 直接連，附 `Host` header），避免 TOCTOU。
- **Schema 限制**：只允許 `https://`（特例 `http://` 給內網服務，需明確設定）。禁止 `file://`、`gopher://`、`ftp://`、`dict://`、`ldap://`。
- **Redirect 限制**：HTTP redirect target 也必須通過白名單；最多 follow 3 次。
- **實作位置**：`backend/app/core/safe_http.py::safe_request`，所有外部呼叫**必須**走這支。直接用 `httpx.AsyncClient(...)` 視為違規（lint 規則 + code review 阻擋）。

---

## 七、A07:2025 — Authentication Failures（從 Identification and Authentication Failures 簡化）

### 7.1 威脅
- 弱密碼、密碼重用、credential stuffing
- Session 固定、Session 劫持
- 缺乏 MFA
- User enumeration（response 訊息洩漏使用者是否存在）

### 7.2 設計
- **密碼政策**：最少 12 字、阻擋 HIBP 已知洩漏（haveibeenpwned k-anon API）；禁止常見字典詞。
- **TOTP MFA**：選用但管理員可強制；使用 `pyotp`，secret 加密存。
- **帳號鎖定**：失敗 5 次鎖 15 分鐘；同 IP 對多帳號失敗 20 次封 1 小時（防 stuffing）。Admin 可透過 `/users/{id}` PATCH `unlock=true` 解鎖。
- **Anti-enumeration**：找不到 user 也要走 dummy argon2 verify、回 401，避免從 timing / status 區分。
- **Session**：
  - 登入後 rotate session ID
  - Idle timeout 30 分鐘 / Absolute timeout 12 小時
  - Cookie HttpOnly + Secure + SameSite=Lax
- **API Token**：
  - 只在建立時顯示一次（hash 後儲存）
  - 必填到期日（最長 1 年）
  - scope 必填
  - 列表顯示「最後使用 IP / 時間」
- **OIDC / SAML**：完整支援（含 metadata / ACS / SLO），相關設定見 INSTALL.md §4。
- **登入紀錄**：所有登入（成功 / 失敗）寫入 audit + 結構化 log。

---

## 八、A08:2025 — Software or Data Integrity Failures

### 8.1 威脅
- 篡改 audit log
- CI/CD pipeline 被植入
- Update 過程被中間人

### 8.2 設計
- **SHA-256 異動鏈**：
  - 每筆 audit record 包含前一筆的 hash
  - 鏈頭來自 `AUDIT_CHAIN_GENESIS`（部署不可變）
  - `POST /api/v1/audit/verify` 提供 admin 鏈完整性驗證
  - 定期匯出 hash 至外部不可變儲存（jt-glogarch / S3 Object Lock）
- **Release 簽章**：每個 release artifact（`.tar.gz`、`.deb`、wheel 快取、Proxmox LXC 範本）用 GPG / sigstore 簽章。
- **Webhook 出站簽章**：HMAC-SHA256（`X-jt-ipam-Signature` header），對方可驗。Secret 只在建立時顯示一次。
- **Webhook 入站驗證**：對端來源亦需簽章（OPNsense / LibreNMS callback）。
- **Plugin 完整性**：plugin entry point 載入時若有 `*.sig` 同名簽章檔則必驗；未簽 plugin 只允許 admin 在設定中明確開啟。
- **CI/CD**：
  - GitHub Actions 鎖定 `uses: ...@<sha>` 而非 tag
  - 部署金鑰最小權限
  - 阻擋直接 push main（必須走 PR + review）

---

## 九、A09:2025 — Security Logging and Alerting Failures（從 Monitoring → Alerting）

> 2025 改名強調「不只記錄，要會叫」。光寫 log 沒人看等於沒做。

### 9.1 威脅
- 攻擊發生時沒留下足夠證據
- 異常事件沒人發現
- 告警通道失靈（Email box 滿、Slack token 過期）卻無感

### 9.2 設計
- **結構化日誌**：JSON 格式（timestamp、level、event、user_id、ip、resource、action、status_code、request_id）。
- **稽核事件清單**（必記）：
  - 登入成功/失敗、登出、密碼變更、MFA 啟用/停用
  - 權限變更、角色指派
  - 資料 CRUD（with diff）
  - API Token 建立/撤銷
  - 外部整合連線（DNS push、LibreNMS sync、OPNsense alias 推送、Wazuh agent 拉）
  - 設定變更
- **Graylog 外送**：GELF over TLS；jt-glogarch 異地備份。
- **主動告警**（A09 2025 重點）：
  - 連續失敗登入 → Telegram + Email
  - 高權限操作（建/刪 admin user）→ 即時 webhook
  - SHA-256 鏈斷裂 → 嚴重告警
  - 整合 sync 連續失敗 N 次 → webhook + UI 紅點
  - **Wazuh missing-agent** 偵測到有 hostname 但無 agent → 加入 SOC dashboard 並告警
  - 備份失敗（jt-ipam-backup.service exit ≠ 0）→ systemd OnFailure 推告警
- **告警通道健康**：每天定時送 heartbeat 到 webhook，缺席 24h 視為斷線。
- **保留**：本機 90 天，異地永久（依稽核需求）。
- **PII 保護**：log 不寫 password、token、API key 原文（自動 redact middleware）。

---

## 十、A10:2025 — Mishandling of Exceptional Conditions（全新類別）

> 2025 全新類別，取代 2021 A10 SSRF。涵蓋 race condition、fail-open、stack trace 外洩、deadlock、未處理的例外路徑等。

### 10.1 威脅
- 出錯時把 stack trace / SQL query / 內部路徑回給 user（資訊洩漏）
- **fail-open**：權限檢查 except 時放行（應該 fail-closed）
- **TOCTOU**：先檢查再寫入之間被改變（IP 重複配發、競爭條件）
- 例外路徑沒寫 audit（攻擊者讓系統 throw 來繞過記錄）
- 外部 API 失敗時 silently swallow，使用者以為成功
- Deadlock / livelock 把 worker 卡死無 timeout

### 10.2 設計
- **統一例外回應**：FastAPI exception handler 把所有未預期例外轉為 `500 Internal Server Error`，回應只含 `request_id`；完整 stack 寫 log，不外送。
  - Production guard 確保 `app_debug=false` 不會回 stack。
  - 移除預設 `Server` header（與 A02 重疊）。
- **Fail-closed**：
  - `require_permission` dependency 拋例外時自動 401/403，**絕不** try/except 後放行
  - SSRF 檢查（safe_http）任何 doubt 都拒絕，不要 fallback 到 raw httpx
  - rate limit 檢查失敗（Redis 連不上）回 503，不允許 bypass
- **Race / TOCTOU**：
  - IP 配發用 `SELECT ... FOR UPDATE` 或 advisory lock
  - audit chain 寫入用 `pg_advisory_xact_lock(AUDIT_LOCK_KEY)` 序列化
  - subnet 切割 / first_free_address 必須在同一 transaction 內 lock subnet row
- **例外也要寫 audit**：try/except 後即使 raise 也要寫 audit（at least `action=*_failed`）。`authenticate()` 在密碼錯誤、帳號鎖定、user not found 等所有失敗路徑都呼叫 `_audit()` + `session.commit()`。
- **外部 API 失敗紀錄**：
  - OPNsense / Wazuh / LibreNMS sync 失敗時，`last_error` 必須寫回 DB
  - 寫回後 `await session.commit()` 才 raise HTTPException（不能因為 raise 就吞掉 last_error）
- **Timeout 必填**：
  - 所有 `httpx` / `safe_request` 呼叫帶顯式 timeout（無預設）
  - PostgreSQL `statement_timeout=30s`（已在 `app/core/db.py` 套用）
  - asyncio task 用 `asyncio.wait_for(..., timeout=N)`
- **Resource cleanup**：
  - `async with` 取代手動 close
  - 連線池 `pool_pre_ping=True` + `pool_recycle=1800`
  - filterwarnings 出現 `ResourceWarning` 時 CI fail（pytest 已開）

### 10.3 測試
- pytest 模擬 DB 連不上、Redis 斷線、外部 API 500，確認：
  1. 不洩 stack
  2. `last_error` 有寫
  3. audit log 有 `*_failed` 條目
  4. 沒有 silent swallow
- 已知坑（記在 memory `feedback_pytest_anyio_filterwarnings.md`）：anyio 在 Py3.11+ `Task.cancel("msg")` 觸發 DeprecationWarning，若 `filterwarnings=error` 會吞 CancelScope cancellation → 死鎖。pyproject.toml 必須 ignore。

---

## 十一、PR 自我檢核清單（每個 PR 走一次 — 2025 版）

提交前自問：

- [ ] **A01** 新增的 endpoint 是否套了權限 dependency？批次操作逐筆檢查？
- [ ] **A02** 有沒有引入 debug 資訊洩漏、放寬 CORS、跳過 TLS 驗證、systemd hardening 退化？
- [ ] **A03** 新加的依賴有過 audit？版本鎖定？GitHub Actions 釘 SHA？
- [ ] **A04** 新增欄位若敏感，是否 AES-GCM 加密？aad 綁 instance id？密碼有沒有變成 plaintext log？
- [ ] **A05** 所有外部輸入都過 Pydantic？沒有字串拼 SQL / 命令？
- [ ] **A06** 業務不變式有 DB constraint 兜底？需要 rate limit 嗎？外部 URL 是否走了 `safe_http`？
- [ ] **A07** 認證流程改動有沒有破壞 lockout / MFA / session rotation / anti-enumeration？
- [ ] **A08** 有沒有寫入 audit log？diff 是否完整？webhook 出站有簽章？
- [ ] **A09** 結構化日誌有 request_id？敏感資料有 redact？失敗事件有沒有觸發告警？
- [ ] **A10** **新**：所有 try/except 都有寫 audit？是否 fail-closed？race / TOCTOU 有沒有用 lock？timeout 有沒有設？

---

## 十二、漏洞回報

回報管道：`security@example.com`（PGP 公鑰見官網）。

承諾：
- 24 小時內確認收到
- 高危漏洞 7 天內 patch
- 不訴訟善意研究者（safe harbor）
- 重大漏洞修補後 90 天內公開揭露

---

## 十三、第三方安全測試

每個大版本（major release）前：
- SAST（CI 內建：bandit、semgrep、ruff S 規則）
- DAST（OWASP ZAP baseline 自動跑）
- 第三方滲透測試（每年至少一次）
- 公開漏洞獎金計畫（在系統穩定後啟動）
