"""
tests/unit/test_renderer.py
Tests for Telegram message rendering functions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from app.bot.renderer import (
    _age_str,
    format_help,
    format_signal_alert,
    format_status,
    signal_inline_keyboard,
)
from app.db.models import Recommendation, Signal


def _make_signal(**kwargs) -> Signal:
    defaults = dict(
        id=42,
        deterministic_score=74.0,
        llm_score=81.0,
        final_score=78.0,
        recommendation=Recommendation.ALERT,
        risk_notes="launch window / low liquidity",
    )
    defaults.update(kwargs)
    sig = MagicMock(spec=Signal)
    for k, v in defaults.items():
        setattr(sig, k, v)
    return sig


def test_age_str_minutes():
    from datetime import timedelta
    dt = datetime.now(timezone.utc) - timedelta(minutes=5)
    assert _age_str(dt) == "5m"


def test_age_str_hours():
    from datetime import timedelta
    dt = datetime.now(timezone.utc) - timedelta(hours=3)
    assert _age_str(dt) == "3h"


def test_age_str_none():
    assert _age_str(None) == "unknown"


def test_format_signal_alert_contains_key_fields():
    signal = _make_signal()
    msg = format_signal_alert(
        signal=signal,
        x_username="example",
        follower_count=240_000,
        post_text="This is a great post about something viral",
        post_age_dt=None,
        engagement_velocity="High",
        coin_symbol="EXMPL",
        coin_age_dt=None,
        price_usd=0.0012,
        liquidity_usd=15_000.0,
        slippage_bps=150,
        volume_5m_usd=5_000.0,
    )
    assert "@example" in msg
    assert "EXMPL" in msg
    assert "240,000" in msg
    assert "ALERT" in msg
    assert "78" in msg           # final score
    assert "74" in msg           # deterministic score
    assert "launch window" in msg


def test_format_signal_alert_truncates_long_text():
    signal = _make_signal(llm_score=None)
    long_text = "x" * 500
    msg = format_signal_alert(
        signal=signal,
        x_username="abc",
        follower_count=None,
        post_text=long_text,
        post_age_dt=None,
        engagement_velocity="Medium",
        coin_symbol="ABC",
        coin_age_dt=None,
        price_usd=None,
        liquidity_usd=None,
        slippage_bps=None,
        volume_5m_usd=None,
    )
    assert "…" in msg
    # Snippet should be capped at ~200 chars + ellipsis
    snippet_start = msg.index("<i>") + 3
    snippet_end = msg.index("</i>")
    snippet = msg[snippet_start:snippet_end]
    assert len(snippet) <= 205


def test_format_signal_alert_no_llm_score():
    """When llm_score is None the LLM row should be absent."""
    signal = _make_signal(llm_score=None)
    msg = format_signal_alert(
        signal=signal,
        x_username="xyz",
        follower_count=1000,
        post_text="short",
        post_age_dt=None,
        engagement_velocity="Low",
        coin_symbol="XYZ",
        coin_age_dt=None,
        price_usd=None,
        liquidity_usd=None,
        slippage_bps=None,
        volume_5m_usd=None,
    )
    assert "LLM classification" not in msg


def test_format_status_kill_switch():
    msg = format_status(
        paper_trading=False,
        live_trading=False,
        open_paper_positions=0,
        open_live_positions=0,
        total_signals_today=3,
        kill_switch_active=True,
    )
    assert "ACTIVE" in msg
    assert "🛑" in msg


def test_format_help_contains_all_commands():
    msg = format_help()
    required_commands = [
        "/status", "/health", "/signals", "/recent", "/positions", "/pnl",
        "/watchlist", "/addaccount", "/removeaccount", "/score",
        "/paper_on", "/paper_off", "/live_on", "/live_off",
        "/approve", "/reject", "/config", "/kill",
    ]
    for cmd in required_commands:
        assert cmd in msg, f"Missing command: {cmd}"


def test_signal_inline_keyboard_has_buttons():
    kb = signal_inline_keyboard(signal_id=7)
    buttons = [btn for row in kb.inline_keyboard for btn in row]
    data_values = [b.callback_data for b in buttons]
    assert "approve_paper:7" in data_values
    assert "ignore:7" in data_values
    assert "refresh:7" in data_values


def test_signal_inline_keyboard_live_button_optional():
    kb_no_live = signal_inline_keyboard(signal_id=1, include_live=False)
    kb_with_live = signal_inline_keyboard(signal_id=1, include_live=True)

    no_live_data = [b.callback_data for row in kb_no_live.inline_keyboard for b in row]
    with_live_data = [b.callback_data for row in kb_with_live.inline_keyboard for b in row]

    assert "approve_live:1" not in no_live_data
    assert "approve_live:1" in with_live_data
