"""Master API Router for Delta Bot V2."""

from fastapi import APIRouter
from app.api.routes import (
    config_info,
    delta,
    execution,
    health,
    indicators,
    llm,
    market,
    market_store,
    risk,
    signals,
    strategies,
    trades,
    trend,
    websocket_feed,
)

api_router = APIRouter()

# Include health & diagnostics
api_router.include_router(health.router)
# Include configuration info
api_router.include_router(config_info.router)
# Include Delta Exchange REST API v2
api_router.include_router(delta.router)
# Include Trades & 7-State Lifecycle
api_router.include_router(trades.router)
# Include Market Data Persistence & DB Stats
api_router.include_router(market_store.router)
# Include Market Data Engine & Ring Buffers
api_router.include_router(market.router)
# Include Indicator Engine & Technical Analysis
api_router.include_router(indicators.router)
# Include Strategy Engine & Signal Management (Phase 6)
api_router.include_router(strategies.router)
api_router.include_router(signals.router)
# Include Deterministic Risk Engine & Lifecycle Execution (Phase 7)
api_router.include_router(risk.router)
api_router.include_router(execution.router)
# Include Multi-Timeframe Trend & LLM Advisory Regime Validation (Phase 8)
api_router.include_router(trend.router)
api_router.include_router(llm.router)
api_router.include_router(websocket_feed.router)

