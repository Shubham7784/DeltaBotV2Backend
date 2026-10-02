"""Pydantic schemas for the Deterministic Risk Management Engine.

Strictly enforces:
1. 1% Max Risk Rule (loss at Stop Loss <= 1% of total account capital).
2. 30% Uncommitted Reserve Buffer (trading capital cannot exceed 70% of equity).
3. 50% Maximum Total Simultaneous Exposure.
4. Multiple positions per instrument are allowed.
5. Liquidation Buffer (Stop Loss must trigger before liquidation price with safety margin).
6. Daily Drawdown Circuit Breaker (5% max daily drawdown).
7. LLM Advisory Guardrail (LLM can never override deterministic risk constraints).
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class AccountCapitalStatus(BaseModel):
    """Real-time capital and reserve metrics."""
    currency: str = "USD"
    total_equity: float = Field(..., description="Total equity in account currency")
    available_margin: float = Field(..., description="Currently available margin")
    blocked_margin: float = Field(..., description="Margin currently committed to open positions/orders")
    reserve_buffer_pct: float = Field(30.0, description="Minimum percentage held untouched in reserve (30%)")
    reserve_buffer_amount: float = Field(..., description="Equity reserved and untradeable")
    usable_trading_capital: float = Field(..., description="Equity deployable after 30% reserve buffer")
    current_exposure_pct: float = Field(..., description="Current active margin / total equity (max 50%)")
    max_exposure_pct: float = Field(50.0, description="Maximum permitted portfolio exposure")
    max_risk_per_trade_pct: float = Field(1.0, description="Maximum capital risk per individual trade (1%)")
    max_risk_per_trade_amount: float = Field(..., description="Max dollar loss permitted at stop-loss")
    daily_drawdown_pct: float = Field(0.0, description="Cumulative realized drawdown today")
    daily_drawdown_limit_pct: float = Field(5.0, description="Circuit breaker threshold (5%)")
    circuit_breaker_active: bool = Field(False, description="True if new trades are halted today")


class RiskCheckRequest(BaseModel):
    """Payload to evaluate a trade decision against deterministic risk rules."""
    symbol: str
    direction: str  # long, short
    entry_price: float
    stop_loss_price: float
    take_profit_price: float
    leverage: int = 50
    subaccount: str = "futures"
    override_capital: Optional[float] = None  # For testing or simulation


class RiskValidationResult(BaseModel):
    """Deterministic validation outcome."""
    is_approved: bool
    rejection_reasons: List[str] = Field(default_factory=list)
    risk_warnings: List[str] = Field(default_factory=list)
    
    # Mathematical sizing
    calculated_size: int = Field(0, description="Exact integer contract quantity where loss <= 1% capital")
    required_margin: float = Field(0.0, description="Margin required at requested leverage")
    dollar_risk: float = Field(0.0, description="Potential loss if stop loss is hit")
    dollar_risk_pct: float = Field(0.0, description="Dollar risk as percentage of equity")
    risk_reward_ratio: float = Field(0.0, description="Mathematical R:R (take profit dist / stop loss dist)")
    
    # Invariant safety status
    single_position_passed: bool = True
    reserve_buffer_passed: bool = True
    max_exposure_passed: bool = True
    liquidation_buffer_passed: bool = True
    circuit_breaker_passed: bool = True
    
    # Liquidation safety
    estimated_liquidation_price: Optional[float] = None
    liquidation_buffer_pct: Optional[float] = None
    
    # Guardrail confirmation
    llm_guardrail_confirmed: bool = Field(
        True,
        description="Confirms deterministic rules override any LLM suggestions"
    )
    details: Dict[str, Any] = Field(default_factory=dict)
