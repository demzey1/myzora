from __future__ import annotations

from datetime import datetime, timedelta
import inspect

from sqlalchemy import select

from app.config import settings
from app.db.base import AsyncSessionLocal
from app.db.models import ConversationSession
from app.integrations.openai_responses_client import OpenAIResponsesClient
from app.logging_config import get_logger

log = get_logger(__name__)

_openai_client: OpenAIResponsesClient | None = None
_openai_assistant_id: str | None = None


async def _resolve(value):
    if inspect.isawaitable(value):
        return await value
    return value


async def init_openai_client() -> tuple[OpenAIResponsesClient, str]:
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

    _openai_assistant_id = await _openai_client.create_assistant(
        name="Zora Signal Bot Assistant",
        instructions=await _openai_client.get_system_prompt(),
    )

    log.info(f"openai_client_initialized assistant_id={_openai_assistant_id}")
    return _openai_client, _openai_assistant_id


async def get_openai_client() -> OpenAIResponsesClient:
    global _openai_client
    if _openai_client is None:
        raise RuntimeError("OpenAI client not initialized. Call init_openai_client() first.")
    return _openai_client


async def get_assistant_id() -> str:
    global _openai_assistant_id
    if _openai_assistant_id is None:
        raise RuntimeError("Assistant ID not initialized. Call init_openai_client() first.")
    return _openai_assistant_id


async def get_or_create_conversation_session(telegram_user_id: int) -> tuple[str, str]:
    client = await get_openai_client()
    assistant_id = await get_assistant_id()

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ConversationSession).where(ConversationSession.telegram_user_id == telegram_user_id)
        )
        existing = await _resolve(result.scalar_one_or_none())
        if existing:
            existing.last_message_at = datetime.utcnow()
            await session.commit()
            return existing.openai_thread_id, existing.openai_assistant_id

    thread_id = await client.create_thread()

    async with AsyncSessionLocal() as session:
        conv_session = ConversationSession(
            telegram_user_id=telegram_user_id,
            openai_thread_id=thread_id,
            openai_assistant_id=assistant_id,
            last_message_at=datetime.utcnow(),
        )
        session.add(conv_session)
        await session.commit()

    return thread_id, assistant_id


async def update_conversation_timestamp(telegram_user_id: int) -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ConversationSession).where(ConversationSession.telegram_user_id == telegram_user_id)
        )
        session_obj = await _resolve(result.scalar_one_or_none())
        if session_obj:
            session_obj.last_message_at = datetime.utcnow()
            await session.commit()


async def cleanup_stale_sessions(timeout_minutes: int | None = None) -> int:
    if timeout_minutes is None:
        timeout_minutes = settings.conversation_timeout_minutes

    threshold = datetime.utcnow() - timedelta(minutes=timeout_minutes)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ConversationSession).where(ConversationSession.last_message_at < threshold)
        )
        scalars = await _resolve(result.scalars())
        stale_sessions = await _resolve(scalars.all())

        for conv_session in stale_sessions:
            delete_result = session.delete(conv_session)
            if inspect.isawaitable(delete_result):
                await delete_result

        await session.commit()

    return len(stale_sessions)


async def close_openai_client() -> None:
    global _openai_client
    if _openai_client:
        if hasattr(_openai_client, "aclose"):
            await _openai_client.aclose()  # type: ignore[attr-defined]
        else:
            await _openai_client.close()
        _openai_client = None

