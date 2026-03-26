"""
tests/unit/test_bot_commands.py
Unit tests for Telegram command handlers.
Uses mocked Update / Context objects to avoid needing a real bot token.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.bot.handlers.commands import (
    cmd_config,
    cmd_help,
    cmd_kill,
    cmd_live_on,
    cmd_paper_off,
    cmd_paper_on,
    cmd_start,
    cmd_status,
)


def _make_update(user_id: int = 12345, text: str = "/start") -> MagicMock:
    """Build a minimal fake Telegram Update."""
    user = MagicMock()
    user.id = user_id
    user.first_name = "TestUser"

    message = MagicMock()
    message.text = text
    message.reply_text = AsyncMock()

    update = MagicMock()
    update.effective_user = user
    update.message = message
    return update


def _make_context(bot_data: dict | None = None) -> MagicMock:
    """Build a minimal fake CallbackContext."""
    ctx = MagicMock()
    ctx.bot_data = bot_data if bot_data is not None else {}
    ctx.args = []
    return ctx


# ── /start ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cmd_start_replies(monkeypatch):
    update = _make_update()
    ctx = _make_context()
    await cmd_start(update, ctx)
    update.message.reply_text.assert_called_once()
    call_text = update.message.reply_text.call_args[0][0]
    assert "Welcome" in call_text


# ── /help ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cmd_help_contains_commands():
    update = _make_update(text="/help")
    ctx = _make_context()
    await cmd_help(update, ctx)
    call_text = update.message.reply_text.call_args[0][0]
    assert "/status" in call_text
    assert "/kill" in call_text


# ── /status (admin-gated) ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cmd_status_admin_allowed():
    update = _make_update(user_id=12345, text="/status")  # 12345 is in ADMIN_IDS
    ctx = _make_context()
    await cmd_status(update, ctx)
    update.message.reply_text.assert_called_once()


@pytest.mark.asyncio
async def test_cmd_status_non_admin_denied():
    update = _make_update(user_id=99999, text="/status")
    ctx = _make_context()
    await cmd_status(update, ctx)
    call_text = update.message.reply_text.call_args[0][0]
    assert "Unauthorised" in call_text or "⛔" in call_text


# ── /paper_on / /paper_off ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cmd_paper_on_sets_flag():
    update = _make_update(user_id=12345, text="/paper_on")
    ctx = _make_context()
    await cmd_paper_on(update, ctx)
    assert ctx.bot_data["paper_trading"] is True


@pytest.mark.asyncio
async def test_cmd_paper_off_clears_flag():
    update = _make_update(user_id=12345, text="/paper_off")
    ctx = _make_context({"paper_trading": True})
    await cmd_paper_off(update, ctx)
    assert ctx.bot_data["paper_trading"] is False


# ── /live_on safety check ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cmd_live_on_blocked_when_config_disabled():
    """live_on should refuse if LIVE_TRADING_ENABLED=false in config."""
    update = _make_update(user_id=12345, text="/live_on")
    ctx = _make_context()
    # settings.live_trading_enabled is False by default in test env
    await cmd_live_on(update, ctx)
    call_text = update.message.reply_text.call_args[0][0]
    assert "disabled at the configuration level" in call_text


# ── /kill ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cmd_kill_activates_kill_switch():
    update = _make_update(user_id=12345, text="/kill")
    ctx = _make_context({"paper_trading": True, "live_trading": False})
    await cmd_kill(update, ctx)
    assert ctx.bot_data["kill_switch"] is True
    assert ctx.bot_data["paper_trading"] is False


@pytest.mark.asyncio
async def test_cmd_kill_non_admin_blocked():
    update = _make_update(user_id=99999, text="/kill")
    ctx = _make_context()
    await cmd_kill(update, ctx)
    # Kill switch must NOT be set for unauthorised users
    assert not ctx.bot_data.get("kill_switch", False)


# ── /config ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cmd_config_shows_thresholds():
    update = _make_update(user_id=12345, text="/config")
    ctx = _make_context()
    await cmd_config(update, ctx)
    call_text = update.message.reply_text.call_args[0][0]
    assert "IGNORE" in call_text
    assert "PAPER" in call_text
    assert "development" in call_text
