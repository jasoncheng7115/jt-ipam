"""FastAPI 應用入口。

OWASP A02 — production guard、安全 headers、CORS 白名單；
A09 — 結構化日誌與 request id。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.phpipam.router import phpipam_router
from app.api.v1.endpoints.health import router as health_router
from app.api.v1.router import api_v1_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.middleware import (
    AccessLogMiddleware,
    RequestIDMiddleware,
    SecurityHeadersMiddleware,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    log = structlog.get_logger("app")
    settings = get_settings()
    log.info("starting", env=settings.app_env, debug=settings.app_debug)
    yield
    log.info("shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    docs_url = "/docs" if not settings.is_production else None
    redoc_url = "/redoc" if not settings.is_production else None

    app = FastAPI(
        title="jt-ipam",
        version="0.3.0",
        description="jt-ipam — 新世代 IPAM 系統",
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url="/openapi.json" if not settings.is_production else None,
        lifespan=lifespan,
    )

    # ── Middleware（執行順序：最後加的先跑）──
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID", "If-Match"],
        expose_headers=["X-Request-ID"],
        max_age=600,
    )

    # ── Routes ──
    app.include_router(health_router)
    app.include_router(api_v1_router, prefix="/api/v1")
    app.include_router(phpipam_router, prefix="/api/phpipam")

    # ── GraphQL（Phase 2）──
    from app.graphql.schema import make_graphql_router
    app.include_router(make_graphql_router(), prefix="/graphql")

    # ── MCP server（Phase 4）──
    from app.mcp.server import build_mcp_app
    app.mount("/mcp", build_mcp_app())

    # ── Plugins（Phase 4）──
    from app.plugins import load_plugins
    load_plugins(app)

    # ── Exception handlers ──
    @app.exception_handler(RequestValidationError)
    async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        # A05 / A09 — 不洩漏 stack trace；只回必要資訊
        return JSONResponse(
            status_code=422,
            content={"detail": "Invalid request", "errors": exc.errors()},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log = structlog.get_logger("error")
        log.error(
            "unhandled_exception",
            error=exc.__class__.__name__,
            request_id=getattr(request.state, "request_id", None),
        )
        if get_settings().app_debug:
            return JSONResponse(status_code=500, content={"detail": str(exc)})
        return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})

    return app


app = create_app()
