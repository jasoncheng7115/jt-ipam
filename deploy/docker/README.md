# Docker Compose deployment (optional)

> 繁體中文：[README_zh-TW.md](README_zh-TW.md)

> ⚠️ This is a **secondary / optional** deployment path. The primary, supported install for jt-ipam is
> still **systemd + apt** (`scripts/jt-ipam.sh install`, see the project README). Use Docker Compose for a
> quick trial / evaluation, or if your environment is already container-first.

The stack brings up: `postgres` (pgvector), `redis`, `backend` (FastAPI/uvicorn), `sync` (a background sync
loop that replaces the systemd timer), and `web` (nginx serving the frontend + reverse-proxying `/api`,
terminating HTTPS).

## Quick start

Prerequisites: **git** and **Docker Engine with the `docker compose` v2 plugin**. The official
`get.docker.com` script installs both. **Do not** `apt install docker.io` — that package has **no
`docker compose` subcommand** (you'll hit `unknown shorthand flag: 'd' in -d`); remove it and use the script:

```bash
sudo apt-get remove -y docker.io docker-compose podman-docker   # if you installed those
sudo apt-get update && sudo apt-get install -y curl git         # get.docker.com needs curl
curl -fsSL https://get.docker.com | sudo sh                     # Docker Engine + compose plugin
docker compose version                                          # verify the v2 plugin is present
```

`gen-env.sh`, `docker-compose.yml` and the rest live in the repo under `deploy/docker/`, so **clone the repo
first**:

```bash
git clone https://github.com/jasoncheng7115/jt-ipam.git
cd jt-ipam/deploy/docker
./gen-env.sh                    # create .env with random secrets (once)
docker compose up -d --build    # build images and start
```

Open `https://localhost` (a self-signed cert is generated on first run; trust the browser warning).

- **Change domain / ports:** edit `JT_IPAM_SERVER_NAME`, `HTTP_PORT`, `HTTPS_PORT` in `.env`, and set
  `APP_PUBLIC_URL` / `API_PUBLIC_URL` / `CORS_ORIGINS` to the matching `https://...`.
- **Use a real cert:** drop `server.crt` / `server.key` into `deploy/docker/certs/` (overrides the self-signed one).

### First admin

`gen-env.sh` generates a random `admin` password — it **prints it in its output** and stores it as
`JT_IPAM_ADMIN_PASSWORD` in `.env` (mode 0600). On first boot the backend creates the `admin` account with it,
so you can log in straight away. **Change it right after the first login.**

Prefer your own password? Set `JT_IPAM_ADMIN_PASSWORD` (and optionally `JT_IPAM_ADMIN_USERNAME` /
`JT_IPAM_ADMIN_EMAIL`) in `.env` before the first `up`. Or leave it empty and create the admin later:

```bash
docker compose exec backend python -m app.cli.bootstrap \
  create-admin --username admin --email admin@example.com --password-stdin
# then type the password (>= 12 chars)
```

## How to update

The easiest way — run in this directory:

```bash
./update.sh
```

It runs: `git pull` → `docker compose build` (rebuild images) → `docker compose up -d` (recreate containers).
**Migrations run automatically:** the backend container's entrypoint runs `alembic upgrade head` on every
start, so there is no manual migration step.

Equivalent manual steps:

```bash
git pull
docker compose build
docker compose up -d
docker compose logs -f backend   # watch migration / boot logs
```

> The version tracks the source (`backend/app/version.py` / `frontend/package.json`), so `git pull` + rebuild
> *is* the upgrade.

## Air-gapped / offline host (build outside, run inside)

If the target host has **no internet** (can't reach Docker Hub), build the images on an
internet-connected host, carry them over, and load them — for both install and upgrade.

**On the internet-connected host** — get the source first, then build:

```bash
git clone https://github.com/jasoncheng7115/jt-ipam.git   # first time (later: git pull to ship a newer version)
cd jt-ipam/deploy/docker
./offline-export.sh            # build + pull base images -> jt-ipam-images-<sha>.tar.gz
```

This saves all four images into one archive: the two app images (`jt-ipam-backend:local`,
`jt-ipam-web:local`) **and** the base images (`pgvector/pgvector:pg16`, `redis:7-alpine`) — the
air-gapped host can't pull those, so they travel too.

**Carry to the air-gapped host:** the `jt-ipam-images-*.tar.gz` archive **and the jt-ipam repo
folder** (compose still needs the build-context path to exist, even though it is never rebuilt there).

**On the air-gapped host** (in `deploy/docker/`):

```bash
./gen-env.sh                          # first install only (needs openssl, no internet)
./offline-import.sh jt-ipam-images-<sha>.tar.gz
```

`offline-import.sh` runs `docker load` then `docker compose up -d --no-build --pull never`, so it
**only** uses the images from the archive — no build, no pull. Migrations still run automatically on
backend start.

**To upgrade** an air-gapped host: re-run `./offline-export.sh` on the online host (after `git pull`),
copy the newer archive over, and run `./offline-import.sh <newer-archive>` again. `.env` stays put.

## Common commands

```bash
docker compose ps                  # status
docker compose logs -f backend     # backend logs (incl. migrations)
docker compose down                # stop (keeps the data volume)
docker compose down -v             # stop and DELETE data (⚠️ drops the DB too)
```

## Backup

All data is in the `pgdata` volume; example backup:

```bash
docker compose exec -T postgres pg_dump -U jt_ipam jt_ipam | gzip > jt-ipam-$(date +%F).sql.gz
```

## Differences from the systemd install / notes

- Background sync uses the `sync` service loop instead of `jt-ipam-sync.timer` (interval via
  `SYNC_INTERVAL_SECONDS` in `.env`).
- The plaintext Graylog DSV port 8088 is not included (use the HTTPS DSV URL inside containers).
- GeoIP / OUI scheduled refreshes are not bundled — add a cron or run them separately if needed.
- HTTPS is enforced: the app sets Secure cookies and https public URLs; do not expose plain http externally.
