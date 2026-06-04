# jt-ipam IPAM 系統規格書

> 版本：v0.3（完整整合版）
> 作者：Jason Tools Co., Ltd.（節省工具箱）
> 定位：可自架、以整合為核心的現代化 IPAM 系統，深度整合多家 DNS Server、LibreNMS、OPNsense 與本地 AI；提供 phpIPAM 相容 API 與無痛遷移路徑（並非建構於 phpIPAM 之上）

---

## 〇、設計主軸

**主軸：自成一格的現代化 IPAM**；操作體驗「參考」phpIPAM 熟悉的術語與習慣以降低遷移門檻，並吸收 NetBox 的優點，但本系統並非建構於 phpIPAM 之上，也不受其架構限制。

| 維度 | 採用方 | 說明 |
|------|--------|------|
| 主資料結構 | **phpIPAM** | Section（區段）→ Subnet（子網）→ IP Address |
| 主要術語 | **phpIPAM** | 使用 Section、Subnet、Address，不用 Aggregate/Prefix |
| UI 操作流程 | **phpIPAM** | 樹狀 Section、Subnet 視覺化方塊圖、IP 表格 |
| 子網掃描 | **phpIPAM** | 保留主動掃描（NetBox 沒有） |
| NAT 模組 | **phpIPAM** | 1:1、N:1、Port Forwarding |
| Devices 設備清單 | **phpIPAM** | 簡單的設備清單，不走 NetBox 複雜的 DCIM |
| Locations | **phpIPAM** | Locations 模組 + 地圖 |
| RACK 機櫃 | **phpIPAM** | 簡單的 U 位管理 |
| 補強功能（選用） | **NetBox** | Tenancy、Contacts、Cabling、Circuits、L2VPN…使用者可選擇啟用 |

**白話說**：使用者打開 jt-ipam 看到的就是 phpIPAM 熟悉的介面（Section 樹、Subnet 方塊、IP 表格），只是介面更現代、效能更好、繁中更完整、API 更強、有 AI 助手、有 DNS/LibreNMS 雙向互通。

---

## 一、產品定位

### 1.1 核心定位
- **完整覆蓋 phpIPAM 全部功能**，phpIPAM 老使用者零學習成本
- **採用現代技術堆疊**，解決 phpIPAM 效能、UI、API 的歷史包袱
- **補強 phpIPAM 缺少的進階功能**（取自 NetBox 設計，但維持簡潔）
- **深度整合多家 DNS Server**：OPNsense Unbound、Windows DNS、BIND 9、PowerDNS
- **深度整合 LibreNMS**：雙向同步、ARP/FDB 抓取、即時狀態互補、自動加入監控
- **整合 Jason 既有開源生態系**：Proxmox VE、Wazuh、Graylog、OPNsense、Zimbra、Odoo
- **內建本地 AI 能力**：Ollama、自然語言查詢、語意搜尋
- **符合台灣資安稽核需求**：繁中介面、民國紀年、SHA-256 異動稽核

### 1.2 設計哲學
1. **phpIPAM 使用者體驗優先**
2. **進階功能可關閉**（NetBox 級模組預設關閉）
3. **API 一等公民**（phpIPAM API 完整相容，舊腳本零改動）
4. **不綁專屬技術**（開源元件、AGPL 授權）
5. **效能優先**（解決 phpIPAM /8 大網段、IPv6 /48 規模的瓶頸）
6. **整合優於重造**：能從 LibreNMS、DNS Server 拿到的資料就直接抓，不重複造輪子
7. **資安內建（Security by Design）**：對齊 OWASP Top 10:2025，詳見 `docs/SECURITY.md`

---

## 二、技術架構

### 2.1 技術堆疊

| 層級 | 選型 | 備註 |
|------|------|------|
| 後端框架 | Python 3.12 + FastAPI | 非同步、自動 OpenAPI |
| ORM | SQLAlchemy 2.0 + Alembic | |
| 資料庫 | PostgreSQL 16（原生 inet/cidr/macaddr） | 效能遠勝 phpIPAM 的 MySQL |
| 快取/佇列 | Redis 7 + RQ/Celery | 背景掃描、事件佇列 |
| 全文搜尋 | PostgreSQL pg_trgm + pgvector | 配合 AI 語意搜尋 |
| 前端 | Vue 3 + TypeScript + Vite + Naive UI | 繁中友善、深淺主題完整支援 |
| 圖表 | ECharts + Cytoscape.js | 拓樸、機櫃、ARP/FDB 關聯圖 |
| API | REST（OpenAPI 3.1）+ GraphQL + phpIPAM 相容層 | 三軌並行 |
| 認證 | 本機 / AD / LDAP / Radius / SAML2 / OIDC | argon2id + TOTP MFA |
| 部署 | systemd + nginx + apt（不採容器化）；Proxmox LXC 範本、裸機 | 與 Jason 既有運維生態一致 |
| HA | PG streaming replication + Redis Sentinel + 多副本 backend | Phase 3+ |

### 2.2 模組架構

