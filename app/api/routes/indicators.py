"""FastAPI routes for Indicator Engine (Phase 5).

Provides endpoints for EMA 9/20, MACD, VWAP, Pivot Points,
Support/Resistance levels, and Composite Multi-Timeframe Snapshots.
"""

from typing import Any, Dict, Optional
from fastapi import APIRouter, HTTPException, Query, status

from app.schemas.indicator import (
    CustomCalculateRequest,
    EMAResult,
    IndicatorSnapshot,
    MACDResult,
    MultiTimeframeSnapshot,
    PivotPointsResult,
    SupportResistanceResult,
    VWAPResult,
)
from app.services.indicator_engine import indicator_engine
from app.services.market_data_engine import market_engine

router = APIRouter(prefix="/indicators", tags=["Technical Indicators"])


@router.get(
    "/{symbol}",
    response_model=Dict[str, Any],
    summary="Get Indicator Snapshot for Symbol",
    description="Returns composite technical indicators (EMA, MACD, VWAP, Pivots, S/R) for single or multi-timeframe.",
)
async def get_indicators_for_symbol(
    symbol: str,
    timeframe: str = Query("5m", description="Timeframe ('5m', '15m', '1h', or 'all')"),
):
    sym = symbol.upper()

    if timeframe == "all":
        mtf_snapshot = indicator_engine.get_multi_timeframe_snapshot(sym)
        return mtf_snapshot.model_dump()

    snap = indicator_engine.get_snapshot(sym, timeframe=timeframe)
    if not snap:
        # Check if symbol is tracked and needs sync
        tracked = market_engine.tracked_symbols
        if sym not in tracked:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Symbol '{sym}' is not currently tracked by MarketDataEngine. Tracked symbols: {tracked}",
            )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Insufficient candles cached in '{timeframe}' buffer for '{sym}'. Trigger POST /api/v1/market/sync first.",
        )
    return snap.model_dump()


@router.get(
    "/{symbol}/ema",
    response_model=EMAResult,
    summary="Get EMA 9 and EMA 20 Analysis",
)
async def get_ema_indicators(
    symbol: str,
    timeframe: str = Query("5m", description="Timeframe ('5m', '15m', '1h')"),
):
    snap = indicator_engine.get_snapshot(symbol.upper(), timeframe=timeframe)
    if not snap:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Insufficient candles for {symbol.upper()} ({timeframe}).",
        )
    return snap.ema


@router.get(
    "/{symbol}/macd",
    response_model=MACDResult,
    summary="Get MACD (12, 26, 9) Analysis",
)
async def get_macd_indicators(
    symbol: str,
    timeframe: str = Query("5m", description="Timeframe ('5m', '15m', '1h')"),
):
    snap = indicator_engine.get_snapshot(symbol.upper(), timeframe=timeframe)
    if not snap:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Insufficient candles for {symbol.upper()} ({timeframe}).",
        )
    return snap.macd


@router.get(
    "/{symbol}/vwap",
    response_model=VWAPResult,
    summary="Get VWAP and Standard Deviation Bands",
)
async def get_vwap_indicators(
    symbol: str,
    timeframe: str = Query("5m", description="Timeframe ('5m', '15m', '1h')"),
):
    snap = indicator_engine.get_snapshot(symbol.upper(), timeframe=timeframe)
    if not snap:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Insufficient candles for {symbol.upper()} ({timeframe}).",
        )
    return snap.vwap


@router.get(
    "/{symbol}/pivots",
    response_model=PivotPointsResult,
    summary="Get Pivot Points (Standard, Fibonacci, Camarilla)",
)
async def get_pivot_points(
    symbol: str,
    timeframe: str = Query("5m", description="Timeframe ('5m', '15m', '1h')"),
):
    snap = indicator_engine.get_snapshot(symbol.upper(), timeframe=timeframe)
    if not snap:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Insufficient candles for {symbol.upper()} ({timeframe}).",
        )
    return snap.pivots


@router.get(
    "/{symbol}/support-resistance",
    response_model=SupportResistanceResult,
    summary="Get Dynamic Swing Support and Resistance Zones",
)
async def get_support_resistance(
    symbol: str,
    timeframe: str = Query("5m", description="Timeframe ('5m', '15m', '1h')"),
):
    snap = indicator_engine.get_snapshot(symbol.upper(), timeframe=timeframe)
    if not snap:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Insufficient candles for {symbol.upper()} ({timeframe}).",
        )
    return snap.support_resistance


@router.post(
    "/calculate",
    response_model=IndicatorSnapshot,
    summary="Calculate Indicators on Custom OHLCV Data",
    description="Accepts arbitrary opens, highs, lows, closes, and volumes to calculate indicators.",
)
async def calculate_custom_indicators(request: CustomCalculateRequest):
    try:
        return indicator_engine.calculate_from_series(
            symbol=request.symbol,
            timeframe=request.timeframe,
            opens=request.opens,
            highs=request.highs,
            lows=request.lows,
            closes=request.closes,
            volumes=request.volumes,
            timestamps=request.timestamps,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to calculate indicators: {str(e)}",
        )
