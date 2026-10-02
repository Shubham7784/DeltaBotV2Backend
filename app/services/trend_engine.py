"""Multi-Timeframe Trend Alignment Engine (Phase 8).

Harmonizes market bias across three core operational timeframes:
1. 1h Macro Trend: Establishes broad institutional market bias and directional filter.
2. 15m Structural Trend: Evaluates momentum continuation, swing structures, and MACD health.
3. 5m Tactical Entry: Pins precision entry triggers, short-term EMA crossovers, and immediate execution signals.

Invariants:
- 3/3 Alignment (Full): Highest confidence (1.0x), ideal for aggressive swing/trend-following setups.
- 2/3 Alignment (Moderate): Acceptable confidence (0.75x), requires tighter invalidation and cautionary sizing.
- 1/3 Alignment (Divergent/Conflict): Low confidence (0.40x), flags counter-trend risk. Signals are rejected or flagged for advisory caution.
"""

import logging
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.schemas.indicator import IndicatorSnapshot
from app.services.indicator_engine import indicator_engine
from app.services.market_data_engine import market_engine

logger = logging.getLogger("delta_bot.trend_engine")


class TimeframeDetail(BaseModel):
    """Detailed trend classification for a single timeframe."""
    timeframe: str
    trend: str = Field(..., description="BULLISH | BEARISH | NEUTRAL")
    technical_score: int
    technical_bias: str
    ema_trend: str
    ema_spread_pct: float
    macd_state: str
    vwap_bias: str
    current_price: float


class TimeframeAlignmentReport(BaseModel):
    """Comprehensive multi-timeframe alignment report for an instrument."""
    symbol: str
    current_price: float
    directional_bias: str = Field(..., description="STRONG_BULLISH | BULLISH | NEUTRAL | BEARISH | STRONG_BEARISH")
    alignment_degree: str = Field(..., description="FULL_ALIGNMENT_3_OF_3 | MODERATE_ALIGNMENT_2_OF_3 | DIVERGENT_CONFLICT_1_OF_3")
    alignment_ratio: str = Field(..., description="3/3 | 2/3 | 1/3 | 0/3")
    alignment_score: int = Field(..., description="Composite score from -100 to +100")
    confidence_multiplier: float = Field(..., ge=0.0, le=1.0)
    is_counter_trend: bool = Field(..., description="True if tactical 5m opposes 1h macro trend")
    recommended_action: str = Field(..., description="PROCEED | CAUTION | AVOID")
    macro_1h: TimeframeDetail
    structural_15m: TimeframeDetail
    tactical_5m: TimeframeDetail
    reasons: List[str] = Field(default_factory=list)


class SignalAlignmentValidation(BaseModel):
    """Result of checking a proposed trading signal against multi-timeframe alignment."""
    symbol: str
    proposed_direction: str
    is_aligned: bool
    alignment_degree: str
    confidence_multiplier: float
    is_counter_trend: bool
    recommended_action: str
    summary: str


