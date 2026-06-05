# jt-ipam 改版 / 發布檢查清單

> English: [RELEASE_CHECKLIST.en.md](RELEASE_CHECKLIST.en.md)

每次「準備 commit」「準備 tag release」「準備 deploy 到 production」都對照這份走一遍。**沒打勾不能上**。

---

##  提交前（commit / PR）

### 程式碼層

- [ ] **Backend pytest 全綠**
  ```bash
  cd backend && sudo -u jtipam env $(grep -v '^#' /etc/jt-ipam/backend.env | xargs) \
      JTIPAM_TEST_DATABASE_URL="postgresql+asyncpg://jt_ipam:$(cat /etc/jt-ipam/.db-password)@127.0.0.1:5432/jt_ipam_test" \
      .venv/bin/python -m pytest tests/ -q
  ```
  目標：124+ passed。
- [ ] **Backend lint**：`ruff check backend/` 與 `mypy backend/app/`（CI 也會檢）
- [ ] **Backend SAST**：`bandit -r backend/app/ -c backend/pyproject.toml`
- [ ] **Frontend build**：`cd frontend && pnpm build` 不能炸
- [ ] **Frontend lint**：`cd frontend && pnpm lint`
- [ ] **Frontend vitest**：`pnpm test:unit`（若改 api/ 或 utils/）
- [ ] **Playwright e2e**（若改 view 或 router）：`pnpm test:e2e`

### 設計層 — OWASP Top 10:2025

提交前自問 11 項（即 docs/SECURITY.md §11 心智清單，照表打勾）：

- [ ] **A01** Access Control：新 endpoint 套了 `require_admin` 或 `require_permission`？批次操作逐筆檢查？
- [ ] **A02** Misconfiguration：debug 沒進 prod？CORS / TLS / systemd hardening 沒退化？
- [ ] **A03** Supply Chain：新依賴鎖版本？CI 過 audit？Actions 釘 SHA？
- [ ] **A04** Crypto：新敏感欄位 AES-GCM 加密？aad 綁 instance id？密碼沒進 log？
- [ ] **A05** Injection：輸入過 Pydantic？沒有字串拼 SQL / 命令？
- [ ] **A06** Insecure Design：DB constraint 兜底？需要 rate limit？外部 URL 走 `safe_http`？
- [ ] **A07** Auth：lockout / MFA / session rotation / anti-enumeration 沒壞？
- [ ] **A08** Integrity：寫了 audit log？diff 完整？webhook 出站有簽章？**chain verify 沒斷？**
- [ ] **A09** Logging & Alerting：有 request_id？敏感欄位 redact？失敗事件有告警？
- [ ] **A10** Mishandling of Exceptional Conditions：try/except 寫 audit？fail-closed？race 有 lock？timeout 必填？

### Schema / Migration

- [ ] 改 model 有對應 alembic migration？
- [ ] migration 在乾淨 DB 跑得起來？`alembic upgrade head` + `alembic downgrade -1` 兩邊都 OK？
- [ ] 敏感新欄位是 `LargeBinary` 雙欄（`*_enc + *_nonce`）不是明文？

### Commit message

- [ ] 寫了「why」不只「what」？格式：`<type>(<scope>): <subject>`
- [ ] 如果 fix bug：對應的 regression test 已加？

---

##  發 Release（git tag）前

- [ ] 上述全部打勾
- [ ] `git log --oneline <last-tag>..HEAD` 看一遍 — 沒有遺漏的非必要 commit
- [ ] `docs/SPEC.md` Phase 進度章節更新到位
- [ ] `README.md` 沒提到不存在的功能
- [ ] **本機跑 smoke test**：`./scripts/smoke-test.sh https://localhost`
- [ ] **本機跑 chain verify**：`curl -kfsS -X POST .../audit/verify` 回 `ok: true`
- [ ] `systemd-analyze security jt-ipam-backend` ≤ 3.5
- [ ] 有 breaking change？INSTALL.md「升級」段有寫到？
- [ ] 增加新環境變數？INSTALL.md §3 表格有列？

---

##  部署到 Production 前

- [ ] 目標機 OS 已 apt update + upgrade + reboot
- [ ] **DB 備份完成**（不是依賴 03:30 cron — 手動再跑一次 `jt-ipam-backup.sh`）
- [ ] backup 檔可讀（pg_restore --list 出列表）
- [ ] 目前生產的 chain verify 還是 ok（沒帶著斷鏈升級）
- [ ] 有 rollback 計畫（git tag 知道從哪退）
- [ ] **公告維護視窗**（如果有使用者）

---

##  部署到 Production 後（必跑）

- [ ] `systemctl is-active jt-ipam-backend nginx postgresql redis-server` 全 active
- [ ] `systemctl is-enabled jt-ipam-sync.timer jt-ipam-backup.timer` 都 enabled
- [ ] `./scripts/smoke-test.sh https://<your-fqdn>` 全綠
- [ ] `curl -kfsS -X POST .../api/v1/audit/verify` 回 `{"ok": true, ...}` ← **A08 chain 沒斷**
- [ ] `journalctl -u jt-ipam-backend --since "5 minutes ago" -p err` 沒錯誤
- [ ] `systemd-analyze security jt-ipam-backend` ≤ 3.5
- [ ] 隨機抽一個 admin endpoint（如 `/api/v1/users`）用 admin token 打通
- [ ] 隨機抽一個整合：手動點 sync 按鈕，看 `last_sync_at` 有更新、`last_error` is null

### 24h 後追蹤

- [ ] `journalctl --since "24 hours ago" -u jt-ipam-sync` 沒連續錯誤
- [ ] `journalctl --since "24 hours ago" -u jt-ipam-backup` exit 0
- [ ] `ls /var/backups/jt-ipam/` 有新一天的 dump
- [ ] audit_logs row 數合理（有人在用）
- [ ] 抽看 audit chain 仍 ok

---

##  出事 rollback SOP

```bash
# 1. 立刻退到上一個 tag（程式碼）
cd /opt/jt-ipam && sudo -u jtipam git fetch && sudo -u jtipam git checkout <last-good-tag>

# 2. 還原 DB（如果有 migration）
sudo systemctl stop jt-ipam-backend
sudo -u jtipam /opt/jt-ipam/backend/.venv/bin/alembic -c backend/alembic.ini downgrade <prev-revision>
# 或：用部署前的 pg_dump 整個還原（見 INSTALL.md §5「還原」段）

# 3. 重啟
sudo systemctl start jt-ipam-backend

# 4. 驗
./scripts/smoke-test.sh https://<your-fqdn>
```

---

##  KPI（追蹤但不阻擋發版）

| 指標 | 目標 | 怎麼看 |
|---|---|---|
| backend test 覆蓋率 | ≥ 70% | `pytest --cov=app tests/` |
| frontend test 覆蓋率 | ≥ 40% | `pnpm test:unit --coverage` |
| systemd security | ≤ 3.5 | `systemd-analyze security jt-ipam-backend` |
| audit chain | 永遠 ok | 每天 cron `/audit/verify` 監控 |
| p95 latency（讀） | < 200ms | nginx access log 統計 |
| backup 大小成長 | 線性 | `ls -lh /var/backups/jt-ipam/` |
