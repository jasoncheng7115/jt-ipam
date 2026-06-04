# jt-ipam

新世代、可自架的 IPAM（整合 DNS / LibreNMS / OPNsense / AdGuard / Wazuh / Proxmox / Ollama LLM；提供 phpIPAM 相容 API 與遷移路徑，但**並非以 phpIPAM 為核心、也非建構於 phpIPAM 之上**）。

## 部署環境

- **Prod**: `192.168.1.144` (ipam2 LXC)。所有改檔/重啟都 `ssh root@192.168.1.144`
- 本機（log4 / 132）`/opt/jt-ipam` 是過時 mirror，本機改檔不會影響線上
- 不用 Docker。靠 systemd + apt（適合 Proxmox LXC / 裸機）
- 強制 HTTPS（`BACKEND_TLS_MODE=nginx` 反代 或 `direct` uvicorn 自簽）

## Stack

- **Backend**: FastAPI + SQLAlchemy 2.0 async + asyncpg + PostgreSQL 16 + Alembic + Pydantic v2
- **Frontend**: Vue 3 + TypeScript + Vite + Naive UI + Pinia + vue-i18n
- **Auth**: argon2id + TOTP + JWT（access 15 min / refresh 14 天）
- **LLM**: Ollama（本地），預設 chat=gpt-oss / embedding=qwen3-embedding；走 `/api/v1/system/llm` 全域設定
- **OWASP Top 10:2025 是硬性需求** — 每個模組與 PR 都要過心智檢查

## 專案結構

```
backend/app/
├── api/v1/endpoints/   # FastAPI routes（addresses / sections / subnets / devices / customers / nat / oui / system_settings ...）
├── api/v1/router.py    # router mount
├── core/               # db / audit / config / safe_http / encrypted_secret
├── models/             # SQLAlchemy 2.0 ORM
├── schemas/            # Pydantic v2
├── services/           # ai / oui / opnsense_firewall / phpipam_migration / topology / search
├── mcp/                # MCP server + tools（LLM 用）
└── plugins/            # 插件系統

alembic/versions/       # 0001 ~ 0026 migrations
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
- **Device** (裝置)、**Rack** (機櫃)、**Location** (地點)
- **Customer** (客戶 / 管理單位) — 2026-05 新增，掛 sections/subnets/devices/IPs
- **OPNsenseFirewall** + alias mappings + rules + NAT
- **OUIVendor** — IEEE MAC 廠商 lookup（Wireshark manuf 每月排程更新）

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

# 前端 build
rsync -az /opt/jt-ipam/frontend/src/ root@192.168.1.144:/opt/jt-ipam/frontend/src/
ssh root@192.168.1.144 'cd /opt/jt-ipam/frontend && npm run build'
```

## 常見背景作業

- `phpipam.migration` — phpIPAM SSH tunnel 匯入（含 sections/subnets/IPs/devices/customers/nat）
- `opnsense.sync` — DHCP（Kea / ISC fallback）/ ARP / OpenVPN / filter rules / NAT rules
- `librenms.sync` / `wazuh.sync` / `adguard.sync` — pull-only

## systemd services / timers

- `jt-ipam-backend.service` — uvicorn 主服務
- `jt-ipam-oui-refresh.timer` — 每月 1 號 03:30 + 15min jitter，跑 `/opt/jt-ipam/scripts/oui_refresh.py`

## 已知地雷

1. **prod DB 是 SQL_ASCII** — engine 設了 `json_serializer=ensure_ascii=False` 繞過。要根治 → 排 maintenance：`pg_dump → drop → createdb -E UTF8 → restore`
2. **pytest filterwarnings=error 必須 ignore anyio DeprecationWarning** — 否則 ASGI 測試會 hang 在 CancelScope._deliver_cancellation
3. **避免把 phpIPAM 的缺點搬過來** — phpIPAM 的 description-only 搜尋、JSONB blob 等都別照抄
4. **Phase 4 範圍縮減** — 不做 Zimbra/Odoo/Ansible/Terraform/HA；只做 MCP/LLM/Plugin

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

## 升版規矩

- **bump `frontend/package.json` version 前先跑 `TEST_CHECKLIST.md`**（pytest/tsc/build/migration up-down/手動點檢），綠了才升版。可跑 `scripts/ci.sh` 當本地 CI。

## 狀態（截至 2026-05-31，v0.4.x）

已完成（原「沒做完的」清單全清）：
- i18n：欄位標籤 + toast/placeholder/template 文字皆改 `t()`；Tools OUI tab 也做了
- 後端 pytest 126 綠（對拋棄式 UTF8 test DB 跑）
- `UserPreference.dashboard_layout`/`default_section_id` 死 code 已移除（migration 0045）
- CSV import 改背景作業（`ip.csv_import`，上限 16 MB）
- **prod DB 已從 SQL_ASCII 轉成 UTF8**（2026-05-31，dry-run 驗過才換；舊庫留 `jt_ipam_old_sqlascii` 當安全網，確認後可 drop）
- 機房平面圖：上傳底圖 + 拖拉定位 + zoom/pan + 機櫃方框(按 U)/旋轉
- VPN 對接偵測：WireGuard（公鑰，可靠）/ IPsec（端點比對，best-effort）

仍可加強（nice-to-have）：
- IPsec 對接無加密身分，只能靠端點精準命中；WireGuard 才是可靠配對
- 機房平面圖機櫃尚未做「真實腳印按比例 / 任意角度」（目前 0/90/180/270）
- 沒有自動化 CI（GitHub Actions 等）；靠 `scripts/ci.sh` + TEST_CHECKLIST 手動把關