```
jt-ipam/
├── core/              # 核心：使用者、群組、權限、自訂欄位、稽核
│
├── ── phpIPAM 主結構 ──
├── sections/          # Section 區段
├── subnets/           # Subnet 子網（IPv4/IPv6、VRF、VLAN、巢狀）
├── addresses/         # IP Address 個別位址
├── vlans/             # VLAN 管理
├── vrfs/              # VRF 管理
├── nat/               # NAT 模組（phpIPAM 招牌）
├── devices/           # 設備清單（phpIPAM 風格）
├── racks/             # 機櫃管理
├── locations/         # 地點 + 地圖
├── ip_requests/       # IP 申請工作流
├── tools/             # 計算機、搜尋、匯入匯出
│
├── ── 資料來源整合（重點） ──
├── scanner/           # 自家主動掃描（ICMP/SNMP/ARP/Nmap）
├── dns_integration/   # 多家 DNS Server 整合
│   ├── powerdns/        # PowerDNS API
│   ├── bind9/           # BIND 9（rndc / zone transfer）
│   ├── unbound_opnsense/# OPNsense Unbound API
│   └── windows_dns/     # Microsoft Windows DNS（PowerShell / WMI / DNSCmd）
├── librenms/          # LibreNMS 深度整合（重點）
│   ├── sync/            # 設備清單雙向同步
│   ├── arp/             # ARP table 抓取
│   ├── fdb/             # FDB / MAC table 抓取
│   ├── status/          # 即時上線狀態互補
│   └── auto_add/        # 自動加入監控
│
├── ── 進階模組（選用，預設關閉） ──
├── advanced/
│   ├── tenancy/         # 租戶/部門
│   ├── contacts/        # 聯絡人
│   ├── circuits/        # 線路
│   ├── cabling/         # 線材
│   ├── power/           # 電力追蹤
│   ├── wireless/        # 無線
│   ├── vpn/             # VPN/L2VPN
│   ├── virtualization/  # 虛擬化（Proxmox 同步）
│   └── asn/             # ASN 管理
│
├── ── 其他外部整合 ──
├── integration/
│   ├── proxmox/         # Proxmox VE
│   ├── opnsense/        # OPNsense（防火牆、別名、DHCP）
│   ├── wazuh/           # Wazuh（agent 同步、IOC 比對）
│   ├── graylog/         # Graylog（事件外送）
│   ├── zimbra/          # Zimbra（聯絡人）
│   └── odoo/            # Odoo ERP（客戶/Tenant）
│
├── ai/                # AI 助手：MCP Server、語意搜尋、自然語言查詢
├── plugin/            # 外掛機制
└── ui/                # Web UI
```

---

## 三、phpIPAM 主結構（核心模組）

### 3.1 Section（區段）
- **與 phpIPAM 完全一致**
- 屬性：名稱、描述、父 Section（巢狀）、權限、嚴格模式（Strict Mode）
- UI：左側樹狀導航

### 3.2 Subnet（子網）
- **與 phpIPAM 完全一致**
- 屬性：CIDR、描述、所屬 Section、VLAN、VRF、Master Subnet（自動計算）、權限、自訂欄位、Show as Folder、Mark as Used/Full、掃描設定、閾值通知
- 核心顯示：
  - **Subnet 視覺化方塊圖**（phpIPAM 招牌，保留並強化）
  - **IP 表格檢視**
  - **Used / Free 統計條**
  - **空閒空間自動顯示**（First free address / First free subnet）
- 巢狀子網自動歸屬
- 子網切割工具（Resize / Split / Renumber）

### 3.3 IP Address（位址）
- **與 phpIPAM 完全一致**
- 屬性：IP、hostname、描述、State、MAC、Owner、Switch+Port、Exclude from ping、自訂欄位、PTR ignore、Note
- **新增屬性（v0.3 整合來源）**：
  - `discovery_source`（資料來源：manual / scanner / librenms / dns / proxmox / opnsense）
  - `last_seen_scanner`（自家掃描最後上線時間）
  - `last_seen_librenms`（LibreNMS 最後上線時間）
  - `last_seen_dns`（DNS 最後解析時間）
  - `effective_status`（綜合狀態，見 §6.4）
- 變更紀錄（誰、何時、改了什麼）

### 3.4 Subnet / IP 視覺化
- **Subnet 矩陣方塊圖**（phpIPAM 招牌）
  - 一個方塊 = 一個 IP，顏色代表狀態
  - 大型網段（/16+）自動聚合縮放
- **IP 計算機**（IPv4/IPv6、CIDR、Netmask、Wildcard、EUI-64）
- **第一個可用 IP / 子網** 直接顯示

---

## 四、phpIPAM 既有功能（完整保留）

| 功能 | 說明 |
|------|------|
| VLAN 管理 | VLAN Domain、ID 1–4094、與 Subnet 關聯、衝突檢測 |
| VRF 管理 | RD、跨 VRF 重疊位址、可獨立設定允許/禁止 IP 重複 |
| NAT 管理 | 1:1、N:1（PAT）、Port Forwarding、與 OPNsense/pfSense 規則對映 |
| Devices 設備 | phpIPAM 風格簡潔清單，名稱、IP、Type、Model、Vendor、Section、Rack |
| Racks 機櫃 | 名稱、Location、U 數、簡單 U 位視覺化 |
| Locations 地點 | 名稱、地址、座標、Leaflet + OpenStreetMap 地圖 |
| IP Requests | 多階段簽核、Email/Telegram/Slack 通知 |
| Subnet 主動掃描 | ICMP / SNMP / ARP / mDNS / NetBIOS / Nmap |
| 匯入/匯出 | CSV/XLS、RIPE、TWNIC、JSON/YAML |
| Email 通知 | 子網閾值、IP 申請、過期提醒 |
| 自訂欄位 | 全物件支援、多型別、Regex 驗證 |
| 變更紀錄 | 完整 Before/After diff + SHA-256 雜湊鏈 |
| IP 計算機 | IPv4/IPv6 |
| 全文搜尋 | + AI 語意搜尋（pgvector） |

---

## 五、DNS Server 多家整合（v0.3 重點）

### 5.1 設計目標
讓 IPAM 與多種 DNS Server 雙向互通：IPAM 是 IP 規劃的 Source of Truth，DNS 是查詢服務，兩者資料要能對得起來。**IP 的主機名既能從 IPAM 推送到 DNS，也能從 DNS 抓回 IPAM 顯示「實際正反解狀態」**，方便快速找出文件 vs 實際不一致。

### 5.2 支援的 DNS Server

