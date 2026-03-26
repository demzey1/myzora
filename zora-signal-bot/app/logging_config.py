"""
app/logging_config.py
─────────────────────────────────────────────────────────────────────────────
Structured logging setup using structlog.
Outputs JSON in production, pretty-printed in development.

Usage:
    from app.logging_config import get_logger
    log = get_logger(__name__)
    log.info("signal_scored", signal_id=42, score=78)
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from app.config import settings


def configure_logging() -> None:
    """Configure structlog and stdlib logging once at application start."""
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if settings.is_production or not settings.app_debug:
        # JSON output for log aggregators (Loki, CloudWatch, etc.)
        renderer = structlog.processors.JSONRenderer()
    else:
        # Human-friendly coloured output for local dev
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(settings.app_log_level)

    # Quieten noisy third-party loggers
    for noisy in ("httpx", "httpcore", "telegram", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger for the given module name."""
    return structlog.get_logger(name)  # type: ignore[return-value]
