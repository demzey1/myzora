"""Shared pytest fixtures."""

from __future__ import annotations

import os
from typing import AsyncGenerator
from unittest.mock import AsyncMock, patch

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-not-used-in-production-64chars!!")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "0000000000:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
os.environ.setdefault("TELEGRAM_ADMIN_USER_IDS", "12345")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("CELERY_BROKER_URL", "redis://localhost:6379/1")
os.environ.setdefault("CELERY_RESULT_BACKEND", "redis://localhost:6379/2")
os.environ.setdefault("LIVE_TRADING_ENABLED", "false")
os.environ.setdefault("PAPER_TRADING_ENABLED", "true")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("SOCIALDATA_API_KEY", "test-socialdata-key")
os.environ.setdefault("ZORA_API_KEY", "test-zora-key")
os.environ.setdefault("ALCHEMY_API_KEY", "test-alchemy-key")
os.environ.setdefault("WALLET_LINK_SECRET", "test-wallet-link-secret")

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()

from app.db.base import Base  # noqa: E402
from app.db import models  # noqa: F401, E402

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(TEST_DATABASE_URL, echo=False)
TestSessionLocal = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture(scope="session", autouse=True)
async def create_tables():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def async_client() -> AsyncGenerator[AsyncClient, None]:
    with (
        patch("app.main.get_application") as mock_get_app,
        patch("app.main.engine", test_engine),
    ):
        mock_bot = AsyncMock()
        mock_bot.username = "test_bot"
        mock_app = AsyncMock()
        mock_app.bot = mock_bot
        mock_get_app.return_value = mock_app

        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            yield client
