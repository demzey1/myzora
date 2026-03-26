"""
app/integrations/x_client.py
─────────────────────────────────────────────────────────────────────────────
X (Twitter) API v2 client.

API reference:
  https://developer.twitter.com/en/docs/twitter-api

Implemented endpoints
  GET /2/users/by/username/:username          → get_user_by_username
  GET /2/users/:id/tweets                     → get_user_recent_tweets
  GET /2/tweets/:id                           → get_tweet_by_id
  GET /2/tweets/search/recent                 → search_recent_tweets
  GET /2/tweets/:id  (metrics refresh)        → get_tweet_metrics

Authentication:
  Bearer Token only (app-only, read access).
  Sufficient for reading public timelines and search.
  OAuth 1.0a is NOT used here — we never post or access private data.

Rate limits (Essential access, as of 2024):
  User timeline: 1 500 requests / 15 min per app
  Tweet lookup:  300 requests / 15 min per app
  Search recent: 450 requests / 15 min per app
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings
from app.integrations.types import XPublicMetrics, XTweet, XUser, XUserPublicMetrics
from app.logging_config import get_logger

log = get_logger(__name__)

_BASE_URL = "https://api.twitter.com/2"

# ── Field sets we request — only documented v2 fields ─────────────────────────
_TWEET_FIELDS = ",".join([
    "id",
    "text",
    "author_id",
    "created_at",
    "lang",
    "public_metrics",
    "conversation_id",
    "in_reply_to_user_id",
])

_USER_FIELDS = ",".join([
    "id",
    "name",
    "username",
    "public_metrics",
    "verified",
    "description",
    "created_at",
])

_TWEET_EXPANSIONS = "author_id"


# ── Parsing helpers ────────────────────────────────────────────────────────────

def _parse_tweet(raw: dict[str, Any]) -> XTweet:
    """
    Translate a raw Twitter v2 tweet dict into XTweet.
    Only maps fields that are documented and explicitly requested.
    """
    pm = raw.get("public_metrics") or {}
    created_raw = raw.get("created_at")
    return XTweet(
        id=raw["id"],
        text=raw["text"],
        author_id=raw.get("author_id", ""),
        created_at=datetime.fromisoformat(created_raw.replace("Z", "+00:00")) if created_raw else None,
        lang=raw.get("lang"),
        public_metrics=XPublicMetrics(
            like_count=pm.get("like_count", 0),
            retweet_count=pm.get("retweet_count", 0),
            reply_count=pm.get("reply_count", 0),
            quote_count=pm.get("quote_count", 0),
            impression_count=pm.get("impression_count"),
            bookmark_count=pm.get("bookmark_count"),
        ),
        conversation_id=raw.get("conversation_id"),
        in_reply_to_user_id=raw.get("in_reply_to_user_id"),
    )


def _parse_user(raw: dict[str, Any]) -> XUser:
    """Translate a raw Twitter v2 user dict into XUser."""
    pm = raw.get("public_metrics") or {}
    created_raw = raw.get("created_at")
    return XUser(
        id=raw["id"],
        name=raw["name"],
        username=raw["username"],
        verified=raw.get("verified", False),
        verified_type=raw.get("verified_type"),
        description=raw.get("description"),
        created_at=datetime.fromisoformat(created_raw.replace("Z", "+00:00")) if created_raw else None,
        public_metrics=XUserPublicMetrics(
            followers_count=pm.get("followers_count", 0),
            following_count=pm.get("following_count", 0),
            tweet_count=pm.get("tweet_count", 0),
            listed_count=pm.get("listed_count", 0),
        ),
    )


# ── Client ────────────────────────────────────────────────────────────────────

class XClient:
    """
    Async HTTP client for Twitter API v2.
    Uses a shared httpx.AsyncClient for connection pooling.
    Retries on transient 5xx errors with exponential back-off.
    """

    def __init__(self, bearer_token: str) -> None:
        self._headers = {
            "Authorization": f"Bearer {bearer_token}",
            "User-Agent": "ZoraSignalBot/0.1",
        }
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=_BASE_URL,
                headers=self._headers,
                timeout=httpx.Timeout(15.0, connect=5.0),
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        """
        Execute a GET request and return the parsed JSON body.
        Raises XAPIError on 4xx / 5xx responses.
        """
        client = await self._get_client()
        log.debug("x_api_request", path=path, params=params)
        response = await client.get(path, params=params)

        if response.status_code == 429:
            reset_at = response.headers.get("x-rate-limit-reset", "unknown")
            raise XRateLimitError(f"Rate limit hit. Reset at unix={reset_at}")

        if response.status_code == 401:
            raise XAuthError("Bearer token invalid or expired")

        if response.status_code >= 400:
            raise XAPIError(
                f"X API error {response.status_code}: {response.text[:200]}"
            )

        body: dict[str, Any] = response.json()

        # The v2 API embeds errors in the body even on 200
        if "errors" in body and "data" not in body:
            raise XAPIError(f"X API body error: {body['errors']}")

        return body

    # ── Public methods ─────────────────────────────────────────────────────────

    async def get_user_by_username(self, username: str) -> XUser | None:
        """
        GET /2/users/by/username/:username
        Returns None if the account does not exist or is suspended.
        """
        username = username.lstrip("@")
        try:
            body = await self._get(
                f"/users/by/username/{username}",
                params={"user.fields": _USER_FIELDS},
            )
        except XAPIError as exc:
            log.warning("x_user_not_found", username=username, error=str(exc))
            return None

        raw = body.get("data")
        if not raw:
            return None
        return _parse_user(raw)

    async def get_user_by_id(self, user_id: str) -> XUser | None:
        """GET /2/users/:id"""
        try:
            body = await self._get(
                f"/users/{user_id}",
                params={"user.fields": _USER_FIELDS},
            )
        except XAPIError as exc:
            log.warning("x_user_id_not_found", user_id=user_id, error=str(exc))
            return None

        raw = body.get("data")
        return _parse_user(raw) if raw else None

    async def get_user_recent_tweets(
        self,
        user_id: str,
        max_results: int = 10,
        since_id: str | None = None,
    ) -> list[XTweet]:
        """
        GET /2/users/:id/tweets
        Returns up to max_results recent tweets, newest first.
        Pass since_id to fetch only tweets newer than the last known one.

        max_results is capped at 100 by the API; we default to 10.
        Excludes retweets and replies to minimise noise.
        """
        params: dict[str, Any] = {
            "tweet.fields": _TWEET_FIELDS,
            "max_results": min(max_results, 100),
            "exclude": "retweets,replies",
        }
        if since_id:
            params["since_id"] = since_id

        try:
            body = await self._get(f"/users/{user_id}/tweets", params=params)
        except XAPIError as exc:
            log.warning("x_timeline_error", user_id=user_id, error=str(exc))
            return []

        raw_list = body.get("data") or []
        tweets = [_parse_tweet(t) for t in raw_list]
        log.debug("x_timeline_fetched", user_id=user_id, count=len(tweets))
        return tweets

    async def get_tweet_by_id(self, tweet_id: str) -> XTweet | None:
        """
        GET /2/tweets/:id
        Used for manual /score lookups and metric refreshes.
        """
        try:
            body = await self._get(
                f"/tweets/{tweet_id}",
                params={"tweet.fields": _TWEET_FIELDS},
            )
        except XAPIError as exc:
            log.warning("x_tweet_not_found", tweet_id=tweet_id, error=str(exc))
            return None

        raw = body.get("data")
        return _parse_tweet(raw) if raw else None

    async def get_tweet_metrics(self, tweet_id: str) -> XPublicMetrics | None:
        """
        Refresh only the public_metrics for a known tweet.
        Thin wrapper over get_tweet_by_id.
        """
        tweet = await self.get_tweet_by_id(tweet_id)
        return tweet.public_metrics if tweet else None

    async def search_recent_tweets(
        self,
        query: str,
        max_results: int = 10,
        since_id: str | None = None,
    ) -> list[XTweet]:
        """
        GET /2/tweets/search/recent
        Searches the last 7 days (Essential access limit).
        query should follow v2 query syntax:
          e.g. 'zora.co lang:en -is:retweet'
        """
        params: dict[str, Any] = {
            "query": query,
            "tweet.fields": _TWEET_FIELDS,
            "expansions": _TWEET_EXPANSIONS,
            "user.fields": _USER_FIELDS,
            "max_results": min(max(10, max_results), 100),
        }
        if since_id:
            params["since_id"] = since_id

        try:
            body = await self._get("/tweets/search/recent", params=params)
        except XAPIError as exc:
            log.warning("x_search_error", query=query, error=str(exc))
            return []

        raw_list = body.get("data") or []
        tweets = [_parse_tweet(t) for t in raw_list]
        log.debug("x_search_results", query=query, count=len(tweets))
        return tweets


# ── Exceptions ────────────────────────────────────────────────────────────────

class XAPIError(Exception):
    """Generic X API error."""


class XRateLimitError(XAPIError):
    """HTTP 429 — rate limit exceeded."""


class XAuthError(XAPIError):
    """HTTP 401 — invalid credentials."""


# ── Singleton factory ──────────────────────────────────────────────────────────

_x_client: XClient | None = None


def get_x_client() -> XClient:
    """
    Return the shared XClient instance.
    Raises RuntimeError if the bearer token is not configured.
    """
    global _x_client
    if _x_client is None:
        if not settings.x_bearer_token:
            raise RuntimeError(
                "X_BEARER_TOKEN is not set. "
                "Configure it in .env to enable X monitoring."
            )
        _x_client = XClient(settings.x_bearer_token.get_secret_value())
    return _x_client
