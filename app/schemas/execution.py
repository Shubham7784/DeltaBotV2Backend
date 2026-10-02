"""Pydantic schemas for the 7-State Trade Lifecycle Execution Orchestrator.

State Transitions:
1. SIGNAL_GENERATED -> 2. VALIDATING -> 3. LLM_CONFIRMED -> 4. RISK_VALIDATED
-> 5. ORDER_SUBMITTED -> 6. POSITION_OPEN -> 7. EXITED
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class AdvanceTradeRequest(BaseModel):
    """Payload to transition a trade to its next sequential lifecycle state."""
    target_state: Optional[str] = None  # If omitted, auto-advances to the natural next state
    auto_execute: bool = Field(False, description="If True, advances all the way through to order submission if rules pass")
    notes: Optional[str] = None


class CloseTradeRequest(BaseModel):
    """Payload to manually close an open position."""
    exit_reason: str = Field("MANUAL_CLOSE", description="Reason for closing (e.g. MANUAL_CLOSE, REVERSAL_EXIT, TAKE_PROFIT)")
    limit_price: Optional[float] = None  # None for market exit


class KillSwitchRequest(BaseModel):
    """Payload for emergency halt."""
    subaccount: Optional[str] = None  # 'futures', 'options', or None for both
    reason: str = "EMERGENCY_KILL_SWITCH_TRIGGERED"


class OrderSnapshot(BaseModel):
    id: int
    exchange_order_id: Optional[str] = None
    symbol: str
    side: str
    order_type: str
    price: Optional[float] = None
    stop_price: Optional[float] = None
    size: int
    unfilled_size: int
    state: str
    is_paper: bool
    is_bracket: bool
    created_at: Optional[str] = None


class PositionSnapshot(BaseModel):
    id: int
    symbol: str
    size: int
    entry_price: float
    current_price: Optional[float] = None
    liquidation_price: Optional[float] = None
    margin: float
    leverage: int
    unrealized_pnl: float
    realized_pnl: float
    take_profit_price: Optional[float] = None
    stop_loss_price: Optional[float] = None
    is_open: bool
    opened_at: Optional[str] = None


class TradeLifecycleStepResponse(BaseModel):
    """Response detailing a trade's lifecycle transition."""
    trade_id: int
    symbol: str
    direction: str
    previous_state: str
    current_state: str
    is_terminal: bool = False
    message: str
    llm_validation: Optional[Dict[str, Any]] = None
    risk_validation: Optional[Dict[str, Any]] = None
    orders: List[OrderSnapshot] = Field(default_factory=list)
    position: Optional[PositionSnapshot] = None
    trade_summary: Dict[str, Any] = Field(default_factory=dict)


class KillSwitchResponse(BaseModel):
    success: bool
    message: str
    cancelled_orders_count: int
    closed_positions_count: int
    affected_trades: List[int]
