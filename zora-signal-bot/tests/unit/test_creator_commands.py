"""
tests/unit/test_creator_commands.py
Tests for creator tracking and wallet linking command handlers.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.bot.handlers.creator_commands import (
    cmd_addcreator,
    cmd_creators,
    cmd_mode,
    cmd_removecreator,
    cmd_linkwallet,
    cmd_walletstatus,
)


def _make_update(user_id: int = 12345, text: str = "/addcreator") -> MagicMock:
    user = MagicMock()
    user.id = user_id
    user.first_name = "Tester"
    message = MagicMock()
    message.text = text
    message.reply_text = AsyncMock()
    update = MagicMock()
    update.effective_user = user
    update.message = message
    return update


def _make_context(args=None) -> MagicMock:
    ctx = MagicMock()
    ctx.args = args or []
    ctx.bot_data = {}
    return ctx


# ── /addcreator ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_addcreator_no_args():
    update = _make_update()
    ctx = _make_context(args=[])
    await cmd_addcreator(update, ctx)
    text = update.message.reply_text.call_args[0][0]
    assert "Usage" in text


@pytest.mark.asyncio
async def test_addcreator_provider_error():
    update = _make_update()
    ctx = _make_context(args=["@testcreator"])
    with patch(
        "app.bot.handlers.creator_commands.get_social_provider",
        side_effect=Exception("No provider configured"),
    ):
        await cmd_addcreator(update, ctx)
    text = update.message.reply_text.call_args[0][0]
    assert "⚠️" in text or "not configured" in text.lower() or "provider" in text.lower()


@pytest.mark.asyncio
async def test_addcreator_user_not_found():
    update = _make_update()
    ctx = _make_context(args=["@nobody"])

    mock_provider = AsyncMock()
    mock_provider.resolve_profile = AsyncMock(return_value=None)

    with patch("app.bot.handlers.creator_commands.get_social_provider", return_value=mock_provider):
        # Also patch the reply to avoid actual DB call for the "resolving..." message
        update.message.reply_text = AsyncMock()
        await cmd_addcreator(update, ctx)

    # Last reply should say not found
    last_call = update.message.reply_text.call_args_list[-1][0][0]
    assert "❌" in last_call or "not found" in last_call.lower()


# ── /removecreator ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_removecreator_no_args():
    update = _make_update()
    ctx = _make_context(args=[])
    await cmd_removecreator(update, ctx)
    text = update.message.reply_text.call_args[0][0]
    assert "Usage" in text


# ── /creators ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_creators_empty_watchlist(db_session):
    update = _make_update()
    ctx = _make_context()
    # Use real DB session via patching AsyncSessionLocal
    with patch(
        "app.bot.handlers.creator_commands.AsyncSessionLocal"
    ) as mock_session_factory:
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=db_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
        await cmd_creators(update, ctx)

    text = update.message.reply_text.call_args[0][0]
    assert "empty" in text.lower() or "no" in text.lower() or "addcreator" in text.lower()


# ── /mode ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mode_no_args_shows_usage():
    update = _make_update()
    ctx = _make_context(args=[])
    await cmd_mode(update, ctx)
    text = update.message.reply_text.call_args[0][0]
    assert "Usage" in text or "creator_only" in text


@pytest.mark.asyncio
async def test_mode_invalid_value():
    update = _make_update()
    ctx = _make_context(args=["invalid_mode"])
    await cmd_mode(update, ctx)
    text = update.message.reply_text.call_args[0][0]
    assert "Usage" in text or "creator_only" in text


# ── /linkwallet ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_linkwallet_disabled():
    update = _make_update()
    ctx = _make_context()
    with patch("app.bot.handlers.creator_commands.settings") as ms:
        ms.enable_wallet_linking = False
        await cmd_linkwallet(update, ctx)
    text = update.message.reply_text.call_args[0][0]
    assert "disabled" in text.lower()


@pytest.mark.asyncio
async def test_linkwallet_returns_url(db_session):
    update = _make_update()
    ctx = _make_context()

    with (
        patch("app.bot.handlers.creator_commands.settings") as ms,
        patch("app.bot.handlers.creator_commands.AsyncSessionLocal") as mock_session_factory,
        patch(
            "app.bot.handlers.creator_commands.create_link_session",
            new=AsyncMock(return_value="http://localhost:8000/wallet/connect?session=abc&sig=xyz"),
        ),
    ):
        ms.enable_wallet_linking = True
        ms.wallet_nonce_ttl_seconds = 300
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=db_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
        await cmd_linkwallet(update, ctx)

    text = update.message.reply_text.call_args[0][0]
    assert "wallet" in text.lower()
    assert "link" in text.lower() or "connect" in text.lower()
