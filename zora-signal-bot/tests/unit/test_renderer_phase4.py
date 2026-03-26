"""
tests/unit/test_renderer_phase4.py
Additional renderer tests for Phase 4 additions.
"""

from __future__ import annotations

from app.bot.renderer import format_help


def test_help_contains_phase4_commands():
    msg = format_help()
    assert "/blacklist" in msg
    assert "/whitelist" in msg
    assert "/overrides" in msg
    assert "/setconfig" in msg or "setconfig" in msg.lower()


def test_help_contains_all_original_commands():
    msg = format_help()
    # All Phase 1/2/3 commands still present
    for cmd in ["/status", "/health", "/signals", "/positions", "/pnl",
                "/watchlist", "/addaccount", "/removeaccount", "/score",
                "/paper_on", "/paper_off", "/live_on", "/live_off",
                "/approve", "/reject", "/config", "/kill"]:
        assert cmd in msg, f"Missing: {cmd}"
