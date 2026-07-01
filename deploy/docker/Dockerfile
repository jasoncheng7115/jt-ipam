# jt-ipam — optional Docker image (multi-stage).
# Build context MUST be the repo root, e.g.:
#   docker build -f deploy/docker/Dockerfile --target backend .
# This is a SECONDARY deploy path; the primary supported install is systemd + apt
# (scripts/jt-ipam.sh). See deploy/docker/README.md.

# ---------- stage: build the frontend (static dist) ----------
FROM node:20-bookworm-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------- stage: backend (FastAPI / uvicorn + sync + alembic) ----------
FROM python:3.12-slim-bookworm AS backend
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
# build deps for any wheels that need compiling + pg_isready for the entrypoint wait
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libffi-dev libpq-dev postgresql-client \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/ /app/backend/
RUN pip install ./backend
COPY scripts/ /app/scripts/
COPY deploy/docker/entrypoint-backend.sh /usr/local/bin/entrypoint-backend.sh
RUN chmod +x /usr/local/bin/entrypoint-backend.sh
WORKDIR /app/backend
EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/entrypoint-backend.sh"]
# default command = run the API; the sync service overrides this in compose
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]

# ---------- stage: web (nginx serves the dist + reverse-proxies /api) ----------
FROM nginx:alpine AS web
RUN apk add --no-cache openssl
COPY --from=frontend /app/frontend/dist /usr/share/nginx/html
COPY deploy/docker/nginx.conf /etc/nginx/conf.d/default.conf
# nginx official image auto-runs scripts in /docker-entrypoint.d/ before starting
COPY deploy/docker/entrypoint-web.sh /docker-entrypoint.d/99-jt-ipam-cert.sh
RUN chmod +x /docker-entrypoint.d/99-jt-ipam-cert.sh
EXPOSE 80 443
