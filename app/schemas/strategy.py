"""Strategy Engine Schemas for Delta Bot V2 (Phase 6)."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class StrategySignal(BaseModel):
    """Standardized trade candidate signal produced by any strategy."""
    id: Optional[int] = None
    strategy_name: str
    symbol: str
    timeframe: str  # 5m, 15m, 1h
    side: str  # buy, sell
    direction: str  # long, short
    trigger_price: float
    stop_loss: float
    take_profit: float
    risk_reward_ratio: float
    confidence_score: float = Field(ge=0.0, le=1.0)
    reasons: List[str] = Field(default_factory=list)
    indicator_snapshot: Dict[str, Any] = Field(default_factory=dict)
    status: str = "PENDING"  # PENDING, VALIDATING, PROMOTED, REJECTED, EXPIRED
    created_at: Optional[str] = None


class StrategyInfo(BaseModel):
    """Registered strategy metadata and configuration."""
    name: str
    description: str
    is_active: bool
    config: Dict[str, Any]
    signal_count: int = 0
    recent_signals: List[StrategySignal] = Field(default_factory=list)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class StrategyEvaluateRequest(BaseModel):
    """Request to trigger evaluation across strategies."""
    symbol: Optional[str] = Field(None, description="BTCUSD, ETHUSD, SOLUSD, or None for all")
    timeframe: Optional[str] = Field(None, description="5m, 15m, 1h, or None for all")
    persist: bool = Field(True, description="Whether to record signals in SQLite database")


class StrategyEvaluateResponse(BaseModel):
    """Result of strategy evaluation."""
    timestamp: int
    evaluated_strategies: int
    signals_generated: int
    signals: List[StrategySignal]


class StrategyToggleRequest(BaseModel):
    """Toggle strategy active state."""
    is_active: bool


class StrategyConfigUpdateRequest(BaseModel):
    """Update strategy parameters."""
    config: Dict[str, Any]


class PromoteSignalRequest(BaseModel):
    """Promote a signal to State 1: SIGNAL_GENERATED in the 7-state trade lifecycle."""
    signal_id: int
    subaccount: str = Field("futures", description="Target subaccount: futures or options")
    is_paper: bool = Field(True, description="Paper trading flag")


class PromoteSignalResponse(BaseModel):
    """Response when signal is successfully promoted to a TradeDecision."""
    trade_id: int
    state: str
    symbol: str
    direction: str
    entry_price: float
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    message: str
