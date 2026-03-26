"""
app/services/feature_flags.py
─────────────────────────────────────────────────────────────────────────────
Redis-backed feature flags.

Admin can toggle any feature on/off from Telegram with:
  /featureoff payments
  /featureon  ai
  /features   (list all)

Flags are stored in Redis so they survive bot restarts.
If Redis is unavailable, defaults to all features ON.

Available flags:
  payments         — premium subscription + payment flow
  ai               — Claude AI chat for all users
  creator_tracking — X creator post polling
  zora_discovery   — Zora coin candidate discovery
  alerts           — Telegram signal alert delivery
  wallet_linking   — /linkwallet flow
  auto_trading     — on-chain trade execution
"""

from __future__ import annotations

from app.logging_config import get_logger

log = get_logger(__name__)

_REDIS_PREFIX = "zsb:feature:"

# All flags default to ON
_FLAGS: dict[str, str] = {
    "payments":         "Premium subscription and payment flow",
    "ai":               "Claude AI chat assistant",
    "creator_tracking": "X/Twitter creator post polling",
    "zora_discovery":   "Zora coin candidate discovery",
    "alerts":           "Telegram signal alert delivery",
    "wallet_linking":   "Wallet connect flow (/linkwallet)",
    "auto_trading":     "On-chain automatic trade execution",
}


def _get_redis():
    from app.config import settings
    import redis
    return redis.from_url(settings.redis_url, decode_responses=True)


def is_enabled(flag: str) -> bool:
    """Return True if the feature is enabled. Defaults to True on error."""
    if flag not in _FLAGS:
        return True
    try:
        r = _get_redis()
        val = r.get(f"{_REDIS_PREFIX}{flag}")
        if val is None:
            return True  # not set = on by default
        return val == "1"
    except Exception as exc:
        log.debug("feature_flag_read_failed", flag=flag, error=str(exc))
        return True


def set_flag(flag: str, enabled: bool, changed_by: int) -> tuple[bool, str]:
    """Set a feature flag. Returns (success, message)."""
    if flag not in _FLAGS:
        available = ", ".join(sorted(_FLAGS.keys()))
        return False, f"Unknown flag: {flag!r}\nAvailable: {available}"
    try:
        r = _get_redis()
        r.set(f"{_REDIS_PREFIX}{flag}", "1" if enabled else "0")
        state = "ON ✅" if enabled else "OFF 🔴"
        log.info("feature_flag_changed",
                 flag=flag, enabled=enabled, changed_by=changed_by)
        return True, f"Feature <b>{flag}</b> is now <b>{state}</b>"
    except Exception as exc:
        return False, f"Redis error: {exc}"


def get_all_flags() -> dict[str, bool]:
    """Return all flags with their current state."""
    try:
        r = _get_redis()
        result = {}
        for flag in _FLAGS:
            val = r.get(f"{_REDIS_PREFIX}{flag}")
            result[flag] = (val is None or val == "1")
        return result
    except Exception:
        return {flag: True for flag in _FLAGS}


def get_flag_description(flag: str) -> str:
    return _FLAGS.get(flag, "Unknown flag")
