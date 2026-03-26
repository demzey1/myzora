"""
app/db/repositories/positions.py
Repositories for PaperPosition and LivePosition models.
Includes portfolio-level queries used by the risk manager and PnL summary.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LivePosition, PaperPosition, PositionStatus
from app.db.repositories.base import BaseRepository


class PaperPositionRepository(BaseRepository[PaperPosition]):
    model = PaperPosition

    async def get_open(self) -> list[PaperPosition]:
        result = await self.session.execute(
            select(PaperPosition).where(PaperPosition.status == PositionStatus.OPEN)
        )
        return list(result.scalars().all())

    async def count_open(self) -> int:
        result = await self.session.execute(
            select(func.count(PaperPosition.id)).where(
                PaperPosition.status == PositionStatus.OPEN
            )
        )
        return result.scalar_one() or 0

    async def get_closed_today(self) -> list[PaperPosition]:
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        result = await self.session.execute(
            select(PaperPosition).where(
                PaperPosition.status != PositionStatus.OPEN,
                PaperPosition.closed_at >= today,
            )
        )
        return list(result.scalars().all())

    async def get_daily_realised_loss(self, session: AsyncSession) -> float:
        """
        Return total realised loss for today (positive = loss).
        Only counts positions closed today with negative PnL.
        """
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        result = await session.execute(
            select(func.sum(PaperPosition.pnl_usd)).where(
                PaperPosition.closed_at >= today,
                PaperPosition.pnl_usd < 0,
            )
        )
        raw = result.scalar_one()
        return abs(float(raw)) if raw is not None else 0.0

    async def get_all_closed(self) -> list[PaperPosition]:
        result = await self.session.execute(
            select(PaperPosition).where(PaperPosition.status != PositionStatus.OPEN)
            .order_by(PaperPosition.closed_at.desc())
        )
        return list(result.scalars().all())

    async def get_pnl_summary(self) -> dict:
        """
        Aggregate PnL statistics across all closed paper positions.
        Returns a dict suitable for display in /pnl command.
        """
        closed = await self.get_all_closed()
        if not closed:
            return {
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "total_pnl_usd": 0.0,
                "avg_pnl_pct": 0.0,
                "best_trade_pnl_usd": None,
                "worst_trade_pnl_usd": None,
                "win_rate_pct": 0.0,
            }

        pnls = [p.pnl_usd for p in closed if p.pnl_usd is not None]
        winners = [p for p in pnls if p > 0]
        losers = [p for p in pnls if p <= 0]
        pnl_pcts = [p.pnl_pct for p in closed if p.pnl_pct is not None]

        return {
            "total_trades": len(closed),
            "winning_trades": len(winners),
            "losing_trades": len(losers),
            "total_pnl_usd": round(sum(pnls), 2),
            "avg_pnl_pct": round(sum(pnl_pcts) / len(pnl_pcts), 2) if pnl_pcts else 0.0,
            "best_trade_pnl_usd": round(max(pnls), 2) if pnls else None,
            "worst_trade_pnl_usd": round(min(pnls), 2) if pnls else None,
            "win_rate_pct": round(len(winners) / len(pnls) * 100, 1) if pnls else 0.0,
        }


class LivePositionRepository(BaseRepository[LivePosition]):
    model = LivePosition

    async def get_open(self) -> list[LivePosition]:
        result = await self.session.execute(
            select(LivePosition).where(LivePosition.status == PositionStatus.OPEN)
        )
        return list(result.scalars().all())

    async def count_open(self) -> int:
        result = await self.session.execute(
            select(func.count(LivePosition.id)).where(
                LivePosition.status == PositionStatus.OPEN
            )
        )
        return result.scalar_one() or 0
