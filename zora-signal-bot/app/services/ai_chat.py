"""
app/services/ai_chat.py
─────────────────────────────────────────────────────────────────────────────
Claude Haiku AI chat service for the Zora Signal Bot.

Design principles:
  - Uses claude-haiku-4-5-20251001 (fast, cheap, smart enough)
  - Constrained to the bot's signal framework — not a general financial advisor
  - Remembers user preferences and last N messages for context
  - Signal-aware: can explain exactly why the bot scored a signal the way it did
  - Users can toggle AI on/off at any time
  - Premium users get longer context and more messages per day
  - Free users get limited messages per day

System prompt keeps the AI focused:
  - Can explain signals, scores, risk flags
  - Can answer questions about Zora coins and creators
  - Frames everything through IGNORE/WATCH/ALERT/PAPER_TRADE/LIVE_TRADE_CANDIDATE
  - Does NOT give open-ended "buy X" financial advice
  - Does NOT discuss non-Zora topics
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import settings
from app.logging_config import get_logger

log = get_logger(__name__)

# Context window: how many previous messages to send to Claude
FREE_CONTEXT_MESSAGES    = 6
PREMIUM_CONTEXT_MESSAGES = 20

# Daily message limits
FREE_DAILY_LIMIT    = 20
PREMIUM_DAILY_LIMIT = 200

CLAUDE_MODEL = "claude-haiku-4-5-20251001"
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"

_SYSTEM_PROMPT = """You are the AI assistant for Zora Signal Bot — a Telegram bot that tracks X/Twitter creators, detects bullish signals for Zora coins on Base, and alerts users.

YOUR ROLE:
- Help users understand signals this bot has generated
- Explain why a coin scored the way it did (liquidity, momentum, creator linkage, etc.)
- Answer questions about specific Zora coins and creators
- Explain the bot's scoring framework

THE BOT'S SIGNAL TIERS (always frame guidance within these):
- IGNORE — score too low, no action needed
- WATCH — interesting but not actionable yet
- ALERT — strong signal, user should pay attention
- PAPER_TRADE — high confidence, would be a paper trade candidate
- LIVE_TRADE_CANDIDATE — very high confidence, meets all criteria

WHAT YOU CAN DO:
✅ Explain exactly why a signal scored the way it did
✅ Break down risk flags (low liquidity, high slippage, coin too new, etc.)
✅ Explain what conviction score, relevance score, and momentum mean
✅ Answer questions about how Zora coins work
✅ Help users understand creator intent signals
✅ Remember user preferences (risk tolerance, favorite creators, etc.)
✅ Explain what the bot's WATCH/ALERT/PAPER_TRADE tiers mean for a specific signal

WHAT YOU MUST NOT DO:
❌ Give open-ended "you should buy X" financial advice outside the signal framework
❌ Discuss stocks, forex, or non-crypto assets
❌ Make predictions about price movements
❌ Act as a general-purpose chatbot for unrelated topics
❌ Encourage taking risks beyond what the signal framework indicates

TONE:
- Direct and analytical
- Explain reasoning clearly
- Flag risks honestly
- Keep responses concise — this is a chat interface, not an essay

If asked about a signal, always reference the bot's own score and tier first, then elaborate."""


async def get_ai_response(
    telegram_user_id: int,
    user_message: str,
    session: Any,
    signal_context: dict | None = None,
    is_premium: bool = False,
) -> str:
    """
    Get a response from Claude Haiku.

    signal_context: optional dict with signal details to inject into context
    Returns the assistant's response text.
    """
    if not settings.anthropic_api_key:
        return (
            "AI chat is not configured. "
            "Add ANTHROPIC_API_KEY to your .env to enable it."
        )

    from app.db.repositories.ai import ChatMessageRepository, UserPreferencesRepository

    msg_repo  = ChatMessageRepository(session)
    pref_repo = UserPreferencesRepository(session)

    # Load conversation history
    limit = PREMIUM_CONTEXT_MESSAGES if is_premium else FREE_CONTEXT_MESSAGES
    history = await msg_repo.get_recent(telegram_user_id, limit=limit)

    # Load user preferences for context
    prefs = await pref_repo.get_all(telegram_user_id)

    # Build messages array
    messages = []

    # Inject preferences as a system context block if any exist
    if prefs:
        pref_text = "User preferences I should remember:\n" + "\n".join(
            f"- {k}: {v}" for k, v in prefs.items()
        )
        messages.append({"role": "user", "content": pref_text})
        messages.append({
            "role": "assistant",
            "content": "Got it, I'll keep those in mind."
        })

    # Add conversation history
    for msg in history:
        messages.append({"role": msg.role, "content": msg.content})

    # Inject signal context if provided
    full_message = user_message
    if signal_context:
        context_block = _format_signal_context(signal_context)
        full_message = f"{context_block}\n\nUser question: {user_message}"

    messages.append({"role": "user", "content": full_message})

    # Call Claude Haiku
    try:
        response_text = await _call_claude(messages)
    except Exception as exc:
        log.error("claude_api_error", error=str(exc))
        return f"AI temporarily unavailable. Try again in a moment."

    # Save both messages to history
    await msg_repo.save_message(
        telegram_user_id=telegram_user_id,
        role="user",
        content=user_message,
        signal_id=signal_context.get("signal_id") if signal_context else None,
        coin_address=signal_context.get("coin_address") if signal_context else None,
    )
    await msg_repo.save_message(
        telegram_user_id=telegram_user_id,
        role="assistant",
        content=response_text,
    )

    # Check if user stated a preference and remember it
    await _maybe_extract_preference(
        user_message, response_text, telegram_user_id, pref_repo
    )

    return response_text


