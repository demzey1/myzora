"""
app/bot/assistant.py
Assistant orchestration layer for OpenAI tool-calling chat.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from app.bot.conversation_store import (
    get_assistant_id,
    get_openai_client,
    get_or_create_conversation_session,
    update_conversation_timestamp,
)
from app.integrations.openai_responses_client import OpenAIResponsesClient
from app.logging_config import get_logger

log = get_logger(__name__)

MAX_POLL_ATTEMPTS = 30
POLL_INTERVAL_SECONDS = 1


class AssistantResponse:
    """Structured response returned to Telegram handlers."""

    def __init__(
        self,
        text: str,
        tool_calls: list[dict[str, Any]] | None = None,
        error: str | None = None,
        tools_executed: list[str] | None = None,
        inline_buttons_data: dict[str, Any] | None = None,
    ):
        self.text = text
        self.tool_calls = tool_calls or []
        self.error = error
        self.tools_executed = tools_executed or []
        self.inline_buttons_data = inline_buttons_data or {}


async def send_message_to_assistant(
    telegram_user_id: int,
    user_message: str,
    max_iterations: int = 5,
) -> AssistantResponse:
    """Send a Telegram message into the assistant loop and return the final reply."""
    try:
        client = await get_openai_client()
        assistant_id = await get_assistant_id()
        thread_id, _ = await get_or_create_conversation_session(telegram_user_id)

        await client.add_message(thread_id, user_message, role="user")
        run = await client.run_thread(thread_id, assistant_id)
        run_id = run["id"]

        iteration = 0
        tools_executed_list: list[str] = []
        latest_tool_results: dict[str, dict[str, Any]] = {}

        while iteration < max_iterations:
            iteration += 1
            run = await _poll_run_until_terminal(client, thread_id, run_id)

            if run["status"] == "completed":
                messages = await client.get_thread_messages(thread_id, limit=1)
                response_text = _extract_message_text(messages["data"][0])
                await update_conversation_timestamp(telegram_user_id)
                if not response_text.strip():
                    response_text = _fallback_response(user_message)
                return AssistantResponse(
                    text=response_text,
                    tools_executed=tools_executed_list,
                    inline_buttons_data=_build_inline_buttons_data(latest_tool_results),
                )

            if run["status"] == "requires_action":
                tool_calls = (
                    run.get("required_action", {})
                    .get("submit_tool_outputs", {})
                    .get("tool_calls", [])
                )
                if not tool_calls:
                    log.warning(f"requires_action_but_no_tool_calls run_id={run_id}")
                    break

                tools_called = [tc["function"]["name"] for tc in tool_calls]
                tools_executed_list.extend(tools_called)
                tool_results = await _execute_tools(tool_calls, telegram_user_id)

                for tool_result in tool_results:
                    latest_tool_results[tool_result["tool_name"]] = tool_result["result_obj"]
                    await client.submit_tool_result(
                        thread_id,
                        run_id,
                        tool_result["tool_call_id"],
                        tool_result["result_str"],
                    )

                run = await client.run_thread(thread_id, assistant_id)
                run_id = run["id"]
                continue

            if run["status"] in ("failed", "cancelled", "expired"):
                last_error = _format_run_error(run)
                if last_error:
                    log.error(
                        "assistant_run_failed",
                        telegram_user_id=telegram_user_id,
                        run_status=run["status"],
                        run_error=last_error,
                    )
                fallback_text = _fallback_response(user_message)
                return AssistantResponse(
                    text=fallback_text,
                    error=last_error or f"Run {run['status']}",
                )

            log.warning(f"unexpected_run_status status={run['status']} run_id={run_id}")
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

        return AssistantResponse(
            text="Assistant reached max iterations without completion",
            error="Max iterations exceeded",
        )

    except Exception as exc:
        log.exception(
            "assistant_error",
            telegram_user_id=telegram_user_id,
            error=str(exc),
            user_message=user_message,
        )
        return AssistantResponse(
            text=_fallback_response(user_message),
            error=str(exc),
        )


async def _poll_run_until_terminal(
    client: OpenAIResponsesClient,
    thread_id: str,
    run_id: str,
    max_attempts: int = MAX_POLL_ATTEMPTS,
) -> dict[str, Any]:
    for _ in range(max_attempts):
        run = await client.get_run_status(thread_id, run_id)
        if run["status"] in ("completed", "requires_action", "failed", "cancelled", "expired"):
            return run
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
    log.warning(f"poll_run_timeout thread_id={thread_id} run_id={run_id} attempts={max_attempts}")
    return run


def _extract_message_text(message: dict[str, Any]) -> str:
    content = message.get("content", [])
    for block in content:
        if block.get("type") == "text":
            text_block = block.get("text", "")
            if isinstance(text_block, dict):
                return str(text_block.get("value", ""))
            return str(text_block)
    return ""


def _format_run_error(run: dict[str, Any]) -> str | None:
    last_error = run.get("last_error") or {}
    if not last_error:
        return None
    code = last_error.get("code")
    message = last_error.get("message")
    if code and message:
        return f"{code}: {message}"
    if message:
        return str(message)
    if code:
        return str(code)
    return None


def _fallback_response(user_message: str) -> str:
    text = user_message.strip().lower()
    if text in {"hi", "hello", "hey", "yo"}:
        return (
            "Hi. I’m Zora Signal Bot. I can track creators, explain flagged signals, "
            "look up Zora coins, preview trades, and help with wallet linking."
        )
    if "what do you do" in text or "who are you" in text or "help" == text:
        return (
            "I’m a Telegram trading assistant for Zora signals. I can track creators, "
            "show recent signals, explain why a coin was flagged, look up Zora coin "
            "market state, preview trades, and start secure wallet linking."
        )
    return (
        "I’m Zora Signal Bot. I help with creator tracking, Zora signal explanation, "
        "coin lookups, trade previews, and secure wallet linking. Try: "
        "'track @creatorname' or 'show top signals'."
    )


async def _execute_tools(
    tool_calls: list[dict[str, Any]],
    telegram_user_id: int,
) -> list[dict[str, Any]]:
    from app.bot.tools import execute_tool

    results: list[dict[str, Any]] = []

    for tool_call in tool_calls:
        tool_call_id = tool_call["id"]
        tool_name = tool_call["function"]["name"]
        tool_args_str = tool_call["function"]["arguments"]

        try:
            tool_args = json.loads(tool_args_str)
        except json.JSONDecodeError:
            result_obj = {"success": False, "error": "Invalid JSON in tool arguments"}
            results.append(
                {
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "result_str": json.dumps(result_obj),
                    "result_obj": result_obj,
                }
            )
            continue

        result_obj = await execute_tool(
            telegram_user_id=telegram_user_id,
            tool_name=tool_name,
            tool_args=tool_args,
        )
        results.append(
            {
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "result_str": json.dumps(result_obj),
                "result_obj": result_obj,
            }
        )

    return results


def _build_inline_buttons_data(tool_results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    preview = tool_results.get("preview_trade") or tool_results.get("preview_trade_signal")
    if preview and preview.get("success"):
        data = preview.get("data", {})
        return {
            "type": "trade_preview",
            "coin_symbol": data.get("coin"),
            "action": data.get("action"),
            "amount_usd": data.get("amount_usd"),
        }

    wallet = tool_results.get("start_wallet_link")
    if wallet and wallet.get("success"):
        data = wallet.get("data", {})
        return {
            "type": "wallet_link",
            "url": data.get("link"),
        }

    return {}


