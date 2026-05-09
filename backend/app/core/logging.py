"""結構化日誌（structlog）+ Graylog GELF 外送（OWASP A09）。"""

from __future__ import annotations

import logging
import sys

import structlog

from app.core.config import get_settings


def configure_logging() -> None:
    settings = get_settings()
    level = getattr(logging, settings.app_log_level.upper(), logging.INFO)

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    pre_chain = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        timestamper,
    ]

    structlog.configure(
        processors=[
            *pre_chain,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            _redact_processor,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # 讓 stdlib logging 也輸出 JSON（uvicorn / sqlalchemy）
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=structlog.processors.JSONRenderer(ensure_ascii=False),
            foreign_pre_chain=pre_chain,
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


_REDACT_KEYS = {
    "password",
    "password_hash",
    "secret",
    "api_key",
    "api_token",
    "authorization",
    "cookie",
    "set-cookie",
    "totp_secret",
    "private_key",
}


def _redact_processor(_logger, _name, event_dict):  # type: ignore[no-untyped-def]
    """A09：移除 log 中的敏感欄位。"""
    for k in list(event_dict.keys()):
        if k.lower() in _REDACT_KEYS:
            event_dict[k] = "***REDACTED***"
    return event_dict
