"""
tests/integration/test_paper_trading_flow.py
─────────────────────────────────────────────────────────────────────────────
Integration test for the full paper trading flow:
  pipeline → signal → approve → open position → check exits → close position

Uses in-memory SQLite DB and mock Zora adapter.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import (
    CoinMarketSnapshot,
    PaperPosition,
    PositionStatus,
    Recommendation,
    Signal,
    ZoraCoin,
)
from app.db.repositories.positions import PaperPositionRepository
from app.integrations.types import (
    XPublicMetrics,
    XTweet,
    XUser,
    XUserPublicMetrics,
    ZoraCoinData,
    ZoraCoinMarketState,
)
from app.integrations.zora_client import ZoraAdapterProtocol
from app.scoring.pipeline import run_pipeline_for_tweet
from app.trading.paper_engine import PaperTradingEngine, _compute_pnl


# ── Fixtures ──────────────────────────────────────────────────────────────────

class FullMockZora(ZoraAdapterProtocol):
    """Returns a complete, valid coin + market state."""

    ADDR = "0xFULL000000000000000000000000000000001234"

    async def get_creator_profile(self, w):
        return None

    async def get_coin_by_address(self, addr):
        return ZoraCoinData(
            contract_address=addr, symbol="FULL",
            launched_at=datetime.now(timezone.utc) - timedelta(hours=3),
        )

    async def get_coins_for_creator(self, wallet):
        return [ZoraCoinData(
            contract_address=self.ADDR, symbol="FULL",
            launched_at=datetime.now(timezone.utc) - timedelta(hours=3),
        )]

    async def get_coin_market_state(self, addr):
        return ZoraCoinMarketState(
            contract_address=addr,
            price_usd=0.005,
            liquidity_usd=80_000.0,
            volume_5m_usd=4_000.0,
            slippage_bps_for_reference_trade=80,
        )

    async def simulate_trade(self, addr, side, amount):
        return None

    async def resolve_creator_by_x_username(self, username):
        from app.integrations.types import ZoraCreatorProfile
        return ZoraCreatorProfile(
            wallet_address="0xCREATOR00000000000000000000000000001234",
            x_username=username,
        )


def _make_tweet(post_id: str = "flow_tweet_001") -> XTweet:
    return XTweet(
        id=post_id,
        text="Big announcement — new Zora coin dropping now!",
        author_id="u_flow_001",
        created_at=datetime.now(timezone.utc) - timedelta(minutes=3),
        lang="en",
        public_metrics=XPublicMetrics(
            like_count=800, retweet_count=200, reply_count=60, quote_count=25,
        ),
    )


def _make_user() -> XUser:
    return XUser(
        id="u_flow_001",
        name="Flow Creator",
        username="flowcreator",
        public_metrics=XUserPublicMetrics(
            followers_count=250_000,
            following_count=300,
            tweet_count=5_000,
            listed_count=200,
        ),
    )


# ── Tests ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_full_paper_trade_open_and_close(db_session):
    """
    Happy path:
    1. Run the pipeline → produces a PAPER_TRADE signal
    2. Open a paper position via the engine
    3. Simulate price hitting take-profit
    4. Close the position and verify PnL > 0
    """
    tweet = _make_tweet()
    user = _make_user()
    zora = FullMockZora()

    signal_id = await run_pipeline_for_tweet(
        session=db_session,
        tweet=tweet,
        user=user,
        zora=zora,
        paper_trading=True,
        live_trading=False,
    )
    assert signal_id is not None, "Pipeline must produce a signal"

    sig = await db_session.get(Signal, signal_id)
    assert sig is not None
    # With a 250k-follower account, fresh tweet, good liquidity → should score well
    assert sig.final_score > 0, f"Expected non-zero score, got {sig.final_score}"

    # Force a valid recommendation for this test
    # (score depends on time-of-day + thresholds; adjust if needed)
    if sig.recommendation not in (Recommendation.PAPER_TRADE, Recommendation.LIVE_TRADE_READY):
        # Bump score to force PAPER_TRADE for testing
        sig.final_score = 78.0
        sig.recommendation = Recommendation.PAPER_TRADE
        await db_session.flush()

    # Make sure there's a market snapshot (pipeline should have added one)
    assert sig.coin_id is not None, "Signal must have a coin"

    # Verify market snapshot exists
    from app.db.repositories.coins import CoinMarketSnapshotRepository
    snap = await CoinMarketSnapshotRepository(db_session).get_latest_for_coin(sig.coin_id)
    assert snap is not None, "Market snapshot must exist"
    assert snap.price_usd == pytest.approx(0.005)

    # Open paper position
    engine = PaperTradingEngine()
    open_result = await engine.open_position(
        session=db_session,
        signal_id=signal_id,
        approved_by_user_id=12345,
        kill_switch=False,
    )

    assert open_result.success is True, f"Expected success, got: {open_result.message}"
    position_id = open_result.position_id
    assert position_id is not None

    # Verify position row
    position = await db_session.get(PaperPosition, position_id)
    assert position is not None
    assert position.status == PositionStatus.OPEN
    assert position.entry_price_usd == pytest.approx(0.005)

    # Check exit conditions at +60% price (above 50% take-profit)
    exit_reason = await engine.check_exit_conditions(
        db_session, position, current_price_usd=0.008
    )
    assert exit_reason == "TAKE_PROFIT"

    # Close the position
    close_result = await engine.close_position(
        session=db_session,
        position_id=position_id,
        exit_price_usd=0.008,
        exit_reason="TAKE_PROFIT",
    )
    assert close_result.success is True
    assert close_result.pnl_usd is not None
    assert close_result.pnl_usd > 0, "Take-profit exit should be profitable"
    assert close_result.pnl_pct > 0

    # Verify DB state
    await db_session.refresh(position)
    assert position.status == PositionStatus.CLOSED
    assert position.exit_reason == "TAKE_PROFIT"
    assert position.pnl_usd == pytest.approx(close_result.pnl_usd, abs=0.01)


@pytest.mark.asyncio
async def test_paper_trade_stop_loss_flow(db_session):
    """Stop-loss: price drops → close at a loss."""
    tweet = _make_tweet(post_id="sl_tweet_001")
    user = _make_user()

    signal_id = await run_pipeline_for_tweet(
        session=db_session,
        tweet=tweet,
        user=user,
        zora=FullMockZora(),
        paper_trading=True,
    )
    assert signal_id is not None
    sig = await db_session.get(Signal, signal_id)
    sig.final_score = 78.0
    sig.recommendation = Recommendation.PAPER_TRADE
    await db_session.flush()

    engine = PaperTradingEngine()
    open_result = await engine.open_position(db_session, signal_id)
    assert open_result.success is True

    position = await db_session.get(PaperPosition, open_result.position_id)
    # Drop price 20% → stop-loss at 15%
    exit_reason = await engine.check_exit_conditions(db_session, position, 0.004)
    assert exit_reason == "STOP_LOSS"

    close_result = await engine.close_position(
        db_session, position.id, 0.004, "STOP_LOSS"
    )
    assert close_result.success is True
    assert close_result.pnl_usd < 0, "Stop-loss should record a loss"


@pytest.mark.asyncio
async def test_pnl_summary_after_multiple_trades(db_session):
    """Verify aggregate PnL summary reflects multiple closed positions."""
    from app.db.repositories.positions import PaperPositionRepository
    from app.db.models import Signal, Recommendation, ZoraCoin

    # Manually insert two closed positions
    signal = Signal(
        deterministic_score=80.0, final_score=80.0,
        recommendation=Recommendation.PAPER_TRADE,
    )
    db_session.add(signal)
    coin = ZoraCoin(
        contract_address="0xSUMM000000000000000000000000000000001234",
        symbol="SUMM",
    )
    db_session.add(coin)
    await db_session.flush()

    now = datetime.now(timezone.utc)

    pos1 = PaperPosition(
        signal_id=signal.id, coin_id=coin.id,
        size_usd=50.0, entry_price_usd=1.0,
        status=PositionStatus.CLOSED,
        pnl_usd=15.0, pnl_pct=30.0,
        closed_at=now,
    )
    pos2 = PaperPosition(
        signal_id=signal.id, coin_id=coin.id,
        size_usd=50.0, entry_price_usd=1.0,
        status=PositionStatus.STOPPED,
        pnl_usd=-7.5, pnl_pct=-15.0,
        closed_at=now,
    )
    db_session.add_all([pos1, pos2])
    await db_session.flush()

    repo = PaperPositionRepository(db_session)
    summary = await repo.get_pnl_summary()

    assert summary["total_trades"] >= 2
    assert summary["winning_trades"] >= 1
    assert summary["losing_trades"] >= 1
    assert summary["total_pnl_usd"] == pytest.approx(15.0 + (-7.5), abs=0.01)
    assert 0 < summary["win_rate_pct"] < 100


@pytest.mark.asyncio
async def test_kill_switch_prevents_opening(db_session):
    """Kill switch must block open_position regardless of signal quality."""
    tweet = _make_tweet(post_id="ks_tweet_001")
    user = _make_user()

    signal_id = await run_pipeline_for_tweet(
        session=db_session,
        tweet=tweet,
        user=user,
        zora=FullMockZora(),
    )
    assert signal_id is not None
    sig = await db_session.get(Signal, signal_id)
    sig.final_score = 90.0
    sig.recommendation = Recommendation.PAPER_TRADE
    await db_session.flush()

    engine = PaperTradingEngine()
    result = await engine.open_position(
        db_session, signal_id, kill_switch=True
    )
    assert result.success is False
    assert result.blocked_by is not None
    assert "kill_switch" in result.blocked_by
