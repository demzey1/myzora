"""
app/db/repositories/coins.py
Repositories for ZoraCoin and CoinMarketSnapshot models.
"""

from __future__ import annotations

from sqlalchemy import select

from app.db.models import CoinMarketSnapshot, ZoraCoin
from app.db.repositories.base import BaseRepository


class ZoraCoinRepository(BaseRepository[ZoraCoin]):
    model = ZoraCoin

    async def get_by_address(self, contract_address: str) -> ZoraCoin | None:
        result = await self.session.execute(
            select(ZoraCoin).where(ZoraCoin.contract_address == contract_address)
        )
        return result.scalar_one_or_none()

    async def get_by_symbol(self, symbol: str) -> ZoraCoin | None:
        result = await self.session.execute(
            select(ZoraCoin).where(ZoraCoin.symbol.ilike(symbol))
        )
        return result.scalar_one_or_none()

    async def get_coins_for_creator(self, creator_id: int) -> list[ZoraCoin]:
        result = await self.session.execute(
            select(ZoraCoin).where(
                ZoraCoin.creator_id == creator_id,
                ZoraCoin.is_active == True,  # noqa: E712
            )
        )
        return list(result.scalars().all())


class CoinMarketSnapshotRepository(BaseRepository[CoinMarketSnapshot]):
    model = CoinMarketSnapshot

    async def get_latest_for_coin(self, coin_id: int) -> CoinMarketSnapshot | None:
        result = await self.session.execute(
            select(CoinMarketSnapshot)
            .where(CoinMarketSnapshot.coin_id == coin_id)
            .order_by(CoinMarketSnapshot.captured_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
