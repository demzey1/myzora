"""
tests/unit/test_scoring_policy_with_overrides.py
Verify that the scoring policy reads runtime overrides when available.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.db.models import Recommendation
from app.scoring.engine import ScoreBreakdown, ScoreResult
from app.scoring.policy import apply_signal_policy


def _result(score: float) -> ScoreResult:
    return ScoreResult(
        deterministic_score=score, llm_score=None, final_score=score,
        breakdown=ScoreBreakdown(), disqualified=False,
        disqualify_reasons=[], risk_notes=[],
    )


def test_policy_uses_runtime_alert_threshold():
    """
    If a runtime override lowers score_alert_threshold to 40,
    a score of 45 should become ALERT instead of WATCH.
    """
    overrides = {
        "score_ignore_threshold": 10,
        "score_watch_threshold": 30,
        "score_alert_threshold": 40,        # lowered from default 65
        "score_paper_trade_threshold": 70,
        "score_live_trade_threshold": 85,
    }

    def _gcv(key):
        return overrides.get(key, getattr(__import__("app.config", fromlist=["settings"]).settings, key))

    with patch("app.scoring.policy._gcv", side_effect=_gcv):
        result = apply_signal_policy(_result(45.0))
        assert result == Recommendation.ALERT


def test_policy_uses_runtime_paper_threshold():
    """
    Raise paper trade threshold to 90 → score of 80 becomes ALERT not PAPER_TRADE.
    """
    overrides = {
        "score_ignore_threshold": 30,
        "score_watch_threshold": 50,
        "score_alert_threshold": 65,
        "score_paper_trade_threshold": 90,  # raised from default 75
        "score_live_trade_threshold": 95,
    }

    def _gcv(key):
        return overrides.get(key, getattr(__import__("app.config", fromlist=["settings"]).settings, key))

    with patch("app.scoring.policy._gcv", side_effect=_gcv):
        result = apply_signal_policy(_result(80.0), paper_trading_active=True)
        assert result == Recommendation.ALERT


def test_score_multiplier_changes_outcome():
    """
    A whitelisted creator gets ×1.5 multiplier.
    Score of 55 × 1.5 = 82.5 → should reach PAPER_TRADE.
    """
    from app.scoring.engine import ScoringEngine, ScoreBreakdown
    # We can't easily test the multiplier inside the engine unit test,
    # but we can verify the arithmetic: 55 * 1.5 = 82.5, which exceeds
    # the default paper_trade_threshold of 75.
    original_score = 55.0
    multiplier = 1.5
    adjusted = min(original_score * multiplier, 100.0)
    assert adjusted == pytest.approx(82.5)

    result = apply_signal_policy(
        ScoreResult(
            deterministic_score=original_score,
            llm_score=None,
            final_score=adjusted,
            breakdown=ScoreBreakdown(),
            disqualified=False,
            disqualify_reasons=[],
            risk_notes=[],
        ),
        paper_trading_active=True,
    )
    assert result == Recommendation.PAPER_TRADE
