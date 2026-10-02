"""Market Data API routes for Delta Bot V2.

Provides endpoints for candle synchronization, in-memory buffer queries,
live price feeds, and background poller management.
"""

import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.candle import (
    CandleBase,
    CandleBufferResponse,
    MarketEngineStatus,
    MarketSyncRequest,
    MarketSyncResult,
)
from app.services.market_data_engine import market_engine

logger = logging.getLogger("delta_bot.api.market")

router = APIRouter(prefix="/market", tags=["Market Data Engine"])


@router.get("/status", response_model=MarketEngineStatus)
async def get_market_engine_status():
    """Retrieves operational status, buffer statistics, and live prices of the Market Data Engine."""
    return market_engine.get_status()


@router.get("/prices")
async def get_market_prices():
    """Returns the latest cached prices for all tracked symbols."""
    return market_engine.get_status().live_prices


@router.post("/sync", response_model=List[MarketSyncResult])
async def trigger_market_sync(
    payload: MarketSyncRequest,
    db: AsyncSession = Depends(get_db),
):
    """Triggers on-demand candlestick ingestion and buffer replenishment for specified symbols and timeframes."""
    results = await market_engine.sync_all(
        session=db,
        symbols=payload.symbols,
        timeframes=payload.timeframes,
        lookback_hours=payload.lookback_hours,
    )
    return results


@router.get("/buffers/{symbol}")
async def get_symbol_buffers(
    symbol: str,
    resolution: Optional[str] = Query(default=None, description="Optional timeframe filter (5m, 15m, 1h)"),
):
    """Retrieves in-memory normalized candlestick buffer and pricing for a symbol."""
    sym = symbol.strip().upper()
    live_price = market_engine.get_live_price(sym)

    if resolution:
        buf = market_engine.get_buffer(sym, resolution)
        if not buf:
            raise HTTPException(status_code=404, detail=f"Buffer for {sym} ({resolution}) not found")
        return CandleBufferResponse(
            symbol=sym,
            resolution=resolution,
            count=len(buf),
            mark_price=live_price.get("mark_price") if live_price else None,
            candles=[CandleBase(**c) for c in buf.get_all()],
        )

    # Return all available timeframes for the symbol
    all_bufs = market_engine.get_all_buffers_for_symbol(sym)
    if not all_bufs:
        raise HTTPException(status_code=404, detail=f"No buffers tracked for {sym}")

    res: Dict[str, Any] = {
        "symbol": sym,
        "live_price": live_price,
        "timeframes": {},
    }
    for tf, buf in all_bufs.items():
        res["timeframes"][tf] = {
            "count": len(buf),
            "stats": buf.stats().model_dump(),
            "latest": buf.latest.to_dict() if buf.latest else None,
            "candles": buf.get_all(),
        }
    return res


@router.get("/live-feed")
async def get_live_market_feed():
    """Retrieves consolidated live prices, 24h metadata, and latest candle summary for all tracked symbols."""
    status = market_engine.get_status()
    symbols_data: Dict[str, Any] = {}

    for sym in status.tracked_symbols:
        live = market_engine.get_live_price(sym) or {}
        tf_data: Dict[str, Any] = {}
        for tf in status.timeframes:
            buf = market_engine.get_buffer(sym, tf)
            if buf:
                tf_data[tf] = {
                    "count": len(buf),
                    "latest": buf.latest.to_dict() if buf.latest else None,
                }

        symbols_data[sym] = {
            "symbol": sym,
            "pricing": live,
            "timeframes": tf_data,
        }

    return {
        "poller_active": status.poller_active,
        "last_sync_time": status.last_sync_time,
        "symbols": symbols_data,
    }


@router.post("/poller/start")
async def start_market_poller(interval_sec: int = Query(default=20, ge=5, le=300)):
    """Starts the continuous background market poller task."""
    market_engine.start_background_poller(interval_sec=interval_sec)
    return {"status": "started", "interval_sec": interval_sec}


@router.post("/poller/stop")
async def stop_market_poller():
    """Stops the continuous background market poller task."""
    await market_engine.stop_background_poller()
    return {"status": "stopped"}
