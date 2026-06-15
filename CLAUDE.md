# jt-ipam

可自架、以整合為核心的 IPAM（整合 DNS / LibreNMS / OPNsense / AdGuard / Wazuh / Proxmox / Ollama LLM；提供 phpIPAM 相容 API 與遷移路徑，但**並非以 phpIPAM 為核心、也非建構於 phpIPAM 之上**）。**已公開發布於 github.com/jasoncheng7115/jt-ipam（Apache-2.0）。**

## 部署環境

- **Prod**: `192.168.1.144` (ipam2 LXC)。所有改檔/重啟都 `ssh root@192.168.1.144`；prod 用 rsync 部署（見下方部署流程），**不是 git pull**
- 本機（log4 / 132）`/opt/jt-ipam` 是 dev；本機改檔不影響線上，要 rsync 過去才生效。**注意本機同時裝了 Docker**（FORWARD policy DROP，會擋 LXD 容器對外）
- 不用 Docker 部署。靠 systemd + apt（適合虛擬機 / 容器）
- 強制 HTTPS（`BACKEND_TLS_MODE=nginx` 反代 / `direct` uvicorn 自簽 / `self-signed` uvicorn 自動產自簽）
- **對外安裝/升級走統一腳本 `scripts/jt-ipam.sh`**（`install` / `upgrade` / `uninstall`）；`scripts/install-debian.sh` 是相容 shim 轉呼叫它。一行式 bootstrap：`curl -fsSL .../scripts/bootstrap.sh | sudo bash`（自動 clone 到 /opt/jt-ipam 再跑 install）。`upgrade` 內含 git pull→備份→pip→alembic→build→restart；`uninstall` 永不刪原始碼（`--purge` 才連 DB/設定一起刪）

## Stack

- **Backend**: FastAPI + SQLAlchemy 2.0 async + asyncpg + PostgreSQL 16 + Alembic + Pydantic v2
- **Frontend**: Vue 3 + TypeScript + Vite + Naive UI + Pinia + vue-i18n
- **Auth**: argon2id + TOTP + JWT（access 15 min / refresh 14 天）
- **LLM**: Ollama（本地），預設 chat=gpt-oss / embedding=qwen3-embedding；走 `/api/v1/system/llm` 全域設定
- **OWASP Top 10:2025 是硬性需求** — 每個模組與 PR 都要過逐項自我檢核

## 權限模型（RBAC）— 硬性需求，新功能/異動一律遵守

物件層級授權：`Permission(object_type, object_id|NULL=萬用, principal user|group, level read|write|admin)`，7 種可授權物件：`customer / section / subnet / ip / device / rack / location`（含階層繼承）。`visible_ids(session, user, object_type)` 回 **None=全部可見（admin 或萬用授權）**、**set=限定**、**空 set=無**。

**任何回傳資料的端點/工具/彙總都必須依此過濾，預設關閉（deny by default）。** 三種資料分類與對應作法：

1. **可逐物件授權的資料**（subnet/ip/device/section/rack/location/customer 及其衍生）→ 用 `filter_visible` / `visible_ids` 過濾到使用者可見範圍。列表、詳情、搜尋、儀表板彙總、計數、趨勢全部都要套。
2. **全域基礎設施資料**（VLAN / VRF / NAT / 防火牆 / DNS / LibreNMS / 虛擬化 / 站對站 VPN / 佈線 / 電力 / ASN / 電路 / 聯絡人…，無法逐物件授權）→ 掛 `require_global_read` dependency：**僅 admin 或具萬用讀取權限者（如唯讀檢視者）可見**；只被指派特定物件的部門帳號一律 403。
3. **純管理資料**（稽核記錄、使用者/群組/權限/系統設定/整合設定）→ `require_admin`。

**前端配合**：`GET /me` 提供 `has_visibility`（任一類型有範圍）與 `has_global_read`（admin 或任一萬用）。側邊選單、新增/編輯/刪除按鈕都要依能力顯示或反灰（後端仍是唯一真相，前端只是 UX）。零權限帳號只留儀表板/工具；無全域讀取者隱藏全域基礎設施選單。

