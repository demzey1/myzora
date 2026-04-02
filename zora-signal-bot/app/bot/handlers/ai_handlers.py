"""
Handlers for conversational AI chat and premium commands.
"""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from app.logging_config import get_logger

log = get_logger(__name__)


async def _reply(update: Update, text: str, reply_markup=None, **kw) -> None:  # type: ignore[no-untyped-def]
    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=reply_markup,
        **kw,
    )


async def handle_free_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle plain chat messages as conversational assistant input."""
    if not update.message or not update.message.text:
        return

    user_id = update.effective_user.id
    text = update.message.text.strip()
    if not text:
        return

    from app.config import settings
    from app.services.feature_flags import is_enabled

    if not settings.enable_conversational_mode or not is_enabled("ai"):
        await _reply(update, "Conversational AI is currently disabled.")
        return

    await update.message.chat.send_action("typing")

    from app.bot.assistant import send_message_to_assistant
    from app.bot.inline_buttons import (
        make_creator_tracked_buttons,
        make_help_buttons,
        make_home_buttons,
        make_positions_buttons,
        make_settings_buttons,
        make_signals_overview_buttons,
        make_status_buttons,
        make_trade_preview_buttons,
        make_wallet_link_button,
        make_wallet_status_buttons,
    )

    try:
        response = await send_message_to_assistant(user_id, text)

        reply_markup = None
        button_data = response.inline_buttons_data or {}
        if button_data.get("type") == "trade_preview":
            reply_markup = make_trade_preview_buttons(
                button_data.get("coin_symbol", "UNKNOWN"),
                button_data.get("action", "buy"),
                float(button_data.get("amount_usd", 0) or 0),
            )
        elif button_data.get("type") == "wallet_link" and button_data.get("url"):
            reply_markup = make_wallet_link_button(button_data["url"])
        elif button_data.get("type") == "home_menu":
            reply_markup = make_home_buttons()
        elif button_data.get("type") == "signals_overview":
            reply_markup = make_signals_overview_buttons(button_data.get("top_signal"))
        elif button_data.get("type") == "creator_tracked":
            reply_markup = make_creator_tracked_buttons(button_data.get("x_username", "creator"))
        elif button_data.get("type") == "wallet_status":
            reply_markup = make_wallet_status_buttons()
        elif button_data.get("type") == "positions":
            reply_markup = make_positions_buttons()
        elif button_data.get("type") == "settings":
            reply_markup = make_settings_buttons()
        elif button_data.get("type") == "help":
            reply_markup = make_help_buttons()
        elif button_data.get("type") == "status":
            reply_markup = make_status_buttons()

        if response.error:
            log.warning(
                "assistant_response_error_fallback",
                error=response.error,
                user_id=user_id,
                message_text=text,
            )

        await _reply(
            update,
            response.text or "I hit a temporary issue. Try one of the actions below.",
            reply_markup=reply_markup,
        )

    except Exception as exc:
        from app.bot.inline_buttons import make_home_buttons

        log.exception(
            "handle_free_text_error",
            error=str(exc),
            user_id=user_id,
            message_text=text,
        )
        await _reply(
            update,
            "I hit a temporary issue, but I’m still here.\n\nTry one of the guided actions below.",
            reply_markup=make_home_buttons(),
        )


async def cmd_ai(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/ai - toggle AI assistant on or off."""
    from app.config import settings
    user_id = update.effective_user.id
    if not settings.is_admin(user_id):
        await _reply(update, "Unauthorized.")
        return

    from app.db.base import AsyncSessionLocal
    from app.db.repositories.ai import UserSubscriptionRepository

    async with AsyncSessionLocal() as session:
        repo = UserSubscriptionRepository(session)
        sub = await repo.get_or_create(user_id)
        new_state = not sub.ai_enabled
        await repo.set_ai_enabled(user_id, new_state)
        await session.commit()

    if new_state:
        await _reply(
            update,
            "<b>AI assistant is ON</b>\n\n"
            "Just type any message and I'll respond.\n"
            "I can explain signals, discuss Zora coins, and remember your preferences.\n\n"
            "Send /ai again to turn off.",
        )
    else:
        await _reply(update, "AI assistant is OFF. Send /ai to turn back on.")


