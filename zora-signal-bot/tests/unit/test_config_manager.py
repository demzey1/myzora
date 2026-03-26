"""
tests/unit/test_config_manager.py
Tests for the runtime config manager.
Uses a fake Redis implementation to avoid needing a real server.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.config_manager import (
    _WRITABLE_KEYS,
    clear_config_override,
    get_config_value,
    get_all_overrides,
    set_config_value,
)


def _mock_redis(store: dict | None = None):
    """Return a mock redis client backed by an in-memory dict."""
    if store is None:
        store = {}
    r = MagicMock()
    r.get.side_effect = lambda k: store.get(k)
    r.set.side_effect = lambda k, v: store.update({k: v})
    r.delete.side_effect = lambda k: store.pop(k, None)
    r.keys.side_effect = lambda pat="*": [k for k in store if pat.replace("*", "") in k]
    return r, store


@patch("app.config_manager._get_redis")
def test_set_and_get_int_override(mock_get_redis):
    r, store = _mock_redis()
    mock_get_redis.return_value = r

    ok, msg = set_config_value("score_alert_threshold", "60", changed_by=12345)
    assert ok is True
    assert "60" in msg

    val = get_config_value("score_alert_threshold")
    assert val == 60
    assert isinstance(val, int)


@patch("app.config_manager._get_redis")
def test_set_and_get_float_override(mock_get_redis):
    r, store = _mock_redis()
    mock_get_redis.return_value = r

    ok, msg = set_config_value("paper_trade_size_usd", "75.50", changed_by=12345)
    assert ok is True

    val = get_config_value("paper_trade_size_usd")
    assert val == pytest.approx(75.50)
    assert isinstance(val, float)


@patch("app.config_manager._get_redis")
def test_unknown_key_rejected(mock_get_redis):
    r, _ = _mock_redis()
    mock_get_redis.return_value = r

    ok, msg = set_config_value("totally_fake_key", "99", changed_by=12345)
    assert ok is False
    assert "Unknown" in msg or "non-writable" in msg


@patch("app.config_manager._get_redis")
def test_out_of_range_rejected(mock_get_redis):
    r, _ = _mock_redis()
    mock_get_redis.return_value = r

    # score_alert_threshold max is 99
    ok, msg = set_config_value("score_alert_threshold", "150", changed_by=12345)
    assert ok is False
    assert "range" in msg.lower()


@patch("app.config_manager._get_redis")
def test_invalid_type_rejected(mock_get_redis):
    r, _ = _mock_redis()
    mock_get_redis.return_value = r

    ok, msg = set_config_value("max_concurrent_positions", "not-a-number", changed_by=12345)
    assert ok is False
    assert "Invalid" in msg or "invalid" in msg


@patch("app.config_manager._get_redis")
def test_clear_override_reverts_to_default(mock_get_redis):
    from app.config import settings
    r, store = _mock_redis({"zsb:config:score_alert_threshold": "60"})
    mock_get_redis.return_value = r

    # Before clear: override is active
    val = get_config_value("score_alert_threshold")
    assert val == 60

    ok, msg = clear_config_override("score_alert_threshold", changed_by=12345)
    assert ok is True

    # After clear: back to settings default
    val_after = get_config_value("score_alert_threshold")
    assert val_after == settings.score_alert_threshold


@patch("app.config_manager._get_redis")
def test_get_all_overrides_empty(mock_get_redis):
    r, _ = _mock_redis()
    mock_get_redis.return_value = r

    overrides = get_all_overrides()
    assert overrides == {}


@patch("app.config_manager._get_redis")
def test_get_all_overrides_shows_active(mock_get_redis):
    r, store = _mock_redis({
        "zsb:config:score_alert_threshold": "55",
        "zsb:config:paper_trade_size_usd": "25.0",
    })
    mock_get_redis.return_value = r

    overrides = get_all_overrides()
    assert overrides.get("score_alert_threshold") == 55
    assert overrides.get("paper_trade_size_usd") == pytest.approx(25.0)


def test_all_writable_keys_have_valid_ranges():
    """Sanity check: every defined key has a sensible min < max."""
    for key, (cast, lo, hi) in _WRITABLE_KEYS.items():
        assert lo < hi, f"{key}: lo ({lo}) must be < hi ({hi})"
        assert cast in (int, float), f"{key}: cast must be int or float"


def test_fallback_to_settings_when_redis_unavailable():
    """If Redis is down, get_config_value must return the settings default."""
    from app.config import settings
    with patch("app.config_manager._get_redis", side_effect=Exception("connection refused")):
        val = get_config_value("score_alert_threshold")
        assert val == settings.score_alert_threshold
