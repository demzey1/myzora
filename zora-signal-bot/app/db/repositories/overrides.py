"""
app/db/repositories/overrides.py
Repository for CreatorOverride model.
Used by the paper engine and pipeline to enforce blacklist/whitelist.
"""

from __future__ import annotations

from sqlalchemy import or_, select

from app.db.models import CreatorOverride
from app.db.repositories.base import BaseRepository


class CreatorOverrideRepository(BaseRepository[CreatorOverride]):
    model = CreatorOverride

    async def get_for_account(
        self, x_username: str | None, contract_address: str | None
    ) -> CreatorOverride | None:
        """
        Find the most specific override for a given account/coin pair.
        Returns the first match (most recently added) or None.
        """
        conditions = []
        if x_username:
            conditions.append(CreatorOverride.x_username.ilike(x_username))
        if contract_address:
            conditions.append(CreatorOverride.contract_address == contract_address)
        if not conditions:
            return None

        result = await self.session.execute(
            select(CreatorOverride)
            .where(or_(*conditions))
            .order_by(CreatorOverride.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def is_blacklisted(
        self, x_username: str | None = None, contract_address: str | None = None
    ) -> bool:
        override = await self.get_for_account(x_username, contract_address)
        return bool(override and override.is_blacklisted)

    async def get_score_multiplier(
        self, x_username: str | None = None, contract_address: str | None = None
    ) -> float:
        override = await self.get_for_account(x_username, contract_address)
        if override and override.score_multiplier is not None:
            return override.score_multiplier
        return 1.0

    async def list_all(self) -> list[CreatorOverride]:
        result = await self.session.execute(
            select(CreatorOverride).order_by(CreatorOverride.created_at.desc())
        )
        return list(result.scalars().all())
