# jt-ipam 部署

> English: [README.md](README.md)

> 主力部署不使用 Docker（另有選用的 docker-compose 路徑見 [`docker/`](docker/README_zh-TW.md)）。預設目標：**Proxmox LXC**（Debian 12 / Ubuntu 24.04）或裸機。

---

## 一、快速安裝（單機 / LXC）

### 1.1 系統需求

- Debian 12 / Ubuntu 22.04+（Proxmox LXC 範本即可）
- 2 vCPU / 4 GB RAM / 20 GB 磁碟（最小）
- Python 3.12、PostgreSQL 16、Redis 7、Node 20+
- **TLS 強制**（兩種模式擇一，見 §1.3）

### 1.2 一鍵安裝

腳本支援 `--tls-mode {nginx|direct|self-signed}`：

```bash
# 模式 A：nginx 反代 HTTPS（建議；公開服務 / 已有 FQDN）
sudo ./scripts/install-debian.sh \
    --tls-mode nginx \
    --public-fqdn ipam.your-domain.tld

# 模式 B：後端 uvicorn 直接吃自簽憑證（極簡 / 內網 / 沒有 FQDN）
sudo ./scripts/install-debian.sh \
    --tls-mode self-signed \
    --public-fqdn ipam.local \
    --bind-port 8443
```

腳本自動：

1. `apt install postgresql-16 redis-server python3.12 nginx*¹ pnpm` 等依賴
2. 建立系統使用者 `jtipam`（無 shell）
3. 建立 PostgreSQL role + DB（SCRAM-SHA-256；密碼自動產生）
4. 設定 Redis `requirepass`
5. 建 Python venv + 安裝 backend
6. 自動產生 `SECRET_KEY` / `ENCRYPTION_KEY` / `AUDIT_CHAIN_GENESIS`
7. 跑 `alembic upgrade head`
8. `pnpm build` 前端到 `frontend/dist`
9. **依 TLS 模式產生 `/etc/jt-ipam/backend.env`**
10. 安裝 systemd unit：`jt-ipam-backend.service`
11. 模式 A：安裝 nginx site；模式 B：產生自簽憑證

> *¹ 模式 A 才裝 nginx；B 不裝。

完成後：

```bash
systemctl status jt-ipam-backend
# 模式 A
curl -fsS http://127.0.0.1:8000/healthz
# 模式 B
curl -fsSk https://127.0.0.1:8443/healthz
```

### 1.3 兩種 TLS 模式詳解

#### 模式 A：nginx 反代 HTTPS（建議，預設）

架構：

```
browser ── HTTPS ──► nginx :443 ── HTTP ──► uvicorn 127.0.0.1:8000
            ▲
            │ 憑證放這裡（Let's Encrypt / 內網 CA / 自簽）
            └─ /etc/letsencrypt/... or /etc/ssl/...
```

`/etc/jt-ipam/backend.env`：

```ini
BACKEND_TLS_MODE=nginx
BACKEND_BIND_HOST=127.0.0.1
BACKEND_BIND_PORT=8000
APP_PUBLIC_URL=https://ipam.your-domain.tld
API_PUBLIC_URL=https://ipam.your-domain.tld
CORS_ORIGINS=https://ipam.your-domain.tld
```

取得憑證（公網）：

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d ipam.your-domain.tld
sudo systemctl reload nginx
```

或內網自簽（給 nginx 用）：

```bash
sudo ./scripts/generate-self-signed-cert.sh \
    --out-dir /etc/jt-ipam/tls \
    --cn ipam.local \
    --san "DNS:ipam.local,IP:192.168.1.10"

# 編輯 /etc/nginx/sites-available/jt-ipam，把 ssl_certificate 路徑改為：
#   ssl_certificate     /etc/jt-ipam/tls/server.crt;
#   ssl_certificate_key /etc/jt-ipam/tls/server.key;
sudo nginx -t && sudo systemctl reload nginx
```

優點：成熟、效能好、可掛 ACME 自動續期、可加 HTTP/2 / HTTP/3、可同時掛多個 site。
缺點：多一個元件要維運。

---

#### 模式 B：後端直接 TLS + 自簽憑證

架構：

```
browser ── HTTPS ──► uvicorn 0.0.0.0:8443
                       │
                       └─ 直接吃 cert/key
                          /etc/jt-ipam/tls/server.{crt,key}
