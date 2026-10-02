"""FastAPI router for Trade Execution & Lifecycle Orchestrator (Phase 7).

Endpoints:
- GET /api/v1/execution/trades: Query trade decisions with filtering.
- GET /api/v1/execution/trades/{trade_id}: Trade decision details with attached orders & positions.
- POST /api/v1/execution/trades/{trade_id}/step: Transition trade by 1 lifecycle step.
- POST /api/v1/execution/trades/{trade_id}/execute: Advance trade through full pipeline to POSITION_OPEN.
- POST /api/v1/execution/trades/{trade_id}/close: Close an open position and transition to EXITED.
- POST /api/v1/execution/scan-triggers: Check all open positions against live market prices for TP/SL.
- POST /api/v1/execution/kill-switch: Emergency circuit breaker to cancel all orders & close all positions.
"""

import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query

from app.db.repository import TradeRepository
from app.db.session import get_db_session
from app.schemas.execution import (
    AdvanceTradeRequest,
    CloseTradeRequest,
    KillSwitchRequest,
    KillSwitchResponse,
    TradeLifecycleStepResponse,
)
from app.services.execution_engine import execution_engine

logger = logging.getLogger("delta_bot.api.execution")
router = APIRouter(prefix="/execution", tags=["Execution & Lifecycle"])


@router.get("/trades")
async def list_trades(
    symbol: Optional[str] = Query(None, description="Filter by instrument symbol"),
    state: Optional[str] = Query(None, description="Filter by lifecycle state"),
    active_only: bool = Query(False, description="Filter for non-terminal trades only"),
    limit: int = Query(50, ge=1, le=200, description="Max trades to retrieve"),
):
    """Lists trade decisions with lifecycle state and performance metrics."""
    try:
        async with get_db_session() as session:
            trades = await TradeRepository.get_trade_decisions(
                session=session,
                symbol=symbol,
                state=state,
                active_only=active_only,
                limit=limit,
            )
            return [t.to_dict() for t in trades]
    except Exception as e:
        logger.error(f"Error listing trades: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trades/active")
async def list_active_trades(limit: int = Query(50, ge=1, le=200)):
    """Lists trades that are currently in a non-terminal lifecycle state."""
    return await list_trades(symbol=None, state=None, active_only=True, limit=limit)


@router.get("/trades/{trade_id}", response_model=TradeLifecycleStepResponse)
async def get_trade_details(trade_id: int):
    """Retrieves full details of a trade decision, including orders, position, and lifecycle status."""
    try:
        async with get_db_session() as session:
            trade = await TradeRepository.get_trade_decision(session, trade_id)
            if not trade:
                raise HTTPException(status_code=404, detail=f"TradeDecision #{trade_id} not found")
            return execution_engine._build_step_response(
                trade=trade,
                prev_state=trade.state,
                message=f"Trade #{trade_id} details fetched successfully",
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching trade #{trade_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/ratchet/rules")
async def get_ratchet_rules():
    """Returns the current bracket defaults used by the execution engine."""
    from app.core.config import settings

    return {
        "take_profit_pct": settings.DEFAULT_TP_PCT,
        "stop_loss_pct": settings.DEFAULT_SL_PCT,
        "trailing_enabled": True,
        "break_even_enabled": True,
    }


@router.get("/ratchet/events")
async def get_ratchet_events():
    """Returns recent ratchet events; persistence is not enabled yet."""
    return []


@router.post("/trades/{trade_id}/step", response_model=TradeLifecycleStepResponse)
async def step_trade_lifecycle(trade_id: int, payload: AdvanceTradeRequest):
    """Transitions a trade decision to its next sequential lifecycle state:
    1. SIGNAL_GENERATED -> 2. VALIDATING -> 3. LLM_CONFIRMED -> 4. RISK_VALIDATED
    -> 5. ORDER_SUBMITTED -> 6. POSITION_OPEN -> 7. EXITED
    """
    try:
        response = await execution_engine.advance_trade_lifecycle(
            trade_id=trade_id,
            target_state=payload.target_state,
            auto_execute=payload.auto_execute,
            notes=payload.notes,
        )
        return response
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error stepping trade #{trade_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/trades/{trade_id}/execute", response_model=TradeLifecycleStepResponse)
async def execute_trade_pipeline(trade_id: int):
    """Executes the full automated validation and order routing pipeline from current state to POSITION_OPEN."""
    try:
        response = await execution_engine.advance_trade_lifecycle(
            trade_id=trade_id,
            auto_execute=True,
            notes="Auto-executing trade lifecycle pipeline",
        )
        return response
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error executing trade pipeline #{trade_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/trades/{trade_id}/close", response_model=TradeLifecycleStepResponse)
async def close_trade(trade_id: int, payload: CloseTradeRequest):
    """Manually closes an open trade position and transitions to State 7: EXITED."""
    try:
        response = await execution_engine.close_trade(
            trade_id=trade_id,
            exit_reason=payload.exit_reason,
            custom_exit_price=payload.limit_price,
        )
        return response
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error closing trade #{trade_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/scan-triggers")
async def scan_position_triggers():
    """Scans all open positions against live market prices for TP/SL triggers and trailing stop updates."""
    try:
        closed_trades = await execution_engine.check_open_positions_triggers()
        return {
            "status": "success",
            "triggered_exits_count": len(closed_trades),
            "triggered_exits": closed_trades,
        }
    except Exception as e:
        logger.error(f"Error scanning position triggers: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/kill-switch", response_model=KillSwitchResponse)
async def emergency_kill_switch(payload: KillSwitchRequest):
    """Emergency Circuit Breaker: liquidates all open positions and cancels all active exchange orders."""
    try:
        response = await execution_engine.execute_kill_switch(
            subaccount=payload.subaccount,
            reason=payload.reason,
        )
        return response
    except Exception as e:
        logger.critical(f"Critical error executing kill-switch: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
