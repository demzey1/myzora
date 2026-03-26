"""
tests/unit/test_risk_manager.py
Tests for every risk rule in RiskManager.evaluate().
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import RiskEventType
from app.trading.risk_manager import RiskContext, RiskManager

rm = RiskManager()


def _ctx(**kwargs) -> RiskContext:
    defaults = dict(
        signal_id=1,
        final_score=80.0,
        coin_id=1,
        contract_address="0xTEST",
        coin_launched_at=datetime.now(timezone.utc) - timedelta(hours=2),
        last_traded_at=None,
        is_blacklisted=False,
        liquidity_usd=50_000.0,
        slippage_bps=100,
        daily_realised_loss_usd=0.0,
        open_position_count=0,
    )
    defaults.update(kwargs)
    return RiskContext(**defaults)


def test_kill_switch_blocks():
    d = rm.evaluate(_ctx(), kill_switch=True)
    assert d.allowed is False
    assert d.risk_event_type == RiskEventType.KILL_SWITCH


def test_low_score_blocks():
    d = rm.evaluate(_ctx(final_score=40.0))
    assert d.allowed is False
    assert d.risk_event_type == RiskEventType.LOW_CONFIDENCE


def test_coin_cooldown_blocks():
    recent = datetime.now(timezone.utc) - timedelta(minutes=5)
    d = rm.evaluate(_ctx(last_traded_at=recent))
    assert d.allowed is False
    assert d.risk_event_type == RiskEventType.COIN_COOLDOWN


def test_coin_cooldown_expired_allows():
    old = datetime.now(timezone.utc) - timedelta(minutes=60)
    d = rm.evaluate(_ctx(last_traded_at=old))
    assert d.allowed is True


def test_new_coin_lockout_blocks():
    just_launched = datetime.now(timezone.utc) - timedelta(seconds=60)
    d = rm.evaluate(_ctx(coin_launched_at=just_launched))
    assert d.allowed is False
    assert d.risk_event_type == RiskEventType.NEW_COIN_LOCKOUT


def test_low_liquidity_blocks():
    d = rm.evaluate(_ctx(liquidity_usd=1_000.0))
    assert d.allowed is False
    assert d.risk_event_type == RiskEventType.LOW_LIQUIDITY


def test_none_liquidity_blocks():
    d = rm.evaluate(_ctx(liquidity_usd=None))
    assert d.allowed is False
    assert d.risk_event_type == RiskEventType.LOW_LIQUIDITY


def test_high_slippage_blocks():
    d = rm.evaluate(_ctx(slippage_bps=500))
    assert d.allowed is False
    assert d.risk_event_type == RiskEventType.HIGH_SLIPPAGE


def test_acceptable_slippage_allows():
    d = rm.evaluate(_ctx(slippage_bps=150))
    assert d.allowed is True


def test_daily_loss_limit_blocks():
    d = rm.evaluate(_ctx(daily_realised_loss_usd=600.0))
    assert d.allowed is False
    assert d.risk_event_type == RiskEventType.DAILY_LOSS_LIMIT


def test_concurrent_position_cap_blocks():
    d = rm.evaluate(_ctx(open_position_count=5))
    assert d.allowed is False
    assert d.risk_event_type == RiskEventType.CONCURRENT_POSITION_LIMIT


def test_blacklist_blocks():
    d = rm.evaluate(_ctx(is_blacklisted=True))
    assert d.allowed is False
    assert d.risk_event_type == RiskEventType.BLACKLISTED


def test_all_rules_pass_allows():
    d = rm.evaluate(_ctx())
    assert d.allowed is True
    assert d.blocking_rule is None


def test_advisory_note_near_daily_loss_limit():
    """When approaching loss limit, note should appear but trade is allowed."""
    # $400 lost out of $500 limit = 80% used, > 75% threshold
    d = rm.evaluate(_ctx(daily_realised_loss_usd=400.0))
    assert d.allowed is True
    assert any("daily_loss_limit" in note for note in d.notes)


def test_rules_evaluated_in_order_kill_switch_first():
    """Kill switch takes priority over all other rules."""
    d = rm.evaluate(
        _ctx(final_score=10.0, liquidity_usd=0.0, is_blacklisted=True),
        kill_switch=True,
    )
    assert d.risk_event_type == RiskEventType.KILL_SWITCH
