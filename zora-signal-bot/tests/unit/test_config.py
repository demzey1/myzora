"""
tests/unit/test_config.py
Tests for settings loading, validation, and safety checks.
"""

from __future__ import annotations

import os
import pytest
from pydantic import ValidationError


def test_settings_loads_from_env(monkeypatch):
    """Settings should parse correctly from environment variables."""
    from app.config import get_settings
    get_settings.cache_clear()
    s = get_settings()

    assert s.app_env == "development"
    assert s.paper_trading_enabled is True
    assert s.live_trading_enabled is False


def test_is_admin_returns_true_for_known_id():
    from app.config import get_settings
    get_settings.cache_clear()
    s = get_settings()
    assert s.is_admin(12345) is True


def test_is_admin_returns_false_for_unknown_id():
    from app.config import get_settings
    get_settings.cache_clear()
    s = get_settings()
    assert s.is_admin(99999) is False


def test_live_trading_blocked_in_development(monkeypatch):
    """Live trading must be rejected if APP_ENV=development."""
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    monkeypatch.setenv("WALLET_PRIVATE_KEY", "0xdeadbeef")
    monkeypatch.setenv("APP_ENV", "development")

    from app.config import get_settings, Settings
    get_settings.cache_clear()

    with pytest.raises(ValidationError, match="LIVE_TRADING_ENABLED"):
        Settings()  # type: ignore[call-arg]

    get_settings.cache_clear()


def test_score_thresholds_ordering():
    """Thresholds should be in ascending order."""
    from app.config import get_settings
    s = get_settings()
    assert s.score_ignore_threshold < s.score_watch_threshold
    assert s.score_watch_threshold < s.score_alert_threshold
    assert s.score_alert_threshold < s.score_paper_trade_threshold
    assert s.score_paper_trade_threshold < s.score_live_trade_threshold


def test_use_webhook_false_when_url_not_set():
    from app.config import get_settings
    s = get_settings()
    assert s.use_webhook is False
