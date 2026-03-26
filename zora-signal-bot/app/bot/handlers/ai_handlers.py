"""
app/bot/handlers/ai_handlers.py
─────────────────────────────────────────────────────────────────────────────
Handlers for:
  - Free-text messages → Claude Haiku AI response
  - /ai       — toggle AI on/off
  - /premium  — show premium info
  - /subscribe — start payment flow
  - /mystatus — show subscription status
  - /clearhistory — clear AI chat history
"""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from app.logging_config import get_logger

log = get_logger(__name__)


async def _reply(update: Update, text: str, **kw) -> None:  # type: ignore[no-untyped-def]
    await update.message.reply_text(text, parse_mode="HTML", **kw)


# ── Free-text message handler ─────────────────────────────────────────────────

async def handle_free_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle any non-command message as a conversational AI input.
    Routes to OpenAI Responses API for multi-turn chat with tool support.
    
    Accepts messages from any user (not admin-only).
    """
    if not update.message or not update.message.text:
        return

    user_id = update.effective_user.id
    text = update.message.text.strip()
    if not text:
        return

    # Check feature flag
    from app.config import settings
    from app.services.feature_flags import is_enabled

    if not settings.enable_conversational_mode or not is_enabled("ai"):
        await _reply(update, "🤖 Conversational AI is currently disabled.")
        return

    # Show typing indicator
    await update.message.chat.send_action("typing")

    # Send to assistant
    from app.bot.assistant import send_message_to_assistant

    try:
        response = await send_message_to_assistant(user_id, text)
        
        if response.error:
            await _reply(update, f"❌ {response.error}")
        else:
            await _reply(update, response.text)

    except Exception as exc:
        log.exception("handle_free_text_error", exc_info=True)
        await _reply(update, "Sorry, I encountered an error. Please try again.")


# ── /ai command ───────────────────────────────────────────────────────────────

async def cmd_ai(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/ai — toggle AI assistant on or off."""
    from app.config import settings
    user_id = update.effective_user.id
    if not settings.is_admin(user_id):
        await _reply(update, "⛔ Unauthorised.")
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
            "🤖 <b>AI assistant is ON</b>\n\n"
            "Just type any message and I'll respond.\n"
            "I can explain signals, discuss Zora coins, and remember your preferences.\n\n"
            "Send /ai again to turn off."
        )
    else:
        await _reply(update, "🤖 AI assistant is OFF. Send /ai to turn back on.")


# ── /premium command ──────────────────────────────────────────────────────────

