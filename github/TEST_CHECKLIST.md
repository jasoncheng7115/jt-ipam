# jt-ipam 升版測試清單

> 規矩：**每次 bump `frontend/package.json` 的 version 之前，先把這份清單跑過一輪，全綠才升版。**
> CI 目前沒跑驗證，所以靠這份手動把關。紅的先修，不要帶病升版。

升版流程：跑清單 → 全綠 → 改 version → 部署（backend rsync + alembic + restart；frontend build）。

---

## 1. 靜態檢查（dev 機，免 DB，最快）

- [ ] 後端可被 import：`cd backend && set -a; source <env>; set +a; .venv/bin/python -c "import app.main"`
- [ ] 後端 pytest 收集無 error（DB 測試會 skip）：`.venv/bin/pytest -q`
- [ ] 前端型別：`cd frontend && npx vue-tsc --noEmit`（必須零錯誤）
- [ ] 前端 build：`npm run build`（成功產生 dist）
- [ ] i18n：這次新增的 key 在 `zh-TW.json` 與 `en-US.json` 都有；無寫死中文漏網

## 2. 資料庫 / Migration（用拋棄式 test DB，勿碰正式資料）

- [ ] 全新 DB 從 0001 升到 head 無誤：對 `jt_ipam_test` 跑 `alembic upgrade head`
- [ ] 這次新增的 migration 有 `downgrade()` 且能 `alembic downgrade -1` 再 `upgrade head` 來回一次
- [ ] 沒有「model 改了但忘了 migration」：升完 head 後 app 啟動不報 asyncpg「column does not exist」

## 3. 後端整合測試（test DB + pytest，全面）

- [ ] 設 `JTIPAM_TEST_DATABASE_URL` 後 `.venv/bin/pytest -q` 全綠（e2e CRUD / auth / 各模組）
- [ ] 認證：登入、refresh、TOTP、權限（require_admin 的端點未授權回 401/403）
- [ ] 核心 CRUD：sections / subnets / addresses / devices / customers / locations / racks
- [ ] 稽核鏈：寫入操作有 audit、鏈完整性驗證過

## 4. 關鍵 API smoke（部署後對 prod 打，唯讀為主）

- [ ] `GET /api/v1/health`（或 `/notifications`）200
- [ ] `GET /api/v1/subnets`、`/addresses`、`/devices`、`/locations`、`/racks` 200
- [ ] 這次動到的端點：手動打一次成功路徑 + 一個失敗路徑（驗證 4xx 正確）

## 5. OWASP Top 10:2025 心智檢查（這次動到的模組）

- [ ] A01 權限：新端點有沒有正確 require_admin / 物件層級授權？
- [ ] A03 注入 / 輸入驗證：Pydantic StrictModel、檔案上傳驗 magic bytes + 限大小 + 禁危險類型（如 SVG）
- [ ] A08 完整性：上傳/外部資料有驗證；路徑無 traversal（上傳/下載檔案路徑解析後仍在白名單目錄內）
- [ ] 機密：無把 secret/token 寫進 log 或回應

## 5b. 部署腳本流程（拋棄式環境，**勿在 dev/prod 跑 install**）

- [ ] **全新安裝**：乾淨 LXC/VM 跑 `scripts/install-debian.sh`，裝完服務起得來、能登入
- [ ] **舊版升級**：對上一版的環境跑 `scripts/jt-ipam-upgrade.sh`，升完正常、必要時可回滾
- [ ] 這次若新增了目錄 / 套件 / 服務 / DB extension / env，確認**兩支腳本都已同步**

## 5c. headless 瀏覽器冒煙

- [ ] `cd frontend && pnpm exec playwright test smoke`（免後端，自起 vite preview）全綠
- [ ] 對已部署實例（給 `E2E_BASE_URL` + `E2E_ADMIN_PASS`）跑 `pnpm test:e2e` 主路徑（登入/sections/audit）

## 6. 主要頁面手動點檢（部署後瀏覽器）

- [ ] 登入 / 登出 / 主題切換（淺/深/自動）
- [ ] 子網路：列表、樹狀、IP 清單（含閒置區間列跨欄位）、編輯
- [ ] 裝置 / 機櫃：排序（IP 自然序）、操作鈕高度一致、機房平面圖上傳+拖拉定位+點選
- [ ] 拓樸圖：節點/連線、VPN 對接連線、圖例
- [ ] 掃描代理 / 同步作業：頁面正常、無 console error

---

### 附：拋棄式 test DB 指令（在 prod 主機，**不碰正式 DB**）

```bash
set -a; source /etc/jt-ipam/backend.env; set +a
sudo -u postgres psql -c "DROP DATABASE IF EXISTS jt_ipam_test;"
sudo -u postgres psql -c "CREATE DATABASE jt_ipam_test OWNER ${POSTGRES_USER} ENCODING UTF8 TEMPLATE template0;"
sudo -u postgres psql -d jt_ipam_test -c "CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pg_trgm;"
cd /opt/jt-ipam/backend
POSTGRES_DB=jt_ipam_test .venv/bin/alembic upgrade head
JTIPAM_TEST_DATABASE_URL="postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/jt_ipam_test" .venv/bin/pytest -q
sudo -u postgres psql -c "DROP DATABASE IF EXISTS jt_ipam_test;"
```
