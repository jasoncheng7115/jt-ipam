"""FastAPI 應用入口。

OWASP A02 — production guard、安全 headers、CORS 白名單；
A09 — 結構化日誌與 request id。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

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
    # 啟動時 best-effort 清除超過保留天數的 AI chat 歷程（部署/重啟頻繁，足以當定期清理）
    try:
        from app.core.db import SessionLocal
        from app.services import ai_chat_store, system_config
        async with SessionLocal() as s:
            days = await system_config.get_ai_chat_retention_days(s)
            removed = await ai_chat_store.purge_old(s, retention_days=days)
            await s.commit()
            if removed:
                log.info("ai_chat_history_purged", removed=removed, retention_days=days)
    except Exception as exc:
        log.warning("ai_chat_purge_failed", error=str(exc))
    # 啟動時確保內建角色存在（冪等）
    try:
        from app.core.db import SessionLocal
        from app.services.permission import seed_default_roles
        async with SessionLocal() as s:
            n = await seed_default_roles(s)
            if n:
                log.info("default_roles_seeded", created=n)
    except Exception as exc:
        log.warning("seed_default_roles_failed", error=str(exc))
    # 啟動時確保內建電路類型存在（表為空才塞，冪等）
    try:
        from app.api.v1.endpoints.advanced import seed_default_circuit_types
        from app.core.db import SessionLocal
        async with SessionLocal() as s:
            n = await seed_default_circuit_types(s)
            if n:
                log.info("default_circuit_types_seeded", created=n)
    except Exception as exc:
        log.warning("seed_default_circuit_types_failed", error=str(exc))
    # 背景作業是用 asyncio.create_task 跑在 worker 程序內；程序一重啟（部署/升級/當機）
    # 在跑的作業就消失了，但 DB 的 status 還停在 pending/running → 在「作業」頁永遠殘留「執行中」。
    # 啟動時把這些孤兒作業標成 failed（已中斷），確保不會卡在進行中清不掉。
    try:
        from datetime import UTC, datetime

        from sqlalchemy import update

        from app.core.db import SessionLocal
        from app.models.background_task import BackgroundTask
        async with SessionLocal() as s:
            res = await s.execute(
                update(BackgroundTask)
                .where(BackgroundTask.status.in_(("pending", "running")))
                .values(status="failed", error="interrupted: backend restarted",
                        finished_at=datetime.now(UTC))
            )
            await s.commit()
            if res.rowcount:
                log.info("orphan_tasks_reconciled", count=res.rowcount)
    except Exception as exc:
        log.warning("orphan_task_reconcile_failed", error=str(exc))
    yield
    log.info("shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    docs_url = "/docs" if not settings.is_production else None
    redoc_url = "/redoc" if not settings.is_production else None

    from app.version import __version__
    app = FastAPI(
        title="jt-ipam",
        version=__version__,
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
    # 掛在 /api/mcp：nginx 只反代 /api/ 到後端，掛 /api 底下外部 client 才連得到。
    # 另保留 /mcp 給 direct（uvicorn 自簽 TLS）模式 / 內部呼叫。
    from app.mcp.server import build_mcp_app
    app.mount("/api/mcp", build_mcp_app())
    app.mount("/mcp", build_mcp_app())

    # ── Plugins（Phase 4）──
    from app.plugins import load_plugins
    load_plugins(app)

    # ── Exception handlers ──
    @app.exception_handler(RequestValidationError)
    async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        # A05 / A09 — 不洩漏 stack trace；只回必要資訊。
        # 自訂 validator 拋 ValueError 時，pydantic 會把 exception 放在 ctx 裡 —
        # 直接 JSON 化會炸；用 jsonable_encoder 把這類非原始型別轉成 str。
        from fastapi.encoders import jsonable_encoder
        return JSONResponse(
            status_code=422,
            content={"detail": "Invalid request",
                     "errors": jsonable_encoder(exc.errors(), custom_encoder={Exception: str})},
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
