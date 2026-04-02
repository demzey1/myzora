"""Tests for settings loading, validation, and safety checks."""

from __future__ import annotations

import pytest
from pydantic import ValidationError


def test_settings_loads_from_env():
    from app.config import get_settings
    get_settings.cache_clear()
    s = get_settings()
    assert s.app_env == "development"
    assert s.paper_trading_enabled is True
    assert s.live_trading_enabled is False
    assert bool(s.openai_api_key)
    assert bool(s.socialdata_api_key)
    assert bool(s.zora_api_key)
    assert bool(s.alchemy_api_key)


def test_is_admin_returns_true_for_known_id():
    from app.config import get_settings
    get_settings.cache_clear()
    assert get_settings().is_admin(12345) is True


def test_is_admin_returns_false_for_unknown_id():
    from app.config import get_settings
    get_settings.cache_clear()
    assert get_settings().is_admin(99999) is False


def test_live_trading_blocked_in_development(monkeypatch):
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    monkeypatch.setenv("WALLET_PRIVATE_KEY", "0xdeadbeef")
    monkeypatch.setenv("APP_ENV", "development")
    from app.config import Settings
    with pytest.raises(ValidationError, match="LIVE_TRADING_ENABLED"):
        Settings()  # type: ignore[call-arg]


def test_wallet_link_secret_required_when_wallet_linking_enabled(monkeypatch):
    monkeypatch.delenv("WALLET_LINK_SECRET", raising=False)
    monkeypatch.setenv("ENABLE_WALLET_LINKING", "true")
    from app.config import Settings
    with pytest.raises(ValidationError, match="WALLET_LINK_SECRET"):
        Settings()  # type: ignore[call-arg]


def test_webhook_secret_required_when_webhook_url_is_set(monkeypatch):
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("TELEGRAM_WEBHOOK_URL", "https://example.com")
    from app.config import Settings
    with pytest.raises(ValidationError, match="TELEGRAM_WEBHOOK_SECRET"):
        Settings()  # type: ignore[call-arg]


def test_score_thresholds_ordering():
    from app.config import get_settings
    s = get_settings()
    assert s.score_ignore_threshold < s.score_watch_threshold < s.score_alert_threshold
    assert s.score_alert_threshold < s.score_paper_trade_threshold < s.score_live_trade_threshold


def test_use_webhook_false_when_url_not_set(monkeypatch):
    monkeypatch.delenv("TELEGRAM_WEBHOOK_URL", raising=False)
    from app.config import Settings
    s = Settings()  # type: ignore[call-arg]
    assert s.use_webhook is False
