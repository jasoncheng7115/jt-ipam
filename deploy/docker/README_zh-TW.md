# Docker Compose 部署（選用）

> English: [README.md](README.md)

> ⚠️ 這是**次要 / 選用**的部署方式。jt-ipam 主要支援的安裝方式仍是 **systemd + apt**
> （`scripts/jt-ipam.sh install`，見專案 README）。docker-compose 適合快速試用 / 評估，或你本來就以容器為主的環境。

整組會起這些服務：`postgres`（pgvector）、`redis`、`backend`（FastAPI/uvicorn）、`sync`（背景同步迴圈，取代 systemd timer）、`web`（nginx：服務前端 + 反代 `/api`、終結 HTTPS）。

## 快速開始

前置需求：**git** 與 **Docker Engine（含 `docker compose` v2 外掛）**。官方腳本 `get.docker.com` 會同時裝好。
**不要** `apt install docker.io` —— 那個套件**沒有 `docker compose` 子指令**（會出現 `unknown shorthand flag: 'd' in -d`）；
請移除它改用官方腳本：

```bash
sudo apt-get remove -y docker.io docker-compose podman-docker   # 若你裝過這些
sudo apt-get update && sudo apt-get install -y curl git         # get.docker.com 需要 curl
curl -fsSL https://get.docker.com | sudo sh                     # Docker Engine + compose 外掛
docker compose version                                          # 確認 v2 外掛已就緒
```

`gen-env.sh`、`docker-compose.yml` 等檔案都在 repo 的 `deploy/docker/` 內，所以**先 git clone 取得專案**：

```bash
git clone https://github.com/jasoncheng7115/jt-ipam.git
cd jt-ipam/deploy/docker
./gen-env.sh                    # 產生 .env 並填入隨機密鑰（只需一次）
docker compose up -d --build    # 建置映像並啟動
```

開瀏覽器到 `https://localhost`（首次啟動會自動產生自簽憑證；瀏覽器會跳安全警告，自行信任即可）。

- 想換對外網域 / 連接埠：編輯 `.env` 的 `JT_IPAM_SERVER_NAME`、`HTTP_PORT`、`HTTPS_PORT`，並把 `APP_PUBLIC_URL` / `API_PUBLIC_URL` / `CORS_ORIGINS` 一起改成相符的 `https://...`。
- 想用**正式憑證**：把 `server.crt` / `server.key` 放到 `deploy/docker/certs/`（會蓋過自簽）。

### 第一個管理員

`gen-env.sh` 會自動產生一組隨機 `admin` 密碼——**印在它的輸出**、並存進 `.env` 的 `JT_IPAM_ADMIN_PASSWORD`（檔案 0600）。backend 首次啟動就用它建立 `admin` 帳號，可直接登入。**登入後請立即更換。**

想自己指定？在第一次 `up` 之前先改 `.env` 的 `JT_IPAM_ADMIN_PASSWORD`（可一併改 `JT_IPAM_ADMIN_USERNAME` / `JT_IPAM_ADMIN_EMAIL`）。或留空、之後用 CLI 手動建：

```bash
docker compose exec backend python -m app.cli.bootstrap \
  create-admin --username admin --email admin@example.com --password-stdin
# 接著輸入密碼（≥ 12 字元）
```

## 怎麼更新版本

最方便的方式——在本目錄跑：

```bash
./update.sh
```

它會做：`git pull` → `docker compose build`（重建映像）→ `docker compose up -d`（重建容器）。
**資料庫遷移會自動執行**：backend 容器的 entrypoint 每次啟動都會跑 `alembic upgrade head`，所以你不需要手動跑 migration。

等同手動：

```bash
git pull
docker compose build
docker compose up -d
docker compose logs -f backend   # 看遷移 / 啟動記錄
```

> 版本號跟著原始碼走（`backend/app/version.py` / `frontend/package.json`），所以 `git pull` + 重建就是升版。

## 內網／離線主機（外網 build、內網 run）

若目標主機**沒有外網**（連不到 Docker Hub），就在有網路的主機把映像 build 好、帶進內網載入 —— 安裝與升級都適用。

**在有外網的主機** —— 先取得原始碼再 build：

```bash
git clone https://github.com/jasoncheng7115/jt-ipam.git   # 首次（之後要派送新版就 git pull）
cd jt-ipam/deploy/docker
./offline-export.sh            # build + 拉 base 映像 → jt-ipam-images-<sha>.tar.gz
```

這會把四個映像打包成一個壓縮檔：兩個 app 映像（`jt-ipam-backend:local`、`jt-ipam-web:local`）**加上** base 映像
（`pgvector/pgvector:pg16`、`redis:7-alpine`）—— 內網主機拉不到 base，所以一起帶過去。

**帶到內網主機：** `jt-ipam-images-*.tar.gz` 壓縮檔 **與 jt-ipam repo 資料夾**（compose 仍需要 build-context 路徑存在，
即使在內網不會重建）。

**在內網主機**（在 `deploy/docker/` 下）：

```bash
./gen-env.sh                          # 僅首次安裝（需要 openssl，不需外網）
./offline-import.sh jt-ipam-images-<sha>.tar.gz
```

`offline-import.sh` 會 `docker load` 後跑 `docker compose up -d --no-build --pull never`，所以**只**用壓縮檔裡的映像
—— 不 build、不 pull。資料庫遷移一樣在 backend 啟動時自動跑。

**升級內網主機：** 在外網主機重跑 `./offline-export.sh`（先 `git pull`），把新的壓縮檔複製過去，再跑一次
`./offline-import.sh <新壓縮檔>` 即可，`.env` 不動。

## 常用指令

```bash
docker compose ps                  # 看狀態
docker compose logs -f backend     # 後端記錄（含遷移）
docker compose down                # 停止（保留資料 volume）
docker compose down -v             # 停止並刪除資料（⚠️ 連 DB 一起刪）
```

## 備份

資料都在 `pgdata` volume，備份範例：

```bash
docker compose exec -T postgres pg_dump -U jt_ipam jt_ipam | gzip > jt-ipam-$(date +%F).sql.gz
```

## 與 systemd 版的差異 / 注意事項

- 背景同步用 `sync` 服務的迴圈取代 `jt-ipam-sync.timer`（間隔由 `.env` 的 `SYNC_INTERVAL_SECONDS` 控制）。
- 未包含 Graylog DSV 的明文 8088 埠（容器內建議直接用 HTTPS 那條 DSV 網址）。
- GeoIP / OUI 等排程更新未內建，需要可自行加 cron 或另跑。
- 強制 HTTPS：app 會送 Secure cookie、公開 URL 為 https，請勿改成純 http 對外。
