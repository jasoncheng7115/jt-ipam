# jt-ipam

> 新世代 IPAM — 以 phpIPAM 操作邏輯為核心，整合多家 DNS Server、LibreNMS 與本地 AI。
>
> 作者：Jason Tools Co., Ltd.（節省工具箱）｜授權：AGPL-3.0｜版本：v0.3 (Phase 1 in progress)

---

## 為什麼是 jt-ipam？

phpIPAM 老用戶零學習成本，現代化技術解決效能/UI/API 的歷史包袱。深度整合：

- **DNS**：PowerDNS、BIND 9、OPNsense Unbound、Microsoft Windows DNS（雙向同步）
- **LibreNMS**：裝置同步、ARP / FDB 抓取、在線狀態互補、自動加入監控
- **Jason 開源生態系**：Proxmox VE、Wazuh、Graylog、OPNsense、Zimbra、Odoo
- **本地 AI**：Ollama、自然語言查詢、語意搜尋（資料不外送）

完整規格詳見 [`docs/SPEC.md`](docs/SPEC.md)。

---

## 安全（OWASP Top 10:2025）

本專案安全是 day-one 需求，所有設計與實作對齊 **OWASP Top 10:2025**。詳見 [`docs/SECURITY.md`](docs/SECURITY.md)。

重點：
- **TLS 強制**：兩模式擇一 — nginx 反代終結（`BACKEND_TLS_MODE=nginx`）或 uvicorn 直接吃自簽（`BACKEND_TLS_MODE=direct`）
- A01 RBAC：deny-by-default、Per-Section/Subnet 權限、物件級檢查
- A02 加密：argon2id 密碼、應用層加密儲存敏感欄位（DNS 帳密 / SNMP / API token）
- A03 注入防護：SQLAlchemy 參數化、Pydantic 嚴格驗證、CSP/輸出跳脫
- A05 安全 Headers：HSTS、CSP、X-Frame-Options、Referrer-Policy
- A07 認證：TOTP MFA、帳號鎖定、Cookie HttpOnly+Secure+SameSite、API Token TTL
- A08 完整性：SHA-256 異動鏈
- A09 監控：結構化稽核日誌外送 Graylog
- A10 SSRF：外部整合 URL 白名單、阻擋 metadata/link-local

---

## 技術堆疊

| 層級 | 選型 |
|------|------|
| 後端 | Python 3.12 · FastAPI · SQLAlchemy 2.0 (async) · Alembic · Pydantic v2 |
| 資料庫 | PostgreSQL 16（原生 inet/cidr/macaddr） |
| 快取 / 佇列 | Redis 7 · RQ / Celery |
| 前端 | Vue 3 · TypeScript · Vite · Naive UI · Pinia · vue-i18n |
| 認證 | argon2id · TOTP · JWT (短) + refresh · OIDC / SAML / LDAP / Radius |
| 部署 | systemd + nginx + apt 套件（Proxmox LXC 友善），不採容器化 |
| AI | Ollama（本地）· pgvector · MCP Server |

---

## 快速安裝（單機 / Proxmox LXC）

> Debian 12 / Ubuntu 22.04+，2 vCPU / 4 GB RAM 起跳。**TLS 強制**，二擇一：

```bash
git clone https://github.com/jasontools/jt-ipam.git /opt/jt-ipam
cd /opt/jt-ipam

# 模式 A：nginx 反代 HTTPS（建議；公開服務）
sudo ./scripts/install-debian.sh \
    --tls-mode nginx \
    --public-fqdn ipam.your-domain.tld
# 之後 sudo certbot --nginx -d ipam.your-domain.tld 取得憑證

# 模式 B：後端 uvicorn 直接吃自簽憑證（極簡 / 內網）
sudo ./scripts/install-debian.sh \
    --tls-mode self-signed \
    --public-fqdn ipam.local \
    --bind-port 8443
```

