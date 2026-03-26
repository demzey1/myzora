"""
tests/unit/test_x_client.py
Tests for the X API client using respx to mock HTTP calls.
Only uses documented Twitter v2 field names.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest
import respx

from app.integrations.x_client import XClient, XAPIError, XRateLimitError

BASE = "https://api.twitter.com/2"


@pytest.fixture
def client():
    return XClient(bearer_token="test-bearer-token")


# ── get_user_by_username ──────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_get_user_by_username_success(client):
    respx.get(f"{BASE}/users/by/username/testuser").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "id": "123456",
                    "name": "Test User",
                    "username": "testuser",
                    "verified": False,
                    "description": "A test account",
                    "created_at": "2020-01-01T00:00:00Z",
                    "public_metrics": {
                        "followers_count": 50000,
                        "following_count": 100,
                        "tweet_count": 1000,
                        "listed_count": 50,
                    },
                }
            },
        )
    )
    user = await client.get_user_by_username("testuser")
    assert user is not None
    assert user.id == "123456"
    assert user.username == "testuser"
    assert user.public_metrics.followers_count == 50000
    assert user.verified is False


@pytest.mark.asyncio
@respx.mock
async def test_get_user_by_username_strips_at(client):
    respx.get(f"{BASE}/users/by/username/testuser").mock(
        return_value=httpx.Response(200, json={"data": {
            "id": "1", "name": "T", "username": "testuser",
            "public_metrics": {"followers_count": 0, "following_count": 0,
                               "tweet_count": 0, "listed_count": 0},
        }})
    )
    user = await client.get_user_by_username("@testuser")
    assert user is not None


@pytest.mark.asyncio
@respx.mock
async def test_get_user_not_found_returns_none(client):
    respx.get(f"{BASE}/users/by/username/nobody").mock(
        return_value=httpx.Response(404, json={"errors": [{"message": "Not found"}]})
    )
    user = await client.get_user_by_username("nobody")
    assert user is None


# ── get_user_recent_tweets ────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_get_recent_tweets_returns_list(client):
    respx.get(f"{BASE}/users/111/tweets").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "tweet1",
                        "text": "Hello world",
                        "author_id": "111",
                        "created_at": "2024-01-01T12:00:00Z",
                        "lang": "en",
                        "public_metrics": {
                            "like_count": 100,
                            "retweet_count": 20,
                            "reply_count": 5,
                            "quote_count": 2,
                        },
                    }
                ],
                "meta": {"result_count": 1},
            },
        )
    )
    tweets = await client.get_user_recent_tweets("111", max_results=10)
    assert len(tweets) == 1
    assert tweets[0].id == "tweet1"
    assert tweets[0].public_metrics.like_count == 100
    assert tweets[0].created_at is not None


@pytest.mark.asyncio
@respx.mock
async def test_get_recent_tweets_empty_returns_list(client):
    respx.get(f"{BASE}/users/111/tweets").mock(
        return_value=httpx.Response(200, json={"meta": {"result_count": 0}})
    )
    tweets = await client.get_user_recent_tweets("111")
    assert tweets == []


# ── get_tweet_by_id ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_get_tweet_by_id_success(client):
    respx.get(f"{BASE}/tweets/tweet99").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "id": "tweet99",
                    "text": "Zora coin launch!",
                    "author_id": "222",
                    "created_at": "2024-06-01T15:00:00Z",
                    "lang": "en",
                    "public_metrics": {
                        "like_count": 500,
                        "retweet_count": 80,
                        "reply_count": 30,
                        "quote_count": 10,
                    },
                }
            },
        )
    )
    tweet = await client.get_tweet_by_id("tweet99")
    assert tweet is not None
    assert tweet.text == "Zora coin launch!"
    assert tweet.public_metrics.like_count == 500


@pytest.mark.asyncio
@respx.mock
async def test_get_tweet_not_found_returns_none(client):
    respx.get(f"{BASE}/tweets/missing").mock(
        return_value=httpx.Response(404, json={"errors": [{"message": "Not found"}]})
    )
    tweet = await client.get_tweet_by_id("missing")
    assert tweet is None


# ── Rate limit handling ───────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_rate_limit_raises_specific_error(client):
    respx.get(f"{BASE}/tweets/abc").mock(
        return_value=httpx.Response(
            429,
            headers={"x-rate-limit-reset": "1700000000"},
            json={"title": "Too Many Requests"},
        )
    )
    with pytest.raises(XRateLimitError):
        await client.get_tweet_by_id("abc")


# ── search_recent_tweets ──────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_search_recent_tweets(client):
    respx.get(f"{BASE}/tweets/search/recent").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "s1",
                        "text": "Check out this zora coin",
                        "author_id": "333",
                        "created_at": "2024-07-01T10:00:00Z",
                        "lang": "en",
                        "public_metrics": {
                            "like_count": 50,
                            "retweet_count": 10,
                            "reply_count": 3,
                            "quote_count": 1,
                        },
                    }
                ]
            },
        )
    )
    results = await client.search_recent_tweets("zora.co lang:en -is:retweet")
    assert len(results) == 1
    assert results[0].id == "s1"
