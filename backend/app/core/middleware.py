"""ASGI middleware：security headers、request id、結構化日誌。

OWASP A05 / A09。
"""

from __future__ import annotations

import time
import uuid
from typing import Final

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.core.config import get_settings

# CSP — frontend 由同源送出，所以預設 self；如需 CDN 再放寬
_CSP: Final[str] = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
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
        response.headers.pop("Server", None)
        return response


class RequestIDMiddleware(BaseHTTPMiddleware):
    """為每個 request 配發 X-Request-ID（A09，與 audit/log 串接）。"""

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        rid = request.headers.get("X-Request-ID") or str(uuid.uuid4())
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
        except Exception as exc:  # noqa: BLE001
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
