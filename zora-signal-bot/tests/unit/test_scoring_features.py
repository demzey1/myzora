"""
tests/unit/test_scoring_features.py
Tests for deterministic feature extraction.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from app.integrations.types import (
    XPublicMetrics,
    XTweet,
    XUser,
    XUserPublicMetrics,
    ZoraCoinData,
    ZoraCoinMarketState,
)
from app.scoring.features import (
    extract_coin_features,
    extract_social_features,
    build_feature_set,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _tweet(
    likes=100,
    retweets=20,
    replies=10,
    quotes=5,
    age_minutes: float | None = 5.0,
) -> XTweet:
    created = (
        datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
        if age_minutes is not None else None
    )
    return XTweet(
        id="999",
        text="Test tweet about a cool Zora coin",
        author_id="111",
        created_at=created,
        public_metrics=XPublicMetrics(
            like_count=likes,
            retweet_count=retweets,
            reply_count=replies,
            quote_count=quotes,
        ),
    )


def _user(followers=50_000, verified=False) -> XUser:
    return XUser(
        id="111",
        name="Test Creator",
        username="testcreator",
        verified=verified,
        public_metrics=XUserPublicMetrics(followers_count=followers),
    )


def _coin(age_minutes: float | None = 30.0) -> ZoraCoinData:
    launched = (
        datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
        if age_minutes is not None else None
    )
    return ZoraCoinData(
        contract_address="0xAAAA",
        symbol="TEST",
        launched_at=launched,
    )


def _market(liquidity=50_000.0, slippage_bps=100, volume_5m=5_000.0) -> ZoraCoinMarketState:
    return ZoraCoinMarketState(
        contract_address="0xAAAA",
        price_usd=0.001,
        liquidity_usd=liquidity,
        volume_5m_usd=volume_5m,
        slippage_bps_for_reference_trade=slippage_bps,
    )


# ── Social feature tests ──────────────────────────────────────────────────────

def test_follower_bucket_assignment():
    f = extract_social_features(_tweet(), _user(followers=500))
    assert f.follower_bucket == 0

    f = extract_social_features(_tweet(), _user(followers=5_000))
    assert f.follower_bucket == 1

    f = extract_social_features(_tweet(), _user(followers=50_000))
    assert f.follower_bucket == 2

    f = extract_social_features(_tweet(), _user(followers=200_000))
    assert f.follower_bucket == 3

    f = extract_social_features(_tweet(), _user(followers=1_000_000))
    assert f.follower_bucket == 4


def test_engagement_rate_calculation():
    f = extract_social_features(_tweet(likes=100, retweets=10, replies=5, quotes=5), _user(followers=1_000))
    # total = 120, rate = 120/1000 = 0.12
    assert abs(f.engagement_rate - 0.12) < 0.001


def test_engagement_rate_zero_followers():
    """Division by zero guard."""
    f = extract_social_features(_tweet(), _user(followers=0))
    assert f.engagement_rate >= 0


def test_velocity_with_prev_snapshot():
    prev = {
        "likes": 50,
        "retweets": 5,
        "captured_at": datetime.now(timezone.utc) - timedelta(minutes=10),
    }
    f = extract_social_features(_tweet(likes=100, retweets=20), _user(), prev_metrics_snapshot=prev)
    # likes gained = 50, over 10 minutes → 5/min
    assert f.likes_velocity_per_min is not None
    assert abs(f.likes_velocity_per_min - 5.0) < 0.5


def test_velocity_none_without_snapshot():
    f = extract_social_features(_tweet(), _user(), prev_metrics_snapshot=None)
    assert f.likes_velocity_per_min is None
    assert f.rt_velocity_per_min is None


def test_post_freshness_young_post():
    f = extract_social_features(_tweet(age_minutes=2.0), _user())
    assert f.post_age_minutes is not None
    assert f.post_age_minutes < 5.0


def test_post_age_none_when_no_created_at():
    tweet = _tweet()
    tweet = XTweet(
        id=tweet.id, text=tweet.text, author_id=tweet.author_id,
        created_at=None, public_metrics=tweet.public_metrics,
    )
    f = extract_social_features(tweet, _user())
    assert f.post_age_minutes is None


# ── Coin feature tests ────────────────────────────────────────────────────────

def test_coin_features_no_coin():
    f = extract_coin_features(
        coin=None, market=None,
        min_liquidity_usd=10_000, max_slippage_bps=200,
        no_trade_after_launch_seconds=300,
    )
    assert f.coin_exists is False
    assert f.has_sufficient_liquidity is False


def test_coin_features_new_coin_lockout():
    """Coins < lockout seconds should set is_new_coin=True."""
    f = extract_coin_features(
        coin=_coin(age_minutes=1.0),
        market=_market(),
        min_liquidity_usd=10_000,
        max_slippage_bps=200,
        no_trade_after_launch_seconds=300,  # 5 minutes
    )
    assert f.is_new_coin is True


def test_coin_features_mature_coin():
    f = extract_coin_features(
        coin=_coin(age_minutes=60.0),
        market=_market(liquidity=50_000),
        min_liquidity_usd=10_000,
        max_slippage_bps=200,
        no_trade_after_launch_seconds=300,
    )
    assert f.is_new_coin is False
    assert f.has_sufficient_liquidity is True
    assert f.coin_exists is True


def test_coin_features_low_liquidity():
    f = extract_coin_features(
        coin=_coin(age_minutes=60.0),
        market=_market(liquidity=5_000),
        min_liquidity_usd=10_000,
        max_slippage_bps=200,
        no_trade_after_launch_seconds=300,
    )
    assert f.has_sufficient_liquidity is False


def test_coin_features_high_slippage():
    f = extract_coin_features(
        coin=_coin(age_minutes=60.0),
        market=_market(slippage_bps=300),
        min_liquidity_usd=10_000,
        max_slippage_bps=200,
        no_trade_after_launch_seconds=300,
    )
    assert f.slippage_acceptable is False


def test_coin_age_none_when_launched_at_unknown():
    coin = ZoraCoinData(contract_address="0xBBBB", symbol="NODT", launched_at=None)
    f = extract_coin_features(
        coin=coin, market=_market(),
        min_liquidity_usd=10_000, max_slippage_bps=200,
        no_trade_after_launch_seconds=300,
    )
    assert f.coin_age_minutes is None
    assert f.is_new_coin is False  # Unknown age → conservative: not locked out
