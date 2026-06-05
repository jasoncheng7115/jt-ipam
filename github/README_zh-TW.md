# jt-ipam（繁體中文）

**🌐 [專案介紹網站 / Project site →](https://jasoncheng7115.github.io/jt-ipam/)**

> 可自架、以整合為核心的 IPAM — 操作流程沿襲 phpIPAM 使用者熟悉的風格、全新獨立開發，整合多家 DNS Server、LibreNMS、OPNsense、Proxmox VE、Wazuh 與本地 AI。
>
> 作者：Jason Tools Co., Ltd.（節省工具箱）｜授權：Apache-2.0｜English: [README.md](README.md)

---

## 為什麼是 jt-ipam？

phpIPAM 老使用者幾乎零學習成本；以現代技術全新打造（非基於 phpIPAM 程式碼）。深度整合：

- **DNS**：PowerDNS、BIND 9、OPNsense Unbound、Univention UCS、Microsoft Windows DNS（雙向同步）
- **LibreNMS**：裝置同步、ARP / FDB 抓取、上線狀態互補、自動加入監控
- **基礎設施**：Proxmox VE、Wazuh、OPNsense
- **Graylog**：提供 IP→主機名稱/FQDN 的 DSV 對照表端點，供 Graylog「DSV File from HTTP」資料配接器抓取
- **本地 AI**：Ollama 自然語言查詢 + 語意搜尋（資料不外送），並提供 MCP server（stdio / Streamable HTTP）；實測搭配 `gemma4:26b` 效果良好

完整規格見 [`docs/SPEC.md`](docs/SPEC.md)。

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

- 在 Graylog 的「DSV File from HTTP」配接器：URL 填上方網址、分隔符依格式選逗號或 Tab、**Key column = 1、Value column = 2**
- token 逐次驗證、可隨時重新產生；設定頁直接提供可複製的完整對照表網址

## 權限（RBAC）

物件級權限，支援 7 種物件類型（單位 / 區段 / 子網路 / IP / 裝置 / 機櫃 / 地點），階層繼承（授權上層自動涵蓋下層）、「全部」wildcard、5 個內建角色（系統管理員 / 唯讀檢視者 / 網路操作員 / 稽核員 / 部門管理員）。清單、搜尋、拓樸圖、下拉選單都依可見範圍過濾。

## 安全（OWASP Top 10:2025）

安全是 day-one 需求，所有設計對齊 **OWASP Top 10:2025**，詳見 [`docs/SECURITY.md`](docs/SECURITY.md)。強制 TLS（nginx 反代或 uvicorn 自簽二擇一）、argon2id + TOTP、敏感欄位應用層加密、SHA-256 稽核鏈、SSRF 白名單。

## 技術堆疊

後端 FastAPI + SQLAlchemy 2.0(async) + PostgreSQL 16 + Alembic + Pydantic v2；前端 Vue 3 + TypeScript + Naive UI + Pinia；本地 AI 走 Ollama + pgvector。**不使用容器**：systemd + apt（適合 Proxmox VE LXC / 裸機）。

## 安裝

```bash
# 一行完成：自動 clone 到 /opt/jt-ipam 並安裝（不必先手動 git）
curl -fsSL https://raw.githubusercontent.com/jasoncheng7115/jt-ipam/main/scripts/bootstrap.sh | sudo bash
```

升級：`sudo bash /opt/jt-ipam/scripts/jt-ipam.sh upgrade`（**腳本內含 `git pull`**，直接跑即可）。詳見 [`docs/INSTALL.md`](docs/INSTALL.md)。

## 授權

Apache-2.0｜商業支援請聯繫 Jason Tools。
