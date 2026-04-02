"""
app/bot/handlers/commands.py
─────────────────────────────────────────────────────────────────────────────
All Telegram command handlers.
Each handler is a plain async function that receives (update, context).
Admin-gated commands call check_admin() before proceeding.

Kill switch state is stored in context.bot_data["kill_switch"] (bool).
Trading flags are runtime state in context.bot_data, seeding from settings.
"""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from app.bot.inline_buttons import (
    make_help_buttons,
    make_home_buttons,
    make_signals_overview_buttons,
    make_status_buttons,
)
from app.bot.middleware import check_admin
from app.bot.renderer import (
    format_help,
    format_recommendation_label,
    format_status,
    signal_inline_keyboard,
)
from app.config import settings
from app.logging_config import get_logger

log = get_logger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _bot_data_defaults(bot_data: dict) -> None:
    """Seed bot_data with defaults on first access."""
    bot_data.setdefault("kill_switch", False)
    bot_data.setdefault("paper_trading", settings.paper_trading_enabled)
    bot_data.setdefault("live_trading", settings.live_trading_enabled)


async def _reply(update: Update, text: str, **kwargs) -> None:  # type: ignore[no-untyped-def]
    await update.message.reply_text(text, parse_mode="HTML", **kwargs)


# ── Public commands ────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _bot_data_defaults(context.bot_data)
    user = update.effective_user
    await _reply(
        update,
        f"<b>Welcome, {user.first_name}</b>\n"
        "<i>Zora Signal Bot</i>\n\n"
        "A premium Telegram assistant for creator-led Zora signals and safety-gated trading.\n\n"
        "Track creators, review high-conviction setups, understand why a signal was flagged, "
        "and move into wallet or real-trade flows without leaving chat.\n\n"
        "Live actions stay guarded behind wallet linking, previews, and confirmations.\n\n"
        "You can just type naturally or start with one of the guided actions below.",
        reply_markup=make_home_buttons(),
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, format_help(), reply_markup=make_help_buttons())


async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lines = ["🏥 <b>Health Check</b>\n"]
    all_ok = True

    # DB
    try:
        from sqlalchemy import text
        from app.db.base import engine
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        lines.append("✅ Database: OK")
    except Exception as exc:
        lines.append(f"❌ Database: {exc}")
        all_ok = False

    # Redis
    try:
        import redis.asyncio as aioredis
        from app.config import settings as _s
        r = aioredis.from_url(_s.redis_url, socket_connect_timeout=2)
        await r.ping()
        await r.aclose()
        lines.append("✅ Redis: OK")
    except Exception as exc:
        lines.append(f"❌ Redis: {exc}")
        all_ok = False

    # Social provider check
    from app.config import settings as _s
    if _s.social_provider == "socialdata":
        sd_ok = bool(_s.socialdata_api_key)
        lines.append(f"{'✅' if sd_ok else '❌'} SocialData API: {'connected' if sd_ok else 'NOT SET'}")
    else:
        x_ok = bool(_s.x_bearer_token)
        lines.append(f"{'✅' if x_ok else '⚠️'} X bearer token: {'configured' if x_ok else 'NOT SET'}")

    # Zora
    zora_ok = bool(_s.zora_api_base_url)
    lines.append(f"{'✅' if zora_ok else '⚠️'} Zora base URL: {'configured' if zora_ok else 'NOT SET'}")

    # LLM
    llm_status = f"{'enabled' if _s.llm_enabled else 'disabled'} ({_s.llm_provider})"
    lines.append(f"ℹ️ LLM: {llm_status}")

    lines.append("")
    lines.append("✅ All systems OK" if all_ok else "⚠️ Some checks failed")
    await _reply(update, "\n".join(lines))