**檢查清單（每次新增端點/工具/儀表板卡片/搜尋結果類型都要過）**：
- 這筆資料屬上面哪一類？有沒有套對應過濾？
- 計數 / total / 趨勢 / 彙總有沒有也跟著縮放（別只過濾 rows 卻回全域 count）？
- 新的搜尋結果型別有沒有納入可見性過濾或全域型別封鎖？
- 不要假設「有 user 參數就安全」。相關：[[project_rbac_ai_topology_leak]]。

## 專案結構

```
backend/app/
├── api/v1/endpoints/   # FastAPI routes（addresses / sections / subnets / devices / customers / nat / oui / firewall / dns / librenms / wazuh / virt / topology / advanced / physical / sso / migration / preferences / system_settings ...）
├── api/v1/router.py    # router mount
├── cli/                # bootstrap.py（create-admin --password-stdin）
├── core/               # db / audit / config / safe_http / encrypted_secret
├── models/             # SQLAlchemy 2.0 ORM
├── schemas/            # Pydantic v2（StrictModel extra=forbid）
├── services/           # ai / anomaly / oui / opnsense_firewall / phpipam_migration / topology / search / librenms / ssh_tunnel / saml / system_config
├── mcp/                # MCP server + tools（LLM 用；stdio + Streamable HTTP）
└── plugins/            # 外掛系統

alembic/versions/       # 0001 ~ 0073 migrations（0066 device_power_ports / 0070 scan agent force_scan / 0071 OPNsense 防火牆關聯範圍 / 0072 整合限定子網路範圍 wazuh/proxmox/adguard/dns / 0073 ip_request_stage_approvals 逐關卡審核）
```

```
frontend/src/
├── views/              # 一個檔案一個頁面
├── components/         # 共用元件（IPAddressEditModal / SubnetGrid / GlobalSearch / NotificationBell / ColumnPicker / LiveStatusDot / ScopeOverlapWarning〔整合頁未設 scope+重疊網段警告〕 ...）
├── composables/        # useCustomers / usePinnedSubnets / useEntityLinks / useColumnPrefs / useTableSort / useLivenessSettings / useTablePagination
├── api/                # API clients
├── stores/             # Pinia
├── i18n/               # zh-TW.json / en-US.json
├── utils/datetime.ts   # fmtDateTime / fmtRelative / fmtDuration — 全站時間格式
├── icons.ts            # 統一 iconoir re-export
└── router/             # vue-router routes
```

## 關鍵 entities

- **Section** (區段) → **Subnet** (子網路) → **IPAddress** (IP 位址)
- **Device** (裝置)、**Rack** (機櫃，支援半 U / 正背面 rack_face)、**Location** (地點＝機房)
- **Customer** (客戶 / 管理單位)，掛 sections/subnets/devices/IPs
- **OPNsenseFirewall** + alias mappings + rules + NAT
- **OUIVendor** — IEEE MAC 廠商 lookup（Wireshark manuf 每月排程更新）
- **DevicePort / Cable** — 裝置間佈線與 Cable Trace（多跳穿透，bridge→NIC→外部裝置）
- **DevicePowerPort → PowerOutlet** — NetBox 風電源埠↔插座建模（migration 0066）
- **進階資源**（advanced.py，全域基礎設施）：VLAN / VRF / ASN / Tenant / Provider / Circuit（含頻寬）/ Contact / SSID — 皆可 CRUD 編輯
- **SSO**：`OidcConfig` / `SamlConfig`（env 預設 + DB override，AES-GCM 加密 secret）；另有 LDAP 管理頁
- **ScanAgent**（掃描代理）— 外部 agent（`agent/jt_ipam_agent.py`）以 key 認證，POST `/scan-agents/report` 回報存活 IP。三層探測模型：agent `available_probes`（由 agent `shutil.which` 自報）∩ subnet `scan_method` − 每 IP `excluded_probes`。探測目錄 `core/scan_probes.py`：icmp/arp/rdns/netbios/mdns/os（不開放需憑證或連接埠掃描類）。代理回報存活即時更新 `effective_status=online (scanner)`

## 部署流程

