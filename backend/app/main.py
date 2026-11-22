"""FastAPI 應用入口。

OWASP A02 — production guard、安全 headers、CORS 允許清單；
A09 — 結構化日誌與 request id。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

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


class _MCPTrailingSlash:
    """把完全等於 /api/mcp、/mcp，以及 /api/phpipam/ 底下沒帶結尾斜線的請求路徑補上斜線（純 ASGI）。"""

    _PATHS = frozenset({"/api/mcp", "/mcp"})

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        path = scope.get("path") or ""
        # phpIPAM 相容 API 也補：phpIPAM 本身兩種寫法都接，我們的路由都帶斜線，不補就回 307；
        # Proxmox VE SDN 外掛（LWP）對 PATCH／DELETE 不跟轉址，刪除與更新就直接失敗（10-24）
        if scope.get("type") == "http" and (
                path in self._PATHS or (path.startswith("/api/phpipam/") and not path.endswith("/"))):
            scope = dict(scope)
            scope["path"] = path + "/"
            if scope.get("raw_path"):
                scope["raw_path"] = bytes(scope["raw_path"]) + b"/"
        await self.app(scope, receive, send)


def _mount_spa(app: FastAPI) -> None:
    """有 frontend/dist 就把 SPA 掛在根路徑（fallback 到 index.html）。

    - 404 → 回 index.html：前端 client-side 路由（/attack-surface 這類）直接輸入
      網址或重新整理才進得來。真正的 API 404 不受影響 —— /api、/healthz 這些
      route 在 mount 之前註冊，永遠先匹配。
    - index.html／version.json 帶 no-cache：長壽分頁的版本自動偵測靠 version.json，
      index 被快取則發新版後一直跑舊 bundle；hash 過的 /assets 維持可快取。
    - dist 不存在（純 API 部署、CI）就不掛，行為與過去一致。
    """
    from pathlib import Path

    from starlette.staticfiles import StaticFiles
    from starlette.types import Scope

    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if not (dist / "index.html").is_file():
        return

    class _SPAStaticFiles(StaticFiles):
        async def get_response(self, path: str, scope: Scope):  # type: ignore[override]
            # API 命名空間的 404 保持 JSON（不可被 fallback 吃掉變成回 HTML）——
            # 前端 client 靠 JSON 錯誤判斷；回 index.html 會變成「JSON 解析失敗」的迷惑錯誤
            spa_fallback = path.split("/", 1)[0] not in ("api", "mcp", "healthz")
            # Starlette 找不到檔案是「拋」HTTPException(404)，不是回 404 response —— 兩種都要接
            fell_back = False
            try:
                resp = await super().get_response(path, scope)
            except StarletteHTTPException as exc:
                if exc.status_code != 404 or not spa_fallback:
                    raise
                resp = await super().get_response("index.html", scope)
                fell_back = True
            if not fell_back and resp.status_code == 404 and spa_fallback:
                resp = await super().get_response("index.html", scope)
                fell_back = True
            if fell_back or path in ("index.html", "version.json", "."):
                resp.headers["cache-control"] = "no-cache"
            return resp

    app.mount("/", _SPAStaticFiles(directory=str(dist), html=True), name="spa")


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
    # 資料庫結構落後於程式時，畫面會到處 500（清單要讀完整欄位，count 卻照樣過）——
    # 啟動時就查得出來，那就要講出來，不要讓使用者一頁一頁踩了再自己猜。
    app.state.schema_behind = False
    try:
        from app.core.db import SessionLocal
        from app.services.self_check import warn_if_schema_behind
        async with SessionLocal() as s:
            app.state.schema_behind = await warn_if_schema_behind(s)
    except Exception as exc:
        log.warning("schema_check_failed", error=str(exc))

    # 啟動時確保內建角色存在（冪等，且函式內以 advisory lock 擋多 worker 同時 seed 的競態）
    try:
        from app.core.db import SessionLocal
        from app.services.permission import seed_default_roles
        async with SessionLocal() as s:
            n = await seed_default_roles(s)
            if n:
                log.info("default_roles_seeded", created=n)
    except Exception as exc:
        log.warning("seed_default_roles_failed", error=str(exc))
    # 啟動時確保內建電路類型存在（表為空才塞，冪等；同樣在函式內排隊）
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
    # IP 變更評估：沒有心跳的分析重新排隊並啟動（只看 impact_runs 自己的心跳，不會誤殺別的 worker 正在跑的）
    try:
        from app.services.change_impact.jobs import reclaim_and_relaunch
        n = await reclaim_and_relaunch()
        if n:
            log.info("impact_runs_relaunched", count=n)
    except Exception as exc:
        log.warning("impact_run_reclaim_failed", error=str(exc))
    # 戰情室 12 AI 情報：每分鐘看一次哪些播報流到期（有人在看、或管理員打開背景產生）。
    # 每個 worker 都跑，但產生靠資料庫租約，同一個範圍只會有一個去問模型
    import asyncio

    async def _intel_loop() -> None:
        from app.services.warroom import intel
        while True:
            await asyncio.sleep(60)
            try:
                await intel.background_tick()
            except Exception as exc:                  # 排程迴圈不可以因為一次錯誤就停掉
                log.warning("warroom_intel_tick_failed", error=exc.__class__.__name__)
    intel_task = asyncio.create_task(_intel_loop())
    yield
    intel_task.cancel()
    log.info("shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    docs_url = "/docs" if not settings.is_production else None
    redoc_url = "/redoc" if not settings.is_production else None

    from app.version import __version__
    app = FastAPI(
        title="jt-ipam",
        version=__version__,
        description="jt-ipam — 可自架、以整合為核心的 IPAM 系統",
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
        # 前端送的自訂標頭都要列（tests/test_cors_custom_headers.py 守著）：前後端不同源時少一個，
        # 預檢就 400、瀏覽器根本不送請求，畫面只看到「連線失敗」
        allow_headers=["Authorization", "Content-Type", "X-Request-ID", "If-Match", "Idempotency-Key", "X-Warroom-Token"],
        # 下載檔名（SBOM、憑證）在 Content-Disposition；不同源時沒公開，前端讀不到就只能用預設檔名
        expose_headers=["X-Request-ID", "Content-Disposition"],
        max_age=600,
    )

    # ── Routes ──
    app.include_router(health_router)
    app.include_router(api_v1_router, prefix="/api/v1")
    app.include_router(phpipam_router, prefix="/api/phpipam")

    # ── MCP server（Phase 4）──
    # 掛在 /api/mcp：nginx 只反代 /api/ 到後端，掛 /api 底下外部 client 才連得到。
    # 另保留 /mcp 給 direct（uvicorn 自簽 TLS）模式 / 內部呼叫。
    from app.mcp.server import build_mcp_app
    app.mount("/api/mcp", build_mcp_app())
    app.mount("/mcp", build_mcp_app())
    # 不帶斜線的 /api/mcp、/mcp 也要進到 MCP：掛載點只認 /api/mcp/…，不帶斜線的落到前端靜態檔回 405，
    # 而手冊與設定頁的客戶端設定產生器給的正是不帶斜線的網址（2026-09-30 盤點 API 手冊時抓到）
    app.add_middleware(_MCPTrailingSlash)

    # ── Plugins（Phase 4）──
    from app.plugins import load_plugins
    load_plugins(app)

    # ── SPA（direct／self-signed 模式）──
    # nginx 模式由 nginx 出 dist，這個 mount 打不到；direct 模式跳過 nginx，
    # 沒有這段的話 UI 無人供應 —— 客戶開 https://host:8443/ 只拿到
    # {"detail":"Not Found"}（2026-08-17 客戶回報）。掛在最後，API route 永遠優先。
    _mount_spa(app)

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
        # 標頭要帶上：401 的 WWW-Authenticate、限流 429 的 Retry-After（以前被丟掉，2026-09-30 發現）
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=getattr(exc, "headers", None),
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
