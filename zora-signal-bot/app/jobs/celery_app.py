"""
app/jobs/celery_app.py
─────────────────────────────────────────────────────────────────────────────
Celery application factory and beat schedule.

Queues:
  default  – general tasks
  signals  – post ingestion and scoring (latency-sensitive)
  alerts   – Telegram notification delivery

Beat schedule (periodic tasks) defined here; workers pick them up
automatically. Actual task implementations live in app/jobs/tasks/.
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.config import settings

celery_app = Celery(
    "zora_signal_bot",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "app.jobs.tasks.ingestion",
        "app.jobs.tasks.scoring",
        "app.jobs.tasks.alerts",
        "app.jobs.tasks.settlement",
        "app.jobs.tasks.creator_tasks",
        "app.jobs.tasks.wallet_tasks",
    ],
)

celery_app.conf.update(
    # Serialisation
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Timezone
    timezone="UTC",
    enable_utc=True,
    # Routing
    task_routes={
        "app.jobs.tasks.ingestion.*": {"queue": "signals"},
        "app.jobs.tasks.scoring.*":   {"queue": "signals"},
        "app.jobs.tasks.alerts.*":    {"queue": "alerts"},
        "app.jobs.tasks.settlement.*": {"queue": "default"},
    },
    # Reliability
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    # Result expiry
    result_expires=3600,
    beat_schedule={
        # Liveness heartbeat
        "heartbeat": {
            "task": "app.jobs.tasks.ingestion.heartbeat",
            "schedule": 60.0,
            "options": {"queue": "default"},
        },
        # Main X polling loop
        "poll-monitored-accounts": {
            "task": "app.jobs.tasks.ingestion.poll_monitored_accounts",
            "schedule": settings.x_poll_interval_seconds,
            "options": {"queue": "signals"},
        },
        # Position exit monitoring — every 30 seconds
        "monitor-open-positions": {
            "task": "app.jobs.tasks.settlement.monitor_open_positions",
            "schedule": 30.0,
            "options": {"queue": "default"},
        },
        # Daily P&L summary — midnight UTC
        "daily-summary": {
            "task": "app.jobs.tasks.alerts.send_daily_summary",
            "schedule": crontab(hour=0, minute=0),
            "options": {"queue": "alerts"},
        },
        # Refresh engagement metrics for recent posts (velocity tracking)
        "refresh-recent-metrics": {
            "task": "app.jobs.tasks.ingestion.refresh_recent_post_metrics",
            "schedule": 120.0,
            "options": {"queue": "signals"},
        },
        # Creator intent tracking — poll all watched creators
        "poll-tracked-creators": {
            "task": "app.jobs.tasks.creator_tasks.poll_all_tracked_creators",
            "schedule": settings.creator_poll_interval_seconds,
            "options": {"queue": "signals"},
        },
        # Premium payment monitoring — every 60s
        "check-pending-payments": {
            "task": "app.jobs.tasks.premium_tasks.check_pending_payments",
            "schedule": 60.0,
            "options": {"queue": "default"},
        },
    },
)
