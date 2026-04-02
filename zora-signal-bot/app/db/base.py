"""Async SQLAlchemy engine + session factory."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings
from app.logging_config import get_logger

log = get_logger(__name__)

engine_kwargs = {
    "pool_pre_ping": True,
    "echo": settings.app_debug,
}

# SQLite test runs do not accept the Postgres pool sizing arguments.
database_url = settings.database_url.get_secret_value()
if not database_url.startswith("sqlite"):
    engine_kwargs.update(
        {
            "pool_size": settings.db_pool_size,
            "max_overflow": settings.db_max_overflow,
            "pool_timeout": settings.db_pool_timeout,
        }
    )

engine = create_async_engine(database_url, **engine_kwargs)

AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """Common base class for all ORM models."""


async def get_db() -> AsyncSession:  # type: ignore[return]
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
