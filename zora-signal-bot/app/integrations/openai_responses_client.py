"""
app/integrations/openai_responses_client.py
─────────────────────────────────────────────────────────────────────────────
OpenAI Responses API (Assistants) wrapper for conversational AI.

This module provides:
  1. OpenAIResponsesClient — wraps OpenAI's Responses API for assistant threads
  2. Tool schema definitions (names, descriptions, parameters)
  3. Per-user thread management and message iteration
  4. Async retry logic with exponential backoff

Design constraints:
  - The assistant CANNOT directly execute tools (that's our job)
  - The assistant just suggests tools and their parameters
  - We collect tool calls, execute them deterministically, and report results back
  - All tool execution happens in the domain services layer (trading, wallet link, etc)
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

log = logging.getLogger(__name__)


# ── Tool Schemas (function calling definitions) ────────────────────────────────

ASSISTANT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "track_creator",
            "description": "Start tracking a Zora creator or X account for signals",
            "parameters": {
                "type": "object",
                "properties": {
                    "x_username": {
                        "type": "string",
                        "description": "The X (Twitter) handle to track, e.g. 'vitalik'",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["creator_only", "keyword_only", "hybrid"],
                        "description": "Tracking mode: creator_only (their coins), keyword_only (mention matches), hybrid (both)",
                    },
                },
                "required": ["x_username", "mode"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tracked_creators",
            "description": "List all creators this user is currently tracking",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_zora_signals",
            "description": "Get recent Zora coin signals (flagged opportunities)",
            "parameters": {
                "type": "object",
                "properties": {
                    "hours": {
                        "type": "integer",
                        "description": "Look back N hours (default 24)",
                    },
                    "min_score": {
                        "type": "integer",
                        "description": "Minimum signal score (0-100, default 50)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "explain_signal",
            "description": "Explain why a signal was flagged and its score breakdown",
            "parameters": {
                "type": "object",
                "properties": {
                    "signal_id": {
                        "type": "integer",
                        "description": "The signal ID to explain",
                    },
                },
                "required": ["signal_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_coin_market_state",
            "description": "Get current market state for a Zora coin (price, liquidity, volume)",
            "parameters": {
                "type": "object",
                "properties": {
                    "coin_symbol": {
                        "type": "string",
                        "description": "Coin symbol or contract address",
                    },
                },
                "required": ["coin_symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "preview_trade",
            "description": "Preview a buy or sell for a coin (price, slippage, fees estimate)",
            "parameters": {
                "type": "object",
                "properties": {
                    "coin_symbol": {
                        "type": "string",
                        "description": "Coin symbol or contract address",
                    },
                    "action": {
                        "type": "string",
                        "enum": ["buy", "sell"],
                        "description": "Trade action",
                    },
                    "amount_usd": {
                        "type": "number",
                        "description": "Trade size in USD",
                    },
                },
                "required": ["coin_symbol", "action", "amount_usd"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_trade",
            "description": "Execute a real trade (buy or sell) if wallet is linked and trading is enabled",
            "parameters": {
                "type": "object",
                "properties": {
                    "coin_symbol": {
                        "type": "string",
                        "description": "Coin symbol or contract address",
                    },
                    "action": {
                        "type": "string",
                        "enum": ["buy", "sell"],
                        "description": "Trade action",
                    },
                    "amount_usd": {
                        "type": "number",
                        "description": "Trade size in USD",
                    },
                },
                "required": ["coin_symbol", "action", "amount_usd"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_wallet_link",
            "description": "Initiate secure wallet linking flow (opens a verified web link)",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_wallet_link_status",
            "description": "Check if wallet is linked and trading is enabled",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_position_status",
            "description": "Get current open positions and their P&L",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_user_preferences",
            "description": "Retrieve user's stored preferences (mode, risk level, etc)",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_user_preferences",
            "description": "Update user preferences like trading mode or risk level",
            "parameters": {
                "type": "object",
                "properties": {
                    "preferences": {
                        "type": "object",
                        "description": "Key-value pairs of preferences to update, e.g. {'mode': 'creator_only', 'risk': 'small'}",
                    },
                },
                "required": ["preferences"],
            },
        },
    },
]


# ── OpenAI Responses Client ────────────────────────────────────────────────────

class OpenAIResponsesClient:
    """
    Wrapper around OpenAI's Responses API (Assistants).
    Manages per-user threads and message iteration.
    """

    def __init__(
        self,
        api_key: str,
        api_base: str = "https://api.openai.com/v1",
        model: str = "gpt-4o-mini",
        timeout: int = 30,
        max_retries: int = 2,
    ):
        """
        Initialize OpenAI client.

        Args:
            api_key: OpenAI API key
            api_base: API base URL (default: https://api.openai.com/v1)
            model: Model name (default: gpt-4o-mini)
            timeout: Request timeout in seconds
            max_retries: Max retries for failed requests
        """
        self.api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create async HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "OpenAI-Beta": "assistants=v2",
                },
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        stop=stop_after_attempt(self.max_retries),
        wait=wait_exponential(multiplier=1, min=1, max=10),
    )
    async def _request(
        self,
        method: str,
        endpoint: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Make a request to OpenAI API with retry logic."""
        client = await self._get_client()
        url = f"{self.api_base}{endpoint}"
        response = await client.request(method, url, **kwargs)
        response.raise_for_status()
        return response.json()

    async def create_thread(self) -> str:
        """Create a new conversation thread. Returns thread_id."""
        result = await self._request("POST", "/threads")
        return result["id"]

    async def create_assistant(
        self,
        name: str,
        instructions: str,
    ) -> str:
        """
        Create a new assistant with tools. Returns assistant_id.
        Usually called once at startup; thread_id is per-user.
        """
        result = await self._request(
            "POST",
            "/assistants",
            json={
                "model": self.model,
                "name": name,
                "instructions": instructions,
                "tools": ASSISTANT_TOOLS,
            },
        )
        return result["id"]

    async def add_message(
        self,
        thread_id: str,
        text: str,
        role: str = "user",
    ) -> str:
        """Add a message to a thread. Returns message_id."""
        result = await self._request(
            "POST",
            f"/threads/{thread_id}/messages",
            json={"role": role, "content": text},
        )
        return result["id"]

    async def run_thread(
        self,
        thread_id: str,
        assistant_id: str,
    ) -> dict[str, Any]:
        """
        Run the assistant on a thread.
        Returns the run object (run_id, status, etc).
        Must poll for completion.
        """
        result = await self._request(
            "POST",
            f"/threads/{thread_id}/runs",
            json={"assistant_id": assistant_id},
        )
        return result

    async def get_run_status(
        self,
        thread_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        """Check the status of a run. Returns run object."""
        result = await self._request("GET", f"/threads/{thread_id}/runs/{run_id}")
        return result

    async def get_thread_messages(
        self,
        thread_id: str,
        limit: int = 10,
    ) -> dict[str, Any]:
        """
        Get recent messages from a thread.
        Returns {data: [message, ...], has_more: bool}
        """
        result = await self._request(
            "GET",
            f"/threads/{thread_id}/messages",
            params={"limit": limit, "order": "desc"},
        )
        return result

    async def submit_tool_result(
        self,
        thread_id: str,
        run_id: str,
        tool_call_id: str,
        result: str,  # JSON stringified tool output
    ) -> dict[str, Any]:
        """Submit the result of a tool call back to the run."""
        result_obj = await self._request(
            "POST",
            f"/threads/{thread_id}/runs/{run_id}/submit_tool_outputs",
            json={
                "tool_outputs": [
                    {
                        "tool_call_id": tool_call_id,
                        "output": result,
                    }
                ]
            },
        )
        return result_obj

    async def get_system_prompt(self) -> str:
        """Return the system prompt for the conversational assistant."""
        return """You are a conversational Zora Signal Bot assistant. Your role is to help traders:
- Track Zora creator coins and discover trading signals
- Link wallets safely (via secure web flow)
- Preview and execute trades with risk controls
- Answer questions about coins and signals
- Remember user preferences

Guidelines:
1. Be concise and trader-friendly in Telegram
2. Always offer buttons for next steps (Track, Buy, Sell, Link Wallet, etc)
3. Use the provided tools to fetch live data, never hallucinate prices or signals
4. Explain signals clearly: why they were flagged, the score breakdown
5. For trades, always show a preview with price, slippage, fees BEFORE execution
6. Keep trades under user-configured risk limits
7. Never ask users to paste private keys — wallet linking uses secure web flow
8. Remember recent preferences and history within the conversation

When the user asks for data, prices, signals, or action, use the appropriate tools.
When you don't have enough info, ask clarifying questions.
Keep responses under 500 characters for telegram readability."""


# ── Async context manager for cleanup ──────────────────────────────────────────

async def get_openai_client(
    api_key: str,
    api_base: str = "https://api.openai.com/v1",
    model: str = "gpt-4o-mini",
) -> OpenAIResponsesClient:
    """Factory function to create an OpenAI client."""
    return OpenAIResponsesClient(api_key=api_key, api_base=api_base, model=model)
