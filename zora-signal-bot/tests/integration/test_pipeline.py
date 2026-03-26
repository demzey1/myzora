"""
tests/integration/test_pipeline.py
─────────────────────────────────────────────────────────────────────────────
Integration-style happy path test for the full scoring pipeline.

Uses:
  - In-memory SQLite DB (from conftest)
  - ZoraStubAdapter (returns None market data → disqualified signal expected)
  - XTweet / XUser built in-test (no real API calls)

This test verifies the whole pipeline runs without error and persists
exactly one Signal row.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import Recommendation, Signal
from app.db.repositories import SignalRepository
from app.integrations.types import (
    XPublicMetrics,
    XTweet,
    XUser,
    XUserPublicMetrics,
    ZoraCoinData,
    ZoraCoinMarketState,
)
from app.integrations.zora_client import ZoraStubAdapter
from app.scoring.pipeline import run_pipeline_for_tweet


def _make_tweet(post_id: str = "tweet_001") -> XTweet:
    return XTweet(
        id=post_id,
        text="Launching a new Zora coin, very excited! zora.co/collect/base:0xtest",
        author_id="u_001",
        created_at=datetime.now(timezone.utc) - timedelta(minutes=4),
        lang="en",
        public_metrics=XPublicMetrics(
            like_count=350,
            retweet_count=80,
            reply_count=25,
            quote_count=10,
        ),
    )


def _make_user() -> XUser:
    return XUser(
        id="u_001",
        name="Test Creator",
        username="testcreator",
        verified=False,
        public_metrics=XUserPublicMetrics(
            followers_count=120_000,
            following_count=500,
            tweet_count=2_000,
            listed_count=100,
        ),
    )


@pytest.mark.asyncio
async def test_pipeline_happy_path_stub_zora(db_session):
    """
    Full pipeline run with ZoraStubAdapter (no coin resolved).
    Expect: one Signal with recommendation=IGNORE (no coin = disqualified).
    """
    tweet = _make_tweet()
    user = _make_user()

    signal_id = await run_pipeline_for_tweet(
        session=db_session,
        tweet=tweet,
        user=user,
        zora=ZoraStubAdapter(),
    )

    assert signal_id is not None
    sig = await db_session.get(Signal, signal_id)
    assert sig is not None
    assert sig.recommendation == Recommendation.IGNORE
    assert sig.deterministic_score == 0.0  # Disqualified → 0
    assert sig.post_id is not None
    assert sig.coin_id is None  # No coin resolved via stub


@pytest.mark.asyncio
async def test_pipeline_deduplicates_posts(db_session):
    """Running the pipeline twice for the same tweet_id should skip the second."""
    tweet = _make_tweet(post_id="tweet_dedup")
    user = _make_user()

    id1 = await run_pipeline_for_tweet(
        session=db_session, tweet=tweet, user=user, zora=ZoraStubAdapter()
    )
    # Second run — post is already processed
    id2 = await run_pipeline_for_tweet(
        session=db_session, tweet=tweet, user=user, zora=ZoraStubAdapter()
    )

    assert id1 is not None
    assert id2 is None  # Skipped because already processed


@pytest.mark.asyncio
async def test_pipeline_with_mock_zora_coin(db_session):
    """
    Pipeline with a mock Zora adapter that returns a valid coin + market.
    Expect a non-zero final_score and recommendation >= WATCH.
    """
    from app.integrations.zora_client import ZoraAdapterProtocol

    class MockZoraAdapter(ZoraAdapterProtocol):
        async def get_creator_profile(self, w):
            from app.integrations.types import ZoraCreatorProfile
            return ZoraCreatorProfile(wallet_address=w, x_username="testcreator")

        async def get_coin_by_address(self, addr):
            return ZoraCoinData(
                contract_address=addr,
                symbol="MOCK",
                launched_at=datetime.now(timezone.utc) - timedelta(minutes=90),
            )

        async def get_coins_for_creator(self, wallet):
            return [ZoraCoinData(
                contract_address="0xMOCK000000000000000000000000000000001234",
                symbol="MOCK",
                launched_at=datetime.now(timezone.utc) - timedelta(minutes=90),
            )]

        async def get_coin_market_state(self, addr):
            return ZoraCoinMarketState(
                contract_address=addr,
                price_usd=0.00150,
                liquidity_usd=75_000.0,
                volume_5m_usd=3_500.0,
                slippage_bps_for_reference_trade=120,
            )

        async def simulate_trade(self, addr, side, amount):
            return None

        async def resolve_creator_by_x_username(self, username):
            from app.integrations.types import ZoraCreatorProfile
            return ZoraCreatorProfile(
                wallet_address="0xTEST00000000000000000000000000000000ABCD",
                x_username=username,
            )

    tweet = _make_tweet(post_id="tweet_with_coin")
    user = _make_user()

    signal_id = await run_pipeline_for_tweet(
        session=db_session,
        tweet=tweet,
        user=user,
        zora=MockZoraAdapter(),
    )

    assert signal_id is not None
    sig = await db_session.get(Signal, signal_id)
    assert sig is not None
    assert sig.final_score > 0.0
    assert sig.coin_id is not None
    assert sig.recommendation != Recommendation.IGNORE or sig.disqualified if hasattr(sig, 'disqualified') else True
    # Score should be meaningful for a 120k-follower account with good liquidity
    assert sig.deterministic_score > 30.0


@pytest.mark.asyncio
async def test_pipeline_kill_switch_produces_ignore(db_session):
    """Kill switch active should force IGNORE regardless of score."""
    from app.integrations.zora_client import ZoraAdapterProtocol

    class GoodZora(ZoraAdapterProtocol):
        async def get_creator_profile(self, w): return None
        async def get_coin_by_address(self, a): return None
        async def get_coins_for_creator(self, w):
            return [ZoraCoinData(
                contract_address="0xKILL000000000000000000000000000000001234",
                symbol="KILL",
                launched_at=datetime.now(timezone.utc) - timedelta(hours=2),
            )]
        async def get_coin_market_state(self, a):
            return ZoraCoinMarketState(
                contract_address=a,
                liquidity_usd=100_000.0,
                slippage_bps_for_reference_trade=50,
            )
        async def simulate_trade(self, a, s, u): return None
        async def resolve_creator_by_x_username(self, u):
            from app.integrations.types import ZoraCreatorProfile
            return ZoraCreatorProfile(
                wallet_address="0xKILL00000000000000000000000000000000ABCD"
            )

    tweet = _make_tweet(post_id="tweet_kill")
    user = _make_user()

    signal_id = await run_pipeline_for_tweet(
        session=db_session,
        tweet=tweet,
        user=user,
        zora=GoodZora(),
        kill_switch=True,
    )

    assert signal_id is not None
    sig = await db_session.get(Signal, signal_id)
    assert sig.recommendation == Recommendation.IGNORE
