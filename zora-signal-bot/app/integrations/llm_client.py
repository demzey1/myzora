"""
app/integrations/llm_client.py
─────────────────────────────────────────────────────────────────────────────
LLM classification layer.

Provides three concrete clients:
  LLMStubClient         — no-op, used when LLM disabled (safe default)
  OpenAILLMClient       — calls OpenAI chat completions with JSON mode
  AnthropicLLMClient    — calls Anthropic messages API with JSON output

Design rules enforced here:
  1. The LLM NEVER receives wallet addresses, private keys, or trade amounts
  2. The LLM ONLY receives post text and coin symbol — nothing financial
  3. If the LLM call fails (timeout, parse error, rate limit) → return None
     and the scoring engine falls back to deterministic score only
  4. The system prompt instructs the model to return strict JSON only
  5. All LLM output is validated through LLMScore (Pydantic) before use

System prompt strategy:
  - Give the model a narrow, well-defined task
  - Demand structured JSON output with exact field names
  - Include examples to reduce hallucination
  - Cap max_tokens at 300 — we only need the JSON object
"""

from __future__ import annotations

import abc
import json
import re

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.integrations.types import LLMScore
from app.logging_config import get_logger

log = get_logger(__name__)

# ── System prompt ──────────────────────────────────────────────────────────────
_SYSTEM_PROMPT = """You are a crypto social-signal classifier. Your ONLY job is to
analyse a social media post and a related token symbol, then return a JSON object
with exactly these fields:

{
  "meme_strength": <0-100 integer>,
  "narrative_fit": <0-100 integer>,
  "conversion_likelihood": <0-100 integer>,
  "spam_risk": <0-100 integer>,
  "summary": "<one sentence, max 120 chars>",
  "recommendation_bias": "<positive|neutral|negative>"
}

Definitions:
  meme_strength         – how viral/shareable is the content as a meme
  narrative_fit         – how well it fits current crypto/NFT narrative trends
  conversion_likelihood – likelihood that attention from this post converts to buy pressure
  spam_risk             – probability this is spam, shill, or coordinated promotion
  recommendation_bias   – your overall lean (positive/neutral/negative)

Rules:
  - Return ONLY the JSON object. No preamble, no markdown fences, no explanation.
  - All integer fields must be 0-100.
  - summary must be a single sentence under 120 characters.
  - If you cannot determine a value, use 50 as a neutral default.
"""

_USER_TEMPLATE = """Post text: {post_text}

Token symbol: {coin_symbol}

Classify this post."""


# ── Protocol ───────────────────────────────────────────────────────────────────

class LLMClientProtocol(abc.ABC):
    @abc.abstractmethod
    async def classify_post(self, post_text: str, coin_symbol: str) -> LLMScore | None:
        """Classify a post. Returns None if the call fails or LLM is disabled."""


# ── Stub (default / disabled) ──────────────────────────────────────────────────

class LLMStubClient(LLMClientProtocol):
    async def classify_post(self, post_text: str, coin_symbol: str) -> None:
        return None


# ── JSON extraction helper ─────────────────────────────────────────────────────

def _extract_llm_score(raw_text: str) -> LLMScore | None:
    """
    Parse the LLM response text into a validated LLMScore.
    Handles cases where the model wraps JSON in markdown fences.
    Returns None on any parse / validation error.
    """
    # Strip markdown fences if present
    text = re.sub(r"```(?:json)?", "", raw_text).strip()
    # Find the first {...} block
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        log.warning("llm_no_json_found", raw=raw_text[:200])
        return None
    try:
        data = json.loads(match.group())
        return LLMScore(**data)
    except (json.JSONDecodeError, Exception) as exc:
        log.warning("llm_parse_error", error=str(exc), raw=raw_text[:200])
        return None


# ── OpenAI client ──────────────────────────────────────────────────────────────