| DNS Server | 介接方式 | 雙向能力 |
|-----------|---------|---------|
| **PowerDNS** | HTTP API（既有 phpIPAM 機制） | 讀+寫 |
| **BIND 9** | rndc + AXFR/IXFR zone transfer + nsupdate（TSIG） | 讀+寫 |
| **OPNsense Unbound** | OPNsense REST API（`/api/unbound/*`） | 讀+寫 |
| **Microsoft Windows DNS** | WinRM + PowerShell（`Get-DnsServerResourceRecord` / `Add-DnsServerResourceRecord`） | 讀+寫 |
| **Knot DNS / NSD** | 選用，第二期支援 | 讀+寫 |

### 5.3 共用資料模型

```
DNSServer
├── id
├── name                     # "OPNsense-Ankang-Unbound"
├── type                     # powerdns / bind9 / unbound_opnsense / windows_dns
├── connection               # API URL / IP / 連接憑證（加密儲存）
├── credentials              # API key / TSIG / WinRM 帳密（加密）
├── enabled
├── sync_interval            # 預設 5 分鐘
└── last_sync_at

DNSZone
├── id
├── server_id → DNSServer
├── name                     # "example.com"、"168.192.in-addr.arpa"
├── type                     # forward / reverse
├── associated_subnets[]     # 與哪些 Subnet 關聯（反解 zone 自動配對）
├── managed                  # true: IPAM 主動寫入；false: 只讀
└── last_sync_at

DNSRecord
├── id
├── zone_id → DNSZone
├── name                     # "host01"
├── type                     # A / AAAA / PTR / CNAME / MX / TXT / SRV
├── value                    # "192.168.1.10"
├── ttl
├── source                   # manual / from_ipam / from_dns_pulled
├── ipam_address_id → IPAddress  # 對應的 IP（若有）
└── last_seen_at
```

### 5.4 同步行為

#### 5.4.1 IPAM → DNS（推送）
- 觸發：建立/編輯 IP，且該 IP 所屬 Subnet 啟用「Auto DNS」
- 動作：依 Subnet 設定的 DNS Server 與 Zone，建立/更新 A/AAAA + PTR
- 寫入失敗：標記 `dns_sync_failed`，UI 紅色警示
- 干跑（Dry-run）：可預覽即將寫入的紀錄，不實際下發

#### 5.4.2 DNS → IPAM（拉取）
- 排程任務（預設 5 分鐘）AXFR/API 抓取所有 zone 的紀錄
- 比對結果分四類：
  1. **一致**：IP 與 hostname 在兩邊都相同（綠燈）
  2. **IPAM 有、DNS 沒有**：建議推送（黃燈）
  3. **DNS 有、IPAM 沒有**：可一鍵匯入為 IP（黃燈）
  4. **兩邊不一致**：IP 同但 hostname 不同 / 反解不對等（紅燈）
- 在 IP 詳細頁顯示「DNS 實際解析狀態」區塊

### 5.5 Subnet 詳細頁的 DNS 區塊

每個 Subnet 可指定：
- 正解 Zone（多個，例：`example.com`、`internal.example.com`）
- 反解 Zone（自動依 CIDR 推算，例：`168.192.in-addr.arpa`）
- DNS Server（選用多台，主從）
- Auto DNS 開關
- TTL 預設值

### 5.6 Windows DNS 特別處理
- 透過 WinRM（HTTPS）連線到 Windows Server
- 使用 `Get-DnsServerZone`、`Get-DnsServerResourceRecord`、`Add/Set/Remove-DnsServerResourceRecord`
- 帳密以 Kerberos / NTLM，存於加密設定檔
- AD 整合的 zone 也支援
- 取代過去人工同步的痛點

### 5.7 OPNsense Unbound 特別處理
- 透過 OPNsense REST API：
  - `GET /api/unbound/settings/get` 取得 Host Override / Domain Override
  - `POST /api/unbound/settings/addHostOverride` 新增 Host
  - `POST /api/unbound/service/reconfigure` 套用變更
- 配合 OPNsense DHCP 靜態對應，可同步 IPAM ↔ DHCP ↔ Unbound 三邊

### 5.8 BIND 9 特別處理
- AXFR / IXFR：使用 TSIG 金鑰做 zone transfer 拉資料
- nsupdate：以 TSIG 對 dynamic zone 做新增/刪除
- 也支援唯讀模式（只拉、不寫）

### 5.9 UI 呈現

| 介面 | 顯示內容 |
|------|---------|
| IP 詳細頁 | 對應的正解、反解紀錄；DNS 是否一致；快速「重新推送」按鈕 |
| Subnet 詳細頁 | 該網段的 DNS Zone、整體一致率（例：97% 一致） |
| DNS Servers 管理頁 | 列出所有 DNS Server、連線狀態、最後同步時間、失敗紀錄 |
| DNS Zones 頁 | 列出所有 Zone、紀錄數、與哪些 Subnet 關聯 |
| 不一致報表 | 全系統不一致 IP 清單，可批次處理 |

---

## 六、LibreNMS 深度整合（v0.3 重點）

### 6.1 設計目標
LibreNMS 已經在做 SNMP 監控、ARP table 抓取、FDB 抓取，這些資料對 IPAM 極有價值，**直接從 LibreNMS API 取用**而不重複造輪子。同時 IPAM 發現的新裝置可選擇性地自動加入 LibreNMS 監控，形成閉環。

### 6.2 LibreNMS API 對接

