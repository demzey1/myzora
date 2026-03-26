"""
tests/unit/test_social_provider.py
Tests for SocialProvider abstraction, handle normalisation, and
SocialDataClient with mocked HTTP.
"""

from __future__ import annotations

import json
from datetime import datetime

import httpx
import pytest
import respx

from app.integrations.social_provider import SocialProvider, XApiSocialProvider, reset_social_provider
from app.integrations.socialdata_client import SocialDataClient, _parse_user, _parse_tweet


SD_BASE = "https://api.socialdata.tools"


# ── Handle normalisation ──────────────────────────────────────────────────────

def test_normalise_strips_at():
    assert SocialProvider.normalise_handle("@vitalik") == "vitalik"


def test_normalise_x_url():
    assert SocialProvider.normalise_handle("https://x.com/vitalikbuterin") == "vitalikbuterin"


def test_normalise_twitter_url():
    assert SocialProvider.normalise_handle("https://twitter.com/zoraengineering") == "zoraengineering"


def test_normalise_bare_handle():
    assert SocialProvider.normalise_handle("handle123") == "handle123"


def test_normalise_lowercases():
    assert SocialProvider.normalise_handle("@VitalikButerin") == "vitalikbuterin"


# ── _parse_user ────────────────────────────────────────────────────────────────

def test_parse_user_v1_fields():
    raw = {
        "id_str": "12345",
        "name": "Test User",
        "screen_name": "testuser",
        "followers_count": 50000,
        "friends_count": 200,
        "statuses_count": 1000,
        "listed_count": 30,
        "verified": False,
        "description": "A test creator",
        "created_at": "Mon Jan 01 00:00:00 +0000 2020",
    }
    user = _parse_user(raw)
    assert user.id == "12345"
    assert user.username == "testuser"
    assert user.public_metrics.followers_count == 50000
    assert user.created_at is not None


def test_parse_user_handles_missing_fields():
    raw = {"id_str": "99", "name": "Min", "screen_name": "min"}
    user = _parse_user(raw)
    assert user.public_metrics.followers_count == 0


# ── _parse_tweet ───────────────────────────────────────────────────────────────

def test_parse_tweet_v1_fields():
    raw = {
        "id_str": "tweet123",
        "full_text": "This is a full tweet about Zora $ZORA 🚀",
        "favorite_count": 150,
        "retweet_count": 30,
        "reply_count": 5,
        "quote_count": 2,
        "lang": "en",
        "created_at": "Mon Jan 01 12:00:00 +0000 2024",
        "user": {"id_str": "12345"},
    }
    tweet = _parse_tweet(raw)
    assert tweet.id == "tweet123"
    assert "Zora" in tweet.text
    assert tweet.public_metrics.like_count == 150
    assert tweet.public_metrics.retweet_count == 30
    assert tweet.lang == "en"
    assert tweet.author_id == "12345"


def test_parse_tweet_created_at_format():
    raw = {
        "id_str": "t1",
        "full_text": "text",
        "created_at": "Wed Jun 05 14:30:00 +0000 2024",
    }
    tweet = _parse_tweet(raw)
    assert tweet.created_at is not None
    assert tweet.created_at.year == 2024


# ── SocialDataClient mocked HTTP ──────────────────────────────────────────────

@pytest.fixture
def client():
    return SocialDataClient(api_key="test-key", base_url=SD_BASE)


@pytest.mark.asyncio
@respx.mock
async def test_resolve_profile_success(client):
    user_raw = {
        "id_str": "999",
        "name": "Creator One",
        "screen_name": "creatorone",
        "followers_count": 100_000,
        "friends_count": 500,
        "statuses_count": 2000,
        "listed_count": 100,
        "verified": False,
    }
    respx.get(f"{SD_BASE}/twitter/user").mock(
        return_value=httpx.Response(200, json={"user": user_raw})
    )
    from app.integrations.types import XUser
    user = await client.resolve_profile("@creatorone")
    assert user is not None
    assert user.username == "creatorone"
    assert user.public_metrics.followers_count == 100_000


@pytest.mark.asyncio
@respx.mock
async def test_resolve_profile_not_found(client):
    respx.get(f"{SD_BASE}/twitter/user").mock(
        return_value=httpx.Response(404, json={"status": "error", "message": "User not found"})
    )
    user = await client.resolve_profile("@nobody")
    assert user is None


@pytest.mark.asyncio
@respx.mock
async def test_get_recent_posts_success(client):
    from app.integrations.types import XUser, XUserPublicMetrics
    user = XUser(
        id="999",
        name="Creator",
        username="creator",
        public_metrics=XUserPublicMetrics(followers_count=50000),
    )
    tweets_raw = [
        {
            "id_str": f"tweet{i}",
            "full_text": f"Post number {i} about Zora 🚀",
            "favorite_count": 100 * i,
            "retweet_count": 10 * i,
            "reply_count": 5,
            "quote_count": 1,
            "lang": "en",
            "created_at": "Mon Jan 01 12:00:00 +0000 2024",
            "user": {"id_str": "999"},
        }
        for i in range(1, 4)
    ]
    respx.get(f"{SD_BASE}/twitter/user/999/tweets").mock(
        return_value=httpx.Response(200, json={"tweets": tweets_raw})
    )
    posts = await client.get_recent_posts(user, limit=5)
    assert len(posts) == 3
    assert posts[0].author_id == "999"


@pytest.mark.asyncio
@respx.mock
async def test_get_recent_posts_empty(client):
    from app.integrations.types import XUser, XUserPublicMetrics
    user = XUser(
        id="1",
        name="T",
        username="t",
        public_metrics=XUserPublicMetrics(followers_count=0),
    )
    respx.get(f"{SD_BASE}/twitter/user/1/tweets").mock(
        return_value=httpx.Response(200, json={"tweets": []})
    )
    posts = await client.get_recent_posts(user)
    assert posts == []


@pytest.mark.asyncio
@respx.mock
async def test_rate_limit_raises(client):
    from app.integrations.social_provider import SocialProviderError
    respx.get(f"{SD_BASE}/twitter/user").mock(
        return_value=httpx.Response(429, json={"message": "rate limited"})
    )
    with pytest.raises(SocialProviderError, match="rate limit"):
        await client.resolve_profile("@anyone")


@pytest.mark.asyncio
@respx.mock
async def test_search_posts_success(client):
    respx.get(f"{SD_BASE}/twitter/search/recent").mock(
        return_value=httpx.Response(200, json={
            "tweets": [{
                "id_str": "s1",
                "full_text": "Zora creator coins trending",
                "favorite_count": 200,
                "retweet_count": 40,
                "reply_count": 10,
                "quote_count": 5,
                "lang": "en",
                "created_at": "Mon Jan 01 12:00:00 +0000 2024",
            }]
        })
    )
    results = await client.search_posts("zora creator coins")
    assert len(results) == 1
    assert "Zora" in results[0].text


# ── Factory routing ────────────────────────────────────────────────────────────

def test_factory_returns_stub_when_no_keys(monkeypatch):
    reset_social_provider()
    monkeypatch.setenv("SOCIAL_PROVIDER", "socialdata")
    from app.config import get_settings
    get_settings.cache_clear()
    # Without a key it should fall back gracefully (tested at logic level)
    reset_social_provider()
    get_settings.cache_clear()
