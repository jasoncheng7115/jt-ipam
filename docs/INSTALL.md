# jt-ipam 安裝與運維 SOP

> English: [INSTALL.en.md](INSTALL.en.md)

針對 **Proxmox LXC、裸機、虛擬機**（Ubuntu 22.04+/Debian 12+）。本專案
**不使用 Docker**；以 systemd + apt 直裝。

> 安全為 day-one 需求：所有環境強制 HTTPS；憑證可走 nginx 反代或
> uvicorn 直接吃自簽。SSL 沒設好 backend **不會啟動**（A02）。

---

## 1. 系統需求

| 項目 | 最低 | 建議 | 備註 |
|---|---|---|---|
| OS | Ubuntu 22.04 / Debian 12 | **Ubuntu 24.04 LTS** | 24.04 內建 Python 3.12 + PG 16 + Node 18，省事 |
| CPU | 2 vCPU | 4 vCPU | argon2id + pgvector embedding 吃 CPU |
| RAM | 4 GB | 8 GB | 開 Ollama 還要再加 8 GB |
| Disk | 20 GB | 50 GB | audit log 累積 |
| Python | 3.11 | 3.12 | 24.04 預設就是 3.12  |
| PostgreSQL | 16 + pgvector | — | 22.04 需 PGDG repo（腳本會自動加）|
| Redis | 7 | — | 24.04 預設 7.0.15  |
| Node | 20 LTS | 22 LTS | 24.04 預設 18.19；vite 6 跑得動但有 warning |

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

```bash
# clone（或從 dev 機 rsync 過去）
git clone https://github.com/jasontools/jt-ipam.git /opt/jt-ipam
cd /opt/jt-ipam

# 三種 TLS 模式擇一：
#
#   nginx         — nginx 終結 HTTPS，後端 loopback；缺憑證時自動產自簽 bootstrap
#   self-signed   — uvicorn direct 自帶自簽（不裝 nginx；最快上線）
#   direct        — uvicorn direct，憑證自備（缺則 fallback 產自簽）

# (A) nginx + 暫用自簽（之後 cp 正式憑證即可）— 推薦生產環境
sudo ./scripts/install-debian.sh --tls-mode nginx --public-fqdn ipam.example.com

# (B) uvicorn direct 自簽（內網/開發環境最快）
sudo ./scripts/install-debian.sh --tls-mode self-signed --public-fqdn ipam.local
```

腳本會：

1. 安裝 PostgreSQL 16 + pgvector + Redis 7（Ubuntu 22.04 自動加 PGDG repo；Ubuntu 24.04 直接走官方源）
2. 建立 `jtipam` 系統使用者、`/opt/jt-ipam/backend/.venv`、安裝 Python deps
3. 建立 DB role/database `jt_ipam`，套 alembic migrations
4. 產生 `SECRET_KEY` / `ENCRYPTION_KEY` / `AUDIT_CHAIN_GENESIS` 寫入 `/etc/jt-ipam/backend.env`（0640）
5. 在 `/etc/jt-ipam/tls/` 產自簽憑證 ECDSA P-384 / 5 年；SAN 自動含 `localhost / FQDN / 短 hostname / 127.0.0.1 / ::1 / 主機 IP`
6. pnpm install + 前端 vite build
7. 安裝 `jt-ipam-backend.service` + `jt-ipam-sync.timer`（每 5 分鐘）+ `jt-ipam-backup.timer`（每天 03:30）
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

# 確認新憑證生效（direct 模式 backend 直接聽 443）
openssl s_client -connect ipam.example.com:443 -servername ipam.example.com </dev/null 2>/dev/null \
    | openssl x509 -noout -issuer -subject -dates
```

> 想自己重新產自簽憑證：`sudo bash /opt/jt-ipam/scripts/generate-self-signed-cert.sh` 後 `systemctl restart jt-ipam-backend`。
> 從 direct 改走 nginx 反代：把 `/etc/jt-ipam/backend.env` 的 `BACKEND_TLS_MODE` 改成 `nginx`、裝好 nginx site，再重啟 backend + reload nginx。

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
| `OUTBOUND_ALLOW_CIDRS` | — | safe_http SSRF allowlist；空白 = 只允公網 |
| `OIDC_*` | — | 啟用 OIDC SSO |
| `SAML_*` | — | 啟用 SAML SSO |
| `LDAP_*` | — | LDAP/AD 認證 |
| `OLLAMA_ENABLED` | — | 開啟 AI 語意搜尋 + chat |

完整列表見 `app/core/config.py` Settings class。

---

## 4. 整合設定（裝完後）

所有整合都在 admin 介面 (`/firewall`、`/wazuh`、`/librenms`、`/dns`) 加主機。
新增後預設每 5 分鐘由 `jt-ipam-sync.timer` 自動同步。

### OPNsense 防火牆

1. OPNsense → System → Access → Users → 加 service user → 拿 API key/secret
2. jt-ipam → 防火牆 → 新增 → 填 `https://opnsense:443`、key、secret
3. 加 alias mapping（selector JSON 例：`{"type":"section","section_id":"<uuid>"}`）

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

