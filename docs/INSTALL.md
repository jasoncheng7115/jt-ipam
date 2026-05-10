# jt-ipam 安裝與運維 SOP

針對 **Proxmox LXC、裸機、虛擬機**（Ubuntu 22.04+/Debian 12+）。本專案
**不使用 Docker**；以 systemd + apt 直裝。

> 安全為 day-one 需求：所有環境強制 HTTPS；憑證可走 nginx 反代或
> uvicorn 直接吃自簽。SSL 沒設好 backend **不會啟動**（A02）。

---

## 1. 系統需求

| 項目 | 最低 | 建議 |
|---|---|---|
| OS | Ubuntu 22.04 / Debian 12 | Ubuntu 24.04 LTS |
| CPU | 2 vCPU | 4 vCPU |
| RAM | 2 GB | 8 GB（含 pgvector + Ollama） |
| Disk | 20 GB | 50 GB（audit log 累積）|
| Python | 3.11 | 3.12 / 3.13 |
| PostgreSQL | 16 + pgvector | — |
| Redis | 7 | — |

---

## 2. 一鍵安裝

```bash
# clone
git clone https://github.com/jasontools/jt-ipam.git /opt/jt-ipam
cd /opt/jt-ipam

# 任選一種 TLS 模式：

# (A) nginx 反代 + Let's Encrypt（建議生產環境）
sudo ./scripts/install-debian.sh --tls-mode nginx --public-fqdn ipam.example.com

# (B) uvicorn direct + 自簽憑證（內網/開發）
sudo ./scripts/install-debian.sh --tls-mode self-signed --public-fqdn ipam.local
```

腳本會：

1. 安裝 PostgreSQL 16（自動加 PGDG repo if needed）+ pgvector + Redis 7
2. 建立 `jtipam` 系統使用者、`/opt/jt-ipam/.venv`、安裝 Python deps
3. 建立 DB role/database `jt_ipam`，套 alembic migrations
4. 自簽 / 取憑證
5. 安裝 `jt-ipam-backend.service` + `jt-ipam-sync.timer`（5 分鐘定期同步）
6. 設定 nginx site 並啟用

完成後：

```bash
# 第一次 bootstrap admin 帳號
sudo -u jtipam /opt/jt-ipam/backend/.venv/bin/python \
    -m app.scripts.bootstrap_admin --username admin --email admin@your.domain
```

開啟瀏覽器到 `https://<your-fqdn>/` 即可登入。

---

## 3. 環境變數

主要設定檔：`/etc/jt-ipam/backend.env`（root:jtipam 0640）

| 變數 | 必填 | 說明 |
|---|---|---|
| `SECRET_KEY` | ✓ | JWT 簽章；安裝腳本自動產 64-byte hex |
| `ENCRYPTION_KEY` | ✓ | AES-256-GCM key（DNS/SNMP/API 憑證加密）|
| `AUDIT_CHAIN_GENESIS` | ✓ | SHA-256 鏈起始；**永不可改**（A08）|
| `POSTGRES_*` | ✓ | DB 連線 |
| `REDIS_PASSWORD` | ✓ | rate limiter / cache |
| `BACKEND_TLS_MODE` | ✓ | `nginx` 或 `direct` |
| `APP_PUBLIC_URL` | ✓ | 前端 base URL |
| `API_PUBLIC_URL` | ✓ | OIDC/SAML callback 用 |
| `CORS_ORIGINS` | ✓ | 多個用逗號分隔 |
| `OUTBOUND_ALLOW_CIDRS` | — | safe_http SSRF allowlist；空白 = 只允公網 |
| `OIDC_*` | — | 啟用 OIDC SSO |
| `SAML_*` | — | 啟用 SAML SSO |
| `LDAP_*` | — | LDAP/AD 認證 |
| `OLLAMA_ENABLED` | — | 開啟 AI 語意搜尋 + chat |

完整列表見 `app/core/config.py` Settings class。

---

## 4. 整合配置（裝完後）

所有整合都在 admin 介面 (`/firewall`、`/wazuh`、`/librenms`、`/dns`) 加實例。
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

> ⚠️ 備份檔內含敏感資料（DB 含加密的 API 憑證；env 含 SECRET_KEY/ENCRYPTION_KEY）。
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
