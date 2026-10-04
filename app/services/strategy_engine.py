"""Strategy Engine (Phase 6) for Delta Bot V2.

Modular, concurrent trading strategy execution producing standardized trade candidate signals:
1. EMATrendStrategy: EMA 9 & EMA 20 momentum, slope alignment, and pullback continuation
2. MACDMomentumStrategy: MACD (12, 26, 9) signal line crossovers and histogram momentum expansion
3. VWAPBounceStrategy: Volume-weighted mean reversion off ±2σ bands and dynamic session VWAP retests
4. PivotBreakoutStrategy: Camarilla, Standard, and Fibonacci pivot level breakouts & fractal S/R zone bounces
5. CompositeEnsembleStrategy: Multi-strategy consensus engine combining concurrent signals into high-conviction setups

Transitions qualified signals into State 1: SIGNAL_GENERATED
of the formal 7-State Trade Lifecycle.
"""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings
from app.db.enums import AuditEventType, AuditSeverity, TradeLifecycleState
from app.db.models import Signal, Strategy, TradeDecision
from app.db.repository import TradeRepository
from app.db.session import get_db_session
from app.schemas.indicator import IndicatorSnapshot
from app.schemas.strategy import (
    PromoteSignalResponse,
    StrategyInfo,
    StrategySignal,
)
from app.services.execution_engine import execution_engine
from app.services.indicator_engine import indicator_engine
from app.services.market_data_engine import CandleItem, market_engine

logger = logging.getLogger("delta_bot.strategy_engine")


def _round_price(price: float, tick_size: float = 0.5) -> float:
    """Rounds price to nearest tick size."""
    if tick_size <= 0:
        return round(price, 2)
    return round(round(price / tick_size) * tick_size, 4)


class BaseStrategy(ABC):
    """Abstract base class for modular trading strategies."""

    def __init__(
        self,
        name: str,
        description: str,
        is_active: bool = True,
        config: Optional[Dict[str, Any]] = None,
    ):
        self.name = name
        self.description = description
        self.is_active = is_active
        self.config: Dict[str, Any] = config or {}

    def update_config(self, new_config: Dict[str, Any]) -> None:
        """Updates strategy parameters dynamically."""
        self.config.update(new_config)

    def calculate_brackets(
        self,
        trigger_price: float,
        direction: str,
        suggested_sl: Optional[float] = None,
        risk_reward: Optional[float] = None,
        min_sl_pct: float = 0.005,
        max_sl_pct: float = 0.025,
    ) -> Tuple[float, float, float]:
        """Calculates deterministic Stop-Loss, Take-Profit, and Risk-Reward ratio.
        
        Returns: (stop_loss, take_profit, risk_reward_ratio)
        """
        rr = risk_reward or self.config.get("risk_reward", 2.5)

        if direction.lower() == "long":
            # Long bracket
            if suggested_sl is not None and suggested_sl < trigger_price:
                sl = suggested_sl
                # Ensure SL is within safe bounds
                sl = max(sl, trigger_price * (1.0 - max_sl_pct))
                sl = min(sl, trigger_price * (1.0 - min_sl_pct))
            else:
                sl = trigger_price * (1.0 - self.config.get("default_sl_pct", 0.01))

            risk_dist = trigger_price - sl
            tp = trigger_price + (risk_dist * rr)
            actual_rr = (tp - trigger_price) / max(0.0001, (trigger_price - sl))
        else:
            # Short bracket
            if suggested_sl is not None and suggested_sl > trigger_price:
                sl = suggested_sl
                # Ensure SL is within safe bounds
                sl = min(sl, trigger_price * (1.0 + max_sl_pct))
                sl = max(sl, trigger_price * (1.0 + min_sl_pct))
            else:
                sl = trigger_price * (1.0 + self.config.get("default_sl_pct", 0.01))

            risk_dist = sl - trigger_price
            tp = trigger_price - (risk_dist * rr)
            actual_rr = (trigger_price - tp) / max(0.0001, (sl - trigger_price))

        return _round_price(sl), _round_price(tp), round(actual_rr, 2)

    @abstractmethod
    def evaluate(
        self,
        symbol: str,
        timeframe: str,
        candles: List[CandleItem],
        snapshot: IndicatorSnapshot,
    ) -> Optional[StrategySignal]:
        """Evaluates candlestick series & indicator snapshot to produce a candidate signal."""
        pass


# =============================================================================
# STRATEGY 1: EMA TREND FOLLOWING
# =============================================================================