```bash
# 後端改檔（從 dev → prod）
rsync -az /opt/jt-ipam/backend/ root@192.168.1.144:/opt/jt-ipam/backend/ \
    --exclude='.venv' --exclude='__pycache__' --exclude='*.pyc'

# 跑 alembic（cd + source env 都要）
ssh root@192.168.1.144 'cd /opt/jt-ipam/backend; \
    set -a; source /etc/jt-ipam/backend.env; set +a; \
    /opt/jt-ipam/backend/.venv/bin/alembic upgrade head'

ssh root@192.168.1.144 'systemctl restart jt-ipam-backend'

# 前端 build（⚠️ 必須先 cd frontend，或用 npm --prefix；漏了會 build 錯目錄）
rsync -az /opt/jt-ipam/frontend/src/ root@192.168.1.144:/opt/jt-ipam/frontend/src/
ssh root@192.168.1.144 'cd /opt/jt-ipam/frontend && npm run build'
# 或：ssh root@192.168.1.144 'npm --prefix /opt/jt-ipam/frontend run build'
```

> 部署前可先 `rsync -azn --checksum` dry-run 比對本機與 prod，確認真的有差異再推（避免無謂重啟線上服務）。rsync 一律從 `/opt/jt-ipam` 根目錄跑。docs/*.md 不在部署流程內（屬 GitHub Pages，不影響 prod 運作）。

## 常見背景作業

- `phpipam.migration` — phpIPAM SSH tunnel 匯入（含 sections/subnets/IPs/devices/customers/nat）
- `opnsense.sync` — DHCP（Kea / ISC fallback）/ ARP / OpenVPN / filter rules / NAT rules
- `librenms.sync` / `wazuh.sync` / `adguard.sync` — pull-only
- 整合（LibreNMS / Wazuh / Proxmox / AdGuard / DNS）皆有 `scope_subnet_ids`（JSONB，留空＝全域）：sync 時只在這些子網路內比對 IP，避免重疊網段把別人 hostname/OS 誤掛。OPNsense 另有 location/customer/subnets/iface 關聯範圍（0071）

## systemd services / timers

- `jt-ipam-backend.service` — uvicorn 主服務
- `jt-ipam-sync.timer` — 每 ~5 分鐘跑 `scripts/jt-ipam-sync.py`，對所有 enabled 且超過各自 `sync_interval_seconds` 的 OPNsense / Wazuh / LibreNMS / AdGuard / Proxmox 實例做 pull（**不是** background_tasks，直接寫表）。某 instance sync 失敗會先 `session.rollback()` 再把 `last_error` 寫回 DB（不 rollback 會二次爆、連鎖中斷整輪）。每輪另跑一次 `librenms.prune_stale_arp()`：刪 `arp_entries` 中 `last_seen_at` 超過 `arp_retention_days`（config 預設 30，0 停用）的舊/孤兒 row（ARP 只新增不回收，否則無限累積）
- `jt-ipam-oui-refresh.timer` — 每月 1 號 03:30 + 15min jitter，跑 `/opt/jt-ipam/scripts/oui_refresh.py`
- `jt-ipam-backup.timer`（每日）/ `jt-ipam-geoip-refresh.timer`（每日）

## 已知地雷

0. **前端 api client baseURL 是 `/`(不是 `/api/v1`)** — `src/api/client.ts` 的 `apiClient` baseURL = `VITE_API_BASE_URL || "/"`,正式 build 沒設 VITE_API_BASE_URL → **每個 api 檔的路徑都要自己帶 `/api/v1` 前綴**(如 `apiClient.get("/api/v1/certificates")`)。漏掉 → 請求打到 SPA 路徑 → nginx 對 POST 回 **405**、對 GET 回 index.html(前端當 JSON 解析→「伺服器發生錯誤」)。新增 api 檔時務必比照既有檔帶前綴(曾在 certificates.ts / integrations.ts 漏掉,整個憑證頁 405)。
1. **長壽 SPA 分頁跑舊 JS bundle** — 多次「存檔沒生效 / 功能怪怪的」真因都是舊 bundle，不是後端。已用 `dist/version.json` + `useVersionCheck` 自動提示重載解掉根因；遇到先請使用者 hard refresh，再查後端。
2. **pytest filterwarnings=error 必須 ignore anyio DeprecationWarning** — 否則 ASGI 測試會 hang 在 CancelScope._deliver_cancellation
3. **AuditLog.object_id 是 UUID** — `append_audit` 要帶 request_id；object_id 別塞非 UUID
4. **避免把 phpIPAM 的缺點搬過來** — phpIPAM 的 description-only 搜尋、JSONB blob 等都別照抄
5. **Phase 4 範圍縮減** — 不做 Zimbra/Odoo/Ansible/Terraform/HA；只做 MCP/LLM/Plugin
6. ~~prod DB SQL_ASCII~~ — 已於 2026-05-31 轉 UTF8（舊庫 `jt_ipam_old_sqlascii` 留作安全網）
7. **重疊網段同 IP 不可用 `.scalar_one_or_none()`** — 多客戶共用 192.168.1.0/24 時同 IP 有多筆 IPAddress；整合 sync 對 `IPAddress.ip == x` 一定要 scope（`subnet_id.in_(scope_ids)`）+ `.limit(1).scalars().first()`，否則 `MultipleResultsFound` 會炸掉整批 sync（曾讓 LibreNMS last_seen 卡住數日）
8. **`effective_status` 來源**：scanner / librenms（含 ARP）兩證據，30 分鐘窗內才算 online；代理 `/report` 即時更新，LibreNMS sync 後 `recompute_effective_status` 整批重算。離線轉換只走後者（agent 只回報存活 IP）
10. **asyncpg 把 INET/CIDR/MACADDR 欄位回成物件不是 str**：`Mapped[str]` 宣告無效，asyncpg 讀 `INET`→`IPv4Address`、`CIDR`→`IPv4Network`、`MACADDR`→物件。任何 (a) read schema 把這些欄位宣告 `str` 又走 `model_validate(ORM)` → Pydantic str 驗證失敗整頁 500；(b) 程式把 `subnet.cidr`/`ipa.ip` 當字串塞進別的 query 參數/VARCHAR → asyncpg DataError。**對策**：read schema 一律加 `@field_validator(欄位, mode="before")` 轉 str（見 IPAddressRead.ip / SubnetRead.cidr / IPRequestRead.requested_ip / api_token / virt / librenms ARP/FDB）；程式用值前一律 `str(...)`。新增這類欄位的 schema/端點都要記得。
9. **重複 IP / ARP 的正確認知**：`ip_addresses` 唯一鍵 `(subnet_id, ip)`、`arp_entries` 唯一鍵 `(ip, mac, device_id)`（upsert，只有 LibreNMS 寫；scanner/opnsense 只 stamp 既有 IP），所以**不會產生真重複**。重疊網段下「同 IP 字串跨多 subnet 多筆」屬設計。兩個真問題已修：(a) ARP 表只增不刪 → `prune_stale_arp` 定期回收；(b) 整合未設 `scope_subnet_ids` + 重疊網段時 `.limit(1)` 會把 last_seen/DHCP/MAC 標到任意一筆同 IP（誤掛、不炸）→ 整合設定頁 `ScopeOverlapWarning` 提醒設範圍

## 用詞慣例（繁中）

- 「裝置」not 「設備」、「上線」not 「在線」、「對應」not 「映射」、「作業」not 「任務」、「子網路」not 「子網」、「失聯 IP」not 「鬼 IP」、「掃描代理」not 「掃描器」、「首碼」not 「前綴」（prefix；prefix length → 首碼長度）、「外掛」not 「插件」（plugin）、「還原」not 「回滾」（rollback）、「單次」not 「一次性」（one-time）、「不中斷換檔／不中斷寫入」not 「原子覆蓋／原子寫入」（atomic write）
- **中文標點一律全形**：，。、；：「」（）？！ — 不要用半形 `,.:;()?!`。但夾在文案裡的「指令／路徑／程式碼／英數識別字」維持半形（如 `--dry-run`、`/etc/...`、`apt/dnf`）。

## 主要 frontend composables

| composable | 用途 |
|---|---|
| `useEntityLinks(router)` | 把 section/subnet/device/customer/IP 等 ID render 成可點連結 |
| `useColumnPrefs(name, defaults, all)` | 表格欄位顯示偏好，存後端 `user_preferences.table_columns` |
| `useTableSort.autoSort(cols)` | 包 DataTableColumns，對沒 sorter 的 column 加預設 sorter |
| `useCustomers()` | 全域共享 customer 清單，options/labelFor 給 dropdown 跟 display 用 |
| `usePinnedSubnets()` | Dashboard 釘選的子網路 ID list（存 prefs.pinned_subnet_ids） |
| `useLivenessSettings()` | online_grace_minutes + classifyLiveness |
| `useTablePagination()` | 全站共用表格分頁設定，pageSize 綁 ui store + `user_preferences.page_size`，size picker 即時套用+寫回+跨裝置同步 |

## API 速查

- `/api/v1/sections` `/subnets` `/addresses` `/devices` `/customers` `/customers/{id}/summary`（device/customer/rack 詳情端點皆 `require_object_perm` read，防 IDOR）`/subnets/overlaps/exists`（admin，是否有重疊網段→整合頁警告用）
- `/api/v1/nat` `/nat/bulk-delete`
- `/api/v1/system/llm` `/system/llm/models`（列 ollama tags）
- `/api/v1/oui/stats` `/oui/refresh` `/oui/lookup?mac=`
- `/api/v1/topology?include_l3=true` — L3 自動推導 device→subnet
- `/api/v1/me/preferences` — 含 pinned_subnet_ids / table_columns / online_grace_minutes / **page_size**（全站表格每頁筆數）
- IP 申請：`/api/v1/ip-requests`（list/detail，含 can_approve）`/ip-requests/{id}/approve|reject`（多關卡走 stage 簽核）`/ip-requests/policy`（審核政策 GET/PUT，4 模式）
- DNS：`/api/v1/dns/servers` `/dns/records`（filter server_id/rtype/q/ip/missing_ip，回 server_name+matched_ip_id）`/dns/records/type-counts`（型別筆數）`/dns/zones` `/dns/consistency`
- 通知：`/api/v1/notifications`（鈴鐺）；通知發送設定 `/system/notification-channels`（admin，Email/SMTP；其餘通訊軟體反灰）+ `/system/notification-channels/test-email`
- `/api/v1/firewalls/opnsense` + alias mappings + rules
- 進階資源：`/api/v1/vlans` `/vrfs` `/asns` `/tenants` `/providers` `/circuits` `/contacts` `/ssids`（皆含 PATCH 編輯）
- 佈線/電力：`/api/v1/cables` `/device-ports` `/cable-trace` `/power-*`
- SSO 設定：`/api/v1/sso/oidc` `/sso/saml`（admin DB 設定）
- 掃描代理：`/api/v1/scan-agents`（CRUD + key rotate）`/scan-agents/report`（agent push）`/scan-agents/agent.py` `/scan-agents/installer.sh`（下載）
- 憑證派送：`/api/v1/certificates`（admin CRUD + `POST /{id}/versions` 上傳 crt/key/chain 驗證+加密私鑰 + `POST /{id}/self-signed` 產自簽[CN/SAN/天數]）`/cert-agents`（admin CRUD+key rotate）+ agent 協定 `GET /cert-agents/check|bundle`、`POST /report`（X-Agent-Key）+ `agent.py`/`installer.sh`。私鑰 AES-GCM 加密、僅 agent 經 scope+TLS 取得且逐次稽核。到期/飄移告警在 `jt-ipam-sync` 每輪跑 `cert_alert.check_cert_alerts`（去重不洗版）
- 版本：`dist/version.json`（前端自動偵測新版提示重載）

## 安裝 / 帳號 CLI

- 全新安裝（`jt-ipam.sh install`）會自動建 `admin` + 隨機密碼，結束時印出一次（並存 `/etc/jt-ipam/.admin-initial-password`，root 0600）
- 重置 / 建管理員：`python -m app.cli.bootstrap create-admin --username admin --email a@b --password-stdin [--force-update]`（密碼 ≥12 字；不加 `--force-update` 是新建，既有 admin 會報錯）
- 掃描代理 installer（`agent/jt-ipam-agent-installer.sh`）會一併裝 `nmap` / `samba-common-bin`(nmblookup) / `avahi-utils`(avahi-resolve) 解鎖 os/netbios/mdns 探測（`JT_IPAM_SKIP_PROBE_TOOLS=1` 可略過）

## 升版規矩

- **升版要同時改**：`frontend/package.json` 的 `version` + `backend/app/version.py`（務必一致），以及 `README.md` / `README_zh-TW.md` 的 H1 標題版本號（`# jt-ipam vX.X.X`）。
- **bump version 前先跑 `TEST_CHECKLIST.md`**（pytest/tsc/build/migration up-down/手動點檢），綠了才升版。可跑 `scripts/ci.sh` 當本地 CI。
- 每次升版實際過一次安裝（全新）+ 升級（舊版升）流程，已整合成單一 `scripts/jt-ipam.sh`（install/upgrade/uninstall）。**安裝/代理層每次發版必驗**（TEST_CHECKLIST 5b）：(A) 安裝產 admin 預設密碼 + 重置 CLI、(B) 代理探測工具安裝 + 不可勾探測的「安裝說明」UI。
- commit/push 訊息**一律英文**；程式註解 / UI 文案仍繁中。**但 `scripts/` 與 `agent/` 的 *.sh（在客戶終端機執行的腳本）一律純英文**（註解、終端輸出、設定檔範本都不夾中文）。

## 狀態（截至 2026-06-12，v0.4.132）

已公開發布於 GitHub（Apache-2.0，程式碼最新 v0.4.132），**v0.4.132（客戶回報 + issue #4）**：修一類 asyncpg INET/CIDR 回物件非 str 的 500 — CSV 實際匯入 `str(subnet.cidr)`、IP 申請列表 `IPRequestRead.requested_ip` 加 `field_validator(mode="before")`、掃描代理帶 hostname 對新 IP 500 改 `session.add(ipa)` 後 `flush()`（autoflush=False + server_default UUID）、APITokenRead/VMInterfaceRead/ARP/FDB 同類補轉型。詳見已知地雷 #10。前版 v0.4.131：install/upgrade SOP 已在乾淨 Ubuntu 22.04 / 24.04 容器端到端驗證通過。**v0.4.131（客戶回報 Ubuntu 26.04 裝不起來）**：安裝腳本原本寫死 `postgresql-16`，但 26.04 預設庫沒有 PG16（出 17/18），舊退路去加 PGDG 對新 codename 的庫（PGDG 對剛發布的 Ubuntu 常延遲數月）→ `apt-get update` 404、整個安裝中斷。改成**偵測已啟用庫裡可用的 PG 版本**（優先 16，否則用發行版自帶 17/18…）+ 對應 `postgresql-N-pgvector`，只有完全沒有 `postgresql-N(>=16)` 才退回 PGDG；app 對 PG 16/17/18 相容；Python 偵測加入 `python3.14`。⚠️ 26.04 尚未實機端到端驗證（缺 26.04 容器），客戶若仍失敗需取得 pip/apt 實際錯誤（可能另有 Python 3.14 套件編譯問題）。

**v0.4.129→0.4.130 重點（資安 + 重複偵測韌性）**：
- **RBAC IDOR 收口**：devices / customers / rack_diagram 的詳情與子資源端點（`/devices/{id}`、`/integrations`〔曾洩 Wazuh CVE+Proxmox VM〕、`/librenms`、`/vlans`、`/relations`、`/customers/{id}`、`/{id}/summary`、`/racks/{id}/diagram`）全補 `require_object_perm(<type>,"read")`；MCP `get_topology` 補 `user=user` 過濾 + 歸 `GLOBAL_READ_TOOLS`，REST `/topology` 補 `require_global_read`
- **OIDC ID Token 驗簽**：`oidc.verify_id_token()` 用 provider JWKS 驗簽（aud/iss/nonce）才信任 claims（groups 決定 admin 提權）；驗失敗退回只用 userinfo。原本只 base64 解開就信任 → 提權風險
- **CSV 公式注入**：IP 匯出對 `= + - @`/tab/CR 開頭欄位前置跳脫
- **整合同步韌性**：`jt-ipam-sync.py` 每個 except 寫 last_error 前先 `session.rollback()`（單一整合失敗不再連鎖中斷整輪）；AdGuard sync + MCP ARP 改 `limit(1)+first()` 修重疊網段 MultipleResultsFound
- **UCS 以外 DNS 連線測試**：bind9（`except (DNSException, OSError)`）/ Windows（`_run_ps` 包 winrm 例外）/ PowerDNS+OPNsense（非 JSON）連線失敗包成 `DNSAdapterError`；`/dns/servers/{id}/test` 加 `except Exception` 安全網 → 回可讀 502（原本無訊息 500）
- **重複 IP/ARP 韌性**（確認＋補強）：scanner/librenms/opnsense 不會產生「真重複」（`ip_addresses` 唯一鍵 `(subnet_id, ip)`、`arp_entries` upsert 鍵 `(ip, mac, device_id)`、只有 LibreNMS 寫 ARP，scanner/opnsense 只 stamp 既有 IP）。補：**ARP 過期清除** `librenms.prune_stale_arp()`（刪 `last_seen_at` 超過 `arp_retention_days` 預設 30 天的舊/孤兒 ARP，sync 每輪跑）解 ARP 表無限累積；**重疊網段警告** `GET /subnets/overlaps/exists` + 前端 `ScopeOverlapWarning.vue` 掛 6 整合設定頁，未設 `scope_subnet_ids` 又有重疊網段時提醒「同步可能標到錯誤單位的同 IP」
- 用詞：plugin 一律「外掛」not「插件」（用詞表已補）

**v0.4.115→0.4.128 重點**：
- **安裝韌性（客戶回報）**：`ensure_node` 保證 Node≥18（in-PATH→nvm→NodeSource 20，先 purge 衝突 distro node 再裝完驗證，否則 die 並印手動補裝指令；不再把輸出丟 /dev/null 靜默失敗）；`build_frontend` 以 root 用乾淨工具鏈建置後 chown 回（繞過 nologin 帳號 sudo -u/PAM）；install/upgrade 兩路徑都走 build_frontend。乾淨 Ubuntu 22.04/24.04 容器實測過
- **外部反向代理 + OIDC/M365（客戶回報）**：新增 `deploy/nginx/jt-ipam-external-proxy.{conf,snippet}`（Mode C，本機只做 HTTP、不送 HSTS、`X-Forwarded-Proto` 透傳 map fallback https）；`Login.vue` 解析 callback 帶回的 `#access_token` fragment（auth store 加 `loginFromSso`，原本 token 被忽略卡登入頁）；`sso.py` 合併 ID Token payload claims 進 userinfo（Entra ID `groups` 只在 ID Token、不在 Graph userinfo→admin 群組對不上）；README 中英補 Mode C（`APP_PUBLIC_URL`/`CORS` 設對外網域、轉發 X-Forwarded-Proto/Host、OIDC redirect URI IdP+UI 都填且 UI/DB 值優先 .env）
- **DHCP/來源標示**：`ip_addresses.in_dhcp_lease`（0074，OPNsense DHCP lease sync 自動標/清、IP 詳情即時 DHCP 標籤）；`discovery_source` CHECK 放寬加 `'phpipam'`，phpIPAM 匯入改標 `phpipam`（原誤標 manual）；DHCP/ARP sync 改 scope 到防火牆關聯子網路 + `limit(1)` 取代 `scalar_one_or_none`（修重疊網段 MultipleResultsFound）
- **AI/MCP/搜尋**：MCP 加 `list_dns_records`；AI system prompt 強制「問子網路/CIDR used/free/usable」要呼叫 `get_subnet_detail`/`get_subnet_usage` 回 IPAM 實數（不准純 CIDR 算術）；AI chat 在 Ollama 未啟用/連不上時把 cryptic 後端錯誤換成友善可行動訊息（指向 管理→LLM/AI）；全域搜尋支援部分 MAC 前綴（如 `bc:24`）；AI chat markdown 加 GFM 表格
- **UX**：NAT 規則點列改唯讀檢視（欄位真正 disabled，操作欄鉛筆才編輯）+ 從頂層選單移進「進階」；更新提示橫幅改有框+陰影可點方塊 + SVG icon（非 emoji）；電路編輯「關聯裝置」下拉修復（listDevices 上限 500）+ 表格加關聯裝置/說明欄 + 狀態 i18n；裝置詳情/掃描代理欄寬收緊不溢出；IP 異動記錄 `switch_port` 顯示 device@port；IP 申請審核信可點絕對連結
- **docs/README**：README 中英逐節對齊（補核心物件/專案結構/藍圖 + RBAC/安全/技術堆疊擴寫）、移除 roadmap「Out of scope」清單、部署用詞改「虛擬機 / 容器」（前半改「不需 Docker image」避免矛盾）；CHANGELOG 中英補 0.4.114–0.4.128

**v0.4.114 之前已完成**：
- **IP 申請審核流程**：可設定審核政策（system_settings JSONB `ip_request_policy`）4 種模式 — admin / 指定使用者群組 / 多組會簽（平行，全過才配發）/ 依序多關卡（逐關通過）；逐關卡審核存 `ip_request_stage_approvals`（0073）；審核人收站內（鈴鐺）+ Email 通知；列內核准/拒絕（含 icon）、審核人可改申請 IP、詳情顯示候選自動配發 IP 與關卡進度。**通知發送設定**管理頁（Email/SMTP 已做，Telegram/Slack/Teams/Nextcloud/Zulip 反灰「開發中」）
- **DNS 記錄頁**（進階→DNS 記錄）：列整合 DNS server 記錄；依伺服器 / 型別（下拉帶 A (12) 統計）/ 搜尋 / IP 反查 / 僅顯示無對應 IP 篩選；來源欄顯示來源 DNS 伺服器名；欄位選擇。**IP 對應改用實際 IP 值查 ip_addresses**（非 sync 的 hostname 比對）修「明明有卻顯示查無對應」誤判
- **全站表格每頁筆數偏好**：`useTablePagination` 綁 ui store + 後端 `user_preferences.page_size`，size picker 改值即時套用+寫回+跨裝置同步，已套 13 張 client 分頁表
- **掃描代理探測**：三層探測模型（agent available_probes ∩ subnet ∩ per-IP excluded）、OS/NetBIOS/mDNS/rDNS 實際執行、OS icon、代理回報即時更新 effective_status、立刻執行一次（0070）
- **整合限定子網路範圍**：LibreNMS / Wazuh / Proxmox / AdGuard / DNS 皆有 scope_subnet_ids（0072）；OPNsense 關聯範圍 location/customer/subnet/iface（0071）— 解重疊網段誤配
- **RBAC 全面收斂**：require_global_read / has_global_read / can_edit；列表/詳情/搜尋/儀表板彙總/計數/趨勢全部依可見性縮放；按鈕依能力反灰。相關 [[project_rbac_ai_topology_leak]]
- **AI / MCP**：100 題實測修一輪、cursor 分頁與「下一批」續抓、異動需確認 gate、工具清單依權限過濾
- **物理層**：機櫃半 U / 正背面、裝置詳情機櫃圖、平面圖拖曳貼齊與任意角度、電源埠↔插座（0066）、Cable Trace（多跳穿透）
- **整合**：OIDC/SAML SSO 後端 DB 化 + webui、LDAP 管理頁、Graylog DSV（含串接教學頁）、Proxmox 網路介面（bridge/bond/NIC）
- **UX**：通用表格欄位選擇 + 多格式匯出（含零相依 .ods/.odt、xlsx、PDF）、版本自動偵測重載、登入整頁載入、側邊巢狀子網路 + 下層繼承單位、NAT IP hover/列點開細節、頂端列響應式
- **安裝/運維**：安裝自動產 admin 預設密碼 + 重置 CLI、代理 installer 裝探測工具 + 安裝說明 UI
- **CI**：`scripts/ci.sh` 收綠（eslint flat config / ruff / bandit + defusedxml）；仍無 GitHub Actions，靠本地 CI + TEST_CHECKLIST 手動把關

仍可加強（nice-to-have）：
- LibreNMS LLDP/CDP 鄰居連線同步、WiFi SSID 撈取：暫無資料源，擱置（#213 / #226）
- IPsec 對接無加密身分，只能靠端點精準命中；WireGuard 才是可靠配對
- GitHub default branch 有 2 個 critical Dependabot 警示待評估
