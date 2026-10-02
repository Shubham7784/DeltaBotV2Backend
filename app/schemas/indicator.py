"""Pydantic schemas for Indicator Engine (Phase 5).

Defines strongly typed payloads for EMA 9/20, MACD, VWAP, Pivot Points,
Support/Resistance levels, and the composite Indicator Snapshot.
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class EMAResult(BaseModel):
    """Exponential Moving Average calculations and trend alignment."""
    ema_fast: Optional[float] = Field(None, description="EMA 9 value")
    ema_slow: Optional[float] = Field(None, description="EMA 20 value")
    fast_period: int = 9
    slow_period: int = 20
    trend: str = Field("NEUTRAL", description="BULLISH_TREND | BEARISH_TREND | BULLISH_CROSS | BEARISH_CROSS | NEUTRAL")
    price_position: str = Field("BETWEEN", description="ABOVE_BOTH | BELOW_BOTH | BETWEEN")
    spread_pct: float = Field(0.0, description="Percentage spread between fast and slow EMA")
    fast_slope: float = Field(0.0, description="Rate of change of fast EMA over recent bars")
    slow_slope: float = Field(0.0, description="Rate of change of slow EMA over recent bars")


class MACDResult(BaseModel):
    """Moving Average Convergence Divergence calculations and signal dynamics."""
    macd_line: Optional[float] = Field(None, description="Fast EMA (12) - Slow EMA (26)")
    signal_line: Optional[float] = Field(None, description="EMA (9) of MACD line")
    histogram: Optional[float] = Field(None, description="MACD line - Signal line")
    fast_period: int = 12
    slow_period: int = 26
    signal_period: int = 9
    crossover: str = Field("NONE", description="BULLISH_CROSS | BEARISH_CROSS | NONE")
    zero_line_position: str = Field("NEUTRAL", description="ABOVE_ZERO | BELOW_ZERO | AT_ZERO")
    momentum_state: str = Field(
        "NEUTRAL",
        description="BULLISH_EXPANDING | BULLISH_CONTRACTING | BEARISH_EXPANDING | BEARISH_CONTRACTING | NEUTRAL",
    )


class VWAPResult(BaseModel):
    """Volume Weighted Average Price and standard deviation bands."""
    vwap: Optional[float] = Field(None, description="Volume Weighted Average Price")
    upper_band_1: Optional[float] = Field(None, description="VWAP + 1 StdDev")
    upper_band_2: Optional[float] = Field(None, description="VWAP + 2 StdDev")
    lower_band_1: Optional[float] = Field(None, description="VWAP - 1 StdDev")
    lower_band_2: Optional[float] = Field(None, description="VWAP - 2 StdDev")
    std_dev: float = Field(0.0, description="Standard deviation of typical price from VWAP")
    distance_pct: float = Field(0.0, description="Percentage distance of current price from VWAP")
    bias: str = Field(
        "AT_VWAP",
        description="OVERBOUGHT | EXTENDED_BULLISH | ABOVE_VWAP | AT_VWAP | BELOW_VWAP | EXTENDED_BEARISH | OVERSOLD",
    )


class PivotLevels(BaseModel):
    """Specific set of calculated pivot resistance and support levels."""
    pivot: float
    r1: float
    s1: float
    r2: float
    s2: float
    r3: float
    s3: float
    r4: Optional[float] = None
    s4: Optional[float] = None


class PivotPointsResult(BaseModel):
    """Multi-model Pivot Points (Standard/Floor, Fibonacci, Camarilla)."""
    standard: PivotLevels
    fibonacci: PivotLevels
    camarilla: PivotLevels
    nearest_support: Optional[float] = None
    nearest_resistance: Optional[float] = None
    support_distance_pct: Optional[float] = None
    resistance_distance_pct: Optional[float] = None


class SRZone(BaseModel):
    """Consolidated support or resistance level zone."""
    price: float
    level_type: str = Field("SUPPORT", description="SUPPORT | RESISTANCE")
    touches: int = Field(1, description="Number of swing peaks/troughs confirming this zone")
    strength: float = Field(1.0, description="Normalized strength score 0.0 - 1.0")


class SupportResistanceResult(BaseModel):
    """Dynamic swing-based Support and Resistance structure."""
    nearest_support: Optional[float] = None
    nearest_resistance: Optional[float] = None
    support_distance_pct: Optional[float] = None
    resistance_distance_pct: Optional[float] = None
    zones: List[SRZone] = Field(default_factory=list)
    proximity_status: str = Field(
        "IN_RANGE",
        description="TESTING_SUPPORT | TESTING_RESISTANCE | IN_RANGE | BREAKOUT_ABOVE | BREAKDOWN_BELOW",
    )


class IndicatorSnapshot(BaseModel):
    """Unified multi-indicator snapshot for trade decisioning and strategy execution."""
    symbol: str
    timeframe: str
    timestamp: int = Field(default=0)
    current_price: float
    candle_count: int = Field(default=0)
    ema: Optional[EMAResult] = None
    macd: Optional[MACDResult] = None
    vwap: Optional[VWAPResult] = None
    pivots: Optional[PivotPointsResult] = None
    support_resistance: Optional[SupportResistanceResult] = None
    technical_score: int = Field(0, description="Composite score from -100 (Strong Bearish) to +100 (Strong Bullish)")
    technical_bias: str = Field(
        "NEUTRAL",
        description="STRONG_BULLISH | BULLISH | NEUTRAL | BEARISH | STRONG_BEARISH",
    )
    summary_reasons: List[str] = Field(default_factory=list)


class MultiTimeframeSnapshot(BaseModel):
    """Snapshot across all active timeframes (5m, 15m, 1h)."""
    symbol: str
    current_price: float
    updated_at: str
    timeframes: Dict[str, IndicatorSnapshot] = Field(default_factory=dict)
    aggregate_bias: str = Field("NEUTRAL")
    aggregate_score: int = Field(0)


class CustomCalculateRequest(BaseModel):
    """Payload for on-demand calculation from external or simulated candle series."""
    symbol: str = "CUSTOM"
    timeframe: str = "5m"
    opens: List[float]
    highs: List[float]
    lows: List[float]
    closes: List[float]
    volumes: List[float]
    timestamps: Optional[List[int]] = None
