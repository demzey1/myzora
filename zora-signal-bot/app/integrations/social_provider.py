"""
app/integrations/social_provider.py
─────────────────────────────────────────────────────────────────────────────
Abstract SocialProvider protocol.

Both the official X/Twitter v2 client and the SocialData.tools client
implement this interface. The rest of the app only depends on this protocol —
never on a specific provider class.

Factory:  get_social_provider() returns the configured implementation.
Fallback: if the primary provider fails, the factory logs a warning and
          returns the secondary if available, else raises.
"""

from __future__ import annotations

import abc
from typing import Any

from app.integrations.types import XTweet, XUser, XPublicMetrics
from app.logging_config import get_logger

log = get_logger(__name__)


class SocialProviderError(Exception):
    """Base error for social provider failures."""


class SocialProviderUnavailableError(SocialProviderError):
    """Raised when the provider is not configured or the API is unreachable."""


class SocialProvider(abc.ABC):
    """
    Abstract interface all social data providers must implement.
    All methods return the canonical XUser / XTweet types from types.py.
    """

    @abc.abstractmethod
    async def resolve_profile(self, handle_or_url: str) -> XUser | None:
        """
        Resolve an X handle or profile URL to an XUser.
        Strips leading @, parses /x.com/handle URLs, etc.
        Returns None if the account does not exist.
        """

    @abc.abstractmethod
    async def get_user_by_id(self, user_id: str) -> XUser | None:
        """Fetch an XUser by their platform user_id string."""

    @abc.abstractmethod
    async def get_recent_posts(
        self,
        user: XUser,
        limit: int = 20,
        since_id: str | None = None,
    ) -> list[XTweet]:
        """
        Fetch recent posts for a user.
        since_id: if provided, return only posts newer than this ID.
        """

    @abc.abstractmethod
    async def get_post_metrics(self, post_id: str) -> XPublicMetrics | None:
        """Fetch current engagement metrics for a single post."""

    @abc.abstractmethod
    async def search_posts(self, query: str, limit: int = 20) -> list[XTweet]:
        """
        Search recent posts by keyword query.
        Some providers may not support this — return [] gracefully.
        """

    # ── Convenience helpers (shared, non-abstract) ─────────────────────────

    @staticmethod
    def normalise_handle(handle_or_url: str) -> str:
        """
        Convert any of the following to a bare lowercase handle:
          @handle  →  handle
          https://x.com/handle  →  handle
          https://twitter.com/handle  →  handle
        """
        import re
        h = handle_or_url.strip()
        # URL patterns
        m = re.search(r"(?:twitter\.com|x\.com)/([A-Za-z0-9_]{1,50})", h)
        if m:
            return m.group(1).lower()
        return h.lstrip("@").lower()


# ── Factory ────────────────────────────────────────────────────────────────────

_provider: SocialProvider | None = None


def get_social_provider() -> SocialProvider:
    """
    Return the configured social provider singleton.
    Reads settings.social_provider and instantiates accordingly.
    """
    global _provider
    if _provider is not None:
        return _provider

    from app.config import settings

    chosen = settings.social_provider.lower()

    if chosen == "socialdata":
        from app.integrations.socialdata_client import SocialDataClient
        if not settings.socialdata_api_key:
            log.warning("socialdata_key_missing — falling back to x_api")
            chosen = "x_api"
        else:
            _provider = SocialDataClient(
                api_key=settings.socialdata_api_key.get_secret_value(),
                base_url=settings.socialdata_base_url,
            )
            log.info("social_provider_initialised", provider="socialdata")
            return _provider

    if chosen == "x_api":
        from app.integrations.x_client import get_x_client
        try:
            _provider = XApiSocialProvider(get_x_client())
            log.info("social_provider_initialised", provider="x_api")
            return _provider
        except RuntimeError as exc:
            raise SocialProviderUnavailableError(
                "No social provider configured. "
                "Set SOCIALDATA_API_KEY or X_BEARER_TOKEN."
            ) from exc

    raise SocialProviderUnavailableError(
        f"Unknown SOCIAL_PROVIDER={chosen!r}. Use 'socialdata' or 'x_api'."
    )


def reset_social_provider() -> None:
    """Force re-initialisation (used in tests)."""
    global _provider
    _provider = None


# ── X API adapter (wraps existing XClient) ────────────────────────────────────

class XApiSocialProvider(SocialProvider):
    """Thin adapter that makes XClient satisfy SocialProvider protocol."""

    def __init__(self, client: Any) -> None:
        self._client = client

    async def resolve_profile(self, handle_or_url: str) -> XUser | None:
        handle = self.normalise_handle(handle_or_url)
        return await self._client.get_user_by_username(handle)

    async def get_user_by_id(self, user_id: str) -> XUser | None:
        return await self._client.get_user_by_id(user_id)

    async def get_recent_posts(
        self, user: XUser, limit: int = 20, since_id: str | None = None
    ) -> list[XTweet]:
        return await self._client.get_user_recent_tweets(
            user_id=user.id, max_results=min(limit, 100), since_id=since_id
        )

    async def get_post_metrics(self, post_id: str) -> XPublicMetrics | None:
        return await self._client.get_tweet_metrics(post_id)

    async def search_posts(self, query: str, limit: int = 20) -> list[XTweet]:
        return await self._client.search_recent_tweets(query, max_results=limit)