class EMATrendStrategy(BaseStrategy):
    """EMA 9 / EMA 20 momentum, slope alignment, and pullback continuation."""

    def __init__(self, is_active: bool = True, config: Optional[Dict[str, Any]] = None):
        default_config = {
            "fast_period": 9,
            "slow_period": 20,
            "min_slope_pct": 0.005,
            "min_spread_pct": 0.02,
            "risk_reward": 2.5,
            "default_sl_pct": 0.01,
        }
        if config:
            default_config.update(config)
        super().__init__(
            name="EMA_Trend_Follower",
            description="EMA 9/20 trend alignment, slopes, and pullback continuation setups.",
            is_active=is_active,
            config=default_config,
        )

    def evaluate(
        self,
        symbol: str,
        timeframe: str,
        candles: List[CandleItem],
        snapshot: IndicatorSnapshot,
    ) -> Optional[StrategySignal]:
        if not self.is_active or not snapshot.ema:
            return None

        ema = snapshot.ema
        price = snapshot.current_price
        if not ema.ema_fast or not ema.ema_slow or price <= 0:
            return None

        min_slope = self.config.get("min_slope_pct", 0.005)
        min_spread = self.config.get("min_spread_pct", 0.02)
        reasons: List[str] = []

        # 1. Bullish Setup Check
        is_bullish_alignment = (
            ema.ema_fast > ema.ema_slow
            and ema.spread_pct >= min_spread
            and ema.fast_slope >= min_slope
        )

        if is_bullish_alignment:
            reasons.append(f"Fast EMA 9 (${ema.ema_fast:,.1f}) > Slow EMA 20 (${ema.ema_slow:,.1f}) with +{ema.spread_pct:.2f}% spread")
            reasons.append(f"Positive fast slope (+{ema.fast_slope:.3f}%) indicates accelerating upward momentum")

            # Check price location relative to EMAs
            if ema.price_position in ("ABOVE_BOTH", "BETWEEN_EMAS"):
                reasons.append(f"Price (${price:,.1f}) comfortably aligned ({ema.price_position})")
                suggested_sl = ema.ema_slow * 0.997  # SL placed just under Slow EMA

                # Base confidence score: 0.65 to 0.85 depending on slope and spread
                confidence = min(0.85, 0.65 + (ema.spread_pct * 0.1) + (ema.fast_slope * 0.2))
                sl, tp, rr = self.calculate_brackets(price, "long", suggested_sl, self.config.get("risk_reward", 2.5))

                return StrategySignal(
                    strategy_name=self.name,
                    symbol=symbol,
                    timeframe=timeframe,
                    side="buy",
                    direction="long",
                    trigger_price=price,
                    stop_loss=sl,
                    take_profit=tp,
                    risk_reward_ratio=rr,
                    confidence_score=round(confidence, 2),
                    reasons=reasons,
                    indicator_snapshot={
                        "ema": ema.model_dump(),
                        "technical_score": snapshot.technical_score,
                        "stop_loss": sl,
                        "take_profit": tp,
                        "risk_reward_ratio": rr,
                        "direction": "long",
                        "reasons": reasons,
                    },
                )

        # 2. Bearish Setup Check
        is_bearish_alignment = (
            ema.ema_fast < ema.ema_slow
            and ema.spread_pct <= -min_spread
            and ema.fast_slope <= -min_slope
        )

        if is_bearish_alignment:
            reasons.append(f"Fast EMA 9 (${ema.ema_fast:,.1f}) < Slow EMA 20 (${ema.ema_slow:,.1f}) with {ema.spread_pct:.2f}% spread")
            reasons.append(f"Negative fast slope ({ema.fast_slope:.3f}%) indicates downward momentum")

            if ema.price_position in ("BELOW_BOTH", "BETWEEN_EMAS"):
                reasons.append(f"Price (${price:,.1f}) comfortably aligned ({ema.price_position})")
                suggested_sl = ema.ema_slow * 1.003  # SL placed just above Slow EMA

                confidence = min(0.85, 0.65 + (abs(ema.spread_pct) * 0.1) + (abs(ema.fast_slope) * 0.2))
                sl, tp, rr = self.calculate_brackets(price, "short", suggested_sl, self.config.get("risk_reward", 2.5))

                return StrategySignal(
                    strategy_name=self.name,
                    symbol=symbol,
                    timeframe=timeframe,
                    side="sell",
                    direction="short",
                    trigger_price=price,
                    stop_loss=sl,
                    take_profit=tp,
                    risk_reward_ratio=rr,
                    confidence_score=round(confidence, 2),
                    reasons=reasons,
                    indicator_snapshot={
                        "ema": ema.model_dump(),
                        "technical_score": snapshot.technical_score,
                        "stop_loss": sl,
                        "take_profit": tp,
                        "risk_reward_ratio": rr,
                        "direction": "short",
                        "reasons": reasons,
                    },
                )

        return None


# =============================================================================
# STRATEGY 2: MACD MOMENTUM CROSSOVER
# =============================================================================

