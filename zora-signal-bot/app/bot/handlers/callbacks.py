"""
app/bot/handlers/callbacks.py
─────────────────────────────────────────────────────────────────────────────
Handles inline keyboard button presses (callback queries).
Pattern: "<action>:<signal_id>"

Actions:
  approve_paper  — open a paper position for this signal
  approve_live   — queue a live trade (Phase 5)
  ignore         — mark signal as rejected
  refresh        — re-fetch coin price and update message
"""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from app.bot.middleware import check_admin
from app.logging_config import get_logger

log = get_logger(__name__)


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    
    # Route to different handlers based on callback data prefix
    # AI trading buttons use pipe-separated format: "action|param=value|param2=value2"
    # Admin buttons use colon format: "action:signal_id"
    
    # Check for AI trading button callbacks first
    if "|" in data:
        # AI trading/wallet button
        from app.bot.inline_buttons import generic_callback_handler as ai_callback_handler
        # Re-answer since we already answered above
        await ai_callback_handler(update, context)
        return
    
    # Legacy admin callbacks (approval, ignore, refresh)
    if ":" not in data:
        log.warning("unknown_callback_format", data=data)
        return
    
    # Admin-only callbacks
    if not await check_admin(update, context):
        await query.answer("⛔ Not authorised.", show_alert=True)
        return

    parts = data.split(":", 1)
    if len(parts) != 2:
        log.warning("unknown_callback", data=data)
        return

    action, signal_id_str = parts
    try:
        signal_id = int(signal_id_str)
    except ValueError:
        log.warning("invalid_callback_signal_id", data=data)
        return

    user_id = update.effective_user.id

    if action == "approve_paper":
        await _handle_approve_paper(query, context, signal_id, user_id)

    elif action == "approve_live":
        await _handle_approve_live(query, context, signal_id, user_id)

    elif action == "ignore":
        await _handle_ignore(query, signal_id, user_id)

    elif action == "refresh":
        await _handle_refresh(query, signal_id)

    else:
        log.warning("unknown_action", action=action, signal_id=signal_id)


async def _handle_approve_paper(query, context, signal_id: int, user_id: int) -> None:
    from app.bot.handlers.commands import _bot_data_defaults
    from app.db.base import AsyncSessionLocal
    from app.trading.paper_engine import get_paper_engine

    _bot_data_defaults(context.bot_data)

    if context.bot_data.get("kill_switch"):
        await query.answer("🛑 Kill switch active.", show_alert=True)
        return
    if not context.bot_data.get("paper_trading", True):
        await query.answer("Paper trading is disabled. Use /paper_on.", show_alert=True)
        return

    engine = get_paper_engine()
    async with AsyncSessionLocal() as session:
        result = await engine.open_position(
            session=session,
            signal_id=signal_id,
            approved_by_user_id=user_id,
            kill_switch=context.bot_data.get("kill_switch", False),
        )
        await session.commit()

    if result.success:
        log.info("inline_paper_approved", signal_id=signal_id, position_id=result.position_id)
        new_text = (
            (query.message.text or "")
            + f"\n\n✅ <b>Paper trade opened</b> — Position <code>#{result.position_id}</code>"
        )
        await query.edit_message_text(new_text, parse_mode="HTML")
    else:
        await query.answer(f"Blocked: {result.message}", show_alert=True)


async def _handle_ignore(query, signal_id: int, user_id: int) -> None:
    from app.db.base import AsyncSessionLocal
    from app.db.repositories import SignalRepository
    from datetime import datetime, timezone

    log.info("inline_ignore", signal_id=signal_id, user_id=user_id)
    async with AsyncSessionLocal() as session:
        sig_repo = SignalRepository(session)
        sig = await sig_repo.get(signal_id)
        if sig and sig.is_approved is None:
            sig.is_approved = False
            sig.approved_by = user_id
            sig.approved_at = datetime.now(timezone.utc)
            await sig_repo.save(sig)
            await session.commit()

    new_text = (query.message.text or "") + "\n\n🙈 <b>Ignored</b>"
    await query.edit_message_text(new_text, parse_mode="HTML")


