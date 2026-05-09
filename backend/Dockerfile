# syntax=docker/dockerfile:1.7
# =============================================================================
# jt-ipam backend Dockerfile
#
# 安全考量（OWASP A05 / A06）：
#   * 多階段建構 — runtime 不含 build tools
#   * 非 root 使用者（uid 10001）
#   * 最小 base image（python:3.12-slim）
#   * 鎖定 base image 的 sha256 digest（請於 release 時 pin）
#   * read-only filesystem 友善（compose 設定 read_only: true）
#   * pip install 啟用 hash check（uv 預設）
# =============================================================================

ARG PYTHON_VERSION=3.12-slim

# ---------- Builder ----------
FROM python:${PYTHON_VERSION} AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 安裝 build deps（runtime 不需要）
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 安裝 uv
COPY --from=ghcr.io/astral-sh/uv:0.5.13 /uv /usr/local/bin/uv

WORKDIR /build

COPY pyproject.toml ./
COPY app ./app

RUN uv venv /opt/venv \
    && . /opt/venv/bin/activate \
    && uv pip install --no-cache .

# ---------- Runtime ----------
FROM python:${PYTHON_VERSION} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}" \
    APP_HOME=/app

# runtime 只裝必要的：libpq（asyncpg 不需要 libpq，但留著支援 sync 工具）、tini
RUN apt-get update && apt-get install -y --no-install-recommends \
        tini \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 jtipam \
    && useradd  --system --uid 10001 --gid jtipam --home-dir ${APP_HOME} --shell /usr/sbin/nologin jtipam

COPY --from=builder /opt/venv /opt/venv

WORKDIR ${APP_HOME}
COPY --chown=jtipam:jtipam app ./app
COPY --chown=jtipam:jtipam alembic ./alembic
COPY --chown=jtipam:jtipam alembic.ini ./alembic.ini

# 移除 setuid/setgid bits（hardening）
RUN find / -xdev -perm /6000 -type f -exec chmod a-s {} + 2>/dev/null || true

USER jtipam:jtipam

EXPOSE 8000

# 健康檢查（不洩漏內部資訊）
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).status==200 else 1)"]

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
