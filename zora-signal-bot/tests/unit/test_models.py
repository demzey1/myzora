"""
tests/unit/test_models.py
Tests for ORM model creation and relationships.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio

from app.db.models import (
    BotUser,
    CommandAuditLog,
    Creator,
    MonitoredAccount,
    PaperPosition,
    PositionStatus,
    Post,
    Recommendation,
    RiskEvent,
    RiskEventType,
    Signal,
    ZoraCoin,
)


@pytest.mark.asyncio
async def test_create_bot_user(db_session):
    user = BotUser(
        telegram_user_id=111222333,
        username="operator",
        first_name="Alice",
        is_admin=True,
    )
    db_session.add(user)
    await db_session.flush()
    assert user.id is not None
    assert user.is_admin is True
    assert user.is_active is True


@pytest.mark.asyncio
async def test_create_monitored_account(db_session):
    account = MonitoredAccount(
        x_user_id="123456789",
        x_username="example_creator",
        follower_count=50_000,
    )
    db_session.add(account)
    await db_session.flush()
    assert account.id is not None
    assert account.is_active is True
    assert account.is_blacklisted is False


@pytest.mark.asyncio
async def test_create_zora_coin(db_session):
    creator = Creator(wallet_address="0xAbCdEf1234567890AbCdEf1234567890AbCdEf12")
    db_session.add(creator)
    await db_session.flush()

    coin = ZoraCoin(
        contract_address="0x1234567890AbCdEf1234567890AbCdEf12345678",
        symbol="TEST",
        name="Test Coin",
        creator_id=creator.id,
        launched_at=datetime.now(timezone.utc),
    )
    db_session.add(coin)
    await db_session.flush()
    assert coin.id is not None
    assert coin.chain_id == 8453  # Base mainnet


@pytest.mark.asyncio
async def test_create_signal_with_recommendation(db_session):
    signal = Signal(
        deterministic_score=72.5,
        final_score=72.5,
        recommendation=Recommendation.ALERT,
        risk_notes="Low liquidity",
    )
    db_session.add(signal)
    await db_session.flush()
    assert signal.id is not None
    assert signal.recommendation == Recommendation.ALERT
    assert signal.is_approved is None  # Pending


@pytest.mark.asyncio
async def test_create_paper_position(db_session):
    signal = Signal(
        deterministic_score=80.0,
        final_score=80.0,
        recommendation=Recommendation.PAPER_TRADE,
    )
    db_session.add(signal)

    coin = ZoraCoin(
        contract_address="0xAAAA567890AbCdEf1234567890AbCdEf12345678",
        symbol="PPRT",
    )
    db_session.add(coin)
    await db_session.flush()

    position = PaperPosition(
        signal_id=signal.id,
        coin_id=coin.id,
        size_usd=50.0,
        entry_price_usd=0.001234,
        status=PositionStatus.OPEN,
    )
    db_session.add(position)
    await db_session.flush()
    assert position.id is not None
    assert position.stop_loss_pct == 0.15
    assert position.take_profit_pct == 0.50


@pytest.mark.asyncio
async def test_recommendation_enum_values():
    """All expected recommendation values should exist."""
    values = {r.value for r in Recommendation}
    assert values == {"IGNORE", "WATCH", "ALERT", "PAPER_TRADE", "LIVE_TRADE_READY"}


@pytest.mark.asyncio
async def test_risk_event_types_exist():
    types = {r.value for r in RiskEventType}
    assert "KILL_SWITCH" in types
    assert "LOW_LIQUIDITY" in types
    assert "HIGH_SLIPPAGE" in types
