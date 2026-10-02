"""FastAPI router for Risk Management Engine (Phase 7).

Endpoints:
- GET /api/v1/risk/status: Live wallet capital, 30% reserve buffer, 50% exposure, 1% risk allowance.
- POST /api/v1/risk/validate: Deterministic validation of a candidate trade against all risk invariants.
"""

import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Query

from app.schemas.risk import AccountCapitalStatus, RiskCheckRequest, RiskValidationResult
from app.services.risk_engine import risk_engine

logger = logging.getLogger("delta_bot.api.risk")
router = APIRouter(prefix="/risk", tags=["Risk Management"])


@router.get("/status", response_model=AccountCapitalStatus)
async def get_risk_status(
    subaccount: str = Query("futures", description="Subaccount to evaluate (futures or options)"),
    override_capital: Optional[float] = Query(None, description="Override total equity for testing/simulation"),
):
    """Returns real-time account capital, 30% reserve buffer, and 1% risk allowances."""
    try:
        status = await risk_engine.get_account_capital(subaccount=subaccount, override_capital=override_capital)
        return status
    except Exception as e:
        logger.error(f"Error calculating risk status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/validate", response_model=RiskValidationResult)
async def validate_candidate_trade(request: RiskCheckRequest):
    """Executes deterministic risk validation against all system invariants:
    - 1% Max Risk Rule sizing
    - 30% Uncommitted Reserve Buffer
    - 50% Maximum Portfolio Exposure
    - Multiple positions per instrument are allowed
    - Liquidation Buffer Protection
    - Daily Drawdown Circuit Breaker
    """
    try:
        result = await risk_engine.validate_trade(
            symbol=request.symbol,
            direction=request.direction,
            entry_price=request.entry_price,
            stop_loss_price=request.stop_loss_price,
            take_profit_price=request.take_profit_price,
            leverage=request.leverage,
            subaccount=request.subaccount,
            override_capital=request.override_capital,
        )
        return result
    except Exception as e:
        logger.error(f"Error validating trade risk: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
