"""Health, diagnostics, and status endpoints."""

from datetime import datetime, timezone
from typing import List
from fastapi import APIRouter, Query

from app.core.config import settings
from app.core.logging import get_recent_logs
from app.schemas.health import HealthResponse, SystemStatusResponse, LogEntryResponse

router = APIRouter(tags=["Health & Diagnostics"])


@router.get("/health", response_model=HealthResponse)
async def get_health() -> HealthResponse:
    """Liveness health check probe."""
    return HealthResponse(
        status="ok",
        app_name=settings.APP_NAME,
        version=settings.APP_VERSION,
        timestamp=datetime.now(timezone.utc),
        phase="Phase 1 - Project Foundation",
    )


@router.get("/status", response_model=SystemStatusResponse)
async def get_system_status() -> SystemStatusResponse:
    """Full operational status of the trading bot and domains."""
    futures_ready = bool(settings.DELTA_FUTURES_API_KEY and settings.DELTA_FUTURES_API_SECRET)
    options_ready = bool(settings.DELTA_OPTIONS_API_KEY and settings.DELTA_OPTIONS_API_SECRET)

    domains = {
        "futures": {
            "name": "Futures Trading Domain",
            "active": True,
            "credentials_configured": futures_ready,
            "default_leverage": settings.DEFAULT_LEVERAGE,
            "btc_eth_leverage": settings.BTC_ETH_FUTURES_LEVERAGE,
            "default_tp_pct": settings.DEFAULT_TP_PCT,
            "default_sl_pct": settings.DEFAULT_SL_PCT,
        },
        "options": {
            "name": "Options Trading Domain",
            "active": True,
            "credentials_configured": options_ready,
            "subaccount_isolated": True,
            "leverage": settings.OPTIONS_LEVERAGE,
            "status": "Ready for Phase 11 Options Engine specification",
        },
    }

    capital_profile = {
        "initial_wallet_inr": settings.INITIAL_WALLET_INR,
        "initial_wallet_usd": settings.INITIAL_WALLET_USD,
        "max_exposure_pct": settings.MAX_EXPOSURE_PCT,
        "safety_reserve_pct": settings.SAFETY_RESERVE_PCT,
    }

    return SystemStatusResponse(
        status="operational",
        environment=settings.ENVIRONMENT,
        paper_trading=settings.PAPER_TRADING,
        bot_active=settings.BOT_ACTIVE,
        domains=domains,
        instruments=settings.INITIAL_INSTRUMENTS,
        timeframes=settings.SUPPORTED_TIMEFRAMES,
        capital_profile=capital_profile,
        timestamp=datetime.now(timezone.utc),
    )


@router.get("/logs", response_model=List[LogEntryResponse])
async def get_logs(limit: int = Query(default=50, ge=1, le=200)) -> List[LogEntryResponse]:
    """Inspect recent in-memory log buffer."""
    raw_logs = get_recent_logs(limit=limit)
    return [LogEntryResponse(**item) for item in raw_logs]


@router.post("/bot/toggle")
async def toggle_bot_state() -> dict:
    """Toggle BOT ON / BOT OFF state."""
    settings.BOT_ACTIVE = not settings.BOT_ACTIVE
    return {
        "bot_active": settings.BOT_ACTIVE,
        "message": f"Bot status switched to {'ON' if settings.BOT_ACTIVE else 'OFF'}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