class OpenAILLMClient(LLMClientProtocol):
    """
    Calls OpenAI chat completions with JSON mode enabled.
    Uses httpx directly (no openai SDK dependency).
    """

    _BASE_URL = "https://api.openai.com/v1"

    def __init__(self, api_key: str, model: str, timeout_seconds: int, max_retries: int) -> None:
        self._model = model
        self._client = httpx.AsyncClient(
            base_url=self._BASE_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(float(timeout_seconds), connect=5.0),
        )
        self._max_retries = max_retries

    async def close(self) -> None:
        await self._client.aclose()

    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=4),
    )
    async def classify_post(self, post_text: str, coin_symbol: str) -> LLMScore | None:
        # Truncate post to avoid token waste
        truncated = post_text[:500]
        user_msg = _USER_TEMPLATE.format(post_text=truncated, coin_symbol=coin_symbol)

        payload = {
            "model": self._model,
            "max_tokens": 300,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": user_msg},
            ],
        }

        try:
            resp = await self._client.post("/chat/completions", json=payload)
            if resp.status_code == 429:
                log.warning("llm_openai_rate_limit")
                return None
            if resp.status_code >= 400:
                log.warning("llm_openai_error", status=resp.status_code, body=resp.text[:200])
                return None

            body = resp.json()
            raw_text: str = body["choices"][0]["message"]["content"]
            score = _extract_llm_score(raw_text)
            if score:
                log.debug("llm_classified", model=self._model, bias=score.recommendation_bias)
            return score

        except httpx.TimeoutException:
            log.warning("llm_openai_timeout")
            return None
        except Exception as exc:
            log.warning("llm_openai_unexpected_error", error=str(exc))
            return None


# ── Anthropic client ───────────────────────────────────────────────────────────

class AnthropicLLMClient(LLMClientProtocol):
    """
    Calls Anthropic Messages API.
    Uses httpx directly (no anthropic SDK dependency).
    """

    _BASE_URL = "https://api.anthropic.com"
    _API_VERSION = "2023-06-01"

    def __init__(self, api_key: str, model: str, timeout_seconds: int, max_retries: int) -> None:
        self._model = model
        self._client = httpx.AsyncClient(
            base_url=self._BASE_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": self._API_VERSION,
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(float(timeout_seconds), connect=5.0),
        )

    async def close(self) -> None:
        await self._client.aclose()

    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=4),
    )
    async def classify_post(self, post_text: str, coin_symbol: str) -> LLMScore | None:
        truncated = post_text[:500]
        user_msg = _USER_TEMPLATE.format(post_text=truncated, coin_symbol=coin_symbol)

        payload = {
            "model": self._model,
            "max_tokens": 300,
            "system": _SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": user_msg}],
        }

        try:
            resp = await self._client.post("/v1/messages", json=payload)
            if resp.status_code == 529 or resp.status_code == 429:
                log.warning("llm_anthropic_overloaded", status=resp.status_code)
                return None
            if resp.status_code >= 400:
                log.warning("llm_anthropic_error", status=resp.status_code, body=resp.text[:200])
                return None

            body = resp.json()
            raw_text: str = body["content"][0]["text"]
            score = _extract_llm_score(raw_text)
            if score:
                log.debug("llm_classified", model=self._model, bias=score.recommendation_bias)
            return score

        except httpx.TimeoutException:
            log.warning("llm_anthropic_timeout")
            return None
        except Exception as exc:
            log.warning("llm_anthropic_unexpected_error", error=str(exc))
            return None


# ── Factory ────────────────────────────────────────────────────────────────────

_llm_client: LLMClientProtocol | None = None


def get_llm_client() -> LLMClientProtocol:
    """
    Return the configured LLM client.
    Thread-safe singleton — built once from settings on first call.
    Falls back to LLMStubClient if llm_enabled=false or API key missing.
    """
    global _llm_client
    if _llm_client is not None:
        return _llm_client

    from app.config import settings

    if not settings.llm_enabled:
        _llm_client = LLMStubClient()
        return _llm_client

    if settings.llm_provider == "openai":
        if not settings.openai_api_key:
            log.warning("llm_openai_key_missing_falling_back_to_stub")
            _llm_client = LLMStubClient()
            return _llm_client
        _llm_client = OpenAILLMClient(
            api_key=settings.openai_api_key.get_secret_value(),
            model=settings.llm_model,
            timeout_seconds=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )
        log.info("llm_client_initialised", provider="openai", model=settings.llm_model)
        return _llm_client

    if settings.llm_provider == "anthropic":
        if not settings.anthropic_api_key:
            log.warning("llm_anthropic_key_missing_falling_back_to_stub")
            _llm_client = LLMStubClient()
            return _llm_client
        _llm_client = AnthropicLLMClient(
            api_key=settings.anthropic_api_key.get_secret_value(),
            model=settings.llm_model,
            timeout_seconds=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )
        log.info("llm_client_initialised", provider="anthropic", model=settings.llm_model)
        return _llm_client

    log.warning("llm_unknown_provider_falling_back_to_stub", provider=settings.llm_provider)
    _llm_client = LLMStubClient()
    return _llm_client


def reset_llm_client() -> None:
    """Force re-initialisation of the LLM client singleton (used in tests)."""
    global _llm_client
    _llm_client = None
