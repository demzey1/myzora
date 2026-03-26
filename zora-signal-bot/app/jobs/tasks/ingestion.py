"""
app/jobs/tasks/ingestion.py
─────────────────────────────────────────────────────────────────────────────
Celery tasks for X/Twitter post ingestion.

Task flow:
  beat scheduler
    └─ poll_monitored_accounts (every X_POLL_INTERVAL_SECONDS)
         └─ for each active account → fetch timeline via X API
              └─ for each new tweet → score_and_persist_tweet.delay(tweet_id, user_id)
                   └─ scoring task runs the full pipeline + enqueues alert
"""

from __future__ import annotations

import asyncio

from app.jobs.celery_app import celery_app
from app.logging_config import get_logger

log = get_logger(__name__)


# ── Heartbeat ─────────────────────────────────────────────────────────────────

@celery_app.task(name="app.jobs.tasks.ingestion.heartbeat", bind=True)
def heartbeat(self) -> dict:  # type: ignore[no-untyped-def]
    """Periodic liveness marker — confirms the worker is alive."""
    log.info("worker_heartbeat", task_id=self.request.id)
    return {"status": "alive"}


# ── Main polling task ─────────────────────────────────────────────────────────

@celery_app.task(
    name="app.jobs.tasks.ingestion.poll_monitored_accounts",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def poll_monitored_accounts(self) -> dict:  # type: ignore[no-untyped-def]
    """
    Fetch recent tweets for all active monitored accounts and enqueue
    scoring for any new posts.

    Runs synchronously (Celery is sync by default) — wraps async via
    asyncio.run() in a dedicated event loop.
    """
    return asyncio.run(_async_poll_accounts())


async def _async_poll_accounts() -> dict:
    from app.db.base import AsyncSessionLocal
    from app.db.repositories import MonitoredAccountRepository
    from app.integrations.x_client import get_x_client, XAPIError, XRateLimitError
    from app.jobs.tasks.scoring import score_and_persist_tweet

    try:
        x = get_x_client()
    except RuntimeError as exc:
        log.error("x_client_unavailable", error=str(exc))
        return {"error": str(exc)}

    async with AsyncSessionLocal() as session:
        account_repo = MonitoredAccountRepository(session)
        accounts = await account_repo.get_active_accounts()

    log.info("polling_accounts", count=len(accounts))
    enqueued = 0

    for account in accounts:
        try:
            tweets = await x.get_user_recent_tweets(
                user_id=account.x_user_id,
                max_results=10,
                since_id=_get_last_seen_tweet_id(account.x_user_id),
            )
        except XRateLimitError:
            log.warning("x_rate_limit_hit", account=account.x_username)
            break  # Back off; let the next beat cycle retry
        except XAPIError as exc:
            log.warning("x_api_error", account=account.x_username, error=str(exc))
            continue

        for tweet in tweets:
            score_and_persist_tweet.apply_async(
                kwargs={"x_post_id": tweet.id, "x_user_id": account.x_user_id},
                queue="signals",
            )
            _set_last_seen_tweet_id(account.x_user_id, tweet.id)
            enqueued += 1

    log.info("poll_complete", enqueued=enqueued)
    return {"enqueued": enqueued}


# ── Last-seen tweet ID cache (Redis-backed) ────────────────────────────────────

def _redis_key(x_user_id: str) -> str:
    return f"zsb:last_tweet:{x_user_id}"


def _get_last_seen_tweet_id(x_user_id: str) -> str | None:
    try:
        import redis
        from app.config import settings
        r = redis.from_url(settings.redis_url, decode_responses=True)
        return r.get(_redis_key(x_user_id))
    except Exception as exc:
        log.debug("redis_get_failed", error=str(exc))
        return None


def _set_last_seen_tweet_id(x_user_id: str, tweet_id: str) -> None:
    try:
        import redis
        from app.config import settings
        r = redis.from_url(settings.redis_url, decode_responses=True)
        # Keep for 7 days — matches X search recent window
        r.setex(_redis_key(x_user_id), 604_800, tweet_id)
    except Exception as exc:
        log.debug("redis_set_failed", error=str(exc))


# ── Metric refresh task (Phase 4) ─────────────────────────────────────────────

@celery_app.task(
    name="app.jobs.tasks.ingestion.refresh_recent_post_metrics",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
)
def refresh_recent_post_metrics(self) -> dict:  # type: ignore[no-untyped-def]
    """
    Re-fetch engagement metrics for all posts ingested in the last hour.
    Stores a new PostMetricsSnapshot so the scoring engine can compute
    velocity on the next scoring pass.
    """
    return asyncio.run(_async_refresh_metrics())


async def _async_refresh_metrics() -> dict:
    from datetime import datetime, timedelta, timezone
    from app.db.base import AsyncSessionLocal
    from app.db.models import PostMetricsSnapshot
    from app.db.repositories.posts import PostRepository
    from app.integrations.x_client import get_x_client, XAPIError

    try:
        x = get_x_client()
    except RuntimeError as exc:
        log.warning("x_client_unavailable_for_refresh", error=str(exc))
        return {"error": str(exc)}

    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    refreshed = 0

    async with AsyncSessionLocal() as session:
        # Fetch recent posts from DB
        from sqlalchemy import select
        from app.db.models import Post
        result = await session.execute(
            select(Post)
            .where(Post.created_at >= cutoff)
            .order_by(Post.created_at.desc())
            .limit(100)
        )
        posts = list(result.scalars().all())

    for post in posts:
        try:
            metrics = await x.get_tweet_metrics(post.x_post_id)
        except XAPIError as exc:
            log.debug("metric_refresh_skip", post_id=post.id, error=str(exc))
            continue

        if metrics is None:
            continue

        async with AsyncSessionLocal() as session:
            snap = PostMetricsSnapshot(
                post_id=post.id,
                like_count=metrics.like_count,
                repost_count=metrics.retweet_count,
                reply_count=metrics.reply_count,
                quote_count=metrics.quote_count,
                view_count=metrics.impression_count,
            )
            session.add(snap)
            await session.commit()
        refreshed += 1

    log.info("metric_refresh_complete", refreshed=refreshed, total=len(posts))
    return {"refreshed": refreshed, "total": len(posts)}
