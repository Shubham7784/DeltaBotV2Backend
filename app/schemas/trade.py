"""Pydantic schemas for Signals, Trade Decisions, Orders, and Positions."""

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field
from app.db.enums import TradeLifecycleState, TradeDirection


class SignalCreate(BaseModel):
    strategy_name: str
    symbol: str
    timeframe: str = "5m"
    side: str = "buy"
    trigger_price: float
    confidence_score: float = Field(default=0.5, ge=0.0, le=1.0)
    indicator_snapshot: Dict[str, Any] = Field(default_factory=dict)
    status: str = "PENDING"


class SignalResponse(SignalCreate):
    id: int
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class TradeDecisionCreate(BaseModel):
    symbol: str
    direction: TradeDirection = TradeDirection.LONG
    subaccount: str = "futures"
    is_paper: bool = True
    timeframe_alignment: Dict[str, Any] = Field(default_factory=dict)
    entry_price: Optional[float] = None
    take_profit_price: Optional[float] = None
    stop_loss_price: Optional[float] = None
    leverage: int = 50
    suggested_size: Optional[int] = None


class TradeDecisionUpdateState(BaseModel):
    state: TradeLifecycleState
    notes: Optional[str] = None
    llm_approved: Optional[bool] = None
    llm_confidence: Optional[float] = None
    llm_reasoning: Optional[str] = None
    risk_approved: Optional[bool] = None
    risk_notes: Optional[str] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    realized_pnl: Optional[float] = None


class OrderResponse(BaseModel):
    id: int
    trade_decision_id: Optional[int] = None
    subaccount: str
    exchange_order_id: Optional[str] = None
    client_order_id: Optional[str] = None
    symbol: str
    product_id: Optional[int] = None
    side: str
    order_type: str
    price: Optional[float] = None
    stop_price: Optional[float] = None
    size: int
    unfilled_size: int = 0
    state: str
    is_paper: bool
    is_bracket: bool
    bracket_take_profit_price: Optional[float] = None
    bracket_stop_loss_price: Optional[float] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class PositionResponse(BaseModel):
    id: int
    trade_decision_id: Optional[int] = None
    subaccount: str
    symbol: str
    product_id: Optional[int] = None
    size: int
    entry_price: float
    current_price: Optional[float] = None
    liquidation_price: Optional[float] = None
    margin: float = 0.0
    leverage: int = 50
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    take_profit_price: Optional[float] = None
    stop_loss_price: Optional[float] = None
    is_open: bool = True
    exit_reason: Optional[str] = None
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class TradeDecisionResponse(BaseModel):
    id: int
    symbol: str
    direction: str
    state: str
    subaccount: str
    is_paper: bool
    timeframe_alignment: Dict[str, Any]
    llm_approved: Optional[bool] = None
    llm_confidence: Optional[float] = None
    llm_reasoning: Optional[str] = None
    risk_approved: Optional[bool] = None
    risk_notes: Optional[str] = None
    suggested_size: Optional[int] = None
    leverage: int
    entry_price: Optional[float] = None
    take_profit_price: Optional[float] = None
    stop_loss_price: Optional[float] = None
    liquidation_buffer_pct: Optional[float] = None
    realized_pnl: float
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    orders: List[OrderResponse] = []
    positions: List[PositionResponse] = []

    model_config = ConfigDict(from_attributes=True)