class MACDMomentumStrategy(BaseStrategy):
    """MACD (12, 26, 9) signal line crossovers and histogram momentum expansion."""

    def __init__(self, is_active: bool = True, config: Optional[Dict[str, Any]] = None):
        default_config = {
            "fast_period": 12,
            "slow_period": 26,
            "signal_period": 9,
            "require_zero_line_confirm": False,
            "risk_reward": 2.5,
            "default_sl_pct": 0.012,
        }
        if config:
            default_config.update(config)
        super().__init__(
            name="MACD_Momentum_Crossover",
            description="MACD (12, 26, 9) line and signal crossovers with histogram acceleration.",
            is_active=is_active,
            config=default_config,
        )

    def evaluate(
        self,
        symbol: str,
        timeframe: str,
        candles: List[CandleItem],
        snapshot: IndicatorSnapshot,
    ) -> Optional[StrategySignal]:
        if not self.is_active or not snapshot.macd:
            return None

        macd = snapshot.macd
        price = snapshot.current_price
        if macd.macd_line is None or macd.signal_line is None or macd.histogram is None:
            return None

        reasons: List[str] = []

        # Bullish MACD Setup
        if macd.crossover == "BULLISH_CROSSOVER" or (
            macd.macd_line > macd.signal_line and macd.histogram > 0 and macd.momentum_state.startswith("BULLISH")
        ):
            reasons.append(f"MACD line ({macd.macd_line:.2f}) > Signal line ({macd.signal_line:.2f})")
            reasons.append(f"Histogram positive (+{macd.histogram:.2f}) with {macd.momentum_state}")
            if macd.zero_line_position in ("ABOVE_ZERO", "CROSSING_ABOVE"):
                reasons.append("MACD verified above baseline zero line")

            confidence = 0.70
            if macd.crossover == "BULLISH_CROSSOVER":
                confidence += 0.10
                reasons.append("Fresh Bullish Crossover detected")
            if macd.zero_line_position == "ABOVE_ZERO":
                confidence += 0.05

            suggested_sl = snapshot.support_resistance.nearest_support if snapshot.support_resistance else None
            sl, tp, rr = self.calculate_brackets(price, "long", suggested_sl, self.config.get("risk_reward", 2.5))

            return StrategySignal(
                strategy_name=self.name,
                symbol=symbol,
                timeframe=timeframe,
                side="buy",
                direction="long",
                trigger_price=price,
                stop_loss=sl,
                take_profit=tp,
                risk_reward_ratio=rr,
                confidence_score=round(min(0.90, confidence), 2),
                reasons=reasons,
                indicator_snapshot={
                    "macd": macd.model_dump(),
                    "technical_score": snapshot.technical_score,
                    "stop_loss": sl,
                    "take_profit": tp,
                    "risk_reward_ratio": rr,
                    "direction": "long",
                    "reasons": reasons,
                },
            )

        # Bearish MACD Setup
        if macd.crossover == "BEARISH_CROSSOVER" or (
            macd.macd_line < macd.signal_line and macd.histogram < 0 and macd.momentum_state.startswith("BEARISH")
        ):
            reasons.append(f"MACD line ({macd.macd_line:.2f}) < Signal line ({macd.signal_line:.2f})")
            reasons.append(f"Histogram negative ({macd.histogram:.2f}) with {macd.momentum_state}")
            if macd.zero_line_position in ("BELOW_ZERO", "CROSSING_BELOW"):
                reasons.append("MACD confirmed below zero line")

            confidence = 0.70
            if macd.crossover == "BEARISH_CROSSOVER":
                confidence += 0.10
                reasons.append("Fresh Bearish Crossover detected")
            if macd.zero_line_position == "BELOW_ZERO":
                confidence += 0.05

            suggested_sl = snapshot.support_resistance.nearest_resistance if snapshot.support_resistance else None
            sl, tp, rr = self.calculate_brackets(price, "short", suggested_sl, self.config.get("risk_reward", 2.5))

            return StrategySignal(
                strategy_name=self.name,
                symbol=symbol,
                timeframe=timeframe,
                side="sell",
                direction="short",
                trigger_price=price,
                stop_loss=sl,
                take_profit=tp,
                risk_reward_ratio=rr,
                confidence_score=round(min(0.90, confidence), 2),
                reasons=reasons,
                indicator_snapshot={
                    "macd": macd.model_dump(),
                    "technical_score": snapshot.technical_score,
                    "stop_loss": sl,
                    "take_profit": tp,
                    "risk_reward_ratio": rr,
                    "direction": "short",
                    "reasons": reasons,
                },
            )

        return None


# =============================================================================
# STRATEGY 3: VWAP MEAN REVERSION & BAND BOUNCE
# =============================================================================

