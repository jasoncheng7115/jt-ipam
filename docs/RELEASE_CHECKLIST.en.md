# jt-ipam Release / Deploy Checklist

> 繁體中文版：[RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md)

Walk through this list every time you "prepare to commit", "prepare to tag a release", or "prepare to deploy to production". **No checkmark, no ship.**

---

## Before commit (commit / PR)

### Code

- [ ] **Backend pytest all green**
  ```bash
  cd backend && sudo -u jtipam env $(grep -v '^#' /etc/jt-ipam/backend.env | xargs) \
      JTIPAM_TEST_DATABASE_URL="postgresql+asyncpg://jt_ipam:$(cat /etc/jt-ipam/.db-password)@127.0.0.1:5432/jt_ipam_test" \
      .venv/bin/python -m pytest tests/ -q
  ```
  Target: 124+ passed.
- [ ] **Backend lint**: `ruff check backend/` and `mypy backend/app/` (CI checks too)
- [ ] **Backend SAST**: `bandit -r backend/app/ -c backend/pyproject.toml`
- [ ] **Frontend build**: `cd frontend && pnpm build` must not break
- [ ] **Frontend lint**: `cd frontend && pnpm lint`
- [ ] **Frontend vitest**: `pnpm test:unit` (if api/ or utils/ changed)
- [ ] **Playwright e2e** (if a view or router changed): `pnpm test:e2e`

### Design — OWASP Top 10:2025

Ask yourself these 11 items before committing (the docs/SECURITY.md §11 self-review checklist):

- [ ] **A01** Access Control: new endpoint guarded with `require_admin` or `require_permission`? Bulk ops checked per-item?
- [ ] **A02** Misconfiguration: debug not in prod? CORS / TLS / systemd hardening not regressed?
- [ ] **A03** Supply Chain: new deps pinned? CI audit passing? Actions pinned to SHA?
- [ ] **A04** Crypto: new sensitive field AES-GCM encrypted? aad bound to instance id? Passwords never logged?
- [ ] **A05** Injection: input validated by Pydantic? No string-built SQL / commands?
- [ ] **A06** Insecure Design: DB constraint as a backstop? Rate limit needed? External URLs go through `safe_http`?
- [ ] **A07** Auth: lockout / MFA / session rotation / anti-enumeration intact?
- [ ] **A08** Integrity: audit log written? diff complete? outbound webhooks signed? **chain verify unbroken?**
- [ ] **A09** Logging & Alerting: request_id present? sensitive fields redacted? failures alerted?
- [ ] **A10** Mishandling of Exceptional Conditions: try/except writes audit? fail-closed? races locked? timeouts mandatory?

### Schema / Migration

- [ ] Model change has a matching alembic migration?
- [ ] Migration runs on a clean DB? `alembic upgrade head` + `alembic downgrade -1` both OK?
- [ ] New sensitive column uses the `LargeBinary` two-column form (`*_enc + *_nonce`), not plaintext?

### Commit message

- [ ] Explains the "why", not just the "what"? Format: `<type>(<scope>): <subject>`
- [ ] If fixing a bug: matching regression test added?

---

## Before a Release (git tag)

- [ ] All of the above checked
- [ ] `git log --oneline <last-tag>..HEAD` reviewed — no stray non-essential commits
- [ ] `docs/SPEC.md` phase-progress section updated
- [ ] `README.md` mentions no non-existent features
- [ ] **Smoke test locally**: `./scripts/smoke-test.sh https://localhost`
- [ ] **Chain verify locally**: `curl -kfsS -X POST .../audit/verify` returns `ok: true`
- [ ] `systemd-analyze security jt-ipam-backend` ≤ 3.5
- [ ] Any breaking change? Documented in INSTALL.md "Upgrade" section?
- [ ] New env var added? Listed in the INSTALL.md §3 table?

---

## Before deploying to Production

- [ ] Target host OS apt update + upgrade + rebooted
- [ ] **DB backup done** (don't rely on the 03:30 cron — run `jt-ipam-backup.sh` manually once more)
- [ ] Backup file readable (`pg_restore --list` shows the listing)
- [ ] Current production chain verify still ok (don't upgrade carrying a broken chain)
- [ ] Rollback plan ready (know which git tag to revert to)
- [ ] **Maintenance window announced** (if there are users)

---

## After deploying to Production (mandatory)

- [ ] `systemctl is-active jt-ipam-backend nginx postgresql redis-server` all active
- [ ] `systemctl is-enabled jt-ipam-sync.timer jt-ipam-backup.timer` all enabled
- [ ] `./scripts/smoke-test.sh https://<your-fqdn>` all green
- [ ] `curl -kfsS -X POST .../api/v1/audit/verify` returns `{"ok": true, ...}` ← **A08 chain unbroken**
- [ ] `journalctl -u jt-ipam-backend --since "5 minutes ago" -p err` shows no errors
- [ ] `systemd-analyze security jt-ipam-backend` ≤ 3.5
- [ ] Spot-check an admin endpoint (e.g. `/api/v1/users`) with an admin token
- [ ] Spot-check an integration: click a sync button, confirm `last_sync_at` updates and `last_error` is null

### Follow up after 24h

- [ ] `journalctl --since "24 hours ago" -u jt-ipam-sync` has no repeated errors
- [ ] `journalctl --since "24 hours ago" -u jt-ipam-backup` exit 0
- [ ] `ls /var/backups/jt-ipam/` has a fresh day's dump
- [ ] audit_logs row count reasonable (people are using it)
- [ ] Spot-check the audit chain is still ok

---

## Rollback SOP when things break

```bash
# 1. Immediately revert to the previous tag (code)
cd /opt/jt-ipam && sudo -u jtipam git fetch && sudo -u jtipam git checkout <last-good-tag>

# 2. Restore the DB (if there was a migration)
sudo systemctl stop jt-ipam-backend
sudo -u jtipam /opt/jt-ipam/backend/.venv/bin/alembic -c backend/alembic.ini downgrade <prev-revision>
# or: full restore from the pre-deploy pg_dump (see INSTALL.md §5 "Restore")

# 3. Restart
sudo systemctl start jt-ipam-backend

# 4. Verify
./scripts/smoke-test.sh https://<your-fqdn>
```

---

## KPIs (tracked but non-blocking for release)

| Metric | Target | How to check |
|---|---|---|
| backend test coverage | ≥ 70% | `pytest --cov=app tests/` |
| frontend test coverage | ≥ 40% | `pnpm test:unit --coverage` |
| systemd security | ≤ 3.5 | `systemd-analyze security jt-ipam-backend` |
| audit chain | always ok | daily cron `/audit/verify` monitor |
| p95 latency (reads) | < 200ms | nginx access log stats |
| backup size growth | linear | `ls -lh /var/backups/jt-ipam/` |
