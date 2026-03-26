"""
app/bot/handlers/creator_commands.py
─────────────────────────────────────────────────────────────────────────────
Telegram command handlers for:
  Creator tracking:  /addcreator /removecreator /creators /creatorstatus /mode
  Wallet linking:    /linkwallet /walletstatus /unlinkwallet
"""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from app.bot.middleware import check_admin
from app.logging_config import get_logger

log = get_logger(__name__)


async def _reply(update: Update, text: str, **kw) -> None:  # type: ignore[no-untyped-def]
    await update.message.reply_text(text, parse_mode="HTML", **kw)


# ══════════════════════════════════════════════════════════════════════════════
# CREATOR TRACKING
# ══════════════════════════════════════════════════════════════════════════════

async def cmd_addcreator(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /addcreator <@handle | x.com/handle | https://x.com/handle>
    Add an X creator to your personal watchlist.
    """
    if not await check_admin(update, context):
        return
    if not context.args:
        await _reply(update, "Usage: /addcreator @handle\nExample: /addcreator @vitalikbuterin")
        return

    raw = context.args[0]
    user_id = update.effective_user.id

    from app.integrations.social_provider import get_social_provider, SocialProviderError
    try:
        provider = get_social_provider()
    except SocialProviderError as exc:
        await _reply(update, f"⚠️ Social provider not configured: {exc}")
        return

    await _reply(update, f"⏳ Resolving <code>{raw}</code>…")

    x_user = await provider.resolve_profile(raw)
    if x_user is None:
        await _reply(update, f"❌ Could not find X account: <code>{raw}</code>")
        return

    from app.db.base import AsyncSessionLocal
    from app.db.models import TrackedCreator
    from app.db.repositories.creator_tracking import TrackedCreatorRepository

    async with AsyncSessionLocal() as session:
        repo = TrackedCreatorRepository(session)
        existing = await repo.get_by_user_and_handle(user_id, x_user.username)

        if existing:
            if existing.is_active:
                await _reply(
                    update,
                    f"ℹ️ @{x_user.username} is already in your watchlist."
                )
                return
            # Re-activate
            existing.is_active = True
            existing.follower_count = x_user.public_metrics.followers_count
            await repo.save(existing)
            await session.commit()
            await _reply(update, f"♻️ Re-activated @{x_user.username} in your watchlist.")
            return

        creator = TrackedCreator(
            telegram_user_id=user_id,
            x_user_id=x_user.id,
            x_username=x_user.username,
            display_name=x_user.name,
            follower_count=x_user.public_metrics.followers_count,
        )
        await repo.add(creator)
        await session.commit()

    log.info("creator_added", username=x_user.username, telegram_user_id=user_id)
    await _reply(
        update,
        f"✅ Added <b>@{x_user.username}</b> to your creator watchlist.\n"
        f"Followers: {x_user.public_metrics.followers_count:,}\n\n"
        f"I'll alert you when they post bullish signals linked to Zora coins.",
    )


async def cmd_removecreator(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /removecreator @handle
    Remove a creator from your watchlist.
    """
    if not await check_admin(update, context):
        return
    if not context.args:
        await _reply(update, "Usage: /removecreator @handle")
        return

    from app.integrations.social_provider import SocialProvider
    handle = SocialProvider.normalise_handle(context.args[0])
    user_id = update.effective_user.id

    from app.db.base import AsyncSessionLocal
    from app.db.repositories.creator_tracking import TrackedCreatorRepository

    async with AsyncSessionLocal() as session:
        repo = TrackedCreatorRepository(session)
        creator = await repo.get_by_user_and_handle(user_id, handle)
        if creator is None or not creator.is_active:
            await _reply(update, f"❌ @{handle} is not in your watchlist.")
            return
        creator.is_active = False
        await repo.save(creator)
        await session.commit()

    await _reply(update, f"🗑️ Removed <b>@{handle}</b> from your watchlist.")


async def cmd_creators(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/creators — list your watched creators."""
    if not await check_admin(update, context):
        return

    user_id = update.effective_user.id
    from app.db.base import AsyncSessionLocal
    from app.db.repositories.creator_tracking import TrackedCreatorRepository

    async with AsyncSessionLocal() as session:
        repo = TrackedCreatorRepository(session)
        creators = await repo.get_active_for_user(user_id)

    if not creators:
        await _reply(
            update,
            "📋 Your creator watchlist is empty.\n\nUse /addcreator @handle to start tracking."
        )
        return

    lines = [f"📋 <b>Your Tracked Creators ({len(creators)})</b>\n"]
    for c in creators:
        fol = f"{c.follower_count:,}" if c.follower_count else "?"
        mode_label = c.mode.value.replace("_", " ")
        lines.append(f"• @{c.x_username}  <i>{fol} followers</i>  [{mode_label}]")

    lines.append("\nUse /mode <creator_only|keyword_only|hybrid> to change strategy.")
    await _reply(update, "\n".join(lines))


async def cmd_creatorstatus(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/creatorstatus @handle — detailed status for one creator."""
    if not await check_admin(update, context):
        return
    if not context.args:
        await _reply(update, "Usage: /creatorstatus @handle")
        return

    from app.integrations.social_provider import SocialProvider
    handle = SocialProvider.normalise_handle(context.args[0])
    user_id = update.effective_user.id

    from app.db.base import AsyncSessionLocal
    from app.db.repositories.creator_tracking import (
        CreatorPostRepository,
        TrackedCreatorRepository,
    )

    async with AsyncSessionLocal() as session:
        repo = TrackedCreatorRepository(session)
        creator = await repo.get_by_user_and_handle(user_id, handle)
        if creator is None:
            await _reply(update, f"❌ @{handle} is not in your watchlist.")
            return
        recent_posts = await CreatorPostRepository(session).get_recent_for_creator(
            creator.id, limit=5
        )

    from app.bot.handlers.commands import _age_label
    last_fetch = _age_label(creator.last_fetched_at)
    lines = [
        f"👤 <b>@{creator.x_username}</b>",
        f"Display name: {creator.display_name or 'N/A'}",
        f"Followers: {creator.follower_count:,}" if creator.follower_count else "Followers: ?",
        f"Mode: <b>{creator.mode.value}</b>",
        f"Last fetched: {last_fetch}",
        f"Zora wallet: <code>{creator.zora_wallet_address or 'not linked'}</code>",
        f"Status: {'🟢 Active' if creator.is_active else '🔴 Paused'}",
        "",
        f"<b>Recent posts ({len(recent_posts)})</b>",
    ]
    for p in recent_posts:
        age = _age_label(p.posted_at)
        sentiment = ""
        if p.classification:
            s = p.classification.sentiment.value
            sentiment = f" [{s}]"
        snippet = (p.text or "")[:60].replace("<", "&lt;").replace(">", "&gt;")
        lines.append(f"• [{age}]{sentiment} {snippet}…")

    await _reply(update, "\n".join(lines))


async def cmd_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /mode <creator_only|keyword_only|hybrid>
    Set your global creator signal strategy mode.
    """
    if not await check_admin(update, context):
        return

    valid = ("creator_only", "keyword_only", "hybrid")
    if not context.args or context.args[0].lower() not in valid:
        await _reply(
            update,
            f"Usage: /mode &lt;{'|'.join(valid)}&gt;\n\n"
            "<b>creator_only</b> — only trade creator's own coins\n"
            "<b>keyword_only</b> — only keyword-discovered trending coins\n"
            "<b>hybrid</b>       — prefer creator coins, allow trending matches",
        )
        return

    mode = context.args[0].lower()
    user_id = update.effective_user.id

    from app.db.base import AsyncSessionLocal
    from app.db.models import CreatorWatchMode
    from app.db.repositories.creator_tracking import UserStrategyPreferencesRepository

    async with AsyncSessionLocal() as session:
        repo = UserStrategyPreferencesRepository(session)
        prefs = await repo.get_or_create(user_id)
        prefs.mode = CreatorWatchMode(mode)
        await repo.save(prefs)
        await session.commit()

    mode_descriptions = {
        "creator_only": "Only your tracked creators' own Zora coins.",
        "keyword_only": "Trending Zora coins matching post keywords.",
        "hybrid": "Creator coins first, then keyword-matched trending coins.",
    }
    await _reply(
        update,
        f"✅ Strategy mode set to <b>{mode}</b>\n<i>{mode_descriptions[mode]}</i>",
    )


# ══════════════════════════════════════════════════════════════════════════════
# WALLET LINKING
# ══════════════════════════════════════════════════════════════════════════════

async def cmd_linkwallet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/linkwallet — get a secure link to connect your wallet."""
    if not await check_admin(update, context):
        return

    from app.config import settings
    if not settings.enable_wallet_linking:
        await _reply(update, "⚠️ Wallet linking is disabled. Set ENABLE_WALLET_LINKING=true.")
        return

    user_id = update.effective_user.id

    from app.db.base import AsyncSessionLocal
    from app.services.wallet_linking import create_link_session

    async with AsyncSessionLocal() as session:
        link_url = await create_link_session(session, user_id)
        await session.commit()

    await _reply(
        update,
        "🔗 <b>Link Your Wallet</b>\n\n"
        "Click the link below to connect your wallet securely.\n"
        "You'll be asked to <b>sign a message</b> — this is free and does not "
        "grant any trading permissions.\n\n"
        f"<a href='{link_url}'>🌐 Open Wallet Connect Page</a>\n\n"
        f"⏰ This link expires in {settings.wallet_nonce_ttl_seconds // 60} minutes.\n\n"
        "<i>Never share your private key or seed phrase with anyone.</i>",
    )


async def cmd_walletstatus(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/walletstatus — show your linked wallet and Zora profile."""
    if not await check_admin(update, context):
        return

    user_id = update.effective_user.id

    from app.db.base import AsyncSessionLocal
    from app.db.repositories.wallet import WalletLinkRepository, ZoraProfileLinkRepository

    async with AsyncSessionLocal() as session:
        wl_repo = WalletLinkRepository(session)
        link = await wl_repo.get_verified_for_user(user_id)

        if link is None:
            await _reply(
                update,
                "❌ No wallet linked.\n\nUse /linkwallet to connect your wallet."
            )
            return

        zora_profile = None
        if link.zora_profile_id:
            zp_repo = ZoraProfileLinkRepository(session)
            zora_profile = await zp_repo.get(link.zora_profile_id)

    short = f"{link.wallet_address[:6]}...{link.wallet_address[-4:]}"
    from app.bot.handlers.commands import _age_label
    verified_ago = _age_label(link.verified_at)

    lines = [
        "🔗 <b>Linked Wallet</b>\n",
        f"Address: <code>{short}</code>",
        f"Chain: Base (chain_id 8453)",
        f"Verified: {verified_ago} ago",
        f"Status: {'🟢 Active' if link.status.value == 'verified' else '🔴 ' + link.status.value}",
    ]

    if zora_profile:
        lines += [
            "",
            "🟣 <b>Zora Profile</b>",
            f"Name: {zora_profile.zora_display_name or 'N/A'}",
            f"X handle: @{zora_profile.zora_x_username or 'N/A'}",
            f"Creator coin: {zora_profile.creator_coin_address or 'none found'}",
        ]
    else:
        lines.append("\n<i>No Zora profile found for this wallet.</i>")

    lines.append("\nUse /unlinkwallet to remove.")
    await _reply(update, "\n".join(lines))


async def cmd_unlinkwallet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/unlinkwallet — revoke your wallet link."""
    if not await check_admin(update, context):
        return

    user_id = update.effective_user.id

    from app.db.base import AsyncSessionLocal
    from app.services.wallet_linking import unlink_wallet

    async with AsyncSessionLocal() as session:
        ok, msg = await unlink_wallet(session, user_id)
        await session.commit()

    icon = "✅" if ok else "❌"
    await _reply(update, f"{icon} {msg}")
