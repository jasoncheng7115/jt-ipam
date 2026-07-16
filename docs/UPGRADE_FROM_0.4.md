# Upgrading jt-ipam from 0.4 to the latest version

> 繁體中文版：[UPGRADE_FROM_0.4_zh-TW.md](UPGRADE_FROM_0.4_zh-TW.md)

This is a runbook for moving an old **0.4.x** install up to the current release. You can upgrade
**directly across many versions** — Alembic runs every intermediate migration in order, so there is no
need to step through intermediate releases first.

The paths below assume the default layout: source at `/opt/jt-ipam`, config at
`/etc/jt-ipam/backend.env`, system account `jtipam`. Run everything as `root` / `sudo`.

> Do NOT re-run `install` as an upgrade shortcut. `install` does not `git pull`, so on its own it just
> re-runs Alembic and the build against the *old* code and the version does not move. It is safe on an
> existing box (it keeps the DB and never regenerates `ENCRYPTION_KEY`), but it only helps as a "repair"
> **after** you have refreshed the code (Step 2).

---

## Prerequisites

```bash
test -d /opt/jt-ipam/backend/.venv && echo "venv OK"
test -r /etc/jt-ipam/backend.env && echo "env OK"
stat -c '%U' /opt/jt-ipam          # should be: jtipam
```

---

## Step 0 — Back up first (do this, always)

```bash
# Database (custom format, restorable with pg_restore)
sudo -u postgres pg_dump -Fc jt_ipam > /root/jt_ipam_$(date +%F_%H%M).dump

# Config + uploads. backend.env holds ENCRYPTION_KEY — losing it makes every stored secret undecryptable.
cp -a /etc/jt-ipam /root/etc-jt-ipam.bak
tar czf /root/jt-ipam-uploads_$(date +%F).tgz -C /var/lib/jt-ipam uploads 2>/dev/null || true
```

> Never change `ENCRYPTION_KEY` / `SECRET_KEY` in `backend.env`, or all integration credentials, TOTP
> seeds, and certificate private keys stop working. The upgrade itself never touches them.

---

## Step 1 — Try the normal path first

```bash
sudo /opt/jt-ipam/scripts/jt-ipam.sh upgrade 2>&1 | tee /root/upgrade.log
```

- **Succeeds** -> skip to Step 6 (verify). Done.
- **Fails** -> look at the last ~20 lines of `upgrade.log` to see which stage broke
  (`git pull` / `alembic` / `pip` / `build`) and continue with the matching manual step below.

The manual steps 2-5 are just the `upgrade` flow (`git pull --ff-only` -> backup -> pip -> `alembic
upgrade head` -> build -> restart) broken out so you can get past a stuck stage.

---

## Step 2 — Bring the code up to date (handles a diverged git history)

`upgrade` uses `git pull --ff-only`, which aborts if an old 0.4 clone's history has diverged from the
public repo. Align it by hand:

```bash
sudo -u jtipam git -C /opt/jt-ipam config --global --add safe.directory /opt/jt-ipam
sudo -u jtipam git -C /opt/jt-ipam fetch origin
sudo -u jtipam git -C /opt/jt-ipam reset --hard origin/main   # customer config lives in /etc/jt-ipam, not the repo — safe
sudo -u jtipam git -C /opt/jt-ipam log --oneline -1           # confirm you are at the latest commit
```

If `origin` points at an old URL or fetch fails, reset the remote:

```bash
sudo -u jtipam git -C /opt/jt-ipam remote set-url origin https://github.com/jasoncheng7115/jt-ipam.git
sudo -u jtipam git -C /opt/jt-ipam fetch origin && sudo -u jtipam git -C /opt/jt-ipam reset --hard origin/main
```

---

## Step 3 — Update backend dependencies

```bash
sudo -u jtipam bash -c 'cd /opt/jt-ipam/backend && .venv/bin/pip install -e .'
```

If it reports `requires a different Python` or a dependency has no wheel, the OS/Python is likely too
old — capture the exact error before continuing.

---

## Step 4 — Database migration (the big 0.4 -> latest jump; most important)

```bash
# Where are we now
sudo -u jtipam bash -c 'cd /opt/jt-ipam/backend; set -a; source /etc/jt-ipam/backend.env; set +a; .venv/bin/alembic current'
# Run every migration up to head
sudo -u jtipam bash -c 'cd /opt/jt-ipam/backend; set -a; source /etc/jt-ipam/backend.env; set +a; .venv/bin/alembic upgrade head'
```

Common failures:

- **`Can't locate revision <xxxx>`** — `alembic_version` points at a revision that no longer exists.
  Inspect the chain and stamp a matching one, then re-run `upgrade head`:
  ```bash
  sudo -u jtipam bash -c 'cd /opt/jt-ipam/backend; .venv/bin/alembic history | head -40'
  # then, only after confirming a revision that matches the actual DB structure:
  # sudo -u jtipam bash -c 'cd /opt/jt-ipam/backend; set -a; source /etc/jt-ipam/backend.env; set +a; .venv/bin/alembic stamp <revision>'
  ```
  `stamp` only rewrites the recorded version, it does not change the schema — pick the revision
  carefully.
- **A migration fails on your data** (unique conflict, type mismatch): note the failing revision and the
  error, then fix that one. The Step 0 backup lets you retry safely.
- **Legacy `SQL_ASCII` database** hitting an encoding error: convert the DB to UTF-8 first (separate
  procedure), then continue.

---

## Step 5 — Rebuild the frontend and restart

The code is now current, so run the rest of the upgrade with `--no-pull` (pip -> alembic -> **frontend
build** -> restart, plus nginx WebSocket fix-ups):

```bash
sudo /opt/jt-ipam/scripts/jt-ipam.sh upgrade --no-pull
```

To rebuild by hand instead:

```bash
cd /opt/jt-ipam/frontend && sudo npm run build
sudo systemctl restart jt-ipam-backend
```

---

## Step 6 — Verify

```bash
# Version + Alembic at head
grep '"version"' /opt/jt-ipam/frontend/package.json
sudo -u jtipam bash -c 'cd /opt/jt-ipam/backend; set -a; source /etc/jt-ipam/backend.env; set +a; .venv/bin/alembic current'

# Service is up
systemctl is-active jt-ipam-backend
journalctl -u jt-ipam-backend -n 30 --no-pager    # no traceback

# API responds
curl -sk https://localhost/api/v1/health || curl -sk https://127.0.0.1:8443/api/v1/health
```

Then log in from a browser (do a **hard refresh** to drop the old JS bundle) and spot-check: subnets /
IPs / devices are present, an integration still connects (confirms `ENCRYPTION_KEY` was preserved and
secrets still decrypt), and login (including TOTP) works.

---

## Rolling back

```bash
# Code back to the old commit
sudo -u jtipam git -C /opt/jt-ipam reset --hard <old-commit>
# Database restore
sudo -u postgres pg_restore --clean --no-owner -d jt_ipam /root/jt_ipam_YYYY-MM-DD_HHMM.dump
# Restart
sudo systemctl restart jt-ipam-backend
```

---

## Notes

- In the normal case, Step 1 (`jt-ipam.sh upgrade`) is the only command you need; Steps 2-5 are the
  manual breakdown for when it gets stuck.
- Cross-version upgrades are supported — you do not need to hop through intermediate releases.
- Re-running `install.sh` is not an upgrade method: it does not `git pull`, and only works as a repair
  once the code has already been refreshed (Step 2). It is non-destructive to an existing env/DB (it does
  not regenerate keys or drop the database).
