"""FastAPI endpoints for Strategy Engine management and execution (Phase 6)."""

import logging
import time
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query, status

from app.schemas.strategy import (
    PromoteSignalRequest,
    PromoteSignalResponse,
    StrategyConfigUpdateRequest,
    StrategyEvaluateRequest,
    StrategyEvaluateResponse,
    StrategyInfo,
    StrategySignal,
    StrategyToggleRequest,
)
from app.services.strategy_engine import strategy_engine

logger = logging.getLogger("delta_bot.api.strategies")
router = APIRouter(prefix="/strategies", tags=["Strategies"])


@router.get("", response_model=List[StrategyInfo])
async def list_strategies() -> List[StrategyInfo]:
    """Retrieves all registered strategy modules with their active status and parameters."""
    try:
        return await strategy_engine.list_strategies()
    except Exception as e:
        logger.error(f"Error listing strategies: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch strategies: {str(e)}",
        )


@router.post("/{name}/toggle", response_model=Dict[str, Any])
async def toggle_strategy(name: str, body: StrategyToggleRequest) -> Dict[str, Any]:
    """Enables or disables a specific strategy module."""
    success = await strategy_engine.toggle_strategy(name=name, is_active=body.is_active)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Strategy '{name}' not found.",
        )
    return {
        "strategy": name,
        "is_active": body.is_active,
        "message": f"Strategy '{name}' is now {'active' if body.is_active else 'inactive'}.",
    }


@router.post("/{name}/config", response_model=Dict[str, Any])
async def update_strategy_config(name: str, body: StrategyConfigUpdateRequest) -> Dict[str, Any]:
    """Updates configuration parameters for a strategy module."""
    success = await strategy_engine.update_strategy_config(name=name, config=body.config)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Strategy '{name}' not found.",
        )
    return {
        "strategy": name,
        "config": body.config,
        "message": f"Strategy '{name}' parameters updated successfully.",
    }


@router.post("/evaluate", response_model=StrategyEvaluateResponse)
async def evaluate_strategies(body: StrategyEvaluateRequest) -> StrategyEvaluateResponse:
    """Triggers concurrent evaluation of active strategies against market data.
    
    If symbol and timeframe are provided, evaluates that specific pair.
    Otherwise, evaluates all core instruments across primary timeframes.
    """
    try:
        all_signals: List[StrategySignal] = []
        if body.symbol and body.timeframe:
            signals = await strategy_engine.evaluate_symbol(
                symbol=body.symbol,
                timeframe=body.timeframe,
                persist=body.persist,
            )
            all_signals.extend(signals)
        elif body.symbol:
            for tf in ["5m", "15m", "1h"]:
                signals = await strategy_engine.evaluate_symbol(
                    symbol=body.symbol,
                    timeframe=tf,
                    persist=body.persist,
                )
                all_signals.extend(signals)
        else:
            multi_results = await strategy_engine.evaluate_all(persist=body.persist)
            for sigs in multi_results.values():
                all_signals.extend(sigs)

        strategies = await strategy_engine.list_strategies()
        active_count = len([s for s in strategies if s.is_active])

        return StrategyEvaluateResponse(
            timestamp=int(time.time()),
            evaluated_strategies=active_count,
            signals_generated=len(all_signals),
            signals=all_signals,
        )
    except Exception as e:
        logger.error(f"Error evaluating strategies: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Evaluation failed: {str(e)}",
        )


@router.post("/promote", response_model=PromoteSignalResponse)
async def promote_signal(body: PromoteSignalRequest) -> PromoteSignalResponse:
    """Promotes a candidate signal into State 1: SIGNAL_GENERATED of the 7-state trade lifecycle.
    
    Strict invariant: Rejects promotion if an active trade decision or open position already exists
    for the instrument.
    """
    try:
        response = await strategy_engine.promote_to_trade(
            signal_id=body.signal_id,
            subaccount=body.subaccount,
            is_paper=body.is_paper,
        )
        return response
    except ValueError as ve:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(ve),
        )
    except Exception as e:
        logger.error(f"Error promoting signal {body.signal_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to promote signal: {str(e)}",
        )
