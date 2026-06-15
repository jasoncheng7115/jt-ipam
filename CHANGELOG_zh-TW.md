# 變更記錄

本檔記錄專案所有重要變更，格式大致依循
[Keep a Changelog](https://keepachangelog.com/)；版本對應
`frontend/package.json` / `backend/app/version.py`。

## [0.4.162] — 2026-06-15

### 新增 — 派送代理支援更多網頁伺服器／服務 profile
- 憑證派送代理（以及**產生設定檔**工具與安裝腳本）新增 9 個 profile：
  **caddy / traefik / lighttpd / zoraxy / jetty / exim4 / mosquitto / cockpit / webmin**（原有
  nginx / apache / haproxy / postfix / dovecot / pve / pmg / pbs / zimbra 之外）。每個 profile 都提供固定
  寫入路徑與重載指令；**jetty** 會收到 **PKCS#12 keystore**（`<cert>.p12`），由 `GET /cert-agents/bundle/raw`
  新增的 `part=pkcs12` 提供。

### 變更 — 安裝說明 UX
- 支援的作業系統／發行版以醒目標籤呈現（Debian／Ubuntu／RHEL 家族／Fedora／SUSE）。
- 修正 curl 一行式指令第一行開頭被縮排一格的問題（行內 `<code>` 改 `display: block`）。
- 隱藏工具列獨立的**設定檔說明**按鈕 — 設定檔產生改由各代理操作欄的**產生設定檔**功能負責；安裝說明第 3 步
  指向它（並附上該按鈕圖示）。

## [0.4.161] — 2026-06-15

### 新增 — 憑證檔案檢視與多格式下載
- 憑證列操作欄新增**檔案**鈕：點開列出該憑證各版本（指紋 / 到期日 / 網域 / 目前版本），可逐版本**下載**並選
  格式：完整鏈 / 憑證(.crt) / 中繼鏈 / 私鑰(.key) / 合併 / **DER** / **PKCS#12(.pfx)**（後端用 cryptography 轉檔）。
  含私鑰的格式（key / combined / pfx）逐次寫稽核（`GET /certificates/{id}/versions/{vid}/file?fmt=`）。

## [0.4.160] — 2026-06-15

### 變更
- 憑證 / 派送代理表格操作欄最右側（刪除鈕）加留白，不再貼齊邊緣。
- 憑證已設來源或已有版本時，**「產生自簽」按鈕停用**（避免覆蓋現有憑證），hover 有說明。
- installer 設定檔註解加提示：可到 jt-ipam 用該代理操作欄的「產生設定檔」工具快速產生內容。

### 安全
- 修 Dependabot 警示(GHSA-gv7w-rqvm-qjhr，High)：透過 pnpm override 把 **esbuild 升到 0.28.1**（原 0.25.12
  經 vite 帶入，<0.28.1 有「Deno 模組二進位完整性」漏洞）。屬建置期 dev 相依、且本專案走 Node/vite 不用其
  Deno 安裝路徑，實際不可觸發;升級後前端建置正常通過。

## [0.4.159] — 2026-06-15

### 變更 — 設定檔產生器更完整
- 每個「憑證／服務」區塊現在會**直接產生該服務的 SSL 設定片段**（如 nginx 的 ssl_certificate /
  ssl_certificate_key、apache 的 SSLCertificate* 等），並對**每個寫入路徑與設定片段都加複製按鈕**。
  pve/pmg/pbs 等讀固定路徑的服務則標示「不需改服務設定」。
- 底部加上**完整的試跑 / 正式執行指令**（含 sudo bash 全路徑）＋複製按鈕，不必再自己拼。
- 服務勾選改為**整齊的格狀排列**。

### 新增 — 派送代理可編輯 / 啟用切換
- 派送代理操作欄新增**編輯**鈕：可事後改名稱、**調整可取憑證範圍**（之後要多放幾個網站直接加）、啟用狀態。
- 「啟用」欄改成**開關**，一鍵停用 / 啟用。
- 「可取憑證」欄滑過去顯示**是哪些憑證**（名稱）。
- 「重設 Key」「檢視 Key」提示文字標明是**代理連線用的 Key（非 SSL 憑證）**。
- 安裝說明步驟 3 提示可用操作欄的「產生設定檔」工具（附 icon）；工具列移除「設定檔說明」按鈕（改由安裝說明內進入）。

## [0.4.158] — 2026-06-15

### 新增 — 派送代理頁多項改善
- **設定檔產生器**（操作欄工具鈕）：選憑證（此代理範圍內）＋勾選服務（nginx/apache/pve…可多選）即自動產生
  快速模式設定；可展開「進階／手動模式」填自訂路徑。即時預覽 ＋ 一鍵複製，貼到主機設定檔即可。
  下方並**列出快速模式憑證會寫到主機的完整路徑與檔名**（每個 cert／key／chain），方便把服務設定指過去。
- 「已部署 / 回報」欄加 tooltip 說明（成功套用數 / 代理回報的派送總數）。
- **安裝說明瘦身**：把設定檔格式說明拆成獨立的**「設定檔說明」**按鈕，安裝說明只留安裝/移除步驟。
- **顯示伺服器最新代理版本**：派送代理頁工具列顯示 server 端目前 agent.sh 版本（`GET /cert-agents/server-version`）。
- 派送代理資訊視窗的「關閉」按鈕補上 icon。

## [0.4.157] — 2026-06-15

### 變更
- installer 設定檔範例的 `DEPLOY_1_CERT` 改用通用佔位符 `example.com`（RFC 2606 保留網域），不再放真實憑證
  名稱；真實可派送的憑證名稱仍列在上方「This agent is allowed to deploy」清單供使用者替換。

## [0.4.156] — 2026-06-15

### 變更 — installer 自動帶出此代理可派送的憑證名稱
- 安裝時 installer 會用代理金鑰向 server 查「這把代理可派送哪些憑證」，把實際的憑證名稱**列在設定檔註解
  並預填到 `DEPLOY_1_CERT` 範例**，使用者不必再猜 `DEPLOY_<N>_CERT` 要填什麼（＝jt-ipam 憑證頁的名稱）。
- 安裝/重跑結束時也會印出此代理可派送的憑證清單（設定檔已存在時不覆蓋，但會印清單供參考）。

## [0.4.155] — 2026-06-15

### 修正
- 派送代理表格:版本欄「可更新」標籤改為**可換行**並加寬欄位,不再溢出到「來源 IP」欄。
- 名稱與最後回報兩欄都設為彈性,多餘寬度由兩欄平分,名稱欄不再獨自過寬。

## [0.4.154] — 2026-06-15

### 變更
- 派送代理設定檔範本重新分成兩區塊:**快速模式（優先）**與**手動模式**。快速模式的註解直接列出**每個 profile
  會把 cert / key / chain 寫到哪個路徑與檔名**,並附上對應的 nginx / apache 指令,讓你知道要把服務設定指到哪。

## [0.4.153] — 2026-06-14

### 變更
- 派送代理設定檔範本的註解**完整列出所有內建 profile**（nginx / apache / haproxy / postfix / dovecot /
  pve / pmg / pbs / zimbra / generic），含各自的預設檔案路徑與重載指令，打開設定檔就看得到能用哪些。

## [0.4.152] — 2026-06-14

### 變更
- 派送代理設定改以 `DEPLOY_<N>_PROFILE`（服務）為主，**重載指令由 profile 提供**，一般情況不必再寫
  `DEPLOY_<N>_RELOAD`。可只設「憑證 + 服務」，或再加自訂路徑（`FULLCHAIN`/`KEY`…）覆寫位置但仍沿用 profile
  的重載；`DEPLOY_<N>_RELOAD` 降為自訂服務時才用的進階覆寫。範本與安裝說明同步更新。

## [0.4.151] — 2026-06-14

### 變更 — 派送代理設定改一行一個設定
- 派送代理設定檔從一行擠一堆（`DEPLOY_1="cert=..; profile=..; fullchain_path=.."`）改成**一行一個設定**的
  `DEPLOY_<N>_*` 群組,好讀好改:
  - `DEPLOY_1_CERT=`（要派送的憑證）、`DEPLOY_1_FULLCHAIN=`（憑證檔路徑）、`DEPLOY_1_KEY=`（私鑰路徑）、
    `DEPLOY_1_RELOAD=`（重載指令）;另有 `DEPLOY_1_CHAIN/CRT/COMBINED/TEST` 可選。
  - 或只設 `DEPLOY_1_CERT=` ＋ `DEPLOY_1_PROFILE=nginx`（內建 profile 用固定路徑）。
- installer 設定檔範本、安裝說明彈窗範例同步更新。已對 prod 實機驗證新格式 dry-run ＋ 真套用。

## [0.4.150] — 2026-06-14

### 變更
- 派送代理腳本（`jt_ipam_cert_agent.sh` 與 installer）全部改英文（註解、終端輸出、設定檔範本），
  比照 `scripts/*.sh` 慣例——在客戶終端機執行的腳本不夾中文。
- installer 新增**移除**模式：`JT_IPAM_UNINSTALL=1` 會停用並移除 timer / service、代理程式、設定檔與狀態
  （已派送到各服務的憑證檔保留不動）；安裝說明彈窗加上「移除派送代理」的一行式指令。

## [0.4.149] — 2026-06-14

### 新增 — 派送代理可再次檢視 Key 與安裝指令
- 派送代理的 enroll key 現在除了存 hash，另以 AES-GCM 加密保存，**之後可在列表的「檢視」再次取得**
  （管理員限定，`GET /cert-agents/{id}/key`）。操作欄新增「檢視」按鈕，點開顯示 Key ＋ 一行式安裝指令
  （已含 key）＋複製按鈕。
- 建立 / 重設 Key 的視窗也一併顯示一行式安裝指令；「離開後無法再取得」改為「之後可在列表按『檢視』再次取得」。
- 刪除代理時一併清掉加密保存的 Key。
- 舊版建立、未保存明文的代理檢視會回提示，請改用「重設 Key」。

## [0.4.148] — 2026-06-14

### 變更
- 按下「自動產生並安裝金鑰」後，「登入私鑰」欄位改為唯讀（disabled）並顯示「已由 jt-ipam 產生並儲存」，
  避免使用者誤以為還要自己貼私鑰。

## [0.4.147] — 2026-06-14

### 修正
- 憑證表格欄寬：操作欄改 `fixed: "right"` 釘在右側（視窗再窄也不會被推出畫面），加寬到容得下全部 icon；
  名稱與網域兩欄設為彈性平分多餘寬度。
- 繁中文案改用全形標點（，。；：（）「」），並把對岸用語改成台灣慣例：回滾→還原、一次性→單次、
  原子覆蓋／原子寫入→不中斷換檔／不中斷寫入（派送代理安裝說明、來源設定、代理腳本註解等）。

## [0.4.146] — 2026-06-14

### 變更 — 派送代理改純 bash（移除 Python / PyYAML 相依）
- 派送代理重寫為**純 bash**(`jt_ipam_cert_agent.sh`),只相依 **curl + coreutils**,不再需要 Python / jq / YAML。
  設定檔改 `KEY=VALUE`(`/etc/jt-ipam-cert-agent/config`,`DEPLOY_N="cert=..; profile=.."`);profiles / 原子寫入 /
  config-test / reload / 回滾 / `--dry-run` / 自我更新 全部保留。
- 後端配合純 bash:`GET /cert-agents/check?format=text`(逐行,免解 JSON)、新增
  `GET /cert-agents/bundle/raw?cert=&part=cert|key|chain|fullchain|combined`(直接回原始 PEM,`curl -o` 寫檔,
  附 `X-Cert-Fingerprint` header)、`POST /report` 兼收 TSV。下載端點改 `agent.sh`,版本/自我更新比對改 `.sh`。
  installer 不再裝 python3-yaml。
- 安裝說明彈窗重新排版(編號步驟 + 留白),需求改「純 bash,只需 curl + coreutils」。

## [0.4.145] — 2026-06-14

### 修正 / 變更
- 憑證 / 派送代理表格補上 `:scroll-x`(對齊全站作法):名稱欄不再過寬撐版、操作欄不再被推出畫面右側;
  視窗較窄時改為水平捲動而非裁切。
- 憑證來源類型選擇器:**被選中的類型整顆填綠底白字**(原本只有細邊框,看不出選了哪個);
  「不自動（手動上傳）」文字精簡為**「手動上傳」**。

## [0.4.144] — 2026-06-14

### 變更
- 憑證 / 派送代理操作欄按鈕改**靠左對齊**（移除置中）,與全站其它列表頁一致。

## [0.4.143] — 2026-06-14

### 修正 — commit 後序列化的一類 500（流程檢查找出）
- `updated_at` 有 SQL 端 `onupdate=func.now()`,UPDATE flush 後該欄過期;憑證模組數個端點 commit 後直接
  `model_validate` ORM 物件 → 同步情境 lazy IO `MissingGreenlet` 500。補上 commit 後 `session.refresh`
  (與其它端點一致):`PATCH /certificates/{id}`、`PATCH /cert-agents/{id}`、`POST /cert-agents/{id}/rotate-key`
  (v0.4.142 已先修 `PUT /certificates/{id}/source`)。

### 變更 — 自動產生金鑰時直接登入主機安裝公鑰
- 既然 jt-ipam 已有 SFTP 登入密碼,「自動產生金鑰」現在會**直接用密碼登入主機,把公鑰寫進
  `~/.ssh/authorized_keys`**(冪等、不重複),免使用者手動貼。安裝成功顯示「已安裝」;沒有密碼或安裝失敗
  則金鑰仍已產生,退回顯示公鑰供手動貼上並附原因(`POST /certificates/{id}/source/ssh-keypair` 改收來源
  設定 + 回 installed/message)。

## [0.4.142] — 2026-06-14

### 修正
- **設定 SFTP/URL 來源儲存時 500**(MissingGreenlet):`PUT /certificates/{id}/source` commit 後
  `model_validate` 在同步情境觸發 lazy IO → 改 commit 後 `session.refresh(cert)` 再序列化。

### 新增 — 憑證來源測試連線 + 自動產生 SSH 金鑰
- 來源設定加**「測試連線」**鈕:以表單目前內容(密碼/私鑰留空＝沿用已存)實際試連 URL / SFTP,
  回成功訊息或可讀失敗原因,不存檔(`POST /certificates/{id}/source/test`)。
- SFTP 登入私鑰加**「自動產生金鑰」**鈕:jt-ipam 產生 ed25519 金鑰對、私鑰 AES-GCM 加密儲存(不回明文),
  回**公鑰**供貼到 SFTP 主機 `authorized_keys`(`POST /certificates/{id}/source/ssh-keypair`)。

### 變更
- 憑證 / 派送代理操作欄按鈕改 **icon-only + hover tooltip**(與全站列表一致)、欄寬收緊並置中,
  解決過寬左空、右側溢出、icon 偏左問題。

## [0.4.141] — 2026-06-14

### 修正 / 變更
- 「系統有更新」重新整理橫幅的 icon 與文字未垂直置中 — icon 改置中於 16×16 框、容器與文字 `line-height:1`。
- 憑證表格「到期」欄拆成**「到期日」**與**「剩餘天數」**兩個獨立欄位（各自可排序、可選擇）。
- 憑證 / 派送代理表格操作欄 icon 改**置中**（欄位 `align:center` + NSpace `justify:center`）。

## [0.4.140] — 2026-06-14

### 變更 — 憑證自動抓取來源設定 UX
- SFTP 來源設定釐清:**「登入密碼」/「登入私鑰（SSH 金鑰，PEM）」**獨立成「SFTP 登入認證」區塊
  並移到帳號下方,加說明「用來登入 SFTP 主機,密碼或 SSH 私鑰二擇一(私鑰優先);憑證本身的私鑰是
  下方 key_path 遠端檔案,與此無關」。遠端檔案路徑(cert_path/key_path/chain_path)另成一區。
  (後端早已支援 SSH 私鑰登入,只是欄位位置/命名易誤解為憑證私鑰。)
- 來源類型「不自動」改顯示**「不自動（手動上傳）」**,讓使用者知道仍可手動上傳 / 貼上 / 自簽。

### 變更 — 憑證 / 派送代理表格比照全站
- 兩張表格欄位**可排序**(autoSort)、加**欄位選擇器**(ColumnPicker,偏好存後端跨裝置同步)。
- 操作欄按鈕改 **icon + 文字**,欄寬不足時自動收成**只剩 icon**(col-actions 容器查詢,hover 仍有提示)。

## [0.4.139] — 2026-06-14

### 新增 — 派送代理版本顯示與自我更新
- 管理頁「派送代理」分頁新增**版本號**(落後 server 時標「可更新」並提示)與**來源 IP** 欄位,
  比照掃描代理。
- 派送代理新增**自我更新**:`/check` 回傳 server 端 agent.py 的 sha256,代理比對自己不同就下載新版、
  原子覆蓋並以新版重新執行(下載後驗 sha 才覆蓋,失敗只記錄不中斷部署)。config 設
  `auto_update: false` 可停用。
- 唯讀「憑證派送現況」頁(`GET /cert-agents/status`)一併回傳 `last_source_ip` /
  `server_agent_version`。

## [0.4.138] — 2026-06-13

### 新增 — 憑證自動抓取來源
- 憑證除了上傳 / 貼上 / 自簽,現在可設**自動抓取來源**:系統定期(或按「立即抓取」)從來源拉續約後
  的 bundle,**只有內容真的變了才存新版** —— fingerprint 與目前版本相同就跳過(不處理)。來源若沒
  提供私鑰,沿用目前版本的 key(多數續約不換 key)。
- 來源:**URL**(走 SSRF 防護的 safe_http)與 **SFTP**(asyncssh;host 先過 SSRF 黑名單)。帳密
  (SFTP 密碼 / 私鑰)AES-GCM 加密(`encrypted_secret`)、不回明文。新增 migration `0076`。
- 端點:`PUT /certificates/{id}/source`、`POST /certificates/{id}/fetch-now`;sync timer 依各憑證
  間隔自動抓取。前端:每張憑證的來源設定(URL/SFTP)+「立即抓取」,並顯示上次抓取錯誤。
- CIFS / NFS 暫不做(backend 非 root 無法掛載);請改用預掛路徑或 URL/SFTP。

## [0.4.137] — 2026-06-13

### 修正
- **憑證頁 405 /「伺服器發生錯誤」(憑證 API client 路徑漏 /api/v1)** — `certificates.ts` 的
  API 呼叫(以及 `integrations.ts` 的重疊網段檢查)漏掉共用 axios client 需要的 `/api/v1` 前綴
  (它的 baseURL 是 `/`),導致請求打到 SPA 路徑(`/certificates`、`/cert-agents`)→ nginx 對
  POST 回 405、對 GET 回 index.html。已全部補上正確前綴。憑證管理頁、派送代理、產自簽、進階現況
  頁都正常了。
- 補上憑證/代理「儲存」按鈕漏掉的 icon。

## [0.4.136] — 2026-06-13

### 憑證派送 — 體驗
- 憑證版本上傳新增**貼上 PEM 文字**(憑證 / 私鑰 / 中繼)選項,與上傳檔案二選一(對話框上方切換)。
- 進階選單的唯讀憑證頁標籤改與管理頁一致(憑證派送)。

## [0.4.135] — 2026-06-13

### 憑證派送 — 後續補強
- **跨發行版 agent 安裝器** — cert-agent 安裝器自動偵測套件管理器(apt / dnf / yum / zypper),
  支援 Debian 11/12/13、Ubuntu 22.04/24.04/26.04、RHEL / Rocky / AlmaLinux / CentOS、Fedora、
  openSUSE/SLES(皆 systemd);PyYAML 依各發行版正確套件名安裝。
- **新增 profile** — 加入 `pbs`(Proxmox Backup Server:`proxy.pem`/`proxy.key`,重載
  `proxmox-backup-proxy`);`apache` profile 改為重載 `apache2` 或 `httpd`(哪個有用哪個),
  Debian/Ubuntu 與 RHEL/SUSE 都能用。
- **「安裝說明」按鈕**(派送代理分頁,比照掃描代理):一行式安裝指令、設定檔範例、支援的發行版、
  `--dry-run` 提示。
- **進階下的唯讀憑證現況** — 非管理員的唯讀檢視者(具萬用讀取)現在可在「進階」看各代理的派送現況
  (最後更新、有效日、到期日、剩餘天數、是否最新/飄移)。新增 `GET /cert-agents/status`
  (`require_global_read` 把關)。

## [0.4.134] — 2026-06-13

### 修正
- **Debian 12 加 PGDG repo 時若 keyring 檔已存在會失敗（客戶回報）** — 安裝腳本把
  `gpg --dearmor` 寫到 `/usr/share/postgresql-common/pgdg/apt.postgresql.org.gpg`（那是
  `postgresql-common` 套件自己的檔）。該檔已存在時 gpg 會跳「File exists. Overwrite?」、
  非互動下直接失敗 → 金鑰沒寫成 → PGDG 簽章無效 → `postgresql-16-pgvector` 變「not installable」。
  現在改寫到自有的 `/etc/apt/keyrings/jt-ipam-pgdg.gpg` 並加 `gpg --dearmor --yes`（不撞檔、可重入）。
  已在 Debian 12 容器端到端驗證。

### 新增 — 憑證集中保管 + 派送（商業憑證一次上傳、派到所有站台）
- 集中保管商業憑證,搭配 pull 模型的派送代理。續約後只要上傳一次 bundle(crt/key/chain),
  各站台代理會自動取走新版、寫到正確路徑、跑 config-test、重載服務,失敗自動回滾。
- **後端**:migration `0075`(`certificates` / `cert_versions` / `cert_agents`);私鑰以 AES-GCM
  加密儲存,任何管理 API 都不回傳明文。`/certificates` admin CRUD + `POST /{id}/versions`
  (驗證 key↔cert 配對、SAN/效期,擋不配對/過期/重複)+ **`POST /{id}/self-signed`**
  (產生自簽憑證,可自訂 CN/SAN/天數 —— 商業憑證還沒到時先頂著)。`/cert-agents` admin CRUD +
  key 輪替,以及 agent 協定(`X-Agent-Key`):`check` / `bundle`(解密私鑰,scope 限定、逐次稽核)
  / `report`。
- **代理**(`agent/jt_ipam_cert_agent.py` + 安裝器):pull 模型,內建 service profiles
  (nginx / apache / haproxy / pve / pmg / postfix / dovecot / zimbra / generic),原子寫入 +
  時間戳備份 + config-test gate + 回滾,冪等,並支援 **`--dry-run`**。設定是每台主機一份小 YAML,
  列出哪些憑證用哪個 profile 派送。
- **監控**:每日到期告警 +**飄移偵測**(某代理回報的指紋不是目前版本 → 那台沒換成功)走既有
  通知/鈴鐺。
- **前端**:憑證管理頁(上傳、產自簽、版本/到期狀態、代理 + 一次性 key、scope)。

## [0.4.133] — 2026-06-13

### 修正
- **最小化 Debian 12 / 13 容器安裝失敗（客戶回報）** — 乾淨容器映像上兩個缺口：
  - 「加 PGDG repo」那步用 `curl | gpg`、後面 PostgreSQL 設定全用 `sudo -u postgres`，但
    `ca-certificates` / `curl` / `gnupg` / `sudo` 不保證存在（最小化 Debian 容器常拿掉）。
    Debian 12 預設庫是 PG15、必走 PGDG → 在 `curl` 那步失敗；補完 PG 後又卡在
    `sudo: command not found`。現在開頭就先把這四個裝齊。
  - 搭配 v0.4.131 的 `apt-cache madison` 版本偵測，現在的對應是：Debian 12 → PGDG 裝 PG16；
    Debian 13 → 用發行版自帶的 PG17（+ `postgresql-17-pgvector`，不必 PGDG）；Ubuntu 24.04 →
    自帶 16；Ubuntu 26.04 → 自帶 17/18。app 對 PG 16/17/18 皆相容。

## [0.4.132] — 2026-06-12

### 修正
- **CSV 實際匯入 500（客戶回報 / issue #4）** — 匯入端點把 `subnet.cidr`（asyncpg 回傳的
  `IPv4Network` 物件，不是 str）當成背景作業的 VARCHAR `target_label` → asyncpg `DataError`。
  dry-run 不受影響（不會 spawn task），所以「模擬匯入正常、實際匯入 500」。已用 `str()` 轉型。
- **IP 申請列表在有手動指定 IP 時 500（issue #4）** — asyncpg 從 `INET` 欄位回傳 `IPv4Address`，
  但 `IPRequestRead.requested_ip` 宣告為 `str`，Pydantic 驗證失敗 → 整個列表頁 500。比照
  `IPAddressRead.ip` / `SubnetRead.cidr` 既有作法加 `mode="before"` 強制轉 str。
- **掃描代理無法回傳主機名稱（客戶回報）** — 回報帶 rdns/NetBIOS/mDNS/OS 主機名稱時，對「新發現
  的 IP」會 500。session `autoflush=False` + UUID 由 DB 端產生，剛 add 的 `IPAddress` 此時 `id=None`，
  `apply_observation` 建 hostname 觀測 FK row 時違反 `NOT NULL`。改成建立 IP 後立即 flush，讓
  id 先有值。（只有 icmp+arp 的回報不受影響，因為不會呼叫 `apply_observation`。）
- **一併修補同類 asyncpg INET/CIDR 當 str 的隱性 bug**：其他以 `model_validate(ORM)` 組裝、漏掉
  轉型的 read schema — `APITokenRead.last_used_ip`、`VMInterfaceRead`（`primary_ip`/`mac`）、
  `ARPEntryRead`/`FDBEntryRead`（`ip`/`mac`）。

## [0.4.131] — 2026-06-12

### 修正
- **Ubuntu 26.04 安裝失敗（客戶回報）** — 安裝腳本原本寫死 `postgresql-16`，但 Ubuntu 26.04
  預設庫沒有 PG 16（改出 PG 17/18）。舊的退路會去加 PGDG 對應「新發行版 codename」的庫，而
  PGDG 對剛發布的 Ubuntu codename 常延遲數月才上架 → `apt-get update` 404、整個安裝中斷。
  現在改成偵測「已啟用的庫裡可用的 PostgreSQL 版本」（優先 16，否則用發行版自帶的 17/18…）
  並安裝對應的 `postgresql-N-pgvector`；只有在完全找不到 `postgresql-N`（>=16）時才退回 PGDG。
  app 對 PG 16/17/18 皆相容。Python 偵測也加入 `python3.14`（Ubuntu 26.04 預設）。

### 修正
- **ARP 紀錄保留** — `arp_entries` 原本只新增/更新、從不回收，長期會無限累積（MAC↔IP 變動、
  來源 device 被刪的孤兒 row 都各留一筆）。同步排程現在每輪刪除 `last_seen_at` 超過
  `ARP_RETENTION_DAYS`（預設 30 天；設 0 停用）的舊 ARP，含孤兒 row。

### 新增
- **整合設定頁的重疊網段警告** — 當存在重疊網段（同一 IP 可能出現在多個子網路）且某整合
  （LibreNMS / OPNsense / Wazuh / Proxmox / AdGuard / DNS）未設定限定子網路範圍時，設定表單
  會顯示警告：同步可能把存活狀態／DHCP／MAC 標到錯誤單位的同 IP，並引導去設定關聯子網路範圍。
  新增 `GET /subnets/overlaps/exists`（admin）。

### 說明
- 沒有新的重複 IP／重複 ARP 風險：`ip_addresses` 對 `(subnet_id, ip)` 唯一；`arp_entries` 以
  `(ip, mac, device_id)` upsert；只有 LibreNMS 會寫 ARP（scanner 與 OPNsense 只 stamp 既有 IP）。
  重疊網段下「同 IP 字串跨子網路多筆」屬設計上正常。

## [0.4.129] — 2026-06-11

### 安全性
- **RBAC IDOR 修補** — 多個詳情／彙整端點只憑物件 id 取資料、未做物件層級可見性檢查，
  讓任何已登入帳號都能讀到範圍外的物件：`GET /devices/{id}` 及其子資源（`/integrations`
  會洩漏 Wazuh CVE 數量＋Proxmox VM，另含 `/librenms`、`/vlans`、`/relations`）、
  `GET /customers/{id}` 與 `/{id}/summary`（傾印整個客戶資產）、`GET /racks/{id}/diagram`。
  以上全部改為需要物件 `read` 權限（無權限回 404）。MCP `get_topology` 工具不再把整張拓樸
  洩漏給受限帳號（原本漏傳 `user` 過濾），並歸為全域讀取；REST `GET /topology` 也補上
  `require_global_read` 一致化。
- **OIDC ID Token 驗簽** — callback 原本只把 ID Token base64 解開就信任其 claims（含決定
  admin 提權的 `groups`），未驗簽章。現在改用 provider 的 JWKS 驗證 ID Token（簽章＋
  `aud`/`iss`/`nonce`）後才信任 claims；驗證失敗則退回只用 userinfo，不信任未驗的 groups。
- **CSV 匯出公式注入** — IP 位址 CSV 匯出對以 `= + - @`／tab／CR 開頭的欄位前置跳脫，
  避免試算表把內容當公式執行。

### 修正
- **整合同步韌性** — `jt-ipam-sync.py` 在每個整合的例外處理寫 `last_error` 前先 rollback；
  單一實例失敗（例如 AdGuard 在重疊網段上 `MultipleResultsFound`）不再中斷整輪同步。
- **重疊網段** — AdGuard 同步（`sync_clients` / `sync_rewrites`）與 MCP ARP 查詢以
  `scalar_one_or_none()` 比對 `IPAddress.ip`；重疊網段下同一 IP 會有多筆 → `MultipleResultsFound`。
  改用 `limit(1)` + `first()`。
- **UCS 以外的 DNS 伺服器連線測試** — BIND 9（dnspython `OSError`／連線被拒）、Windows DNS
  （WinRM／`requests` 例外）、PowerDNS 與 OPNsense Unbound（認證失敗回非 JSON）會漏出原始例外，
  而 `/dns/servers/{id}/test` 端點沒接到 → 變成無訊息的 500。各 adapter 現在把這些包成
  `DNSAdapterError`，端點也加了安全網把任何非預期錯誤轉成可讀的 502。

### UI / 文件
- 修正區段詳情頁缺少的 i18n key（「顯示順序」原本顯示原始 key）。
- 通知「全部標為已讀」與群組成員操作補上錯誤提示。
- 用詞：繁中文件 plugin 一律用「外掛」（不用「插件」）。

## [0.4.128] — 2026-06-10

### 修正 / 改善
- **外部反向代理 + OIDC / Microsoft 365(Entra ID) 登入**：OIDC/SAML callback 後前端能正確解析
  網址 fragment 裡帶回的 token（原本會被忽略、卡在登入頁）；後端合併 **ID Token** 的 claims —
  Entra ID 的 `groups` 只在 ID Token、不在 Graph userinfo，補上後管理員群組才比對得到。新增
  `deploy/nginx/jt-ipam-external-proxy.{conf,snippet}` 外部代理模式範本（HTTP-only、不送 HSTS、
  `X-Forwarded-Proto` 透傳）；README 新增「模式 C — 外部反向代理」與 `APP_PUBLIC_URL` 等設定提醒。
- **安裝（Ubuntu 24.04）**：`ensure_node` 不再把 NodeSource 安裝輸出丟 `/dev/null`，且安裝後**驗證
  Node ≥ 18**，否則直接停止並印出手動補裝指令——修「Node 安裝靜默失敗、前端沒 build、卻看似安裝成功」。
- **AI 對話**：Ollama 未啟用 / 連不上 / 設定錯時，改顯示**友善且可行動**的錯誤訊息（指向 管理 → LLM / AI），
  不再是 `Ollama is disabled` / `transport: …` 這類難懂字串。
- **電路**：編輯時「關聯裝置」下拉空白修復（裝置查詢 `page_size` 超過後端上限被擋）；電路表格新增
  「關聯裝置 / 說明」欄（欄位選擇器可選），狀態欄改顯示中文（active → 使用中）。
- **掃描代理 / 裝置詳情**：表格欄寬收緊——操作欄不再被擠出右側、空欄不再吃滿寬度、MAC 與時間不再折行。
- **NAT 規則**：從頂層選單移到「進階」群組；點某一列改為**唯讀檢視**（欄位禁用），編輯改走操作欄鉛筆鈕。
- **更新提示橫幅**：改為有框 + 陰影、整塊可點的方塊、SVG icon（非 emoji），文案改「系統有更新，請按此重新整理載入最新版本」。
- **全站表格每頁筆數**改為記住使用者偏好（`user_preferences.page_size`，跨裝置同步）。

## [0.4.114] — 2026-06-09

### 新增 / 改善
- **DNS 記錄頁**：可依伺服器 / 型別篩選（型別下拉帶筆數統計）、來源欄顯示**來源 DNS 伺服器**、IP 對應
  改用**實際 IP 值**查 `ip_addresses`（修「子網路裡明明有、卻顯示查無對應」）、欄位選擇器；DNS 同步只
  保留 **A / AAAA / PTR**（IP↔名稱對應用途），不再存 CNAME/MX/TXT 等。
- **IP 位址**：新增 `in_dhcp_lease`（migration 0074）由 OPNsense DHCP lease 同步**自動標記/撤銷**；
  phpIPAM 匯入來源改標 `phpipam`（原本一律誤標「手動」）；OPNsense DHCP/ARP 同步加上防火牆關聯子網路
  範圍 + `limit(1)`，修重疊網段同 IP 的 `MultipleResultsFound` 整批 crash。
- **全域搜尋**：支援**部分 MAC 前綴**（如 `bc:24`）；DNS 記錄搜尋結果改導向「進階 → DNS 記錄」並把名稱代入搜尋。
- **機櫃**：合併單卡也能匯出 **SVG / PNG / draw.io**（整機房多櫃並排）；draw.io 方塊改**直角**與畫面一致。
- **AI 對話**：零相依 Markdown 渲染器支援 **GFM 表格**（修表格亂掉）。
- **MCP**：新增 `list_dns_records` 工具；AI 回答「某子網路還有幾個可用 IP」改呼叫實際資料而非純 CIDR 算術。
- **IP 申請審核通知信**：加上**可點連結**（未登入先導登入、登入後自動返回審核頁）。
- IP 異動記錄的 `switch_port` 顯示改「**裝置@埠號**」。

## [0.4.113] — 2026-06-09

### 新增 — IP 申請審核關卡 + 通知
- **可設定的審核政策**（管理 → IP 申請審核設定），四種模式供各站台選用：僅管理員；管理員 +
  指定使用者/群組（單關卡，任一核准）；**多組會簽**（多關卡、不分先後、全部都要核准）；
  **依序多關卡**（有序關卡，每關各自指定審核人，須逐關通過）。另有「是否允許自核」職責分離
  開關。逐關核准記錄存於新表 `ip_request_stage_approvals`（migration 0073）。核准/駁回端點
  改依政策授權（不再只看是否為 admin）。
- 申請詳情頁顯示**關卡進度**（哪些關卡已過、目前待哪一關）；依序模式下，輪到某關才通知該關審核人。
- IP 申請清單對審核人的待審項目直接提供 **核准 / 駁回** 按鈕（不必點進詳情）。
- 申請詳情頁全面中文化；顯示子網路 CIDR（可點）與「**將配發的 IP**」——含系統自動挑的第一個
  空位，且**審核人可在核准前改成別的 IP**。
- **審核人通知**：申請送出時，每位審核人會收到站內鈴鐺通知；若已啟用 Email 管道也會收到信。
- **通知發送設定**（管理 → 通知發送設定）：Email/SMTP 管道（主機/埠/TLS/帳密/寄件者，密碼
  加密儲存，可寄測試信）。Telegram / Slack / Teams / Nextcloud / Zulip 顯示「開發中」。

### 新增 — DHCP
- 子網路詳情在有 OPNsense DHCP 發放範圍時多顯示 **DHCP 發放範圍** 欄（無資料則不顯示），
  IP 清單並新增 **只看 DHCP** 篩選。

### 新增 — DNS 記錄（進階 → DNS 記錄）
- 新頁面列出從整合 DNS Server 取回的記錄，可搜尋、**用 IP 反查**對應記錄（正解 A/AAAA 或該 IP
  的 PTR），以及**只看「沒有對應 IPAM IP」**的 A/AAAA 記錄。

## [0.4.112] — 2026-06-09

### 修正
- **人工編輯的 MAC 未受同步覆寫保護。** 不像 hostname（會記一筆 `manual` 觀測），在 UI
  改 IP 的 MAC 只設了 `ip.mac`、沒標 `mac_source="manual"`，導致下次掃描 / ARP 同步可能
  把它蓋掉。IP 編輯端點現在會在人工改 MAC 時標記 `mac_source="manual"`（ARP 優先序最高），
  清空 MAC 時一併清掉來源。
  （hostname 的人工 vs 優先序流程已端到端驗證正確；若人工填的主機名稱看似消失，多半是
  瀏覽器跑到舊的 SPA bundle，請強制重新整理，而非後端問題。）
- IP 申請工具列：狀態篩選下拉是 `small`、旁邊按鈕是預設大小，導致下拉較矮 → 調成同高。

## [0.4.111] — 2026-06-08

### 安全（MCP 逐物件 RBAC 範圍）
- 多個 MCP/AI 清單工具會回到使用者可見範圍以外的資料。`list_racks` /
  `list_locations` / `list_sections` / `list_customers` 現在依逐物件可見性過濾列；
  `recent_ip_changes` 限定可見子網路；`get_customer_summary` 對不可見客戶回查無；
  `stats_overview` 的逐物件計數依範圍縮放，且對無全域讀取者不洩漏全域基礎設施計數。
  `dns_lookup` 改歸類為全域基礎設施工具。
- 新增回歸測試（`test_mcp_rbac_scope.py`）：零權限全擋、部門帳號擋全域工具、清單列
  範圍過濾、stats 計數縮放。

## [0.4.110] — 2026-06-08

### 修正（create-admin CLI）
- 當指定的帳號名稱命中某帳號、而 email 命中另一個帳號（或同一 email 被多個帳號共用）時，
  `create-admin` 會以 `MultipleResultsFound` 崩潰。改成分開查 username 與 email，遇到衝突
  時回清楚的錯誤而非崩潰。
- `--force-update` 現在會把指定的 username/email 一併寫回該帳號 —— 先前只重設密碼，導致
  帶了新的 `--email` 卻被忽略、畫面仍顯示舊位址。

## [0.4.109] — 2026-06-08

### 新增（MCP / AI 工具）
- **10 個新 MCP 工具**，補上先前 AI 查不到的實體：`list_circuits`、`list_providers`、
  `list_asns`、`list_tenants`、`list_contacts`、`list_ssids`、`list_cables`、
  `cable_trace`、`list_power`、`list_wazuh_agents`。

### 變更（MCP 欄位跟上近期功能成長）
- `list_subnet_ips` 補回 `effective_status`（上線/離線）與 `os_family`。
- `list_nat` 解析出真正的來源/目的 IP，並加入介面、別名、disabled/no_rdr、ip_version
  （原本只有名稱/協定/埠）。
- `get_subnet_detail` 補 scan_method、掃描代理、VRF、上層子網路、是否歸檔。
- `get_device` 補單位、fqdn、地點、說明、電源埠；`list_devices` 補單位 + fqdn。
- `list_vms` 補租戶/主要 IP/裝置 + 網路介面。
- `get_ip_detail` 補 `effective_probes`；`list_customers` 補 title/address；
  `stats_overview` 加計 VM / 電路 / 供應商 / ASN / 租戶 / 聯絡人 / 佈線。

### 安全（MCP RBAC 收斂）
- MCP HTTP/stdio 的 `tools/call` 先前**完全沒有**可見性閘。現在 MCP 協定與 NL chat
  共用同一個 `authorize_tool`：零權限帳號擋掉所有資料工具；全域基礎設施工具
  （VLAN/VRF/NAT/防火牆/DNS/VM/VPN/電路/佈線/電力/Wazuh…）需 admin 或萬用讀取；
  異動工具需 admin。`tools/list` 與 LLM 工具清單都依可呼叫範圍過濾。

## [0.4.108] — 2026-06-07

### 修正
- **掃描代理回報主機存活後，「實際狀態」仍卡在「離線」。** 代理 `/report` 端點只更新
  `last_seen_scanner`，卻沒有重算 `effective_status`，導致實際狀態停留在上次 LibreNMS
  重算結果（可能已過時好幾天）。現在只要代理當次掃到回應，便立即把該 IP 翻成
  `online (scanner)` / `online`，並記錄離線→上線的異動。

### 新增
- **安裝程式自動建立第一個 `admin` 帳號並產生隨機密碼**，於結束時印出一次（亦存到
  `/etc/jt-ipam/.admin-initial-password`，僅 root 可讀）。README 補上
  `create-admin --force-update` 重置密碼的 CLI 說明。
- **掃描代理安裝程式會一併安裝選用探測工具**（`nmap`、`samba-common-bin`、
  `avahi-utils`），讓 OS / NetBIOS / mDNS 探測開箱即用；可用
  `JT_IPAM_SKIP_PROBE_TOOLS=1` 略過。
- **不可用探測旁新增「安裝說明」彈出**（掃描代理頁與子網路編輯對話框），顯示解鎖該探測
  所需的套件 / 指令。

## [0.4.107] — 2026-06-07

### 新增
- **Wazuh / Proxmox VE / AdGuard / DNS 整合的限定子網路範圍**（migration 0072），比照
  LibreNMS：各整合可限定只在指定子網路內比對 IP，避免不相關系統的重疊網段把 hostname /
  OS 等資訊誤掛到別的 IP。留空＝全域比對。

### 變更
- 子網路編輯：所選掃描代理不支援的探測項目會反灰（與掃描代理頁一致）。
- NAT 表格：點規則列可開啟該筆細節（會略過 IP / 裝置連結）。
- 子網路列表：樹狀展開箭頭改放在 CIDR 欄，不再佔用釘選欄。

### 修正
- switch_port 提示改顯示 `裝置@連接埠` 格式（非 `裝置 / 連接埠`）。

## [0.4.106] — 2026-06-07

### 新增
- **OPNsense 防火牆關聯範圍**（migration 0071）：每台防火牆可依機房 / 單位 / 明確子網路 /
  介面→子網路對應設定範圍。同步進來的 NAT 規則只會在範圍內子網路配對 IP，多台防火牆共用
  同一 RFC1918 網段時不再誤掛到錯的 jt-ipam IP；未設定範圍者沿用原本全域 IP 字串比對。
- NAT 頁：滑過已關聯到 jt-ipam 的 IP 會即時彈出該 IP 細節（主機名稱 / 狀態 / MAC / 製造商 /
  子網路 / 單位 / 裝置 / 交換器位置…），懶載入；點擊進入該 IP 詳情頁。

### 變更
- 新增的子網段繼承父網段的單位；`rebuild_subnet_hierarchy` 會自動修補（多層串接）。
- 左側子網路樹：下層子網段改為真正可展開的巢狀節點＋連接線（取代「↳」前綴）；父節點標題仍可點進詳情。
- 左側選單版本號字體放大。

### 修正
- 淺色主題下含連結的 tooltip（如表格 ellipsis tooltip）綠字在深底看不清；連結改用 tooltip 淺色字。
- 防火牆關聯範圍表單的「單位 / 客戶」下拉顯示「無資料」（選項未載入）。

## [0.4.105] — 2026-06-07

### 修正
- 子網路存檔出現「Invalid request」：編輯表單會送 `master_subnet_id`，但 `SubnetUpdate`
  （嚴格 schema、禁止額外欄位）沒宣告它。已補上欄位，編輯子網路恢復正常。
- 子網路列表：點「展開下層」的箭頭會誤進入該子網路而非展開；已讓 row 點擊忽略展開鈕。

### 變更
- **統一子網路編輯**：子網路列表與子網路詳情頁改共用同一個 `SubnetEditModal` 元件，
  兩處編輯欄位完全一致（區段 / VLAN / VRF / 上層子網路 / 逐項掃描 / 掃描代理…），不再分歧。
- 左側選單：子網段依 `master_subnet_id` 巢狀排在父網段之後並以「↳」縮排（仍可點擊進入）。
- 頂列 RWD 美化：語言 / 佈景 / 帳號改 pill 按鈕含 hover、鈴鐺兩側分隔線、與搜尋框垂直置中；
  移除下拉小箭頭以省寬度。
- Graylog 教學：建議 Title / Description / Name（`jt_ipam_adapter` / `jt_ipam_cache` /
  `jt_ipam_table`）；HTTPS 與明文 HTTP(8088) 兩種網址；Line Separator `\n`、Ignore
  characters `#`、Refresh interval 300s、Expire after access 300s、Default single/multi
  value 留空；IP 欄位名輸入框即時改寫 pipeline rule 並做 Graylog 欄位名驗證；rule 名為
  `jt-ipam enrich <欄位> -> <欄位>_hostname`；pipeline `lookup_value()` 用對照表名稱；
  範例改用 `src_ip_hostname`。

## [0.4.104] — 2026-06-07

### 修正
- Graylog DSV 對照表端點改為每個 IP 只輸出一次。同一個 IP 可能存在於多個（重疊）
  子網路，原本會產生重複列，導致 Graylog「DSV File from HTTP」資料配接器報
  「Multiple entries with same key」。現已依 IP 去重（依排序取第一筆）。

## [0.4.103] — 2026-06-07

### 變更
- 頂列改為 RWD：螢幕較窄時，語言 / 佈景 / 帳號改為只顯示 icon（icon 觸發下拉）、
  搜尋框自動縮小，不再換行擠成多列。
- 新增子網路時，未指定單位則**自動繼承父網段的單位**，子網段會歸到側邊選單同一個
  單位群組下；新增 / 編輯 / 刪除後左側子網路樹立即刷新。
- IP 編輯視窗：OS 探測顯示與子網路 / 掃描代理設定相同的「侵入性」標籤 + 提示。

## [0.4.102] — 2026-06-07

### 變更
- 異常偵測（MAC 漂移）：「出現位置」的交換器改顯示友善名（LibreNMS sysname / hostname），
  不再顯示原始裝置 UUID；裝置 / 埠 / 最後出現三欄改為對齊網格呈現。

## [0.4.101] — 2026-06-06

### 變更
- 儀表板**區段熱度**卡片重新設計：熱度條改用「平均子網路使用率」（不再被單一大型稀疏網段
  稀釋成趨近 0%），並列出該區段子網路依使用率分布（滿載 / 偏高 / 中等 / 偏低）與
  子網路 / 已用摘要，資訊更充實。

### 修正
- 機櫃：最左機櫃的框線左邊被水平捲動容器裁掉；於兩側補小邊距使其完整顯示。
- 「OS 來源優先順序」區塊標題用詞。

## [0.4.100] — 2026-06-06

### 新增
- **OS 來源優先順序**（掃描代理 / LibreNMS / Wazuh）：在「名稱 / ARP 來源順序」頁可拖拉
  排序，決定多個來源都回報 OS 時以誰為準；IP 詳情的 OS 欄位會顯示解析出的來源。
- 機櫃：原「合併單卡」開關改為清楚的兩段式切換（獨立卡片 / 合併卡片）；合併卡片新增
  共用的正面 / 背面切換與匯出（合併裝置清單）。

### 變更
- 稽核轉送設定由「Graylog」改為通用的「Log 伺服器」（GELF / syslog 適用任何收集器）。
- IP 清單 OS 欄改為一行顯示（icon 永遠不縮、空間不足時 label 裁切）；主機名稱欄縮窄讓位。
- 掃描代理表格欄寬調整，使「最後回報」不再折行。
- switch_port 提示一律顯示完整 `裝置@連接埠` 全文（低信心時再附說明）。

## [0.4.99] — 2026-06-06

### 變更
- MCP 工具帶出新的掃描 / OS 欄位：`get_ip_detail` 回傳 OS 推測 / 家族 / 來源與排除的探測；
  `list_scan_agents` 回傳已啟用 / 可用探測與最後來源 IP。

## [0.4.98] — 2026-06-06

### 變更
- OS 偵測結果在 IP 詳情主要欄位表中以**「作業系統」**欄位呈現（上方），子網路 IP
  清單也新增可切換的 **OS** 欄。

## [0.4.97] — 2026-06-06

### 新增
- 掃描代理：新增**「立刻執行一次」**動作，代理下次 poll 時立即跑所有已啟用的探測
  （migration 0070）；OS 偵測（`nmap -O`）在裝有 nmap 的代理主機上端到端驗證可用
  （以 root 執行）。
- 探測間隔輸入框顯示單位（秒）與換算提示。

## [0.4.96] — 2026-06-06

### 變更
- 掃描代理的探測間隔輸入框加上單位後綴（秒）與換算提示（如 86400 → 「1 天」）。

## [0.4.95] — 2026-06-06

### 變更
- 掃描探測：移除連接埠探測選項（TCP 連接埠存活、連接埠 / 服務掃描）與 SNMP —
  jt-ipam 不開放需要憑證或連接埠掃描類的探測。保留：ICMP / ARP / 反解 DNS /
  NetBIOS / mDNS / OS 偵測。
- 掃描代理：當代理回報的版本落後 server 內建的代理程式時，顯示**「可更新」**標籤
  （代理是從 jt-ipam server 自我更新，非 GitHub；此標籤用來凸顯自我更新失敗的代理）。
- 拓樸圖：子網路中心點分開時，VPN 對連的防火牆會保持靠近，不再被推到相對最遠的兩端。

> 英文版見 [CHANGELOG.md](CHANGELOG.md)。

## [0.4.94] — 2026-06-06

### 新增
- **可組態的掃描探測**，三層模型（migration 0069）：
  - **探測項目目錄**（icmp / tcp / arp / rdns / netbios / mdns / os / ports），
    每項分輕量 / 重量、各自預設間隔與侵入性；預設**只開 ICMP**。重項目（OS / 連接埠
    掃描）走**自己的長間隔**，絕不跟 ICMP 同頻。
  - **掃描代理**：勾選可執行的探測 + 各重項目間隔；代理會自我回報實際裝得起哪些
    （其餘反灰）。
  - **子網路**：選擇要跑的探測（`scan_method`）。
  - **IP 位址**：逐項略過探測（原「不掃 ping」一般化，icmp 保持同步）。IP 詳情頁
    顯示**實際生效**的探測集（子網路要跑 − IP 略過 ∩ 代理能力）。
- **OS 偵測顯示**：掃描結果正規化成 OS 家族（Windows / Linux / macOS / BSD /
  網路裝置 / 印表機 / 儲存 / 虛擬化…），並以每家族 SVG icon 呈現（IP 清單欄 +
  IP 詳情），tooltip 顯示原始字串。
- 代理 poll/report 協定帶上每子網路探測、各探測間隔、逐 IP 略過覆寫，以及更豐富的
  結果（rdns / os_guess / open_ports / probes_run）；內建代理新增 tcp/arp/rdns 探測
  與快 / 慢迴圈排程。

## [0.4.92] — 2026-06-06

### 新增
- 進階（租戶 / 電路 / 聯絡人）與電力頁：同頁多張表格改拆成**內層頁籤**（比照防火牆
  規則 / 別名的樣式）。進階 / 電力 / 虛擬化每張表格都改成**統一工具列**：
  篩選 + 重新整理 + 新增 + 欄位選擇 + 匯出。
- 機櫃：新增**合併單卡**檢視模式（同機房所有機櫃排在同一張卡）。
- IP 拓樸圖：**選取連線後維持高亮**，並以卡片顯示兩端（IP / 裝置 / 連接埠，單邊
  已知也顯示）；多個子網路中心點分散，雙網段裝置不再擠在一起。
- 電路：**固定 IP 欄位**（IP / 閘道 / 遮罩 / DNS）+ 關聯裝置（migration 0067）；
  內建**電路類型**並可新增 / 刪除管理。
- 掃描代理：顯示**最近來源 IP**（migration 0068）。
- 裝置詳情：IP 清單新增一鍵「**新增 IP 對應**」按鈕。
- Graylog DSV 設定獨立成「**整合 Graylog**」頁面（放在整合 Wazuh 下），附串接教學。

### 變更
- nginx API 限流上調（100 → 1200 r/m、burst 20 → 80），解決 API 密集頁面偶發的
  「連線失敗」。
- IP 位址編輯：儲存鈕改綠色；存檔 / 取消後回到該 IP 詳情頁。

### 修正
- **舊 bundle 換頁卡住**：換頁載入分割 chunk 失敗時，路由會自動重載一次；建置改為
  **跨版本保留 hash 資產**（清理 7 天前的舊檔），讓部署前開著的分頁換頁時不再
  抓不到 chunk（404）而卡住。
- 聯絡群組表格誤用租戶群組欄位（打到錯的 API）— 已用專屬欄位修正。
- ruff：修正 `audit.py` 的 noqa 規則碼、`sso.py` 的 import 排序。

### 安全 / 雜項
- **vitest** 開發相依升級 3.2.6 → 4.1.8（修補「Vitest UI server 任意檔讀取 / 執行」
  critical 通報；僅開發用，不在正式 bundle 內）。
- 新增雙語文件：`CHANGELOG_zh-TW.md`、`SECURITY_zh-TW.md`，以及英文
  `TEST_CHECKLIST.md`（搭配 `TEST_CHECKLIST_zh-TW.md`）。
- `scripts/*.sh` 全面英文化（註解與訊息）；行為不變。

## [0.4.79] — 2026-06-06

### 新增
- **SSO 網頁設定**：OIDC 與 SAML 改為 DB 化並提供管理員網頁設定（env 預設 + DB
  覆寫，secret 以 AES-GCM 加密）；新增 LDAP 管理頁。
- **裝置電源埠 ↔ PDU 插座**建模（NetBox PowerPort 風，migration 0066）。
- **版本自動重載**：輪詢 `dist/version.json`，部署新版時提示重新整理（這是「存檔
  沒生效」舊 bundle 問題的根因）。
- **完整雙語文件**：README 與所有 `docs/*.md` 提供中英版本；GitHub Pages 功能地圖
  樹狀頁。

### 變更
- 全站通用表格**欄位選擇 + 多格式匯出**（含零相依 `.ods` / `.odt`、`.xlsx`、PDF）。
- 通用釘選改存後端偏好（migration 0065）；機櫃正面 / 背面安裝面支援。

## [0.4.61] — 2026-06-05

### 新增
- **全域基礎設施資料的 RBAC 收斂**：`require_global_read` / `has_global_read` /
  `can_edit`。列表、詳情、搜尋、儀表板彙總、計數與趨勢全部依使用者可見範圍縮放；
  操作按鈕依能力反灰。
- **纜線追蹤**（NetBox 風多跳，migration 0063）：新增 `device_ports` 表，支援
  bridge → NIC → 外部裝置穿透。
- 機櫃**半 U** 支援與正 / 背面視覺化；裝置詳情的機櫃圖標示本裝置位置。

### 變更
- AI / MCP：100 題實測修一輪；工具清單依權限過濾；任何異動前加**確認 gate**；
  大量結果支援 cursor 分頁與「下一批」續抓。
- 歸檔的子網路連帶隱藏其 IP（清單 + 搜尋）。

### 修正
- 修補 AI 對話 / 拓樸圖的 **RBAC 繞過**（零權限帳號原本可問到 IP / 裝置、看得到
  拓樸）。

## [0.4.43] — 2026-06-04

### 新增
- **裝置間纜線 / 連接埠**連線管理；佈線與電力資源支援完整 CRUD 編輯。
- LDAP 管理頁；AI 異動確認 gate；Graylog DSV 查表（埠 8088 明文 HTTP）。
- 儀表板圖表；裝置詳情顯示所在機櫃圖。

### 變更
- Proxmox 連線設定移到管理區；撈取節點網路介面（bridge / bond / NIC）並可追蹤。

### 修正
- 主機名稱同步反覆抖動；左半 U 放入裝置存檔 500；稽核 `object_id` 必須是 UUID
  （`append_audit` 要帶 `request_id`）。

## [0.4.32] — 2026-06-02

### 新增
- 裝置安裝方向（migration 0057）：裝置可標記安裝於**機櫃正面或背面**；移除機櫃
  呈現面欄位。
- 版本資訊管理頁：顯示目前版本 + Python 與主要後端套件版本，並可一鍵檢查 GitHub
  最新版。
- 地點清單顯示**機櫃數 / 裝置數**欄；子網路清單顯示**釘選**欄並可一鍵切換。
- IP 位址編輯器提供一鍵**關聯相符裝置**（與裝置→IP 關聯鈕互為鏡像）。
- 機櫃表單常見寬 / 深**快選膠囊**；進機櫃頁自動選取已釘選的地點。
- 遠端分頁清單（IP 位址 / 稽核 / 使用者 / 裝置）的匯出可抓**完整資料集**（非僅
  當前頁）。
- GitHub Actions CI 實際執行並把關：前端（eslint flat config / vue-tsc / vitest /
  build）與後端（alembic / pytest / bandit / gitleaks）。
- 裝置 ↔ IP 關聯：裝置清單推導有效管理 IP（primary_ip → LibreNMS 管理 IP →
  名稱即 IP），有相符 IP 物件時 render 成可點連結，未掛上時提供一鍵「關聯」鈕；
  IP 編輯器可挑裝置，`/devices/{id}/relations` 提供關係鏈。
- 掃描代理自動探索：代理對其負責且開啟掃描的子網路內未知 IP 推送時，自動建立 IP
  物件（自帶說明，非抄 phpIPAM）；重疊網段僅在代理自己的子網路內以最長首碼比對。
- 各來源優先序拆成獨立卡片：主機名稱、裝置名稱、ARP/MAC，以及新的**裝置型號**
  優先序（各卡中手動最高且不可關閉）。
- IP 搜尋新增**完全相符**切換（IP / 主機名稱須等於關鍵字，`192.168.1.1` 不再也
  比對到 `192.168.1.1xx`）。
- 子網路可透過 `allow_overlap` 明確**允許重疊**（如不同租戶 / 地點下的同一 CIDR）。
- OPNsense 別名同步：別名拉進 `opnsense_synced_aliases`。
- 儀表板：釘選地點 / 釘選機櫃卡片；機櫃頁可用迷你機櫃挑選器把裝置放入空 U 位。
- GitHub Pages 站：專案 logo + favicon、內嵌 SVG 圖示（無 emoji）、修正定位
  （非「建構於 phpIPAM 之上」）。

### 變更
- 來自 LibreNMS 的裝置命名改優先 **sysName** 而非 hostname（常只是 IP）；裝置名稱
  優先序預設順序對應調整，型號由 LibreNMS 硬體資訊回填。
- DNS 同步對每個 IP 套用一個決定性主機名稱（排序後），修正 IP 有多筆 A 記錄時的
  名稱抖動。
- 主機名稱優先序納入 Wazuh 與 AdGuard 來源（原本有觀測到卻沒列在優先序 UI）。
- 平面圖機櫃可旋轉**任意角度**（軟貼齊正交），不限 0/90/180/270；佔地依實際寬 / 深
  縮放。
- VPN 隧道配對依方法標示（migration 0058）：WireGuard 公鑰（可靠）vs IPsec 端點
  比對（盡力）；IPsec 比對也對應各防火牆自身隧道本地端點以提高命中率。
- 全站文案從「新世代」改為「可自架、以整合為核心」（Pages / README / SPEC / app）；
  Pages 強調開源整合 + phpIPAM 匯入、加上強調色、拆分安裝 / 升級 / 解除安裝。

### 安全
- OPNsense config.xml 改用 **defusedxml** 解析（XXE）；子網路重疊 / master SQL 全
  參數化；bandit 在 medium+ 嚴重度下乾淨。

### 修正
- 子網路釘選重整後不一致 — 改為切換時同步持久化，而非靠元件層 watcher。
- GeoIP 資料庫下載改回 legacy `geoip_download` 端點（新 permalink 會 302 到 S3 並
  拒絕轉發的授權標頭）。
- 平面圖上傳 500（uploads 目錄擁有者）；稽核記錄文件表格列因未跳脫 SQL `||` 損壞。

### 測試
- 新增回歸覆蓋：型號優先序、子網路重疊、完全相符 IP 搜尋、掃描自動探索、主機名稱
  來源清除、主機名稱順序完整性、裝置 IP 比對旗標、裝置 / IP 關係鏈、OPNsense 別名
  解析 + 同步、LibreNMS 裝置連結、DNS 拉取命名、Wazuh/Proxmox 同步、IPsec 配對、
  版本端點、機櫃面 / 地點計數，以及前端 usePinned 單元測試。

## [0.4.31] — 2026-06-01

### 新增
- NAT / 電路欄位擴充（migration 0053）：NAT 補齊完整 OPNsense 規則集（停用 /
  no-RDR / IP 版本 / 來源·目的反向 / 埠範圍 / 記錄 / 分類 / NAT reflection / pool /
  filter-rule / 別名參照）；電路新增上 / 下行頻寬。OPNsense 同步會填入。
- 裝置詳情：Wazuh 代理 + Proxmox VM 面板（依 IP 比對）；編輯鈕。
- 工具：DNS / 郵件診斷（MX/SPF/DKIM/DMARC）+ 機房電力試算。
- 機櫃圖 → 可用 draw.io 編輯的 SVG 匯出；機房釘選；清單頁快速篩選；使用者選單內
  自己的 AI 對話歷程；權限總覽。
- 拓樸圖：縮放 / 適配按鈕、可點圖例切換、預設顯示釘選子網路。

### 變更
- 機房 = 地點（選單改標「機房 / 地點」）；站對站 VPN；NAT 別名參照可點到防火牆頁。
- VPN WireGuard 配對交叉填入兩端真實 WAN IP（原本顯示 LAN）。
- 平面圖：固定大小控制點、0/90/180/270 旋轉貼齊、工具列移到畫布下方。
- 全站卡片標題色帶 + 深色模式表格 / 卡片層次。

### 修正
- 拓樸圖子網路篩選漏掉以名稱 / ARP 推導的裝置。
- 多處 i18n / 用詞 / 按鈕高度修正（協定、配電盤 / 饋線 / 插座、通知…）。

## [0.4.30] — 2026-06-01

### 新增
- 管理表格的匯出（CSV / Markdown / PDF / ODS / ODT）：使用者、稽核、DNS、LibreNMS、
  Wazuh（執行個體 / 代理 / 失聯）、防火牆（防火牆 / 對應 / 規則）與掃描代理。

### 變更
- 全域**地圖供應商**選擇從地點頁移到 **設定 → 系統**（僅管理員）。新增非管理員的
  `GET /system/map-provider` 讓地點地圖預覽對所有人可 render，`PUT` 仍限管理員。

### 修正
- 資料表標頭不再換行：全域規則讓短的 CJK 標題（如 子網路）不受排序箭頭間距影響
  維持單行。

## [0.4.x] — 2026-05/06

### 新增
- **物件層級 RBAC**，涵蓋 7 種物件（客戶 / 區段 / 子網路 / IP / 裝置 / 機櫃 /
  地點），含階層繼承、各類型「全部」萬用，與 5 個內建角色（系統管理員、唯讀檢視者、
  網路操作員、稽核員、部門管理員）。可見性套用於列表端點、全域搜尋、拓樸圖與所有
  選擇器。
- **權限管理 UI** — 主體（使用者 / 群組）挑選器、授權表，與「全部」/ 特定多選 +
  讀 / 寫 / 管理層級的授權流程。
- **MCP 伺服器** — 擴充工具集，支援 stdio 與 Streamable HTTP；掛在 `/api/mcp`，可
  透過 nginx 反代連到。寫入工具自我限管理員。
- **客戶**（管理單位）掛到區段 / 子網路 / 裝置 / IP，以及 IEEE **OUI 廠商**表與
  月排程更新。
- **AI 對話**改進 — 歷程持久化、每則時間戳、模型與耗時顯示、模型參數 tooltip
  （家族 / 參數量 / 量化 / 上下文長度，經 Ollama `/api/show`）。
- **全域搜尋**擴及 VPN、客戶、機櫃、地點、NAT、DNS 記錄、防火牆與 IP 申請 — 全部
  經 RBAC 過濾。
- 寬表格的浮動黏附水平捲軸；精緻淺 / 深主題；佈線 / 電力 / VPN 拆成三個獨立頁面。

### 變更
- 正式資料庫由 `SQL_ASCII` 轉 `UTF8`。
- 台灣用語修正（如 首碼 取代 前綴）。

### 修正
- 多項 QA 驅動的 UI 修正（欄寬、儀表板小工具樣式、淺色模式文字選取對比、拓樸節點
  詳情 popover、tooltip 裁切）。

## [0.3] — Phase 1–3 baseline

- phpIPAM 對等（區段 / 子網路 / IP / VLAN / VRF / NAT / 裝置 / 機櫃 / 地點 /
  IP 申請）、TOTP + API token、強制 TLS。
- 多廠牌 DNS、深度 LibreNMS 整合、異常偵測、SHA-256 稽核鏈、pgvector 語意搜尋。
- 租戶 / 佈線 / 電力 / VPN / 虛擬化、Proxmox 同步、Cytoscape 拓樸、OIDC/SAML SSO、
  OPNsense 防火牆同步、Wazuh 代理盤點。
