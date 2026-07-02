# jt-ipam Release Test Checklist

> zh-TW version: [TEST_CHECKLIST_zh-TW.md](TEST_CHECKLIST_zh-TW.md).

> Rule: **before bumping the `version` in `frontend/package.json`, run this whole
> checklist once; only release when everything is green.**
> Treat it as the manual gate. Fix the red ones first — do not ship sick.

Release flow: run the checklist → all green → bump version → deploy
(backend rsync + alembic + restart; frontend build).

---

## 1. Static checks (dev box, no DB, fastest)

- [ ] Backend imports: `cd backend && set -a; source <env>; set +a; .venv/bin/python -c "import app.main"`
- [ ] Backend pytest collection has no error (DB tests skip): `.venv/bin/pytest -q`
- [ ] Frontend types: `cd frontend && npx vue-tsc --noEmit` (must be zero errors)
- [ ] Frontend build: `npm run build` (dist produced successfully)
- [ ] i18n: every new key exists in both `zh-TW.json` and `en-US.json`; no hard-coded
  Chinese slipped through

## 2. Database / migration (use a throwaway test DB, never touch prod data)

- [ ] A fresh DB upgrades from 0001 to head cleanly: run `alembic upgrade head`
  against `jt_ipam_test`
- [ ] Each new migration has a `downgrade()` and survives one
  `alembic downgrade -1` then `upgrade head` round-trip
- [ ] No "model changed but migration forgotten": after upgrading to head the app
  starts without an asyncpg "column does not exist" error

## 3. Backend integration tests (test DB + pytest, thorough)

- [ ] With `JTIPAM_TEST_DATABASE_URL` set, `.venv/bin/pytest -q` is all green
  (e2e CRUD / auth / each module)
- [ ] Auth: login, refresh, TOTP, permissions (unauthorized `require_admin`
  endpoints return 401/403)
- [ ] Core CRUD: sections / subnets / addresses / devices / customers / locations / racks
- [ ] Audit chain: write operations are audited and chain integrity verifies

## 4. Key API smoke (against prod after deploy, mostly read-only)

- [ ] `GET /api/v1/health` (or `/notifications`) returns 200
- [ ] `GET /api/v1/subnets`, `/addresses`, `/devices`, `/locations`, `/racks` return 200
- [ ] Endpoints touched this release: manually hit one success path + one failure
  path (verify the 4xx is correct)

## 5. OWASP Top 10:2025 self-review (modules touched this release)

- [ ] A01 authorization: do new endpoints correctly use `require_admin` /
  object-level authorization?
- [ ] A03 injection / input validation: Pydantic StrictModel; file uploads verify
  magic bytes + size limit + reject dangerous types (e.g. SVG)
- [ ] A08 integrity: uploads / external data are validated; no path traversal
  (resolved upload/download paths stay inside the allow-listed directory)
- [ ] Secrets: no secret/token written to logs or responses

## 5b. Deploy-script flows (throwaway environment, **never run install on dev/prod**)

- [ ] **Fresh install**: on a clean LXC/VM run `scripts/install-debian.sh`; the
  service comes up and you can log in
- [ ] **Upgrade**: against a previous-version environment run
  `scripts/jt-ipam-upgrade.sh`; it upgrades cleanly and can roll back if needed
- [ ] If this release added a directory / package / service / DB extension / env,
  confirm **both scripts are in sync**
- [ ] **(A) Default admin credentials**: fresh install prints the `admin` account +
  random password at the end and saves it to `/etc/jt-ipam/.admin-initial-password`
  (root 0600); that password logs in
- [ ] **(A) Password-reset CLI**: `python -m app.cli.bootstrap create-admin --username
  admin --password-stdin --force-update` resets an existing admin; both READMEs
  document it
- [ ] **(B) Agent probe tools**: after `agent/jt-ipam-agent-installer.sh`, the host has
  `nmap` / `nmblookup` (samba-common-bin) / `avahi-resolve` (avahi-utils); the agent's
  reported `available_probes` includes os/netbios/mdns
- [ ] **(B) Install-help UI**: on the scan-agent page and the subnet edit dialog,
  unavailable probes show an "install help" popover with the matching install command

## 5c. Headless browser smoke

- [ ] `cd frontend && pnpm exec playwright test smoke` (no backend; self-starts
  vite preview) all green
- [ ] Against a deployed instance (with `E2E_BASE_URL` + `E2E_ADMIN_PASS`) run
  `pnpm test:e2e` main paths (login / sections / audit)

## 6. Manual page review (browser, after deploy)

