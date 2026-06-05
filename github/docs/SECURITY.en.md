# jt-ipam Security Design (aligned with OWASP Top 10:2025)

> 繁體中文版：[SECURITY.md](SECURITY.md)

> This document is the **mandatory** security spec for the jt-ipam project. Every PR and feature design must pass the self-review checklist here.
>
> Aligned with: [OWASP Top 10:2025](https://owasp.org/Top10/)
> Maintainer: Jason Tools security team
> Last updated: 2026-05-23 (upgraded from 2021 to 2025)

> **Differences from 2021**:
> - A02 changed from Cryptographic Failures to **Security Misconfiguration** (the biggest category in the cloud era)
> - A03 changed from Injection to **Software Supply Chain Failures** (replacing and expanding Vulnerable Components)
> - A04 Cryptographic Failures (moved down from A02)
> - A05 Injection (moved down from A03, as ORM / framework adoption reduced incidence)
> - A07 renamed to **Authentication Failures**
> - A09 changed from Logging and **Monitoring** Failures to Logging and **Alerting** Failures (more emphasis on "it must page someone")
> - **A10 brand new**: **Mishandling of Exceptional Conditions** replaces SSRF; SSRF is no longer listed separately and folds into A06 Insecure Design

---

## 0. Principles

1. **Security by Design**: get it right the first time, not as an afterthought.
2. **Defense in Depth**: every layer (network, application, data, people) has a line of defense; breaching any one layer does not compromise the whole.
3. **Least Privilege**: users, containers, service accounts, API tokens all get minimum privilege; deny-by-default.
4. **Fail Closed**: on auth failure, authz failure, missing config, or exception, the default is to **deny**, not allow (the core of the new A10 category).
5. **Auditable**: every write is traceable (who, when, what), with a SHA-256 hash chain guaranteeing tamper-evidence.
6. **No Secrets in Repo**: no password, key, or token may enter git history; use `.env` + secret management.

---

## 1. A01:2025 — Broken Access Control

### 1.1 Threats
- Horizontal/vertical privilege escalation
- IDOR (Insecure Direct Object Reference)
- Authorization bypass, forced browsing, URL tampering

### 1.2 Design
- **RBAC**: User Group + Role, deny by default; only explicit grants allow.
- **Object-level permissions**: Section / Subnet / Tenant each have an ACL; check before accessing any object.
- **Mandatory endpoint checks**: every endpoint applies a `Depends(require_permission("ipam.subnet.read", subnet_id))`-style dependency; never rely on routing logic to imply permission.
- **Bulk ops checked per item**: batch delete/update must not bypass permission for performance.
- **API Token scope**: each token is limited to endpoint patterns + object scope.
- **Tenant isolation**: with the Tenancy module enabled, cross-tenant queries must auto-add a `tenant_id` condition at the query layer.
- **Cannot delete the last active admin**: protected at both the application and DB layers.

### 1.3 Testing
- pytest fixtures simulate users with different roles; every endpoint has a 403 case.
- Automated scanning: horizontal escalation tests (user A using user B's ID).
- Existing test: `test_users_groups_audit.py::test_cannot_delete_last_admin`.

---

## 2. A02:2025 — Security Misconfiguration (promoted from 2021 A05)

> In 2025, cloud misconfiguration became the top risk, promoted from A05 to A02.

### 2.1 Threats
- Debug info leakage
- Default credentials
- Unnecessary services / ports
- Missing HTTP security headers
- Open cloud IAM / storage permissions

### 2.2 Design
- **Production guard**: when `APP_ENV=production`:
  - `APP_DEBUG=false`
  - disable `/docs`, `/redoc` (or require admin role)
  - never return stack traces to the client (overlaps with A10)
  - disallow default or sample passwords
- **HTTP Headers** (injected uniformly by middleware):
  - `Strict-Transport-Security: max-age=63072000; includeSubDomains; preload`
  - `Content-Security-Policy: ...` (see §5)
  - `X-Frame-Options: DENY`
  - `X-Content-Type-Options: nosniff`
  - `Referrer-Policy: strict-origin-when-cross-origin`
  - `Permissions-Policy: geolocation=(), microphone=(), camera=()`
  - `Cross-Origin-Opener-Policy: same-origin`
  - remove default headers that leak version info (e.g. `Server`)
- **CORS**: allowlisted origins; `*` forbidden in prod.
- **systemd hardening** (no containerization; see `deploy/systemd/jt-ipam-backend.service`):
  - `User=jtipam` / `Group=jtipam` (non-root)
  - `NoNewPrivileges=true`, `CapabilityBoundingSet=` (drop all), `AmbientCapabilities=`
  - `ProtectSystem=strict` (entire `/` read-only, only `ReadWritePaths` writable)
  - `ProtectHome=true`, `PrivateTmp=true`, `PrivateDevices=true`
  - `ProtectKernelTunables/Modules/Logs=true`, `ProtectControlGroups=true`, `ProtectClock=true`, `ProtectHostname=true`, `ProtectProc=invisible`
  - `RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6`, `RestrictNamespaces=true`, `RestrictRealtime=true`, `RestrictSUIDSGID=true`
  - `LockPersonality=true`, `MemoryDenyWriteExecute=true`
  - `SystemCallArchitectures=native`, `SystemCallFilter=@system-service` excluding `@privileged @resources @obsolete @debug`
  - `LimitNOFILE=65536` / `TasksMax=1024`
  - Verify: `systemd-analyze security jt-ipam-backend` (target score ≤ 3.5; currently measured 1.3)
- **Default credentials**: force the admin to change the password on first start; the installer generates a random password rather than hardcoding one.
- **Health endpoint**: `/healthz` (liveness) returns only 200, leaking no internal info.

---

## 3. A03:2025 — Software Supply Chain Failures (replaces Vulnerable Components)

> 2025 renamed and expanded Vulnerable Components to the **entire supply chain**: dependencies, CI/CD, build tools, release channels.

### 3.1 Threats
- Outdated dependencies containing CVEs
- Unpinned versions, non-reproducible builds
- GitHub Actions / npm registry compromise (typosquatting)
- Untrusted third-party plugins
- container/wheel artifacts replaced via MITM

### 3.2 Design
- **Dependency pinning**:
  - Python: `uv lock` / `poetry.lock` in git
  - Node: `pnpm-lock.yaml` in git; `packageManager: "pnpm@9.15.9"` pins the version (avoids corepack pulling a new version and breaking)
- **CI scanning**:
  - `pip-audit` (Python CVEs)
  - `pnpm audit --audit-level=high`
  - `osv-scanner` (reinforcement, dual-track Python + Node)
  - `bandit` (Python SAST)
  - `apt list --upgradable` + `unattended-upgrades` (OS layer; LXC / bare-metal patched regularly)
- **Dependabot / Renovate**: auto PR updates; patches with CVSS ≥ 7 reviewed within 24 hours.
- **SBOM**: generate a CycloneDX / SPDX SBOM on every release.
- **Supply chain hardening**:
  - PyPI uses `uv` (or `pip` + `--require-hashes`) with hash checking enabled
  - Released `.deb` and offline install bundles are GPG-signed
  - Lock `apt` sources (PGDG, official Debian/Ubuntu repos; no third-party PPAs unless reviewed)
  - **GitHub Actions pinned to SHA, not tag** (`uses: actions/checkout@<sha>`)
  - **Plugin signing**: jt-ipam verifies GPG / sigstore before loading a plugin; unsigned plugins only load when an admin force-enables them
  - **Release tags GPG-signed**: `git tag -s`
  - Block direct push to main (PR + review required)

---

## 4. A04:2025 — Cryptographic Failures (demoted from 2021 A02 to A04)

### 4.1 Threats
- Sensitive data stored in plaintext (DNS credentials, SNMP community, API tokens, WinRM passwords)
- Weak password hashing (MD5/SHA-1/unsalted)
- TLS misconfiguration (old versions, unvalidated self-signed certs)

### 4.2 Design
- **Password hashing**: argon2id (`time_cost=3, memory=64MiB, parallelism=4`); bcrypt/sha256 not allowed.
- **Application-layer field encryption**: sensitive DB columns use AES-256-GCM two-column form (`*_enc` + `*_nonce`); aad bound to instance id to prevent nonce reuse across instances. Keys come from the `ENCRYPTION_KEY` env var; in production switch to a KMS (AWS / Vault Transit).
- **Encrypted field list**:
  - DNS Server credentials (PowerDNS API key, BIND TSIG, Windows WinRM password, OPNsense API secret)
  - OPNsense Firewall API key/secret (two-column, aad bound to firewall id)
  - Wazuh API password (aad bound to instance id)
  - LibreNMS API token, scan agent token
  - SNMP community (v1/v2c), SNMPv3 auth/priv password
  - User TOTP secret
  - Webhook secret (shown only once at creation)
- **TLS (HTTPS mandatory — any environment)**: the transport from the user's browser to jt-ipam must be HTTPS; **there is no HTTP-only deployment option**. `config.py._tls_guards` rejects any `http://` `APP_PUBLIC_URL` / `API_PUBLIC_URL` at startup.
  - Two supported modes:
    - **`BACKEND_TLS_MODE=nginx`** (default): uvicorn binds `127.0.0.1:8000` plain-HTTP loopback, nginx terminates TLS.
    - **`BACKEND_TLS_MODE=direct`**: uvicorn directly serves the PEM cert/key.
  - Self-signed certs use ECDSA P-384 / SHA-384 / 5 years.
  - Outbound integration calls (DNS server / LibreNMS / Webhook) enforce TLS 1.2+, recommend 1.3; verify the cert chain, **do not accept `verify=False`** (OPNsense/Wazuh have a `verify_tls` flag to disable it, but prod should not).
- **Cookie**: `Secure`, `HttpOnly`, `SameSite=Lax` (`Strict` for sensitive-operation tokens).
- **JWT**: HS512 for short-lived access tokens; refresh token not in LocalStorage, use an HttpOnly cookie.
- **Key rotation**: `SECRET_KEY` / `ENCRYPTION_KEY` support multiple versions (kid) for seamless rotation.

### 4.3 Testing
- Unit test: after writing an SNMP community, the DB column should be ciphertext and decrypt correctly after restart.
- Existing tests: `test_firewall_opnsense.py::test_credential_encrypt_roundtrip` / `test_wazuh.py::test_password_encrypt_aad` cover aad preventing cross-instance decryption.
- Pentest: after dumping the DB, no sensitive field can be recovered.

---

## 5. A05:2025 — Injection (demoted from 2021 A03 to A05)

> In 2025, SQL injection etc. are less common, mainly due to ORM / framework adoption; but they remain high-impact.

### 5.1 Threats
- SQL Injection, NoSQL Injection
- LDAP Injection (AD/LDAP auth)
- Command Injection (scanner invoking nmap, ping, SNMP CLI)
- XSS (IP description / custom fields / webhook payload)
- SSTI (email templates, PDF reports)

### 5.2 Design
- **SQLAlchemy ORM fully parameterized**: `.execute(text(f"..."))` string interpolation is forbidden; raw SQL must use bound parameters.
- **Pydantic v2 strict validation**: all API input goes through a schema; StrictModel sets `extra='forbid'`.
- **Email template sandbox**: Jinja2 uses `SandboxedEnvironment`; custom templates disallow `__class__`, `__mro__`, import.
- **Scanner subprocesses**: invoking `nmap` / `ping` uses `subprocess.run([...], shell=False)`; target IP/CIDR is first validated via the `ipaddress` module.
- **LDAP**: uses the `ldap3` library, escaping DN and filters (`ldap3.utils.conv.escape_filter_chars`).
- **XSS**:
  - Vue 3 escapes HTML by default; `v-html` on user input is forbidden (enforced by lint rule).
  - Backend HTML output (PDF reports) uses markupsafe / jinja autoescape.
  - Set `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self';`
- **WinRM PowerShell**: pass parameters via PowerShell splatting / parameter binding, not string concatenation.

### 5.3 Testing
- pytest tests payloads like `'; DROP TABLE`, `<script>`, `{{7*7}}`, `$(rm -rf /)`.
- CI runs `bandit` on Python and a `semgrep` rule set.

---

## 6. A06:2025 — Insecure Design

> In 2025 SSRF is no longer A10 on its own; it **folds into this item**.

### 6.1 Threats
- Lack of a threat model
- Business logic flaws (duplicate IP allocation, duplicate requests, TOCTOU)
- Lack of rate limiting enabling abuse / brute force
- **SSRF**: an attacker sets a DNS Server / LibreNMS / Webhook target to a metadata IP / internal IP / file://, making the server issue internal requests; DNS rebinding bypasses validation

### 6.2 Design (general)
- **Threat model**: each new module (DNS integration, LibreNMS, AI) does a STRIDE analysis at design time, recorded in `docs/threat-models/`.
- **Business invariants**: enforced with DB-layer unique constraints (IP+VRF unique, subnet non-overlap); not application-layer checks alone.
- **Rate Limiting**:
  - global: `100 req/min/IP`
  - auth endpoints: `10 req/min/IP` (anti-brute-force)
  - write endpoints: `30 req/min/user`
  - outbound integration calls (DNS push, LibreNMS API): token bucket rate control
- **Idempotency**: all write endpoints support the `Idempotency-Key` header.
- **Anti-CSRF**: cookie-auth endpoints verify a CSRF token; API-token-auth endpoints don't need it (no ambient credential).
- **Dangerous features off by default**: advanced modules (Cabling/Power/...), outbound webhooks, the AI MCP server are disabled by default.

### 6.3 Design (SSRF protection, formerly 2021 A10)
- **Target URL allowlist**:
  - when configuring DNS Server / LibreNMS / Webhook / OPNsense / Wazuh URLs, DNS-resolve first; the resolved result must pass a CIDR allowlist check
  - blocked by default: `127.0.0.0/8`, `169.254.0.0/16` (incl. AWS metadata `169.254.169.254`, GCP/Azure metadata), `::1`, `fe80::/10`, `fc00::/7`, `0.0.0.0/8`
  - set `OUTBOUND_ALLOW_PRIVATE=true` to allow RFC1918 (internal IPAM usually needs it, but it must be explicitly enabled)
  - extra allowlists: `OUTBOUND_ALLOW_CIDRS` / `OUTBOUND_ALLOW_HOSTS`
- **DNS rebinding protection**: pin the IP when creating the HTTP client (connect to the resolved IP directly with a `Host` header) to avoid TOCTOU.
- **Scheme restriction**: only `https://` (with `http://` as a special case for internal services, explicitly configured). `file://`, `gopher://`, `ftp://`, `dict://`, `ldap://` forbidden.
- **Redirect restriction**: HTTP redirect targets must also pass the allowlist; follow at most 3 times.
- **Implementation**: `backend/app/core/safe_http.py::safe_request`; all external calls **must** go through it. Using `httpx.AsyncClient(...)` directly is a violation (blocked by lint rule + code review).

---

## 7. A07:2025 — Authentication Failures (simplified from Identification and Authentication Failures)

### 7.1 Threats
- Weak passwords, password reuse, credential stuffing
- Session fixation, session hijacking
- Lack of MFA
- User enumeration (response messages leaking whether a user exists)

### 7.2 Design
- **Password policy**: minimum 12 chars, block known-breached (HIBP k-anonymity API); reject common dictionary words.
- **TOTP MFA**: optional but admins can enforce; uses `pyotp`, secret stored encrypted.
- **Account lockout**: 5 failures → lock 15 min; 20 failures from one IP across multiple accounts → ban 1 hour (anti-stuffing). Admin can unlock via `/users/{id}` PATCH `unlock=true`.
- **Anti-enumeration**: even when a user isn't found, run a dummy argon2 verify and return 401 to avoid distinguishing via timing / status.
- **Session**:
  - rotate session ID after login
  - idle timeout 30 min / absolute timeout 12 h
  - cookie HttpOnly + Secure + SameSite=Lax
- **API Token**:
  - shown only once at creation (stored hashed)
  - mandatory expiry (max 1 year)
  - mandatory scope
  - list shows "last used IP / time"
- **OIDC / SAML**: fully supported (incl. metadata / ACS / SLO); related config in INSTALL.md §4, and configurable in the web UI under Admin → System Settings.
- **Login records**: all logins (success / failure) written to audit + structured log.

---

## 8. A08:2025 — Software or Data Integrity Failures

### 8.1 Threats
- Audit log tampering
- CI/CD pipeline compromise
- MITM during updates

### 8.2 Design
- **SHA-256 change chain**:
  - each audit record includes the previous record's hash
  - the chain head comes from `AUDIT_CHAIN_GENESIS` (immutable per deployment)
  - `POST /api/v1/audit/verify` provides admin chain-integrity verification
  - periodically export hashes to external immutable storage (jt-glogarch / S3 Object Lock)
- **Release signing**: every release artifact (`.tar.gz`, `.deb`, wheel cache, Proxmox LXC template) signed via GPG / sigstore.
- **Outbound webhook signing**: HMAC-SHA256 (`X-jt-ipam-Signature` header), verifiable by the receiver. Secret shown only once at creation.
- **Inbound webhook verification**: peer sources must also be signed (OPNsense / LibreNMS callbacks).
- **Plugin integrity**: when a plugin entry point loads, if a same-named `*.sig` signature file exists it must verify; unsigned plugins only load when an admin explicitly enables them in settings.
- **CI/CD**:
  - GitHub Actions pinned to `uses: ...@<sha>` not tag
  - deploy keys least-privilege
  - block direct push to main (PR + review required)

---

## 9. A09:2025 — Security Logging and Alerting Failures (from Monitoring → Alerting)

> The 2025 rename emphasizes "not just log, but page". Writing logs nobody reads is the same as not doing it.

### 9.1 Threats
- Insufficient evidence left when an attack happens
- Anomalous events going unnoticed
- Alert channel failure (full mailbox, expired Slack token) going unfelt

### 9.2 Design
- **Structured logs**: JSON format (timestamp, level, event, user_id, ip, resource, action, status_code, request_id).
- **Audit event list** (must record):
  - login success/failure, logout, password change, MFA enable/disable
  - permission changes, role assignments
  - data CRUD (with diff)
  - API token create/revoke
  - external integration connections (DNS push, LibreNMS sync, OPNsense alias push, Wazuh agent pull)
  - configuration changes
- **Graylog forwarding**: GELF over TLS; jt-glogarch offsite backup.
- **Active alerting** (the A09 2025 focus):
  - repeated failed logins → Telegram + Email
  - high-privilege operations (create/delete admin user) → real-time webhook
  - SHA-256 chain break → critical alert
  - integration sync failing N times in a row → webhook + UI red dot
  - **Wazuh missing-agent**: detect a hostname with no agent → add to the SOC dashboard and alert
  - backup failure (jt-ipam-backup.service exit ≠ 0) → systemd OnFailure pushes an alert
- **Alert channel health**: send a heartbeat to the webhook on a schedule daily; absence for 24h is treated as down.
- **Retention**: 90 days local, permanent offsite (per audit requirements).
- **PII protection**: logs never write password, token, or API key in cleartext (automatic redaction middleware).

---

## 10. A10:2025 — Mishandling of Exceptional Conditions (brand-new category)

> A brand-new 2025 category replacing 2021 A10 SSRF. Covers race conditions, fail-open, stack-trace leakage, deadlock, unhandled exceptional paths, etc.

### 10.1 Threats
- Returning a stack trace / SQL query / internal path to the user on error (info leakage)
- **fail-open**: allowing through when a permission check excepts (should fail-closed)
- **TOCTOU**: changed between check and write (duplicate IP allocation, race conditions)
- Exceptional paths not writing audit (attacker makes the system throw to bypass logging)
- External API failures silently swallowed, user thinks it succeeded
- Deadlock / livelock stalling a worker with no timeout

### 10.2 Design
- **Unified exception response**: a FastAPI exception handler turns all unexpected exceptions into `500 Internal Server Error`, the response containing only `request_id`; the full stack is logged, not sent.
  - The production guard ensures `app_debug=false` won't return a stack.
  - Remove the default `Server` header (overlaps with A02).
- **Fail-closed**:
  - the `require_permission` dependency raises 401/403 automatically; **never** try/except then allow through
  - SSRF checks (safe_http) deny on any doubt; never fall back to raw httpx
  - rate-limit check failure (Redis unreachable) returns 503, no bypass allowed
- **Race / TOCTOU**:
  - IP allocation uses `SELECT ... FOR UPDATE` or an advisory lock
  - audit chain writes serialized with `pg_advisory_xact_lock(AUDIT_LOCK_KEY)`
  - subnet split / first_free_address must lock the subnet row within the same transaction
- **Exceptions also write audit**: after try/except, even on re-raise, write audit (at least `action=*_failed`). `authenticate()` calls `_audit()` + `session.commit()` on every failure path (wrong password, locked account, user not found).
- **External API failure recording**:
  - on OPNsense / Wazuh / LibreNMS sync failure, `last_error` must be written back to the DB
  - after writing back, `await session.commit()` then raise HTTPException (raising must not swallow last_error)
- **Timeouts mandatory**:
  - all `httpx` / `safe_request` calls carry an explicit timeout (no default)
  - PostgreSQL `statement_timeout=30s` (applied in `app/core/db.py`)
  - asyncio tasks use `asyncio.wait_for(..., timeout=N)`
- **Resource cleanup**:
  - `async with` instead of manual close
  - connection pool `pool_pre_ping=True` + `pool_recycle=1800`
  - CI fails when filterwarnings surfaces a `ResourceWarning` (pytest enabled)

### 10.3 Testing
- pytest simulates DB unreachable, Redis down, external API 500, confirming:
  1. no stack leaked
  2. `last_error` written
  3. audit log has a `*_failed` entry
  4. no silent swallow
- Known pitfall (recorded in memory `feedback_pytest_anyio_filterwarnings.md`): anyio on Py3.11+ triggers a DeprecationWarning on `Task.cancel("msg")`; with `filterwarnings=error` it swallows CancelScope cancellation → deadlock. pyproject.toml must ignore it.

---

## 11. PR self-review checklist (run once per PR — 2025 edition)

Ask yourself before committing:

- [ ] **A01** Does the new endpoint apply a permission dependency? Bulk ops checked per item?
- [ ] **A02** Did you introduce debug info leakage, loosen CORS, skip TLS verification, or regress systemd hardening?
- [ ] **A03** Did new deps pass audit? Versions pinned? GitHub Actions pinned to SHA?
- [ ] **A04** If the new field is sensitive, is it AES-GCM encrypted? aad bound to instance id? Are passwords leaking into plaintext logs?
- [ ] **A05** Does all external input go through Pydantic? No string-built SQL / commands?
- [ ] **A06** Do business invariants have a DB-constraint backstop? Is rate limit needed? Do external URLs go through `safe_http`?
- [ ] **A07** Did auth-flow changes break lockout / MFA / session rotation / anti-enumeration?
- [ ] **A08** Did you write an audit log? Is the diff complete? Are outbound webhooks signed?
- [ ] **A09** Do structured logs have request_id? Sensitive data redacted? Do failures trigger alerts?
- [ ] **A10** **New**: do all try/except write audit? Is it fail-closed? Do races / TOCTOU use locks? Are timeouts set?

---

## 12. Vulnerability reporting

Channel: `security@example.com` (PGP public key on the website).

Commitment:
- acknowledge receipt within 24 hours
- patch high-severity issues within 7 days
- no litigation against good-faith researchers (safe harbor)
- public disclosure within 90 days of patching a major vulnerability

---

## 13. Third-party security testing

Before each major release:
- SAST (built into CI: bandit, semgrep, ruff S rules)
- DAST (OWASP ZAP baseline runs automatically)
- third-party penetration testing (at least once a year)
- public bug bounty program (launched once the system stabilizes)
