"""
tests/unit/test_conversation_store.py
─────────────────────────────────────────────────────────────────────────────
Unit tests for conversation session management.

Tests:
  - OpenAI client initialization
  - Conversation session creation
  - Session retrieval and caching
  - Stale session cleanup
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timedelta

from app.bot.conversation_store import (
    init_openai_client,
    get_openai_client,
    get_assistant_id,
    close_openai_client,
)


@pytest.fixture(autouse=True)
def mock_settings_fixture(monkeypatch):
    """Mock settings for tests."""
    from app.config import settings as real_settings

    # Create a mock settings with required OpenAI config
    mock_settings = MagicMock()
    mock_settings.enable_conversational_mode = True
    mock_settings.openai_api_key = MagicMock()
    mock_settings.openai_api_key.get_secret_value.return_value = "sk-test-key"
    mock_settings.openai_api_base = "https://api.openai.com/v1"
    mock_settings.openai_responses_model = "gpt-4o-mini"
    mock_settings.conversation_timeout_minutes = 30

    # Patch the settings module
    monkeypatch.setattr("app.bot.conversation_store.settings", mock_settings)
    return mock_settings


@pytest.mark.asyncio
async def test_init_openai_client(mock_settings_fixture):
    """Test OpenAI client initialization."""
    with patch(
        "app.bot.conversation_store.OpenAIResponsesClient"
    ) as mock_client_class:
        mock_client = AsyncMock()
        mock_client.create_assistant = AsyncMock(return_value="asst_123")
        mock_client_class.return_value = mock_client

        # Reset global state
        import app.bot.conversation_store as cs

        cs._openai_client = None
        cs._openai_assistant_id = None

        client, assistant_id = await init_openai_client()

        assert client is not None
        assert assistant_id == "asst_123"
        mock_client.create_assistant.assert_called_once()


@pytest.mark.asyncio
async def test_get_openai_client_raises_if_not_initialized():
    """Test that getting client before init raises error."""
    import app.bot.conversation_store as cs

    cs._openai_client = None

    with pytest.raises(RuntimeError):
        await get_openai_client()


@pytest.mark.asyncio
async def test_get_assistant_id_raises_if_not_initialized():
    """Test that getting assistant ID before init raises error."""
    import app.bot.conversation_store as cs

    cs._openai_assistant_id = None

    with pytest.raises(RuntimeError):
        await get_assistant_id()


@pytest.mark.asyncio
async def test_close_openai_client():
    """Test closing the OpenAI client."""
    import app.bot.conversation_store as cs

    # Create a mock client
    mock_client = AsyncMock()
    cs._openai_client = mock_client

    await close_openai_client()

    mock_client.aclose.assert_called_once()
    assert cs._openai_client is None


@pytest.mark.asyncio
@patch("app.bot.conversation_store.AsyncSessionLocal")
@patch("app.bot.conversation_store.get_openai_client")
@patch("app.bot.conversation_store.get_assistant_id")
async def test_get_or_create_conversation_session_new(
    mock_get_assistant_id,
    mock_get_client,
    mock_session_local,
):
    """Test creating a new conversation session."""
    # Setup mocks
    mock_client = AsyncMock()
    mock_client.create_thread = AsyncMock(return_value="thread_456")
    mock_get_client.return_value = mock_client
    mock_get_assistant_id.return_value = "asst_123"

    # Mock database session
    mock_db_session = AsyncMock()
    mock_session_local.return_value.__aenter__.return_value = mock_db_session
    mock_db_session.execute = AsyncMock()
    mock_db_session.execute.return_value.scalar_one_or_none.return_value = None

    from app.bot.conversation_store import get_or_create_conversation_session

    thread_id, assistant_id = await get_or_create_conversation_session(
        telegram_user_id=12345
    )

    assert thread_id == "thread_456"
    assert assistant_id == "asst_123"
    mock_client.create_thread.assert_called_once()
    mock_db_session.add.assert_called_once()


@pytest.mark.asyncio
@patch("app.bot.conversation_store.AsyncSessionLocal")
@patch("app.bot.conversation_store.get_openai_client")
@patch("app.bot.conversation_store.get_assistant_id")
async def test_get_or_create_conversation_session_existing(
    mock_get_assistant_id,
    mock_get_client,
    mock_session_local,
):
    """Test retrieving an existing conversation session."""
    from app.db.models import ConversationSession

    # Setup mocks
    mock_client = AsyncMock()
    mock_get_client.return_value = mock_client
    mock_get_assistant_id.return_value = "asst_123"

    # Mock existing session in database
    existing_session = ConversationSession(
        telegram_user_id=12345,
        openai_thread_id="thread_existing",
        openai_assistant_id="asst_123",
        last_message_at=datetime.utcnow(),
    )

    mock_db_session = AsyncMock()
    mock_session_local.return_value.__aenter__.return_value = mock_db_session
    mock_query_result = AsyncMock()
    mock_query_result.scalar_one_or_none.return_value = existing_session
    mock_db_session.execute.return_value = mock_query_result

    from app.bot.conversation_store import get_or_create_conversation_session

    thread_id, assistant_id = await get_or_create_conversation_session(
        telegram_user_id=12345
    )

    assert thread_id == "thread_existing"
    assert assistant_id == "asst_123"
    # Should not create a new thread
    mock_client.create_thread.assert_not_called()


@pytest.mark.asyncio
@patch("app.bot.conversation_store.AsyncSessionLocal")
async def test_cleanup_stale_sessions(mock_session_local):
    """Test cleaning up stale conversation sessions."""
    from app.db.models import ConversationSession

    # Create stale session (older than timeout)
    stale_session = ConversationSession(
        telegram_user_id=12345,
        openai_thread_id="thread_old",
        openai_assistant_id="asst_123",
        last_message_at=datetime.utcnow() - timedelta(hours=2),
    )

    mock_db_session = AsyncMock()
    mock_session_local.return_value.__aenter__.return_value = mock_db_session
    mock_query_result = AsyncMock()
    mock_query_result.scalars.return_value.all.return_value = [stale_session]
    mock_db_session.execute.return_value = mock_query_result

    from app.bot.conversation_store import cleanup_stale_sessions

    count = await cleanup_stale_sessions(timeout_minutes=30)

    assert count == 1
    mock_db_session.delete.assert_called_once()
