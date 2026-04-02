from __future__ import annotations

from typing import Any, Optional

import httpx
from tenacity import retry_if_exception_type, stop_after_attempt, wait_exponential
from tenacity import AsyncRetrying
from app.logging_config import get_logger

log = get_logger(__name__)

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
                        "description": "Tracking mode: creator_only, keyword_only, or hybrid",
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
            "parameters": {"type": "object", "properties": {}, "required": []},
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
                    "hours": {"type": "integer", "description": "Look back N hours"},
                    "min_score": {
                        "type": "integer",
                        "description": "Minimum signal score from 0 to 100",
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
                    "signal_id": {"type": "integer", "description": "Signal ID"}
                },
                "required": ["signal_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_coin_market_state",
            "description": "Get current market state for a Zora coin",
            "parameters": {
                "type": "object",
                "properties": {
                    "coin_symbol": {
                        "type": "string",
                        "description": "Coin symbol or contract address",
                    }
                },
                "required": ["coin_symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "preview_trade",
            "description": "Preview a buy or sell for a coin",
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
            "description": "Execute a real trade if wallet is linked and trading is enabled",
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
            "description": "Initiate secure wallet linking flow",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_wallet_link_status",
            "description": "Check if wallet is linked and trading is enabled",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_position_status",
            "description": "Get current open positions and their P&L",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_user_preferences",
            "description": "Retrieve the user's stored preferences",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_user_preferences",
            "description": "Update user preferences like mode or risk level",
            "parameters": {
                "type": "object",
                "properties": {
                    "preferences": {
                        "type": "object",
                        "description": "Preference values to update",
                    }
                },
                "required": ["preferences"],
            },
        },
    },
]


class OpenAIResponsesClient:
    """Minimal async wrapper used by the bot's conversational layer."""

    def __init__(
        self,
        api_key: str,
        api_base: str = "https://api.openai.com/v1",
        model: str = "gpt-4o-mini",
        timeout: int = 30,
        max_retries: int = 2,
    ):
        self.api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
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
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _request(self, method: str, endpoint: str, **kwargs: Any) -> dict[str, Any]:
        client = await self._get_client()
        url = f"{self.api_base}{endpoint}"

        async for attempt in AsyncRetrying(
            retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
            stop=stop_after_attempt(self.max_retries),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            reraise=True,
        ):
            with attempt:
                response = await client.request(method, url, **kwargs)
                if response.status_code >= 400:
                    body_preview = response.text[:500]
                    log.error(
                        "openai_request_failed",
                        method=method,
                        endpoint=endpoint,
                        status_code=response.status_code,
                        response_body=body_preview,
                    )
                response.raise_for_status()
                return response.json()

        raise RuntimeError("OpenAI request retry loop exited unexpectedly")

    async def create_thread(self) -> str:
        result = await self._request("POST", "/threads")
        return result["id"]

    async def create_assistant(self, name: str, instructions: str) -> str:
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

    async def add_message(self, thread_id: str, text: str, role: str = "user") -> str:
        result = await self._request(
            "POST",
            f"/threads/{thread_id}/messages",
            json={"role": role, "content": text},
        )
        return result["id"]

    async def run_thread(self, thread_id: str, assistant_id: str) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/threads/{thread_id}/runs",
            json={"assistant_id": assistant_id},
        )

    async def get_run_status(self, thread_id: str, run_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/threads/{thread_id}/runs/{run_id}")

    async def get_thread_messages(self, thread_id: str, limit: int = 10) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/threads/{thread_id}/messages",
            params={"limit": limit, "order": "desc"},
        )

    async def submit_tool_result(
        self,
        thread_id: str,
        run_id: str,
        tool_call_id: str,
        result: str,
    ) -> dict[str, Any]:
        return await self._request(
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

    async def get_system_prompt(self) -> str:
        return (
            "You are a conversational Zora Signal Bot assistant. "
            "Help traders track creators, explain signals, link wallets safely, "
            "preview trades before execution, and use tools whenever live data or actions are needed. "
            "Keep responses concise and readable for Telegram."
        )


async def get_openai_client(
    api_key: str,
    api_base: str = "https://api.openai.com/v1",
    model: str = "gpt-4o-mini",
) -> OpenAIResponsesClient:
    return OpenAIResponsesClient(api_key=api_key, api_base=api_base, model=model)
