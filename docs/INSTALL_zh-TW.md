# jt-ipam 安裝與運維 SOP

> English: [INSTALL.md](INSTALL.md) · 日本語：[INSTALL_ja.md](INSTALL_ja.md)

針對 **Proxmox LXC、裸機、虛擬機**，作業系統為 Debian 12/13 或 Ubuntu 22.04/24.04/26.04（x86_64，見[支援的發行版本](#支援的發行版本)）。**主力且建議**的安裝方式是
**systemd + apt** 直裝（不使用 Docker）。另有 Docker Compose 路徑，但**屬選用 / 次要、並非優先模式**，見下方 §2.8。

> 安全為 day-one 需求：所有環境強制 HTTPS；憑證可走 nginx 反代或
> uvicorn 直接吃自簽。SSL 沒設好 backend **不會啟動**（A02）。

---

## 1. 系統需求

| 項目 | 最低 | 建議 | 備註 |
|---|---|---|---|
| OS | Debian 12/13、Ubuntu 22.04/24.04/26.04（x86_64） | **Ubuntu 24.04 LTS** | 只支援這些，見下方；24.04 內建 Python 3.12 + PG 16，省事 |
| CPU | 2 vCPU | 4 vCPU | 升級時要 build 前端（約 1 分鐘）；RDP 主控台（guacd）與本機掃描代理（nmap）吃 CPU。embedding 是 LLM 伺服器算的，不在這台 |
| RAM | 4 GB | 8 GB | 4 個 worker 時平常約用 1.8 GB；升級時 build 前端峰值約 1.6 GB。4 GB 或 2 核的機器後端只開 2 個 worker，記憶體不夠時 build 期間會暫停後端（加 2 GB swap 就不會）。每條 RDP 主控台約佔數百 MB。LLM 伺服器裝在同一台要再加 8 GB 以上 |
| Disk | 20 GB | 50 GB | 安裝本身約 2 GB（Python 套件、node_modules、快取）加作業系統；資料庫、稽核記錄、IP 異動記錄與備份會隨網路規模成長；journald 的日誌有上限 |
| Python | 3.11 | 3.12 | 24.04 預設就是 3.12  |
| PostgreSQL | 16 + pgvector | 無 | 22.04 需 PGDG repo（腳本會自動加）|
| Redis | 7 | 無 | 24.04 預設 7.0.15  |
| Node | 22 LTS | 無 | 只用來建置前端；`jt-ipam.sh` 安裝時會裝 NodeSource 22（只有 Ubuntu 26.04 內建 22），升級時會把較舊的 Node 換成 22 |
| guacd | jt-ipam 為該 OS 編的版本 | 無 | **必要**：RDP/VNC 主控台的連線引擎，`jt-ipam.sh` 會裝（見下方 guacd 一節）；舊引擎 aardwolf 改為選用 |
| Recog | 最新發佈版 | 無 | **選用**：IP 探測用來認出設備與 OS 版本的指紋庫；`jt-ipam.sh` 會下載、每週檢查新版（見下方 Recog 一節） |
| 中文字型 | `fonts-noto-cjk` | 無 | **PDF 報告需要**：後端產生 PDF 時內嵌的中文/日文字型（下載約 60 MB）。`jt-ipam.sh` 安裝與升級時都會裝；升級時連不到 apt 鏡像只會警告，PDF 匯出會說明缺字型（DOCX/ODT 照常可用），之後用 `sudo apt install fonts-noto-cjk` 補上 |
| SNMP 用戶端工具 | `snmp`（net-snmp） | 無 | **選用**：本機掃描代理做 SNMP 收集（收集者「伺服器」）要用的 `snmpget`/`snmpbulkwalk`。`jt-ipam.sh` 安裝與升級時都會裝（升級裝不起來只會警告）；遠端掃描代理由它的安裝腳本安裝（apt 是 `snmp`、dnf 是 `net-snmp-utils`、apk 是 `net-snmp-tools`）。沒裝時那台代理就只是沒有 SNMP 能力 |

### 支援的發行版本

| 發行版 | 版本 | 架構 |
|---|---|---|
| Debian | 12（bookworm）、13（trixie） | x86_64（amd64） |
| Ubuntu | 22.04 LTS、24.04 LTS、26.04 LTS | x86_64（amd64） |

為什麼剛好是這些：RDP 與 VNC 主控台的引擎 guacd 是**必要元件**，而各發行版都沒有可用的 guacd（Debian 已移除，
Ubuntu 只有帶著可遠端執行程式碼漏洞的 1.3.0）。jt-ipam 替上面每個版本各編一份；其他版本 `install` 會停下來，
`upgrade` 會警告。安裝腳本依 `/etc/os-release` 的 `ID` ＋ `VERSION_ID` 找對應的預編檔，所以：

- 上列版本的 **Proxmox LXC 範本、虛擬機、裸機**都可以，Proxmox VE 主機本身也可以（它就是 Debian）。
- **衍生發行版**（Linux Mint、Pop!_OS、Zorin…）、**其他版本**（例如 Debian 11、Ubuntu 20.04、沒有預編檔的非 LTS 版）
  與 **ARM 機器**（樹莓派、Ampere）不支援。
- **作業系統出新版時**，每次發版都會檢查（`scripts/guacd/check-new-os.sh`），編好、驗證過才加入。
  jt-ipam 主機在新版出現在這張表之前，**不要**升級作業系統的大版本。
- 進階：其他 Debian/Ubuntu 版本可以用 `scripts/guacd/build.sh`（需要 Docker）自己編，再用
  `jt-ipam.sh install --guacd-tarball <檔案>` 安裝；這種組合我們沒有測試。

**虛擬化備註**：在 Proxmox VM / LXC 上跑時，剛開機 / 重開後 1-2 分鐘內 load avg 可能飆高（hypervisor 上其他 VM 在搶 CPU，看 `mpstat` 的 `%steal`）；這不是 VM 本身忙，可以直接跑 install。

---

## 2. 一鍵安裝

### 2.1 預備：apt 系統更新 + reboot（強烈建議）

新機器先把 OS 全更新一次再裝，避免新舊核心 + libc 不一致：

```bash
sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get -y -qq upgrade
sudo systemctl reboot
```

### 2.2 一鍵安裝

最快：一鍵 bootstrap（自動 clone 到 /opt/jt-ipam 後執行統一部署腳本，可附帶安裝參數）：

```bash
# 前置：最小化系統可能沒有 curl（一行式安裝需要它）
sudo apt-get update && sudo apt-get install -y curl
curl -fsSL https://raw.githubusercontent.com/jasoncheng7115/jt-ipam/main/scripts/bootstrap.sh \
  | sudo bash -s -- --tls-mode nginx --public-fqdn ipam.example.com
```

或手動 clone 後跑統一部署腳本 `scripts/jt-ipam.sh`：

```bash
git clone https://github.com/jasoncheng7115/jt-ipam.git /opt/jt-ipam
cd /opt/jt-ipam

# 三種 TLS 模式擇一：
#
#   nginx         ：nginx 終結 HTTPS，後端 loopback；缺憑證時自動產自簽 bootstrap
#   self-signed   ：uvicorn direct 內建自簽（不裝 nginx；最快上線）
#   direct        ：uvicorn direct，憑證自備（缺則 fallback 產自簽）

# (A) nginx + 暫用自簽（之後 cp 正式憑證即可），推薦生產環境
sudo ./scripts/jt-ipam.sh install --tls-mode nginx --public-fqdn ipam.example.com

# (B) uvicorn direct 自簽（內網/開發環境最快）
sudo ./scripts/jt-ipam.sh install --tls-mode self-signed --public-fqdn ipam.local
```

> `scripts/install-debian.sh` 仍保留為相容 shim（會轉呼叫 `jt-ipam.sh install`），舊指令不會壞。

腳本會：

1. 安裝 PostgreSQL 16 + pgvector + Redis 7（Ubuntu 22.04 自動加 PGDG repo；Ubuntu 24.04 直接走官方源）
2. 建立 `jtipam` 系統使用者、`/opt/jt-ipam/backend/.venv`、安裝 Python deps
3. 建立 DB role/database `jt_ipam`，套 alembic migrations
4. 產生 `SECRET_KEY` / `ENCRYPTION_KEY` / `AUDIT_CHAIN_GENESIS` 寫入 `/etc/jt-ipam/backend.env`（0640）
5. 在 `/etc/jt-ipam/tls/` 產自簽憑證 ECDSA P-384 / 5 年；SAN 自動含 `localhost / FQDN / 短 hostname / 127.0.0.1 / ::1 / 主機 IP`
6. pnpm install + 前端 vite build
7. 安裝 `jt-ipam-backend.service` + `jt-ipam-sync.timer`（每 5 分鐘）+ `jt-ipam-ai-audit.timer`（每 5 分鐘檢查排程 AI 巡檢是否到時間；沒打開就什麼都不做）+ `jt-ipam-backup.timer`（每天 03:30）
8. nginx 模式才裝 nginx site 並 reload

### 2.3 Bootstrap 第一個 admin

```bash
# 隨機產一個強密碼，從 stdin 讀（不留 shell history）
ADMIN_PW=$(openssl rand -base64 24)
sudo -u jtipam env $(grep -v '^#' /etc/jt-ipam/backend.env | xargs) \
    /opt/jt-ipam/backend/.venv/bin/python -m app.cli.bootstrap create-admin \
    --username admin --email admin@your.domain --password-stdin <<<"$ADMIN_PW"
echo "ADMIN_PASSWORD=$ADMIN_PW"   # 自己保管好
```

開啟瀏覽器到 `https://<your-fqdn>/` 即可登入。瀏覽器會警告自簽憑證，按進階繼續即可。

### 2.4 端對端 sanity check

```bash
# healthz
curl -kfsS https://127.0.0.1/healthz                       # 應回 ok

# login + chain verify（驗 A08）
TOKEN=$(curl -kfsS -X POST https://127.0.0.1/api/v1/auth/login \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"admin\",\"password\":\"$ADMIN_PW\"}" | jq -r .access_token)
curl -kfsS -X POST https://127.0.0.1/api/v1/audit/verify \
    -H "Authorization: Bearer $TOKEN" | jq .
# 應該看到 {"ok": true, "broken_at_id": null, "checked": N}

# systemd 安全分數
systemd-analyze security jt-ipam-backend | tail -3
# 目標：≤ 3.5；目前實測 1.3
```

### 2.5 換成正式 TLS 憑證（nginx 模式）

安裝時用自簽撐起來，正式憑證到手後**只要 cp 過去 reload nginx**：

```bash
# 把廠商給的 cert + key cp 到固定位置（保留原本權限 root:jtipam）
sudo install -m 0644 -o root -g jtipam /path/to/your-cert.pem  /etc/jt-ipam/tls/server.crt
sudo install -m 0640 -o root -g jtipam /path/to/your-key.pem   /etc/jt-ipam/tls/server.key

# 如果你拿到的是 fullchain（含中繼憑證）+ key，建議用 fullchain
sudo install -m 0644 -o root -g jtipam /path/to/fullchain.pem  /etc/jt-ipam/tls/server.crt

# 驗證 + reload
sudo nginx -t && sudo systemctl reload nginx

# 確認新憑證生效
openssl s_client -connect ipam.example.com:443 -servername ipam.example.com </dev/null 2>/dev/null \
    | openssl x509 -noout -issuer -subject -dates
```

之後要再換，重複上面三步即可，不需重跑 install。

走 Let's Encrypt 路線：

```bash
sudo apt install -y certbot python3-certbot-nginx
# certbot 會自動改 ssl_certificate 指到 /etc/letsencrypt/live/...
sudo certbot --nginx -d ipam.example.com
```

### 2.6 換成正式 TLS 憑證（uvicorn direct / self-signed 模式）

`BACKEND_TLS_MODE=direct`(或 `self-signed`)時是 **uvicorn 自己掛 TLS**，沒有 nginx。憑證路徑跟 nginx 模式相同，差別只在換完要**重啟 backend**(不是 reload nginx)：

```bash
# cp 正式(或自管)憑證 + key 到固定位置
sudo install -m 0644 -o root -g jtipam /path/to/fullchain.pem /etc/jt-ipam/tls/server.crt
sudo install -m 0640 -o root -g jtipam /path/to/your-key.pem  /etc/jt-ipam/tls/server.key

# 重啟服務套用（uvicorn 啟動時讀 --ssl-certfile/--ssl-keyfile）
sudo systemctl restart jt-ipam-backend

# 確認新憑證生效（埠號見下方「監聽在哪個埠」）
openssl s_client -connect ipam.example.com:8443 -servername ipam.example.com </dev/null 2>/dev/null \
    | openssl x509 -noout -issuer -subject -dates
```

> 想自己重新產自簽憑證：`sudo bash /opt/jt-ipam/scripts/generate-self-signed-cert.sh` 後 `systemctl restart jt-ipam-backend`。

#### 監聽在哪個埠？（**預設是 8443，不是 443**）

`self-signed` / `direct` 模式由 uvicorn 自己掛 TLS，**預設監聽 8443**，網址是
`https://<你的網域>:8443/`。裝完發現「443 沒有起來」多半就是這個原因，不是安裝失敗。
實際值寫在 `/etc/jt-ipam/backend.env` 的 `BACKEND_BIND_PORT`。

想直接用 443（不加 nginx）：

```bash
# 安裝時指定（推薦：安裝腳本會一併授予綁定特權埠所需的 capability）
sudo ./scripts/jt-ipam.sh install --tls-mode self-signed --public-fqdn ipam.example.com --bind-port 443

# 已經裝好了才要改：
sudo sed -i 's/^BACKEND_BIND_PORT=.*/BACKEND_BIND_PORT=443/' /etc/jt-ipam/backend.env
sudo sed -i 's#^APP_PUBLIC_URL=.*#APP_PUBLIC_URL=https://ipam.example.com#' /etc/jt-ipam/backend.env
sudo install -d /etc/systemd/system/jt-ipam-backend.service.d
sudo tee /etc/systemd/system/jt-ipam-backend.service.d/20-bind-privileged-port.conf >/dev/null <<'EOF'
[Service]
AmbientCapabilities=CAP_NET_RAW CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_RAW CAP_NET_BIND_SERVICE
EOF
sudo systemctl daemon-reload && sudo systemctl restart jt-ipam-backend
```

> 服務不是以 root 執行，所以綁 1024 以下的埠**一定要** `CAP_NET_BIND_SERVICE`；少了它服務會起來後立刻死掉，
> 錯誤訊息是 `Permission denied`，看起來很像憑證問題，其實是埠的問題。

#### 自簽模式要不要另外裝 nginx？

**不需要。** `self-signed` / `direct` 模式已經是完整可用的 HTTPS 服務，不必裝 web server。
會想加 nginx 通常是這三種情形：要用 443 又不想給服務特權、要與同一台上的其他站共用 443、
或想要反向代理層的嚴格安全標頭（見 2.7）。要加的話**改走 nginx 模式**，不要在自簽的後端前面再包一層 TLS：

```bash
sudo apt install -y nginx

# 1) 後端改回 loopback + 明文（TLS 交給 nginx）
sudo sed -i 's/^BACKEND_TLS_MODE=.*/BACKEND_TLS_MODE=nginx/' /etc/jt-ipam/backend.env
sudo sed -i 's/^BACKEND_BIND_PORT=.*/BACKEND_BIND_PORT=8000/' /etc/jt-ipam/backend.env
sudo sed -i 's#^APP_PUBLIC_URL=.*#APP_PUBLIC_URL=https://ipam.example.com#' /etc/jt-ipam/backend.env

# 2) 裝 nginx site（把範本的 server_name 換成你的網域）
sudo install -d -m 0755 /etc/nginx/snippets
sudo install -m 0644 /opt/jt-ipam/deploy/nginx/jt-ipam-proxy.conf /etc/nginx/snippets/jt-ipam-proxy.conf
sudo sed 's/ipam\.example\.com/ipam.example.com/g' /opt/jt-ipam/deploy/nginx/jt-ipam.conf \
    | sudo tee /etc/nginx/sites-available/jt-ipam >/dev/null
sudo ln -sf /etc/nginx/sites-available/jt-ipam /etc/nginx/sites-enabled/jt-ipam
sudo rm -f /etc/nginx/sites-enabled/default

# 3) 憑證放在 nginx 讀得到的位置（沿用原本的自簽也可以）
#    範本讀的是 /etc/jt-ipam/tls/server.crt / server.key

sudo nginx -t && sudo systemctl restart jt-ipam-backend && sudo systemctl reload nginx
```

> nginx 範本已內含 WebSocket 升級設定（SSH/SFTP/RDP/VNC/noVNC/BMC 主控台需要）。
> **自己寫 nginx 設定的話，這段一定要照抄**：少了升級標頭，主控台會連不上，
> 而畫面上只會看到一個沒頭沒尾的 404。

### 2.7 正式環境標準：高安全性 nginx 反向代理

任何對外或正式環境，**標準做法都是讓 jt-ipam 跑在內建的高安全性 nginx 反向代理之後**（`--tls-mode nginx`，
參考設定 `deploy/nginx/jt-ipam.conf`）。由 nginx 終結 TLS、後端只綁在 loopback，代理層強制套上嚴格的安全基線，
應用伺服器絕不直接對外曝露：

- **TLS**：僅 TLS 1.2/1.3、現代化加密套件、OCSP stapling、關閉 session tickets。
- **HSTS**：`max-age` 2 年 + `includeSubDomains` + `preload`。
- **CSP**：`default-src 'self'`、`script-src 'self'`、`connect-src 'self'`、`frame-src 'self'`、
  `frame-ancestors 'none'`、`base-uri 'self'`、`form-action 'self'`；不含任何第三方 script/frame 來源。
- **標頭**：`X-Content-Type-Options: nosniff`、`X-Frame-Options: DENY`、`Referrer-Policy`、
  `Permissions-Policy`（關閉定位/麥克風/相機/付款/USB）、`Cross-Origin-Opener-Policy` 與
  `Cross-Origin-Resource-Policy: same-origin`。
- **不洩漏版本指紋**：`server_tokens off`，並隱藏上游（uvicorn）的 `Server`/`X-Powered-By` 標頭。
- 後端只監聽 `127.0.0.1`，nginx 是唯一對外監聽者。

> **請勿**把 uvicorn 直接對外。`--tls-mode self-signed`/`direct` 只適用於內部/開發。

> ### ⚠️ 自己在前面再擋一層反向代理時（Mode C）＝必要設定
> 上述安全標頭是由「**在公開邊緣終結 TLS 的那台 nginx**」送出的。如果你用另一台反向代理（例如公司邊緣 nginx/
> 負載平衡器）擋在 jt-ipam 前面，**那台代理必須自己也設這些安全標頭**：它們不會自動跨多一跳存活，否則對外網站就會
> **完全沒有** CSP/HSTS/Permissions-Policy。這是部署的**必要**步驟，不是選用。
>
> 把內建的硬化外部代理設定套到那台邊緣機：
> [`deploy/nginx/jt-ipam-external-proxy.conf`](https://github.com/jasoncheng7115/jt-ipam/blob/main/deploy/nginx/jt-ipam-external-proxy.conf)
> ＋ [`jt-ipam-external-proxy-snippet.conf`](https://github.com/jasoncheng7115/jt-ipam/blob/main/deploy/nginx/jt-ipam-external-proxy-snippet.conf)
> （含 HSTS preload、收緊的 CSP、X-Frame-Options DENY、nosniff、Referrer-Policy、Permissions-Policy、COOP＋CORP、
> `server_tokens off`，並用 `proxy_hide_header` 擋掉上游重複的標頭，讓每個標頭只出現一次）。

**請從「使用者實際連的對外網址」驗證**（也就是要穿過你的邊緣代理，而不只是本機）：

```bash
curl -skI https://ipam.example.com/ \
  | grep -iE 'strict-transport|content-security|x-frame|x-content|referrer|permissions|cross-origin|^server'
# 應看到：HSTS、Content-Security-Policy（frame-src 'self'）、X-Frame-Options、X-Content-Type-Options、
# Referrer-Policy、Permissions-Policy、COOP、CORP：每個各一份，且 Server: nginx（無版本）。
```

**主控台與 SFTP 經過你的邊緣代理。** 每個主控台（包含 SFTP）都是一條長時間的 WebSocket，路徑是
`/api/v1/addresses/<id>/(ssh|sftp|rdp|vnc|novnc|bmc|rustdesk)/ws`。SFTP 的檔案在這條 WebSocket 上以 256 KB 為單位傳送，
所以 nginx 的 `client_max_body_size` 這類 HTTP 內容大小上限**管不到**檔案大小。會讓大檔傳輸失敗的是路徑上：

- 沒有替這組路徑轉發 WebSocket 升級（主控台會完全連不上）；
- 限制單一 WebSocket 訊息大小（有些 WAF 會；請至少放寬到 1 MB。相容 RustDesk 的網頁連線會整張傳送畫面的關鍵影格，請放寬到 16 MB）；
- 限制單一 WebSocket 連線的傳輸量或時間，或閒置不到 30 秒就切斷（jt-ipam 每 20 秒送一次保活）。

不必自己去讀各層的設定：在**管理 → 系統設定**的「SFTP 單檔上下傳上限」，只要把上限調高到預設值以上，就會由你的瀏覽器
實際傳一次，經過每一層（邊緣代理、IPAM 的 nginx、後端），並講出是哪一種限制擋住；也會依量到的速度，估算傳一個上限大小的檔案要多久。

### 2.8 選用：Docker Compose（非本專案優先使用模式）

> ⚠️ **Docker Compose 是次要 / 選用的部署路徑，並非本專案優先或主力的部署模式。** 受支援且建議的安裝方式是
> **systemd + apt**（上面各節）。Compose 適合快速試用或本來就以容器為主的環境；systemd 路徑測試最完整。

檔案在 [`deploy/docker/`](https://github.com/jasoncheng7115/jt-ipam/tree/main/deploy/docker)。一組 compose 會起：
`postgres`（pgvector）、`redis`、`backend`（FastAPI/uvicorn）、`sync`（背景同步迴圈，取代 systemd timer）、
`web`（nginx：服務前端 + 反代 `/api`，首次啟動自動產自簽 HTTPS 憑證）。

前置需求：**git** 與 **Docker Engine（含 `docker compose` v2 外掛）**。建議用官方腳本 `get.docker.com` 安裝
（同時裝好引擎與 compose 外掛）；**不要用 `apt install docker.io`**，那個版本沒有 `docker compose` 子指令。
非 Debian 系發行版請用各自的套件管理員裝 git。

```bash
# 前置：先裝 curl/git，再裝 Docker Engine（含 compose 外掛）
sudo apt-get update && sudo apt-get install -y curl git
curl -fsSL https://get.docker.com | sudo sh
docker compose version         # 應印出 v2.x

# 先 git clone 取得專案：gen-env.sh / docker-compose.yml 都在 repo 的 deploy/docker/ 內
git clone https://github.com/jasoncheng7115/jt-ipam.git
cd jt-ipam/deploy/docker
./gen-env.sh                   # 產生 .env 並填入隨機密鑰（只需一次）
docker compose up -d --build   # 建置映像並啟動
# 開瀏覽器到 https://localhost（首次自簽憑證，瀏覽器跳警告自行信任）
```

- **第一個管理員：** `gen-env.sh` 會自動產生一組隨機 `admin` 密碼（印在它的輸出、並存進 `.env` 的
  `JT_IPAM_ADMIN_PASSWORD`，檔案 0600），backend 首次啟動就用它建立 admin，登入後請立即更換。想自己指定就在
  第一次 `up` 前改 `.env` 的 `JT_IPAM_ADMIN_PASSWORD`；或留空、之後用
  `docker compose exec backend python -m app.cli.bootstrap create-admin --username admin --email admin@example.com --password-stdin` 建立。
- **正式憑證：** 把 `server.crt` / `server.key` 放到 `deploy/docker/certs/` 即蓋過自簽。
- **連接埠 / 網域：** 改 `.env` 的 `HTTP_PORT` / `HTTPS_PORT` / `JT_IPAM_SERVER_NAME`，並把 `APP_PUBLIC_URL` / `CORS_ORIGINS` 一起改成相符的 `https://...`。

**升版只要一個指令：**

```bash
./update.sh    # git pull  →  docker compose build  →  docker compose up -d
```

backend 容器啟動時會**自動**跑資料庫遷移（entrypoint 執行 `alembic upgrade head`），不需另外手動跑 migration。

**內網/無外網主機**（外網 build、內網 run）：在有外網的主機把映像 build 好、帶進內網載入；安裝與升級同一套流程。

```bash
# 在有外網的主機：先取得原始碼，再 build + 打包
git clone https://github.com/jasoncheng7115/jt-ipam.git
cd jt-ipam/deploy/docker
./offline-export.sh                    # → jt-ipam-images-<sha>.tar.gz（app + postgres/redis 映像）
                                       #（之後要升級：先 git pull 取新版，再重跑 offline-export.sh）

# 把壓縮檔 + 整個 jt-ipam repo 資料夾複製到內網主機，在 deploy/docker/ 下執行：
./gen-env.sh                           # 僅首次安裝（需 openssl，免外網）
./offline-import.sh jt-ipam-images-<sha>.tar.gz   # docker load + up -d --no-build --pull never
```

要升級內網主機：在外網主機 `git pull` 後重跑 `./offline-export.sh`，把新的壓縮檔複製過去，再跑一次
`./offline-import.sh <新壓縮檔>`（`.env` 不動）。

> Compose 版未內含：Graylog DSV 的明文 8088 埠、以及 GeoIP / OUI 排程更新。完整說明見
> [`deploy/docker/README.md`](https://github.com/jasoncheng7115/jt-ipam/blob/main/deploy/docker/README_zh-TW.md)。

---

## 3. 環境變數

主要設定檔：`/etc/jt-ipam/backend.env`（root:jtipam 0640）

| 變數 | 必填 | 說明 |
|---|---|---|
| `SECRET_KEY` |  | JWT 簽章；安裝腳本自動產 64-byte hex |
| `ENCRYPTION_KEY` |  | AES-256-GCM key（DNS/SNMP/API 憑證加密）|
| `AUDIT_CHAIN_GENESIS` |  | SHA-256 鏈起始；**永不可改**（A08）|
| `POSTGRES_*` |  | DB 連線 |
| `REDIS_PASSWORD` |  | rate limiter / cache |
| `BACKEND_TLS_MODE` |  | `nginx` 或 `direct` |
| `APP_PUBLIC_URL` |  | 前端 base URL |
| `API_PUBLIC_URL` |  | OIDC/SAML callback 用 |
| `CORS_ORIGINS` |  | 多個用逗號分隔 |
| `OUTBOUND_ALLOW_CIDRS` | 無 | safe_http SSRF allowlist；空白 = 只允公網 |
| `FDB_CURRENT_MAX_AGE_HOURS` | 24 | FDB 條目多久沒被看到就只算歷史（不再參與「目前接在哪個埠」的判斷）|
| `FDB_RETENTION_DAYS` | 365 | FDB 歷史保留天數；0＝永久。這張表的價值是「以前接在哪個埠」，所以預設比 ARP 長很多 |
| `ARP_RETENTION_DAYS` | 30 | ARP 條目保留天數；0＝停用回收 |
| `OIDC_*` | 無 | 啟用 OIDC SSO |
| `SAML_*` | 無 | 啟用 SAML SSO |
| `LDAP_*` | 無 | LDAP/AD 認證 |
| `OLLAMA_ENABLED` | 無 | 開啟 AI 語意搜尋 + chat |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | LLM Server 位址 |
| `OLLAMA_CHAT_MODEL` | `gemma4:26b` | 對話模型 |
| `OLLAMA_EMBEDDING_MODEL` | `granite-embedding:278m` | 嵌入模型，**維度必須等於 `EMBEDDING_DIM`** |
| `EMBEDDING_DIM` | `768` | 資料庫 `vector(N)` 欄位的維度；改這個要一併改 migration |

完整列表見 `app/core/config.py` Settings class。

---

## 4. 整合設定（裝完後）

所有整合都在管理介面的「管理 → 外部系統整合」裡新增（例如 `/firewall`、`/wazuh`、`/librenms`、`/dns`），已設定的整合在選單名稱後面會有標示。
新增後預設每 5 分鐘由 `jt-ipam-sync.timer` 自動同步。

### OPNsense 防火牆

1. OPNsense → System → Access → Users → 加 service user → 拿 API key/secret
2. jt-ipam → 防火牆 → 新增 → 填 `https://opnsense:443`、key、secret
3. 加 alias mapping（selector JSON 例：`{"type":"section","section_id":"<uuid>"}`）

### pfSense 防火牆

pfSense 無內建 REST API，需安裝第三方 **pfSense-pkg-RESTAPI** 套件（pfrest.org）：

1. pfSense → System → Package Manager → 安裝 **pfSense-pkg-RESTAPI**
2. System → REST API → Settings → 把 **API Key** 加進認證方式（auth methods；預設只有 BasicAuth）
3. System → REST API → Keys → 建立一把 key
4. jt-ipam → 整合 pfSense → 新增 → 填 `https://pfsense`、API key；憑證自簽請關閉 Verify TLS
5. 同步 DHCP / ARP / 別名 / 規則 / NAT（base path `/api/v2`、`X-API-Key` 認證）。pfSense 有獨立設定頁（不與 OPNsense 共用）

### Wazuh

1. Wazuh manager → API user（預設 `wazuh-wui` 或自建）
2. jt-ipam → Wazuh → 新增 → 填 `https://wazuh:55000`、user、password
3. 點同步；之後 missing-agent 頁會自動列出沒裝 agent 的 IP

### LibreNMS

1. LibreNMS → API → 產 token
2. jt-ipam → LibreNMS → 新增 → 填 URL、token

### OIDC（Keycloak/Azure AD/Google）

直接在 `/etc/jt-ipam/backend.env` 加：

```
OIDC_ENABLED=true
OIDC_ISSUER=https://accounts.google.com
OIDC_CLIENT_ID=xxx
OIDC_CLIENT_SECRET=yyy
OIDC_REDIRECT_URI=https://ipam.example.com/api/v1/auth/oidc/callback
OIDC_ADMIN_GROUPS=jt-ipam-admins
```

`systemctl restart jt-ipam-backend`。Login 頁會出現「OIDC 單一登入」按鈕。

### SAML（AD FS / Shibboleth）

```
SAML_ENABLED=true
SAML_IDP_METADATA_URL=https://idp.example.com/FederationMetadata.xml
SAML_ADMIN_GROUPS=jt-ipam-admins
```

或離線環境用 `SAML_IDP_METADATA_XML="<EntityDescriptor>...</EntityDescriptor>"`。
重啟後 IdP 註冊 SP metadata：`curl https://ipam.example.com/api/v1/auth/saml/metadata`。

---

## 5. 備份與還原

### Recog 指紋庫（選用）

IP 探測會把主機自己回的文字（SSH banner、HTTP `Server` 標頭、網頁標題、TLS 憑證、SMB 回報的 OS）拿去比對
[Recog](https://github.com/rapid7/recog)（Rapid7，BSD-2-Clause），認出設備（廠商的出廠預設憑證、管理介面）與精確的
OS 版本。沒有安裝時探測照常運作，只是認得比較少。

- `jt-ipam.sh install` 與 `upgrade` 會下載最新版（需要對外 HTTPS 連到 `api.github.com`、`github.com`、
  `release-assets.githubusercontent.com`），下載失敗只會警告。
- `jt-ipam-recog-refresh.timer` 每週一檢查一次新版（Recog 約每 1～6 週發佈一版），有新版才下載。
  「版本資訊」頁顯示已安裝的版本、上次檢查時間，並有「立即檢查更新」按鈕；系統診斷在沒安裝、或三週沒有更新成功時
  會提出警告。
- 離線主機：從 [發佈頁](https://github.com/rapid7/recog/releases) 下載 `recog-content-<版本>.zip`，再執行
  `sudo /opt/jt-ipam/scripts/jt-ipam.sh upgrade --recog-zip <檔案>`（或 `install --recog-zip <檔案>`）。

### 外部 DNS 解析（選用）

「對外開放服務」的 FQDN 頁籤可以顯示每個名稱從網際網路上查到什麼。到「管理 → 系統設定」的「DNS 解析器」卡片設定：
一台以上的解析器（例如 1.1.1.1）、要查的對外網域，以及選填的我方公網網段。只有對外網域底下的名稱會送出去查。
伺服器要能對外連到解析器的 UDP 與 TCP 53 埠；打開「同時直接問權威伺服器」時，也要能連到各網域的權威伺服器。不需要額外套件。

### LLM / AI（選用）

AI 對話與語意搜尋預設走**自架的 Ollama**，資料不出自己的網路。安裝腳本不會幫你裝 LLM Server，它通常跑在另一台有顯示卡的機器上。

```bash
# 在 LLM Server 上（範例）
ollama pull gemma4:26b                 # 對話模型
ollama pull granite-embedding:278m     # 嵌入模型（768 維、多語系）
```

然後在 **管理 → LLM / AI** 填上位址與兩個模型，按一次「**檢查維度**」確認相符，再按「**重建索引**」把既有資料補上向量。

> **從舊版升級的人請特別看這段。** 升級**不會**自動修好語意搜尋，有三件事只能手動：
> 1. 出廠預設換成 `granite-embedding:278m`，但**只在你沒有在設定頁存過嵌入模型時才生效**；存過的話資料庫裡的值優先，仍是舊的。
> 2. 新模型要自己在 LLM Server 上 `ollama pull`。
> 3. 既有資料的向量要按「重建索引」才會補上。
>
> 設定頁在載入時會自動驗一次維度，不符會直接顯示出來，這正是舊版最要命的地方：維度不合完全沒有錯誤訊息，只表現為「語意搜尋永遠沒有結果」。

> ⚠️ **嵌入模型的輸出維度必須等於 `EMBEDDING_DIM`（預設 768）**，那是資料庫 `vector(N)` 欄位的維度。維度不合時**不會有錯誤訊息**，唯一的症狀是「語意搜尋永遠沒有結果」，因為每一筆索引寫入都失敗了。換模型之後請按設定頁的「**檢查維度**」驗一次，它會直接告訴你模型回幾維、欄位要幾維。
>
> 另外，**只支援英文的嵌入模型（如 `nomic-embed-text`）會把不同的中文描述壓成同一個向量**，維度看起來對、搜尋也有結果，但排序是亂的。要換模型的話，挑多語系的，並拿自己實際在用的描述比對一下不同描述是否得到不同結果。

想改接外部服務（ChatGPT、vLLM、LM Studio、OpenRouter 等 OpenAI 相容端點）也可以，設定頁選「OpenAI 相容」並填入 API 金鑰（AES-GCM 加密存放）。**這代表網段、主機名稱與拓樸會送到該服務**；要求資料不出網的話請維持自架的 Ollama。

### 自動備份

安裝腳本會裝好並啟用每日備份（`jt-ipam-backup.timer`），升級時也會更新備份腳本。不是用安裝腳本裝的，可以手動加上：

```bash
sudo cp /opt/jt-ipam/scripts/jt-ipam-backup.sh /usr/local/bin/
sudo install -m 0644 /opt/jt-ipam/deploy/systemd/jt-ipam-backup.{service,timer} \
    /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now jt-ipam-backup.timer
```

預設每天 03:30 跑，把 `pg_dump -Fc` + `/etc/jt-ipam/backend.env` + TLS 憑證
打包到 `/var/backups/jt-ipam/`，保留 14 天。

每次執行（成功或失敗）都會寫 `/var/backups/jt-ipam/last-run`：結果、時間、最後一次成功的時間、失敗原因。「系統診斷」頁的「每日備份」與 `sudo /opt/jt-ipam/scripts/jt-ipam.sh doctor` 都看這個檔；備份失敗或超過 48 小時沒有成功時診斷會標紅，並發系統告警給管理員。最常見的失敗原因是資料庫裡有一張不是 jt-ipam 角色擁有的資料表（例如手動留下的複本），pg_dump 讀不到就整份中止；診斷頁會直接列出要執行的指令。

### 備份加密

備份目錄裡有資料庫 dump，**也有** `backend.env`（解密 dump 裡那些機密用的金鑰）。在 **管理 → 系統設定 → 每日備份加密** 設定備份密碼（至少 12 個字元）之後，每天的備份會整包加密成一個檔案 `/var/backups/jt-ipam/jt-ipam-<日期>.jtbak`（scrypt 導出金鑰、AES-256-GCM 以 1 MiB 為單位分塊加密；截斷、調換或修改都解不開），明文目錄刪除。`last-run` 會記 `encrypted=1`；沒設定密碼時系統診斷頁與 `doctor` 會提醒。

密碼請存在這台主機以外的地方（例如密碼管理器）：忘記就解不開備份。更換密碼後，之前的備份仍要用當時的密碼解。解密工具只需要 Python 3 與 `cryptography`（`apt install python3-cryptography`），還沒重裝 jt-ipam 的新機器也能用：

```bash
python3 /opt/jt-ipam/backend/app/services/backup_crypt.py decrypt \
    /var/backups/jt-ipam/jt-ipam-2026-10-09.jtbak --out /root/jt-ipam-restore
# → /root/jt-ipam-restore/2026-10-09/（dump、backend.env、tls.tar.gz、uploads.tar.gz）
```

### 異地備份

把 `/var/backups/jt-ipam/` rsync 到 NAS / S3 / 另一台機器：

```bash
# 例：每天 04:00 推到 NAS
0 4 * * * rsync -a /var/backups/jt-ipam/ jtipam@nas.local:/backups/jt-ipam/
```

### 還原

```bash
# 0. 停服務
sudo systemctl stop jt-ipam-backend jt-ipam-sync.timer jt-ipam-ai-audit.timer

# 0b. 加密的備份（.jtbak）：先解密（會問備份密碼）
python3 /opt/jt-ipam/backend/app/services/backup_crypt.py decrypt \
    /var/backups/jt-ipam/jt-ipam-2026-05-10.jtbak --out /root/jt-ipam-restore
# 下面的 /var/backups/jt-ipam/2026-05-10/ 改用 /root/jt-ipam-restore/2026-05-10/

# 1. 重建空 DB
sudo -u postgres dropdb jt_ipam
sudo -u postgres createdb -O jt_ipam jt_ipam
sudo -u postgres psql -d jt_ipam -c '
    CREATE EXTENSION IF NOT EXISTS pgcrypto;
    CREATE EXTENSION IF NOT EXISTS citext;
    CREATE EXTENSION IF NOT EXISTS pg_trgm;
    CREATE EXTENSION IF NOT EXISTS btree_gist;
    CREATE EXTENSION IF NOT EXISTS vector;
'

# 2. 還原 dump（注意：必須用相同 ENCRYPTION_KEY 才能解密 DNS/API 憑證等敏感欄）
sudo -u postgres pg_restore -d jt_ipam \
    /var/backups/jt-ipam/2026-05-10/jt-ipam-2026-05-10.dump

# 3. 還原設定檔（如果還在）
sudo cp /var/backups/jt-ipam/2026-05-10/backend.env /etc/jt-ipam/

# 4. 把稽核表交回不能登入的擁有者（應用程式帳號只能讀取與新增），再啟動
sudo bash /opt/jt-ipam/scripts/jt-ipam.sh harden-audit
sudo systemctl start jt-ipam-backend jt-ipam-sync.timer jt-ipam-ai-audit.timer

# 5. 驗 chain（任何 row 被竄改會立刻看到）
curl -X POST https://ipam.example.com/api/v1/audit/verify \
    -H "Authorization: Bearer <admin token>"
```

>  備份檔內含敏感資料（DB 含加密的 API 憑證；env 含 SECRET_KEY/ENCRYPTION_KEY）。
> 請設定備份加密密碼（見上方），以 `0600` 權限儲存，並做加密傳輸（rsync over ssh / s3 server-side encryption）。

---

## 6. 升級

統一腳本一鍵升級（內含 git pull → 備份 → pip → 停止寫入 → 基準清冊 → alembic → 逐筆核對 → build → restart）：

```bash
sudo bash /opt/jt-ipam/scripts/jt-ipam.sh upgrade
# 已自行 git pull、不想再拉：  sudo bash /opt/jt-ipam/scripts/jt-ipam.sh upgrade --no-pull
```

等同的手動步驟（需要時逐步執行）：

```bash
cd /opt/jt-ipam
git pull
sudo systemctl stop jt-ipam-backend
sudo -u jtipam /opt/jt-ipam/backend/.venv/bin/pip install -e backend
sudo -u jtipam bash -c 'cd /opt/jt-ipam/backend; set -a; . /etc/jt-ipam/backend.env; set +a; .venv/bin/python -m app.cli.upgrade_verify snapshot --out /var/lib/jt-ipam/upgrade-checks/manual/before.json'
sudo -u jtipam /opt/jt-ipam/backend/.venv/bin/alembic -c backend/alembic.ini upgrade head
sudo -u jtipam bash -c 'cd /opt/jt-ipam/backend; set -a; . /etc/jt-ipam/backend.env; set +a; .venv/bin/python -m app.cli.upgrade_verify compare --before /var/lib/jt-ipam/upgrade-checks/manual/before.json --report /var/lib/jt-ipam/upgrade-checks/manual/report.json'
cd frontend && sudo -u jtipam pnpm install --frozen-lockfile && sudo -u jtipam pnpm run build
sudo systemctl start jt-ipam-backend
```

**資料核對。**原地升級不能遺失既有資料。遷移前腳本會先停止寫入（後端與 jt-ipam 的排程），把每一筆機房、機櫃、裝置位置、電力、佈線、裝置與 IP 的關聯、權限都記下來；遷移後**逐筆、逐欄**比對（不是只比筆數；平面圖檔比內容摘要）。升級前就存在的資料問題分開列出，只有新出現的才算失敗。只要有缺或有變，腳本就停下來，印出怎麼還原剛才的備份，並**讓後端與排程維持停止**，避免在受損的資料上繼續寫入。清冊與報告（`/var/lib/jt-ipam/upgrade-checks/<時間>/`）含站台資料，只有服務帳號讀得到（0600）。

---

## 7. 監控與告警

### Journal 觀察

```bash
journalctl -u jt-ipam-backend -f          # backend
journalctl -u jt-ipam-sync -n 200          # 定期同步
journalctl -u jt-ipam-ai-audit -n 200      # 排程 AI 巡檢
journalctl -u jt-ipam-backup -n 50         # 備份
```

### healthcheck

`https://<your-fqdn>/api/v1/healthz` 回 200 = OK。

### 推到 SIEM/Slack

backend 已支援 webhook subscription：admin → 設定 → notifications 加
webhook URL。也可在 `BACKEND_*` env 加 Graylog GELF endpoint，全 audit
log 同步外送。

---

## 8. 移除

統一腳本移除（預設只停服務、移除 systemd units/timers + nginx site，**保留 DB / 設定 / 原始碼**）：

```bash
sudo bash /opt/jt-ipam/scripts/jt-ipam.sh uninstall
# 連 DB / 設定 / 上傳檔 / 系統 user 一起刪（會要求確認；--yes 跳過確認）：
sudo bash /opt/jt-ipam/scripts/jt-ipam.sh uninstall --purge
```

> `uninstall` 永不刪除 `/opt/jt-ipam` 原始碼。

等同的手動步驟：

```bash
sudo systemctl disable --now jt-ipam-backend jt-ipam-sync.timer jt-ipam-ai-audit.timer jt-ipam-backup.timer
sudo rm /etc/systemd/system/jt-ipam-{backend,sync,backup}.{service,timer}
sudo rm -rf /etc/jt-ipam /var/log/jt-ipam /var/lib/jt-ipam
sudo -u postgres dropdb jt_ipam
sudo -u postgres dropuser jt_ipam
sudo userdel -r jtipam
```

備份（`/var/backups/jt-ipam/`）需自行決定是否保留。

---

## 9. 常見問題

**Q: 我用 `--tls-mode self-signed` 裝好了，還需要另外裝 nginx 或 apache 嗎？**
A: **不需要。** 這個模式是 uvicorn 自己掛 TLS，裝完就是一個完整的 HTTPS 服務，
不必再裝任何 web server。網址是 `https://<你的網域>:8443/`（**預設 8443，不是 443**）。
確認方式：

```bash
sudo systemctl status jt-ipam-backend        # 應為 active (running)
sudo ss -ltnp | grep 8443                    # 應看到 uvicorn 在聽
curl -kI https://127.0.0.1:8443/             # 應回 HTTP/1.1 200
```

要改用 443、或之後想加 nginx，見 2.6「監聽在哪個埠」與「自簽模式要不要另外裝 nginx」。

**Q: 安裝中斷在 `extension "vector" is not available`，但我確實裝了 postgresql-16-pgvector？**
A: 這台通常**本來就有一個 PostgreSQL 叢集在跑**（例如原本裝過 SonarQube、GitLab 等）。
jt-ipam 連的是 `127.0.0.1:5432`，也就是**那個既有叢集**；pgvector 必須裝在**它的版本**上，
裝到別的版本沒有用。查它的版本再補對應套件：

```bash
sudo -u postgres psql -tAc 'SHOW server_version_num'   # 例如 180004 → 主版本 18
sudo apt install -y postgresql-18-pgvector
sudo -u postgres psql -d jt_ipam -c 'CREATE EXTENSION IF NOT EXISTS vector;'
```

> v0.5.161 起安裝腳本會自動偵測既有叢集的版本並裝對應的 pgvector，也不會再另外拉一個
> server 套件（那會多建一個叢集）；擴充功能建立失敗會當場停下並說明，不再拖到 alembic 才爆。

**Q: 安裝停在 `/usr/local/bin/pnpm: 沒有此一檔案或目錄`？**
A: pnpm 沒有安裝成功，而舊版腳本把 npm 的錯誤訊息丟掉了。手動裝好再重跑安裝即可
（安裝腳本可重複執行，已完成的步驟會跳過）：

```bash
sudo npm install -g pnpm@9      # 或：curl -fsSL https://get.pnpm.io/install.sh | sh -
sudo ./scripts/jt-ipam.sh install --tls-mode self-signed --public-fqdn ipam.example.com
```

> v0.5.161 起腳本會保留 npm 的原始錯誤、依序嘗試三種安裝方式，並在確認 `pnpm --version`
> 跑得起來之後才繼續。

**Q: 安裝說「完成」，但服務其實沒起來？**
A: v0.5.161 起，安裝結束前會逐項自我檢查（設定檔、前端 dist、服務狀態、監聽埠、
nginx 模式下的 nginx），缺什麼會直接列出來。舊版沒有這道檢查，請手動確認：

```bash
ls -l /etc/jt-ipam/backend.env                 # 設定檔在不在
ls -l /opt/jt-ipam/frontend/dist/index.html    # 前端有沒有建出來
sudo systemctl status jt-ipam-backend
sudo journalctl -u jt-ipam-backend -n 50 --no-pager
```

**Q: backend 起不來，journal 顯示 "ENCRYPTION_KEY: invalid format"？**
A: ENCRYPTION_KEY 必須是 32-byte 的 base64（44 字元結尾 `=`）。安裝腳本有產；
若手動設定，用 `python -c 'import base64,os; print(base64.b64encode(os.urandom(32)).decode())'`。

**Q: backend OOM？**
A: argon2id 預設 64 MiB / 4 parallelism；若 RAM 緊（< 2GB），可降 `ARGON2_MEMORY_COST_KIB=32768`。

**Q: nginx 502？**
A: backend bind 預設 `127.0.0.1:8000`；確認 systemctl status jt-ipam-backend
是 active，且 nginx site 的 upstream 也指 `127.0.0.1:8000`。

**Q: pgvector 找不到？**
A: Ubuntu 22.04 沒內建；安裝腳本會自動加 PGDG repo + 對應版本的 `postgresql-N-pgvector`。
手動補：先用 `sudo -u postgres psql -tAc 'SHOW server_version_num'` 查出**實際在跑的**主版本，
再 `sudo apt install postgresql-<主版本>-pgvector`，最後 `CREATE EXTENSION vector;`。
（裝錯版本是最常見的原因，見上面「extension "vector" is not available」那一題。）

**Q: `/api/v1/audit/verify` 回 `{"ok": false}` chain 斷裂？**
A: 已知歷史 bug：v0.3.0 之前 nginx 把它的 `$request_id`（32-hex 無 hyphen）
透傳給 backend，backend 寫進 audit canonical 但 PG 讀回後是 hyphenated UUID，
verify 時對不上。0.3.1+ middleware 已標準化（見 `app/core/middleware.py`
`RequestIDMiddleware`）。如果你舊鏈已斷且資料還沒上 production：
```bash
# 重置 chain（會清掉所有 audit_logs；只能對未上線環境做）
sudo -u postgres psql -d jt_ipam -c "TRUNCATE audit_logs RESTART IDENTITY;"
sudo systemctl restart jt-ipam-backend
```

**Q: 安裝腳本說 `nginx: $request_id` warning `ssl_stapling ignored, issuer certificate not found`？**
A: 自簽憑證沒有 issuer 鏈所以 OCSP stapling 用不到，無害。換成正式憑證或 Let's Encrypt 後會消失。

**Q: 整合測試需要 `JTIPAM_TEST_DATABASE_URL`，正式部署也要嗎？**
A: 不用。正式部署只需要 `backend.env` 裡的 `POSTGRES_*`。`JTIPAM_TEST_DATABASE_URL` 只是給 pytest 用的另一個 DB（避免污染 prod 資料）。

**Q: 安裝完後預設 IP 訪問會看到 "Welcome to nginx" 而不是 jt-ipam？**
A: 已知問題（0.3.1 以前）：apt 裝完 nginx 預設啟用 `default` site，會搶 IP-only 訪問。0.3.1+ 的 `install-debian.sh` 會：(1) 自動 `rm /etc/nginx/sites-enabled/default`，(2) jt-ipam site 加 `listen ... default_server`，這樣 IP / FQDN / 任何 hostname 都會走 jt-ipam。舊機可手動：
```bash
sudo rm /etc/nginx/sites-enabled/default
sudo sed -i 's|listen 80;|listen 80 default_server;|; s|listen 443 ssl http2;|listen 443 ssl http2 default_server;|' /etc/nginx/sites-available/jt-ipam
sudo nginx -t && sudo systemctl reload nginx
```

**Q: 預設 admin 帳密是什麼？忘了怎麼辦？**
A: **沒有預設帳密**。安裝完後必須手動 bootstrap（見 §2.3）；密碼由 `openssl rand` 隨機產，**只在 bootstrap 當下印一次**，要自己存好。忘了或要換：
```bash
# 方案 A：再開一個 admin（如果原 admin 還能用，先讓另一個 admin 從 UI /users 改密）
ADMIN_PW=$(openssl rand -base64 24)
sudo -u jtipam env $(grep -v '^#' /etc/jt-ipam/backend.env | xargs) \
    /opt/jt-ipam/backend/.venv/bin/python -m app.cli.bootstrap create-admin \
    --username admin2 --email admin2@your.domain --password-stdin <<<"$ADMIN_PW"
echo "$ADMIN_PW"

# 方案 B：原 admin 已鎖死 / 失聯，直接改 DB 把它解鎖並重設密碼
sudo -u jtipam env $(grep -v '^#' /etc/jt-ipam/backend.env | xargs) \
    /opt/jt-ipam/backend/.venv/bin/python -c '
import asyncio, sys
from app.core.db import SessionLocal
from app.core.security import hash_password
from app.models.user import User
from sqlalchemy import select

async def main(username, new_pwd):
    async with SessionLocal() as s:
        u = (await s.execute(select(User).where(User.username == username))).scalar_one()
        u.password_hash = hash_password(new_pwd)
        u.locked_until = None
        u.failed_login_count = 0
        u.is_active = True
        u.is_admin = True
        await s.commit()
        print(f"reset {u.username}")

asyncio.run(main(sys.argv[1], sys.argv[2]))
' admin "MyNewPassword2026!"
```

**Q: 出現 `Node.js install failed or too old (need >= 22)`，或升級時印出「Node.js 22 could not be installed」的警告框？**
A: Node.js 22 LTS 只用來建置前端（Node 20 已在 2026-04-30 停止支援）。`jt-ipam.sh` 在安裝與升級時都會從
NodeSource（`setup_22.x`）裝好，平常不用做任何事。主機裝不起來時（連不到 deb.nodesource.com、走代理、apt 衝突），
`install` 會停下來；`upgrade` 則沿用既有的 Node 20 以上版本建置、照常完成，最後印出那個警告框，此時 pnpm 也會印
`Unsupported engine: wanted: {"node":">=22"}`，`doctor` 會持續提醒 Node 太舊。手動裝好後重跑同一個指令：
```bash
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo bash -
sudo apt install -y nodejs
node -v      # v22.x
sudo bash /opt/jt-ipam/scripts/jt-ipam.sh upgrade      # 或 install
```
apt 回報和發行版自己的套件衝突（`trying to overwrite ... libnode-dev`）時，先移除：
`sudo apt-get purge -y nodejs libnode-dev`。有用 nvm 的話，執行 `sudo` 的那個使用者若有 nvm 的 Node 22 會直接沿用；
較舊的 nvm Node 不會被採用。

## RDP 主控台的連線引擎

瀏覽器 RDP 主控台可以在「管理 → 系統設定」選用三個引擎之一：

- **guacd**（預設、必要）：見下一節。相容性最好，也不需要虛擬顯示。
- **aardwolf**（選用）：純 Python 用戶端，現在只是 guacd 停掉時的備用引擎。它的預編套件只到
  Python 3.13，在 3.14 上會當掉（GitHub issue #42），所以能裝才裝、沒有任何東西依賴它。
- **FreeRDP**：相容性比 aardwolf 好。Linux 上的 RDP 伺服器（xrdp，以及 Ubuntu 24 內建的
  GNOME「遠端登入」）會拒絕 aardwolf 的 NTLM 認證，因為那個函式庫沒有送出這些伺服器
  要求的訊息完整性碼；FreeRDP 則通得過。

FreeRDP 需要的套件**預設不安裝**，多數站台不會切換：

```
sudo apt-get install -y freerdp2-x11 xvfb xclip ffmpeg
```

或在安裝時加上 `jt-ipam.sh install --with-freerdp`。站台若已經選用 FreeRDP 引擎，
升級時會自動補上這些套件。`jt-ipam.sh doctor` 會報出目前用的是哪個引擎、套件在不在，
設定頁也會顯示同樣的資訊與該執行的指令。

ffmpeg 是用來抓畫面的，不是拿來做影片：我們量過其他抓法每張要 334 毫秒，
會把主控台壓在每秒 3 張以下。

## guacd 主控台引擎（RDP/VNC 的預設）

guacd 是 [Apache Guacamole](https://guacamole.apache.org/) 的伺服器端，是 **RDP 與 VNC 主控台的預設引擎**，
也是**必要元件**（0.6.49 起；已安裝的站台升級後也會改過來），SSH 也可以在「管理 → 系統設定」改用它。
`install` 會裝上，裝不起來就停下來；`upgrade` 沒裝就裝上。guacd 停掉時，RDP 與 VNC 會退回選用的內建引擎
（aardwolf，有裝的話），`doctor`、「管理 → 系統診斷」與「版本資訊 → 必要相依」都會列出問題。

為什麼要另外安裝：Debian 已經不提供 guacd，Ubuntu 只有帶著可遠端執行程式碼漏洞的 1.3.0。
所以 jt-ipam 替每個支援中的 OS 版本各編一份（Debian 12/13、Ubuntu 22.04/24.04/26.04）。
預編檔連結的是 OS 自己的函式庫，FreeRDP、libvncclient 等的安全更新照樣由 apt 提供。

```
sudo /opt/jt-ipam/scripts/jt-ipam.sh install                     # 會一併裝 guacd
sudo /opt/jt-ipam/scripts/jt-ipam.sh upgrade                     # 沒裝就裝上、裝了就更新
sudo /opt/jt-ipam/scripts/jt-ipam.sh upgrade --guacd-tarball ./jt-ipam-guacd-...-ubuntu24.04-amd64.tar.gz
                                                                 # 離線：同一目錄要有對應的 .deps 檔
```

- 以 `jt-ipam-guacd` systemd 服務執行，**只綁 127.0.0.1:4822**。guacd 本身沒有任何驗證，
  絕不可以開在其他介面上。服務以沒有任何權限的動態使用者執行。
- 每次 `upgrade` 都會換成與該版 jt-ipam 對應的預編檔。下載的檔案會用 `scripts/guacd/SHA256SUMS` 核對。
- 帳密由伺服器交給 guacd，不會經過瀏覽器。SSH 主機金鑰一樣首次確認後釘選，guacd 必須對得上釘選的金鑰。
- SSH 走 guacd 時，終端機在伺服器端畫出來。複製、貼上用 Ctrl+Shift+C/Ctrl+Shift+V
  （Mac 用 ⌘C/⌘V），中文、日文輸入法都可以用。
- `jt-ipam.sh doctor` 與「管理 → 系統診斷」會顯示 guacd 有沒有在跑，以及選了它的協定是否都有支援。

授權：guacd 是 Apache-2.0。每個預編檔都附 LICENSE、NOTICE，以及寫明確切原始碼與編譯腳本的 SOURCE 檔。
這些是 jt-ipam 編譯的版本，不是 Apache 官方發行版。
