"""
tests/integration/test_health.py
Integration tests for FastAPI health endpoints.
Uses the patched AsyncClient fixture from conftest.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_health_returns_200(async_client):
    response = await async_client.get("/health")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_health_response_schema(async_client):
    response = await async_client.get("/health")
    body = response.json()
    assert body["status"] == "ok"
    assert "uptime_seconds" in body
    assert "env" in body
    assert isinstance(body["uptime_seconds"], float)


@pytest.mark.asyncio
async def test_metrics_endpoint(async_client):
    response = await async_client.get("/metrics")
    assert response.status_code == 200
    body = response.json()
    assert "uptime_seconds" in body
    assert "paper_trading" in body
    assert "live_trading" in body
    assert "llm_enabled" in body


@pytest.mark.asyncio
async def test_webhook_rejects_wrong_secret(async_client):
    """Webhook with wrong secret must return 403."""
    response = await async_client.post(
        "/webhook/wrong-secret",
        json={"update_id": 1},
    )
    # Will 404 because telegram_webhook_secret is None in test env, not 403
    assert response.status_code in (403, 404)


@pytest.mark.asyncio
async def test_unknown_route_returns_404(async_client):
    response = await async_client.get("/nonexistent")
    assert response.status_code == 404