# ── Admin-gated commands ───────────────────────────────────────────────────────

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_admin(update, context):
        return
    _bot_data_defaults(context.bot_data)

    from app.db.base import AsyncSessionLocal
    from app.db.repositories import SignalRepository
    from app.db.repositories.positions import PaperPositionRepository, LivePositionRepository
    async with AsyncSessionLocal() as session:
        open_paper = await PaperPositionRepository(session).count_open()
        open_live = await LivePositionRepository(session).count_open()
        sig_today = await SignalRepository(session).count_today()
    msg = format_status(
        paper_trading=context.bot_data["paper_trading"],
        live_trading=context.bot_data["live_trading"],
        open_paper_positions=open_paper,
        open_live_positions=open_live,
        total_signals_today=sig_today,
        kill_switch_active=context.bot_data["kill_switch"],
    )
    await _reply(update, msg, reply_markup=make_status_buttons())


async def cmd_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_admin(update, context):
        return
    from app.db.base import AsyncSessionLocal
    from app.db.repositories import MonitoredAccountRepository
    async with AsyncSessionLocal() as session:
        accounts = await MonitoredAccountRepository(session).get_active_accounts()
    if not accounts:
        await _reply(update, "📋 No monitored accounts yet. Use /addaccount @handle.")
        return
    lines = [f"📋 <b>Monitored accounts ({len(accounts)})</b>\n"]
    for a in accounts:
        bl = " 🚫" if a.is_blacklisted else ""
        fol = f"{a.follower_count:,}" if a.follower_count else "?"
        lines.append(f"• @{a.x_username}  ({fol} followers){bl}")
    await _reply(update, "\n".join(lines))


async def cmd_addaccount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_admin(update, context):
        return
    if not context.args:
        await _reply(update, "Usage: /addaccount @handle")
        return
    handle = context.args[0].lstrip("@")
    log.info("add_account_requested", handle=handle, user_id=update.effective_user.id)
    try:
        from app.integrations.x_client import get_x_client
        x = get_x_client()
    except RuntimeError:
        await _reply(update, "⚠️ X API not configured. Set X_BEARER_TOKEN in .env")
        return
    user = await x.get_user_by_username(handle)
    if user is None:
        await _reply(update, f"❌ @{handle} not found on X.")
        return
    from app.db.base import AsyncSessionLocal
    from app.db.models import MonitoredAccount
    from app.db.repositories import MonitoredAccountRepository
    async with AsyncSessionLocal() as session:
        repo = MonitoredAccountRepository(session)
        existing = await repo.get_by_x_user_id(user.id)
        if existing:
            if not existing.is_active:
                existing.is_active = True
                await repo.save(existing)
                await session.commit()
                await _reply(update, f"♻️ @{handle} re-activated in watchlist.")
            else:
                await _reply(update, f"ℹ️ @{handle} is already being monitored.")
            return
        account = MonitoredAccount(
            x_user_id=user.id,
            x_username=user.username,
            display_name=user.name,
            follower_count=user.public_metrics.followers_count,
        )
        await repo.add(account)
        await session.commit()
    await _reply(update,
        f"✅ Added @{handle} to watchlist.\n"
        f"Followers: {user.public_metrics.followers_count:,}")


async def cmd_removeaccount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_admin(update, context):
        return
    if not context.args:
        await _reply(update, "Usage: /removeaccount @handle")
        return
    handle = context.args[0].lstrip("@")
    from app.db.base import AsyncSessionLocal
    from app.db.repositories import MonitoredAccountRepository
    async with AsyncSessionLocal() as session:
        repo = MonitoredAccountRepository(session)
        account = await repo.get_by_x_username(handle)
        if account is None:
            await _reply(update, f"❌ @{handle} is not in the watchlist.")
            return
        account.is_active = False
        await repo.save(account)
        await session.commit()
    await _reply(update, f"🗑️ @{handle} removed from active watchlist.")


