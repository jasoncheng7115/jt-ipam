# Security Policy

Security is a day-one requirement for jt-ipam: every module and every pull request
is reviewed against the **OWASP Top 10:2025** checklist, and every release must pass
an OWASP ZAP baseline scan with zero new findings before it is published.

繁體中文: [SECURITY_zh-TW.md](SECURITY_zh-TW.md) · 日本語: [SECURITY_ja.md](SECURITY_ja.md)

## Supported versions

Only the latest release receives security fixes; please upgrade before reporting.
Releases are frequent and a fix is delivered as a new release, not as a backport onto an
older one. Releases are tagged (`vX.Y.Z`) from `v0.6.8` onwards.

## Reporting a vulnerability

**Please do not open a public issue.**

- GitHub: use **Report a vulnerability** on the [Security tab](https://github.com/jasoncheng7115/jt-ipam/security)
  (private vulnerability reporting)
- Email: service@jason.tools

Include the affected version, reproduction steps and impact. We aim to acknowledge within
3 business days, and for confirmed issues to release a fix and publish a GitHub Security
Advisory within 7 days.

## Security advisories

Published advisories: https://github.com/jasoncheng7115/jt-ipam/security/advisories

An advisory is published for every exploitable issue fixed in a release, after the fixed
release is out. Pure hardening and dependency upgrades that do not affect jt-ipam are listed
in the release notes only.

## Scope highlights

- TLS is mandatory (nginx reverse proxy or uvicorn self-signed).
- Secrets (DNS credentials, SNMP, API tokens) are encrypted at the application
 layer, each bound to its purpose with AES-GCM associated data; passwords use
 argon2id.
- Two-factor authentication (TOTP) can be required for administrators or for
 everyone, with one-time recovery codes; a TOTP code cannot be replayed.
- Server-side sessions: the refresh token lives only in an HttpOnly, SameSite=Strict
 cookie and is rotated with reuse detection; sign-out, deactivation and admin
 revocation take effect immediately, and open consoles are closed within 30 seconds.
- Outbound connections are checked at connect time for HTTP and before connecting
 for every other protocol (cloud metadata, link-local and reserved addresses are
 blocked; consoles also refuse loopback).
- Audit events are chained with SHA-256, append-only in the database, anchored
 externally, and include exports and secret reveals.
- Daily backups can be encrypted with a passphrase.
- What maps to ISO/IEC 27001:2022 and ISO/IEC 42001:2023 controls, and how to verify each item,
 is in [docs/COMPLIANCE.md](docs/COMPLIANCE.md).

## Accepted risks (documented, with compensating controls)

These are known scanner findings that cannot be removed without disproportionate
cost (e.g. replacing the UI framework), kept here for audit transparency. Each is
neutralised in practice by the surrounding controls.

### CSP `style-src 'unsafe-inline'` (rated *Medium* by ZAP rule 10055)

- **Why it cannot be removed:** the frontend is Vue 3 + Naive UI. Vue's `v-show`,
  dynamic `:style` bindings and Naive UI's floating-element positioning all emit
  inline `style="…"` **attributes**. CSP has no way to allow inline style
  *attributes* with a nonce or hash (nonces/hashes apply only to `<style>`
  blocks), and adding a nonce makes browsers *ignore* `'unsafe-inline'`, which
  would break every `v-show`/`:style` in the app. This is a CSP-level limitation
  shared by all major Vue/React component libraries (MUI, Angular Material, …).
- **Why the real risk is low (compensating controls):**
  - `script-src 'self'` (no `unsafe-inline` for scripts) → injected CSS cannot
    execute JavaScript.
  - `img-src 'self' data: blob:` and `connect-src 'self'` → injected CSS cannot
    exfiltrate data (the classic attribute-selector + external-image trick is
    blocked; no external origins are reachable).
  - Output is auto-escaped by Vue, so there is no injection point to begin with.
  - `inline-theme-disabled` is enabled on Naive UI's config provider, moving
    theme styling out of inline attributes into `<style>` blocks to minimise the
    inline surface.
- **Net effect:** at most cosmetic CSS tampering, never code execution or data
  theft. Tracked in `deploy/zap-baseline.conf`.

## Release gate

Before every release we run a ZAP scan (HTTP + behind the public reverse proxy)
and require **no findings beyond the documented baseline** above
(`deploy/zap-baseline.conf`), i.e. zero new High/Medium/Low.

CI also runs a ZAP baseline scan on every push, against the real nginx configuration
and a production build of the frontend; any alert outside the baseline fails the build.

When in doubt about whether something is a security issue, report it privately
and we will triage.
