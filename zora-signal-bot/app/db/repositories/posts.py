"""
app/db/repositories/posts.py
Repositories for Post and PostMetricsSnapshot models.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.db.models import Post, PostMetricsSnapshot
from app.db.repositories.base import BaseRepository


class PostRepository(BaseRepository[Post]):
    model = Post

    async def get_by_x_post_id(self, x_post_id: str) -> Post | None:
        result = await self.session.execute(
            select(Post).where(Post.x_post_id == x_post_id)
        )
        return result.scalar_one_or_none()

    async def get_unprocessed(self, limit: int = 50) -> list[Post]:
        result = await self.session.execute(
            select(Post)
            .where(Post.is_processed == False)  # noqa: E712
            .order_by(Post.posted_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_recent(self, limit: int = 20) -> list[Post]:
        result = await self.session.execute(
            select(Post).order_by(Post.created_at.desc()).limit(limit)
        )
        return list(result.scalars().all())


class PostMetricsSnapshotRepository(BaseRepository[PostMetricsSnapshot]):
    model = PostMetricsSnapshot

    async def get_latest_for_post(self, post_id: int) -> PostMetricsSnapshot | None:
        result = await self.session.execute(
            select(PostMetricsSnapshot)
            .where(PostMetricsSnapshot.post_id == post_id)
            .order_by(PostMetricsSnapshot.captured_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_previous_for_velocity(
        self, post_id: int, current_snapshot_id: int
    ) -> PostMetricsSnapshot | None:
        """
        Return the snapshot immediately before current_snapshot_id.
        Used for velocity calculation in the scoring engine.
        """
        result = await self.session.execute(
            select(PostMetricsSnapshot)
            .where(
                PostMetricsSnapshot.post_id == post_id,
                PostMetricsSnapshot.id < current_snapshot_id,
            )
            .order_by(PostMetricsSnapshot.captured_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
