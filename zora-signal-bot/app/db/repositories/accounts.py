"""
app/db/repositories/accounts.py
Repositories for MonitoredAccount and Creator models.
"""

from __future__ import annotations

from sqlalchemy import select

from app.db.models import Creator, MonitoredAccount
from app.db.repositories.base import BaseRepository


class MonitoredAccountRepository(BaseRepository[MonitoredAccount]):
    model = MonitoredAccount

    async def get_by_x_username(self, username: str) -> MonitoredAccount | None:
        username = username.lstrip("@").lower()
        result = await self.session.execute(
            select(MonitoredAccount).where(
                MonitoredAccount.x_username.ilike(username)
            )
        )
        return result.scalar_one_or_none()

    async def get_by_x_user_id(self, x_user_id: str) -> MonitoredAccount | None:
        result = await self.session.execute(
            select(MonitoredAccount).where(MonitoredAccount.x_user_id == x_user_id)
        )
        return result.scalar_one_or_none()

    async def get_active_accounts(self) -> list[MonitoredAccount]:
        result = await self.session.execute(
            select(MonitoredAccount).where(
                MonitoredAccount.is_active == True,  # noqa: E712
                MonitoredAccount.is_blacklisted == False,  # noqa: E712
            )
        )
        return list(result.scalars().all())


class CreatorRepository(BaseRepository[Creator]):
    model = Creator

    async def get_by_wallet(self, wallet_address: str) -> Creator | None:
        result = await self.session.execute(
            select(Creator).where(Creator.wallet_address == wallet_address)
        )
        return result.scalar_one_or_none()

    async def get_by_x_username(self, x_username: str) -> Creator | None:
        result = await self.session.execute(
            select(Creator).where(Creator.x_username.ilike(x_username))
        )
        return result.scalar_one_or_none()
