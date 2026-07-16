# jt-ipam 從 0.4 升級到最新版

> English: [UPGRADE_FROM_0.4.md](UPGRADE_FROM_0.4.md)

這是把舊的 **0.4.x** 安裝升到最新版的操作手冊。**可以跨很多版直升** —— Alembic 會把中間所有 migration
依序跑完，不需要先逐版升到中間版本。

以下路徑假設預設佈局：原始碼在 `/opt/jt-ipam`、設定在 `/etc/jt-ipam/backend.env`、系統帳號 `jtipam`。
全程以 `root` / `sudo` 執行。

> 別把「重跑 `install`」當升級捷徑。`install` **不會 `git pull`**，單獨跑只是拿*舊*程式碼再跑一次 Alembic 與
> build，版本升不上去。它對既有主機是安全的（保留 DB、絕不重生 `ENCRYPTION_KEY`），但只有在你**先更新完
> 程式碼（步驟 2）之後**，才能當「修復重裝」用。

---

## 前提檢查

```bash
test -d /opt/jt-ipam/backend/.venv && echo "venv OK"
test -r /etc/jt-ipam/backend.env && echo "env OK"
stat -c '%U' /opt/jt-ipam          # 應為 jtipam
```

---

## 步驟 0 — 先備份（務必做）

```bash
# 資料庫（自訂格式，可用 pg_restore 還原）
sudo -u postgres pg_dump -Fc jt_ipam > /root/jt_ipam_$(date +%F_%H%M).dump

# 設定與上傳檔。backend.env 內含 ENCRYPTION_KEY，遺失會導致所有機密無法解密。
cp -a /etc/jt-ipam /root/etc-jt-ipam.bak
tar czf /root/jt-ipam-uploads_$(date +%F).tgz -C /var/lib/jt-ipam uploads 2>/dev/null || true
```

> 千萬不要更動 `backend.env` 裡的 `ENCRYPTION_KEY` / `SECRET_KEY`，否則整合金鑰、TOTP、憑證私鑰全部失效。
> 升級流程本身不會動它們。

---

## 步驟 1 — 先試「正常路徑」

```bash
sudo /opt/jt-ipam/scripts/jt-ipam.sh upgrade 2>&1 | tee /root/upgrade.log
```

- **成功** -> 直接跳到步驟 6（驗證）。收工。
- **失敗** -> 看 `upgrade.log` 最後約 20 行，確認卡在哪一步（`git pull` / `alembic` / `pip` / `build`），
  再照下面對應的手動步驟走。

手動步驟 2-5 只是把 `upgrade` 流程（`git pull --ff-only` -> 備份 -> pip -> `alembic upgrade head` ->
build -> 重啟）拆開，讓你能跨過卡住的環節。

---

## 步驟 2 — 更新程式碼到最新（處理 git 歷史分岔）

`upgrade` 用 `git pull --ff-only`，舊 0.4 的 clone 若歷史和公開 repo 分岔就會中止。手動對齊：

```bash
sudo -u jtipam git -C /opt/jt-ipam config --global --add safe.directory /opt/jt-ipam
sudo -u jtipam git -C /opt/jt-ipam fetch origin
sudo -u jtipam git -C /opt/jt-ipam reset --hard origin/main   # 客戶設定在 /etc/jt-ipam，不在 repo，安全
sudo -u jtipam git -C /opt/jt-ipam log --oneline -1           # 確認已到最新 commit
```

若 `origin` 指向舊網址或 fetch 失敗，重設 remote：

```bash
sudo -u jtipam git -C /opt/jt-ipam remote set-url origin https://github.com/jasoncheng7115/jt-ipam.git
sudo -u jtipam git -C /opt/jt-ipam fetch origin && sudo -u jtipam git -C /opt/jt-ipam reset --hard origin/main
```

---

## 步驟 3 — 更新後端相依

```bash
sudo -u jtipam bash -c 'cd /opt/jt-ipam/backend && .venv/bin/pip install -e .'
```