async def cmd_score(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_admin(update, context):
        return
    if not context.args:
        await _reply(update, "Usage: /score <post_url_or_id>")
        return
    target = context.args[0]
    log.info("manual_score_requested", target=target, user_id=update.effective_user.id)
    # Extract tweet ID from URL or bare ID
    import re as _re
    id_match = _re.search(r"(?:status/)?([0-9]{5,})", target)
    if not id_match:
        await _reply(update, "❌ Could not parse a tweet ID from that input.")
        return
    tweet_id = id_match.group(1)
    try:
        from app.integrations.x_client import get_x_client
        x = get_x_client()
    except RuntimeError:
        await _reply(update, "⚠️ X API not configured.")
        return
    await _reply(update, f"⏳ Fetching tweet <code>{tweet_id}</code>…")
    tweet = await x.get_tweet_by_id(tweet_id)
    if tweet is None:
        await _reply(update, "❌ Tweet not found.")
        return
    user = await x.get_user_by_id(tweet.author_id)
    if user is None:
        await _reply(update, "❌ Tweet author not found.")
        return
    from app.db.base import AsyncSessionLocal
    from app.db.models import Signal
    from app.scoring.pipeline import run_pipeline_for_tweet
    _bot_data_defaults(context.bot_data)
    async with AsyncSessionLocal() as session:
        signal_id = await run_pipeline_for_tweet(
            session=session,
            tweet=tweet,
            user=user,
            kill_switch=context.bot_data.get("kill_switch", False),
            paper_trading=context.bot_data.get("paper_trading", True),
            live_trading=context.bot_data.get("live_trading", False),
        )
        await session.commit()
    if signal_id is None:
        await _reply(update, "ℹ️ Post was already processed.")
        return
    async with AsyncSessionLocal() as session:
        sig = await session.get(Signal, signal_id)
    if sig is None:
        await _reply(update, "❌ Signal not found after scoring.")
        return
    from app.db.models import Recommendation
    icon = {"IGNORE":"🔇","WATCH":"👀","ALERT":"🚨",
            "PAPER_TRADE":"📝","LIVE_TRADE_READY":"⚡"}.get(sig.recommendation.value,"ℹ️")
    decision_label = format_recommendation_label(sig.recommendation)
    await _reply(update,
        f"{icon} <b>Score result for <code>{tweet_id}</code></b>\n\n"
        f"Deterministic: <b>{sig.deterministic_score:.1f}</b>\n"
        + (f"LLM:           <b>{sig.llm_score:.1f}</b>\n" if sig.llm_score else "")
        + f"Final:         <b>{sig.final_score:.1f}</b>\n"
        f"Decision:      <b>{decision_label}</b>\n"
        + (f"Risk notes:    {sig.risk_notes}" if sig.risk_notes else "")
        + f"\n<code>Signal ID: {sig.id}</code>"
    )


async def cmd_recent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_admin(update, context):
        return
    from app.db.base import AsyncSessionLocal
    from app.db.repositories import PostRepository
    async with AsyncSessionLocal() as session:
        posts = await PostRepository(session).get_recent(limit=10)
    if not posts:
        await _reply(update, "📰 No posts ingested yet.")
        return
    lines = ["📰 <b>Recent Posts</b>\n"]
    for p in posts:
        age = _age_label(p.posted_at)
        snippet = (p.text or "")[:80].replace("<", "&lt;").replace(">", "&gt;")
        lines.append(f"• <code>{p.x_post_id}</code> [{age}] {snippet}…")
    await _reply(update, "\n".join(lines))


async def cmd_signals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_admin(update, context):
        return
    from app.db.base import AsyncSessionLocal
    from app.db.repositories import SignalRepository
    async with AsyncSessionLocal() as session:
        signals = await SignalRepository(session).get_recent(limit=10)
    if not signals:
        await _reply(
            update,
            "<b>Top Signals</b>\n\nNo signals are live right now.\n\nTry again soon or track a creator first.",
            reply_markup=make_home_buttons(),
        )
        return
    lines = ["<b>Top Signals</b>\n"]
    for sig in signals:
        rec_icon = {"IGNORE": "🔇","WATCH": "👀","ALERT": "🚨",
                    "PAPER_TRADE": "📝","LIVE_TRADE_READY": "⚡"}.get(sig.recommendation.value, "ℹ️")
        rec_label = format_recommendation_label(sig.recommendation)
        lines.append(
            f"{rec_icon} <code>#{sig.id}</code>  "
            f"score=<b>{sig.final_score:.0f}</b>  {rec_label}"
        )
    lines.append("\nUse the buttons below to explain or review the top setup.")
    top_signal = {
        "id": signals[0].id,
        "coin_symbol": signals[0].coin.symbol if getattr(signals[0], "coin", None) else "UNKNOWN",
    }
    await _reply(
        update,
        "\n".join(lines),
        reply_markup=make_signals_overview_buttons(top_signal),
    )


async def cmd_positions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_admin(update, context):
        return
    from app.db.base import AsyncSessionLocal
    from app.db.repositories.positions import LivePositionRepository, PaperPositionRepository
    from app.db.repositories.coins import ZoraCoinRepository
    async with AsyncSessionLocal() as session:
        live_positions = await LivePositionRepository(session).get_open()
        simulation_positions = await PaperPositionRepository(session).get_open()
        coin_repo = ZoraCoinRepository(session)
        live_rows = []
        simulation_rows = []
        for p in live_positions:
            coin = await coin_repo.get(p.coin_id)
            sym = coin.symbol if coin else "???"
            live_rows.append((p, sym))
        for p in simulation_positions:
            coin = await coin_repo.get(p.coin_id)
            sym = coin.symbol if coin else "???"
            simulation_rows.append((p, sym))
    if not live_rows and not simulation_rows:
        await _reply(update, "📊 No open live positions. No fallback simulation positions either.")
        return
    lines = ["📊 <b>Open Positions</b>\n"]
    if live_rows:
        lines.append("<b>Live</b>")
    for p, sym in live_rows:
        age = _age_label(p.opened_at)
        entry_price = f"${p.entry_price_usd:.6f}" if p.entry_price_usd is not None else "pending"
        lines.append(
            f"• <code>#{p.id}</code> <b>{sym}</b>  "
            f"${p.size_usd:.0f} @ {entry_price}  [{age}]"
        )
    if simulation_rows:
        if live_rows:
            lines.append("")
        lines.append("<b>Fallback Simulation</b>")
        lines.append("<i>Admin-only testing path. Secondary to live execution.</i>")
    for p, sym in simulation_rows:
        age = _age_label(p.opened_at)
        lines.append(
            f"• <code>#{p.id}</code> <b>{sym}</b>  "
            f"${p.size_usd:.0f} @ ${p.entry_price_usd:.6f}  [{age}]  "
            f"SL={p.stop_loss_pct*100:.0f}% TP={p.take_profit_pct*100:.0f}%"
        )
    await _reply(update, "\n".join(lines))


async def cmd_pnl(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_admin(update, context):
        return
    from app.db.base import AsyncSessionLocal
    from app.db.repositories.positions import PaperPositionRepository
    async with AsyncSessionLocal() as session:
        s = await PaperPositionRepository(session).get_pnl_summary()
    if s["total_trades"] == 0:
        await _reply(update, "💰 No closed fallback simulation positions yet.")
        return
    pnl_color = "🟢" if s["total_pnl_usd"] >= 0 else "🔴"
    sign = "+" if s["total_pnl_usd"] >= 0 else ""
    msg = (
        "💰 <b>Fallback Simulation P&amp;L</b>\n"
        "<i>Admin-only testing summary</i>\n\n"
        f"Total trades:  <b>{s['total_trades']}</b>\n"
        f"Win / Loss:    <b>{s['winning_trades']} / {s['losing_trades']}</b>\n"
        f"Win rate:      <b>{s['win_rate_pct']:.1f}%</b>\n"
        f"Avg P&amp;L:   <b>{s['avg_pnl_pct']:+.2f}%</b>\n\n"
        f"{pnl_color} Total P&amp;L: <b>{sign}${s['total_pnl_usd']:.2f}</b>\n"
    )
    if s["best_trade_pnl_usd"] is not None:
        msg += f"Best:  <b>${s['best_trade_pnl_usd']:+.2f}</b>\n"
        msg += f"Worst: <b>${s['worst_trade_pnl_usd']:+.2f}</b>"
    await _reply(update, msg)


# ── Trading toggles ────────────────────────────────────────────────────────────

async def cmd_paper_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_admin(update, context):
        return
    _bot_data_defaults(context.bot_data)
    context.bot_data["paper_trading"] = True
    log.info("paper_trading_enabled", user_id=update.effective_user.id)
    await _reply(
        update,
        "📝 Fallback simulation <b>ENABLED</b>.\n"
        "This is an admin testing path and does not change the main live-trading product flow.",
    )


async def cmd_paper_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_admin(update, context):
        return
    _bot_data_defaults(context.bot_data)
    context.bot_data["paper_trading"] = False
    log.warning("paper_trading_disabled", user_id=update.effective_user.id)
    await _reply(update, "📝 Fallback simulation <b>DISABLED</b>.")


async def cmd_live_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_admin(update, context):
        return
    _bot_data_defaults(context.bot_data)

    if not settings.live_trading_enabled:
        await _reply(
            update,
            "⛔ Live trading is <b>disabled at the configuration level</b>.\n"
            "Set <code>LIVE_TRADING_ENABLED=true</code> in your environment and restart.",
        )
        return

    context.bot_data["live_trading"] = True
    log.warning("live_trading_enabled", user_id=update.effective_user.id)
    await _reply(
        update,
        "⚡ Live trading <b>ENABLED</b>.\n"
        "⚠️ All trades still require manual approval and configured safety gates.",
    )


async def cmd_live_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_admin(update, context):
        return
    _bot_data_defaults(context.bot_data)
    context.bot_data["live_trading"] = False
    log.warning("live_trading_disabled", user_id=update.effective_user.id)
    await _reply(update, "⚡ Live trading <b>DISABLED</b>.")


# ── Approval workflow ──────────────────────────────────────────────────────────

async def cmd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_admin(update, context):
        return
    if not context.args:
        await _reply(update, "Usage: /approve <signal_id>")
        return
    try:
        signal_id = int(context.args[0])
    except ValueError:
        await _reply(update, "❌ signal_id must be an integer.")
        return
    log.info("signal_approve_requested", signal_id=signal_id, user_id=update.effective_user.id)
    _bot_data_defaults(context.bot_data)
    if context.bot_data.get("kill_switch"):
        await _reply(update, "🛑 Kill switch is active — cannot approve trades.")
        return
    if not context.bot_data.get("paper_trading"):
        await _reply(
            update,
            "⚠️ Fallback simulation is disabled. Use /paper_on only if you need the admin test path.",
        )
        return
    from app.db.base import AsyncSessionLocal
    from app.trading.paper_engine import get_paper_engine
    engine = get_paper_engine()
    async with AsyncSessionLocal() as session:
        result = await engine.open_position(
            session=session,
            signal_id=signal_id,
            approved_by_user_id=update.effective_user.id,
            kill_switch=context.bot_data.get("kill_switch", False),
        )
        await session.commit()
    if result.success:
        await _reply(
            update,
            f"✅ Fallback simulation position opened. ID: <code>{result.position_id}</code>",
        )
    else:
        await _reply(update, f"⛔ Could not open position: {result.message}")


async def cmd_reject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_admin(update, context):
        return
    if not context.args:
        await _reply(update, "Usage: /reject <signal_id>")
        return
    try:
        signal_id = int(context.args[0])
    except ValueError:
        await _reply(update, "❌ signal_id must be an integer.")
        return
    log.info("signal_reject_requested", signal_id=signal_id, user_id=update.effective_user.id)
    from app.db.base import AsyncSessionLocal
    from app.db.repositories import SignalRepository
    from datetime import datetime, timezone
    async with AsyncSessionLocal() as session:
        sig_repo = SignalRepository(session)
        sig = await sig_repo.get(signal_id)
        if sig is None:
            await _reply(update, f"❌ Signal <code>{signal_id}</code> not found.")
            return
        if sig.is_approved is not None:
            status = "approved" if sig.is_approved else "already rejected"
            await _reply(update, f"ℹ️ Signal <code>{signal_id}</code> is {status}.")
            return
        sig.is_approved = False
        sig.approved_by = update.effective_user.id
        sig.approved_at = datetime.now(timezone.utc)
        await sig_repo.save(sig)
        await session.commit()
    await _reply(update, f"🙈 Signal <code>{signal_id}</code> rejected.")


# ── Admin controls ─────────────────────────────────────────────────────────────

async def cmd_config(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_admin(update, context):
        return
    s = settings

    # Fetch any live Redis overrides
    try:
        from app.config_manager import get_config_value, get_all_overrides
        overrides = get_all_overrides()
    except Exception:
        overrides = {}

    def _v(key: str, fmt: str = "{}") -> str:
        """Format a config value, marking overrides with ✏️."""
        val = overrides.get(key, getattr(s, key))
        tag = " ✏️" if key in overrides else ""
        return f"<code>{fmt.format(val)}</code>{tag}"

    msg = (
        "⚙️ <b>Current Configuration</b>  (✏️ = runtime override)\n\n"
        f"Environment:           <code>{s.app_env}</code>\n"
        f"Simulation fallback:   <code>{s.paper_trading_enabled}</code>\n"
        f"Live execution:        <code>{s.live_trading_enabled}</code>\n"
        f"LLM enabled:           <code>{s.llm_enabled}</code>\n\n"
        f"<b>Trade sizing</b>\n"
        f"  Fallback sim size:   {_v('paper_trade_size_usd', '${:.2f}')}\n"
        f"  Max position:        {_v('max_position_size_usd', '${:.2f}')}\n"
        f"  Max daily loss:      {_v('max_daily_loss_usd', '${:.2f}')}\n"
        f"  Max concurrent:      {_v('max_concurrent_positions')}\n\n"
        f"<b>Risk limits</b>\n"
        f"  Min liquidity:       {_v('min_liquidity_usd', '${:,.0f}')}\n"
        f"  Max slippage:        {_v('max_slippage_bps')} bps\n"
        f"  Launch lockout:      {_v('no_trade_after_launch_seconds')}s\n\n"
        f"<b>Score thresholds</b>\n"
        f"  IGNORE  &lt; {s.score_ignore_threshold}\n"
        f"  WATCH   &lt; {s.score_watch_threshold}\n"
        f"  ALERT   &lt; {_v('score_alert_threshold')}\n"
        f"  REVIEW  &lt; {_v('score_paper_trade_threshold')}\n"
        f"  LIVE    ≥ {_v('score_live_trade_threshold')}\n\n"
        f"Use /setconfig &lt;key&gt; &lt;value&gt; to change at runtime."
    )
    await _reply(update, msg)


async def cmd_kill(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_admin(update, context):
        return
    _bot_data_defaults(context.bot_data)
    context.bot_data["kill_switch"] = True
    context.bot_data["paper_trading"] = False
    context.bot_data["live_trading"] = False
    log.critical("KILL_SWITCH_ACTIVATED", user_id=update.effective_user.id)
    await _reply(
        update,
        "🛑 <b>KILL SWITCH ACTIVATED</b>\n\n"
        "All trading halted. No new signals will be acted upon.\n"
        "To resume: restart the bot and explicitly re-enable the required admin modes.",
    )


# ── Shared display helpers ────────────────────────────────────────────────────

def _age_label(dt) -> str:
    """Return a human-readable age string for a datetime."""
    if dt is None:
        return "?"
    from datetime import datetime, timezone
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - dt
    minutes = int(delta.total_seconds() / 60)
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h"
    return f"{hours // 24}d"


# ── Blacklist / whitelist commands (Phase 4) ──────────────────────────────────

async def cmd_blacklist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /blacklist @handle_or_address [reason]
    Blacklists an X account or contract address from all future signals.
    """
    if not await check_admin(update, context):
        return
    if not context.args:
        await _reply(update, "Usage: /blacklist @handle_or_0xaddress [reason]")
        return

    target = context.args[0].lstrip("@")
    reason = " ".join(context.args[1:]) if len(context.args) > 1 else None

    x_username = target if not target.startswith("0x") else None
    contract_address = target if target.startswith("0x") else None

    from app.db.base import AsyncSessionLocal
    from app.db.models import CreatorOverride
    from app.db.repositories.overrides import CreatorOverrideRepository

    async with AsyncSessionLocal() as session:
        repo = CreatorOverrideRepository(session)
        override = CreatorOverride(
            x_username=x_username,
            contract_address=contract_address,
            is_blacklisted=True,
            reason=reason,
            added_by=update.effective_user.id,
        )
        await repo.add(override)
        await session.commit()

    log.warning("blacklisted", target=target, added_by=update.effective_user.id, reason=reason)
    await _reply(update, f"🚫 Blacklisted: <code>{target}</code>" + (f"\nReason: {reason}" if reason else ""))


async def cmd_whitelist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /whitelist @handle [multiplier]
    Whitelists an account with an optional score multiplier (default 1.2).
    """
    if not await check_admin(update, context):
        return
    if not context.args:
        await _reply(update, "Usage: /whitelist @handle [score_multiplier]")
        return

    target = context.args[0].lstrip("@")
    try:
        multiplier = float(context.args[1]) if len(context.args) > 1 else 1.2
    except ValueError:
        await _reply(update, "❌ Multiplier must be a number (e.g. 1.2)")
        return
    if not (0.1 <= multiplier <= 3.0):
        await _reply(update, "❌ Multiplier must be between 0.1 and 3.0")
        return

    x_username = target if not target.startswith("0x") else None
    contract_address = target if target.startswith("0x") else None

    from app.db.base import AsyncSessionLocal
    from app.db.models import CreatorOverride
    from app.db.repositories.overrides import CreatorOverrideRepository

    async with AsyncSessionLocal() as session:
        repo = CreatorOverrideRepository(session)
        override = CreatorOverride(
            x_username=x_username,
            contract_address=contract_address,
            is_whitelisted=True,
            score_multiplier=multiplier,
            added_by=update.effective_user.id,
        )
        await repo.add(override)
        await session.commit()

    log.info("whitelisted", target=target, multiplier=multiplier)
    await _reply(update,
        f"✅ Whitelisted: <code>{target}</code>\n"
        f"Score multiplier: <b>×{multiplier:.1f}</b>")


async def cmd_overrides(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/overrides — list all active blacklist/whitelist entries."""
    if not await check_admin(update, context):
        return
    from app.db.base import AsyncSessionLocal
    from app.db.repositories.overrides import CreatorOverrideRepository

    async with AsyncSessionLocal() as session:
        overrides = await CreatorOverrideRepository(session).list_all()

    if not overrides:
        await _reply(update, "📋 No overrides configured.")
        return

    lines = [f"📋 <b>Overrides ({len(overrides)})</b>\n"]
    for o in overrides[:20]:
        target = o.x_username or o.contract_address or "?"
        flags = []
        if o.is_blacklisted:
            flags.append("🚫 BL")
        if o.is_whitelisted:
            flags.append("✅ WL")
        if o.score_multiplier and o.score_multiplier != 1.0:
            flags.append(f"×{o.score_multiplier:.1f}")
        flag_str = " ".join(flags) or "?"
        reason = f" — {o.reason}" if o.reason else ""
        lines.append(f"• <code>{target}</code> {flag_str}{reason}")
    await _reply(update, "\n".join(lines))


# ── Runtime config commands (Phase 4) ─────────────────────────────────────────

async def cmd_setconfig(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /setconfig <key> <value>
    Update a runtime config value (stored in Redis, no restart required).
    Example: /setconfig score_alert_threshold 60
    """
    if not await check_admin(update, context):
        return
    if len(context.args or []) < 2:
        from app.config_manager import _WRITABLE_KEYS
        key_list = "\n".join(f"  • {k}" for k in sorted(_WRITABLE_KEYS))
        await _reply(update, f"Usage: /setconfig &lt;key&gt; &lt;value&gt;\n\nWritable keys:\n{key_list}")
        return

    key, raw_val = context.args[0], context.args[1]
    from app.config_manager import set_config_value
    ok, msg = set_config_value(key, raw_val, update.effective_user.id)
    icon = "✅" if ok else "❌"
    await _reply(update, f"{icon} {msg}")


async def cmd_config_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /configreset <key>
    Clear a runtime override, reverting to the .env default.
    """
    if not await check_admin(update, context):
        return
    if not context.args:
        await _reply(update, "Usage: /configreset &lt;key&gt;")
        return
    from app.config_manager import clear_config_override
    ok, msg = clear_config_override(context.args[0], update.effective_user.id)
    icon = "✅" if ok else "❌"
    await _reply(update, f"{icon} {msg}")