| LibreNMS API | 用途 |
|--------------|------|
| `GET /api/v0/devices` | 取得所有受監控裝置清單 |
| `GET /api/v0/devices/{id}` | 取得單一裝置詳細（含狀態、uptime、SNMP 資訊） |
| `POST /api/v0/devices` | **自動加入監控**（v1 / v2c / v3） |
| `GET /api/v0/devices/{id}/ports` | 取得裝置所有 port |
| `GET /api/v0/resources/ip/arp/{ip}` | **依 IP 查 ARP**（重點） |
| `GET /api/v0/devices/{id}/fdb` | **取得裝置 FDB / MAC table**（重點） |
| `GET /api/v0/resources/fdb/{mac}` | **依 MAC 反查在哪台 switch 哪個 port**（重點） |
| `GET /api/v0/devices/{id}/availability` | 上線狀態與可用率 |
| `GET /api/v0/alerts` | 取得告警 |

### 6.3 雙向同步：Devices

#### 6.3.1 LibreNMS → IPAM
- 排程任務（預設 5 分鐘）拉取裝置清單
- 比對 IPAM 內 Devices：
  - LibreNMS 有、IPAM 沒有 → 在「未匹配清單」顯示，可一鍵匯入或忽略
  - 兩邊都有 → 同步上線狀態、SNMP 資訊（型號、OS、序號、uptime）
  - IPAM 有、LibreNMS 沒有 → 在 IPAM 該 Device 顯示「未受監控」標籤，可一鍵加入監控（見 §6.5）

#### 6.3.2 同步欄位
- 主機名、IP、SNMP sysDescr / sysObjectID、廠牌、型號、OS、版本、序號、Uptime、地點

### 6.4 上線狀態互補（重點功能）

#### 6.4.1 問題情境
phpIPAM 自家掃描有限制：
- 防火牆封 ICMP → 誤判離線
- 設備在另一個網段 → 掃描器到不了
- 掃描頻率受限

但 LibreNMS 走 SNMP，視角不同，能補上這些盲點。

#### 6.4.2 綜合狀態（effective_status）

| 自家 Scanner | LibreNMS | effective_status | UI 顯示 |
|------------|----------|-----------------|---------|
| Online | Online | **Online** | 綠（雙確認） |
| Online | (no data) | **Online (scanner)** | 綠 |
| Offline | Online | **Online (via LibreNMS)** | 綠（標註來源） |
| Online | Offline | **Online (scanner)** | 綠（LibreNMS 可能延遲） |
| Offline | Offline | **Offline** | 紅 |
| (no data) | Online | **Online (LibreNMS only)** | 綠（標註） |
| Offline | (no data) | **Offline** | 紅 |
| (no data) | (no data) | **Unknown** | 灰 |

- 每筆 IP 顯示「狀態來源」：scanner / librenms / both
- 滑鼠移上去可看到 `last_seen_scanner` 與 `last_seen_librenms` 兩個時間
- 此邏輯可在「設定 → 狀態判定」自訂優先權重

#### 6.4.3 IP 詳細頁狀態區塊

```
狀態：● 上線（綜合判定）
  自家掃描：● 上線    最後回應：2026-05-06 09:32:11
  LibreNMS：● 上線    最後輪詢：2026-05-06 09:33:45
  資料來源：scanner + librenms
```

### 6.5 自動加入 LibreNMS 監控（重點功能）

#### 6.5.1 流程
1. 自家 Scanner 發現新 IP（之前沒看過的）
2. 嘗試 SNMP 探測（v1 / v2c / v3，使用設定中的 community / 認證資料庫）
3. 若 SNMP 探測成功 → 在 IP 旁顯示「 SNMP 可達」徽章與「加入 LibreNMS」按鈕
4. 使用者點擊或啟用「自動加入」規則時，呼叫 `POST /api/v0/devices` 加入監控
5. **以單一 IP 為單位決定要不要加入**，不是全網一刀切

#### 6.5.2 自動加入規則（可條件設定）
- 條件可組合：
  - Subnet 等於 X
  - VLAN 等於 X
  - hostname 符合 regex
  - SNMP sysObjectID 符合（自動辨識 Cisco / HPE / Aruba…）
  - 設備類型 = Switch / Router / Firewall / AP
- 動作：
  - 自動加入（直接送 API）
  - 建議加入（在「待審清單」等使用者確認）
  - 忽略
- 可設定 SNMP profile（v2c community 或 v3 帳密 group）

#### 6.5.3 反向流程
- 在 IPAM 刪除 Device → 詢問是否同步從 LibreNMS 移除
- LibreNMS 移除某 Device → IPAM 顯示「已不在監控」

### 6.6 ARP Table 抓取（重點）

#### 6.6.1 資料來源
LibreNMS 對所有受監控的 L3 裝置（Router、Firewall、L3 Switch）會週期性 SNMP 抓 `ipNetToMediaTable`（ARP cache），這份資料 IPAM 直接拿來用。

#### 6.6.2 資料模型

```
ARPEntry
├── id
├── ip                       # inet
├── mac                      # macaddr
├── device_id → LibreNMSDevice  # 從哪台抓到
├── interface                # 哪個介面
├── vrf                      # 若該裝置有 VRF
├── first_seen_at
├── last_seen_at
└── source                   # librenms / opnsense / proxmox_host
```

#### 6.6.3 抓取頻率
- 預設每 15 分鐘從 LibreNMS API 同步一次
- 可手動觸發單一裝置抓取

#### 6.6.4 用途
- **自動補 IP 的 MAC**：IPAM 有 IP 但沒填 MAC，從 ARP table 自動填入
- **MAC 衝突偵測**：同一 MAC 出現在多個 IP（正常）；同一 IP 不同 MAC（異常，可能 IP 衝突）
- **找鬼 IP**：IPAM 有 IP 紀錄但 ARP 從沒看過 → 可能是離線太久或假紀錄
- **找未紀錄 IP**：ARP 有但 IPAM 沒有 → 可一鍵匯入

### 6.7 FDB / MAC Table 抓取（重點）

#### 6.7.1 資料來源
LibreNMS 對 L2 裝置（Switch）週期性抓 `dot1dTpFdbTable`（橋接 MAC table）或 `qBridgeMib`（VLAN 感知版本）。

