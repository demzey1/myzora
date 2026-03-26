"""
app/integrations/socialdata_client.py
─────────────────────────────────────────────────────────────────────────────
SocialData.tools API client.

SocialData provides Twitter/X data without requiring Twitter API approval.
It mirrors the Twitter v1.1 response shape.

Base URL:    https://api.socialdata.tools
Auth header: Authorization: Bearer YOUR_API_KEY
Docs:        https://socialdata.tools/docs

Endpoints used:
  GET /twitter/user?username={handle}       → user profile by handle
  GET /twitter/user/{id}                    → user profile by ID
  GET /twitter/user/{id}/tweets             → user timeline
  GET /twitter/tweet/{id}                   → single tweet
  GET /twitter/search/recent?query={q}      → search tweets

Rate limits: ~500 req/day on free tier; higher on paid.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.integrations.social_provider import SocialProvider, SocialProviderError
from app.integrations.types import XPublicMetrics, XTweet, XUser, XUserPublicMetrics
from app.logging_config import get_logger

log = get_logger(__name__)


# ── Parsing helpers ────────────────────────────────────────────────────────────

def _parse_user(raw: dict[str, Any]) -> XUser:
    """
    Map a SocialData user object to XUser.
    SocialData mirrors the Twitter v1.1 user shape.
    """
    pm_raw = raw.get("public_metrics") or {}
    # SocialData v1.1 shape uses followers_count directly
    followers = (
        pm_raw.get("followers_count")
        or raw.get("followers_count")
        or 0
    )
    following = (
        pm_raw.get("following_count")
        or raw.get("friends_count")
        or 0
    )
    tweet_count = (
        pm_raw.get("tweet_count")
        or raw.get("statuses_count")
        or 0
    )
    listed = pm_raw.get("listed_count") or raw.get("listed_count") or 0

    created_raw = raw.get("created_at")
    created_at: datetime | None = None
    if created_raw:
        try:
            # SocialData returns Twitter v1.1 date format: "Mon Jan 01 00:00:00 +0000 2020"
            created_at = datetime.strptime(created_raw, "%a %b %d %H:%M:%S %z %Y")
        except ValueError:
            try:
                created_at = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
            except ValueError:
                pass

    return XUser(
        id=str(raw.get("id") or raw.get("id_str") or ""),
        name=raw.get("name") or "",
        username=raw.get("screen_name") or raw.get("username") or "",
        verified=raw.get("verified") or raw.get("is_blue_verified") or False,
        description=raw.get("description"),
        created_at=created_at,
        public_metrics=XUserPublicMetrics(
            followers_count=int(followers),
            following_count=int(following),
            tweet_count=int(tweet_count),
            listed_count=int(listed),
        ),
    )


def _parse_tweet(raw: dict[str, Any], author_id: str = "") -> XTweet:
    """
    Map a SocialData tweet object to XTweet.
    SocialData v1.1 shape: 'favorite_count', 'retweet_count', etc.
    """
    created_raw = raw.get("created_at")
    created_at: datetime | None = None
    if created_raw:
        try:
            created_at = datetime.strptime(created_raw, "%a %b %d %H:%M:%S %z %Y")
        except ValueError:
            try:
                created_at = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
            except ValueError:
                pass

    # Author ID — may be embedded in user sub-object
    aid = author_id or str(
        raw.get("user", {}).get("id_str")
        or raw.get("user", {}).get("id")
        or raw.get("author_id")
        or ""
    )

    tweet_id = str(raw.get("id_str") or raw.get("id") or "")

    return XTweet(
        id=tweet_id,
        text=raw.get("full_text") or raw.get("text") or "",
        author_id=aid,
        created_at=created_at,
        lang=raw.get("lang"),
        public_metrics=XPublicMetrics(
            like_count=int(raw.get("favorite_count") or 0),
            retweet_count=int(raw.get("retweet_count") or 0),
            reply_count=int(raw.get("reply_count") or 0),
            quote_count=int(raw.get("quote_count") or 0),
            impression_count=raw.get("views_count") or raw.get("impression_count"),
        ),
        conversation_id=str(raw.get("conversation_id") or ""),
        in_reply_to_user_id=str(raw.get("in_reply_to_user_id_str") or ""),
    )


# ── Client ────────────────────────────────────────────────────────────────────

class SocialDataClient(SocialProvider):
    """
    Async HTTP client for the SocialData.tools API.
    Normalises all responses to canonical XUser / XTweet types.
    """

    def __init__(self, api_key: str, base_url: str = "https://api.socialdata.tools") -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(15.0, connect=5.0),
        )

    async def close(self) -> None:
        await self._client.aclose()

    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=8),
    )
    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        log.debug("socialdata_request", path=path)
        try:
            resp = await self._client.get(path, params=params or {})
        except httpx.TransportError as exc:
            log.warning("socialdata_transport_error", error=str(exc))
            raise

        if resp.status_code == 429:
            raise SocialProviderError("SocialData rate limit hit (429)")
        if resp.status_code == 401:
            raise SocialProviderError("SocialData auth failed — check SOCIALDATA_API_KEY")
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise SocialProviderError(
                f"SocialData error {resp.status_code}: {resp.text[:200]}"
            )

        body = resp.json()
        # SocialData wraps errors in {"status": "error", "message": "..."}
        if isinstance(body, dict) and body.get("status") == "error":
            log.warning("socialdata_api_error", message=body.get("message"))
            return None
        return body

    # ── SocialProvider interface ───────────────────────────────────────────────

    async def resolve_profile(self, handle_or_url: str) -> XUser | None:
        handle = self.normalise_handle(handle_or_url)
        raw = await self._get("/twitter/user", params={"username": handle})
        if not raw:
            return None
        # SocialData may return the user directly or wrapped in {"user": {...}}
        user_raw = raw.get("user") if isinstance(raw, dict) and "user" in raw else raw
        if not user_raw or not user_raw.get("id_str"):
            return None
        return _parse_user(user_raw)

    async def get_user_by_id(self, user_id: str) -> XUser | None:
        raw = await self._get(f"/twitter/user/{user_id}")
        if not raw:
            return None
        user_raw = raw.get("user") if isinstance(raw, dict) and "user" in raw else raw
        return _parse_user(user_raw) if user_raw else None

    async def get_recent_posts(
        self,
        user: XUser,
        limit: int = 20,
        since_id: str | None = None,
    ) -> list[XTweet]:
        params: dict[str, Any] = {"count": min(limit, 200), "tweet_mode": "extended"}
        if since_id:
            params["since_id"] = since_id

        raw = await self._get(f"/twitter/user/{user.id}/tweets", params=params)
        if not raw:
            return []

        # Response shape: {"tweets": [...]} or bare list
        tweets_raw = raw if isinstance(raw, list) else raw.get("tweets") or []
        result = [_parse_tweet(t, author_id=user.id) for t in tweets_raw]
        log.debug("socialdata_timeline_fetched", username=user.username, count=len(result))
        return result

    async def get_post_metrics(self, post_id: str) -> XPublicMetrics | None:
        raw = await self._get(f"/twitter/tweet/{post_id}", params={"tweet_mode": "extended"})
        if not raw:
            return None
        tweet_raw = raw.get("tweet") if isinstance(raw, dict) and "tweet" in raw else raw
        if not tweet_raw:
            return None
        t = _parse_tweet(tweet_raw)
        return t.public_metrics

    async def search_posts(self, query: str, limit: int = 20) -> list[XTweet]:
        params: dict[str, Any] = {
            "query": query,
            "count": min(limit, 100),
            "tweet_mode": "extended",
        }
        raw = await self._get("/twitter/search/recent", params=params)
        if not raw:
            return []

        # Shape: {"tweets": [...]} or {"statuses": [...]}
        tweets_raw = (
            raw.get("tweets")
            or raw.get("statuses")
            or (raw if isinstance(raw, list) else [])
        )
        return [_parse_tweet(t) for t in tweets_raw]
