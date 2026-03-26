"""
app/main.py
─────────────────────────────────────────────────────────────────────────────
FastAPI application entrypoint.

Responsibilities:
  - Startup / shutdown lifespan (DB pool, bot initialisation)
  - Health and readiness endpoints
  - Telegram webhook endpoint
  - Serves as the container process entrypoint via uvicorn

Run locally:
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

In production the Telegram webhook URL should be set to:
    https://<your-domain>/webhook/<TELEGRAM_WEBHOOK_SECRET>
"""

from __future__ import annotations

import hashlib
import hmac
import time
from contextlib import asynccontextmanager
from typing import Any

import redis.asyncio as aioredis
from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from telegram import Update

from app.bot.application import get_application
from app.config import settings
from app.db.base import engine
from app.logging_config import configure_logging, get_logger

configure_logging()
log = get_logger(__name__)

# ── Mount feature routers ──────────────────────────────────────────────────────
from app.api.wallet_routes import router as wallet_router  # noqa: E402

# ── Startup time (for uptime reporting) ──────────────────────────────────────
_START_TIME = time.time()


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[type-arg]
    """
    Async context manager executed once on startup and once on shutdown.
    Use this to initialise / teardown long-lived resources.
    """
    log.info("startup_begin", env=settings.app_env)

    # Initialise OpenAI Responses API client (if conversational mode enabled)
    if settings.enable_conversational_mode:
        try:
            from app.bot.conversation_store import init_openai_client
            await init_openai_client()
            log.info("openai_responses_client_initialized")
        except Exception as exc:
            log.error("openai_responses_client_init_failed", exc_info=True)
            if settings.is_production:
                raise

    # Initialise Telegram bot application
    tg_app = get_application()
    await tg_app.initialize()

    # Register webhook if URL is configured, else fall back to polling
    if settings.use_webhook:
        await tg_app.bot.set_webhook(
            url=f"{settings.telegram_webhook_url}/webhook/{settings.telegram_webhook_secret.get_secret_value()}",
            secret_token=settings.telegram_webhook_secret.get_secret_value(),
            allowed_updates=Update.ALL_TYPES,
        )
        log.info("telegram_webhook_set", url=settings.telegram_webhook_url)
    else:
        # Long-polling mode — start in background
        await tg_app.start()
        await tg_app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        log.info("telegram_polling_started")

    log.info("startup_complete")
    yield

    # ── Shutdown ──────────────────────────────────────────────────────────
    log.info("shutdown_begin")

    # Close OpenAI client
    if settings.enable_conversational_mode:
        try:
            from app.bot.conversation_store import close_openai_client
            await close_openai_client()
        except Exception as exc:
            log.error("openai_client_close_failed", exc_info=True)

    if settings.use_webhook:
        await tg_app.shutdown()
    else:
        await tg_app.updater.stop()
        await tg_app.stop()
        await tg_app.shutdown()

    await engine.dispose()
    log.info("shutdown_complete")


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="Zora Signal Bot",
    description="Event-driven Telegram bot for Zora coin signal detection",
    version="0.2.0",
    docs_url="/docs" if not settings.is_production else None,
    redoc_url="/redoc" if not settings.is_production else None,
    lifespan=lifespan,
)

app.include_router(wallet_router)


# ── Health endpoints ───────────────────────────────────────────────────────────

@app.get("/health", tags=["observability"])
async def health() -> dict[str, Any]:
    """
    Liveness probe — returns 200 if the process is running.
    Does NOT check downstream dependencies.
    """
    return {
        "status": "ok",
        "uptime_seconds": round(time.time() - _START_TIME, 1),
        "env": settings.app_env,
    }


@app.get("/ready", tags=["observability"])
async def readiness() -> JSONResponse:
    """
    Readiness probe — checks DB and Redis connectivity.
    Returns 503 if any dependency is unavailable.
    """
    checks: dict[str, str] = {}
    healthy = True

    # ── Database ──────────────────────────────────────────────────────────
    try:
        async with engine.connect() as conn:
            await conn.execute(engine.dialect.statement_compiler(  # type: ignore[arg-type]
                None, None  # type: ignore[arg-type]
            ).__class__.__mro__[0].__new__(  # just test the connection object
                engine.dialect.statement_compiler  # type: ignore[arg-type]
            ))
        checks["database"] = "ok"
    except Exception:
        # Simpler check — just try to acquire a connection
        try:
            from sqlalchemy import text
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception as exc:
            log.error("readiness_db_fail", error=str(exc))
            checks["database"] = f"error: {exc}"
            healthy = False

    # ── Redis ─────────────────────────────────────────────────────────────
    try:
        r = aioredis.from_url(settings.redis_url, socket_connect_timeout=2)
        await r.ping()
        await r.aclose()
        checks["redis"] = "ok"
    except Exception as exc:
        log.error("readiness_redis_fail", error=str(exc))
        checks["redis"] = f"error: {exc}"
        healthy = False

    # ── Telegram ──────────────────────────────────────────────────────────
    try:
        tg_app = get_application()
        me = await tg_app.bot.get_me()
        checks["telegram"] = f"ok (@{me.username})"
    except Exception as exc:
        log.error("readiness_telegram_fail", error=str(exc))
        checks["telegram"] = f"error: {exc}"
        healthy = False

    code = status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(
        content={"status": "ready" if healthy else "degraded", "checks": checks},
        status_code=code,
    )


@app.get("/metrics", tags=["observability"])
async def metrics() -> dict[str, Any]:
    """
    Lightweight metrics snapshot.
    TODO (Phase 2+): expose signal counts, position counts, error rates.
    """
    return {
        "uptime_seconds": round(time.time() - _START_TIME, 1),
        "paper_trading": settings.paper_trading_enabled,
        "live_trading": settings.live_trading_enabled,
        "llm_enabled": settings.llm_enabled,
    }


# ── Telegram webhook endpoint ─────────────────────────────────────────────────

@app.post("/webhook/{secret}", tags=["telegram"])
async def telegram_webhook(
    secret: str,
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, str]:
    """
    Receive Telegram updates via webhook.
    The secret path segment + header token provide two layers of validation.
    """
    if not settings.telegram_webhook_secret:
        raise HTTPException(status_code=404, detail="Not found")

    expected_secret = settings.telegram_webhook_secret.get_secret_value()

    # Validate path secret
    if not hmac.compare_digest(secret, expected_secret):
        log.warning("webhook_invalid_path_secret")
        raise HTTPException(status_code=403, detail="Forbidden")

    # Validate header token (PTB sets this automatically when secret_token is provided)
    if x_telegram_bot_api_secret_token and not hmac.compare_digest(
        x_telegram_bot_api_secret_token, expected_secret
    ):
        log.warning("webhook_invalid_header_token")
        raise HTTPException(status_code=403, detail="Forbidden")

    # Parse and dispatch the update
    try:
        body = await request.json()
        tg_app = get_application()
        update = Update.de_json(data=body, bot=tg_app.bot)
        await tg_app.process_update(update)
    except Exception as exc:
        log.error("webhook_processing_error", error=str(exc), exc_info=True)
        # Always return 200 to Telegram to prevent retry storms
        return {"status": "error_logged"}

    return {"status": "ok"}