#### 6.7.2 資料模型

```
FDBEntry
├── id
├── mac                      # macaddr
├── vlan_id
├── device_id → LibreNMSDevice  # switch
├── port_id → DevicePort     # 接在哪個 port
├── port_name                # "Gi0/24"
├── first_seen_at
├── last_seen_at
└── source                   # librenms
```

#### 6.7.3 完整鏈路推導
透過 ARP + FDB 雙表 join，可以推導出：

```
IP (IPAM)
  ↓ ARP (LibreNMS)
MAC
  ↓ FDB (LibreNMS)
Switch + Port
  ↓ Cabling（選用模組，若啟用）
實體 Patch Panel + 跳線
```

這正是 phpIPAM 一直靠人工維護「IP → Switch + Port」欄位的痛點，現在自動化。

#### 6.7.4 IP 詳細頁顯示

```
網路位置（自動推導）
├─ MAC：00:11:22:33:44:55（來源：LibreNMS ARP @ Core-Router-Ankang，2 分鐘前）
├─ Switch：Access-SW-3F-01
├─ Port：Gi0/24（VLAN 100）
└─ 上聯：Core-SW-Ankang Te1/1（如啟用 Cabling 模組）
```

點擊可展開時間軸：「最近 7 天此 IP 出現在哪些 port」（移機追蹤）。

### 6.8 拓樸視覺化（補強）

整合 LibreNMS 的 LLDP / CDP 資料 + IPAM 的 IP/Subnet/VLAN，產生互動式拓樸圖（Cytoscape.js）：
- 點選 Switch 可看接在它各 port 上的所有 MAC/IP
- 點選 IP 可高亮其完整路徑
- 顏色區分 VLAN
- 可匯出 PNG / SVG / PDF（用於文件、稽核）

### 6.9 衝突與異常偵測

| 偵測項 | 邏輯 |
|--------|------|
| IP 衝突 | 同一 IP 在不同時間有不同 MAC（短時間內） |
| MAC 漂移 | 同一 MAC 在多個 switch port 跳動 |
| 鬼 IP | IPAM 有但 ARP/FDB 從未出現過超過 N 天 |
| 未授權設備 | ARP 出現但 IPAM 沒有，且不在白名單內 |
| 跨 VLAN 異常 | MAC 出現在預期之外的 VLAN |

異常觸發：站內通知 + Email + Telegram + 寫入 Graylog（jt-glogarch 歸檔，符合稽核）。

### 6.10 LibreNMS 設定頁

可在 IPAM 內：
- 設定多台 LibreNMS 實例（多站）
- 設定 API URL + Token
- 設定每個整合項的開關（裝置同步 / ARP / FDB / 上線狀態 / 自動加入）
- 設定同步頻率
- 顯示連線狀態與最後同步時間
- 同步失敗的錯誤紀錄

---

## 七、進階模組（選用，預設關閉）

> 這些是 NetBox 才有、phpIPAM 沒有的功能，作為補強加入。使用者可在「系統設定 → 模組管理」決定是否啟用，不啟用則 UI 完全不顯示。

| 模組 | 內容 |
|------|------|
| Tenancy | Tenant Group / Tenant，多客戶/多部門隔離 |
| Contacts | Contact Group / Contact / Role，可掛 Site/Device/Subnet，與 Zimbra 同步 |
| Circuits | Provider（中華電信、台固…）、Circuit Type、月租費、合約到期 |
| Cabling | 端對端追蹤、Patch Panel 對映、Cable Trace |
| Power | Power Panel → Feed → PDU → Device、雙路電源冗餘檢查 |
| Wireless | SSID 管理、無線回傳鏈路 |
| VPN / L2VPN | IKE/IPsec Tunnel、VPLS/VXLAN/EVPN，與 OPNsense/Strongswan 整合 |
| Virtualization | Cluster / VM / VM Interface，**與 Proxmox VE 雙向同步** |
| ASN 管理 | 16/32-bit ASN，與 Site/Tenant 關聯 |

---

## 八、核心系統功能

### 8.1 認證與權限
- **本機帳號**（密碼複雜度、TOTP MFA、argon2id）
- **AD / LDAP**（phpIPAM 既有）
- **Radius**（phpIPAM 既有）
- **SAML 2.0**（補強：Keycloak、Azure AD、Google Workspace）
- **OIDC / OAuth2**（補強）
- **API Token**（每 Token 可設定權限範圍與到期日）
- **權限模型**：
  - Per Section / Per Subnet 權限（phpIPAM 一致）
  - User Group 群組權限
  - 補強：物件級權限、標籤過濾

### 8.2 異動稽核
- 每筆寫入紀錄：時間、使用者、IP、Before/After diff
- **SHA-256 雜湊鏈**（與 jt-glogarch 設計一致）
- 可外送至 Graylog
- 保留期可設定

### 8.3 通知
- Email（SMTP / Zimbra）
- Telegram Bot
- Webhook
- 站內通知中心
- 自訂模板

### 8.4 搜尋
- 全文搜尋（PostgreSQL FTS + pg_trgm）
- 進階篩選（每欄位 operator）
- 儲存的查詢
- **AI 語意搜尋**（pgvector + qwen3-embedding:8b）

### 8.5 背景工作
- 排程（Celery Beat）：DNS 同步、LibreNMS 同步、ARP/FDB 抓取、Subnet 掃描
- 即時（RQ）
- 任務狀態頁、失敗重試、log 查詢

### 8.6 報表
- 內建：IP 使用率、Subnet 使用率、設備保固到期、孤兒 IP、DNS 不一致、未受監控設備、ARP/FDB 異常
- 自訂報表（SQL/GraphQL 儲存）
- PDF 匯出（Playwright，與 jt-glogarch 一致）
- 民國紀年支援、浮水印