async def _handle_refresh(query, signal_id: int) -> None:
    from app.db.base import AsyncSessionLocal
    from app.db.repositories import CoinMarketSnapshotRepository, SignalRepository, ZoraCoinRepository
    from app.integrations.zora_client import get_zora_adapter

    log.info("inline_refresh", signal_id=signal_id)
    async with AsyncSessionLocal() as session:
        sig = await SignalRepository(session).get(signal_id)
        if not sig or not sig.coin_id:
            await query.answer("No coin data available.", show_alert=True)
            return

        coin = await ZoraCoinRepository(session).get(sig.coin_id)
        if not coin:
            await query.answer("Coin not found.", show_alert=True)
            return

        # Fetch fresh market data
        zora = get_zora_adapter()
        fresh_market = await zora.get_coin_market_state(coin.contract_address)

        if fresh_market and fresh_market.price_usd:
            from app.db.models import CoinMarketSnapshot
            snap = CoinMarketSnapshot(
                coin_id=coin.id,
                price_usd=fresh_market.price_usd,
                liquidity_usd=fresh_market.liquidity_usd,
                volume_5m_usd=fresh_market.volume_5m_usd,
                slippage_bps_reference=fresh_market.slippage_bps_for_reference_trade,
            )
            session.add(snap)
            await session.commit()
            price_str = f"${fresh_market.price_usd:.6f}"
            liq_str = f"${fresh_market.liquidity_usd:,.0f}" if fresh_market.liquidity_usd else "N/A"
            await query.answer(
                f"Refreshed: price={price_str} liq={liq_str}", show_alert=True
            )
        else:
            await query.answer("Could not fetch fresh data.", show_alert=True)


async def _handle_approve_live(query, context, signal_id: int, user_id: int) -> None:
    from app.bot.handlers.commands import _bot_data_defaults
    from app.config import settings
    from app.db.base import AsyncSessionLocal
    from app.trading.live_execution import (
        LiveTradingDisabledError,
        get_live_position_manager,
    )

    _bot_data_defaults(context.bot_data)

    if not settings.live_trading_enabled:
        await query.answer(
            "⛔ Live trading is disabled at the config level. "
            "Set LIVE_TRADING_ENABLED=true to enable.",
            show_alert=True,
        )
        return

    if context.bot_data.get("kill_switch"):
        await query.answer("🛑 Kill switch is active.", show_alert=True)
        return

    if not context.bot_data.get("live_trading", False):
        await query.answer(
            "⚡ Live trading is OFF. Use /live_on to enable.", show_alert=True
        )
        return

    # Step 1: always dry-run first and show the preview
    manager = get_live_position_manager()
    async with AsyncSessionLocal() as session:
        # Mark signal as approved first
        from app.db.repositories import SignalRepository
        from datetime import datetime, timezone
        sig_repo = SignalRepository(session)
        sig = await sig_repo.get(signal_id)
        if sig is None:
            await query.answer("❌ Signal not found.", show_alert=True)
            return
        sig.is_approved = True
        sig.approved_by = user_id
        sig.approved_at = datetime.now(timezone.utc)
        await sig_repo.save(sig)

        dry_result = await manager.open_position(
            session=session,
            signal_id=signal_id,
            approved_by_user_id=user_id,
            dry_run=True,
            kill_switch=context.bot_data.get("kill_switch", False),
        )
        await session.commit()

    if not dry_result.success:
        await query.answer(f"⛔ Pre-trade check failed: {dry_result.message}", show_alert=True)
        return

    # Show dry-run preview and ask for final confirmation
    new_text = (
        (query.message.text or "")
        + f"\n\n⚡ <b>Live trade dry-run passed</b>\n"
        f"All risk checks cleared. Position would open for "
        f"<b>${settings.max_position_size_usd:.0f}</b>.\n\n"
        f"<i>Use /approve {signal_id} with live_on confirmed to execute.</i>"
    )
    await query.edit_message_text(new_text, parse_mode="HTML")
    log.warning(
        "live_approve_dry_run_shown",
        signal_id=signal_id,
        user_id=user_id,
    )
