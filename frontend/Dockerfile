# syntax=docker/dockerfile:1.7
# =============================================================================
# jt-ipam frontend Dockerfile（多階段：build with pnpm，serve with nginx）
#
# 安全考量（OWASP A05）：
#   * 非 root nginx user（nginxinc/nginx-unprivileged）
#   * 安全 headers 由 nginx.conf 統一注入
#   * 不打包 sourcemap（vite.config.ts 已設定）
# =============================================================================

ARG NODE_VERSION=20-alpine
ARG NGINX_VERSION=1.27-alpine

# ---------- Builder ----------
FROM node:${NODE_VERSION} AS builder

ENV PNPM_HOME=/usr/local/pnpm \
    PATH=/usr/local/pnpm:$PATH \
    CI=1

RUN corepack enable && corepack prepare pnpm@9 --activate

WORKDIR /build

COPY package.json pnpm-lock.yaml* ./
RUN pnpm install --frozen-lockfile --prefer-offline || pnpm install

COPY . .
RUN pnpm build

# ---------- Runtime ----------
FROM nginxinc/nginx-unprivileged:${NGINX_VERSION} AS runtime

USER 0
RUN rm -rf /usr/share/nginx/html/* /etc/nginx/conf.d/default.conf

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /build/dist /usr/share/nginx/html

USER 101  # nginx unprivileged uid
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qO- http://127.0.0.1:8080/healthz >/dev/null || exit 1
