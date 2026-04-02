"""
app/jobs/tasks/creator_tasks.py
─────────────────────────────────────────────────────────────────────────────
Celery tasks for the creator intent tracking pipeline.

Task graph:
  beat → poll_all_tracked_creators
           └─ for each active creator → poll_creator_posts
                └─ for each new post  → classify_and_discover
                     └─ if actionable → send_creator_signal_alert
"""

from __future__ import annotations

import asyncio
import json

from app.jobs.celery_app import celery_app
from app.logging_config import get_logger

log = get_logger(__name__)


# ── Top-level polling task ─────────────────────────────────────────────────────

@celery_app.task(
    name="app.jobs.tasks.creator_tasks.poll_all_tracked_creators",
    bind=True, max_retries=2, default_retry_delay=30,
)
def poll_all_tracked_creators(self) -> dict:  # type: ignore[no-untyped-def]
    return asyncio.run(_async_poll_all())


async def _async_poll_all() -> dict:
    from app.db.base import AsyncSessionLocal
    from app.db.repositories.creator_tracking import TrackedCreatorRepository

    async with AsyncSessionLocal() as session:
        repo = TrackedCreatorRepository(session)
        creators = await repo.get_all_active()

    log.info("creator_poll_start", count=len(creators))
    for creator in creators:
        poll_creator_posts.apply_async(
            kwargs={"tracked_creator_id": creator.id},
            queue="signals",
        )
    return {"dispatched": len(creators)}


# ── Per-creator polling ────────────────────────────────────────────────────────

@celery_app.task(
    name="app.jobs.tasks.creator_tasks.poll_creator_posts",
    bind=True, max_retries=3, default_retry_delay=20,
)
def poll_creator_posts(self, tracked_creator_id: int) -> dict:  # type: ignore[no-untyped-def]
    return asyncio.run(_async_poll_creator(tracked_creator_id))


async def _async_poll_creator(tracked_creator_id: int) -> dict:
    from datetime import datetime, timezone

    from app.db.base import AsyncSessionLocal
    from app.db.models import CreatorPost, TrackedCreator
    from app.db.repositories.creator_tracking import (
        CreatorPostRepository,
        TrackedCreatorRepository,
    )
    from app.integrations.social_provider import (
        SocialProviderError,
        get_social_provider,
    )
    from app.integrations.types import XUser, XUserPublicMetrics

    try:
        provider = get_social_provider()
    except SocialProviderError as exc:
        log.error("no_social_provider", error=str(exc))
        return {"error": str(exc)}

    async with AsyncSessionLocal() as session:
        repo = TrackedCreatorRepository(session)
        creator = await repo.get(tracked_creator_id)
        if creator is None or not creator.is_active:
            return {"skipped": True}

        # Build a minimal XUser from stored data
        user = XUser(
            id=creator.x_user_id,
            name=creator.display_name or creator.x_username,
            username=creator.x_username,
            public_metrics=XUserPublicMetrics(
                followers_count=creator.follower_count or 0
            ),
        )

        try:
            posts = await provider.get_recent_posts(
                user, limit=10, since_id=creator.last_post_id
            )
        except SocialProviderError as exc:
            log.warning("creator_poll_failed",
                        username=creator.x_username, error=str(exc))
            return {"error": str(exc)}

        if not posts:
            return {"new_posts": 0}

        post_repo = CreatorPostRepository(session)
        new_count = 0
        newest_id = creator.last_post_id

        for tweet in posts:
            existing = await post_repo.get_by_x_post_id(tweet.id)
            if existing:
                continue

            db_post = CreatorPost(
                tracked_creator_id=tracked_creator_id,
                x_post_id=tweet.id,
                text=tweet.text,
                posted_at=tweet.created_at,
                lang=tweet.lang,
                like_count=tweet.public_metrics.like_count,
                retweet_count=tweet.public_metrics.retweet_count,
                reply_count=tweet.public_metrics.reply_count,
                quote_count=tweet.public_metrics.quote_count,
            )
            session.add(db_post)
            await session.flush()
            new_count += 1

            # Track the newest post ID for next poll
            if newest_id is None or tweet.id > newest_id:
                newest_id = tweet.id

            # Dispatch classification task
            classify_and_discover.apply_async(
                kwargs={
                    "creator_post_id": db_post.id,
                    "tracked_creator_id": tracked_creator_id,
                },
                queue="signals",
            )

        # Update last_post_id and last_fetched_at
        creator.last_post_id = newest_id
        creator.last_fetched_at = datetime.now(timezone.utc)
        # Also refresh follower count if available
        try:
            fresh_user = await provider.get_user_by_id(creator.x_user_id)
            if fresh_user:
                creator.follower_count = fresh_user.public_metrics.followers_count
        except Exception:
            pass

        await session.commit()

    log.info("creator_polled", creator_id=tracked_creator_id, new_posts=new_count)
    return {"new_posts": new_count}


# ── Classification + discovery ─────────────────────────────────────────────────

@celery_app.task(
    name="app.jobs.tasks.creator_tasks.classify_and_discover",
    bind=True, max_retries=2, default_retry_delay=15,
)
def classify_and_discover(
    self,  # type: ignore[no-untyped-def]
    creator_post_id: int,
    tracked_creator_id: int,
) -> dict:
    return asyncio.run(_async_classify(creator_post_id, tracked_creator_id))


