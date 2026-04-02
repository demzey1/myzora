"""
app/jobs/tasks/alerts.py
─────────────────────────────────────────────────────────────────────────────
Celery tasks for Telegram alert delivery.

send_signal_alert   — formats and dispatches a signal alert to all admins
send_daily_summary  — daily P&L summary sent at midnight UTC
"""

from __future__ import annotations

import asyncio

from app.jobs.celery_app import celery_app
from app.logging_config import get_logger

log = get_logger(__name__)


@celery_app.task(
    name="app.jobs.tasks.alerts.send_signal_alert",
    bind=True,
    max_retries=3,
    default_retry_delay=10,
)
def send_signal_alert(self, signal_id: int) -> dict:  # type: ignore[no-untyped-def]
    return asyncio.run(_async_send_signal_alert(signal_id))


async def _async_send_signal_alert(signal_id: int) -> dict:
    from app.bot.application import get_application
    from app.bot.renderer import format_signal_alert, signal_inline_keyboard
    from app.config import settings
    from app.db.base import AsyncSessionLocal
    from app.db.models import Recommendation
    from app.db.repositories import (
        CoinMarketSnapshotRepository,
        SignalRepository,
        ZoraCoinRepository,
    )
    from app.db.repositories.accounts import MonitoredAccountRepository
    from app.db.repositories.posts import PostRepository

    async with AsyncSessionLocal() as session:
        sig_repo   = SignalRepository(session)
        post_repo  = PostRepository(session)
        coin_repo  = ZoraCoinRepository(session)
        market_repo = CoinMarketSnapshotRepository(session)
        acct_repo  = MonitoredAccountRepository(session)

        signal = await sig_repo.get(signal_id)
        if signal is None:
            log.warning("alert_signal_not_found", signal_id=signal_id)
            return {"status": "not_found"}

        # Gather context
        post    = await post_repo.get(signal.post_id) if signal.post_id else None
        account = await acct_repo.get(post.account_id) if post else None
        coin    = await coin_repo.get(signal.coin_id) if signal.coin_id else None
        market  = await market_repo.get_latest_for_coin(coin.id) if coin else None

        # Engagement velocity label
        vel_label = _velocity_label(signal, post)

        msg = format_signal_alert(
            signal=signal,
            x_username=account.x_username if account else "unknown",
            follower_count=account.follower_count if account else None,
            post_text=post.text or "" if post else "",
            post_age_dt=post.posted_at if post else None,
            engagement_velocity=vel_label,
            coin_symbol=coin.symbol if coin else "???",
            coin_age_dt=coin.launched_at if coin else None,
            price_usd=market.price_usd if market else None,
            liquidity_usd=market.liquidity_usd if market else None,
            slippage_bps=market.slippage_bps_reference if market else None,
            volume_5m_usd=market.volume_5m_usd if market else None,
        )

        include_live = (
            settings.live_trading_enabled
            and signal.recommendation == Recommendation.LIVE_TRADE_READY
        )
        keyboard = signal_inline_keyboard(signal_id=signal_id, include_live=include_live)

    # Send to every admin
    tg_app = get_application()
    sent_to = 0
    first_msg_id = None

    for admin_id in settings.admin_user_ids:
        try:
            sent = await tg_app.bot.send_message(
                chat_id=admin_id,
                text=msg,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            if first_msg_id is None:
                first_msg_id = sent.message_id
            sent_to += 1
        except Exception as exc:
            log.warning("alert_send_failed", admin_id=admin_id, error=str(exc))

    # Store the telegram message_id for button callback routing
    if first_msg_id:
        async with AsyncSessionLocal() as session:
            sig_repo = SignalRepository(session)
            sig = await sig_repo.get(signal_id)
            if sig:
                sig.telegram_message_id = first_msg_id
                await sig_repo.save(sig)
            await session.commit()

    log.info("alert_sent", signal_id=signal_id, sent_to=sent_to)
    return {"status": "ok", "sent_to": sent_to}


@celery_app.task(
    name="app.jobs.tasks.alerts.send_daily_summary",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
)
def send_daily_summary(self) -> dict:  # type: ignore[no-untyped-def]
    return asyncio.run(_async_send_daily_summary())


async def _async_send_daily_summary() -> dict:
    from app.bot.application import get_application
    from app.config import settings
    from app.db.base import AsyncSessionLocal
    from app.db.repositories import SignalRepository
    from app.db.repositories.positions import PaperPositionRepository

    async with AsyncSessionLocal() as session:
        pos_repo = PaperPositionRepository(session)
        sig_repo = SignalRepository(session)

        summary  = await pos_repo.get_pnl_summary()
        sig_count = await sig_repo.count_today()
        closed_today = await pos_repo.get_closed_today()

    # Format message
    pnl_sign = "+" if summary["total_pnl_usd"] >= 0 else ""
    pnl_color = "🟢" if summary["total_pnl_usd"] >= 0 else "🔴"

    closed_rows = ""
    for p in closed_today[:10]:  # cap at 10 rows
        sign = "+" if (p.pnl_usd or 0) >= 0 else ""
        closed_rows += (
            f"  • {p.exit_reason or 'CLOSED'}  "
            f"{sign}${p.pnl_usd:.2f} ({sign}{p.pnl_pct:.1f}%)\n"
        )

    msg = (
        "📊 <b>Daily Paper Trading Summary</b>\n\n"
        f"Signals today:     <b>{sig_count}</b>\n"
        f"Trades closed:     <b>{summary['total_trades']}</b>\n"
        f"Win / Loss:        <b>{summary['winning_trades']} / {summary['losing_trades']}</b>\n"
        f"Win rate:          <b>{summary['win_rate_pct']:.1f}%</b>\n"
        f"Avg trade P&amp;L: <b>{summary['avg_pnl_pct']:+.2f}%</b>\n\n"
        f"{pnl_color} <b>Total P&amp;L: {pnl_sign}${summary['total_pnl_usd']:.2f}</b>\n"
    )
    if summary["best_trade_pnl_usd"] is not None:
        msg += f"Best trade:  ${summary['best_trade_pnl_usd']:+.2f}\n"
        msg += f"Worst trade: ${summary['worst_trade_pnl_usd']:+.2f}\n"
    if closed_rows:
        msg += f"\n<b>Closed today:</b>\n{closed_rows}"

    tg_app = get_application()
    sent_to = 0
    for admin_id in settings.admin_user_ids:
        try:
            await tg_app.bot.send_message(
                chat_id=admin_id, text=msg, parse_mode="HTML"
            )
            sent_to += 1
        except Exception as exc:
            log.warning("daily_summary_send_failed", admin_id=admin_id, error=str(exc))

    log.info("daily_summary_sent", sent_to=sent_to)
    return {"status": "ok", "sent_to": sent_to}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _velocity_label(signal, post) -> str:
    """Classify engagement velocity into a human-readable label."""
    if post is None:
        return "Unknown"
    total = (post.like_count or 0) + (post.repost_count or 0)
    if total > 1000:
        return "Very High 🔥"
    if total > 300:
        return "High"
    if total > 50:
        return "Medium"
    return "Low"

