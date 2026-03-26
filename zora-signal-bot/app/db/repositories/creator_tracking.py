"""
app/db/repositories/creator_tracking.py
Repositories for TrackedCreator, CreatorPost, CreatorPostClassification,
CreatorSignalCandidate, and UserStrategyPreferences.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select

from app.db.models import (
    CreatorPost,
    CreatorPostClassification,
    CreatorSignalCandidate,
    CreatorWatchMode,
    TrackedCreator,
    UserStrategyPreferences,
)
from app.db.repositories.base import BaseRepository


class TrackedCreatorRepository(BaseRepository[TrackedCreator]):
    model = TrackedCreator

    async def get_by_user_and_handle(
        self, telegram_user_id: int, x_username: str
    ) -> TrackedCreator | None:
        result = await self.session.execute(
            select(TrackedCreator).where(
                TrackedCreator.telegram_user_id == telegram_user_id,
                TrackedCreator.x_username.ilike(x_username),
            )
        )
        return result.scalar_one_or_none()

    async def get_active_for_user(self, telegram_user_id: int) -> list[TrackedCreator]:
        result = await self.session.execute(
            select(TrackedCreator).where(
                TrackedCreator.telegram_user_id == telegram_user_id,
                TrackedCreator.is_active == True,  # noqa: E712
            ).order_by(TrackedCreator.x_username)
        )
        return list(result.scalars().all())

    async def get_all_active(self) -> list[TrackedCreator]:
        """All active tracked creators across all users (for polling job)."""
        result = await self.session.execute(
            select(TrackedCreator).where(TrackedCreator.is_active == True)  # noqa: E712
        )
        return list(result.scalars().all())


class CreatorPostRepository(BaseRepository[CreatorPost]):
    model = CreatorPost

    async def get_by_x_post_id(self, x_post_id: str) -> CreatorPost | None:
        result = await self.session.execute(
            select(CreatorPost).where(CreatorPost.x_post_id == x_post_id)
        )
        return result.scalar_one_or_none()

    async def get_unclassified(self, limit: int = 50) -> list[CreatorPost]:
        result = await self.session.execute(
            select(CreatorPost)
            .where(CreatorPost.is_classified == False)  # noqa: E712
            .order_by(CreatorPost.posted_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_recent_for_creator(
        self, tracked_creator_id: int, limit: int = 10
    ) -> list[CreatorPost]:
        result = await self.session.execute(
            select(CreatorPost)
            .where(CreatorPost.tracked_creator_id == tracked_creator_id)
            .order_by(CreatorPost.posted_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())


class CreatorPostClassificationRepository(BaseRepository[CreatorPostClassification]):
    model = CreatorPostClassification

    async def get_for_post(self, post_id: int) -> CreatorPostClassification | None:
        result = await self.session.execute(
            select(CreatorPostClassification).where(
                CreatorPostClassification.post_id == post_id
            )
        )
        return result.scalar_one_or_none()

    def decode_json_field(self, value: str | None) -> list[str]:
        if not value:
            return []
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return []


class CreatorSignalCandidateRepository(BaseRepository[CreatorSignalCandidate]):
    model = CreatorSignalCandidate

    async def get_for_post(self, post_id: int) -> list[CreatorSignalCandidate]:
        result = await self.session.execute(
            select(CreatorSignalCandidate)
            .where(CreatorSignalCandidate.post_id == post_id)
            .order_by(CreatorSignalCandidate.final_rank_score.desc())
        )
        return list(result.scalars().all())


class UserStrategyPreferencesRepository(BaseRepository[UserStrategyPreferences]):
    model = UserStrategyPreferences

    async def get_for_user(self, telegram_user_id: int) -> UserStrategyPreferences | None:
        result = await self.session.execute(
            select(UserStrategyPreferences).where(
                UserStrategyPreferences.telegram_user_id == telegram_user_id
            )
        )
        return result.scalar_one_or_none()

    async def get_or_create(self, telegram_user_id: int) -> UserStrategyPreferences:
        existing = await self.get_for_user(telegram_user_id)
        if existing:
            return existing
        prefs = UserStrategyPreferences(telegram_user_id=telegram_user_id)
        return await self.add(prefs)
