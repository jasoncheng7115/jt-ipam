# Security Policy

Security is a day-one requirement for jt-ipam. Every module and pull request is
reviewed against the **OWASP Top 10:2025** checklist documented in
[`docs/SECURITY.md`](docs/SECURITY.md).

## Supported versions

The latest released `0.4.x` line receives security fixes. Older lines are not
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

When in doubt about whether something is a security issue, report it privately
and we will triage.
