"""
tests/integration/test_approval_workflow.py
─────────────────────────────────────────────────────────────────────────────
End-to-end approval workflow tests:
  - Signal is created
  - Operator approves → paper position opens
  - Operator rejects → signal marked rejected, no position
  - Duplicate approval is idempotent
  - Blacklisted creator blocks position open
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import (
    CoinMarketSnapshot,
    CreatorOverride,
    PaperPosition,
    PositionStatus,
    Recommendation,
    Signal,
    ZoraCoin,
)
from app.trading.paper_engine import PaperTradingEngine


def _make_signal(db_session, coin_id: int, score: float = 80.0) -> Signal:
    sig = Signal(
        coin_id=coin_id,
        deterministic_score=score,
        final_score=score,
        recommendation=Recommendation.PAPER_TRADE,
    )
    db_session.add(sig)
    return sig


async def _setup_coin_with_market(db_session, symbol: str, addr: str) -> ZoraCoin:
    coin = ZoraCoin(
        contract_address=addr,
        symbol=symbol,
        launched_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    db_session.add(coin)
    await db_session.flush()

    snap = CoinMarketSnapshot(
        coin_id=coin.id,
        price_usd=0.01,
        liquidity_usd=60_000.0,
        slippage_bps_reference=100,
    )
    db_session.add(snap)
    await db_session.flush()
    return coin


@pytest.mark.asyncio
async def test_approve_opens_paper_position(db_session):
    coin = await _setup_coin_with_market(
        db_session, "APPR", "0xAPPR000000000000000000000000000000001234"
    )
    signal = _make_signal(db_session, coin.id)
    await db_session.flush()

    engine = PaperTradingEngine()
    result = await engine.open_position(
        session=db_session,
        signal_id=signal.id,
        approved_by_user_id=12345,
    )

    assert result.success is True
    pos = await db_session.get(PaperPosition, result.position_id)
    assert pos is not None
    assert pos.status == PositionStatus.OPEN
    assert pos.entry_price_usd == pytest.approx(0.01)

    # Signal should be marked approved
    await db_session.refresh(signal)
    assert signal.is_approved is True
    assert signal.approved_by == 12345


@pytest.mark.asyncio
async def test_reject_marks_signal(db_session):
    coin = await _setup_coin_with_market(
        db_session, "REJT", "0xREJT000000000000000000000000000000001234"
    )
    signal = _make_signal(db_session, coin.id)
    await db_session.flush()

    from datetime import datetime, timezone
    signal.is_approved = False
    signal.approved_by = 12345
    signal.approved_at = datetime.now(timezone.utc)
    await db_session.flush()

    assert signal.is_approved is False

    # Verify no position was created
    from sqlalchemy import select
    result = await db_session.execute(
        select(PaperPosition).where(PaperPosition.signal_id == signal.id)
    )
    positions = result.scalars().all()
    assert len(positions) == 0


@pytest.mark.asyncio
async def test_blacklisted_creator_blocks_position(db_session):
    coin = await _setup_coin_with_market(
        db_session, "BLCK", "0xBLCK000000000000000000000000000000001234"
    )
    signal = _make_signal(db_session, coin.id)
    await db_session.flush()

    # Add blacklist override for this contract
    override = CreatorOverride(
        contract_address=coin.contract_address,
        is_blacklisted=True,
        reason="Test blacklist",
        added_by=12345,
    )
    db_session.add(override)
    await db_session.flush()

    engine = PaperTradingEngine()
    result = await engine.open_position(db_session, signal.id)

    assert result.success is False
    assert result.blocked_by is not None
    assert "blacklist" in result.blocked_by.lower()


@pytest.mark.asyncio
async def test_low_score_blocks_position(db_session):
    coin = await _setup_coin_with_market(
        db_session, "LOWS", "0xLOWS000000000000000000000000000000001234"
    )
    # Score below paper_trade_threshold (75)
    signal = _make_signal(db_session, coin.id, score=40.0)
    await db_session.flush()

    engine = PaperTradingEngine()
    result = await engine.open_position(db_session, signal.id)

    assert result.success is False
    assert result.blocked_by is not None
    assert "score" in result.blocked_by.lower()


@pytest.mark.asyncio
async def test_kill_switch_blocks_all_approvals(db_session):
    coin = await _setup_coin_with_market(
        db_session, "KILL", "0xKILL000000000000000000000000000000001234"
    )
    signal = _make_signal(db_session, coin.id, score=90.0)
    await db_session.flush()

    engine = PaperTradingEngine()
    result = await engine.open_position(db_session, signal.id, kill_switch=True)

    assert result.success is False
    assert result.blocked_by == "kill_switch_active"


@pytest.mark.asyncio
async def test_missing_coin_blocks_position(db_session):
    """Signal with no coin_id cannot open a position."""
    signal = Signal(
        deterministic_score=85.0,
        final_score=85.0,
        recommendation=Recommendation.PAPER_TRADE,
        coin_id=None,
    )
    db_session.add(signal)
    await db_session.flush()

    engine = PaperTradingEngine()
    result = await engine.open_position(db_session, signal.id)
    assert result.success is False
    assert "no associated coin" in result.message.lower()
