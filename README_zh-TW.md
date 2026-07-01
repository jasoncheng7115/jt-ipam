# jt-ipam v0.5.72

[![License](https://img.shields.io/github/license/jasoncheng7115/jt-ipam?color=blue)](LICENSE)
[![Last commit](https://img.shields.io/github/last-commit/jasoncheng7115/jt-ipam)](https://github.com/jasoncheng7115/jt-ipam/commits/main)
[![Stars](https://img.shields.io/github/stars/jasoncheng7115/jt-ipam?style=flat)](https://github.com/jasoncheng7115/jt-ipam/stargazers)
![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-async-009688?logo=fastapi&logoColor=white)
![Vue](https://img.shields.io/badge/Vue-3-42b883?logo=vuedotjs&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)
![OWASP](https://img.shields.io/badge/OWASP-Top%2010%3A2025-000000)

**🌐 [專案介紹網站 / Project site →](https://jasoncheng7115.github.io/jt-ipam/?lang=zh-TW)**

> 可自架、以整合為核心的 IPAM — 操作流程沿襲 phpIPAM 使用者熟悉的風格、全新獨立開發，整合多家 DNS Server、LibreNMS、OPNsense、pfSense、Proxmox VE、Wazuh 與本地 AI。
>
> 作者：Jason Tools Co., Ltd.（節省工具箱）｜授權：Apache-2.0｜English: [README.md](README.md)

---

## 為什麼是 jt-ipam？

phpIPAM 老使用者幾乎零學習成本；以現代技術全新打造（非基於 phpIPAM 程式碼）。深度整合：

- **DNS**：PowerDNS、BIND 9、OPNsense Unbound、Univention UCS、Microsoft Windows DNS（讀取正反解狀態，可選擇性推送記錄）
- **LibreNMS**：裝置同步、ARP / FDB 抓取、上線狀態互補、自動加入監控
- **基礎設施**：Proxmox VE、Wazuh、OPNsense / pfSense（別名 / 規則 / NAT 同步）
- **Graylog**：提供 IP→主機名稱/FQDN 的 DSV 對照表端點，供 Graylog「DSV File from HTTP」資料配接器抓取
- **本地 AI**：LLM Server 自然語言查詢 + 語意搜尋（資料不外送），並提供 MCP server（stdio / Streamable HTTP）；實測搭配 `gemma4:26b` 效果良好

也內建：**瀏覽器內遠端連線管理** —— SSH 終端機，外加 RDP、VNC 桌面與 **BMC 帶外序列主控台**（IPMI SOL）（RDP/VNC/BMC 為 **Beta**），全部在瀏覽器內，連線帳密預設不儲存、可選用**個人加密憑證金庫**（by-user、AES-GCM），物件層級 RBAC、單次 ticket→WebSocket 連線與完整稽核（RDP/VNC 走選用相依，僅在有預編譯 wheel 時才安裝，不影響基礎安裝）、**IP 申請審核流程**（可設多關卡會簽 / 依序關卡，站內 + Email 通知）、**DNS 記錄檢視**（找出沒有對應 IPAM 的記錄）、**掃描代理**（ICMP/ARP/反解/NetBIOS/mDNS/OS 探測）、**憑證集中保管與派送**（商業 / 自簽憑證一次上傳，純 bash 代理依排程自動派送到 nginx/apache/caddy/haproxy/Proxmox VE·PMG·PBS/Zimbra…等服務並重載，私鑰加密保存、到期告警、可手動續簽）、**機房平面圖 + 機櫃 U 位圖**（含半 U / 正背面、SVG/PNG/draw.io 匯出）、**纜線追蹤**（多跳穿透）、IP 異動記錄與失聯 IP 回收、通用表格欄位選擇 + 多格式匯出。

## Graylog 記錄補實（DSV 對照表）

jt-ipam 會**即時**產生一份 IP → 主機名稱 / FQDN 的對照表，讓 Graylog 的「DSV File from HTTP」資料配接器直接抓取，把記錄裡只有 IP 的事件自動補上可讀名稱。

- 在 **管理 → 系統設定 → Graylog DSV** 啟用：設定路徑代稱、輸出格式（CSV / TSV）並產生存取 token
- 端點 `GET /api/v1/lookup/<路徑>?token=<token>`，每次請求即時查詢資料庫產生
- **提供的欄位**：每列兩欄 —— 第 1 欄＝IP（key），第 2 欄＝主機名稱或 FQDN（value）；只輸出「有主機名稱」的 IP
- **資料格式**：UTF-8 純文字。CSV 以逗號分隔、**每欄用雙引號包覆**並依 RFC 4180 跳脫；TSV 以 Tab 分隔（不加引號）。例如：

  ```csv
  "10.1.1.141","log1.example.com"
  "10.1.1.145","mg-host"
  ```

- 在 Graylog 的「DSV File from HTTP」配接器：URL 填上方網址、分隔符依格式選逗號或 Tab、**Key column = 0、Value column = 1**（Graylog 欄位索引從 0 起算）
- token 逐次驗證、可隨時重新產生；設定頁直接提供可複製的完整對照表網址

## BMC 帶外主控台（IPMI SOL，Beta）

直接從伺服器的 IP 開一個鍵盤 + 文字主控台到它的 **BMC**（IPMI 2.0 Serial-over-LAN），免裝各廠 Java／HTML5 KVM。逐 IP 啟用（RBAC 與 SSH 同級），BMC 帳密收進同一個加密金庫，每次連線都留稽核。**非破壞**：只有鍵盤 + 文字畫面，不含電源控制或滑鼠。

SOL 只是把主機的**序列埠**轉播出來，所以主機端要先設好序列主控台，否則畫面一片空白。主機一次性設定：

1. **找出 SOL 對應的埠** —— `dmesg | grep -iE 'ttyS|SPCR'`（例：`SPCR: console: uart,io,0x3f8,115200` → `0x3f8`＝ttyS0、`0x2f8`＝ttyS1）。插錯埠一樣空白。
2. **加入核心 console 參數**（保留 `tty0` 讓實體螢幕不失輸出）：
   - 一般 Linux（GRUB）：在 `/etc/default/grub` 的 `GRUB_CMDLINE_LINUX` 加 `console=tty0 console=ttyS0,115200n8`，再 `update-grub`。
   - Proxmox VE（systemd-boot／ZFS）：把同一段加到 `/etc/kernel/cmdline`，再 `proxmox-boot-tool refresh`。
3. **啟用序列登入**（立即生效、免重開機）：`systemctl enable --now serial-getty@ttyS0`。
4. **（選用）BIOS Console Redirection** —— 指到同一個 COM 埠（115200 8N1），SOL 才看得到 POST／BIOS。
5. **重新開機**讓 `console=` 生效 —— 之後 SOL 就能看到完整開機與 kernel panic。實體螢幕不受影響。

只想馬上能登入？做步驟 3 就夠了。同一份教學也內建在 App 裡，從 BMC 主控台的 **設定教學** 按鈕打開。

**疑難排解（實測常見坑）：**

- **連上但一片空白／按 Enter 沒反應** —— SOL 對應的埠未必是 SPCR 宣告的那個。連著 SOL 時 `echo test > /dev/ttyS0`（與 `/dev/ttyS1`），看哪個出現，那才是真正的 SOL 埠。
- **有畫面但亂碼** —— 序列 baud 沒對齊 SOL。查 `ipmitool -I open sol info 1 | grep 'Bit Rate'`，把 `serial-getty` 設成同一個 baud。
- **方框字／顏色亂（例如 glances）** —— 把序列登入的 `TERM` 設成 `xterm-256color`（serial-getty 預設常是 `vt220`）。
- **畫面範圍很小、四周留黑** —— 序列無法自動傳視窗大小；按主控台的 **符合視窗**（它會送一段 `stty rows/cols` 指令，請在 shell 提示字元按），或自行 `stty rows N cols N`。

## 核心物件

`區段 → 子網路 → IP 位址`，外加 `裝置` / `機櫃` / `地點`、`客戶`（管理單位）、`VLAN` / `VRF`、`NAT`、OPNsense / pfSense 防火牆，以及 IEEE OUI 廠商對照表（每月更新）。

## 權限（RBAC）

物件級權限，涵蓋 **7 種物件類型**（客戶 / 區段 / 子網路 / IP / 裝置 / 機櫃 / 地點）：

- **階層繼承** — 授權上層（如某客戶或區段）自動涵蓋其下所有物件（子網路 → IP；地點 → 機櫃 → 裝置）
- 每種物件類型可用 **「全部」wildcard**
- **5 個內建角色** — 系統管理員、唯讀檢視者、網路操作員、稽核員、部門管理員
- 可見性處處強制：清單端點、全域搜尋、拓樸圖、所有下拉選單，永遠只會出現使用者可見的物件。預設關閉（deny-by-default）。

## 安全（OWASP Top 10:2025）

安全是 day-one 需求，每個模組與 PR 都對齊 **OWASP Top 10:2025**，詳見 [`SECURITY_zh-TW.md`](SECURITY_zh-TW.md)。

- **強制 TLS** — 二擇一：nginx 反代終止 TLS（`BACKEND_TLS_MODE=nginx`），或 uvicorn 直接掛自簽憑證（`BACKEND_TLS_MODE=direct`）
- A01 — deny-by-default RBAC、物件級檢查（如上）
- A02 — argon2id 密碼雜湊；儲存的敏感資料（DNS 憑證 / SNMP / API token）應用層加密
- A03 — 參數化 SQLAlchemy、嚴格 Pydantic v2 驗證、CSP + 輸出跳脫
- A05 — HSTS、CSP、X-Frame-Options、Referrer-Policy
- A07 — TOTP MFA、帳號鎖定、HttpOnly+Secure+SameSite cookie、API token TTL
- A08 — SHA-256 稽核鏈
- A09 — 結構化稽核記錄
- A10 — 所有對外整合走 SSRF 白名單；封鎖 metadata / link-local

## 技術堆疊

| 層 | 選用 |
|------|--------|
| 後端 | Python 3.12 · FastAPI · SQLAlchemy 2.0（async）· asyncpg · Alembic · Pydantic v2 |
| 資料庫 | PostgreSQL 16（原生 `inet`/`cidr`/`macaddr`）+ pgvector |
| 前端 | Vue 3 · TypeScript · Vite · Naive UI · Pinia · vue-i18n |
| 認證 | argon2id · TOTP · 短效 JWT + refresh |
| AI | LLM Server（本地）· pgvector · MCP server |
| 部署 | systemd + nginx + apt 套件 —— **不需 Docker image**（適合虛擬機 / 容器） |

## 安裝（單機 / 虛擬機 / 容器）

> Debian 12 / Ubuntu 22.04+（64 位元）。強制 HTTPS。
>
> **最低需求：** 2 核心 CPU · 4 GB 記憶體 · 20 GB 磁碟。**建議：** 4 核心 · 8 GB 記憶體 · 40 GB 以上磁碟（保留空間給 PostgreSQL 資料庫、GeoIP/OUI 資料與備份成長）。
>
> 選用的本地 LLM（Ollama）**不含**在上述數字內——請另跑於獨立主機，並依所選模型自行配足記憶體 / 顯示記憶體。

```bash
# 前置：最小化系統可能沒有 curl（一行式安裝需要它）
sudo apt-get update && sudo apt-get install -y curl
# 一行完成：自動 clone 到 /opt/jt-ipam 並安裝（不必先手動 git）
curl -fsSL https://raw.githubusercontent.com/jasoncheng7115/jt-ipam/main/scripts/bootstrap.sh | sudo bash
```

腳本會安裝 `postgresql-16` / `python3.12` / `nginx` / `redis`，建立 `jtipam` 系統帳號與 PG 角色，產生金鑰寫入 `/etc/jt-ipam/backend.env`，跑 `alembic upgrade head`，build 前端並啟用 `jt-ipam-backend.service`。

升級：`sudo bash /opt/jt-ipam/scripts/jt-ipam.sh upgrade`（**腳本內含 `git pull`**，直接跑即可），接著備份 → 相依 → alembic → build → 重啟。詳見 [`docs/INSTALL.md`](docs/INSTALL.md)。

> **選用：Docker Compose。** 另有一條次要部署路徑在 [`deploy/docker/`](deploy/docker/)（`./gen-env.sh` 後 `docker compose up -d --build`；之後用 `./update.sh` 升版）。主力且完整支援的仍是 systemd + apt。

### 首次登入與重置管理員密碼

全新安裝時，腳本會**自動建立 `admin` 帳號、產生隨機密碼並在結束時印出一次**（也存到 `/etc/jt-ipam/.admin-initial-password`，僅 root 可讀；該檔位於 `/etc` 之下、不在 web root 內，無法透過 HTTP 連到）。登入後請立即更換，之後即可安全刪除此檔：`sudo rm /etc/jt-ipam/.admin-initial-password`。

重置管理員密碼（或在尚無管理員時建立第一個），在伺服器上執行：

```bash
sudo -u jtipam bash -c 'cd /opt/jt-ipam/backend; set -a; source /etc/jt-ipam/backend.env; set +a; \
  .venv/bin/python -m app.cli.bootstrap create-admin \
    --username admin --email admin@example.com --password-stdin --force-update'
# 接著在 stdin 輸入新密碼（≥ 12 字元）
```

不加 `--force-update` 則是新建管理員，而非重置既有帳號。

## TLS / HTTPS

強制 HTTPS，兩種模式擇一（`/etc/jt-ipam/backend.env` 的 `BACKEND_TLS_MODE`）：

**模式 A — nginx 反代（預設、建議）** `BACKEND_TLS_MODE=nginx`
nginx 終止 TLS、反代到本機 uvicorn(127.0.0.1:8000)。換成正式憑證：

```bash
# 把正式憑證/私鑰覆蓋到固定路徑後 reload（路徑已寫死在 nginx 設定）
cp fullchain.pem /etc/jt-ipam/tls/server.crt
cp privkey.pem   /etc/jt-ipam/tls/server.key
chmod 600 /etc/jt-ipam/tls/server.key
nginx -t && systemctl reload nginx
```

Let's Encrypt：把 `ssl_certificate` 指到 `/etc/letsencrypt/live/<FQDN>/fullchain.pem`、`ssl_certificate_key` 指到 `…/privkey.pem`，續期後 `systemctl reload nginx`。自架 nginx 反代最小設定：

```nginx
server {
    listen 443 ssl;
    server_name ipam.example.com;
    ssl_certificate     /etc/jt-ipam/tls/server.crt;
    ssl_certificate_key /etc/jt-ipam/tls/server.key;
    root /opt/jt-ipam/frontend/dist;
    index index.html;
    location /api/ { proxy_pass http://127.0.0.1:8000; proxy_set_header Host $host; proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for; proxy_set_header X-Forwarded-Proto $scheme; }
    location / { try_files $uri $uri/ /index.html; }
}
```

**模式 B — uvicorn 直接自簽** `BACKEND_TLS_MODE=direct`
uvicorn 自己掛 `--ssl-*`，安裝時 `scripts/generate-self-signed-cert.sh` 會產自簽憑證。換憑證：把正式(或自管)憑證覆蓋同一組路徑後重啟服務：

```bash
cp fullchain.pem /etc/jt-ipam/tls/server.crt
cp privkey.pem   /etc/jt-ipam/tls/server.key
chmod 600 /etc/jt-ipam/tls/server.key
systemctl restart jt-ipam-backend
```

> 兩種模式憑證路徑相同(`/etc/jt-ipam/tls/server.{crt,key}`)，差別只在「誰終止 TLS」：模式 A reload nginx、模式 B 重啟 backend。

**模式 C — 前面已有外部反向代理負責 SSL**（你自己有一台 nginx / LB 終止 TLS）
本機 nginx 只做 HTTP，套用外部代理模式範本：

```bash
sudo cp deploy/nginx/jt-ipam-external-proxy.conf         /etc/nginx/sites-available/jt-ipam
sudo cp deploy/nginx/jt-ipam-external-proxy-snippet.conf /etc/nginx/snippets/jt-ipam-proxy.conf
sudo nginx -t && sudo systemctl reload nginx
```

> ⚠️ **必要——公開邊緣的安全標頭。** 真正**為使用者終結 TLS 的那台代理**必須送出安全標頭（HSTS、CSP `frame-src 'self'`、
> X-Frame-Options、nosniff、Referrer-Policy、Permissions-Policy、COOP、CORP）與 `server_tokens off`。這些標頭**不會**
> 自動跨多一跳存活，所以如果你的邊緣機是上面這台以外的**另一台**，**那台邊緣機也要設**（內建範本已含；非 nginx 的 LB
> 請照樣複製一份）。請從真正的對外網址驗證：
> `curl -skI https://你的網域/ | grep -iE 'strict-transport|content-security|x-frame|cross-origin|^server'`
> ——每個標頭應**剛好出現一次**、`Server: nginx`（無版本）。

外部代理本身**不會**影響 OIDC / M365(Entra ID) 登入，但有三個一定要對，否則登入會被導到 `ipam.example.com` 或卡在登入頁：

1. **`/etc/jt-ipam/backend.env`** 的 `APP_PUBLIC_URL` / `API_PUBLIC_URL` / `CORS_ORIGINS` 都設成對外網域（`https://ipam.your-domain.com`），不要留預設 `ipam.example.com`（後端簽發 token、OIDC 回呼網址都看它）→ 改完 `systemctl restart jt-ipam-backend`。
2. **外部 nginx 轉發時要送** `proxy_set_header X-Forwarded-Proto $scheme;`（=https）與 `Host $host;`；本機範本會把它透傳給後端（避免後端誤判成 http、Secure cookie 設不起來）。
3. **OIDC Redirect URI** 在 IdP 與 jt-ipam UI（系統設定 → SSO → OIDC）都填 `https://ipam.your-domain.com/api/v1/auth/oidc/callback`。注意 **UI 存過的 DB 值優先於 .env**，改 .env 後要在 UI 再存一次。
> HSTS 由持有憑證的外部 nginx 送出，本機（HTTP）不送。

## 專案結構

```
jt-ipam/
├── docs/              # 規格、安全、資料模型、API 參考
├── backend/           # FastAPI app
│   └── app/
│       ├── core/      # config / db / audit / safe_http / encrypted_secret
│       ├── models/    # SQLAlchemy 2.0
│       ├── schemas/   # Pydantic v2
│       ├── api/v1/    # REST API
│       ├── services/  # 商業邏輯（ai / oui / opnsense / topology / search / permission）
│       ├── mcp/       # MCP server + tools（給 LLM 用戶端）
│       └── plugins/   # 外掛系統
├── frontend/          # Vue 3 + TS
│   └── src/{views,components,composables,api,stores,i18n,router}
└── scripts/           # jt-ipam.sh（install/upgrade/uninstall）、ci.sh、oui_refresh.py
```

## 藍圖進度

- **Phase 1（完成）** — phpIPAM 對等功能 + 改良（區段/子網路/IP/VLAN/VRF/NAT/裝置/機櫃/地點/IP 申請、TOTP/API-Token/RBAC、phpIPAM 匯入、CSV/RIPE/TWNIC、視覺化子網路格、強制 TLS）
- **Phase 2（完成）** — 多家 DNS + 深度 LibreNMS 整合（裝置/ARP/FDB/實際狀態）+ 異常偵測 + SHA-256 稽核鏈 + pgvector AI 語意搜尋
- **Phase 3（完成）** — 租戶/聯絡人/佈線/電力/VPN/虛擬化 + Proxmox VE 同步 + Cytoscape 拓樸 + OIDC/SAML SSO + OPNsense / pfSense 防火牆同步 + Wazuh agent 盤點
- **Phase 4（完成、已縮減範圍）** — MCP server + 本地 LLM 自然語言（LLM Server）+ 外掛機制

## 授權

Apache-2.0｜商業支援請聯繫 Jason Tools。
