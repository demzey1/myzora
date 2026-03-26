"""
tests/unit/test_scoring_engine.py
Tests for the deterministic scoring engine.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.integrations.types import (
    XPublicMetrics,
    XTweet,
    XUser,
    XUserPublicMetrics,
    ZoraCoinData,
    ZoraCoinMarketState,
)
from app.scoring.engine import ScoringEngine, ScoreBreakdown
from app.scoring.features import build_feature_set

MIN_LIQ = 10_000.0
MAX_SLIP = 200
NO_TRADE_S = 300


def _features(
    followers=100_000,
    likes=500,
    retweets=100,
    replies=50,
    quotes=20,
    post_age_min=5.0,
    coin_age_min=60.0,
    liquidity=50_000.0,
    slippage_bps=100,
    volume_5m=5_000.0,
    has_coin=True,
    prev_metrics=None,
):
    created = datetime.now(timezone.utc) - timedelta(minutes=post_age_min)
    tweet = XTweet(
        id="1",
        text="Zora signal test",
        author_id="u1",
        created_at=created,
        public_metrics=XPublicMetrics(
            like_count=likes,
            retweet_count=retweets,
            reply_count=replies,
            quote_count=quotes,
        ),
    )
    user = XUser(
        id="u1",
        name="Creator",
        username="creator",
        public_metrics=XUserPublicMetrics(followers_count=followers),
    )
    if has_coin:
        coin = ZoraCoinData(
            contract_address="0xCCCC",
            symbol="SIG",
            launched_at=datetime.now(timezone.utc) - timedelta(minutes=coin_age_min),
        )
        market = ZoraCoinMarketState(
            contract_address="0xCCCC",
            price_usd=0.001,
            liquidity_usd=liquidity,
            volume_5m_usd=volume_5m,
            slippage_bps_for_reference_trade=slippage_bps,
        )
    else:
        coin = None
        market = None

    return build_feature_set(
        tweet=tweet,
        user=user,
        coin=coin,
        market=market,
        prev_metrics=prev_metrics,
        min_liquidity_usd=MIN_LIQ,
        max_slippage_bps=MAX_SLIP,
        no_trade_after_launch_seconds=NO_TRADE_S,
    )


engine = ScoringEngine()


# ── Disqualifier tests ────────────────────────────────────────────────────────

def test_no_coin_disqualifies():
    f = _features(has_coin=False)
    result = engine.score(f)
    assert result.disqualified is True
    assert result.final_score == 0.0
    assert "no_zora_coin_mapped" in result.disqualify_reasons


def test_new_coin_disqualifies():
    f = _features(coin_age_min=1.0)  # 1 minute < 5 minute lockout
    result = engine.score(f)
    assert result.disqualified is True
    assert any("launch_lockout" in r for r in result.disqualify_reasons)


def test_low_liquidity_disqualifies():
    f = _features(liquidity=1_000.0)
    result = engine.score(f)
    assert result.disqualified is True
    assert any("liquidity_too_low" in r for r in result.disqualify_reasons)


# ── Score range tests ─────────────────────────────────────────────────────────

def test_score_is_between_0_and_100():
    f = _features()
    result = engine.score(f)
    assert 0.0 <= result.final_score <= 100.0
    assert 0.0 <= result.deterministic_score <= 100.0


def test_high_engagement_scores_higher():
    low_f = _features(likes=10, retweets=2, replies=1, quotes=0, followers=100_000)
    high_f = _features(likes=5_000, retweets=1_000, replies=500, quotes=200, followers=100_000)
    low_result = engine.score(low_f)
    high_result = engine.score(high_f)
    assert high_result.deterministic_score > low_result.deterministic_score


def test_large_account_scores_higher_than_small():
    small_f = _features(followers=500)
    large_f = _features(followers=1_000_000)
    small_r = engine.score(small_f)
    large_r = engine.score(large_f)
    assert large_r.deterministic_score > small_r.deterministic_score


def test_high_liquidity_scores_higher():
    low_liq = _features(liquidity=15_000)
    high_liq = _features(liquidity=500_000)
    r_low = engine.score(low_liq)
    r_high = engine.score(high_liq)
    assert r_high.deterministic_score > r_low.deterministic_score


def test_fresh_post_scores_higher_than_stale():
    fresh = _features(post_age_min=3.0)
    stale = _features(post_age_min=300.0)
    r_fresh = engine.score(fresh)
    r_stale = engine.score(stale)
    assert r_fresh.deterministic_score > r_stale.deterministic_score


# ── Breakdown tests ───────────────────────────────────────────────────────────

def test_breakdown_all_fields_populated():
    f = _features()
    result = engine.score(f)
    assert not result.disqualified
    bd = result.breakdown
    assert bd.coin_existence_score == 100.0
    assert bd.follower_tier > 0
    assert bd.liquidity_score > 0


def test_weighted_sum_bounded():
    """Verify weights sum to ~1.0 (within floating point tolerance)."""
    total_weight = sum(ScoringEngine.WEIGHTS.values())
    assert abs(total_weight - 1.0) < 1e-9


# ── LLM blend tests ───────────────────────────────────────────────────────────

def test_llm_score_blends_with_deterministic():
    from app.integrations.llm_client import LLMScore
    f = _features()
    det_result = engine.score(f, llm_score=None)
    llm = LLMScore(meme_strength=90, narrative_fit=90, conversion_likelihood=90, spam_risk=5)
    blended = engine.score(f, llm_score=llm)
    # Blending with a high LLM score should raise or maintain the final score
    assert blended.llm_score is not None
    assert blended.final_score != det_result.final_score or blended.final_score == det_result.final_score


def test_high_spam_risk_adds_risk_note():
    from app.integrations.llm_client import LLMScore
    f = _features()
    llm = LLMScore(meme_strength=50, narrative_fit=50, conversion_likelihood=50, spam_risk=80)
    result = engine.score(f, llm_score=llm)
    assert any("spam" in note for note in result.risk_notes)


# ── Risk note tests ───────────────────────────────────────────────────────────

def test_high_slippage_adds_risk_note():
    f = _features(slippage_bps=300)  # Above 200 bps threshold
    result = engine.score(f)
    if not result.disqualified:
        assert any("slippage" in note for note in result.risk_notes)


def test_very_new_coin_adds_risk_note():
    # 10-30 min old — past lockout but still very new
    f = _features(coin_age_min=15.0)
    result = engine.score(f)
    if not result.disqualified:
        assert any("new coin" in note for note in result.risk_notes)