### 8.7 匯入/匯出
- CSV / XLS / XLSX（保留 phpIPAM 行為）
- JSON / YAML
- RIPE / TWNIC
- **phpIPAM 完整資料庫遷移工具**（一鍵搬遷）
- NetBox 資料匯入（從現有 NetBox 帶入）

---

## 九、UI 與外觀

### 9.1 整體版面（phpIPAM 風格）
- **左側樹狀導航**（Section 樹）
- **頂部工具列**：搜尋、語言切換、主題切換、使用者選單、通知中心
- **主內容區**：Subnet 視覺方塊、IP 表格、Dashboard
- **儀表板**：phpIPAM 風格的 Top stats（Subnet 數、IP 使用率、最近異動、DNS 不一致、未受監控裝置…）

### 9.2 多語言（i18n）
- **首發版本支援兩種語言**：
  - **正體中文（台灣）zh-TW**：一級公民
  - **English (en-US)**：完整對應
- 預留語系擴充框架（gettext / ICU MessageFormat），未來可加 ja-JP、zh-CN
- 切換方式：登入頁、使用者偏好、Accept-Language 自動偵測、URL `?lang=` 強制
- 物件名稱（Site name、Tenant name…）由使用者輸入，不翻譯
- 系統內建字典（Status、Role…）有雙語對應
- 自訂欄位 label 支援雙語輸入
- 日期/時間/紀年：繁中可切西元/民國，英文 ISO 8601
- PDF 報表依語系產生（繁中思源黑體、英文 Inter / Noto Sans）

### 9.3 主題（Theme）
- **三種模式**：
  - **Light（淺色）**：白底深字，適合白天辦公室、列印
  - **Dark（深色）**：深底淺字，適合機房值班、長時間操作
  - **Auto（自動）**：跟隨作業系統 `prefers-color-scheme`
- 切換方式：右上角按鈕一鍵切換、儲存於使用者偏好
- 實作：CSS Variables（單套樣式表）
- 圖表（ECharts、Cytoscape、Subnet 方塊圖）兩主題下皆需可讀
- 符合 WCAG 2.1 AA 對比度（內文 4.5:1、大字 3:1）
- 色盲友善 palette 切換選項
- PDF 報表固定淺色（列印友善）

### 9.4 使用者偏好（統一儲存）
- 語言、主題、時區、紀年、每頁筆數、預設 Section、Dashboard 卡片設定
- 儲存於 `user_preferences` 表

---

## 十、API 與整合

### 10.1 phpIPAM API 相容層（重要）
- **完整對應 phpIPAM v1.7 API endpoint**
- 舊腳本零改動可遷移
- 路徑首碼 `/api/phpipam/`
- 同樣的 token 機制

### 10.2 現代 REST API
- OpenAPI 3.1 自動文件（Swagger UI + Redoc）
- 完整 CRUD、過濾、排序、分頁、欄位選擇、Bulk
- 路徑首碼 `/api/v1/`
- API Token 限流

### 10.3 GraphQL API
- 巢狀查詢（特別適合「IP → MAC → Switch Port → 上聯」這種多層 join）
- Subscription（即時推送：新 IP 出現、狀態變化）

### 10.4 MCP Server
- 暴露 IPAM 能力給 LLM（Ollama gpt-oss:120b、qwen3.5:122b）
- 工具：`search_ip`、`allocate_subnet`、`find_free_ip`、`get_device_by_ip`、`list_vlans`、`trace_mac`、`check_dns_consistency`…
- 整合 Jason 既有 Telegram Bot + Node-RED + Ollama
- 自然語言：「幫我在安康機房 VLAN 100 找一個空閒 IP 給新 Wazuh agent」

### 10.5 Webhook 出站
- 物件變更 → POST 到指定 URL
- HMAC 簽章
- 重試機制
- 目標 URL 須過 SSRF 白名單（A10）

### 10.6 完整外部整合矩陣

| 系統 | 整合內容 | 階段 |
|------|---------|------|
| **phpIPAM** | 完整資料遷移工具 + API 相容層 | Phase 1 |
| **PowerDNS** | 正反解雙向同步（既有保留） | Phase 1 |
| **BIND 9** | AXFR/IXFR + nsupdate（TSIG） | Phase 2 |
| **OPNsense Unbound** | REST API 雙向 | Phase 2 |
| **Windows DNS** | WinRM + PowerShell 雙向 | Phase 2 |
| **LibreNMS** | 裝置同步、ARP、FDB、狀態互補、自動加入 | Phase 2 |
| **OPNsense / pfSense** | DHCP scope、別名、NAT 規則 | Phase 3 |
| **Proxmox VE** | VM/CT/Node 同步、自動配 IP、SDN | Phase 3 |
| **Wazuh** | Agent IP/主機名同步、IOC 比對 | Phase 3 |
| **Graylog** | IP 異動事件外送、查詢端點關聯 | Phase 2 |
| **Zimbra** | 聯絡人同步 | Phase 4 |
| **Odoo ERP** | 客戶/Tenant 同步 | Phase 4 |

---

## 十一、AI 能力

### 11.1 本地 AI 助手
- 對接本地 Ollama（gpt-oss:120b、qwen3-vl）
- **資料完全不外送**（符合政府/企業客戶資安要求）
- UI 右下角浮動按鈕

### 11.2 應用場景
1. **自然語言查詢**：「列出所有過保的 Dell PowerEdge 且接在 VLAN 100」
2. **智慧配發**：「我要部署 5 台新 Proxmox 節點，幫我規劃 IP 與 VLAN」
3. **異常偵測**：MAC 漂移、IP 衝突、鬼 IP、未授權設備
4. **文件生成**：自動產出網路拓樸說明、機櫃配置報告（繁中/英文）
5. **OCR 匯入**：拍機房白板照片，自動辨識手寫網段規劃（qwen3-vl）
6. **合規檢查**：自動檢視 IP 配發是否符合內部政策