async def cmd_premium(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from app.config import settings
    user_id = update.effective_user.id
    if not settings.is_admin(user_id):
        await _reply(update, "Unauthorized.")
        return

    from app.db.base import AsyncSessionLocal
    from app.db.repositories.ai import UserSubscriptionRepository
    from app.services.premium import PREMIUM_PRICE_USD

    async with AsyncSessionLocal() as session:
        repo = UserSubscriptionRepository(session)
        is_premium = await repo.is_premium(user_id)
        sub = await repo.get_for_user(user_id)

    if is_premium and sub and sub.premium_expires_at:
        await _reply(
            update,
            f"<b>You are Premium</b>\n\nExpires: {sub.premium_expires_at.strftime('%Y-%m-%d')}\n\nUse /subscribe to extend.",
        )
        return

    payment_configured = bool(settings.premium_payment_address)
    await _reply(
        update,
        f"<b>Zora Signal Bot Premium</b>\n\n"
        f"<b>Free tier:</b>\n"
        f"AI chat, creator tracking, and signal discovery\n\n"
        f"<b>Premium - ${PREMIUM_PRICE_USD:.2f}/month:</b>\n"
        f"More AI usage, richer history, and priority features\n\n"
        + (f"Use /subscribe to pay ${PREMIUM_PRICE_USD:.2f} on Base." if payment_configured else "Payments are not configured yet."),
    )


async def cmd_subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from app.config import settings
    user_id = update.effective_user.id
    if not settings.is_admin(user_id):
        await _reply(update, "Unauthorized.")
        return

    from app.services.feature_flags import is_enabled
    if not is_enabled("payments"):
        await _reply(update, "Premium subscriptions are not available right now.")
        return

    if not settings.premium_payment_address:
        await _reply(update, "Premium payments are not configured yet.")
        return

    from app.db.base import AsyncSessionLocal
    from app.db.repositories.ai import UserSubscriptionRepository
    from app.services.premium import create_payment_request

    async with AsyncSessionLocal() as session:
        repo = UserSubscriptionRepository(session)
        if await repo.is_premium(user_id):
            await _reply(update, "You're already Premium.")
            return
        result = await create_payment_request(session, user_id)
        await session.commit()

    if "error" in result:
        await _reply(update, f"Error: {result['error']}")
        return

    eth_line = (
        f"OR <b>{result['eth_amount']} ETH</b> (approx. ${result['eth_price_usd']:.0f}/ETH)\n"
        if result.get("eth_amount")
        else ""
    )
    await _reply(
        update,
        f"<b>Premium Payment</b>\n\n"
        f"Send exactly <b>${result['usdc_amount']:.2f} USDC</b>\n"
        f"{eth_line}"
        f"to <code>{result['payment_address']}</code> on <b>Base</b>.\n\n"
        f"Window: 60 minutes\n"
        f"Subscription: {result['subscription_days']} days\n\n"
        f"<b>Send on Base only.</b>",
    )


async def cmd_mystatus(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from app.config import settings
    user_id = update.effective_user.id
    if not settings.is_admin(user_id):
        await _reply(update, "Unauthorized.")
        return

    from app.db.base import AsyncSessionLocal
    from app.db.repositories.ai import (
        ChatMessageRepository,
        UserPreferencesRepository,
        UserSubscriptionRepository,
    )
    from app.services.ai_chat import FREE_DAILY_LIMIT, PREMIUM_DAILY_LIMIT

    async with AsyncSessionLocal() as session:
        sub_repo = UserSubscriptionRepository(session)
        msg_repo = ChatMessageRepository(session)
        pref_repo = UserPreferencesRepository(session)

        sub = await sub_repo.get_or_create(user_id)
        is_premium = await sub_repo.is_premium(user_id)
        msgs_today = await msg_repo.count_today(user_id)
        prefs = await pref_repo.get_all(user_id)

    tier_label = "Premium" if is_premium else "Free"
    limit = PREMIUM_DAILY_LIMIT if is_premium else FREE_DAILY_LIMIT
    ai_status = "ON" if sub.ai_enabled else "OFF"

    lines = [
        "<b>Your Status</b>",
        f"Tier: {tier_label}",
        f"AI chat: {ai_status}",
        f"AI today: {msgs_today}/{limit}",
        "",
    ]
    if prefs:
        lines.append("<b>Remembered preferences:</b>")
        for key, value in prefs.items():
            lines.append(f"- {key}: {value}")
    else:
        lines.append("<i>No preferences saved yet.</i>")

    await _reply(update, "\n".join(lines))


async def cmd_clearhistory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from app.config import settings
    user_id = update.effective_user.id
    if not settings.is_admin(user_id):
        await _reply(update, "Unauthorized.")
        return

    from app.db.base import AsyncSessionLocal
    from app.db.repositories.ai import ChatMessageRepository

    async with AsyncSessionLocal() as session:
        repo = ChatMessageRepository(session)
        count = await repo.clear_history(user_id)
        await session.commit()

    await _reply(update, f"Cleared {count} messages from AI chat history.")