```

`/etc/jt-ipam/backend.env`：

```ini
BACKEND_TLS_MODE=direct
BACKEND_BIND_HOST=0.0.0.0
BACKEND_BIND_PORT=8443
BACKEND_TLS_CERT_FILE=/etc/jt-ipam/tls/server.crt
BACKEND_TLS_KEY_FILE=/etc/jt-ipam/tls/server.key
APP_PUBLIC_URL=https://ipam.local:8443
API_PUBLIC_URL=https://ipam.local:8443
CORS_ORIGINS=https://ipam.local:8443
```

產生 / 重發自簽憑證（ECDSA P-384、5 年；自動偵測 hostname / IP 加入 SAN）：

```bash
sudo ./scripts/generate-self-signed-cert.sh \
    --out-dir /etc/jt-ipam/tls \
    --cn ipam.local \
    --san "DNS:ipam.local,DNS:ipam,IP:192.168.1.10" \
    --days 1825 \
    --owner root:jtipam
```

驗證：

```bash
openssl x509 -in /etc/jt-ipam/tls/server.crt -noout -text \
    | grep -E 'Subject:|DNS:|IP Address:|Not After'
```

想用 443 而不是 8443？預設 systemd unit `CapabilityBoundingSet=` 全 drop（A05 hardening），瀏覽器要訪問 `https://host/` 而不是 `:8443` 的話需給 backend `CAP_NET_BIND_SERVICE`：

```bash
sudo systemctl edit jt-ipam-backend
# 把 deploy/systemd/override-direct-tls-port443.conf.example 內容貼進去
sudo systemctl daemon-reload
sudo systemctl restart jt-ipam-backend
```

> **注意**：自簽憑證瀏覽器會出現警告。可選擇：(1) 使用者端匯入 CA／信任憑證；(2) 內網架小型 CA（step-ca、smallstep）；(3) 改回模式 A 走 Let's Encrypt。

優點：少一個元件、設定簡單、適合 LXC 小型部署。
缺點：HTTP/2 / HTTP/3 / 多 site 不便、自簽憑證信任問題、效能不如 nginx。

---

### 1.4 切換模式

之後想換模式，**不用重裝**：

1. 編輯 `/etc/jt-ipam/backend.env`，更新 `BACKEND_TLS_MODE` / 相關欄位
2. 視情況裝 / 移除 nginx site
3. `sudo systemctl restart jt-ipam-backend`

---

## 二、目錄與檔案佈局

```
/opt/jt-ipam/                      # 程式碼（git checkout）
├── backend/
│   ├── .venv/                     # Python venv (jtipam 擁有)
│   └── ...
└── frontend/
    └── dist/                      # vite build 後的靜態檔（nginx 指向此）

/etc/jt-ipam/
├── backend.env                    # 應用設定（含密鑰，0640 root:jtipam）
├── .db-password                   # 由腳本產生（0600 root:root）
└── .redis-password                # 由腳本產生（0600 root:root）

/etc/systemd/system/
└── jt-ipam-backend.service

/etc/nginx/
├── sites-available/jt-ipam
├── sites-enabled/jt-ipam → ../sites-available/jt-ipam
└── snippets/jt-ipam-proxy.conf

/var/lib/jt-ipam/                  # state（StateDirectory；jtipam:jtipam）
/var/log/jt-ipam/                  # log（LogsDirectory；jtipam:jtipam）
```

---

## 三、systemd hardening（OWASP A05）

`jt-ipam-backend.service` 已預設套用：