- [ ] Login / logout / theme switch (light / dark / auto)
- [ ] Subnets: list, tree, IP list (incl. idle-range rows spanning columns), edit
- [ ] Devices / racks: sorting (natural IP order), consistent action-button height,
  floor-plan upload + drag-to-place + select
- [ ] Topology: nodes / links, VPN pairing links, legend
- [ ] Scan agents / sync jobs: pages render, no console errors

## 7. pfSense integration (Admin → 整合 pfSense)

> Prereq on the pfSense (CE 2.8.x): install **pfSense-pkg-RESTAPI** (pfrest.org), then System →
> REST API → Settings add **"API Key"** to auth methods, and create a key under Keys.

- [ ] Add instance: API URL + X-API-Key, **Verify TLS off** for a self-signed cert; save (key write-only, never returned).
- [ ] **Test connection** → success with the pfSense version.
- [ ] **Sync now** (ARP + aliases + rules on; **DHCP off** if another DHCP server owns the LAN) → counts return; an
  in-scope ARP IP gets `last_seen` (source `pfsense`) + MAC; aliases/rules counts reflect the box.
- [ ] **Field-name regression**: ARP/DHCP use `ip_address`/`mac_address` (not `ip`/`mac`); `hostname == "?"` → blank.
- [ ] **Scope safety**: with `scope_subnet_ids` set, stamping only hits IPs in those subnets (overlap-safe `.limit(1)`).
- [ ] **Rules / NAT viewer** (eye action) renders synced rules + NAT counts.
- [ ] **Graylog DSV** (Expose DSV on + a Graylog DSV token set): `GET /api/v1/lookup/pfsense/{id}/aliases?token=…`
  and `…/rules?token=…` return CSV/TSV; **wrong token → 401**; `expose_dsv` off → 404.
- [ ] Delete instance; periodic `jt-ipam-sync` picks up enabled instances every ~5 min without errors.

## 8. Recent feature spot-checks

- [ ] **Notification matrix** (Admin → 通知發送設定): toggle events × (in-app / email); save persists; events fire
  per matrix (IP request, cert expiring/deployed/drift, anomaly).
- [ ] **Cert distribution `files` profile**: writes cert files only, no reload/restart.
- [ ] **Anomaly page**: tabs, per-table column picker, `ip_address_id` hidden by default, MAC drift shows IP/hostname.
- [ ] **MCP client-config generator** (LLM/AI): button outputs Claude Desktop / opencode / mcpo / generic snippets.
- [ ] **Add address in a subnet**: the create form has a required IP field (issue #14).

### Recent (v0.5.6x–0.5.7x)

- [ ] **BMC out-of-band console** (IPMI SOL, Beta): enable per IP (`bmc_enabled`, migration 0092) → connect
  button appears on IP detail + Connections; connects with cipher auto-fallback (17→3); credential vault
  “remember” persists (`protocol='bmc'`) and pre-fills next time; RBAC = same as SSH (per-object + can_ssh);
  session open/close audited; **Setup guide** modal opens (form/toolbar/blank-hint) with troubleshooting;
  **Fit to window** button sends `stty` (tooltip warns it sends a command).
- [ ] **Disconnected overlay** (SSH / RDP / VNC / noVNC / xterm / BMC): dropping the session shows a big
  centered “Disconnected” + broken-link icon **over the display only** (toolbar / Reconnect stay clickable);
  fades out on reconnect.
- [ ] **Connections OS column** matches the IP-detail page (shared `OsCell`): OS icon + localized family name
  + （source） annotation, raw guess on hover; value is the source-precedence-resolved OS.
- [ ] **Scan-agent OS detection** (agent ≥ 1.7.0): appliances/BMCs are no longer mis-guessed — Debian
  appliance (SSH banner) → `Debian`, Windows via SMB/Service-Info → `Windows`, device-model-only guesses
  (NAS / OpenWrt / router) are dropped to unknown rather than shown.
- [ ] **Notification i18n**: switch UI language (繁中 ⇄ English) → the bell **and** the Notifications page
  render in the current language (IP-request, anomaly, cert, stale-IP); old notifications fall back to stored text.
- [ ] **Notification channels** (Admin → 通知發送設定): Telegram / Slack / Teams / Nextcloud Talk / Zulip each
  save (encrypted token/webhook; “set — leave blank to keep”), the per-channel **Test** button delivers, and an
  enabled channel receives a matrix-fired event (e.g. an IP request) alongside Email/in-app.
- [ ] **Export button** on table pages is bordered (matches Columns / Refresh).

---

### Appendix: throwaway test DB commands (on the prod host, **never the prod DB**)

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
