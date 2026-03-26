"""
app/bot/conversation_store.py
─────────────────────────────────────────────────────────────────────────────
Manager for per-user conversation sessions with OpenAI.

This module:
  1. Maintains a singleton OpenAI client for all users
  2. Creates/retrieves per-user conversation threads
  3. Persists conversation metadata to the database
  4. Handles session timeout and cleanup

Design:
  - One OpenAI assistant per bot (created at startup)
  - One thread per Telegram user (created on first message)
  - Thread metadata stored in ConversationSession table
  - Thread IDs cached in Redis for fast lookup
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from app.config import settings
from app.db.base import AsyncSessionLocal
from app.db.models import ConversationSession
from app.integrations.openai_responses_client import OpenAIResponsesClient
from sqlalchemy import select

log = logging.getLogger(__name__)


# ── Singleton OpenAI client ────────────────────────────────────────────────────

_openai_client: OpenAIResponsesClient | None = None
_openai_assistant_id: str | None = None


async def init_openai_client() -> tuple[OpenAIResponsesClient, str]:
    """
    Initialize the OpenAI client and create/retrieve the assistant.
    Called once at bot startup.

    Returns:
        (client, assistant_id)
    """
    global _openai_client, _openai_assistant_id

    if _openai_client is not None and _openai_assistant_id is not None:
        return _openai_client, _openai_assistant_id

    if not settings.openai_api_key:
        raise ValueError("OPENAI_API_KEY is required for conversational mode")

    _openai_client = OpenAIResponsesClient(
        api_key=settings.openai_api_key.get_secret_value(),
        api_base=settings.openai_api_base,
        model=settings.openai_responses_model,
    )

    # Create the assistant (idempotent — we'd need to track this per-deployment)
    # For now, create fresh each time; in production, store the ID in config
    _openai_assistant_id = await _openai_client.create_assistant(
        name="Zora Signal Bot Assistant",
        instructions=_openai_client.get_system_prompt(),
    )

    log.info("openai_client_initialized", assistant_id=_openai_assistant_id)
    return _openai_client, _openai_assistant_id


async def get_openai_client() -> OpenAIResponsesClient:
    """Get the singleton OpenAI client."""
    global _openai_client
    if _openai_client is None:
        raise RuntimeError("OpenAI client not initialized. Call init_openai_client() first.")
    return _openai_client


async def get_assistant_id() -> str:
    """Get the singleton assistant ID."""
    global _openai_assistant_id
    if _openai_assistant_id is None:
        raise RuntimeError("Assistant ID not initialized. Call init_openai_client() first.")
    return _openai_assistant_id


# ── Conversation session management ────────────────────────────────────────────

async def get_or_create_conversation_session(
    telegram_user_id: int,
) -> tuple[str, str]:
    """
    Get or create a conversation session for a user.

    Returns:
        (thread_id, assistant_id)

    Raises:
        RuntimeError if OpenAI client not initialized
    """
    client = await get_openai_client()
    assistant_id = await get_assistant_id()

    # Check database for existing session
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ConversationSession).where(
                ConversationSession.telegram_user_id == telegram_user_id
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            # Update last_message_at
            existing.last_message_at = datetime.utcnow()
            await session.commit()
            log.debug(
                "conversation_session_retrieved",
                telegram_user_id=telegram_user_id,
                thread_id=existing.openai_thread_id,
            )
            return existing.openai_thread_id, existing.openai_assistant_id

    # Create new thread
    thread_id = await client.create_thread()

    # Persist to database
    async with AsyncSessionLocal() as session:
        conv_session = ConversationSession(
            telegram_user_id=telegram_user_id,
            openai_thread_id=thread_id,
            openai_assistant_id=assistant_id,
            last_message_at=datetime.utcnow(),
        )
        session.add(conv_session)
        await session.commit()

    log.info(
        "conversation_session_created",
        telegram_user_id=telegram_user_id,
        thread_id=thread_id,
    )
    return thread_id, assistant_id


async def update_conversation_timestamp(telegram_user_id: int) -> None:
    """Update the last_message_at timestamp for a session (for timeout tracking)."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ConversationSession).where(
                ConversationSession.telegram_user_id == telegram_user_id
            )
        )
        session_obj = result.scalar_one_or_none()
        if session_obj:
            session_obj.last_message_at = datetime.utcnow()
            await session.commit()


async def cleanup_stale_sessions(
    timeout_minutes: int | None = None,
) -> int:
    """
    Clean up conversation sessions that have been idle longer than timeout.

    Args:
        timeout_minutes: Override settings.conversation_timeout_minutes

    Returns:
        Number of sessions cleaned up
    """
    if timeout_minutes is None:
        timeout_minutes = settings.conversation_timeout_minutes

    threshold = datetime.utcnow() - timedelta(minutes=timeout_minutes)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ConversationSession).where(
                ConversationSession.last_message_at < threshold
            )
        )
        stale_sessions = result.scalars().all()

        for conv_session in stale_sessions:
            await session.delete(conv_session)
            log.info(
                "conversation_session_cleaned",
                telegram_user_id=conv_session.telegram_user_id,
                thread_id=conv_session.openai_thread_id,
            )

        await session.commit()

    return len(stale_sessions)


async def close_openai_client() -> None:
    """Close the OpenAI client (call at shutdown)."""
    global _openai_client
    if _openai_client:
        await _openai_client.close()
        _openai_client = None
    log.info("openai_client_closed")
