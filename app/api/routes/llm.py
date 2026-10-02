"""LLM Advisory & Market Regime Validation Routes (Phase 8)."""

import os
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.llm_advisory import llm_advisory, LLMAdvisoryEvaluation

router = APIRouter(prefix="/llm", tags=["LLM Advisory Regime Engine"])


class EvaluateTradeRequest(BaseModel):
    symbol: str
    direction: str  # long or short
    entry_price: float
    stop_loss_price: float
    take_profit_price: float
    primary_timeframe: str = "5m"


class LLMStatusResponse(BaseModel):
    provider: str
    has_api_key: bool
    model: str
    mode: str
    guardrail_invariant: str = "Advisory only. Cannot override 1% risk limit or widen stop-loss."


@router.get("/status", response_model=LLMStatusResponse)
async def get_llm_status():
    """Checks the status and operational mode of the LLM Advisory engine."""
    api_key_present = bool(os.getenv("GEMINI_API_KEY"))
    return LLMStatusResponse(
        provider="Google Gemini via @google/genai" if api_key_present else "Deterministic Technical Fallback",
        has_api_key=api_key_present,
        model="gemini-3.8-flash" if api_key_present else "rule-engine-v2",
        mode="ONLINE_LLM" if api_key_present else "OFFLINE_DETERMINISTIC",
    )


@router.post("/evaluate-trade", response_model=LLMAdvisoryEvaluation)
async def evaluate_trade(payload: EvaluateTradeRequest):
    """Evaluates a proposed trade candidate against broader market regime, multi-timeframe trend, and sentiment."""
    try:
        return await llm_advisory.evaluate_trade(
            symbol=payload.symbol,
            direction=payload.direction,
            entry_price=payload.entry_price,
            stop_loss_price=payload.stop_loss_price,
            take_profit_price=payload.take_profit_price,
            primary_timeframe=payload.primary_timeframe,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM advisory evaluation failed: {e}")


@router.get("/regime/{symbol}", response_model=LLMAdvisoryEvaluation)
async def evaluate_symbol_regime(symbol: str):
    """Evaluates market regime and advisory bias for a symbol without requiring an active order."""
    try:
        return await llm_advisory.evaluate_symbol_regime(symbol)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to evaluate regime for {symbol}: {e}")