### 11.3 嵌入式語意搜尋
- qwen3-embedding:8b 將每筆物件描述向量化
- pgvector 儲存
- 模糊查詢、相似物件推薦

---

## 十二、資安與部署

### 12.1 資安（總覽，細節見 docs/SECURITY.md）
- 對齊 OWASP Top 10:2025
- **TLS 強制**：使用者層一律 HTTPS；支援兩模式（① nginx 反代終結，② uvicorn 直接吃自簽）
- SHA-256 異動鏈
- Graylog 外送
- 登入失敗鎖定、IP 白名單、API Token 到期
- 個資去識別化選項
- 敏感欄位加密儲存（DNS 帳密、SNMP community、API Key）

### 12.2 合規
- 符合 ISMS / ISO 27001 稽核需求
- 民國紀年支援
- 備份策略：每日 PG pg_dump + 每週完整 + 異地（Proxmox Backup Server 安康/太平雙站）

### 12.3 部署形式（不採容器化）
1. **Proxmox LXC 範本**（首選，Jason 客戶熟悉）
2. **裸機 Debian / Ubuntu**（systemd + nginx + apt 套件）
3. **離線安裝包**（封閉政府環境；含 .deb 與 wheel 快取）

> 一鍵腳本見 `scripts/install-debian.sh`；systemd unit 見 `deploy/systemd/`。

### 12.4 系統需求（最小）
- 2 vCPU、4 GB RAM、20 GB 磁碟
- PostgreSQL 16+、Redis 7+、Python 3.12+
- 建議：Proxmox VE LXC 容器

### 12.5 高可用
- PostgreSQL Streaming Replication
- Redis Sentinel
- 應用層無狀態（多副本）
- 健康檢查端點（/healthz、/readyz）

### 12.6 授權
- **AGPL-3.0**（建議）
- 商業支援由 Jason Tools 提供

---

## 十三、開發路線圖（v0.3 落地狀態）

### Phase 1 ：phpIPAM 等價 + 升級
-  Section / Subnet / IP Address 三層核心
-  VLAN / VRF / NAT
-  自家 Subnet 掃描（ICMP；SNMP/ARP/Nmap Phase 2 Celery 排程版）
-  Devices / Racks / Locations / IP Requests（含 timeline 狀態機）
-  認證（本機 + LDAP/AD/Radius）+ argon2id + TOTP + 帳號鎖定 + API Token
-  phpIPAM API 相容層（讀寫）
-  phpIPAM 資料**同步**工具（多次匯入、衝突策略、平行使用）
-  PowerDNS 整合
-  繁中/英文雙語、深淺主題
-  **systemd + nginx + apt 部署**（Docker 已不採用，改 Proxmox LXC / 裸機）
-  **OWASP Top 10:2025 baseline 全面落地**（含 A10 Mishandling of Exceptional Conditions）
-  **強制 SSL（nginx 反代 / uvicorn 自簽 雙模式）**
-  Subnet 視覺方塊圖、Rack U 位視覺化、IP 指示儀表板
-  Tools（IP/CIDR 計算機、EUI-64）
-  Custom Fields、CSV 匯入/匯出（dry-run、idempotent）
-  RIPE / TWNIC whois 匯入
-  通知中心（站內 + Webhook + SMTP）
-  全文搜尋 + 自動偵測查詢類型

### Phase 2 ：DNS 多家整合 + LibreNMS 深度整合 + AI 語意搜尋
-  BIND 9（AXFR + nsupdate TSIG）
-  OPNsense Unbound（REST host override）
-  Windows DNS（WinRM + PowerShell）
-  PowerDNS（v4 HTTP API）
-  DNS 雙向同步、不一致偵測報表
-  LibreNMS 裝置雙向同步
-  LibreNMS ARP table 抓取（自動補 IP 的 MAC）
-  LibreNMS FDB / MAC table 抓取
-  上線狀態互補（effective_status §6.4.2 真值表）
-  自動加入 LibreNMS 監控
-  IP → MAC → Switch Port 自動推導 trace
-  異常偵測（IP 衝突 / MAC 漂移 / 鬼 IP / 未授權 IP）
-  SHA-256 異動鏈、Graylog 外送
-  現代 REST API + GraphQL（Strawberry，read-only）
-  AI 語意搜尋（pgvector + Ollama embedding）

### Phase 3 ：進階模組 + 整合 + 拓樸 + SSO
-  Tenancy（TenantGroup / Tenant）
-  Contacts（Group / Role / Contact / 多型 Assignment）
-  Circuits（Provider / Type / Circuit）
-  Cabling（Cable + 多型 Termination）
-  Power（Panel → Feed → Outlet）
-  Wireless（SSID + Link）
-  VPN / L2VPN（IPsec/WG/L2TP/VxLAN/VPLS/EVPN）
-  Virtualization（Cluster / VM / Interface）+ Proxmox VE 同步
-  ASN
-  拓樸視覺化（Cytoscape.js + cose-bilkent）
-  OIDC SSO（discovery + state/nonce + auto-provision）
-  SAML 2.0 SSO（python3-saml；metadata/ACS/SLO；assertion 簽章預設 on）
-  OPNsense 防火牆 alias 同步（雙向；selector by section/subnet/tag/custom_field）
-  Wazuh agent inventory 同步 + missing-agent 偵測（給 SOC 的漏裝清單）

### Phase 4 （縮減版）：AI / Plugin
-  MCP Server（暴露 IPAM 工具給本地 LLM；JSON-RPC 2.0 子集）
-  本地 LLM 自然語言查詢（Ollama chat + tool use；UI 浮動視窗）
-  Plugin 機制（importlib.metadata entry_points + admin 列表 + 文件）

