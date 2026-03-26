"""
tests/integration/test_live_position_manager.py
─────────────────────────────────────────────────────────────────────────────
Integration tests for LivePositionManager.
All tests use dry_run=True (the default) and mock the live adapter.
No real RPC calls are made.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.db.models import (
    CoinMarketSnapshot,
    LivePosition,
    PositionStatus,
    Recommendation,
    Signal,
    ZoraCoin,
)
from app.trading.live_execution import (
    LiveExecutionDisabledAdapter,
    LivePositionManager,
    LiveTradingDisabledError,
    ZoraOnChainAdapter,
)


async def _setup(db_session, symbol="LIVE", addr=None, score=85.0, approved=True):
    if addr is None:
        addr = f"0x{'CAFE' * 10}"
    coin = ZoraCoin(
        contract_address=addr[:42].ljust(42, "0"),
        symbol=symbol,
        launched_at=datetime.now(timezone.utc) - timedelta(hours=3),
    )
    db_session.add(coin)
    await db_session.flush()

    snap = CoinMarketSnapshot(
        coin_id=coin.id,
        price_usd=0.005,
        liquidity_usd=80_000.0,
        slippage_bps_reference=100,
    )
    db_session.add(snap)

    signal = Signal(
        coin_id=coin.id,
        deterministic_score=score,
        final_score=score,
        recommendation=Recommendation.LIVE_TRADE_READY,
        is_approved=True if approved else None,
        approved_by=12345 if approved else None,
    )
    db_session.add(signal)
    await db_session.flush()
    return signal, coin


@pytest.mark.asyncio
async def test_live_position_manager_dry_run(db_session):
    """dry_run=True should pass all checks and create a position row."""
    signal, coin = await _setup(db_session)

    mock_buy = AsyncMock(return_value={
        "tx_hash": None,
        "actual_slippage_bps": 80,
        "gas_cost_usd": 0.5,
        "tokens_received": None,
        "dry_run": True,
    })

    with (
        patch("app.trading.live_execution.settings") as ms,
        patch("app.trading.live_execution.get_live_adapter") as mock_get_adapter,
    ):
        ms.live_trading_enabled = True
        ms.wallet_address = "0xWALLET00000000000000000000000000000001"
        ms.max_position_size_usd = 100.0
        ms.max_slippage_bps = 200
        ms.min_liquidity_usd = 10_000.0
        ms.max_daily_loss_usd = 500.0
        ms.max_concurrent_positions = 5
        ms.no_trade_after_launch_seconds = 300
        ms.score_paper_trade_threshold = 75
        ms.paper_trade_size_usd = 50.0

        mock_adapter = AsyncMock(spec=ZoraOnChainAdapter)
        mock_adapter.execute_buy = mock_buy
        mock_get_adapter.return_value = mock_adapter

        manager = LivePositionManager()
        result = await manager.open_position(
            session=db_session,
            signal_id=signal.id,
            approved_by_user_id=12345,
            dry_run=True,
            kill_switch=False,
        )

    assert result.success is True
    assert result.dry_run is True
    assert result.position_id is not None

    pos = await db_session.get(LivePosition, result.position_id)
    assert pos is not None
    assert pos.status == PositionStatus.OPEN
    assert pos.buy_tx_hash is None  # dry run


@pytest.mark.asyncio
async def test_live_manager_blocked_when_disabled(db_session):
    """Should block immediately if live_trading_enabled is false."""
    signal, _ = await _setup(db_session)

    with patch("app.trading.live_execution.settings") as ms:
        ms.live_trading_enabled = False
        manager = LivePositionManager()
        result = await manager.open_position(
            db_session, signal.id, approved_by_user_id=12345, dry_run=True
        )

    assert result.success is False
    assert result.blocked_by == "live_trading_disabled"


@pytest.mark.asyncio
async def test_live_manager_blocked_by_kill_switch(db_session):
    signal, _ = await _setup(db_session)

    with patch("app.trading.live_execution.settings") as ms:
        ms.live_trading_enabled = True
        manager = LivePositionManager()
        result = await manager.open_position(
            db_session, signal.id, approved_by_user_id=12345,
            dry_run=True, kill_switch=True
        )

    assert result.success is False
    assert result.blocked_by == "kill_switch_active"


@pytest.mark.asyncio
async def test_live_manager_blocked_without_approval(db_session):
    """Signal must be approved before live execution."""
    signal, _ = await _setup(db_session, approved=False)

    with patch("app.trading.live_execution.settings") as ms:
        ms.live_trading_enabled = True
        manager = LivePositionManager()
        result = await manager.open_position(
            db_session, signal.id, approved_by_user_id=12345, dry_run=True
        )

    assert result.success is False
    assert result.blocked_by == "not_approved"


@pytest.mark.asyncio
async def test_live_manager_blocked_on_adapter_error(db_session):
    """If the adapter raises, the manager returns failure (no crash)."""
    signal, _ = await _setup(db_session)

    with (
        patch("app.trading.live_execution.settings") as ms,
        patch("app.trading.live_execution.get_live_adapter") as mock_get_adapter,
    ):
        ms.live_trading_enabled = True
        ms.max_position_size_usd = 100.0
        ms.max_slippage_bps = 200
        ms.min_liquidity_usd = 10_000.0
        ms.max_daily_loss_usd = 500.0
        ms.max_concurrent_positions = 5
        ms.no_trade_after_launch_seconds = 300
        ms.score_paper_trade_threshold = 75

        mock_adapter = AsyncMock()
        mock_adapter.execute_buy = AsyncMock(
            side_effect=LiveTradingDisabledError("Adapter error")
        )
        mock_get_adapter.return_value = mock_adapter

        manager = LivePositionManager()
        result = await manager.open_position(
            db_session, signal.id, approved_by_user_id=12345, dry_run=True
        )

    assert result.success is False
    assert "Adapter error" in result.message