| Directive | 用途 |
|---|---|
| `User=jtipam` / `Group=jtipam` | 非 root 執行 |
| `NoNewPrivileges=true` | 阻擋 setuid / setcap 提權 |
| `ProtectSystem=strict` | 整個 `/` 唯讀，僅 ReadWritePaths 可寫 |
| `ProtectHome=true` | 看不到使用者 home |
| `PrivateTmp=true` | 隔離 `/tmp` |
| `PrivateDevices=true` | 隱藏除 `/dev/null` 等以外的裝置 |
| `ProtectKernelTunables/Modules/Logs` | 不能改 sysctl / 載入模組 |
| `RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6` | 只允許 IP socket |
| `RestrictNamespaces=true` | 阻擋容器/namespace 操作 |
| `MemoryDenyWriteExecute=true` | 阻擋 W^X 違反 |
| `SystemCallFilter=@system-service` | seccomp 白名單 |
| `CapabilityBoundingSet=` | 全部 capability drop |
| `LimitNOFILE=65536` / `TasksMax=1024` | 資源限制 |

驗證：

```bash
sudo systemd-analyze security jt-ipam-backend
# 期望分數 ≤ 3.5（OK）；若 > 5.0 表示 hardening 未生效
```

---

## 四、升級流程

```bash
cd /opt/jt-ipam
sudo -u jtipam git pull --ff-only

# 後端依賴
sudo -u jtipam backend/.venv/bin/pip install -e backend

# DB migration（先備份）
sudo -u postgres pg_dump jt_ipam | gzip > /var/backups/jt-ipam-$(date +%F).sql.gz
sudo -u jtipam --preserve-env=PATH bash -c \
  'set -a; source /etc/jt-ipam/backend.env; set +a; cd backend && .venv/bin/alembic upgrade head'

# 前端
cd /opt/jt-ipam/frontend
sudo -u jtipam pnpm install --frozen-lockfile
sudo -u jtipam pnpm build

# 重啟
sudo systemctl restart jt-ipam-backend
sudo systemctl reload nginx
```

---

## 五、備份與還原

### 5.1 備份

```bash
# 每日（建議放 /etc/cron.daily/jt-ipam-backup）
pg_dump -U jt_ipam jt_ipam | zstd -19 > /var/backups/jt-ipam/db-$(date +%F).sql.zst

# /etc/jt-ipam（含金鑰）— 保險櫃 / Vault
tar czf - /etc/jt-ipam | gpg --encrypt --recipient backup@example.com \
    > /var/backups/jt-ipam/etc-$(date +%F).tar.gz.gpg
```

### 5.2 還原

```bash
systemctl stop jt-ipam-backend
sudo -u postgres dropdb jt_ipam
sudo -u postgres createdb -O jt_ipam jt_ipam
zstdcat db-2026-05-09.sql.zst | sudo -u postgres psql jt_ipam
systemctl start jt-ipam-backend
```

> **注意（A02）**：`/etc/jt-ipam/backend.env` 中的 `ENCRYPTION_KEY` 與 `AUDIT_CHAIN_GENESIS` **不可遺失**，否則無法解密既有敏感欄位、無法驗證稽核鏈。建議用 GPG 加密後存於異地（Proxmox Backup Server 雙站、Vault）。

---

## 六、HA 架構（Phase 3+ 規劃）

> 第一版只支援單機。HA 文件由 Phase 3 補完，框架預先列出：

- PostgreSQL **streaming replication**（pg-primary + pg-standby）+ pgBouncer + patroni
- Redis **Sentinel**（3 節點）
- 應用層多副本：`jt-ipam-backend.service` 跑多台 LXC，前端統一 nginx upstream
- 共用儲存：靜態前端可 rsync 同步，或統一從 CDN/物件儲存提供

---

## 七、Proxmox LXC 範本

（規劃中）jt-ipam 將提供 Proxmox LXC `.tar.zst` 範本：

- Debian 12 base
- 已安裝 PG 16、Redis 7、Python 3.12、nginx
- 首次啟動 cloud-init 跑 `install-debian.sh`
- 預設帳號 `jtipam`，systemd 已啟用

下載與部署文件待 Phase 1 後期釋出。