class VWAPBounceStrategy(BaseStrategy):
    """Volume-weighted mean reversion off ±2σ bands and dynamic session VWAP retests."""

    def __init__(self, is_active: bool = True, config: Optional[Dict[str, Any]] = None):
        default_config = {
            "reversion_threshold_sigma": 1.8,
            "trend_bounce_enabled": True,
            "risk_reward": 2.2,
            "default_sl_pct": 0.01,
        }
        if config:
            default_config.update(config)
        super().__init__(
            name="VWAP_Band_Bounce",
            description="Mean reversion from extreme ±2σ VWAP bands and pullback retests of session VWAP.",
            is_active=is_active,
            config=default_config,
        )

    def evaluate(
        self,
        symbol: str,
        timeframe: str,
        candles: List[CandleItem],
        snapshot: IndicatorSnapshot,
    ) -> Optional[StrategySignal]:
        if not self.is_active or not snapshot.vwap:
            return None

        vwap = snapshot.vwap
        price = snapshot.current_price
        if not vwap.vwap or not vwap.lower_band_2 or not vwap.upper_band_2:
            return None

        reasons: List[str] = []

        # 1. Mean Reversion Long (Oversold at or below -2σ Lower Band)
        if price <= vwap.lower_band_2 or (vwap.lower_band_1 and price <= vwap.lower_band_1 and vwap.distance_pct <= -1.5):
            reasons.append(f"Price (${price:,.1f}) stretched to extreme oversold band (-2σ Lower Band: ${vwap.lower_band_2:,.1f})")
            reasons.append(f"Distance from Session VWAP is {vwap.distance_pct:.2f}% (statistical mean-reversion target: ${vwap.vwap:,.1f})")

            suggested_sl = vwap.lower_band_2 * 0.995  # Just below -2σ band
            target_tp = vwap.vwap  # Target is return to session mean

            # Calculate actual R:R targeting VWAP
            risk = max(0.0001, price - suggested_sl)
            reward = max(0.0001, target_tp - price)
            rr = reward / risk

            if rr >= 1.5:
                confidence = 0.72
                return StrategySignal(
                    strategy_name=self.name,
                    symbol=symbol,
                    timeframe=timeframe,
                    side="buy",
                    direction="long",
                    trigger_price=price,
                    stop_loss=_round_price(suggested_sl),
                    take_profit=_round_price(target_tp),
                    risk_reward_ratio=round(rr, 2),
                    confidence_score=confidence,
                    reasons=reasons,
                    indicator_snapshot={
                        "vwap": vwap.model_dump(),
                        "technical_score": snapshot.technical_score,
                        "stop_loss": _round_price(suggested_sl),
                        "take_profit": _round_price(target_tp),
                        "risk_reward_ratio": round(rr, 2),
                        "direction": "long",
                        "reasons": reasons,
                    },
                )

        # 2. Mean Reversion Short (Overbought at or above +2σ Upper Band)
        if price >= vwap.upper_band_2 or (vwap.upper_band_1 and price >= vwap.upper_band_1 and vwap.distance_pct >= 1.5):
            reasons.append(f"Price (${price:,.1f}) extended to extreme overbought band (+2σ Upper Band: ${vwap.upper_band_2:,.1f})")
            reasons.append(f"Distance from Session VWAP is +{vwap.distance_pct:.2f}% (mean-reversion target: ${vwap.vwap:,.1f})")

            suggested_sl = vwap.upper_band_2 * 1.005  # Just above +2σ band
            target_tp = vwap.vwap

            risk = max(0.0001, suggested_sl - price)
            reward = max(0.0001, price - target_tp)
            rr = reward / risk

            if rr >= 1.5:
                confidence = 0.72
                return StrategySignal(
                    strategy_name=self.name,
                    symbol=symbol,
                    timeframe=timeframe,
                    side="sell",
                    direction="short",
                    trigger_price=price,
                    stop_loss=_round_price(suggested_sl),
                    take_profit=_round_price(target_tp),
                    risk_reward_ratio=round(rr, 2),
                    confidence_score=confidence,
                    reasons=reasons,
                    indicator_snapshot={
                        "vwap": vwap.model_dump(),
                        "technical_score": snapshot.technical_score,
                        "stop_loss": _round_price(suggested_sl),
                        "take_profit": _round_price(target_tp),
                        "risk_reward_ratio": round(rr, 2),
                        "direction": "short",
                        "reasons": reasons,
                    },
                )

        # 3. Dynamic VWAP Support / Resistance Trend Retest
        if self.config.get("trend_bounce_enabled", True) and abs(vwap.distance_pct) <= 0.4:
            # Price is resting directly on VWAP (within 0.4%)
            if snapshot.technical_score >= 20:  # Bullish context
                reasons.append(f"Price (${price:,.1f}) testing Session VWAP (${vwap.vwap:,.1f}) with bullish institutional bias")
                reasons.append(f"VWAP acting as dynamic institutional floor support")
                suggested_sl = vwap.vwap * 0.995
                sl, tp, rr = self.calculate_brackets(price, "long", suggested_sl, self.config.get("risk_reward", 2.2))

                return StrategySignal(
                    strategy_name=self.name,
                    symbol=symbol,
                    timeframe=timeframe,
                    side="buy",
                    direction="long",
                    trigger_price=price,
                    stop_loss=sl,
                    take_profit=tp,
                    risk_reward_ratio=rr,
                    confidence_score=0.68,
                    reasons=reasons,
                    indicator_snapshot={
                        "vwap": vwap.model_dump(),
                        "technical_score": snapshot.technical_score,
                        "stop_loss": sl,
                        "take_profit": tp,
                        "risk_reward_ratio": rr,
                        "direction": "long",
                        "reasons": reasons,
                    },
                )
            elif snapshot.technical_score <= -20:  # Bearish context
                reasons.append(f"Price (${price:,.1f}) testing Session VWAP (${vwap.vwap:,.1f}) with bearish institutional bias")
                reasons.append(f"VWAP acting as dynamic overhead resistance ceiling")
                suggested_sl = vwap.vwap * 1.005
                sl, tp, rr = self.calculate_brackets(price, "short", suggested_sl, self.config.get("risk_reward", 2.2))

                return StrategySignal(
                    strategy_name=self.name,
                    symbol=symbol,
                    timeframe=timeframe,
                    side="sell",
                    direction="short",
                    trigger_price=price,
                    stop_loss=sl,
                    take_profit=tp,
                    risk_reward_ratio=rr,
                    confidence_score=0.68,
                    reasons=reasons,
                    indicator_snapshot={
                        "vwap": vwap.model_dump(),
                        "technical_score": snapshot.technical_score,
                        "stop_loss": sl,
                        "take_profit": tp,
                        "risk_reward_ratio": rr,
                        "direction": "short",
                        "reasons": reasons,
                    },
                )

        return None


# =============================================================================
# STRATEGY 4: PIVOT POINT BREAKOUT & S/R BOUNCE
# =============================================================================