async def _call_claude(messages: list[dict]) -> str:
    """Make the actual Anthropic API call."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            ANTHROPIC_API_URL,
            headers={
                "x-api-key": settings.anthropic_api_key.get_secret_value(),
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": CLAUDE_MODEL,
                "max_tokens": 1024,
                "system": _SYSTEM_PROMPT,
                "messages": messages,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["content"][0]["text"]


def _format_signal_context(ctx: dict) -> str:
    """Format signal context to inject before user's question."""
    lines = ["[Signal Context for AI]"]
    if ctx.get("signal_id"):
        lines.append(f"Signal ID: {ctx['signal_id']}")
    if ctx.get("recommendation"):
        lines.append(f"Bot recommendation: {ctx['recommendation']}")
    if ctx.get("final_score"):
        lines.append(f"Final score: {ctx['final_score']}/100")
    if ctx.get("coin_symbol"):
        lines.append(f"Coin: {ctx['coin_symbol']}")
    if ctx.get("coin_address"):
        lines.append(f"Contract: {ctx['coin_address']}")
    if ctx.get("liquidity_usd"):
        lines.append(f"Liquidity: ${ctx['liquidity_usd']:,.0f}")
    if ctx.get("risk_flags"):
        lines.append(f"Risk flags: {ctx['risk_flags']}")
    if ctx.get("creator_username"):
        lines.append(f"Creator: @{ctx['creator_username']}")
    if ctx.get("post_summary"):
        lines.append(f"Post summary: {ctx['post_summary']}")
    return "\n".join(lines)


async def _maybe_extract_preference(
    user_msg: str,
    assistant_msg: str,
    telegram_user_id: int,
    pref_repo: Any,
) -> None:
    """
    Detect if user stated a preference and save it.
    Simple heuristic — no extra API call needed.
    """
    msg_lower = user_msg.lower()
    prefs_to_save = {}

    if any(w in msg_lower for w in ["risk tolerance", "i'm conservative", "i'm aggressive",
                                     "low risk", "high risk", "medium risk"]):
        if "conservative" in msg_lower or "low risk" in msg_lower:
            prefs_to_save["risk_tolerance"] = "conservative"
        elif "aggressive" in msg_lower or "high risk" in msg_lower:
            prefs_to_save["risk_tolerance"] = "aggressive"
        else:
            prefs_to_save["risk_tolerance"] = "moderate"

    if "only alert me" in msg_lower or "minimum score" in msg_lower:
        import re
        m = re.search(r"(\d+)", user_msg)
        if m:
            prefs_to_save["min_alert_score"] = m.group(1)

    for key, value in prefs_to_save.items():
        await pref_repo.set_preference(telegram_user_id, key, value)


async def check_daily_limit(
    telegram_user_id: int,
    session: Any,
    is_premium: bool,
) -> tuple[bool, int]:
    """
    Check if user has hit their daily message limit.
    Returns (allowed: bool, remaining: int).
    """
    from app.db.repositories.ai import ChatMessageRepository
    repo = ChatMessageRepository(session)
    count_today = await repo.count_today(telegram_user_id)
    limit = PREMIUM_DAILY_LIMIT if is_premium else FREE_DAILY_LIMIT
    remaining = max(0, limit - count_today)
    return remaining > 0, remaining
