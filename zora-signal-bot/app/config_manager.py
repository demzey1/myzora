"""
app/config_manager.py
─────────────────────────────────────────────────────────────────────────────
Runtime configuration overrides stored in Redis.

The base `settings` object (Pydantic) is immutable after startup — this
module provides a thin mutable layer on top that the Telegram /setconfig
command writes to and the scoring engine reads from at call time.

Design:
  - All writes go to Redis with a "zsb:config:" prefix
  - Reads fall through to settings if the Redis key is absent
  - Type-validated on write so invalid values are rejected before storage
  - A separate audit log entry is written for every change

Supported runtime keys (must match Settings field names):
  score_alert_threshold          int   30–99
  score_paper_trade_threshold    int   30–99
  score_live_trade_threshold     int   30–99
  max_position_size_usd          float 1–10000
  max_daily_loss_usd             float 1–100000
  max_concurrent_positions       int   1–20
  min_liquidity_usd              float 0–10000000
  max_slippage_bps               int   1–2000
  paper_trade_size_usd           float 1–10000
  no_trade_after_launch_seconds  int   0–3600
"""

from __future__ import annotations

from typing import Any

from app.config import settings
from app.logging_config import get_logger

log = get_logger(__name__)

_REDIS_PREFIX = "zsb:config:"

# name → (type, min, max)
_WRITABLE_KEYS: dict[str, tuple[type, float, float]] = {
    "score_alert_threshold":          (int,   30,  99),
    "score_paper_trade_threshold":    (int,   30,  99),
    "score_live_trade_threshold":     (int,   30,  99),
    "max_position_size_usd":          (float, 1,   10_000),
    "max_daily_loss_usd":             (float, 1,   100_000),
    "max_concurrent_positions":       (int,   1,   20),
    "min_liquidity_usd":              (float, 0,   10_000_000),
    "max_slippage_bps":               (int,   1,   2_000),
    "paper_trade_size_usd":           (float, 1,   10_000),
    "no_trade_after_launch_seconds":  (int,   0,   3_600),
}


def _get_redis():
    import redis
    return redis.from_url(settings.redis_url, decode_responses=True)


def get_config_value(key: str) -> Any:
    """
    Return the current effective value for a settings key.
    Checks Redis override first; falls back to the immutable settings object.
    """
    try:
        r = _get_redis()
        raw = r.get(f"{_REDIS_PREFIX}{key}")
        if raw is not None:
            cast, _, _ = _WRITABLE_KEYS[key]
            return cast(raw)
    except Exception as exc:
        log.debug("config_redis_read_failed", key=key, error=str(exc))
    return getattr(settings, key)


def set_config_value(key: str, raw_value: str, changed_by: int) -> tuple[bool, str]:
    """
    Validate and persist a runtime config override to Redis.

    Returns (success: bool, message: str).
    """
    if key not in _WRITABLE_KEYS:
        return False, f"Unknown or non-writable key: {key!r}. Writable keys: {sorted(_WRITABLE_KEYS)}"

    cast, lo, hi = _WRITABLE_KEYS[key]
    try:
        value = cast(raw_value)
    except (ValueError, TypeError):
        return False, f"Invalid value {raw_value!r} — expected {cast.__name__}"

    if not (lo <= value <= hi):
        return False, f"Value {value} out of range [{lo}, {hi}]"

    try:
        r = _get_redis()
        r.set(f"{_REDIS_PREFIX}{key}", str(value))
        log.info("config_override_set", key=key, value=value, changed_by=changed_by)
        return True, f"Set {key} = {value}"
    except Exception as exc:
        log.error("config_redis_write_failed", key=key, error=str(exc))
        return False, f"Redis write failed: {exc}"


def get_all_overrides() -> dict[str, Any]:
    """Return a dict of all currently active runtime overrides."""
    overrides: dict[str, Any] = {}
    try:
        r = _get_redis()
        for key, (cast, _, _) in _WRITABLE_KEYS.items():
            raw = r.get(f"{_REDIS_PREFIX}{key}")
            if raw is not None:
                overrides[key] = cast(raw)
    except Exception as exc:
        log.debug("config_redis_list_failed", error=str(exc))
    return overrides


def clear_config_override(key: str, changed_by: int) -> tuple[bool, str]:
    """Remove a runtime override, reverting to the settings.py default."""
    if key not in _WRITABLE_KEYS:
        return False, f"Unknown key: {key!r}"
    try:
        r = _get_redis()
        r.delete(f"{_REDIS_PREFIX}{key}")
        log.info("config_override_cleared", key=key, changed_by=changed_by)
        return True, f"Cleared override for {key} — reverted to default"
    except Exception as exc:
        return False, f"Redis delete failed: {exc}"