### 自動備份

安裝腳本不會啟用備份；要手動加 cron 或 systemd timer。最簡單：

```bash
sudo cp /opt/jt-ipam/scripts/jt-ipam-backup.sh /usr/local/bin/
sudo install -m 0644 /opt/jt-ipam/deploy/systemd/jt-ipam-backup.{service,timer} \
    /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now jt-ipam-backup.timer
```

預設每天 03:30 跑，把 `pg_dump -Fc` + `/etc/jt-ipam/backend.env` + TLS 憑證
打包到 `/var/backups/jt-ipam/`，保留 14 天。

### 異地備份

把 `/var/backups/jt-ipam/` rsync 到 NAS / S3 / 另一台機器：

```bash
# 例：每天 04:00 推到 NAS
0 4 * * * rsync -a /var/backups/jt-ipam/ jtipam@nas.local:/backups/jt-ipam/
```

### 還原

```bash
# 0. 停服務
sudo systemctl stop jt-ipam-backend jt-ipam-sync.timer

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
    /var/backups/jt-ipam/jt-ipam-2026-05-10.dump

# 3. 還原設定檔（如果還在）
sudo cp /var/backups/jt-ipam/2026-05-10/backend.env /etc/jt-ipam/

# 4. 啟動
sudo systemctl start jt-ipam-backend jt-ipam-sync.timer

# 5. 驗 chain（任何 row 被竄改會立刻看到）
curl -X POST https://ipam.example.com/api/v1/audit/verify \
    -H "Authorization: Bearer <admin token>"
```

>  備份檔內含敏感資料（DB 含加密的 API 憑證；env 含 SECRET_KEY/ENCRYPTION_KEY）。
> 必須以 `0600` 權限儲存，並做加密傳輸（rsync over ssh / s3 server-side encryption）。

---

## 6. 升級

```bash
cd /opt/jt-ipam
git pull
sudo systemctl stop jt-ipam-backend
sudo -u jtipam /opt/jt-ipam/backend/.venv/bin/pip install -e backend
sudo -u jtipam /opt/jt-ipam/backend/.venv/bin/alembic -c backend/alembic.ini upgrade head
cd frontend && sudo -u jtipam pnpm install --frozen-lockfile && sudo -u jtipam pnpm run build
sudo systemctl start jt-ipam-backend
```

---

## 7. 監控與告警

### Journal 觀察

```bash
journalctl -u jt-ipam-backend -f          # backend
journalctl -u jt-ipam-sync -n 200          # 定期同步
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

```bash
sudo systemctl disable --now jt-ipam-backend jt-ipam-sync.timer jt-ipam-backup.timer
sudo rm /etc/systemd/system/jt-ipam-{backend,sync,backup}.{service,timer}
sudo rm -rf /opt/jt-ipam /etc/jt-ipam /var/log/jt-ipam /var/lib/jt-ipam
sudo -u postgres dropdb jt_ipam
sudo -u postgres dropuser jt_ipam
sudo userdel -r jtipam
```

備份（`/var/backups/jt-ipam/`）需自行決定是否保留。

---

## 9. 常見問題

**Q: backend 起不來，journal 顯示 "ENCRYPTION_KEY: invalid format"？**
A: ENCRYPTION_KEY 必須是 32-byte 的 base64（44 字元結尾 `=`）。安裝腳本有產；
若手動設定，用 `python -c 'import base64,os; print(base64.b64encode(os.urandom(32)).decode())'`。

**Q: backend OOM？**
A: argon2id 預設 64 MiB / 4 parallelism；若 RAM 緊（< 2GB），可降 `ARGON2_MEMORY_COST_KIB=32768`。

**Q: nginx 502？**
A: backend bind 預設 `127.0.0.1:8000`；確認 systemctl status jt-ipam-backend
是 active，且 nginx site 的 upstream 也指 `127.0.0.1:8000`。

**Q: pgvector 找不到？**
A: Ubuntu 22.04 沒內建；安裝腳本會自動加 PGDG repo + `postgresql-16-pgvector`。
手動補：`sudo apt install postgresql-16-pgvector` 後 `CREATE EXTENSION vector;`。

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

# 方案 B：原 admin 已鎖死 / 失聯 — 直接改 DB 把它解鎖並重設密碼
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

**Q: Ubuntu 24.04 跑前端 build 出現 `Unsupported engine: wanted Node >= 20`？**
A: 24.04 內建 nodejs 18，vite 6 / vue-tsc 跑得動但有警告。要消警告：用 nvm / nodesource 安裝 Node 20+：
```bash
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo bash -
sudo apt install -y nodejs
```
然後在 `/opt/jt-ipam/frontend` 重 `pnpm install && pnpm build`。
