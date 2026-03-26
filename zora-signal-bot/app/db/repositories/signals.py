"""
app/db/repositories/signals.py
Repositories for Signal and RiskEvent models.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select

from app.db.models import Recommendation, RiskEvent, RiskEventType, Signal
from app.db.repositories.base import BaseRepository


class SignalRepository(BaseRepository[Signal]):
    model = Signal

    async def get_recent(self, limit: int = 20) -> list[Signal]:
        result = await self.session.execute(
            select(Signal)
            .order_by(Signal.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def count_today(self) -> int:
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        result = await self.session.execute(
            select(func.count(Signal.id)).where(Signal.created_at >= today_start)
        )
        return result.scalar_one() or 0

    async def get_pending_approval(self) -> list[Signal]:
        """Return signals that are ALERT or higher but not yet approved/rejected."""
        result = await self.session.execute(
            select(Signal).where(
                Signal.recommendation.in_([
                    Recommendation.PAPER_TRADE,
                    Recommendation.LIVE_TRADE_READY,
                ]),
                Signal.is_approved == None,  # noqa: E711
            ).order_by(Signal.created_at.desc())
        )
        return list(result.scalars().all())


class RiskEventRepository(BaseRepository[RiskEvent]):
    model = RiskEvent

    async def log_event(
        self,
        event_type: RiskEventType,
        signal_id: int | None = None,
        coin_id: int | None = None,
        description: str | None = None,
    ) -> RiskEvent:
        evt = RiskEvent(
            event_type=event_type,
            signal_id=signal_id,
            coin_id=coin_id,
            description=description,
        )
        return await self.add(evt)
