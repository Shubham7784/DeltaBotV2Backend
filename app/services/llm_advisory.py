"""Advisory LLM & Market Regime Validation Service (Phase 7).

CRITICAL ARCHITECTURAL CONSTRAINTS:
1. Advisory Only:
   - The LLM acts purely as a secondary advisory filter (State 2: VALIDATING -> State 3: LLM_CONFIRMED).
   - The LLM is strictly prohibited from altering or overriding risk limits, stop-loss prices, or position sizing.
2. Graceful Fallback:
   - If no Gemini API key is configured, provides a deterministic technical market regime classification
     (Trend Alignment, Volatility State, Momentum Health) with zero external network dependencies.
3. Structured Output:
   - Returns approved (bool), confidence score (0.0 to 1.0), detected regime, and clear reasoning.
"""

import logging
import asyncio
import os
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.schemas.indicator import IndicatorSnapshot
from app.services.indicator_engine import indicator_engine
from app.services.market_data_engine import market_engine
from app.services.trend_engine import trend_engine, TimeframeAlignmentReport

logger = logging.getLogger("delta_bot.llm_advisory")


class LLMAdvisoryEvaluation(BaseModel):
    """Structured advisory evaluation output."""
    approved: bool
    confidence: float = Field(..., ge=0.0, le=1.0)
    market_regime: str = Field(..., description="TRENDING_BULLISH | TRENDING_BEARISH | MEAN_REVERTING | CHOPPY_VOLATILE | BREAKOUT")
    advisory_recommendation: str = Field(..., description="CONFIRM | CAUTION | REJECT")
    reasoning: str
    risk_guardrail_acknowledged: bool = True
    macro_sentiment: str = "NEUTRAL"
    timeframe_confluence: str = "ALIGNED"
    timeframe_ratio: str = "3/3"
    is_counter_trend: bool = False
    confidence_multiplier: float = 1.0
    key_factors: List[str] = Field(default_factory=list)


