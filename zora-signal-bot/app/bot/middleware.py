"""
app/bot/middleware.py
─────────────────────────────────────────────────────────────────────────────
Telegram bot middleware:
  - AdminAuthMiddleware  – restricts commands to configured admin user IDs
  - AuditLogMiddleware   – persists every command invocation to DB
"""

from __future__ import annotations

from telegram import Update
from telegram.ext import BaseHandler, CallbackContext

from app.config import settings
from app.logging_config import get_logger

log = get_logger(__name__)

# Commands that any authenticated user (not just admins) may call
PUBLIC_COMMANDS = {"/start", "/help", "/health", "/status"}


async def check_admin(update: Update, context: CallbackContext) -> bool:
    """
    Return True if the requesting user is an authorised admin.
    Sends a denial message and returns False otherwise.
    Called manually at the top of admin command handlers.
    """
    user = update.effective_user
    if user is None:
        return False

    command = update.message.text.split()[0].lower() if update.message and update.message.text else ""

    if command in PUBLIC_COMMANDS:
        return True

    if settings.is_admin(user.id):
        return True

    log.warning("unauthorised_command", user_id=user.id, command=command)
    await update.message.reply_text(
        "⛔ Unauthorised. This bot is for authorised operators only."
    )
    return False