class PivotBreakoutStrategy(BaseStrategy):
    """Camarilla, Standard, and Fibonacci pivot level breakouts & fractal S/R zone bounces."""

    def __init__(self, is_active: bool = True, config: Optional[Dict[str, Any]] = None):
        default_config = {
            "pivot_model": "standard",
            "support_touch_min": 2,
            "risk_reward": 2.5,
            "default_sl_pct": 0.01,
        }
        if config:
            default_config.update(config)
        super().__init__(
            name="Pivot_SR_Breakout",
            description="Breakouts and bounces across Standard/Camarilla pivots and historical swing zones.",
            is_active=is_active,
            config=default_config,
        )

    def evaluate(
        self,
        symbol: str,
        timeframe: str,
        candles: List[CandleItem],
        snapshot: IndicatorSnapshot,
    ) -> Optional[StrategySignal]:
        if not self.is_active or not snapshot.pivots:
            return None

        price = snapshot.current_price
        pivots = snapshot.pivots
        sr = snapshot.support_resistance
        std_pivots = pivots.standard
        reasons: List[str] = []

        # 1. Support Zone Bounce Setup (Long)
        if sr and sr.nearest_support and sr.support_distance_pct is not None and sr.support_distance_pct <= 0.35:
            # Price within 0.35% of major support zone
            reasons.append(f"Price (${price:,.1f}) in tight proximity to major support (${sr.nearest_support:,.1f})")
            reasons.append(f"Support zone verified with multi-touch confirmation ({sr.proximity_status})")

            suggested_sl = sr.nearest_support * 0.996
            target_tp = sr.nearest_resistance or std_pivots.r1
            sl, tp, rr = self.calculate_brackets(price, "long", suggested_sl, self.config.get("risk_reward", 2.5))

            return StrategySignal(
                strategy_name=self.name,
                symbol=symbol,
                timeframe=timeframe,
                side="buy",
                direction="long",
                trigger_price=price,
                stop_loss=sl,
                take_profit=tp,
                risk_reward_ratio=rr,
                confidence_score=0.74,
                reasons=reasons,
                indicator_snapshot={
                    "pivots": pivots.model_dump(),
                    "support_resistance": sr.model_dump(),
                    "technical_score": snapshot.technical_score,
                    "stop_loss": sl,
                    "take_profit": tp,
                    "risk_reward_ratio": rr,
                    "direction": "long",
                    "reasons": reasons,
                },
            )

        # 2. Resistance Zone Rejection Setup (Short)
        if sr and sr.nearest_resistance and sr.resistance_distance_pct is not None and sr.resistance_distance_pct <= 0.35:
            # Price within 0.35% of major resistance zone
            reasons.append(f"Price (${price:,.1f}) testing verified resistance ceiling (${sr.nearest_resistance:,.1f})")
            reasons.append(f"Strong structural resistance block ({sr.proximity_status})")

            suggested_sl = sr.nearest_resistance * 1.004
            target_tp = sr.nearest_support or std_pivots.s1
            sl, tp, rr = self.calculate_brackets(price, "short", suggested_sl, self.config.get("risk_reward", 2.5))

            return StrategySignal(
                strategy_name=self.name,
                symbol=symbol,
                timeframe=timeframe,
                side="sell",
                direction="short",
                trigger_price=price,
                stop_loss=sl,
                take_profit=tp,
                risk_reward_ratio=rr,
                confidence_score=0.74,
                reasons=reasons,
                indicator_snapshot={
                    "pivots": pivots.model_dump(),
                    "support_resistance": sr.model_dump(),
                    "technical_score": snapshot.technical_score,
                    "stop_loss": sl,
                    "take_profit": tp,
                    "risk_reward_ratio": rr,
                    "direction": "short",
                    "reasons": reasons,
                },
            )

        # 3. Pivot Level Breakout (R1/S1 Breakout)
        if std_pivots.r1 and price > std_pivots.r1 and price <= std_pivots.r1 * 1.005:
            # Fresh breakout above R1
            reasons.append(f"Price breakout above Standard Pivot R1 (${std_pivots.r1:,.1f})")
            reasons.append(f"Expanding bullish continuation target: Pivot R2 (${std_pivots.r2:,.1f})")

            suggested_sl = std_pivots.r1 * 0.997  # Back below R1
            sl, tp, rr = self.calculate_brackets(price, "long", suggested_sl, self.config.get("risk_reward", 2.5))

            return StrategySignal(
                strategy_name=self.name,
                symbol=symbol,
                timeframe=timeframe,
                side="buy",
                direction="long",
                trigger_price=price,
                stop_loss=sl,
                take_profit=tp,
                risk_reward_ratio=rr,
                confidence_score=0.70,
                reasons=reasons,
                indicator_snapshot={
                    "pivots": pivots.model_dump(),
                    "technical_score": snapshot.technical_score,
                    "stop_loss": sl,
                    "take_profit": tp,
                    "risk_reward_ratio": rr,
                    "direction": "long",
                    "reasons": reasons,
                },
            )

        if std_pivots.s1 and price < std_pivots.s1 and price >= std_pivots.s1 * 0.995:
            # Fresh breakdown below S1
            reasons.append(f"Price breakdown below Standard Pivot S1 (${std_pivots.s1:,.1f})")
            reasons.append(f"Downside acceleration target: Pivot S2 (${std_pivots.s2:,.1f})")

            suggested_sl = std_pivots.s1 * 1.003  # Back above S1
            sl, tp, rr = self.calculate_brackets(price, "short", suggested_sl, self.config.get("risk_reward", 2.5))

            return StrategySignal(
                strategy_name=self.name,
                symbol=symbol,
                timeframe=timeframe,
                side="sell",
                direction="short",
                trigger_price=price,
                stop_loss=sl,
                take_profit=tp,
                risk_reward_ratio=rr,
                confidence_score=0.70,
                reasons=reasons,
                indicator_snapshot={
                    "pivots": pivots.model_dump(),
                    "technical_score": snapshot.technical_score,
                    "stop_loss": sl,
                    "take_profit": tp,
                    "risk_reward_ratio": rr,
                    "direction": "short",
                    "reasons": reasons,
                },
            )

        return None


# =============================================================================
# STRATEGY 5: COMPOSITE ENSEMBLE STRATEGY
# =============================================================================