class LLMAdvisoryService:
    """Service providing advisory market regime analysis and trade confirmation."""

    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        self._evaluation_cache: Dict[tuple, LLMAdvisoryEvaluation] = {}
        self._cache_lock = asyncio.Lock()

    def _regime_cache_key(self, symbol: str, direction: str) -> tuple:
        """Key advisory reuse to the completed market candles used by regime analysis."""
        candle_times = []
        for timeframe in ("1h", "15m", "5m"):
            buffer = market_engine.get_buffer(symbol, timeframe)
            latest = next(
                (candle for candle in reversed(buffer.get_all()) if candle.get("is_closed", True)),
                None,
            ) if buffer else None
            candle_times.append(latest["open_time"] if latest else None)
        return symbol.upper(), direction.lower(), tuple(candle_times)

    def _deterministic_regime_eval(
        self,
        symbol: str,
        direction: str,
        snapshot: Optional[IndicatorSnapshot],
        trend_report: Optional[TimeframeAlignmentReport] = None,
    ) -> LLMAdvisoryEvaluation:
        """Deterministic rule-based regime classification when LLM is unavailable or offline."""
        if not trend_report:
            trend_report = trend_engine.analyze_alignment(symbol)

        score = trend_report.alignment_score if trend_report else (snapshot.technical_score if snapshot else 0)
        factors: List[str] = []

        # Determine Regime from Multi-Timeframe Alignment
        if trend_report.alignment_degree == "FULL_ALIGNMENT_3_OF_3":
            regime = "TRENDING_BULLISH" if trend_report.directional_bias == "STRONG_BULLISH" else "TRENDING_BEARISH"
        elif trend_report.alignment_degree == "MODERATE_ALIGNMENT_2_OF_3":
            regime = "TRENDING_BULLISH" if "BULLISH" in trend_report.directional_bias else "TRENDING_BEARISH"
        elif snapshot and snapshot.vwap and abs(snapshot.vwap.distance_pct) >= 2.0:
            regime = "MEAN_REVERTING"
        else:
            regime = "CHOPPY_VOLATILE"

        # Confluence check
        is_long = direction.lower() in ("long", "buy")
        is_counter_trend = trend_report.is_counter_trend

        factors.append(f"Multi-Timeframe Confluence: {trend_report.alignment_ratio} ({trend_report.directional_bias})")
        factors.append(f"1h Macro: {trend_report.macro_1h.trend} | 15m: {trend_report.structural_15m.trend} | 5m: {trend_report.tactical_5m.trend}")

        if is_counter_trend:
            factors.append("Warning: 5m tactical signal opposes 1h institutional macro trend")

        if snapshot and snapshot.ema:
            factors.append(f"5m EMA Alignment: {snapshot.ema.trend} (spread: {snapshot.ema.spread_pct:.2f}%)")
        if snapshot and snapshot.macd:
            factors.append(f"5m MACD State: {snapshot.macd.momentum_state}")

        # Advisory decision logic
        if is_long:
            if "BEARISH" in trend_report.directional_bias and trend_report.alignment_degree == "FULL_ALIGNMENT_3_OF_3":
                approved = False
                rec = "REJECT"
                conf = 0.35
                reason = f"Advisory Reject: Proposed LONG opposes unanimous 3/3 bearish trend across 1h, 15m, and 5m."
            elif is_counter_trend or trend_report.alignment_degree == "DIVERGENT_CONFLICT_1_OF_3":
                approved = True
                rec = "CAUTION"
                conf = 0.55
                reason = f"Advisory Caution: Long setup in mixed/divergent regime ({trend_report.alignment_ratio}). Risk capped at 1%."
            else:
                approved = True
                rec = "CONFIRM"
                conf = min(0.95, 0.70 + (score / 200.0) * trend_report.confidence_multiplier)
                reason = f"Advisory Confirm: Long direction aligns with {trend_report.alignment_ratio} {regime} confluence."
        else:  # short
            if "BULLISH" in trend_report.directional_bias and trend_report.alignment_degree == "FULL_ALIGNMENT_3_OF_3":
                approved = False
                rec = "REJECT"
                conf = 0.35
                reason = f"Advisory Reject: Proposed SHORT opposes unanimous 3/3 bullish trend across 1h, 15m, and 5m."
            elif is_counter_trend or trend_report.alignment_degree == "DIVERGENT_CONFLICT_1_OF_3":
                approved = True
                rec = "CAUTION"
                conf = 0.55
                reason = f"Advisory Caution: Short setup in mixed/divergent regime ({trend_report.alignment_ratio}). Risk capped at 1%."
            else:
                approved = True
                rec = "CONFIRM"
                conf = min(0.95, 0.70 + (abs(score) / 200.0) * trend_report.confidence_multiplier)
                reason = f"Advisory Confirm: Short direction aligns with {trend_report.alignment_ratio} {regime} confluence."

        return LLMAdvisoryEvaluation(
            approved=approved,
            confidence=round(conf, 2),
            market_regime=regime,
            advisory_recommendation=rec,
            reasoning=reason,
            risk_guardrail_acknowledged=True,
            macro_sentiment="BULLISH" if score > 20 else ("BEARISH" if score < -20 else "NEUTRAL"),
            timeframe_confluence=trend_report.alignment_degree,
            timeframe_ratio=trend_report.alignment_ratio,
            is_counter_trend=is_counter_trend,
            confidence_multiplier=trend_report.confidence_multiplier,
            key_factors=factors,
        )

    async def evaluate_trade(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        stop_loss_price: float,
        take_profit_price: float,
        primary_timeframe: str = "5m",
    ) -> LLMAdvisoryEvaluation:
        """Reuse one advisory decision per symbol, direction, and candle set."""
        cache_key = self._regime_cache_key(symbol, direction)
        async with self._cache_lock:
            cached = self._evaluation_cache.get(cache_key)
        if cached is not None:
            return cached

        result = await self._evaluate_trade_uncached(
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            primary_timeframe=primary_timeframe,
        )
        async with self._cache_lock:
            self._evaluation_cache[cache_key] = result
            if len(self._evaluation_cache) > 1000:
                self._evaluation_cache.clear()
                self._evaluation_cache[cache_key] = result
        return result

    async def _evaluate_trade_uncached(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        stop_loss_price: float,
        take_profit_price: float,
        primary_timeframe: str = "5m",
    ) -> LLMAdvisoryEvaluation:
        """Evaluates a trade proposal against multi-timeframe regime and macro sentiment."""
        symbol = symbol.upper()
        snapshot = indicator_engine.get_snapshot(symbol, primary_timeframe)
        trend_report = trend_engine.analyze_alignment(symbol)

        # Fallback if no Gemini API key is configured
        if not self.api_key:
            return self._deterministic_regime_eval(symbol, direction, snapshot, trend_report)

        # If Gemini API key is configured, perform advisory query using gemini-3.8-flash
        try:
            from google import genai
            client = genai.Client(api_key=self.api_key)
            prompt = f"""You are a professional quantitative trading advisory engine for Delta Exchange.
Evaluate the following trade proposal against multi-timeframe market conditions:

Target Trade:
- Symbol: {symbol}
- Proposed Direction: {direction.upper()}
- Target Entry: ${entry_price:,.2f}
- Stop Loss: ${stop_loss_price:,.2f}
- Take Profit: ${take_profit_price:,.2f}

Multi-Timeframe Trend Confluence:
- 1h Institutional Macro Trend: {trend_report.macro_1h.trend} (Score: {trend_report.macro_1h.technical_score}, EMA: {trend_report.macro_1h.ema_trend})
- 15m Structural Trend: {trend_report.structural_15m.trend} (Score: {trend_report.structural_15m.technical_score}, MACD: {trend_report.structural_15m.macd_state})
- 5m Tactical Trigger: {trend_report.tactical_5m.trend} (Score: {trend_report.tactical_5m.technical_score})
- Overall Alignment: {trend_report.alignment_ratio} ({trend_report.alignment_degree})
- Counter-Trend Divergence Detected: {trend_report.is_counter_trend}

Non-Negotiable Guardrails:
1. You are strictly ADVISORY. You CANNOT modify risk parameters, enlarge position size, or widen stop loss.
2. Recommend exactly one of: CONFIRM | CAUTION | REJECT.
3. Provide concise reasoning in 2-3 sentences.
"""

            response = await client.aio.models.generate_content(
                model="gemini-3.8-flash",
                contents=prompt,
            )
            text = response.text or ""
            rec = "CONFIRM"
            approved = True
            if "REJECT" in text.upper():
                rec = "REJECT"
                approved = False
            elif "CAUTION" in text.upper():
                rec = "CAUTION"
                approved = True

            factors = [
                f"Gemini-3.8-flash Advisory: {rec}",
                f"Multi-Timeframe Ratio: {trend_report.alignment_ratio}",
                f"1h Macro: {trend_report.macro_1h.trend} | 15m: {trend_report.structural_15m.trend}",
            ]
            if trend_report.is_counter_trend:
                factors.append("Tactical counter-trend flag active")

            return LLMAdvisoryEvaluation(
                approved=approved,
                confidence=0.85 if approved else 0.40,
                market_regime="TRENDING" if "trend" in text.lower() else "CHOPPY_VOLATILE",
                advisory_recommendation=rec,
                reasoning=text[:350].strip(),
                risk_guardrail_acknowledged=True,
                macro_sentiment=trend_report.directional_bias,
                timeframe_confluence=trend_report.alignment_degree,
                timeframe_ratio=trend_report.alignment_ratio,
                is_counter_trend=trend_report.is_counter_trend,
                confidence_multiplier=trend_report.confidence_multiplier,
                key_factors=factors,
            )
        except Exception as e:
            logger.warning(f"Gemini API advisory query error: {e}. Falling back to deterministic regime eval.")
            return self._deterministic_regime_eval(symbol, direction, snapshot, trend_report)

    async def evaluate_symbol_regime(self, symbol: str) -> LLMAdvisoryEvaluation:
        """Evaluates overall market regime for an active instrument without a specific trade order."""
        symbol = symbol.upper()
        trend_report = trend_engine.analyze_alignment(symbol)
        snapshot = indicator_engine.get_snapshot(symbol, "5m")
        # Evaluate hypothetical continuation of dominant trend
        direction = "long" if "BULLISH" in trend_report.directional_bias else "short"
        return self._deterministic_regime_eval(symbol, direction, snapshot, trend_report)


# Global singleton
llm_advisory = LLMAdvisoryService()
