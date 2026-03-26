"""
tests/unit/test_paper_engine.py
Tests for the paper trading engine and PnL calculator.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.trading.paper_engine import _compute_pnl, PaperTradingEngine


# ── PnL calculator (pure function) ────────────────────────────────────────────

def test_pnl_profitable_trade():
    pnl_usd, pnl_pct = _compute_pnl(
        size_usd=100.0,
        entry_price=1.0,
        exit_price=1.5,       # +50%
        entry_slippage_bps=0,
        exit_slippage_bps=0,
        fee_bps=0,
    )
    assert abs(pnl_usd - 50.0) < 0.01
    assert abs(pnl_pct - 50.0) < 0.01


def test_pnl_losing_trade():
    pnl_usd, pnl_pct = _compute_pnl(
        size_usd=100.0,
        entry_price=1.0,
        exit_price=0.85,      # -15%
        entry_slippage_bps=0,
        exit_slippage_bps=0,
        fee_bps=0,
    )
    assert pnl_usd < 0
    assert pnl_pct < 0
    assert abs(pnl_pct - (-15.0)) < 0.1


def test_pnl_fees_reduce_profit():
    """Fees eat into profit — no-fee trade must beat with-fee trade."""
    pnl_no_fee, _ = _compute_pnl(
        size_usd=100.0,
        entry_price=1.0,
        exit_price=1.5,
        entry_slippage_bps=0,
        exit_slippage_bps=0,
        fee_bps=0,
    )
    pnl_with_fee, _ = _compute_pnl(
        size_usd=100.0,
        entry_price=1.0,
        exit_price=1.5,
        entry_slippage_bps=0,
        exit_slippage_bps=0,
        fee_bps=30,
    )
    assert pnl_no_fee > pnl_with_fee


def test_pnl_slippage_reduces_profit():
    pnl_no_slip, _ = _compute_pnl(
        size_usd=100.0,
        entry_price=1.0,
        exit_price=1.5,
        entry_slippage_bps=0,
        exit_slippage_bps=0,
        fee_bps=0,
    )
    pnl_with_slip, _ = _compute_pnl(
        size_usd=100.0,
        entry_price=1.0,
        exit_price=1.5,
        entry_slippage_bps=100,
        exit_slippage_bps=100,
        fee_bps=0,
    )
    assert pnl_no_slip > pnl_with_slip


def test_pnl_zero_entry_price_safe():
    """Guard against division by zero."""
    pnl_usd, pnl_pct = _compute_pnl(
        size_usd=100.0,
        entry_price=0.0,
        exit_price=1.0,
        entry_slippage_bps=0,
        exit_slippage_bps=0,
        fee_bps=0,
    )
    assert pnl_usd == 0.0
    assert pnl_pct == 0.0


def test_pnl_breakeven_trade():
    pnl_usd, pnl_pct = _compute_pnl(
        size_usd=100.0,
        entry_price=1.0,
        exit_price=1.0,
        entry_slippage_bps=0,
        exit_slippage_bps=0,
        fee_bps=0,
    )
    assert abs(pnl_usd) < 0.001
    assert abs(pnl_pct) < 0.001


# ── Exit condition checks ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_check_exit_stop_loss(db_session):
    from app.db.models import PaperPosition, PositionStatus, Signal, Recommendation, ZoraCoin
    from app.trading.paper_engine import PaperTradingEngine

    # Minimal DB objects
    signal = Signal(
        deterministic_score=80.0, final_score=80.0,
        recommendation=Recommendation.PAPER_TRADE,
    )
    db_session.add(signal)
    coin = ZoraCoin(
        contract_address="0xSLTP0000000000000000000000000000000001",
        symbol="SLTP",
    )
    db_session.add(coin)
    await db_session.flush()

    position = PaperPosition(
        signal_id=signal.id,
        coin_id=coin.id,
        size_usd=50.0,
        entry_price_usd=1.0,
        status=PositionStatus.OPEN,
        stop_loss_pct=0.15,
        take_profit_pct=0.50,
        timeout_minutes=60,
    )
    db_session.add(position)
    await db_session.flush()

    engine = PaperTradingEngine()

    # Price dropped 20% → should trigger stop-loss
    reason = await engine.check_exit_conditions(db_session, position, 0.80)
    assert reason == "STOP_LOSS"


@pytest.mark.asyncio
async def test_check_exit_take_profit(db_session):
    from app.db.models import PaperPosition, PositionStatus, Signal, Recommendation, ZoraCoin

    signal = Signal(
        deterministic_score=80.0, final_score=80.0,
        recommendation=Recommendation.PAPER_TRADE,
    )
    db_session.add(signal)
    coin = ZoraCoin(
        contract_address="0xSLTP0000000000000000000000000000000002",
        symbol="SLTP",
    )
    db_session.add(coin)
    await db_session.flush()

    position = PaperPosition(
        signal_id=signal.id,
        coin_id=coin.id,
        size_usd=50.0,
        entry_price_usd=1.0,
        status=PositionStatus.OPEN,
        stop_loss_pct=0.15,
        take_profit_pct=0.50,
        timeout_minutes=60,
    )
    db_session.add(position)
    await db_session.flush()

    engine = PaperTradingEngine()

    # Price up 55% → take-profit
    reason = await engine.check_exit_conditions(db_session, position, 1.55)
    assert reason == "TAKE_PROFIT"


@pytest.mark.asyncio
async def test_check_exit_timeout(db_session):
    from app.db.models import PaperPosition, PositionStatus, Signal, Recommendation, ZoraCoin

    signal = Signal(
        deterministic_score=80.0, final_score=80.0,
        recommendation=Recommendation.PAPER_TRADE,
    )
    db_session.add(signal)
    coin = ZoraCoin(
        contract_address="0xSLTP0000000000000000000000000000000003",
        symbol="SLTP",
    )
    db_session.add(coin)
    await db_session.flush()

    # Opened 2 hours ago, timeout is 60 minutes
    old_open = datetime.now(timezone.utc) - timedelta(hours=2)
    position = PaperPosition(
        signal_id=signal.id,
        coin_id=coin.id,
        size_usd=50.0,
        entry_price_usd=1.0,
        status=PositionStatus.OPEN,
        stop_loss_pct=0.15,
        take_profit_pct=0.50,
        timeout_minutes=60,
        opened_at=old_open,
    )
    db_session.add(position)
    await db_session.flush()

    engine = PaperTradingEngine()
    reason = await engine.check_exit_conditions(db_session, position, 1.0)
    assert reason == "TIMEOUT"


@pytest.mark.asyncio
async def test_check_exit_no_trigger(db_session):
    from app.db.models import PaperPosition, PositionStatus, Signal, Recommendation, ZoraCoin

    signal = Signal(
        deterministic_score=80.0, final_score=80.0,
        recommendation=Recommendation.PAPER_TRADE,
    )
    db_session.add(signal)
    coin = ZoraCoin(
        contract_address="0xSLTP0000000000000000000000000000000004",
        symbol="SLTP",
    )
    db_session.add(coin)
    await db_session.flush()

    position = PaperPosition(
        signal_id=signal.id,
        coin_id=coin.id,
        size_usd=50.0,
        entry_price_usd=1.0,
        status=PositionStatus.OPEN,
        stop_loss_pct=0.15,
        take_profit_pct=0.50,
        timeout_minutes=60,
    )
    db_session.add(position)
    await db_session.flush()

    engine = PaperTradingEngine()
    # Price up 10% — within bands, just opened
    reason = await engine.check_exit_conditions(db_session, position, 1.10)
    assert reason is None


# ── open_position / close_position integration ────────────────────────────────

@pytest.mark.asyncio
async def test_open_position_no_price_data_fails(db_session):
    """open_position must refuse if there is no price data."""
    from app.db.models import Signal, Recommendation, ZoraCoin
    from app.trading.paper_engine import PaperTradingEngine

    signal = Signal(
        deterministic_score=80.0, final_score=80.0,
        recommendation=Recommendation.PAPER_TRADE,
    )
    db_session.add(signal)
    coin = ZoraCoin(
        contract_address="0xNOPRICE00000000000000000000000000001234",
        symbol="NOPX",
    )
    db_session.add(coin)
    await db_session.flush()
    signal.coin_id = coin.id

    engine = PaperTradingEngine()
    result = await engine.open_position(db_session, signal.id)
    # No market snapshot exists → should fail
    assert result.success is False
    assert "price" in result.message.lower() or "no" in result.message.lower()


@pytest.mark.asyncio
async def test_close_position_computes_pnl(db_session):
    from app.db.models import (
        CoinMarketSnapshot, PaperPosition, PositionStatus,
        Recommendation, Signal, ZoraCoin,
    )
    from app.trading.paper_engine import PaperTradingEngine

    signal = Signal(
        deterministic_score=80.0, final_score=80.0,
        recommendation=Recommendation.PAPER_TRADE,
    )
    db_session.add(signal)
    coin = ZoraCoin(
        contract_address="0xCLOSE0000000000000000000000000000001234",
        symbol="CLSE",
    )
    db_session.add(coin)
    await db_session.flush()

    position = PaperPosition(
        signal_id=signal.id,
        coin_id=coin.id,
        size_usd=100.0,
        entry_price_usd=1.0,
        entry_slippage_bps=0,
        assumed_fee_bps=0,
        status=PositionStatus.OPEN,
        stop_loss_pct=0.15,
        take_profit_pct=0.50,
        timeout_minutes=60,
    )
    db_session.add(position)
    await db_session.flush()

    engine = PaperTradingEngine()
    result = await engine.close_position(
        session=db_session,
        position_id=position.id,
        exit_price_usd=1.5,
        exit_reason="TAKE_PROFIT",
    )

    assert result.success is True
    assert result.pnl_usd is not None
    assert result.pnl_usd > 0  # Profitable
    assert result.pnl_pct > 0


@pytest.mark.asyncio
async def test_close_already_closed_position_fails(db_session):
    from app.db.models import (
        PaperPosition, PositionStatus, Recommendation, Signal, ZoraCoin,
    )
    from app.trading.paper_engine import PaperTradingEngine

    signal = Signal(
        deterministic_score=80.0, final_score=80.0,
        recommendation=Recommendation.PAPER_TRADE,
    )
    db_session.add(signal)
    coin = ZoraCoin(
        contract_address="0xALRDY0000000000000000000000000000001234",
        symbol="ALRD",
    )
    db_session.add(coin)
    await db_session.flush()

    position = PaperPosition(
        signal_id=signal.id,
        coin_id=coin.id,
        size_usd=50.0,
        entry_price_usd=1.0,
        status=PositionStatus.CLOSED,  # Already closed
    )
    db_session.add(position)
    await db_session.flush()

    engine = PaperTradingEngine()
    result = await engine.close_position(db_session, position.id, 1.5, "MANUAL")
    assert result.success is False
    assert "already closed" in result.message.lower()
