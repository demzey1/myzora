"""
app/jobs/tasks/wallet_tasks.py
─────────────────────────────────────────────────────────────────────────────
Celery tasks for wallet linking post-processing.
"""

from __future__ import annotations

import asyncio

from app.jobs.celery_app import celery_app
from app.logging_config import get_logger

log = get_logger(__name__)


@celery_app.task(name="app.jobs.tasks.wallet_tasks.sync_zora_profile_for_wallet")
def sync_zora_profile_for_wallet(telegram_user_id: int, wallet_address: str) -> dict:
    return asyncio.run(_async_sync_zora_profile(telegram_user_id, wallet_address))


async def _async_sync_zora_profile(telegram_user_id: int, wallet_address: str) -> dict:
    from datetime import datetime, timezone

    from app.db.base import AsyncSessionLocal
    from app.db.models import ZoraProfileLink
    from app.db.repositories.wallet import WalletLinkRepository, ZoraProfileLinkRepository
    from app.integrations.zora_client import get_zora_adapter

    adapter = get_zora_adapter()
    try:
        profile = await adapter.get_creator_profile(wallet_address)
    except Exception as exc:
        log.warning("zora_profile_sync_failed", wallet=wallet_address, error=str(exc))
        return {"error": str(exc)}

    if profile is None:
        log.debug("no_zora_profile_found", wallet=wallet_address)
        return {"found": False}

    async with AsyncSessionLocal() as session:
        zora_repo = ZoraProfileLinkRepository(session)
        existing = await zora_repo.get_by_wallet(wallet_address)

        now = datetime.now(timezone.utc)
        if existing:
            existing.zora_display_name = profile.display_name
            existing.zora_bio = profile.bio
            existing.zora_profile_url = profile.profile_url
            existing.zora_x_username = profile.x_username
            existing.last_synced_at = now
        else:
            zp = ZoraProfileLink(
                wallet_address=wallet_address.lower(),
                zora_display_name=profile.display_name,
                zora_bio=profile.bio,
                zora_profile_url=profile.profile_url,
                zora_x_username=profile.x_username,
                last_synced_at=now,
            )
            session.add(zp)
            await session.flush()

            # Link to wallet_link row
            wl_repo = WalletLinkRepository(session)
            wl = await wl_repo.get_verified_for_user(telegram_user_id)
            if wl:
                wl.zora_profile_id = zp.id

        await session.commit()

    log.info("zora_profile_synced", wallet=wallet_address)
    return {"found": True, "display_name": profile.display_name}


@celery_app.task(name="app.jobs.tasks.wallet_tasks.notify_wallet_linked_telegram")
def notify_wallet_linked_telegram(wallet_address: str) -> None:
    asyncio.run(_async_notify_linked(wallet_address))


async def _async_notify_linked(wallet_address: str) -> None:
    from app.bot.application import get_application
    from app.db.base import AsyncSessionLocal
    from app.db.repositories.wallet import WalletLinkRepository

    async with AsyncSessionLocal() as session:
        from sqlalchemy import select
        from app.db.models import WalletLink, WalletLinkStatus
        result = await session.execute(
            select(WalletLink).where(
                WalletLink.wallet_address == wallet_address.lower(),
                WalletLink.status == WalletLinkStatus.VERIFIED,
            ).order_by(WalletLink.verified_at.desc()).limit(1)
        )
        wl = result.scalar_one_or_none()

    if not wl:
        return

    short = f"{wallet_address[:6]}...{wallet_address[-4:]}"
    msg = (
        f"✅ <b>Wallet linked!</b>\n\n"
        f"Address: <code>{short}</code>\n\n"
        f"I'll now match Zora profiles and coins to this wallet.\n"
        f"Use /walletstatus to see your linked wallet.\n"
        f"Use /unlinkwallet to remove it."
    )

    tg_app = get_application()
    try:
        await tg_app.bot.send_message(
            chat_id=wl.telegram_user_id,
            text=msg,
            parse_mode="HTML",
        )
    except Exception as exc:
        log.warning("wallet_linked_notify_failed", error=str(exc))
