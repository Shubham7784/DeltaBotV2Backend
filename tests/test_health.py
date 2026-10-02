"""Unit tests for Phase 1 health endpoints and configuration."""

import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.core.config import settings


@pytest.mark.asyncio
async def test_health_endpoint():
    """Verify that /api/v1/health returns 200 and expected metadata."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["app_name"] == "Delta Bot V2"
    assert data["version"] == "2.0.0"
    assert "timestamp" in data


@pytest.mark.asyncio
async def test_system_status_endpoint():
    """Verify that /api/v1/status provides domain separation and capital parameters."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/api/v1/status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "operational"
    assert "futures" in data["domains"]
    assert "options" in data["domains"]
    assert "BTC" in data["instruments"]
    assert "ETH" in data["instruments"]
    assert "GOLD" in data["instruments"]
    assert "5m" in data["timeframes"]
    assert "15m" in data["timeframes"]
    assert "1h" in data["timeframes"]
    assert data["capital_profile"]["initial_wallet_inr"] == 1000.0


@pytest.mark.asyncio
async def test_safe_config_endpoint():
    """Verify that /api/v1/config does not expose API secrets."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/api/v1/config")
    assert response.status_code == 200
    data = response.json()
    assert "DELTA_FUTURES_API_SECRET" not in data
    assert "DELTA_OPTIONS_API_SECRET" not in data
    assert "credentials" in data
    assert "futures_configured" in data["credentials"]
    assert "options_configured" in data["credentials"]


@pytest.mark.asyncio
async def test_bot_toggle():
    """Verify the bot toggle switches BOT ON / BOT OFF."""
    initial_state = settings.BOT_ACTIVE
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res1 = await ac.post("/api/v1/bot/toggle")
        assert res1.status_code == 200
        assert res1.json()["bot_active"] == (not initial_state)

        # Toggle back
        res2 = await ac.post("/api/v1/bot/toggle")
        assert res2.status_code == 200
        assert res2.json()["bot_active"] == initial_state
