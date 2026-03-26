"""
app/bot/assistant.py
─────────────────────────────────────────────────────────────────────────────
Assistant orchestration layer for OpenAI Responses API.

Responsibilities:
  1. Accept user message
  2. Send to OpenAI assistant for routing/classification
  3. Iterate on tool calls (LLM suggests, we execute, report back)
  4. Return final response to user

Design:
  - No tool execution here (that's the services layer)
  - Pure message iteration logic
  - Tool execution is deferred and mocked for now
  - In Phase 2, tool execution wiring will happen
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app.bot.conversation_store import (
    get_assistant_id,
    get_openai_client,
    get_or_create_conversation_session,
    update_conversation_timestamp,
)
from app.integrations.openai_responses_client import OpenAIResponsesClient

log = logging.getLogger(__name__)


# ── Polling constants ──────────────────────────────────────────────────────────

MAX_POLL_ATTEMPTS = 30  # 5 minutes with 10s intervals
POLL_INTERVAL_SECONDS = 1


# ── Response types ─────────────────────────────────────────────────────────────

class AssistantResponse:
    """Structured response from assistant after tool iteration."""

    def __init__(
        self,
        text: str,
        tool_calls: list[dict[str, Any]] | None = None,
        error: str | None = None,
    ):
        self.text = text
        self.tool_calls = tool_calls or []
        self.error = error


# ── Assistant orchestration ────────────────────────────────────────────────────

async def send_message_to_assistant(
    telegram_user_id: int,
    user_message: str,
    max_iterations: int = 5,
) -> AssistantResponse:
    """
    Send a user message to the assistant and iterate until completion.

    Flow:
      1. Create/get conversation thread for user
      2. Add user message to thread
      3. Run assistant
      4. Poll for completion
      5. While status is "requires_action":
         a. Collect tool calls
         b. Execute tools (stub for now)
         c. Submit tool results
         d. Run assistant again
         e. Poll for completion
      6. Extract final message and return

    Args:
        telegram_user_id: Telegram user ID
        user_message: User's text input
        max_iterations: Max tool-call iterations (safety limit)

    Returns:
        AssistantResponse with final text and any tool calls summary
    """
    try:
        client = await get_openai_client()
        assistant_id = await get_assistant_id()

        # Get or create user's conversation thread
        thread_id, _ = await get_or_create_conversation_session(telegram_user_id)

        # Add user message to thread
        await client.add_message(thread_id, user_message, role="user")

        # Run the assistant
        run = await client.run_thread(thread_id, assistant_id)
        run_id = run["id"]

        # Iterate until completion or max iterations
        iteration = 0
        while iteration < max_iterations:
            iteration += 1

            # Poll for run completion (with backoff)
            run = await _poll_run_until_terminal(client, thread_id, run_id)

            if run["status"] == "completed":
                # Extract final message
                messages = await client.get_thread_messages(thread_id, limit=1)
                response_text = _extract_message_text(messages["data"][0])
                await update_conversation_timestamp(telegram_user_id)
                return AssistantResponse(text=response_text)

            elif run["status"] == "requires_action":
                # Extract tool calls
                tool_calls = run.get("required_action", {}).get("submit_tool_outputs", {}).get(
                    "tool_calls", []
                )

                if not tool_calls:
                    log.warning("requires_action but no tool_calls", run_id=run_id)
                    break

                # Execute tools (stub — will be wired in Phase 2)
                tool_results = await _execute_tools(tool_calls, telegram_user_id)

                # Submit results and continue
                for tool_call_id, result_str in tool_results:
                    await client.submit_tool_result(thread_id, run_id, tool_call_id, result_str)

                # Run again
                run = await client.run_thread(thread_id, assistant_id)
                run_id = run["id"]

            elif run["status"] in ("failed", "cancelled", "expired"):
                return AssistantResponse(
                    text=f"Assistant run failed with status: {run['status']}",
                    error=f"Run {run['status']}",
                )

            else:
                log.warning("unexpected_run_status", status=run["status"], run_id=run_id)
                await asyncio.sleep(POLL_INTERVAL_SECONDS)

        return AssistantResponse(
            text="Assistant reached max iterations without completion",
            error="Max iterations exceeded",
        )

    except Exception as exc:
        log.exception("assistant_error", telegram_user_id=telegram_user_id, exc_info=True)
        return AssistantResponse(
            text="Sorry, I encountered an error. Please try again.",
            error=str(exc),
        )


async def _poll_run_until_terminal(
    client: OpenAIResponsesClient,
    thread_id: str,
    run_id: str,
    max_attempts: int = MAX_POLL_ATTEMPTS,
) -> dict[str, Any]:
    """Poll run status until terminal state or max attempts."""
    for attempt in range(max_attempts):
        run = await client.get_run_status(thread_id, run_id)
        status = run["status"]

        if status in ("completed", "requires_action", "failed", "cancelled", "expired"):
            return run

        await asyncio.sleep(POLL_INTERVAL_SECONDS)

    log.warning(
        "poll_run_timeout",
        thread_id=thread_id,
        run_id=run_id,
        attempts=max_attempts,
    )
    return run


def _extract_message_text(message: dict[str, Any]) -> str:
    """Extract text from an OpenAI message object."""
    content = message.get("content", [])
    for block in content:
        if block.get("type") == "text":
            return block.get("text", "")
    return ""


async def _execute_tools(
    tool_calls: list[dict[str, Any]],
    telegram_user_id: int,
) -> list[tuple[str, str]]:
    """
    Execute tool calls using real domain services (Phase 2).

    Imports and calls the ToolExecutor from app.bot.tools.
    Each tool is wired to backend services:
      - Creator tracking → TrackedCreatorRepository
      - Signals → SignalRepository + ScoringEngine
      - Trading → TradeExecutionService + RiskManager
      - Wallet linking → WalletLinkingService
      - Preferences → UserPreferences repository

    Args:
        tool_calls: List of tool call objects from OpenAI
        telegram_user_id: User ID for context

    Returns:
        List of (tool_call_id, result_json_str) tuples for submission back to OpenAI
    """
    from app.bot.tools import execute_tool

    results = []

    for tool_call in tool_calls:
        tool_call_id = tool_call["id"]
        tool_name = tool_call["function"]["name"]
        tool_args_str = tool_call["function"]["arguments"]

        try:
            tool_args = json.loads(tool_args_str)
        except json.JSONDecodeError:
            result_obj = {"success": False, "error": "Invalid JSON in tool arguments"}
            results.append((tool_call_id, json.dumps(result_obj)))
            continue

        log.info(
            "tool_call_received",
            tool_name=tool_name,
            tool_args=tool_args,
            telegram_user_id=telegram_user_id,
        )

        # Execute the tool with real domain services
        result_obj = await execute_tool(
            telegram_user_id=telegram_user_id,
            tool_name=tool_name,
            tool_args=tool_args,
        )

        log.info(
            "tool_call_completed",
            tool_name=tool_name,
            success=result_obj.get("success", False),
            telegram_user_id=telegram_user_id,
        )

        results.append((tool_call_id, json.dumps(result_obj)))

    return results
