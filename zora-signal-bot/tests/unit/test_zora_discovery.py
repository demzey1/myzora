"""
tests/unit/test_zora_discovery.py
Tests for Zora discovery service: scoring, ranking, hard-reject rules,
creator_only vs keyword_only vs hybrid modes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.integrations.types import ZoraCoinData, ZoraCoinMarketState
from app.integrations.zora_discovery import (
    CoinCandidate,
    ZoraDiscoveryService,
    _hard_reject,
    _score_liquidity,
    _score_momentum,
    _score_relevance,
)


def _coin(symbol="TEST", age_hours=3, addr=None) -> ZoraCoinData:
    addr = addr or f"0x{'A' * 40}"
    return ZoraCoinData(
        contract_address=addr[:42],
        symbol=symbol,
        name=f"{symbol} Token",
        launched_at=datetime.now(timezone.utc) - timedelta(hours=age_hours),
    )


def _market(liq=50_000.0, slip=100, vol_5m=3_000.0, holders=50) -> ZoraCoinMarketState:
    return ZoraCoinMarketState(
        contract_address="0x" + "A" * 40,
        price_usd=0.001,
        liquidity_usd=liq,
        volume_5m_usd=vol_5m,
        slippage_bps_for_reference_trade=slip,
        holder_count=holders,
    )


# ── Hard reject rules ──────────────────────────────────────────────────────────

def test_hard_reject_no_market():
    flags = _hard_reject(_coin(), None)
    assert "no_market_data" in flags


def test_hard_reject_low_liquidity():
    market = _market(liq=500.0)
    flags = _hard_reject(_coin(), market)
    assert "low_liquidity" in flags


def test_hard_reject_high_slippage():
    market = _market(slip=1000)
    flags = _hard_reject(_coin(), market)
    assert "high_slippage" in flags


def test_hard_reject_too_new():
    # Coin launched 1 minute ago (well within 300s lockout)
    coin = ZoraCoinData(
        contract_address="0x" + "B" * 40,
        symbol="NEW",
        launched_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    flags = _hard_reject(coin, _market())
    assert "too_new" in flags


def test_no_reject_for_good_coin():
    flags = _hard_reject(_coin(age_hours=2), _market())
    assert flags == []


# ── Score functions ────────────────────────────────────────────────────────────

def test_score_liquidity_zero():
    assert _score_liquidity(None) == 0
    assert _score_liquidity(0) == 0


def test_score_liquidity_high():
    assert _score_liquidity(1_000_000) == 10


def test_score_liquidity_mid():
    score = _score_liquidity(50_000)
    assert 1 <= score <= 9


def test_score_momentum_with_volume_and_holders():
    score = _score_momentum(10_000.0, 100)
    assert score > 0


def test_score_momentum_no_data():
    assert _score_momentum(None, None) == 0


def test_score_relevance_exact_symbol_match():
    coin = _coin(symbol="ZORA")
    score = _score_relevance(coin, ["zora"], [], ["ZORA"])
    assert score >= 15  # exact cashtag/symbol match


def test_score_relevance_name_partial():
    coin = ZoraCoinData(
        contract_address="0x" + "C" * 40,
        symbol="XYZ",
        name="Zora Base Coin",
    )
    score = _score_relevance(coin, ["zora", "base"], [], [])
    assert score > 0


def test_score_relevance_no_match():
    coin = _coin(symbol="UNRELATED")
    score = _score_relevance(coin, ["base", "zora"], [], [])
    assert score == 0


# ── Discovery service (mocked adapter) ────────────────────────────────────────

def _make_mock_adapter(creator_coins=None, trending=None):
    from app.integrations.types import ZoraCreatorProfile
    adapter = AsyncMock()
    adapter.resolve_creator_by_x_username = AsyncMock(
        return_value=ZoraCreatorProfile(
            wallet_address="0x" + "D" * 40,
            x_username="testcreator",
        )
    )
    adapter.get_creator_profile = AsyncMock(return_value=None)
    adapter.get_coins_for_creator = AsyncMock(return_value=creator_coins or [])
    adapter.get_coin_market_state = AsyncMock(return_value=_market())
    adapter.explore_trending = AsyncMock(return_value=trending or [])
    return adapter


@pytest.mark.asyncio
async def test_creator_only_mode_uses_creator_coins():
    coins = [_coin("CREATOR", age_hours=5, addr="0x" + "E" * 40)]
    adapter = _make_mock_adapter(creator_coins=coins)
    service = ZoraDiscoveryService(adapter=adapter)

    result = await service.discover(
        x_username="testcreator",
        creator_wallet=None,
        keywords=["base"],
        entities=["base"],
        cashtags=[],
        mode="creator_only",
    )
    assert any(c.match_type in ("creator_coin", "content_coin") for c in result.candidates)
    # Should NOT call explore_trending in creator_only mode
    adapter.explore_trending.assert_not_called()


@pytest.mark.asyncio
async def test_keyword_only_mode_uses_trending():
    trending = [_coin("TRENDY", age_hours=2, addr="0x" + "F" * 40)]
    # Give TRENDY name relevance
    trending[0] = ZoraCoinData(
        contract_address="0x" + "F" * 40,
        symbol="TRENDY",
        name="Trendy Base Coin",
        launched_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    adapter = _make_mock_adapter(trending=trending)
    service = ZoraDiscoveryService(adapter=adapter)

    result = await service.discover(
        x_username="testcreator",
        creator_wallet=None,
        keywords=["base", "trendy"],
        entities=["base"],
        cashtags=[],
        mode="keyword_only",
    )
    # Should NOT call creator lookup in keyword_only
    adapter.get_coins_for_creator.assert_not_called()


@pytest.mark.asyncio
async def test_hybrid_mode_combines_both():
    creator_coins = [_coin("CRTR", age_hours=3, addr="0x" + "1" * 40)]
    trending = [_coin("TRND", age_hours=4, addr="0x" + "2" * 40)]
    trending[0] = ZoraCoinData(
        contract_address="0x" + "2" * 40,
        symbol="TRND",
        name="Trendy Base Token",
        launched_at=datetime.now(timezone.utc) - timedelta(hours=4),
    )
    adapter = _make_mock_adapter(creator_coins=creator_coins, trending=trending)
    service = ZoraDiscoveryService(adapter=adapter)

    result = await service.discover(
        x_username="creator",
        creator_wallet=None,
        keywords=["base", "trendy"],
        entities=["base"],
        cashtags=[],
        mode="hybrid",
    )
    symbols = [c.coin.symbol for c in result.candidates]
    assert "CRTR" in symbols


@pytest.mark.asyncio
async def test_rejected_coins_excluded_from_ranked():
    # Coin with no market data → rejected
    adapter = _make_mock_adapter(
        creator_coins=[_coin("NODATA", age_hours=2, addr="0x" + "3" * 40)]
    )
    adapter.get_coin_market_state = AsyncMock(return_value=None)
    service = ZoraDiscoveryService(adapter=adapter)

    result = await service.discover(
        x_username="creator",
        creator_wallet=None,
        keywords=[],
        entities=[],
        cashtags=[],
        mode="creator_only",
    )
    ranked = result.ranked()
    assert all(not c.rejected for c in ranked)


@pytest.mark.asyncio
async def test_creator_coin_gets_highest_linkage_score():
    coins = [
        _coin("FIRST", age_hours=3, addr="0x" + "4" * 40),
        _coin("SECOND", age_hours=3, addr="0x" + "5" * 40),
    ]
    adapter = _make_mock_adapter(creator_coins=coins)
    service = ZoraDiscoveryService(adapter=adapter)

    result = await service.discover(
        x_username="creator",
        creator_wallet=None,
        keywords=[],
        entities=[],
        cashtags=[],
        mode="creator_only",
    )
    creator_coins_found = [c for c in result.candidates if c.match_type == "creator_coin"]
    content_coins_found = [c for c in result.candidates if c.match_type == "content_coin"]

    if creator_coins_found and content_coins_found:
        assert creator_coins_found[0].creator_linkage_score > content_coins_found[0].creator_linkage_score