若報 `requires a different Python` 或某套件沒有 wheel，多半是 OS/Python 太舊 —— 先把實際錯誤記下來再繼續。

---

## 步驟 4 — 資料庫遷移（0.4 -> 最新的大跳躍，最關鍵）

```bash
# 目前在哪個 revision
sudo -u jtipam bash -c 'cd /opt/jt-ipam/backend; set -a; source /etc/jt-ipam/backend.env; set +a; .venv/bin/alembic current'
# 一路升到 head
sudo -u jtipam bash -c 'cd /opt/jt-ipam/backend; set -a; source /etc/jt-ipam/backend.env; set +a; .venv/bin/alembic upgrade head'
```

常見錯誤：

- **`Can't locate revision <xxxx>`** —— `alembic_version` 指向新版已不存在的 revision。先看鏈、stamp 一個對得上的，
  再重跑 `upgrade head`：
  ```bash
  sudo -u jtipam bash -c 'cd /opt/jt-ipam/backend; .venv/bin/alembic history | head -40'
  # 確認過「與實際資料結構相符」的 revision 後，才：
  # sudo -u jtipam bash -c 'cd /opt/jt-ipam/backend; set -a; source /etc/jt-ipam/backend.env; set +a; .venv/bin/alembic stamp <revision>'
  ```
  `stamp` 只改記錄的版本、不改結構 —— revision 要挑對。
- **某支 migration 在你的資料上失敗**（唯一鍵衝突、型別不符）：記下失敗的 revision 與錯誤，逐支解。有步驟 0 的備份
  就能安全重試。
- **舊庫是 `SQL_ASCII` 編碼**踩到編碼錯誤：先把 DB 轉成 UTF-8（另有流程），再續。

---

## 步驟 5 — 重建前端並重啟

程式碼已是新版，用 `--no-pull` 跑完其餘步驟（pip -> alembic -> **前端 build** -> 重啟，並補上 nginx
WebSocket 設定）：

```bash
sudo /opt/jt-ipam/scripts/jt-ipam.sh upgrade --no-pull
```

若想純手動 build：

```bash
cd /opt/jt-ipam/frontend && sudo npm run build
sudo systemctl restart jt-ipam-backend
```

---

## 步驟 6 — 驗證

```bash
# 版本 + Alembic 已到 head
grep '"version"' /opt/jt-ipam/frontend/package.json
sudo -u jtipam bash -c 'cd /opt/jt-ipam/backend; set -a; source /etc/jt-ipam/backend.env; set +a; .venv/bin/alembic current'

# 服務起來
systemctl is-active jt-ipam-backend
journalctl -u jt-ipam-backend -n 30 --no-pager    # 無 traceback

# API 通
curl -sk https://localhost/api/v1/health || curl -sk https://127.0.0.1:8443/api/v1/health
```

接著用瀏覽器登入（記得 **hard refresh** 清掉舊 JS bundle），抽查：子網路 / IP / 裝置在、整合仍能連線（代表
`ENCRYPTION_KEY` 有保住、機密仍可解）、能登入（含 TOTP）。

---

## 出事怎麼還原

```bash
# 程式碼回舊 commit
sudo -u jtipam git -C /opt/jt-ipam reset --hard <舊commit>
# 資料庫還原
sudo -u postgres pg_restore --clean --no-owner -d jt_ipam /root/jt_ipam_YYYY-MM-DD_HHMM.dump
# 重啟
sudo systemctl restart jt-ipam-backend
```

---

## 重點

- 正常情況只要步驟 1（`jt-ipam.sh upgrade`）一行就好；步驟 2-5 是它卡住時的手動拆解。
- 支援跨版直升 —— 不需要逐版跳到中間版本。
- 重跑 `install.sh` 不是升級手段：它不 `git pull`，只有在程式碼已先更新（步驟 2）後才能當修復用；且它對既有
  env/DB 不具破壞性（不重生金鑰、不 dropdb）。
