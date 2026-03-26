"""
app/scoring/features.py
─────────────────────────────────────────────────────────────────────────────
Pure functions that extract numeric features from typed domain objects.
No database access, no I/O — takes domain objects, returns a FeatureSet.

Every feature is documented: what it measures, its range, and how it's used.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import NamedTuple

from app.integrations.types import (
    XTweet,
    XUser,
    ZoraCoinData,
    ZoraCoinMarketState,
)


class SocialFeatures(NamedTuple):
    """Features derived from the X post and account."""
    # ── Account features ──────────────────────────────────────────────────────
    follower_count: int               # Raw follower count
    follower_bucket: int              # 0-4: <1k | 1-10k | 10-100k | 100-500k | 500k+
    is_verified: bool
    # ── Post engagement ───────────────────────────────────────────────────────
    likes: int
    retweets: int
    replies: int
    quotes: int
    total_engagement: int             # sum of the four above
    engagement_rate: float            # total_engagement / max(follower_count, 1)
    # ── Velocity (requires ≥2 metric snapshots; None if only one snapshot) ───
    likes_velocity_per_min: float | None   # likes gained per minute
    rt_velocity_per_min: float | None
    # ── Post age ──────────────────────────────────────────────────────────────
    post_age_minutes: float | None    # minutes since post was created


class CoinFeatures(NamedTuple):
    """Features derived from Zora coin data and market state."""
    coin_exists: bool
    coin_age_minutes: float | None    # None if coin or launch time unknown
    liquidity_usd: float | None
    price_usd: float | None
    volume_5m_usd: float | None
    volume_1h_usd: float | None
    slippage_bps: int | None
    holder_count: int | None
    market_cap_usd: float | None
    # Derived
    is_new_coin: bool                 # < 10 minutes old — too risky to trade
    has_sufficient_liquidity: bool    # ≥ settings.min_liquidity_usd
    slippage_acceptable: bool         # ≤ settings.max_slippage_bps


class ContextFeatures(NamedTuple):
    """
    Contextual features that aren't directly from the post or coin.
    """
    hour_of_day_utc: int              # 0-23 (crypto is most active 14-22 UTC)
    day_of_week: int                  # 0=Mon … 6=Sun
    # TODO (Phase 3+): market regime, narrative keyword overlap


class FeatureSet(NamedTuple):
    """Complete feature set passed to the scoring engine."""
    social: SocialFeatures
    coin: CoinFeatures
    context: ContextFeatures


# ── Extractors ─────────────────────────────────────────────────────────────────

def extract_social_features(
    tweet: XTweet,
    user: XUser,
    prev_metrics_snapshot: dict | None = None,  # {"likes": int, "captured_at": datetime}
) -> SocialFeatures:
    """
    Compute social features from a tweet and its author.
    prev_metrics_snapshot enables velocity calculation.
    """
    pm = tweet.public_metrics
    likes = pm.like_count
    retweets = pm.retweet_count
    replies = pm.reply_count
    quotes = pm.quote_count
    total = likes + retweets + replies + quotes

    follower_count = user.public_metrics.followers_count
    engagement_rate = total / max(follower_count, 1)

    # Follower bucket: 0=<1k, 1=1-10k, 2=10-100k, 3=100-500k, 4=500k+
    if follower_count < 1_000:
        bucket = 0
    elif follower_count < 10_000:
        bucket = 1
    elif follower_count < 100_000:
        bucket = 2
    elif follower_count < 500_000:
        bucket = 3
    else:
        bucket = 4

    # Velocity — requires a previous snapshot
    likes_vel: float | None = None
    rt_vel: float | None = None
    if prev_metrics_snapshot:
        prev_likes = prev_metrics_snapshot.get("likes", 0)
        prev_rts = prev_metrics_snapshot.get("retweets", 0)
        prev_time: datetime | None = prev_metrics_snapshot.get("captured_at")
        if prev_time:
            now = datetime.now(timezone.utc)
            # Normalise timezone
            if prev_time.tzinfo is None:
                prev_time = prev_time.replace(tzinfo=timezone.utc)
            elapsed_minutes = max((now - prev_time).total_seconds() / 60, 0.01)
            likes_vel = max(likes - prev_likes, 0) / elapsed_minutes
            rt_vel = max(retweets - prev_rts, 0) / elapsed_minutes

    # Post age
    post_age: float | None = None
    if tweet.created_at:
        created = tweet.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        post_age = (datetime.now(timezone.utc) - created).total_seconds() / 60

    return SocialFeatures(
        follower_count=follower_count,
        follower_bucket=bucket,
        is_verified=user.verified,
        likes=likes,
        retweets=retweets,
        replies=replies,
        quotes=quotes,
        total_engagement=total,
        engagement_rate=engagement_rate,
        likes_velocity_per_min=likes_vel,
        rt_velocity_per_min=rt_vel,
        post_age_minutes=post_age,
    )


def extract_coin_features(
    coin: ZoraCoinData | None,
    market: ZoraCoinMarketState | None,
    min_liquidity_usd: float,
    max_slippage_bps: int,
    no_trade_after_launch_seconds: int,
) -> CoinFeatures:
    """Compute coin features from coin metadata and current market state."""
    if coin is None:
        return CoinFeatures(
            coin_exists=False,
            coin_age_minutes=None,
            liquidity_usd=None,
            price_usd=None,
            volume_5m_usd=None,
            volume_1h_usd=None,
            slippage_bps=None,
            holder_count=None,
            market_cap_usd=None,
            is_new_coin=False,
            has_sufficient_liquidity=False,
            slippage_acceptable=False,
        )

    # Coin age
    coin_age_minutes: float | None = None
    is_new = False
    if coin.launched_at:
        launched = coin.launched_at
        if launched.tzinfo is None:
            launched = launched.replace(tzinfo=timezone.utc)
        age_seconds = (datetime.now(timezone.utc) - launched).total_seconds()
        coin_age_minutes = age_seconds / 60
        is_new = age_seconds < no_trade_after_launch_seconds

    liquidity = market.liquidity_usd if market else None
    slippage = market.slippage_bps_for_reference_trade if market else None

    return CoinFeatures(
        coin_exists=True,
        coin_age_minutes=coin_age_minutes,
        liquidity_usd=liquidity,
        price_usd=market.price_usd if market else None,
        volume_5m_usd=market.volume_5m_usd if market else None,
        volume_1h_usd=market.volume_1h_usd if market else None,
        slippage_bps=slippage,
        holder_count=market.holder_count if market else None,
        market_cap_usd=market.market_cap_usd if market else None,
        is_new_coin=is_new,
        has_sufficient_liquidity=(liquidity or 0) >= min_liquidity_usd,
        slippage_acceptable=(slippage is None) or (slippage <= max_slippage_bps),
    )


def extract_context_features() -> ContextFeatures:
    """Extract context features from the current timestamp."""
    now = datetime.now(timezone.utc)
    return ContextFeatures(
        hour_of_day_utc=now.hour,
        day_of_week=now.weekday(),
    )


def build_feature_set(
    tweet: XTweet,
    user: XUser,
    coin: ZoraCoinData | None,
    market: ZoraCoinMarketState | None,
    prev_metrics: dict | None,
    min_liquidity_usd: float,
    max_slippage_bps: int,
    no_trade_after_launch_seconds: int,
) -> FeatureSet:
    """Convenience function to build a full FeatureSet in one call."""
    return FeatureSet(
        social=extract_social_features(tweet, user, prev_metrics),
        coin=extract_coin_features(
            coin, market, min_liquidity_usd, max_slippage_bps, no_trade_after_launch_seconds
        ),
        context=extract_context_features(),
    )