腳本會自動：apt 安裝 `postgresql-16` / `redis-server` / `python3.12` / `nginx`*，建立 `jtipam` 系統帳號、PG role、Redis password、自動產生金鑰寫入 `/etc/jt-ipam/backend.env`、跑 `alembic upgrade head`、`pnpm build` 前端，並啟用 `jt-ipam-backend.service`。

> *nginx 僅模式 A 安裝；模式 B 由 uvicorn 直接終結 TLS。

完成後：

```bash
systemctl status jt-ipam-backend
# 模式 A
curl -fsS http://127.0.0.1:8000/healthz
# 模式 B
curl -fsSk https://127.0.0.1:8443/healthz
```

詳見 [`deploy/README.md`](deploy/README.md)（含兩種 TLS 模式、升級、備份、HA、Proxmox LXC 範本）。

---

## 開發模式（無容器）

```bash
# 前置：本機已有 PostgreSQL 16 + Redis 7
cp backend/.env.example backend/.env   # 編輯，產生金鑰
#   openssl rand -hex 64    # SECRET_KEY / AUDIT_CHAIN_GENESIS
#   openssl rand -base64 32 # ENCRYPTION_KEY

./scripts/dev.sh setup                  # venv + deps + alembic
./scripts/dev.sh up                     # backend (8000) + frontend (5173)
```

子命令：

```bash
./scripts/dev.sh migrate revision --autogenerate -m "msg"
./scripts/dev.sh test
```

---

## 專案結構

```
jt-ipam/
├── docs/              # 規格、安全、資料模型、API 對照
├── backend/           # FastAPI 應用
│   └── app/
│       ├── core/      # config / db / security / audit / middleware
│       ├── models/    # SQLAlchemy 2.0
│       ├── schemas/   # Pydantic v2
│       ├── api/v1/    # 現代 REST API
│       ├── api/phpipam/ # phpIPAM v1.7 相容層
│       └── services/  # 業務邏輯
├── frontend/          # Vue 3 + TS
│   └── src/
│       ├── views/     # 頁面
│       ├── components/layout/ # 樹狀導航 / 頂部列
│       ├── i18n/      # zh-TW / en-US
│       ├── stores/    # Pinia
│       └── api/       # API client
└── docker-compose.yml
```

---

## 開發路線圖（v0.3 落地狀態）

- **Phase 1 ✅**：phpIPAM 等價 + 升級（Section/Subnet/IP/VLAN/VRF/NAT/Devices/Racks/Locations/IP Requests、TOTP/API Token/RBAC、phpIPAM 同步工具、CSV/RIPE/TWNIC、視覺方塊、Tools、強制 SSL）
- **Phase 2 ✅**：DNS 多家（PowerDNS/BIND9/Unbound/WinDNS）+ LibreNMS 深度整合（裝置/ARP/FDB/effective_status）+ 異常偵測 + SHA-256 異動鏈 + GraphQL + pgvector AI 語意搜尋
- **Phase 3 ✅**：Tenancy/Contacts/ASN/Circuits/Wireless/Cabling/Power/VPN/Virtualization + Proxmox 同步 + Cytoscape.js 拓樸 + OIDC SSO + SAML 2.0 SSO + OPNsense 防火牆 alias 同步 + Wazuh agent inventory（missing-agent 偵測）
- **Phase 4 ✅**（縮減版）：MCP Server + 本地 LLM 自然語言（Ollama chat） + Plugin 機制

**明確不做（out of scope）**：HA 部署、Ansible Collection、Terraform Provider、Zimbra/Odoo 整合、Docker/Helm/K8s 容器化。

---

## 貢獻

1. 每個 PR 都要過一次 `docs/SECURITY.md` 的 OWASP Top 10:2025 心智檢查清單
2. Backend 跑 `ruff`、`mypy`、`pytest`、`pip-audit`
3. Frontend 跑 `pnpm lint`、`pnpm typecheck`、`pnpm test`、`pnpm audit`
4. 異動敏感檔（auth / crypto / SSRF / migration）需另行 review

---

## 授權

AGPL-3.0｜商業支援請聯繫 Jason Tools。
