# Security Policy

Security is a day-one requirement for jt-ipam. Every module and pull request is
reviewed against the **OWASP Top 10:2025** checklist documented in
[`docs/SECURITY.md`](docs/SECURITY.md).

## Supported versions

The latest released `0.5.x` line receives security fixes. Older lines are not
maintained — please upgrade.

## Reporting a vulnerability

**Do not open a public issue for security problems.**

Please report privately via one of:

- GitHub: open a [private security advisory](https://github.com/jasoncheng7115/jt-ipam/security/advisories/new)
- Email: the maintainer address listed on the repository profile

Include affected version, reproduction steps, and impact. We aim to acknowledge
within a few business days and will coordinate a fix and disclosure timeline with
you.

## Scope highlights

- TLS is mandatory (nginx reverse proxy or uvicorn self-signed).
- Secrets (DNS credentials, SNMP, API tokens) are encrypted at the application
 layer; passwords use argon2id; MFA via TOTP.
- All outbound integration URLs are SSRF allow-listed (metadata / link-local
 blocked).
- Audit events are chained with SHA-256.

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
(`deploy/zap-baseline.conf`) — i.e. zero new High/Medium/Low.

When in doubt about whether something is a security issue, report it privately
and we will triage.