async def _async_classify(creator_post_id: int, tracked_creator_id: int) -> dict:
    from app.classification.classifier import classify_post
    from app.db.base import AsyncSessionLocal
    from app.db.models import (
        CreatorPostClassification,
        CreatorSignalCandidate,
        PostSentiment,
    )
    from app.db.repositories.creator_tracking import (
        CreatorPostRepository,
        TrackedCreatorRepository,
    )
    from app.integrations.zora_discovery import ZoraDiscoveryService

    async with AsyncSessionLocal() as session:
        post_repo = TrackedCreatorRepository(session)
        creator = await post_repo.get(tracked_creator_id)

        post_q = CreatorPostRepository(session)
        post = await post_q.get(creator_post_id)
        if post is None or post.is_classified:
            return {"skipped": True}

        # ── 1. Classify ──────────────────────────────────────────────────
        result = await classify_post(
            text=post.text or "",
            follower_count=creator.follower_count or 0 if creator else 0,
            like_count=post.like_count,
            retweet_count=post.retweet_count,
        )

        clf = CreatorPostClassification(
            post_id=post.id,
            actionable=result.actionable,
            sentiment=result.sentiment,
            confidence=result.confidence,
            conviction_score=result.conviction_score,
            entities_json=json.dumps(result.entities),
            keywords_json=json.dumps(result.keywords),
            narratives_json=json.dumps(result.narratives),
            summary=result.summary,
            used_llm=result.used_llm,
        )
        session.add(clf)
        post.is_classified = True

        if not result.actionable or result.sentiment != PostSentiment.BULLISH:
            await session.commit()
            return {"actionable": False, "sentiment": result.sentiment.value}

        # ── 2. Zora discovery ─────────────────────────────────────────────
        mode = creator.mode.value if creator else "hybrid"
        discovery = ZoraDiscoveryService()
        discovery_result = await discovery.discover(
            x_username=creator.x_username if creator else "",
            creator_wallet=creator.zora_wallet_address if creator else None,
            keywords=result.keywords,
            entities=result.entities,
            cashtags=[k for k in result.keywords if k.isupper() and len(k) <= 8],
            mode=mode,
        )

        # ── 3. Persist candidates ─────────────────────────────────────────
        candidate_ids = []
        for c in discovery_result.ranked()[:5]:  # top 5
            db_c = CreatorSignalCandidate(
                post_id=post.id,
                contract_address=c.coin.contract_address,
                symbol=c.coin.symbol,
                match_type=c.match_type,
                relevance_score=c.relevance_score,
                creator_linkage_score=c.creator_linkage_score,
                momentum_score=c.momentum_score,
                liquidity_score=c.liquidity_score,
                final_rank_score=c.final_score,
                price_usd=c.market.price_usd if c.market else None,
                liquidity_usd=c.market.liquidity_usd if c.market else None,
                slippage_bps=c.market.slippage_bps_for_reference_trade if c.market else None,
                volume_5m_usd=c.market.volume_5m_usd if c.market else None,
                risk_flags="|".join(c.risk_flags) if c.risk_flags else None,
            )
            session.add(db_c)
            await session.flush()
            candidate_ids.append(db_c.id)

        await session.commit()

    # ── 4. Alert ──────────────────────────────────────────────────────────
    if candidate_ids:
        send_creator_signal_alert.apply_async(
            kwargs={
                "creator_post_id": creator_post_id,
                "tracked_creator_id": tracked_creator_id,
            },
            queue="alerts",
        )

    return {"actionable": True, "candidates": len(candidate_ids)}


# ── Alert dispatch ─────────────────────────────────────────────────────────────

@celery_app.task(
    name="app.jobs.tasks.creator_tasks.send_creator_signal_alert",
    bind=True, max_retries=3, default_retry_delay=10,
)
def send_creator_signal_alert(
    self,  # type: ignore[no-untyped-def]
    creator_post_id: int,
    tracked_creator_id: int,
) -> dict:
    return asyncio.run(_async_send_creator_alert(creator_post_id, tracked_creator_id))


async def _async_send_creator_alert(
    creator_post_id: int, tracked_creator_id: int
) -> dict:
    from app.bot.application import get_application
    from app.bot.renderer import format_creator_signal_alert, creator_signal_keyboard
    from app.db.base import AsyncSessionLocal
    from app.db.repositories.creator_tracking import (
        CreatorPostRepository,
        CreatorSignalCandidateRepository,
        TrackedCreatorRepository,
    )

    async with AsyncSessionLocal() as session:
        creator = await TrackedCreatorRepository(session).get(tracked_creator_id)
        post = await CreatorPostRepository(session).get(creator_post_id)
        if not creator or not post:
            return {"error": "missing_data"}

        clf = post.classification
        candidates = await CreatorSignalCandidateRepository(session).get_for_post(post.id)

    if not clf or not candidates:
        return {"skipped": True}

    msg = format_creator_signal_alert(
        creator=creator,
        post=post,
        classification=clf,
        candidates=candidates[:3],
    )
    keyboard = creator_signal_keyboard(creator_post_id=creator_post_id)

    tg_app = get_application()
    from app.config import settings

    sent = 0
    for admin_id in settings.admin_user_ids:
        # Also notify the specific user who added this creator
        target_ids = {admin_id, creator.telegram_user_id}
        for tid in target_ids:
            try:
                await tg_app.bot.send_message(
                    chat_id=tid,
                    text=msg,
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )
                sent += 1
            except Exception as exc:
                log.warning("creator_alert_send_failed", tid=tid, error=str(exc))

    log.info("creator_alert_sent", post_id=creator_post_id, sent_to=sent)
    return {"sent_to": sent}

