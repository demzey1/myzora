"""
app/jobs/tasks/settlement.py
─────────────────────────────────────────────────────────────────────────────
Celery task that monitors all open paper positions on a schedule and
closes any that hit their stop-loss, take-profit, or timeout threshold.
"""

from __future__ import annotations

import asyncio

from app.jobs.celery_app import celery_app
from app.logging_config import get_logger

log = get_logger(__name__)


@celery_app.task(
    name="app.jobs.tasks.settlement.monitor_open_positions",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
)
def monitor_open_positions(self) -> dict:  # type: ignore[no-untyped-def]
    return asyncio.run(_async_monitor())


async def _async_monitor() -> dict:
    from app.db.base import AsyncSessionLocal
    from app.db.repositories.positions import PaperPositionRepository
    from app.db.repositories.coins import CoinMarketSnapshotRepository, ZoraCoinRepository
    from app.trading.paper_engine import get_paper_engine

    engine = get_paper_engine()
    closed_count = 0
    checked_count = 0

    async with AsyncSessionLocal() as session:
        pos_repo    = PaperPositionRepository(session)
        coin_repo   = ZoraCoinRepository(session)
        market_repo = CoinMarketSnapshotRepository(session)

        open_positions = await pos_repo.get_open()
        checked_count = len(open_positions)

        for position in open_positions:
            coin   = await coin_repo.get(position.coin_id)
            market = await market_repo.get_latest_for_coin(position.coin_id) if coin else None

            if market is None or market.price_usd is None:
                log.debug(
                    "settlement_skip_no_price",
                    position_id=position.id,
                    coin_id=position.coin_id,
                )
                continue

            exit_reason = await engine.check_exit_conditions(
                session=session,
                position=position,
                current_price_usd=market.price_usd,
            )

            if exit_reason:
                result = await engine.close_position(
                    session=session,
                    position_id=position.id,
                    exit_price_usd=market.price_usd,
                    exit_reason=exit_reason,
                )
                if result.success:
                    closed_count += 1
                    log.info(
                        "position_auto_closed",
                        position_id=position.id,
                        reason=exit_reason,
                        pnl_usd=result.pnl_usd,
                    )
                    # Notify admins
                    _notify_position_closed.apply_async(
                        kwargs={
                            "position_id": position.id,
                            "exit_reason": exit_reason,
                            "pnl_usd": result.pnl_usd,
                            "pnl_pct": result.pnl_pct,
                            "coin_symbol": coin.symbol if coin else "???",
                        },
                        queue="alerts",
                    )

        await session.commit()

    log.info("settlement_run", checked=checked_count, closed=closed_count)
    return {"checked": checked_count, "closed": closed_count}


@celery_app.task(name="app.jobs.tasks.settlement.notify_position_closed")
def _notify_position_closed(
    position_id: int,
    exit_reason: str,
    pnl_usd: float | None,
    pnl_pct: float | None,
    coin_symbol: str,
) -> None:
    asyncio.run(_async_notify_closed(position_id, exit_reason, pnl_usd, pnl_pct, coin_symbol))


async def _async_notify_closed(
    position_id: int,
    exit_reason: str,
    pnl_usd: float | None,
    pnl_pct: float | None,
    coin_symbol: str,
) -> None:
    from app.bot.application import get_application
    from app.config import settings

    icon_map = {
        "STOP_LOSS":   "🛑",
        "TAKE_PROFIT": "✅",
        "TIMEOUT":     "⏰",
        "MANUAL":      "🤚",
    }
    icon = icon_map.get(exit_reason, "📋")
    pnl_sign = "+" if (pnl_usd or 0) >= 0 else ""
    pnl_color = "🟢" if (pnl_usd or 0) >= 0 else "🔴"

    msg = (
        f"{icon} <b>Paper Position Closed</b>\n\n"
        f"Coin:    <code>{coin_symbol}</code>\n"
        f"Reason:  <b>{exit_reason}</b>\n"
        f"{pnl_color} P&amp;L: <b>{pnl_sign}${pnl_usd:.2f}</b> ({pnl_sign}{pnl_pct:.1f}%)\n"
        f"<code>Position ID: {position_id}</code>"
    )

    tg_app = get_application()
    for admin_id in settings.admin_user_ids:
        try:
            await tg_app.bot.send_message(
                chat_id=admin_id, text=msg, parse_mode="HTML"
            )
        except Exception as exc:
            log.warning("close_notify_failed", admin_id=admin_id, error=str(exc))

