"""
tests/unit/test_zora_client.py
Tests for the Zora adapter layer.

Because Zora's API shape is not fully confirmed (TODO_ZORA), these tests:
  1. Verify the ZoraStubAdapter returns safe None/[] defaults
  2. Verify the ZoraApiAdapter's _parse_* methods map fields correctly
     using the best-guess field names (will be updated when API is confirmed)
  3. Verify the Protocol contract is fulfilled by both adapters
"""

from __future__ import annotations

import pytest

from app.integrations.zora_client import ZoraApiAdapter, ZoraStubAdapter


# ── ZoraStubAdapter ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stub_creator_profile_returns_none():
    adapter = ZoraStubAdapter()
    result = await adapter.get_creator_profile("0xABCD")
    assert result is None


@pytest.mark.asyncio
async def test_stub_coin_returns_none():
    adapter = ZoraStubAdapter()
    result = await adapter.get_coin_by_address("0x1234")
    assert result is None


@pytest.mark.asyncio
async def test_stub_coins_for_creator_returns_empty():
    adapter = ZoraStubAdapter()
    result = await adapter.get_coins_for_creator("0xABCD")
    assert result == []


@pytest.mark.asyncio
async def test_stub_market_state_returns_none():
    adapter = ZoraStubAdapter()
    result = await adapter.get_coin_market_state("0x1234")
    assert result is None


@pytest.mark.asyncio
async def test_stub_trade_simulation_returns_none():
    adapter = ZoraStubAdapter()
    result = await adapter.simulate_trade("0x1234", "buy", 50.0)
    assert result is None


@pytest.mark.asyncio
async def test_stub_resolve_x_username_returns_none():
    adapter = ZoraStubAdapter()
    result = await adapter.resolve_creator_by_x_username("someuser")
    assert result is None


# ── ZoraApiAdapter._parse_* methods ──────────────────────────────────────────

def test_parse_creator_profile_camelCase_fields():
    """
    Test that _parse_creator_profile handles camelCase field names
    (the most common REST convention for JS-origin APIs).
    """
    adapter = ZoraApiAdapter(base_url="https://api.zora.co")
    raw = {
        "address": "0xAAAABBBBCCCCDDDD1111222233334444AAAABBBB",
        "displayName": "Test Creator",
        "bio": "I make coins",
        "profileUrl": "https://zora.co/@testcreator",
        "twitterHandle": "testcreator",
    }
    profile = adapter._parse_creator_profile(raw)
    assert profile.wallet_address == "0xAAAABBBBCCCCDDDD1111222233334444AAAABBBB"
    assert profile.display_name == "Test Creator"
    assert profile.x_username == "testcreator"


def test_parse_creator_profile_snake_case_fields():
    """Also works with snake_case fallback."""
    adapter = ZoraApiAdapter(base_url="https://api.zora.co")
    raw = {
        "wallet_address": "0xAAAABBBBCCCCDDDD1111222233334444AAAABBBB",
        "display_name": "Snake Creator",
        "twitter_username": "snakecreator",
    }
    profile = adapter._parse_creator_profile(raw)
    assert profile.x_username == "snakecreator"


def test_parse_creator_profile_missing_fields_returns_none():
    adapter = ZoraApiAdapter(base_url="https://api.zora.co")
    raw = {"address": "0xAAAA1234567890AAAA1234567890AAAA12345678"}
    profile = adapter._parse_creator_profile(raw)
    assert profile.display_name is None
    assert profile.x_username is None


def test_parse_coin_data_basic():
    adapter = ZoraApiAdapter(base_url="https://api.zora.co")
    raw = {
        "address": "0x1234567890ABCDEF1234567890ABCDEF12345678",
        "symbol": "MYCOIN",
        "name": "My Test Coin",
        "creatorAddress": "0xAAAABBBBCCCCDDDD1111222233334444AAAABBBB",
        "createdAt": "2024-01-15T10:30:00Z",
        "chainId": 8453,
        "decimals": 18,
    }
    coin = adapter._parse_coin_data(raw)
    assert coin.symbol == "MYCOIN"
    assert coin.contract_address == "0x1234567890ABCDEF1234567890ABCDEF12345678"
    assert coin.chain_id == 8453
    assert coin.launched_at is not None
    assert coin.launched_at.year == 2024


def test_parse_coin_data_missing_date():
    adapter = ZoraApiAdapter(base_url="https://api.zora.co")
    raw = {
        "address": "0xAAAA1234567890AAAA1234567890AAAA12345678",
        "symbol": "NODATE",
    }
    coin = adapter._parse_coin_data(raw)
    assert coin.launched_at is None


def test_parse_market_state_camelCase():
    adapter = ZoraApiAdapter(base_url="https://api.zora.co")
    raw = {
        "priceUsd": "0.001234",
        "liquidityUsd": "50000.0",
        "volume5m": "1234.56",
        "volume1h": "8000.0",
        "volume24h": "50000.0",
        "marketCapUsd": "250000.0",
        "holderCount": 150,
    }
    market = adapter._parse_market_state(raw, "0xCCCC1234567890CCCC1234567890CCCC12345678")
    assert market.price_usd == pytest.approx(0.001234)
    assert market.liquidity_usd == pytest.approx(50000.0)
    assert market.volume_5m_usd == pytest.approx(1234.56)
    assert market.holder_count == 150


def test_parse_market_state_missing_fields_return_none():
    adapter = ZoraApiAdapter(base_url="https://api.zora.co")
    market = adapter._parse_market_state({}, "0xDDDD1234567890DDDD1234567890DDDD12345678")
    assert market.price_usd is None
    assert market.liquidity_usd is None
    assert market.volume_5m_usd is None


# ── Protocol compliance ───────────────────────────────────────────────────────

def test_stub_satisfies_protocol():
    """ZoraStubAdapter must implement all ZoraAdapterProtocol methods."""
    from app.integrations.zora_client import ZoraAdapterProtocol
    adapter = ZoraStubAdapter()
    assert hasattr(adapter, "get_creator_profile")
    assert hasattr(adapter, "get_coin_by_address")
    assert hasattr(adapter, "get_coins_for_creator")
    assert hasattr(adapter, "get_coin_market_state")
    assert hasattr(adapter, "simulate_trade")
    assert hasattr(adapter, "resolve_creator_by_x_username")