async def cmd_premium(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/premium — show what premium includes and how to subscribe."""
    from app.config import settings
    user_id = update.effective_user.id
    if not settings.is_admin(user_id):
        await _reply(update, "⛔ Unauthorised.")
        return

    from app.db.base import AsyncSessionLocal
    from app.db.repositories.ai import UserSubscriptionRepository
    from app.services.premium import PREMIUM_PRICE_USD, SUBSCRIPTION_DAYS

    async with AsyncSessionLocal() as session:
        repo = UserSubscriptionRepository(session)
        is_premium = await repo.is_premium(user_id)
        sub = await repo.get_for_user(user_id)

    if is_premium and sub and sub.premium_expires_at:
        from app.bot.handlers.commands import _age_label
        await _reply(
            update,
            f"🌟 <b>You are Premium</b>\n\n"
            f"Expires: {sub.premium_expires_at.strftime('%Y-%m-%d')}\n\n"
            f"Use /subscribe to extend."
        )
        return

    payment_configured = bool(settings.premium_payment_address)

    await _reply(
        update,
        f"🌟 <b>Zora Signal Bot Premium</b>\n\n"
        f"<b>Free tier (current):</b>\n"
        f"✅ Creator tracking\n"
        f"✅ Real-time signal alerts\n"
        f"✅ Zora coin discovery\n"
        f"✅ AI chat (20 messages/day)\n"
        f"❌ Auto-trading\n"
        f"❌ Extended AI context\n\n"
        f"<b>Premium — ${PREMIUM_PRICE_USD:.2f}/month:</b>\n"
        f"✅ Everything in Free\n"
        f"⚡ Auto-trading (link wallet + it executes for you)\n"
        f"🤖 AI chat (200 messages/day, deeper context)\n"
        f"📊 Extended signal history\n"
        f"🔔 Priority alerts\n\n"
        + (f"Use /subscribe to pay ${PREMIUM_PRICE_USD:.2f} USDC or ETH on Base."
           if payment_configured
           else "⚠️ Payments not yet configured. Contact the operator.")
    )


# ── /subscribe command ────────────────────────────────────────────────────────

async def cmd_subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/subscribe — initiate crypto payment for premium."""
    from app.config import settings
    user_id = update.effective_user.id
    if not settings.is_admin(user_id):
        await _reply(update, "⛔ Unauthorised.")
        return

    from app.services.feature_flags import is_enabled
    if not is_enabled("payments"):
        await _reply(update, "💳 Premium subscriptions are not available right now.")
        return

    if not settings.premium_payment_address:
        await _reply(
            update,
            "⚠️ Premium payments not configured yet.\n"
            "The operator needs to set PREMIUM_PAYMENT_ADDRESS in .env."
        )
        return

    from app.db.base import AsyncSessionLocal
    from app.db.repositories.ai import UserSubscriptionRepository
    from app.services.premium import create_payment_request, PREMIUM_PRICE_USD

    async with AsyncSessionLocal() as session:
        repo = UserSubscriptionRepository(session)
        if await repo.is_premium(user_id):
            await _reply(update, "🌟 You're already Premium! Use /premium to see details.")
            return

        result = await create_payment_request(session, user_id)
        await session.commit()

    if "error" in result:
        await _reply(update, f"❌ {result['error']}")
        return

    addr = result["payment_address"]
    short_addr = f"{addr[:6]}...{addr[-4:]}"
    eth_line = (
        f"OR <b>{result['eth_amount']} ETH</b> "
        f"(≈ ${result['eth_price_usd']:.0f}/ETH)\n"
        if result.get("eth_amount") else ""
    )

    await _reply(
        update,
        f"💳 <b>Premium Payment</b>\n\n"
        f"Send exactly:\n"
        f"<b>${result['usdc_amount']:.2f} USDC</b>\n"
        f"{eth_line}\n"
        f"To this address on <b>Base network</b>:\n"
        f"<code>{addr}</code>\n\n"
        f"USDC contract: <code>{result['usdc_contract']}</code>\n\n"
        f"⏰ Payment window: 60 minutes\n"
        f"✅ Bot confirms automatically after on-chain detection\n"
        f"📅 Gets you {result['subscription_days']} days of Premium\n\n"
        f"<b>⚠️ Send on Base (chain ID 8453) only. Other chains = lost funds.</b>"
    )


# ── /mystatus command ─────────────────────────────────────────────────────────

async def cmd_mystatus(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/mystatus — show user's subscription and AI usage."""
    from app.config import settings
    user_id = update.effective_user.id
    if not settings.is_admin(user_id):
        await _reply(update, "⛔ Unauthorised.")
        return

    from app.db.base import AsyncSessionLocal
    from app.db.repositories.ai import (
        UserSubscriptionRepository,
        ChatMessageRepository,
        UserPreferencesRepository,
    )
    from app.services.ai_chat import FREE_DAILY_LIMIT, PREMIUM_DAILY_LIMIT

    async with AsyncSessionLocal() as session:
        sub_repo  = UserSubscriptionRepository(session)
        msg_repo  = ChatMessageRepository(session)
        pref_repo = UserPreferencesRepository(session)

        sub        = await sub_repo.get_or_create(user_id)
        is_premium = await sub_repo.is_premium(user_id)
        msgs_today = await msg_repo.count_today(user_id)
        prefs      = await pref_repo.get_all(user_id)

    tier_label  = "🌟 Premium" if is_premium else "🆓 Free"
    limit       = PREMIUM_DAILY_LIMIT if is_premium else FREE_DAILY_LIMIT
    ai_status   = "ON 🟢" if sub.ai_enabled else "OFF 🔴"

    lines = [
        f"👤 <b>Your Status</b>\n",
        f"Tier:         {tier_label}",
    ]

    if is_premium and sub.premium_expires_at:
        lines.append(f"Expires:      {sub.premium_expires_at.strftime('%Y-%m-%d')}")

    lines += [
        f"AI chat:      {ai_status}",
        f"AI today:     {msgs_today}/{limit} messages",
        "",
    ]

    if prefs:
        lines.append("<b>Remembered preferences:</b>")
        for k, v in prefs.items():
            lines.append(f"  • {k}: {v}")
    else:
        lines.append("<i>No preferences saved yet — just tell me your preferences in chat.</i>")

    if not is_premium:
        lines.append("\nUse /premium to see what Premium includes.")

    await _reply(update, "\n".join(lines))


# ── /clearhistory command ─────────────────────────────────────────────────────

async def cmd_clearhistory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/clearhistory — wipe AI chat history for a fresh start."""
    from app.config import settings
    user_id = update.effective_user.id
    if not settings.is_admin(user_id):
        await _reply(update, "⛔ Unauthorised.")
        return

    from app.db.base import AsyncSessionLocal
    from app.db.repositories.ai import ChatMessageRepository

    async with AsyncSessionLocal() as session:
        repo  = ChatMessageRepository(session)
        count = await repo.clear_history(user_id)
        await session.commit()

    await _reply(
        update,
        f"🗑️ Cleared {count} messages from AI chat history.\n"
        "Starting fresh next time you chat."
    )