### Out of scope（本專案明確不做）
-  HA 部署（PG streaming + Redis Sentinel + 多副本 backend）
-  Ansible Collection（jasontools.jt-ipam）
-  Terraform Provider
-  Zimbra 聯絡人同步
-  Odoo ERP 同步
-  Docker / Helm Chart / Kubernetes 容器化部署

---

## 十四、命名與品牌

- 專案名稱：`jt-ipam`
- GitHub：`github.com/jasontools/jt-ipam`
- 官方網站：`ipam.example.com`
- Demo：`demo-ipam.example.com`
- 文件：`docs-ipam.example.com`
- Logo 風格：延續 jt- 系列（簡潔、藍綠色系，深淺主題各一版本）

---

## 附錄 A：phpIPAM 功能對照（一比一覆蓋）

| phpIPAM 功能 | jt-ipam 對應 | Phase |
|---|---|---|
| Section 區段 | Section（同名同邏輯） | 1 |
| Subnet 子網 | Subnet（同名同邏輯） | 1 |
| IP Address 位址 | IP Address（同名同邏輯） | 1 |
| Subnet 視覺化方塊 | Subnet 視覺化方塊（保留） | 1 |
| 自動空閒空間顯示 | First free address / subnet | 1 |
| 自動子網掃描 | Scanner（ICMP/SNMP/Nmap） | 1 |
| PowerDNS 整合 | PowerDNS 模組 | 1 |
| NAT 支援 | NAT 模組 | 1 |
| RACK 管理 | Racks 模組 | 1 |
| AD/LDAP/Radius | Auth | 1 |
| 群組權限 | Per Section/Subnet 權限 | 1 |
| 設備管理 | Devices 模組 | 1 |
| RIPE 匯入 | Import（+ TWNIC） | 1 |
| XLS/CSV 匯入 | Import | 1 |
| IP 申請模組 | IP Requests | 1 |
| REST API | phpIPAM API 相容層 | 1 |
| Locations 模組 | Locations + 地圖 | 1 |
| VLAN 管理 | VLAN 模組 | 1 |
| VRF 管理 | VRF 模組 | 1 |
| IPv4 / IPv6 計算機 | Tools | 1 |
| IP 資料庫搜尋 | Search（+ AI 語意） | 1 |
| Email 通知 | Notification（+ Telegram/Webhook） | 1 |
| 自訂欄位 | Custom Fields | 1 |
| 翻譯 | i18n（繁中/英文） | 1 |
| 變更紀錄 | Change Log（+ SHA-256） | 1 |

## 附錄 B：v0.3 新增功能總表

| 類別 | 功能 | Phase |
|------|------|------|
| DNS 整合 | OPNsense Unbound 雙向同步 | 2 |
| DNS 整合 | Windows DNS 雙向同步（WinRM） | 2 |
| DNS 整合 | BIND 9 雙向同步（TSIG） | 2 |
| DNS 整合 | DNS ↔ IPAM 不一致報表 | 2 |
| LibreNMS | 裝置雙向同步 | 2 |
| LibreNMS | ARP table 抓取 | 2 |
| LibreNMS | FDB / MAC table 抓取 | 2 |
| LibreNMS | 上線狀態互補（effective_status） | 2 |
| LibreNMS | 自動加入監控（個別決定） | 2 |
| LibreNMS | IP→MAC→Switch Port 自動推導 | 2 |
| LibreNMS | MAC 漂移、鬼 IP 異常偵測 | 2 |
| UI | 繁中/英文雙語 | 1 |
| UI | 深淺主題（含 Auto） | 1 |
| AI | MCP Server | 4 |
| AI | 本地 LLM 自然語言查詢 | 4 |
| AI | 語意搜尋（pgvector） | 2 |
| Security | OWASP Top 10:2025 baseline | 1 |
| Security | argon2id + TOTP MFA | 1 |
| Security | SHA-256 異動鏈 | 2 |
| Security | SSRF allowlist | 2 |

## 附錄 C：NetBox 補強功能對照（選用）

| NetBox 功能 | jt-ipam 對應 | 預設 | Phase |
|---|---|---|---|
| Tenancy | 進階模組 | 關閉 | 3 |
| Contacts | 進階模組 | 關閉 | 3 |
| Circuits | 進階模組 | 關閉 | 3 |
| Cabling | 進階模組 | 關閉 | 3 |
| Power Tracking | 進階模組 | 關閉 | 3 |
| Wireless | 進階模組 | 關閉 | 3 |
| VPN / L2VPN | 進階模組 | 關閉 | 3 |
| Virtualization | 進階模組 | 關閉 | 3 |
| ASN 管理 | 進階模組 | 關閉 | 3 |
| GraphQL API | API | 啟用 | 2 |
| Plugin 機制 | Plugin | 啟用 | 4 |

---

**v0.3 整合重點**
1. 主軸明定為「phpIPAM 為準、NetBox 為輔」
2. 加入 phpIPAM API 相容層（舊腳本零改動）與一鍵資料遷移工具
3. 繁中/英文雙語 + 深淺主題明確列入 Phase 1
4. **新增 DNS 多家整合**：OPNsense Unbound、Windows DNS、BIND 9、PowerDNS 雙向同步
5. **新增 LibreNMS 深度整合**：
   - 裝置雙向同步
   - ARP table 抓取（自動補 IP 的 MAC）
   - FDB table 抓取（自動定位 IP 接在哪個 Switch Port）
   - 上線狀態互補（自家測不到時用 LibreNMS 結果）
   - 自動加入監控（以單一裝置為單位個別決定）
6. 異常偵測：IP 衝突、MAC 漂移、鬼 IP、未授權設備
7. **資安內建**：對齊 OWASP Top 10:2025
