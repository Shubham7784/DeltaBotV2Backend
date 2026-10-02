"""Instruments, Candlestick persistence, and Database status routes."""

import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.db.models import (
    AuditLog,
    Candle,
    Instrument,
    Order,
    Position,
    Signal,
    TradeDecision,
)
from app.db.repository import TradeRepository
from app.schemas.candle import CandleBulkCreate, CandleResponse
from app.schemas.instrument import InstrumentCreate, InstrumentResponse

logger = logging.getLogger("delta_bot.api.market_store")
router = APIRouter(tags=["Market Store & Database"])


# =============================================================================
# 1. INSTRUMENTS
# =============================================================================

@router.get("/instruments", response_model=List[InstrumentResponse])
async def list_instruments(
    active_only: bool = Query(default=True),
    db: AsyncSession = Depends(get_db),
):
    """Lists trading instruments registered in the database."""
    instruments = await TradeRepository.get_instruments(session=db, active_only=active_only)
    return instruments


@router.post("/instruments", response_model=InstrumentResponse, status_code=status.HTTP_201_CREATED)
async def upsert_instrument(payload: InstrumentCreate, db: AsyncSession = Depends(get_db)):
    """Registers or updates a trading instrument."""
    inst = await TradeRepository.upsert_instrument(
        session=db,
        symbol=payload.symbol,
        product_id=payload.product_id,
        contract_type=payload.contract_type,
        underlying_asset=payload.underlying_asset,
        tick_size=payload.tick_size,
        contract_value=payload.contract_value,
        is_active=payload.is_active,
    )
    return inst


# =============================================================================
# 2. CANDLESTICK STORE
# =============================================================================

@router.get("/market/candles", response_model=List[CandleResponse])
async def get_persisted_candles(
    symbol: str = Query(default="BTCUSD"),
    resolution: str = Query(default="5m"),
    limit: int = Query(default=100, le=500),
    db: AsyncSession = Depends(get_db),
):
    """Retrieves persisted OHLC candlestick series for technical indicators."""
    candles = await TradeRepository.get_recent_candles(
        session=db, symbol=symbol, resolution=resolution, limit=limit
    )
    return candles


@router.post("/market/candles", status_code=status.HTTP_201_CREATED)
async def save_candle_batch(payload: CandleBulkCreate, db: AsyncSession = Depends(get_db)):
    """Persists a batch of candlestick bars from Delta Exchange feed."""
    saved_count = 0
    for bar in payload.candles:
        await TradeRepository.save_candle(
            session=db,
            symbol=payload.symbol,
            resolution=payload.resolution,
            open_time=bar.open_time,
            open_p=bar.open,
            high_p=bar.high,
            low_p=bar.low,
            close_p=bar.close,
            volume=bar.volume,
            close_time=bar.close_time,
            is_closed=bar.is_closed,
        )
        saved_count += 1
    return {"success": True, "saved_count": saved_count, "symbol": payload.symbol, "resolution": payload.resolution}


# =============================================================================
# 3. DATABASE STATS
# =============================================================================

@router.get("/database/stats")
async def get_database_stats(db: AsyncSession = Depends(get_db)):
    """Returns database connection status, table row counts, and storage metrics."""
    inst_count = (await db.execute(select(func.count(Instrument.id)))).scalar_one()
    candle_count = (await db.execute(select(func.count(Candle.id)))).scalar_one()
    signals_count = (await db.execute(select(func.count(Signal.id)))).scalar_one()
    trades_count = (await db.execute(select(func.count(TradeDecision.id)))).scalar_one()
    orders_count = (await db.execute(select(func.count(Order.id)))).scalar_one()
    positions_count = (await db.execute(select(func.count(Position.id)))).scalar_one()
    audit_count = (await db.execute(select(func.count(AuditLog.id)))).scalar_one()

    file_size_bytes = 0
    storage_type = "PostgreSQL Async (asyncpg)"
    if "sqlite" in settings.DATABASE_URL:
        from pathlib import Path

        db_path = settings.DATABASE_URL.replace("sqlite+aiosqlite:///", "").replace("sqlite:///", "")
        db_file = Path(db_path)
        if db_file.exists():
            file_size_bytes = db_file.stat().st_size
        storage_type = "SQLite Async (aiosqlite)"

    return {
        "status": "healthy",
        "database_url": settings.DATABASE_URL.split("@")[-1],  # Sanitized
        "storage_type": storage_type,
        "file_size_bytes": file_size_bytes,
        "counts": {
            "instruments": inst_count,
            "candles": candle_count,
            "signals": signals_count,
            "trade_decisions": trades_count,
            "orders": orders_count,
            "positions": positions_count,
            "audit_logs": audit_count,
        },
    }
