"""
tests/unit/test_scoring_policy.py
Tests for the signal policy layer (score → Recommendation).
"""

from __future__ import annotations

import pytest

from app.db.models import Recommendation
from app.scoring.engine import ScoreBreakdown, ScoreResult
from app.scoring.policy import apply_signal_policy


def _result(score: float, disqualified: bool = False) -> ScoreResult:
    return ScoreResult(
        deterministic_score=score,
        llm_score=None,
        final_score=score,
        breakdown=ScoreBreakdown(),
        disqualified=disqualified,
        disqualify_reasons=["test"] if disqualified else [],
        risk_notes=[],
    )


# ── Threshold mapping (defaults from settings) ───────────────────────────────

def test_score_below_ignore_threshold():
    result = apply_signal_policy(_result(10.0))
    assert result == Recommendation.IGNORE


def test_score_in_watch_range():
    result = apply_signal_policy(_result(40.0))
    assert result == Recommendation.WATCH


def test_score_in_alert_range():
    result = apply_signal_policy(_result(58.0))
    assert result == Recommendation.ALERT


def test_score_at_paper_trade_threshold():
    # 75 is the paper trade threshold (default)
    result = apply_signal_policy(_result(78.0), paper_trading_active=True)
    assert result == Recommendation.PAPER_TRADE


def test_score_at_live_trade_threshold():
    result = apply_signal_policy(
        _result(90.0),
        paper_trading_active=True,
        live_trading_active=True,
    )
    assert result == Recommendation.LIVE_TRADE_READY


# ── Override tests ────────────────────────────────────────────────────────────

def test_kill_switch_forces_ignore():
    result = apply_signal_policy(_result(99.0), kill_switch_active=True)
    assert result == Recommendation.IGNORE


def test_disqualified_forces_ignore():
    result = apply_signal_policy(_result(0.0, disqualified=True))
    assert result == Recommendation.IGNORE


def test_paper_off_caps_at_alert():
    """With paper trading disabled, high score should produce ALERT not PAPER_TRADE."""
    result = apply_signal_policy(_result(78.0), paper_trading_active=False)
    assert result == Recommendation.ALERT


def test_live_off_caps_at_paper_trade():
    """With live trading disabled, LIVE_TRADE_READY score produces PAPER_TRADE."""
    result = apply_signal_policy(
        _result(90.0),
        paper_trading_active=True,
        live_trading_active=False,
    )
    assert result == Recommendation.PAPER_TRADE


def test_live_on_requires_paper_on():
    """If paper trading is off but live trading is on, still cap at ALERT."""
    result = apply_signal_policy(
        _result(90.0),
        paper_trading_active=False,
        live_trading_active=True,
    )
    assert result == Recommendation.ALERT
