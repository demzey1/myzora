"""
app/scoring/policy.py
─────────────────────────────────────────────────────────────────────────────
Maps a ScoreResult to a Recommendation and enforces runtime trading flags.

Signal policy:
  score < IGNORE_THRESHOLD              → IGNORE
  score < WATCH_THRESHOLD               → WATCH
  score < ALERT_THRESHOLD               → ALERT
  score < PAPER_TRADE_THRESHOLD         → ALERT  (with paper trade suggestion)
  score < LIVE_TRADE_THRESHOLD          → PAPER_TRADE
  score >= LIVE_TRADE_THRESHOLD         → LIVE_TRADE_READY (NOT executed yet)

Additional overrides:
  - disqualified=True always → IGNORE
  - kill_switch=True always → IGNORE
  - live_trading runtime flag=False caps at PAPER_TRADE
  - paper_trading runtime flag=False caps at ALERT
"""

from __future__ import annotations

from app.config import settings
try:
    from app.config_manager import get_config_value as _gcv
except Exception:
    _gcv = lambda k: getattr(settings, k)  # noqa: E731
from app.db.models import Recommendation
from app.scoring.engine import ScoreResult


def apply_signal_policy(
    result: ScoreResult,
    *,
    kill_switch_active: bool = False,
    paper_trading_active: bool | None = None,
    live_trading_active: bool | None = None,
) -> Recommendation:
    """
    Convert a ScoreResult to a Recommendation, respecting runtime flags.

    paper_trading_active / live_trading_active default to settings values
    if not passed (allows the Telegram bot_data state to override).
    """
    paper = paper_trading_active if paper_trading_active is not None else settings.paper_trading_enabled
    live = live_trading_active if live_trading_active is not None else settings.live_trading_enabled

    # Hard overrides first
    if kill_switch_active:
        return Recommendation.IGNORE
    if result.disqualified:
        return Recommendation.IGNORE

    score = result.final_score

    # Use runtime override if set, else fall back to settings
    ignore_t      = _gcv("score_ignore_threshold")
    watch_t       = _gcv("score_watch_threshold")
    alert_t       = _gcv("score_alert_threshold")
    paper_t       = _gcv("score_paper_trade_threshold")
    live_t        = _gcv("score_live_trade_threshold")

    if score < ignore_t:
        return Recommendation.IGNORE
    if score < watch_t:
        return Recommendation.WATCH
    if score < alert_t:
        return Recommendation.ALERT
    if score < paper_t:
        return Recommendation.ALERT  # Suggest paper trade via notes

    # Candidate for paper or live trade
    if not paper:
        return Recommendation.ALERT  # Paper trading disabled → alert only

    if score < live_t:
        return Recommendation.PAPER_TRADE

    # High-confidence signal
    if not live:
        return Recommendation.PAPER_TRADE  # Live trading disabled → paper only

    return Recommendation.LIVE_TRADE_READY
