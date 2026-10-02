"""Domain enums for Delta Bot V2."""

from enum import Enum


class TradeLifecycleState(str, Enum):
    """The formal 7-state lifecycle for automated trade execution."""
    SIGNAL_GENERATED = "SIGNAL_GENERATED"
    VALIDATING = "VALIDATING"
    LLM_CONFIRMED = "LLM_CONFIRMED"
    RISK_VALIDATED = "RISK_VALIDATED"
    ORDER_SUBMITTED = "ORDER_SUBMITTED"
    POSITION_OPEN = "POSITION_OPEN"
    EXITED = "EXITED"


class TradeDirection(str, Enum):
    """Trade direction."""
    LONG = "long"
    SHORT = "short"


class OrderSide(str, Enum):
    """Exchange order side."""
    BUY = "buy"
    SELL = "sell"


class ContractType(str, Enum):
    """Derivative contract type."""
    PERPETUAL_FUTURES = "perpetual_futures"
    CALL_OPTIONS = "call_options"
    PUT_OPTIONS = "put_options"
    INTEREST_RATE_SWAP = "interest_rate_swap"


class Timeframe(str, Enum):
    """Supported technical analysis timeframes."""
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"


class OrderType(str, Enum):
    """Exchange order types."""
    MARKET_ORDER = "market_order"
    LIMIT_ORDER = "limit_order"
    STOP_MARKET_ORDER = "stop_market_order"
    STOP_LIMIT_ORDER = "stop_limit_order"


class OrderState(str, Enum):
    """Order execution status."""
    OPEN = "open"
    PENDING = "pending"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class ExitReason(str, Enum):
    """Reasons for position or trade exit."""
    TAKE_PROFIT = "take_profit"
    STOP_LOSS = "stop_loss"
    TRAILING_STOP = "trailing_stop"
    MANUAL_CLOSE = "manual_close"
    LIQUIDATION_PREVENTED = "liquidation_prevented"
    EMERGENCY_SHUTDOWN = "emergency_shutdown"
    CANCELLED_BEFORE_FILL = "cancelled_before_fill"


class AuditSeverity(str, Enum):
    """Audit log severity levels."""
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class AuditEventType(str, Enum):
    """System event categorizations for audits."""
    SYSTEM = "SYSTEM"
    SIGNAL_ENGINE = "SIGNAL_ENGINE"
    LLM_VALIDATION = "LLM_VALIDATION"
    RISK_CHECK = "RISK_CHECK"
    ORDER_EXECUTION = "ORDER_EXECUTION"
    POSITION_UPDATE = "POSITION_UPDATE"
    EMERGENCY_ACTION = "EMERGENCY_ACTION"
