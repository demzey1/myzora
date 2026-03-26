"""
app/scoring/pipeline.py
─────────────────────────────────────────────────────────────────────────────
Orchestration layer: ties together X ingestion, Zora lookup, feature
extraction, scoring, and DB persistence.

This is the async "service" that Celery tasks call.
It owns the unit-of-work: one DB session per pipeline run.

Sequence for a single post:
  1. Upsert the MonitoredAccount / XUser metadata
  2. Upsert the Post row (skip if already processed)
  3. Save a PostMetricsSnapshot
  4. Resolve Zora coin (creator wallet lookup + coin lookup)
  5. Fetch ZoraCoinMarketState and save a CoinMarketSnapshot
  6. Build FeatureSet
  7. Score (deterministic + optional LLM)
  8. Apply signal policy → Recommendation
  9. Persist Signal row
  10. Return the Signal ID (Celery alert task picks it up)
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import (
    CoinMarketSnapshot,
    MonitoredAccount,
    Post,
    PostMetricsSnapshot,
    Signal,
    ZoraCoin,
)
from app.db.repositories import (
    CoinMarketSnapshotRepository,
    CreatorRepository,
    MonitoredAccountRepository,
    PostMetricsSnapshotRepository,
    PostRepository,
    SignalRepository,
    ZoraCoinRepository,
)
from app.integrations.llm_client import get_llm_client
from app.integrations.types import XTweet, XUser
from app.integrations.zora_client import ZoraAdapterProtocol, get_zora_adapter
from app.logging_config import get_logger
from app.scoring.engine import get_scoring_engine
from app.scoring.features import build_feature_set
from app.scoring.policy import apply_signal_policy

log = get_logger(__name__)


async def run_pipeline_for_tweet(
    session: AsyncSession,
    tweet: XTweet,
    user: XUser,
    zora: ZoraAdapterProtocol | None = None,
    kill_switch: bool = False,
    paper_trading: bool | None = None,
    live_trading: bool | None = None,
) -> int | None:
    """
    Full pipeline for a single tweet.
    Returns the Signal.id if a signal was created, else None.
    """
    if zora is None:
        zora = get_zora_adapter()

    # ── 1. Upsert MonitoredAccount ─────────────────────────────────────────────
    account_repo = MonitoredAccountRepository(session)
    account = await account_repo.get_by_x_user_id(user.id)
    if account is None:
        account = MonitoredAccount(
            x_user_id=user.id,
            x_username=user.username,
            display_name=user.name,
            follower_count=user.public_metrics.followers_count,
        )
        await account_repo.add(account)
        log.info("account_upserted", x_username=user.username)
    else:
        account.follower_count = user.public_metrics.followers_count
        account.display_name = user.name
        account.last_fetched_at = datetime.now(timezone.utc)
        await account_repo.save(account)

    # ── 2. Upsert Post ─────────────────────────────────────────────────────────
    post_repo = PostRepository(session)
    post = await post_repo.get_by_x_post_id(tweet.id)
    if post is None:
        post = Post(
            x_post_id=tweet.id,
            account_id=account.id,
            text=tweet.text,
            posted_at=tweet.created_at,
            lang=tweet.lang,
            like_count=tweet.public_metrics.like_count,
            repost_count=tweet.public_metrics.retweet_count,
            reply_count=tweet.public_metrics.reply_count,
            quote_count=tweet.public_metrics.quote_count,
            view_count=tweet.public_metrics.impression_count,
        )
        await post_repo.add(post)
        log.info("post_ingested", x_post_id=tweet.id, account=user.username)
    elif post.is_processed:
        log.debug("post_already_processed", x_post_id=tweet.id)
        return None
    else:
        # Update metrics on re-visit
        post.like_count = tweet.public_metrics.like_count
        post.repost_count = tweet.public_metrics.retweet_count
        post.reply_count = tweet.public_metrics.reply_count
        post.quote_count = tweet.public_metrics.quote_count
        await post_repo.save(post)

    # ── 3. Save metrics snapshot ───────────────────────────────────────────────
    snap_repo = PostMetricsSnapshotRepository(session)
    snap = PostMetricsSnapshot(
        post_id=post.id,
        like_count=tweet.public_metrics.like_count,
        repost_count=tweet.public_metrics.retweet_count,
        reply_count=tweet.public_metrics.reply_count,
        quote_count=tweet.public_metrics.quote_count,
        view_count=tweet.public_metrics.impression_count,
    )
    await snap_repo.add(snap)

    # Fetch previous snapshot for velocity
    prev_snap = await snap_repo.get_previous_for_velocity(post.id, snap.id)
    prev_metrics: dict | None = None
    if prev_snap:
        prev_metrics = {
            "likes": prev_snap.like_count,
            "retweets": prev_snap.repost_count,
            "captured_at": prev_snap.captured_at,
        }

    # ── 4. Resolve Zora coin ───────────────────────────────────────────────────
    coin_repo = ZoraCoinRepository(session)
    creator_repo = CreatorRepository(session)
    zora_coin_data = None
    zora_market = None
    db_coin: ZoraCoin | None = None

    # Try resolving creator by X username → wallet → coins
    creator = await creator_repo.get_by_x_username(user.username)
    if creator is None:
        zora_profile = await zora.resolve_creator_by_x_username(user.username)
        if zora_profile and zora_profile.wallet_address:
            creator = await creator_repo.get_by_wallet(zora_profile.wallet_address)
            if creator is None:
                from app.db.models import Creator
                creator = Creator(
                    wallet_address=zora_profile.wallet_address,
                    display_name=zora_profile.display_name,
                    x_username=user.username,
                )
                await creator_repo.add(creator)
                log.info("creator_created", wallet=zora_profile.wallet_address)

    if creator:
        # Get all coins for this creator, pick the most recent active one
        coins = await zora.get_coins_for_creator(creator.wallet_address)
        if coins:
            zora_coin_data = coins[0]  # Most recent first (adapter responsibility)
            db_coin = await coin_repo.get_by_address(zora_coin_data.contract_address)
            if db_coin is None:
                db_coin = ZoraCoin(
                    contract_address=zora_coin_data.contract_address,
                    symbol=zora_coin_data.symbol,
                    name=zora_coin_data.name,
                    creator_id=creator.id,
                    launched_at=zora_coin_data.launched_at,
                )
                await coin_repo.add(db_coin)
                log.info("coin_created", symbol=zora_coin_data.symbol)

    # ── 5. Fetch market state + save snapshot ──────────────────────────────────
    market_snap_repo = CoinMarketSnapshotRepository(session)
    if db_coin and zora_coin_data:
        zora_market = await zora.get_coin_market_state(zora_coin_data.contract_address)
        if zora_market:
            msnap = CoinMarketSnapshot(
                coin_id=db_coin.id,
                price_usd=zora_market.price_usd,
                liquidity_usd=zora_market.liquidity_usd,
                volume_5m_usd=zora_market.volume_5m_usd,
                volume_1h_usd=zora_market.volume_1h_usd,
                volume_24h_usd=zora_market.volume_24h_usd,
                market_cap_usd=zora_market.market_cap_usd,
                holder_count=zora_market.holder_count,
                slippage_bps_reference=zora_market.slippage_bps_for_reference_trade,
            )
            await market_snap_repo.add(msnap)
        # Link post to coin
        post.zora_coin_id = db_coin.id

    # ── 6. Build feature set ───────────────────────────────────────────────────
    feature_set = build_feature_set(
        tweet=tweet,
        user=user,
        coin=zora_coin_data,
        market=zora_market,
        prev_metrics=prev_metrics,
        min_liquidity_usd=settings.min_liquidity_usd,
        max_slippage_bps=settings.max_slippage_bps,
        no_trade_after_launch_seconds=settings.no_trade_after_launch_seconds,
    )

    # ── 7. Score ──────────────────────────────────────────────────────────────
    engine = get_scoring_engine()
    llm_client = get_llm_client()
    llm_result = None

    if settings.llm_enabled and zora_coin_data:
        try:
            llm_result = await llm_client.classify_post(
                post_text=tweet.text,
                coin_symbol=zora_coin_data.symbol,
            )
        except Exception as exc:
            log.warning("llm_classify_failed", error=str(exc))

    score_result = engine.score(feature_set, llm_score=llm_result)

    # ── 7b. Apply per-creator score multiplier (whitelist/blacklist boost) ──────
    from app.db.repositories.overrides import CreatorOverrideRepository
    override_repo = CreatorOverrideRepository(session)
    x_uname = user.username if user else None
    coin_addr = zora_coin_data.contract_address if zora_coin_data else None
    multiplier = await override_repo.get_score_multiplier(x_uname, coin_addr)
    if multiplier != 1.0:
        adjusted = round(min(score_result.final_score * multiplier, 100.0), 1)
        log.info("score_multiplier_applied",
                 original=score_result.final_score, multiplier=multiplier, adjusted=adjusted)
        # Rebuild ScoreResult with adjusted final score (keep breakdown intact)
        from app.scoring.engine import ScoreResult as _SR
        score_result = _SR(
            deterministic_score=score_result.deterministic_score,
            llm_score=score_result.llm_score,
            final_score=adjusted,
            breakdown=score_result.breakdown,
            disqualified=score_result.disqualified,
            disqualify_reasons=score_result.disqualify_reasons,
            risk_notes=score_result.risk_notes,
        )

    # ── 8. Apply signal policy ─────────────────────────────────────────────────
    recommendation = apply_signal_policy(
        score_result,
        kill_switch_active=kill_switch,
        paper_trading_active=paper_trading,
        live_trading_active=live_trading,
    )

    # ── 9. Persist Signal ──────────────────────────────────────────────────────
    signal = Signal(
        post_id=post.id,
        coin_id=db_coin.id if db_coin else None,
        deterministic_score=score_result.deterministic_score,
        llm_score=score_result.llm_score,
        final_score=score_result.final_score,
        recommendation=recommendation,
        risk_notes=score_result.risk_notes_str,
        llm_summary=llm_result.summary if llm_result else None,
        llm_meme_strength=llm_result.meme_strength if llm_result else None,
        llm_narrative_fit=llm_result.narrative_fit if llm_result else None,
        llm_conversion_likelihood=llm_result.conversion_likelihood if llm_result else None,
        llm_spam_risk=llm_result.spam_risk if llm_result else None,
        llm_recommendation_bias=llm_result.recommendation_bias if llm_result else None,
    )
    signal_repo = SignalRepository(session)
    await signal_repo.add(signal)

    # Mark post as processed
    post.is_processed = True
    await post_repo.save(post)

    log.info(
        "signal_created",
        signal_id=signal.id,
        score=score_result.final_score,
        recommendation=recommendation.value,
        x_post_id=tweet.id,
        disqualified=score_result.disqualified,
    )

    return signal.id
