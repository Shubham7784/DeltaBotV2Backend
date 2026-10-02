"""Multi-Timeframe Trend Alignment Routes (Phase 8)."""

from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.services.trend_engine import (
    trend_engine,
    TimeframeAlignmentReport,
    SignalAlignmentValidation,
)

router = APIRouter(prefix="/trend", tags=["Multi-Timeframe Trend Engine"])


class ValidateSignalRequest(BaseModel):
    symbol: str
    proposed_direction: str  # long or short


@router.get("/align/{symbol}", response_model=TimeframeAlignmentReport)
async def get_symbol_trend_alignment(symbol: str):
    """Computes multi-timeframe trend alignment (1h macro, 15m structural, 5m tactical) for a symbol."""
    try:
        return trend_engine.analyze_alignment(symbol)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to calculate trend alignment for {symbol}: {e}")


@router.get("/all", response_model=List[TimeframeAlignmentReport])
async def get_all_trend_alignments(symbols: Optional[str] = Query(None, description="Comma-separated symbols")):
    """Returns multi-timeframe trend alignment across all monitored instruments."""
    sym_list = [s.strip().upper() for s in symbols.split(",")] if symbols else None
    try:
        return trend_engine.analyze_all(sym_list)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to calculate all trend alignments: {e}")


@router.post("/validate-signal", response_model=SignalAlignmentValidation)
async def validate_signal(payload: ValidateSignalRequest):
    """Validates if a proposed trade direction satisfies multi-timeframe trend confluence."""
    try:
        return trend_engine.validate_signal_alignment(payload.symbol, payload.proposed_direction)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to validate signal alignment: {e}")
