"""
app/db/base.py
─────────────────────────────────────────────────────────────────────────────
Async SQLAlchemy engine + session factory.
Import `AsyncSessionLocal` for use in repositories.
Import `Base` in all model files so Alembic can detect them.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings
from app.logging_config import get_logger

log = get_logger(__name__)

# ── Engine ────────────────────────────────────────────────────────────────────
engine = create_async_engine(
    settings.database_url.get_secret_value(),
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout=settings.db_pool_timeout,
    pool_pre_ping=True,   # Detect stale connections
    echo=settings.app_debug,
)

# ── Session factory ───────────────────────────────────────────────────────────
AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


# ── Declarative base ──────────────────────────────────────────────────────────
class Base(DeclarativeBase):
    """Common base class for all ORM models."""
    pass


# ── Dependency helper (FastAPI) ───────────────────────────────────────────────
async def get_db() -> AsyncSession:  # type: ignore[return]
    """
    FastAPI dependency that yields an async DB session and
    guarantees commit/rollback/close.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