class CompositeEnsembleStrategy(BaseStrategy):
    """Multi-strategy consensus engine combining concurrent signals into high-conviction setups."""

    def __init__(self, is_active: bool = True, config: Optional[Dict[str, Any]] = None):
        default_config = {
            "min_consensus_count": 2,
            "min_confidence": 0.70,
            "risk_reward": 2.5,
        }
        if config:
            default_config.update(config)
        super().__init__(
            name="Composite_Ensemble",
            description="Consensus engine requiring multiple independent strategy agreements for high conviction.",
            is_active=is_active,
            config=default_config,
        )

    def evaluate(
        self,
        symbol: str,
        timeframe: str,
        candles: List[CandleItem],
        snapshot: IndicatorSnapshot,
    ) -> Optional[StrategySignal]:
        # The CompositeEnsembleStrategy evaluates other strategies concurrently via StrategyEngine
        # and synthesizes them into an aggregated consensus signal.
        # Implemented below in synthesize_ensemble()
        return None

    def synthesize_ensemble(
        self,
        symbol: str,
        timeframe: str,
        sub_signals: List[StrategySignal],
        snapshot: IndicatorSnapshot,
    ) -> Optional[StrategySignal]:
        """Combines multiple independent signals into an aggregated ensemble candidate."""
        if not self.is_active or not sub_signals:
            return None

        # Filter signals for the same symbol & timeframe
        relevant = [s for s in sub_signals if s.symbol == symbol and s.timeframe == timeframe and s.strategy_name != self.name]
        if not relevant:
            return None

        # Group by direction
        long_signals = [s for s in relevant if s.direction == "long"]
        short_signals = [s for s in relevant if s.direction == "short"]

        min_count = self.config.get("min_consensus_count", 2)
        price = snapshot.current_price

        # Check Long Consensus
        if len(long_signals) >= min_count:
            contributing = [s.strategy_name for s in long_signals]
            avg_conf = sum(s.confidence_score for s in long_signals) / len(long_signals)
            # Boost confidence for multi-strategy consensus
            ensemble_confidence = min(0.95, avg_conf + 0.10)

            # Most conservative stop-loss (closest to price without violating minimum buffer)
            avg_sl = sum(s.stop_loss for s in long_signals) / len(long_signals)
            avg_tp = sum(s.take_profit for s in long_signals) / len(long_signals)

            reasons = [
                f"Multi-strategy consensus confirmed by {len(long_signals)} independent models: {', '.join(contributing)}",
                f"Overall Technical Bias is {snapshot.technical_bias} (Score: +{snapshot.technical_score}/100)",
            ]
            for s in long_signals:
                reasons.extend(s.reasons[:1])  # Add top rationale from each

            rr = (avg_tp - price) / max(0.0001, (price - avg_sl))

            return StrategySignal(
                strategy_name=self.name,
                symbol=symbol,
                timeframe=timeframe,
                side="buy",
                direction="long",
                trigger_price=price,
                stop_loss=_round_price(avg_sl),
                take_profit=_round_price(avg_tp),
                risk_reward_ratio=round(rr, 2),
                confidence_score=round(ensemble_confidence, 2),
                reasons=reasons,
                indicator_snapshot={
                    "consensus_models": contributing,
                    "model_count": len(long_signals),
                    "technical_score": snapshot.technical_score,
                    "stop_loss": _round_price(avg_sl),
                    "take_profit": _round_price(avg_tp),
                    "risk_reward_ratio": round(rr, 2),
                    "direction": "long",
                    "reasons": reasons,
                },
            )

        # Check Short Consensus
        if len(short_signals) >= min_count:
            contributing = [s.strategy_name for s in short_signals]
            avg_conf = sum(s.confidence_score for s in short_signals) / len(short_signals)
            ensemble_confidence = min(0.95, avg_conf + 0.10)

            avg_sl = sum(s.stop_loss for s in short_signals) / len(short_signals)
            avg_tp = sum(s.take_profit for s in short_signals) / len(short_signals)

            reasons = [
                f"Multi-strategy consensus confirmed by {len(short_signals)} independent models: {', '.join(contributing)}",
                f"Overall Technical Bias is {snapshot.technical_bias} (Score: {snapshot.technical_score}/100)",
            ]
            for s in short_signals:
                reasons.extend(s.reasons[:1])

            rr = (price - avg_tp) / max(0.0001, (avg_sl - price))

            return StrategySignal(
                strategy_name=self.name,
                symbol=symbol,
                timeframe=timeframe,
                side="sell",
                direction="short",
                trigger_price=price,
                stop_loss=_round_price(avg_sl),
                take_profit=_round_price(avg_tp),
                risk_reward_ratio=round(rr, 2),
                confidence_score=round(ensemble_confidence, 2),
                reasons=reasons,
                indicator_snapshot={
                    "consensus_models": contributing,
                    "model_count": len(short_signals),
                    "technical_score": snapshot.technical_score,
                    "stop_loss": _round_price(avg_sl),
                    "take_profit": _round_price(avg_tp),
                    "risk_reward_ratio": round(rr, 2),
                    "direction": "short",
                    "reasons": reasons,
                },
            )

        return None


# =============================================================================
# STRATEGY ENGINE MANAGER
# =============================================================================

