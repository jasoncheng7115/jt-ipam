# jt-ipam

可自架、以整合為核心的 IPAM（整合 DNS / LibreNMS / OPNsense / AdGuard / Wazuh / Proxmox / Ollama LLM；提供 phpIPAM 相容 API 與遷移路徑，但**並非以 phpIPAM 為核心、也非建構於 phpIPAM 之上**）。**已公開發布於 github.com/jasoncheng7115/jt-ipam（Apache-2.0）。**

## 部署環境

- **Prod**: `192.168.1.144` (ipam2 LXC)。所有改檔/重啟都 `ssh root@192.168.1.144`；prod 用 rsync 部署（見下方部署流程），**不是 git pull**
- 本機（log4 / 132）`/opt/jt-ipam` 是 dev；本機改檔不影響線上，要 rsync 過去才生效。**注意本機同時裝了 Docker**（FORWARD policy DROP，會擋 LXD 容器對外）
- 不用 Docker 部署。靠 systemd + apt（適合 Proxmox LXC / 裸機）
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
└── plugins/            # 插件系統

alembic/versions/       # 0001 ~ 0066 migrations（最新 0066 device_power_ports）
```

```
frontend/src/
├── views/              # 一個檔案一個頁面
├── components/         # 共用元件（IPAddressEditModal / SubnetGrid / GlobalSearch / NotificationBell / ColumnPicker / LiveStatusDot ...）
├── composables/        # useCustomers / usePinnedSubnets / useEntityLinks / useColumnPrefs / useTableSort / useLivenessSettings
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

## systemd services / timers

- `jt-ipam-backend.service` — uvicorn 主服務
- `jt-ipam-oui-refresh.timer` — 每月 1 號 03:30 + 15min jitter，跑 `/opt/jt-ipam/scripts/oui_refresh.py`

## 已知地雷

1. **長壽 SPA 分頁跑舊 JS bundle** — 多次「存檔沒生效 / 功能怪怪的」真因都是舊 bundle，不是後端。已用 `dist/version.json` + `useVersionCheck` 自動提示重載解掉根因；遇到先請使用者 hard refresh，再查後端。
2. **pytest filterwarnings=error 必須 ignore anyio DeprecationWarning** — 否則 ASGI 測試會 hang 在 CancelScope._deliver_cancellation
3. **AuditLog.object_id 是 UUID** — `append_audit` 要帶 request_id；object_id 別塞非 UUID
4. **避免把 phpIPAM 的缺點搬過來** — phpIPAM 的 description-only 搜尋、JSONB blob 等都別照抄
5. **Phase 4 範圍縮減** — 不做 Zimbra/Odoo/Ansible/Terraform/HA；只做 MCP/LLM/Plugin
6. ~~prod DB SQL_ASCII~~ — 已於 2026-05-31 轉 UTF8（舊庫 `jt_ipam_old_sqlascii` 留作安全網）

## 用詞慣例（繁中）

- 「裝置」not 「設備」、「上線」not 「在線」、「對應」not 「映射」、「作業」not 「任務」、「子網路」not 「子網」、「失聯 IP」not 「鬼 IP」、「掃描代理」not 「掃描器」、「首碼」not 「前綴」（prefix；prefix length → 首碼長度）

## 主要 frontend composables

| composable | 用途 |
|---|---|
| `useEntityLinks(router)` | 把 section/subnet/device/customer/IP 等 ID render 成可點連結 |
| `useColumnPrefs(name, defaults, all)` | 表格欄位顯示偏好，存後端 `user_preferences.table_columns` |
| `useTableSort.autoSort(cols)` | 包 DataTableColumns，對沒 sorter 的 column 加預設 sorter |
| `useCustomers()` | 全域共享 customer 清單，options/labelFor 給 dropdown 跟 display 用 |
| `usePinnedSubnets()` | Dashboard 釘選的子網路 ID list（存 prefs.pinned_subnet_ids） |
| `useLivenessSettings()` | online_grace_minutes + classifyLiveness |

## API 速查

- `/api/v1/sections` `/subnets` `/addresses` `/devices` `/customers` `/customers/{id}/summary`
- `/api/v1/nat` `/nat/bulk-delete`
- `/api/v1/system/llm` `/system/llm/models`（列 ollama tags）
- `/api/v1/oui/stats` `/oui/refresh` `/oui/lookup?mac=`
- `/api/v1/topology?include_l3=true` — L3 自動推導 device→subnet
- `/api/v1/me/preferences` — 含 pinned_subnet_ids / table_columns / online_grace_minutes
- `/api/v1/firewalls/opnsense` + alias mappings + rules
- 進階資源：`/api/v1/vlans` `/vrfs` `/asns` `/tenants` `/providers` `/circuits` `/contacts` `/ssids`（皆含 PATCH 編輯）
- 佈線/電力：`/api/v1/cables` `/device-ports` `/cable-trace` `/power-*`
- SSO 設定：`/api/v1/sso/oidc` `/sso/saml`（admin DB 設定）
- 版本：`dist/version.json`（前端自動偵測新版提示重載）

## 升版規矩

- **升版要同時改兩個檔**：`frontend/package.json` 的 `version` + `backend/app/version.py`，務必一致。
- **bump version 前先跑 `TEST_CHECKLIST.md`**（pytest/tsc/build/migration up-down/手動點檢），綠了才升版。可跑 `scripts/ci.sh` 當本地 CI。
- 每次升版實際過一次安裝（全新）+ 升級（舊版升）流程，已整合成單一 `scripts/jt-ipam.sh`（install/upgrade/uninstall）。
- commit/push 訊息**一律英文**；程式註解 / UI 文案仍繁中。

## 狀態（截至 2026-06-06，v0.4.79）

已公開發布於 GitHub（Apache-2.0），install/upgrade SOP 已在乾淨 Ubuntu 24.04 容器端到端驗證通過。近期重點完成：
- **RBAC 全面收斂**：require_global_read / has_global_read / can_edit；列表/詳情/搜尋/儀表板彙總/計數/趨勢全部依可見性縮放；按鈕依能力反灰。相關 [[project_rbac_ai_topology_leak]]
- **AI / MCP**：100 題實測修一輪、cursor 分頁與「下一批」續抓、異動需確認 gate、工具清單依權限過濾
- **物理層**：機櫃半 U / 正背面、裝置詳情機櫃圖、平面圖拖曳貼齊與任意角度、電源埠↔插座（0066）、Cable Trace（多跳穿透）
- **整合**：OIDC/SAML SSO 後端 DB 化 + webui、LDAP 管理頁、Graylog DSV、Proxmox 網路介面（bridge/bond/NIC）
- **UX**：通用表格欄位選擇 + 多格式匯出（含零相依 .ods/.odt、xlsx、PDF）、版本自動偵測重載、登入整頁載入
- **CI**：`scripts/ci.sh` 收綠（eslint flat config / ruff / bandit + defusedxml）；仍無 GitHub Actions，靠本地 CI + TEST_CHECKLIST 手動把關

仍可加強（nice-to-have）：
- LibreNMS LLDP/CDP 鄰居連線同步、WiFi SSID 撈取：暫無資料源，擱置（#213 / #226）
- IPsec 對接無加密身分，只能靠端點精準命中；WireGuard 才是可靠配對
- GitHub default branch 有 2 個 critical Dependabot 警示待評估
