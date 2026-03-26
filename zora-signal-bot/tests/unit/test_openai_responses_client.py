"""
tests/unit/test_openai_responses_client.py
─────────────────────────────────────────────────────────────────────────────
Unit tests for OpenAI Responses API client.

Tests:
  - Client initialization
  - Thread creation
  - Message addition
  - Run submission
  - Tool extraction
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.integrations.openai_responses_client import (
    OpenAIResponsesClient,
    ASSISTANT_TOOLS,
)


@pytest.fixture
def mock_api_key():
    return "sk-test-key-12345"


@pytest.fixture
def client(mock_api_key):
    return OpenAIResponsesClient(api_key=mock_api_key)


@pytest.mark.asyncio
async def test_openai_client_initialization(mock_api_key):
    """Test client initializes with correct parameters."""
    client = OpenAIResponsesClient(
        api_key=mock_api_key,
        api_base="https://api.openai.com/v1",
        model="gpt-4o-mini",
    )

    assert client.api_key == mock_api_key
    assert client.api_base == "https://api.openai.com/v1"
    assert client.model == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_assistant_tools_defined():
    """Verify that assistant tools are properly defined."""
    assert len(ASSISTANT_TOOLS) > 0

    # Check required tool names
    tool_names = [tool["function"]["name"] for tool in ASSISTANT_TOOLS]
    required_tools = [
        "track_creator",
        "list_tracked_creators",
        "get_zora_signals",
        "explain_signal",
        "get_coin_market_state",
        "preview_trade",
        "execute_trade",
        "start_wallet_link",
        "check_wallet_link_status",
        "get_position_status",
    ]

    for required_tool in required_tools:
        assert (
            required_tool in tool_names
        ), f"Required tool '{required_tool}' not found in ASSISTANT_TOOLS"


@pytest.mark.asyncio
async def test_create_thread(client):
    """Test thread creation via mocked API."""
    with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {"id": "thread_123"}

        result = await client.create_thread()

        assert result == "thread_123"
        mock_request.assert_called_once_with("POST", "/threads")


@pytest.mark.asyncio
async def test_create_assistant(client):
    """Test assistant creation via mocked API."""
    with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {"id": "asst_456"}

        result = await client.create_assistant(
            name="Test Assistant",
            instructions="You are a test assistant.",
        )

        assert result == "asst_456"
        mock_request.assert_called_once()
        call_args = mock_request.call_args
        assert call_args[0][0] == "POST"
        assert call_args[0][1] == "/assistants"


@pytest.mark.asyncio
async def test_add_message(client):
    """Test adding a message to a thread."""
    with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {"id": "msg_789"}

        result = await client.add_message(
            thread_id="thread_123",
            text="Hello, assistant!",
            role="user",
        )

        assert result == "msg_789"
        mock_request.assert_called_once()


@pytest.mark.asyncio
async def test_run_thread(client):
    """Test starting a run on a thread."""
    with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {
            "id": "run_111",
            "status": "queued",
            "thread_id": "thread_123",
        }

        result = await client.run_thread(
            thread_id="thread_123",
            assistant_id="asst_456",
        )

        assert result["id"] == "run_111"
        assert result["status"] == "queued"


@pytest.mark.asyncio
async def test_get_run_status(client):
    """Test checking run status."""
    with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {
            "id": "run_111",
            "status": "completed",
        }

        result = await client.get_run_status(
            thread_id="thread_123",
            run_id="run_111",
        )

        assert result["status"] == "completed"


@pytest.mark.asyncio
async def test_get_thread_messages(client):
    """Test retrieving messages from a thread."""
    with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {
            "data": [
                {
                    "id": "msg_111",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": "Hello, user!",
                        }
                    ],
                }
            ],
            "has_more": False,
        }

        result = await client.get_thread_messages(thread_id="thread_123")

        assert len(result["data"]) == 1
        assert result["data"][0]["role"] == "assistant"


@pytest.mark.asyncio
async def test_submit_tool_result(client):
    """Test submitting tool output to a run."""
    with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = {
            "id": "run_111",
            "status": "queued",
        }

        result = await client.submit_tool_result(
            thread_id="thread_123",
            run_id="run_111",
            tool_call_id="call_xyz",
            result='{"status": "success"}',
        )

        assert result["id"] == "run_111"
        mock_request.assert_called_once()


@pytest.mark.asyncio
async def test_client_cleanup(client):
    """Test that client can be safely closed."""
    # Create a mock async client
    with patch("httpx.AsyncClient") as mock_async_client_class:
        mock_client_instance = AsyncMock()
        mock_async_client_class.return_value = mock_client_instance

        # Trigger client creation
        _ = await client._get_client()

        # Close the client
        await client.close()

        # Verify aclose was called
        mock_client_instance.aclose.assert_called_once()
        assert client._client is None


@pytest.mark.asyncio
async def test_get_system_prompt(client):
    """Test that system prompt is returned."""
    prompt = await client.get_system_prompt()

    assert isinstance(prompt, str)
    assert len(prompt) > 0
    assert "Zora Signal Bot" in prompt
    assert "tools" in prompt.lower() or "function" in prompt.lower()
