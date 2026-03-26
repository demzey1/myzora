"""
app/jobs/tasks/scoring.py
─────────────────────────────────────────────────────────────────────────────
Celery task that fetches a full tweet + user from X, runs the scoring
pipeline, and enqueues an alert task if the recommendation warrants it.
"""

from __future__ import annotations

import asyncio

from app.jobs.celery_app import celery_app
from app.logging_config import get_logger

log = get_logger(__name__)


@celery_app.task(
    name="app.jobs.tasks.scoring.score_and_persist_tweet",
    bind=True,
    max_retries=2,
    default_retry_delay=15,
)
def score_and_persist_tweet(
    self,  # type: ignore[no-untyped-def]
    x_post_id: str,
    x_user_id: str,
) -> dict:
    """
    1. Fetch full tweet + user from X API
    2. Run the full pipeline (feature extraction → scoring → persist)
    3. If recommendation >= ALERT, enqueue send_signal_alert
    """
    return asyncio.run(_async_score(x_post_id, x_user_id))


async def _async_score(x_post_id: str, x_user_id: str) -> dict:
    from app.db.base import AsyncSessionLocal
    from app.db.models import Recommendation
    from app.integrations.x_client import get_x_client
    from app.jobs.tasks.alerts import send_signal_alert
    from app.scoring.pipeline import run_pipeline_for_tweet

    x = get_x_client()

    tweet = await x.get_tweet_by_id(x_post_id)
    if tweet is None:
        log.warning("tweet_not_found_for_scoring", x_post_id=x_post_id)
        return {"status": "tweet_not_found"}

    user = await x.get_user_by_id(x_user_id)
    if user is None:
        log.warning("user_not_found_for_scoring", x_user_id=x_user_id)
        return {"status": "user_not_found"}

    async with AsyncSessionLocal() as session:
        signal_id = await run_pipeline_for_tweet(
            session=session,
            tweet=tweet,
            user=user,
        )
        await session.commit()

    if signal_id is None:
        return {"status": "skipped"}

    # Fetch the signal recommendation to decide whether to alert
    async with AsyncSessionLocal() as session:
        from app.db.repositories import SignalRepository
        sig = await SignalRepository(session).get(signal_id)
        if sig and sig.recommendation not in (Recommendation.IGNORE, Recommendation.WATCH):
            send_signal_alert.apply_async(
                kwargs={"signal_id": signal_id},
                queue="alerts",
            )

    return {"status": "ok", "signal_id": signal_id}