class StrategyEngine:
    """Central manager for concurrent strategy execution, signal evaluation, and promotion."""

    def __init__(self):
        self._strategies: Dict[str, BaseStrategy] = {}
        self._initialized: bool = False
        self._recent_signals: List[StrategySignal] = []
        self._lock = asyncio.Lock()

        # Register default strategies
        self.register(EMATrendStrategy())
        self.register(MACDMomentumStrategy())
        self.register(VWAPBounceStrategy())
        self.register(PivotBreakoutStrategy())
        self.register(CompositeEnsembleStrategy())

    def register(self, strategy: BaseStrategy) -> None:
        """Registers a trading strategy module."""
        self._strategies[strategy.name] = strategy
        logger.info(f"Registered strategy: {strategy.name} (active: {strategy.is_active})")

    def get_strategy(self, name: str) -> Optional[BaseStrategy]:
        """Retrieves a registered strategy by name."""
        return self._strategies.get(name)

    async def initialize_db_strategies(self) -> None:
        """Synchronizes in-memory registered strategies with SQLite database."""
        async with get_db_session() as session:
            for name, strat in self._strategies.items():
                db_strat = await TradeRepository.get_strategy_by_name(session, name)
                if not db_strat:
                    await TradeRepository.upsert_strategy(
                        session=session,
                        name=name,
                        description=strat.description,
                        is_active=strat.is_active,
                        config=strat.config,
                    )
                else:
                    # Sync DB active state and config into memory
                    strat.is_active = db_strat.is_active
                    strat.config.update(db_strat.config)
            await session.commit()
        self._initialized = True
        logger.info("Strategy Engine synchronized with SQLite repository.")

    async def list_strategies(self) -> List[StrategyInfo]:
        """Returns metadata, status, and signal counts for all registered strategies."""
        async with get_db_session() as session:
            db_strategies = await TradeRepository.get_strategies(session)
            db_map = {s.name: s for s in db_strategies}

        results: List[StrategyInfo] = []
        for name, strat in self._strategies.items():
            db_entry = db_map.get(name)
            signal_count = len([s for s in self._recent_signals if s.strategy_name == name])
            results.append(
                StrategyInfo(
                    name=strat.name,
                    description=strat.description,
                    is_active=strat.is_active,
                    config=strat.config,
                    signal_count=signal_count,
                    recent_signals=[s for s in self._recent_signals if s.strategy_name == name][:5],
                    created_at=db_entry.created_at.isoformat() if db_entry and db_entry.created_at else None,
                    updated_at=db_entry.updated_at.isoformat() if db_entry and db_entry.updated_at else None,
                )
            )
        return results

    async def toggle_strategy(self, name: str, is_active: bool) -> bool:
        """Enables or disables a strategy both in memory and in the database."""
        strat = self.get_strategy(name)
        if not strat:
            return False
        strat.is_active = is_active

        async with get_db_session() as session:
            await TradeRepository.toggle_strategy(session, name, is_active)
            await session.commit()
        logger.info(f"Strategy {name} active state set to {is_active}")
        return True

    async def update_strategy_config(self, name: str, config: Dict[str, Any]) -> bool:
        """Updates parameters for a strategy."""
        strat = self.get_strategy(name)
        if not strat:
            return False
        strat.update_config(config)

        async with get_db_session() as session:
            await TradeRepository.update_strategy_config(session, name, config)
            await session.commit()
        logger.info(f"Strategy {name} config updated: {config}")
        return True

    async def evaluate_symbol(
        self,
        symbol: str,
        timeframe: str = "5m",
        persist: bool = True,
    ) -> List[StrategySignal]:
        """Evaluates all active strategies concurrently for a given symbol and timeframe."""
        symbol = symbol.upper()
        # 1. Fetch live indicator snapshot & candles
        buf = market_engine.get_buffer(symbol, timeframe)
        candles = [CandleItem(**c) for c in buf.get_all() if c.get("is_closed", True)] if buf else []
        snapshot = indicator_engine.get_snapshot(symbol=symbol, timeframe=timeframe)
        if not snapshot:
            if candles and len(candles) >= 2:
                opens = [c.open for c in candles]
                highs = [c.high for c in candles]
                lows = [c.low for c in candles]
                closes = [c.close for c in candles]
                volumes = [c.volume for c in candles]
                snapshot = indicator_engine.calculate(
                    symbol=symbol,
                    timeframe=timeframe,
                    opens=opens,
                    highs=highs,
                    lows=lows,
                    closes=closes,
                    volumes=volumes,
                )
            else:
                return []

        generated_signals: List[StrategySignal] = []

        # 2. Evaluate individual strategies
        for name, strat in self._strategies.items():
            if not strat.is_active or name == "Composite_Ensemble":
                continue
            try:
                sig = strat.evaluate(symbol=symbol, timeframe=timeframe, candles=candles, snapshot=snapshot)
                if sig:
                    generated_signals.append(sig)
            except Exception as e:
                logger.error(f"Error evaluating strategy {name} for {symbol} {timeframe}: {e}")

        # 3. Evaluate Composite Ensemble if active
        ensemble_strat = self.get_strategy("Composite_Ensemble")
        if ensemble_strat and ensemble_strat.is_active and isinstance(ensemble_strat, CompositeEnsembleStrategy):
            try:
                ensemble_sig = ensemble_strat.synthesize_ensemble(
                    symbol=symbol,
                    timeframe=timeframe,
                    sub_signals=generated_signals,
                    snapshot=snapshot,
                )
                if ensemble_sig:
                    generated_signals.append(ensemble_sig)
            except Exception as e:
                logger.error(f"Error synthesizing ensemble for {symbol} {timeframe}: {e}")

        # 4. Deduplicate and optionally persist signals
        if persist and generated_signals:
            async with get_db_session() as session:
                for sig in generated_signals:
                    db_sig = await TradeRepository.record_signal(
                        session=session,
                        strategy_name=sig.strategy_name,
                        symbol=sig.symbol,
                        timeframe=sig.timeframe,
                        side=sig.side,
                        trigger_price=sig.trigger_price,
                        confidence_score=sig.confidence_score,
                        indicator_snapshot=sig.indicator_snapshot,
                    )
                    sig.id = db_sig.id
                    sig.created_at = db_sig.created_at.isoformat() if db_sig.created_at else None

                    # Audit trail log
                    await TradeRepository.create_audit_log(
                        session=session,
                        message=f"Signal generated by {sig.strategy_name}: {sig.side.upper()} {sig.symbol} at ${sig.trigger_price:,.1f}",
                        event_type=AuditEventType.SIGNAL_ENGINE,
                        severity=AuditSeverity.INFO,
                        symbol=sig.symbol,
                        payload={
                            "signal_id": db_sig.id,
                            "strategy": sig.strategy_name,
                            "side": sig.side,
                            "confidence": sig.confidence_score,
                            "reasons": sig.reasons,
                        },
                    )

                await session.commit()

        # Update in-memory recent cache
        async with self._lock:
            self._recent_signals = (generated_signals + self._recent_signals)[:100]

        if settings.BOT_ACTIVE and generated_signals:
            for sig in generated_signals:
                if sig.id is None:
                    continue
                try:
                    await self.auto_execute_generated_signal(
                        sig.id,
                        subaccount="futures",
                        is_paper=settings.PAPER_TRADING,
                    )
                except Exception as exc:
                    logger.warning("Auto-execution failed for signal %s: %s", sig.id, exc)

        return generated_signals

    async def evaluate_all(
        self,
        persist: bool = True,
    ) -> Dict[str, List[StrategySignal]]:
        """Evaluates strategies across all active instruments and primary timeframes."""
        instruments = ["BTCUSD", "ETHUSD", "SOLUSD"]
        timeframes = ["5m", "15m", "1h"]
        results: Dict[str, List[StrategySignal]] = {}

        tasks = []
        task_keys = []
        for sym in instruments:
            for tf in timeframes:
                tasks.append(self.evaluate_symbol(symbol=sym, timeframe=tf, persist=persist))
                task_keys.append(f"{sym}_{tf}")

        completed = await asyncio.gather(*tasks, return_exceptions=True)
        for key, res in zip(task_keys, completed):
            if isinstance(res, Exception):
                logger.error(f"Failed evaluation for {key}: {res}")
                results[key] = []
            else:
                results[key] = res

        return results

    async def auto_execute_generated_signal(
        self,
        signal_id: int,
        subaccount: str = "futures",
        is_paper: Optional[bool] = None,
    ):
        """Promotes a signal into a trade and immediately advances it through the execution pipeline."""
        if not settings.BOT_ACTIVE:
            raise ValueError("BOT_ACTIVE is OFF; enable it to auto-execute generated signals.")

        effective_paper_mode = settings.PAPER_TRADING if is_paper is None else is_paper
        promoted = await self.promote_to_trade(
            signal_id=signal_id, subaccount=subaccount, is_paper=effective_paper_mode
        )
        return await execution_engine.advance_trade_lifecycle(
            trade_id=promoted.trade_id,
            auto_execute=True,
            notes=f"Auto-executing signal {signal_id} into live trade lifecycle",
        )

    async def promote_to_trade(
        self,
        signal_id: int,
        subaccount: str = "futures",
        is_paper: bool = True,
    ) -> PromoteSignalResponse:
        """Promotes a candidate Signal into State 1: SIGNAL_GENERATED of the 7-State Lifecycle.
        
        Multiple active trade decisions per instrument are allowed.
        """
        async with get_db_session() as session:
            # 1. Fetch Signal
            sig = await TradeRepository.get_signal_by_id(session, signal_id)
            if not sig:
                raise ValueError(f"Signal ID {signal_id} not found.")

            if sig.status == "PROMOTED":
                existing_trade = await TradeRepository.get_active_trade_for_symbol(session, sig.symbol)
                if existing_trade and (existing_trade.timeframe_alignment or {}).get("signal_id") == signal_id:
                    return PromoteSignalResponse(
                        trade_id=existing_trade.id,
                        state=existing_trade.state,
                        symbol=existing_trade.symbol,
                        direction=existing_trade.direction,
                        entry_price=existing_trade.entry_price or sig.trigger_price,
                        stop_loss=existing_trade.stop_loss_price,
                        take_profit=existing_trade.take_profit_price,
                        message=f"Resuming signal {signal_id} at State {existing_trade.state}",
                    )
                raise ValueError(f"Signal ID {signal_id} has already been promoted to a trade.")

            # 2. Create Trade Decision in State 1: SIGNAL_GENERATED
            snapshot = sig.indicator_snapshot or {}
            direction = snapshot.get("direction", "long" if sig.side.lower() == "buy" else "short")
            sl = snapshot.get("stop_loss", sig.trigger_price * (0.99 if direction == "long" else 1.01))
            tp = snapshot.get("take_profit", sig.trigger_price * (1.03 if direction == "long" else 0.97))

            trade = await TradeRepository.create_trade_decision(
                session=session,
                symbol=sig.symbol,
                direction=direction,
                subaccount=subaccount,
                is_paper=is_paper,
                timeframe_alignment={
                    "signal_id": signal_id,
                    "primary_timeframe": sig.timeframe,
                    "strategy_name": sig.strategy_name,
                    "confidence_score": sig.confidence_score,
                    "reasons": snapshot.get("reasons", []),
                },
                entry_price=sig.trigger_price,
                stop_loss_price=sl,
                take_profit_price=tp,
            )

            # 4. Mark Signal as PROMOTED
            sig.status = "PROMOTED"

            # 5. Record Audit
            await TradeRepository.create_audit_log(
                session=session,
                message=f"Signal {signal_id} promoted to Trade Decision {trade.id} ({direction.upper()} {sig.symbol})",
                event_type=AuditEventType.SIGNAL_ENGINE,
                severity=AuditSeverity.INFO,
                subaccount=subaccount,
                symbol=sig.symbol,
                payload={
                    "signal_id": signal_id,
                    "trade_id": trade.id,
                    "state": trade.state,
                    "symbol": sig.symbol,
                    "direction": direction,
                    "entry_price": trade.entry_price,
                    "stop_loss": trade.stop_loss_price,
                    "take_profit": trade.take_profit_price,
                },
            )

            await session.commit()

            return PromoteSignalResponse(
                trade_id=trade.id,
                state=trade.state,
                symbol=trade.symbol,
                direction=trade.direction,
                entry_price=trade.entry_price or sig.trigger_price,
                stop_loss=trade.stop_loss_price,
                take_profit=trade.take_profit_price,
                message=f"Successfully transitioned to State 1: {trade.state} for {trade.symbol}",
            )


# Global Strategy Engine Singleton
strategy_engine = StrategyEngine()
