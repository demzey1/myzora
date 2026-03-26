"""
app/bot/application.py
─────────────────────────────────────────────────────────────────────────────
Builds the python-telegram-bot Application instance and registers all
command and callback handlers.

Returns a singleton via `get_application()`.
"""

from __future__ import annotations

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from app.bot.handlers import (
    cmd_addcreator, cmd_removecreator, cmd_creators, cmd_creatorstatus, cmd_mode,
    cmd_linkwallet, cmd_walletstatus, cmd_unlinkwallet,
    handle_free_text, cmd_ai, cmd_premium, cmd_subscribe, cmd_mystatus, cmd_clearhistory,
    cmd_features, cmd_featureon, cmd_featureoff, cmd_botstatus,
    cmd_blacklist,
    cmd_whitelist,
    cmd_overrides,
    cmd_setconfig,
    cmd_config_reset,
    callback_handler,
    cmd_addaccount,
    cmd_approve,
    cmd_config,
    cmd_health,
    cmd_help,
    cmd_kill,
    cmd_live_off,
    cmd_live_on,
    cmd_paper_off,
    cmd_paper_on,
    cmd_pnl,
    cmd_positions,
    cmd_recent,
    cmd_reject,
    cmd_removeaccount,
    cmd_score,
    cmd_signals,
    cmd_start,
    cmd_status,
    cmd_watchlist,
)
from app.config import settings
from app.logging_config import get_logger

log = get_logger(__name__)

_application: Application | None = None


def build_application() -> Application:
    """Construct the PTB Application and register all handlers."""
    app = (
        Application.builder()
        .token(settings.telegram_bot_token.get_secret_value())
        .build()
    )

    # ── Command handlers ────────────────────────────────────────────────
    handlers = [
        CommandHandler("start",         cmd_start),
        CommandHandler("help",          cmd_help),
        CommandHandler("health",        cmd_health),
        CommandHandler("status",        cmd_status),
        CommandHandler("watchlist",     cmd_watchlist),
        CommandHandler("addaccount",    cmd_addaccount),
        CommandHandler("removeaccount", cmd_removeaccount),
        CommandHandler("score",         cmd_score),
        CommandHandler("recent",        cmd_recent),
        CommandHandler("signals",       cmd_signals),
        CommandHandler("positions",     cmd_positions),
        CommandHandler("pnl",           cmd_pnl),
        CommandHandler("paper_on",      cmd_paper_on),
        CommandHandler("paper_off",     cmd_paper_off),
        CommandHandler("live_on",       cmd_live_on),
        CommandHandler("live_off",      cmd_live_off),
        CommandHandler("approve",       cmd_approve),
        CommandHandler("reject",        cmd_reject),
        CommandHandler("config",        cmd_config),
        CommandHandler("kill",          cmd_kill),
        CommandHandler("blacklist",    cmd_blacklist),
        CommandHandler("whitelist",    cmd_whitelist),
        CommandHandler("overrides",    cmd_overrides),
        CommandHandler("setconfig",    cmd_setconfig),
        CommandHandler("configreset",  cmd_config_reset),
        # Creator intent tracking + wallet linking
        CommandHandler("addcreator",    cmd_addcreator),
        CommandHandler("removecreator", cmd_removecreator),
        CommandHandler("creators",      cmd_creators),
        CommandHandler("creatorstatus", cmd_creatorstatus),
        CommandHandler("mode",          cmd_mode),
        CommandHandler("linkwallet",    cmd_linkwallet),
        CommandHandler("walletstatus",  cmd_walletstatus),
        CommandHandler("unlinkwallet",  cmd_unlinkwallet),
        # AI chat + premium
        CommandHandler("ai",            cmd_ai),
        CommandHandler("premium",       cmd_premium),
        CommandHandler("subscribe",     cmd_subscribe),
        CommandHandler("mystatus",      cmd_mystatus),
        CommandHandler("clearhistory",  cmd_clearhistory),
        # Admin feature toggles
        CommandHandler("features",     cmd_features),
        CommandHandler("featureon",    cmd_featureon),
        CommandHandler("featureoff",   cmd_featureoff),
        CommandHandler("botstatus",    cmd_botstatus),
    ]
    for handler in handlers:
        app.add_handler(handler)

    # ── Free-text → AI handler (non-command messages) ──────────────────
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_free_text,
    ))

    # ── Inline button callbacks ─────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(callback_handler))

    log.info("telegram_handlers_registered", count=len(handlers))
    return app


def get_application() -> Application:
    """Return the singleton Application, building it on first call."""
    global _application
    if _application is None:
        _application = build_application()
    return _application
