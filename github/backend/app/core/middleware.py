"""ASGI middleware：security headers、request id、結構化日誌。

OWASP A02 / A09。
"""

from __future__ import annotations

import time
import uuid
from typing import Final

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp

from app.core.config import get_settings

# CSP — frontend 由同源送出，所以預設 self；如需 CDN 再放寬
_CSP: Final[str] = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    # 世界地圖（Locations 多點 Leaflet）需要載入 OSM 圖磚（<img>）
    "img-src 'self' data: blob: https://*.tile.openstreetmap.org; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    # 地圖預覽（Location 經緯度）需要內嵌 OSM / Google Maps 的 iframe；
    # frame-src 只放行這幾個地圖網域，其餘維持 default-src 'self'。
    "frame-src 'self' https://www.openstreetmap.org https://www.google.com https://maps.google.com; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self';"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """注入 OWASP 推薦 headers（A05）。"""

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        settings = get_settings()
        response.headers.setdefault("Content-Security-Policy", _CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "geolocation=(), microphone=(), camera=(), payment=(), usb=()",
        )
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        # HSTS 只在 HTTPS / production 加（避免本機 dev 卡住）
        if settings.is_production:
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=63072000; includeSubDomains; preload",
            )
        # 移除可能洩漏資訊的預設 header
        if "Server" in response.headers:
            del response.headers["Server"]
        return response


class RequestIDMiddleware(BaseHTTPMiddleware):
    """為每個 request 配發 X-Request-ID（A09，與 audit/log 串接）。

    傳入的 X-Request-ID 若可解析為 UUID（含 nginx `$request_id` 那種無 hyphen
    的 32-hex），一律標準化成 hyphenated UUID 字串。原因：
      1. audit_logs.request_id 欄位是 UUID 型別，PG 讀回會是 hyphenated
      2. SHA-256 chain（A08）在 verify 時會用 `str(UUID)` 重組 canonical，
         若 write 時用了無 hyphen 字串會 hash 對不上 → chain 斷裂
    無法解析者重產新 UUID，避免奇怪字串注入。
    """

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        raw = request.headers.get("X-Request-ID")
        if raw:
            try:
                rid = str(uuid.UUID(raw))   # 32-hex 或 hyphenated 都吃；輸出一律 hyphenated
            except ValueError:
                rid = str(uuid.uuid4())
        else:
            rid = str(uuid.uuid4())
        request.state.request_id = rid
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response


class AccessLogMiddleware(BaseHTTPMiddleware):
    """結構化 access log（A09）。

    不輸出 query string 或 body（避免敏感資料寫入 log）。
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._logger = structlog.get_logger("access")

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:
            duration_ms = (time.perf_counter() - start) * 1000
            self._logger.error(
                "request_error",
                method=request.method,
                path=request.url.path,
                status=500,
                duration_ms=round(duration_ms, 2),
                request_id=getattr(request.state, "request_id", None),
                client_ip=_client_ip(request),
                error=str(exc.__class__.__name__),
            )
            raise
        duration_ms = (time.perf_counter() - start) * 1000
        self._logger.info(
            "request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=round(duration_ms, 2),
            request_id=getattr(request.state, "request_id", None),
            client_ip=_client_ip(request),
        )
        return response


def _client_ip(request: Request) -> str | None:
    # 信任 X-Forwarded-For 僅在 reverse proxy 後（uvicorn --proxy-headers 處理）
    if request.client is None:
        return None
    return request.client.host