class TrendEngine:
    """Multi-timeframe trend analysis and confluence engine."""

    TIMEFRAMES = ["1h", "15m", "5m"]

    def _extract_tf_detail(self, symbol: str, tf: str) -> TimeframeDetail:
        """Extracts indicator details and trend classification for a single timeframe."""
        snapshot = indicator_engine.get_snapshot(symbol, tf)
        if not snapshot:
            return TimeframeDetail(
                timeframe=tf,
                trend="NEUTRAL",
                technical_score=0,
                technical_bias="NEUTRAL",
                ema_trend="NEUTRAL",
                ema_spread_pct=0.0,
                macd_state="NEUTRAL",
                vwap_bias="NEUTRAL",
                current_price=0.0,
            )

        # Classify trend direction
        score = snapshot.technical_score
        if score >= 20:
            trend = "BULLISH"
        elif score <= -20:
            trend = "BEARISH"
        else:
            trend = "NEUTRAL"

        return TimeframeDetail(
            timeframe=tf,
            trend=trend,
            technical_score=score,
            technical_bias=snapshot.technical_bias,
            ema_trend=snapshot.ema.trend if snapshot.ema else "NEUTRAL",
            ema_spread_pct=round(snapshot.ema.spread_pct, 3) if snapshot.ema else 0.0,
            macd_state=snapshot.macd.momentum_state if snapshot.macd else "NEUTRAL",
            vwap_bias=snapshot.vwap.bias if snapshot.vwap else "NEUTRAL",
            current_price=snapshot.current_price,
        )

    def analyze_alignment(self, symbol: str) -> TimeframeAlignmentReport:
        """Computes multi-timeframe trend alignment across 1h, 15m, and 5m for a symbol."""
        symbol = symbol.upper()

        detail_1h = self._extract_tf_detail(symbol, "1h")
        detail_15m = self._extract_tf_detail(symbol, "15m")
        detail_5m = self._extract_tf_detail(symbol, "5m")

        # Preferred price: live mark price if available, else latest from 5m snapshot
        live = market_engine.get_live_price(symbol)
        current_price = (
            live.get("mark_price") or live.get("spot_price")
            if live
            else (detail_5m.current_price or detail_15m.current_price or detail_1h.current_price or 0.0)
        )

        # Weighted composite score: 1h Macro (45%), 15m Structural (35%), 5m Tactical (20%)
        weighted_score = int(
            round(
                (detail_1h.technical_score * 0.45)
                + (detail_15m.technical_score * 0.35)
                + (detail_5m.technical_score * 0.20)
            )
        )
        weighted_score = max(-100, min(100, weighted_score))

        # Count directional agreement
        bullish_count = sum(1 for d in [detail_1h, detail_15m, detail_5m] if d.trend == "BULLISH")
        bearish_count = sum(1 for d in [detail_1h, detail_15m, detail_5m] if d.trend == "BEARISH")

        reasons: List[str] = []

        # Evaluate Alignment Degree & Counter-Trend
        is_counter_trend = False
        if detail_1h.trend != "NEUTRAL" and detail_5m.trend != "NEUTRAL":
            if detail_1h.trend != detail_5m.trend:
                is_counter_trend = True
                reasons.append(
                    f"Counter-trend divergence: 5m tactical ({detail_5m.trend}) contradicts 1h macro ({detail_1h.trend})"
                )

        if bullish_count == 3:
            alignment_degree = "FULL_ALIGNMENT_3_OF_3"
            alignment_ratio = "3/3"
            directional_bias = "STRONG_BULLISH"
            confidence_multiplier = 1.00
            recommended_action = "PROCEED"
            reasons.append("Unanimous bullish alignment across 1h, 15m, and 5m timeframes")
        elif bearish_count == 3:
            alignment_degree = "FULL_ALIGNMENT_3_OF_3"
            alignment_ratio = "3/3"
            directional_bias = "STRONG_BEARISH"
            confidence_multiplier = 1.00
            recommended_action = "PROCEED"
            reasons.append("Unanimous bearish alignment across 1h, 15m, and 5m timeframes")
        elif bullish_count == 2:
            alignment_degree = "MODERATE_ALIGNMENT_2_OF_3"
            alignment_ratio = "2/3"
            directional_bias = "BULLISH"
            confidence_multiplier = 0.75 if not is_counter_trend else 0.55
            recommended_action = "CAUTION" if is_counter_trend else "PROCEED"
            reasons.append("Moderate bullish confluence (2 of 3 timeframes positive)")
        elif bearish_count == 2:
            alignment_degree = "MODERATE_ALIGNMENT_2_OF_3"
            alignment_ratio = "2/3"
            directional_bias = "BEARISH"
            confidence_multiplier = 0.75 if not is_counter_trend else 0.55
            recommended_action = "CAUTION" if is_counter_trend else "PROCEED"
            reasons.append("Moderate bearish confluence (2 of 3 timeframes negative)")
        else:
            alignment_degree = "DIVERGENT_CONFLICT_1_OF_3"
            alignment_ratio = "1/3" if (bullish_count or bearish_count) else "0/3"
            directional_bias = "NEUTRAL"
            confidence_multiplier = 0.40
            recommended_action = "AVOID"
            reasons.append("Market in choppy divergence or consolidation across timeframes")

        # Additional indicator context
        if detail_1h.ema_trend != "NEUTRAL":
            reasons.append(f"1h Macro EMA Trend: {detail_1h.ema_trend}")
        if detail_15m.macd_state != "NEUTRAL":
            reasons.append(f"15m Momentum: {detail_15m.macd_state}")

        return TimeframeAlignmentReport(
            symbol=symbol,
            current_price=current_price,
            directional_bias=directional_bias,
            alignment_degree=alignment_degree,
            alignment_ratio=alignment_ratio,
            alignment_score=weighted_score,
            confidence_multiplier=round(confidence_multiplier, 2),
            is_counter_trend=is_counter_trend,
            recommended_action=recommended_action,
            macro_1h=detail_1h,
            structural_15m=detail_15m,
            tactical_5m=detail_5m,
            reasons=reasons,
        )

    def analyze_all(self, symbols: Optional[List[str]] = None) -> List[TimeframeAlignmentReport]:
        """Runs multi-timeframe trend alignment for all monitored instruments."""
        target_symbols = symbols or ["BTC", "ETH", "SOL", "GOLD", "AVAX"]
        reports: List[TimeframeAlignmentReport] = []
        for sym in target_symbols:
            try:
                reports.append(self.analyze_alignment(sym))
            except Exception as e:
                logger.warning(f"Error analyzing alignment for {sym}: {e}")
        return reports

    def validate_signal_alignment(
        self, symbol: str, proposed_direction: str
    ) -> SignalAlignmentValidation:
        """Validates whether a proposed trade direction satisfies multi-timeframe confluence."""
        symbol = symbol.upper()
        dir_norm = proposed_direction.lower()
        is_long = dir_norm in ("long", "buy")

        report = self.analyze_alignment(symbol)

        # Check alignment
        if is_long:
            aligned = report.directional_bias in ("STRONG_BULLISH", "BULLISH")
            opposing = report.directional_bias in ("STRONG_BEARISH", "BEARISH")
        else:
            aligned = report.directional_bias in ("STRONG_BEARISH", "BEARISH")
            opposing = report.directional_bias in ("STRONG_BULLISH", "BULLISH")

        if aligned:
            rec = "PROCEED" if report.alignment_degree == "FULL_ALIGNMENT_3_OF_3" else "CAUTION"
            summary = (
                f"Approved: Proposed {proposed_direction.upper()} aligns with {report.directional_bias} bias "
                f"({report.alignment_ratio} confluence, multiplier {report.confidence_multiplier}x)"
            )
        elif opposing:
            rec = "AVOID"
            summary = (
                f"Opposed: Proposed {proposed_direction.upper()} trades directly against dominant "
                f"{report.directional_bias} trend. Counter-trend risk flagged."
            )
        else:
            rec = "CAUTION"
            summary = (
                f"Caution: Market regime is NEUTRAL/CHOPPY. Multi-timeframe confluence is weak ({report.alignment_ratio})."
            )

        return SignalAlignmentValidation(
            symbol=symbol,
            proposed_direction=proposed_direction.upper(),
            is_aligned=aligned,
            alignment_degree=report.alignment_degree,
            confidence_multiplier=report.confidence_multiplier if aligned else 0.40,
            is_counter_trend=opposing or report.is_counter_trend,
            recommended_action=rec,
            summary=summary,
        )


# Global singleton
trend_engine = TrendEngine()
