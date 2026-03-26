"""
app/jobs/tasks/premium_tasks.py
Celery tasks for premium payment monitoring and notifications.
"""

from __future__ import annotations

import asyncio

from app.jobs.celery_app import celery_app
from app.logging_config import get_logger

log = get_logger(__name__)


@celery_app.task(name="app.jobs.tasks.premium_tasks.check_pending_payments")
def check_pending_payments() -> dict:
    """Check all pending premium payments on Base chain."""
    return asyncio.run(_async_check_payments())


async def _async_check_payments() -> dict:
    from app.db.base import AsyncSessionLocal
    from app.db.repositories.ai import PremiumPaymentRepository
    from app.services.premium import verify_payment_onchain

    async with AsyncSessionLocal() as session:
        repo = PremiumPaymentRepository(session)
        pending = await repo.get_all_pending()

        confirmed = 0
        for payment in pending:
            try:
                ok = await verify_payment_onchain(payment.id, session)
                if ok:
                    confirmed += 1
            except Exception as exc:
                log.warning("payment_check_error",
                           payment_id=payment.id, error=str(exc))

        await session.commit()

    log.info("payment_check_complete",
             pending=len(pending), confirmed=confirmed)
    return {"checked": len(pending), "confirmed": confirmed}


@celery_app.task(name="app.jobs.tasks.premium_tasks.notify_premium_activated")
def notify_premium_activated(telegram_user_id: int, days: int) -> None:
    asyncio.run(_async_notify_premium(telegram_user_id, days))


async def _async_notify_premium(telegram_user_id: int, days: int) -> None:
    from app.bot.application import get_application

    msg = (
        "🌟 <b>Premium Activated!</b>\n\n"
        f"Your subscription is active for <b>{days} days</b>.\n\n"
        "You now have access to:\n"
        "⚡ Auto-trading with your linked wallet\n"
        "🤖 Extended AI chat (200 messages/day)\n"
        "📊 Deeper signal context\n"
        "🔔 Priority alerts\n\n"
        "Use /status to see your subscription details.\n"
        "Use /ai to chat with the AI assistant."
    )

    tg_app = get_application()
    try:
        await tg_app.bot.send_message(
            chat_id=telegram_user_id,
            text=msg,
            parse_mode="HTML",
        )
    except Exception as exc:
        log.warning("premium_notify_send_failed", error=str(exc))
